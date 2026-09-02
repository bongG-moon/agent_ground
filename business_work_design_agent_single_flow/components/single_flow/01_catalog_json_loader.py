from __future__ import annotations

"""Load and normalise a local Agent Hub catalog JSON file.

The component accepts exactly one UTF-8 JSON file and emits a deliberately closed
projection.  It never trusts a source URL: a catalog URL is derived solely from a
validated UUID and the declared py/json (component/flow) type.
"""

import hashlib
import json
import os
import re
import unicodedata
import uuid
from pathlib import Path
from typing import Any

from lfx.custom import Component
from lfx.io import FileInput, IntInput, Output
from lfx.schema import Data


_SCHEMA_VERSION = "local-catalog-bundle/v2"
_AGENT_HUB_BASE = "https://agent-hub.skhynix.com/#"
_SOURCE_URL_FIELDS = {"catalog_url", "detail_url", "asset_url", "link", "url"}
_ASSET_TYPE_MAP = {"py": "component", "component": "component", "json": "flow", "flow": "flow"}
_TECHNICAL_STATUSES = {
    "metadata_only",
    "ports_extracted",
    "flow_graph_extracted",
    "verified_runtime",
    "unknown",
}
_OPTIONAL_TEXT_LIMITS = {
    "version": 100,
    "description": 10_000,
    "category": 500,
    "readme": 200_000,
    "technical_contract_status": 128,
    "updated_at": 100,
}
_LIST_FIELDS = ("aliases", "capabilities", "systems", "tags", "use_cases", "limitations")
_PORT_FIELDS = {
    "port_id",
    "name",
    "label",
    "description",
    "data_type",
    "semantic_role",
    "schema_ref",
    "cardinality",
    "required",
    "has_default",
    "secret",
    "permission",
    "network_zone",
    "streaming",
}
_SECRET_KEY_WORDS = {
    "apikey",
    "authorization",
    "clientsecret",
    "cookie",
    "credential",
    "password",
    "passwd",
    "privatekey",
    "pwd",
    "secret",
    "token",
}
_SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)\b(?:api[_-]?key|password|passwd|secret|access[_-]?token|client[_-]?secret|authorization|cookie)\s*[:=]\s*[\"']?[^\s,;]{8,}"),
    re.compile(r"(?i)\b(?:bearer|basic)\s+\S{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^/@\s]+:[^/@\s]+@"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256_text(value: str | bytes) -> str:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _reject_constant(value: str) -> None:
    raise ValueError(f"CATALOG_JSON_NON_FINITE_NUMBER: {value} is not allowed")


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("CATALOG_JSON_DUPLICATE_KEY: duplicate JSON object keys are not allowed")
        result[key] = value
    return result


def _validate_json_shape(value: Any, max_depth: int, path: str = "$", depth: int = 1) -> None:
    if depth > max_depth:
        raise ValueError(f"CATALOG_JSON_DEPTH_EXCEEDED: {path} exceeds maximum nesting depth")
    if value is None or type(value) in {str, int, float, bool}:
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_shape(item, max_depth, f"{path}[{index}]", depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError(f"CATALOG_JSON_KEY_INVALID: {path} has a non-string key")
            _validate_json_shape(item, max_depth, f"{path}.{key}", depth + 1)
        return
    raise ValueError(f"CATALOG_JSON_VALUE_INVALID: {path} contains an unsupported value")


def _is_secret_key(value: Any) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", str(value or "").casefold())
    return compact in _SECRET_KEY_WORDS or any(word in compact for word in _SECRET_KEY_WORDS)


def _contains_secret_material(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if _is_secret_key(key) and item not in (None, "", False, "[REDACTED]"):
                return True
            if _contains_secret_material(item):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_secret_material(item) for item in value)
    return isinstance(value, str) and any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS)


def _bounded_text(value: Any, field: str, maximum: int, *, required: bool = False) -> str:
    if value is None and not required:
        return ""
    if type(value) is not str:
        raise ValueError(f"CATALOG_FIELD_INVALID: {field} must be a string")
    text = unicodedata.normalize("NFKC", value).strip()
    if required and not text:
        raise ValueError(f"CATALOG_FIELD_REQUIRED: {field} is required")
    if len(text) > maximum:
        raise ValueError(f"CATALOG_FIELD_TOO_LARGE: {field} exceeds {maximum:,} characters")
    return text


def _normalise_uuid(value: Any) -> str:
    asset_id = _bounded_text(value, "asset_id", 128, required=True).lower()
    try:
        parsed = uuid.UUID(asset_id)
    except (ValueError, AttributeError) as exc:
        raise ValueError("CATALOG_ID_INVALID: asset_id must be a standard UUID") from exc
    if str(parsed) != asset_id:
        raise ValueError("CATALOG_ID_INVALID: asset_id must use canonical UUID form")
    return asset_id


def _normalise_asset_type(value: Any) -> str:
    raw = _bounded_text(value, "asset_type", 32, required=True).casefold()
    asset_type = _ASSET_TYPE_MAP.get(raw)
    if asset_type is None:
        raise ValueError("CATALOG_TYPE_INVALID: type must be py/component or json/flow")
    return asset_type


def _normalise_text_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"CATALOG_FIELD_INVALID: {field} must be a string array")
    if len(value) > 200:
        raise ValueError(f"CATALOG_FIELD_TOO_LARGE: {field} has more than 200 entries")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _bounded_text(item, field, 500)
        if not text:
            continue
        key = text.casefold()
        if key not in seen:
            result.append(text)
            seen.add(key)
    return result


def _nonnegative_integer(value: Any, field: str) -> int:
    if value is None:
        return 0
    if type(value) is not int or value < 0:
        raise ValueError(f"CATALOG_FIELD_INVALID: {field} must be a non-negative integer")
    return value


def _normalise_port_list(value: Any, direction: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 500:
        raise ValueError(f"CATALOG_PORTS_INVALID: ports.{direction} must contain at most 500 entries")
    result: list[dict[str, Any]] = []
    for index, raw_port in enumerate(value):
        if not isinstance(raw_port, dict):
            raise ValueError(f"CATALOG_PORTS_INVALID: ports.{direction}[{index}] must be an object")
        if _contains_secret_material(raw_port):
            raise ValueError("CATALOG_SECRET_MATERIAL_DETECTED: ports must not contain secret material")
        port: dict[str, Any] = {}
        for key in sorted(_PORT_FIELDS):
            if key not in raw_port:
                continue
            value = raw_port[key]
            if type(value) is bool:
                port[key] = value
            elif type(value) is str:
                port[key] = _bounded_text(value, f"ports.{direction}.{key}", 500)
            elif value is None:
                port[key] = None
            else:
                raise ValueError(f"CATALOG_PORTS_INVALID: ports.{direction}.{key} has an invalid type")
        result.append(port)
    return result


def _normalise_ports(value: Any) -> dict[str, list[dict[str, Any]]]:
    if value is None:
        return {"inputs": [], "outputs": []}
    if not isinstance(value, dict):
        raise ValueError("CATALOG_PORTS_INVALID: ports must be an object")
    ports = {
        "inputs": _normalise_port_list(value.get("inputs"), "inputs"),
        "outputs": _normalise_port_list(value.get("outputs"), "outputs"),
    }
    if len(_canonical_json(ports)) > 50_000:
        raise ValueError("CATALOG_PORTS_TOO_LARGE: serialized ports exceed 50,000 characters")
    return ports


def _safe_file_path(component: Component, value: Any) -> str:
    candidate = value
    if isinstance(candidate, (list, tuple)):
        if len(candidate) != 1:
            raise ValueError("CATALOG_FILE_COUNT_INVALID: exactly one catalog JSON file is required")
        candidate = candidate[0]
    if not isinstance(candidate, str):
        candidate = getattr(candidate, "path", None) or getattr(candidate, "file_path", None)
    if isinstance(candidate, (list, tuple)):
        if len(candidate) != 1:
            raise ValueError("CATALOG_FILE_COUNT_INVALID: exactly one catalog JSON file is required")
        candidate = candidate[0]
    if not isinstance(candidate, str) or not candidate.strip():
        raise ValueError("CATALOG_FILE_REQUIRED: a catalog JSON file is required")
    resolved = component.resolve_path(candidate.strip())
    path = Path(resolved)
    if not path.is_file():
        raise ValueError("CATALOG_FILE_INVALID: the uploaded value must resolve to one JSON file")
    return str(path)


def _search_text(item: dict[str, Any], maximum: int) -> str:
    values: list[str] = [
        item["title"],
        item["description"],
        item["category"],
        item["readme"],
    ]
    for field in _LIST_FIELDS:
        values.extend(item[field])
    # The ranker recomputes field-level scores; this bounded digest records the
    # exact maximum search surface applied by the loader without retaining raw JSON.
    return "\n".join(value for value in values if value)[:maximum]


class LocalCatalogJsonLoaderComponent(Component):
    display_name = "01 기능 카탈로그 JSON 파일"
    description = "기능 카탈로그 JSON을 검증하고 Agent Hub 링크가 포함된 안전한 후보 묶음으로 만듭니다."
    icon = "FileJson2"
    name = "LocalCatalogJsonLoader"

    inputs = [
        FileInput(
            name="catalog_json_file",
            display_name="기능 카탈로그 JSON",
            fileTypes=["json"],
            required=True,
            info="UTF-8 JSON 파일 한 개를 선택합니다.",
        ),
        IntInput(name="max_file_size_mib", display_name="최대 파일 크기 MiB", value=20, advanced=True),
        IntInput(name="max_items", display_name="최대 항목 수", value=5_000, advanced=True),
        IntInput(name="max_item_raw_chars", display_name="항목당 최대 원문 문자 수", value=200_000, advanced=True),
        IntInput(name="max_search_text_chars", display_name="항목당 검색 text 최대 문자 수", value=6_000, advanced=True),
        IntInput(name="max_json_depth", display_name="최대 JSON 중첩 깊이", value=12, advanced=True),
    ]
    outputs = [Output(name="catalog_bundle", display_name="정규화 카탈로그", method="load_catalog")]

    def load_catalog(self) -> Data:
        max_file_size = int(getattr(self, "max_file_size_mib", 20) or 20)
        max_items = int(getattr(self, "max_items", 5_000) or 5_000)
        max_raw_item_chars = int(getattr(self, "max_item_raw_chars", 200_000) or 200_000)
        max_search_chars = int(getattr(self, "max_search_text_chars", 6_000) or 6_000)
        max_depth = int(getattr(self, "max_json_depth", 12) or 12)
        if not (1 <= max_file_size <= 100 and 1 <= max_items <= 5_000 and 100 <= max_raw_item_chars <= 200_000 and 100 <= max_search_chars <= 6_000 and 1 <= max_depth <= 32):
            raise ValueError("CATALOG_LIMIT_INVALID: one or more advanced loader limits are out of range")

        path_text = _safe_file_path(self, getattr(self, "catalog_json_file", None))
        path = Path(path_text)
        raw_bytes = path.read_bytes()
        if len(raw_bytes) > max_file_size * 1024 * 1024:
            raise ValueError("CATALOG_FILE_TOO_LARGE: catalog file exceeds the configured size limit")
        try:
            decoded = raw_bytes.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("CATALOG_ENCODING_INVALID: catalog file must be UTF-8 JSON") from exc
        try:
            parsed = json.loads(decoded, parse_constant=_reject_constant, object_pairs_hook=_pairs_without_duplicates)
        except (json.JSONDecodeError, ValueError) as exc:
            if str(exc).startswith("CATALOG_"):
                raise
            raise ValueError("CATALOG_JSON_INVALID: catalog file must contain strict JSON") from exc
        _validate_json_shape(parsed, max_depth)

        if isinstance(parsed, list):
            raw_items = parsed
        elif isinstance(parsed, dict) and isinstance(parsed.get("items"), list):
            raw_items = parsed["items"]
        else:
            raise ValueError("CATALOG_ROOT_INVALID: JSON must be an array or an object with an items array")
        if not raw_items:
            raise ValueError("CATALOG_EMPTY: catalog must contain at least one item")
        if len(raw_items) > max_items:
            raise ValueError("CATALOG_ITEM_LIMIT_EXCEEDED: catalog has more items than the configured limit")

        normalised_items: list[dict[str, Any]] = []
        seen_identity: set[tuple[str, str]] = set()
        removed_secret_fields = 0
        ignored_source_url_fields = 0
        for index, raw_item in enumerate(raw_items):
            if not isinstance(raw_item, dict):
                raise ValueError(f"CATALOG_ITEM_INVALID: items[{index}] must be an object")
            if len(_canonical_json(raw_item)) > max_raw_item_chars:
                raise ValueError(f"CATALOG_ITEM_TOO_LARGE: items[{index}] exceeds the configured raw size")
            ignored_source_url_fields += sum(1 for key in raw_item if key.casefold() in _SOURCE_URL_FIELDS)

            asset_id = _normalise_uuid(raw_item.get("asset_id", raw_item.get("id")))
            title = _bounded_text(raw_item.get("title"), "title", 500, required=True)
            asset_type = _normalise_asset_type(raw_item.get("asset_type", raw_item.get("type")))
            if _contains_secret_material(title):
                raise ValueError("CATALOG_SECRET_MATERIAL_DETECTED: title must not contain secret material")

            item: dict[str, Any] = {
                "asset_id": asset_id,
                "version": _bounded_text(raw_item.get("version"), "version", 100) or "unknown",
                "asset_type": asset_type,
                "title": title,
                "description": "",
                "category": "",
                "readme": "",
                "aliases": [],
                "capabilities": [],
                "systems": [],
                "tags": [],
                "use_cases": [],
                "limitations": [],
                "technical_contract_status": "unknown",
                "stars_count": 0,
                "downloads_count": 0,
                "updated_at": "",
                "catalog_url": f"{_AGENT_HUB_BASE}/{'component' if asset_type == 'component' else 'flow'}/{asset_id}",
                "ports": {"inputs": [], "outputs": []},
            }
            for field, maximum in _OPTIONAL_TEXT_LIMITS.items():
                if field == "version" or field not in raw_item:
                    continue
                raw_value = raw_item[field]
                if _contains_secret_material(raw_value):
                    removed_secret_fields += 1
                    continue
                item[field] = _bounded_text(raw_value, field, maximum)
            status = item["technical_contract_status"].casefold() or "unknown"
            item["technical_contract_status"] = status if status in _TECHNICAL_STATUSES else "unknown"
            for field in ("stars_count", "downloads_count"):
                if field in raw_item:
                    item[field] = _nonnegative_integer(raw_item[field], field)
            for field in _LIST_FIELDS:
                if field not in raw_item:
                    continue
                raw_value = raw_item[field]
                if _contains_secret_material(raw_value):
                    removed_secret_fields += 1
                    continue
                item[field] = _normalise_text_list(raw_value, field)
            if "ports" in raw_item:
                if _contains_secret_material(raw_item["ports"]):
                    removed_secret_fields += 1
                else:
                    item["ports"] = _normalise_ports(raw_item["ports"])

            identity = (item["asset_id"], item["version"])
            if identity in seen_identity:
                raise ValueError("CATALOG_IDENTITY_DUPLICATE: asset_id and version must be unique")
            seen_identity.add(identity)
            # This is internal retrieval material.  It is not a source URL or raw
            # source object and the ranker never forwards it to the LLM/report.
            item["search_text"] = _search_text(item, max_search_chars)
            content_basis = {key: value for key, value in item.items() if key not in {"content_sha256", "search_text"}}
            item["content_sha256"] = _sha256_text(_canonical_json(content_basis))
            normalised_items.append(item)

        result = {
            "schema_version": _SCHEMA_VERSION,
            "source": {
                "file_name": os.path.basename(path.name),
                "file_sha256": _sha256_text(raw_bytes),
                "file_size_bytes": len(raw_bytes),
            },
            "counts": {
                "input_items": len(raw_items),
                "valid_items": len(normalised_items),
                "removed_secret_fields": removed_secret_fields,
                "ignored_source_url_fields": ignored_source_url_fields,
                "derived_agent_hub_links": len(normalised_items),
            },
            "items": normalised_items,
        }
        self.status = f"카탈로그 {len(normalised_items):,}개 항목을 안전하게 정규화했습니다."
        return Data(data=result)
