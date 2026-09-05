"""Safe Python MCP bridge for Arma Reforger Enfusion Workbench."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("enfusion-mcp-rj")
except PackageNotFoundError:  # pragma: no cover - editable install normally supplies metadata
    __version__ = "0.1.0a0"

__all__ = ["__version__"]
