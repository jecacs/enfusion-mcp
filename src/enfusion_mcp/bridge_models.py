"""Strict Python side of the staged Python ↔ Enforce JSON contract."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from .models import CanonicalUUIDString, StrictModel

BRIDGE_PROTOCOL_VERSION = "enfusion-mcp-bridge-v1"
BRIDGE_BUILD_ID = "enfusion-mcp-bridge-v1-map-agnostic"
BRIDGE_ERROR_CODE_PATTERN = r"^(?:|[A-Z][A-Z0-9_]*)$"
MAX_CONTEXT_POINTS = 1024
MAX_TERRAIN_POINTS = 1000
MAX_PLACEMENTS = 100
FULL_CIRCLE_DEGREES = 360.0


def _to_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


class BridgeModel(StrictModel):
    """Strict lowerCamelCase wire model shared with staged handler RegV names."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        allow_inf_nan=False,
        alias_generator=_to_camel,
        validate_by_alias=True,
        validate_by_name=False,
        serialize_by_alias=True,
    )


class BridgeVec3(BridgeModel):
    x: float
    y: float
    z: float


class BridgeTerrainBounds(BridgeModel):
    min_x: float
    min_y: float
    min_z: float
    max_x: float
    max_y: float
    max_z: float

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.min_x > self.max_x or self.min_y > self.max_y or self.min_z > self.max_z:
            raise ValueError("terrain bounds must be ordered")
        return self


class BridgeContextResponse(BridgeModel):
    status: Literal["ok", "error"]
    error_code: str = Field(default="", max_length=64, pattern=BRIDGE_ERROR_CODE_PATTERN)
    message: str = Field(default="", max_length=2048)
    bridge_protocol_version: str
    bridge_build_id: str
    catalog_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    world_path: str = Field(default="", max_length=512)
    mode: Literal["edit", "game", "prefab", "no_world_editor", "unknown"]
    current_subscene: int = Field(default=-1, ge=-1)
    current_layer_id: int = Field(default=-1, ge=-1)
    active_layer_path: str = Field(default="", max_length=512)
    terrain_bounds: BridgeTerrainBounds | None = None
    selection_count: int = Field(default=0, ge=0)
    selected_name: str = Field(default="", max_length=256)
    selected_class: str = Field(default="", max_length=256)
    polygon_compatible: bool = False
    shape_closed: bool = False
    shape_points_world: list[BridgeVec3] = Field(
        default_factory=list,
        max_length=MAX_CONTEXT_POINTS,
    )

    @model_validator(mode="after")
    def coherent_status(self) -> Self:
        if self.status == "error" and not self.error_code:
            raise ValueError("error response requires errorCode")
        if self.status == "ok" and self.error_code:
            raise ValueError("successful response must not carry errorCode")
        return self


class BridgeTerrainPointRequest(BridgeModel):
    x: float = Field(ge=-1_000_000.0, le=1_000_000.0)
    z: float = Field(ge=-1_000_000.0, le=1_000_000.0)


class BridgeTerrainSampleRequest(BridgeModel):
    points: list[BridgeTerrainPointRequest] = Field(
        min_length=1,
        max_length=MAX_TERRAIN_POINTS,
    )


class BridgeTerrainSample(BridgeModel):
    requested_x: float
    requested_z: float
    terrain_y: float
    normal_x: float
    normal_y: float
    normal_z: float
    has_terrain: bool


class BridgeTerrainSampleResponse(BridgeModel):
    status: Literal["ok", "error"]
    error_code: str = Field(default="", max_length=64, pattern=BRIDGE_ERROR_CODE_PATTERN)
    message: str = Field(default="", max_length=2048)
    bridge_protocol_version: str
    results: list[BridgeTerrainSample] = Field(default_factory=list, max_length=MAX_TERRAIN_POINTS)
    warnings: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def coherent_status(self) -> Self:
        if self.status == "error" and not self.error_code:
            raise ValueError("error response requires errorCode")
        if self.status == "ok" and self.error_code:
            raise ValueError("successful response must not carry errorCode")
        return self


class BridgeVegetationApplyRequest(BridgeModel):
    mode: Literal["create", "reconcile"]
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation_id: CanonicalUUIDString
    bridge_build_id: str
    catalog_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    world_path: str = Field(max_length=512)
    subscene: int = Field(ge=0)
    target_layer: Literal["MCP_Preview", "MCP_Vegetation"]
    count: int = Field(ge=1, le=MAX_PLACEMENTS)
    min_spacing_m: float = Field(gt=0.0, le=1_000.0)
    max_slope_deg: float = Field(ge=0.0, lt=90.0)
    scale_min: float = Field(gt=0.0, le=10.0)
    scale_max: float = Field(gt=0.0, le=10.0)
    terrain_y_epsilon: float = Field(gt=0.0, le=0.05)
    prefabs: list[str] = Field(min_length=1, max_length=MAX_PLACEMENTS)
    entity_names: list[str] = Field(min_length=1, max_length=MAX_PLACEMENTS)
    x: list[float] = Field(min_length=1, max_length=MAX_PLACEMENTS)
    y: list[float] = Field(min_length=1, max_length=MAX_PLACEMENTS)
    z: list[float] = Field(min_length=1, max_length=MAX_PLACEMENTS)
    yaw: list[float] = Field(min_length=1, max_length=MAX_PLACEMENTS)
    scale: list[float] = Field(min_length=1, max_length=MAX_PLACEMENTS)

    @model_validator(mode="after")
    def cardinality_and_ranges(self) -> Self:
        arrays = (
            self.prefabs,
            self.entity_names,
            self.x,
            self.y,
            self.z,
            self.yaw,
            self.scale,
        )
        if any(len(values) != self.count for values in arrays):
            raise ValueError("all placement arrays must have exactly count items")
        if self.scale_min > self.scale_max:
            raise ValueError("scaleMin must be <= scaleMax")
        if any(not 0.0 <= yaw < FULL_CIRCLE_DEGREES for yaw in self.yaw):
            raise ValueError("yaw values must be in [0, 360)")
        if any(not self.scale_min <= value <= self.scale_max for value in self.scale):
            raise ValueError("scale values must be inside declared range")
        return self


class BridgeVegetationApplyResponse(BridgeModel):
    status: Literal["ok", "error"]
    error_code: str = Field(max_length=64, pattern=BRIDGE_ERROR_CODE_PATTERN)
    message: str = Field(max_length=2048)
    bridge_protocol_version: str = Field(min_length=1, max_length=64)
    bridge_build_id: str = Field(min_length=1, max_length=128)
    catalog_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation_id: CanonicalUUIDString
    mode: Literal["create", "reconcile"]
    state: Literal[
        "COMPLETE",
        "NONE",
        "PARTIAL",
        "CHANGED",
        "ROLLBACK_VERIFIED",
        "ROLLBACK_FAILED",
        "UNKNOWN",
        "REJECTED",
    ]
    expected_count: int = Field(ge=0, le=MAX_PLACEMENTS)
    matching_count: int = Field(ge=0, le=MAX_PLACEMENTS)
    created_count: int = Field(ge=0, le=MAX_PLACEMENTS)
    rollback_verified: bool
    entity_names: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        max_length=MAX_PLACEMENTS
    )

    @model_validator(mode="after")
    def coherent_status(self) -> Self:
        if self.status == "error" and not self.error_code:
            raise ValueError("error response requires errorCode")
        if self.status == "ok" and self.error_code:
            raise ValueError("successful response must not carry errorCode")
        return self
