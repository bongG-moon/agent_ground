from __future__ import annotations

import hashlib
import json
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, IntInput, Output
from lfx.schema import Data


_INGEST_CONTRACT_VERSION = "catalog-file-vector-ingest/v1"
_PARENT_REQUIRED_FIELDS = {
    "tenant_id",
    "catalog_id",
    "asset_id",
    "version",
    "asset_type",
    "title",
    "acl",
    "lexical_text_redacted",
    "raw_record_redacted",
    "raw_text_redacted",
    "raw_record_redacted_sha256",
    "content_sha256",
}
_CHUNK_SHARED_FIELDS = (
    "tenant_id",
    "catalog_id",
    "asset_id",
    "version",
    "asset_type",
    "title",
    "title_normalized",
    "aliases_normalized",
    "description",
    "category",
    "readme",
    "catalog_url",
    "acl",
    "technical_contract_status",
    "technical_contract",
    "ports",
    "relations",
    "stars_count",
    "downloads_count",
    "created_at",
    "updated_at",
    "source",
)


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


def _payload(value: Any) -> dict[str, Any]:
    data = getattr(value, "data", None)
    value = data if isinstance(data, dict) else value
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("catalog_bundle must be valid JSON.") from exc
    if not isinstance(value, dict):
        raise ValueError("catalog_bundle must be an object.")
    return value


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
            lower_bound = start + chunk_chars // 2
            boundary = max(text.rfind("\n", lower_bound, proposed_end), text.rfind(" ", lower_bound, proposed_end))
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
        raise ValueError("A catalog record requires more chunks than the configured per-record maximum.")
    return chunks


def _validate_catalog_bundle(bundle: dict[str, Any]) -> tuple[str, str, str, int, list[dict[str, Any]]]:
    if not bundle.get("ok") or bundle.get("status") != "LOADED":
        raise ValueError("A successful normalized catalog bundle is required.")
    if bundle.get("schema_version") != "catalog-normalized-bundle/v1":
        raise ValueError("catalog_bundle schema_version is not supported.")
    if bundle.get("ingest_contract_version") != _INGEST_CONTRACT_VERSION:
        raise ValueError("catalog_bundle ingest contract version is not supported.")
    tenant_id = str(bundle.get("tenant_id") or "").strip()
    catalog_id = str(bundle.get("catalog_id") or "").strip()
    source_sha256 = str(bundle.get("source_sha256") or "").strip()
    source_size_bytes = bundle.get("source_size_bytes")
    records = bundle.get("records")
    if not tenant_id or not catalog_id or len(source_sha256) != 64:
        raise ValueError("catalog_bundle scope or source hash is missing.")
    if isinstance(source_size_bytes, bool) or not isinstance(source_size_bytes, int) or source_size_bytes <= 0:
        raise ValueError("catalog_bundle source size is invalid.")
    if not isinstance(records, list) or not records:
        raise ValueError("catalog_bundle records are required.")
    seen: set[tuple[str, str]] = set()
    validated: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict) or not _PARENT_REQUIRED_FIELDS.issubset(record):
            raise ValueError(f"Normalized catalog record {index} is incomplete.")
        if record.get("tenant_id") != tenant_id or record.get("catalog_id") != catalog_id:
            raise ValueError("A normalized catalog record cannot change tenant or catalog scope.")
        identity = (str(record.get("asset_id") or ""), str(record.get("version") or ""))
        if not all(identity) or identity in seen:
            raise ValueError("Normalized catalog identities must be present and unique.")
        seen.add(identity)
        lexical_text = record.get("lexical_text_redacted")
        if not isinstance(lexical_text, str) or not lexical_text.strip():
            raise ValueError(f"Normalized catalog record {index} has no searchable text.")
        validated.append(record)
    return tenant_id, catalog_id, source_sha256, source_size_bytes, validated


def _build_chunk_bundle(
    catalog_bundle: dict[str, Any],
    *,
    chunk_chars: int,
    overlap_chars: int,
    max_chunks_per_record: int,
    max_total_chunks: int,
) -> dict[str, Any]:
    tenant_id, catalog_id, source_sha256, source_size_bytes, records = _validate_catalog_bundle(catalog_bundle)
    parents: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []
    for record in records:
        parent = dict(record)
        text_chunks = _split_text(
            str(parent["lexical_text_redacted"]),
            chunk_chars,
            overlap_chars,
            max_chunks_per_record,
        )
        if not text_chunks:
            raise ValueError(f"Catalog asset {parent['asset_id']} did not produce searchable text.")
        if len(chunks) + len(text_chunks) > max_total_chunks:
            raise ValueError("The uploaded catalog requires more chunks than the configured total maximum.")
        parent["chunk_count"] = len(text_chunks)
        parents.append(parent)
        shared = {field: parent.get(field) for field in _CHUNK_SHARED_FIELDS}
        for ordinal, chunk_text in enumerate(text_chunks):
            chunk_id = "whole" if len(text_chunks) == 1 else f"chunk-{ordinal:04d}"
            chunks.append(
                {
                    **shared,
                    "chunk_id": chunk_id,
                    "chunk_ordinal": ordinal,
                    "lexical_text_redacted": chunk_text,
                    "embedding_text_redacted": chunk_text,
                    "embedding_input_sha256": _sha256_text(chunk_text),
                    "parent_content_sha256": str(parent["content_sha256"]),
                }
            )
    ingest_sha256 = _sha256_text(
        _canonical_json(
            {
                "assets": [item["content_sha256"] for item in parents],
                "chunks": [item["embedding_input_sha256"] for item in chunks],
            }
        )
    )
    return {
        "ok": True,
        "status": "CHUNKED",
        "schema_version": "catalog-chunk-bundle/v1",
        "ingest_contract_version": _INGEST_CONTRACT_VERSION,
        "tenant_id": tenant_id,
        "catalog_id": catalog_id,
        "source_sha256": source_sha256,
        "source_size_bytes": source_size_bytes,
        "ingest_sha256": ingest_sha256,
        "chunk_policy": {
            "chunk_chars": chunk_chars,
            "overlap_chars": overlap_chars,
            "max_chunks_per_record": max_chunks_per_record,
            "max_total_chunks": max_total_chunks,
            "max_text_chars": int(catalog_bundle.get("max_text_chars") or 60000),
        },
        "parents": parents,
        "chunks": chunks,
        "counts": {"records": len(parents), "chunks": len(chunks)},
    }


def _failure(code: str, message: str) -> Data:
    return Data(data={"ok": False, "status": "BLOCKED", "error": {"code": code, "message": message, "retryable": False}})


class CatalogDeterministicChunkerComponent(Component):
    display_name = "01 Catalog Deterministic Chunker"
    description = "Split normalized catalog text deterministically while preserving parent identity, hashes, ACL, and metadata."
    icon = "SplitSquareVertical"
    name = "CatalogDeterministicChunker"

    inputs = [
        DataInput(name="catalog_bundle", display_name="Normalized Catalog Bundle", required=True),
        IntInput(name="chunk_chars", display_name="Chunk Size (Characters)", value=6000, required=True),
        IntInput(name="overlap_chars", display_name="Chunk Overlap (Characters)", value=200, required=True),
        IntInput(name="max_chunks_per_record", display_name="Maximum Chunks Per Record", value=16, advanced=True),
        IntInput(name="max_total_chunks", display_name="Maximum Total Chunks", value=200000, advanced=True),
    ]

    outputs = [Output(name="chunk_bundle", display_name="Deterministic Chunk Bundle", method="build_chunks", types=["Data"])]

    def build_chunks(self) -> Data:
        try:
            bundle = _payload(getattr(self, "catalog_bundle", None))
            chunk_chars = _bounded_int(getattr(self, "chunk_chars", 6000), 6000, 500, 20000)
            overlap_chars = _bounded_int(getattr(self, "overlap_chars", 200), 200, 0, chunk_chars // 3)
            max_chunks = _bounded_int(getattr(self, "max_chunks_per_record", 16), 16, 1, 64)
            max_total = _bounded_int(getattr(self, "max_total_chunks", 200000), 200000, 1, 1000000)
            result = _build_chunk_bundle(
                bundle,
                chunk_chars=chunk_chars,
                overlap_chars=overlap_chars,
                max_chunks_per_record=max_chunks,
                max_total_chunks=max_total,
            )
            self.status = f"Created {result['counts']['chunks']} chunks from {result['counts']['records']} catalog records."
            return Data(data=result)
        except (TypeError, ValueError) as exc:
            self.status = "Catalog chunking was blocked."
            return _failure("CATALOG_CHUNKING_INVALID", str(exc))
