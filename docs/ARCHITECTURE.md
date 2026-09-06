# Architecture

## Trust boundaries

The MCP client is untrusted input. MCP annotations are hints to a host, never an
authorization mechanism. Every input is validated before any network
connection. The Python server is the policy enforcement point. Workbench's NET
API is a loopback transport without meaningful authentication; its client ID is
diagnostic metadata, not a credential.

```text
MCP host(s) -> independent Python STDIO process(es)
                       |       |
                       |       +-> shared SQLite operation ledger
                       |           + Linux Workbench-endpoint flock
                       |
                       +-> one TCP connection per request
                           127.0.0.1:5775
                                |
                       manually started Workbench/Proton
                                |
                       narrow staged RJMCP handlers
```

## Components

- `config`: fail-closed environment parsing and safe-profile validation.
- `paths`: nominal host, Wine, engine, and resource types plus real
  `dosdevices` mapping and project containment.
- `net_api`: byte-exact request/response framing and phase-aware timeouts.
- `catalog`: an exact prefab allowlist with a canonical content hash.
- `planner`: pure PCG32-based deterministic geometry and canonical plan hash.
- `ledger`: immutable plans and a transactional operation state machine.
- `locking`: in-process async lock plus crash-released `fcntl.flock`.
- `service`: plan/apply/reconcile policy independent of MCP and model vendors.
- `server`: exactly five safe-profile MCP tools and structured results.
- `bridge/`: staged Enforce handlers. These are never auto-installed.

## Mutation state machine

```text
PLANNED -> SENDING -> APPLIED -> UNDONE
              |          ^
              +-> UNKNOWN +-- reconcile observes a complete matching batch
              |      +-> PARTIAL / ENTITY_CONFLICT
              +-> PRE_SEND_FAILED (no request byte sent; same-key retry only)
              +-> ROLLBACK_VERIFIED / ROLLBACK_FAILED
```

After request transmission begins, a mutation transport failure becomes
`UNKNOWN_OUTCOME`. The same idempotency key may reconcile but may not resubmit
the create batch. An unresolved operation blocks new create requests for every
plan on the same Workbench endpoint, including after process restart. Absence
during reconciliation leaves an unknown operation UNKNOWN; only absence after
confirmed application can establish UNDONE. Entity names are a deterministic
function of plan hash and placement
index, which gives reconciliation a stable observation target.

The durable send claim rechecks plan expiry and the endpoint's unresolved
operations in one SQLite transaction. Diagnostic messages are bounded before
storage; oversized error text cannot prevent a critical state transition.

The complete project/world/build/catalog scope must equal the validated server
configuration. Its interprocess lock key is nevertheless derived only from the
literal Workbench endpoint, ensuring that two configurations aimed at the same
process cannot accidentally acquire different mutation locks.

## Protocol compatibility

The official `mcp` Python SDK is pinned. SDK v2 supports the current
2026-07-28 discovery flow and negotiates older initialize-based clients; tests
cover the legacy initialize handshake because many deployed hosts still use it.
The server itself does not emit vendor-specific messages.
