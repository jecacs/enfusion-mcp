"""Safe business service independent of MCP transport and model vendors."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from uuid import UUID

from .catalog import PRODUCTION_CATALOG, VegetationCatalog
from .config import ServerConfig
from .ledger import Ledger
from .locking import TargetLockManager, TargetScope
from .models import (
    ToolErrorInfo,
    VegetationApplyOutput,
    VegetationCatalogOutput,
    VegetationPlanInput,
    VegetationPlanOutput,
    WorkbenchStatusOutput,
    WorldContextOutput,
)
from .net_api import (
    JsonValue,
    NetApiClient,
    NetApiError,
    NetApiPhase,
    UnknownOutcomeError,
    WorkbenchApiError,
)

MANUAL_START_GUIDANCE = (
    "Start Arma Reforger Tools manually through Steam/Proton, open the intended project, "
    "and enable File > Options > General > Net API. The server never starts Workbench."
)
MAX_ERROR_PAYLOAD_CHARS = 1024
MAX_ERROR_MESSAGE_CHARS = 2048


def _typed_net_error(error: NetApiError) -> ToolErrorInfo:
    details: dict[str, str | int | float | bool | None] = {
        "send_started": error.send_started,
    }
    workbench_error: WorkbenchApiError | None = None
    if isinstance(error, WorkbenchApiError):
        workbench_error = error
    if isinstance(error, UnknownOutcomeError):
        details["cause_code"] = error.cause.code.value
        details["cause_phase"] = error.cause.phase.value
        details["cause_send_started"] = error.cause.send_started
        if isinstance(error.cause, WorkbenchApiError):
            workbench_error = error.cause
    if workbench_error is not None:
        encoded_payload = workbench_error.payload.encode("utf-8")
        details["workbench_status"] = workbench_error.status[:256]
        details["workbench_payload"] = workbench_error.payload[:MAX_ERROR_PAYLOAD_CHARS]
        details["workbench_payload_bytes"] = len(encoded_payload)
        details["workbench_payload_truncated"] = (
            len(workbench_error.payload) > MAX_ERROR_PAYLOAD_CHARS
        )
        details["workbench_payload_sha256"] = hashlib.sha256(encoded_payload).hexdigest()
    message = str(error)
    if error.code.value == "CONNECTION_REFUSED":
        message = f"{message} {MANUAL_START_GUIDANCE}"
    encoded_message = message.encode("utf-8")
    if len(message) > MAX_ERROR_MESSAGE_CHARS:
        details["message_bytes"] = len(encoded_message)
        details["message_truncated"] = True
        details["message_sha256"] = hashlib.sha256(encoded_message).hexdigest()
        message = message[:MAX_ERROR_MESSAGE_CHARS]
    return ToolErrorInfo(
        code=error.code.value,
        message=message,
        phase=error.phase.value,
        retryable=(
            not isinstance(error, UnknownOutcomeError)
            and error.code.value in {"CONNECTION_REFUSED", "CONNECTION_LOST", "TIMEOUT"}
        ),
        unknown_outcome=isinstance(error, UnknownOutcomeError),
        details=details,
    )


def _object_payload(value: JsonValue, endpoint: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"{endpoint} returned a non-object payload")
    return value


def _required_bool(payload: Mapping[str, JsonValue], field: str, endpoint: str) -> bool:
    value = payload.get(field)
    if type(value) is not bool:
        raise ValueError(f"{endpoint}.{field} must be a boolean")
    return value


class SafeRuntimeService:
    """Implement safe tools over a separately testable NET API client."""

    def __init__(  # noqa: PLR0913 - explicit dependencies make policy wiring auditable
        self,
        config: ServerConfig,
        client: NetApiClient,
        *,
        catalog: VegetationCatalog = PRODUCTION_CATALOG,
        ledger: Ledger | None = None,
        lock_manager: TargetLockManager | None = None,
        target_scope: TargetScope | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._catalog = catalog
        self._ledger = ledger
        self._lock_manager = lock_manager
        self._target_scope = target_scope

    async def workbench_status(self) -> WorkbenchStatusOutput:
        successful_round_trips = 0
        try:
            workbench = _object_payload(
                await self._client.call("IsWorkbenchRunning"),
                "IsWorkbenchRunning",
            )
            successful_round_trips += 1
            workbench_running = _required_bool(workbench, "IsRunning", "IsWorkbenchRunning")
            editor = _object_payload(
                await self._client.call("IsWorldEditorRunning"),
                "IsWorldEditorRunning",
            )
            editor_running = _required_bool(editor, "IsRunning", "IsWorldEditorRunning")
        except NetApiError as error:
            reachable = successful_round_trips > 0 or error.phase not in {
                NetApiPhase.BEFORE_CONNECT,
                NetApiPhase.CONNECT,
            }
            return WorkbenchStatusOutput(
                ok=False,
                reachable=reachable,
                host=self._config.workbench_host,
                port=self._config.workbench_port,
                message=(
                    "Workbench is reachable but returned an invalid or incomplete status response."
                    if reachable
                    else MANUAL_START_GUIDANCE
                ),
                error=_typed_net_error(error),
            )
        except ValueError as error:
            return WorkbenchStatusOutput(
                ok=False,
                reachable=True,
                host=self._config.workbench_host,
                port=self._config.workbench_port,
                message="Workbench returned an invalid status response.",
                error=ToolErrorInfo(code="INVALID_RESPONSE", message=str(error)),
            )

        return WorkbenchStatusOutput(
            ok=True,
            reachable=True,
            workbench_running=workbench_running,
            world_editor_running=editor_running,
            host=self._config.workbench_host,
            port=self._config.workbench_port,
            message=(
                "Workbench and World Editor report running."
                if workbench_running and editor_running
                else MANUAL_START_GUIDANCE
            ),
        )

    async def world_context(self) -> WorldContextOutput:
        try:
            payload = _object_payload(
                await self._client.call("RJMCP_GetContext"),
                "RJMCP_GetContext",
            )
        except NetApiError as error:
            return WorldContextOutput(ok=False, error=_typed_net_error(error))
        except ValueError as error:
            return WorldContextOutput(
                ok=False,
                error=ToolErrorInfo(code="INVALID_RESPONSE", message=str(error)),
            )

        # The complete, statically checked bridge mapping is added with the
        # staged handler in Checkpoint C. Reject an unknown shape rather than
        # passing arbitrary handler fields through to MCP clients.
        world_path = payload.get("worldPath")
        if not isinstance(world_path, str):
            return WorldContextOutput(
                ok=False,
                error=ToolErrorInfo(
                    code="INVALID_RESPONSE",
                    message="RJMCP_GetContext.worldPath must be a string",
                ),
            )
        if world_path != self._config.allowed_world.value:
            return WorldContextOutput(
                ok=False,
                world_path=world_path,
                error=ToolErrorInfo(
                    code="WORLD_NOT_ALLOWED",
                    message="Workbench has a world open that is not the configured allowed world",
                ),
            )
        return WorldContextOutput(ok=True, world_path=world_path)

    async def vegetation_catalog(self) -> VegetationCatalogOutput:
        return self._catalog.as_output()

    async def vegetation_plan(self, request: VegetationPlanInput) -> VegetationPlanOutput:
        return VegetationPlanOutput(
            ok=False,
            algorithm_version="pcg32-rj-v1-not-yet-enabled",
            requested_count=request.count,
            target_layer=request.target_layer,
            error=ToolErrorInfo(
                code="CATALOG_NOT_READY",
                message=(
                    "Production vegetation allowlist is intentionally empty until exact resources "
                    "can be proven without violating the active-project permission boundary."
                ),
            ),
        )

    async def vegetation_apply(
        self,
        plan_id: str,
        idempotency_key: UUID,
    ) -> VegetationApplyOutput:
        return VegetationApplyOutput(
            ok=False,
            plan_id=plan_id,
            operation_id=str(idempotency_key),
            state="PRE_SEND_FAILED",
            error=ToolErrorInfo(
                code="CATALOG_NOT_READY",
                message="Apply is fail-closed while the production catalog is unverified.",
            ),
        )


__all__ = ["MANUAL_START_GUIDANCE", "SafeRuntimeService"]
