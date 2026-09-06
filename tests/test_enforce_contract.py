from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from enfusion_mcp_rj.bridge_models import (
    BRIDGE_BUILD_ID,
    BRIDGE_PROTOCOL_VERSION,
    BridgeContextResponse,
    BridgeTerrainBounds,
    BridgeTerrainPointRequest,
    BridgeTerrainSample,
    BridgeTerrainSampleRequest,
    BridgeTerrainSampleResponse,
    BridgeVec3,
    BridgeVegetationApplyRequest,
    BridgeVegetationApplyResponse,
)
from enfusion_mcp_rj.catalog import PRODUCTION_CATALOG

ROOT = Path(__file__).resolve().parents[1]
HANDLER_DIR = ROOT / "bridge/Scripts/WorkbenchGame/RJMCP"
HANDLERS = sorted(HANDLER_DIR.glob("RJMCP_*.c"))


def _source(name: str) -> str:
    return (HANDLER_DIR / name).read_text()


def _class_body(source: str, class_name: str) -> str:
    match = re.search(rf"\bclass\s+{re.escape(class_name)}\b[^{{]*\{{", source)
    assert match is not None, f"missing class {class_name}"
    depth = 1
    cursor = match.end()
    while cursor < len(source) and depth:
        if source[cursor] == "{":
            depth += 1
        elif source[cursor] == "}":
            depth -= 1
        cursor += 1
    assert depth == 0, f"unbalanced class {class_name}"
    return source[match.end() : cursor - 1]


def _wire_fields(source: str, class_name: str) -> set[str]:
    body = _class_body(source, class_name)
    registered = set(re.findall(r'\bRegV\("([A-Za-z][A-Za-z0-9]*)"\)', body))
    packed_arrays = set(re.findall(r'\bStartArray\("([A-Za-z][A-Za-z0-9]*)"\)', body))
    return registered | packed_arrays


def _schema_fields(model: type[BaseModel]) -> set[str]:
    schema: dict[str, Any] = model.model_json_schema(by_alias=True)
    return set(schema["properties"])


def _json_type(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        return "object"
    if "type" in schema:
        value = schema["type"]
        assert isinstance(value, str)
        if value == "array":
            items = schema.get("items")
            assert isinstance(items, dict)
            return f"array[{_json_type(items)}]"
        return value
    variants = schema.get("anyOf")
    assert isinstance(variants, list)
    non_null = [item for item in variants if item.get("type") != "null"]
    assert len(non_null) == 1
    return _json_type(non_null[0])


def _c_wire_types(source: str, class_name: str) -> dict[str, str]:
    body = _class_body(source, class_name)
    declarations = {
        name: c_type.strip()
        for c_type, name in re.findall(
            r"^\s*((?:ref\s+)?array<[^;]+>|ref\s+[A-Za-z_]\w*|string|int|float|bool)"
            r"\s+([A-Za-z_]\w*)\s*;",
            body,
            flags=re.MULTILINE,
        )
    }
    wire_types: dict[str, str] = {}
    c_to_json = {"string": "string", "int": "integer", "float": "number", "bool": "boolean"}
    for field in _wire_fields(source, class_name):
        variable = field
        if variable not in declarations:
            variable = "m_a" + field[0].upper() + field[1:]
        c_type = declarations[variable]
        if "array<" in c_type:
            element_type = c_type[c_type.index("array<") + len("array<") : c_type.rindex(">")]
            element_type = element_type.removeprefix("ref ").strip()
            element_json_type = c_to_json.get(element_type, "object")
            wire_types[field] = f"array[{element_json_type}]"
        elif c_type.startswith("ref "):
            wire_types[field] = "object"
        else:
            wire_types[field] = c_to_json[c_type]
    return wire_types


def _schema_types(model: type[BaseModel]) -> dict[str, str]:
    schema: dict[str, Any] = model.model_json_schema(by_alias=True)
    return {name: _json_type(value) for name, value in schema["properties"].items()}


def _block_after(source: str, marker: str, *, start: int = 0) -> str:
    marker_index = source.index(marker, start)
    opening = source.index("{", marker_index + len(marker))
    depth = 1
    cursor = opening + 1
    while cursor < len(source) and depth:
        if source[cursor] == "{":
            depth += 1
        elif source[cursor] == "}":
            depth -= 1
        cursor += 1
    assert depth == 0, f"unbalanced block after {marker!r}"
    return source[opening + 1 : cursor - 1]


def test_exact_staged_handler_manifest_and_registration_prefix() -> None:
    assert [path.name for path in HANDLERS] == [
        "RJMCP_GetContext.c",
        "RJMCP_TerrainSample.c",
        "RJMCP_VegetationApply.c",
    ]
    for path in HANDLERS:
        source = path.read_text()
        assert re.search(rf"class\s+{re.escape(path.stem)}\s*:\s*NetApiHandler", source)
        assert "UNVERIFIED_LIVE" in source


def test_each_installable_handler_has_self_contained_mit_upstream_attribution() -> None:
    expected = (
        "SPDX-License-Identifier: MIT",
        "Adapted JsonApiStruct/NetApiHandler patterns",
        "https://github.com/steffenbk/enfusion-mcp-BK",
        "Upstream commit: 0acfa884228477043c6b4c2b8c7f0c270d648398",
        "See the source repository NOTICE.md for complete attribution.",
    )
    for path in HANDLERS:
        header = "\n".join(path.read_text().splitlines()[:12])
        assert all(line in header for line in expected), path.name


@pytest.mark.parametrize(
    ("filename", "class_name", "model"),
    [
        ("RJMCP_GetContext.c", "RJMCP_GetContextRequest", None),
        ("RJMCP_GetContext.c", "RJMCP_GetContextResponse", BridgeContextResponse),
        ("RJMCP_TerrainSample.c", "RJMCP_TerrainSampleRequest", BridgeTerrainSampleRequest),
        ("RJMCP_TerrainSample.c", "RJMCP_TerrainSampleResponse", BridgeTerrainSampleResponse),
        (
            "RJMCP_VegetationApply.c",
            "RJMCP_VegetationApplyRequest",
            BridgeVegetationApplyRequest,
        ),
        (
            "RJMCP_VegetationApply.c",
            "RJMCP_VegetationApplyResponse",
            BridgeVegetationApplyResponse,
        ),
    ],
)
def test_top_level_python_models_match_real_handler_wire_fields(
    filename: str,
    class_name: str,
    model: type[BaseModel] | None,
) -> None:
    handler_fields = _wire_fields(_source(filename), class_name)
    expected = set() if model is None else _schema_fields(model)
    assert handler_fields == expected


@pytest.mark.parametrize(
    ("filename", "class_name", "model"),
    [
        ("RJMCP_GetContext.c", "RJMCP_GetContextResponse", BridgeContextResponse),
        ("RJMCP_TerrainSample.c", "RJMCP_TerrainSampleRequest", BridgeTerrainSampleRequest),
        ("RJMCP_TerrainSample.c", "RJMCP_TerrainSampleResponse", BridgeTerrainSampleResponse),
        (
            "RJMCP_VegetationApply.c",
            "RJMCP_VegetationApplyRequest",
            BridgeVegetationApplyRequest,
        ),
        (
            "RJMCP_VegetationApply.c",
            "RJMCP_VegetationApplyResponse",
            BridgeVegetationApplyResponse,
        ),
    ],
)
def test_top_level_python_scalar_object_and_array_types_match_handler_declarations(
    filename: str,
    class_name: str,
    model: type[BaseModel],
) -> None:
    assert _c_wire_types(_source(filename), class_name) == _schema_types(model)


@pytest.mark.parametrize(
    ("filename", "class_name", "model"),
    [
        ("RJMCP_GetContext.c", "RJMCP_ContextVec3", BridgeVec3),
        ("RJMCP_GetContext.c", "RJMCP_ContextTerrainBounds", BridgeTerrainBounds),
        ("RJMCP_TerrainSample.c", "RJMCP_TerrainPointRequest", BridgeTerrainPointRequest),
        ("RJMCP_TerrainSample.c", "RJMCP_TerrainSampleItem", BridgeTerrainSample),
    ],
)
def test_nested_python_models_match_real_handler_regv_fields(
    filename: str,
    class_name: str,
    model: type[BaseModel],
) -> None:
    assert _wire_fields(_source(filename), class_name) == _schema_fields(model)
    assert _c_wire_types(_source(filename), class_name) == _schema_types(model)


def test_all_mutation_request_fields_are_required_by_python_before_connection() -> None:
    # This is the Python boundary, not evidence of Enforce decoder behaviour.
    schema = BridgeVegetationApplyRequest.model_json_schema(by_alias=True)
    assert set(schema["required"]) == set(schema["properties"])
    terrain_schema = BridgeTerrainSampleRequest.model_json_schema(by_alias=True)
    assert set(terrain_schema["required"]) == {"points"}


def test_apply_response_requires_every_always_packed_handler_field() -> None:
    schema = BridgeVegetationApplyResponse.model_json_schema(by_alias=True)
    handler_fields = _wire_fields(
        _source("RJMCP_VegetationApply.c"), "RJMCP_VegetationApplyResponse"
    )
    assert set(schema["required"]) == handler_fields


def test_missing_zero_valid_scalars_have_invalid_enforce_defaults() -> None:
    """Inspect actual constructors; live JSON type/cardinality decoding is separate."""

    terrain = _source("RJMCP_TerrainSample.c")
    point = _class_body(terrain, "RJMCP_TerrainPointRequest")
    constructor = _block_after(point, "void RJMCP_TerrainPointRequest()")
    for axis in ("x", "z"):
        assert f"{axis} = 1000001.0;" in constructor
    assert "Math.AbsFloat(value) <= 1000000.0" in terrain

    apply = _source("RJMCP_VegetationApply.c")
    request = _class_body(apply, "RJMCP_VegetationApplyRequest")
    constructor = _block_after(request, "void RJMCP_VegetationApplyRequest()")
    assert "subscene = -1;" in constructor
    assert "maxSlopeDeg = -1.0;" in constructor
    response = _block_after(apply, "override JsonApiStruct GetResponse")
    begin = response.index("api.BeginEntityAction(")
    assert response.index("if (req.subscene < 0)") < begin
    assert response.index("req.maxSlopeDeg < 0") < begin


def test_bridge_models_accept_only_exact_lower_camel_wire_names() -> None:
    wire = {
        "requestedX": 1.0,
        "requestedZ": 2.0,
        "terrainY": 3.0,
        "normalX": 0.0,
        "normalY": 1.0,
        "normalZ": 0.0,
        "hasTerrain": True,
    }
    parsed = BridgeTerrainSample.model_validate(wire)
    assert parsed.model_dump(mode="json") == wire

    wrong_spelling = dict(wire)
    wrong_spelling["requested_x"] = wrong_spelling.pop("requestedX")
    with pytest.raises(ValidationError):
        BridgeTerrainSample.model_validate(wrong_spelling)


def test_bridge_and_catalog_fingerprints_are_embedded_exactly() -> None:
    apply = _source("RJMCP_VegetationApply.c")
    context = _source("RJMCP_GetContext.c")
    assert BRIDGE_PROTOCOL_VERSION in context
    assert BRIDGE_PROTOCOL_VERSION in apply
    assert BRIDGE_BUILD_ID in context
    assert BRIDGE_BUILD_ID in apply
    assert PRODUCTION_CATALOG.catalog_hash in _source("RJMCP_GetContext.c")
    assert PRODUCTION_CATALOG.catalog_hash in apply
    assert 'RegV("bridgeBuildId")' in _class_body(apply, "RJMCP_VegetationApplyRequest")
    response = _class_body(apply, "RJMCP_VegetationApplyResponse")
    assert 'RegV("bridgeBuildId")' in response
    assert 'RegV("catalogHash")' in response


def test_wire_array_element_types_and_nested_handler_classes_are_exact() -> None:
    terrain = _source("RJMCP_TerrainSample.c")
    apply = _source("RJMCP_VegetationApply.c")
    assert "ref array<ref RJMCP_TerrainPointRequest> points;" in _class_body(
        terrain, "RJMCP_TerrainSampleRequest"
    )
    assert "ref array<ref RJMCP_TerrainSampleItem> m_aResults;" in _class_body(
        terrain, "RJMCP_TerrainSampleResponse"
    )
    apply_request = _class_body(apply, "RJMCP_VegetationApplyRequest")
    for field in ("prefabs", "entityNames"):
        assert f"ref array<string> {field};" in apply_request
    for field in ("x", "y", "z", "yaw", "scale"):
        assert f"ref array<float> {field};" in apply_request
    assert "ref array<string> m_aEntityNames;" in _class_body(
        apply, "RJMCP_VegetationApplyResponse"
    )


def test_context_and_terrain_handlers_are_statically_read_only() -> None:
    forbidden = (
        "BeginEntityAction(",
        "CreateEntity(",
        "DeleteEntity(",
        "SetVariableValue(",
        "ClearEntitySelection(",
        "Save(",
        "CreateSubsceneLayer(",
    )
    for name in ("RJMCP_GetContext.c", "RJMCP_TerrainSample.c"):
        source = _source(name)
        assert all(token not in source for token in forbidden)


def test_context_uses_documented_world_and_shape_coordinate_contract() -> None:
    source = _source("RJMCP_GetContext.c")
    assert "api.GetWorldPath(response.worldPath);" in source
    assert "api.GetSelectedEntitiesCount()" in source
    assert "PolylineShapeEntity.Cast" in source
    assert ".IsClosed()" in source
    assert ".GetPointsPositions(" in source
    assert ".CoordToParent(" in source
    terrain_failure = source.index(
        'Fail(response, "TERRAIN_UNAVAILABLE", "Terrain bounds are unavailable")'
    )
    success = source.index('response.status = "ok";')
    assert "if (!worldEditor.GetTerrainBounds(boundsMin, boundsMax))" in source
    assert terrain_failure < success


def test_terrain_uses_one_batched_array_and_documented_sampling_contract() -> None:
    source = _source("RJMCP_TerrainSample.c")
    assert 'RegV("points")' in source
    assert "typedRequest.points.Count() > 1000" in source
    assert "api.TryGetTerrainSurfaceY(" in source
    assert "SCR_TerrainHelper.GetTerrainNormal(" in source
    assert 'StartArray("results")' in source
    assert "response.m_aResults.Count() != typedRequest.points.Count()" in source
    assert "BoundsAreFiniteAndOrdered(boundsMin, boundsMax)" in source
    assert "if (!world)" in source
    assert "&& IsFiniteCoordinate(terrainY)" in source
    assert "IsFiniteVector(normalPosition)" in source
    assert "IsFiniteVector(normal)" in source
    assert "IsFiniteCoordinate(normalLength)" in source
    assert "IsFiniteCoordinate(normalizedX)" in source
    assert "IsFiniteCoordinate(normalizedY)" in source
    assert "IsFiniteCoordinate(normalizedZ)" in source


def test_primitive_arrays_use_item_string_not_empty_named_store() -> None:
    combined = "\n".join(path.read_text() for path in HANDLERS)
    assert "ItemString(" in combined
    assert 'StoreString("",' not in combined


def test_apply_is_fail_closed_on_empty_exact_catalog_before_mutation() -> None:
    source = _source("RJMCP_VegetationApply.c")
    allowlist = _class_body(source, "RJMCP_VegetationApply")
    allowlist_start = allowlist.index("static bool IsAllowedPrefab")
    allowlist_end = allowlist.index("static bool CardinalityIsExact")
    allowlist_body = allowlist[allowlist_start:allowlist_end]
    assert "return false;" in allowlist_body
    assert ".Contains(" not in allowlist_body
    assert ".IndexOf(" not in allowlist_body
    assert source.index("IsAllowedPrefab(req.prefabs") < source.index("api.BeginEntityAction(")


def test_apply_compile_live_gate_dominates_every_create_mutation() -> None:
    source = _source("RJMCP_VegetationApply.c")
    handler = _class_body(source, "RJMCP_VegetationApply")
    response_body = _block_after(handler, "override JsonApiStruct GetResponse")
    assert "static const bool MUTATION_IMPLEMENTATION_VALIDATED = false;" in handler
    assert "MUTATION_IMPLEMENTATION_VALIDATED = true" not in handler

    gate = response_body.index("if (!MUTATION_IMPLEMENTATION_VALIDATED)")
    begin = response_body.index('api.BeginEntityAction("RJMCP vegetation')
    create = response_body.index("api.CreateEntity(")
    rename = response_body.index("api.RenameEntity(")
    cleanup = response_body.index("CleanupCreated(api, created)")
    assert gate < begin < create < rename < cleanup

    reconcile = response_body.index('if (req.mode == "reconcile")')
    reconcile_branch = _block_after(response_body, 'if (req.mode == "reconcile")')
    assert reconcile < gate < begin
    assert "return response;" in reconcile_branch
    assert all(
        mutation not in reconcile_branch
        for mutation in (
            "BeginEntityAction(",
            "EndEntityAction(",
            "CreateEntity(",
            "DeleteEntity(",
            "RenameEntity(",
            "SetVariableValue(",
        )
    )


def test_scale_uses_checked_editor_source_writes_inside_cleanup_responsibility() -> None:
    source = _source("RJMCP_VegetationApply.c")
    response_body = _block_after(
        _class_body(source, "RJMCP_VegetationApply"),
        "override JsonApiStruct GetResponse",
    )
    begin = response_body.index('api.BeginEntityAction("RJMCP vegetation')
    assert response_body.index("req.scaleMin <= 0 || req.scaleMin > req.scaleMax") < begin
    assert response_body.index("req.scale[scaleIndex] < req.scaleMin") < begin
    assert response_body.index("req.scale[scaleIndex] > req.scaleMax") < begin
    assert '"UNSUPPORTED_SCALE"' not in response_body
    assert "req.scaleMin != 1.0" not in response_body
    cleanup_ownership = response_body.index("created.Insert(source);")
    setter = response_body.index('api.SetVariableValue(source, null, "scale",')
    verification = response_body.index(
        "if (!EntityMatches(api, source, req, createIndex, layerId))"
    )
    rollback = response_body.index("if (createFailed)")
    end = response_body.index('api.EndEntityAction("RJMCP vegetation "')
    assert begin < cleanup_ownership < setter < verification < rollback < end
    setter_failure = _block_after(
        response_body,
        'if (!api.SetVariableValue(source, null, "scale", req.scale[createIndex].ToString()))',
    )
    assert "createFailed = true;" in setter_failure
    assert "break;" in setter_failure
    assert 'source.Get("scale", currentSourceScale)' in response_body
    assert "Math.AbsFloat(currentSourceScale - req.scale[createIndex]) > SCALE_EPSILON" in (
        response_body
    )
    matches = _block_after(source, "static bool EntityMatches")
    assert 'source.Get("scale", sourceScale)' in matches
    assert "Math.AbsFloat(sourceScale - req.scale[index]) > SCALE_EPSILON" in matches
    assert "Math.AbsFloat(actualScale - req.scale[index]) > SCALE_EPSILON" in matches
    for forbidden in (
        ".SetScale(",
        ".SetOrigin(",
        ".SetAngles(",
        ".SetYawPitchRoll(",
    ):
        assert forbidden not in source


def test_apply_uses_finite_exact_and_circular_transform_comparison() -> None:
    source = _source("RJMCP_VegetationApply.c")
    entity_matches = _block_after(source, "static bool EntityMatches")
    circular = _block_after(source, "static float CircularYawDifference")
    assert "while (difference >= 360.0)" in circular
    assert "if (difference > 180.0)" in circular
    assert "entity.GetYawPitchRoll()" in entity_matches
    assert "OrientationMatches(yawPitchRoll, req.yaw[index])" in entity_matches
    assert "IsFiniteVector(position)" in entity_matches
    assert "IsFiniteBounded(actualScale)" in entity_matches
    assert "ancestor.GetResourceName() != req.prefabs[index]" in entity_matches
    assert ".GetResourceName().GetPath()" not in source


def test_yaw_source_api_and_orientation_axes_match_official_documented_contract() -> None:
    """Source/API regression, not execution of Enforce or proof of engine transforms.

    Official IEntity documents GetYawPitchRoll as yaw/pitch/roll and GetAngles
    as X/Y/Z. Bohemia's SampleWorldEditorTool writes yaw to angleY after
    CreateEntity(..., vector.Zero). This checks those actual call sites.
    """

    source = _source("RJMCP_VegetationApply.c")
    assert ".GetAngles()" not in source
    orientation = _block_after(source, "static bool OrientationMatches")
    assert "IsFiniteVector(yawPitchRoll)" in orientation
    comparisons = re.findall(
        r"CircularYawDifference\(yawPitchRoll\[(\d)\], ([^)]+)\) <= TRANSFORM_EPSILON",
        orientation,
    )
    assert comparisons == [("0", "plannedYaw"), ("1", "0"), ("2", "0")]

    response = _block_after(source, "override JsonApiStruct GetResponse")
    creation = response[response.index("IEntitySource source = api.CreateEntity(") :]
    create_arguments = creation[: creation.index(");")]
    assert "vector.Zero" in create_arguments
    assert "req.yaw" not in create_arguments
    cleanup_ownership = response.index("created.Insert(source);")
    setter = response.index('api.SetVariableValue(source, null, "angleY",')
    assert cleanup_ownership < setter
    failure = _block_after(
        response,
        'if (!api.SetVariableValue(source, null, "angleY", req.yaw[createIndex].ToString()))',
    )
    assert "createFailed = true;" in failure
    assert "break;" in failure
    assert 'source.Get("angleY", currentSourceYaw)' in response
    assert "CircularYawDifference(currentSourceYaw, req.yaw[createIndex]) > TRANSFORM_EPSILON" in (
        response
    )


def test_apply_rechecks_editor_terrain_layer_and_conflicts_before_begin() -> None:
    source = _source("RJMCP_VegetationApply.c")
    begin = source.index('api.BeginEntityAction("RJMCP vegetation')
    required_before_begin = (
        "api.IsGameMode()",
        "api.IsPrefabEditMode()",
        "api.IsDoingEditAction()",
        "api.UndoOrRedoIsRestoring()",
        "api.GetWorldPath(actualWorld)",
        "api.GetSubsceneLayerId(",
        "api.GetSubsceneLayerPath(",
        "api.IsEntityLayerLockedHierarchy(",
        "api.TryGetTerrainSurfaceY(",
        "SCR_TerrainHelper.GetTerrainNormal(",
        "BoundsAreFiniteAndOrdered(boundsMin, boundsMax)",
        "!IsFiniteBounded(currentY)",
        "IsFiniteVector(normalPosition)",
        "IsFiniteVector(normal)",
        "IsFiniteBounded(normalLength)",
        "IsFiniteBounded(normalizedY)",
        "SPACING_VIOLATION",
        "PARTIAL_OPERATION",
        "ENTITY_CONFLICT",
    )
    assert all(source.index(token) < begin for token in required_before_begin)
    assert "CreateSubsceneLayer(" not in source
    assert "targetLayer = 0" not in source


def test_reconcile_checks_editor_stability_then_skips_create_only_preflight() -> None:
    source = _source("RJMCP_VegetationApply.c")
    response_body = _block_after(
        _class_body(source, "RJMCP_VegetationApply"),
        "override JsonApiStruct GetResponse",
    )
    world_identity = response_body.index("api.GetWorldPath(actualWorld)")
    layer_identity = response_body.index("api.GetSubsceneLayerId(")
    layer_round_trip = response_body.index("api.GetSubsceneLayerPath(")
    stable_editor = response_body.index(
        "if (api.IsDoingEditAction() || api.UndoOrRedoIsRestoring())"
    )
    classification = response_body.index("int presentCount = 0;")
    reconcile_exit = response_body.index('if (req.mode == "reconcile")')
    gate = response_body.index("if (!MUTATION_IMPLEMENTATION_VALIDATED)")
    edit_mode = response_body.index("api.IsGameMode()")
    prefab_mode = response_body.index("api.IsPrefabEditMode()")
    editor_busy = response_body.index("api.IsDoingEditAction()", gate)
    layer_lock = response_body.index("api.IsEntityLayerLockedHierarchy(")
    terrain_bounds = response_body.index("worldEditor.GetTerrainBounds(")
    terrain_sample = response_body.index("api.TryGetTerrainSurfaceY(")
    spacing = response_body.index("SPACING_VIOLATION")
    begin = response_body.index('api.BeginEntityAction("RJMCP vegetation')

    assert world_identity < layer_identity <= layer_round_trip < stable_editor < classification
    assert classification < reconcile_exit < gate
    assert gate < edit_mode < prefab_mode < editor_busy < layer_lock
    assert layer_lock < terrain_bounds < terrain_sample < spacing < begin

    busy_branch = _block_after(
        response_body,
        "if (api.IsDoingEditAction() || api.UndoOrRedoIsRestoring())",
    )
    assert 'Fail(response, "EDITOR_BUSY",' in busy_branch
    assert "return response;" in busy_branch
    assert "BeginEntityAction(" not in busy_branch
    assert stable_editor < response_body.index('response.state = "COMPLETE";')
    assert stable_editor < reconcile_exit

    reconciliation_region = response_body[classification:gate]
    for state in ('"COMPLETE"', '"PARTIAL"', '"CHANGED"', '"NONE"'):
        assert state in reconciliation_region
    for create_only in (
        "IsGameMode(",
        "IsPrefabEditMode(",
        "IsDoingEditAction(",
        "UndoOrRedoIsRestoring(",
        "IsEntityLayerLockedHierarchy(",
        "GetTerrainBounds(",
        "TryGetTerrainSurfaceY(",
        "GetTerrainNormal(",
        "SPACING_VIOLATION",
        "BeginEntityAction(",
        "CreateEntity(",
        "DeleteEntity(",
        "RenameEntity(",
    ):
        assert create_only not in reconciliation_region


def test_apply_has_conservative_rollback_post_end_verification_and_no_save() -> None:
    source = _source("RJMCP_VegetationApply.c")
    response_body = _block_after(
        _class_body(source, "RJMCP_VegetationApply"),
        "override JsonApiStruct GetResponse",
    )
    assert "if (!api.BeginEntityAction(" in source
    assert "if (!created[i] || !api.DeleteEntity(created[i]))" in source
    assert "CountEntitiesByName(api, createdNames[verifyIndex], ignoredEntity) != 0" in source
    assert (
        "response.rollbackVerified = cleanupSucceeded && absentAfterEnd && sourcesAbsentAfterEnd;"
        in source
    )

    cleanup = response_body.index("bool cleanupSucceeded = CleanupCreated(api, created);")
    rollback_end = response_body.index(
        'bool actionEnded = api.EndEntityAction("RJMCP vegetation rollback")'
    )
    absence = response_body.index("bool absentAfterEnd = EntityNamesAreAbsent(api, createdNames);")
    source_absence = response_body.index(
        "bool sourcesAbsentAfterEnd = EntitySourcesAreAbsent(api, created);"
    )
    assert cleanup < rollback_end < absence < source_absence

    normal_end = response_body.index('bool actionEnded = api.EndEntityAction("RJMCP vegetation "')
    post_end = response_body.index("VerifyExactBatch(api, req, layerId, postEndMatchingCount)")
    assert normal_end < post_end
    assert response_body[normal_end:post_end].count("api.EndEntityAction(") == 1
    assert "CleanupCreated(" not in response_body[normal_end:post_end]
    assert "rollbackEnd" not in source
    assert "cleanupAfterEndFailure" not in source
    assert source.count("api.EndEntityAction(") == 2

    multiplicity = _block_after(source, "static int CountEntitiesByName")
    assert "api.GetEditorEntityCount()" in multiplicity
    assert "api.GetEditorEntity(entityIndex)" in multiplicity
    source_absence_check = _block_after(source, "static bool EntitySourcesAreAbsent")
    assert "candidate == created[createdIndex]" in source_absence_check
    exact_batch = _block_after(source, "static bool VerifyExactBatch")
    assert "CountEntitiesByName(" in exact_batch
    assert "!= 1" in exact_batch
    assert "EntityMatches(" in exact_batch
    assert '"ROLLBACK_FAILED"' in source
    assert '"UNKNOWN_OUTCOME"' in source
    assert '"POST_COMMIT_VERIFICATION_FAILED"' in source
    assert "Save(" not in source
    assert "Regenerate" not in source


def test_known_upstream_field_mismatch_names_are_not_reintroduced() -> None:
    schemas = {
        "context": _schema_fields(BridgeContextResponse),
        "terrain": _schema_fields(BridgeTerrainSampleResponse),
        "apply": _schema_fields(BridgeVegetationApplyResponse),
    }
    combined_fields = set().union(*schemas.values())
    # The new contract deliberately uses unambiguous targetLayer/entityNames/
    # selectionCount. It never mixes the old TypeScript/handler pairs below.
    assert (
        not {
            "layerPath",
            "layerID",
            "entityName",
            "name",
            "selectedEntities",
            "selected",
            "totalCount",
            "total",
        }
        & combined_fields
    )
