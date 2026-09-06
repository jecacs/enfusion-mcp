"""Pure deterministic vegetation planning.

Algorithm contract (``rjmcp-pcg32-poisson-v1``)
================================================

* PRNG is the reference PCG-XSH-RR 64/32 generator.  Seeding performs one
  discarded draw, adds the seed to the 64-bit state, and performs a second
  discarded draw.  The default stream selector is 54.
* Candidate generation consumes exactly two ``next_u32`` draws per bounding-box
  attempt: X first, then Z.  Coordinates use integer multiply-high mapping into
  a lower-inclusive/upper-exclusive quantized bounding box, then make an
  explicit IEEE-754 binary32 round trip before polygon/bounds checks.  Thus the
  queried, echoed, hashed, and eventually applied X/Z coordinate is exactly the
  value Enforce Script can represent.
* The whole in-polygon, in-terrain-bounds candidate pool (at most 1000 points)
  is passed to one terrain batch callback.  No per-point callback is possible.
  Its target size is ``max(64, count * 10)`` capped at 1000, and bounding-box
  attempts stop unconditionally at 10000.
* Terrain results are processed in candidate order.  Each accepted placement
  then consumes exactly three draws: prefab ticket, yaw, scale.  Rejected
  terrain candidates consume no placement draws.  A fixed scale still consumes
  its scale draw so changing a scale range cannot shift later draw categories.
* Polygon boundaries are inside.  Spacing is measured in X/Z.  Exact integer
  equality passes the geometry rule, but the additional conservative binary32
  handler emulation may reject a nominal equality after wire rounding.
  After binary32/fixed-point conversion, a ring is represented by the
  lexicographically smallest rotation across both traversal directions.  A
  cyclic start or winding change therefore does not change Shape identity.
* Slope is ``degrees(atan2(hypot(normalX, normalZ), normalY))``, quantized to a
  microdegree before comparison.  Normal components are first quantized to
  1e-9; the vector must be finite, non-zero, and within one percent of unit
  length.
* Positive weights are quantized to 1e-9 and divided by their integer GCD.
  Every entry receives one ticket; the remainder of the complete ``2**32``
  ticket space is distributed proportionally by largest remainder, with input
  order as the tie-breaker.  Prefab choice consumes one raw uint32 ticket.
  Yaw and scale use integer multiply-high mapping; their upper endpoints are
  excluded unless the configured scale range is a single fixed value.
* Canonical immutable plans contain integer fixed-point values: world/terrain
  coordinates in micrometres, angles in microdegrees, and scale in parts per
  million.  JSON uses the ledger's UTF-8/sorted-key/compact serialization.
  ``createdAt`` and ``expiresAt`` are metadata on :class:`VegetationPlan`, never
  members of the hashed document.
* Entity names are derived after hashing as ``RJMCP_<full plan id>_<index>`` and
  are intentionally absent from the hashed document, avoiding a hash/name
  cycle.

This module performs no I/O, reads no clock, and imports no runtime PRNG.  Time
and the one terrain batch operation are explicitly injected by the caller.
"""

from __future__ import annotations

import hashlib
import math
import struct
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from functools import reduce
from math import gcd
from typing import Final, cast

from pydantic import ValidationError

from enfusion_mcp_rj.bridge_models import BRIDGE_BUILD_ID, BRIDGE_PROTOCOL_VERSION
from enfusion_mcp_rj.catalog import VegetationCatalog
from enfusion_mcp_rj.ledger import canonical_plan_json, plan_digest
from enfusion_mcp_rj.models import (
    PlannedPlacement,
    TargetLayer,
    ToolErrorInfo,
    Vec3,
    VegetationPlanInput,
    VegetationPlanOutput,
)
from enfusion_mcp_rj.path_types import PathValidationError, ResourceName

ALGORITHM_VERSION: Final = "rjmcp-pcg32-poisson-v1"
PLAN_SCHEMA_VERSION: Final = 1
PCG32_MULTIPLIER: Final = 6_364_136_223_846_793_005
PCG32_DEFAULT_STREAM: Final = 54
UINT32_RANGE: Final = 1 << 32
UINT32_MASK: Final = UINT32_RANGE - 1
UINT64_MASK: Final = (1 << 64) - 1

COORDINATE_SCALE: Final = 1_000_000  # micrometres per metre
ANGLE_SCALE: Final = 1_000_000  # microdegrees per degree
SCALE_SCALE: Final = 1_000_000  # parts per one scale unit
WEIGHT_SCALE: Final = 1_000_000_000
NORMAL_SCALE: Final = 1_000_000_000
TERRAIN_Y_EPSILON_UM: Final = 50_000
MAX_ABS_COORDINATE_M: Final = 1_000_000.0
MAX_CONTEXT_TEXT_CHARS: Final = 1_024
MIN_POLYGON_POINTS: Final = 3
MAX_POLYGON_VERTICES: Final = 1_000
MIN_POLYGON_AREA_M2: Final = 0.01
MIN_POLYGON_AREA_TWICE_UNITS: Final = round(
    2 * MIN_POLYGON_AREA_M2 * COORDINATE_SCALE * COORDINATE_SCALE
)
MAX_CANDIDATES: Final = 1_000
MAX_CANDIDATE_ATTEMPTS: Final = 10_000
MIN_CANDIDATES: Final = 64
CANDIDATES_PER_REQUESTED_PLACEMENT: Final = 10
NORMAL_LENGTH_TOLERANCE: Final = 0.01
DEFAULT_PLAN_TTL: Final = timedelta(minutes=30)
MAX_PLACEMENTS: Final = 100
SHA256_HEX_LENGTH: Final = 64
MAX_ENFORCE_YAW_DEG: Final = 359.999969482421875
ENFORCE_DEG_TO_RAD: Final = 0.017453292519943295

_STAT_KEYS: Final = (
    "candidate_attempts",
    "polygon_outside",
    "outside_terrain_bounds",
    "duplicate_candidate",
    "invalid_terrain_result",
    "no_terrain",
    "terrain_y_out_of_bounds",
    "invalid_normal",
    "slope_exceeded",
    "spacing_conflict",
    "unused_after_count",
    "accepted",
)


class PlannerError(ValueError):
    """Base error carrying a stable planner code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class PolygonValidationError(PlannerError):
    """The selected Shape cannot define a safe simple polygon."""


class TerrainContractError(PlannerError):
    """A terrain batch response cannot be associated with its request."""


class CatalogValidationError(PlannerError):
    """The requested palette is not an exact subset of the catalog."""


class NoValidPlacementsError(PlannerError):
    """A non-applyable underfilled result retaining every rejection counter."""

    def __init__(self, rejection_stats: dict[str, int]) -> None:
        super().__init__(
            "NO_VALID_PLACEMENTS",
            "Terrain filtering produced no valid vegetation placement; no plan was stored.",
        )
        self.rejection_stats = tuple(rejection_stats.items())


class PCG32:
    """Reference PCG-XSH-RR 64/32 with explicit 64-bit wraparound.

    ``PCG32(seed=42, stream=54)`` starts with the published reference vector
    ``a15c02b7, 7b47f409, ba1d3330, 83d2f293, bfa4784b, cbed606e``.
    """

    __slots__ = ("_increment", "_state")

    def __init__(self, seed: int, stream: int = PCG32_DEFAULT_STREAM) -> None:
        _validate_uint(seed, bits=64, field_name="PCG32 seed")
        _validate_uint(stream, bits=63, field_name="PCG32 stream")
        self._state = 0
        self._increment = ((stream << 1) | 1) & UINT64_MASK
        self.next_u32()
        self._state = (self._state + seed) & UINT64_MASK
        self.next_u32()

    @classmethod
    def _from_state(cls, state: int, increment: int) -> PCG32:
        generator = object.__new__(cls)
        generator._state = state & UINT64_MASK
        generator._increment = increment & UINT64_MASK
        return generator

    @property
    def state(self) -> int:
        return self._state

    @property
    def increment(self) -> int:
        return self._increment

    def next_u32(self) -> int:
        """Return the next unsigned 32-bit value."""

        old_state = self._state
        self._state = (old_state * PCG32_MULTIPLIER + self._increment) & UINT64_MASK
        xorshifted = (((old_state >> 18) ^ old_state) >> 27) & UINT32_MASK
        rotation = (old_state >> 59) & 31
        return ((xorshifted >> rotation) | (xorshifted << ((-rotation) & 31))) & UINT32_MASK


def _validate_uint(value: int, *, bits: int, field_name: str) -> None:
    if type(value) is not int or not 0 <= value < (1 << bits):
        raise ValueError(f"{field_name} must be an unsigned {bits}-bit integer")


@dataclass(frozen=True, slots=True)
class TerrainBounds:
    """Finite full 3D terrain bounds reported by ``RJMCP_GetContext``."""

    min_x: float
    min_y: float
    min_z: float
    max_x: float
    max_y: float
    max_z: float

    def __post_init__(self) -> None:
        values = (
            self.min_x,
            self.min_y,
            self.min_z,
            self.max_x,
            self.max_y,
            self.max_z,
        )
        for index, value in enumerate(values):
            _finite_number(value, field_name=f"terrain_bounds[{index}]", coordinate=True)
        if self.min_x >= self.max_x or self.min_z >= self.max_z or self.min_y > self.max_y:
            raise PlannerError("INVALID_TERRAIN_BOUNDS", "terrain bounds must be ordered")


@dataclass(frozen=True, slots=True)
class PlanningContext:
    """Read-only WorldEditor context required by the vegetation planner."""

    world_path: str
    mode: str
    current_subscene: int
    current_layer_id: int
    active_layer_path: str
    terrain_bounds: TerrainBounds
    selection_count: int
    selected_name: str
    selected_class: str
    polygon_compatible: bool
    shape_closed: bool
    shape_points_world: tuple[Vec3, ...]
    bridge_protocol_version: str
    bridge_build_id: str
    bridge_catalog_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "shape_points_world", tuple(self.shape_points_world))


@dataclass(frozen=True, slots=True)
class TerrainQuery:
    """One quantized X/Z point sent in the single terrain batch."""

    x: float
    z: float


@dataclass(frozen=True, slots=True)
class TerrainSample:
    """One ordered ``RJMCP_TerrainSample`` result with a full surface normal."""

    requested_x: float
    requested_z: float
    has_terrain: bool
    terrain_y: float | None = None
    normal_x: float | None = None
    normal_y: float | None = None
    normal_z: float | None = None


@dataclass(frozen=True, slots=True)
class NormalizedPaletteItem:
    """Exact resource and its share of the complete uint32 ticket space."""

    prefab: str
    tickets: int


@dataclass(frozen=True, slots=True)
class ValidatedVegetationInput:
    """Context-independent input checks, safe to complete before connecting."""

    request: VegetationPlanInput
    catalog_hash: str
    palette: tuple[NormalizedPaletteItem, ...]
    min_spacing_units: int
    max_slope_units: int
    scale_min_units: int
    scale_max_units: int


@dataclass(frozen=True, slots=True)
class _Point2Units:
    x: int
    z: int


@dataclass(frozen=True, slots=True)
class _Point3Units:
    x: int
    y: int
    z: int


@dataclass(frozen=True, slots=True)
class ValidatedPolygon:
    """Quantized, simple polygon whose boundary is considered inside."""

    points_world: tuple[Vec3, ...]
    points_units: tuple[_Point3Units, ...] = field(repr=False)
    shape_hash: str
    signed_area_twice_units: int
    min_x_units: int
    min_z_units: int
    max_x_units: int
    max_z_units: int

    @property
    def area_m2(self) -> float:
        return abs(self.signed_area_twice_units) / (2 * COORDINATE_SCALE**2)

    def contains(self, x: float, z: float) -> bool:
        point = _Point2Units(
            _quantize(x, COORDINATE_SCALE, field_name="point.x", coordinate=True),
            _quantize(z, COORDINATE_SCALE, field_name="point.z", coordinate=True),
        )
        polygon = tuple(_Point2Units(item.x, item.z) for item in self.points_units)
        return _point_in_polygon_units(point, polygon)


@dataclass(frozen=True, slots=True)
class _PreparedCandidate:
    x_units: int
    z_units: int
    query_x: float
    query_z: float


@dataclass(frozen=True, slots=True)
class PreparedVegetationPlan:
    """Validated deterministic candidate batch awaiting one terrain response."""

    world_path: str
    mode: str
    current_subscene: int
    current_layer_id: int
    active_layer_path: str
    selected_name: str
    selected_class: str
    bridge_protocol_version: str
    bridge_build_id: str
    polygon: ValidatedPolygon
    terrain_bounds_units: tuple[int, int, int, int, int, int]
    requested_count: int
    seed: int
    min_spacing_units: int
    max_slope_units: int
    target_layer: TargetLayer
    scale_min_units: int
    scale_max_units: int
    catalog_version: str
    catalog_hash: str
    palette: tuple[NormalizedPaletteItem, ...]
    candidates: tuple[_PreparedCandidate, ...] = field(repr=False)
    terrain_queries: tuple[TerrainQuery, ...]
    generation_stats: tuple[tuple[str, int], ...]
    rng_state: int = field(repr=False)
    rng_increment: int = field(repr=False)


@dataclass(frozen=True, slots=True)
class VegetationPlan:
    """Final immutable plan, plus unhashed creation/expiry metadata."""

    plan_id: str
    canonical_json: str
    placements: tuple[PlannedPlacement, ...]
    requested_count: int
    target_layer: TargetLayer
    rejection_stats: tuple[tuple[str, int], ...]
    warnings: tuple[str, ...]
    created_at: datetime
    expires_at: datetime

    def as_output(self) -> VegetationPlanOutput:
        return VegetationPlanOutput(
            ok=True,
            plan_id=self.plan_id,
            algorithm_version=ALGORITHM_VERSION,
            requested_count=self.requested_count,
            placements=list(self.placements),
            rejection_stats=dict(self.rejection_stats),
            warnings=list(self.warnings),
            created_at=self.created_at.isoformat(),
            expires_at=self.expires_at.isoformat(),
            target_layer=self.target_layer,
        )


TerrainBatchSampler = Callable[[tuple[TerrainQuery, ...]], Sequence[TerrainSample]]


def validate_polygon(points_world: Sequence[Vec3]) -> ValidatedPolygon:
    """Quantize and validate a simple Shape polygon.

    The point list must not repeat its first point at the end; closedness is a
    separate Workbench Shape property represented by ``PlanningContext``.
    """

    points = tuple(points_world)
    if not MIN_POLYGON_POINTS <= len(points) <= MAX_POLYGON_VERTICES:
        raise PolygonValidationError(
            "INVALID_POLYGON_CARDINALITY",
            f"polygon must contain between 3 and {MAX_POLYGON_VERTICES} points",
        )

    quantized: list[_Point3Units] = []
    for index, point in enumerate(points):
        if type(point) is not Vec3:
            raise PolygonValidationError(
                "INVALID_POLYGON_POINT",
                f"polygon point {index} must be Vec3",
            )
        quantized.append(
            _Point3Units(
                _quantize(
                    _float32(
                        point.x,
                        field_name=f"polygon[{index}].x",
                        coordinate=True,
                    ),
                    COORDINATE_SCALE,
                    field_name=f"polygon[{index}].x",
                    coordinate=True,
                ),
                _quantize(
                    _float32(
                        point.y,
                        field_name=f"polygon[{index}].y",
                        coordinate=True,
                    ),
                    COORDINATE_SCALE,
                    field_name=f"polygon[{index}].y",
                    coordinate=True,
                ),
                _quantize(
                    _float32(
                        point.z,
                        field_name=f"polygon[{index}].z",
                        coordinate=True,
                    ),
                    COORDINATE_SCALE,
                    field_name=f"polygon[{index}].z",
                    coordinate=True,
                ),
            )
        )

    points_2d = tuple(_Point2Units(point.x, point.z) for point in quantized)
    if len(set(points_2d)) != len(points_2d):
        raise PolygonValidationError(
            "DUPLICATE_POLYGON_VERTEX",
            "polygon contains duplicate X/Z vertices or a repeated closing point",
        )
    if all(_orientation(points_2d[0], points_2d[1], point) == 0 for point in points_2d[2:]):
        raise PolygonValidationError(
            "DEGENERATE_POLYGON",
            "polygon vertices are collinear",
        )
    _reject_overlapping_adjacent_edges(points_2d)
    _reject_self_intersections(points_2d)

    area_twice = _signed_area_twice(points_2d)
    if abs(area_twice) < MIN_POLYGON_AREA_TWICE_UNITS:
        raise PolygonValidationError(
            "DEGENERATE_POLYGON",
            f"polygon area must be at least {MIN_POLYGON_AREA_M2} m^2",
        )

    canonical_units = _canonicalize_ring(tuple(quantized))
    canonical_2d = tuple(_Point2Units(point.x, point.z) for point in canonical_units)
    canonical_points = tuple(
        Vec3(
            x=_from_units(point.x, COORDINATE_SCALE),
            y=_from_units(point.y, COORDINATE_SCALE),
            z=_from_units(point.z, COORDINATE_SCALE),
        )
        for point in canonical_units
    )
    polygon_payload = {
        "coordinateScale": COORDINATE_SCALE,
        "pointsWorldUm": [{"x": point.x, "y": point.y, "z": point.z} for point in canonical_units],
    }
    shape_hash = hashlib.sha256(canonical_plan_json(polygon_payload).encode("utf-8")).hexdigest()
    return ValidatedPolygon(
        points_world=canonical_points,
        points_units=canonical_units,
        shape_hash=shape_hash,
        signed_area_twice_units=_signed_area_twice(canonical_2d),
        min_x_units=min(point.x for point in canonical_units),
        min_z_units=min(point.z for point in canonical_units),
        max_x_units=max(point.x for point in canonical_units),
        max_z_units=max(point.z for point in canonical_units),
    )


def validate_vegetation_input(
    request: VegetationPlanInput,
    catalog: VegetationCatalog,
) -> ValidatedVegetationInput:
    """Validate every input independent of Workbench before opening a connection.

    Rebuild through Pydantic, including nested palette entries, because direct
    library callers can use ``model_construct``/``model_copy`` or mutate a list
    inside a frozen model. Both the service and pure planner use this boundary.
    """

    if type(request) is not VegetationPlanInput:
        raise PlannerError("INVALID_REQUEST", "request must be VegetationPlanInput")
    try:
        request = VegetationPlanInput.model_validate(request.model_dump(warnings=False))
    except (ValidationError, ValueError, TypeError) as error:
        raise PlannerError("INVALID_REQUEST", f"Invalid vegetation input: {error}") from error
    if type(catalog) is not VegetationCatalog:
        raise CatalogValidationError("INVALID_CATALOG", "catalog must be VegetationCatalog")
    if not catalog.production_ready:
        raise CatalogValidationError("CATALOG_NOT_READY", "production catalog is not verified")
    if (
        not catalog.version
        or catalog.version.strip() != catalog.version
        or len(catalog.version) > MAX_CONTEXT_TEXT_CHARS
    ):
        raise CatalogValidationError(
            "INVALID_CATALOG", "catalog version must be non-empty and trimmed"
        )
    try:
        catalog_hash = catalog.catalog_hash
    except (UnicodeError, ValueError) as error:
        raise CatalogValidationError(
            "INVALID_CATALOG", "catalog cannot be serialized as canonical UTF-8"
        ) from error
    palette = _normalize_palette(request, catalog)
    min_spacing_units = _wire_units(
        request.min_spacing_m,
        COORDINATE_SCALE,
        field_name="min_spacing_m",
    )
    if min_spacing_units <= 0:
        raise PlannerError("INVALID_SPACING", "min_spacing_m is below V1 precision")
    max_slope_units = _wire_units(
        request.max_slope_deg,
        ANGLE_SCALE,
        field_name="max_slope_deg",
    )
    if max_slope_units >= 90 * ANGLE_SCALE:
        raise PlannerError(
            "INVALID_SLOPE",
            "max_slope_deg rounds to 90 degrees at V1 precision",
        )
    scale_min_units = _wire_units(
        request.scale_min,
        SCALE_SCALE,
        field_name="scale_min",
    )
    scale_max_units = _wire_units(
        request.scale_max,
        SCALE_SCALE,
        field_name="scale_max",
    )
    if scale_min_units <= 0 or scale_max_units <= 0:
        raise PlannerError("INVALID_SCALE", "scale range is below V1 precision")

    return ValidatedVegetationInput(
        request=request,
        catalog_hash=catalog_hash,
        palette=palette,
        min_spacing_units=min_spacing_units,
        max_slope_units=max_slope_units,
        scale_min_units=scale_min_units,
        scale_max_units=scale_max_units,
    )


def prepare_vegetation_plan(
    context: PlanningContext,
    request: VegetationPlanInput,
    catalog: VegetationCatalog,
) -> PreparedVegetationPlan:
    """Validate context/input and deterministically build one terrain batch."""

    validated = validate_vegetation_input(request, catalog)
    request = validated.request
    _validate_context(context)
    if context.active_layer_path != request.target_layer:
        raise PlannerError(
            "TARGET_LAYER_NOT_ACTIVE",
            "requested target layer must be the exact active root-level layer",
        )
    if context.bridge_catalog_hash != validated.catalog_hash:
        raise CatalogValidationError(
            "CATALOG_MISMATCH",
            "Workbench bridge catalog hash does not match the Python exact allowlist",
        )
    polygon = validate_polygon(context.shape_points_world)
    bounds = _quantized_terrain_bounds(context.terrain_bounds)

    rng = PCG32(request.seed)
    stats = _empty_stats()
    candidate_target = min(
        MAX_CANDIDATES,
        max(MIN_CANDIDATES, request.count * CANDIDATES_PER_REQUESTED_PLACEMENT),
    )
    candidates: list[_PreparedCandidate] = []
    seen: set[_Point2Units] = set()
    polygon_2d = tuple(_Point2Units(point.x, point.z) for point in polygon.points_units)

    sample_min_x = max(polygon.min_x_units, bounds[0])
    sample_min_z = max(polygon.min_z_units, bounds[2])
    sample_max_x = min(polygon.max_x_units, bounds[3])
    sample_max_z = min(polygon.max_z_units, bounds[5])
    x_span = sample_max_x - sample_min_x
    z_span = sample_max_z - sample_min_z
    if x_span <= 0 or z_span <= 0:
        raise PlannerError(
            "NO_TERRAIN_INTERSECTION",
            "polygon bounding box does not overlap terrain X/Z bounds",
        )

    while (
        len(candidates) < candidate_target and stats["candidate_attempts"] < MAX_CANDIDATE_ATTEMPTS
    ):
        stats["candidate_attempts"] += 1
        raw_x_units = sample_min_x + (x_span * rng.next_u32()) // UINT32_RANGE
        raw_z_units = sample_min_z + (z_span * rng.next_u32()) // UINT32_RANGE
        query_x = _float32(
            _from_units(raw_x_units, COORDINATE_SCALE),
            field_name="candidate.x",
            coordinate=True,
        )
        query_z = _float32(
            _from_units(raw_z_units, COORDINATE_SCALE),
            field_name="candidate.z",
            coordinate=True,
        )
        x_units = _quantize(
            query_x,
            COORDINATE_SCALE,
            field_name="candidate.x",
            coordinate=True,
        )
        z_units = _quantize(
            query_z,
            COORDINATE_SCALE,
            field_name="candidate.z",
            coordinate=True,
        )
        point = _Point2Units(x_units, z_units)
        if not _point_in_polygon_units(point, polygon_2d):
            stats["polygon_outside"] += 1
            continue
        if point in seen:
            stats["duplicate_candidate"] += 1
            continue
        seen.add(point)
        if not _xz_in_bounds(point, bounds):
            stats["outside_terrain_bounds"] += 1
            continue
        candidates.append(_PreparedCandidate(x_units, z_units, query_x, query_z))

    if not candidates:
        raise PlannerError(
            "NO_TERRAIN_CANDIDATES",
            "bounded generation produced no candidate safe to terrain-sample",
        )

    terrain_queries = tuple(
        TerrainQuery(
            x=candidate.query_x,
            z=candidate.query_z,
        )
        for candidate in candidates
    )
    return PreparedVegetationPlan(
        world_path=context.world_path,
        mode=context.mode,
        current_subscene=context.current_subscene,
        current_layer_id=context.current_layer_id,
        active_layer_path=context.active_layer_path,
        selected_name=context.selected_name,
        selected_class=context.selected_class,
        bridge_protocol_version=context.bridge_protocol_version,
        bridge_build_id=context.bridge_build_id,
        polygon=polygon,
        terrain_bounds_units=bounds,
        requested_count=request.count,
        seed=request.seed,
        min_spacing_units=validated.min_spacing_units,
        max_slope_units=validated.max_slope_units,
        target_layer=request.target_layer,
        scale_min_units=validated.scale_min_units,
        scale_max_units=validated.scale_max_units,
        catalog_version=catalog.version,
        catalog_hash=validated.catalog_hash,
        palette=validated.palette,
        candidates=tuple(candidates),
        terrain_queries=terrain_queries,
        generation_stats=tuple(stats.items()),
        rng_state=rng.state,
        rng_increment=rng.increment,
    )


def finalize_vegetation_plan(
    prepared: PreparedVegetationPlan,
    terrain_samples: Sequence[TerrainSample],
    *,
    created_at: datetime,
    ttl: timedelta = DEFAULT_PLAN_TTL,
) -> VegetationPlan:
    """Filter one ordered terrain batch and build/hash the immutable plan."""

    if type(prepared) is not PreparedVegetationPlan:
        raise PlannerError("INVALID_PREPARED_PLAN", "prepared plan has the wrong type")
    if not _is_bounded_sequence(terrain_samples):
        raise TerrainContractError(
            "INVALID_TERRAIN_BATCH", "terrain response must be a bounded sequence"
        )
    if len(terrain_samples) != len(prepared.candidates):
        raise TerrainContractError(
            "TERRAIN_CARDINALITY_MISMATCH",
            f"terrain response has {len(terrain_samples)} items for "
            f"{len(prepared.candidates)} queries",
        )
    samples = tuple(terrain_samples)

    created = _aware_utc(created_at)
    if not isinstance(ttl, timedelta) or ttl <= timedelta(0):
        raise PlannerError("INVALID_TTL", "plan TTL must be a positive timedelta")
    try:
        expires = created + ttl
    except OverflowError as error:
        raise PlannerError("INVALID_TTL", "plan expiry is outside datetime range") from error

    stats = _empty_stats()
    stats.update(dict(prepared.generation_stats))
    accepted_units: list[tuple[_PreparedCandidate, int, str, int, int]] = []
    rng = PCG32._from_state(prepared.rng_state, prepared.rng_increment)

    # Validate every item in the untrusted batch before placement selection.
    # A malformed trailing item must not be hidden merely because ``count`` was
    # already reached by an earlier prefix.
    validated_samples = tuple(
        _validated_terrain_sample(
            candidate,
            sample,
            prepared.terrain_bounds_units,
            prepared.max_slope_units,
            stats,
        )
        for candidate, sample in zip(prepared.candidates, samples, strict=True)
    )

    for candidate, parsed in zip(prepared.candidates, validated_samples, strict=True):
        if parsed is None:
            continue
        if len(accepted_units) >= prepared.requested_count:
            stats["unused_after_count"] += 1
            continue
        terrain_y_units, _slope_units = parsed
        if any(
            _spacing_conflict(candidate, prior[0], prepared.min_spacing_units)
            for prior in accepted_units
        ):
            stats["spacing_conflict"] += 1
            continue

        prefab = _choose_prefab(rng.next_u32(), prepared.palette)
        yaw_units = _quantize(
            yaw_degrees_from_u32(rng.next_u32()),
            ANGLE_SCALE,
            field_name="yaw",
        )
        scale_draw = rng.next_u32()
        scale_span = prepared.scale_max_units - prepared.scale_min_units
        raw_scale_units = prepared.scale_min_units + (
            (scale_span * scale_draw) // UINT32_RANGE if scale_span else 0
        )
        scale_units = _wire_units(
            _from_units(raw_scale_units, SCALE_SCALE),
            SCALE_SCALE,
            field_name="scale",
        )
        scale_units = min(
            prepared.scale_max_units,
            max(prepared.scale_min_units, scale_units),
        )
        accepted_units.append((candidate, terrain_y_units, prefab, yaw_units, scale_units))
        stats["accepted"] += 1

    if not accepted_units:
        raise NoValidPlacementsError(stats)

    immutable_placements = [
        {
            "index": index,
            "positionUm": {
                "x": candidate.x_units,
                "y": terrain_y_units,
                "z": candidate.z_units,
            },
            "prefab": prefab,
            "scalePpm": scale_units,
            "yawMicrodegrees": yaw_units,
        }
        for index, (candidate, terrain_y_units, prefab, yaw_units, scale_units) in enumerate(
            accepted_units
        )
    ]
    immutable = {
        "algorithmVersion": ALGORITHM_VERSION,
        "catalog": {
            "hash": prepared.catalog_hash,
            "version": prepared.catalog_version,
        },
        "constraints": {
            "count": prepared.requested_count,
            "maxSlopeMicrodegrees": prepared.max_slope_units,
            "minSpacingUm": prepared.min_spacing_units,
            "scaleMaxPpm": prepared.scale_max_units,
            "scaleMinPpm": prepared.scale_min_units,
            "terrainYEpsilonUm": TERRAIN_Y_EPSILON_UM,
        },
        "context": {
            "activeLayerPath": prepared.active_layer_path,
            "bridgeBuildId": prepared.bridge_build_id,
            "bridgeProtocolVersion": prepared.bridge_protocol_version,
            "currentLayerId": prepared.current_layer_id,
            "currentSubscene": prepared.current_subscene,
            "mode": prepared.mode,
            "terrainBoundsUm": {
                "maxX": prepared.terrain_bounds_units[3],
                "maxY": prepared.terrain_bounds_units[4],
                "maxZ": prepared.terrain_bounds_units[5],
                "minX": prepared.terrain_bounds_units[0],
                "minY": prepared.terrain_bounds_units[1],
                "minZ": prepared.terrain_bounds_units[2],
            },
            "worldPath": prepared.world_path,
        },
        "palette": [
            {"normalizedTicketsU32": item.tickets, "prefab": item.prefab}
            for item in prepared.palette
        ],
        "placements": immutable_placements,
        "quantization": {
            "angleMicrodegreesPerDegree": ANGLE_SCALE,
            "coordinateMicrometersPerMeter": COORDINATE_SCALE,
            "scalePartsPerUnit": SCALE_SCALE,
            "weightTicketTotal": UINT32_RANGE,
        },
        "rejectionStats": stats,
        "seed": prepared.seed,
        "schemaVersion": PLAN_SCHEMA_VERSION,
        "shape": {
            "class": prepared.selected_class,
            "hash": prepared.polygon.shape_hash,
            "name": prepared.selected_name,
            "pointsWorldUm": [
                {"x": point.x, "y": point.y, "z": point.z}
                for point in prepared.polygon.points_units
            ],
        },
        "targetLayer": prepared.target_layer,
    }
    canonical = canonical_plan_json(immutable)
    plan_id = plan_digest(canonical)
    placements = tuple(
        PlannedPlacement(
            index=index,
            prefab=prefab,
            entity_name=entity_name(plan_id, index),
            position=Vec3(
                x=_from_units(candidate.x_units, COORDINATE_SCALE),
                y=_from_units(terrain_y_units, COORDINATE_SCALE),
                z=_from_units(candidate.z_units, COORDINATE_SCALE),
            ),
            yaw_deg=_from_units(yaw_units, ANGLE_SCALE),
            scale=_from_units(scale_units, SCALE_SCALE),
        )
        for index, (candidate, terrain_y_units, prefab, yaw_units, scale_units) in enumerate(
            accepted_units
        )
    )
    warnings = ("UNDERFILLED",) if len(placements) < prepared.requested_count else ()
    return VegetationPlan(
        plan_id=plan_id,
        canonical_json=canonical,
        placements=placements,
        requested_count=prepared.requested_count,
        target_layer=prepared.target_layer,
        rejection_stats=tuple(stats.items()),
        warnings=warnings,
        created_at=created,
        expires_at=expires,
    )


def plan_vegetation(  # noqa: PLR0913 - the pure boundary keeps all effects explicit
    context: PlanningContext,
    request: VegetationPlanInput,
    catalog: VegetationCatalog,
    terrain_sampler: TerrainBatchSampler,
    *,
    created_at: datetime,
    ttl: timedelta = DEFAULT_PLAN_TTL,
) -> VegetationPlan:
    """Convenience pipeline which invokes ``terrain_sampler`` exactly once."""

    prepared = prepare_vegetation_plan(context, request, catalog)
    samples = terrain_sampler(prepared.terrain_queries)
    return finalize_vegetation_plan(prepared, samples, created_at=created_at, ttl=ttl)


def entity_name(plan_id: str, index: int) -> str:
    """Derive the deterministic Enfusion entity name outside the plan hash."""

    if len(plan_id) != SHA256_HEX_LENGTH or any(
        character not in "0123456789abcdef" for character in plan_id
    ):
        raise ValueError("plan_id must be 64 lowercase hexadecimal characters")
    if type(index) is not int or not 0 <= index < MAX_PLACEMENTS:
        raise ValueError("placement index must be between 0 and 99")
    return f"RJMCP_{plan_id}_{index}"


def yaw_degrees_from_u32(draw: int) -> float:
    """Map a raw draw to an Enforce-representable yaw in ``[0, 360)``."""

    _validate_uint(draw, bits=32, field_name="yaw draw")
    unrounded = (draw * 360 * ANGLE_SCALE) // UINT32_RANGE / ANGLE_SCALE
    wire = _float32(unrounded, field_name="yaw", coordinate=False)
    return min(wire, MAX_ENFORCE_YAW_DEG)


def underfilled_output(
    request: object,
    error: PlannerError,
) -> VegetationPlanOutput:
    """Build a typed failure for service adapters; planning itself raises errors."""

    requested_count = getattr(request, "count", None)
    target_layer = getattr(request, "target_layer", None)
    if type(requested_count) is not int or not 1 <= requested_count <= MAX_PLACEMENTS:
        requested_count = 0
    if type(target_layer) is not str or target_layer not in {"MCP_Preview", "MCP_Vegetation"}:
        target_layer = None
    return VegetationPlanOutput(
        ok=False,
        algorithm_version=ALGORITHM_VERSION,
        requested_count=requested_count,
        target_layer=cast("TargetLayer | None", target_layer),
        rejection_stats=(
            dict(error.rejection_stats) if isinstance(error, NoValidPlacementsError) else {}
        ),
        warnings=["UNDERFILLED"] if isinstance(error, NoValidPlacementsError) else [],
        error=ToolErrorInfo(code=error.code, message=str(error)[:2048]),
    )


def _validate_context(  # noqa: PLR0912 - ordered fail-closed context contract
    context: PlanningContext,
) -> None:
    if type(context) is not PlanningContext:
        raise PlannerError("INVALID_CONTEXT", "context must be PlanningContext")
    for field_name, value in (
        ("mode", context.mode),
        ("active_layer_path", context.active_layer_path),
        ("selected_name", context.selected_name),
        ("selected_class", context.selected_class),
        ("bridge_protocol_version", context.bridge_protocol_version),
        ("bridge_build_id", context.bridge_build_id),
    ):
        _validated_text(value, field_name=field_name)
    try:
        ResourceName(context.world_path)
    except (PathValidationError, TypeError) as error:
        raise PlannerError("INVALID_WORLD", f"invalid world resource: {error}") from error
    _validated_text(context.world_path, field_name="world_path")
    if type(context.current_subscene) is not int:
        raise PlannerError("INVALID_SUBSCENE", "current_subscene must be an integer")
    if context.current_subscene < 0:
        raise PlannerError("INVALID_SUBSCENE", "current_subscene must be non-negative")
    if type(context.current_layer_id) is not int:
        raise PlannerError("INVALID_LAYER", "current_layer_id must be an integer")
    if context.current_layer_id < 0:
        raise PlannerError("INVALID_LAYER", "current_layer_id must be non-negative")
    if context.mode != "edit":
        raise PlannerError("INVALID_EDITOR_MODE", "planning requires Workbench edit mode")
    if context.selection_count != 1 or type(context.selection_count) is not int:
        raise PlannerError("INVALID_SELECTION", "planning requires exactly one selected object")
    if context.polygon_compatible is not True:
        raise PlannerError("SELECTION_NOT_SHAPE", "selected object is not a Shape")
    if context.shape_closed is not True:
        raise PlannerError("SHAPE_NOT_CLOSED", "selected Shape must be closed")
    if type(context.terrain_bounds) is not TerrainBounds:
        raise PlannerError("INVALID_TERRAIN_BOUNDS", "terrain_bounds must be TerrainBounds")
    if len(context.bridge_catalog_hash) != SHA256_HEX_LENGTH or any(
        character not in "0123456789abcdef" for character in context.bridge_catalog_hash
    ):
        raise PlannerError(
            "INVALID_BRIDGE_CATALOG_HASH",
            "bridge_catalog_hash must be 64 lowercase hexadecimal characters",
        )
    if context.bridge_protocol_version != BRIDGE_PROTOCOL_VERSION:
        raise PlannerError(
            "INCOMPATIBLE_BRIDGE",
            "Workbench bridge protocol version is not supported",
        )
    if context.bridge_build_id != BRIDGE_BUILD_ID:
        raise PlannerError(
            "INCOMPATIBLE_BRIDGE",
            "Workbench bridge build ID is not supported",
        )


def _validated_text(value: str, *, field_name: str) -> str:
    if (
        type(value) is not str
        or not value
        or value.strip() != value
        or "\x00" in value
        or len(value) > MAX_CONTEXT_TEXT_CHARS
    ):
        raise PlannerError(
            "INVALID_CONTEXT",
            f"{field_name} must be 1..{MAX_CONTEXT_TEXT_CHARS} trimmed characters without NUL",
        )
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise PlannerError(
            "INVALID_CONTEXT",
            f"{field_name} must be valid UTF-8 text",
        ) from error
    return value


def _normalize_palette(
    request: VegetationPlanInput,
    catalog: VegetationCatalog,
) -> tuple[NormalizedPaletteItem, ...]:
    raw_units: list[int] = []
    prefabs: list[str] = []
    for index, item in enumerate(request.palette):
        if not catalog.contains(item.prefab):
            raise CatalogValidationError(
                "PREFAB_NOT_ALLOWED",
                f"palette[{index}] is not an exact catalog resource",
            )
        units = _quantize(item.weight, WEIGHT_SCALE, field_name=f"palette[{index}].weight")
        if units <= 0:
            raise CatalogValidationError(
                "INVALID_PALETTE_WEIGHT",
                f"palette[{index}] weight is below V1 precision",
            )
        prefabs.append(item.prefab)
        raw_units.append(units)

    common = reduce(gcd, raw_units)
    relative = [units // common for units in raw_units]
    total = sum(relative)
    distributable = UINT32_RANGE - len(relative)
    allocations: list[int] = []
    remainders: list[tuple[int, int]] = []
    for index, units in enumerate(relative):
        quotient, remainder = divmod(units * distributable, total)
        allocations.append(quotient + 1)
        remainders.append((remainder, index))
    missing = UINT32_RANGE - sum(allocations)
    for _, index in sorted(remainders, key=lambda item: (-item[0], item[1]))[:missing]:
        allocations[index] += 1
    if sum(allocations) != UINT32_RANGE:  # pragma: no cover - arithmetic invariant
        raise AssertionError("normalized palette does not cover the uint32 ticket space")
    return tuple(
        NormalizedPaletteItem(prefab=prefab, tickets=tickets)
        for prefab, tickets in zip(prefabs, allocations, strict=True)
    )


def _choose_prefab(draw: int, palette: tuple[NormalizedPaletteItem, ...]) -> str:
    cumulative = 0
    for item in palette:
        cumulative += item.tickets
        if draw < cumulative:
            return item.prefab
    raise AssertionError("palette ticket space is incomplete")  # pragma: no cover


def _quantized_terrain_bounds(bounds: TerrainBounds) -> tuple[int, int, int, int, int, int]:
    values = (
        bounds.min_x,
        bounds.min_y,
        bounds.min_z,
        bounds.max_x,
        bounds.max_y,
        bounds.max_z,
    )
    quantized_values = [
        _quantize(
            value,
            COORDINATE_SCALE,
            field_name=f"terrain_bounds[{index}]",
            coordinate=True,
        )
        for index, value in enumerate(values)
    ]
    quantized = (
        quantized_values[0],
        quantized_values[1],
        quantized_values[2],
        quantized_values[3],
        quantized_values[4],
        quantized_values[5],
    )
    if quantized[0] >= quantized[3] or quantized[2] >= quantized[5]:
        raise PlannerError(
            "INVALID_TERRAIN_BOUNDS",
            "terrain X/Z extent collapses at V1 coordinate precision",
        )
    return quantized


def _validated_terrain_sample(  # noqa: PLR0911, PLR0912 - explicit rejection precedence
    candidate: _PreparedCandidate,
    sample: TerrainSample,
    bounds: tuple[int, int, int, int, int, int],
    max_slope_units: int,
    stats: dict[str, int],
) -> tuple[int, int] | None:
    if type(sample) is not TerrainSample:
        stats["invalid_terrain_result"] += 1
        return None
    try:
        requested_x = _quantize(
            sample.requested_x,
            COORDINATE_SCALE,
            field_name="terrain.requested_x",
            coordinate=True,
        )
        requested_z = _quantize(
            sample.requested_z,
            COORDINATE_SCALE,
            field_name="terrain.requested_z",
            coordinate=True,
        )
    except PlannerError:
        stats["invalid_terrain_result"] += 1
        return None
    if requested_x != candidate.x_units or requested_z != candidate.z_units:
        stats["invalid_terrain_result"] += 1
        return None
    if not _is_strict_bool(sample.has_terrain):
        stats["invalid_terrain_result"] += 1
        return None
    if not sample.has_terrain:
        optional = (sample.terrain_y, sample.normal_x, sample.normal_y, sample.normal_z)
        if any(value is not None and not _is_finite_number(value) for value in optional):
            stats["invalid_terrain_result"] += 1
        else:
            stats["no_terrain"] += 1
        return None

    terrain_y_raw = sample.terrain_y
    normal_x_raw = sample.normal_x
    normal_y_raw = sample.normal_y
    normal_z_raw = sample.normal_z
    numeric = (terrain_y_raw, normal_x_raw, normal_y_raw, normal_z_raw)
    if any(value is None or not _is_finite_number(value) for value in numeric):
        stats["invalid_terrain_result"] += 1
        return None
    assert terrain_y_raw is not None
    assert normal_x_raw is not None
    assert normal_y_raw is not None
    assert normal_z_raw is not None
    terrain_y = float(terrain_y_raw)
    normal_x = float(normal_x_raw)
    normal_y = float(normal_y_raw)
    normal_z = float(normal_z_raw)
    try:
        terrain_y_units = _quantize(
            terrain_y,
            COORDINATE_SCALE,
            field_name="terrain.y",
            coordinate=True,
        )
    except PlannerError:
        stats["invalid_terrain_result"] += 1
        return None
    if not bounds[1] <= terrain_y_units <= bounds[4]:
        stats["terrain_y_out_of_bounds"] += 1
        return None

    normal_x = _from_units(
        _quantize(normal_x, NORMAL_SCALE, field_name="terrain.normal_x"), NORMAL_SCALE
    )
    normal_y = _from_units(
        _quantize(normal_y, NORMAL_SCALE, field_name="terrain.normal_y"), NORMAL_SCALE
    )
    normal_z = _from_units(
        _quantize(normal_z, NORMAL_SCALE, field_name="terrain.normal_z"), NORMAL_SCALE
    )
    normal_length = math.hypot(normal_x, normal_y, normal_z)
    if normal_length == 0 or abs(normal_length - 1.0) > NORMAL_LENGTH_TOLERANCE:
        stats["invalid_normal"] += 1
        return None
    slope = math.degrees(math.atan2(math.hypot(normal_x, normal_z), normal_y))
    slope_units = _quantize(slope, ANGLE_SCALE, field_name="terrain.slope")
    # The maximum is included: a requested 30 degree limit accepts 30 exactly.
    if slope_units > max_slope_units:
        stats["slope_exceeded"] += 1
        return None
    if _engine_slope_exceeded(normal_x, normal_y, normal_z, max_slope_units):
        stats["slope_exceeded"] += 1
        return None
    return terrain_y_units, slope_units


def _finite_number(value: object, *, field_name: str, coordinate: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlannerError("INVALID_NUMBER", f"{field_name} must be a number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise PlannerError("INVALID_NUMBER", f"{field_name} must be finite")
    if coordinate and abs(numeric) > MAX_ABS_COORDINATE_M:
        raise PlannerError(
            "COORDINATE_OUT_OF_RANGE",
            f"{field_name} exceeds {MAX_ABS_COORDINATE_M:g} metres",
        )
    return numeric


def _is_finite_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _is_strict_bool(value: object) -> bool:
    return type(value) is bool


def _is_bounded_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _quantize(
    value: object,
    scale: int,
    *,
    field_name: str,
    coordinate: bool = False,
) -> int:
    numeric = _finite_number(value, field_name=field_name, coordinate=coordinate)
    try:
        scaled = Decimal(str(numeric)) * scale
        return int(scaled.to_integral_value(rounding=ROUND_HALF_EVEN))
    except (InvalidOperation, OverflowError, ValueError) as error:
        raise PlannerError("INVALID_NUMBER", f"{field_name} cannot be quantized") from error


def _wire_units(value: object, scale: int, *, field_name: str) -> int:
    wire = _float32(value, field_name=field_name, coordinate=False)
    return _quantize(wire, scale, field_name=field_name)


def _float32(value: object, *, field_name: str, coordinate: bool) -> float:
    numeric = _finite_number(value, field_name=field_name, coordinate=coordinate)
    try:
        wire_value: float = struct.unpack("<f", struct.pack("<f", numeric))[0]
    except (OverflowError, struct.error) as error:
        raise PlannerError(
            "COORDINATE_OUT_OF_RANGE",
            f"{field_name} cannot be represented as an Enforce float",
        ) from error
    if not math.isfinite(wire_value):  # pragma: no cover - pack range check is defensive
        raise PlannerError(
            "COORDINATE_OUT_OF_RANGE",
            f"{field_name} cannot be represented as a finite Enforce float",
        )
    return wire_value


def _from_units(value: int, scale: int) -> float:
    return value / scale


def _empty_stats() -> dict[str, int]:
    return dict.fromkeys(_STAT_KEYS, 0)


def _aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PlannerError("INVALID_TIMESTAMP", "created_at must be timezone-aware")
    return value.astimezone(UTC)


def _xz_in_bounds(
    point: _Point2Units,
    bounds: tuple[int, int, int, int, int, int],
) -> bool:
    return bounds[0] <= point.x <= bounds[3] and bounds[2] <= point.z <= bounds[5]


def _canonicalize_ring(points: tuple[_Point3Units, ...]) -> tuple[_Point3Units, ...]:
    """Choose the minimum rotation across forward and reverse traversal."""

    minimum = min(points, key=lambda point: (point.x, point.y, point.z))

    def rotate_to_minimum(sequence: tuple[_Point3Units, ...]) -> tuple[_Point3Units, ...]:
        start = sequence.index(minimum)
        return sequence[start:] + sequence[:start]

    forward = rotate_to_minimum(points)
    reverse = rotate_to_minimum(tuple(reversed(points)))

    def sequence_key(sequence: tuple[_Point3Units, ...]) -> tuple[tuple[int, int, int], ...]:
        return tuple((point.x, point.y, point.z) for point in sequence)

    return min(forward, reverse, key=sequence_key)


def _signed_area_twice(points: tuple[_Point2Units, ...]) -> int:
    return sum(
        point.x * points[(index + 1) % len(points)].z
        - points[(index + 1) % len(points)].x * point.z
        for index, point in enumerate(points)
    )


def _orientation(a: _Point2Units, b: _Point2Units, c: _Point2Units) -> int:
    return (b.x - a.x) * (c.z - a.z) - (b.z - a.z) * (c.x - a.x)


def _on_segment(a: _Point2Units, b: _Point2Units, point: _Point2Units) -> bool:
    return (
        _orientation(a, b, point) == 0
        and min(a.x, b.x) <= point.x <= max(a.x, b.x)
        and min(a.z, b.z) <= point.z <= max(a.z, b.z)
    )


def _segments_intersect(
    a: _Point2Units,
    b: _Point2Units,
    c: _Point2Units,
    d: _Point2Units,
) -> bool:
    ab_c = _orientation(a, b, c)
    ab_d = _orientation(a, b, d)
    cd_a = _orientation(c, d, a)
    cd_b = _orientation(c, d, b)
    if ((ab_c > 0) != (ab_d > 0)) and ((cd_a > 0) != (cd_b > 0)):
        return True
    return (
        (ab_c == 0 and _on_segment(a, b, c))
        or (ab_d == 0 and _on_segment(a, b, d))
        or (cd_a == 0 and _on_segment(c, d, a))
        or (cd_b == 0 and _on_segment(c, d, b))
    )


def _reject_overlapping_adjacent_edges(points: tuple[_Point2Units, ...]) -> None:
    for index, current in enumerate(points):
        previous = points[index - 1]
        following = points[(index + 1) % len(points)]
        if _orientation(previous, current, following) != 0:
            continue
        previous_ray = (previous.x - current.x, previous.z - current.z)
        following_ray = (following.x - current.x, following.z - current.z)
        if (previous_ray[0] * following_ray[0] + previous_ray[1] * following_ray[1]) > 0:
            raise PolygonValidationError(
                "OVERLAPPING_POLYGON_EDGES",
                "adjacent polygon edges overlap or backtrack",
            )


def _reject_self_intersections(points: tuple[_Point2Units, ...]) -> None:
    count = len(points)
    for first in range(count):
        first_end = (first + 1) % count
        for second in range(first + 1, count):
            second_end = (second + 1) % count
            if second in (first, first_end) or second_end == first:
                continue
            if _segments_intersect(
                points[first],
                points[first_end],
                points[second],
                points[second_end],
            ):
                raise PolygonValidationError(
                    "SELF_INTERSECTING_POLYGON",
                    f"polygon edges {first} and {second} intersect",
                )


def _point_in_polygon_units(
    point: _Point2Units,
    polygon: tuple[_Point2Units, ...],
) -> bool:
    inside = False
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        if _on_segment(start, end, point):
            return True
        if (start.z > point.z) == (end.z > point.z):
            continue
        cross = _orientation(start, end, point)
        if (end.z > start.z and cross > 0) or (end.z < start.z and cross < 0):
            inside = not inside
    return inside


def _distance_squared(first: _Point2Units, second: _Point2Units) -> int:
    return (first.x - second.x) ** 2 + (first.z - second.z) ** 2


def _engine_slope_exceeded(
    normal_x: float,
    normal_y: float,
    normal_z: float,
    max_slope_units: int,
) -> bool:
    """Conservatively mirror the staged handler's binary32 cosine check."""

    nx = _float32(normal_x, field_name="normal_x", coordinate=False)
    ny = _float32(normal_y, field_name="normal_y", coordinate=False)
    nz = _float32(normal_z, field_name="normal_z", coordinate=False)
    nx2 = _float32(nx * nx, field_name="normal_x_squared", coordinate=False)
    ny2 = _float32(ny * ny, field_name="normal_y_squared", coordinate=False)
    nz2 = _float32(nz * nz, field_name="normal_z_squared", coordinate=False)
    length_squared = _float32(
        _float32(nx2 + ny2, field_name="normal_xy_squared", coordinate=False) + nz2,
        field_name="normal_length_squared",
        coordinate=False,
    )
    length = _float32(
        math.sqrt(length_squared),
        field_name="normal_length",
        coordinate=False,
    )
    normalized_y = _float32(ny / length, field_name="normalized_normal_y", coordinate=False)
    max_slope = _float32(
        _from_units(max_slope_units, ANGLE_SCALE),
        field_name="max_slope_deg",
        coordinate=False,
    )
    radians = _float32(
        max_slope * _float32(ENFORCE_DEG_TO_RAD, field_name="degrees_to_radians", coordinate=False),
        field_name="max_slope_radians",
        coordinate=False,
    )
    minimum_normal_y = _float32(
        math.cos(radians),
        field_name="minimum_normal_y",
        coordinate=False,
    )
    return normalized_y < minimum_normal_y


def _spacing_conflict(
    first: _PreparedCandidate,
    second: _PreparedCandidate,
    min_spacing_units: int,
) -> bool:
    first_units = _Point2Units(first.x_units, first.z_units)
    second_units = _Point2Units(second.x_units, second.z_units)
    if _distance_squared(first_units, second_units) < min_spacing_units**2:
        return True

    # Match the staged Enforce handler's float expression exactly enough to be
    # conservative: every input, subtraction, multiplication, and addition is
    # rounded back to IEEE-754 binary32 before the comparison.
    spacing = _float32(
        _from_units(min_spacing_units, COORDINATE_SCALE),
        field_name="min_spacing_m",
        coordinate=False,
    )
    spacing_squared = _float32(
        spacing * spacing,
        field_name="spacing_squared",
        coordinate=False,
    )
    delta_x = _float32(
        first.query_x - second.query_x,
        field_name="spacing.delta_x",
        coordinate=False,
    )
    delta_z = _float32(
        first.query_z - second.query_z,
        field_name="spacing.delta_z",
        coordinate=False,
    )
    distance_squared = _float32(
        _float32(delta_x * delta_x, field_name="spacing.dx2", coordinate=False)
        + _float32(delta_z * delta_z, field_name="spacing.dz2", coordinate=False),
        field_name="spacing.distance_squared",
        coordinate=False,
    )
    return distance_squared < spacing_squared


__all__ = [
    "ALGORITHM_VERSION",
    "ANGLE_SCALE",
    "COORDINATE_SCALE",
    "DEFAULT_PLAN_TTL",
    "MAX_ABS_COORDINATE_M",
    "MAX_CANDIDATES",
    "MAX_ENFORCE_YAW_DEG",
    "PCG32",
    "PCG32_DEFAULT_STREAM",
    "PCG32_MULTIPLIER",
    "PLAN_SCHEMA_VERSION",
    "SCALE_SCALE",
    "TERRAIN_Y_EPSILON_UM",
    "CatalogValidationError",
    "NoValidPlacementsError",
    "NormalizedPaletteItem",
    "PlannerError",
    "PlanningContext",
    "PolygonValidationError",
    "PreparedVegetationPlan",
    "TerrainBatchSampler",
    "TerrainBounds",
    "TerrainContractError",
    "TerrainQuery",
    "TerrainSample",
    "ValidatedPolygon",
    "ValidatedVegetationInput",
    "VegetationPlan",
    "entity_name",
    "finalize_vegetation_plan",
    "plan_vegetation",
    "prepare_vegetation_plan",
    "underfilled_output",
    "validate_polygon",
    "validate_vegetation_input",
    "yaw_degrees_from_u32",
]
