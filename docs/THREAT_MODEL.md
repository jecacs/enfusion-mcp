# Threat model

## Assets

- the unsaved world and its single-step Undo history;
- the active project directory and staged bridge source;
- operation identity across independent MCP processes;
- deterministic placement plans and their catalog/world binding;
- the integrity of stdout as an MCP transport.

## Adversaries and failures

The design assumes a buggy, compromised, or merely over-eager MCP host can call
tools with arbitrary arguments and ignore annotations. It also assumes two or
more hosts can race, a process can crash while applying, TCP can fragment or
disconnect at every byte, the response can be lost after Workbench mutates, and
a user can manually edit or Undo generated entities.

The design does not treat local users with arbitrary filesystem/process access
as attackers it can contain. Workbench's unauthenticated loopback API is also
outside the authentication boundary; another local process could speak to it.

## Controls

- Safe mode is mandatory and pins host, world, project, and allowed layers.
- Inputs use separate nominal path/resource types and explicit conversions.
- Resource names and prefabs are exact allowlists, never substrings or globs.
- Frame lengths are bounded before allocation and every response has exactly
  two UTF-8 Pascal strings with no trailing data.
- The plan is immutable and SHA-256-bound to world/shape/catalog/transforms.
- Apply accepts only a stored plan ID plus a UUID idempotency key.
- SQLite uniqueness, transactions, async locking, and `flock` jointly prevent
  duplicate concurrent application across processes.
- Mutation is never automatically retried after sending begins.
- The Enforce handler revalidates the trusted envelope immediately before one
  `BeginEntityAction`/`EndEntityAction`; it never saves the world.
- Logs use stderr only and redact no secrets because the protocol has none;
  nevertheless raw arbitrary payloads are not logged.

## Residual risks

- Staged Enforce code cannot be claimed compatible until Workbench's real
  `ValidateScripts` succeeds after explicit installation permission.
- `flock` is a Linux local-filesystem primitive; shared network filesystems are
  out of scope.
- Process death after Workbench mutation but before observation necessarily
  leaves an unknown outcome. Reconciliation can classify it but must not guess.
- A user can bypass this server and mutate Workbench directly.
- MCP host approval UX varies. Server validation remains authoritative.

