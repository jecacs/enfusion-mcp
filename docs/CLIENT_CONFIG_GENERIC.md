# Generic STDIO client configuration

An MCP host should launch the audited local executable directly. It must not
resolve an npm/PyPI package, use a shell command string, or start Workbench.

The concrete configuration below is a **post-permission deployment example**,
not an instruction to enable the server during Checkpoint C. Starting it causes
strict configuration validation to resolve the configured Proton/project paths.
Do not activate this example until the bridge-install permission gate has been
completed. Merely opening an IDE does not authorize that filesystem access.

Many hosts accept the following standard-shaped JSON (consult the host's own
schema for its outer key):

```json
{
  "mcpServers": {
    "enfusion-mcp-rj": {
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

Every independent process must use the exact same absolute state path to share
operation identity and the mutation lock. The state directory may initially be
absent; the server creates it with private permissions. Its parent must exist.

Client approval policy should allow the four read-oriented tools according to
local policy and prompt explicitly for `vegetation_apply`. This is defense in
depth: the server enforces validation, ledger, and locking even if the host does
not understand MCP annotations.

If exporting the world resource in a shell for diagnostics, quote the dollar
sign, for example:

```console
export ENFUSION_ALLOWED_WORLD='$thenewRJ:rj.ent'
```

The server itself never reads `.env`; configuration must be passed explicitly
by the host.
