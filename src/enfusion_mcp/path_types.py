"""Nominal path types and safe Proton ``dosdevices`` path mapping.

The types in this module deliberately do not inherit from :class:`str` or
``os.PathLike`` (except that ``HostPath`` implements ``__fspath__``).  A
resource name is not a filesystem path, and an engine-facing Windows path is
not accepted by APIs which require a Wine path without an explicit conversion.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

_DRIVE_ENTRY_RE = re.compile(r"(?P<drive>[A-Za-z]):\Z")
_WINDOWS_ABSOLUTE_RE = re.compile(r"(?P<drive>[A-Za-z]):\\(?P<tail>.*)\Z")
_RESOURCE_ALIAS_RE = re.compile(r"\$(?P<project>[A-Za-z_][A-Za-z0-9_]*):(?P<path>.+)\Z")
_RESOURCE_GUID_RE = re.compile(r"\{(?P<guid>[0-9A-Fa-f]{16})\}(?P<path>.+)\Z")


class PathValidationError(ValueError):
    """A value does not satisfy the syntax or containment rules of its type."""


class PathMappingError(PathValidationError):
    """A host/Wine path cannot be mapped through the configured Proton prefix."""


class PathContainmentError(PathMappingError):
    """A canonical path escapes its mapping root or configured project root."""


def _require_plain_string(value: object, type_name: str) -> str:
    # Exact type checking is intentional.  It prevents accidental coercion of
    # another nominal wrapper whose ``str()`` happens to look plausible.
    if type(value) is not str:
        raise TypeError(f"{type_name} requires a plain str")
    return value


def _reject_nul(value: str, type_name: str) -> None:
    if "\x00" in value:
        raise PathValidationError(f"{type_name} must not contain NUL")


def _validate_windows_absolute(value: object, type_name: str) -> str:
    text = _require_plain_string(value, type_name)
    _reject_nul(text, type_name)

    # Check device namespaces before the general UNC check so diagnostics stay
    # precise.  Both syntaxes bypass ordinary drive/path semantics on Windows.
    if text.startswith(("\\\\?\\", "\\\\.\\")):
        raise PathValidationError(f"{type_name} device paths are forbidden")
    if text.startswith("\\\\"):
        raise PathValidationError(f"{type_name} UNC paths are forbidden")
    if "/" in text:
        raise PathValidationError(f"{type_name} must use Windows separators only")

    match = _WINDOWS_ABSOLUTE_RE.fullmatch(text)
    if match is None:
        raise PathValidationError(f"{type_name} must be an absolute drive path such as C:\\path")

    tail = match.group("tail")
    parts = () if not tail else tuple(tail.split("\\"))
    if any(not part for part in parts):
        raise PathValidationError(f"{type_name} contains an empty path component")
    if any(part in {".", ".."} for part in parts):
        raise PathValidationError(f"{type_name} traversal is forbidden")
    if any(":" in part for part in parts):
        raise PathValidationError(f"{type_name} contains a reserved colon")

    return f"{match.group('drive').upper()}:\\{tail}"


def _validate_resource_path(path: str) -> None:
    if path.startswith("/") or "\\" in path:
        raise PathValidationError("ResourceName must use a relative engine resource path")
    parts = tuple(path.split("/"))
    if any(not part for part in parts):
        raise PathValidationError("ResourceName contains an empty path component")
    if any(part in {".", ".."} for part in parts):
        raise PathValidationError("ResourceName traversal is forbidden")
    if any(":" in part for part in parts):
        raise PathValidationError("ResourceName contains an unexpected colon")


@dataclass(frozen=True, slots=True)
class HostPath:
    """An absolute native POSIX host path.

    Construction validates syntax but does not access the filesystem.  Call
    :meth:`resolved` or use :class:`ProtonPathMapper` when canonical filesystem
    resolution is required.
    """

    value: str

    def __post_init__(self) -> None:
        text = _require_plain_string(self.value, type(self).__name__)
        _reject_nul(text, type(self).__name__)
        if "\\" in text:
            raise PathValidationError("HostPath must not contain Windows separators")
        if not text.startswith("/"):
            raise PathValidationError("HostPath must be absolute")
        if any(part == ".." for part in PurePosixPath(text).parts):
            raise PathValidationError("HostPath traversal is forbidden")

    @property
    def path(self) -> Path:
        """Return the native ``pathlib`` representation."""

        return Path(self.value)

    def resolved(self, *, strict: bool = False) -> HostPath:
        """Return a new wrapper around the canonical filesystem path."""

        try:
            resolved = self.path.resolve(strict=strict)
        except (OSError, RuntimeError) as error:
            raise PathValidationError(f"HostPath cannot be resolved: {error}") from error
        return HostPath(os.fspath(resolved))

    def __fspath__(self) -> str:
        return self.value

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class WinePath:
    """An absolute path addressed through a Wine drive mapping."""

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "value",
            _validate_windows_absolute(self.value, type(self).__name__),
        )

    @property
    def drive(self) -> str:
        return self.value[0]

    @property
    def parts(self) -> tuple[str, ...]:
        tail = self.value[3:]
        return () if not tail else tuple(tail.split("\\"))

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class EnginePath:
    """A Windows-shaped path passed to an engine API, distinct from WinePath."""

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "value",
            _validate_windows_absolute(self.value, type(self).__name__),
        )

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ResourceName:
    """An Enfusion resource identifier, never an operating-system path."""

    value: str

    def __post_init__(self) -> None:
        text = _require_plain_string(self.value, type(self).__name__)
        _reject_nul(text, type(self).__name__)
        if text.startswith(("\\\\", "/")):
            raise PathValidationError("ResourceName must not be an OS path")

        match = _RESOURCE_ALIAS_RE.fullmatch(text)
        if match is None:
            match = _RESOURCE_GUID_RE.fullmatch(text)
        if match is None:
            raise PathValidationError(
                "ResourceName must start with $project: or a 16-hex GUID in braces"
            )
        _validate_resource_path(match.group("path"))

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class DriveMapping:
    """One resolved Wine drive-letter to native directory mapping."""

    drive: str
    root: HostPath


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _canonical_directory(path: HostPath, label: str) -> Path:
    if type(path) is not HostPath:
        raise TypeError(f"{label} requires HostPath")
    try:
        canonical = path.path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise PathMappingError(f"{label} cannot be resolved: {error}") from error
    if not canonical.is_dir():
        raise PathMappingError(f"{label} is not a directory: {canonical}")
    return canonical


@dataclass(frozen=True, slots=True)
class ProtonPathMapper:
    """Resolve paths through real symlinks in ``<prefix>/dosdevices``.

    If ``project_root`` is supplied, every result is automatically constrained
    to that canonical root.  This makes the project boundary a property of the
    mapper rather than an optional flag callers could forget.
    """

    proton_prefix: HostPath
    project_root: HostPath | None = None
    _mappings: tuple[DriveMapping, ...] = field(init=False, repr=False)
    _canonical_project_root: Path | None = field(init=False, repr=False)

    def __post_init__(self) -> None:
        prefix = _canonical_directory(self.proton_prefix, "proton_prefix")
        dosdevices = prefix / "dosdevices"
        if not dosdevices.is_dir():
            raise PathMappingError(f"missing Proton dosdevices directory: {dosdevices}")

        mappings_by_drive: dict[str, DriveMapping] = {}
        try:
            entries = sorted(dosdevices.iterdir(), key=lambda item: item.name.casefold())
        except OSError as error:
            raise PathMappingError(f"cannot read Proton dosdevices: {error}") from error

        for entry in entries:
            match = _DRIVE_ENTRY_RE.fullmatch(entry.name)
            if match is None:
                continue
            drive = match.group("drive").upper()
            if drive in mappings_by_drive:
                raise PathMappingError(f"duplicate dosdevices mapping for drive {drive}:")
            if not entry.is_symlink():
                raise PathMappingError(f"dosdevices mapping {entry} is not a symlink")
            try:
                target = entry.resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise PathMappingError(f"broken dosdevices mapping {entry}: {error}") from error
            if not target.is_dir():
                raise PathMappingError(f"dosdevices mapping {entry} is not a directory")
            mappings_by_drive[drive] = DriveMapping(drive, HostPath(os.fspath(target)))

        if not mappings_by_drive:
            raise PathMappingError(f"no drive mappings found in {dosdevices}")

        # Longest canonical root wins for Host -> Wine.  The drive name is a
        # stable tie-breaker when Wine exposes aliases to the same directory.
        mappings = tuple(
            sorted(
                mappings_by_drive.values(),
                key=lambda mapping: (-len(mapping.root.path.parts), mapping.drive),
            )
        )
        object.__setattr__(self, "_mappings", mappings)

        canonical_project_root: Path | None = None
        if self.project_root is not None:
            canonical_project_root = _canonical_directory(self.project_root, "project_root")
            if not any(
                _is_relative_to(canonical_project_root, mapping.root.path) for mapping in mappings
            ):
                raise PathMappingError("project_root is not reachable through any Wine drive")
        object.__setattr__(self, "_canonical_project_root", canonical_project_root)

    @property
    def mappings(self) -> tuple[DriveMapping, ...]:
        """Return immutable, canonical drive mappings ordered by specificity."""

        return self._mappings

    def _enforce_project_containment(self, path: Path) -> None:
        if self._canonical_project_root is not None and not _is_relative_to(
            path, self._canonical_project_root
        ):
            raise PathContainmentError(
                f"canonical path escapes project root {self._canonical_project_root}: {path}"
            )

    def host_to_wine(self, host_path: HostPath) -> WinePath:
        """Map a native path using the most specific containing drive root."""

        if type(host_path) is not HostPath:
            raise TypeError("host_to_wine requires HostPath")
        try:
            canonical = host_path.path.resolve(strict=False)
        except (OSError, RuntimeError) as error:
            raise PathMappingError(f"host path cannot be resolved: {error}") from error
        self._enforce_project_containment(canonical)

        for mapping in self._mappings:
            root = mapping.root.path
            if not _is_relative_to(canonical, root):
                continue
            relative = canonical.relative_to(root)
            suffix = "\\".join(relative.parts)
            value = f"{mapping.drive}:\\{suffix}" if suffix else f"{mapping.drive}:\\"
            return WinePath(value)
        raise PathMappingError(f"host path has no Wine drive mapping: {canonical}")

    def wine_to_host(self, wine_path: WinePath) -> HostPath:
        """Resolve a Wine drive path and enforce mapping/project containment."""

        if type(wine_path) is not WinePath:
            raise TypeError("wine_to_host requires WinePath")

        mapping = next(
            (candidate for candidate in self._mappings if candidate.drive == wine_path.drive),
            None,
        )
        if mapping is None:
            raise PathMappingError(f"unknown Wine drive: {wine_path.drive}:")

        mapping_root = mapping.root.path
        try:
            canonical = mapping_root.joinpath(*wine_path.parts).resolve(strict=False)
        except (OSError, RuntimeError) as error:
            raise PathMappingError(f"Wine path cannot be resolved: {error}") from error
        if not _is_relative_to(canonical, mapping_root):
            raise PathContainmentError(
                f"Wine path escapes mapped drive root {mapping_root}: {canonical}"
            )
        self._enforce_project_containment(canonical)
        return HostPath(os.fspath(canonical))


__all__ = [
    "DriveMapping",
    "EnginePath",
    "HostPath",
    "PathContainmentError",
    "PathMappingError",
    "PathValidationError",
    "ProtonPathMapper",
    "ResourceName",
    "WinePath",
]
