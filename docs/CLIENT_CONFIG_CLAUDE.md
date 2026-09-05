# Claude STDIO client configuration

Current Anthropic documentation describes project-scoped `.mcp.json` servers
with `command`, `args`, and `env`, and asks the user to approve project servers.
See [Claude Code MCP](https://docs.anthropic.com/en/docs/claude-code/mcp).

This is a **post-permission deployment example**. Do not enable it during
Checkpoint C: server startup resolves the configured Proton/project paths, and
opening Claude or an IDE is not permission to inspect the active project.

```json
{
  "mcpServers": {
    "enfusion-mcp-rj": {
      "type": "stdio",
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
