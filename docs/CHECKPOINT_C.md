# Checkpoint C evidence and stop report

Historical report for the original pre-install artifact at `1a633cb`.
An independent review subsequently found logic defects and incomplete scale
support. Its original test results and manifest below are retained as historical
evidence, not current readiness or installation approval. See
`REVIEW_FIXES.md` for the corrective revision and current verification limits.

Checkpoint C was completed on 2026-09-06 on branch
`feature/python-linux-proton-safe-vegetation`. The original repository baseline
is `9e46b6e51819764acb4e6d08c2838af3895e49ff` (`main`). The implementation
stops here before bridge installation or any live Workbench access.

## Delivered boundary

- deterministic, pure Python PCG32 vegetation planner with bounded candidate
  generation, polygon canonicalization, one terrain batch, fixed-point plan
  serialization, SHA-256 identity, TTL, and rejection statistics;
- shared SQLite immutable plans and operation state machine;
- in-process read/write coordination plus endpoint-wide Linux `fcntl.flock`;
- create-once, post-send-unknown, same-key reconciliation semantics across
  independent STDIO processes;
- strict lowerCamelCase Python↔Enforce models and exact apply build/catalog
  fingerprints;
- three staged, attributed `RJMCP_*.c` handlers;
- generic, Codex, Claude, and local/self-hosted STDIO configuration examples;
- a deliberately empty production vegetation catalog and independent
  `MUTATION_IMPLEMENTATION_VALIDATED=false` handler gate.

The last two points are intentional. This artifact is suitable for static and
fake-process verification, not live mutation. The original prompt's assumption
that an empty/unproven catalog can immediately produce an 8–12-object live plan
was corrected in `PROMPT_REVIEW.md` and `BRIDGE_INSTALLATION_PLAN.md`.

## Exact code/handler verification results

All commands ran from the new repository before this evidence file was added.
TCP tests used only test-owned fake listeners on ephemeral loopback ports; no
command probed `127.0.0.1:5775`. The post-documentation build is repeated and
reported in the external handoff because an sdist cannot contain a stable hash
of itself inside this report.

```text
git diff --check
exit 0; no output

UV_CACHE_DIR=/tmp/enfusion-mcp-rj-uv-cache uv lock --check
exit 0; Resolved 90 packages

.venv/bin/ruff format --check .
exit 0; 49 files already formatted

.venv/bin/ruff check .
exit 0; All checks passed!

.venv/bin/mypy
exit 0; Success: no issues found in 28 source files

.venv/bin/pytest -q
exit 0; 380 passed in 5.92s
```

The required suites were also run separately:

```text
.venv/bin/pytest -q tests/test_net_api.py
41 passed in 0.42s

.venv/bin/pytest -q tests/test_stdio_blackbox.py tests/test_server_contract.py
16 passed in 3.33s

.venv/bin/pytest -q tests/test_planner.py tests/test_catalog.py
73 passed in 0.66s

.venv/bin/pytest -q tests/test_enforce_contract.py
33 passed in 0.18s

.venv/bin/pytest -q tests/test_coordinator.py tests/test_ledger.py tests/test_locking.py tests/test_service_planning.py
110 passed in 3.03s

.venv/bin/pytest -q tests/test_coordinator.py tests/test_ledger.py tests/test_locking.py -k 'process or two_stdio or independent_client'
9 passed, 94 deselected in 2.20s

.venv/bin/pytest -q tests/test_paths.py tests/test_config.py
99 passed in 0.17s
```

Build and metadata validation:

```text
UV_CACHE_DIR=/tmp/enfusion-mcp-rj-uv-cache uv build --no-build-isolation --out-dir /tmp/enfusion-mcp-rj-checkpoint-c-build.o6vvcg
exit 0
Successfully built enfusion_mcp_rj-0.1.0a0.tar.gz
Successfully built enfusion_mcp_rj-0.1.0a0-py3-none-any.whl

.venv/bin/twine check <wheel> <sdist>
exit 0; wheel PASSED; sdist PASSED

pre-report wheel SHA-256: 1f527cbcfea7a1e96935d6118d4069b33378b34889a91630013d764de524aca3
pre-report sdist SHA-256: 1574959f1752fbf2729ba4e27e80d96eaaea8fe969f81b797aeedc189102fe9f
```

The wheel contains the Python runtime, `LICENSE`, and `NOTICE.md`. The sdist
also contains `uv.lock`, all documentation, and all three staged handlers.
Neither artifact was published.

Dependency audits were intentionally separated and rerun against frozen,
hash-bearing exports with the project itself omitted:

```text
production export: 30 distributions
.venv/bin/pip-audit --progress-spinner off --requirement /tmp/enfusion-mcp-rj-prod-final.txt
exit 0; No known vulnerabilities found

full dev/test export: 89 distributions
.venv/bin/pip-audit --progress-spinner off --requirement /tmp/enfusion-mcp-rj-full-final.txt
exit 0; No known vulnerabilities found
```

Static policy scans found exactly five `@mcp.tool` registrations, no
`package.json`/JavaScript/TypeScript runtime, no OpenAI/Anthropic/Codex/Claude/
LangChain/LangGraph imports, and no source use of Steam launch, shell execution,
`EvaluateScript`, `0.0.0.0`, or `resourceDatabase.rdb`.

## Local commits before the final documentation commit

```text
297e65e feat: stage fail-closed Enforce vegetation bridge
e77cc2c feat: add deterministic planning and coordinated apply
321f58d feat: add safe platform protocol and MCP core
a7657dd chore: establish audited Python MCP scaffold
```

No commit was pushed. The final `git log` in the handoff also includes the
documentation commit that contains this report.

## Staged bridge manifest

Calculated destination (not resolved, listed, or read):

```text
/home/jecacs/.local/share/Steam/steamapps/compatdata/1874910/pfx/drive_c/users/steamuser/Documents/My Games/ArmaReforgerWorkbench/addons/new_rj/Scripts/WorkbenchGame/RJMCP
```

Only these files are proposed:

| Staged source | Bytes | SHA-256 | Proposed destination filename |
|---|---:|---|---|
| `bridge/Scripts/WorkbenchGame/RJMCP/RJMCP_GetContext.c` | 5506 | `8d15c9f1765aa417a98ffd3b0f9e03ff9ba1a4f9f444fe4bc590ee3f3298a986` | `RJMCP_GetContext.c` |
| `bridge/Scripts/WorkbenchGame/RJMCP/RJMCP_TerrainSample.c` | 6463 | `2af16362d9d71ae50e1ba8894e49c06a663db43ae2c6de7717811289b481f261` | `RJMCP_TerrainSample.c` |
| `bridge/Scripts/WorkbenchGame/RJMCP/RJMCP_VegetationApply.c` | 22032 | `2e6f98df929089c4bd060268f69acfb8688ce756eefe956f8dfb271390654fa2` | `RJMCP_VegetationApply.c` |

Collision status is **unknown by design**: the active project was not read.
Possible conflicting existing files are those exact three destination names;
no claim is made about whether they exist. No unknown file may be deleted or
overwritten. `resourceDatabase.rdb`, `rj.ent`, `default.layer`, terrain, and all
other project files are excluded.

## Known risks and blockers

1. Enforce compilation is **UNVERIFIED_LIVE**. Static parsing and published API
   inspection are not a substitute for the permitted real `ValidateScripts`.
2. The production catalog is empty because no real vegetation prefab was
   provable from the permitted sibling reference. Planning/apply fail closed.
3. Create is independently disabled by
   `MUTATION_IMPLEMENTATION_VALIDATED=false`. Catalog changes alone cannot
   enable it.
4. A later live-capable revision must prove the actual `ResourceName`
   representation, handler registration/JSON packing, yaw convention,
   post-`EndEntityAction` observation, cleanup, and Undo behaviour.
5. The staged handler permits identity scale only and contains no runtime
   `IEntity.SetScale`. The public scale-range contract must be reconciled before
   enabling create.
6. One completed batch being removed by exactly one Ctrl+Z remains unproven.
7. NET API has no real authentication. Python's ledger policy protects
   cooperating MCP processes, not another same-user process calling NET API
   directly.
8. Cross-process guarantees require every cooperating host to use the same
   canonical local `ENFUSION_STATE_DIR`. Remote/NFS state is unsupported.
9. Destination realpath, project markers, and collisions cannot be verified
   until the exact installation permission is received.

## Backup and permission boundary

Before any future copy, first verify the exact active realpath and project
markers, then have the user make a timestamped backup to a new, separate backup
location such as `<separate-backup-root>/new_rj-YYYYMMDD-HHMMSS`. Never use,
compare, or synchronize `/home/jecacs/Documents/the new RJ` as that backup.
Show the backup location and complete source → destination manifest before
copying anything.

Installation may proceed only after the user literally writes:

```text
Разрешаю установить bridge в active new_rj
```

Even then, this gate-false artifact is read-only validation scaffolding. A later
catalog/live-capable revision needs a new reviewed commit, new hashes/manifest,
renewed installation approval, manual restart, and another clean
`ValidateScripts`. Mutation additionally requires a shown current 8–12-object
plan and a separate `применяй` message.

At this checkpoint the active map and stale copy were not read or modified;
handlers were not installed; Steam, Workbench, and Proton were not launched;
the Proton prefix was not changed; and port 5775 was neither probed nor used.
