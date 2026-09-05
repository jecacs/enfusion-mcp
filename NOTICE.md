# Notices and upstream attribution

`enfusion-mcp-rj` is an independent Python implementation. It is distributed
under the MIT License in [`LICENSE`](LICENSE).

The following MIT-licensed project is used as a read-only source of protocol
and Enfusion Workbench integration knowledge:

- upstream project: `steffenbk/enfusion-mcp-BK`;
- upstream URL: <https://github.com/steffenbk/enfusion-mcp-BK>;
- sibling reference commit examined for this implementation:
  `0acfa884228477043c6b4c2b8c7f0c270d648398`;
- upstream license at that commit: MIT, copyright (c) 2025
  `enfusion-mcp contributors`.

At Checkpoint A no TypeScript or Enforce source has been copied into the new
runtime. The NET API framing description, handler registration patterns, and
known field-name interoperability problems are treated as upstream-derived
ideas and are independently tested here. If a staged Enforce handler later
contains adapted upstream code, this notice will name that file explicitly.

