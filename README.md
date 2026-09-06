# Enfusion MCP

A Python [Model Context Protocol](docs/CLIENT_CONFIG_GENERIC.md) server for
**Arma Reforger Enfusion Workbench**. Inspect the editor, read world context,
and prepare deterministic vegetation placements through an MCP client.

The target addon and world are configured explicitly. No particular map,
project name, or author-specific filesystem layout is required.

**Status: alpha (`0.1.0a0`).** The Python server, planner, coordination layer,
and staged Enforce bridge are implemented. The production vegetation catalog
is empty and a separate bridge mutation gate is disabled. Live vegetation
planning and creation therefore remain unavailable until the catalog and
bridge have been validated in Workbench. See [current limitations](#safety-and-current-limitations).

## What it provides

| MCP tool | Purpose | Effect |
| --- | --- | --- |
| `workbench_status` | Check Workbench and World Editor availability. | Reads editor status. |
| `world_context` | Inspect the configured world, layer, terrain bounds, and selected shape. | Reads editor context; requires the bridge. |
| `vegetation_catalog` | List approved vegetation prefabs and catalog readiness. | Reads the server-owned catalog. |
| `vegetation_plan` | Generate reproducible placements from a selected polygon, palette, seed, spacing, slope, and scale constraints. | Stores an immutable local plan; leaves the world unchanged. |
| `vegetation_apply` | Apply a stored plan or reconcile an uncertain result using an idempotency key. | Creates at most 100 entities when enabled; never saves the world. |

The server exposes these five tools over **STDIO**. It does not require an LLM
API key: the MCP client supplies the assistant and manages its own credentials.

```text
MCP client
    │ STDIO
    ▼
Python server on Linux ── SQLite ledger + process lock
    │ loopback NET API
    ▼
Enfusion Workbench through Proton
    └── EnfusionMCP bridge in your addon
```

## Requirements

- Linux and Python **3.11 or newer**.
- `uv` for the locked development environment.
- Arma Reforger Tools / Enfusion Workbench, launched manually through
  Steam/Proton, for editor integration.
- An MCP client that can launch a local STDIO process.

The supported runtime profile is `native-linux-proton`. Windows-native and
remote Workbench connections are not implemented. Workbench is not needed to
run the Python tests.

## Getting started

### 1. Install from a source checkout

```sh
git clone https://github.com/jecacs/enfusion-mcp.git
cd enfusion-mcp
uv sync --frozen
```

This installs the `enfusion-mcp` executable in `.venv/bin/` and the development
tools in the same virtual environment.

### 2. Configure your addon and world

All ten variables below are required. The server reads the process environment;
it does **not** load `.env` files or infer the target from the currently open map.

| Variable | Value |
| --- | --- |
| `ENFUSION_PLATFORM_MODE` | `native-linux-proton` |
| `ENFUSION_STEAM_TOOLS_APP_ID` | `1874910` |
| `ENFUSION_PROTON_PREFIX` | Absolute, canonical path to your existing Proton prefix (`pfx`). |
| `ENFUSION_WORKBENCH_HOST` | `127.0.0.1` |
| `ENFUSION_WORKBENCH_PORT` | `5775` |
| `ENFUSION_PROJECT_HOST_PATH` | Absolute, canonical Linux path to your existing addon directory. |
| `ENFUSION_PROJECT_ENGINE_PATH` | The same directory as a Windows path, mapped through that prefix's `dosdevices`. |
| `ENFUSION_ALLOWED_WORLD` | Exact world resource name, for example `$myaddon:world.ent` or `{0123456789ABCDEF}Worlds/Example.ent`. |
| `ENFUSION_SAFE_MODE` | `1` |
| `ENFUSION_STATE_DIR` | Absolute, canonical state directory outside both the addon and Proton prefix. Its parent must exist. |

Example environment, with placeholders to replace with your own paths and
world resource:

```sh
export ENFUSION_PLATFORM_MODE='native-linux-proton'
export ENFUSION_STEAM_TOOLS_APP_ID='1874910'
export ENFUSION_PROTON_PREFIX='/path/to/proton-prefix'
export ENFUSION_WORKBENCH_HOST='127.0.0.1'
export ENFUSION_WORKBENCH_PORT='5775'
export ENFUSION_PROJECT_HOST_PATH='/path/to/proton-prefix/drive_c/path/to/addon'
export ENFUSION_PROJECT_ENGINE_PATH='C:\path\to\addon'
export ENFUSION_ALLOWED_WORLD='$myaddon:world.ent'
export ENFUSION_SAFE_MODE='1'
export ENFUSION_STATE_DIR='/path/to/enfusion-mcp/.state'
```

The example path pair assumes `dosdevices/c:` points to the prefix's `drive_c`.
Use the real mapping for your installation. Quote world resource names in the
shell so `$` is preserved. Resource identifiers are compared exactly; an alias
and a GUID representation are not treated as interchangeable.

Each server process targets one configured world. To use another map, change
the project paths and world resource as needed, then restart the process.
Processes targeting the same Workbench endpoint must share the same state
directory, even when their configured maps differ.

See [Linux/Proton paths](docs/LINUX_PROTON.md) for mapping details and
[coordination](docs/MULTI_AGENT.md) for shared state requirements.

### 3. Prepare the Workbench bridge

World context, terrain sampling, and vegetation operations use the three
handlers in [`bridge/Scripts/WorkbenchGame/EnfusionMCP/`](bridge/Scripts/WorkbenchGame/EnfusionMCP/).
The Python installer does not copy them into an addon or start Workbench.

Follow the [bridge installation and validation procedure](docs/BRIDGE_INSTALLATION_PLAN.md)
for your chosen addon. Start Workbench manually, open the configured world,
and validate the handlers before using the editor integration. Installing the
staged bridge does not enable vegetation creation.

### 4. Connect an MCP client

Configure your client to launch the absolute path to
`/path/to/enfusion-mcp/.venv/bin/enfusion-mcp` with the environment above.
Use the complete [generic STDIO configuration](docs/CLIENT_CONFIG_GENERIC.md),
or the examples for [Codex](docs/CLIENT_CONFIG_CODEX.md),
[Claude Code](docs/CLIENT_CONFIG_CLAUDE.md), and
[other local hosts](docs/CLIENT_CONFIG_LOCAL_HOST.md).

Start with `workbench_status`, then `world_context` and `vegetation_catalog`.
The shipped catalog reports `production_ready: false`; this is expected.

## Vegetation workflow

Once a verified catalog and a validated bridge revision are available:

1. Open the configured world and manually create or select `MCP_Preview` or
   `MCP_Vegetation` as the target layer.
2. Select one closed, polygon-compatible shape outlining the placement area.
3. Inspect `world_context` and choose prefabs from `vegetation_catalog`.
4. Call `vegetation_plan` with a seed, count, spacing, slope, scale range, and
   weighted palette. Review the resulting placements and rejection statistics.
5. Call `vegetation_apply` with the returned `plan_id` and a UUID idempotency
   key. Keep that same key when checking an uncertain result.
6. Inspect the result in Workbench and save manually if desired.

Plans expire after 30 minutes. The intended editor operation is one undoable
batch; a single Ctrl+Z still requires live acceptance testing. Detailed inputs,
determinism guarantees, and retry behavior are documented in the
[vegetation workflow](docs/VEGETATION_MCP.md).

## Safety and current limitations

The server validates the configured world, project path mapping, catalog,
stored plan, and bridge identity. The bridge also checks that the requested
world and subscene match the editor before inspecting or creating a batch.
Selecting a different map cannot silently retarget an existing plan.

- Connections are restricted to literal `127.0.0.1:5775`.
- The server never launches Steam, Proton, Workbench, a shell, or a model API.
- Mutations are serialized across processes using a shared SQLite ledger and
  Linux file lock. Uncertain outcomes are reconciled before further creation.
- Tools expose no arbitrary script execution, endpoint forwarding, prefab
  spawning, automatic layer creation, or world saving.
- STDIO protocol output goes to stdout; diagnostics go to stderr.

The production catalog is deliberately empty, and
`MUTATION_IMPLEMENTATION_VALIDATED` remains `false`. Enforce source contracts
are checked by Python tests, but handler compilation, engine decoding,
transforms, rollback, and Undo still need live Workbench validation. Map
configuration support does not imply every addon or prefab has been tested.

For the detailed boundaries, see [safe mode](docs/SECURITY_SAFE_MODE.md),
[threat model](docs/THREAT_MODEL.md), and [bridge evidence](docs/ENFORCE_BRIDGE.md).

## Development

```sh
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
uv build --no-build-isolation
uv run twine check dist/*
```

Tests cover configuration, Proton path containment, NET API framing, STDIO
contracts, deterministic geometry, persistent coordination, and static bridge
contracts. They use temporary projects and fake Workbench endpoints.

The wheel contains the Python runtime and license notices. Bridge sources and
the full documentation are included in the source distribution. See
[architecture](docs/ARCHITECTURE.md) and [NET API protocol](docs/NET_API_PROTOCOL.md)
for implementation details. Earlier development evidence is retained in the
[implementation history](docs/IMPLEMENTATION_PLAN.md).

When upgrading an earlier installation, resolve outstanding operations first,
run `uv sync --frozen`, and update the client executable and all bridge handlers
to the matching revision. Package, handler, and protocol identifiers have been
generalized; old plans and entity names are not migrated. Keep the operation
ledger if any result is unresolved.

## License

[MIT](LICENSE). Upstream attribution and adapted integration patterns are
documented in [NOTICE.md](NOTICE.md).
