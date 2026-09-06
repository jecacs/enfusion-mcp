# enfusion-mcp-rj

`enfusion-mcp-rj` is a safe, vendor-neutral MCP server for Arma Reforger
Enfusion Workbench. The server runtime is native Python on Linux; Workbench is
started manually through Steam/Proton and is contacted only through its loopback
NET API. Version 1 exposes STDIO only.

The project is currently pre-release. The implementation provides the strict
platform and NET API boundary, exact five-tool STDIO surface, deterministic
planner, durable multi-process coordination, and three staged Enforce handlers.
The production catalog is intentionally empty and the staged create handler has
an independent compile-time safety gate, so live planning/application remains
fail closed until its resources and Enforce behaviour are explicitly validated.

## Non-negotiable safety boundary

- The server never launches Steam, Proton, Workbench, a shell, or a model API.
- Safe mode accepts only literal `127.0.0.1` for Workbench.
- MCP JSON-RPC goes to stdout; logs and diagnostics go to stderr.
- Read-only calls may run concurrently. Mutations are serialized across STDIO
  processes by a durable SQLite ledger and Linux `flock`.
- The only non-Python runtime code is a narrow set of staged `RJMCP_*.c`
  Workbench handlers. They are not installed by this repository workflow.
- The active `new_rj` project, the stale map copy, Workbench, Proton, and port
  5775 are out of bounds until the user gives the exact staged permissions.

## Development

Prerequisites: Python 3.11+ and `uv`. Nothing is installed globally.

```console
UV_CACHE_DIR=/tmp/enfusion-mcp-rj-uv-cache uv sync --frozen
UV_CACHE_DIR=/tmp/enfusion-mcp-rj-uv-cache uv run ruff format --check .
UV_CACHE_DIR=/tmp/enfusion-mcp-rj-uv-cache uv run ruff check .
UV_CACHE_DIR=/tmp/enfusion-mcp-rj-uv-cache uv run mypy
UV_CACHE_DIR=/tmp/enfusion-mcp-rj-uv-cache uv run pytest
UV_CACHE_DIR=/tmp/enfusion-mcp-rj-uv-cache uv build --no-build-isolation
UV_CACHE_DIR=/tmp/enfusion-mcp-rj-uv-cache uv run twine check dist/*
```

Production and full-tree audits are intentionally separate:

```console
UV_CACHE_DIR=/tmp/enfusion-mcp-rj-uv-cache uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file /tmp/enfusion-mcp-rj-prod.txt
UV_CACHE_DIR=/tmp/enfusion-mcp-rj-uv-cache uv run pip-audit --requirement /tmp/enfusion-mcp-rj-prod.txt
UV_CACHE_DIR=/tmp/enfusion-mcp-rj-uv-cache uv export --frozen --all-groups --no-emit-project --format requirements-txt --output-file /tmp/enfusion-mcp-rj-full.txt
UV_CACHE_DIR=/tmp/enfusion-mcp-rj-uv-cache uv run pip-audit --requirement /tmp/enfusion-mcp-rj-full.txt
```

This repository has no `package.json` and is not an npm package. It has no Node,
npm, JavaScript, or TypeScript runtime.

The wheel is intentionally Python-runtime-only (plus `LICENSE`/`NOTICE.md`).
Staged `bridge/` source and full documentation are carried by the source tree
and sdist, not extracted or installed by the CLI. Bridge installation therefore
always uses an audited source checkout and an explicit manifest; installing the
wheel can never copy a handler into an active project.

See [Architecture](docs/ARCHITECTURE.md), [Threat model](docs/THREAT_MODEL.md),
[prompt review](docs/PROMPT_REVIEW.md), [reference audit](docs/REFERENCE_AUDIT.md),
[MCP SDK baseline](docs/MCP_SDK_BASELINE.md), and
[implementation plan](docs/IMPLEMENTATION_PLAN.md). The original pre-install
handoff is recorded in [Checkpoint C evidence](docs/CHECKPOINT_C.md); the
independent review and corrective revision are recorded in
[review fixes](docs/REVIEW_FIXES.md). Runtime details are in
[safe mode](docs/SECURITY_SAFE_MODE.md), [NET API](docs/NET_API_PROTOCOL.md),
[Linux/Proton](docs/LINUX_PROTON.md), [deterministic vegetation](docs/VEGETATION_MCP.md),
[staged Enforce bridge](docs/ENFORCE_BRIDGE.md),
[client configuration](docs/CLIENT_CONFIG_GENERIC.md), and
[multi-process coordination](docs/MULTI_AGENT.md).
