from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from enfusion_mcp.config import (
    REQUIRED_ENVIRONMENT,
    ConfigurationError,
    ServerConfig,
    Settings,
)
from enfusion_mcp.path_types import EnginePath, HostPath, ResourceName


class _StringSubclass(str):
    pass


class _IntegerSubclass(int):
    pass


@pytest.fixture
def valid_environment(tmp_path: Path) -> Iterator[dict[str, str]]:
    prefix = tmp_path / "Proton Prefix"
    drive_c = prefix / "drive_c"
    dosdevices = prefix / "dosdevices"
    project = drive_c / "users" / "steamuser" / "Моя карта"
    state = tmp_path / "repository" / ".state"
    project.mkdir(parents=True)
    state.mkdir(parents=True)
    dosdevices.mkdir()
    (dosdevices / "c:").symlink_to(drive_c, target_is_directory=True)

    yield {
        "ENFUSION_PLATFORM_MODE": "native-linux-proton",
        "ENFUSION_STEAM_TOOLS_APP_ID": "1874910",
        "ENFUSION_PROTON_PREFIX": os.fspath(prefix),
        "ENFUSION_WORKBENCH_HOST": "127.0.0.1",
        "ENFUSION_WORKBENCH_PORT": "5775",
        "ENFUSION_PROJECT_HOST_PATH": os.fspath(project),
        "ENFUSION_PROJECT_ENGINE_PATH": r"C:\users\steamuser\Моя карта",
        "ENFUSION_ALLOWED_WORLD": "$myaddon:world.ent",
        "ENFUSION_SAFE_MODE": "1",
        "ENFUSION_STATE_DIR": os.fspath(state),
    }


def test_valid_safe_configuration_is_frozen_and_typed(
    valid_environment: dict[str, str],
) -> None:
    config = ServerConfig.from_env(valid_environment)

    assert Settings is ServerConfig
    assert config.platform_mode == "native-linux-proton"
    assert config.steam_tools_app_id == 1874910
    assert config.workbench_address == ("127.0.0.1", 5775)
    assert config.proton_prefix == HostPath(valid_environment["ENFUSION_PROTON_PREFIX"])
    assert config.project_host_path == HostPath(valid_environment["ENFUSION_PROJECT_HOST_PATH"])
    assert config.project_engine_path == EnginePath(
        valid_environment["ENFUSION_PROJECT_ENGINE_PATH"]
    )
    assert config.allowed_world == ResourceName("$myaddon:world.ent")
    assert config.safe_mode is True
    assert config.state_dir == HostPath(valid_environment["ENFUSION_STATE_DIR"])
    with pytest.raises(AttributeError):
        config.workbench_port = 1234  # type: ignore[assignment,misc]


def test_dataclass_replace_cannot_bypass_safe_runtime_invariants(
    valid_environment: dict[str, str],
) -> None:
    config = ServerConfig.from_env(valid_environment)
    with pytest.raises(ConfigurationError, match="safe profile requires exactly"):
        replace(config, workbench_host="localhost")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "world",
    [
        "$myaddon:world.ent",
        "$another_project:Worlds/Island.ent",
        "{0123456789ABCDEF}Worlds/Terrain.ent",
    ],
)
def test_target_world_is_explicitly_configurable(
    valid_environment: dict[str, str],
    world: str,
) -> None:
    original = ServerConfig.from_env(valid_environment)
    valid_environment["ENFUSION_ALLOWED_WORLD"] = world

    configured = ServerConfig.from_env(valid_environment)

    assert configured.allowed_world == ResourceName(world)
    assert replace(original, allowed_world=ResourceName(world)) == configured


@pytest.mark.parametrize(
    "world",
    [
        "world.ent",
        "/project/world.ent",
        r"C:\project\world.ent",
        "$myaddon:../world.ent",
        "$myaddon:/world.ent",
        "$myaddon:world.ent\x00suffix",
    ],
)
def test_target_world_must_be_a_valid_resource_name(
    valid_environment: dict[str, str],
    world: str,
) -> None:
    valid_environment["ENFUSION_ALLOWED_WORLD"] = world

    with pytest.raises(ConfigurationError) as captured:
        ServerConfig.from_env(valid_environment)
    assert captured.value.field == "ENFUSION_ALLOWED_WORLD"


def test_direct_config_requires_typed_target_world(
    valid_environment: dict[str, str],
) -> None:
    config = ServerConfig.from_env(valid_environment)

    with pytest.raises(ConfigurationError, match="must be a ResourceName") as captured:
        replace(config, allowed_world="$myaddon:world.ent")  # type: ignore[arg-type]
    assert captured.value.field == "allowed_world"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("platform_mode", _StringSubclass("native-linux-proton")),
        ("steam_tools_app_id", 1_874_910.0),
        ("steam_tools_app_id", _IntegerSubclass(1_874_910)),
        ("workbench_host", _StringSubclass("127.0.0.1")),
        ("workbench_port", 5_775.0),
        ("workbench_port", _IntegerSubclass(5_775)),
        ("safe_mode", 1),
    ],
)
def test_direct_config_rejects_equal_values_with_wrong_builtin_type(
    valid_environment: dict[str, str],
    field: str,
    value: object,
) -> None:
    config = ServerConfig.from_env(valid_environment)

    with pytest.raises(ConfigurationError, match="safe profile requires exactly") as captured:
        replace(config, **{field: value})  # type: ignore[arg-type]
    assert captured.value.field == field


@pytest.mark.parametrize("missing", REQUIRED_ENVIRONMENT)
def test_every_required_environment_variable_is_fail_closed(
    valid_environment: dict[str, str],
    missing: str,
) -> None:
    del valid_environment[missing]

    with pytest.raises(ConfigurationError, match="required environment variable is missing"):
        ServerConfig.from_env(valid_environment)


@pytest.mark.parametrize("empty", REQUIRED_ENVIRONMENT)
def test_empty_environment_values_are_rejected(
    valid_environment: dict[str, str],
    empty: str,
) -> None:
    valid_environment[empty] = ""

    with pytest.raises(ConfigurationError, match="must not be empty"):
        ServerConfig.from_env(valid_environment)


def test_unknown_enfusion_environment_variable_is_fail_closed(
    valid_environment: dict[str, str],
) -> None:
    valid_environment["ENFUSION_WORKBENCH_HOTS"] = "127.0.0.1"
    valid_environment["ENFUSION_UNSAFE_FUTURE_OPTION"] = "1"

    with pytest.raises(ConfigurationError, match="unknown ENFUSION_") as captured:
        ServerConfig.from_env(valid_environment)
    assert captured.value.field == ("ENFUSION_UNSAFE_FUTURE_OPTION,ENFUSION_WORKBENCH_HOTS")


def test_unrelated_process_environment_variables_remain_allowed(
    valid_environment: dict[str, str],
) -> None:
    valid_environment["PATH"] = "/usr/bin"
    valid_environment["HOME"] = "/synthetic/home"
    valid_environment["CLAUDE_PROJECT_DIR"] = "/synthetic/project"

    assert ServerConfig.from_env(valid_environment).safe_mode is True


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("ENFUSION_PLATFORM_MODE", "windows"),
        ("ENFUSION_PLATFORM_MODE", "native-linux-proton "),
        ("ENFUSION_STEAM_TOOLS_APP_ID", "1874880"),
        ("ENFUSION_STEAM_TOOLS_APP_ID", "01874910"),
        ("ENFUSION_WORKBENCH_HOST", "localhost"),
        ("ENFUSION_WORKBENCH_HOST", "0.0.0.0"),  # noqa: S104 - rejection fixture
        ("ENFUSION_WORKBENCH_HOST", "::1"),
        ("ENFUSION_WORKBENCH_PORT", "5776"),
        ("ENFUSION_WORKBENCH_PORT", "05775"),
        ("ENFUSION_SAFE_MODE", "0"),
        ("ENFUSION_SAFE_MODE", "true"),
    ],
)
def test_security_boundary_values_must_match_exactly(
    valid_environment: dict[str, str],
    field: str,
    unsafe_value: str,
) -> None:
    valid_environment[field] = unsafe_value

    with pytest.raises(ConfigurationError, match="requires exactly") as captured:
        ServerConfig.from_env(valid_environment)
    assert captured.value.field == field


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("ENFUSION_PROTON_PREFIX", "relative/prefix"),
        ("ENFUSION_PROJECT_HOST_PATH", "relative/project"),
        ("ENFUSION_STATE_DIR", "relative/state"),
    ],
)
def test_host_directories_must_be_absolute(
    valid_environment: dict[str, str],
    field: str,
    replacement: str,
) -> None:
    valid_environment[field] = replacement

    with pytest.raises(ConfigurationError, match="must be absolute"):
        ServerConfig.from_env(valid_environment)


@pytest.mark.parametrize(
    "field",
    ["ENFUSION_PROTON_PREFIX", "ENFUSION_PROJECT_HOST_PATH"],
)
def test_host_directories_must_exist(
    valid_environment: dict[str, str],
    tmp_path: Path,
    field: str,
) -> None:
    valid_environment[field] = os.fspath(tmp_path / f"missing-{field}")

    with pytest.raises(ConfigurationError, match="cannot be resolved"):
        ServerConfig.from_env(valid_environment)


def test_missing_state_leaf_is_allowed_but_its_parent_must_exist(
    valid_environment: dict[str, str],
    tmp_path: Path,
) -> None:
    missing_state = tmp_path / "existing-repository" / ".state"
    missing_state.parent.mkdir()
    valid_environment["ENFUSION_STATE_DIR"] = os.fspath(missing_state)

    assert ServerConfig.from_env(valid_environment).state_dir == HostPath(os.fspath(missing_state))
    assert not missing_state.exists()

    valid_environment["ENFUSION_STATE_DIR"] = os.fspath(tmp_path / "missing-repository" / ".state")
    with pytest.raises(ConfigurationError, match="parent cannot be resolved"):
        ServerConfig.from_env(valid_environment)


def test_state_directory_must_be_canonical(
    valid_environment: dict[str, str],
    tmp_path: Path,
) -> None:
    real_state = Path(valid_environment["ENFUSION_STATE_DIR"])
    alias = tmp_path / "state-alias"
    alias.symlink_to(real_state, target_is_directory=True)
    valid_environment["ENFUSION_STATE_DIR"] = os.fspath(alias)

    with pytest.raises(ConfigurationError, match="path must be canonical"):
        ServerConfig.from_env(valid_environment)


def test_project_engine_path_must_match_real_dosdevices_mapping(
    valid_environment: dict[str, str],
) -> None:
    valid_environment["ENFUSION_PROJECT_ENGINE_PATH"] = r"C:\users\steamuser\Other Map"

    with pytest.raises(ConfigurationError, match="does not map") as captured:
        ServerConfig.from_env(valid_environment)
    assert captured.value.field == "ENFUSION_PROJECT_ENGINE_PATH"


def test_project_engine_path_rejects_unc_and_device_paths(
    valid_environment: dict[str, str],
) -> None:
    valid_environment["ENFUSION_PROJECT_ENGINE_PATH"] = r"\\?\C:\users\steamuser\Моя карта"

    with pytest.raises(ConfigurationError, match="device paths are forbidden"):
        ServerConfig.from_env(valid_environment)


def test_broken_dosdevice_mapping_fails_closed(valid_environment: dict[str, str]) -> None:
    prefix = Path(valid_environment["ENFUSION_PROTON_PREFIX"])
    mapping = prefix / "dosdevices" / "c:"
    mapping.unlink()
    mapping.symlink_to(prefix / "missing-drive", target_is_directory=True)

    with pytest.raises(ConfigurationError, match="broken dosdevices mapping"):
        ServerConfig.from_env(valid_environment)


def test_state_directory_cannot_be_inside_proton_prefix_or_project(
    valid_environment: dict[str, str],
) -> None:
    prefix_state = Path(valid_environment["ENFUSION_PROTON_PREFIX"]) / "state"
    prefix_state.mkdir()
    valid_environment["ENFUSION_STATE_DIR"] = os.fspath(prefix_state)
    with pytest.raises(ConfigurationError, match="outside the Proton prefix"):
        ServerConfig.from_env(valid_environment)

    project_state = Path(valid_environment["ENFUSION_PROJECT_HOST_PATH"]) / ".state"
    project_state.mkdir()
    valid_environment["ENFUSION_STATE_DIR"] = os.fspath(project_state)
    with pytest.raises(ConfigurationError, match="outside the Proton prefix"):
        ServerConfig.from_env(valid_environment)


def test_from_env_without_argument_reads_process_environment(
    valid_environment: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in REQUIRED_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    for name, value in valid_environment.items():
        monkeypatch.setenv(name, value)

    assert ServerConfig.from_env().allowed_world == ResourceName("$myaddon:world.ent")


def test_client_or_agent_identity_is_not_required(valid_environment: dict[str, str]) -> None:
    assert not any("AGENT" in name or "CLIENT" in name for name in REQUIRED_ENVIRONMENT)
    assert ServerConfig.from_env(valid_environment).safe_mode is True
