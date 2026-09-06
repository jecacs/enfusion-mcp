from __future__ import annotations

from uuid import UUID

import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError

from enfusion_mcp.models import (
    CatalogEntry,
    ToolErrorInfo,
    VegetationApplyOutput,
    VegetationCatalogOutput,
    VegetationPlanInput,
    VegetationPlanOutput,
    WorkbenchStatusOutput,
    WorldContextOutput,
)
from enfusion_mcp.server import SERVER_INSTRUCTIONS, create_server


class FakeService:
    def __init__(self) -> None:
        self.plan_calls = 0
        self.status_calls = 0
        self.context_calls = 0

    async def workbench_status(self) -> WorkbenchStatusOutput:
        self.status_calls += 1
        return WorkbenchStatusOutput(
            ok=True,
            reachable=True,
            workbench_running=True,
            world_editor_running=True,
            message="fake ready",
        )

    async def world_context(self) -> WorldContextOutput:
        self.context_calls += 1
        return WorldContextOutput(ok=True, world_path="$myaddon:world.ent", selection_count=0)

    async def vegetation_catalog(self) -> VegetationCatalogOutput:
        return VegetationCatalogOutput(
            ok=True,
            version="test-v1",
            catalog_hash="0" * 64,
            production_ready=False,
            entries=[
                CatalogEntry(
                    resource_name="{0000000000000000}Synthetic/TestBush.et",
                    label="Synthetic",
                    provenance="test only",
                )
            ],
            warnings=["synthetic"],
        )

    async def vegetation_plan(self, request: VegetationPlanInput) -> VegetationPlanOutput:
        self.plan_calls += 1
        return VegetationPlanOutput(
            ok=False,
            algorithm_version="not-ready",
            requested_count=request.count,
            error=ToolErrorInfo(
                code="NOT_READY",
                message="planner arrives at Checkpoint C",
            ),
        )

    async def vegetation_apply(self, plan_id: str, idempotency_key: UUID) -> VegetationApplyOutput:
        return VegetationApplyOutput(
            ok=False,
            plan_id=plan_id,
            operation_id=str(idempotency_key),
            state="PRE_SEND_FAILED",
            error=ToolErrorInfo(
                code="NOT_READY",
                message="apply arrives at Checkpoint C",
            ),
        )


@pytest.mark.asyncio
async def test_exact_safe_tool_surface_annotations_and_instructions() -> None:
    service = FakeService()
    server = create_server(service)

    async with Client(server) as client:
        tools = await client.list_tools()
        by_name = {tool.name: tool for tool in tools.tools}

        assert list(by_name) == [
            "workbench_status",
            "world_context",
            "vegetation_catalog",
            "vegetation_plan",
            "vegetation_apply",
        ]
        assert client.instructions == SERVER_INSTRUCTIONS

        for name in (
            "workbench_status",
            "world_context",
            "vegetation_catalog",
            "vegetation_plan",
        ):
            annotations = by_name[name].annotations
            assert annotations is not None
            assert annotations.read_only_hint is True
            assert annotations.destructive_hint is False
            assert annotations.idempotent_hint is True
            assert annotations.open_world_hint is False

        apply_annotations = by_name["vegetation_apply"].annotations
        assert apply_annotations is not None
        assert apply_annotations.read_only_hint is False
        assert apply_annotations.destructive_hint is False
        assert apply_annotations.idempotent_hint is False
        assert apply_annotations.open_world_hint is False

        assert all(tool.output_schema is not None for tool in tools.tools)
        assert all(tool.input_schema.get("additionalProperties") is False for tool in tools.tools)


@pytest.mark.asyncio
async def test_structured_output_and_error_flag() -> None:
    server = create_server(FakeService())
    async with Client(server) as client:
        catalog = await client.call_tool("vegetation_catalog", {})
        assert catalog.is_error is False
        assert catalog.structured_content is not None
        assert catalog.structured_content["catalog_hash"] == "0" * 64

        apply = await client.call_tool(
            "vegetation_apply",
            {"plan_id": "a" * 64, "idempotency_key": "00000000-0000-4000-8000-000000000001"},
        )
        assert apply.is_error is True
        assert apply.structured_content is not None
        assert apply.structured_content["error"]["code"] == "NOT_READY"


@pytest.mark.asyncio
async def test_invalid_arguments_do_not_reach_service() -> None:
    service = FakeService()
    server = create_server(service)
    async with Client(server) as client:
        with pytest.raises(MCPError):
            await client.call_tool(
                "vegetation_plan",
                {
                    "count": 0,
                    "seed": 0,
                    "min_spacing_m": 1.0,
                    "max_slope_deg": 30.0,
                    "target_layer": "MCP_Preview",
                    "palette": [{"prefab": "not-allowlisted", "weight": 1.0}],
                    "scale_min": 1.0,
                    "scale_max": 1.0,
                },
            )
        assert service.plan_calls == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"count": "1"},
        {"count": True},
        {"APIFunc": "EnfusionMCP_VegetationApply"},
    ],
)
@pytest.mark.asyncio
async def test_coercive_or_extra_plan_arguments_never_reach_service(
    overrides: dict[str, object],
) -> None:
    service = FakeService()
    arguments: dict[str, object] = {
        "count": 1,
        "seed": 0,
        "min_spacing_m": 1.0,
        "max_slope_deg": 30.0,
        "target_layer": "MCP_Preview",
        "palette": [{"prefab": "{0000000000000000}Synthetic/TestBush.et", "weight": 1.0}],
        "scale_min": 1.0,
        "scale_max": 1.0,
    }
    arguments.update(overrides)
    async with Client(create_server(service)) as client:
        with pytest.raises(MCPError):
            await client.call_tool("vegetation_plan", arguments)
    assert service.plan_calls == 0


@pytest.mark.parametrize(
    "idempotency_key",
    [
        "00000000000040008000000000000001",
        "{00000000-0000-4000-8000-000000000001}",
        "00000000-0000-0000-0000-000000000000",
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa".upper(),
    ],
)
@pytest.mark.asyncio
async def test_apply_rejects_noncanonical_or_nil_uuid(idempotency_key: str) -> None:
    async with Client(create_server(FakeService())) as client:
        with pytest.raises(MCPError):
            await client.call_tool(
                "vegetation_apply",
                {"plan_id": "a" * 64, "idempotency_key": idempotency_key},
            )


@pytest.mark.parametrize("tool_name", ["workbench_status", "world_context"])
@pytest.mark.asyncio
async def test_extra_read_tool_arguments_do_not_reach_service(tool_name: str) -> None:
    service = FakeService()
    async with Client(create_server(service)) as client:
        with pytest.raises(MCPError):
            await client.call_tool(tool_name, {"APIFunc": "attacker"})
    assert service.status_calls == 0
    assert service.context_calls == 0


@pytest.mark.asyncio
async def test_uuid_v7_is_accepted_without_normalizing_client_spelling() -> None:
    key = "01941f29-7c00-7000-8000-000000000001"
    async with Client(create_server(FakeService())) as client:
        result = await client.call_tool(
            "vegetation_apply",
            {"plan_id": "a" * 64, "idempotency_key": key},
        )
    assert result.is_error is True
    assert result.structured_content is not None
    assert result.structured_content["operation_id"] == key
