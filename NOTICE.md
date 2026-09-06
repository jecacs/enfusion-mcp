# Notices and upstream attribution

`enfusion-mcp` is an independent Python implementation. It is distributed
under the MIT License in [`LICENSE`](LICENSE).

The following MIT-licensed project is used as a read-only source of protocol
and Enfusion Workbench integration knowledge:

- upstream project: `steffenbk/enfusion-mcp-BK`;
- upstream URL: <https://github.com/steffenbk/enfusion-mcp-BK>;
- sibling reference commit examined for this implementation:
  `0acfa884228477043c6b4c2b8c7f0c270d648398`;
- upstream license at that commit: MIT, copyright (c) 2025
  `enfusion-mcp contributors`.

No TypeScript code was copied into the Python runtime. The NET API framing
description, handler registration patterns, and known field-name
interoperability problems are upstream-derived ideas and are independently
tested here.

The following staged files adapt the upstream's conventional
`NetApiHandler`/`JsonApiStruct`/`RegV`/`OnPack` and
`Workbench.GetModule(WorldEditor)` integration patterns, while replacing the
schema and behaviour with independently written narrow handlers:

- `bridge/Scripts/WorkbenchGame/EnfusionMCP/EnfusionMCP_GetContext.c`, informed by
  `EMCP_WB_GetState.c` and `EMCP_WB_ListEntities.c`;
- `bridge/Scripts/WorkbenchGame/EnfusionMCP/EnfusionMCP_TerrainSample.c`, informed by
  `EMCP_WB_Terrain.c`;
- `bridge/Scripts/WorkbenchGame/EnfusionMCP/EnfusionMCP_VegetationApply.c`, informed by
  `EMCP_WB_CreateEntity.c`, `EMCP_WB_GetEntity.c`, and
  `EMCP_WB_ListEntities.c`.

The upstream files are MIT licensed under the copyright and permission notice
in the upstream `LICENSE`; the complete same MIT grant/disclaimer is also the
license text used by this repository's `LICENSE`. This attribution must remain
with redistributed staged handler source.
