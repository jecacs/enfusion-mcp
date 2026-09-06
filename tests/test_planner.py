from __future__ import annotations

import hashlib
import json
import math
import struct
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from enfusion_mcp.bridge_models import BRIDGE_BUILD_ID, BRIDGE_PROTOCOL_VERSION
from enfusion_mcp.catalog import VegetationCatalog
from enfusion_mcp.ledger import canonical_plan_json
from enfusion_mcp.models import (
    CatalogEntry,
    PaletteItem,
    TargetLayer,
    Vec3,
    VegetationPlanInput,
)
from enfusion_mcp.planner import (
    ALGORITHM_VERSION,
    COORDINATE_SCALE,
    MAX_CANDIDATES,
    MAX_ENFORCE_YAW_DEG,
    PCG32,
    PLAN_SCHEMA_VERSION,
    TERRAIN_Y_EPSILON_UM,
    CatalogValidationError,
    NoValidPlacementsError,
    PlannerError,
    PlanningContext,
    PolygonValidationError,
    TerrainBounds,
    TerrainContractError,
    TerrainQuery,
    TerrainSample,
    entity_name,
    finalize_vegetation_plan,
    plan_vegetation,
    prepare_vegetation_plan,
    validate_polygon,
    validate_vegetation_input,
    yaw_degrees_from_u32,
)

RESOURCE_A = "{0000000000000001}Prefabs/Synthetic/Bush A.et"
RESOURCE_B = "{0000000000000002}Prefabs/Synthetic/Куст Б.et"
CREATED_AT = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def _point(x: float, z: float, y: float = 0.0) -> Vec3:
    return Vec3(x=x, y=y, z=z)


def _rectangle(size: float = 30.0) -> tuple[Vec3, ...]:
    return (
        _point(0.0, 0.0),
        _point(size, 0.0),
        _point(size, size),
        _point(0.0, size),
    )


def _triangle() -> tuple[Vec3, ...]:
    return (_point(0.0, 0.0), _point(30.0, 0.0), _point(10.0, 25.0))


def _concave() -> tuple[Vec3, ...]:
    return (
        _point(0.0, 0.0),
        _point(30.0, 0.0),
        _point(30.0, 30.0),
        _point(15.0, 12.0),
        _point(0.0, 30.0),
    )


def _catalog() -> VegetationCatalog:
    return VegetationCatalog(
        version="synthetic-vegetation-v1",
        production_ready=True,
        entries=(
            CatalogEntry(resource_name=RESOURCE_A, label="Bush A", provenance="unit fixture"),
            CatalogEntry(resource_name=RESOURCE_B, label="Куст Б", provenance="unit fixture"),
        ),
    )


def _context(
    *,
    points: tuple[Vec3, ...] | None = None,
    bounds: TerrainBounds | None = None,
    catalog: VegetationCatalog | None = None,
) -> PlanningContext:
    effective_catalog = _catalog() if catalog is None else catalog
    return PlanningContext(
        world_path="$myaddon:world.ent",
        mode="edit",
        current_subscene=7,
        current_layer_id=42,
        active_layer_path="MCP_Preview",
        terrain_bounds=(
            TerrainBounds(-100.0, -100.0, -100.0, 100.0, 100.0, 100.0) if bounds is None else bounds
        ),
        selection_count=1,
        selected_name="Посадочная Shape",
        selected_class="ShapeEntity",
        polygon_compatible=True,
        shape_closed=True,
        shape_points_world=_rectangle() if points is None else points,
        bridge_protocol_version=BRIDGE_PROTOCOL_VERSION,
        bridge_build_id=BRIDGE_BUILD_ID,
        bridge_catalog_hash=effective_catalog.catalog_hash,
    )


def _request(  # noqa: PLR0913 - concise fixture builder
    *,
    count: int = 12,
    seed: int = 0,
    min_spacing_m: float = 1.0,
    max_slope_deg: float = 45.0,
    target_layer: TargetLayer = "MCP_Preview",
    palette: list[PaletteItem] | None = None,
    scale_min: float = 0.8,
    scale_max: float = 1.2,
) -> VegetationPlanInput:
    return VegetationPlanInput(
        count=count,
        seed=seed,
        min_spacing_m=min_spacing_m,
        max_slope_deg=max_slope_deg,
        target_layer=target_layer,
        palette=(
            [
                PaletteItem(prefab=RESOURCE_A, weight=1.0),
                PaletteItem(prefab=RESOURCE_B, weight=3.0),
            ]
            if palette is None
            else palette
        ),
        scale_min=scale_min,
        scale_max=scale_max,
    )


def _flat_samples(
    queries: tuple[TerrainQuery, ...],
    *,
    y: float = 0.0,
) -> tuple[TerrainSample, ...]:
    return tuple(
        TerrainSample(
            requested_x=query.x,
            requested_z=query.z,
            has_terrain=True,
            terrain_y=y,
            normal_x=0.0,
            normal_y=1.0,
            normal_z=0.0,
        )
        for query in queries
    )


def _wire_f32(value: float) -> float:
    wire_value: float = struct.unpack("<f", struct.pack("<f", value))[0]
    return wire_value


@pytest.mark.parametrize(
    ("seed", "seeded_state", "expected"),
    [
        (
            0,
            0x9AE4F7499BA72696,
            [0x47C28B93, 0xB98F6A27, 0x7D3DCB1E, 0xF0761116, 0x9CC33F5B, 0xBE0E744D],
        ),
        (
            1,
            0xF336EB76E83CA5C3,
            [0x9B6BDDA9, 0x0C31FC48, 0xCE97F8EF, 0x822C03D4, 0x943E8CF2, 0x1B75DDE3],
        ),
        (
            42,
            0x185706B82C2E03F8,
            [0xA15C02B7, 0x7B47F409, 0xBA1D3330, 0x83D2F293, 0xBFA4784B, 0xCBED606E],
        ),
        (
            4_294_967_295,
            0x8F2882494F11A769,
            [0x1836F28A, 0x41720992, 0x67039735, 0xE39C0B28, 0xDD934C44, 0x04B356A9],
        ),
    ],
)
def test_pcg32_reference_vectors(seed: int, seeded_state: int, expected: list[int]) -> None:
    generator = PCG32(seed)

    assert generator.state == seeded_state
    assert [generator.next_u32() for _ in expected] == expected


@pytest.mark.parametrize("seed", [-1, 1 << 64, True, 1.5])
def test_pcg32_rejects_non_uint64_seed(seed: object) -> None:
    with pytest.raises(ValueError, match="unsigned 64-bit"):
        PCG32(seed)  # type: ignore[arg-type]


def test_seed_zero_has_a_nonzero_deterministic_stream() -> None:
    first = PCG32(0)
    second = PCG32(0)

    assert first.next_u32() == 0x47C28B93
    assert [first.next_u32() for _ in range(10)] == [second.next_u32() for _ in range(11)][1:]


def test_maximum_yaw_draw_stays_below_360_after_enforce_float32_rounding() -> None:
    yaw = yaw_degrees_from_u32((1 << 32) - 1)

    assert yaw == MAX_ENFORCE_YAW_DEG
    assert yaw == _wire_f32(yaw)
    assert yaw < 360.0


@pytest.mark.parametrize("points", [_rectangle(), _triangle(), _concave()])
def test_rectangle_triangle_and_concave_polygons_are_valid(points: tuple[Vec3, ...]) -> None:
    polygon = validate_polygon(points)

    assert polygon.area_m2 > 0
    assert len(polygon.shape_hash) == 64
    assert polygon.contains(points[0].x, points[0].z)  # vertices are inside


def test_polygon_boundary_policy_is_inside() -> None:
    polygon = validate_polygon(_rectangle(10.0))

    assert polygon.contains(0.0, 0.0)
    assert polygon.contains(5.0, 0.0)
    assert polygon.contains(10.0, 5.0)
    assert polygon.contains(5.0, 5.0)
    assert not polygon.contains(-0.000001, 5.0)
    assert not polygon.contains(10.000001, 5.0)


def test_concave_polygon_excludes_its_notch() -> None:
    polygon = validate_polygon(_concave())

    assert polygon.contains(5.0, 20.0)
    assert not polygon.contains(15.0, 20.0)


def test_cyclic_start_and_reversed_winding_have_one_canonical_ring_and_plan() -> None:
    catalog = _catalog()
    ring = (
        _point(0.0, 0.0, y=3.0),
        _point(30.0, 0.0, y=2.0),
        _point(30.0, 30.0, y=1.0),
        _point(0.0, 30.0, y=4.0),
    )
    rotated = ring[2:] + ring[:2]
    reversed_ring = tuple(reversed(ring))
    polygons = tuple(validate_polygon(points) for points in (ring, rotated, reversed_ring))

    assert polygons[0].points_units == polygons[1].points_units == polygons[2].points_units
    assert polygons[0].points_world == polygons[1].points_world == polygons[2].points_world
    assert len({polygon.shape_hash for polygon in polygons}) == 1
    assert [(point.x, point.y, point.z) for point in polygons[0].points_world] == [
        (0.0, 3.0, 0.0),
        (0.0, 4.0, 30.0),
        (30.0, 1.0, 30.0),
        (30.0, 2.0, 0.0),
    ]

    prepared = tuple(
        prepare_vegetation_plan(_context(points=points, catalog=catalog), _request(), catalog)
        for points in (ring, rotated, reversed_ring)
    )
    assert prepared[0].terrain_queries == prepared[1].terrain_queries == prepared[2].terrain_queries
    plans = tuple(
        finalize_vegetation_plan(
            item,
            _flat_samples(item.terrain_queries),
            created_at=CREATED_AT,
        )
        for item in prepared
    )
    assert plans[0].canonical_json == plans[1].canonical_json == plans[2].canonical_json
    assert plans[0].placements == plans[1].placements == plans[2].placements
    assert plans[0].plan_id == plans[1].plan_id == plans[2].plan_id


@pytest.mark.parametrize(
    ("points", "code"),
    [
        (
            (_point(0.0, 0.0), _point(10.0, 10.0), _point(0.0, 10.0), _point(10.0, 0.0)),
            "SELF_INTERSECTING_POLYGON",
        ),
        (
            (_point(0.0, 0.0), _point(1.0, 0.0), _point(2.0, 0.0)),
            "DEGENERATE_POLYGON",
        ),
        (
            (_point(0.0, 0.0), _point(10.0, 0.0), _point(10.0, 10.0), _point(0.0, 0.0)),
            "DUPLICATE_POLYGON_VERTEX",
        ),
        (
            (
                _point(0.0, 0.0),
                _point(10.0, 0.0),
                _point(5.0, 0.0),
                _point(10.0, 10.0),
                _point(0.0, 10.0),
            ),
            "OVERLAPPING_POLYGON_EDGES",
        ),
    ],
)
def test_invalid_polygon_topologies_are_rejected(
    points: tuple[Vec3, ...],
    code: str,
) -> None:
    with pytest.raises(PolygonValidationError) as captured:
        validate_polygon(points)
    assert captured.value.code == code


def test_vertices_collapsed_by_canonical_precision_are_rejected() -> None:
    points = (
        _point(0.0, 0.0),
        _point(0.0000004, 0.0000004),
        _point(10.0, 0.0),
        _point(0.0, 10.0),
    )

    with pytest.raises(PolygonValidationError) as captured:
        validate_polygon(points)
    assert captured.value.code == "DUPLICATE_POLYGON_VERTEX"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"selection_count": 0}, "INVALID_SELECTION"),
        ({"selection_count": 2}, "INVALID_SELECTION"),
        ({"polygon_compatible": False}, "SELECTION_NOT_SHAPE"),
        ({"shape_closed": False}, "SHAPE_NOT_CLOSED"),
        ({"mode": "game"}, "INVALID_EDITOR_MODE"),
        ({"current_subscene": -1}, "INVALID_SUBSCENE"),
        ({"current_layer_id": -1}, "INVALID_LAYER"),
        ({"bridge_protocol_version": "old"}, "INCOMPATIBLE_BRIDGE"),
        ({"bridge_build_id": "old"}, "INCOMPATIBLE_BRIDGE"),
        (
            {"shape_points_world": (_point(0.0, 0.0), _point(1.0, 1.0))},
            "INVALID_POLYGON_CARDINALITY",
        ),
    ],
)
def test_selection_and_shape_preconditions_are_fail_closed(
    changes: dict[str, object],
    code: str,
) -> None:
    context = replace(_context(), **changes)  # type: ignore[arg-type]

    with pytest.raises(PlannerError) as captured:
        prepare_vegetation_plan(context, _request(), _catalog())
    assert captured.value.code == code


def test_requested_target_layer_must_already_be_active() -> None:
    catalog = _catalog()
    request = _request(target_layer="MCP_Vegetation")
    with pytest.raises(PlannerError) as captured:
        prepare_vegetation_plan(_context(catalog=catalog), request, catalog)
    assert captured.value.code == "TARGET_LAYER_NOT_ACTIVE"


def test_palette_must_be_an_exact_subset_of_verified_matching_catalog() -> None:
    catalog = _catalog()
    invalid_request = _request(palette=[PaletteItem(prefab="$myaddon:Bush A.et", weight=1.0)])

    with pytest.raises(CatalogValidationError) as captured:
        prepare_vegetation_plan(_context(catalog=catalog), invalid_request, catalog)
    assert captured.value.code == "PREFAB_NOT_ALLOWED"

    mismatched_context = replace(_context(catalog=catalog), bridge_catalog_hash="0" * 64)
    with pytest.raises(CatalogValidationError) as mismatch:
        prepare_vegetation_plan(mismatched_context, _request(), catalog)
    assert mismatch.value.code == "CATALOG_MISMATCH"


def test_positive_weight_below_fixed_precision_is_rejected() -> None:
    catalog = _catalog()
    request = _request(palette=[PaletteItem(prefab=RESOURCE_A, weight=1e-12)])

    with pytest.raises(CatalogValidationError) as captured:
        prepare_vegetation_plan(_context(catalog=catalog), request, catalog)
    assert captured.value.code == "INVALID_PALETTE_WEIGHT"


def test_positive_scale_below_fixed_precision_is_rejected() -> None:
    catalog = _catalog()
    request = _request(scale_min=1e-9, scale_max=1e-9)

    with pytest.raises(PlannerError) as captured:
        prepare_vegetation_plan(_context(catalog=catalog), request, catalog)
    assert captured.value.code == "INVALID_SCALE"


def test_slope_limit_cannot_round_up_to_ninety_degrees() -> None:
    catalog = _catalog()

    with pytest.raises(PlannerError) as captured:
        prepare_vegetation_plan(
            _context(catalog=catalog),
            _request(max_slope_deg=89.9999999),
            catalog,
        )
    assert captured.value.code == "INVALID_SLOPE"


def test_empty_polygon_terrain_intersection_fails_before_sampling() -> None:
    catalog = _catalog()
    context = _context(
        catalog=catalog,
        bounds=TerrainBounds(100.0, -10.0, 100.0, 200.0, 10.0, 200.0),
    )

    with pytest.raises(PlannerError) as captured:
        prepare_vegetation_plan(context, _request(), catalog)
    assert captured.value.code == "NO_TERRAIN_INTERSECTION"


def test_candidate_pool_uses_one_bounded_batch_inside_polygon_and_terrain() -> None:
    catalog = _catalog()
    bounds = TerrainBounds(20.0, -10.0, 20.0, 80.0, 10.0, 80.0)
    context = _context(points=_rectangle(100.0), bounds=bounds, catalog=catalog)
    request = _request(count=100, min_spacing_m=0.01)
    calls: list[tuple[TerrainQuery, ...]] = []

    def sampler(queries: tuple[TerrainQuery, ...]) -> tuple[TerrainSample, ...]:
        calls.append(queries)
        return _flat_samples(queries)

    plan = plan_vegetation(context, request, catalog, sampler, created_at=CREATED_AT)

    assert len(calls) == 1
    assert 0 < len(calls[0]) <= MAX_CANDIDATES
    polygon = validate_polygon(context.shape_points_world)
    assert all(polygon.contains(query.x, query.z) for query in calls[0])
    assert all(20.0 <= query.x <= 80.0 and 20.0 <= query.z <= 80.0 for query in calls[0])
    assert dict(plan.rejection_stats)["outside_terrain_bounds"] == 0


def test_candidates_survive_exact_enforce_float32_round_trip() -> None:
    catalog = _catalog()
    context = _context(catalog=catalog)
    prepared = prepare_vegetation_plan(context, _request(count=40), catalog)
    assert all(
        query.x == _wire_f32(query.x) and query.z == _wire_f32(query.z)
        for query in prepared.terrain_queries
    )
    echoed = tuple(
        TerrainSample(
            requested_x=_wire_f32(query.x),
            requested_z=_wire_f32(query.z),
            has_terrain=True,
            terrain_y=_wire_f32(0.0),
            normal_x=_wire_f32(0.0),
            normal_y=_wire_f32(1.0),
            normal_z=_wire_f32(0.0),
        )
        for query in prepared.terrain_queries
    )

    plan = finalize_vegetation_plan(prepared, echoed, created_at=CREATED_AT)

    assert len(plan.placements) == 40
    assert dict(plan.rejection_stats)["invalid_terrain_result"] == 0


def test_float32_canonicalization_prevents_large_coordinate_collisions() -> None:
    catalog = _catalog()
    points = (
        _point(999_999.0, 999_999.0),
        _point(1_000_000.0, 999_999.0),
        _point(1_000_000.0, 1_000_000.0),
        _point(999_999.0, 1_000_000.0),
    )
    bounds = TerrainBounds(
        999_998.0,
        -10.0,
        999_998.0,
        1_000_000.0,
        10.0,
        1_000_000.0,
    )
    context = _context(points=points, bounds=bounds, catalog=catalog)
    request = _request(count=100, min_spacing_m=0.001)
    prepared = prepare_vegetation_plan(context, request, catalog)
    plan = finalize_vegetation_plan(
        prepared,
        _flat_samples(prepared.terrain_queries),
        created_at=CREATED_AT,
    )

    wire_positions = {
        (_wire_f32(item.position.x), _wire_f32(item.position.z)) for item in plan.placements
    }
    assert len(wire_positions) == len(plan.placements)
    assert len(plan.placements) == 100
    for index, first in enumerate(plan.placements):
        for second in plan.placements[index + 1 :]:
            assert (
                math.hypot(
                    _wire_f32(first.position.x) - _wire_f32(second.position.x),
                    _wire_f32(first.position.z) - _wire_f32(second.position.z),
                )
                >= request.min_spacing_m
            )


@pytest.mark.parametrize("points", [_rectangle(), _triangle(), _concave()])
def test_planning_supported_polygon_shapes_keeps_points_inside(points: tuple[Vec3, ...]) -> None:
    catalog = _catalog()
    context = _context(points=points, catalog=catalog)
    request = _request(count=10, min_spacing_m=0.5)

    plan = plan_vegetation(
        context,
        request,
        catalog,
        _flat_samples,
        created_at=CREATED_AT,
    )

    polygon = validate_polygon(points)
    assert len(plan.placements) == request.count
    assert all(polygon.contains(item.position.x, item.position.z) for item in plan.placements)


def test_identical_input_seed_is_repeatable_and_timestamps_do_not_affect_hash() -> None:
    catalog = _catalog()
    context = _context(catalog=catalog)
    request = _request(seed=0)

    first = plan_vegetation(context, request, catalog, _flat_samples, created_at=CREATED_AT)
    later = plan_vegetation(
        context,
        request,
        catalog,
        _flat_samples,
        created_at=CREATED_AT + timedelta(days=10),
    )

    assert first.plan_id == later.plan_id
    assert first.canonical_json == later.canonical_json
    assert first.placements == later.placements
    assert first.created_at != later.created_at
    assert first.expires_at - first.created_at == timedelta(minutes=30)
    assert "createdAt" not in first.canonical_json
    assert "expiresAt" not in first.canonical_json


def test_different_seed_changes_candidates_transforms_and_plan_id() -> None:
    catalog = _catalog()
    context = _context(catalog=catalog)
    first = plan_vegetation(
        context, _request(seed=0), catalog, _flat_samples, created_at=CREATED_AT
    )
    second = plan_vegetation(
        context, _request(seed=1), catalog, _flat_samples, created_at=CREATED_AT
    )

    assert first.plan_id != second.plan_id
    assert first.placements != second.placements


def test_candidate_draw_order_has_a_golden_seed_zero_prefix() -> None:
    catalog = _catalog()
    prepared = prepare_vegetation_plan(_context(catalog=catalog), _request(count=1), catalog)

    assert prepared.terrain_queries[:3] == (
        TerrainQuery(x=8.409367561340332, z=21.745336532592773),
        TerrainQuery(x=14.676724433898926, z=28.179046630859375),
        TerrainQuery(x=18.37062644958496, z=22.272241592407227),
    )


def test_spacing_is_respected_and_equality_is_allowed() -> None:
    catalog = _catalog()
    request = _request(count=30, min_spacing_m=2.0)
    plan = plan_vegetation(
        _context(catalog=catalog), request, catalog, _flat_samples, created_at=CREATED_AT
    )

    for index, first in enumerate(plan.placements):
        for second in plan.placements[index + 1 :]:
            distance = math.hypot(
                first.position.x - second.position.x,
                first.position.z - second.position.z,
            )
            assert distance >= request.min_spacing_m


def test_spacing_boundary_accepts_exact_integer_equality() -> None:
    catalog = _catalog()
    prepared = prepare_vegetation_plan(_context(catalog=catalog), _request(count=2), catalog)
    candidate_type = type(prepared.candidates[0])
    candidates = (
        candidate_type(x_units=0, z_units=0, query_x=0.0, query_z=0.0),
        candidate_type(
            x_units=2 * COORDINATE_SCALE,
            z_units=0,
            query_x=2.0,
            query_z=0.0,
        ),
    )
    exact = replace(
        prepared,
        requested_count=2,
        min_spacing_units=2 * COORDINATE_SCALE,
        candidates=candidates,
        terrain_queries=(TerrainQuery(0.0, 0.0), TerrainQuery(2.0, 0.0)),
    )

    plan = finalize_vegetation_plan(
        exact, _flat_samples(exact.terrain_queries), created_at=CREATED_AT
    )

    assert len(plan.placements) == 2
    assert dict(plan.rejection_stats)["spacing_conflict"] == 0


def test_spacing_also_matches_enforce_float32_arithmetic_at_boundary() -> None:
    catalog = _catalog()
    prepared = prepare_vegetation_plan(_context(catalog=catalog), _request(count=2), catalog)
    first_x = _wire_f32(0.001548)
    second_x = _wire_f32(0.001557)
    candidate_type = type(prepared.candidates[0])
    candidates = (
        candidate_type(x_units=1_548, z_units=0, query_x=first_x, query_z=0.0),
        candidate_type(x_units=1_557, z_units=0, query_x=second_x, query_z=0.0),
    )
    boundary = replace(
        prepared,
        requested_count=2,
        min_spacing_units=9,
        candidates=candidates,
        terrain_queries=(TerrainQuery(first_x, 0.0), TerrainQuery(second_x, 0.0)),
    )

    plan = finalize_vegetation_plan(
        boundary,
        _flat_samples(boundary.terrain_queries),
        created_at=CREATED_AT,
    )

    assert len(plan.placements) == 1
    assert plan.warnings == ("UNDERFILLED",)
    assert dict(plan.rejection_stats)["spacing_conflict"] == 1


def test_impossible_density_is_underfilled_without_looping() -> None:
    catalog = _catalog()
    context = _context(points=_rectangle(1.0), catalog=catalog)
    request = _request(count=100, min_spacing_m=10.0)
    calls = 0

    def sampler(queries: tuple[TerrainQuery, ...]) -> tuple[TerrainSample, ...]:
        nonlocal calls
        calls += 1
        return _flat_samples(queries)

    plan = plan_vegetation(context, request, catalog, sampler, created_at=CREATED_AT)

    assert calls == 1
    assert len(plan.placements) == 1
    assert plan.warnings == ("UNDERFILLED",)
    assert dict(plan.rejection_stats)["spacing_conflict"] == 999


def test_sparse_concave_polygon_stops_at_hard_candidate_attempt_limit() -> None:
    catalog = _catalog()
    narrow_l = (
        _point(0.0, 0.0),
        _point(1_000.0, 0.0),
        _point(1_000.0, 0.1),
        _point(0.1, 0.1),
        _point(0.1, 1_000.0),
        _point(0.0, 1_000.0),
    )
    prepared = prepare_vegetation_plan(
        _context(points=narrow_l, catalog=catalog),
        _request(count=100, min_spacing_m=0.001),
        catalog,
    )

    assert dict(prepared.generation_stats)["candidate_attempts"] == 10_000
    assert len(prepared.terrain_queries) <= MAX_CANDIDATES
    plan = finalize_vegetation_plan(
        prepared, _flat_samples(prepared.terrain_queries), created_at=CREATED_AT
    )
    assert len(plan.placements) < 100
    assert plan.warnings == ("UNDERFILLED",)


def test_no_terrain_returns_typed_failure_instead_of_unapplyable_zero_plan() -> None:
    catalog = _catalog()
    prepared = prepare_vegetation_plan(_context(catalog=catalog), _request(count=5), catalog)
    samples = tuple(
        TerrainSample(query.x, query.z, has_terrain=False) for query in prepared.terrain_queries
    )

    with pytest.raises(NoValidPlacementsError) as captured:
        finalize_vegetation_plan(prepared, samples, created_at=CREATED_AT)
    assert captured.value.code == "NO_VALID_PLACEMENTS"
    stats = dict(captured.value.rejection_stats)
    assert stats["no_terrain"] == len(samples)
    assert stats["accepted"] == 0
    assert stats["candidate_attempts"] >= len(samples)


def test_standalone_input_validation_detaches_mutable_palette() -> None:
    request = _request()
    validated = validate_vegetation_input(request, _catalog())
    request.palette.clear()

    assert len(validated.request.palette) == 2
    assert len(validated.palette) == 2


def test_terrain_filter_reasons_are_deterministic_and_ordered() -> None:
    catalog = _catalog()
    prepared = prepare_vegetation_plan(
        _context(catalog=catalog),
        _request(count=4, min_spacing_m=0.01, max_slope_deg=30.0),
        catalog,
    )
    queries = prepared.terrain_queries
    samples = list(_flat_samples(queries))
    samples[0] = replace(samples[0], requested_x=samples[0].requested_x + 1.0)
    samples[1] = TerrainSample(queries[1].x, queries[1].z, has_terrain=False)
    samples[2] = replace(samples[2], terrain_y=1_000.0)
    samples[3] = replace(samples[3], normal_x=0.0, normal_y=0.0, normal_z=0.0)
    samples[4] = replace(samples[4], normal_x=math.sqrt(3) / 2, normal_y=0.5)
    samples[5] = replace(samples[5], terrain_y=math.nan)

    plan = finalize_vegetation_plan(prepared, samples, created_at=CREATED_AT)
    stats = dict(plan.rejection_stats)

    assert len(plan.placements) == 4
    assert stats["invalid_terrain_result"] == 2
    assert stats["no_terrain"] == 1
    assert stats["terrain_y_out_of_bounds"] == 1
    assert stats["invalid_normal"] == 1
    assert stats["slope_exceeded"] == 1


def test_slope_limit_includes_exact_equality_and_rejects_above_limit() -> None:
    catalog = _catalog()
    prepared = prepare_vegetation_plan(
        _context(catalog=catalog), _request(count=1, max_slope_deg=30.0), catalog
    )
    exact_x = math.sin(math.radians(30.0))
    exact_y = math.cos(math.radians(30.0))
    exact = tuple(
        TerrainSample(query.x, query.z, True, 0.0, exact_x, exact_y, 0.0)
        for query in prepared.terrain_queries
    )
    accepted = finalize_vegetation_plan(prepared, exact, created_at=CREATED_AT)
    assert len(accepted.placements) == 1

    above_x = math.sin(math.radians(30.00001))
    above_y = math.cos(math.radians(30.00001))
    above = tuple(
        TerrainSample(query.x, query.z, True, 0.0, above_x, above_y, 0.0)
        for query in prepared.terrain_queries
    )
    with pytest.raises(PlannerError) as captured:
        finalize_vegetation_plan(prepared, above, created_at=CREATED_AT)
    assert captured.value.code == "NO_VALID_PLACEMENTS"


def test_slope_filter_conservatively_matches_enforce_float32_cosine_boundary() -> None:
    catalog = _catalog()
    prepared = prepare_vegetation_plan(
        _context(catalog=catalog),
        _request(count=1, max_slope_deg=89.999),
        catalog,
    )
    samples = tuple(
        TerrainSample(
            query.x,
            query.z,
            True,
            0.0,
            1.0,
            0.000017479473171988502,
            0.0,
        )
        for query in prepared.terrain_queries
    )

    with pytest.raises(PlannerError) as captured:
        finalize_vegetation_plan(prepared, samples, created_at=CREATED_AT)
    assert captured.value.code == "NO_VALID_PLACEMENTS"


@pytest.mark.parametrize("delta", [-1, 1])
def test_terrain_batch_cardinality_must_match_exactly(delta: int) -> None:
    catalog = _catalog()
    prepared = prepare_vegetation_plan(_context(catalog=catalog), _request(), catalog)
    samples = list(_flat_samples(prepared.terrain_queries))
    if delta < 0:
        samples.pop()
    else:
        samples.append(samples[-1])

    with pytest.raises(TerrainContractError) as captured:
        finalize_vegetation_plan(prepared, samples, created_at=CREATED_AT)
    assert captured.value.code == "TERRAIN_CARDINALITY_MISMATCH"


def test_unbounded_terrain_iterable_is_rejected_before_consumption() -> None:
    catalog = _catalog()
    prepared = prepare_vegetation_plan(_context(catalog=catalog), _request(), catalog)
    generator = (sample for sample in _flat_samples(prepared.terrain_queries))

    with pytest.raises(TerrainContractError) as captured:
        finalize_vegetation_plan(
            prepared,
            generator,  # type: ignore[arg-type]
            created_at=CREATED_AT,
        )
    assert captured.value.code == "INVALID_TERRAIN_BATCH"


def test_reordered_terrain_echoes_are_not_reassociated() -> None:
    catalog = _catalog()
    prepared = prepare_vegetation_plan(_context(catalog=catalog), _request(count=3), catalog)
    samples = list(_flat_samples(prepared.terrain_queries))
    samples[0], samples[1] = samples[1], samples[0]

    plan = finalize_vegetation_plan(prepared, samples, created_at=CREATED_AT)

    assert dict(plan.rejection_stats)["invalid_terrain_result"] == 2
    assert len(plan.placements) == 3


def test_malformed_trailing_terrain_item_is_validated_after_count_is_reached() -> None:
    catalog = _catalog()
    prepared = prepare_vegetation_plan(_context(catalog=catalog), _request(count=1), catalog)
    samples = list(_flat_samples(prepared.terrain_queries))
    samples[-1] = replace(samples[-1], normal_x=math.inf)

    plan = finalize_vegetation_plan(prepared, samples, created_at=CREATED_AT)
    stats = dict(plan.rejection_stats)

    assert len(plan.placements) == 1
    assert stats["invalid_terrain_result"] == 1
    assert stats["unused_after_count"] == len(samples) - 2


def test_normalized_weights_are_positive_complete_and_scale_invariant() -> None:
    catalog = _catalog()
    context = _context(catalog=catalog)
    one_three = _request(
        palette=[
            PaletteItem(prefab=RESOURCE_A, weight=1.0),
            PaletteItem(prefab=RESOURCE_B, weight=3.0),
        ]
    )
    two_six = _request(
        palette=[
            PaletteItem(prefab=RESOURCE_A, weight=2.0),
            PaletteItem(prefab=RESOURCE_B, weight=6.0),
        ]
    )
    first_prepared = prepare_vegetation_plan(context, one_three, catalog)
    second_prepared = prepare_vegetation_plan(context, two_six, catalog)

    assert first_prepared.palette == second_prepared.palette
    assert all(item.tickets > 0 for item in first_prepared.palette)
    assert sum(item.tickets for item in first_prepared.palette) == 1 << 32

    first = finalize_vegetation_plan(
        first_prepared, _flat_samples(first_prepared.terrain_queries), created_at=CREATED_AT
    )
    second = finalize_vegetation_plan(
        second_prepared, _flat_samples(second_prepared.terrain_queries), created_at=CREATED_AT
    )
    assert first.plan_id == second.plan_id


def test_scale_draw_is_consumed_even_for_a_fixed_scale() -> None:
    catalog = _catalog()
    context = _context(catalog=catalog)
    fixed = plan_vegetation(
        context,
        _request(scale_min=1.0, scale_max=1.0),
        catalog,
        _flat_samples,
        created_at=CREATED_AT,
    )
    ranged = plan_vegetation(
        context,
        _request(scale_min=0.5, scale_max=1.5),
        catalog,
        _flat_samples,
        created_at=CREATED_AT,
    )

    assert [item.prefab for item in fixed.placements] == [item.prefab for item in ranged.placements]
    assert [item.yaw_deg for item in fixed.placements] == [
        item.yaw_deg for item in ranged.placements
    ]
    assert all(item.scale == 1.0 for item in fixed.placements)
    assert all(0.5 <= item.scale <= 1.5 for item in ranged.placements)


def test_canonical_plan_is_fixed_point_complete_and_entity_names_are_derived() -> None:
    catalog = _catalog()
    context = _context(catalog=catalog)
    plan = plan_vegetation(
        context,
        _request(seed=42),
        catalog,
        _flat_samples,
        created_at=CREATED_AT,
    )
    payload = json.loads(plan.canonical_json)

    assert canonical_plan_json(payload) == plan.canonical_json
    assert hashlib.sha256(plan.canonical_json.encode()).hexdigest() == plan.plan_id
    assert payload["algorithmVersion"] == ALGORITHM_VERSION
    assert payload["schemaVersion"] == PLAN_SCHEMA_VERSION
    assert payload["context"]["worldPath"] == "$myaddon:world.ent"
    assert payload["context"]["currentSubscene"] == 7
    assert payload["shape"]["name"] == "Посадочная Shape"
    assert payload["shape"]["pointsWorldUm"][1]["z"] == 30 * COORDINATE_SCALE
    assert payload["shape"]["pointsWorldUm"][2]["x"] == 30 * COORDINATE_SCALE
    assert payload["catalog"]["hash"] == catalog.catalog_hash
    assert payload["targetLayer"] == "MCP_Preview"
    assert payload["constraints"]["terrainYEpsilonUm"] == TERRAIN_Y_EPSILON_UM
    assert all(isinstance(item["positionUm"]["x"], int) for item in payload["placements"])
    assert all("entityName" not in item for item in payload["placements"])
    assert [item.entity_name for item in plan.placements] == [
        entity_name(plan.plan_id, index) for index in range(len(plan.placements))
    ]
    assert plan.placements[0].entity_name == f"EnfusionMCP_{plan.plan_id}_0"
    assert plan.as_output().mutated_workbench is False


def test_relevant_context_constraint_and_palette_changes_change_plan_id() -> None:
    catalog = _catalog()
    context = _context(catalog=catalog)
    request = _request(seed=42)
    baseline = plan_vegetation(context, request, catalog, _flat_samples, created_at=CREATED_AT)
    other_subscene = plan_vegetation(
        replace(context, current_subscene=8),
        request,
        catalog,
        _flat_samples,
        created_at=CREATED_AT,
    )
    other_layer = plan_vegetation(
        replace(context, active_layer_path="MCP_Vegetation"),
        _request(seed=42, target_layer="MCP_Vegetation"),
        catalog,
        _flat_samples,
        created_at=CREATED_AT,
    )
    reversed_palette = plan_vegetation(
        context,
        _request(
            seed=42,
            palette=[
                PaletteItem(prefab=RESOURCE_B, weight=3.0),
                PaletteItem(prefab=RESOURCE_A, weight=1.0),
            ],
        ),
        catalog,
        _flat_samples,
        created_at=CREATED_AT,
    )

    assert (
        len(
            {
                baseline.plan_id,
                other_subscene.plan_id,
                other_layer.plan_id,
                reversed_palette.plan_id,
            }
        )
        == 4
    )


@pytest.mark.parametrize(
    "values",
    [
        (0.0, 0.0, 0.0, 0.0, 1.0, 1.0),
        (0.0, 0.0, 0.0, 1.0, 1.0, 0.0),
    ],
)
def test_terrain_bounds_require_positive_xz_extent(
    values: tuple[float, float, float, float, float, float],
) -> None:
    with pytest.raises(PlannerError) as captured:
        TerrainBounds(*values)
    assert captured.value.code == "INVALID_TERRAIN_BOUNDS"


def test_terrain_bounds_collapsed_by_fixed_precision_are_rejected() -> None:
    catalog = _catalog()
    context = _context(
        catalog=catalog,
        bounds=TerrainBounds(0.0, 0.0, 0.0, 0.0000004, 1.0, 1.0),
    )

    with pytest.raises(PlannerError) as captured:
        prepare_vegetation_plan(context, _request(), catalog)
    assert captured.value.code == "INVALID_TERRAIN_BOUNDS"


def test_canonical_plan_has_a_golden_sha256() -> None:
    catalog = _catalog()
    plan = plan_vegetation(
        _context(catalog=catalog),
        _request(seed=42),
        catalog,
        _flat_samples,
        created_at=CREATED_AT,
    )

    assert plan.plan_id == "eef04e6ba3066c329209ad4489a5e4f9fa34835bf39608698cacc5a42f5ffac8"
