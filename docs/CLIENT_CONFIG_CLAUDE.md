# Claude STDIO client configuration

Current Anthropic documentation describes project-scoped `.mcp.json` servers
with `command`, `args`, and `env`, and asks the user to approve project servers.
See [Claude Code MCP](https://docs.anthropic.com/en/docs/claude-code/mcp).

Replace every `/path/to/...` value and `C:\path\to\addon` with your actual
deployment paths. The host and engine addon paths must resolve to the same
directory through the configured Proton prefix. `$myaddon:world.ent` is an
example: set `ENFUSION_ALLOWED_WORLD` to the exact resource name of your world.
Server startup validates these paths; complete the
[bridge setup](BRIDGE_INSTALLATION_PLAN.md) before using Workbench tools.

```json
{
  "mcpServers": {
    "enfusion-mcp": {
      "type": "stdio",
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
      }
    }
  }
}
```

Do not use `--dangerously-skip-permissions`. Configure Claude's local permission
policy so `vegetation_apply` remains user-approved. The exact permission UI and
settings are client policy, not part of this server's security contract.

The path is pinned to the locally built executable; there is no `npx`/registry
resolution and no model-vendor code inside the server.
