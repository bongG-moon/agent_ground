from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from pymongo import MongoClient, UpdateOne
from pymongo.errors import BulkWriteError, PyMongoError

from lfx.custom import Component
from lfx.io import DataInput, IntInput, Output, SecretStrInput, StrInput
from lfx.schema import Data


_JOB_REF_KEYS = ("tenant_id", "job_id", "snapshot_id", "stage", "expected_cursor", "trace_id")


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


def _clean_text(value: Any) -> str:
    return re.sub(r"[\t ]+", " ", str(value or "").replace("\x00", "")).strip()


def _split_text(text: str, chunk_chars: int, overlap_chars: int, max_chunks: int) -> list[str]:
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    complete = False
    while start < len(text) and len(chunks) < max_chunks:
        proposed_end = min(len(text), start + chunk_chars)
        end = proposed_end
        if proposed_end < len(text):
            boundary = max(text.rfind("\n", start + chunk_chars // 2, proposed_end), text.rfind(" ", start + chunk_chars // 2, proposed_end))
            if boundary > start:
                end = boundary
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            complete = True
            break
        next_start = max(0, end - overlap_chars)
        start = next_start if next_start > start else end
    if not complete:
        raise ValueError("The canonical text requires more chunks than the configured per-asset limit.")
    return chunks


def _build_asset_documents(
    normalized: dict[str, Any],
    *,
    tenant_id: str,
    snapshot_id: str,
    max_text_chars: int,
    chunk_chars: int,
    overlap_chars: int,
    max_chunks: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(normalized, dict):
        raise ValueError("A normalized catalog record is required.")
    asset_id = str(normalized.get("asset_id") or normalized.get("id") or "").strip()
    version = str(normalized.get("version") or "unversioned").strip()
    title = _clean_text(normalized.get("title"))
    if not asset_id or not title:
        raise ValueError("Normalized records require asset_id and title.")

    technical = normalized.get("technical_contract") if isinstance(normalized.get("technical_contract"), dict) else {}
    text_fields = [
        ("title", title),
        ("type", f"{_clean_text(normalized.get('type'))} / {_clean_text(normalized.get('asset_type'))}"),
        ("category", _clean_text(normalized.get("category"))),
        ("description", _clean_text(normalized.get("description"))),
        ("readme", _clean_text(normalized.get("readme"))),
    ]
    if technical.get("status") and technical.get("status") != "metadata_only":
        safe_contract = {
            "status": technical.get("status"),
            "inputs": technical.get("inputs") if isinstance(technical.get("inputs"), list) else [],
            "outputs": technical.get("outputs") if isinstance(technical.get("outputs"), list) else [],
            "dependencies": technical.get("dependencies") if isinstance(technical.get("dependencies"), list) else [],
        }
        text_fields.append(("technical_contract", json.dumps(safe_contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))
    raw_text = "\n".join(f"{label}: {text}" for label, text in text_fields if text).strip()
    if not raw_text:
        raise ValueError("The normalized record has no safe searchable text.")
    if len(raw_text) > max_text_chars:
        raw_text = raw_text[:max_text_chars].rstrip()

    content_basis = {
        "asset_id": asset_id,
        "version": version,
        "asset_type": normalized.get("asset_type"),
        "title_normalized": normalized.get("title_normalized"),
        "aliases_normalized": normalized.get("aliases_normalized"),
        "raw_text": raw_text,
        "acl": normalized.get("acl"),
        "technical_contract": technical,
    }
    content_sha256 = hashlib.sha256(
        json.dumps(content_basis, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    chunks = _split_text(raw_text, chunk_chars, overlap_chars, max_chunks)
    if not chunks:
        raise ValueError("The normalized record did not produce an embedding chunk.")

    asset_document = {
        "tenant_id": tenant_id,
        "snapshot_id": snapshot_id,
        "asset_id": asset_id,
        "asset_type": normalized.get("asset_type"),
        "title": title,
        "title_normalized": normalized.get("title_normalized") or "",
        "aliases_normalized": normalized.get("aliases_normalized") if isinstance(normalized.get("aliases_normalized"), list) else [],
        "version": version,
        "description": normalized.get("description") or "",
        "category": normalized.get("category") or "Uncategorized",
        "readme": normalized.get("readme") or "",
        "raw_record_redacted": normalized.get("raw_record_redacted") or {},
        "raw_text": raw_text,
        "source": normalized.get("source") or {},
        "popularity": {
            "stars": int(normalized.get("stars_count") or 0),
            "downloads": int(normalized.get("downloads_count") or 0),
        },
        "created_at": normalized.get("created_at"),
        "updated_at": normalized.get("updated_at"),
        "technical_contract": technical,
        "relations": normalized.get("relations") if isinstance(normalized.get("relations"), list) else [],
        "acl": normalized.get("acl") if isinstance(normalized.get("acl"), dict) else {"visibility": "tenant", "groups": []},
        "content_sha256": content_sha256,
    }
    chunk_documents: list[dict[str, Any]] = []
    for ordinal, chunk_text in enumerate(chunks):
        chunk_id = "whole" if len(chunks) == 1 else f"chunk-{ordinal:04d}"
        input_sha256 = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
        chunk_documents.append(
            {
                "tenant_id": tenant_id,
                "snapshot_id": snapshot_id,
                "asset_id": asset_id,
                "version": version,
                "asset_type": normalized.get("asset_type"),
                "title": title,
                "title_normalized": asset_document["title_normalized"],
                "aliases_normalized": asset_document["aliases_normalized"],
                "category": normalized.get("category") or "Uncategorized",
                "chunk_id": chunk_id,
                "chunk_ordinal": ordinal,
                "lexical_text_redacted": chunk_text,
                "embedding_text_redacted": chunk_text,
                "embedding_input_sha256": input_sha256,
                "acl": asset_document["acl"],
                "source": normalized.get("source") or {},
            }
        )
    return asset_document, chunk_documents


class CatalogEmbeddingTextBuilderComponent(Component):
    display_name = "Catalog Embedding Text Builder"
    description = "Build deterministic redacted parent and chunk documents without placing their contents on Flow edges."
    icon = "TextCursorInput"
    name = "CatalogEmbeddingTextBuilder"

    inputs = [
        DataInput(name="job_ref", display_name="Normalized Job Reference", required=True),
        SecretStrInput(name="mongodb_uri", display_name="MongoDB URI", required=True),
        StrInput(name="mongodb_database", display_name="MongoDB Database", value="business_work_design", required=True),
        IntInput(name="max_records_per_run", display_name="Maximum Records Per Run", value=1000, advanced=True),
        IntInput(name="max_text_chars", display_name="Maximum Canonical Text Characters", value=60000, advanced=True),
        IntInput(name="chunk_chars", display_name="Embedding Chunk Characters", value=6000, advanced=True),
        IntInput(name="overlap_chars", display_name="Chunk Overlap Characters", value=200, advanced=True),
        IntInput(name="max_chunks_per_asset", display_name="Maximum Chunks Per Asset", value=16, advanced=True),
        IntInput(name="connect_timeout_ms", display_name="MongoDB Connect Timeout (ms)", value=5000, advanced=True),
    ]

    outputs = [Output(name="text_built_job_ref", display_name="Text-Built Job Reference", method="build_text", types=["Data"])]

    def build_text(self) -> Data:
        trace_id = "trace-unassigned"
        try:
            incoming = _validate_job_ref(getattr(self, "job_ref", None))
            trace_id = incoming["trace_id"]
            mongodb_uri = _secret_value(getattr(self, "mongodb_uri", ""))
            database_name = str(getattr(self, "mongodb_database", "") or "").strip()
            if not mongodb_uri or not database_name:
                return _failure("MONGODB_CONFIG_MISSING", "MongoDB configuration is required.", trace_id)

            max_records = _bounded_int(getattr(self, "max_records_per_run", 1000), 1000, 1, 3000)
            max_text_chars = _bounded_int(getattr(self, "max_text_chars", 60000), 60000, 1000, 200000)
            chunk_chars = _bounded_int(getattr(self, "chunk_chars", 6000), 6000, 500, 20000)
            overlap_chars = _bounded_int(getattr(self, "overlap_chars", 200), 200, 0, chunk_chars // 3)
            max_chunks = _bounded_int(getattr(self, "max_chunks_per_asset", 16), 16, 1, 32)
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
                if current_stage == "TEXT_BUILD_COMPLETED":
                    cursor = int((job.get("stage_cursors") or {}).get("text") or 0)
                    return Data(data=_job_ref(incoming, current_stage, cursor))
                if current_stage not in {"NORMALIZE_COMPLETED", "TEXT_BUILD_PARTIAL"}:
                    return _failure("CATALOG_STAGE_CONFLICT", "Text building requires a fully normalized job.", trace_id)

                durable_cursor = int((job.get("stage_cursors") or {}).get("text") or 0)
                normalize_cursor = int((job.get("stage_cursors") or {}).get("normalize") or 0)
                valid_transition = (
                    current_stage == "NORMALIZE_COMPLETED"
                    and incoming["stage"] == "NORMALIZE_COMPLETED"
                    and incoming["expected_cursor"] == normalize_cursor
                ) or (
                    current_stage == "TEXT_BUILD_PARTIAL"
                    and incoming["stage"] == "TEXT_BUILD_PARTIAL"
                    and incoming["expected_cursor"] == durable_cursor
                )
                if not valid_transition:
                    return _failure("CATALOG_CURSOR_CONFLICT", "The text-build cursor is stale.", trace_id)
                documents = list(
                    staging.find(
                        {"tenant_id": incoming["tenant_id"], "job_id": incoming["job_id"], "record_index": {"$gte": durable_cursor}}
                    ).sort("record_index", 1).limit(max_records + 1)
                )
                has_more = len(documents) > max_records
                selected = documents[:max_records]
                operations: list[UpdateOne] = []
                built_count = 0
                quarantined_count = 0
                next_cursor = durable_cursor
                now = _utc_now()
                for document in selected:
                    record_index = int(document.get("record_index") or 0)
                    next_cursor = record_index + 1
                    if document.get("normalize_status") != "NORMALIZED" or not isinstance(document.get("normalized_record"), dict):
                        operations.append(
                            UpdateOne(
                                {"_id": document["_id"], "tenant_id": incoming["tenant_id"]},
                                {"$set": {"text_build_status": "SKIPPED_QUARANTINE", "updated_at": now}},
                            )
                        )
                        quarantined_count += 1
                        continue
                    try:
                        asset_document, chunk_documents = _build_asset_documents(
                            document["normalized_record"],
                            tenant_id=incoming["tenant_id"],
                            snapshot_id=incoming["snapshot_id"],
                            max_text_chars=max_text_chars,
                            chunk_chars=chunk_chars,
                            overlap_chars=overlap_chars,
                            max_chunks=max_chunks,
                        )
                        update_doc = {
                            "$set": {
                                "text_build_status": "TEXT_BUILT",
                                "asset_document": asset_document,
                                "asset_chunks": chunk_documents,
                                "updated_at": now,
                            }
                        }
                        built_count += 1
                    except ValueError as exc:
                        update_doc = {
                            "$set": {
                                "text_build_status": "QUARANTINED_TEXT",
                                "text_build_error_code": "TEXT_BUILD_INVALID",
                                "text_build_error_message": str(exc)[:300],
                                "updated_at": now,
                            }
                        }
                        quarantined_count += 1
                    operations.append(UpdateOne({"_id": document["_id"], "tenant_id": incoming["tenant_id"]}, update_doc))
                if operations:
                    try:
                        staging.bulk_write(operations, ordered=False)
                    except BulkWriteError as exc:
                        if (exc.details or {}).get("writeErrors"):
                            raise
                next_stage = "TEXT_BUILD_PARTIAL" if has_more else "TEXT_BUILD_COMPLETED"
                update = jobs.update_one(
                    {**query, "stage": current_stage, "stage_cursors.text": durable_cursor},
                    {
                        "$set": {
                            "stage": next_stage,
                            "expected_cursor": next_cursor,
                            "stage_cursors.text": next_cursor,
                            "text_build_completed": not has_more,
                            "updated_at": now,
                        },
                        "$inc": {"counts.text_built": built_count, "counts.text_quarantined": quarantined_count},
                    },
                )
                if update.modified_count != 1:
                    return _failure("CATALOG_CURSOR_CONFLICT", "The text-build cursor changed during this batch.", trace_id, True)
                self.status = (
                    f"Catalog text batch stored: job_id={incoming['job_id']}, built={built_count}, "
                    f"quarantined={quarantined_count}, cursor={next_cursor}, complete={not has_more}"
                )
                return Data(data=_job_ref(incoming, next_stage, next_cursor))
            finally:
                client.close()
        except ValueError as exc:
            self.status = "Catalog text building rejected by input validation."
            return _failure("CATALOG_TEXT_BUILD_INVALID", str(exc), trace_id)
        except PyMongoError:
            self.status = "Catalog text building failed before the durable cursor advanced."
            return _failure(
                "CATALOG_TEXT_BUILD_FAILED",
                "The text-build batch could not be stored. Resume from the last durable cursor.",
                trace_id,
                retryable=True,
            )
