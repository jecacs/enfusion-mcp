# Codex STDIO client configuration

The syntax below was checked against current [official OpenAI MCP documentation](https://developers.openai.com/codex/mcp)
and the [Codex configuration reference](https://developers.openai.com/codex/config-reference)
on 2026-09-05.

Replace every `/path/to/...` value and `C:\path\to\addon` with your actual
deployment paths. The host and engine addon paths must resolve to the same
directory through the configured Proton prefix. `$myaddon:world.ent` is an
example: set `ENFUSION_ALLOWED_WORLD` to the exact resource name of your world.
Server startup validates these paths; complete the
[bridge setup](BRIDGE_INSTALLATION_PLAN.md) before using Workbench tools.

```toml
[mcp_servers.enfusion_mcp]
command = "/path/to/enfusion-mcp/.venv/bin/enfusion-mcp"
args = []
cwd = "/path/to/enfusion-mcp"
enabled = true
enabled_tools = [
  "workbench_status",
  "world_context",
  "vegetation_catalog",
  "vegetation_plan",
  "vegetation_apply",
]
default_tools_approval_mode = "writes"

[mcp_servers.enfusion_mcp.tools.vegetation_apply]
approval_mode = "prompt"

[mcp_servers.enfusion_mcp.env]
ENFUSION_PLATFORM_MODE = "native-linux-proton"
ENFUSION_STEAM_TOOLS_APP_ID = "1874910"
ENFUSION_PROTON_PREFIX = "/path/to/proton-prefix"
ENFUSION_WORKBENCH_HOST = "127.0.0.1"
ENFUSION_WORKBENCH_PORT = "5775"
ENFUSION_PROJECT_HOST_PATH = "/path/to/addon"
ENFUSION_PROJECT_ENGINE_PATH = 'C:\path\to\addon'
ENFUSION_ALLOWED_WORLD = '$myaddon:world.ent'
ENFUSION_SAFE_MODE = "1"
ENFUSION_STATE_DIR = "/path/to/enfusion-mcp/.state"
```

`default_tools_approval_mode = "writes"` uses MCP read-only annotations for the
default decision, while the per-tool rule pins `vegetation_apply` to a prompt.
The `enabled_tools` list is another client-side allowlist. None of these replaces
server-side checks.

The command is an absolute, local, lock-file-built executable. Do not replace it
with `npx`, `uvx ...@latest`, a shell wrapper, or a remote package name.
