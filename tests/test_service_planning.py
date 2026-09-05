from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from enfusion_mcp_rj.bridge_models import BRIDGE_BUILD_ID, BRIDGE_PROTOCOL_VERSION
from enfusion_mcp_rj.catalog import VegetationCatalog
from enfusion_mcp_rj.config import ServerConfig
from enfusion_mcp_rj.ledger import Ledger, OperationState
from enfusion_mcp_rj.locking import TargetLockManager, TargetScope
from enfusion_mcp_rj.models import CatalogEntry, PaletteItem, VegetationPlanInput
from enfusion_mcp_rj.net_api import JsonValue, NetApiClient
from enfusion_mcp_rj.service import SafeRuntimeService

PREFAB = "{0000000000000001}Prefabs/Synthetic/Bush.et"


def _config(tmp_path: Path) -> ServerConfig:
    prefix = tmp_path / "prefix"
    drive_c = prefix / "drive_c"
    project = drive_c / "project"
    project.mkdir(parents=True)
    (prefix / "dosdevices").mkdir()
    (prefix / "dosdevices/c:").symlink_to(drive_c)
    return ServerConfig.from_env(
        {
            "ENFUSION_PLATFORM_MODE": "native-linux-proton",
            "ENFUSION_STEAM_TOOLS_APP_ID": "1874910",
            "ENFUSION_PROTON_PREFIX": str(prefix),
            "ENFUSION_WORKBENCH_HOST": "127.0.0.1",
            "ENFUSION_WORKBENCH_PORT": "5775",
            "ENFUSION_PROJECT_HOST_PATH": str(project),
            "ENFUSION_PROJECT_ENGINE_PATH": r"C:\project",
            "ENFUSION_ALLOWED_WORLD": "$thenewRJ:rj.ent",
            "ENFUSION_SAFE_MODE": "1",
            "ENFUSION_STATE_DIR": str(tmp_path / ".state"),
        }
    )


def _catalog() -> VegetationCatalog:
    return VegetationCatalog(
        version="synthetic-v1",
        entries=(
            CatalogEntry(resource_name=PREFAB, label="Synthetic bush", provenance="unit test"),
        ),
        production_ready=True,
    )


class PlanningNetClient:
    def __init__(self, catalog_hash: str, *, selection_count: int = 1) -> None:
        self.catalog_hash = catalog_hash
        self.selection_count = selection_count
        self.context_calls = 0
        self.terrain_calls = 0
        self.last_batch_size = 0
        self.context_error_code = ""

    @property
    def workbench_address(self) -> tuple[str, int]:
        return ("127.0.0.1", 5775)

    async def call(
        self,
        api_func: str,
        params: Mapping[str, JsonValue] | None = None,
        **_kwargs: object,
    ) -> JsonValue:
        if api_func == "RJMCP_GetContext":
            self.context_calls += 1
            return {
                "status": "error" if self.context_error_code else "ok",
                "errorCode": self.context_error_code,
                "message": "test",
                "bridgeProtocolVersion": BRIDGE_PROTOCOL_VERSION,
                "bridgeBuildId": BRIDGE_BUILD_ID,
                "catalogHash": self.catalog_hash,
                "worldPath": "$thenewRJ:rj.ent",
                "mode": "edit",
                "currentSubscene": 0,
                "currentLayerId": 7,
                "activeLayerPath": "MCP_Preview",
                "terrainBounds": {
                    "minX": 0.0,
                    "minY": -100.0,
                    "minZ": 0.0,
                    "maxX": 100.0,
                    "maxY": 100.0,
                    "maxZ": 100.0,
                },
                "selectionCount": self.selection_count,
                "selectedName": "PlantingArea",
                "selectedClass": "PolylineShapeEntity",
                "polygonCompatible": True,
                "shapeClosed": True,
                "shapePointsWorld": [
                    {"x": 10.0, "y": 0.0, "z": 10.0},
                    {"x": 40.0, "y": 0.0, "z": 10.0},
                    {"x": 40.0, "y": 0.0, "z": 40.0},
                    {"x": 10.0, "y": 0.0, "z": 40.0},
                ],
            }
        if api_func == "RJMCP_TerrainSample":
            self.terrain_calls += 1
            assert params is not None
            points = params["points"]
            assert isinstance(points, list)
            self.last_batch_size = len(points)
            results: list[JsonValue] = []
            for point in points:
                assert isinstance(point, dict)
                results.append(
                    {
                        "requestedX": point["x"],
                        "requestedZ": point["z"],
                        "terrainY": 5.0,
                        "normalX": 0.0,
                        "normalY": 1.0,
                        "normalZ": 0.0,
                        "hasTerrain": True,
                    }
                )
            return {
                "status": "ok",
                "errorCode": "",
                "message": "test",
                "bridgeProtocolVersion": BRIDGE_PROTOCOL_VERSION,
                "results": results,
                "warnings": [],
            }
        raise AssertionError(f"unexpected endpoint {api_func}")

    async def call_mutation(
        self,
        api_func: str,
        params: Mapping[str, JsonValue] | None = None,
    ) -> JsonValue:
        del api_func, params
        raise AssertionError("planning must not mutate")

    async def call_reconcile(
        self,
        api_func: str,
        params: Mapping[str, JsonValue],
    ) -> JsonValue:
        del api_func, params
        raise AssertionError("planning must not reconcile")


class ApplyingNetClient(PlanningNetClient):
    def __init__(self, catalog_hash: str) -> None:
        super().__init__(catalog_hash)
        self.mutation_calls = 0
        self.reconcile_calls = 0
        self.reconcile_state = "COMPLETE"
        self.mutation_error: Exception | None = None
        self.mutation_state = "COMPLETE"
        self.mutation_error_code: str | None = None

    @staticmethod
    def _operation_response(
        params: Mapping[str, JsonValue],
        *,
        mode: str,
        state: str,
        created_count: int,
        error_code: str | None = None,
    ) -> JsonValue:
        count = params["count"]
        names = params["entityNames"]
        assert isinstance(count, int) and not isinstance(count, bool)
        assert isinstance(names, list)
        complete = state == "COMPLETE"
        none = state == "NONE"
        return {
            "status": "ok" if complete or none else "error",
            "errorCode": "" if complete or none else error_code or state,
            "message": f"fake {state}",
            "bridgeProtocolVersion": BRIDGE_PROTOCOL_VERSION,
            "bridgeBuildId": params["bridgeBuildId"],
            "catalogHash": params["catalogHash"],
            "planId": params["planId"],
            "operationId": params["operationId"],
            "mode": mode,
            "state": state,
            "expectedCount": count,
            "matchingCount": count if complete else 0,
            "createdCount": created_count,
            "rollbackVerified": False,
            "entityNames": names[:created_count],
        }

    async def call_mutation(
        self,
        api_func: str,
        params: Mapping[str, JsonValue] | None = None,
    ) -> JsonValue:
        assert api_func == "RJMCP_VegetationApply"
        assert params is not None
        self.mutation_calls += 1
        if self.mutation_error is not None:
            raise self.mutation_error
        count = params["count"]
        assert isinstance(count, int) and not isinstance(count, bool)
        return self._operation_response(
            params,
            mode="create",
            state=self.mutation_state,
            created_count=count if self.mutation_state == "COMPLETE" else 0,
            error_code=self.mutation_error_code,
        )

    async def call_reconcile(
        self,
        api_func: str,
        params: Mapping[str, JsonValue],
    ) -> JsonValue:
        assert api_func == "RJMCP_VegetationApply"
        self.reconcile_calls += 1
        return self._operation_response(
            params,
            mode="reconcile",
            state=self.reconcile_state,
            created_count=0,
        )


def _service(
    tmp_path: Path,
    client: PlanningNetClient,
    catalog: VegetationCatalog,
) -> tuple[SafeRuntimeService, Ledger]:
    config = _config(tmp_path)
    ledger = Ledger(config.state_dir.path, allowed_root=tmp_path)
    locks = TargetLockManager(config.state_dir.path, allowed_root=tmp_path)
    scope = TargetScope.derive(
        workbench_host=config.workbench_host,
        workbench_port=config.workbench_port,
        project_host_path=config.project_host_path.path,
        project_engine_path=config.project_engine_path.value,
        world=config.allowed_world.value,
    )
    return (
        SafeRuntimeService(
            config,
            cast(NetApiClient, client),
            catalog=catalog,
            ledger=ledger,
            lock_manager=locks,
            target_scope=scope,
        ),
        ledger,
    )


def _request(seed: int = 0) -> VegetationPlanInput:
    return VegetationPlanInput(
        count=5,
        seed=seed,
        min_spacing_m=1.0,
        max_slope_deg=30.0,
        target_layer="MCP_Preview",
        palette=[PaletteItem(prefab=PREFAB, weight=1.0)],
        scale_min=0.8,
        scale_max=1.2,
    )


@pytest.mark.asyncio
async def test_world_context_maps_every_staged_bridge_field(tmp_path: Path) -> None:
    catalog = _catalog()
    client = PlanningNetClient(catalog.catalog_hash)
    service, _ledger = _service(tmp_path, client, catalog)
    result = await service.world_context()
    assert result.ok is True
    assert result.world_path == "$thenewRJ:rj.ent"
    assert result.mode == "edit"
    assert result.subscene == 0
    assert result.current_layer_id == 7
    assert result.active_layer_path == "MCP_Preview"
    assert result.selection_count == 1
    assert result.selected_name == "PlantingArea"
    assert result.selected_class == "PolylineShapeEntity"
    assert result.polygon_compatible is True
    assert result.shape_closed is True
    assert len(result.shape_points_world) == 4
    assert result.terrain_bounds is not None
    assert result.bridge_protocol_version == BRIDGE_PROTOCOL_VERSION
    assert result.bridge_build_id == BRIDGE_BUILD_ID
    assert result.catalog_hash == catalog.catalog_hash


@pytest.mark.asyncio
async def test_invalid_bridge_error_code_becomes_structured_invalid_response(
    tmp_path: Path,
) -> None:
    catalog = _catalog()
    client = PlanningNetClient(catalog.catalog_hash)
    client.context_error_code = "bad code"
    service, _ledger = _service(tmp_path, client, catalog)
    result = await service.world_context()
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "INVALID_RESPONSE"


@pytest.mark.asyncio
async def test_service_plans_with_exactly_one_batched_terrain_call_and_persists(
    tmp_path: Path,
) -> None:
    catalog = _catalog()
    client = PlanningNetClient(catalog.catalog_hash)
    service, ledger = _service(tmp_path, client, catalog)

    first = await service.vegetation_plan(_request(seed=0))
    assert first.ok is True
    assert first.plan_id is not None
    assert len(first.placements) == 5
    assert client.context_calls == 1
    assert client.terrain_calls == 1
    assert 1 <= client.last_batch_size <= 1000
    persisted = ledger.get_plan(first.plan_id)
    assert persisted.state is OperationState.PLANNED

    operation = ledger.bind_operation(
        plan_id=first.plan_id,
        idempotency_key="00000000-0000-4000-8000-000000000001",
    )
    ledger.transition_operation(operation.operation_id, OperationState.SENDING)
    ledger.transition_operation(operation.operation_id, OperationState.APPLIED)

    second = await service.vegetation_plan(_request(seed=0))
    assert second.ok is True
    assert second.plan_id == first.plan_id
    assert second.created_at == first.created_at
    assert second.expires_at == first.expires_at
    assert ledger.get_plan(first.plan_id).state is OperationState.APPLIED


@pytest.mark.asyncio
async def test_invalid_selection_stops_before_terrain_and_persistence(tmp_path: Path) -> None:
    catalog = _catalog()
    client = PlanningNetClient(catalog.catalog_hash, selection_count=0)
    service, ledger = _service(tmp_path, client, catalog)
    result = await service.vegetation_plan(_request())
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "INVALID_SELECTION"
    assert client.context_calls == 1
    assert client.terrain_calls == 0
    assert ledger.count_plans() == 0


@pytest.mark.asyncio
async def test_service_apply_is_create_once_then_reconcile_only_and_marks_undo(
    tmp_path: Path,
) -> None:
    catalog = _catalog()
    client = ApplyingNetClient(catalog.catalog_hash)
    service, _ledger = _service(tmp_path, client, catalog)
    planned = await service.vegetation_plan(_request())
    assert planned.ok and planned.plan_id is not None
    key = "00000000-0000-4000-8000-000000000001"

    first = await service.vegetation_apply(planned.plan_id, UUID(key))
    assert first.ok is True
    assert first.state == "APPLIED"
    assert first.operation_id == key
    assert client.mutation_calls == 1

    repeated = await service.vegetation_apply(planned.plan_id, UUID(key))
    assert repeated.ok is True
    assert repeated.reconciled is True
    assert client.mutation_calls == 1
    assert client.reconcile_calls == 1

    conflicting = await service.vegetation_apply(
        planned.plan_id,
        UUID("00000000-0000-4000-8000-000000000002"),
    )
    assert conflicting.ok is False
    assert conflicting.error is not None
    assert conflicting.error.code == "OPERATION_CONFLICT"
    assert client.mutation_calls == 1

    client.reconcile_state = "NONE"
    undone = await service.vegetation_apply(planned.plan_id, UUID(key))
    assert undone.ok is False
    assert undone.state == "UNDONE"
    assert undone.error is not None
    assert undone.error.code == "UNDONE"
    assert client.mutation_calls == 1


@pytest.mark.asyncio
async def test_service_preserves_proven_handler_rejection_code(tmp_path: Path) -> None:
    catalog = _catalog()
    client = ApplyingNetClient(catalog.catalog_hash)
    client.mutation_state = "REJECTED"
    client.mutation_error_code = "LAYER_NOT_FOUND"
    service, _ledger = _service(tmp_path, client, catalog)
    planned = await service.vegetation_plan(_request())
    assert planned.ok and planned.plan_id is not None

    result = await service.vegetation_apply(
        planned.plan_id,
        UUID("00000000-0000-4000-8000-000000000001"),
    )

    assert result.ok is False
    assert result.state == "PRE_SEND_FAILED"
    assert result.error is not None
    assert result.error.code == "LAYER_NOT_FOUND"
    assert result.error.unknown_outcome is False


@pytest.mark.asyncio
async def test_service_marks_coordinator_unknown_outcome_in_structured_error(
    tmp_path: Path,
) -> None:
    catalog = _catalog()
    client = ApplyingNetClient(catalog.catalog_hash)
    service, _ledger = _service(tmp_path, client, catalog)
    planned = await service.vegetation_plan(_request())
    assert planned.ok and planned.plan_id is not None
    client.mutation_error = TimeoutError("lost after fake mutation")

    result = await service.vegetation_apply(
        planned.plan_id,
        UUID("00000000-0000-4000-8000-000000000003"),
    )

    assert result.ok is False
    assert result.state == "UNKNOWN"
    assert result.error is not None
    assert result.error.code == "UNKNOWN_OUTCOME"
    assert result.error.unknown_outcome is True
