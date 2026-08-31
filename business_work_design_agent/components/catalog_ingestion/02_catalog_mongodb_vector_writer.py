from __future__ import annotations

import hashlib
import json
import math
import re
import time
from datetime import datetime, timezone
from typing import Any

from lfx.custom import Component
from lfx.io import BoolInput, DataInput, FloatInput, HandleInput, IntInput, Output, SecretStrInput, StrInput
from lfx.schema import Data
from pymongo import ASCENDING, MongoClient, ReplaceOne
from pymongo.errors import PyMongoError


_SAFE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_INGEST_CONTRACT_VERSION = "catalog-file-vector-ingest/v1"
_EMBEDDING_CONTRACT_VERSION = "embedding-runtime-contract/v2"
# Keep this value independent from the cross-Flow ingest contract.  Bump it
# only when Writer persistence/validation semantics change in a way that must
# not reuse an older deterministic snapshot for the same uploaded file.
_WRITER_SEMANTIC_REVISION = "catalog-vector-writer/v2"
_EMBEDDING_CONTRACT_FIELDS = (
    "schema_version",
    "runtime_class",
    "model_id",
    "dimension",
    "fingerprint",
)
_MODEL_ID_ATTRIBUTES = (
    "model_name",
    "model",
    "model_id",
    "deployment_name",
    "deployment",
)
_MAX_EMBEDDING_DIMENSION = 65536
_MIN_EMBEDDING_CALL_INTERVAL_SECONDS = 1.0
_MAX_EMBEDDING_CALL_INTERVAL_SECONDS = 60.0
_MAX_EMBEDDING_RETRIES = 5
_RESUME_LOOKUP_BATCH_SIZE = 1000
_DEFAULT_MAX_EMBEDDING_CHUNKS_PER_RUN = 80
_MAX_EMBEDDING_CHUNKS_PER_RUN = 1000
_NATIVE_HITL_REASON = "human_input_required"
_CONTINUE_INGESTION_ACTION = "continue_ingestion"
_STOP_INGESTION_ACTION = "stop_ingestion"
# Keep a material safety margin below the 300-second Langflow execution limit.
# Loader/chunker work and final MongoDB validation happen in the same Flow run,
# so the Writer deliberately does not consume the full platform allowance.
_DEFAULT_EMBEDDING_RUN_TIME_BUDGET_SECONDS = 180
_MIN_EMBEDDING_RUN_TIME_BUDGET_SECONDS = 30
_MAX_EMBEDDING_RUN_TIME_BUDGET_SECONDS = 240
_DEFAULT_MONGO_CHECKPOINT_BATCH_SIZE = 10
_RUNTIME_CLASS_PATTERN = re.compile(
    r"^<class '([A-Za-z_][A-Za-z0-9_]*(?:\.(?:[A-Za-z_][A-Za-z0-9_]*|<locals>))*)'>$"
)
_SAFE_EXCEPTION_TYPE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
_CHUNK_PARENT_FIELDS = (
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


def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if not math.isfinite(number):
        number = default
    return max(minimum, min(maximum, number))


def _secret_value(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        return str(getter()).strip()
    return str(value or "").strip()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_document_id(parts: list[str]) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _payload(value: Any) -> dict[str, Any]:
    data = getattr(value, "data", None)
    value = data if isinstance(data, dict) else value
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("chunk_bundle must be valid JSON.") from exc
    if not isinstance(value, dict):
        raise ValueError("chunk_bundle must be an object.")
    return value


def _safe_identifier(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_IDENTIFIER_PATTERN.fullmatch(text):
        raise ValueError(f"{field_name} contains unsupported characters.")
    return text


def _direct_attribute(value: Any, name: str) -> Any:
    """Read only the small allowlist of embedding runtime metadata attributes."""
    try:
        if name == "embeddings":
            return getattr(value, "embeddings", None)
        if name == "available_models":
            return getattr(value, "available_models", None)
        if name == "model_name":
            return getattr(value, "model_name", None)
        if name == "model":
            return getattr(value, "model", None)
        if name == "model_id":
            return getattr(value, "model_id", None)
        if name == "deployment_name":
            return getattr(value, "deployment_name", None)
        if name == "deployment":
            return getattr(value, "deployment", None)
    except Exception:  # noqa: BLE001 - provider wrappers may reject a metadata property
        return None
    return None


def _underlying_embedding(embedding: Any) -> Any:
    """Unwrap Langflow's EmbeddingsWithModels without guessing through provider proxies."""
    underlying = _direct_attribute(embedding, "embeddings")
    return embedding if underlying is None else underlying


def _model_id_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return text if 0 < len(text) <= 500 else ""


def _model_id_from_available_models(embedding: Any, underlying: Any) -> str:
    """Use the configured Langflow model key only when it points to this exact instance."""
    available_models = _direct_attribute(embedding, "available_models")
    if not isinstance(available_models, dict):
        return ""
    matches = {
        text
        for key, candidate in available_models.items()
        if candidate is underlying and (text := _model_id_text(key))
    }
    if len(matches) > 1:
        raise ValueError("Embedding Model identity resolves to more than one configured model ID.")
    return next(iter(matches), "")


def _resolved_model_id(embedding: Any, underlying: Any) -> str:
    configured_model_id = _model_id_from_available_models(embedding, underlying)
    if configured_model_id:
        return configured_model_id
    for attribute in _MODEL_ID_ATTRIBUTES:
        model_id = _model_id_text(_direct_attribute(underlying, attribute))
        if model_id:
            return model_id
    raise ValueError(
        "The connected Embedding Model has no resolvable model ID. "
        "Use Langflow Embedding Model or a provider that exposes a supported model identifier."
    )


def _validated_dimension(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _MAX_EMBEDDING_DIMENSION:
        raise ValueError(f"The embedding vector dimension must be between 1 and {_MAX_EMBEDDING_DIMENSION}.")
    return value


def _embedding_runtime_contract(embedding: Any, dimension: Any) -> dict[str, Any]:
    """Build the exact, provider-derived contract stored with an active snapshot."""
    dimension_number = _validated_dimension(dimension)
    underlying = _underlying_embedding(embedding)
    runtime_match = _RUNTIME_CLASS_PATTERN.fullmatch(str(type(underlying)))
    runtime_class = runtime_match.group(1) if runtime_match else ""
    if not runtime_class or len(runtime_class) > 1000:
        raise ValueError("The connected Embedding Model runtime class cannot be identified.")
    core = {
        "schema_version": _EMBEDDING_CONTRACT_VERSION,
        "runtime_class": runtime_class,
        "model_id": _resolved_model_id(embedding, underlying),
        "dimension": dimension_number,
    }
    return {**core, "fingerprint": "sha256:" + _sha256_text(_canonical_json(core))}


def _validate_embedding_contract(contract: Any) -> dict[str, Any]:
    if not isinstance(contract, dict) or set(contract) != set(_EMBEDDING_CONTRACT_FIELDS):
        raise ValueError("embedding runtime contract must contain exactly the v2 contract fields.")
    if contract.get("schema_version") != _EMBEDDING_CONTRACT_VERSION:
        raise ValueError("embedding runtime contract schema version is not supported.")
    runtime_class = _model_id_text(contract.get("runtime_class"))
    model_id = _model_id_text(contract.get("model_id"))
    dimension = _validated_dimension(contract.get("dimension"))
    if not runtime_class or not model_id:
        raise ValueError("embedding runtime contract runtime_class and model_id are required.")
    core = {
        "schema_version": _EMBEDDING_CONTRACT_VERSION,
        "runtime_class": runtime_class,
        "model_id": model_id,
        "dimension": dimension,
    }
    expected_fingerprint = "sha256:" + _sha256_text(_canonical_json(core))
    if contract.get("fingerprint") != expected_fingerprint:
        raise ValueError("embedding runtime contract fingerprint is invalid.")
    return {**core, "fingerprint": expected_fingerprint}


def _deferred_embedding_contract() -> dict[str, Any]:
    """Mark a test-run result as non-publishable without inspecting a provider handle."""
    return {
        "schema_version": _EMBEDDING_CONTRACT_VERSION,
        "runtime_class": None,
        "model_id": None,
        "dimension": None,
        "fingerprint": None,
        "state": "DEFERRED",
    }


def _validate_chunk_bundle(bundle: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not bundle.get("ok") or bundle.get("status") != "CHUNKED":
        raise ValueError("A successful deterministic chunk bundle is required.")
    if bundle.get("schema_version") != "catalog-chunk-bundle/v1":
        raise ValueError("chunk_bundle schema_version is not supported.")
    if bundle.get("ingest_contract_version") != _INGEST_CONTRACT_VERSION:
        raise ValueError("chunk_bundle ingest contract version is not supported.")
    tenant_id = str(bundle.get("tenant_id") or "")
    catalog_id = str(bundle.get("catalog_id") or "")
    source_sha256 = str(bundle.get("source_sha256") or "")
    source_size_bytes = bundle.get("source_size_bytes")
    ingest_sha256 = str(bundle.get("ingest_sha256") or "")
    policy = bundle.get("chunk_policy")
    parents = bundle.get("parents")
    chunks = bundle.get("chunks")
    if not tenant_id or not catalog_id or len(source_sha256) != 64 or len(ingest_sha256) != 64:
        raise ValueError("chunk_bundle scope or digest is missing.")
    if isinstance(source_size_bytes, bool) or not isinstance(source_size_bytes, int) or source_size_bytes <= 0:
        raise ValueError("chunk_bundle source size is invalid.")
    if not isinstance(policy, dict):
        raise ValueError("chunk_bundle chunk policy is required.")
    for field in ("chunk_chars", "overlap_chars", "max_chunks_per_record", "max_text_chars"):
        if isinstance(policy.get(field), bool) or not isinstance(policy.get(field), int):
            raise ValueError("chunk_bundle chunk policy is invalid.")
    if not isinstance(parents, list) or not parents or not isinstance(chunks, list) or not chunks:
        raise ValueError("chunk_bundle parents and chunks are required.")
    identities: set[tuple[str, str]] = set()
    parent_by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    parent_hash_by_identity: dict[tuple[str, str], str] = {}
    record_indices: set[int] = set()
    chunk_identities: set[tuple[str, str, str]] = set()
    chunk_counts: dict[tuple[str, str], int] = {}
    chunk_ordinals: dict[tuple[str, str], list[int]] = {}
    chunk_ids_by_identity: dict[tuple[str, str], set[str]] = {}
    for parent in parents:
        if not isinstance(parent, dict):
            raise ValueError("chunk_bundle parent must be an object.")
        identity = (str(parent.get("asset_id") or ""), str(parent.get("version") or ""))
        if not all(identity) or identity in identities:
            raise ValueError("chunk_bundle parent identities must be present and unique.")
        if parent.get("tenant_id") != tenant_id or parent.get("catalog_id") != catalog_id:
            raise ValueError("chunk_bundle parent scope mismatch.")
        if parent.get("asset_type") not in {"component", "flow"} or not str(parent.get("title") or "").strip():
            raise ValueError("chunk_bundle parent search identity is invalid.")
        if not isinstance(parent.get("lexical_text_redacted"), str) or not parent["lexical_text_redacted"].strip():
            raise ValueError("chunk_bundle parent searchable text is missing.")
        acl = parent.get("acl")
        if not isinstance(acl, dict) or acl.get("visibility") not in {"tenant", "group", "private"}:
            raise ValueError("chunk_bundle parent ACL is invalid.")
        if acl.get("visibility") == "group" and not (
            isinstance(acl.get("groups"), list) and acl.get("groups")
        ):
            raise ValueError("chunk_bundle group ACL is invalid.")
        if acl.get("visibility") == "private" and not (
            isinstance(acl.get("subjects"), list) and acl.get("subjects")
        ):
            raise ValueError("chunk_bundle private ACL is invalid.")
        raw_record = parent.get("raw_record_redacted")
        raw_text = parent.get("raw_text_redacted")
        raw_sha256 = str(parent.get("raw_record_redacted_sha256") or "")
        if not isinstance(raw_record, dict) or not isinstance(raw_text, str):
            raise ValueError("chunk_bundle parent raw record contract is missing.")
        if raw_text != _canonical_json(raw_record) or raw_sha256 != _sha256_text(raw_text):
            raise ValueError("chunk_bundle parent raw record hash does not match its canonical text.")
        expected_content_sha256 = _sha256_text(
            _canonical_json(
                {
                    "identity": [identity[0], identity[1]],
                    "record_sha256": raw_sha256,
                    "technical_contract": parent.get("technical_contract"),
                    "acl": parent.get("acl"),
                }
            )
        )
        content_sha256 = str(parent.get("content_sha256") or "")
        if content_sha256 != expected_content_sha256:
            raise ValueError("chunk_bundle parent content hash is invalid.")
        source = parent.get("source")
        if not isinstance(source, dict):
            raise ValueError("chunk_bundle parent source contract is missing.")
        if source.get("file_sha256") != source_sha256 or source.get("file_size_bytes") != source_size_bytes:
            raise ValueError("chunk_bundle parent source does not match the uploaded file.")
        record_index = source.get("record_index")
        if isinstance(record_index, bool) or not isinstance(record_index, int) or record_index < 0:
            raise ValueError("chunk_bundle parent source record index is invalid.")
        if record_index in record_indices:
            raise ValueError("chunk_bundle parent source record index is duplicated.")
        record_indices.add(record_index)
        identities.add(identity)
        parent_by_identity[identity] = parent
        parent_hash_by_identity[identity] = content_sha256
        chunk_counts[identity] = 0
        chunk_ordinals[identity] = []
        chunk_ids_by_identity[identity] = set()
    if record_indices != set(range(len(parents))):
        raise ValueError("chunk_bundle parent source record indices must be contiguous from zero.")
    for chunk in chunks:
        if not isinstance(chunk, dict):
            raise ValueError("chunk_bundle chunk must be an object.")
        identity = (str(chunk.get("asset_id") or ""), str(chunk.get("version") or ""))
        chunk_id = str(chunk.get("chunk_id") or "")
        chunk_identity = (*identity, chunk_id)
        text = chunk.get("embedding_text_redacted")
        if identity not in identities or not chunk_id or chunk_identity in chunk_identities:
            raise ValueError("chunk_bundle chunk identity is invalid or duplicated.")
        if chunk.get("tenant_id") != tenant_id or chunk.get("catalog_id") != catalog_id:
            raise ValueError("chunk_bundle chunk scope mismatch.")
        ordinal = chunk.get("chunk_ordinal")
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
            raise ValueError("chunk_bundle chunk ordinal is invalid.")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("chunk_bundle embedding text is required.")
        if chunk.get("lexical_text_redacted") != text:
            raise ValueError("chunk_bundle lexical and embedding text must match.")
        if str(chunk.get("embedding_input_sha256") or "") != _sha256_text(text):
            raise ValueError("chunk_bundle embedding input hash does not match its text.")
        parent = parent_by_identity[identity]
        if str(chunk.get("parent_content_sha256") or "") != parent_hash_by_identity[identity]:
            raise ValueError("chunk_bundle parent content hash does not match.")
        if any(chunk.get(field) != parent.get(field) for field in _CHUNK_PARENT_FIELDS):
            raise ValueError("chunk_bundle chunk metadata does not match its authoritative parent.")
        if "embedding" in chunk:
            raise ValueError("chunk_bundle must not contain caller-supplied embeddings.")
        chunk_identities.add(chunk_identity)
        chunk_counts[identity] += 1
        chunk_ordinals[identity].append(ordinal)
        chunk_ids_by_identity[identity].add(chunk_id)
    for parent in parents:
        identity = (str(parent["asset_id"]), str(parent["version"]))
        declared_chunk_count = parent.get("chunk_count")
        if isinstance(declared_chunk_count, bool) or not isinstance(declared_chunk_count, int):
            raise ValueError("chunk_bundle parent chunk count is invalid.")
        if declared_chunk_count != chunk_counts[identity]:
            raise ValueError("chunk_bundle parent chunk count does not match.")
        expected_ordinals = list(range(declared_chunk_count))
        if sorted(chunk_ordinals[identity]) != expected_ordinals:
            raise ValueError("chunk_bundle chunk ordinals must be unique and contiguous from zero.")
        expected_ids = {"whole"} if declared_chunk_count == 1 else {f"chunk-{index:04d}" for index in expected_ordinals}
        if chunk_ids_by_identity[identity] != expected_ids:
            raise ValueError("chunk_bundle chunk IDs do not match their deterministic ordinals.")
    expected_ingest_sha256 = _sha256_text(
        _canonical_json(
            {
                "assets": [str(item["content_sha256"]) for item in parents],
                "chunks": [str(item["embedding_input_sha256"]) for item in chunks],
            }
        )
    )
    if ingest_sha256 != expected_ingest_sha256:
        raise ValueError("chunk_bundle ingest hash does not match its parent and chunk contents.")
    counts = bundle.get("counts")
    if not isinstance(counts, dict) or counts.get("records") != len(parents) or counts.get("chunks") != len(chunks):
        raise ValueError("chunk_bundle counts do not match its contents.")
    return parents, chunks


def _snapshot_id(bundle: dict[str, Any], embedding_contract: dict[str, Any]) -> str:
    validated_contract = _validate_embedding_contract(embedding_contract)
    policy = bundle["chunk_policy"]
    basis = {
        "ingest_contract_version": _INGEST_CONTRACT_VERSION,
        "writer_semantic_revision": _WRITER_SEMANTIC_REVISION,
        "tenant_id": bundle["tenant_id"],
        "catalog_id": bundle["catalog_id"],
        "source_sha256": bundle["source_sha256"],
        "embedding_contract": validated_contract,
        "chunk_chars": policy["chunk_chars"],
        "overlap_chars": policy["overlap_chars"],
        "max_chunks": policy["max_chunks_per_record"],
        "max_text_chars": policy["max_text_chars"],
    }
    return "snap-" + _sha256_text(_canonical_json(basis))[:24]


def _upload_semantics() -> dict[str, Any]:
    """Return the user-visible rule for F00's deliberately non-merge ingest."""
    return {
        "mode": "complete_catalog_snapshot_replace",
        "requires_complete_catalog_file": True,
        "rule": (
            "The uploaded file is the complete current catalog. Publishing it replaces the active snapshot; "
            "assets omitted from the file are not searchable in the next active snapshot."
        ),
    }


def _chunk_document_id(bundle: dict[str, Any], snapshot_id: str, chunk: dict[str, Any]) -> str:
    return _stable_document_id(
        [
            str(bundle["tenant_id"]),
            snapshot_id,
            str(chunk["asset_id"]),
            str(chunk["version"]),
            str(chunk["chunk_id"]),
        ]
    )


def _vectors(value: Any, expected_count: int, dimension: int) -> list[list[float]]:
    dimension = _validated_dimension(dimension)
    if not isinstance(value, list) or len(value) != expected_count:
        raise ValueError("The embedding response count does not match the batch.")
    result: list[list[float]] = []
    for vector in value:
        if not isinstance(vector, list) or len(vector) != dimension:
            raise ValueError("The embedding response vector dimension is invalid.")
        normalized: list[float] = []
        for item in vector:
            if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
                raise ValueError("The embedding response contains a non-finite or non-numeric value.")
            normalized.append(float(item))
        result.append(normalized)
    return result


def _vectors_with_derived_dimension(value: Any, expected_count: int) -> tuple[list[list[float]], int]:
    if not isinstance(value, list) or len(value) != expected_count or not value:
        raise ValueError("The first embedding response count does not match the batch.")
    first_vector = value[0]
    if not isinstance(first_vector, list):
        raise ValueError("The first embedding response vector is invalid.")
    dimension = _validated_dimension(len(first_vector))
    return _vectors(value, expected_count, dimension), dimension


def _single_vector_response(value: Any) -> list[Any] | None:
    """Recognize only an unambiguous, finite single-vector provider response.

    LangChain's ``Embeddings.embed_documents`` contract is a list of vectors, but
    a few provider adapters return one vector when given a list of documents.  A
    single response can safely trigger a per-document retry; a partial multi-vector
    response cannot, because its text-to-vector correspondence is unknown.
    """
    candidate = value
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], list):
        candidate = value[0]
    if not isinstance(candidate, list) or not candidate:
        return None
    for item in candidate:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
            return None
    return candidate


def _embed_documents(
    embedding: Any,
    texts: list[str],
    dimension: int | None = None,
) -> tuple[list[list[float]], int]:
    method = getattr(embedding, "embed_documents", None)
    if not callable(method):
        raise ValueError("The connected Embedding Model does not provide embed_documents.")
    try:
        raw_vectors = method(texts)
    except Exception as exc:  # noqa: BLE001 - provider SDKs expose heterogeneous exception classes
        raise RuntimeError("The connected Embedding Model failed.") from exc

    # A provider that returns exactly one valid vector for a multi-chunk request
    # has not supplied an index-preserving batch response.  Do not reuse that
    # vector for other chunks.  Instead retry one chunk per request, where each
    # response must still validate as exactly one vector.  Any partial or malformed
    # multi-vector response remains fail-closed below.
    single_vector = _single_vector_response(raw_vectors)
    if len(texts) > 1 and single_vector is not None:
        vectors: list[list[float]] = []
        current_dimension = dimension
        for text in texts:
            one_vector, current_dimension = _embed_documents(embedding, [text], current_dimension)
            if len(one_vector) != 1:
                raise ValueError("The embedding response did not return exactly one vector for a single chunk.")
            vectors.extend(one_vector)
        if current_dimension is None:
            raise ValueError("The embedding response did not provide a vector dimension.")
        return vectors, current_dimension

    # Some adapters use a flat ``[float, ...]`` shape only for a one-item
    # request.  Normalize that documented one-vector shape before the ordinary
    # cardinality and dimension validation; it is never expanded across chunks.
    if len(texts) == 1 and single_vector is not None and raw_vectors is single_vector:
        raw_vectors = [single_vector]
    if dimension is None:
        return _vectors_with_derived_dimension(raw_vectors, len(texts))
    validated_dimension = _validated_dimension(dimension)
    return _vectors(raw_vectors, len(texts), validated_dimension), validated_dimension


def _build_stored_parent_documents(
    bundle: dict[str, Any],
    embedding_contract: dict[str, Any],
    snapshot_id: str,
) -> list[dict[str, Any]]:
    validated_contract = _validate_embedding_contract(embedding_contract)
    documents: list[dict[str, Any]] = []
    for parent in bundle["parents"]:
        asset_id = str(parent["asset_id"])
        version = str(parent["version"])
        chunk_count = int(parent.get("chunk_count") or 0)
        stored_parent = {key: value for key, value in parent.items() if key != "chunk_count"}
        documents.append(
            {
                **stored_parent,
                "_id": _stable_document_id([str(bundle["tenant_id"]), snapshot_id, asset_id, version]),
                "snapshot_id": snapshot_id,
                "is_active": True,
                "embedding_manifest": {
                    "embedding_contract": validated_contract,
                    "chunk_count": chunk_count,
                    "complete": True,
                },
            }
        )
    return documents


def _build_stored_chunk_documents(
    bundle: dict[str, Any],
    selected_chunks: list[dict[str, Any]],
    embedding_contract: dict[str, Any],
    snapshot_id: str,
    vectors: list[list[float]],
) -> list[dict[str, Any]]:
    if len(selected_chunks) != len(vectors):
        raise ValueError("The selected chunk and vector counts do not match.")
    validated_contract = _validate_embedding_contract(embedding_contract)
    validated_vectors = _vectors(vectors, len(selected_chunks), int(validated_contract["dimension"]))
    documents: list[dict[str, Any]] = []
    for document, vector in zip(selected_chunks, validated_vectors):
        documents.append(
            {
                **document,
                "_id": _chunk_document_id(bundle, snapshot_id, document),
                "snapshot_id": snapshot_id,
                "is_active": True,
                "embedding": {
                    "contract": validated_contract,
                    "vector": vector,
                    "input_sha256": document["embedding_input_sha256"],
                },
            }
        )
    return documents


def _build_stored_documents(
    bundle: dict[str, Any],
    embedding_contract: dict[str, Any],
    snapshot_id: str,
    vectors: list[list[float]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build final parent/chunk documents without I/O for contract tests and bounded callers."""
    validated_contract = _validate_embedding_contract(embedding_contract)
    validated_vectors = _vectors(vectors, len(bundle["chunks"]), int(validated_contract["dimension"]))
    return (
        _build_stored_parent_documents(bundle, validated_contract, snapshot_id),
        _build_stored_chunk_documents(bundle, bundle["chunks"], validated_contract, snapshot_id, validated_vectors),
    )


class _CatalogActivationConflict(Exception):
    """Another execution switched the active pointer after this run started."""


class _EmbeddingRetriesExhausted(Exception):
    """A transient provider error remained after bounded, rate-limited retries."""


def _existing_active_pointer(collection: Any, tenant_id: str) -> dict[str, Any] | None:
    pointer = collection.find_one({"_id": tenant_id})
    if pointer is None:
        return None
    if not isinstance(pointer, dict):
        raise PyMongoError("The active catalog pointer is not a document.")
    return pointer


def _stored_chunk_is_reusable(
    stored: Any,
    expected_chunk: dict[str, Any],
    *,
    bundle: dict[str, Any],
    snapshot_id: str,
    embedding_contract: dict[str, Any],
) -> bool:
    """Reuse only a complete, exact deterministic chunk from a failed prior run."""
    if not isinstance(stored, dict):
        return False
    if stored.get("_id") != _chunk_document_id(bundle, snapshot_id, expected_chunk):
        return False
    for field in ("tenant_id", "catalog_id", "asset_id", "version", "chunk_id", "snapshot_id"):
        expected = snapshot_id if field == "snapshot_id" else expected_chunk.get(field)
        if stored.get(field) != expected:
            return False
    if stored.get("embedding_input_sha256") != expected_chunk.get("embedding_input_sha256"):
        return False
    embedding = stored.get("embedding")
    if not isinstance(embedding, dict) or embedding.get("contract") != embedding_contract:
        return False
    if embedding.get("input_sha256") != expected_chunk.get("embedding_input_sha256"):
        return False
    try:
        _vectors([embedding.get("vector")], 1, int(embedding_contract["dimension"]))
    except ValueError:
        return False
    return True


def _reusable_chunk_ids(
    collection: Any,
    chunks: list[dict[str, Any]],
    *,
    bundle: dict[str, Any],
    snapshot_id: str,
    embedding_contract: dict[str, Any],
) -> set[str]:
    """Find valid partial writes in bounded lookup batches for a safe resume."""
    expected_by_id = {_chunk_document_id(bundle, snapshot_id, chunk): chunk for chunk in chunks}
    reusable: set[str] = set()
    ids = list(expected_by_id)
    projection = {
        "_id": 1,
        "tenant_id": 1,
        "catalog_id": 1,
        "asset_id": 1,
        "version": 1,
        "chunk_id": 1,
        "snapshot_id": 1,
        "embedding_input_sha256": 1,
        "embedding": 1,
    }
    for start in range(0, len(ids), _RESUME_LOOKUP_BATCH_SIZE):
        selected_ids = ids[start : start + _RESUME_LOOKUP_BATCH_SIZE]
        for stored in collection.find({"_id": {"$in": selected_ids}}, projection):
            if not isinstance(stored, dict):
                continue
            document_id = stored.get("_id")
            expected_chunk = expected_by_id.get(document_id)
            if expected_chunk is not None and _stored_chunk_is_reusable(
                stored,
                expected_chunk,
                bundle=bundle,
                snapshot_id=snapshot_id,
                embedding_contract=embedding_contract,
            ):
                reusable.add(document_id)
    return reusable


def _pointer_is_current_and_complete(
    pointer: dict[str, Any] | None,
    *,
    snapshot_id: str,
    embedding_contract: dict[str, Any],
    expected_records: int,
    expected_chunks: int,
    bundle: dict[str, Any],
    chunks: list[dict[str, Any]],
    assets_collection: Any,
    chunks_collection: Any,
    tenant_id: str,
) -> bool:
    """Accept an existing active snapshot only after content verification.

    A matching pointer and matching document counts are not sufficient: a
    previous interrupted/manual write can leave a same-count chunk with a
    wrong vector, embedding contract, or source hash.  Reuse the ordinary
    bounded resume verifier so the ``ACTIVE_ALREADY_CURRENT`` fast path is
    equivalent to the verification used before a new pointer is published.
    """
    if not isinstance(pointer, dict):
        return False
    if pointer.get("active_snapshot_id") != snapshot_id or pointer.get("embedding_contract") != embedding_contract:
        return False
    counts = pointer.get("counts")
    if not isinstance(counts, dict) or counts.get("records") != expected_records or counts.get("chunks") != expected_chunks:
        return False
    scope_filter = {"tenant_id": tenant_id, "snapshot_id": snapshot_id}
    if (
        assets_collection.count_documents(scope_filter) != expected_records
        or chunks_collection.count_documents(scope_filter) != expected_chunks
    ):
        return False
    return len(
        _reusable_chunk_ids(
            chunks_collection,
            chunks,
            bundle=bundle,
            snapshot_id=snapshot_id,
            embedding_contract=embedding_contract,
        )
    ) == expected_chunks


def _activate_pointer_compare_and_swap(
    collection: Any,
    *,
    tenant_id: str,
    previous_pointer: dict[str, Any] | None,
    replacement: dict[str, Any],
) -> None:
    """Publish only if a concurrent run has not already changed the pointer."""
    previous_snapshot_id = (
        str(previous_pointer.get("active_snapshot_id") or "") if isinstance(previous_pointer, dict) else ""
    )
    query: dict[str, Any] = {"_id": tenant_id}
    if previous_snapshot_id:
        query["active_snapshot_id"] = previous_snapshot_id
        upsert = False
    else:
        # A missing pointer may be inserted. If another writer inserts or
        # changes it before us, the compare condition must fail rather than
        # silently making the older execution active again.
        query["active_snapshot_id"] = {"$exists": False}
        upsert = True
    try:
        result = collection.update_one(query, {"$set": replacement}, upsert=upsert)
    except Exception as exc:  # noqa: BLE001 - DuplicateKeyError varies by PyMongo deployment
        duplicate_code = getattr(exc, "code", None)
        if type(exc).__name__ == "DuplicateKeyError" or duplicate_code == 11000:
            raise _CatalogActivationConflict from exc
        raise
    # Simple test doubles may return None. Real PyMongo returns UpdateResult.
    if result is None:
        return
    matched_count = getattr(result, "matched_count", None)
    upserted_id = getattr(result, "upserted_id", None)
    if previous_snapshot_id:
        if matched_count != 1:
            raise _CatalogActivationConflict
    elif matched_count == 0 and upserted_id is None:
        raise _CatalogActivationConflict


def _embed_one_chunk_with_retries(
    embedding: Any,
    text: str,
    *,
    expected_dimension: int | None,
    interval_seconds: float,
    max_retries: int,
    is_first_provider_call: bool,
) -> tuple[list[list[float]], int, int, int]:
    """Embed one chunk and keep the 1-second floor across retry attempts too."""
    retry_count = 0
    while True:
        if not is_first_provider_call or retry_count:
            time.sleep(interval_seconds)
        try:
            vectors, dimension = _embed_documents(embedding, [text], expected_dimension)
            return vectors, dimension, retry_count + 1, retry_count
        except RuntimeError as exc:
            if retry_count >= max_retries:
                raise _EmbeddingRetriesExhausted from exc
            retry_count += 1


def _bulk_replace(collection: Any, documents: list[dict[str, Any]], batch_size: int) -> None:
    for start in range(0, len(documents), batch_size):
        operations = [
            ReplaceOne({"_id": document["_id"]}, document, upsert=True)
            for document in documents[start : start + batch_size]
        ]
        if operations:
            collection.bulk_write(operations, ordered=False)


_MONGODB_STAGE_GUIDANCE = {
    "connect_and_ping": (
        "MONGO_URL, 네트워크/DNS·방화벽·TLS 연결, MongoDB 사용자 인증 정보를 확인하세요."
    ),
    "ensure_assets_index": (
        "catalog_assets 컬렉션의 createIndex 권한과 같은 이름의 기존 인덱스 충돌 여부를 확인하세요."
    ),
    "ensure_chunks_index": (
        "catalog_asset_chunks 컬렉션의 createIndex 권한과 같은 이름의 기존 인덱스 충돌 여부를 확인하세요."
    ),
    "load_active_pointer": "catalog_active_pointers 컬렉션의 읽기 권한을 확인하세요.",
    "verify_active_snapshot": "기존 catalog snapshot 컬렉션의 읽기 권한과 데이터 상태를 확인하세요.",
    "load_reusable_chunks": "catalog_asset_chunks 컬렉션의 읽기 권한을 확인하세요.",
    "write_vector_chunks": (
        "catalog_asset_chunks 컬렉션의 readWrite 권한, 컬렉션 검증 규칙, 저장 용량을 확인하세요."
    ),
    "write_parent_assets": (
        "catalog_assets 컬렉션의 readWrite 권한, 컬렉션 검증 규칙, 저장 용량을 확인하세요."
    ),
    "verify_persisted_counts": "저장 후 catalog_assets 및 catalog_asset_chunks의 읽기 권한을 확인하세요.",
    "activate_snapshot": (
        "catalog_active_pointers 컬렉션의 readWrite 권한을 확인하고, 동시에 실행 중인 적재가 끝난 뒤 다시 실행하세요."
    ),
}


def _mongodb_failure_details(stage: str, exc: BaseException) -> dict[str, str]:
    """Expose only actionable, non-secret MongoDB diagnostics to the Flow result."""
    exception_type = type(exc).__name__
    if not _SAFE_EXCEPTION_TYPE_PATTERN.fullmatch(exception_type):
        exception_type = "MongoDBError"
    return {
        "stage": stage,
        "exception_type": exception_type,
        "next_check": _MONGODB_STAGE_GUIDANCE.get(
            stage,
            "MONGO_URL, MongoDB 권한, 네트워크 연결을 확인한 뒤 다시 실행하세요.",
        ),
        "sensitive_error_message_omitted": "URI, 인증정보, 서버 상세 오류는 결과에 표시하지 않았습니다.",
    }


def _failure(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> Data:
    error = {"code": code, "message": message, "retryable": retryable}
    if details:
        error["details"] = details
    return Data(
        data={
            "ok": False,
            "status": "BLOCKED",
            "error": error,
        }
    )


def _component_id(component: Any) -> str:
    """Return a stable, bounded native-HITL component identity."""

    value = str(getattr(component, "_id", "") or getattr(component, "name", "") or "CatalogMongoDBVectorWriter")
    return value.strip()[:200] or "CatalogMongoDBVectorWriter"


def _continuation_request_id(component: Any, snapshot_id: str, completed_chunks: int) -> str:
    """Bind one continue/stop card to exactly one durable checkpoint."""

    graph = getattr(component, "graph", None)
    run_id = str(getattr(graph, "run_id", "") or "run").strip()[:200] or "run"
    return f"{_component_id(component)}:{run_id}:catalog:{snapshot_id}:{completed_chunks}"[:500]


def _continuation_decision(component: Any, request_id: str) -> dict[str, Any] | None:
    graph = getattr(component, "graph", None)
    decisions = getattr(graph, "human_input_decisions", None) if graph is not None else None
    decision = decisions.get(request_id) if isinstance(decisions, dict) else None
    return decision if isinstance(decision, dict) else None


def _native_continuation_pause_context(component: Any) -> tuple[bool, dict[str, Any]]:
    """Return whether this run can create a resumable Langflow HITL card.

    ``Graph.request_pause`` only records a pause request.  Langflow creates a
    durable Human Input card at the next graph boundary only when the caller
    has enabled checkpointing for a background workflow job.  The normal
    Canvas ``Run Flow`` route exposes ``request_pause`` too, but does not
    supply that durable job context; returning a WAITING status there would
    falsely promise a card that cannot appear.
    """

    graph = getattr(component, "graph", None)
    request_pause = getattr(graph, "request_pause", None) if graph is not None else None
    if not callable(request_pause):
        return False, {
            "available": False,
            "reason": "durable_background_job_required",
            "message": "현재 실행은 Langflow durable background job이 아니므로 계속/중단 HITL 카드를 만들 수 없습니다.",
        }
    if not bool(getattr(graph, "checkpointing_enabled", False)):
        return False, {
            "available": False,
            "reason": "durable_background_job_required",
            "message": "일반 Canvas Run Flow에서는 checkpointing이 활성화되지 않아 계속/중단 HITL 카드가 표시되지 않습니다.",
        }
    job_id = str(getattr(graph, "job_id", "") or "").strip()
    if not job_id:
        return False, {
            "available": False,
            "reason": "durable_background_job_required",
            "message": "계속/중단 HITL 카드는 Langflow durable background job ID가 있는 실행에서만 지원됩니다.",
        }
    return True, {
        "available": True,
        "reason": None,
        "execution_mode": "durable_background_job",
    }


def _continuation_pause_request(
    component: Any,
    *,
    snapshot_id: str,
    completed_chunks: int,
    total_chunks: int,
    records: int,
    previous_active_snapshot_id: str | None,
) -> dict[str, Any]:
    """Build the native Langflow 1.11.1 button-only HITL pause payload."""

    completed = max(0, min(int(completed_chunks), int(total_chunks)))
    total = max(1, int(total_chunks))
    remaining = max(0, total - completed)
    percent = round((completed / total) * 100, 1)
    return {
        "request_id": _continuation_request_id(component, snapshot_id, completed),
        "kind": "node_input",
        "prompt": (
            "카탈로그 임베딩 checkpoint를 저장했습니다.\n\n"
            f"- 처리 진행률: {completed}/{total} 청크 ({percent}%)\n"
            f"- 남은 청크: {remaining}\n"
            f"- 카탈로그 항목: {records}\n"
            f"- 준비 중인 snapshot: {snapshot_id}\n"
            f"- 현재 검색에 사용 중인 snapshot: {previous_active_snapshot_id or '없음'}\n\n"
            "아직 전체 vector 검증이 끝나지 않아 새 snapshot은 검색에 게시되지 않았습니다. "
            "계속 적재를 선택하면 저장된 checkpoint를 검증한 뒤 다음 배치를 이어서 처리합니다."
        )[:16_000],
        "schema": [],
        "options": [
            {"action_id": _CONTINUE_INGESTION_ACTION, "label": "계속 적재"},
            {"action_id": _STOP_INGESTION_ACTION, "label": "중단하고 나중에 실행"},
        ],
        "allowed_decisions": [_CONTINUE_INGESTION_ACTION, _STOP_INGESTION_ACTION],
        "snapshot_id": snapshot_id,
        "completed_chunks": completed,
        "total_chunks": total,
        "paused_at": _utc_now().isoformat().replace("+00:00", "Z"),
    }


class CatalogMongoDBVectorWriterComponent(Component):
    display_name = "02 MongoDB Catalog Vector Writer"
    description = (
        "Test a complete catalog snapshot safely by default. Live ingestion checkpoints bounded groups of one-at-a-time "
        "embeddings, shows a native continue/stop checkpoint card, and activates a verified snapshot only last."
    )
    icon = "DatabaseZap"
    name = "CatalogMongoDBVectorWriter"

    inputs = [
        DataInput(name="chunk_bundle", display_name="Deterministic Chunk Bundle", required=True),
        HandleInput(
            name="embedding",
            display_name="Embedding Model",
            input_types=["Embeddings"],
            required=False,
            info="Connect Langflow's Embedding Model. Runtime model ID and vector dimension are derived automatically.",
        ),
        SecretStrInput(name="mongodb_uri", display_name="MongoDB URI", required=False),
        StrInput(name="mongodb_database", display_name="MongoDB Database", value="business_work_design", required=True),
        StrInput(name="assets_collection", display_name="Parent Assets Collection", value="catalog_assets", required=True),
        StrInput(name="chunks_collection", display_name="Vector Chunks Collection", value="catalog_asset_chunks", required=True),
        StrInput(name="pointer_collection", display_name="Active Pointer Collection", value="catalog_active_pointers", required=True),
        BoolInput(
            name="dry_run",
            display_name="테스트 실행 (저장하지 않음)",
            value=True,
            info="기본값은 켜짐입니다. 켜면 scope, hash, chunk 정책만 검증하며 Embedding Model이나 MongoDB를 호출하거나 저장하지 않습니다.",
        ),
        BoolInput(
            name="confirm_complete_catalog_snapshot",
            display_name="전체 카탈로그 파일 확인 (실제 저장용)",
            value=False,
            info=(
                "실제 저장 전에 이 파일이 현재 catalog의 전체 목록임을 확인하세요. 실제 저장은 새 active snapshot을 이 파일 전체로 교체하므로, "
                "파일에서 빠진 기존 자산은 다음 검색 대상에서 제외됩니다. 테스트 실행에서는 이 확인이 필요하지 않습니다."
            ),
        ),
        BoolInput(
            name="resume_verified_partial_snapshot",
            display_name="중단된 동일 Snapshot 이어쓰기",
            value=True,
            advanced=True,
            info=(
                "같은 전체 파일·chunk 정책·embedding 계약으로 앞선 실행이 중단된 경우, hash와 vector 계약이 모두 일치하는 저장 청크만 재사용합니다. "
                "검증되지 않은 청크는 다시 임베딩합니다."
            ),
        ),
        BoolInput(
            name="pause_for_next_batch",
            display_name="부분 적재 후 계속 여부 확인 (HITL)",
            value=True,
            info=(
                "Langflow durable background job에서만 새 청크가 남을 때 처리 수·남은 수와 '계속 적재'/'중단하고 나중에 실행' "
                "카드를 표시합니다. 일반 Canvas Run Flow에서는 checkpoint만 저장하고 같은 파일·모델 설정으로 다시 실행해 이어갑니다."
            ),
        ),
        IntInput(
            name="max_embedding_chunks_per_run",
            display_name="실행 1회당 신규 임베딩 청크 수",
            value=_DEFAULT_MAX_EMBEDDING_CHUNKS_PER_RUN,
            info=(
                "한 번의 F00 실행에서 새로 임베딩할 최대 청크 수입니다. 기본 80개를 처리하면 현재 진행분을 MongoDB에 "
                "체크포인트하고 완료 전에는 active snapshot을 바꾸지 않습니다. 같은 파일·모델 설정으로 다시 실행하면 "
                "저장된 검증 청크를 건너뛰고 이어서 처리합니다."
            ),
        ),
        IntInput(
            name="embedding_run_time_budget_seconds",
            display_name="실행 최대 처리 시간 (초)",
            value=_DEFAULT_EMBEDDING_RUN_TIME_BUDGET_SECONDS,
            advanced=True,
            info=(
                "Langflow의 300초 실행 제한보다 먼저 안전하게 멈추는 Writer 내부 시간 상한입니다. "
                "기본 180초에 도달하면 현재 진행분을 저장하고 다음 실행에서 이어갑니다. 이미 시작한 provider 호출 하나의 "
                "실행 시간까지 강제 중단하지는 않습니다."
            ),
        ),
        FloatInput(
            name="embedding_call_interval_seconds",
            display_name="임베딩 호출 간격(초)",
            value=_MIN_EMBEDDING_CALL_INTERVAL_SECONDS,
            advanced=True,
            info="청크 1개씩 순차 호출할 때 다음 Embedding Model 호출 전 대기 시간입니다. 첫 호출 전에는 대기하지 않으며 1초 미만은 1초로 처리합니다.",
        ),
        IntInput(
            name="mongo_write_batch_size",
            display_name="MongoDB 저장 체크포인트 청크 수",
            value=_DEFAULT_MONGO_CHECKPOINT_BATCH_SIZE,
            advanced=True,
            info=(
                "이 수만큼 임베딩한 청크마다 MongoDB에 체크포인트합니다. 기본 10은 실행이 예기치 않게 끝나도 "
                "다시 임베딩해야 할 양을 작게 유지합니다. 임베딩 요청은 여전히 청크 1개씩 순차 호출합니다."
            ),
        ),
        IntInput(
            name="embedding_max_retries",
            display_name="임베딩 재시도 횟수",
            value=2,
            advanced=True,
            info="일시적인 Embedding Model 오류만 재시도합니다. 재시도 사이에도 임베딩 호출 간격(최소 1초)을 지킵니다.",
        ),
        IntInput(
            name="mongodb_timeout_ms",
            display_name="MongoDB 연결·서버 선택 제한 시간 (ms)",
            value=5000,
            advanced=True,
            info="MongoDB 연결과 서버 선택에 적용합니다. 소켓 read/write는 안정성을 위해 최소 10초를 사용하며 Embedding Model 호출 시간에는 적용되지 않습니다.",
        ),
    ]

    outputs = [Output(name="ingestion_result", display_name="Catalog Ingestion Result", method="write_catalog", types=["Data"])]

    def write_catalog(self) -> Data:
        client: Any = None
        mongo_stage = "not_started"
        try:
            run_started_at = time.monotonic()
            bundle = _payload(getattr(self, "chunk_bundle", None))
            parents, chunks = _validate_chunk_bundle(bundle)
            embedding_call_interval_seconds = _bounded_float(
                getattr(self, "embedding_call_interval_seconds", _MIN_EMBEDDING_CALL_INTERVAL_SECONDS),
                _MIN_EMBEDDING_CALL_INTERVAL_SECONDS,
                _MIN_EMBEDDING_CALL_INTERVAL_SECONDS,
                _MAX_EMBEDDING_CALL_INTERVAL_SECONDS,
            )
            compact_base = {
                "tenant_id": bundle["tenant_id"],
                "catalog_id": bundle["catalog_id"],
                "source_sha256": bundle["source_sha256"],
                "ingest_sha256": bundle["ingest_sha256"],
                "source_size_bytes": bundle["source_size_bytes"],
                "counts": {"records": len(parents), "chunks": len(chunks)},
                "ingest_contract_version": _INGEST_CONTRACT_VERSION,
                "writer_semantic_revision": _WRITER_SEMANTIC_REVISION,
                "upload_semantics": _upload_semantics(),
            }
            if bool(getattr(self, "dry_run", False)):
                self.status = (
                    f"테스트 실행 완료: {len(parents)}개 레코드와 {len(chunks)}개 청크를 검증했습니다. "
                    "MongoDB에는 저장하지 않았습니다."
                )
                return Data(
                    data={
                        "ok": True,
                        "status": "DRY_RUN_VALIDATED",
                        "dry_run": True,
                        "execution_mode_display": "테스트 실행 (저장하지 않음)",
                        "message": "테스트 실행입니다. MongoDB에는 저장하지 않았습니다.",
                        "snapshot_id": None,
                        "embedding_contract": _deferred_embedding_contract(),
                        "embedding_execution": {
                            "mode": "deferred_test_run",
                            "calls": 0,
                            "minimum_interval_seconds": embedding_call_interval_seconds,
                        },
                        **compact_base,
                    }
                )

            if not bool(getattr(self, "confirm_complete_catalog_snapshot", False)):
                self.status = "실제 저장을 시작하지 않았습니다. 전체 카탈로그 파일 확인이 필요합니다."
                return _failure(
                    "FULL_SNAPSHOT_CONFIRMATION_REQUIRED",
                    (
                        "실제 저장은 업로드 파일 전체를 새 active catalog snapshot으로 게시합니다. "
                        "이 파일이 현재 전체 catalog임을 확인한 뒤 '전체 카탈로그 파일 확인 (실제 저장용)'을 켜세요."
                    ),
                )

            mongodb_uri = _secret_value(getattr(self, "mongodb_uri", ""))
            embedding = getattr(self, "embedding", None)
            if not mongodb_uri or embedding is None:
                return _failure(
                    "PRODUCTION_CONFIG_MISSING",
                    "테스트 실행을 해제한 실제 저장 실행에는 MongoDB URI와 연결된 Embedding Model이 필요합니다.",
                )
            database_name = _safe_identifier(getattr(self, "mongodb_database", ""), "mongodb_database")
            assets_name = _safe_identifier(getattr(self, "assets_collection", ""), "assets_collection")
            chunks_name = _safe_identifier(getattr(self, "chunks_collection", ""), "chunks_collection")
            pointer_name = _safe_identifier(getattr(self, "pointer_collection", ""), "pointer_collection")
            if len({assets_name, chunks_name, pointer_name}) != 3:
                raise ValueError("Parent, chunk, and pointer collections must be distinct.")
            write_batch_size = _bounded_int(
                getattr(self, "mongo_write_batch_size", _DEFAULT_MONGO_CHECKPOINT_BATCH_SIZE),
                _DEFAULT_MONGO_CHECKPOINT_BATCH_SIZE,
                1,
                1000,
            )
            timeout_ms = _bounded_int(getattr(self, "mongodb_timeout_ms", 5000), 5000, 1000, 30000)
            max_retries = _bounded_int(
                getattr(self, "embedding_max_retries", 2), 2, 0, _MAX_EMBEDDING_RETRIES
            )
            resume_partial_snapshot = bool(getattr(self, "resume_verified_partial_snapshot", True))
            max_embedding_chunks_per_run = _bounded_int(
                getattr(self, "max_embedding_chunks_per_run", _DEFAULT_MAX_EMBEDDING_CHUNKS_PER_RUN),
                _DEFAULT_MAX_EMBEDDING_CHUNKS_PER_RUN,
                1,
                _MAX_EMBEDDING_CHUNKS_PER_RUN,
            )
            embedding_run_time_budget_seconds = _bounded_int(
                getattr(
                    self,
                    "embedding_run_time_budget_seconds",
                    _DEFAULT_EMBEDDING_RUN_TIME_BUDGET_SECONDS,
                ),
                _DEFAULT_EMBEDDING_RUN_TIME_BUDGET_SECONDS,
                _MIN_EMBEDDING_RUN_TIME_BUDGET_SECONDS,
                _MAX_EMBEDDING_RUN_TIME_BUDGET_SECONDS,
            )

            # Establish the actual vector contract before any MongoDB side effect. The
            # first response determines dimension; model identity comes only from the
            # connected Langflow embedding runtime, never a duplicate writer field.
            # Each provider request intentionally contains exactly one chunk.
            first_selected = chunks[:1]
            first_vectors, dimension, embedding_call_count, retry_count = _embed_one_chunk_with_retries(
                embedding,
                str(first_selected[0]["embedding_text_redacted"]),
                expected_dimension=None,
                interval_seconds=embedding_call_interval_seconds,
                max_retries=max_retries,
                is_first_provider_call=True,
            )
            contract = _embedding_runtime_contract(embedding, dimension)
            snapshot_id = _snapshot_id(bundle, contract)
            compact = {
                **compact_base,
                "snapshot_id": snapshot_id,
                "embedding_contract": contract,
            }
            first_documents = _build_stored_chunk_documents(
                bundle,
                first_selected,
                contract,
                snapshot_id,
                first_vectors,
            )

            mongo_stage = "connect_and_ping"
            client = MongoClient(
                mongodb_uri,
                connectTimeoutMS=timeout_ms,
                serverSelectionTimeoutMS=timeout_ms,
                socketTimeoutMS=max(timeout_ms, 10000),
                retryReads=True,
                retryWrites=True,
            )
            client.admin.command("ping")
            database = client[database_name]
            assets_collection = database[assets_name]
            chunks_collection = database[chunks_name]
            pointers_collection = database[pointer_name]
            mongo_stage = "ensure_assets_index"
            assets_collection.create_index(
                [("tenant_id", ASCENDING), ("snapshot_id", ASCENDING), ("asset_id", ASCENDING), ("version", ASCENDING)],
                unique=True,
                name="uq_catalog_asset_version",
            )
            mongo_stage = "ensure_chunks_index"
            chunks_collection.create_index(
                [
                    ("tenant_id", ASCENDING),
                    ("snapshot_id", ASCENDING),
                    ("asset_id", ASCENDING),
                    ("version", ASCENDING),
                    ("chunk_id", ASCENDING),
                ],
                unique=True,
                name="uq_catalog_asset_chunk",
            )

            mongo_stage = "load_active_pointer"
            previous_pointer = _existing_active_pointer(pointers_collection, str(bundle["tenant_id"]))
            mongo_stage = "verify_active_snapshot"
            if _pointer_is_current_and_complete(
                previous_pointer,
                snapshot_id=snapshot_id,
                embedding_contract=contract,
                expected_records=len(parents),
                expected_chunks=len(chunks),
                bundle=bundle,
                chunks=chunks,
                assets_collection=assets_collection,
                chunks_collection=chunks_collection,
                tenant_id=str(bundle["tenant_id"]),
            ):
                self.status = (
                    f"Active snapshot already current: {snapshot_id}. "
                    "No parent/chunk write or pointer switch was needed."
                )
                return Data(
                    data={
                        "ok": True,
                        "status": "ACTIVE_ALREADY_CURRENT",
                        "dry_run": False,
                        **compact,
                        "counts": {"records": len(parents), "chunks": len(chunks), "vectors": len(chunks)},
                        "activation": {
                            "action": "already_active",
                            "compare_and_swap": False,
                            "previous_active_snapshot_id": snapshot_id,
                        },
                        "embedding_execution": {
                            "mode": "already_active_probe",
                            "calls": embedding_call_count,
                            "new_embeddings": 1,
                            "resumed_vectors": max(0, len(chunks) - 1),
                            "retry_attempts": retry_count,
                            "minimum_interval_seconds": embedding_call_interval_seconds,
                        },
                    }
                )

            mongo_stage = "load_reusable_chunks"
            reusable_document_ids = (
                _reusable_chunk_ids(
                    chunks_collection,
                    chunks,
                    bundle=bundle,
                    snapshot_id=snapshot_id,
                    embedding_contract=contract,
                )
                if resume_partial_snapshot
                else set()
            )

            # A native HITL pause marks this Writer vertex unbuilt.  On resume
            # Langflow rebuilds this exact component, so confirm a decision
            # bound to the durable snapshot/checkpoint before starting another
            # bounded batch.  A normal manual rerun has no decision and simply
            # continues from the verified MongoDB chunk set.
            reusable_before_batch = len(reusable_document_ids)
            if reusable_before_batch:
                prior_request_id = _continuation_request_id(self, snapshot_id, reusable_before_batch)
                prior_decision = _continuation_decision(self, prior_request_id)
                if prior_decision is not None:
                    prior_action = str(prior_decision.get("action_id") or "").strip()
                    if prior_action == _STOP_INGESTION_ACTION:
                        self.status = (
                            f"사용자가 {reusable_before_batch}/{len(chunks)}개 checkpoint 뒤에 적재를 중단했습니다. "
                            "새 active snapshot은 게시하지 않았습니다."
                        )
                        return Data(
                            data={
                                "ok": True,
                                "status": "PARTIAL_EMBEDDINGS_STOPPED",
                                "dry_run": False,
                                **compact,
                                "counts": {
                                    "records": len(parents),
                                    "chunks": len(chunks),
                                    "vectors": reusable_before_batch,
                                },
                                "progress": {
                                    "total_chunks": len(chunks),
                                    "verified_vectors": reusable_before_batch,
                                    "remaining_chunks": len(chunks) - reusable_before_batch,
                                    "new_embeddings_this_run": 0,
                                    "reused_vectors_this_run": reusable_before_batch,
                                    "checkpoint_batch_size": write_batch_size,
                                    "next_run_required": True,
                                },
                                "activation": {
                                    "action": "checkpoint_retained_not_published",
                                    "compare_and_swap": False,
                                    "previous_active_snapshot_id": (
                                        str(previous_pointer.get("active_snapshot_id") or "")
                                        if previous_pointer
                                        else None
                                    ),
                                },
                                "embedding_execution": {
                                    "mode": "continuation_stopped_after_contract_probe",
                                    "calls": embedding_call_count,
                                    "new_embeddings": 0,
                                    "resumed_vectors": reusable_before_batch,
                                    "retry_attempts": retry_count,
                                    "minimum_interval_seconds": embedding_call_interval_seconds,
                                    "max_embedding_chunks_per_run": max_embedding_chunks_per_run,
                                    "time_budget_seconds": embedding_run_time_budget_seconds,
                                    "stop_reason": "stop_ingestion",
                                },
                                "human_decision": {"action_id": _STOP_INGESTION_ACTION},
                                "next_action": {
                                    "action": "run_f00_again_with_same_file_and_embedding_model",
                                    "message": (
                                        "저장된 checkpoint는 유지됩니다. 나중에 같은 전체 카탈로그 파일과 같은 "
                                        "Embedding Model 설정으로 F00을 새로 실행하면 이어서 처리합니다."
                                    ),
                                },
                            }
                        )
                    if prior_action != _CONTINUE_INGESTION_ACTION:
                        return _failure(
                            "CATALOG_CONTINUATION_DECISION_INVALID",
                            "계속 적재 또는 중단하고 나중에 실행 중 하나를 선택해야 합니다.",
                        )

            # Keep MongoDB checkpointing independent from the provider call
            # policy: vectors arrive one chunk at a time, then are flushed in
            # configured document groups. The first chunk is intentionally
            # embedded as the contract probe; later chunks may be reused only
            # after their complete persisted contract has been revalidated.
            #
            # Crucially, this loop stops before the host Flow's 300-second
            # timeout.  When HITL is enabled, the writer itself creates a
            # native pause after a durable checkpoint.  Langflow resumes this
            # vertex for the next bounded batch; it is not a graph self-loop.
            # A manual rerun follows the same verified-resume path.
            first_document_id = _chunk_document_id(bundle, snapshot_id, first_selected[0])
            first_was_reusable = first_document_id in reusable_document_ids
            pending_chunk_documents = [] if first_was_reusable else list(first_documents)
            stored_chunk_ids_this_run = set() if first_was_reusable else {first_document_id}
            newly_embedded_count = 0 if first_was_reusable else len(first_documents)
            resumed_vector_count = 1 if first_was_reusable else 0
            stop_reason: str | None = None
            if len(pending_chunk_documents) >= write_batch_size:
                mongo_stage = "write_vector_chunks"
                _bulk_replace(chunks_collection, pending_chunk_documents, write_batch_size)
                pending_chunk_documents = []
            for start in range(len(first_selected), len(chunks)):
                selected = chunks[start : start + 1]
                document_id = _chunk_document_id(bundle, snapshot_id, selected[0])
                if document_id in reusable_document_ids:
                    resumed_vector_count += 1
                    continue
                if newly_embedded_count >= max_embedding_chunks_per_run:
                    stop_reason = "max_embedding_chunks_per_run"
                    break
                if time.monotonic() - run_started_at >= embedding_run_time_budget_seconds:
                    stop_reason = "embedding_run_time_budget_seconds"
                    break
                vectors, validated_dimension, calls, retries = _embed_one_chunk_with_retries(
                    embedding,
                    str(selected[0]["embedding_text_redacted"]),
                    expected_dimension=int(contract["dimension"]),
                    interval_seconds=embedding_call_interval_seconds,
                    max_retries=max_retries,
                    is_first_provider_call=False,
                )
                embedding_call_count += calls
                retry_count += retries
                if validated_dimension != int(contract["dimension"]):
                    raise ValueError("The embedding response dimension changed between batches.")
                embedded_documents = _build_stored_chunk_documents(
                    bundle,
                    selected,
                    contract,
                    snapshot_id,
                    vectors,
                )
                pending_chunk_documents.extend(embedded_documents)
                stored_chunk_ids_this_run.add(document_id)
                newly_embedded_count += len(embedded_documents)
                if len(pending_chunk_documents) >= write_batch_size:
                    mongo_stage = "write_vector_chunks"
                    _bulk_replace(chunks_collection, pending_chunk_documents, write_batch_size)
                    pending_chunk_documents = []

            if pending_chunk_documents:
                mongo_stage = "write_vector_chunks"
                _bulk_replace(chunks_collection, pending_chunk_documents, write_batch_size)

            completed_document_ids = set(reusable_document_ids)
            completed_document_ids.update(stored_chunk_ids_this_run)
            if len(completed_document_ids) < len(chunks):
                # This is an expected resumable checkpoint, not a failure:
                # parent documents and the active pointer remain untouched
                # until every deterministic vector is present and verified.
                elapsed_seconds = round(max(0.0, time.monotonic() - run_started_at), 3)
                partial_result = {
                    "ok": True,
                    "status": "PARTIAL_EMBEDDINGS_SAVED",
                    "dry_run": False,
                    **compact,
                    "counts": {
                        "records": len(parents),
                        "chunks": len(chunks),
                        "vectors": len(completed_document_ids),
                    },
                    "progress": {
                        "total_chunks": len(chunks),
                        "verified_vectors": len(completed_document_ids),
                        "remaining_chunks": len(chunks) - len(completed_document_ids),
                        "new_embeddings_this_run": newly_embedded_count,
                        "reused_vectors_this_run": resumed_vector_count,
                        "checkpoint_batch_size": write_batch_size,
                        "next_run_required": True,
                    },
                    "activation": {
                        "action": "checkpoint_saved_not_published",
                        "compare_and_swap": False,
                        "previous_active_snapshot_id": (
                            str(previous_pointer.get("active_snapshot_id") or "") if previous_pointer else None
                        ),
                    },
                    "embedding_execution": {
                        "mode": "resumable_one_chunk_per_call",
                        "calls": embedding_call_count,
                        "new_embeddings": newly_embedded_count,
                        "resumed_vectors": resumed_vector_count,
                        "retry_attempts": retry_count,
                        "minimum_interval_seconds": embedding_call_interval_seconds,
                        "max_embedding_chunks_per_run": max_embedding_chunks_per_run,
                        "time_budget_seconds": embedding_run_time_budget_seconds,
                        "elapsed_seconds": elapsed_seconds,
                        "stop_reason": stop_reason or "incomplete_snapshot",
                    },
                    "next_action": {
                        "action": "run_f00_again_with_same_file_and_embedding_model",
                        "message": (
                            "같은 전체 카탈로그 파일과 같은 Embedding Model 설정으로 F00을 다시 실행하세요. "
                            "저장된 검증 청크는 건너뛰며, 모든 청크가 완료된 마지막 실행에서만 active snapshot을 게시합니다."
                        ),
                    },
                }
                pause_available, hitl = _native_continuation_pause_context(self)
                graph = getattr(self, "graph", None)
                request_pause = getattr(graph, "request_pause", None) if graph is not None else None
                if (
                    bool(getattr(self, "pause_for_next_batch", True))
                    and resume_partial_snapshot
                    and pause_available
                    and callable(request_pause)
                ):
                    pause_request = _continuation_pause_request(
                        self,
                        snapshot_id=snapshot_id,
                        completed_chunks=len(completed_document_ids),
                        total_chunks=len(chunks),
                        records=len(parents),
                        previous_active_snapshot_id=(
                            str(previous_pointer.get("active_snapshot_id") or "") if previous_pointer else None
                        ),
                    )
                    request_pause(reason=_NATIVE_HITL_REASON, data=pause_request)
                    partial_result["status"] = "WAITING_INGESTION_CONTINUATION"
                    partial_result["resume"] = {
                        "reason": _NATIVE_HITL_REASON,
                        "request_id": pause_request["request_id"],
                    }
                    partial_result["hitl"] = {**hitl, "request_id": pause_request["request_id"]}
                    partial_result["next_action"] = {
                        "action": "choose_continue_or_stop_in_playground",
                        "message": "Playground에서 계속 적재 또는 중단하고 나중에 실행을 선택하세요.",
                    }
                    self.status = (
                        f"임베딩 checkpoint 저장: {len(completed_document_ids)}/{len(chunks)}개 청크. "
                        "Playground에서 계속 여부를 기다립니다."
                    )
                else:
                    if not bool(getattr(self, "pause_for_next_batch", True)) or not resume_partial_snapshot:
                        hitl = {
                            "available": False,
                            "reason": "disabled_by_configuration",
                            "message": "부분 적재 HITL이 설정에서 꺼져 있어 checkpoint를 저장한 뒤 새 실행으로 이어갑니다.",
                        }
                    partial_result["hitl"] = hitl
                    self.status = (
                        f"임베딩 진행 상태를 저장했습니다: {len(completed_document_ids)}/{len(chunks)}개 청크. "
                        "같은 파일과 Embedding Model로 F00을 다시 실행하면 이어서 처리합니다."
                    )
                return Data(data=partial_result)

            parent_documents = _build_stored_parent_documents(bundle, contract, snapshot_id)
            mongo_stage = "write_parent_assets"
            _bulk_replace(assets_collection, parent_documents, write_batch_size)
            scope_filter = {"tenant_id": bundle["tenant_id"], "snapshot_id": snapshot_id}
            mongo_stage = "verify_persisted_counts"
            stored_assets = assets_collection.count_documents(scope_filter)
            stored_chunks = chunks_collection.count_documents(scope_filter)
            if (
                stored_assets != len(parent_documents)
                or stored_chunks != len(chunks)
                or newly_embedded_count + resumed_vector_count != len(chunks)
            ):
                raise PyMongoError("The completed snapshot counts do not match the prepared catalog.")

            now = _utc_now()
            pointer = {
                "tenant_id": bundle["tenant_id"],
                "catalog_id": bundle["catalog_id"],
                "snapshot_id": snapshot_id,
                "active_snapshot_id": snapshot_id,
                "embedding_contract": contract,
                "source_sha256": bundle["source_sha256"],
                "counts": {"records": len(parent_documents), "chunks": len(chunks)},
                "writer_semantic_revision": _WRITER_SEMANTIC_REVISION,
                "upload_semantics": _upload_semantics(),
                "activated_at": now,
                "updated_at": now,
            }
            mongo_stage = "activate_snapshot"
            _activate_pointer_compare_and_swap(
                pointers_collection,
                tenant_id=str(bundle["tenant_id"]),
                previous_pointer=previous_pointer,
                replacement=pointer,
            )
            self.status = (
                f"Activated {snapshot_id}: {len(parent_documents)} records, {len(chunks)} vectors, "
                f"{embedding_call_count} one-chunk embedding calls at >= {embedding_call_interval_seconds:.1f}s intervals "
                f"({resumed_vector_count} verified partial vectors reused)."
            )
            return Data(
                data={
                    "ok": True,
                    "status": "ACTIVE",
                    "dry_run": False,
                    **compact,
                    "counts": {"records": len(parent_documents), "chunks": len(chunks), "vectors": len(chunks)},
                    "activation": {
                        "action": "published",
                        "compare_and_swap": True,
                        "previous_active_snapshot_id": (
                            str(previous_pointer.get("active_snapshot_id") or "") if previous_pointer else None
                        ),
                    },
                    "embedding_execution": {
                        "mode": "sequential_one_chunk_per_call_with_verified_resume",
                        "calls": embedding_call_count,
                        "new_embeddings": newly_embedded_count,
                        "resumed_vectors": resumed_vector_count,
                        "retry_attempts": retry_count,
                        "minimum_interval_seconds": embedding_call_interval_seconds,
                    },
                }
            )
        except _CatalogActivationConflict:
            self.status = "Catalog vector storage completed but a newer execution changed the active pointer first."
            return _failure(
                "CATALOG_ACTIVATION_CONFLICT",
                "다른 적재 실행이 먼저 active snapshot을 변경했습니다. 현재 실행은 pointer를 덮어쓰지 않았습니다. 최신 전체 파일로 다시 실행하세요.",
                retryable=True,
            )
        except _EmbeddingRetriesExhausted:
            self.status = "Catalog vectorization stopped after bounded embedding retries before snapshot activation."
            return _failure(
                "EMBEDDING_PROVIDER_FAILED",
                "Embedding Model 재시도 횟수를 모두 사용했습니다. 새 active snapshot은 게시되지 않았습니다.",
                retryable=True,
            )
        except RuntimeError:
            self.status = "Catalog vectorization stopped before snapshot activation."
            return _failure(
                "EMBEDDING_PROVIDER_FAILED",
                "The connected Embedding Model failed; no new active snapshot was published.",
                retryable=True,
            )
        except ValueError as exc:
            self.status = "Catalog vector storage was rejected by validation."
            return _failure("CATALOG_PIPELINE_INVALID", str(exc))
        except (PyMongoError, OSError) as exc:
            self.status = "Catalog vector storage stopped before snapshot activation."
            return _failure(
                "MONGODB_INGEST_FAILED",
                "MongoDB storage failed; no new active snapshot was published.",
                retryable=True,
                details=_mongodb_failure_details(mongo_stage, exc),
            )
        finally:
            if client is not None:
                client.close()
