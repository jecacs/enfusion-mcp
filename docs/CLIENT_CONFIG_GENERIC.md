# Generic STDIO client configuration

An MCP host should launch the audited local executable directly. It must not
resolve an npm/PyPI package, use a shell command string, or start Workbench.

Replace every `/path/to/...` value and `C:\path\to\addon` with your actual
deployment paths. The host and engine addon paths must resolve to the same
directory through the configured Proton prefix. `$myaddon:world.ent` is an
example: set the required `ENFUSION_ALLOWED_WORLD` to your world's exact resource
name. Server startup validates these paths; complete the
[bridge setup](BRIDGE_INSTALLATION_PLAN.md) before using Workbench tools.

Many hosts accept the following standard-shaped JSON (consult the host's own
schema for its outer key):

```json
{
  "mcpServers": {
    "enfusion-mcp": {
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

Every independent process must use the exact same absolute state path to share
operation identity and the mutation lock. The state directory may initially be
absent; the server creates it with private permissions. Its parent must exist.
Keep it outside the addon directory and Proton prefix. The `.state` path below
the server checkout is suitable when that checkout is separate from both.

Client approval policy should allow the four read-oriented tools according to
local policy and prompt explicitly for `vegetation_apply`. This is defense in
depth: the server enforces validation, ledger, and locking even if the host does
not understand MCP annotations.

If exporting the world resource in a shell for diagnostics, quote the dollar
sign, for example:

```console
export ENFUSION_ALLOWED_WORLD='$myaddon:world.ent'
```

The server itself never reads `.env`; configuration must be passed explicitly
by the host.
