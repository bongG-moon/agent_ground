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
_RUNTIME_CLASS_PATTERN = re.compile(
    r"^<class '([A-Za-z_][A-Za-z0-9_]*(?:\.(?:[A-Za-z_][A-Za-z0-9_]*|<locals>))*)'>$"
)
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
        asset_id = str(document["asset_id"])
        version = str(document["version"])
        chunk_id = str(document["chunk_id"])
        documents.append(
            {
                **document,
                "_id": _stable_document_id(
                    [str(bundle["tenant_id"]), snapshot_id, asset_id, version, chunk_id]
                ),
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


def _bulk_replace(collection: Any, documents: list[dict[str, Any]], batch_size: int) -> None:
    for start in range(0, len(documents), batch_size):
        operations = [
            ReplaceOne({"_id": document["_id"]}, document, upsert=True)
            for document in documents[start : start + batch_size]
        ]
        if operations:
            collection.bulk_write(operations, ordered=False)


def _failure(code: str, message: str, *, retryable: bool = False) -> Data:
    return Data(
        data={
            "ok": False,
            "status": "BLOCKED",
            "error": {"code": code, "message": message, "retryable": retryable},
        }
    )


class CatalogMongoDBVectorWriterComponent(Component):
    display_name = "02 MongoDB Catalog Vector Writer"
    description = "Embed catalog chunks sequentially one at a time with a rate-limit interval, buffer MongoDB writes, then activate the verified snapshot."
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
            value=False,
            info="켜면 scope, hash, chunk 정책만 검증하며 Embedding Model이나 MongoDB를 호출하거나 저장하지 않습니다.",
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
            display_name="MongoDB 일괄 저장 문서 수",
            value=500,
            advanced=True,
            info="한 번의 MongoDB bulk write에 저장할 문서 수입니다. 임베딩 요청 단위와 별개이며 청크 크기는 바꾸지 않습니다.",
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
        try:
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
            write_batch_size = _bounded_int(getattr(self, "mongo_write_batch_size", 500), 500, 1, 1000)
            timeout_ms = _bounded_int(getattr(self, "mongodb_timeout_ms", 5000), 5000, 1000, 30000)

            # Establish the actual vector contract before any MongoDB side effect. The
            # first response determines dimension; model identity comes only from the
            # connected Langflow embedding runtime, never a duplicate writer field.
            # Each provider request intentionally contains exactly one chunk.
            first_selected = chunks[:1]
            first_vectors, dimension = _embed_documents(
                embedding,
                [str(item["embedding_text_redacted"]) for item in first_selected],
            )
            embedding_call_count = 1
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
            assets_collection.create_index(
                [("tenant_id", ASCENDING), ("snapshot_id", ASCENDING), ("asset_id", ASCENDING), ("version", ASCENDING)],
                unique=True,
                name="uq_catalog_asset_version",
            )
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

            # Keep MongoDB write batching independent from the provider call
            # policy: vectors arrive one chunk at a time, then are flushed in
            # configured document groups.
            pending_chunk_documents = list(first_documents)
            embedded_count = len(first_documents)
            if len(pending_chunk_documents) >= write_batch_size:
                _bulk_replace(chunks_collection, pending_chunk_documents, write_batch_size)
                pending_chunk_documents = []
            for start in range(len(first_selected), len(chunks)):
                # The first provider request is made above without waiting.  Every
                # later request is delayed so providers receive at most one chunk
                # per call and at least the configured interval between calls.
                time.sleep(embedding_call_interval_seconds)
                selected = chunks[start : start + 1]
                vectors, validated_dimension = _embed_documents(
                    embedding,
                    [str(item["embedding_text_redacted"]) for item in selected],
                    int(contract["dimension"]),
                )
                embedding_call_count += 1
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
                embedded_count += len(embedded_documents)
                if len(pending_chunk_documents) >= write_batch_size:
                    _bulk_replace(chunks_collection, pending_chunk_documents, write_batch_size)
                    pending_chunk_documents = []

            if pending_chunk_documents:
                _bulk_replace(chunks_collection, pending_chunk_documents, write_batch_size)

            parent_documents = _build_stored_parent_documents(bundle, contract, snapshot_id)
            _bulk_replace(assets_collection, parent_documents, write_batch_size)
            scope_filter = {"tenant_id": bundle["tenant_id"], "snapshot_id": snapshot_id}
            stored_assets = assets_collection.count_documents(scope_filter)
            stored_chunks = chunks_collection.count_documents(scope_filter)
            if stored_assets != len(parent_documents) or stored_chunks != len(chunks) or embedded_count != len(chunks):
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
                "activated_at": now,
                "updated_at": now,
            }
            pointers_collection.update_one(
                {"_id": bundle["tenant_id"]},
                {"$set": pointer},
                upsert=True,
            )
            self.status = (
                f"Activated {snapshot_id}: {len(parent_documents)} records, {embedded_count} vectors, "
                f"{embedding_call_count} one-chunk embedding calls at >= {embedding_call_interval_seconds:.1f}s intervals."
            )
            return Data(
                data={
                    "ok": True,
                    "status": "ACTIVE",
                    "dry_run": False,
                    **compact,
                    "counts": {"records": len(parent_documents), "chunks": len(chunks), "vectors": embedded_count},
                    "embedding_execution": {
                        "mode": "sequential_one_chunk_per_call",
                        "calls": embedding_call_count,
                        "minimum_interval_seconds": embedding_call_interval_seconds,
                    },
                }
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
        except (PyMongoError, OSError):
            self.status = "Catalog vector storage stopped before snapshot activation."
            return _failure(
                "MONGODB_INGEST_FAILED",
                "MongoDB storage failed; no new active snapshot was published.",
                retryable=True,
            )
        finally:
            if client is not None:
                client.close()
