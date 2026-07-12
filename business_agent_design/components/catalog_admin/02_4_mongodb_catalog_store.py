from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MessageTextInput, Output
from lfx.schema.data import Data

DEFAULT_DB_NAME = "business_agent_design"
DEFAULT_CAPABILITY_COLLECTION = "agent_capability_catalog"
DEFAULT_CASE_COLLECTION = "agent_improvement_cases"
DEFAULT_CATALOG_VERSION_COLLECTION = "agent_catalog_versions"


def store_catalog_items(
    catalog_items_value: Any,
    mongo_uri: str = "",
    db_name: str = DEFAULT_DB_NAME,
    capability_collection: str = DEFAULT_CAPABILITY_COLLECTION,
    case_collection: str = DEFAULT_CASE_COLLECTION,
) -> dict[str, Any]:
    """검증된 카탈로그 항목을 MongoDB에 upsert 저장합니다."""
    payload = _payload(catalog_items_value)
    validation = _dict(payload.get("catalog_validation"))
    items = [_dict(item) for item in _as_list(payload.get("catalog_items"))]

    if validation.get("valid") is not True:
        return {
            **payload,
            "store_result": {
                "status": "blocked_by_validation",
                "reason": "catalog_validation.valid가 true가 아니어서 저장하지 않았습니다.",
                "upserted": 0,
                "matched": 0,
                "modified": 0,
            },
        }

    if not str(mongo_uri or "").strip():
        return {
            **payload,
            "store_result": {
                "status": "skipped",
                "reason": "Mongo URI가 비어 있어 저장을 건너뛰었습니다.",
                "preview_count": len(items),
                "upserted": 0,
                "matched": 0,
                "modified": 0,
            },
        }

    try:
        result = _upsert_to_mongodb(
            items=items,
            mongo_uri=mongo_uri,
            db_name=db_name or DEFAULT_DB_NAME,
            capability_collection=capability_collection or DEFAULT_CAPABILITY_COLLECTION,
            case_collection=case_collection or DEFAULT_CASE_COLLECTION,
        )
        return {**payload, "store_result": result}
    except Exception as exc:
        return {
            **payload,
            "store_result": {
                "status": "error",
                "reason": str(exc),
                "upserted": 0,
                "matched": 0,
                "modified": 0,
            },
        }


def _upsert_to_mongodb(
    items: list[dict[str, Any]],
    mongo_uri: str,
    db_name: str,
    capability_collection: str,
    case_collection: str,
) -> dict[str, Any]:
    from pymongo import MongoClient, UpdateOne

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=2500)
    client.admin.command("ping")
    db = client[db_name]
    operations: dict[str, list[Any]] = {}
    version_docs = []

    for item in items:
        row = deepcopy(item)
        created_at = row.pop("created_at", None) or _now_iso()
        row["updated_at"] = _now_iso()
        collection_name = case_collection if row.get("item_type") == "case" else capability_collection
        operations.setdefault(collection_name, []).append(
            UpdateOne(
                {"canonical_key": row.get("canonical_key")},
                {
                    "$set": row,
                    "$setOnInsert": {"created_at": created_at},
                },
                upsert=True,
            )
        )
        stored_item = {**row, "created_at": created_at}
        version_docs.append(
            {
                "catalog_key": row.get("canonical_key"),
                "target_collection": collection_name,
                "stored_item": stored_item,
                "stored_at": _now_iso(),
            }
        )

    totals = {"status": "ok", "upserted": 0, "matched": 0, "modified": 0, "version_records": 0}
    for collection_name, ops in operations.items():
        result = db[collection_name].bulk_write(ops, ordered=False)
        totals["upserted"] += len(result.upserted_ids or {})
        totals["matched"] += int(result.matched_count)
        totals["modified"] += int(result.modified_count)

    if version_docs:
        db[DEFAULT_CATALOG_VERSION_COLLECTION].insert_many(version_docs, ordered=False)
        totals["version_records"] = len(version_docs)
        totals["version_collection"] = DEFAULT_CATALOG_VERSION_COLLECTION
    return totals


def _payload(value: Any) -> dict[str, Any]:
    data = getattr(value, "data", value)
    return deepcopy(data) if isinstance(data, dict) else {}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, "", {}):
        return []
    return [value]


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class MongoCatalogStore(Component):
    display_name = "2.4 MongoDB 카탈로그 저장"
    description = "검증된 카탈로그 항목을 MongoDB에 저장합니다. Mongo URI가 없으면 저장하지 않고 preview로 종료합니다."
    icon = "DatabaseZap"
    inputs = [
        DataInput(name="catalog_items", display_name="카탈로그 항목", required=True),
        MessageTextInput(name="mongo_uri", display_name="Mongo URI", value="", advanced=True),
        MessageTextInput(name="db_name", display_name="DB 이름", value=DEFAULT_DB_NAME, advanced=True),
        MessageTextInput(name="capability_collection", display_name="기능 컬렉션", value=DEFAULT_CAPABILITY_COLLECTION, advanced=True),
        MessageTextInput(name="case_collection", display_name="사례 컬렉션", value=DEFAULT_CASE_COLLECTION, advanced=True),
    ]
    outputs = [Output(name="store_result", display_name="저장 결과", method="build_payload")]

    def build_payload(self) -> Data:
        result = store_catalog_items(
            getattr(self, "catalog_items", None),
            mongo_uri=getattr(self, "mongo_uri", ""),
            db_name=getattr(self, "db_name", DEFAULT_DB_NAME),
            capability_collection=getattr(self, "capability_collection", DEFAULT_CAPABILITY_COLLECTION),
            case_collection=getattr(self, "case_collection", DEFAULT_CASE_COLLECTION),
        )
        store_result = result.get("store_result", {})
        self.status = {
            "저장 상태": store_result.get("status"),
            "upsert": store_result.get("upserted", 0),
            "matched": store_result.get("matched", 0),
        }
        return Data(data=result)
