from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from lfx.custom import Component
from lfx.io import BoolInput, DataInput, IntInput, Output
from lfx.schema import Data


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except Exception:
        number = default
    return max(minimum, min(maximum, number))


def _payload_from_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {"documents": value}

    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        return {"documents": data}

    text = getattr(value, "text", None) or getattr(value, "content", None)
    if isinstance(value, str):
        text = value
    if isinstance(text, str) and text.strip():
        try:
            parsed = json.loads(text)
        except Exception:
            return {}
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"documents": parsed}
    return {}


def _version_key(value: Any) -> tuple[tuple[int, Any], ...]:
    """Return a deterministic natural-sort key for common document versions."""
    text = str(value or "1").strip().lower()
    parts = re.findall(r"\d+|[^\d]+", text)
    key: list[tuple[int, Any]] = []
    for part in parts:
        cleaned = part.strip("._+- ")
        if not cleaned:
            continue
        if cleaned.isdigit():
            key.append((1, int(cleaned)))
        else:
            key.append((0, cleaned))
    return tuple(key) or ((1, 1),)


def _string_list(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    result: list[str] = []
    for item in values:
        text = str(item or "").strip().lower()
        if text and text not in result:
            result.append(text)
    return result


def _normalized_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _split_text(text: str, chunk_chars: int, overlap_chars: int) -> list[str]:
    text = _normalized_text(text)
    if not text:
        return []

    chunk_chars = max(50, int(chunk_chars))
    overlap_chars = max(0, min(int(overlap_chars), chunk_chars // 2))
    chunks: list[str] = []
    start = 0
    text_length = len(text)
    while start < text_length:
        proposed_end = min(text_length, start + chunk_chars)
        end = proposed_end
        if proposed_end < text_length:
            # Prefer a word boundary in the second half of the window.  If no
            # boundary exists (for example, a long identifier), use the exact
            # character limit so the loop always makes progress.
            boundary = text.rfind(" ", start + chunk_chars // 2, proposed_end)
            if boundary > start:
                end = boundary

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= text_length:
            break
        next_start = end - overlap_chars
        start = next_start if next_start > start else end
    return chunks


def _page_number(value: Any) -> int | None:
    try:
        page = int(value)
    except Exception:
        return None
    return page if page > 0 else None


def _latest_document_records(documents: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Select the latest active version per document and return delete plans."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    invalid_count = 0
    for document in documents:
        if not isinstance(document, dict):
            invalid_count += 1
            continue
        document_id = str(document.get("document_id") or "").strip()
        if not document_id:
            invalid_count += 1
            continue
        grouped.setdefault(document_id, []).append(document)

    selected: list[dict[str, Any]] = []
    delete_operations: list[dict[str, Any]] = []
    for document_id, records in grouped.items():
        latest_version = max((str(record.get("version") or "1") for record in records), key=_version_key)
        latest_records = [record for record in records if str(record.get("version") or "1") == latest_version]

        has_tombstone = any(
            _as_bool(record.get("deleted"), False) or not _as_bool(record.get("active"), True)
            for record in latest_records
        )
        if has_tombstone:
            reason = "tombstone" if any(_as_bool(record.get("deleted"), False) for record in latest_records) else "inactive"
            delete_operations.append(
                {
                    "operation": "delete_document",
                    "document_id": document_id,
                    "version": latest_version,
                    "reason": reason,
                }
            )
            continue

        selected.extend(latest_records)
    return selected, delete_operations, invalid_count


def _all_active_records(documents: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    active: list[dict[str, Any]] = []
    delete_operations: list[dict[str, Any]] = []
    invalid_count = 0
    for document in documents:
        if not isinstance(document, dict):
            invalid_count += 1
            continue
        document_id = str(document.get("document_id") or "").strip()
        if not document_id:
            invalid_count += 1
            continue
        version = str(document.get("version") or "1")
        if _as_bool(document.get("deleted"), False) or not _as_bool(document.get("active"), True):
            delete_operations.append(
                {
                    "operation": "delete_document_version",
                    "document_id": document_id,
                    "version": version,
                    "reason": "tombstone" if _as_bool(document.get("deleted"), False) else "inactive",
                }
            )
        else:
            active.append(document)
    return active, delete_operations, invalid_count


def _chunk_record(document: dict[str, Any], chunk: str, chunk_index: int) -> dict[str, Any]:
    document_id = str(document.get("document_id") or "").strip()
    version = str(document.get("version") or "1").strip()
    page = _page_number(document.get("page"))
    locator = str(document.get("source_locator") or "").strip()
    if not locator:
        locator = f"page:{page}" if page is not None else f"document:{document_id}"

    chunk_content_hash = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
    identity = "|".join([document_id, version, locator, str(chunk_index), chunk_content_hash])
    chunk_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    metadata = {
        "document_id": document_id,
        "title": str(document.get("title") or document_id).strip(),
        "version": version,
        "page": page,
        "source_locator": locator,
        "source_url": str(document.get("source_url") or "").strip(),
        "source_name": str(document.get("source_name") or "").strip(),
        "source_file": str(document.get("source_name") or "").strip(),
        "tenant_id": str(document.get("tenant_id") or "").strip().lower(),
        "visibility": str(document.get("visibility") or document.get("classification") or "internal").strip().lower(),
        "classification": str(document.get("classification") or "internal").strip().lower(),
        "allowed_roles": _string_list(document.get("allowed_roles")),
        "allowed_groups": _string_list(document.get("allowed_groups")),
        "chunk_index": chunk_index,
        "chunk_content_hash": chunk_content_hash,
    }
    return {
        "id": chunk_id,
        "chunk_id": chunk_id,
        "text": chunk,
        "page_content": chunk,
        "metadata": metadata,
    }


def build_document_chunk_index(
    value: Any,
    *,
    chunk_chars: int = 900,
    overlap_chars: int = 120,
    max_chunks: int = 5_000,
    latest_version_only: bool = True,
) -> dict[str, Any]:
    """Build a deterministic, payload-local retrieval index.

    This component deliberately does not claim persistent vector-store writes.
    `operations` describes how a later persistent indexer should replace or
    delete versions, while `chunks` is immediately usable by the model-free
    lexical retriever in the example flow.
    """
    payload = _payload_from_value(value)
    raw_documents = payload.get("documents")
    documents = raw_documents if isinstance(raw_documents, list) else []
    chunk_chars = _clamp_int(chunk_chars, 900, 50, 20_000)
    overlap_chars = _clamp_int(overlap_chars, 120, 0, chunk_chars // 2)
    max_chunks = _clamp_int(max_chunks, 5_000, 1, 50_000)
    select_latest = _as_bool(latest_version_only, True)

    errors: list[str] = []
    warnings: list[str] = []
    if select_latest:
        selected_documents, delete_operations, invalid_document_count = _latest_document_records(documents)
    else:
        selected_documents, delete_operations, invalid_document_count = _all_active_records(documents)

    chunks_by_id: dict[str, dict[str, Any]] = {}
    source_chunk_count = 0
    limit_reached = False
    for document in selected_documents:
        content = str(document.get("content") or "").strip()
        if not content:
            invalid_document_count += 1
            continue
        for chunk_index, chunk in enumerate(_split_text(content, chunk_chars, overlap_chars), start=1):
            source_chunk_count += 1
            record = _chunk_record(document, chunk, chunk_index)
            chunks_by_id.setdefault(record["id"], record)
            if len(chunks_by_id) >= max_chunks:
                limit_reached = True
                break
        if limit_reached:
            break

    chunks = list(chunks_by_id.values())
    deduplicated_chunk_count = max(0, source_chunk_count - len(chunks))

    chunk_ids_by_document: dict[tuple[str, str], list[str]] = {}
    for chunk in chunks:
        metadata = chunk["metadata"]
        key = (metadata["document_id"], metadata["version"])
        chunk_ids_by_document.setdefault(key, []).append(chunk["id"])

    upsert_operations: list[dict[str, Any]] = []
    for (document_id, version), chunk_ids in sorted(chunk_ids_by_document.items()):
        upsert_operations.append(
            {
                "operation": "replace_document_version" if select_latest else "upsert_document_version",
                "document_id": document_id,
                "version": version,
                "delete_previous_versions": select_latest,
                "upsert_chunk_ids": chunk_ids,
            }
        )
    operations = delete_operations + upsert_operations

    if not documents:
        errors.append("No documents were available for chunk indexing.")
    if invalid_document_count:
        warnings.append("One or more document records were skipped because required indexing fields were missing.")
    if deduplicated_chunk_count:
        warnings.append("Duplicate chunks were removed by their stable chunk IDs.")
    if limit_reached:
        warnings.append("The maximum chunk limit was reached; remaining content was not indexed.")
    if documents and not chunks and delete_operations:
        warnings.append("The current payload contains deletion or inactive records and no active chunks.")
    if documents and not chunks and not delete_operations:
        errors.append("No active text chunks could be built from the document payload.")

    # Deletion-only payloads are valid ingestion plans. They are intentionally
    # successful even though the model-free current-run index is empty.
    success = bool(chunks or delete_operations) and not errors
    selected_document_ids = sorted({chunk["metadata"]["document_id"] for chunk in chunks})
    selected_versions = {
        document_id: sorted(
            {chunk["metadata"]["version"] for chunk in chunks if chunk["metadata"]["document_id"] == document_id},
            key=_version_key,
        )[-1]
        for document_id in selected_document_ids
    }

    return {
        "success": success,
        "stage": "document_chunk_index_builder",
        "backend": "payload_lexical_v1",
        "persistence": "ephemeral_current_run",
        "chunks": chunks,
        "chunk_count": len(chunks),
        "document_count": len(selected_document_ids),
        "selected_versions": selected_versions,
        "operations": operations,
        "operation_count": len(operations),
        "delete_operation_count": len(delete_operations),
        "deduplicated_chunk_count": deduplicated_chunk_count,
        "settings": {
            "chunk_chars": chunk_chars,
            "overlap_chars": overlap_chars,
            "max_chunks": max_chunks,
            "latest_version_only": select_latest,
        },
        "errors": errors,
        "warnings": warnings,
        "trace": [
            {
                "stage": "document_chunk_index_builder",
                "status": "success" if success else "failed",
                "backend": "payload_lexical_v1",
                "input_document_count": len(documents),
                "indexed_document_count": len(selected_document_ids),
                "chunk_count": len(chunks),
                "delete_operation_count": len(delete_operations),
                "deduplicated_chunk_count": deduplicated_chunk_count,
                "limit_reached": limit_reached,
            }
        ],
    }


class DocumentChunkIndexBuilder(Component):
    display_name = "02 Document Chunk Index Builder"
    description = "Build stable, deduplicated chunks and version/tombstone operations for a model-free payload index."
    icon = "DatabaseZap"
    name = "DocumentChunkIndexBuilder"

    inputs = [
        DataInput(
            name="documents",
            display_name="Safe Documents",
            input_types=["Data", "JSON"],
            required=True,
        ),
        IntInput(name="chunk_chars", display_name="Chunk Characters", value=900, advanced=True),
        IntInput(name="overlap_chars", display_name="Overlap Characters", value=120, advanced=True),
        IntInput(name="max_chunks", display_name="Maximum Chunks", value=5000, advanced=True),
        BoolInput(
            name="latest_version_only",
            display_name="Keep Latest Version Only",
            value=True,
            advanced=True,
        ),
    ]

    outputs = [
        Output(
            name="document_index",
            display_name="Document Index",
            method="build_document_index",
            types=["Data"],
        )
    ]

    def build_document_index(self) -> Data:
        result = build_document_chunk_index(
            getattr(self, "documents", None),
            chunk_chars=getattr(self, "chunk_chars", 900),
            overlap_chars=getattr(self, "overlap_chars", 120),
            max_chunks=getattr(self, "max_chunks", 5000),
            latest_version_only=getattr(self, "latest_version_only", True),
        )
        self.status = {
            "success": result["success"],
            "document_count": result["document_count"],
            "chunk_count": result["chunk_count"],
            "delete_operation_count": result["delete_operation_count"],
            "deduplicated_chunk_count": result["deduplicated_chunk_count"],
            "warning_count": len(result["warnings"]),
            "error_count": len(result["errors"]),
        }
        return Data(data=result)
