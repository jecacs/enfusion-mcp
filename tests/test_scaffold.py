from __future__ import annotations

from enfusion_mcp_rj import __version__
from enfusion_mcp_rj.cli import main


def test_distribution_version_is_available() -> None:
    assert __version__ == "0.1.0a0"


def test_scaffold_cli_never_writes_to_stdout(capsys: object) -> None:
    assert main() == 2
    # Deliberately avoid depending on pytest's capture protocol in the annotation.
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert captured.out == ""
    assert "not implemented" in captured.err
