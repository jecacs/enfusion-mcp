# Staged Enforce bridge

The repository stages exactly three handlers under
`bridge/Scripts/WorkbenchGame/RJMCP/`. They are source artifacts only and are
not installed by any server tool or development command.

- `RJMCP_GetContext`: read-only world/subscene/layer/terrain/selection and
  polygon-compatible `PolylineShapeEntity` points transformed from local to
  world coordinates.
- `RJMCP_TerrainSample`: one ordered batch of 1–1000 X/Z points using
  `TryGetTerrainSurfaceY` and a normalized full XYZ normal from
  `SCR_TerrainHelper.GetTerrainNormal`.
- `RJMCP_VegetationApply`: trusted flat create/reconcile envelope, physical
  preflight, deterministic names, one entity action, verified cleanup attempts,
  and no save.

## Source evidence

The source was written against these published signatures, checked on
2026-09-05:

- [Workbench NET API](https://community.bistudio.com/wikidata/external-data/arma-reforger/EnfusionScriptAPIPublic/Page_NetApi.html):
  `NetApiHandler`, `JsonApiStruct`, request/response framing;
- [WorldEditorAPI](https://community.bistudio.com/wikidata/external-data/arma-reforger/EnfusionScriptAPIPublic/interfaceWorldEditorAPI.html):
  `BeginEntityAction`, `EndEntityAction`, editor busy/Undo/prefab state,
  selection, subscene/layer/hierarchy lock, `TryGetTerrainSurfaceY`,
  `CreateEntity`, `DeleteEntity`, and `GetWorldPath(out string)`;
- [ShapeEntity](https://community.bistudio.com/wikidata/external-data/arma-reforger/EnfusionScriptAPIPublic/interfaceShapeEntity.html):
  `IsClosed`, `GetPointsPositions`, and inherited `CoordToParent`;
- [SCR_TerrainHelper](https://community.bistudio.com/wikidata/external-data/arma-reforger/ArmaReforgerScriptAPIPublic/interfaceSCR__TerrainHelper.html):
  `GetTerrainNormal(inout vector, BaseWorld, ...)` and its Y-modifying
  behaviour.

MIT-licensed upstream handlers at commit
`0acfa884228477043c6b4c2b8c7f0c270d648398` supplied examples of the
`JsonApiStruct`/`RegV`/`OnPack` and `Workbench.GetModule(WorldEditor)` patterns.
The new handlers have independent schemas and a narrow policy. Primitive
response arrays use `ItemString`, never the old `StoreString("", ...)` pattern.

## Fail-closed production catalog

The permitted reference contains no vegetation prefab that can be proven valid
for the user's current Tools/project build. The Python production catalog and
the handler allowlist are therefore both empty and share the exact same hash.
`RJMCP_VegetationApply` rejects every create before `BeginEntityAction` with
`CATALOG_NOT_READY`; an independent
`MUTATION_IMPLEMENTATION_VALIDATED=false` gate also control-dominates every
create mutation. Changing the catalog alone cannot enable writes.

This is intentional. Merely finding a resource-looking string in a fixture does
not prove that Workbench can load it or that it is appropriate vegetation. A
permissioned later stage must establish exact resources, update one canonical
catalog, regenerate/check both allowlists, and produce new handler hashes.

## Evidence level and known risks

Static contract tests parse the actual staged `.c` files, compare `RegV` and
dynamically packed array fields with the real Python Pydantic wire models, check
endpoint class names, and reject broad/mutating APIs in read handlers. This is
`STATIC_VERIFIED`, not compilation evidence.

Until a later explicit installation and clean live `ValidateScripts`:

- Enforce parser support for the chosen arrays-of-objects request shape is
  `UNVERIFIED_LIVE`;
- actual layer-not-found sentinel and resource-name round-trip are
  `UNVERIFIED_LIVE`;
- `CreateEntity` prefab identity and transform angle conventions are
  `UNVERIFIED_LIVE`; runtime `IEntity.SetScale` is deliberately absent and the
  staged implementation accepts only identity scale;
- cleanup and `EndEntityAction` semantics, including an empty history point on
  successful rollback, are `UNVERIFIED_LIVE`;
- exactly one Ctrl+Z removing a completed batch is `UNVERIFIED_LIVE`.

No code calls a save API. No layer is created. No default layer fallback,
generator regeneration, arbitrary prefab, arbitrary endpoint, or script
evaluation exists.

The current artifact is not the live-mutation revision. After a permissioned
read-only compile/contract stage, a later reviewed commit must establish the
exact catalog, reconcile the planner's scale-range interface with the handler's
identity-scale-only policy, bump the build fingerprint, explicitly change the
hard gate, show a new installation manifest/hashes, obtain renewed installation
approval, and pass `ValidateScripts` again. Separate `применяй` approval is
still required for the exact shown plan.
