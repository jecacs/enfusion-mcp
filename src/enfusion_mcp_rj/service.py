"""Safe business service independent of MCP transport and model vendors."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError

from .bridge_models import (
    BRIDGE_BUILD_ID,
    BRIDGE_PROTOCOL_VERSION,
    BridgeContextResponse,
    BridgeTerrainSampleResponse,
)
from .catalog import PRODUCTION_CATALOG, VegetationCatalog
from .config import ServerConfig
from .coordinator import ApplyCoordinator, CoordinatorError, CoordinatorErrorCode
from .ledger import Ledger, LedgerError, OperationNotFoundError, OperationRecord, OperationState
from .locking import LockError, TargetLockManager, TargetScope
from .models import (
    BoundsXZ,
    OperationStateName,
    ToolErrorInfo,
    Vec3,
    VegetationApplyInput,
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
from .planner import (
    ALGORITHM_VERSION,
    CatalogValidationError,
    PlannerError,
    PlanningContext,
    TerrainBounds,
    TerrainContractError,
    TerrainSample,
    finalize_vegetation_plan,
    prepare_vegetation_plan,
    underfilled_output,
    validate_vegetation_input,
)

MANUAL_START_GUIDANCE = (
    "Start Arma Reforger Tools manually through Steam/Proton, open the intended project, "
    "and enable File > Options > General > Net API. The server never starts Workbench."
)
MAX_ERROR_PAYLOAD_CHARS = 1024
MAX_ERROR_MESSAGE_CHARS = 2048


def _tool_error(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    unknown_outcome: bool = False,
    **details: str | int | float | bool | None,
) -> ToolErrorInfo:
    return ToolErrorInfo(
        code=code,
        message=message[:MAX_ERROR_MESSAGE_CHARS],
        retryable=retryable,
        unknown_outcome=unknown_outcome,
        details=details,
    )


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
        ledger: Ledger,
        lock_manager: TargetLockManager,
        target_scope: TargetScope,
    ) -> None:
        try:
            client_address = client.workbench_address
        except AttributeError as error:
            raise TypeError("client must expose its immutable Workbench address") from error
        if client_address != config.workbench_address:
            raise ValueError(
                "NET API client target does not match the validated server configuration"
            )
        expected_state_dir = config.state_dir.path.resolve(strict=True)
        if ledger.state_dir != expected_state_dir or lock_manager.state_dir != expected_state_dir:
            raise ValueError(
                "ledger and lock manager must use the configured shared state directory"
            )
        expected_scope = TargetScope.derive(
            workbench_host=config.workbench_host,
            workbench_port=config.workbench_port,
            project_host_path=config.project_host_path.path,
            project_engine_path=config.project_engine_path.value,
            world=config.allowed_world.value,
        )
        if target_scope != expected_scope:
            raise ValueError("target scope does not match the validated server configuration")
        if type(catalog) is not VegetationCatalog:
            raise TypeError("catalog must be an immutable VegetationCatalog")
        self._config = config
        self._client = client
        self._catalog = catalog
        self._ledger = ledger
        self._lock_manager = lock_manager
        self._target_scope = target_scope
        self._coordinator = ApplyCoordinator(
            ledger=ledger,
            lock_manager=lock_manager,
            target_scope=target_scope,
            bridge=client,
            expected_catalog_hash=catalog.catalog_hash,
            expected_prefabs=frozenset(entry.resource_name for entry in catalog.entries),
            expected_world_path=config.allowed_world.value,
        )

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
            context = await self._fetch_context()
        except NetApiError as error:
            return WorldContextOutput(ok=False, error=_typed_net_error(error))
        except (ValidationError, ValueError) as error:
            return WorldContextOutput(
                ok=False,
                error=_tool_error("INVALID_RESPONSE", str(error)),
            )
        validation_error = self._validate_context_identity(context)
        if validation_error is not None:
            return WorldContextOutput(
                ok=False,
                world_path=context.world_path or None,
                error=validation_error,
            )
        if context.status == "error":
            return WorldContextOutput(
                ok=False,
                world_path=context.world_path or None,
                error=_tool_error(
                    context.error_code,
                    context.message or "Workbench context handler returned an error",
                ),
            )
        bounds = context.terrain_bounds
        return WorldContextOutput(
            ok=True,
            world_path=context.world_path,
            mode=context.mode,
            subscene=context.current_subscene,
            current_layer_id=context.current_layer_id,
            active_layer_path=context.active_layer_path,
            terrain_bounds=(
                BoundsXZ(
                    min_x=bounds.min_x,
                    min_z=bounds.min_z,
                    max_x=bounds.max_x,
                    max_z=bounds.max_z,
                )
                if bounds is not None
                else None
            ),
            selection_count=context.selection_count,
            selected_name=context.selected_name or None,
            selected_class=context.selected_class or None,
            polygon_compatible=context.polygon_compatible,
            shape_closed=context.shape_closed,
            shape_points_world=[
                Vec3(x=point.x, y=point.y, z=point.z) for point in context.shape_points_world
            ],
            bridge_protocol_version=context.bridge_protocol_version,
            bridge_build_id=context.bridge_build_id,
            catalog_hash=context.catalog_hash,
        )

    async def vegetation_catalog(self) -> VegetationCatalogOutput:
        return self._catalog.as_output()

    async def vegetation_plan(self, request: VegetationPlanInput) -> VegetationPlanOutput:
        try:
            request = validate_vegetation_input(request, self._catalog).request
        except PlannerError as error:
            return underfilled_output(request, error)
        try:
            async with self._lock_manager.shared(self._target_scope):
                bridge_context = await self._fetch_context()
                identity_error = self._validate_context_identity(bridge_context)
                if identity_error is not None:
                    raise PlannerError(identity_error.code, identity_error.message)
                if bridge_context.status == "error":
                    raise PlannerError(
                        bridge_context.error_code,
                        bridge_context.message or "Workbench context handler returned an error",
                    )
                planning_context = self._planning_context(bridge_context)
                prepared = prepare_vegetation_plan(planning_context, request, self._catalog)
                terrain_payload: dict[str, JsonValue] = {
                    "points": [{"x": query.x, "z": query.z} for query in prepared.terrain_queries]
                }
                raw_terrain = await self._client.call("RJMCP_TerrainSample", terrain_payload)
                terrain_response = BridgeTerrainSampleResponse.model_validate(raw_terrain)
                if terrain_response.bridge_protocol_version != BRIDGE_PROTOCOL_VERSION:
                    raise TerrainContractError(
                        "BRIDGE_VERSION_MISMATCH",
                        "Terrain handler protocol version does not match Python",
                    )
                if terrain_response.status == "error":
                    raise TerrainContractError(
                        terrain_response.error_code,
                        terrain_response.message or "Terrain handler returned an error",
                    )
                samples = tuple(
                    TerrainSample(
                        requested_x=item.requested_x,
                        requested_z=item.requested_z,
                        has_terrain=item.has_terrain,
                        terrain_y=item.terrain_y,
                        normal_x=item.normal_x,
                        normal_y=item.normal_y,
                        normal_z=item.normal_z,
                    )
                    for item in terrain_response.results
                )
                plan = finalize_vegetation_plan(
                    prepared,
                    samples,
                    created_at=datetime.now(tz=UTC),
                )
                persisted = self._ledger.store_plan(
                    canonical_json=plan.canonical_json,
                    created_at=plan.created_at,
                    expires_at=plan.expires_at,
                    plan_id=plan.plan_id,
                )
        except NetApiError as error:
            return VegetationPlanOutput(
                ok=False,
                algorithm_version=ALGORITHM_VERSION,
                requested_count=request.count,
                target_layer=request.target_layer,
                error=_typed_net_error(error),
            )
        except (PlannerError, CatalogValidationError, TerrainContractError) as error:
            return underfilled_output(request, error)
        except (ValidationError, ValueError) as error:
            return VegetationPlanOutput(
                ok=False,
                algorithm_version=ALGORITHM_VERSION,
                requested_count=request.count,
                target_layer=request.target_layer,
                error=_tool_error("INVALID_RESPONSE", str(error)),
            )
        except LockError as error:
            return VegetationPlanOutput(
                ok=False,
                algorithm_version=ALGORITHM_VERSION,
                requested_count=request.count,
                target_layer=request.target_layer,
                error=_tool_error("LOCK_FAILED", str(error)),
            )

        output = plan.as_output()
        return output.model_copy(
            update={
                "created_at": persisted.created_at.isoformat(),
                "expires_at": persisted.expires_at.isoformat(),
            }
        )

    async def vegetation_apply(
        self,
        plan_id: str,
        idempotency_key: UUID,
    ) -> VegetationApplyOutput:
        validated = VegetationApplyInput(plan_id=plan_id, idempotency_key=str(idempotency_key))
        operation_id = validated.idempotency_key
        if not self._catalog.production_ready:
            return self._apply_error_output(
                plan_id,
                operation_id,
                "CATALOG_NOT_READY",
                "Apply is fail-closed while the production catalog is unverified.",
            )
        try:
            result = await self._coordinator.apply(plan_id, operation_id)
        except CoordinatorError as error:
            return self._apply_error_output(
                plan_id,
                operation_id,
                error.code.value,
                str(error),
                blocking_operation=error.blocking_operation,
            )
        error_info = None
        if not result.ok:
            code = result.error_code or CoordinatorErrorCode.STATE_CONFLICT
            code_text = code.value if isinstance(code, CoordinatorErrorCode) else code
            error_info = _tool_error(
                code_text,
                result.message or code_text,
                unknown_outcome=(
                    result.state.value == "UNKNOWN"
                    or code_text == CoordinatorErrorCode.UNKNOWN_OUTCOME.value
                ),
            )
        return VegetationApplyOutput(
            ok=result.ok,
            plan_id=result.plan_id,
            operation_id=result.operation_id,
            state=result.state.value,
            created_count=result.created_count,
            reconciled=result.reconciled,
            error=error_info,
        )

    def _apply_error_output(
        self,
        plan_id: str,
        operation_id: str,
        code: str,
        message: str,
        *,
        blocking_operation: OperationRecord | None = None,
    ) -> VegetationApplyOutput:
        """Preserve durable outcomes even when this invocation fails before send.

        Top-level IDs always describe the requested binding. A different
        existing binding is reported only in details; its uncertainty must not
        disappear behind a pre-send failure of the conflicting invocation.
        """

        state: OperationStateName = "PRE_SEND_FAILED"
        unknown = code == CoordinatorErrorCode.UNKNOWN_OUTCOME.value
        details: dict[str, str | int | float | bool | None] = {
            "state_subject": "requested_invocation",
        }
        try:
            existing = blocking_operation or self._ledger.get_operation_for_plan(plan_id)
            if existing is None:
                try:
                    existing = self._ledger.get_operation(operation_id)
                except OperationNotFoundError:
                    existing = None
            if existing is not None:
                same_binding = existing.plan_id == plan_id and existing.operation_id == operation_id
                details.update(
                    existing_plan_id=existing.plan_id,
                    existing_operation_id=existing.operation_id,
                    existing_state=existing.state.value,
                )
                if same_binding:
                    state = existing.state.value
                    details["state_subject"] = "requested_operation"
                unknown = unknown or existing.state in {
                    OperationState.SENDING,
                    OperationState.UNKNOWN,
                    OperationState.ROLLBACK_FAILED,
                }
        except (LedgerError, sqlite3.Error, OSError) as error:
            # Failure to read durable evidence is never proof that no previous
            # send occurred. Preserve IDs and report conservative uncertainty.
            state = "UNKNOWN"
            unknown = True
            details["state_subject"] = "ledger_unavailable"
            details["ledger_error"] = str(error)[:512]
        return VegetationApplyOutput(
            ok=False,
            plan_id=plan_id,
            operation_id=operation_id,
            state=state,
            error=ToolErrorInfo(
                code=code,
                message=message[:MAX_ERROR_MESSAGE_CHARS],
                unknown_outcome=unknown,
                details=details,
            ),
        )

    async def _fetch_context(self) -> BridgeContextResponse:
        payload = _object_payload(
            await self._client.call("RJMCP_GetContext"),
            "RJMCP_GetContext",
        )
        return BridgeContextResponse.model_validate(payload)

    def _validate_context_identity(self, context: BridgeContextResponse) -> ToolErrorInfo | None:
        if context.bridge_protocol_version != BRIDGE_PROTOCOL_VERSION:
            return _tool_error("BRIDGE_VERSION_MISMATCH", "Bridge protocol version mismatch")
        if context.bridge_build_id != BRIDGE_BUILD_ID:
            return _tool_error("BRIDGE_BUILD_MISMATCH", "Bridge build ID mismatch")
        if context.catalog_hash != self._catalog.catalog_hash:
            return _tool_error("CATALOG_MISMATCH", "Python and bridge catalog hashes differ")
        if context.status == "ok" and context.world_path != self._config.allowed_world.value:
            return _tool_error(
                "WORLD_NOT_ALLOWED",
                "Workbench has a world open that is not the configured allowed world",
            )
        return None

    @staticmethod
    def _planning_context(context: BridgeContextResponse) -> PlanningContext:
        bounds = context.terrain_bounds
        if bounds is None:
            raise PlannerError("TERRAIN_UNAVAILABLE", "Context did not return terrain bounds")
        return PlanningContext(
            world_path=context.world_path,
            mode=context.mode,
            current_subscene=context.current_subscene,
            current_layer_id=context.current_layer_id,
            active_layer_path=context.active_layer_path,
            terrain_bounds=TerrainBounds(
                min_x=bounds.min_x,
                min_y=bounds.min_y,
                min_z=bounds.min_z,
                max_x=bounds.max_x,
                max_y=bounds.max_y,
                max_z=bounds.max_z,
            ),
            selection_count=context.selection_count,
            selected_name=context.selected_name,
            selected_class=context.selected_class,
            polygon_compatible=context.polygon_compatible,
            shape_closed=context.shape_closed,
            shape_points_world=tuple(
                Vec3(x=point.x, y=point.y, z=point.z) for point in context.shape_points_world
            ),
            bridge_protocol_version=context.bridge_protocol_version,
            bridge_build_id=context.bridge_build_id,
            bridge_catalog_hash=context.catalog_hash,
        )


__all__ = ["MANUAL_START_GUIDANCE", "SafeRuntimeService"]
