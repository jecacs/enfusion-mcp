from __future__ import annotations

import pytest
from pydantic import ValidationError

from enfusion_mcp.catalog import PRODUCTION_CATALOG, VegetationCatalog
from enfusion_mcp.models import CatalogEntry


def test_production_catalog_is_explicitly_fail_closed() -> None:
    output = PRODUCTION_CATALOG.as_output()
    assert output.ok is True
    assert output.production_ready is False
    assert output.entries == []
    assert len(output.catalog_hash) == 64
    assert "UNVERIFIED" in output.warnings[0]


def test_catalog_membership_is_exact() -> None:
    resource = "{0000000000000001}Prefabs/Synthetic/Bush.et"
    catalog = VegetationCatalog(
        version="synthetic-v1",
        production_ready=True,
        entries=(
            CatalogEntry(resource_name=resource, label="Synthetic bush", provenance="unit test"),
        ),
    )
    assert catalog.contains(resource)
    assert not catalog.contains(resource.lower())
    assert not catalog.contains("Bush.et")
    assert not catalog.contains(resource + ".bak")


def test_catalog_hash_is_order_sensitive_and_stable() -> None:
    first = CatalogEntry(
        resource_name="{0000000000000001}Prefabs/Synthetic/A.et",
        label="A",
        provenance="unit",
    )
    second = CatalogEntry(
        resource_name="{0000000000000002}Prefabs/Synthetic/B.et",
        label="B",
        provenance="unit",
    )
    one = VegetationCatalog("v1", (first, second), True)
    two = VegetationCatalog("v1", (first, second), True)
    reversed_catalog = VegetationCatalog("v1", (second, first), True)
    assert one.catalog_hash == two.catalog_hash
    assert one.catalog_hash != reversed_catalog.catalog_hash


def test_catalog_entries_are_frozen_after_hashing() -> None:
    entry = CatalogEntry(
        resource_name="{0000000000000001}Prefabs/Synthetic/A.et",
        label="A",
        provenance="unit",
    )
    catalog = VegetationCatalog("v1", (entry,), True)
    before = catalog.catalog_hash
    with pytest.raises(ValidationError):
        entry.resource_name = "{0000000000000002}Prefabs/Synthetic/B.et"
    assert catalog.catalog_hash == before


def test_catalog_copies_runtime_list_into_an_immutable_tuple() -> None:
    first = CatalogEntry(
        resource_name="{0000000000000001}Prefabs/Synthetic/A.et",
        label="A",
        provenance="unit",
    )
    second = CatalogEntry(
        resource_name="{0000000000000002}Prefabs/Synthetic/B.et",
        label="B",
        provenance="unit",
    )
    mutable_entries = [first]
    catalog = VegetationCatalog("v1", mutable_entries, True)  # type: ignore[arg-type]
    before = catalog.catalog_hash
    mutable_entries.append(second)
    assert catalog.entries == (first,)
    assert catalog.catalog_hash == before
