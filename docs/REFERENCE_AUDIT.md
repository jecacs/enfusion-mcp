# Read-only upstream reference audit

Audit date: 2026-09-05. Reference checkout:
`../govno-enfusion-mcp`, branch `feature/linux-proton-safe-vegetation`, commit
`0acfa884228477043c6b4c2b8c7f0c270d648398`. Its working tree was clean before
and after inspection. No npm install, build, test, formatter, or audit command
was run there.

The checkout's configured `origin` is `git@github.com:jecacs/enfusion-mcp.git`,
not the claimed upstream. Provenance is instead supported by
`package.json:45-50` and `README.md:183-185`, which identify
<https://github.com/steffenbk/enfusion-mcp-BK>, plus the commit authorship. Its
complete MIT license is at `LICENSE:1-20` and names copyright (c) 2025
`enfusion-mcp contributors`.

## Architecture and dependency baseline

- `package.json:2-8,35-36` defines a Node >=20 TypeScript npm CLI.
- `src/index.ts:3-23` uses the JavaScript MCP SDK and STDIO transport.
- `src/server.ts:2-126` imports and registers the broad tool set without a safe
  profile: 54 `server.registerTool(...)` calls were counted in source.
- `mod/Scripts/WorkbenchGame/EnfusionMCP/` contains 21 broad `EMCP_WB_*.c`
  handlers.
- `src/tuning-server/index.ts:7-60` and `server.ts:55-79` add a separate HTTP
  tuning service; this architecture is intentionally not carried over.
- No model-provider SDK import was found. User-facing configuration is still
  Claude-oriented (`README.md:3-51`).
- STDIO runtime logs use stderr (`src/utils/logger.ts:1-15`).
- `package.json:21-33` declares MCP SDK, archive/scraping, Zod, Playwright, and
  Node toolchain dependencies. `package.json:3` says version 0.12.0 while
  `package-lock.json:2-9` still says 0.7.0.

The following values are historical information supplied by the user, not a
new run: at this same reference commit a prior `npm ci` reportedly installed
173 packages and audited 174 after a network retry; npm summarized 16 findings
(2 low, 1 moderate, 12 high, 1 critical); `npm run build` reportedly exited 0;
`npm test` was interrupted with exit 130 after local TCP tests saw sandbox
`EPERM`; separate production/full audits were not completed. This audit did not
attempt to reproduce those results.

## Source-verified interoperability findings

### Linux/Proton boundary

`README.md:13-17` supports running the Node process on Linux, but the Workbench
path defaults to Windows (`src/config.ts:32-38`), executable discovery looks for
`ArmaReforgerWorkbenchSteamDiag.exe` (`src/utils/game-paths.ts:62-75`), and the
client directly calls `spawn(exePath, ...)` (`src/workbench/client.ts:532-560`).
No Proton, app ID 1874910, compatdata, or dosdevices support was found. Host,
Wine, engine, and resource paths are all plain strings (`src/config.ts:7-29`).
The prompt's phrase "search Windows PE" is too strong: source checks an `.exe`
filename/existence, not a PE signature.

### Entity creation contract

The TypeScript tool accepts/sends `layerPath` and renders `result.name/id`
(`src/tools/wb-entities.ts:82-108`). The handler registers `layerID`, returns
`entityName/entityClass`, and falls back to layer 0
(`mod/Scripts/WorkbenchGame/EnfusionMCP/EMCP_WB_CreateEntity.c:14-48,109-142`).
It also does not validate the boolean results of the edit-action calls in
`EMCP_WB_CreateEntity.c:117-136`. All of these mismatches are confirmed.

### Layer actions

The client advertises list/create/delete/rename/setActive/setVisibility/
lock/unlock/isVisible/getInfo/toggleLock (`src/tools/wb-layers.ts:12-29,60-66`).
The handler implements only list/getActive/getEntityLayer/isVisible/getInfo/
toggleLock and names those as the valid actions
(`EMCP_WB_Layers.c:110-260`). The two sides are not contract-compatible.

### Selection

The client reads `result.selected` and claims it can select entities
(`src/tools/wb-entities.ts:401-461`). The handler returns
`selectedEntities`, and its `select` path clears selection without adding the
requested entity while still returning success
(`EMCP_WB_SelectEntity.c:48-61,118-139`). The mismatch and misleading action
are confirmed.

### Entity list

The formatter expects `total` (`src/tools/wb-entities.ts:36-42`) while the
handler registers/returns `totalCount` (`EMCP_WB_ListEntities.c:23-42,107-159`).
The fake client test returns a third field, `count`
(`tests/workbench/client.test.ts:81-112`), so it does not prove the real
contract.

### NET API framing

Request framing is signed int32 little-endian version 1 plus three UTF-8 Pascal
strings (`src/workbench/protocol.ts:4-43,68-87`), and each call creates a fresh
socket (`src/workbench/client.ts:742-755`). Negative, oversized, and truncated
strings are already rejected (`protocol.ts:47-65`). Trusted `APIFunc` is written
last (`protocol.ts:76-87`), so client params do not override it.

Confirmed decoder defects:

- a non-`Ok` status throws before the required second Pascal string is decoded,
  losing error payload (`protocol.ts:93-107`);
- empty/no payload is rejected (`protocol.ts:109-124` and tests at
  `tests/workbench/protocol.test.ts:176-189`);
- trailing bytes are not rejected;
- UTF-8 decoding is replacement-based rather than fatal (`protocol.ts:64`);
- one timeout covers all phases (`client.ts:757-769`) and request transmission
  at `client.ts:892-896` is not recorded, so mutation has no unknown-outcome
  semantics.

One prompt allegation needed correction: TCP fragmentation is not known to
break this client. It accumulates `data` chunks and concatenates them after
`end` (`client.ts:798-834`). What is missing is incremental exact-read framing,
strict final consumption, and dedicated fragmentation/fault coverage.

### Broad mutation and automatic installation

Broad capabilities include arbitrary-ish Workbench menu execution
(`src/tools/wb-execute-action.ts:6-51`), project writes
(`src/tools/project.ts:23-28,175-198`), world save
(`src/tools/wb-editor.ts:90-119`), and script editing
(`src/tools/wb-script-editor.ts:10-29`). `src/server.ts:60-126` registers them
without a safe profile.

On connection failure the client automatically installs handlers, starts the
executable, and retries (`src/workbench/client.ts:106-140,500-560,685-735`). It
also exposes recursive cleanup (`src/tools/wb-launch.ts:85-105` and
`client.ts:298-317`). None of these behaviours belongs in the new server.

### MCP metadata, supply chain, and authentication claim

The server constructor has no instructions (`src/index.ts:15-18`); source has no
MCP tool annotations or structured output. README config invokes unversioned
`npx -y enfusion-mcp` (`README.md:7-47`). That does not prove the package is
owned by someone else, but it does allow registry resolution to differ from the
audited checkout and is an avoidable supply-chain risk.

The wire/client has no token, challenge, TLS, or secret; client ID is a normal
string (`src/workbench/protocol.ts:4-11`, `src/workbench/client.ts:23,99-104`).
Reference source proves it does not authenticate, but cannot alone prove the
absolute claim that no other Workbench authentication facility exists. Safe
mode therefore treats the API as unauthenticated and enforces loopback.

### Portability and multi-process safety

`tests/animation/integration-m151a2.test.ts:14-25` embeds
`C:/Users/Steffen/.../TESTANIM` and reads it at suite initialization. It is not a
portable fixture.

No SQLite ledger, plan/idempotency binding, `flock`, or interprocess mutex was
found. The only deduplication is a process-local `launchPromise`
(`src/workbench/client.ts:90-93,227-240`). A generic `TIMEOUT` is used for reads
and writes alike (`client.ts:75-84,747-769`).

### Limits of the reference for new handlers

The 21 handlers do not use `WorldEditorAPI.GetWorldPath`, `Shape`,
`CoordToParent`, `TryGetTerrainSurfaceY`, or `SCR_TerrainHelper`. The old terrain
handler samples one X/Z through `GetTerrainSurfaceY` and separately reads bounds
(`EMCP_WB_Terrain.c:63-96`). It cannot prove any new Shape or batch-terrain
signature. Live `ValidateScripts` remains a hard gate after explicit bridge
installation permission.

Old handlers also serialize primitive arrays with `StoreString("", ...)`
(`EMCP_WB_GetState.c:47-54`, `EMCP_WB_Localization.c:77`); no `ItemString`
pattern was found. New contract checks must inspect actual staged handler source
and must not use the old pattern as proof of correctness.

