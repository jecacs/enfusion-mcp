# Implementation plan

## Checkpoint A — reference and boundary

1. Record both Git states and the exact upstream commit/license.
2. Audit source-level interoperability and security findings without running or
   modifying the Node project.
3. Pin the official stable Python MCP SDK and create a lockable Python package.
4. Document architecture, threat model, permission boundary, and commands.
5. Build, lint, type-check, and test the minimal scaffold; commit locally.

## Checkpoint B — safe platform and MCP profile

1. Implement strict environment settings and nominal path/resource types.
2. Implement safe Proton `dosdevices` conversion and containment tests.
3. Implement exact NET API framing, phase errors, retry policy, and fake TCP
   tests without connecting to port 5775.
4. Add SQLite/`flock` foundations.
5. Expose exactly five tools through the official SDK, with annotations,
   instructions, structured output, and black-box STDIO tests.
6. Run all quality and dependency checks; commit locally.

## Checkpoint C — deterministic vegetation workflow

1. Add a versioned exact catalog framework and explicit production-incomplete
   status if the allowlist cannot be proven from the read-only reference.
2. Implement PCG32 planning, polygon validation, one batched terrain sample,
   rejection statistics, quantization, canonical JSON, and plan persistence.
3. Implement operation binding/state transitions, cross-process serialization,
   unknown-outcome reconciliation, Undo classification, and fault tests.
4. Stage the three `RJMCP_*.c` handlers and statically validate their real
   registration/`RegV` contracts against Python models.
5. Finish client-neutral and client-adapter documentation.
6. Run format, lint, typing, build, tests, and separate audits; commit and stop.

No checkpoint in this plan reads or writes the active map, starts Workbench or
Steam, changes Proton, probes port 5775, or installs bridge files.
