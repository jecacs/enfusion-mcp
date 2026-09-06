from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest
from mcp import Client, StdioServerParameters


def _safe_env(tmp_path: Path) -> dict[str, str]:
    prefix = tmp_path / "prefix"
    project = tmp_path / "project"
    state = tmp_path / "state"
    dosdevices = prefix / "dosdevices"
    dosdevices.mkdir(parents=True)
    project.mkdir()
    (dosdevices / "z:").symlink_to(tmp_path)
    return {
        **os.environ,
        "ENFUSION_PLATFORM_MODE": "native-linux-proton",
        "ENFUSION_STEAM_TOOLS_APP_ID": "1874910",
        "ENFUSION_PROTON_PREFIX": str(prefix),
        "ENFUSION_WORKBENCH_HOST": "127.0.0.1",
        "ENFUSION_WORKBENCH_PORT": "5775",
        "ENFUSION_PROJECT_HOST_PATH": str(project),
        "ENFUSION_PROJECT_ENGINE_PATH": r"Z:\project",
        "ENFUSION_ALLOWED_WORLD": "$myaddon:world.ent",
        "ENFUSION_SAFE_MODE": "1",
        "ENFUSION_STATE_DIR": str(state),
    }


def _stdio_parameters(tmp_path: Path) -> StdioServerParameters:
    root = Path(__file__).resolve().parents[1]
    return StdioServerParameters(
        command=str(root / ".venv/bin/enfusion-mcp"),
        args=[],
        cwd=root,
        env=_safe_env(tmp_path),
    )


@pytest.mark.asyncio
async def test_modern_stdio_discovery_and_generic_contract(tmp_path: Path) -> None:
    async with Client(_stdio_parameters(tmp_path)) as client:
        tools = await client.list_tools()
        assert client.protocol_version == "2026-07-28"
        assert [tool.name for tool in tools.tools] == [
            "workbench_status",
            "world_context",
            "vegetation_catalog",
            "vegetation_plan",
            "vegetation_apply",
        ]
        result = await client.call_tool("vegetation_catalog", {})
        assert result.structured_content is not None
        assert result.structured_content["production_ready"] is False


async def _write_message(process: asyncio.subprocess.Process, message: dict[str, Any]) -> None:
    assert process.stdin is not None
    process.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode())
    await process.stdin.drain()


async def _read_message(process: asyncio.subprocess.Process) -> dict[str, Any]:
    assert process.stdout is not None
    line = await asyncio.wait_for(process.stdout.readline(), timeout=5)
    assert line.endswith(b"\n")
    value = json.loads(line)
    assert isinstance(value, dict)
    return value


@pytest.mark.asyncio
async def test_legacy_initialize_and_stdout_stays_jsonrpc(tmp_path: Path) -> None:
    params = _stdio_parameters(tmp_path)
    process = await asyncio.create_subprocess_exec(
        params.command,
        *params.args,
        cwd=params.cwd,
        env=params.env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        await _write_message(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "generic-blackbox", "version": "1.0"},
                },
            },
        )
        initialized = await _read_message(process)
        assert initialized["id"] == 1
        assert initialized["result"]["protocolVersion"] == "2025-11-25"
        assert "Always call vegetation_plan" in initialized["result"]["instructions"]

        await _write_message(
            process,
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        )
        await _write_message(
            process,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        listed = await _read_message(process)
        assert listed["id"] == 2
        assert len(listed["result"]["tools"]) == 5

        assert process.stdin is not None
        process.stdin.close()
        await asyncio.wait_for(process.wait(), timeout=5)
        assert process.returncode == 0
        assert process.stdout is not None
        assert await process.stdout.read() == b""
        assert process.stderr is not None
        stderr = (await process.stderr.read()).decode()
        assert "starting safe STDIO profile" in stderr
    finally:
        if process.returncode is None:
            process.terminate()
            await process.wait()


def test_runtime_has_no_model_vendor_imports() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src/enfusion_mcp"
    banned = ("openai", "anthropic", "langchain", "langgraph", "codex", "claude")
    source = "\n".join(path.read_text() for path in source_root.glob("*.py")).lower()
    for name in banned:
        assert f"import {name}" not in source
        assert f"from {name}" not in source
