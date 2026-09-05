# Bridge installation plan — not executed

Checkpoint C does not inspect or modify the active project. Installation is
blocked until the user writes exactly:

```text
Разрешаю установить bridge в active new_rj
```

Discussion, a similar sentence, or permission to continue development is not
installation permission.

## Calculated destination

Without resolving or reading it yet, the configured destination base is:

```text
/home/jecacs/.local/share/Steam/steamapps/compatdata/1874910/pfx/drive_c/users/steamuser/Documents/My Games/ArmaReforgerWorkbench/addons/new_rj/Scripts/WorkbenchGame/RJMCP
```

The proposed manifest contains only:

```text
bridge/Scripts/WorkbenchGame/RJMCP/RJMCP_GetContext.c
  -> <destination>/RJMCP_GetContext.c
bridge/Scripts/WorkbenchGame/RJMCP/RJMCP_TerrainSample.c
  -> <destination>/RJMCP_TerrainSample.c
bridge/Scripts/WorkbenchGame/RJMCP/RJMCP_VegetationApply.c
  -> <destination>/RJMCP_VegetationApply.c
```

Collision status is deliberately unknown because reading the destination is not
yet permitted. No unknown file will be deleted, and no collision outside a
previous RJMCP installation manifest will be overwritten without separate
approval. `resourceDatabase.rdb`, `rj.ent`, layers, terrain, and all other
project files are excluded.

## Permissioned procedure

1. Resolve the exact active path and its realpath.
2. Verify project ID/GUID and additional markers, while proving the path is not
   `/home/jecacs/Documents/the new RJ`.
3. Do not compare, copy, or synchronize the stale map.
4. Show the exact source → destination manifest, sizes, and staged SHA-256.
5. Ask the user to create a timestamped backup of the active project in a new,
   separate backup location. The stale copy is not a backup source/destination.
6. Copy only the three named handlers, without overwriting an unexplained
   collision.
7. Record timestamp, source Git commit, destination, filename, size, staged
   SHA-256, and installed SHA-256; verify every installed hash.
8. Do not launch Steam or Workbench. The user manually restarts Tools through
   Steam/Proton and manually enables NET API.

## First permitted live stage

After a separate confirmation that Workbench was manually started, only these
read operations are allowed initially:

1. `IsWorkbenchRunning`;
2. `IsWorldEditorRunning`;
3. `ValidateScripts` with `Configuration=WORKBENCH`;
4. `RJMCP_GetContext`.

Any script validation error stops the workflow. Mutation remains blocked until
a clean validation.

## Required second revision before any plan/apply

The Checkpoint C artifact intentionally has both an empty production catalog
and `MUTATION_IMPLEMENTATION_VALIDATED=false`. It can validate compilation and
the read-only context/terrain contract, but it cannot produce or apply a live
vegetation plan. This is a deliberate correction to the original prompt's
circular live sequence.

After the read-only stage, the operator must either provide exact vegetation
resource names or separately authorize a narrowly scoped way to establish them.
Then a new reviewed source revision must:

1. put the identical exact allowlist and catalog hash in Python and Enforce;
2. reconcile the public scale contract: either prove a checked editor-source
   scale write or restrict the public V1 policy to identity scale;
3. address every finding from `ValidateScripts` and read-only round trips;
4. deliberately change `MUTATION_IMPLEMENTATION_VALIDATED` only after review;
5. bump the bridge build ID;
6. produce a new commit, source hashes, and source → destination manifest;
7. obtain renewed explicit installation approval for those changed hashes;
8. restart Workbench manually and pass `ValidateScripts` again.

Only that later revision may generate and show an 8–12-object plan targeting
`MCP_Preview`. It still must not mutate until the user separately writes
`применяй` for the exact shown, non-expired plan. If build/catalog/Shape/context
changes, the plan is stale and must be regenerated and shown again.
