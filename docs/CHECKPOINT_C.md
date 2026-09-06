# Checkpoint C evidence and stop report

Historical report for the original pre-install artifact at `1a633cb`.
An independent review subsequently found logic defects and incomplete scale
support. Its original test results and manifest below are retained as historical
evidence, not current readiness or installation approval. See
[REVIEW_FIXES.md](REVIEW_FIXES.md) for the subsequent corrective revision and
[ENFORCE_BRIDGE.md](ENFORCE_BRIDGE.md) for current verification limits. Paths
and package identifiers are generalized for public documentation.

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
- three staged, attributed `EnfusionMCP_*.c` handlers;
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

UV_CACHE_DIR=/tmp/enfusion-mcp-uv-cache uv lock --check
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
UV_CACHE_DIR=/tmp/enfusion-mcp-uv-cache uv build --no-build-isolation --out-dir /tmp/enfusion-mcp-checkpoint-c-build.o6vvcg
exit 0
Successfully built enfusion_mcp-0.1.0a0.tar.gz
Successfully built enfusion_mcp-0.1.0a0-py3-none-any.whl

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
.venv/bin/pip-audit --progress-spinner off --requirement /tmp/enfusion-mcp-prod-final.txt
exit 0; No known vulnerabilities found

full dev/test export: 89 distributions
.venv/bin/pip-audit --progress-spinner off --requirement /tmp/enfusion-mcp-full-final.txt
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

## Historical staged bridge manifest

The following sizes/hashes belong only to the checkpoint source revision. They
must not be used to verify or install today's source. The original local
destination is represented by this placeholder:

```text
/path/to/addon/Scripts/WorkbenchGame/EnfusionMCP
```

Only these files are proposed:

| Staged source | Bytes | SHA-256 | Proposed destination filename |
|---|---:|---|---|
| `bridge/Scripts/WorkbenchGame/EnfusionMCP/EnfusionMCP_GetContext.c` | 5506 | `8d15c9f1765aa417a98ffd3b0f9e03ff9ba1a4f9f444fe4bc590ee3f3298a986` | `EnfusionMCP_GetContext.c` |
| `bridge/Scripts/WorkbenchGame/EnfusionMCP/EnfusionMCP_TerrainSample.c` | 6463 | `2af16362d9d71ae50e1ba8894e49c06a663db43ae2c6de7717811289b481f261` | `EnfusionMCP_TerrainSample.c` |
| `bridge/Scripts/WorkbenchGame/EnfusionMCP/EnfusionMCP_VegetationApply.c` | 22032 | `2e6f98df929089c4bd060268f69acfb8688ce756eefe956f8dfb271390654fa2` | `EnfusionMCP_VegetationApply.c` |

Collision status is **unknown by design**: the active project was not read.
Possible conflicting existing files are those exact three destination names;
no claim is made about whether they exist. No unknown file may be deleted or
overwritten. `resourceDatabase.rdb`, world `.ent` files, layers, terrain, and all
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
9. Destination realpath, project markers, and collisions were not verified
   as part of this checkpoint.

## Historical deployment boundary

The checkpoint did not install handlers, inspect or modify an active addon,
launch Steam/Workbench/Proton, change a Proton prefix, or probe/use port 5775.
Installation and application were deferred to a separate reviewed deployment.
The reusable current procedure is in
[BRIDGE_INSTALLATION_PLAN.md](BRIDGE_INSTALLATION_PLAN.md).

The historical gate-false artifact was intended for read-only validation. A
future live-capable revision still needs a reviewed catalog and implementation,
fresh source hashes, manual restart, and clean `ValidateScripts`. Application
also requires review and explicit approval of the exact current plan.
