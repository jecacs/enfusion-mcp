# enfusion-mcp-rj

`enfusion-mcp-rj` is a safe, vendor-neutral MCP server for Arma Reforger
Enfusion Workbench. The server runtime is native Python on Linux; Workbench is
started manually through Steam/Proton and is contacted only through its loopback
NET API. Version 1 exposes STDIO only.

The project is currently pre-release. Checkpoint A establishes the audited
architecture and packaging boundary; the operational server arrives in the
following checkpoints.

## Non-negotiable safety boundary

- The server never launches Steam, Proton, Workbench, a shell, or a model API.
- Safe mode accepts only literal `127.0.0.1` for Workbench.
- MCP JSON-RPC goes to stdout; logs and diagnostics go to stderr.
- Read-only calls may run concurrently. Mutations are serialized across STDIO
  processes by a durable SQLite ledger and Linux `flock`.
- The only planned non-Python code is a narrow set of staged `RJMCP_*.c`
  Workbench handlers. They are not installed by this repository workflow.
- The active `new_rj` project, the stale map copy, Workbench, Proton, and port
  5775 are out of bounds until the user gives the exact staged permissions.

## Development

Prerequisites: Python 3.11+ and `uv`. Nothing is installed globally.

```console
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
uv build
uv run twine check dist/*
```

Production and full-tree audits are intentionally separate:

```console
uv export --frozen --no-dev --format requirements-txt --output-file /tmp/enfusion-mcp-rj-prod.txt
uv run pip-audit --requirement /tmp/enfusion-mcp-rj-prod.txt
uv run pip-audit
```

This repository has no `package.json` and is not an npm package. It has no Node,
npm, JavaScript, or TypeScript runtime.

See [Architecture](docs/ARCHITECTURE.md), [Threat model](docs/THREAT_MODEL.md),
[prompt review](docs/PROMPT_REVIEW.md), [reference audit](docs/REFERENCE_AUDIT.md),
[MCP SDK baseline](docs/MCP_SDK_BASELINE.md), and
[implementation plan](docs/IMPLEMENTATION_PLAN.md).
