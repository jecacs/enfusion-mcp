from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from enfusion_mcp_rj.config import ServerConfig
from enfusion_mcp_rj.ledger import Ledger
from enfusion_mcp_rj.locking import TargetLockManager, TargetScope
from enfusion_mcp_rj.net_api import (
    JsonValue,
    NetApiClient,
    NetApiError,
    NetApiErrorCode,
    NetApiPhase,
    UnknownOutcomeError,
    WorkbenchApiError,
)
from enfusion_mcp_rj.service import SafeRuntimeService, _typed_net_error


def config(tmp_path: Path) -> ServerConfig:
    prefix = tmp_path / "prefix"
    drive_c = prefix / "drive_c"
    project = drive_c / "project"
    state = tmp_path / ".state"
    project.mkdir(parents=True)
    (prefix / "dosdevices").mkdir()
    (prefix / "dosdevices/c:").symlink_to(drive_c)
    return ServerConfig.from_env(
        {
            "ENFUSION_PLATFORM_MODE": "native-linux-proton",
            "ENFUSION_STEAM_TOOLS_APP_ID": "1874910",
            "ENFUSION_PROTON_PREFIX": str(prefix),
            "ENFUSION_WORKBENCH_HOST": "127.0.0.1",
            "ENFUSION_WORKBENCH_PORT": "5775",
            "ENFUSION_PROJECT_HOST_PATH": str(project),
            "ENFUSION_PROJECT_ENGINE_PATH": r"C:\project",
            "ENFUSION_ALLOWED_WORLD": "$thenewRJ:rj.ent",
            "ENFUSION_SAFE_MODE": "1",
            "ENFUSION_STATE_DIR": str(state),
        }
    )


class FakeNetClient:
    def __init__(self, outcomes: list[JsonValue | BaseException]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, Mapping[str, JsonValue] | None]] = []

    @property
    def workbench_address(self) -> tuple[str, int]:
        return ("127.0.0.1", 5775)

    async def call(
        self,
        api_func: str,
        params: Mapping[str, JsonValue] | None = None,
        **_kwargs: object,
    ) -> JsonValue:
        self.calls.append((api_func, params))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def service(client: FakeNetClient, tmp_path: Path) -> SafeRuntimeService:
    server_config = config(tmp_path)
    ledger = Ledger(server_config.state_dir.path, allowed_root=tmp_path)
    lock_manager = TargetLockManager(server_config.state_dir.path, allowed_root=tmp_path)
    scope = TargetScope.derive(
        workbench_host=server_config.workbench_host,
        workbench_port=server_config.workbench_port,
        project_host_path=server_config.project_host_path.path,
        project_engine_path=server_config.project_engine_path.value,
        world=server_config.allowed_world.value,
    )
    return SafeRuntimeService(
        server_config,
        cast(NetApiClient, client),
        ledger=ledger,
        lock_manager=lock_manager,
        target_scope=scope,
    )


def test_service_rejects_lock_scope_that_does_not_match_validated_config(tmp_path: Path) -> None:
    server_config = config(tmp_path)
    ledger = Ledger(server_config.state_dir.path, allowed_root=tmp_path)
    lock_manager = TargetLockManager(server_config.state_dir.path, allowed_root=tmp_path)
    wrong_scope = TargetScope.derive(
        workbench_host=server_config.workbench_host,
        workbench_port=server_config.workbench_port,
        project_host_path=tmp_path / "different-project",
        project_engine_path=r"Z:\different-project",
        world=server_config.allowed_world.value,
    )
    with pytest.raises(ValueError, match="target scope"):
        SafeRuntimeService(
            server_config,
            cast(NetApiClient, FakeNetClient([])),
            ledger=ledger,
            lock_manager=lock_manager,
            target_scope=wrong_scope,
        )


def test_service_rejects_net_client_target_that_does_not_match_config(tmp_path: Path) -> None:
    server_config = config(tmp_path)
    ledger = Ledger(server_config.state_dir.path, allowed_root=tmp_path)
    lock_manager = TargetLockManager(server_config.state_dir.path, allowed_root=tmp_path)
    scope = TargetScope.derive(
        workbench_host=server_config.workbench_host,
        workbench_port=server_config.workbench_port,
        project_host_path=server_config.project_host_path.path,
        project_engine_path=server_config.project_engine_path.value,
        world=server_config.allowed_world.value,
    )

    with pytest.raises(ValueError, match="client target"):
        SafeRuntimeService(
            server_config,
            NetApiClient("127.0.0.1", 5774),
            ledger=ledger,
            lock_manager=lock_manager,
            target_scope=scope,
        )


@pytest.mark.asyncio
async def test_status_connection_refused_has_manual_guidance(tmp_path: Path) -> None:
    client = FakeNetClient(
        [
            NetApiError(
                NetApiErrorCode.CONNECTION_REFUSED,
                "refused",
                phase=NetApiPhase.CONNECT,
            )
        ]
    )
    result = await service(client, tmp_path).workbench_status()
    assert result.ok is False
    assert result.reachable is False
    assert result.error is not None
    assert result.error.code == "CONNECTION_REFUSED"
    assert "Steam/Proton" in result.error.message


@pytest.mark.asyncio
async def test_status_second_probe_failure_preserves_reachability(tmp_path: Path) -> None:
    client = FakeNetClient(
        [
            {"IsRunning": True, "ScriptsCompiled": True},
            NetApiError(
                NetApiErrorCode.PROTOCOL_ERROR,
                "truncated payload",
                phase=NetApiPhase.RESPONSE,
                send_started=True,
            ),
        ]
    )
    result = await service(client, tmp_path).workbench_status()
    assert result.ok is False
    assert result.reachable is True
    assert result.error is not None
    assert result.error.code == "PROTOCOL_ERROR"
    assert [call[0] for call in client.calls] == [
        "IsWorkbenchRunning",
        "IsWorldEditorRunning",
    ]


def test_unknown_error_retains_bounded_nested_workbench_payload_evidence() -> None:
    payload = "x" * 2048
    error = _typed_net_error(UnknownOutcomeError(WorkbenchApiError("HandlerError", payload)))
    assert error.code == "UNKNOWN_OUTCOME"
    assert error.unknown_outcome is True
    assert error.retryable is False
    assert error.details["cause_code"] == "API_ERROR"
    assert error.details["workbench_status"] == "HandlerError"
    assert error.details["workbench_payload"] == "x" * 1024
    assert error.details["workbench_payload_bytes"] == 2048
    assert error.details["workbench_payload_truncated"] is True
    assert len(cast(str, error.details["workbench_payload_sha256"])) == 64


def test_oversized_direct_error_message_is_typed_and_bounded() -> None:
    error = _typed_net_error(WorkbenchApiError("X" * 3000, "payload"))
    assert len(error.message) == 2048
    assert error.details["message_truncated"] is True
    assert len(cast(str, error.details["message_sha256"])) == 64
