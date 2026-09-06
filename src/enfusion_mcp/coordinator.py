"""Fail-closed vegetation apply/reconciliation coordination.

The coordinator is the only layer allowed to turn an immutable stored plan into
an ``EnfusionMCP_VegetationApply`` request.  Public callers supply only ``plan_id`` and
a UUID idempotency key; transforms and resource names are always recovered from
the shared ledger.  Every create attempt is serialized by the target lock and
is durably marked ``SENDING`` before the bridge is called exactly once.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol

from pydantic import ValidationError

from enfusion_mcp.bridge_models import (
    BRIDGE_BUILD_ID,
    BRIDGE_PROTOCOL_VERSION,
    MAX_PLACEMENTS,
    BridgeContextResponse,
    BridgeVegetationApplyRequest,
    BridgeVegetationApplyResponse,
)
from enfusion_mcp.ledger import (
    MAX_DETAIL_LENGTH,
    InvalidOperationTransitionError,
    Ledger,
    OperationConflictError,
    OperationNotFoundError,
    OperationRecord,
    OperationState,
    PlanExpiredError,
    PlanNotFoundError,
    PlanRecord,
    PlanValidationError,
    TargetOperationConflictError,
    validate_idempotency_key,
)
from enfusion_mcp.locking import LockError, TargetLockManager, TargetScope
from enfusion_mcp.models import Vec3
from enfusion_mcp.net_api import JsonValue, NetApiError
from enfusion_mcp.path_types import ResourceName
from enfusion_mcp.planner import (
    ALGORITHM_VERSION,
    ANGLE_SCALE,
    COORDINATE_SCALE,
    MAX_ABS_COORDINATE_M,
    MAX_POLYGON_VERTICES,
    MIN_POLYGON_POINTS,
    PLAN_SCHEMA_VERSION,
    SCALE_SCALE,
    PlannerError,
    validate_polygon,
)

VEGETATION_ENDPOINT: Final = "EnfusionMCP_VegetationApply"
CONTEXT_ENDPOINT: Final = "EnfusionMCP_GetContext"
MAX_MIN_SPACING_M: Final = 1_000.0
MAX_SLOPE_DEG: Final = 90.0
MAX_SCALE: Final = 10.0
MAX_TERRAIN_Y_EPSILON: Final = 0.05
FULL_ROTATION_DEG: Final = 360.0
MAX_DIAGNOSTIC_CHARS: Final = 2048
MAX_ABS_COORDINATE_UNITS: Final = round(MAX_ABS_COORDINATE_M * COORDINATE_SCALE)
_HASH_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_LAYERS: Final = frozenset({"MCP_Preview", "MCP_Vegetation"})


class CoordinatorErrorCode(StrEnum):
    """Stable machine-readable coordinator failures."""

    PLAN_NOT_FOUND = "PLAN_NOT_FOUND"
    PLAN_EXPIRED = "PLAN_EXPIRED"
    OPERATION_CONFLICT = "OPERATION_CONFLICT"
    INVALID_STORED_PLAN = "INVALID_STORED_PLAN"
    LOCK_FAILED = "LOCK_FAILED"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    BRIDGE_PROTOCOL_ERROR = "BRIDGE_PROTOCOL_ERROR"
    RECONCILIATION_FAILED = "RECONCILIATION_FAILED"
    PARTIAL_OPERATION = "PARTIAL_OPERATION"
    ENTITY_CONFLICT = "ENTITY_CONFLICT"
    MUTATION_ROLLED_BACK = "MUTATION_ROLLED_BACK"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"
    UNDONE = "UNDONE"
    PRE_SEND_FAILED = "PRE_SEND_FAILED"
    STATE_CONFLICT = "STATE_CONFLICT"
    CONTEXT_PREFLIGHT_FAILED = "CONTEXT_PREFLIGHT_FAILED"
    STALE_CONTEXT = "STALE_CONTEXT"
    TARGET_OPERATION_UNRESOLVED = "TARGET_OPERATION_UNRESOLVED"


class CoordinatorError(RuntimeError):
    """A typed failure before an operation result can be safely returned."""

    def __init__(
        self,
        code: CoordinatorErrorCode,
        message: str,
        *,
        blocking_operation: OperationRecord | None = None,
    ) -> None:
        super().__init__(_bounded_diagnostic(message, limit=MAX_DIAGNOSTIC_CHARS))
        self.code = code
        self.blocking_operation = blocking_operation


class BridgeProtocolError(CoordinatorError):
    """A bridge response did not satisfy the trusted response contract."""

    def __init__(self, message: str) -> None:
        super().__init__(CoordinatorErrorCode.BRIDGE_PROTOCOL_ERROR, message)


class ReconciliationClassification(StrEnum):
    """Read-only classification returned by the Enforce reconciliation mode."""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    CHANGED = "CHANGED"
    NONE = "NONE"


class BridgeResponseState(StrEnum):
    """Unified states emitted by the staged Enforce apply handler."""

    COMPLETE = "COMPLETE"
    NONE = "NONE"
    PARTIAL = "PARTIAL"
    CHANGED = "CHANGED"
    ROLLBACK_VERIFIED = "ROLLBACK_VERIFIED"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"
    UNKNOWN = "UNKNOWN"
    REJECTED = "REJECTED"


class BridgeClient(Protocol):
    """Narrow structural contract implemented by :class:`NetApiClient` and fakes."""

    async def call(
        self,
        api_func: str,
        params: Mapping[str, JsonValue] | None = None,
    ) -> JsonValue:
        """Call one read-only bridge endpoint."""

    async def call_mutation(
        self,
        api_func: str,
        params: Mapping[str, JsonValue] | None = None,
    ) -> JsonValue:
        """Send one non-retryable mutation request."""

    async def call_reconcile(
        self,
        api_func: str,
        params: Mapping[str, JsonValue],
    ) -> JsonValue:
        """Send the handler's read-only ``mode=reconcile`` request."""


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """Durable result suitable for conversion to structured MCP output."""

    plan_id: str
    operation_id: str
    state: OperationState
    created_count: int
    reconciled: bool
    classification: ReconciliationClassification | None = None
    error_code: CoordinatorErrorCode | str | None = None
    message: str | None = None

    @property
    def ok(self) -> bool:
        """Only a proved applied batch is successful."""

        return self.state is OperationState.APPLIED and self.error_code is None


@dataclass(frozen=True, slots=True)
class _Placement:
    index: int
    prefab: str
    x: float
    y: float
    z: float
    yaw: float
    scale: float


@dataclass(frozen=True, slots=True)
class _TrustedPlan:
    plan_id: str
    catalog_hash: str
    world_path: str
    bridge_build_id: str
    subscene: int
    current_layer_id: int
    active_layer_path: str
    target_layer: str
    shape_name: str
    shape_class: str
    shape_hash: str
    shape_points_world: tuple[Vec3, ...]
    min_spacing_m: float
    max_slope_deg: float
    scale_min: float
    scale_max: float
    terrain_y_epsilon: float
    placements: tuple[_Placement, ...]

    def envelope(self, *, mode: str, operation_id: str) -> dict[str, JsonValue]:
        if mode not in {"create", "reconcile"}:  # trusted-code assertion
            raise ValueError(f"unsupported vegetation operation mode: {mode}")
        wire: dict[str, JsonValue] = {
            "mode": mode,
            "planId": self.plan_id,
            "operationId": operation_id,
            "bridgeBuildId": self.bridge_build_id,
            "catalogHash": self.catalog_hash,
            "worldPath": self.world_path,
            "subscene": self.subscene,
            "targetLayer": self.target_layer,
            "count": len(self.placements),
            "minSpacingM": self.min_spacing_m,
            "maxSlopeDeg": self.max_slope_deg,
            "scaleMin": self.scale_min,
            "scaleMax": self.scale_max,
            "terrainYEpsilon": self.terrain_y_epsilon,
            "prefabs": [placement.prefab for placement in self.placements],
            "entityNames": [
                f"EnfusionMCP_{self.plan_id}_{placement.index}" for placement in self.placements
            ],
            "x": [placement.x for placement in self.placements],
            "y": [placement.y for placement in self.placements],
            "z": [placement.z for placement in self.placements],
            "yaw": [placement.yaw for placement in self.placements],
            "scale": [placement.scale for placement in self.placements],
        }
        return BridgeVegetationApplyRequest.model_validate(wire).model_dump(
            mode="json", by_alias=True
        )


@dataclass(frozen=True, slots=True)
class _MutationReport:
    state: BridgeResponseState
    error_code: str
    created_count: int
    detail: str | None


@dataclass(frozen=True, slots=True)
class _ReconciliationReport:
    classification: ReconciliationClassification
    matched_count: int
    detail: str | None


@dataclass(frozen=True, slots=True)
class _UnifiedBridgeReport:
    status: str
    error_code: str
    message: str
    state: BridgeResponseState
    expected_count: int
    matching_count: int
    created_count: int
    rollback_verified: bool
    entity_names: tuple[str, ...]

    @property
    def detail(self) -> str:
        if self.error_code:
            return f"{self.error_code}: {self.message}"
        return self.message


class ApplyCoordinator:
    """Coordinate one shared ledger/lock target without client identity assumptions."""

    def __init__(  # noqa: PLR0913 - explicit security dependencies are intentional
        self,
        *,
        ledger: Ledger,
        lock_manager: TargetLockManager,
        target_scope: TargetScope,
        bridge: BridgeClient,
        expected_catalog_hash: str,
        expected_prefabs: frozenset[str],
        expected_world_path: str,
        lock_wait_limit: float | None = None,
    ) -> None:
        if any(dependency is None for dependency in (ledger, lock_manager, target_scope, bridge)):
            raise TypeError("ledger, lock_manager, target_scope, and bridge are required")
        if _HASH_RE.fullmatch(expected_catalog_hash) is None:
            raise ValueError("expected_catalog_hash must be lowercase SHA-256")
        if type(expected_prefabs) is not frozenset:
            raise TypeError("expected_prefabs must be an immutable frozenset")
        for prefab in expected_prefabs:
            ResourceName(prefab)
        if not expected_world_path:
            raise ValueError("expected_world_path must not be empty")
        if lock_wait_limit is not None and (
            not math.isfinite(lock_wait_limit) or lock_wait_limit < 0
        ):
            raise ValueError("lock_wait_limit must be finite and non-negative")
        self._ledger = ledger
        self._lock_manager = lock_manager
        self._target_scope = target_scope
        self._bridge = bridge
        self._expected_catalog_hash = expected_catalog_hash
        self._expected_prefabs = expected_prefabs
        self._expected_world_path = expected_world_path
        self._lock_wait_limit = lock_wait_limit

    async def apply(self, plan_id: str, idempotency_key: str) -> ApplyResult:
        """Create once for a new binding; reconcile only for every existing binding."""

        key = _validated_key(idempotency_key)
        try:
            async with self._lock_manager.exclusive(
                self._target_scope, wait_limit=self._lock_wait_limit
            ):
                return await self._apply_locked(plan_id, key)
        except LockError as exc:
            raise CoordinatorError(
                CoordinatorErrorCode.LOCK_FAILED,
                f"could not acquire exclusive Workbench target lock: {exc}",
            ) from exc
        except TargetOperationConflictError as exc:
            raise CoordinatorError(
                CoordinatorErrorCode.TARGET_OPERATION_UNRESOLVED,
                str(exc),
                blocking_operation=exc.operation,
            ) from exc
        except OperationConflictError as exc:
            raise CoordinatorError(CoordinatorErrorCode.OPERATION_CONFLICT, str(exc)) from exc
        except PlanExpiredError as exc:
            raise CoordinatorError(CoordinatorErrorCode.PLAN_EXPIRED, str(exc)) from exc

    async def reconcile(self, plan_id: str, idempotency_key: str) -> ApplyResult:
        """Explicitly reconcile an existing binding without ever creating entities."""

        key = _validated_key(idempotency_key)
        try:
            async with self._lock_manager.exclusive(
                self._target_scope, wait_limit=self._lock_wait_limit
            ):
                plan, trusted = self._load_plan(plan_id)
                operation = self._ledger.get_operation_for_plan(plan.plan_id)
                if operation is None:
                    raise CoordinatorError(
                        CoordinatorErrorCode.OPERATION_CONFLICT,
                        "plan has no operation binding to reconcile",
                    )
                _require_same_binding(operation, key)
                self._ledger.assert_target_binding(key, self._target_scope.endpoint_canonical)
                return await self._reconcile_locked(trusted, operation)
        except LockError as exc:
            raise CoordinatorError(
                CoordinatorErrorCode.LOCK_FAILED,
                f"could not acquire exclusive Workbench target lock: {exc}",
            ) from exc
        except OperationConflictError as exc:
            raise CoordinatorError(CoordinatorErrorCode.OPERATION_CONFLICT, str(exc)) from exc

    async def _apply_locked(self, plan_id: str, key: str) -> ApplyResult:
        plan, trusted = self._load_plan(plan_id)
        try:
            bound_key = self._ledger.get_operation(key)
        except OperationNotFoundError:
            pass
        else:
            if bound_key.plan_id != plan.plan_id:
                raise CoordinatorError(
                    CoordinatorErrorCode.OPERATION_CONFLICT,
                    "idempotency key is already bound to another plan",
                    blocking_operation=bound_key,
                )
        existing = self._ledger.get_operation_for_plan(plan.plan_id)
        if existing is not None:
            _require_same_binding(existing, key)
            self._ledger.assert_target_binding(key, self._target_scope.endpoint_canonical)
            if existing.state in {OperationState.PLANNED, OperationState.PRE_SEND_FAILED}:
                if plan.is_expired():
                    raise CoordinatorError(
                        CoordinatorErrorCode.PLAN_EXPIRED,
                        f"plan {plan.plan_id} has expired",
                    )
                self._ledger.assert_target_sendable(key, self._target_scope.endpoint_canonical)
                await self._preflight_new_plan(trusted)
                return await self._send_create(trusted, existing)
            return await self._reconcile_locked(trusted, existing)

        # Expiry is checked before even the read-only context request.  The
        # ledger checks it again at bind time to close the race while preflight
        # is in progress.
        if plan.is_expired():
            raise CoordinatorError(
                CoordinatorErrorCode.PLAN_EXPIRED,
                f"plan {plan.plan_id} has expired",
            )
        self._ledger.assert_target_sendable(key, self._target_scope.endpoint_canonical)
        await self._preflight_new_plan(trusted)

        try:
            operation = self._ledger.bind_operation(
                plan_id=plan.plan_id,
                idempotency_key=key,
            )
        except PlanExpiredError as exc:
            raise CoordinatorError(CoordinatorErrorCode.PLAN_EXPIRED, str(exc)) from exc
        except OperationConflictError as exc:
            raise CoordinatorError(CoordinatorErrorCode.OPERATION_CONFLICT, str(exc)) from exc

        return await self._send_create(trusted, operation)

    async def _send_create(
        self,
        trusted: _TrustedPlan,
        operation: OperationRecord,
    ) -> ApplyResult:
        """Send once after a new or proven-pre-send-failed operation claim."""

        # Validate the real staged-wire model before recording the send claim.
        envelope = trusted.envelope(mode="create", operation_id=operation.operation_id)
        # This compare-and-transition is the durable single-sender claim.
        try:
            operation = self._ledger.transition_operation(
                operation.operation_id,
                OperationState.SENDING,
                expected_state=operation.state,
                target_endpoint=self._target_scope.endpoint_canonical,
            )
        except InvalidOperationTransitionError:
            operation = self._ledger.get_operation(operation.operation_id)
            return await self._reconcile_locked(trusted, operation)

        try:
            raw_report = await self._bridge.call_mutation(VEGETATION_ENDPOINT, envelope)
        except NetApiError as exc:
            if not exc.send_started:
                failed = self._ledger.transition_operation(
                    operation.operation_id,
                    OperationState.PRE_SEND_FAILED,
                    expected_state=OperationState.SENDING,
                    detail=_bounded_diagnostic(
                        f"mutation transport failed before send: {exc}", limit=MAX_DETAIL_LENGTH
                    ),
                )
                return _failed_result(
                    failed,
                    code=CoordinatorErrorCode.PRE_SEND_FAILED,
                    message=(
                        "mutation request was proven unsent; an explicit same-key retry is safe"
                    ),
                )
            unknown = self._mark_unknown(operation, str(exc))
            return _failed_result(
                unknown,
                code=CoordinatorErrorCode.UNKNOWN_OUTCOME,
                message="mutation outcome is unknown; retry the same key to reconcile only",
            )
        except asyncio.CancelledError:
            self._mark_unknown(operation, "mutation task cancelled after durable SENDING")
            raise
        except Exception as exc:  # bridge may have crossed the send boundary
            unknown = self._mark_unknown(
                operation,
                f"mutation call did not return a provable outcome: {type(exc).__name__}: {exc}",
            )
            return _failed_result(
                unknown,
                code=CoordinatorErrorCode.UNKNOWN_OUTCOME,
                message="mutation outcome is unknown; retry the same key to reconcile only",
            )

        try:
            report = _parse_mutation_report(
                raw_report,
                plan_id=trusted.plan_id,
                operation_id=operation.operation_id,
                expected_count=len(trusted.placements),
                expected_bridge_build_id=trusted.bridge_build_id,
                expected_catalog_hash=trusted.catalog_hash,
            )
        except BridgeProtocolError as exc:
            unknown = self._mark_unknown(operation, str(exc))
            return _failed_result(
                unknown,
                code=CoordinatorErrorCode.UNKNOWN_OUTCOME,
                message=f"mutation response was not trustworthy: {exc}",
            )
        return self._record_mutation_report(operation, report)

    async def _preflight_new_plan(self, trusted: _TrustedPlan) -> None:
        """Re-bind a new operation to the current selected Shape, read-only."""

        try:
            raw_context = await self._bridge.call(CONTEXT_ENDPOINT)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise CoordinatorError(
                CoordinatorErrorCode.CONTEXT_PREFLIGHT_FAILED,
                f"read-only Workbench context preflight failed: {type(exc).__name__}: {exc}",
            ) from exc
        try:
            context = BridgeContextResponse.model_validate(raw_context)
        except ValidationError as exc:
            raise CoordinatorError(
                CoordinatorErrorCode.CONTEXT_PREFLIGHT_FAILED,
                f"EnfusionMCP_GetContext response failed strict validation: {exc}",
            ) from exc
        if context.status != "ok":
            raise CoordinatorError(
                CoordinatorErrorCode.CONTEXT_PREFLIGHT_FAILED,
                f"EnfusionMCP_GetContext rejected preflight: "
                f"{context.error_code}: {context.message}",
            )

        checks = (
            (
                context.bridge_protocol_version == BRIDGE_PROTOCOL_VERSION,
                "bridge protocol changed after planning",
            ),
            (
                context.bridge_build_id == trusted.bridge_build_id == BRIDGE_BUILD_ID,
                "bridge build changed after planning",
            ),
            (
                context.catalog_hash == trusted.catalog_hash == self._expected_catalog_hash,
                "vegetation catalog changed after planning",
            ),
            (
                context.world_path == trusted.world_path == self._expected_world_path,
                "open world changed after planning",
            ),
            (context.mode == "edit", "WorldEditor is not in normal edit mode"),
            (
                context.current_subscene == trusted.subscene,
                "current subscene changed after planning",
            ),
            (
                context.current_layer_id == trusted.current_layer_id,
                "current layer id changed after planning",
            ),
            (
                context.active_layer_path == trusted.active_layer_path == trusted.target_layer,
                "the immutable target layer is no longer active",
            ),
            (context.selection_count == 1, "exactly one Shape must remain selected"),
            (context.selected_name == trusted.shape_name, "selected Shape name changed"),
            (context.selected_class == trusted.shape_class, "selected Shape class changed"),
            (context.polygon_compatible, "selected object is no longer a Shape"),
            (context.shape_closed, "selected Shape is no longer closed"),
        )
        for matches, message in checks:
            if not matches:
                raise CoordinatorError(CoordinatorErrorCode.STALE_CONTEXT, message)

        points = tuple(
            Vec3(x=point.x, y=point.y, z=point.z) for point in context.shape_points_world
        )
        try:
            polygon = validate_polygon(points)
        except PlannerError as exc:
            raise CoordinatorError(
                CoordinatorErrorCode.STALE_CONTEXT,
                f"selected Shape is no longer a valid polygon: {exc}",
            ) from exc
        if polygon.shape_hash != trusted.shape_hash:
            raise CoordinatorError(
                CoordinatorErrorCode.STALE_CONTEXT,
                "selected Shape world-coordinate hash changed after planning",
            )

    async def _reconcile_locked(
        self,
        trusted: _TrustedPlan,
        operation: OperationRecord,
    ) -> ApplyResult:
        current = self._prepare_for_reconciliation(operation)
        envelope = trusted.envelope(mode="reconcile", operation_id=current.operation_id)
        try:
            raw_report = await self._bridge.call_reconcile(VEGETATION_ENDPOINT, envelope)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return _failed_result(
                current,
                code=CoordinatorErrorCode.RECONCILIATION_FAILED,
                message=f"read-only reconciliation failed: {type(exc).__name__}: {exc}",
                reconciled=True,
            )
        try:
            report = _parse_reconciliation_report(
                raw_report,
                plan_id=trusted.plan_id,
                operation_id=current.operation_id,
                expected_count=len(trusted.placements),
                expected_bridge_build_id=trusted.bridge_build_id,
                expected_catalog_hash=trusted.catalog_hash,
            )
        except BridgeProtocolError as exc:
            return _failed_result(
                current,
                code=CoordinatorErrorCode.BRIDGE_PROTOCOL_ERROR,
                message=str(exc),
                reconciled=True,
            )
        return self._record_reconciliation_report(current, report)

    def _load_plan(self, plan_id: str) -> tuple[PlanRecord, _TrustedPlan]:
        try:
            plan = self._ledger.get_plan(plan_id)
        except PlanNotFoundError as exc:
            raise CoordinatorError(CoordinatorErrorCode.PLAN_NOT_FOUND, str(exc)) from exc
        except PlanValidationError as exc:
            raise CoordinatorError(CoordinatorErrorCode.INVALID_STORED_PLAN, str(exc)) from exc
        try:
            trusted = _trusted_plan_from_record(plan)
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise CoordinatorError(
                CoordinatorErrorCode.INVALID_STORED_PLAN,
                f"stored plan does not satisfy apply schema: {exc}",
            ) from exc
        if trusted.catalog_hash != self._expected_catalog_hash:
            raise CoordinatorError(
                CoordinatorErrorCode.INVALID_STORED_PLAN,
                "stored plan catalog hash does not match the running server",
            )
        if any(placement.prefab not in self._expected_prefabs for placement in trusted.placements):
            raise CoordinatorError(
                CoordinatorErrorCode.INVALID_STORED_PLAN,
                "stored plan contains a prefab outside the running exact allowlist",
            )
        if trusted.world_path != self._expected_world_path:
            raise CoordinatorError(
                CoordinatorErrorCode.INVALID_STORED_PLAN,
                "stored plan world does not match the configured target",
            )
        return plan, trusted

    def _prepare_for_reconciliation(self, operation: OperationRecord) -> OperationRecord:
        if operation.state is OperationState.SENDING:
            return self._ledger.transition_operation(
                operation.operation_id,
                OperationState.UNKNOWN,
                detail="reconciliation replaced unresolved SENDING state",
            )
        if operation.state is OperationState.PLANNED:
            # A crash between bind and SENDING proves that this coordinator did
            # not call mutation.  A repeated invocation remains reconcile-only.
            return self._ledger.transition_operation(
                operation.operation_id,
                OperationState.PRE_SEND_FAILED,
                detail="operation binding existed without a durable send claim",
            )
        return operation

    def _mark_unknown(self, operation: OperationRecord, detail: str) -> OperationRecord:
        current = self._ledger.get_operation(operation.operation_id)
        if current.state is OperationState.SENDING:
            return self._ledger.transition_operation(
                current.operation_id,
                OperationState.UNKNOWN,
                detail=_bounded_diagnostic(detail, limit=MAX_DETAIL_LENGTH),
            )
        return current

    def _record_mutation_report(
        self,
        operation: OperationRecord,
        report: _MutationReport,
    ) -> ApplyResult:
        if report.state is BridgeResponseState.REJECTED:
            rejected = self._ledger.transition_operation(
                operation.operation_id,
                OperationState.PRE_SEND_FAILED,
                expected_state=OperationState.SENDING,
                detail=report.detail,
            )
            return _failed_result(
                rejected,
                code=report.error_code,
                message=report.detail or "Workbench rejected the request before mutation",
            )

        state = {
            BridgeResponseState.COMPLETE: OperationState.APPLIED,
            BridgeResponseState.PARTIAL: OperationState.PARTIAL,
            BridgeResponseState.CHANGED: OperationState.ENTITY_CONFLICT,
            BridgeResponseState.ROLLBACK_VERIFIED: OperationState.ROLLBACK_VERIFIED,
            BridgeResponseState.ROLLBACK_FAILED: OperationState.ROLLBACK_FAILED,
            BridgeResponseState.UNKNOWN: OperationState.UNKNOWN,
        }[report.state]
        updated = self._ledger.transition_operation(
            operation.operation_id,
            state,
            expected_state=OperationState.SENDING,
            detail=report.detail,
        )
        if updated.state is OperationState.APPLIED:
            return ApplyResult(
                plan_id=updated.plan_id,
                operation_id=updated.operation_id,
                state=updated.state,
                created_count=report.created_count,
                reconciled=False,
            )
        code = {
            OperationState.PARTIAL: CoordinatorErrorCode.PARTIAL_OPERATION,
            OperationState.ENTITY_CONFLICT: CoordinatorErrorCode.ENTITY_CONFLICT,
            OperationState.ROLLBACK_VERIFIED: CoordinatorErrorCode.MUTATION_ROLLED_BACK,
            OperationState.ROLLBACK_FAILED: CoordinatorErrorCode.ROLLBACK_FAILED,
            OperationState.UNKNOWN: CoordinatorErrorCode.UNKNOWN_OUTCOME,
        }[updated.state]
        return _failed_result(
            updated,
            code=code,
            message=report.detail or f"Workbench reported {updated.state.value}",
            created_count=(
                0 if updated.state is OperationState.ROLLBACK_VERIFIED else report.created_count
            ),
        )

    def _record_reconciliation_report(
        self,
        operation: OperationRecord,
        report: _ReconciliationReport,
    ) -> ApplyResult:
        if report.classification is ReconciliationClassification.COMPLETE:
            return self._reconcile_complete(operation, report)
        if report.classification is ReconciliationClassification.PARTIAL:
            return self._reconcile_partial(operation, report)
        if report.classification is ReconciliationClassification.CHANGED:
            return self._reconcile_changed(operation, report)
        return self._reconcile_none(operation, report)

    def _reconcile_complete(
        self,
        operation: OperationRecord,
        report: _ReconciliationReport,
    ) -> ApplyResult:
        current = operation
        if current.state in {OperationState.SENDING, OperationState.UNKNOWN}:
            current = self._ledger.transition_operation(
                current.operation_id, OperationState.APPLIED, detail=report.detail
            )
        elif current.state is OperationState.ROLLBACK_FAILED:
            current = self._ledger.transition_operation(
                current.operation_id, OperationState.UNKNOWN, detail=report.detail
            )
            current = self._ledger.transition_operation(
                current.operation_id, OperationState.APPLIED, detail=report.detail
            )
        elif current.state is OperationState.PARTIAL:
            current = self._ledger.transition_operation(
                current.operation_id, OperationState.ENTITY_CONFLICT, detail=report.detail
            )

        if current.state is OperationState.APPLIED:
            return ApplyResult(
                plan_id=current.plan_id,
                operation_id=current.operation_id,
                state=current.state,
                created_count=report.matched_count,
                reconciled=True,
                classification=report.classification,
            )
        return _failed_result(
            current,
            code=CoordinatorErrorCode.ENTITY_CONFLICT,
            message=(
                report.detail
                or "a complete batch exists but contradicts the durable operation history"
            ),
            created_count=report.matched_count,
            reconciled=True,
            classification=report.classification,
        )

    def _reconcile_partial(
        self,
        operation: OperationRecord,
        report: _ReconciliationReport,
    ) -> ApplyResult:
        current = operation
        if current.state in {OperationState.SENDING, OperationState.UNKNOWN}:
            current = self._ledger.transition_operation(
                current.operation_id, OperationState.PARTIAL, detail=report.detail
            )
        elif current.state is OperationState.APPLIED:
            current = self._ledger.transition_operation(
                current.operation_id, OperationState.ENTITY_CONFLICT, detail=report.detail
            )
        elif current.state is OperationState.ROLLBACK_FAILED:
            current = self._ledger.transition_operation(
                current.operation_id, OperationState.UNKNOWN, detail=report.detail
            )
            current = self._ledger.transition_operation(
                current.operation_id, OperationState.PARTIAL, detail=report.detail
            )
        return _failed_result(
            current,
            code=CoordinatorErrorCode.PARTIAL_OPERATION,
            message=report.detail or "only part of the deterministic entity batch exists",
            created_count=report.matched_count,
            reconciled=True,
            classification=report.classification,
        )

    def _reconcile_changed(
        self,
        operation: OperationRecord,
        report: _ReconciliationReport,
    ) -> ApplyResult:
        current = operation
        if current.state is OperationState.ROLLBACK_FAILED:
            current = self._ledger.transition_operation(
                current.operation_id, OperationState.UNKNOWN, detail=report.detail
            )
        if current.state in {
            OperationState.SENDING,
            OperationState.UNKNOWN,
            OperationState.APPLIED,
            OperationState.PARTIAL,
        }:
            current = self._ledger.transition_operation(
                current.operation_id,
                OperationState.ENTITY_CONFLICT,
                detail=report.detail,
            )
        return _failed_result(
            current,
            code=CoordinatorErrorCode.ENTITY_CONFLICT,
            message=report.detail or "deterministic entities exist with changed attributes",
            created_count=report.matched_count,
            reconciled=True,
            classification=report.classification,
        )

    def _reconcile_none(
        self,
        operation: OperationRecord,
        report: _ReconciliationReport,
    ) -> ApplyResult:
        current = operation
        if current.state is OperationState.SENDING:
            current = self._ledger.transition_operation(
                current.operation_id, OperationState.UNKNOWN, detail=report.detail
            )
        if current.state is OperationState.APPLIED:
            current = self._ledger.transition_operation(
                current.operation_id, OperationState.UNDONE, detail=report.detail
            )
        if current.state is OperationState.UNKNOWN:
            code = CoordinatorErrorCode.UNKNOWN_OUTCOME
            message = (
                "no deterministic entities were observed, but the original mutation may still "
                "be executing or may have been undone; the target remains blocked"
            )
        elif current.state is OperationState.PARTIAL:
            code = CoordinatorErrorCode.PARTIAL_OPERATION
            message = "the partial batch is absent, but completion/rollback remains unproved"
        elif current.state is OperationState.ROLLBACK_FAILED:
            code = CoordinatorErrorCode.ROLLBACK_FAILED
            message = "the batch is absent, but the failed rollback/action remains unproved"
        elif current.state is OperationState.PRE_SEND_FAILED:
            code = CoordinatorErrorCode.PRE_SEND_FAILED
            message = report.detail or "no mutation was sent and no deterministic entities exist"
        elif current.state is OperationState.ROLLBACK_VERIFIED:
            code = CoordinatorErrorCode.MUTATION_ROLLED_BACK
            message = report.detail or "rollback is verified; no deterministic entities exist"
        elif current.state is OperationState.ENTITY_CONFLICT:
            code = CoordinatorErrorCode.ENTITY_CONFLICT
            message = report.detail or "prior entity conflict cannot be cleared automatically"
        else:
            code = CoordinatorErrorCode.UNDONE
            message = report.detail or "the applied batch is absent and is treated as undone"
        return _failed_result(
            current,
            code=code,
            message=message,
            reconciled=True,
            classification=report.classification,
        )


def _bounded_diagnostic(message: str, *, limit: int) -> str:
    """Keep untrusted diagnostics from preventing a durable safety transition."""

    if len(message) <= limit:
        return message
    digest = hashlib.sha256(message.encode("utf-8", errors="replace")).hexdigest()
    suffix = f" [truncated sha256={digest}]"
    return message[: limit - len(suffix)] + suffix


def _trusted_plan_from_record(  # noqa: PLR0912, PLR0915 - explicit schema checks
    record: PlanRecord,
) -> _TrustedPlan:
    parsed = json.loads(record.canonical_json)
    root = _mapping(parsed, field="plan")
    schema_version = _integer(
        root.get("schemaVersion"), field="schemaVersion", minimum=PLAN_SCHEMA_VERSION
    )
    if schema_version != PLAN_SCHEMA_VERSION:
        raise ValueError("stored plan schemaVersion is unsupported")
    algorithm_version = _text(root.get("algorithmVersion"), field="algorithmVersion", maximum=128)
    if algorithm_version != ALGORITHM_VERSION:
        raise ValueError("stored plan algorithmVersion is unsupported")

    quantization = _mapping(root.get("quantization"), field="quantization")
    if (
        _integer(
            quantization.get("coordinateMicrometersPerMeter"),
            field="quantization.coordinateMicrometersPerMeter",
            minimum=1,
        )
        != COORDINATE_SCALE
    ):
        raise ValueError("coordinate quantization is incompatible")
    if (
        _integer(
            quantization.get("angleMicrodegreesPerDegree"),
            field="quantization.angleMicrodegreesPerDegree",
            minimum=1,
        )
        != ANGLE_SCALE
    ):
        raise ValueError("angle quantization is incompatible")
    if (
        _integer(
            quantization.get("scalePartsPerUnit"),
            field="quantization.scalePartsPerUnit",
            minimum=1,
        )
        != SCALE_SCALE
    ):
        raise ValueError("scale quantization is incompatible")

    catalog = _mapping(root.get("catalog"), field="catalog")
    catalog_hash = _text(catalog.get("hash"), field="catalog.hash", maximum=64)
    if _HASH_RE.fullmatch(catalog_hash) is None:
        raise ValueError("catalog.hash must be 64 lowercase hexadecimal characters")

    context = _mapping(root.get("context"), field="context")
    world_path = _text(context.get("worldPath"), field="context.worldPath", maximum=512)
    subscene = _integer(context.get("currentSubscene"), field="context.currentSubscene", minimum=0)
    current_layer_id = _integer(
        context.get("currentLayerId"), field="context.currentLayerId", minimum=0
    )
    active_layer_path = _text(
        context.get("activeLayerPath"), field="context.activeLayerPath", maximum=512
    )
    if _text(context.get("mode"), field="context.mode", maximum=32) != "edit":
        raise ValueError("stored plan context was not normal edit mode")
    bridge_protocol_version = _text(
        context.get("bridgeProtocolVersion"),
        field="context.bridgeProtocolVersion",
        maximum=64,
    )
    if bridge_protocol_version != BRIDGE_PROTOCOL_VERSION:
        raise ValueError("plan bridgeProtocolVersion is incompatible")
    bridge_build_id = _text(
        context.get("bridgeBuildId"),
        field="context.bridgeBuildId",
        maximum=128,
    )
    if bridge_build_id != BRIDGE_BUILD_ID:
        raise ValueError("plan bridgeBuildId is incompatible")

    target_layer = _text(root.get("targetLayer"), field="targetLayer", maximum=64)
    if target_layer not in _ALLOWED_LAYERS:
        raise ValueError("targetLayer is not allowlisted")
    if active_layer_path != target_layer:
        raise ValueError("stored activeLayerPath differs from targetLayer")

    shape = _mapping(root.get("shape"), field="shape")
    shape_name = _text(shape.get("name"), field="shape.name", maximum=256)
    shape_class = _text(shape.get("class"), field="shape.class", maximum=256)
    shape_hash = _text(shape.get("hash"), field="shape.hash", maximum=64)
    if _HASH_RE.fullmatch(shape_hash) is None:
        raise ValueError("shape.hash must be 64 lowercase hexadecimal characters")
    shape_values = shape.get("pointsWorldUm")
    if not isinstance(shape_values, list) or not (
        MIN_POLYGON_POINTS <= len(shape_values) <= MAX_POLYGON_VERTICES
    ):
        raise ValueError("shape.pointsWorldUm has invalid cardinality")
    shape_points_world = tuple(
        Vec3(
            x=_coordinate_from_units(
                _mapping(value, field=f"shape.pointsWorldUm[{index}]").get("x"),
                field=f"shape.pointsWorldUm[{index}].x",
            ),
            y=_coordinate_from_units(
                _mapping(value, field=f"shape.pointsWorldUm[{index}]").get("y"),
                field=f"shape.pointsWorldUm[{index}].y",
            ),
            z=_coordinate_from_units(
                _mapping(value, field=f"shape.pointsWorldUm[{index}]").get("z"),
                field=f"shape.pointsWorldUm[{index}].z",
            ),
        )
        for index, value in enumerate(shape_values)
    )
    try:
        stored_polygon = validate_polygon(shape_points_world)
    except PlannerError as exc:
        raise ValueError(f"stored Shape polygon is invalid: {exc}") from exc
    if stored_polygon.shape_hash != shape_hash:
        raise ValueError("stored shape.hash does not match shape.pointsWorldUm")

    constraints = _mapping(root.get("constraints"), field="constraints")
    requested_count = _integer(
        constraints.get("count"),
        field="constraints.count",
        minimum=1,
        maximum=MAX_PLACEMENTS,
    )
    min_spacing_m = (
        _integer(
            constraints.get("minSpacingUm"),
            field="constraints.minSpacingUm",
            minimum=1,
        )
        / COORDINATE_SCALE
    )
    max_slope_deg = (
        _integer(
            constraints.get("maxSlopeMicrodegrees"),
            field="constraints.maxSlopeMicrodegrees",
            minimum=0,
        )
        / ANGLE_SCALE
    )
    scale_min = (
        _integer(
            constraints.get("scaleMinPpm"),
            field="constraints.scaleMinPpm",
            minimum=1,
        )
        / SCALE_SCALE
    )
    scale_max = (
        _integer(
            constraints.get("scaleMaxPpm"),
            field="constraints.scaleMaxPpm",
            minimum=1,
        )
        / SCALE_SCALE
    )
    terrain_y_epsilon = (
        _integer(
            constraints.get("terrainYEpsilonUm"),
            field="constraints.terrainYEpsilonUm",
            minimum=1,
        )
        / COORDINATE_SCALE
    )
    if not 0 < min_spacing_m <= MAX_MIN_SPACING_M:
        raise ValueError("minSpacingM must be in (0, 1000]")
    if not 0 <= max_slope_deg < MAX_SLOPE_DEG:
        raise ValueError("maxSlopeDeg must be in [0, 90)")
    if not 0 < scale_min <= scale_max <= MAX_SCALE:
        raise ValueError("scaleMin/scaleMax must satisfy 0 < min <= max <= 10")
    if not 0 < terrain_y_epsilon <= MAX_TERRAIN_Y_EPSILON:
        raise ValueError("terrainYEpsilon must be in (0, 0.05]")
    placement_values = root.get("placements")
    if not isinstance(placement_values, list) or not 1 <= len(placement_values) <= MAX_PLACEMENTS:
        raise ValueError("placements must contain between 1 and 100 items")
    if len(placement_values) > requested_count:
        raise ValueError("placement count exceeds requested immutable count")

    placements: list[_Placement] = []
    for index, value in enumerate(placement_values):
        placement = _mapping(value, field=f"placements[{index}]")
        position = _mapping(placement.get("positionUm"), field=f"placements[{index}].positionUm")
        placement_index = _integer(
            placement.get("index"),
            field=f"placements[{index}].index",
            minimum=0,
            maximum=MAX_PLACEMENTS - 1,
        )
        if placement_index != index:
            raise ValueError("placement indices must be contiguous and match array order")
        if "entityName" in placement:
            raise ValueError("stored placements must not contain plan-id-derived entityName")
        yaw = (
            _integer(
                placement.get("yawMicrodegrees"),
                field=f"placements[{index}].yawMicrodegrees",
                minimum=0,
                maximum=round(FULL_ROTATION_DEG * ANGLE_SCALE) - 1,
            )
            / ANGLE_SCALE
        )
        scale = (
            _integer(
                placement.get("scalePpm"),
                field=f"placements[{index}].scalePpm",
                minimum=1,
                maximum=round(MAX_SCALE * SCALE_SCALE),
            )
            / SCALE_SCALE
        )
        if not 0 < scale <= MAX_SCALE:
            raise ValueError(f"placements[{index}].scalePpm must represent (0, 10]")
        if not scale_min <= scale <= scale_max:
            raise ValueError(f"placements[{index}].scale is outside plan scale bounds")
        placements.append(
            _Placement(
                index=placement_index,
                prefab=_text(
                    placement.get("prefab"),
                    field=f"placements[{index}].prefab",
                    maximum=512,
                ),
                x=_coordinate_from_units(
                    position.get("x"), field=f"placements[{index}].positionUm.x"
                ),
                y=_coordinate_from_units(
                    position.get("y"), field=f"placements[{index}].positionUm.y"
                ),
                z=_coordinate_from_units(
                    position.get("z"), field=f"placements[{index}].positionUm.z"
                ),
                yaw=yaw,
                scale=scale,
            )
        )
    return _TrustedPlan(
        plan_id=record.plan_id,
        catalog_hash=catalog_hash,
        world_path=world_path,
        bridge_build_id=bridge_build_id,
        subscene=subscene,
        current_layer_id=current_layer_id,
        active_layer_path=active_layer_path,
        target_layer=target_layer,
        shape_name=shape_name,
        shape_class=shape_class,
        shape_hash=shape_hash,
        shape_points_world=shape_points_world,
        min_spacing_m=min_spacing_m,
        max_slope_deg=max_slope_deg,
        scale_min=scale_min,
        scale_max=scale_max,
        terrain_y_epsilon=terrain_y_epsilon,
        placements=tuple(placements),
    )


def _parse_mutation_report(  # noqa: PLR0913 - identity fields stay explicit
    value: JsonValue,
    *,
    plan_id: str,
    operation_id: str,
    expected_count: int,
    expected_bridge_build_id: str,
    expected_catalog_hash: str,
) -> _MutationReport:
    report = _parse_unified_report(
        value,
        mode="create",
        plan_id=plan_id,
        operation_id=operation_id,
        expected_count=expected_count,
        expected_bridge_build_id=expected_bridge_build_id,
        expected_catalog_hash=expected_catalog_hash,
    )
    if report.state is BridgeResponseState.NONE:
        raise BridgeProtocolError("create response must not report NONE")
    if report.state is BridgeResponseState.COMPLETE:
        if report.status != "ok" or report.matching_count != expected_count:
            raise BridgeProtocolError("COMPLETE create must prove the full matching batch")
        if report.created_count not in {0, expected_count}:
            raise BridgeProtocolError(
                "COMPLETE create createdCount must be zero or immutable plan count"
            )
    elif report.status != "error":
        raise BridgeProtocolError("non-COMPLETE create state must have status='error'")
    if report.state is BridgeResponseState.PARTIAL and not (
        0 < report.matching_count < expected_count
    ):
        raise BridgeProtocolError("PARTIAL create must match some but not all entities")
    if report.state is BridgeResponseState.REJECTED and (
        report.matching_count != 0 or report.created_count != 0 or report.entity_names
    ):
        raise BridgeProtocolError("REJECTED create must prove that no entity was affected")
    if report.rollback_verified != (report.state is BridgeResponseState.ROLLBACK_VERIFIED):
        raise BridgeProtocolError("rollbackVerified is inconsistent with create state")
    if report.state is BridgeResponseState.ROLLBACK_VERIFIED and report.matching_count != 0:
        raise BridgeProtocolError("ROLLBACK_VERIFIED must prove zero remaining matching entities")
    return _MutationReport(
        state=report.state,
        error_code=report.error_code,
        created_count=report.created_count,
        detail=report.detail,
    )


def _parse_reconciliation_report(  # noqa: PLR0913 - identity fields stay explicit
    value: JsonValue,
    *,
    plan_id: str,
    operation_id: str,
    expected_count: int,
    expected_bridge_build_id: str,
    expected_catalog_hash: str,
) -> _ReconciliationReport:
    report = _parse_unified_report(
        value,
        mode="reconcile",
        plan_id=plan_id,
        operation_id=operation_id,
        expected_count=expected_count,
        expected_bridge_build_id=expected_bridge_build_id,
        expected_catalog_hash=expected_catalog_hash,
    )
    if report.state not in {
        BridgeResponseState.COMPLETE,
        BridgeResponseState.PARTIAL,
        BridgeResponseState.CHANGED,
        BridgeResponseState.NONE,
    }:
        raise BridgeProtocolError("reconciliation response has a non-reconciliation state")
    classification = ReconciliationClassification(report.state.value)
    if classification in {
        ReconciliationClassification.COMPLETE,
        ReconciliationClassification.NONE,
    }:
        if report.status != "ok":
            raise BridgeProtocolError("COMPLETE/NONE reconciliation must have status='ok'")
    elif report.status != "error":
        raise BridgeProtocolError("PARTIAL/CHANGED reconciliation must have status='error'")
    if report.created_count != 0 or report.rollback_verified or report.entity_names:
        raise BridgeProtocolError("read-only reconciliation reported mutation effects")
    matched_count = report.matching_count
    if classification is ReconciliationClassification.COMPLETE and matched_count != expected_count:
        raise BridgeProtocolError("COMPLETE must match every planned entity")
    if classification is ReconciliationClassification.PARTIAL and not (
        0 < matched_count < expected_count
    ):
        raise BridgeProtocolError("PARTIAL must match some but not all planned entities")
    if classification is ReconciliationClassification.NONE and matched_count != 0:
        raise BridgeProtocolError("NONE must report matchingCount=0")
    return _ReconciliationReport(
        classification=classification,
        matched_count=matched_count,
        detail=report.detail,
    )


def _parse_unified_report(  # noqa: PLR0913 - explicit operation identity checks
    value: JsonValue,
    *,
    mode: str,
    plan_id: str,
    operation_id: str,
    expected_count: int,
    expected_bridge_build_id: str,
    expected_catalog_hash: str,
) -> _UnifiedBridgeReport:
    try:
        report = BridgeVegetationApplyResponse.model_validate(value)
    except ValidationError as exc:
        raise BridgeProtocolError(str(exc)) from exc
    if report.bridge_protocol_version != BRIDGE_PROTOCOL_VERSION:
        raise BridgeProtocolError("bridgeProtocolVersion is incompatible")
    if (
        report.bridge_build_id != expected_bridge_build_id
        or report.bridge_build_id != BRIDGE_BUILD_ID
    ):
        raise BridgeProtocolError("bridgeBuildId is incompatible")
    if report.catalog_hash != expected_catalog_hash:
        raise BridgeProtocolError("catalogHash differs from the immutable plan")
    if report.plan_id != plan_id or report.operation_id != operation_id:
        raise BridgeProtocolError("bridge response operation/plan binding does not match request")
    if report.mode != mode:
        raise BridgeProtocolError("bridge response mode does not match request")
    if report.expected_count != expected_count:
        raise BridgeProtocolError("bridge expectedCount differs from immutable plan")
    if report.matching_count > expected_count or report.created_count > expected_count:
        raise BridgeProtocolError("bridge entity counts exceed immutable plan count")
    entity_names = tuple(report.entity_names)
    if len(entity_names) != report.created_count:
        raise BridgeProtocolError("entityNames cardinality differs from createdCount")
    expected_names = tuple(f"EnfusionMCP_{plan_id}_{index}" for index in range(expected_count))
    if entity_names and entity_names != expected_names[: len(entity_names)]:
        raise BridgeProtocolError("bridge entityNames do not match deterministic plan names")
    return _UnifiedBridgeReport(
        status=report.status,
        error_code=report.error_code,
        message=report.message,
        state=BridgeResponseState(report.state),
        expected_count=report.expected_count,
        matching_count=report.matching_count,
        created_count=report.created_count,
        rollback_verified=report.rollback_verified,
        entity_names=entity_names,
    )


def _mapping(value: object, *, field: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise TypeError(f"{field} must be a JSON object with string keys")
    return value


def _text(value: object, *, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or "\x00" in value:
        raise ValueError(f"{field} must be non-empty trimmed text without NUL")
    if len(value) > maximum:
        raise ValueError(f"{field} exceeds maximum length {maximum}")
    return value


def _integer(
    value: object,
    *,
    field: str,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{field} is outside its permitted range")
    return value


def _coordinate_from_units(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be a fixed-point integer")
    if abs(value) > MAX_ABS_COORDINATE_UNITS:
        raise ValueError(f"{field} exceeds bounded Workbench coordinates")
    return value / COORDINATE_SCALE


def _validated_key(value: str) -> str:
    try:
        return validate_idempotency_key(value)
    except OperationConflictError as exc:
        raise CoordinatorError(CoordinatorErrorCode.OPERATION_CONFLICT, str(exc)) from exc


def _require_same_binding(operation: OperationRecord, key: str) -> None:
    if operation.idempotency_key != key or operation.operation_id != key:
        raise CoordinatorError(
            CoordinatorErrorCode.OPERATION_CONFLICT,
            "plan is already bound to a different idempotency key",
        )


def _failed_result(  # noqa: PLR0913 - typed result fields are intentionally explicit
    operation: OperationRecord,
    *,
    code: CoordinatorErrorCode | str,
    message: str,
    created_count: int = 0,
    reconciled: bool = False,
    classification: ReconciliationClassification | None = None,
) -> ApplyResult:
    return ApplyResult(
        plan_id=operation.plan_id,
        operation_id=operation.operation_id,
        state=operation.state,
        created_count=created_count,
        reconciled=reconciled,
        classification=classification,
        error_code=code,
        message=message,
    )
