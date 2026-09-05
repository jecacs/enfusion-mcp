from __future__ import annotations

import os
from pathlib import Path

import pytest

from enfusion_mcp_rj.path_types import (
    EnginePath,
    HostPath,
    PathContainmentError,
    PathMappingError,
    PathValidationError,
    ProtonPathMapper,
    ResourceName,
    WinePath,
)


def _make_prefix(tmp_path: Path, mappings: dict[str, Path]) -> Path:
    prefix = tmp_path / "prefix"
    dosdevices = prefix / "dosdevices"
    dosdevices.mkdir(parents=True)
    for drive, target in mappings.items():
        target.mkdir(parents=True, exist_ok=True)
        (dosdevices / f"{drive.lower()}:").symlink_to(target, target_is_directory=True)
    return prefix


def test_nominal_path_types_are_frozen_and_not_interchangeable(tmp_path: Path) -> None:
    root = tmp_path / "drive_c"
    project = root / "project"
    project.mkdir(parents=True)
    prefix = _make_prefix(tmp_path, {"C": root})
    mapper = ProtonPathMapper(HostPath(os.fspath(prefix)), HostPath(os.fspath(project)))

    with pytest.raises(TypeError, match="HostPath"):
        mapper.host_to_wine(WinePath(r"C:\project"))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="WinePath"):
        mapper.wine_to_host(EnginePath(r"C:\project"))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="plain str"):
        HostPath(WinePath(r"C:\project"))  # type: ignore[arg-type]
    with pytest.raises(AttributeError):
        mapper.proton_prefix = HostPath("/different")  # type: ignore[misc]


@pytest.mark.parametrize(
    "value",
    [
        "relative/path",
        r"C:\project\file.c",
        "/safe/../escape",
        "/contains\\windows",
        "/tmp/nul\x00tail",
    ],
)
def test_host_path_rejects_wrong_domain_and_unsafe_syntax(value: str) -> None:
    with pytest.raises(PathValidationError):
        HostPath(value)


@pytest.mark.parametrize("wrapper", [WinePath, EnginePath])
@pytest.mark.parametrize(
    "value",
    [
        r"\\server\share\file.c",
        r"\\?\C:\project\file.c",
        r"\\.\C:\project\file.c",
        r"C:\project\..\escape",
        "C:\\project\\nul\x00tail",
        "C:/mixed/separators",
        r"C:relative",
        r"C:\project\\empty",
    ],
)
def test_windows_shaped_types_reject_unc_device_traversal_and_nul(
    wrapper: type[WinePath] | type[EnginePath],
    value: str,
) -> None:
    with pytest.raises(PathValidationError):
        wrapper(value)


def test_windows_shaped_types_preserve_spaces_unicode_and_normalize_drive() -> None:
    wine = WinePath("c:\\users\\steamuser\\Моя карта\\big tree.c")
    engine = EnginePath("z:\\Проекты\\new rj")

    assert wine.value == "C:\\users\\steamuser\\Моя карта\\big tree.c"
    assert wine.drive == "C"
    assert wine.parts == ("users", "steamuser", "Моя карта", "big tree.c")
    assert engine.value == "Z:\\Проекты\\new rj"


@pytest.mark.parametrize(
    "value",
    [
        "$thenewRJ:rj.ent",
        "$thenewRJ:Prefabs/Vegetation/Куст 01.et",
        "{604B36AADC73C902}Prefabs/Vegetation/Bush.et",
    ],
)
def test_resource_name_accepts_engine_resource_syntax(value: str) -> None:
    assert ResourceName(value).value == value


@pytest.mark.parametrize(
    "value",
    [
        "/project/rj.ent",
        r"C:\project\rj.ent",
        "$thenewRJ:../rj.ent",
        "$thenewRJ:/rj.ent",
        "$thenewRJ:folder//rj.ent",
        "$thenewRJ:rj.ent\x00suffix",
        "not-a-resource.ent",
    ],
)
def test_resource_name_rejects_os_paths_and_unsafe_resource_paths(value: str) -> None:
    with pytest.raises(PathValidationError):
        ResourceName(value)


def test_c_and_z_mappings_round_trip_with_spaces_and_cyrillic(tmp_path: Path) -> None:
    host_root = tmp_path / "host root"
    drive_c = host_root / "prefix data" / "drive_c"
    project = drive_c / "users" / "steamuser" / "Моя карта"
    asset = project / "куст большой.c"
    z_only_asset = host_root / "Общие файлы" / "z asset.c"
    asset.parent.mkdir(parents=True)
    asset.touch()
    z_only_asset.parent.mkdir(parents=True)
    z_only_asset.touch()
    prefix = _make_prefix(tmp_path, {"Z": host_root, "C": drive_c})

    mapper = ProtonPathMapper(HostPath(os.fspath(prefix)), HostPath(os.fspath(project)))
    wine = mapper.host_to_wine(HostPath(os.fspath(asset)))

    assert wine == WinePath(r"C:\users\steamuser\Моя карта\куст большой.c")
    assert mapper.wine_to_host(wine) == HostPath(os.fspath(asset))
    assert {mapping.drive for mapping in mapper.mappings} == {"C", "Z"}

    unscoped_mapper = ProtonPathMapper(HostPath(os.fspath(prefix)))
    z_wine = unscoped_mapper.host_to_wine(HostPath(os.fspath(z_only_asset)))
    assert z_wine == WinePath(r"Z:\Общие файлы\z asset.c")
    assert unscoped_mapper.wine_to_host(z_wine) == HostPath(os.fspath(z_only_asset))


def test_most_specific_additional_steam_drive_mapping_wins(tmp_path: Path) -> None:
    host_root = tmp_path / "host"
    steam_library = host_root / "media" / "Steam Library"
    project = steam_library / "steamapps" / "compatdata" / "project"
    asset = project / "Дерево.et"
    asset.parent.mkdir(parents=True)
    asset.touch()
    prefix = _make_prefix(tmp_path, {"Z": host_root, "S": steam_library})

    mapper = ProtonPathMapper(HostPath(os.fspath(prefix)), HostPath(os.fspath(project)))

    assert mapper.host_to_wine(HostPath(os.fspath(asset))) == WinePath(
        r"S:\steamapps\compatdata\project\Дерево.et"
    )


def test_reverse_mapping_rejects_unknown_drive(tmp_path: Path) -> None:
    drive_c = tmp_path / "drive_c"
    project = drive_c / "project"
    project.mkdir(parents=True)
    prefix = _make_prefix(tmp_path, {"C": drive_c})
    mapper = ProtonPathMapper(HostPath(os.fspath(prefix)), HostPath(os.fspath(project)))

    with pytest.raises(PathMappingError, match="unknown Wine drive"):
        mapper.wine_to_host(WinePath(r"X:\project\file.c"))


def test_host_and_reverse_mapping_reject_project_root_escape(tmp_path: Path) -> None:
    drive_c = tmp_path / "drive_c"
    project = drive_c / "project"
    outside = drive_c / "another-project"
    project.mkdir(parents=True)
    outside.mkdir()
    prefix = _make_prefix(tmp_path, {"C": drive_c})
    mapper = ProtonPathMapper(HostPath(os.fspath(prefix)), HostPath(os.fspath(project)))

    with pytest.raises(PathContainmentError, match="escapes project root"):
        mapper.host_to_wine(HostPath(os.fspath(outside)))
    with pytest.raises(PathContainmentError, match="escapes project root"):
        mapper.wine_to_host(WinePath(r"C:\another-project\file.c"))


def test_symlink_escape_is_rejected_in_both_directions(tmp_path: Path) -> None:
    drive_c = tmp_path / "drive_c"
    project = drive_c / "project"
    outside = drive_c / "outside"
    project.mkdir(parents=True)
    outside.mkdir()
    (project / "escape").symlink_to(outside, target_is_directory=True)
    prefix = _make_prefix(tmp_path, {"C": drive_c})
    mapper = ProtonPathMapper(HostPath(os.fspath(prefix)), HostPath(os.fspath(project)))

    with pytest.raises(PathContainmentError, match="escapes project root"):
        mapper.host_to_wine(HostPath(os.fspath(project / "escape" / "file.c")))
    with pytest.raises(PathContainmentError, match="escapes project root"):
        mapper.wine_to_host(WinePath(r"C:\project\escape\file.c"))


def test_broken_and_non_symlink_dosdevices_mappings_are_rejected(tmp_path: Path) -> None:
    prefix = tmp_path / "prefix"
    dosdevices = prefix / "dosdevices"
    dosdevices.mkdir(parents=True)
    (dosdevices / "c:").symlink_to(tmp_path / "does-not-exist", target_is_directory=True)

    with pytest.raises(PathMappingError, match="broken dosdevices mapping"):
        ProtonPathMapper(HostPath(os.fspath(prefix)))

    (dosdevices / "c:").unlink()
    (dosdevices / "c:").mkdir()
    with pytest.raises(PathMappingError, match="not a symlink"):
        ProtonPathMapper(HostPath(os.fspath(prefix)))


def test_mapping_rejects_missing_dosdevices_and_unmapped_project(tmp_path: Path) -> None:
    empty_prefix = tmp_path / "empty-prefix"
    empty_prefix.mkdir()
    with pytest.raises(PathMappingError, match="missing Proton dosdevices"):
        ProtonPathMapper(HostPath(os.fspath(empty_prefix)))

    mapped = tmp_path / "mapped"
    outside_project = tmp_path / "outside-project"
    outside_project.mkdir()
    prefix = _make_prefix(tmp_path, {"C": mapped})
    with pytest.raises(PathMappingError, match="not reachable"):
        ProtonPathMapper(
            HostPath(os.fspath(prefix)),
            HostPath(os.fspath(outside_project)),
        )


def test_resource_and_engine_paths_are_never_implicitly_converted(tmp_path: Path) -> None:
    drive_c = tmp_path / "drive_c"
    project = drive_c / "project"
    project.mkdir(parents=True)
    prefix = _make_prefix(tmp_path, {"C": drive_c})
    mapper = ProtonPathMapper(HostPath(os.fspath(prefix)), HostPath(os.fspath(project)))

    with pytest.raises(TypeError, match="HostPath"):
        mapper.host_to_wine(ResourceName("$thenewRJ:rj.ent"))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="WinePath"):
        mapper.wine_to_host(ResourceName("$thenewRJ:rj.ent"))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="WinePath"):
        mapper.wine_to_host(EnginePath(r"C:\project"))  # type: ignore[arg-type]


def test_unscoped_mapper_can_map_a_windows_style_drive_root(tmp_path: Path) -> None:
    """Library-level mapping remains useful without a Linux project boundary."""

    drive_d = tmp_path / "windows-volume"
    target = drive_d / "Workspace" / "Map"
    target.mkdir(parents=True)
    prefix = _make_prefix(tmp_path, {"D": drive_d})
    mapper = ProtonPathMapper(HostPath(os.fspath(prefix)))

    wine = mapper.host_to_wine(HostPath(os.fspath(target)))

    assert wine == WinePath(r"D:\Workspace\Map")
    assert mapper.wine_to_host(wine) == HostPath(os.fspath(target))
