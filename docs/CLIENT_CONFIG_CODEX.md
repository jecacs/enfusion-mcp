# Codex STDIO client configuration

The syntax below was checked against current [official OpenAI MCP documentation](https://developers.openai.com/codex/mcp)
and the [Codex configuration reference](https://developers.openai.com/codex/config-reference)
on 2026-09-05.

This is a **post-permission deployment example**. Do not enable it during
Checkpoint C: server startup resolves the configured Proton/project paths, and
opening Codex or an IDE is not permission to inspect the active project.

```toml
[mcp_servers.enfusion_mcp_rj]
command = "/home/jecacs/work/arma/enfusion-mcp/.venv/bin/enfusion-mcp-rj"
args = []
cwd = "/home/jecacs/work/arma/enfusion-mcp"
enabled = true
enabled_tools = [
  "workbench_status",
  "world_context",
  "vegetation_catalog",
  "vegetation_plan",
  "vegetation_apply",
]
default_tools_approval_mode = "writes"

[mcp_servers.enfusion_mcp_rj.tools.vegetation_apply]
approval_mode = "prompt"

[mcp_servers.enfusion_mcp_rj.env]
ENFUSION_PLATFORM_MODE = "native-linux-proton"
ENFUSION_STEAM_TOOLS_APP_ID = "1874910"
ENFUSION_PROTON_PREFIX = "/home/jecacs/.local/share/Steam/steamapps/compatdata/1874910/pfx"
ENFUSION_WORKBENCH_HOST = "127.0.0.1"
ENFUSION_WORKBENCH_PORT = "5775"
ENFUSION_PROJECT_HOST_PATH = "/home/jecacs/.local/share/Steam/steamapps/compatdata/1874910/pfx/drive_c/users/steamuser/Documents/My Games/ArmaReforgerWorkbench/addons/new_rj"
ENFUSION_PROJECT_ENGINE_PATH = 'C:\users\steamuser\Documents\My Games\ArmaReforgerWorkbench\addons\new_rj'
ENFUSION_ALLOWED_WORLD = '$thenewRJ:rj.ent'
ENFUSION_SAFE_MODE = "1"
ENFUSION_STATE_DIR = "/home/jecacs/work/arma/enfusion-mcp/.state"
```

`default_tools_approval_mode = "writes"` uses MCP read-only annotations for the
default decision, while the per-tool rule pins `vegetation_apply` to a prompt.
The `enabled_tools` list is another client-side allowlist. None of these replaces
server-side checks.

The command is an absolute, local, lock-file-built executable. Do not replace it
with `npx`, `uvx ...@latest`, a shell wrapper, or a remote package name.
