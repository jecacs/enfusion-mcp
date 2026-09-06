# Checkpoint B evidence

Historical report for the safe platform/protocol/MCP checkpoint. Its test
counts and command results describe that revision; paths and package identifiers
are generalized for public documentation. No test or command read an active
addon or real Proton prefix, started Steam/Workbench, probed port 5775, or
called a live NET API.

## Implemented

- strict safe-only environment configuration and literal loopback target;
- nominal `HostPath`, `WinePath`, `EnginePath`, and `ResourceName` types;
- synthetic real-symlink `dosdevices` resolution, longest mapping, reverse
  mapping, and project/symlink containment;
- signed-int32/Pascal UTF-8 NET framing and an exact-read async client;
- explicit read-only retry policy, mutation-only entry point, bounded teardown,
  and post-send `UNKNOWN_OUTCOME` classification;
- durable SQLite schema, immutable plans, UUID operation binding, audited state
  transitions, and conservative dead-sender recovery;
- async read/write coordination plus target-scoped Linux shared/exclusive
  `fcntl.flock` and process-crash release;
- exactly five MCP tools, exact annotations, server instructions, validated
  structured success/error output, strict raw argument middleware, and
  `additionalProperties=false` schemas;
- modern MCP 2026-07-28 discovery and legacy initialize STDIO subprocess tests;
- an explicit empty/unverified production catalog, so premature planning and
  apply fail closed.

Review found and tests now cover several non-obvious hazards: coercive SDK
argument parsing, ignored extra args, UUID spelling normalization, mutable
catalog entries, unsafe direct `ServerConfig` construction, mutation endpoint
misclassification, unexpected post-send exceptions, hanging writer teardown,
nested lost error payload, inaccurate reachability, and trailing stdout.

## Test breakdown

```text
config/path nominal and mapping tests:             90
NET API framing/client/fault tests:                 41
ledger and locking/restart/multiprocess tests:      40
MCP/catalog/service/CLI/STDIO tests:                27
                                                     --
total:                                             198
```

The full run required the product's network-sandbox exception because fake TCP
tests bind `127.0.0.1:0` and AnyIO uses an internal local socketpair. Test code
asserts OS-assigned ports or performs no TCP call; it never uses 5775.

## Exact final command results

```text
uv run ruff format --check .
  exit 0; 34 files already formatted

uv run ruff check .
  exit 0; All checks passed!

uv run mypy
  exit 0; Success: no issues found in 21 source files

.venv/bin/pytest -q
  exit 0; 198 passed in 4.03s

uv build
  exit 0
  built dist/enfusion_mcp-0.1.0a0.tar.gz
  built dist/enfusion_mcp-0.1.0a0-py3-none-any.whl

uv run twine check dist/*
  exit 0; wheel PASSED; sdist PASSED

pip-audit --requirement /tmp/enfusion-mcp-prod.txt
  exit 0; No known vulnerabilities found

pip-audit --requirement /tmp/enfusion-mcp-full.txt
  exit 0; No known vulnerabilities found

git diff --check
  exit 0; no whitespace errors
```

## Evidence limits

All runtime properties above are `PYTHON_SIMULATED`; the upstream/source
observations are `STATIC_VERIFIED`. Staged Enforce handlers, planner/apply
coordinator, and Python↔Enforce contract arrive in Checkpoint C. Enforce
compilation, editor transform persistence, reconciliation, and one-step Undo
remain `UNVERIFIED_LIVE` until the later permissioned stages.

Configuration accepts an operator-chosen absolute state path and blocks the
active project/Proton prefix. The ledger treats its parent as operator-trusted
and secures the leaf; deployment examples pin it to this repo's `.state`.

Because safe config fixes production port 5775, no black-box test weakens config
to point the CLI at a random fake port. Instead the composed boundary is proven
in layers: MCP raw-invalid-argument tests show the service is never called, and
NET-client invalid-request tests show zero fake TCP connections. This proves the
same no-connection property without binding the forbidden production port.
