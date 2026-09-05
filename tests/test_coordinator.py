from __future__ import annotations

import asyncio
import multiprocessing
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from multiprocessing.connection import Connection
from pathlib import Path

import pytest

from enfusion_mcp_rj.bridge_models import (
    BRIDGE_BUILD_ID,
    BRIDGE_PROTOCOL_VERSION,
    BridgeVegetationApplyRequest,
)
from enfusion_mcp_rj.coordinator import (
    VEGETATION_ENDPOINT,
    ApplyCoordinator,
    BridgeClient,
    CoordinatorError,
    CoordinatorErrorCode,
    ReconciliationClassification,
)
from enfusion_mcp_rj.ledger import Ledger, OperationState
from enfusion_mcp_rj.locking import TargetLockManager, TargetScope
from enfusion_mcp_rj.models import Vec3
from enfusion_mcp_rj.net_api import (
    JsonValue,
    NetApiClient,
    NetApiError,
    NetApiErrorCode,
    NetApiPhase,
)
from enfusion_mcp_rj.planner import ALGORITHM_VERSION, validate_polygon

_KEY_ONE = "11111111-1111-4111-8111-111111111111"
_KEY_TWO = "22222222-2222-4222-8222-222222222222"
_CREATED = datetime(2020, 1, 1, tzinfo=UTC)
_EXPIRES = datetime(2100, 1, 1, tzinfo=UTC)
_CATALOG_HASH = "c" * 64
_ALLOWED_PREFABS = frozenset(
    f"{{A{index:015X}}}Prefabs/Vegetation/Bush_{index}.et" for index in range(100)
)
_SHAPE_NAME = "Посадочная Shape"
_SHAPE_CLASS = "ShapeEntity"
_SHAPE_POINTS = (
    Vec3(x=0.0, y=4.0, z=0.0),
    Vec3(x=30.0, y=4.0, z=0.0),
    Vec3(x=30.0, y=4.0, z=30.0),
    Vec3(x=0.0, y=4.0, z=30.0),
)
_SHAPE_HASH = validate_polygon(_SHAPE_POINTS).shape_hash


def _plan_payload(*, seed: int = 0, count: int = 2) -> dict[str, object]:
    placements: list[dict[str, object]] = [
        {
            "index": index,
            "prefab": f"{{A{index:015X}}}Prefabs/Vegetation/Bush_{index}.et",
            "positionUm": {
                "x": 10_000_000 + index * 3_000_000,
                "y": 4_250_000,
                "z": 20_000_000,
            },
            "yawMicrodegrees": 45_000_000 + index * 1_000_000,
            "scalePpm": 900_000 + index * 100_000,
        }
        for index in range(count)
    ]
    return {
        "algorithmVersion": ALGORITHM_VERSION,
        "schemaVersion": 1,
        "catalog": {"hash": _CATALOG_HASH, "version": "synthetic-v1"},
        "context": {
            "worldPath": "$thenewRJ:rj.ent",
            "currentSubscene": 7,
            "currentLayerId": 42,
            "activeLayerPath": "MCP_Preview",
            "mode": "edit",
            "bridgeProtocolVersion": BRIDGE_PROTOCOL_VERSION,
            "bridgeBuildId": BRIDGE_BUILD_ID,
        },
        "shape": {
            "name": _SHAPE_NAME,
            "class": _SHAPE_CLASS,
            "hash": _SHAPE_HASH,
            "pointsWorldUm": [
                {
                    "x": round(point.x * 1_000_000),
                    "y": round(point.y * 1_000_000),
                    "z": round(point.z * 1_000_000),
                }
                for point in _SHAPE_POINTS
            ],
        },
        "targetLayer": "MCP_Preview",
        "constraints": {
            "count": count,
            "minSpacingUm": 2_500_000,
            "maxSlopeMicrodegrees": 28_000_000,
            "scaleMinPpm": 800_000,
            "scaleMaxPpm": 1_200_000,
            "terrainYEpsilonUm": 20_000,
        },
        "quantization": {
            "coordinateMicrometersPerMeter": 1_000_000,
            "angleMicrodegreesPerDegree": 1_000_000,
            "scalePartsPerUnit": 1_000_000,
        },
        "seed": seed,
        "placements": placements,
        "plannerOnlyMetadata": {"rejections": {"slope": 3}},
    }


def _components(
    root: Path,
) -> tuple[Ledger, TargetLockManager, TargetScope]:
    state_dir = root / "state"
    ledger = Ledger(state_dir, allowed_root=root)
    locks = TargetLockManager(state_dir, allowed_root=root)
    scope = TargetScope.derive(
        workbench_host="127.0.0.1",
        workbench_port=5775,
        project_host_path=root / "active-project",
        project_engine_path=r"C:\users\steamuser\Documents\My Games\new_rj",
        world="$thenewRJ:rj.ent",
    )
    return ledger, locks, scope


def _store_plan(ledger: Ledger, *, seed: int = 0, count: int = 2) -> str:
    return ledger.save_plan(
        _plan_payload(seed=seed, count=count),
        created_at=_CREATED,
        expires_at=_EXPIRES,
    ).plan_id


def _set_plan_field(plan: dict[str, object], path: str, value: object) -> None:
    section, separator, field = path.partition(".")
    if not separator:
        plan[section] = value
        return
    nested = plan.get(section)
    if not isinstance(nested, dict):
        raise TypeError(f"test plan section {section} is not an object")
    nested[field] = value


def _required_string(params: Mapping[str, JsonValue], key: str) -> str:
    value = params[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} is not a string")
    return value


def _required_integer(params: Mapping[str, JsonValue], key: str) -> int:
    value = params[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} is not an integer")
    return value


def _context_response() -> dict[str, JsonValue]:
    return {
        "status": "ok",
        "errorCode": "",
        "message": "context",
        "bridgeProtocolVersion": BRIDGE_PROTOCOL_VERSION,
        "bridgeBuildId": BRIDGE_BUILD_ID,
        "catalogHash": _CATALOG_HASH,
        "worldPath": "$thenewRJ:rj.ent",
        "mode": "edit",
        "currentSubscene": 7,
        "currentLayerId": 42,
        "activeLayerPath": "MCP_Preview",
        "terrainBounds": None,
        "selectionCount": 1,
        "selectedName": _SHAPE_NAME,
        "selectedClass": _SHAPE_CLASS,
        "polygonCompatible": True,
        "shapeClosed": True,
        "shapePointsWorld": [{"x": point.x, "y": point.y, "z": point.z} for point in _SHAPE_POINTS],
    }


class ScriptedBridge:
    def __init__(self) -> None:
        self.read_calls: list[tuple[str, Mapping[str, JsonValue] | None]] = []
        self.mutation_calls: list[dict[str, JsonValue]] = []
        self.reconcile_calls: list[dict[str, JsonValue]] = []
        self.context_error: Exception | None = None
        self.context_response_override: JsonValue | None = None
        self.mutation_error: Exception | None = None
        self.reconcile_error: Exception | None = None
        self.mutation_state = "COMPLETE"
        self.mutation_error_code: str | None = None
        self.response_bridge_build_id: str | None = None
        self.response_catalog_hash: str | None = None
        self.mutation_created_count: int | None = None
        self.mutation_response_override: JsonValue | None = None
        self.reconciliation = ReconciliationClassification.COMPLETE
        self.reconcile_matched_count: int | None = None
        self.delay_seconds = 0.0
        self.active_mutations = 0
        self.maximum_active_mutations = 0
        self.mutation_entered = asyncio.Event()

    async def call(
        self,
        api_func: str,
        params: Mapping[str, JsonValue] | None = None,
    ) -> JsonValue:
        self.read_calls.append((api_func, params))
        if api_func != "RJMCP_GetContext":
            raise ValueError(f"unexpected read endpoint: {api_func}")
        if self.context_error is not None:
            raise self.context_error
        if self.context_response_override is not None:
            return self.context_response_override
        return _context_response()

    async def call_mutation(
        self,
        api_func: str,
        params: Mapping[str, JsonValue] | None = None,
    ) -> JsonValue:
        assert api_func == VEGETATION_ENDPOINT
        if params is None:
            raise TypeError("coordinator must provide mutation params")
        copied = dict(params)
        self.mutation_calls.append(copied)
        self.active_mutations += 1
        self.maximum_active_mutations = max(self.maximum_active_mutations, self.active_mutations)
        self.mutation_entered.set()
        try:
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
            if self.mutation_error is not None:
                raise self.mutation_error
            if self.mutation_response_override is not None:
                return self.mutation_response_override
            expected = _required_integer(copied, "count")
            created = (
                expected if self.mutation_created_count is None else self.mutation_created_count
            )
            status = "ok" if self.mutation_state == "COMPLETE" else "error"
            matching = (
                expected
                if self.mutation_state == "COMPLETE"
                else created
                if self.mutation_state == "PARTIAL"
                else 0
            )
            names_value = copied["entityNames"]
            if not isinstance(names_value, list):
                raise TypeError("entityNames is not an array")
            return {
                "status": status,
                "errorCode": (
                    "" if status == "ok" else self.mutation_error_code or self.mutation_state
                ),
                "message": f"scripted {self.mutation_state}",
                "bridgeProtocolVersion": BRIDGE_PROTOCOL_VERSION,
                "bridgeBuildId": self.response_bridge_build_id or copied["bridgeBuildId"],
                "catalogHash": self.response_catalog_hash or copied["catalogHash"],
                "state": self.mutation_state,
                "planId": _required_string(copied, "planId"),
                "operationId": _required_string(copied, "operationId"),
                "mode": "create",
                "expectedCount": expected,
                "matchingCount": matching,
                "createdCount": created,
                "rollbackVerified": self.mutation_state == "ROLLBACK_VERIFIED",
                "entityNames": names_value[:created],
            }
        finally:
            self.active_mutations -= 1

    async def call_reconcile(
        self,
        api_func: str,
        params: Mapping[str, JsonValue],
    ) -> JsonValue:
        assert api_func == VEGETATION_ENDPOINT
        copied = dict(params)
        self.reconcile_calls.append(copied)
        if self.reconcile_error is not None:
            raise self.reconcile_error
        expected = _required_integer(copied, "count")
        if self.reconcile_matched_count is None:
            matched = {
                ReconciliationClassification.COMPLETE: expected,
                ReconciliationClassification.PARTIAL: max(1, expected - 1),
                ReconciliationClassification.CHANGED: expected,
                ReconciliationClassification.NONE: 0,
            }[self.reconciliation]
        else:
            matched = self.reconcile_matched_count
        ok = self.reconciliation in {
            ReconciliationClassification.COMPLETE,
            ReconciliationClassification.NONE,
        }
        return {
            "status": "ok" if ok else "error",
            "errorCode": "" if ok else self.reconciliation.value,
            "message": f"scripted {self.reconciliation.value}",
            "bridgeProtocolVersion": BRIDGE_PROTOCOL_VERSION,
            "bridgeBuildId": self.response_bridge_build_id or copied["bridgeBuildId"],
            "catalogHash": self.response_catalog_hash or copied["catalogHash"],
            "planId": _required_string(copied, "planId"),
            "operationId": _required_string(copied, "operationId"),
            "mode": "reconcile",
            "state": self.reconciliation.value,
            "expectedCount": expected,
            "matchingCount": matched,
            "createdCount": 0,
            "rollbackVerified": False,
            "entityNames": [],
        }


def _coordinator(
    root: Path,
    bridge: BridgeClient,
) -> tuple[ApplyCoordinator, Ledger]:
    ledger, locks, scope = _components(root)
    return (
        ApplyCoordinator(
            ledger=ledger,
            lock_manager=locks,
            target_scope=scope,
            bridge=bridge,
            expected_catalog_hash=_CATALOG_HASH,
            expected_prefabs=_ALLOWED_PREFABS,
            expected_world_path="$thenewRJ:rj.ent",
        ),
        ledger,
    )


class ProcessFileBridge:
    def __init__(self, log_path: Path, marker_path: Path, *, delay: float = 0.0) -> None:
        self.log_path = log_path
        self.marker_path = marker_path
        self.delay = delay

    def _log(self, entry: str) -> None:
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(f"{entry}\n")

    async def call(
        self,
        api_func: str,
        params: Mapping[str, JsonValue] | None = None,
    ) -> JsonValue:
        if api_func != "RJMCP_GetContext" or params is not None:
            raise ValueError("unexpected process bridge read request")
        self._log("context")
        return _context_response()

    async def call_mutation(
        self,
        api_func: str,
        params: Mapping[str, JsonValue] | None = None,
    ) -> JsonValue:
        if params is None:
            raise TypeError("missing mutation params")
        self._log("mutation:start")
        if self.delay:
            await asyncio.sleep(self.delay)
        plan_id = _required_string(params, "planId")
        operation_id = _required_string(params, "operationId")
        count = _required_integer(params, "count")
        self.marker_path.write_text(f"{plan_id}\n{operation_id}\n{count}\n", encoding="utf-8")
        self._log("mutation:end")
        names_value = params["entityNames"]
        if not isinstance(names_value, list):
            raise TypeError("entityNames is not an array")
        return {
            "status": "ok",
            "errorCode": "",
            "message": "created",
            "bridgeProtocolVersion": BRIDGE_PROTOCOL_VERSION,
            "bridgeBuildId": params["bridgeBuildId"],
            "catalogHash": params["catalogHash"],
            "state": "COMPLETE",
            "planId": plan_id,
            "operationId": operation_id,
            "mode": "create",
            "expectedCount": count,
            "matchingCount": count,
            "createdCount": count,
            "rollbackVerified": False,
            "entityNames": names_value,
        }

    async def call_reconcile(
        self,
        api_func: str,
        params: Mapping[str, JsonValue],
    ) -> JsonValue:
        self._log("reconcile")
        expected = _required_integer(params, "count")
        complete = self.marker_path.exists()
        return {
            "status": "ok",
            "errorCode": "",
            "message": "reconciled",
            "bridgeProtocolVersion": BRIDGE_PROTOCOL_VERSION,
            "bridgeBuildId": params["bridgeBuildId"],
            "catalogHash": params["catalogHash"],
            "planId": _required_string(params, "planId"),
            "operationId": _required_string(params, "operationId"),
            "mode": "reconcile",
            "state": "COMPLETE" if complete else "NONE",
            "expectedCount": expected,
            "matchingCount": expected if complete else 0,
            "createdCount": 0,
            "rollbackVerified": False,
            "entityNames": [],
        }


class CrashingProcessBridge(ProcessFileBridge):
    async def call_mutation(
        self,
        api_func: str,
        params: Mapping[str, JsonValue] | None = None,
    ) -> JsonValue:
        if params is None:
            raise TypeError("missing mutation params")
        self._log("mutation:crash")
        plan_id = _required_string(params, "planId")
        operation_id = _required_string(params, "operationId")
        count = _required_integer(params, "count")
        self.marker_path.write_text(f"{plan_id}\n{operation_id}\n{count}\n", encoding="utf-8")
        os._exit(23)


def _process_apply(  # noqa: PLR0913, PLR0917 - multiprocessing target uses primitives
    root_text: str,
    plan_id: str,
    key: str,
    log_text: str,
    marker_text: str,
    connection: Connection,
) -> None:
    root = Path(root_text)
    bridge = ProcessFileBridge(Path(log_text), Path(marker_text), delay=0.15)
    coordinator, _ledger = _coordinator(root, bridge)
    connection.send("ready")
    connection.recv()
    try:
        result = asyncio.run(coordinator.apply(plan_id, key))
    except CoordinatorError as exc:
        connection.send(("error", exc.code.value))
    else:
        connection.send(
            ("result", result.state.value, result.reconciled, result.error_code is None)
        )
    finally:
        connection.close()


def _process_crash_during_apply(
    root_text: str,
    plan_id: str,
    key: str,
    log_text: str,
    marker_text: str,
) -> None:
    root = Path(root_text)
    bridge = CrashingProcessBridge(Path(log_text), Path(marker_text))
    coordinator, _ledger = _coordinator(root, bridge)
    asyncio.run(coordinator.apply(plan_id, key))


async def test_first_apply_uses_exact_trusted_flat_envelope_and_persists_applied(
    tmp_path: Path,
) -> None:
    bridge = ScriptedBridge()
    coordinator, ledger = _coordinator(tmp_path, bridge)
    plan_id = _store_plan(ledger)

    result = await coordinator.apply(plan_id, _KEY_ONE)

    assert result.ok
    assert result.state is OperationState.APPLIED
    assert result.created_count == 2
    assert not result.reconciled
    assert bridge.read_calls == [("RJMCP_GetContext", None)]
    assert len(bridge.mutation_calls) == 1
    assert bridge.reconcile_calls == []
    envelope = bridge.mutation_calls[0]
    assert set(envelope) == {
        "mode",
        "planId",
        "operationId",
        "bridgeBuildId",
        "catalogHash",
        "worldPath",
        "subscene",
        "targetLayer",
        "count",
        "minSpacingM",
        "maxSlopeDeg",
        "scaleMin",
        "scaleMax",
        "terrainYEpsilon",
        "prefabs",
        "entityNames",
        "x",
        "y",
        "z",
        "yaw",
        "scale",
    }
    assert envelope["mode"] == "create"
    assert envelope["planId"] == plan_id
    assert envelope["operationId"] == _KEY_ONE
    assert envelope["operationId"] == ledger.get_operation(_KEY_ONE).idempotency_key
    assert envelope["bridgeBuildId"] == BRIDGE_BUILD_ID
    assert envelope["catalogHash"] == _CATALOG_HASH
    assert envelope["worldPath"] == "$thenewRJ:rj.ent"
    assert envelope["subscene"] == 7
    assert envelope["targetLayer"] == "MCP_Preview"
    assert envelope["count"] == 2
    assert envelope["minSpacingM"] == 2.5
    assert envelope["maxSlopeDeg"] == 28.0
    assert envelope["scaleMin"] == 0.8
    assert envelope["scaleMax"] == 1.2
    assert envelope["terrainYEpsilon"] == 0.02
    assert envelope["x"] == [10.0, 13.0]
    assert envelope["entityNames"] == [
        f"RJMCP_{plan_id}_0",
        f"RJMCP_{plan_id}_1",
    ]
    assert "placements" not in envelope
    assert "plannerOnlyMetadata" not in envelope
    assert (
        BridgeVegetationApplyRequest.model_validate(envelope).model_dump(by_alias=True) == envelope
    )
    assert ledger.get_plan(plan_id).state is OperationState.APPLIED


async def test_lost_mutation_response_becomes_unknown_then_same_key_reconciles_only(
    tmp_path: Path,
) -> None:
    bridge = ScriptedBridge()
    bridge.mutation_error = TimeoutError("response lost")
    coordinator, ledger = _coordinator(tmp_path, bridge)
    plan_id = _store_plan(ledger)

    unknown = await coordinator.apply(plan_id, _KEY_ONE)
    bridge.mutation_error = None
    recovered = await coordinator.apply(plan_id, _KEY_ONE)

    assert unknown.state is OperationState.UNKNOWN
    assert unknown.error_code is CoordinatorErrorCode.UNKNOWN_OUTCOME
    assert recovered.ok
    assert recovered.reconciled
    assert recovered.classification is ReconciliationClassification.COMPLETE
    assert len(bridge.mutation_calls) == 1
    assert len(bridge.reconcile_calls) == 1
    assert bridge.reconcile_calls[0]["mode"] == "reconcile"
    assert ledger.get_operation(_KEY_ONE).state is OperationState.APPLIED


async def test_new_key_for_bound_unknown_plan_conflicts_without_bridge_call(
    tmp_path: Path,
) -> None:
    bridge = ScriptedBridge()
    bridge.mutation_error = ConnectionError("lost")
    coordinator, ledger = _coordinator(tmp_path, bridge)
    plan_id = _store_plan(ledger)
    await coordinator.apply(plan_id, _KEY_ONE)

    with pytest.raises(CoordinatorError) as captured:
        await coordinator.apply(plan_id, _KEY_TWO)

    assert captured.value.code is CoordinatorErrorCode.OPERATION_CONFLICT
    assert len(bridge.mutation_calls) == 1
    assert bridge.reconcile_calls == []


async def test_missing_and_expired_plans_fail_before_any_bridge_call(tmp_path: Path) -> None:
    bridge = ScriptedBridge()
    coordinator, ledger = _coordinator(tmp_path, bridge)

    with pytest.raises(CoordinatorError) as missing:
        await coordinator.apply("a" * 64, _KEY_ONE)
    assert missing.value.code is CoordinatorErrorCode.PLAN_NOT_FOUND

    expired = ledger.save_plan(
        _plan_payload(),
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
        expires_at=datetime(2020, 1, 2, tzinfo=UTC),
    )
    with pytest.raises(CoordinatorError) as stale:
        await coordinator.apply(expired.plan_id, _KEY_ONE)
    assert stale.value.code is CoordinatorErrorCode.PLAN_EXPIRED
    assert ledger.get_operation_for_plan(expired.plan_id) is None
    assert bridge.read_calls == []
    assert bridge.mutation_calls == []
    assert bridge.reconcile_calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("selectionCount", 0),
        ("selectedName", "Другая Shape"),
        ("selectedClass", "GenericEntity"),
        ("polygonCompatible", False),
        ("shapeClosed", False),
        ("mode", "game"),
        ("currentSubscene", 8),
        ("currentLayerId", 43),
        ("activeLayerPath", "MCP_Vegetation"),
        ("catalogHash", "d" * 64),
        ("worldPath", "$other:world.ent"),
        ("bridgeProtocolVersion", "old-protocol"),
        ("bridgeBuildId", "old-build"),
    ],
)
async def test_stale_new_apply_context_never_binds_or_mutates(
    tmp_path: Path, field: str, value: JsonValue
) -> None:
    bridge = ScriptedBridge()
    stale_context = _context_response()
    stale_context[field] = value
    bridge.context_response_override = stale_context
    coordinator, ledger = _coordinator(tmp_path, bridge)
    plan_id = _store_plan(ledger)

    with pytest.raises(CoordinatorError) as captured:
        await coordinator.apply(plan_id, _KEY_ONE)

    assert captured.value.code is CoordinatorErrorCode.STALE_CONTEXT
    assert ledger.get_operation_for_plan(plan_id) is None
    assert bridge.read_calls == [("RJMCP_GetContext", None)]
    assert bridge.mutation_calls == []
    assert bridge.reconcile_calls == []


async def test_changed_shape_world_points_fail_hash_preflight_before_binding(
    tmp_path: Path,
) -> None:
    bridge = ScriptedBridge()
    stale_context = _context_response()
    stale_context["shapePointsWorld"] = [
        {"x": 0.0, "y": 4.0, "z": 0.0},
        {"x": 30.0, "y": 4.0, "z": 0.0},
        {"x": 31.0, "y": 4.0, "z": 30.0},
        {"x": 0.0, "y": 4.0, "z": 30.0},
    ]
    bridge.context_response_override = stale_context
    coordinator, ledger = _coordinator(tmp_path, bridge)
    plan_id = _store_plan(ledger)

    with pytest.raises(CoordinatorError) as captured:
        await coordinator.apply(plan_id, _KEY_ONE)

    assert captured.value.code is CoordinatorErrorCode.STALE_CONTEXT
    assert "hash changed" in str(captured.value)
    assert ledger.get_operation_for_plan(plan_id) is None
    assert len(bridge.read_calls) == 1
    assert bridge.mutation_calls == []


@pytest.mark.parametrize("failure_kind", ["malformed", "handler_error", "transport"])
async def test_context_preflight_failures_are_typed_and_never_bind(
    tmp_path: Path, failure_kind: str
) -> None:
    bridge = ScriptedBridge()
    if failure_kind == "malformed":
        malformed = _context_response()
        malformed["unexpectedField"] = True
        bridge.context_response_override = malformed
    elif failure_kind == "handler_error":
        rejected = _context_response()
        rejected["status"] = "error"
        rejected["errorCode"] = "EDITOR_BUSY"
        rejected["message"] = "editor busy"
        bridge.context_response_override = rejected
    else:
        bridge.context_error = ConnectionError("read-only context unavailable")
    coordinator, ledger = _coordinator(tmp_path, bridge)
    plan_id = _store_plan(ledger)

    with pytest.raises(CoordinatorError) as captured:
        await coordinator.apply(plan_id, _KEY_ONE)

    assert captured.value.code is CoordinatorErrorCode.CONTEXT_PREFLIGHT_FAILED
    assert ledger.get_operation_for_plan(plan_id) is None
    assert len(bridge.read_calls) == 1
    assert bridge.mutation_calls == []
    assert bridge.reconcile_calls == []


async def test_existing_same_key_reconcile_ignores_current_selection_preflight(
    tmp_path: Path,
) -> None:
    bridge = ScriptedBridge()
    bridge.mutation_error = TimeoutError("response lost")
    coordinator, ledger = _coordinator(tmp_path, bridge)
    plan_id = _store_plan(ledger)
    unknown = await coordinator.apply(plan_id, _KEY_ONE)
    assert unknown.state is OperationState.UNKNOWN
    assert len(bridge.read_calls) == 1
    stale_context = _context_response()
    stale_context["selectionCount"] = 0
    bridge.context_response_override = stale_context
    bridge.mutation_error = None

    reconciled = await coordinator.apply(plan_id, _KEY_ONE)

    assert reconciled.ok
    assert reconciled.reconciled
    assert len(bridge.read_calls) == 1
    assert len(bridge.mutation_calls) == 1
    assert len(bridge.reconcile_calls) == 1


@pytest.mark.parametrize(
    ("classification", "expected_state", "expected_code"),
    [
        (ReconciliationClassification.COMPLETE, OperationState.APPLIED, None),
        (
            ReconciliationClassification.PARTIAL,
            OperationState.PARTIAL,
            CoordinatorErrorCode.PARTIAL_OPERATION,
        ),
        (
            ReconciliationClassification.CHANGED,
            OperationState.ENTITY_CONFLICT,
            CoordinatorErrorCode.ENTITY_CONFLICT,
        ),
        (
            ReconciliationClassification.NONE,
            OperationState.UNDONE,
            CoordinatorErrorCode.UNDONE,
        ),
    ],
)
async def test_unknown_operation_reconciliation_classifies_without_second_create(
    tmp_path: Path,
    classification: ReconciliationClassification,
    expected_state: OperationState,
    expected_code: CoordinatorErrorCode | None,
) -> None:
    bridge = ScriptedBridge()
    bridge.mutation_error = RuntimeError("fault injection after possible send")
    bridge.reconciliation = classification
    coordinator, ledger = _coordinator(tmp_path, bridge)
    plan_id = _store_plan(ledger, count=3)
    await coordinator.apply(plan_id, _KEY_ONE)
    bridge.mutation_error = None

    result = await coordinator.apply(plan_id, _KEY_ONE)

    assert result.state is expected_state
    assert result.error_code is expected_code
    assert result.reconciled
    assert len(bridge.mutation_calls) == 1
    assert len(bridge.reconcile_calls) == 1


async def test_reconciliation_none_changes_previously_applied_to_undone(tmp_path: Path) -> None:
    bridge = ScriptedBridge()
    coordinator, ledger = _coordinator(tmp_path, bridge)
    plan_id = _store_plan(ledger)
    applied = await coordinator.apply(plan_id, _KEY_ONE)
    assert applied.ok
    bridge.reconciliation = ReconciliationClassification.NONE

    undone = await coordinator.apply(plan_id, _KEY_ONE)

    assert undone.state is OperationState.UNDONE
    assert undone.error_code is CoordinatorErrorCode.UNDONE
    assert undone.reconciled
    assert len(bridge.mutation_calls) == 1
    assert ledger.get_operation(_KEY_ONE).state is OperationState.UNDONE


@pytest.mark.parametrize(
    ("bridge_state", "expected_state", "expected_code", "created_count"),
    [
        (
            "ROLLBACK_VERIFIED",
            OperationState.ROLLBACK_VERIFIED,
            CoordinatorErrorCode.MUTATION_ROLLED_BACK,
            1,
        ),
        (
            "ROLLBACK_FAILED",
            OperationState.ROLLBACK_FAILED,
            CoordinatorErrorCode.ROLLBACK_FAILED,
            1,
        ),
        (
            "PARTIAL",
            OperationState.PARTIAL,
            CoordinatorErrorCode.PARTIAL_OPERATION,
            1,
        ),
        (
            "CHANGED",
            OperationState.ENTITY_CONFLICT,
            CoordinatorErrorCode.ENTITY_CONFLICT,
            0,
        ),
        (
            "UNKNOWN",
            OperationState.UNKNOWN,
            CoordinatorErrorCode.UNKNOWN_OUTCOME,
            0,
        ),
    ],
)
async def test_mutation_report_persists_rollback_and_fault_states(
    tmp_path: Path,
    bridge_state: str,
    expected_state: OperationState,
    expected_code: CoordinatorErrorCode,
    created_count: int,
) -> None:
    bridge = ScriptedBridge()
    bridge.mutation_state = bridge_state
    bridge.mutation_created_count = created_count
    coordinator, ledger = _coordinator(tmp_path, bridge)
    plan_id = _store_plan(ledger)

    result = await coordinator.apply(plan_id, _KEY_ONE)

    assert result.state is expected_state
    assert result.error_code is expected_code
    expected_surviving_count = 0 if bridge_state == "ROLLBACK_VERIFIED" else created_count
    assert result.created_count == expected_surviving_count
    assert ledger.get_plan(plan_id).state is expected_state


async def test_proven_handler_rejection_preserves_exact_code_and_same_key_can_retry(
    tmp_path: Path,
) -> None:
    bridge = ScriptedBridge()
    bridge.mutation_state = "REJECTED"
    bridge.mutation_error_code = "LAYER_NOT_FOUND"
    bridge.mutation_created_count = 0
    coordinator, ledger = _coordinator(tmp_path, bridge)
    plan_id = _store_plan(ledger)

    rejected = await coordinator.apply(plan_id, _KEY_ONE)

    assert rejected.state is OperationState.PRE_SEND_FAILED
    assert rejected.error_code == "LAYER_NOT_FOUND"
    assert ledger.get_operation(_KEY_ONE).state is OperationState.PRE_SEND_FAILED
    assert len(bridge.mutation_calls) == 1

    bridge.mutation_state = "COMPLETE"
    bridge.mutation_error_code = None
    bridge.mutation_created_count = None
    applied = await coordinator.apply(plan_id, _KEY_ONE)

    assert applied.ok
    assert applied.state is OperationState.APPLIED
    assert len(bridge.mutation_calls) == 2
    assert bridge.reconcile_calls == []


async def test_rejected_response_claiming_entity_effects_is_unknown(tmp_path: Path) -> None:
    bridge = ScriptedBridge()
    bridge.mutation_state = "REJECTED"
    bridge.mutation_error_code = "LAYER_NOT_FOUND"
    bridge.mutation_created_count = 1
    coordinator, ledger = _coordinator(tmp_path, bridge)
    plan_id = _store_plan(ledger)

    result = await coordinator.apply(plan_id, _KEY_ONE)

    assert result.state is OperationState.UNKNOWN
    assert result.error_code is CoordinatorErrorCode.UNKNOWN_OUTCOME
    assert ledger.get_operation(_KEY_ONE).state is OperationState.UNKNOWN


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("response_bridge_build_id", "old-bridge"),
        ("response_catalog_hash", "d" * 64),
    ],
)
async def test_mutation_response_identity_mismatch_is_unknown(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    bridge = ScriptedBridge()
    setattr(bridge, field, value)
    coordinator, ledger = _coordinator(tmp_path, bridge)
    plan_id = _store_plan(ledger)

    result = await coordinator.apply(plan_id, _KEY_ONE)

    assert result.state is OperationState.UNKNOWN
    assert result.error_code is CoordinatorErrorCode.UNKNOWN_OUTCOME
    assert ledger.get_operation(_KEY_ONE).state is OperationState.UNKNOWN


@pytest.mark.parametrize("initial_state", ["PARTIAL", "ROLLBACK_FAILED"])
async def test_reconcile_none_verifies_cleanup_after_partial_or_failed_rollback(
    tmp_path: Path, initial_state: str
) -> None:
    bridge = ScriptedBridge()
    bridge.mutation_state = initial_state
    bridge.mutation_created_count = 1
    coordinator, ledger = _coordinator(tmp_path, bridge)
    plan_id = _store_plan(ledger)
    first = await coordinator.apply(plan_id, _KEY_ONE)
    assert first.state in {OperationState.PARTIAL, OperationState.ROLLBACK_FAILED}
    bridge.reconciliation = ReconciliationClassification.NONE

    verified = await coordinator.apply(plan_id, _KEY_ONE)

    assert verified.state is OperationState.ROLLBACK_VERIFIED
    assert verified.error_code is CoordinatorErrorCode.MUTATION_ROLLED_BACK
    assert verified.reconciled
    assert len(bridge.mutation_calls) == 1
    assert len(bridge.reconcile_calls) == 1


@pytest.mark.parametrize(
    "invalid_response",
    [
        None,
        {"state": "APPLIED"},
        {
            "state": "APPLIED",
            "planId": "f" * 64,
            "operationId": _KEY_ONE,
            "createdCount": 2,
        },
        {
            "state": "APPLIED",
            "planId": "placeholder",
            "operationId": _KEY_ONE,
            "createdCount": 1,
        },
    ],
)
async def test_untrustworthy_mutation_response_is_unknown(
    tmp_path: Path, invalid_response: JsonValue
) -> None:
    bridge = ScriptedBridge()
    bridge.mutation_response_override = invalid_response
    # None selects the default valid response, so replace it with a non-object.
    if invalid_response is None:
        bridge.mutation_response_override = []
    coordinator, ledger = _coordinator(tmp_path, bridge)
    plan_id = _store_plan(ledger)
    if (
        isinstance(bridge.mutation_response_override, dict)
        and bridge.mutation_response_override.get("planId") == "placeholder"
    ):
        bridge.mutation_response_override["planId"] = plan_id

    result = await coordinator.apply(plan_id, _KEY_ONE)

    assert result.state is OperationState.UNKNOWN
    assert result.error_code is CoordinatorErrorCode.UNKNOWN_OUTCOME
    assert ledger.get_operation(_KEY_ONE).state is OperationState.UNKNOWN


async def test_existing_planned_binding_is_proven_unsent_and_can_create_with_same_key(
    tmp_path: Path,
) -> None:
    bridge = ScriptedBridge()
    coordinator, ledger = _coordinator(tmp_path, bridge)
    plan_id = _store_plan(ledger)
    ledger.bind_operation(
        plan_id=plan_id,
        idempotency_key=_KEY_ONE,
        now=datetime(2025, 1, 1, tzinfo=UTC),
    )

    result = await coordinator.apply(plan_id, _KEY_ONE)

    assert result.state is OperationState.APPLIED
    assert result.ok
    assert len(bridge.read_calls) == 1
    assert len(bridge.mutation_calls) == 1
    assert bridge.reconcile_calls == []


async def test_proven_pre_send_failure_allows_only_explicit_same_key_create_retry(
    tmp_path: Path,
) -> None:
    bridge = ScriptedBridge()
    bridge.mutation_error = NetApiError(
        NetApiErrorCode.CONNECTION_REFUSED,
        "connection refused before write",
        phase=NetApiPhase.CONNECT,
        send_started=False,
    )
    coordinator, ledger = _coordinator(tmp_path, bridge)
    plan_id = _store_plan(ledger)

    first = await coordinator.apply(plan_id, _KEY_ONE)

    assert first.state is OperationState.PRE_SEND_FAILED
    assert first.error_code is CoordinatorErrorCode.PRE_SEND_FAILED
    assert ledger.get_operation(_KEY_ONE).state is OperationState.PRE_SEND_FAILED
    assert len(bridge.mutation_calls) == 1

    with pytest.raises(CoordinatorError) as conflict:
        await coordinator.apply(plan_id, _KEY_TWO)
    assert conflict.value.code is CoordinatorErrorCode.OPERATION_CONFLICT
    assert len(bridge.mutation_calls) == 1

    bridge.mutation_error = None
    second = await coordinator.apply(plan_id, _KEY_ONE)
    assert second.ok
    assert second.state is OperationState.APPLIED
    assert len(bridge.mutation_calls) == 2
    assert bridge.reconcile_calls == []


async def test_reconciliation_transport_failure_preserves_unknown_state(tmp_path: Path) -> None:
    bridge = ScriptedBridge()
    bridge.mutation_error = TimeoutError("lost mutation response")
    coordinator, ledger = _coordinator(tmp_path, bridge)
    plan_id = _store_plan(ledger)
    await coordinator.apply(plan_id, _KEY_ONE)
    bridge.reconcile_error = ConnectionError("read-only request failed")

    result = await coordinator.apply(plan_id, _KEY_ONE)

    assert result.state is OperationState.UNKNOWN
    assert result.error_code is CoordinatorErrorCode.RECONCILIATION_FAILED
    assert result.reconciled
    assert len(bridge.mutation_calls) == 1


async def test_cancellation_after_sending_is_durably_unknown(tmp_path: Path) -> None:
    bridge = ScriptedBridge()
    bridge.delay_seconds = 30
    coordinator, ledger = _coordinator(tmp_path, bridge)
    plan_id = _store_plan(ledger)
    task = asyncio.create_task(coordinator.apply(plan_id, _KEY_ONE))
    await asyncio.wait_for(bridge.mutation_entered.wait(), timeout=2)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert ledger.get_operation(_KEY_ONE).state is OperationState.UNKNOWN


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("targetLayer", "default"),
        ("catalog.hash", "not-a-hash"),
        ("constraints.terrainYEpsilonUm", 50_001),
        ("constraints.minSpacingUm", 0),
        ("context.currentSubscene", True),
        ("context.bridgeBuildId", "old-bridge"),
        ("shape.hash", "d" * 64),
        ("placements", []),
    ],
)
async def test_invalid_stored_plan_never_binds_or_calls_bridge(
    tmp_path: Path, field: str, value: object
) -> None:
    bridge = ScriptedBridge()
    coordinator, ledger = _coordinator(tmp_path, bridge)
    payload = _plan_payload()
    _set_plan_field(payload, field, value)
    plan = ledger.save_plan(payload, created_at=_CREATED, expires_at=_EXPIRES)

    with pytest.raises(CoordinatorError) as captured:
        await coordinator.apply(plan.plan_id, _KEY_ONE)

    assert captured.value.code is CoordinatorErrorCode.INVALID_STORED_PLAN
    assert ledger.get_operation_for_plan(plan.plan_id) is None
    assert bridge.read_calls == []
    assert bridge.mutation_calls == []
    assert bridge.reconcile_calls == []


async def test_stored_prefab_outside_running_exact_catalog_never_reaches_bridge(
    tmp_path: Path,
) -> None:
    bridge = ScriptedBridge()
    coordinator, ledger = _coordinator(tmp_path, bridge)
    payload = _plan_payload()
    placements = payload["placements"]
    assert isinstance(placements, list)
    first = placements[0]
    assert isinstance(first, dict)
    first["prefab"] = "{FFFFFFFFFFFFFFFF}Prefabs/Vegetation/NotAllowed.et"
    plan = ledger.save_plan(payload, created_at=_CREATED, expires_at=_EXPIRES)

    with pytest.raises(CoordinatorError) as captured:
        await coordinator.apply(plan.plan_id, _KEY_ONE)

    assert captured.value.code is CoordinatorErrorCode.INVALID_STORED_PLAN
    assert ledger.get_operation_for_plan(plan.plan_id) is None
    assert bridge.read_calls == []
    assert bridge.mutation_calls == []


@pytest.mark.parametrize(
    ("catalog_hash", "world_path"),
    [("d" * 64, "$thenewRJ:rj.ent"), (_CATALOG_HASH, "$other:world.ent")],
)
async def test_running_server_identity_mismatch_stops_before_binding_or_bridge(
    tmp_path: Path,
    catalog_hash: str,
    world_path: str,
) -> None:
    bridge = ScriptedBridge()
    ledger, locks, scope = _components(tmp_path)
    coordinator = ApplyCoordinator(
        ledger=ledger,
        lock_manager=locks,
        target_scope=scope,
        bridge=bridge,
        expected_catalog_hash=catalog_hash,
        expected_prefabs=_ALLOWED_PREFABS,
        expected_world_path=world_path,
    )
    plan_id = _store_plan(ledger)

    with pytest.raises(CoordinatorError) as captured:
        await coordinator.apply(plan_id, _KEY_ONE)

    assert captured.value.code is CoordinatorErrorCode.INVALID_STORED_PLAN
    assert ledger.get_operation_for_plan(plan_id) is None
    assert bridge.mutation_calls == []
    assert bridge.reconcile_calls == []


async def test_two_independent_client_coordinators_serialize_mutations(tmp_path: Path) -> None:
    bridge = ScriptedBridge()
    bridge.delay_seconds = 0.05
    first_coordinator, first_ledger = _coordinator(tmp_path, bridge)
    second_coordinator, second_ledger = _coordinator(tmp_path, bridge)
    first_plan = _store_plan(first_ledger, seed=1)
    second_plan = _store_plan(second_ledger, seed=2)

    results = await asyncio.gather(
        first_coordinator.apply(first_plan, _KEY_ONE),
        second_coordinator.apply(second_plan, _KEY_TWO),
    )

    assert all(result.ok for result in results)
    assert bridge.maximum_active_mutations == 1
    assert len(bridge.mutation_calls) == 2


async def test_net_api_client_satisfies_required_bridge_protocol_without_network(
    tmp_path: Path,
) -> None:
    client = NetApiClient("127.0.0.1", 5775)
    coordinator, ledger = _coordinator(tmp_path, client)
    plan_id = _store_plan(ledger)
    # The explicit call would use the network, which is outside this test.  The
    # construction itself is statically checked against BridgeClient by mypy.
    assert coordinator is not None
    assert plan_id


def test_two_stdio_processes_share_one_create_and_one_reconciliation(tmp_path: Path) -> None:
    ledger, _locks, _scope_value = _components(tmp_path)
    plan_id = _store_plan(ledger)
    log_path = tmp_path / "bridge.log"
    marker_path = tmp_path / "entities.marker"
    context = multiprocessing.get_context("spawn")
    parent_one, child_one = context.Pipe()
    parent_two, child_two = context.Pipe()
    common = (str(tmp_path), plan_id, _KEY_ONE, str(log_path), str(marker_path))
    first = context.Process(target=_process_apply, args=(*common, child_one))
    second = context.Process(target=_process_apply, args=(*common, child_two))
    first.start()
    second.start()
    child_one.close()
    child_two.close()
    assert parent_one.recv() == "ready"
    assert parent_two.recv() == "ready"
    parent_one.send("go")
    parent_two.send("go")
    results = [parent_one.recv(), parent_two.recv()]
    first.join(timeout=10)
    second.join(timeout=10)
    parent_one.close()
    parent_two.close()

    assert first.exitcode == 0
    assert second.exitcode == 0
    assert all(result[0] == "result" and result[1] == "APPLIED" for result in results)
    assert {result[2] for result in results} == {False, True}
    log = log_path.read_text(encoding="utf-8").splitlines()
    assert log.count("mutation:start") == 1
    assert log.count("mutation:end") == 1
    assert log.count("reconcile") == 1
    assert log.count("context") == 1


async def test_process_crash_after_mutation_is_recovered_and_never_resent(
    tmp_path: Path,
) -> None:
    ledger, _locks, _scope_value = _components(tmp_path)
    plan_id = _store_plan(ledger)
    log_path = tmp_path / "crash-bridge.log"
    marker_path = tmp_path / "crash-entities.marker"
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_process_crash_during_apply,
        args=(str(tmp_path), plan_id, _KEY_ONE, str(log_path), str(marker_path)),
    )
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 23

    bridge = ProcessFileBridge(log_path, marker_path)
    restarted, restarted_ledger = _coordinator(tmp_path, bridge)
    assert restarted_ledger.get_operation(_KEY_ONE).state is OperationState.UNKNOWN

    result = await restarted.apply(plan_id, _KEY_ONE)

    assert result.ok
    assert result.reconciled
    assert result.classification is ReconciliationClassification.COMPLETE
    log = log_path.read_text(encoding="utf-8").splitlines()
    assert log.count("mutation:crash") == 1
    assert log.count("mutation:start") == 0
    assert log.count("reconcile") == 1
    assert log.count("context") == 1
