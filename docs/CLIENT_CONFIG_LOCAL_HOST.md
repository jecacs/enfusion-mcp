# Local/self-hosted agent host

The server does not call a model and does not care which model an orchestration
host uses. A local host only needs a standard MCP STDIO client and an explicit
process specification.

Replace every `/path/to/...` value and `C:\path\to\addon` with your actual
deployment paths. The host and engine addon paths must resolve to the same
directory through the configured Proton prefix. `$myaddon:world.ent` is an
example: set `ENFUSION_ALLOWED_WORLD` to the exact resource name of your world.
Server startup validates these paths; complete the
[bridge setup](BRIDGE_INSTALLATION_PLAN.md) before using Workbench tools.

```json
{
  "name": "enfusion-mcp",
  "transport": "stdio",
  "command": "/path/to/enfusion-mcp/.venv/bin/enfusion-mcp",
  "args": [],
  "env": {
    "ENFUSION_PLATFORM_MODE": "native-linux-proton",
    "ENFUSION_STEAM_TOOLS_APP_ID": "1874910",
    "ENFUSION_PROTON_PREFIX": "/path/to/proton-prefix",
    "ENFUSION_WORKBENCH_HOST": "127.0.0.1",
    "ENFUSION_WORKBENCH_PORT": "5775",
    "ENFUSION_PROJECT_HOST_PATH": "/path/to/addon",
    "ENFUSION_PROJECT_ENGINE_PATH": "C:\\path\\to\\addon",
    "ENFUSION_ALLOWED_WORLD": "$myaddon:world.ent",
    "ENFUSION_SAFE_MODE": "1",
    "ENFUSION_STATE_DIR": "/path/to/enfusion-mcp/.state"
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
