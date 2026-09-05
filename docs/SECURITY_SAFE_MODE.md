# Safe mode security contract

V1 supports only `ENFUSION_SAFE_MODE=1`. Any missing, unknown, contradictory,
or unsafe configuration prevents the STDIO server from starting.

## Server guarantees

- Workbench target is literal `127.0.0.1:5775`.
- Platform and Tools app ID are exactly `native-linux-proton` and `1874910`.
- Allowed world is exactly `$thenewRJ:rj.ent`.
- Project host and engine paths must round-trip through real Proton
  `dosdevices` mappings and the host path must remain in its configured root.
- Shared state uses one required canonical absolute local directory outside the
  project and Proton prefix.
- The server never starts Steam/Proton/Workbench, creates a proxy/listener,
  installs/uninstalls handlers, executes a shell/script, saves a world, creates
  a layer, writes project files, or proxies arbitrary endpoints.
- Exactly five MCP tools are registered.
- Every tool input is validated before any NET API connection.
- Prefabs come only from an exact server-owned allowlist.
- Apply loads immutable transforms from SQLite rather than accepting them from
  a client.
- Mutations are serialized across cooperating processes and never
  automatically retried after transmission starts.
- MCP wire output uses stdout exclusively; diagnostics use stderr.

The current production vegetation catalog is intentionally empty because no
real vegetation resource can be proven from the permitted read-only reference.
This makes plan/apply fail closed rather than turning fixture strings into a
production allowlist.

## What annotations do and do not mean

MCP annotations help compatible clients choose approval UX. They do not grant
or enforce authority. The server validates the same policy even if a client
ignores, removes, or misinterprets annotations.

`vegetation_plan` is annotated read-only in the narrow Workbench/world sense.
It does write an immutable plan record to the server's control-plane SQLite
ledger, which is explicitly disclosed because the wider MCP meaning of
"environment" can include such durable state.

`vegetation_apply` is additive, not a deletion tool, so it has
`destructiveHint=false`; it still mutates unsaved editor state and creates an
Undo history point. It remains `idempotentHint=false` until live lost-response,
reconcile, partial, conflict, and Ctrl+Z behaviour is proven end to end.

## Client-side approval

Client allowlists and approval prompts are defense in depth and user experience,
not server guarantees. A recommended client asks before `vegetation_apply` and
allows the four read-oriented tools according to local policy.

## Out of scope

The server does not protect Workbench from another same-user process that calls
NET API directly, from manual user edits, from an owner modifying SQLite/source,
or from other pre-existing handlers. NET API client ID is not a credential.
