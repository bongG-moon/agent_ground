from __future__ import annotations

"""Check or create the ordinary MongoDB indexes required by F00/F10.

This helper deliberately does *not* create MongoDB Atlas Search indexes.  A
vector index must use the dimension recorded by the active catalog pointer and
its exact definition depends on the Atlas deployment.  The script reports that
dimension and leaves the Atlas index creation as an explicit administrator
step.  Normal MongoDB indexes are deterministic and may be created with the
``--apply`` opt-in.
"""

import argparse
import json
import os
import re
from dataclasses import dataclass
from typing import Any

from pymongo import ASCENDING, MongoClient
from pymongo.errors import PyMongoError


DEFAULT_DATABASE = "business_work_design"
DEFAULT_TENANT_ID = "default"
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")


@dataclass(frozen=True)
class IndexRequirement:
    collection: str
    name: str
    keys: tuple[tuple[str, int], ...]
    unique: bool = False
    expire_after_seconds: int | None = None
    purpose: str = ""


REQUIRED_INDEXES: tuple[IndexRequirement, ...] = (
    IndexRequirement(
        "work_definitions",
        "uq_work_definition_identity",
        (("tenant_id", ASCENDING), ("work_definition_id", ASCENDING)),
        unique=True,
        purpose="F10 WorkDefinition revision/CAS identity",
    ),
    IndexRequirement(
        "clarification_batches",
        "uq_clarification_batch_identity",
        (("tenant_id", ASCENDING), ("batch_id", ASCENDING)),
        unique=True,
        purpose="F10 native HITL question-batch idempotency",
    ),
    IndexRequirement(
        "clarification_batches",
        "ttl_clarification_batch_expires_at",
        (("expires_at", ASCENDING),),
        expire_after_seconds=0,
        purpose="expire completed/abandoned clarification batches",
    ),
    IndexRequirement(
        "work_definition_events",
        "ix_work_definition_event_timeline",
        (("tenant_id", ASCENDING), ("work_definition_id", ASCENDING), ("revision", ASCENDING)),
        purpose="F10 audit-event lookup",
    ),
    IndexRequirement(
        "work_runtime_states",
        "uq_work_runtime_identity",
        (("tenant_id", ASCENDING), ("work_definition_id", ASCENDING), ("session_id", ASCENDING)),
        unique=True,
        purpose="runtime-state idempotency",
    ),
    IndexRequirement(
        "work_runtime_events",
        "ix_work_runtime_event_timeline",
        (("tenant_id", ASCENDING), ("work_definition_id", ASCENDING), ("occurred_at", ASCENDING)),
        purpose="runtime-event lookup",
    ),
    IndexRequirement(
        "catalog_assets",
        "ix_catalog_asset_exact_title",
        (("tenant_id", ASCENDING), ("snapshot_id", ASCENDING), ("title_normalized", ASCENDING)),
        purpose="F20 exact catalog title lookup within the active snapshot",
    ),
    IndexRequirement(
        "catalog_assets",
        "ix_catalog_asset_exact_alias",
        (("tenant_id", ASCENDING), ("snapshot_id", ASCENDING), ("aliases_normalized", ASCENDING)),
        purpose="F20 exact catalog alias lookup within the active snapshot",
    ),
    IndexRequirement(
        "catalog_asset_chunks",
        "ix_catalog_chunk_snapshot_asset_type",
        (("tenant_id", ASCENDING), ("snapshot_id", ASCENDING), ("asset_type", ASCENDING)),
        purpose="F20/F90 portable keyword fallback scope",
    ),
)


def _safe_identifier(value: str, label: str) -> str:
    candidate = str(value or "").strip()
    if not IDENTIFIER_PATTERN.fullmatch(candidate):
        raise ValueError(f"{label} must be a safe MongoDB identifier.")
    return candidate


def _transaction_capability(hello: dict[str, Any]) -> tuple[bool, str]:
    """Return whether F10's required MongoDB transactions can be used."""

    if hello.get("msg") == "isdbgrid":
        return True, "mongos"
    replica_set = str(hello.get("setName") or "").strip()
    if replica_set:
        return True, f"replica_set:{replica_set}"
    return False, "standalone"


def _index_key_tuple(value: Any) -> tuple[tuple[str, int], ...]:
    if hasattr(value, "items"):
        pairs = value.items()
    elif isinstance(value, list):
        pairs = value
    else:
        return ()
    normalized: list[tuple[str, int]] = []
    for key, direction in pairs:
        try:
            normalized.append((str(key), int(direction)))
        except (TypeError, ValueError):
            return ()
    return tuple(normalized)


def _index_status(existing: dict[str, Any] | None, requirement: IndexRequirement) -> str:
    if not isinstance(existing, dict):
        return "missing"
    if _index_key_tuple(existing.get("key")) != requirement.keys:
        return "conflict"
    if bool(existing.get("unique", False)) != requirement.unique:
        return "conflict"
    actual_ttl = existing.get("expireAfterSeconds")
    if requirement.expire_after_seconds is None:
        if actual_ttl is not None:
            return "conflict"
    else:
        try:
            if int(actual_ttl) != requirement.expire_after_seconds:
                return "conflict"
        except (TypeError, ValueError):
            return "conflict"
    return "ready"


def _index_options(requirement: IndexRequirement) -> dict[str, Any]:
    options: dict[str, Any] = {"name": requirement.name}
    if requirement.unique:
        options["unique"] = True
    if requirement.expire_after_seconds is not None:
        options["expireAfterSeconds"] = requirement.expire_after_seconds
    return options


def _atlas_search_guidance(pointer: dict[str, Any] | None) -> dict[str, Any]:
    contract = pointer.get("embedding_contract") if isinstance(pointer, dict) else {}
    dimension = contract.get("dimension") if isinstance(contract, dict) else None
    return {
        "required": [
            {"collection": "catalog_asset_chunks", "index_name": "catalog_lexical", "purpose": "F20/F90 lexical search"},
            {
                "collection": "catalog_asset_chunks",
                "index_name": "catalog_vector",
                "purpose": "F20/F90 vector search",
                "expected_dimension": dimension,
            },
        ],
        "status": "ADMINISTRATOR_CONFIGURATION_REQUIRED",
        "reason": "Atlas Search index definitions depend on the active embedding dimension and deployment capabilities.",
    }


def _report_index_status(database: Any) -> list[dict[str, Any]]:
    by_collection: dict[str, dict[str, dict[str, Any]]] = {}
    for requirement in REQUIRED_INDEXES:
        if requirement.collection in by_collection:
            continue
        by_collection[requirement.collection] = {
            str(item.get("name") or ""): item for item in database[requirement.collection].list_indexes()
        }
    report: list[dict[str, Any]] = []
    for requirement in REQUIRED_INDEXES:
        existing = by_collection[requirement.collection].get(requirement.name)
        report.append(
            {
                "collection": requirement.collection,
                "name": requirement.name,
                "status": _index_status(existing, requirement),
                "keys": list(requirement.keys),
                "unique": requirement.unique,
                "expire_after_seconds": requirement.expire_after_seconds,
                "purpose": requirement.purpose,
            }
        )
    return report


def _apply_indexes(database: Any, report: list[dict[str, Any]]) -> list[str]:
    applied: list[str] = []
    for requirement, status in zip(REQUIRED_INDEXES, report, strict=True):
        if status["status"] == "ready":
            continue
        if status["status"] == "conflict":
            raise RuntimeError(
                f"Existing index {requirement.collection}.{requirement.name} has a conflicting definition; "
                "correct it explicitly before applying this bootstrap."
            )
        database[requirement.collection].create_index(list(requirement.keys), **_index_options(requirement))
        applied.append(f"{requirement.collection}.{requirement.name}")
    return applied


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check or explicitly create F00/F10 MongoDB prerequisites. No write occurs without --apply."
    )
    parser.add_argument("--mongodb-uri", default=os.environ.get("MONGODB_URI") or os.environ.get("MONGO_URL") or "")
    parser.add_argument("--database", default=os.environ.get("MONGODB_DATABASE", DEFAULT_DATABASE))
    parser.add_argument(
        "--tenant-id",
        default=os.environ.get("CATALOG_TENANT_ID", DEFAULT_TENANT_ID),
        help="Tenant whose active F00 pointer supplies the expected vector dimension.",
    )
    parser.add_argument("--apply", action="store_true", help="Create only missing ordinary MongoDB indexes.")
    parser.add_argument("--timeout-ms", type=int, default=5000)
    args = parser.parse_args()

    uri = str(args.mongodb_uri or "").strip()
    if not uri:
        raise ValueError("MONGODB_URI, MONGO_URL, or --mongodb-uri is required.")
    database_name = _safe_identifier(str(args.database), "database")
    tenant_id = _safe_identifier(str(args.tenant_id), "tenant_id")
    timeout = max(1000, min(int(args.timeout_ms), 30_000))
    client = MongoClient(
        uri,
        serverSelectionTimeoutMS=timeout,
        connectTimeoutMS=timeout,
        socketTimeoutMS=max(timeout, 10_000),
        retryReads=True,
        retryWrites=True,
    )
    try:
        client.admin.command("ping")
        hello = dict(client.admin.command("hello"))
        transactions_ready, topology = _transaction_capability(hello)
        database = client[database_name]
        report = _report_index_status(database)
        applied: list[str] = []
        if args.apply:
            applied = _apply_indexes(database, report)
            report = _report_index_status(database)
        # F00 publishes one active pointer whose stable MongoDB _id is the
        # tenant ID.  Using a hard-coded ``default`` hid the active embedding
        # dimension for every non-default tenant from this preflight report.
        pointer = database["catalog_active_pointers"].find_one({"_id": tenant_id})
        indexes_ready = all(item["status"] == "ready" for item in report)
        result = {
            "ok": transactions_ready and indexes_ready,
            "status": "READY" if transactions_ready and indexes_ready else "ACTION_REQUIRED",
            "database": database_name,
            "tenant_id": tenant_id,
            "transaction_support": {"ready": transactions_ready, "topology": topology},
            "ordinary_indexes": report,
            "applied": applied,
            "atlas_search_indexes": _atlas_search_guidance(pointer if isinstance(pointer, dict) else None),
            "langflow_secret": {
                "required_global_variable": "MONGO_URL",
                "note": "Set the same URI as a Secret Global Variable in Langflow; this script never writes Langflow settings.",
            },
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result["ok"] else 2
    except PyMongoError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "status": "BLOCKED",
                    "error": {"code": "MONGODB_PREFLIGHT_FAILED", "exception_type": type(exc).__name__},
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 3
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
