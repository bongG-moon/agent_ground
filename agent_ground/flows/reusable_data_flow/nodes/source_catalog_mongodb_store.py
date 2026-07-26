from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from importlib import import_module
from typing import Any, Dict

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MessageTextInput, Output
from lfx.schema.data import Data


DEFAULT_DB_NAME = "langflow"
DEFAULT_COLLECTION_NAME = "reusable_source_catalogs"


def _make_data(payload: Dict[str, Any]) -> Any:
    """Langflow 버전에 따라 Data 생성자 모양이 달라지는 부분을 흡수합니다."""
    try:
        return Data(data=payload)
    except TypeError:
        return Data(payload)


def _text_from_value(value: Any) -> str:
    """Text/Message/Data/dict 입력에서 문자열 값을 꺼냅니다."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("text", "content", "message", "value", "output_text"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
    for attr in ("text", "content", "message"):
        item = getattr(value, attr, None)
        if isinstance(item, str) and item.strip():
            return item.strip()
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return _text_from_value(data)
    return ""


def _payload_from_value(value: Any) -> Dict[str, Any]:
    """Data/Message/Text/JSON 입력을 dict 후보로 변환합니다."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return deepcopy(value)
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return deepcopy(data)
    text = _text_from_value(value)
    if text:
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _source_catalog_from_value(value: Any) -> Dict[str, Any]:
    """wrapper가 있으면 벗기고 실제 source_catalog 본문만 반환합니다."""
    payload = _payload_from_value(value)
    if isinstance(payload.get("source_catalog"), dict):
        return deepcopy(payload["source_catalog"])
    if isinstance(payload.get("catalog"), dict):
        return deepcopy(payload["catalog"])
    if isinstance(payload.get("data"), dict):
        data_payload = payload["data"]
        if isinstance(data_payload.get("source_catalog"), dict):
            return deepcopy(data_payload["source_catalog"])
        if isinstance(data_payload.get("sources"), dict):
            return deepcopy(data_payload)
    if isinstance(payload.get("sources"), dict):
        return deepcopy(payload)
    return {}


def _now_utc() -> str:
    """MongoDB 문서에 남길 현재 UTC 시간을 ISO 문자열로 만듭니다."""
    return datetime.now(timezone.utc).isoformat()


def _collection(mongo_uri: str, db_name: str, collection_name: str, timeout_ms_value: Any = "5000") -> Any:
    """MongoDB collection 객체를 가져옵니다."""
    timeout_ms = max(1000, int(timeout_ms_value or 5000))
    mongo_client_cls = getattr(import_module("pymongo"), "MongoClient")
    client = mongo_client_cls(mongo_uri, serverSelectionTimeoutMS=timeout_ms)
    return client[db_name][collection_name]


def save_source_catalog_to_mongo(
    source_catalog_value: Any,
    mongo_uri: str = "",
    db_name: str = DEFAULT_DB_NAME,
    collection_name: str = DEFAULT_COLLECTION_NAME,
    timeout_ms: Any = "5000",
) -> Dict[str, Any]:
    """source_catalog의 각 source를 source 이름 기준으로 MongoDB에 upsert합니다."""
    catalog = _source_catalog_from_value(source_catalog_value)
    sources = catalog.get("sources") if isinstance(catalog.get("sources"), dict) else {}
    errors: list[str] = []
    mongo_uri = str(mongo_uri or "").strip()
    db_name = str(db_name or DEFAULT_DB_NAME).strip()
    collection_name = str(collection_name or DEFAULT_COLLECTION_NAME).strip()

    if not sources:
        errors.append("source_catalog가 비어 있습니다.")
    if not mongo_uri:
        errors.append("mongo_uri를 입력해주세요.")
    if errors:
        return {"source_catalog": catalog, "mongo_store": {"success": False, "errors": errors, "collection_name": collection_name}}

    try:
        collection = _collection(mongo_uri, db_name, collection_name, timeout_ms)
        saved_sources: list[str] = []
        # source_name은 사람이 따로 입력하지 않고 source_catalog.sources의 key에서 자동으로 가져옵니다.
        for source_name, source_payload in sources.items():
            if not isinstance(source_payload, dict):
                continue
            clean_name = str(source_name or source_payload.get("name") or source_payload.get("source_name") or "").strip()
            if not clean_name:
                continue
            source_doc = deepcopy(source_payload)
            source_doc.setdefault("name", clean_name)
            document = {
                "source_name": clean_name,
                "source": source_doc,
                "updated_at": _now_utc(),
            }
            collection.update_one(
                {"source_name": clean_name},
                {"$set": document},
                upsert=True,
            )
            saved_sources.append(clean_name)
        if not saved_sources:
            return {"source_catalog": catalog, "mongo_store": {"success": False, "errors": ["저장할 source가 없습니다."], "collection_name": collection_name}}
        return {
            "source_catalog": catalog,
            "mongo_store": {
                "success": True,
                "errors": [],
                "db_name": db_name,
                "collection_name": collection_name,
                "saved_sources": saved_sources,
                "source_count": len(saved_sources),
            },
        }
    except Exception as exc:
        return {"source_catalog": catalog, "mongo_store": {"success": False, "errors": [str(exc)], "collection_name": collection_name}}


class SourceCatalogMongoDBStore(Component):
    """정규화된 source_catalog를 MongoDB에 저장하는 전용 노드입니다."""

    display_name = "Catalog MongoDB Store"
    description = "source_catalog를 source 이름 기준으로 MongoDB에 저장하거나 업데이트합니다."
    icon = "Database"
    name = "SourceCatalogMongoDBStore"

    inputs = [
        DataInput(name="source_catalog_data", display_name="Catalog Data", input_types=["Data", "JSON", "Message", "Text"]),
        MessageTextInput(name="mongo_uri", display_name="Mongo URI", value=""),
        MessageTextInput(name="db_name", display_name="DB Name", value=DEFAULT_DB_NAME),
        MessageTextInput(name="collection_name", display_name="Collection Name", value=DEFAULT_COLLECTION_NAME),
        MessageTextInput(name="timeout_ms", display_name="Timeout MS", value="5000", advanced=True),
    ]
    outputs = [
        Output(name="catalog_data", display_name="Catalog Data", method="build_catalog_data", types=["Data"]),
        Output(name="store_result", display_name="Store Result", method="build_store_result", types=["Data"]),
    ]

    def _payload(self) -> Dict[str, Any]:
        cached = getattr(self, "_cached_payload", None)
        if isinstance(cached, dict):
            return cached
        payload = save_source_catalog_to_mongo(
            getattr(self, "source_catalog_data", None),
            getattr(self, "mongo_uri", ""),
            getattr(self, "db_name", DEFAULT_DB_NAME),
            getattr(self, "collection_name", DEFAULT_COLLECTION_NAME),
            getattr(self, "timeout_ms", "5000"),
        )
        self._cached_payload = payload
        store = payload.get("mongo_store", {})
        self.status = {"success": bool(store.get("success")), "collection_name": store.get("collection_name", ""), "errors": len(store.get("errors", []))}
        return payload

    def build_catalog_data(self):
        """저장한 source_catalog를 다음 노드에 바로 연결할 수 있게 반환합니다."""
        return _make_data(self._payload().get("source_catalog", {}))

    def build_store_result(self):
        """MongoDB 저장 상태를 확인할 수 있게 반환합니다."""
        return _make_data(self._payload())
