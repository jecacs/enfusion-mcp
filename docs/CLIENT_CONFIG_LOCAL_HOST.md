# Local/self-hosted agent host

The server does not call a model and does not care which model an orchestration
host uses. A local host only needs a standard MCP STDIO client and an explicit
process specification.

This is a **post-permission deployment example**. Do not enable it during
Checkpoint C: server startup resolves the configured Proton/project paths, and
starting an agent host is not permission to inspect the active project.

```json
{
  "name": "enfusion-mcp-rj",
  "transport": "stdio",
  "command": "/home/jecacs/work/arma/enfusion-mcp/.venv/bin/enfusion-mcp-rj",
  "args": [],
  "env": {
    "ENFUSION_PLATFORM_MODE": "native-linux-proton",
    "ENFUSION_STEAM_TOOLS_APP_ID": "1874910",
    "ENFUSION_PROTON_PREFIX": "/home/jecacs/.local/share/Steam/steamapps/compatdata/1874910/pfx",
    "ENFUSION_WORKBENCH_HOST": "127.0.0.1",
    "ENFUSION_WORKBENCH_PORT": "5775",
    "ENFUSION_PROJECT_HOST_PATH": "/home/jecacs/.local/share/Steam/steamapps/compatdata/1874910/pfx/drive_c/users/steamuser/Documents/My Games/ArmaReforgerWorkbench/addons/new_rj",
    "ENFUSION_PROJECT_ENGINE_PATH": "C:\\users\\steamuser\\Documents\\My Games\\ArmaReforgerWorkbench\\addons\\new_rj",
    "ENFUSION_ALLOWED_WORLD": "$thenewRJ:rj.ent",
    "ENFUSION_SAFE_MODE": "1",
    "ENFUSION_STATE_DIR": "/home/jecacs/work/arma/enfusion-mcp/.state"
  },
  "approval": {
    "vegetation_apply": "prompt"
  }
}
```

The outer `transport` and `approval` keys are illustrative because host schemas
vary. The process `command`/`args`/`env` values are the portable core. A host
must not combine them into a shell string.

Multiple local agents may launch separate processes. They must all use the same
canonical state directory and target configuration. Model name, prompt, agent
name, and MCP client/session metadata never participate in authorization or
operation identity.
