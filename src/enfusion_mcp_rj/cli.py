"""Console entry point.

The Checkpoint B implementation replaces the scaffold error with the STDIO MCP
server. Keeping this entry point silent on stdout protects the MCP transport
during intermediate development.
"""

from __future__ import annotations

import sys


def main() -> int:
    """Refuse to start until the safe profile is implemented."""
    sys.stderr.write("enfusion-mcp-rj: safe MCP runtime is not implemented yet\n")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
