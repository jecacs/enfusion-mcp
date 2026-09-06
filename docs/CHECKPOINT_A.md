# Checkpoint A evidence

Historical scaffold report, completed on 2026-09-05. These results describe
that checkpoint, not the current revision. Local paths and package identifiers
are generalized for public documentation. No active addon or Proton prefix was
read, and Steam/Workbench and NET API were not used.

## Initial Git evidence

The repository baseline was `9e46b6e51819764acb4e6d08c2838af3895e49ff` on
`main`; implementation used `feature/python-linux-proton-safe-vegetation`.
The read-only upstream reference was commit
`0acfa884228477043c6b4c2b8c7f0c270d648398` from
[enfusion-mcp-BK](https://github.com/steffenbk/enfusion-mcp-BK).
Its identity was checked through `package.json:45-50` and `README.md:183-185`;
its complete MIT license was at `LICENSE:1-20`. The repository's existing MIT
license was preserved.

## Toolchain and dependency decision

```text
uv 0.12.10
resolver Python: CPython 3.11.16
host python3: Python 3.14.7
official MCP SDK: mcp==2.1.1
direct Pydantic: pydantic==2.13.5
resolved packages: 88 (including project/build markers)
installed packages in local .venv: 84 at initial sync
```

The first full-tree audit found one development-only advisory:

```text
pytest 8.4.2  PYSEC-2026-1845  fix: 9.0.3
```

No advisory was suppressed. The direct constraint changed from pytest `<9` to
`>=9.0.3,<10`; targeted resolution selected `pytest==9.1.1`, and all checks and
audits were rerun.

## Final exact command results

```text
uv lock --upgrade-package pytest
  Resolved 88 packages
  Updated pytest v8.4.2 -> v9.1.1

uv sync --frozen
  exit 0

uv run ruff format --check .
  exit 0; 11 files already formatted

uv run ruff check .
  exit 0; All checks passed!

uv run mypy
  exit 0; Success: no issues found in 3 source files

uv run pytest -q
  exit 0; 2 passed in 0.01s

uv build
  exit 0
  built dist/enfusion_mcp-0.1.0a0.tar.gz
  built dist/enfusion_mcp-0.1.0a0-py3-none-any.whl

uv run twine check dist/*
  exit 0; wheel PASSED; sdist PASSED

production export: uv export --frozen --no-dev --no-emit-project ...
  exit 0; 30 locked third-party requirement entries
pip-audit --requirement /tmp/enfusion-mcp-prod.txt
  exit 0; No known vulnerabilities found

full export: uv export --frozen --all-groups --no-emit-project ...
  exit 0; 87 locked third-party requirement entries
pip-audit --requirement /tmp/enfusion-mcp-full.txt
  exit 0; No known vulnerabilities found

git diff --check
  exit 0; no whitespace errors
```

The absolute audit export files live in `/tmp` and are not project artifacts.
The reproducible source of dependency versions and hashes is `uv.lock`.

## Scope/evidence status

- Source/reference findings: `STATIC_VERIFIED`.
- Python scaffold build/type/test: `PYTHON_SIMULATED`.
- Enforce compilation and all live Workbench claims: `UNVERIFIED_LIVE`.
