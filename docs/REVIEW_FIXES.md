# Independent review corrections

Review baseline: `1a633cb67d82a574dc26297377cf51f57470759c`.
Historical correction report dated 2026-09-06. Its verification results and
manifest describe the commits below, not the current source. Local paths and
package identifiers are generalized for public documentation. Windows path
normalization was outside this review's scope. No active-project, Proton,
Workbench, or port-5775 access was part of the revision.

Implementation commits: `8a95e62` (lock deadlines) and `341142b` (durable
mutation policy, planner/service validation, bridge transforms and regressions).

The original Checkpoint C test results were reproducible, but they did not
establish the complete requested safety contract. `CHECKPOINT_C.md` is retained
as a historical report; its old handler hashes are not the current manifest.

## Implemented corrections

- New create requests are blocked by unresolved operations for the entire
  Workbench endpoint, even when another plan/key/client/process is used.
  SQLite schema 2 adds immutable endpoint bindings. Legacy unresolved rows
  without a binding conservatively block all endpoints. Migration retains
  existing plans, operations, and event history.
- Plan expiry is checked in the transaction that claims SENDING, including
  retries after PLANNED/PRE_SEND_FAILED and contention on the database.
- UNKNOWN plus an absent batch remains UNKNOWN. Partial/failed rollback
  history is not cleared merely by observing no entities. Reconciliation
  cannot certify completion while an edit action or Undo/Redo is active.
- Diagnostic strings are bounded with a SHA-256 reference when truncated;
  error size cannot prevent recording UNKNOWN. Apply errors identify whether
  their state describes the requested invocation or an existing operation.
- The service and planner share one pre-I/O input validator for exact catalog
  membership, weights, and numerical precision. Inputs are validated into a
  separate snapshot, including nested mutable containers.
- Zero surviving placements retain the full rejection statistics and
  UNDERFILLED warning. This result has no plan ID and is never stored/applied.
- The staged handler reads orientation with GetYawPitchRoll and checks pitch
  and roll as well as yaw. Checked editor-source angleY and scale writes replace
  the wrong-axis comparison and identity-scale-only restriction. Each created
  entity is owned by rollback before either setter is called.
- Production request/response processing now uses the same real Pydantic
  bridge models checked against staged source. Response fields cannot silently
  default when absent. Contract tests distinguish source evidence from live
  decoder/transform behaviour.
- The async lock deadline includes both its local queue and Linux flock.
  Timed-out writers wake readers instead of leaving them behind stale writer
  preference.

The corrected revision used bridge build fingerprint `enfusion-mcp-bridge-v1-review-fixes`; old plans with
the old bridge build remain stale. The planner algorithm and draw sequence are
unchanged. Its golden hash changes because the bridge build is part of the
immutable plan.

## Evidence and permission limits

The yaw/scale implementation follows the official Bohemia API and
SampleWorldEditorTool source referenced in `ENFORCE_BRIDGE.md`. Static tests
cannot execute Enforce. Compilation, source/runtime transform round trips,
resource loading, rollback, and one Ctrl+Z remain unverified in live Workbench.

The production catalog remains empty because no permitted source establishes
the required exact vegetation prefabs. `MUTATION_IMPLEMENTATION_VALIDATED`
remains false. This revision is staged and cannot enable live creation simply
by changing client configuration. No handler was installed.

Windows trailing-dot/path normalization findings were outside the scope of
this correction; `path_types.py` and its tests were unchanged in that revision.
Native Linux/Proton remains the runtime target.

The reusable installation and backup/collision procedure is in
[BRIDGE_INSTALLATION_PLAN.md](BRIDGE_INSTALLATION_PLAN.md). Review the exact
files and destination before installation; review and approve a concrete plan
before application. Do not delete state or switch state directories to bypass
unresolved operations.

Before deploying this revision to any existing MCP clients, stop all old Python
server processes, back up the shared state while they are stopped, and restart
all clients with the corrected revision. Schema migration cannot make an
already-running old process follow the new guards. This development task did
not start/stop user applications or migrate any active-project state.

## Verification

Commands ran inside this repository using its existing local `.venv`. Network
tests used only fake servers on ephemeral loopback ports. Mutation regression
tests used synthetic plans/bridges and temporary ledgers.

```text
.venv/bin/pytest -q
430 passed in 6.83s; exit 0 (final code at 341142b)

.venv/bin/pytest -q tests/test_coordinator.py tests/test_ledger.py tests/test_locking.py -k 'process or independent_client or two_stdio'
10 passed, 121 deselected in 1.95s; exit 0

.venv/bin/ruff format --check .
51 files already formatted; exit 0

.venv/bin/ruff check .
All checks passed!; exit 0

.venv/bin/mypy
Success: no issues found in 28 source files; exit 0

UV_CACHE_DIR=/tmp/enfusion-mcp-review-uv-cache uv lock --check --offline
Resolved 90 packages; exit 0

.venv/bin/pip-audit --progress-spinner off --requirement /tmp/enfusion-mcp-fixes-prod.txt
No known vulnerabilities found; exit 0

.venv/bin/pip-audit --progress-spinner off --requirement /tmp/enfusion-mcp-fixes-full.txt
No known vulnerabilities found; exit 0
```

Package validation used the existing locked local build tools, without network
or build isolation:

```text
UV_CACHE_DIR=/tmp/enfusion-mcp-review-uv-cache uv build --no-build-isolation --offline --out-dir /tmp/enfusion-mcp-fixes-build.GJhedm
Successfully built enfusion_mcp-0.1.0a0.tar.gz
Successfully built enfusion_mcp-0.1.0a0-py3-none-any.whl
exit 0

.venv/bin/twine check /tmp/enfusion-mcp-fixes-build.GJhedm/*
wheel: PASSED; sdist: PASSED; exit 0
```

The final documentation-containing artifacts are rebuilt after writing this
report. Their hashes are reported in the final handoff; the sdist cannot embed
a hash of itself here. Nothing is published.

The audit inputs were separate frozen/hash-bearing `uv export` outputs with
`--no-emit-project`, production using `--no-dev` and full using `--all-groups`.
They contained 30 production and 89 full-tree distributions respectively.
`pyproject.toml` and `uv.lock` are unchanged from the reviewed baseline. The 50
additional pytest cases cover the observed failures and adjacent uncertainty,
transaction-contention, model-contract and lock-cancellation paths. Static
Enforce tests remain 36 source/contract checks, not runtime engine tests.

## Historical staged manifest

These sizes/hashes apply only to the correction commits above. Calculate fresh
hashes before installing the current source. The original unread destination is
represented by this placeholder:

```text
/path/to/addon/Scripts/WorkbenchGame/EnfusionMCP
```

| Source under `bridge/Scripts/WorkbenchGame/EnfusionMCP/` | Bytes | SHA-256 |
|---|---:|---|
| `EnfusionMCP_GetContext.c` | 5503 | `0a85a537d27095132060ea54f6594063b07d371f4a0407470df1ad60f9830d88` |
| `EnfusionMCP_TerrainSample.c` | 6575 | `738318ebcf0d60b54c99578792a5da180aad71b6d66c2749f44b6797bf024dfd` |
| `EnfusionMCP_VegetationApply.c` | 24489 | `95152f33db112f9a50271da262827b146fa12c309cfbbe46a33e8c04dc981d57` |

Only these three handler roles were proposed for installation at the time.
Existing-file collisions were unknown because the active project was not read.
No files were installed, overwritten, or removed there. The earlier manifest in
`CHECKPOINT_C.md` applies only to its own historical source revision.
