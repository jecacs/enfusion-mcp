# MCP SDK baseline

Checked against official release metadata on 2026-09-05:

- official package: `mcp`;
- stable version: `2.1.1`, released 2026-08-25;
- Git tag/commit: `v2.1.1` /
  `0921d94a74db900dccd2d534842aa7b6160542d2`;
- wheel SHA-256:
  `1c6c31c5d6471c58db76af3af8af67f46d11d01f0a59077d0a308cbdb3d3e915`;
- sdist SHA-256:
  `50b7ba1ebbe117008ea7bdd288234043e69c20b403d6851d19661e6d431a75ef`.

Primary sources: [release](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.1.1),
[PyPI metadata](https://pypi.org/project/mcp/2.1.1/), and
[SDK documentation](https://py.sdk.modelcontextprotocol.io/).

Plain `mcp`, not the `mcp[cli]` extra, is used. The extra would add development
CLI dependencies and its Inspector workflow uses `npx`; neither is required by
this server. Direct Pydantic use is declared and pinned separately. The full
transitive graph is fixed in `uv.lock`.

The current MCP revision, 2026-07-28, uses `server/discover` and per-request
metadata rather than requiring initialize/session state. SDK v2 also serves
legacy initialize-based clients. Therefore compatibility tests cover both
modern discovery and legacy initialize instead of treating one legacy handshake
as the whole protocol.

Even plain `mcp` currently carries HTTP/OAuth packages such as Starlette,
Uvicorn, HTTPX, SSE, JWT, and crypto as mandatory upstream dependencies. The
application does not import or expose those transports; version 1 explicitly
invokes STDIO only. This larger dependency tree is a known cost of using the
official stable SDK, not an application HTTP server.

