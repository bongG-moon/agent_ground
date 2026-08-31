from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "scripts" / "bootstrap_mongodb_prerequisites.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("test_mongodb_prerequisites_runtime", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


class FakeCollection:
    def __init__(self, indexes: list[dict[str, Any]] | None = None) -> None:
        self.indexes = list(indexes or [{"name": "_id_", "key": {"_id": 1}}])
        self.calls: list[tuple[list[tuple[str, int]], dict[str, Any]]] = []

    def list_indexes(self) -> list[dict[str, Any]]:
        return list(self.indexes)

    def create_index(self, keys: list[tuple[str, int]], **kwargs: Any) -> str:
        self.calls.append((list(keys), dict(kwargs)))
        entry: dict[str, Any] = {"name": kwargs["name"], "key": dict(keys)}
        entry.update({key: value for key, value in kwargs.items() if key != "name"})
        self.indexes.append(entry)
        return str(kwargs["name"])


class FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection())


def test_transaction_capability_requires_replica_set_or_mongos() -> None:
    module = _load()
    assert module._transaction_capability({}) == (False, "standalone")
    assert module._transaction_capability({"setName": "rs0"}) == (True, "replica_set:rs0")
    assert module._transaction_capability({"msg": "isdbgrid"}) == (True, "mongos")


def test_index_status_detects_missing_ready_and_conflicting_definitions() -> None:
    module = _load()
    requirement = module.IndexRequirement(
        "example",
        "uq_example",
        (("tenant_id", 1), ("id", 1)),
        unique=True,
    )
    assert module._index_status(None, requirement) == "missing"
    assert module._index_status(
        {"name": "uq_example", "key": {"tenant_id": 1, "id": 1}, "unique": True}, requirement
    ) == "ready"
    assert module._index_status(
        {"name": "uq_example", "key": {"tenant_id": 1, "id": -1}, "unique": True}, requirement
    ) == "conflict"


def test_apply_creates_only_missing_indexes_and_reports_ready_afterward() -> None:
    module = _load()
    database = FakeDatabase()
    report = module._report_index_status(database)
    assert all(item["status"] == "missing" for item in report)
    applied = module._apply_indexes(database, report)
    assert len(applied) == len(module.REQUIRED_INDEXES)
    final_report = module._report_index_status(database)
    assert all(item["status"] == "ready" for item in final_report)
    ttl = next(item for item in final_report if item["name"] == "ttl_clarification_batch_expires_at")
    assert ttl["expire_after_seconds"] == 0


def test_atlas_search_guidance_uses_active_embedding_dimension_without_creating_indexes() -> None:
    module = _load()
    guidance = module._atlas_search_guidance({"embedding_contract": {"dimension": 1024}})
    assert guidance["status"] == "ADMINISTRATOR_CONFIGURATION_REQUIRED"
    assert guidance["required"][1]["expected_dimension"] == 1024


def test_catalog_hybrid_fallback_indexes_are_part_of_the_preflight_contract() -> None:
    module = _load()
    requirements = {item.name: item for item in module.REQUIRED_INDEXES}
    assert requirements["ix_catalog_asset_exact_title"].keys == (
        ("tenant_id", 1), ("snapshot_id", 1), ("title_normalized", 1)
    )
    assert requirements["ix_catalog_asset_exact_alias"].keys == (
        ("tenant_id", 1), ("snapshot_id", 1), ("aliases_normalized", 1)
    )
    assert requirements["ix_catalog_chunk_snapshot_asset_type"].keys == (
        ("tenant_id", 1), ("snapshot_id", 1), ("asset_type", 1)
    )
