# Workbench NET API protocol

The protocol implementation is independent of MCP. Its primary public source is
Bohemia Interactive's [Workbench NET API reference](https://community.bistudio.com/wikidata/external-data/arma-reforger/EnfusionScriptAPIPublic/Page_NetApi.html),
checked on 2026-09-05.

## Request frame

```text
signed int32 little-endian: protocol version 1
signed int32 byte length + UTF-8 bytes: client ID
signed int32 byte length + UTF-8 bytes: "JsonRPC"
signed int32 byte length + UTF-8 bytes: JSON payload
```

The JSON payload is a canonical object with trusted `APIFunc` inserted by the
client library. Supplying `APIFunc` inside params is rejected before a socket is
opened. Client ID is descriptive and is never treated as authentication.

JSON keys are sorted, separators are compact, Unicode is encoded directly, and
NaN/Infinity are rejected. Pascal lengths count UTF-8 bytes, not code points.

## Response frame

Every response is exactly two Pascal UTF-8 strings:

```text
status/error code
JSON payload
```

Both strings are read regardless of status. A non-success status preserves the
raw payload in `WorkbenchApiError`. Extra bytes after the second string are a
protocol error. Invalid UTF-8, negative or oversized lengths, truncated prefixes
or data, and a missing second string are protocol errors.

The source describes `Error Code` but does not normatively identify a success
literal. `Ok` is retained as an upstream compatibility assumption until a later
permissioned live capture confirms it.

The documented `BringModuleWindowToFront` response has no body and is the only
V1 endpoint whose empty second string maps to `None`. Other empty responses fail
closed. This server does not expose that built-in as an MCP tool.

## Connections and timeout phases

The official protocol requires a fresh TCP connection for each transaction.
`NetApiClient` therefore never pools or reuses sockets.

Failures identify one of:

- `BEFORE_CONNECT`: local validation/encoding;
- `CONNECT`: TCP connection attempt;
- `PRE_SEND`: connected, no request write attempted;
- `SENDING`: the first request write has begun;
- `RESPONSE`: waiting/reading exact response bytes;
- `DECODING`: UTF-8, JSON, or response-schema interpretation.

Read-only retries are disabled by default and are explicitly bounded to three
attempts for selected connection/time errors. Mutation never retries. Any
mutation failure after the first write boundary—including malformed response,
invalid UTF-8/JSON, truncation, trailing data, socket loss, or timeout—becomes
`UNKNOWN_OUTCOME`. The caller must persist/reconcile with the same idempotency
key and must not send create again.

## Limits

- maximum Pascal string: 16 MiB before allocation/read;
- TCP port: 1–65535;
- client/endpoint identifiers: bounded, printable, no NUL/control characters;
- request and response consist of one transaction per connection.

Tests use literal golden byte frames and fake servers bound to
`127.0.0.1:0`. They never address port 5775.

