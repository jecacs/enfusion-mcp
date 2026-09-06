"""Validated MCP input and structured-output models."""

from __future__ import annotations

import math
from typing import Annotated, Literal, Self
from uuid import RFC_4122, UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TargetLayer = Literal["MCP_Preview", "MCP_Vegetation"]
OperationStateName = Literal[
    "PLANNED",
    "SENDING",
    "UNKNOWN",
    "APPLIED",
    "PARTIAL",
    "ENTITY_CONFLICT",
    "ROLLBACK_VERIFIED",
    "ROLLBACK_FAILED",
    "UNDONE",
    "PRE_SEND_FAILED",
]
CANONICAL_UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
CanonicalUUIDString = Annotated[
    str,
    Field(min_length=36, max_length=36, pattern=CANONICAL_UUID_PATTERN),
]


class StrictModel(BaseModel):
    """Base model used at every untrusted JSON boundary."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


class ToolErrorInfo(StrictModel):
    """Stable machine-readable failure information."""

    code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_]*$")
    message: str = Field(min_length=1, max_length=2048)
    phase: str | None = Field(default=None, max_length=64)
    retryable: bool = False
    unknown_outcome: bool = False
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class WorkbenchStatusOutput(StrictModel):
    ok: bool
    reachable: bool
    workbench_running: bool | None = None
    world_editor_running: bool | None = None
    safe_mode: Literal[True] = True
    host: Literal["127.0.0.1"] = "127.0.0.1"
    port: Literal[5775] = 5775
    message: str
    error: ToolErrorInfo | None = None

    @model_validator(mode="after")
    def coherent_result(self) -> Self:
        if self.ok == (self.error is not None):
            raise ValueError("successful status must omit error; failed status must include it")
        return self


class Vec3(StrictModel):
    x: float
    y: float
    z: float


class BoundsXZ(StrictModel):
    min_x: float
    min_z: float
    max_x: float
    max_z: float

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.min_x > self.max_x or self.min_z > self.max_z:
            raise ValueError("terrain bounds must be ordered")
        return self


class WorldContextOutput(StrictModel):
    ok: bool
    world_path: str | None = None
    mode: Literal["edit", "game", "prefab", "no_world_editor", "unknown"] | None = None
    subscene: int | None = Field(default=None, ge=-1)
    current_layer_id: int | None = None
    active_layer_path: str | None = None
    terrain_bounds: BoundsXZ | None = None
    selection_count: int | None = None
    selected_name: str | None = None
    selected_class: str | None = None
    polygon_compatible: bool | None = None
    shape_closed: bool | None = None
    shape_points_world: list[Vec3] = Field(default_factory=list, max_length=1024)
    bridge_protocol_version: str | None = None
    bridge_build_id: str | None = None
    catalog_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    error: ToolErrorInfo | None = None

    @model_validator(mode="after")
    def coherent_result(self) -> Self:
        if self.ok and (self.error is not None or self.world_path is None):
            raise ValueError("successful context requires world_path and no error")
        if not self.ok and self.error is None:
            raise ValueError("failed context requires error")
        return self


class CatalogEntry(StrictModel):
    resource_name: str = Field(min_length=1, max_length=512)
    label: str = Field(min_length=1, max_length=128)
    provenance: str = Field(min_length=1, max_length=512)


class VegetationCatalogOutput(StrictModel):
    ok: bool
    version: str
    catalog_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    production_ready: bool
    entries: list[CatalogEntry]
    warnings: list[str] = Field(default_factory=list)
    error: ToolErrorInfo | None = None

    @model_validator(mode="after")
    def coherent_result(self) -> Self:
        if self.ok == (self.error is not None):
            raise ValueError("successful catalog must omit error; failed catalog must include it")
        return self


class PaletteItem(StrictModel):
    prefab: str = Field(min_length=1, max_length=512)
    weight: float = Field(gt=0.0, le=1_000_000.0)

    @field_validator("weight")
    @classmethod
    def finite_weight(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("weight must be finite")
        return value


class VegetationPlanInput(StrictModel):
    count: int = Field(ge=1, le=100)
    seed: int = Field(ge=0, le=4_294_967_295)
    min_spacing_m: float = Field(gt=0.0, le=1_000.0)
    max_slope_deg: float = Field(ge=0.0, lt=90.0)
    target_layer: TargetLayer
    palette: list[PaletteItem] = Field(min_length=1, max_length=32)
    scale_min: float = Field(gt=0.0, le=10.0)
    scale_max: float = Field(gt=0.0, le=10.0)

    @model_validator(mode="after")
    def validate_numbers_and_scale(self) -> Self:
        numeric = (self.min_spacing_m, self.max_slope_deg, self.scale_min, self.scale_max)
        if not all(math.isfinite(value) for value in numeric):
            raise ValueError("numeric constraints must be finite")
        if self.scale_min > self.scale_max:
            raise ValueError("scale_min must be <= scale_max")
        prefabs = [item.prefab for item in self.palette]
        if len(prefabs) != len(set(prefabs)):
            raise ValueError("palette prefabs must be unique")
        return self


class NoToolArguments(StrictModel):
    """Strict empty object for the three no-argument tools."""


def _canonical_non_nil_uuid(value: str) -> str:
    parsed = UUID(value)
    if str(parsed) != value or parsed.variant != RFC_4122 or parsed.int == 0:
        raise ValueError("UUID must be canonical, non-nil, and use the RFC-4122 variant")
    return value


class VegetationApplyInput(StrictModel):
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", max_length=64)
    idempotency_key: CanonicalUUIDString

    @field_validator("idempotency_key")
    @classmethod
    def canonical_non_nil_uuid(cls, value: str) -> str:
        return _canonical_non_nil_uuid(value)


class PlannedPlacement(StrictModel):
    index: int = Field(ge=0, lt=100)
    prefab: str = Field(min_length=1, max_length=512)
    entity_name: str = Field(min_length=1, max_length=128)
    position: Vec3
    yaw_deg: float = Field(ge=0.0, lt=360.0)
    scale: float = Field(gt=0.0, le=10.0)


class VegetationPlanOutput(StrictModel):
    ok: bool
    plan_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    algorithm_version: str
    requested_count: int
    placements: list[PlannedPlacement] = Field(default_factory=list, max_length=100)
    rejection_stats: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    created_at: str | None = None
    expires_at: str | None = None
    target_layer: TargetLayer | None = None
    mutated_workbench: Literal[False] = False
    error: ToolErrorInfo | None = None

    @model_validator(mode="after")
    def coherent_result(self) -> Self:
        if self.ok and (self.error is not None or self.plan_id is None):
            raise ValueError("successful plan requires plan_id and no error")
        if not self.ok and self.error is None:
            raise ValueError("failed plan requires error")
        return self


class VegetationApplyOutput(StrictModel):
    ok: bool
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$", max_length=64)
    operation_id: CanonicalUUIDString
    state: OperationStateName
    created_count: int = Field(default=0, ge=0, le=100)
    reconciled: bool = False
    world_saved: Literal[False] = False
    error: ToolErrorInfo | None = None

    @field_validator("operation_id")
    @classmethod
    def canonical_non_nil_operation_id(cls, value: str) -> str:
        return _canonical_non_nil_uuid(value)

    @model_validator(mode="after")
    def coherent_result(self) -> Self:
        if self.ok == (self.error is not None):
            raise ValueError("successful apply must omit error; failed apply must include it")
        return self
