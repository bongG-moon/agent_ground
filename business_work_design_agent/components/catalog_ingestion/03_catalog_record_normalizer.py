from __future__ import annotations

import json
import math
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

from pymongo import MongoClient, UpdateOne
from pymongo.errors import BulkWriteError, PyMongoError

from lfx.custom import Component
from lfx.io import DataInput, IntInput, Output, SecretStrInput, StrInput
from lfx.schema import Data


_JOB_REF_KEYS = ("tenant_id", "job_id", "snapshot_id", "stage", "expected_cursor", "trace_id")
_SENSITIVE_KEY = re.compile(r"(?:password|passwd|secret|token|api[_-]?key|authorization|cookie|session)", re.IGNORECASE)
_VALUE_PATTERNS = (
    ("PRIVATE_KEY", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----", re.IGNORECASE | re.DOTALL)),
    ("ASSIGNED_CREDENTIAL", re.compile(r"\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|password|passwd)\b\s*[:=]\s*[^\s,;]{4,}", re.IGNORECASE)),
    ("EMAIL", re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+(?![\w.-])")),
    ("PHONE", re.compile(r"(?<!\d)(?:(?:\+?82[- ]?)?0?1[016789]|0\d{1,2})[- ]?\d{3,4}[- ]?\d{4}(?!\d)")),
    ("NATIONAL_ID", re.compile(r"(?<!\d)\d{6}-?[1-4]\d{6}(?!\d)")),
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _secret_value(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        return str(getter()).strip()
    return str(value or "").strip()


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return dict(data)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _validate_job_ref(value: Any) -> dict[str, Any]:
    payload = _payload(value)
    result = {key: payload.get(key) for key in _JOB_REF_KEYS}
    for key in ("tenant_id", "job_id", "snapshot_id", "stage", "trace_id"):
        if not isinstance(result[key], str) or not result[key].strip():
            raise ValueError(f"job_ref.{key} is required.")
        result[key] = result[key].strip()
    try:
        result["expected_cursor"] = max(0, int(result["expected_cursor"] or 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("job_ref.expected_cursor must be an integer.") from exc
    return result


def _job_ref(source: dict[str, Any], stage: str, cursor: int) -> dict[str, Any]:
    return {
        "tenant_id": source["tenant_id"],
        "job_id": source["job_id"],
        "snapshot_id": source["snapshot_id"],
        "stage": stage,
        "expected_cursor": max(0, int(cursor)),
        "trace_id": source["trace_id"],
    }


def _failure(code: str, message: str, trace_id: str, retryable: bool = False) -> Data:
    return Data(
        data={
            "ok": False,
            "run_id": trace_id,
            "status": "BLOCKED",
            "artifact_refs": [],
            "error": {"code": code, "message": message, "retryable": retryable, "details": {}},
            "resume": None,
            "trace_id": trace_id,
        }
    )


def _merge_counts(target: dict[str, int], incoming: dict[str, int]) -> None:
    for code, count in incoming.items():
        target[code] = target.get(code, 0) + int(count)


def _redact_string(value: Any, max_chars: int) -> tuple[str, dict[str, int]]:
    text = str(value or "")
    if len(text) > max_chars:
        text = text[:max_chars]
    counts: dict[str, int] = {}
    for code, pattern in _VALUE_PATTERNS:
        matches = pattern.findall(text)
        if matches:
            counts[code] = len(matches)
            text = pattern.sub(f"[REDACTED:{code}]", text)
    return text, counts


def _redact_value(
    value: Any,
    *,
    key: str,
    depth: int,
    max_depth: int,
    max_items: int,
    max_string_chars: int,
) -> tuple[Any, dict[str, int]]:
    if depth > max_depth:
        raise ValueError("A catalog record exceeds the configured nesting depth.")
    if _SENSITIVE_KEY.search(key):
        return "[REDACTED:SENSITIVE_FIELD]", {"SENSITIVE_FIELD": 1}
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value, {}
    if isinstance(value, float):
        return (value if math.isfinite(value) else None), {}
    if isinstance(value, str):
        return _redact_string(value, max_string_chars)
    if isinstance(value, list):
        if len(value) > max_items:
            raise ValueError("A catalog record contains too many list items.")
        result: list[Any] = []
        counts: dict[str, int] = {}
        for item in value:
            safe, item_counts = _redact_value(
                item,
                key=key,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                max_string_chars=max_string_chars,
            )
            result.append(safe)
            _merge_counts(counts, item_counts)
        return result, counts
    if isinstance(value, dict):
        if len(value) > max_items:
            raise ValueError("A catalog record contains too many object fields.")
        result_dict: dict[str, Any] = {}
        counts = {}
        for raw_key, item in value.items():
            normalized_key = str(raw_key)[:256]
            safe, item_counts = _redact_value(
                item,
                key=normalized_key,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                max_string_chars=max_string_chars,
            )
            result_dict[normalized_key] = safe
            _merge_counts(counts, item_counts)
        return result_dict, counts
    safe_text, counts = _redact_string(value, max_string_chars)
    return safe_text, counts


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
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def _string_list(value: Any, maximum: int = 100) -> list[str]:
    values = value if isinstance(value, list) else []
    result: list[str] = []
    for item in values[:maximum]:
        text = str(item or "").strip().lower()
        if text and len(text) <= 128 and text not in result:
            result.append(text)
    return result


def _normalize_search_key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return re.sub(r"\s+", " ", text)[:500]


def _search_aliases(title: str, value: Any, maximum: int = 32) -> list[str]:
    explicit = value if isinstance(value, list) else []
    candidates = list(explicit[:maximum])
    candidates.extend(re.findall(r"[A-Za-z][A-Za-z0-9._-]{1,63}", unicodedata.normalize("NFKC", title)))
    title_key = _normalize_search_key(title)
    result: list[str] = []
    for item in candidates:
        normalized = _normalize_search_key(item)
        if normalized and normalized != title_key and normalized not in result:
            result.append(normalized)
        if len(result) >= maximum:
            break
    return result


def _identity_list(value: Any, maximum: int = 100) -> list[str]:
    """Preserve gateway-canonical subject IDs exactly; only groups fold case."""
    values = value if isinstance(value, list) else []
    result: list[str] = []
    for item in values[:maximum]:
        text = str(item or "").strip()
        if text and len(text) <= 128 and text not in result:
            result.append(text)
    return result


def _normalize_record(
    raw_record: dict[str, Any],
    *,
    tenant_id: str,
    source: dict[str, Any],
    max_depth: int = 12,
    max_items: int = 5000,
    max_string_chars: int = 100000,
) -> tuple[dict[str, Any], dict[str, int], list[str]]:
    safe_value, redaction_counts = _redact_value(
        raw_record,
        key="record",
        depth=0,
        max_depth=max_depth,
        max_items=max_items,
        max_string_chars=max_string_chars,
    )
    if not isinstance(safe_value, dict):
        raise ValueError("A catalog record must be an object.")

    asset_id = str(safe_value.get("id") or "").strip()
    title = str(safe_value.get("title") or "").strip()
    original_type = str(safe_value.get("type") or "").strip().lower()
    if not asset_id or len(asset_id) > 256:
        raise ValueError("Catalog field 'id' is required and must be at most 256 characters.")
    if not title or len(title) > 1000:
        raise ValueError("Catalog field 'title' is required and must be at most 1000 characters.")
    type_map = {"py": "component", "component": "component", "json": "flow", "flow": "flow"}
    if original_type not in type_map:
        raise ValueError("Catalog field 'type' must be py, component, json, or flow.")
    record_tenant = str(safe_value.get("tenant_id") or tenant_id).strip().lower()
    if record_tenant != tenant_id:
        raise ValueError("A catalog record cannot override the ingest tenant_id.")

    warnings: list[str] = []
    created_at = _normalize_datetime(safe_value.get("created_at"))
    updated_at = _normalize_datetime(safe_value.get("updated_at"))
    if safe_value.get("created_at") and created_at is None:
        warnings.append("INVALID_CREATED_AT")
    if safe_value.get("updated_at") and updated_at is None:
        warnings.append("INVALID_UPDATED_AT")

    raw_acl = safe_value.get("acl") if isinstance(safe_value.get("acl"), dict) else {}
    visibility = str(raw_acl.get("visibility") or "tenant").strip().lower()
    if visibility not in {"tenant", "group", "private"}:
        visibility = "tenant"
        warnings.append("INVALID_VISIBILITY_DEFAULTED")
    groups = _string_list(raw_acl.get("groups"))
    subjects = _identity_list(raw_acl.get("subjects"))
    if visibility == "group" and not groups:
        raise ValueError("Group-visible catalog records require at least one ACL group.")
    if visibility == "private" and not subjects:
        raise ValueError("Private catalog records require at least one ACL subject.")

    normalized = {
        "id": asset_id,
        "asset_id": asset_id,
        "title": title,
        "title_normalized": _normalize_search_key(title),
        "aliases_normalized": _search_aliases(title, safe_value.get("aliases")),
        "type": original_type,
        "asset_type": type_map[original_type],
        "description": str(safe_value.get("description") or "").strip(),
        "category": str(safe_value.get("category") or "Uncategorized").strip() or "Uncategorized",
        "version": str(safe_value.get("version") or "unversioned").strip() or "unversioned",
        "stars_count": _nonnegative_int(safe_value.get("stars_count")),
        "downloads_count": _nonnegative_int(safe_value.get("downloads_count")),
        "created_at": created_at,
        "updated_at": updated_at,
        "readme": str(safe_value.get("readme") or "").strip(),
        "raw_record_redacted": safe_value,
        "source": {
            "file_id": str(source.get("file_id") or ""),
            "record_index": int(source.get("record_index") or 0),
            "file_sha256": str(source.get("file_sha256") or ""),
        },
        "acl": {"visibility": visibility, "groups": groups, "subjects": subjects},
        "technical_contract": {
            "status": "metadata_only",
            "inputs": [],
            "outputs": [],
            "dependencies": [],
            "verified_at": None,
        },
        "relations": [],
    }
    return normalized, redaction_counts, warnings


class CatalogRecordNormalizerComponent(Component):
    display_name = "Catalog Record Normalizer"
    description = "Validate required metadata, redact sensitive values, and normalize staged catalog records in bounded batches."
    icon = "ListChecks"
    name = "CatalogRecordNormalizer"

    inputs = [
        DataInput(name="job_ref", display_name="Parsed Job Reference", required=True),
        SecretStrInput(name="mongodb_uri", display_name="MongoDB URI", required=True),
        StrInput(name="mongodb_database", display_name="MongoDB Database", value="business_work_design", required=True),
        IntInput(name="max_records_per_run", display_name="Maximum Records Per Run", value=2000, advanced=True),
        IntInput(name="max_nesting_depth", display_name="Maximum Nesting Depth", value=12, advanced=True),
        IntInput(name="max_collection_items", display_name="Maximum Items Per Field", value=5000, advanced=True),
        IntInput(name="max_string_chars", display_name="Maximum Characters Per String", value=100000, advanced=True),
        IntInput(name="connect_timeout_ms", display_name="MongoDB Connect Timeout (ms)", value=5000, advanced=True),
    ]

    outputs = [Output(name="normalized_job_ref", display_name="Normalized Job Reference", method="normalize_records", types=["Data"])]

    def normalize_records(self) -> Data:
        trace_id = "trace-unassigned"
        try:
            incoming = _validate_job_ref(getattr(self, "job_ref", None))
            trace_id = incoming["trace_id"]
            mongodb_uri = _secret_value(getattr(self, "mongodb_uri", ""))
            database_name = str(getattr(self, "mongodb_database", "") or "").strip()
            if not mongodb_uri or not database_name:
                return _failure("MONGODB_CONFIG_MISSING", "MongoDB configuration is required.", trace_id)
            max_records = _bounded_int(getattr(self, "max_records_per_run", 2000), 2000, 1, 5000)
            max_depth = _bounded_int(getattr(self, "max_nesting_depth", 12), 12, 2, 32)
            max_items = _bounded_int(getattr(self, "max_collection_items", 5000), 5000, 10, 10000)
            max_string_chars = _bounded_int(getattr(self, "max_string_chars", 100000), 100000, 1000, 500000)
            timeout_ms = _bounded_int(getattr(self, "connect_timeout_ms", 5000), 5000, 1000, 30000)

            client = MongoClient(
                mongodb_uri,
                connectTimeoutMS=timeout_ms,
                serverSelectionTimeoutMS=timeout_ms,
                socketTimeoutMS=max(timeout_ms, 10000),
                retryReads=True,
                retryWrites=True,
            )
            try:
                client.admin.command("ping")
                database = client[database_name]
                jobs = database["catalog_ingest_jobs"]
                staging = database["catalog_ingest_chunks"]
                query = {"_id": incoming["job_id"], "tenant_id": incoming["tenant_id"], "snapshot_id": incoming["snapshot_id"]}
                job = jobs.find_one(query)
                if not job:
                    return _failure("CATALOG_JOB_NOT_FOUND", "The catalog ingest job was not found for this tenant.", trace_id)
                current_stage = str(job.get("stage") or "")
                if current_stage == "NORMALIZE_COMPLETED":
                    cursor = int((job.get("stage_cursors") or {}).get("normalize") or 0)
                    return Data(data=_job_ref(incoming, current_stage, cursor))
                if current_stage not in {"PARSE_COMPLETED", "NORMALIZE_PARTIAL"}:
                    return _failure("CATALOG_STAGE_CONFLICT", "Normalization requires a fully parsed job.", trace_id)

                durable_cursor = int((job.get("stage_cursors") or {}).get("normalize") or 0)
                parse_cursor = int((job.get("stage_cursors") or {}).get("parse") or 0)
                valid_transition = (
                    current_stage == "PARSE_COMPLETED"
                    and incoming["stage"] == "PARSE_COMPLETED"
                    and incoming["expected_cursor"] == parse_cursor
                ) or (
                    current_stage == "NORMALIZE_PARTIAL"
                    and incoming["stage"] == "NORMALIZE_PARTIAL"
                    and incoming["expected_cursor"] == durable_cursor
                )
                if not valid_transition:
                    return _failure("CATALOG_CURSOR_CONFLICT", "The normalization cursor is stale.", trace_id)
                documents = list(
                    staging.find(
                        {"tenant_id": incoming["tenant_id"], "job_id": incoming["job_id"], "record_index": {"$gte": durable_cursor}}
                    ).sort("record_index", 1).limit(max_records + 1)
                )
                has_more = len(documents) > max_records
                selected = documents[:max_records]
                operations: list[UpdateOne] = []
                normalized_count = 0
                quarantined_count = 0
                next_cursor = durable_cursor
                now = _utc_now()
                for document in selected:
                    record_index = int(document.get("record_index") or 0)
                    next_cursor = record_index + 1
                    if document.get("normalize_status") == "NORMALIZED" and isinstance(document.get("normalized_record"), dict):
                        # A previous attempt may have persisted this idempotent
                        # record update before the job cursor CAS committed.
                        # Count it in the recovered batch and never downgrade it.
                        normalized_count += 1
                        continue
                    if document.get("parse_status") != "PARSED" or not isinstance(document.get("raw_record"), dict):
                        operations.append(
                            UpdateOne(
                                {"_id": document["_id"], "tenant_id": incoming["tenant_id"]},
                                {"$set": {"normalize_status": "SKIPPED_PARSE_QUARANTINE", "updated_at": now}},
                            )
                        )
                        quarantined_count += 1
                        continue
                    try:
                        normalized, redaction_counts, warnings = _normalize_record(
                            document["raw_record"],
                            tenant_id=incoming["tenant_id"],
                            source=document.get("source") or {},
                            max_depth=max_depth,
                            max_items=max_items,
                            max_string_chars=max_string_chars,
                        )
                        update_doc = {
                            "$set": {
                                "normalize_status": "NORMALIZED",
                                "normalized_record": normalized,
                                "redaction_summary": {"counts": redaction_counts, "redacted": bool(redaction_counts)},
                                "normalize_warnings": warnings,
                                "updated_at": now,
                            },
                        }
                        normalized_count += 1
                    except ValueError as exc:
                        update_doc = {
                            "$set": {
                                "normalize_status": "QUARANTINED_SCHEMA",
                                "normalize_error_code": "INVALID_CATALOG_RECORD",
                                "normalize_error_message": str(exc)[:300],
                                "updated_at": now,
                            },
                        }
                        quarantined_count += 1
                    operations.append(UpdateOne({"_id": document["_id"], "tenant_id": incoming["tenant_id"]}, update_doc))

                if operations:
                    try:
                        staging.bulk_write(operations, ordered=False)
                    except BulkWriteError as exc:
                        if (exc.details or {}).get("writeErrors"):
                            raise
                next_stage = "NORMALIZE_PARTIAL" if has_more else "NORMALIZE_COMPLETED"
                update = jobs.update_one(
                    {**query, "stage": current_stage, "stage_cursors.normalize": durable_cursor},
                    {
                        "$set": {
                            "stage": next_stage,
                            "expected_cursor": next_cursor,
                            "stage_cursors.normalize": next_cursor,
                            "normalize_completed": not has_more,
                            "updated_at": now,
                        },
                        "$inc": {"counts.normalized": normalized_count, "counts.normalize_quarantined": quarantined_count},
                    },
                )
                if update.modified_count != 1:
                    return _failure("CATALOG_CURSOR_CONFLICT", "The normalization cursor changed during this batch.", trace_id, True)
                self.status = (
                    f"Catalog normalization batch stored: job_id={incoming['job_id']}, normalized={normalized_count}, "
                    f"quarantined={quarantined_count}, cursor={next_cursor}, complete={not has_more}"
                )
                return Data(data=_job_ref(incoming, next_stage, next_cursor))
            finally:
                client.close()
        except ValueError as exc:
            self.status = "Catalog normalization rejected by input validation."
            return _failure("CATALOG_NORMALIZE_INVALID", str(exc), trace_id)
        except PyMongoError:
            self.status = "Catalog normalization failed before the durable cursor advanced."
            return _failure(
                "CATALOG_NORMALIZE_FAILED",
                "The normalization batch could not be stored. Resume from the last durable cursor.",
                trace_id,
                retryable=True,
            )
