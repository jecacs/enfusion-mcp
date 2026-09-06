# Staged Enforce bridge

The repository stages exactly three handlers under
`bridge/Scripts/WorkbenchGame/EnfusionMCP/`. They are source artifacts only and are
not installed by any server tool or development command.

- `EnfusionMCP_GetContext`: read-only world/subscene/layer/terrain/selection and
  polygon-compatible `PolylineShapeEntity` points transformed from local to
  world coordinates.
- `EnfusionMCP_TerrainSample`: one ordered batch of 1–1000 X/Z points using
  `TryGetTerrainSurfaceY` and a normalized full XYZ normal from
  `SCR_TerrainHelper.GetTerrainNormal`.
- `EnfusionMCP_VegetationApply`: trusted flat create/reconcile envelope, physical
  preflight, deterministic names, one entity action, verified cleanup attempts,
  and no save.

The bridge contains no addon ID or fixed map resource. Python requires an
explicit `ENFUSION_ALLOWED_WORLD` and checks observed context against that exact
value. The apply handler rejects an empty requested world, then requires the
current Workbench world and subscene to exactly match the stored plan's request
before reconciliation or mutation. A different world is stale; no wildcard or
substring match authorizes it. Direct NET API callers remain outside Python's
configuration and ledger boundary.

## Source evidence

The source was written against these published signatures and examples,
rechecked on 2026-09-06:

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
  behaviour;
- [IEntity](https://community.bistudio.com/wikidata/external-data/arma-reforger/EnfusionScriptAPIPublic/interfaceIEntity.html):
  `GetYawPitchRoll` returns yaw/pitch/roll, while `GetAngles` returns X/Y/Z
  rotations; these two orders must not be mixed;
- Bohemia Interactive's official
  [SampleWorldEditorTool.c](https://github.com/BohemiaInteractive/Arma-Reforger-Samples/blob/main/SampleMod_WorkbenchPlugin/Scripts/WorkbenchGame/SamplePlugins/SampleWorldEditorTool.c):
  `RandomizeScale` and `OnMousePressEvent` write the entity-source `scale`
  property through `WorldEditorAPI.SetVariableValue` inside an entity action.
  `OnMouseReleaseEvent` writes yaw from `VectorToAngles()[0]` to the source
  `angleY` property. The bridge uses these API operations; it does not copy
  the sample tool or its randomization/selection behaviour.

MIT-licensed upstream handlers at commit
`0acfa884228477043c6b4c2b8c7f0c270d648398` supplied examples of the
`JsonApiStruct`/`RegV`/`OnPack` and `Workbench.GetModule(WorldEditor)` patterns.
The new handlers have independent schemas and a narrow policy. Primitive
response arrays use `ItemString`, never the old `StoreString("", ...)` pattern.

## Editor-source transforms

The build fingerprint is `enfusion-mcp-bridge-v1-map-agnostic`, with protocol
`enfusion-mcp-bridge-v1`. Creation uses
`CreateEntity(..., vector.Zero)` followed by checked editor-source writes to
`angleY` and `scale`, in the same `BeginEntityAction`/`EndEntityAction` batch.
This follows the official sample's explicit yaw property and does not assume
that a nonzero `CreateEntity` rotation argument uses yaw/pitch/roll order.
There are no runtime-only entity transform setters.

Every returned entity source enters the cleanup set before either property
write. Current source values are read first; a value already matching the plan
does not cause a no-change setter call. Every setter that is called has its
boolean result checked. A failed read, setter, or verification ends the create
loop and enters the existing checked rollback path.

Verification reads source `angleY`/`scale` and the runtime entity again. It checks
world position within 0.01 m, yaw/pitch/roll via `GetYawPitchRoll` within 0.01
degrees using circular angular distance, and both stored/runtime scale within
0.000001. Pitch and roll must remain zero. Scale must be finite, positive, no
greater than 10, and placements must lie in the declared plan scale range.
The same checks run after the action ends and during reconciliation. Actual
editor reinitialization, persisted source round-trips and Undo remain live
acceptance checks; source inspection does not prove their runtime behaviour.

Before scanning deterministic entities or returning any reconciliation
classification, the handler requires both `IsDoingEditAction()` and
`UndoOrRedoIsRestoring()` to be false. An active edit/Undo/Redo returns
`EDITOR_BUSY` without classifying the batch, so an intermediate entity view
cannot resolve an uncertain operation or release the target-wide mutation
fence. Layer locks and terrain changes remain create-only checks: they do not
prevent read-only reconciliation once the editor is stable. Create repeats
the edit/Undo/Redo guard before its terrain preflight and mutation action.

## Fail-closed production catalog

No production vegetation prefab has been validated for a target Tools/addon
build. The Python production catalog and
the handler allowlist are therefore both empty and share the exact same hash.
`EnfusionMCP_VegetationApply` rejects every create before `BeginEntityAction` with
`CATALOG_NOT_READY`; an independent
`MUTATION_IMPLEMENTATION_VALIDATED=false` gate also control-dominates every
create mutation. Changing the catalog alone cannot enable writes.

This is intentional. Merely finding a resource-looking string in a fixture does
not prove that Workbench can load it or that it is appropriate vegetation. A
permissioned later stage must establish exact resources, update one canonical
catalog, regenerate/check both allowlists, and produce new handler hashes.

## Evidence level and known risks

Static contract tests parse the actual staged `.c` files, compare `RegV` and
dynamically packed array fields with the Python wire models, check endpoint
class names, and reject broad/mutating APIs in read handlers. The yaw tests
check the documented getter order and the source property used by the official
sample; they do not execute Enforce. Setter tests check cleanup ownership,
checked failure branches, source/runtime read-back and action ordering. This
is `STATIC_VERIFIED`, not compilation or physical-transform evidence.

Python required-field tests establish the Python validation boundary. Enforce
request constructors now use invalid defaults where zero is otherwise valid:
terrain X/Z start outside the allowed coordinate bound, and subscene/max slope
start at -1. These defaults prevent omitted values silently becoming accepted
zeroes. Static checks cover those constructor assignments and rejection checks;
actual JSON scalar types, missing/null fields, arrays-of-objects decoding and
required/optional equivalence still require live decoder tests.

Until a later explicit installation and clean live `ValidateScripts`:

- Enforce parser support for the chosen arrays-of-objects request shape is
  `UNVERIFIED_LIVE`;
- actual layer-not-found sentinel and resource-name round-trip are
  `UNVERIFIED_LIVE`;
- `CreateEntity` prefab identity, source `angleY`/`scale` read/write round-trips,
  entity reinitialization, and final yaw/scale transforms are `UNVERIFIED_LIVE`;
  the source-level property names and getter conventions have official API and
  sample evidence, but still need to be exercised in the target Tools build;
- cleanup and `EndEntityAction` semantics, including an empty history point on
  successful rollback, are `UNVERIFIED_LIVE`;
- exactly one Ctrl+Z removing a completed batch is `UNVERIFIED_LIVE`.

No code calls a save API. No layer is created. No default layer fallback,
generator regeneration, arbitrary prefab, arbitrary endpoint, or script
evaluation exists.

The current artifact is not the live-mutation revision. After a permissioned
read-only compile/contract stage, a later reviewed commit must establish the
exact catalog, prepare the explicit live acceptance of source transforms and
rollback, bump the build fingerprint, explicitly change the hard gate, show a
new installation manifest/hashes, obtain renewed installation approval, and
pass `ValidateScripts` again. Application also requires explicit approval of
the exact reviewed, non-expired plan.

## Upgrading an existing installation

The current package, executable, handler names, and protocol/build identities
replace those used by earlier revisions. Update all client configurations and
installed handlers together; older clients and bridges are not interchangeable
with this revision. Resolve outstanding operations with the matching previous
runtime/bridge before upgrading. Stop all cooperating clients and back up their
shared state, then review the new installation manifest. Do not delete the
ledger or change state directories to bypass an unresolved operation. Existing
plans refer to their original protocol/build identity and must not be replayed
through this revision.
