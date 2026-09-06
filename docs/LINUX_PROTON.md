# Native Linux and Proton boundary

The Python process runs natively on Linux. Arma Reforger Tools/Workbench remains
a Windows executable managed manually by Steam with Proton Experimental. V1 has
no launcher code.

Configured deployment values:

```text
Steam game app ID: 1874880
Steam Tools app ID: 1874910
Workbench NET API: 127.0.0.1:5775
platform mode: native-linux-proton
project ID: thenewRJ
project GUID: 604B36AADC73C902
allowed world: $thenewRJ:rj.ent
```

The runtime requires explicit environment variables for Proton prefix, active
host project directory, its engine-facing Wine path, and shared state. No
default path silently selects a checkout.

## Four non-interchangeable path kinds

- `HostPath`: an absolute native POSIX path.
- `WinePath`: an absolute drive path resolved through `dosdevices`.
- `EnginePath`: a Windows-shaped value for engine-facing configuration,
  nominally distinct from `WinePath`.
- `ResourceName`: `$project:path` or `{16HEX}path`; never a filesystem path.

Mappings are discovered from symlinks in `<prefix>/dosdevices`. Host-to-Wine
chooses the longest matching canonical root. Wine-to-host strictly parses the
drive, resolves the real symlink, and proves project containment. Unknown or
broken drives, UNC/device paths, NUL, traversal, mixed separators, symlink
escapes, and project-root escapes are rejected.

Example only:

```text
/home/user/My Map/file.c
Z:\home\user\My Map\file.c
```

Additional Steam drive letters are supported; `C:` and `Z:` are not hardcoded
as the only possibilities.

## Development boundary

Checkpoint A–C tests create synthetic prefixes and `dosdevices` symlinks under
pytest temporary directories. They do not read the configured real Proton
prefix, either copy of the map, Workbench executable, or port 5775. Real mapping
inspection belongs only to the later permissioned installation/live stage.
