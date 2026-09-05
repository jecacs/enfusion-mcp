"""Vendor-neutral MCP surface for the mandatory safe profile."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Annotated, Any, Protocol
from uuid import UUID

from mcp.server import MCPServer
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS, CallToolResult, TextContent, Tool, ToolAnnotations
from pydantic import Field
from pydantic_core import ValidationError

from . import __version__
from .models import (
    CanonicalUUIDString,
    NoToolArguments,
    PaletteItem,
    StrictModel,
    VegetationApplyInput,
    VegetationApplyOutput,
    VegetationCatalogOutput,
    VegetationPlanInput,
    VegetationPlanOutput,
    WorkbenchStatusOutput,
    WorldContextOutput,
)

SERVER_INSTRUCTIONS = """Safe Arma Reforger vegetation workflow.
Always call vegetation_plan before vegetation_apply. Planning does not modify the
Workbench world, but it stores an immutable control-plane plan in the shared
ledger. Apply accepts only that stored plan and a UUID idempotency key. Apply
creates at most 100 entities, never saves the world, and must form one batch that
the user can later test with one Ctrl+Z. Only the user saves the world manually.
MCP annotations are client hints, not a security boundary; the server validates
every constraint regardless of client behaviour. Do not treat client name,
session ID, model name, or NET API Client ID as an identity or credential.
"""

READ_ONLY_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

APPLY_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)


_TOOL_INPUT_MODELS: dict[str, type[StrictModel]] = {
    "workbench_status": NoToolArguments,
    "world_context": NoToolArguments,
    "vegetation_catalog": NoToolArguments,
    "vegetation_plan": VegetationPlanInput,
    "vegetation_apply": VegetationApplyInput,
}


class StrictToolArgumentsMiddleware:
    """Validate raw tool JSON before the SDK's coercive function adapter."""

    async def __call__(
        self,
        ctx: ServerRequestContext[Any, Any],
        call_next: CallNext,
    ) -> HandlerResult:
        if ctx.method != "tools/call":
            return await call_next(ctx)
        params = ctx.params
        if not isinstance(params, Mapping):
            raise MCPError(INVALID_PARAMS, "tools/call params must be an object")
        name = params.get("name")
        model = _TOOL_INPUT_MODELS.get(name) if isinstance(name, str) else None
        if model is None:
            return await call_next(ctx)
        arguments = params.get("arguments", {})
        try:
            model.model_validate(arguments)
        except ValidationError as error:
            fields = sorted(
                {".".join(str(part) for part in item["loc"]) for item in error.errors()}
            )
            field_text = ", ".join(fields) if fields else "arguments"
            raise MCPError(
                INVALID_PARAMS,
                f"invalid arguments for {name}: {field_text}",
            ) from error
        return await call_next(ctx)


class SafeMCPServer(MCPServer[None]):
    """MCPServer that publishes the same strict object boundary it enforces."""

    async def list_tools(self) -> list[Tool]:
        tools = await super().list_tools()
        strict_tools = []
        for tool in tools:
            schema = dict(tool.input_schema)
            schema["additionalProperties"] = False
            strict_tools.append(tool.model_copy(update={"input_schema": schema}))
        return strict_tools


class ToolService(Protocol):
    """Business service used by MCP registration and easy to fake in tests."""

    async def workbench_status(self) -> WorkbenchStatusOutput: ...

    async def world_context(self) -> WorldContextOutput: ...

    async def vegetation_catalog(self) -> VegetationCatalogOutput: ...

    async def vegetation_plan(self, request: VegetationPlanInput) -> VegetationPlanOutput: ...

    async def vegetation_apply(
        self, plan_id: str, idempotency_key: UUID
    ) -> VegetationApplyOutput: ...


def _call_result(
    output: WorkbenchStatusOutput
    | WorldContextOutput
    | VegetationCatalogOutput
    | VegetationPlanOutput
    | VegetationApplyOutput,
) -> CallToolResult:
    payload = output.model_dump(mode="json")
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=payload,
        is_error=not output.ok,
    )


def create_server(service: ToolService) -> MCPServer[None]:
    """Create the exact five-tool safe MCP server."""

    mcp: MCPServer[None] = SafeMCPServer(
        name="enfusion-mcp-rj",
        version=__version__,
        instructions=SERVER_INSTRUCTIONS,
        log_level="WARNING",
        middleware=[StrictToolArgumentsMiddleware()],
    )

    @mcp.tool(
        name="workbench_status",
        description=(
            "Read loopback Workbench and World Editor availability. Never starts Steam, "
            "Proton, or Workbench."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    async def workbench_status() -> Annotated[CallToolResult, WorkbenchStatusOutput]:
        return _call_result(await service.workbench_status())

    @mcp.tool(
        name="world_context",
        description=(
            "Read the current world, subscene, root layer, terrain bounds, and exactly selected "
            "polygon-compatible closed Shape. Never changes selection or world state."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    async def world_context() -> Annotated[CallToolResult, WorldContextOutput]:
        return _call_result(await service.world_context())

    @mcp.tool(
        name="vegetation_catalog",
        description=(
            "Return the versioned exact prefab allowlist and its production-readiness status."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    async def vegetation_catalog() -> Annotated[CallToolResult, VegetationCatalogOutput]:
        return _call_result(await service.vegetation_catalog())

    @mcp.tool(
        name="vegetation_plan",
        description=(
            "Read context and one batched terrain sample, deterministically plan at most 100 "
            "allowlisted entities, and persist an immutable plan. Does not mutate Workbench."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    async def vegetation_plan(  # noqa: PLR0913, PLR0917 - flat MCP schema is intentional
        count: Annotated[int, Field(ge=1, le=100)],
        seed: Annotated[int, Field(ge=0, le=4_294_967_295)],
        min_spacing_m: Annotated[float, Field(gt=0.0, le=1_000.0)],
        max_slope_deg: Annotated[float, Field(ge=0.0, lt=90.0)],
        target_layer: Annotated[
            str, Field(pattern=r"^(MCP_Preview|MCP_Vegetation)$", max_length=32)
        ],
        palette: Annotated[list[PaletteItem], Field(min_length=1, max_length=32)],
        scale_min: Annotated[float, Field(gt=0.0, le=10.0)],
        scale_max: Annotated[float, Field(gt=0.0, le=10.0)],
    ) -> Annotated[CallToolResult, VegetationPlanOutput]:
        request = VegetationPlanInput.model_validate(
            {
                "count": count,
                "seed": seed,
                "min_spacing_m": min_spacing_m,
                "max_slope_deg": max_slope_deg,
                "target_layer": target_layer,
                "palette": palette,
                "scale_min": scale_min,
                "scale_max": scale_max,
            }
        )
        return _call_result(await service.vegetation_plan(request))

    @mcp.tool(
        name="vegetation_apply",
        description=(
            "Mutate unsaved editor state by applying one previously stored plan as one Undo batch. "
            "Never saves the world and never automatically retries an uncertain mutation."
        ),
        annotations=APPLY_ANNOTATIONS,
        structured_output=True,
    )
    async def vegetation_apply(
        plan_id: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", max_length=64)],
        idempotency_key: CanonicalUUIDString,
    ) -> Annotated[CallToolResult, VegetationApplyOutput]:
        return _call_result(await service.vegetation_apply(plan_id, UUID(idempotency_key)))

    return mcp
