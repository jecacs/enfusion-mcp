# Bridge installation and live validation

The bridge is staged source. The Python server does not install it, start
Workbench, or enable world mutation. Choose the addon you want to use and review
the destination and files before installation. If an assistant performs the
copy, authorize that concrete installation; no special confirmation phrase is
required.

## Destination and file scope

Install under your addon's `Scripts/WorkbenchGame/EnfusionMCP/` directory. For
`ENFUSION_PROJECT_HOST_PATH=/path/to/addon`, the destination is:

```text
/path/to/addon/Scripts/WorkbenchGame/EnfusionMCP
```

`/path/to/addon` is a placeholder. Use the active addon directory whose
engine-facing path resolves through your configured Proton prefix. The source
and destination manifest contains only:

```text
bridge/Scripts/WorkbenchGame/EnfusionMCP/EnfusionMCP_GetContext.c
  -> <destination>/EnfusionMCP_GetContext.c
bridge/Scripts/WorkbenchGame/EnfusionMCP/EnfusionMCP_TerrainSample.c
  -> <destination>/EnfusionMCP_TerrainSample.c
bridge/Scripts/WorkbenchGame/EnfusionMCP/EnfusionMCP_VegetationApply.c
  -> <destination>/EnfusionMCP_VegetationApply.c
```

`resourceDatabase.rdb`, world `.ent` files, layers, terrain, and all other addon
files are outside the installation scope. Inspect existing destination files
before copying: do not overwrite an unexplained collision or delete unknown
files. Historical hashes in checkpoint reports describe older revisions;
calculate new hashes from the source revision you intend to install.

## Installation procedure

1. Resolve the active addon path and its realpath. Verify its project markers
   and the world resource you intend to configure; do not infer identity from
   a similarly named checkout or backup.
2. Create a timestamped backup of that addon in a separate backup location.
3. Record the exact source revision, source → destination manifest, file sizes,
   and SHA-256 hashes. Review any existing destination files and replacement
   decisions before copying.
4. Copy only the three listed handlers. Record the installation timestamp and
   installed hashes, and verify that every installed hash matches its source.
5. Manually restart Tools through Steam/Proton and enable NET API. The server
   does not launch or restart these applications.
6. Configure the Python server with the matching host/engine addon paths,
   Proton prefix, and exact `ENFUSION_ALLOWED_WORLD`. All cooperating MCP clients
   must share one canonical state directory outside the addon and prefix.

## First live validation

The first checks are read-only:

1. `IsWorkbenchRunning`;
2. `IsWorldEditorRunning`;
3. `ValidateScripts` with `Configuration=WORKBENCH`;
4. `EnfusionMCP_GetContext`.

These are Workbench NET API checks, not additional public MCP tools. The five
public tools are documented in the [README](../README.md). Perform script
validation in Workbench or through a separately reviewed deployment procedure.
Any script validation error stops the workflow. Check that the reported world
resource exactly matches `ENFUSION_ALLOWED_WORLD`; the setting is required and
has no project-specific default.

## Requirements before live plan/apply

The current source has an empty production catalog and
`MUTATION_IMPLEMENTATION_VALIDATED=false`. It can support compilation and
read-only context/terrain validation, but it cannot produce or apply a live
vegetation plan.

To enable a future live-capable revision:

1. Establish exact vegetation resource names for the target Tools/addon build
   and put the identical exact allowlist and catalog hash in Python and Enforce.
2. Validate editor-source scale/yaw writes, failure cleanup, and Undo against
   that live build; address script validation and read-only contract findings.
3. Review the implementation before changing `MUTATION_IMPLEMENTATION_VALIDATED`
   and bumping the bridge build ID.
4. Produce a reviewed source revision and a fresh installation manifest with
   hashes; review and authorize installation of those changed files.
5. Restart Workbench manually and pass `ValidateScripts` again.

Only that later revision may generate a small initial plan, such as 8–12
objects targeting `MCP_Preview`. Review the exact non-expired plan and explicitly
approve its application before any mutation. If the build, catalog, Shape, or
context changes, regenerate and review the plan. Installation permission does
not authorize applying a plan.
