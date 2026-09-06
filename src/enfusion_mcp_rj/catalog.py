"""Versioned exact vegetation prefab allowlist.

The reference repository contains no vegetation prefab whose production
validity can be established without reading the active project or a permitted
live Workbench. The initial production catalog is therefore intentionally empty
and fail-closed. Synthetic catalogs are constructed explicitly in tests.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .models import CatalogEntry, VegetationCatalogOutput
from .path_types import ResourceName

CATALOG_SCHEMA_VERSION = 1
CATALOG_VERSION = "rj-vegetation-v1-unverified"


def _canonical_catalog_bytes(version: str, entries: tuple[CatalogEntry, ...]) -> bytes:
    payload = {
        "entries": [entry.model_dump(mode="json") for entry in entries],
        "schema_version": CATALOG_SCHEMA_VERSION,
        "version": version,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


@dataclass(frozen=True, slots=True)
class VegetationCatalog:
    """Immutable catalog with exact, nominally validated resource names."""

    version: str
    entries: tuple[CatalogEntry, ...]
    production_ready: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(self.entries))
        resources = [ResourceName(entry.resource_name).value for entry in self.entries]
        if len(resources) != len(set(resources)):
            raise ValueError("catalog resource names must be unique")
        if self.production_ready and not self.entries:
            raise ValueError("a production-ready catalog cannot be empty")

    @property
    def catalog_hash(self) -> str:
        return hashlib.sha256(_canonical_catalog_bytes(self.version, self.entries)).hexdigest()

    def contains(self, resource_name: str) -> bool:
        """Return exact membership; no substring, glob, or normalization."""
        try:
            validated = ResourceName(resource_name)
        except (TypeError, ValueError):
            return False
        return any(entry.resource_name == validated.value for entry in self.entries)

    def as_output(self) -> VegetationCatalogOutput:
        warnings = []
        if not self.production_ready:
            warnings.append(
                "PRODUCTION_CATALOG_UNVERIFIED: no vegetation prefab can be proven from the "
                "read-only reference; planning and apply fail closed"
            )
        return VegetationCatalogOutput(
            ok=True,
            version=self.version,
            catalog_hash=self.catalog_hash,
            production_ready=self.production_ready,
            entries=list(self.entries),
            warnings=warnings,
        )


PRODUCTION_CATALOG = VegetationCatalog(
    version=CATALOG_VERSION,
    entries=(),
    production_ready=False,
)

__all__ = [
    "CATALOG_SCHEMA_VERSION",
    "CATALOG_VERSION",
    "PRODUCTION_CATALOG",
    "VegetationCatalog",
]
