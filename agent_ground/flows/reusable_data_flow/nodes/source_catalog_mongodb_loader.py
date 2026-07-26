from __future__ import annotations

import json
from copy import deepcopy
from importlib import import_module
from typing import Any, Dict

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message


DEFAULT_DB_NAME = "langflow"
DEFAULT_COLLECTION_NAME = "reusable_source_catalogs"


def _make_data(payload: Dict[str, Any]) -> Any:
    """Langflow 버전에 따라 Data 생성자 모양이 달라지는 부분을 흡수합니다."""
    try:
        return Data(data=payload)
    except TypeError:
        return Data(payload)


def _make_message(text: str) -> Any:
    """Langflow 버전에 따라 Message 생성자 모양이 달라지는 부분을 흡수합니다."""
    try:
        return Message(text=text)
    except TypeError:
        try:
            return Message(content=text)
        except TypeError:
            return Message(text)


def _collection(mongo_uri: str, db_name: str, collection_name: str, timeout_ms_value: Any = "5000") -> Any:
    """MongoDB collection 객체를 가져옵니다."""
    timeout_ms = max(1000, int(timeout_ms_value or 5000))
    mongo_client_cls = getattr(import_module("pymongo"), "MongoClient")
    client = mongo_client_cls(mongo_uri, serverSelectionTimeoutMS=timeout_ms)
    return client[db_name][collection_name]


def _catalog_with_sources(sources: Dict[str, Any]) -> Dict[str, Any]:
    """MongoDB에 source별로 저장된 문서를 다시 source_catalog 형태로 묶습니다."""
    return {
        "sources": deepcopy(sources),
        "needs_more_info": False,
        "questions": [],
        "warnings": [],
    }


def load_source_catalog_from_mongo(
    mongo_uri: str = "",
    db_name: str = DEFAULT_DB_NAME,
    collection_name: str = DEFAULT_COLLECTION_NAME,
    timeout_ms: Any = "5000",
) -> Dict[str, Any]:
    """collection 안의 source 문서들을 읽어 source_catalog로 묶습니다."""
    errors: list[str] = []
    mongo_uri = str(mongo_uri or "").strip()
    db_name = str(db_name or DEFAULT_DB_NAME).strip()
    collection_name = str(collection_name or DEFAULT_COLLECTION_NAME).strip()

    if not mongo_uri:
        errors.append("mongo_uri를 입력해주세요.")
    if errors:
        return {"source_catalog": {}, "mongo_load": {"success": False, "errors": errors, "collection_name": collection_name}}

    try:
        collection = _collection(mongo_uri, db_name, collection_name, timeout_ms)
        docs = []
        if hasattr(collection, "find"):
            docs = [dict(item) for item in collection.find({})]
        if not docs and hasattr(collection, "find_one"):
            # 이전 버전처럼 catalog 전체가 한 문서로 저장된 경우도 읽을 수 있게 둡니다.
            legacy_doc = collection.find_one({}) or {}
            if isinstance(legacy_doc.get("source_catalog"), dict):
                catalog = legacy_doc["source_catalog"]
                return {
                    "source_catalog": deepcopy(catalog),
                    "mongo_load": {
                        "success": True,
                        "errors": [],
                        "db_name": db_name,
                        "collection_name": collection_name,
                        "source_count": len(catalog.get("sources", {})) if isinstance(catalog.get("sources"), dict) else 0,
                    },
                }
        sources: Dict[str, Any] = {}
        for doc in docs:
            source_name = str(doc.get("source_name") or "").strip()
            source_payload = doc.get("source")
            if source_name and isinstance(source_payload, dict):
                sources[source_name] = deepcopy(source_payload)
        if not sources:
            return {"source_catalog": {}, "mongo_load": {"success": False, "errors": [f"source_catalog를 찾지 못했습니다: {collection_name}"], "collection_name": collection_name}}
        catalog = _catalog_with_sources(sources)
        return {
            "source_catalog": deepcopy(catalog),
            "mongo_load": {
                "success": True,
                "errors": [],
                "db_name": db_name,
                "collection_name": collection_name,
                "source_count": len(sources),
            },
        }
    except Exception as exc:
        return {"source_catalog": {}, "mongo_load": {"success": False, "errors": [str(exc)], "collection_name": collection_name}}


def build_catalog_text(payload: Dict[str, Any]) -> str:
    """Text Input에 바로 연결할 수 있도록 source_catalog만 JSON 문자열로 만듭니다."""
    catalog = payload.get("source_catalog") if isinstance(payload.get("source_catalog"), dict) else {}
    return json.dumps(catalog, ensure_ascii=False, indent=2, default=str)


class SourceCatalogMongoDBLoader(Component):
    """MongoDB에서 source_catalog를 불러오는 전용 노드입니다."""

    display_name = "Catalog MongoDB Loader"
    description = "MongoDB collection에서 source_catalog를 읽어옵니다."
    icon = "Database"
    name = "SourceCatalogMongoDBLoader"

    inputs = [
        MessageTextInput(name="mongo_uri", display_name="Mongo URI", value=""),
        MessageTextInput(name="db_name", display_name="DB Name", value=DEFAULT_DB_NAME),
        MessageTextInput(name="collection_name", display_name="Collection Name", value=DEFAULT_COLLECTION_NAME),
        MessageTextInput(name="timeout_ms", display_name="Timeout MS", value="5000", advanced=True),
    ]
    outputs = [
        Output(name="catalog_text", display_name="Catalog Text", method="build_catalog_text", types=["Message"]),
        Output(name="catalog_data", display_name="Catalog Data", method="build_catalog_data", types=["Data"]),
    ]

    def _payload(self) -> Dict[str, Any]:
        cached = getattr(self, "_cached_payload", None)
        if isinstance(cached, dict):
            return cached
        payload = load_source_catalog_from_mongo(
            getattr(self, "mongo_uri", ""),
            getattr(self, "db_name", DEFAULT_DB_NAME),
            getattr(self, "collection_name", DEFAULT_COLLECTION_NAME),
            getattr(self, "timeout_ms", "5000"),
        )
        self._cached_payload = payload
        load = payload.get("mongo_load", {})
        catalog = payload.get("source_catalog", {})
        source_count = len(catalog.get("sources", {})) if isinstance(catalog.get("sources"), dict) else 0
        self.status = {"success": bool(load.get("success")), "collection_name": load.get("collection_name", ""), "source_count": source_count, "errors": len(load.get("errors", []))}
        return payload

    def build_catalog_text(self):
        """Text Input.Text에 연결할 JSON Message를 반환합니다."""
        return _make_message(build_catalog_text(self._payload()))

    def build_catalog_data(self):
        """JSON 형태로 로드 결과와 source_catalog를 확인할 수 있게 반환합니다."""
        return _make_data(self._payload())
