"""Fail-closed runtime configuration for the safe Linux/Proton profile."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast

from enfusion_mcp.path_types import (
    EnginePath,
    HostPath,
    PathMappingError,
    PathValidationError,
    ProtonPathMapper,
    ResourceName,
)

PLATFORM_MODE: Final = "native-linux-proton"
STEAM_TOOLS_APP_ID: Final = "1874910"
WORKBENCH_HOST: Final = "127.0.0.1"
WORKBENCH_PORT: Final = "5775"
SAFE_MODE: Final = "1"

REQUIRED_ENVIRONMENT: Final[tuple[str, ...]] = (
    "ENFUSION_PLATFORM_MODE",
    "ENFUSION_STEAM_TOOLS_APP_ID",
    "ENFUSION_PROTON_PREFIX",
    "ENFUSION_WORKBENCH_HOST",
    "ENFUSION_WORKBENCH_PORT",
    "ENFUSION_PROJECT_HOST_PATH",
    "ENFUSION_PROJECT_ENGINE_PATH",
    "ENFUSION_ALLOWED_WORLD",
    "ENFUSION_SAFE_MODE",
    "ENFUSION_STATE_DIR",
)


class ConfigurationError(ValueError):
    """The safe runtime cannot start because configuration is invalid."""

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"invalid configuration {field}: {reason}")


def _required_values(environ: Mapping[str, str]) -> dict[str, str]:
    supported = frozenset(REQUIRED_ENVIRONMENT)
    unknown = sorted(
        name
        for name in environ
        if type(name) is str and name.startswith("ENFUSION_") and name not in supported
    )
    if unknown:
        raise ConfigurationError(
            ",".join(unknown),
            "unknown ENFUSION_ environment variable",
        )
    missing = [name for name in REQUIRED_ENVIRONMENT if name not in environ]
    if missing:
        raise ConfigurationError(
            ",".join(missing),
            "required environment variable is missing",
        )

    values: dict[str, str] = {}
    for name in REQUIRED_ENVIRONMENT:
        value = environ[name]
        if type(value) is not str:
            raise ConfigurationError(name, "value must be a string")
        if not value:
            raise ConfigurationError(name, "value must not be empty")
        values[name] = value
    return values


def _require_exact(values: Mapping[str, str], field: str, expected: str) -> None:
    actual = values[field]
    if actual != expected:
        raise ConfigurationError(field, f"safe profile requires exactly {expected!r}")


def _canonical_directory(raw: str, field: str) -> HostPath:
    try:
        wrapped = HostPath(raw)
        canonical = wrapped.resolved(strict=True)
    except (PathValidationError, OSError) as error:
        raise ConfigurationError(field, str(error)) from error
    if wrapped.value != canonical.value:
        raise ConfigurationError(field, f"path must be canonical ({canonical.value})")
    if not canonical.path.is_dir():
        raise ConfigurationError(field, "path must identify an existing directory")
    return canonical


def _canonical_state_directory(raw: str) -> HostPath:
    field = "ENFUSION_STATE_DIR"
    try:
        wrapped = HostPath(raw)
        parent = wrapped.path.parent.resolve(strict=True)
    except (OSError, PathValidationError, RuntimeError) as error:
        raise ConfigurationError(
            field, f"state directory parent cannot be resolved: {error}"
        ) from error
    if not parent.is_dir():
        raise ConfigurationError(field, "state directory parent must be an existing directory")

    canonical_path = parent / wrapped.path.name
    if canonical_path.parent == canonical_path:
        raise ConfigurationError(field, "filesystem root cannot be used as the state directory")
    if wrapped.value != os.fspath(canonical_path):
        raise ConfigurationError(field, f"path must be canonical ({canonical_path})")

    # The durable-ledger layer creates a missing leaf atomically with mode 0700.
    # If a leaf already exists, reject aliases, broken links, and non-directories
    # here before any database code sees the location.
    if wrapped.path.is_symlink():
        try:
            resolved = wrapped.path.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ConfigurationError(
                field, f"state directory symlink is invalid: {error}"
            ) from error
        raise ConfigurationError(field, f"path must be canonical ({resolved})")
    if wrapped.path.exists() and not wrapped.path.is_dir():
        raise ConfigurationError(field, "path must identify a directory or an unused leaf")
    return HostPath(os.fspath(canonical_path))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """Validated settings for the only V1 runtime profile.

    Both direct construction and ``from_env`` run the same fail-closed runtime
    invariants. ``from_env`` is the supported external configuration boundary.
    The target world is explicitly configured; no map is built into this profile.
    """

    platform_mode: Literal["native-linux-proton"]
    steam_tools_app_id: Literal[1874910]
    proton_prefix: HostPath
    workbench_host: Literal["127.0.0.1"]
    workbench_port: Literal[5775]
    project_host_path: HostPath
    project_engine_path: EnginePath
    allowed_world: ResourceName
    safe_mode: Literal[True]
    state_dir: HostPath

    def __post_init__(self) -> None:  # noqa: PLR0912 - ordered fail-closed checks
        exact_values: tuple[tuple[str, object, object, type[object]], ...] = (
            ("platform_mode", self.platform_mode, PLATFORM_MODE, str),
            ("steam_tools_app_id", self.steam_tools_app_id, int(STEAM_TOOLS_APP_ID), int),
            ("workbench_host", self.workbench_host, WORKBENCH_HOST, str),
            ("workbench_port", self.workbench_port, int(WORKBENCH_PORT), int),
            ("safe_mode", self.safe_mode, True, bool),
        )
        for field_name, actual, expected, expected_type in exact_values:
            if type(actual) is not expected_type or actual != expected:
                raise ConfigurationError(field_name, f"safe profile requires exactly {expected!r}")
        if type(self.proton_prefix) is not HostPath:
            raise ConfigurationError("proton_prefix", "must be a HostPath")
        if type(self.project_host_path) is not HostPath:
            raise ConfigurationError("project_host_path", "must be a HostPath")
        if type(self.project_engine_path) is not EnginePath:
            raise ConfigurationError("project_engine_path", "must be an EnginePath")
        if type(self.allowed_world) is not ResourceName:
            raise ConfigurationError("allowed_world", "must be a ResourceName")
        if type(self.state_dir) is not HostPath:
            raise ConfigurationError("state_dir", "must be a HostPath")

        proton_prefix = _canonical_directory(self.proton_prefix.value, "proton_prefix")
        project_host_path = _canonical_directory(
            self.project_host_path.value,
            "project_host_path",
        )
        state_dir = _canonical_state_directory(self.state_dir.value)
        if proton_prefix != self.proton_prefix or project_host_path != self.project_host_path:
            raise ConfigurationError("ServerConfig", "host paths must be canonical")
        if state_dir != self.state_dir:
            raise ConfigurationError("state_dir", "path must be canonical")
        if _is_relative_to(state_dir.path, proton_prefix.path):
            raise ConfigurationError("state_dir", "must be outside the Proton prefix")
        if _is_relative_to(state_dir.path, project_host_path.path):
            raise ConfigurationError("state_dir", "must be outside the active project")
        try:
            mapped = ProtonPathMapper(
                proton_prefix,
                project_root=project_host_path,
            ).host_to_wine(project_host_path)
        except (PathMappingError, PathValidationError, TypeError) as error:
            raise ConfigurationError("proton_prefix", str(error)) from error
        if mapped.value != self.project_engine_path.value:
            raise ConfigurationError(
                "project_engine_path",
                "does not map to project_host_path through Proton dosdevices",
            )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ServerConfig:
        """Load and strictly validate configuration without applying defaults."""

        source: Mapping[str, str] = os.environ if environ is None else environ
        values = _required_values(source)

        _require_exact(values, "ENFUSION_PLATFORM_MODE", PLATFORM_MODE)
        _require_exact(values, "ENFUSION_STEAM_TOOLS_APP_ID", STEAM_TOOLS_APP_ID)
        _require_exact(values, "ENFUSION_WORKBENCH_HOST", WORKBENCH_HOST)
        _require_exact(values, "ENFUSION_WORKBENCH_PORT", WORKBENCH_PORT)
        _require_exact(values, "ENFUSION_SAFE_MODE", SAFE_MODE)

        proton_prefix = _canonical_directory(
            values["ENFUSION_PROTON_PREFIX"],
            "ENFUSION_PROTON_PREFIX",
        )
        project_host_path = _canonical_directory(
            values["ENFUSION_PROJECT_HOST_PATH"],
            "ENFUSION_PROJECT_HOST_PATH",
        )
        state_dir = _canonical_state_directory(values["ENFUSION_STATE_DIR"])

        try:
            project_engine_path = EnginePath(values["ENFUSION_PROJECT_ENGINE_PATH"])
        except (PathValidationError, TypeError) as error:
            raise ConfigurationError("ENFUSION_PROJECT_ENGINE_PATH", str(error)) from error
        try:
            allowed_world = ResourceName(values["ENFUSION_ALLOWED_WORLD"])
        except (PathValidationError, TypeError) as error:
            raise ConfigurationError("ENFUSION_ALLOWED_WORLD", str(error)) from error

        if _is_relative_to(state_dir.path, proton_prefix.path):
            raise ConfigurationError(
                "ENFUSION_STATE_DIR",
                "state directory must be outside the Proton prefix",
            )
        if _is_relative_to(state_dir.path, project_host_path.path):
            raise ConfigurationError(
                "ENFUSION_STATE_DIR",
                "state directory must be outside the active project",
            )

        try:
            mapper = ProtonPathMapper(proton_prefix, project_root=project_host_path)
            mapped_project = mapper.host_to_wine(project_host_path)
        except (PathMappingError, PathValidationError, TypeError) as error:
            raise ConfigurationError("ENFUSION_PROTON_PREFIX", str(error)) from error
        if mapped_project.value != project_engine_path.value:
            raise ConfigurationError(
                "ENFUSION_PROJECT_ENGINE_PATH",
                "does not map to ENFUSION_PROJECT_HOST_PATH through Proton dosdevices "
                f"(expected {mapped_project.value!r})",
            )

        return cls(
            platform_mode=PLATFORM_MODE,
            steam_tools_app_id=cast("Literal[1874910]", int(STEAM_TOOLS_APP_ID)),
            proton_prefix=proton_prefix,
            workbench_host=WORKBENCH_HOST,
            workbench_port=cast("Literal[5775]", int(WORKBENCH_PORT)),
            project_host_path=project_host_path,
            project_engine_path=project_engine_path,
            allowed_world=allowed_world,
            safe_mode=True,
            state_dir=state_dir,
        )

    @property
    def workbench_address(self) -> tuple[str, int]:
        """Return the fixed loopback NET API target."""

        return (self.workbench_host, self.workbench_port)


# A descriptive compatibility alias for callers that prefer an application-
# neutral settings name.  ``ServerConfig`` remains the canonical API name.
Settings = ServerConfig


__all__ = [
    "PLATFORM_MODE",
    "REQUIRED_ENVIRONMENT",
    "SAFE_MODE",
    "STEAM_TOOLS_APP_ID",
    "WORKBENCH_HOST",
    "WORKBENCH_PORT",
    "ConfigurationError",
    "ServerConfig",
    "Settings",
]
