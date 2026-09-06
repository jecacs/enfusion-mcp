# Review of the original implementation prompt

The prompt is a strong security-oriented design brief, but it is not a fully
self-consistent acceptance specification. The implementation adopts the
following clarifications rather than silently promising properties that cannot
yet be proven.

## Accepted strengths

- Explicit permission gates for bridge installation, live reads, and mutation.
- A narrow STDIO-only Python server with no model/provider dependency.
- Plan before apply; stored immutable transforms; exact allowlists; no arbitrary
  file, script, endpoint, shell, save, launch, delete, or layer-create tool.
- Durable SQLite operation identity plus process-local and Linux interprocess
  locking for cooperating STDIO processes.
- No automatic mutation retry after transmission begins; unknown outcomes are
  reconciled only.
- Byte-exact, bounded framing; nominal path types; real dosdevices mappings;
  deterministic PRNG/planner; canonical plan hashes.
- MCP annotations are client hints and never a security boundary.
- Python/fake/static tests do not claim live Enforce compatibility.

## Clarifications adopted

1. **MCP lifecycle.** Current MCP 2026-07-28 uses `server/discover`; legacy
   clients use `initialize`. Official SDK 2.1.1 supports both. Both are tested.
2. **Plan annotation.** The requested `readOnlyHint=true` is retained to mean
   "no Workbench/world mutation". Planning necessarily inserts immutable
   control-plane state in SQLite, so the standard's broader "does not change
   its environment" interpretation is debatable and is documented explicitly.
3. **Expiry.** A deterministic plan has immutable creation/expiry metadata.
   Repeating identical input never renews an expired/applied/unknown/undone
   plan. A new workflow must change a hashed input such as seed.
4. **Ledger boundary.** Enforce cannot read the Linux SQLite ledger. Python
   proves plan/key/state binding; the handler independently proves physical
   safety constraints and observable entity reconciliation. The server does not
   claim to defend Workbench against another same-user process calling NET API
   directly.
5. **Project identity.** Resource path and GUID cannot cryptographically prove
   which duplicate host checkout Workbench loaded. Bridge protocol/build and
   catalog hashes strengthen evidence, but host identity remains a live residual
   risk.
6. **Shape geometry.** V1 accepts only polygon-compatible closed shapes whose
   returned points form straight segments. Spline-like shapes are rejected
   unless a future version defines deterministic tessellation.
7. **Numbers.** "Exact" means the handler-observed finite engine values after
   one documented fixed-point quantization. Geometry and hashing use that
   representation; strict JSON rejects NaN/Infinity and normalizes negative
   zero.
8. **Terrain normal.** The bridge contract carries all three normal components,
   not only `normalY`, so Python can validate/normalize it before calculating
   `degrees(atan2(hypot(normal_x, normal_z), normal_y))`.
9. **Unknown outcomes.** Any timeout, socket failure, malformed frame/UTF-8/JSON,
   or invalid response after the first mutation byte may have been sent is
   `UNKNOWN_OUTCOME`, unless a trusted handler result explicitly proves that no
   mutation started or rollback completed.
10. **Locks.** Multi-process safety is conditional on all cooperating processes
    using the same canonical absolute local `ENFUSION_STATE_DIR`. Planning holds
    a shared lock across context/sample/persist; apply/reconcile holds an
    exclusive lock. The file-lock namespace is Workbench-endpoint-wide, even
    when two misconfigured processes disagree on project/world metadata. NFS
    and other remote filesystems are unsupported.
11. **Layer paths.** V1 accepts exactly the two root-level paths
    `MCP_Preview`/`MCP_Vegetation`; live validation must establish Workbench's
    sentinel/round-trip behaviour and hierarchy lock check.
12. **Protocol success literal.** `Ok` is an upstream compatibility assumption
    until a permitted live capture confirms it. Golden tests use literal bytes
    and state their provenance.
13. **Catalog/live-sequence dependency.** The prompt asks Checkpoint C to keep
    an unproven production catalog empty, yet its first live sequence later
    assumes that an 8–12-object production plan can already be made. Those
    conditions cannot both hold. The current bridge is deliberately
    read-capable/create-disabled. After the first permissioned read-only
    validation, exact resources must be established and a second reviewed
    bridge/catalog revision (new build ID and catalog hash) must be installed
    and validated before a production plan or apply can exist.

## Claims deliberately deferred

Static staged handler review cannot prove Enforce compilation, actual prefab
loading/resource-name representation, transform round-tripping, rollback
history semantics, or that a batch is removed by exactly one Ctrl+Z. The staged
create path therefore remains independently hard-disabled after Checkpoint C.
These properties require later permissioned validation before that gate can be
changed and before any live mutation is acceptable.

Likewise, Checkpoint C cannot inspect destination collisions while the active
project is forbidden. Its report must say collision status is unknown, show the
calculated destination and proposed files, and perform no read of that path.

The exact installation permission is not treated as permission to silently
enable a later handler revision. A changed manifest/hash is shown again and
requires a renewed explicit installation decision. Enabling create still does
not authorize a mutation: the exact new plan must be shown and the user must
then separately write `применяй`.

## Evidence labels

- `STATIC_VERIFIED`: source/API/contract inspection.
- `PYTHON_SIMULATED`: unit, subprocess, fake TCP, or fault-injection evidence.
- `ENFORCE_COMPILED`: permitted live `ValidateScripts` passed.
- `LIVE_READ_VERIFIED`: permitted live context/terrain evidence.
- `LIVE_MUTATION_VERIFIED`: permitted create/reconcile/Undo smoke evidence.
- `UNVERIFIED_LIVE`: requires a later permission gate.
