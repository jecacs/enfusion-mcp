# Multiple MCP hosts and processes

Codex, Claude, an IDE, and a local/self-hosted orchestration process will usually
launch independent STDIO server processes. There is no shared Python heap, and
client/model/session names are not security identities.

## Coordination invariant

Every cooperating process must use the same canonical absolute
`ENFUSION_STATE_DIR` on a local Linux filesystem. Client configs also have to
name the same Workbench host/port, canonical project root, world, bridge
protocol/build, and catalog. The complete trusted scope is checked for exact
service/config agreement, while the actual file-lock namespace is deliberately
coarser: every configuration aimed at the same Workbench endpoint
`host:port` shares one lock. Thus conflicting project/world metadata cannot
split two cooperating writers that still reach the same Workbench process. The
client never supplies either scope.

If clients use different state directories, they are different coordination
domains. The server cannot manufacture a cross-process guarantee across them.
NFS and other remote filesystems are unsupported for SQLite + `flock`.

V1 treats the parent of the explicitly configured `ENFUSION_STATE_DIR` as an
operator-trusted storage root. Ledger checks then protect the state leaf against
symlink/type/ownership/mode escapes; they do not independently decide which
global directory the operator may authorize. Shipped client examples pin the
state path to this repository's `.state`, and the development workflow performs
no writes elsewhere.

## Layers of coordination

1. An in-process async read/write lock coordinates coroutines.
2. A Workbench-endpoint-scoped Linux `fcntl.flock` coordinates processes and is
   released by the kernel on process death.
3. SQLite transactions and unique constraints bind one immutable plan to at
   most one operation/idempotency key and preserve state across restarts.

Planning holds a shared lock across context → one terrain batch → persistence.
Apply and reconcile hold the exclusive target lock. Multiple reads/plans can
coexist; no mutation can overlap them or another mutation among cooperating
processes.

The lock file is stable and must not be unlinked/recreated while processes are
running because `flock` attaches to an inode.

## Durable operation semantics

Before the first mutation attempt, the operation is transactionally moved to
`SENDING` with process ownership metadata. A transport failure proven to occur
before any request byte is sent becomes `PRE_SEND_FAILED`; an explicit retry
with the same key may make a new attempt, but the server never retries it
automatically. A dead owner's `SENDING` state is recovered as `UNKNOWN`; a live
other process is never assumed dead merely because it has not responded to an
MCP client.

After the send boundary, any untrusted/incomplete outcome becomes `UNKNOWN`.
The same key may call handler reconcile mode only. A second create request is
forbidden. A different key is forbidden while the plan is bound/unknown/applied.
Full matching entities may confirm `APPLIED`; partial or changed entities become
`PARTIAL`/`ENTITY_CONFLICT`; absence after a previously confirmed application
becomes `UNDONE`, never silent reapplication.

## Residual concurrency

Manual Workbench edits and direct NET API callers do not take these locks.
Therefore apply still revalidates world, subscene, layer/hierarchy lock, shape
identity, terrain, and existing deterministic entities immediately before the
single edit action.
