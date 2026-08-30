from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lfx.custom import Component
from lfx.io import FileInput, IntInput, Output
from lfx.schema import Data


_SENSITIVE_KEY_PATTERN = re.compile(
    r"(^|_)(api_?key|private_?key|authorization|cookie|credential|password|passwd|secret|session|smauthreason|smsession|token)(_|$)",
    re.IGNORECASE,
)
_EMAIL_PATTERN = re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])", re.IGNORECASE)
_BEARER_PATTERN = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
_BASIC_AUTH_PATTERN = re.compile(r"\bBasic\s+[A-Za-z0-9+/=]{8,}", re.IGNORECASE)
_JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_GITHUB_TOKEN_PATTERN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
_AWS_ACCESS_KEY_PATTERN = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_CREDENTIAL_URL_PATTERN = re.compile(r"(?i)\b(https?://)[^\s/@:]+:[^\s/@]+@")
_PRIVATE_KEY_BLOCK_PATTERN = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_ASSIGNED_SECRET_PATTERN = re.compile(
    r"\b(password|passwd|secret|token|api[_-]?key|authorization|cookie|session)\s*[:=]\s*[^\s,;]{4,}",
    re.IGNORECASE,
)
_TYPE_MAP = {"py": "component", "component": "component", "json": "flow", "flow": "flow"}
_TECHNICAL_STATUSES = {"metadata_only", "ports_extracted", "flow_graph_extracted", "verified_runtime"}
_SUPPORTED_SUFFIXES = {".json", ".jsonl", ".ndjson"}
_INGEST_CONTRACT_VERSION = "catalog-file-vector-ingest/v1"
_TENANT_ID = "default"
_CATALOG_ID = "internal-assets"


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _reject_nonfinite_json(value: str) -> Any:
    raise ValueError(f"Non-finite JSON number is not allowed: {value}.")


def _uploaded_file_value(value: Any) -> str:
    candidate = getattr(value, "path", None) or getattr(value, "file_path", None) or value
    if not isinstance(candidate, str) or not candidate.strip():
        raise ValueError("A JSON, JSONL, or NDJSON file upload is required.")
    return candidate.strip()


def _read_catalog_records(
    file_path: Any,
    *,
    max_file_bytes: int,
    max_records: int,
    max_record_chars: int,
) -> tuple[list[dict[str, Any]], str, int]:
    try:
        path = Path(str(file_path)).resolve(strict=True)
    except OSError as exc:
        raise ValueError("The uploaded catalog file is unavailable.") from exc
    if not path.is_file() or path.suffix.lower() not in _SUPPORTED_SUFFIXES:
        raise ValueError("Only .json, .jsonl, and .ndjson files are supported.")
    size_bytes = path.stat().st_size
    if size_bytes <= 0:
        raise ValueError("The uploaded catalog file is empty.")
    if size_bytes > max_file_bytes:
        raise ValueError("The uploaded catalog file exceeds the configured size limit.")
    raw_bytes = path.read_bytes()
    source_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("The uploaded catalog file must use UTF-8 encoding.") from exc

    records: list[Any]
    if path.suffix.lower() in {".jsonl", ".ndjson"}:
        records = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            if len(stripped) > max_record_chars:
                raise ValueError(f"Catalog record on line {line_number} exceeds the configured size limit.")
            try:
                records.append(json.loads(stripped, parse_constant=_reject_nonfinite_json))
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"Catalog record on line {line_number} is not valid JSON.") from exc
            if len(records) > max_records:
                raise ValueError("The uploaded catalog contains more records than the configured maximum.")
    else:
        try:
            root = json.loads(text, parse_constant=_reject_nonfinite_json)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError("The uploaded catalog is not valid JSON.") from exc
        if isinstance(root, list):
            records = root
        elif isinstance(root, dict) and "items" in root and not (root.get("id") or root.get("asset_id")):
            if not isinstance(root.get("items"), list):
                raise ValueError("A catalog object wrapper must contain an items array.")
            records = root["items"]
        elif isinstance(root, dict):
            records = [root]
        else:
            raise ValueError("The catalog root must be an object, an array of objects, or an object containing items.")

    if not records:
        raise ValueError("The uploaded catalog contains no records.")
    if len(records) > max_records:
        raise ValueError("The uploaded catalog contains more records than the configured maximum.")
    validated: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"Catalog record {index} must be a JSON object.")
        if len(_canonical_json(record)) > max_record_chars:
            raise ValueError(f"Catalog record {index} exceeds the configured size limit.")
        validated.append(record)
    return validated, source_sha256, size_bytes


def _redact_text(value: Any, max_chars: int) -> str:
    text = str(value or "").replace("\x00", "")[:max_chars]
    text = _PRIVATE_KEY_BLOCK_PATTERN.sub("[REDACTED:PRIVATE_KEY]", text)
    text = _EMAIL_PATTERN.sub("[REDACTED:EMAIL]", text)
    text = _BEARER_PATTERN.sub("[REDACTED:BEARER]", text)
    text = _BASIC_AUTH_PATTERN.sub("[REDACTED:BASIC_AUTH]", text)
    text = _JWT_PATTERN.sub("[REDACTED:JWT]", text)
    text = _GITHUB_TOKEN_PATTERN.sub("[REDACTED:GITHUB_TOKEN]", text)
    text = _AWS_ACCESS_KEY_PATTERN.sub("[REDACTED:AWS_ACCESS_KEY]", text)
    text = _CREDENTIAL_URL_PATTERN.sub(r"\1[REDACTED:CREDENTIALS]@", text)
    return _ASSIGNED_SECRET_PATTERN.sub("[REDACTED:ASSIGNED_SECRET]", text)


def _redact_value(
    value: Any,
    *,
    key: str,
    depth: int,
    max_depth: int,
    max_items: int,
    max_string_chars: int,
) -> Any:
    if depth > max_depth:
        raise ValueError("A catalog record exceeds the configured nesting depth.")
    snake_key = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    normalized_key = re.sub(r"[^a-z0-9]+", "_", snake_key.casefold()).strip("_")
    if _SENSITIVE_KEY_PATTERN.search(normalized_key):
        return "[REDACTED:SENSITIVE_FIELD]"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("A catalog record contains a non-finite number.")
        return value
    if isinstance(value, str):
        return _redact_text(value, max_string_chars)
    if isinstance(value, list):
        if len(value) > max_items:
            raise ValueError("A catalog record contains too many list items.")
        return [
            _redact_value(
                item,
                key=key,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                max_string_chars=max_string_chars,
            )
            for item in value
        ]
    if isinstance(value, dict):
        if len(value) > max_items:
            raise ValueError("A catalog record contains too many object fields.")
        result: dict[str, Any] = {}
        for raw_key, item in value.items():
            safe_key = str(raw_key)
            if not safe_key or len(safe_key) > 256 or "\x00" in safe_key or "." in safe_key or safe_key.startswith("$"):
                raise ValueError("Catalog object keys must be MongoDB-safe strings of at most 256 characters.")
            result[safe_key] = _redact_value(
                item,
                key=safe_key,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                max_string_chars=max_string_chars,
            )
        return result
    raise ValueError("A catalog record contains an unsupported JSON value.")


def _normalize_datetime(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _string_list(value: Any, *, preserve_case: bool, maximum: int = 100) -> list[str]:
    source = value if isinstance(value, list) else []
    result: list[str] = []
    for item in source[:maximum]:
        text = str(item or "").strip()
        if not preserve_case:
            text = text.lower()
        if text and len(text) <= 128 and text not in result:
            result.append(text)
    return result


def _normalize_acl(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    visibility = str(source.get("visibility") or "tenant").strip().lower()
    if visibility not in {"tenant", "group", "private"}:
        raise ValueError("Catalog ACL visibility must be tenant, group, or private.")
    groups = _string_list(source.get("groups"), preserve_case=False)
    subjects = _string_list(source.get("subjects"), preserve_case=True)
    if visibility == "group" and not groups:
        raise ValueError("Group-visible records require at least one ACL group.")
    if visibility == "private" and not subjects:
        raise ValueError("Private records require at least one ACL subject.")
    return {"visibility": visibility, "groups": groups, "subjects": subjects}


def _normalize_ports(safe_record: dict[str, Any]) -> tuple[str, dict[str, list[Any]], dict[str, Any]]:
    source_contract = safe_record.get("technical_contract") if isinstance(safe_record.get("technical_contract"), dict) else {}
    raw_ports = safe_record.get("ports") if isinstance(safe_record.get("ports"), dict) else {}
    inputs = raw_ports.get("inputs") if isinstance(raw_ports.get("inputs"), list) else source_contract.get("inputs")
    outputs = raw_ports.get("outputs") if isinstance(raw_ports.get("outputs"), list) else source_contract.get("outputs")
    inputs = inputs if isinstance(inputs, list) else []
    outputs = outputs if isinstance(outputs, list) else []
    requested_status = str(
        safe_record.get("technical_contract_status") or source_contract.get("status") or ""
    ).strip()
    if requested_status and requested_status not in _TECHNICAL_STATUSES:
        raise ValueError("technical_contract_status is not supported.")
    status = requested_status or ("ports_extracted" if inputs or outputs else "metadata_only")
    ports = {"inputs": inputs, "outputs": outputs}
    technical_contract = {
        "status": status,
        "inputs": inputs,
        "outputs": outputs,
        "dependencies": source_contract.get("dependencies") if isinstance(source_contract.get("dependencies"), list) else [],
        "verified_at": _normalize_datetime(source_contract.get("verified_at")),
    }
    return status, ports, technical_contract


def _normalize_records(
    records: list[dict[str, Any]],
    *,
    tenant_id: str,
    catalog_id: str,
    source_sha256: str,
    source_size_bytes: int,
    max_record_chars: int,
    max_text_chars: int,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for record_index, raw_record in enumerate(records):
        safe = _redact_value(
            raw_record,
            key="record",
            depth=0,
            max_depth=12,
            max_items=5000,
            max_string_chars=max_record_chars,
        )
        if not isinstance(safe, dict):
            raise ValueError(f"Catalog record {record_index} must be an object.")
        record_tenant = str(safe.get("tenant_id") or tenant_id).strip().lower()
        if record_tenant != tenant_id:
            raise ValueError("A catalog record cannot override tenant_id.")
        asset_id = str(safe.get("id") or safe.get("asset_id") or "").strip()
        title = str(safe.get("title") or "").strip()
        original_type = str(safe.get("type") or safe.get("asset_type") or "").strip().lower()
        version = str(safe.get("version") or "unversioned").strip() or "unversioned"
        if not asset_id or len(asset_id) > 256:
            raise ValueError(f"Catalog record {record_index} requires id of at most 256 characters.")
        if not title or len(title) > 1000:
            raise ValueError(f"Catalog record {record_index} requires title of at most 1000 characters.")
        if original_type not in _TYPE_MAP:
            raise ValueError(f"Catalog record {record_index} type must be py, component, json, or flow.")
        if len(version) > 100:
            raise ValueError(f"Catalog record {record_index} version is too long.")
        identity = (asset_id, version)
        if identity in seen:
            raise ValueError(f"Duplicate asset identity in upload: {asset_id}@{version}.")
        seen.add(identity)

        description = str(safe.get("description") or "").strip()
        category = str(safe.get("category") or "Uncategorized").strip() or "Uncategorized"
        readme = str(safe.get("readme") or "").strip()
        technical_status, ports, technical_contract = _normalize_ports(safe)
        acl = _normalize_acl(safe.get("acl"))
        relations = safe.get("relations") if isinstance(safe.get("relations"), list) else []
        raw_text_redacted = _canonical_json(safe)
        record_sha256 = _sha256_text(raw_text_redacted)
        searchable_lines = [
            f"title: {title}",
            f"type: {original_type} / {_TYPE_MAP[original_type]}",
            f"category: {category}",
            f"description: {description}" if description else "",
            f"readme: {readme}" if readme else "",
            f"original_record: {raw_text_redacted}",
        ]
        lexical_text = "\n".join(line for line in searchable_lines if line).strip()
        if len(lexical_text) > max_text_chars:
            raise ValueError(
                f"Catalog record {record_index} searchable text exceeds the configured maximum; increase max_text_chars explicitly."
            )
        content_basis = {
            "identity": [asset_id, version],
            "record_sha256": record_sha256,
            "technical_contract": technical_contract,
            "acl": acl,
        }
        normalized.append(
            {
                "tenant_id": tenant_id,
                "catalog_id": catalog_id,
                "asset_id": asset_id,
                "version": version,
                "asset_type": _TYPE_MAP[original_type],
                "title": title,
                "title_normalized": re.sub(r"\s+", " ", title.casefold()).strip()[:500],
                "aliases_normalized": _string_list(safe.get("aliases"), preserve_case=False, maximum=32),
                "description": description,
                "category": category,
                "readme": readme,
                "acl": acl,
                "technical_contract_status": technical_status,
                "technical_contract": technical_contract,
                "ports": ports,
                "relations": relations,
                "stars_count": _nonnegative_int(safe.get("stars_count")),
                "downloads_count": _nonnegative_int(safe.get("downloads_count")),
                "created_at": _normalize_datetime(safe.get("created_at")),
                "updated_at": _normalize_datetime(safe.get("updated_at")),
                "source": {
                    "file_sha256": source_sha256,
                    "file_size_bytes": source_size_bytes,
                    "record_index": record_index,
                },
                "lexical_text_redacted": lexical_text,
                "raw_record_redacted": safe,
                "raw_text_redacted": raw_text_redacted,
                "raw_record_redacted_sha256": record_sha256,
                "content_sha256": _sha256_text(_canonical_json(content_basis)),
            }
        )
    return normalized


def _failure(code: str, message: str) -> Data:
    return Data(data={"ok": False, "status": "BLOCKED", "error": {"code": code, "message": message, "retryable": False}})


class CatalogJsonLoaderComponent(Component):
    display_name = "00 Catalog JSON Loader & Normalizer"
    description = "Upload one catalog JSON file, validate it, redact secrets, and preserve canonical parent metadata."
    icon = "FileJson"
    name = "CatalogJsonLoader"

    inputs = [
        FileInput(
            name="catalog_file",
            display_name="Catalog JSON File",
            file_types=["json", "jsonl", "ndjson"],
            required=True,
            info="Upload one JSON, JSONL, or NDJSON catalog file. Start with samples/f00_catalog_assets_example.json.",
        ),
        IntInput(name="max_records", display_name="Maximum Records", value=50000, advanced=True),
        IntInput(name="max_file_size_mb", display_name="Maximum File Size (MiB)", value=100, advanced=True),
        IntInput(name="max_record_chars", display_name="Maximum Record Characters", value=200000, advanced=True),
        IntInput(name="max_text_chars", display_name="Maximum Searchable Text Characters", value=60000, advanced=True),
    ]

    outputs = [Output(name="catalog_bundle", display_name="Normalized Catalog Bundle", method="load_catalog", types=["Data"])]

    def load_catalog(self) -> Data:
        try:
            max_records = _bounded_int(getattr(self, "max_records", 50000), 50000, 1, 100000)
            max_file_mb = _bounded_int(getattr(self, "max_file_size_mb", 100), 100, 1, 500)
            max_record_chars = _bounded_int(getattr(self, "max_record_chars", 200000), 200000, 1000, 1000000)
            max_text_chars = _bounded_int(getattr(self, "max_text_chars", 60000), 60000, 1000, 200000)
            upload_value = _uploaded_file_value(getattr(self, "catalog_file", None))
            resolved_path = self.resolve_path(upload_value)
            records, source_sha256, source_size_bytes = _read_catalog_records(
                resolved_path,
                max_file_bytes=max_file_mb * 1024 * 1024,
                max_records=max_records,
                max_record_chars=max_record_chars,
            )
            normalized = _normalize_records(
                records,
                tenant_id=_TENANT_ID,
                catalog_id=_CATALOG_ID,
                source_sha256=source_sha256,
                source_size_bytes=source_size_bytes,
                max_record_chars=max_record_chars,
                max_text_chars=max_text_chars,
            )
            result = {
                "ok": True,
                "status": "LOADED",
                "schema_version": "catalog-normalized-bundle/v1",
                "ingest_contract_version": _INGEST_CONTRACT_VERSION,
                "tenant_id": _TENANT_ID,
                "catalog_id": _CATALOG_ID,
                "source_sha256": source_sha256,
                "source_size_bytes": source_size_bytes,
                "max_text_chars": max_text_chars,
                "records": normalized,
                "counts": {"records": len(normalized)},
            }
            self.status = f"Loaded and normalized {len(normalized)} catalog records."
            return Data(data=result)
        except (OSError, ValueError) as exc:
            self.status = "Catalog file loading was blocked."
            return _failure("CATALOG_FILE_INVALID", str(exc))
