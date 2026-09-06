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
Apply and reconcile hold the exclusive target lock. Multiple plans can coexist;
their context/sample/persist sequences exclude cooperating apply requests.
Standalone status/context reads do not acquire this lock and may observe an
intermediate editor state. They never mutate the world.

The async lock wait limit covers both the in-process queue and `flock`
acquisition. It does not limit the duration of work after acquisition.

The lock file is stable and must not be unlinked/recreated while processes are
running because `flock` attaches to an inode.

## Durable operation semantics

When upgrading from schema 1, first stop **all old Python MCP processes** and
back up the shared state directory while no process holds it open. Restart every
client with this same code revision. The new process migrates the database
transactionally; already-running old code cannot enforce the new endpoint
guard. Do not mix old and new server revisions against the same ledger.

Before the first mutation attempt, the operation is transactionally moved to
`SENDING` with process ownership metadata. A transport failure proven to occur
before any request byte is sent becomes `PRE_SEND_FAILED`; an explicit retry
with the same key may make a new attempt, but the server never retries it
automatically. A dead owner's `SENDING` state is recovered as `UNKNOWN`; a live
other process is never assumed dead merely because it has not responded to an
MCP client.

The send claim checks TTL again atomically, including retries after a proven
pre-send failure. Expiry during an awaited context preflight prevents sending.

After the send boundary, any untrusted/incomplete outcome becomes `UNKNOWN`.
The same key may call handler reconcile mode only. A second create request is
forbidden. A different key is forbidden while the plan is bound/unknown/applied.
Full matching entities may confirm `APPLIED`; partial or changed entities become
`PARTIAL`/`ENTITY_CONFLICT`; absence after a previously confirmed application
becomes `UNDONE`, never silent reapplication.

Kernel lock release after a timeout or crash does not prove that Workbench has
finished the request. Durable endpoint binding therefore blocks **all new
create requests**, including different plan IDs, while that endpoint has an
unresolved operation. SQLite schema migration preserves older records; legacy
unresolved records with no endpoint binding conservatively block every target
until their history can be reconciled. Do not delete the ledger or switch state
directories to bypass a block.

No entities observed for an UNKNOWN operation leaves it UNKNOWN and keeps the
endpoint blocked. The five-tool interface offers no force-reset operation.
Resolving an outcome that cannot be proven by read-only reconciliation requires
a separately reviewed operator workflow; absence is never used as permission
to resend create.

Unresolved here includes `SENDING`, `UNKNOWN`, `PARTIAL`, `ROLLBACK_FAILED`,
and `ENTITY_CONFLICT`. Absence alone does not turn a partial/failed rollback
into a verified rollback. Reconciliation also refuses to classify a batch
while the editor is doing an edit action or restoring Undo/Redo.

Apply error outputs retain the requested plan/operation IDs. For that exact
binding, `state` reflects the ledger. For a new conflicting invocation,
`state=PRE_SEND_FAILED` describes that invocation only; `error.details` names
the existing blocking plan/operation/state, and `unknown_outcome` preserves its
uncertainty. `details.state_subject` makes this distinction explicit.

## Residual concurrency

Manual Workbench edits and direct NET API callers do not take these locks.
Therefore apply still revalidates world, subscene, layer/hierarchy lock, shape
identity, terrain, and existing deterministic entities immediately before the
single edit action.
