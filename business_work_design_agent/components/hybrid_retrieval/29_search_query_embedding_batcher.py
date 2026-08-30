from __future__ import annotations

"""Create locked query vectors through the connected Langflow Embedding Model."""

import hashlib
import hmac
import json
import math
import re
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, HandleInput, IntInput, Output
from lfx.schema import Data


_RUNTIME_CONTRACT_SCHEMA = "embedding-runtime-contract/v2"
_MAX_EMBEDDING_DIMENSION = 65536
_RUNTIME_CLASS_PATTERN = re.compile(
    r"^<class '([A-Za-z_][A-Za-z0-9_]*(?:\.(?:[A-Za-z_][A-Za-z0-9_]*|<locals>))*)'>$"
)


def _payload(value: Any, field: str) -> dict[str, Any]:
    data = getattr(value, "data", None)
    value = data if isinstance(data, dict) else value
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field} must be JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    if field == "query_plan" and isinstance(value.get("query_plan"), dict):
        value = value["query_plan"]
    return value


def _canonical_hash(value: Any) -> str:
    material = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _verified_plan_locks(plan: dict[str, Any]) -> dict[str, str]:
    lock_fields = ("design_scope_sha256", "query_plan_sha256")
    locks = {field: str(plan.get(field) or "") for field in lock_fields}
    if any(not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", value) for value in locks.values()):
        raise ValueError("query plan scope/hash lock is missing or invalid")
    plan_core = {
        key: value
        for key, value in plan.items()
        if key not in {"ok", "status", "query_plan_sha256", "trace_id"}
    }
    if not hmac.compare_digest(locks["query_plan_sha256"].lower(), _canonical_hash(plan_core).lower()):
        raise ValueError("query_plan_sha256 does not match query plan")
    return locks


def _queries(plan: dict[str, Any], maximum: int) -> list[dict[str, str]]:
    raw = plan.get("queries")
    if not isinstance(raw, list) or not raw:
        raise ValueError("query_plan.queries is required")
    if len(raw) > maximum:
        raise ValueError("query count exceeds max_queries")
    result: list[dict[str, str]] = []
    ids: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError("each query must be an object")
        query_id = str(item.get("query_id") or item.get("id") or f"query-{index + 1}").strip()[:200]
        text = str(item.get("text") or item.get("query") or "").strip()[:20_000]
        if not query_id or query_id in ids or not text:
            raise ValueError("query IDs must be unique and query text must be present")
        ids.add(query_id)
        result.append({"id": query_id, "text": text})
    return result


def _underlying_embedding(embedding: Any) -> Any:
    if embedding is None:
        raise ValueError("A connected Embedding Model is required")
    try:
        underlying = getattr(embedding, "embeddings", None)
    except Exception:  # noqa: BLE001 - provider wrappers can reject metadata reads
        underlying = None
    return embedding if underlying is None else underlying


def _model_id_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return text if 0 < len(text) <= 500 else ""


def _model_id(embedding: Any, underlying: Any) -> str:
    """Resolve only a provider-selected model identity; never infer from a class name."""
    try:
        available_models = getattr(embedding, "available_models", None)
    except Exception:  # noqa: BLE001 - provider wrappers can reject metadata reads
        available_models = None
    if isinstance(available_models, dict):
        matches = {
            text
            for candidate_id, candidate in available_models.items()
            if candidate is underlying and (text := _model_id_text(candidate_id))
        }
        if len(matches) > 1:
            raise ValueError("Embedding Model identity resolves to more than one configured model ID")
        if matches:
            return next(iter(matches))
    candidates: list[Any] = []
    try:
        candidates.append(getattr(underlying, "model_name", None))
    except Exception:  # noqa: BLE001 - provider model metadata is heterogeneous
        pass
    try:
        candidates.append(getattr(underlying, "model", None))
    except Exception:  # noqa: BLE001 - provider model metadata is heterogeneous
        pass
    try:
        candidates.append(getattr(underlying, "model_id", None))
    except Exception:  # noqa: BLE001 - provider model metadata is heterogeneous
        pass
    try:
        candidates.append(getattr(underlying, "deployment_name", None))
    except Exception:  # noqa: BLE001 - provider model metadata is heterogeneous
        pass
    try:
        candidates.append(getattr(underlying, "deployment", None))
    except Exception:  # noqa: BLE001 - provider model metadata is heterogeneous
        pass
    for candidate in candidates:
        text = _model_id_text(candidate)
        if text:
            return text
    raise ValueError("The connected Embedding Model does not expose a stable model identity")


def _runtime_class(underlying: Any) -> str:
    rendered = str(type(underlying))
    match = _RUNTIME_CLASS_PATTERN.fullmatch(rendered) if len(rendered) <= 1024 else None
    value = match.group(1) if match else ""
    if not value or len(value) > 1000:
        raise ValueError("The connected Embedding Model does not expose a stable runtime class")
    return value


def _runtime_contract(embedding: Any, dimension: int) -> dict[str, Any]:
    if isinstance(dimension, bool) or not isinstance(dimension, int) or not 1 <= dimension <= _MAX_EMBEDDING_DIMENSION:
        raise ValueError("The embedding response dimension is invalid")
    underlying = _underlying_embedding(embedding)
    contract = {
        "schema_version": _RUNTIME_CONTRACT_SCHEMA,
        "runtime_class": _runtime_class(underlying),
        "model_id": _model_id(embedding, underlying),
        "dimension": dimension,
    }
    return {**contract, "fingerprint": _canonical_hash(contract)}


def _vectors(value: Any, expected_count: int, dimension: int | None, query_ids: list[str]) -> tuple[list[list[float]], int]:
    if not isinstance(value, list) or len(value) != expected_count:
        raise ValueError("The embedding response count does not match the query batch")
    actual_dimension = dimension
    result: list[list[float]] = []
    for index, vector in enumerate(value):
        query_id = query_ids[index]
        if not isinstance(vector, list) or not vector:
            raise ValueError(f"The embedding response vector is missing for query_id={query_id}")
        if actual_dimension is None:
            actual_dimension = len(vector)
            if not 1 <= actual_dimension <= _MAX_EMBEDDING_DIMENSION:
                raise ValueError("The embedding response dimension is invalid")
        if len(vector) != actual_dimension:
            raise ValueError(f"The embedding response vector dimension is invalid for query_id={query_id}")
        normalized: list[float] = []
        for item in vector:
            if isinstance(item, bool):
                raise ValueError(f"The embedding response contains a boolean for query_id={query_id}")
            try:
                number = float(item)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"The embedding response contains a non-numeric value for query_id={query_id}") from exc
            if not math.isfinite(number):
                raise ValueError(f"The embedding response contains a non-finite value for query_id={query_id}")
            normalized.append(number)
        result.append(normalized)
    if actual_dimension is None:
        raise ValueError("The embedding response did not contain a vector dimension")
    return result, actual_dimension


def _embed_documents(embedding: Any, texts: list[str], query_ids: list[str], dimension: int | None) -> tuple[list[list[float]], int]:
    try:
        method = getattr(embedding, "embed_documents", None)
    except Exception as exc:  # noqa: BLE001 - provider wrappers can expose arbitrary properties
        raise ValueError("The connected Embedding Model could not be used") from exc
    if not callable(method):
        raise ValueError("The connected Embedding Model does not provide embed_documents")
    try:
        raw_vectors = method(texts)
    except Exception as exc:  # noqa: BLE001 - providers expose heterogeneous error types
        raise RuntimeError("The connected Embedding Model failed to vectorize the query batch") from exc
    return _vectors(raw_vectors, len(texts), dimension, query_ids)


class SearchQueryEmbeddingBatcherComponent(Component):
    display_name = "29 Search Query Embedding Batcher"
    description = "Vectorize locked search queries through the connected Langflow Embedding Model in bounded batches."
    icon = "Binary"
    name = "SearchQueryEmbeddingBatcher"

    inputs = [
        DataInput(name="query_plan", display_name="Search Query Plan", required=True),
        HandleInput(name="embedding", display_name="Embedding Model", input_types=["Embeddings"], required=True),
        IntInput(name="batch_size", display_name="Embedding Batch Size", value=16, advanced=True),
        IntInput(name="max_queries", display_name="Maximum Queries", value=30, advanced=True),
    ]
    outputs = [Output(name="query_vectors", display_name="ID-preserving Query Vectors", method="build_query_vectors", types=["Data"])]

    def build_query_vectors(self) -> Data:
        plan = _payload(getattr(self, "query_plan", None), "query_plan")
        locks = _verified_plan_locks(plan)
        maximum = max(1, min(int(getattr(self, "max_queries", 30) or 30), 100))
        queries = _queries(plan, maximum)
        embedding = getattr(self, "embedding", None)
        underlying = _underlying_embedding(embedding)
        # Resolve model metadata before the first provider request so unknown model
        # identities fail closed without creating an untraceable vector snapshot.
        _model_id(embedding, underlying)
        _runtime_class(underlying)
        batch_size = max(1, min(int(getattr(self, "batch_size", 16) or 16), 128))
        vectors: dict[str, list[float]] = {}
        dimension: int | None = None
        for start in range(0, len(queries), batch_size):
            batch = queries[start : start + batch_size]
            batch_vectors, dimension = _embed_documents(
                embedding,
                [item["text"] for item in batch],
                [item["id"] for item in batch],
                dimension,
            )
            for item, vector in zip(batch, batch_vectors):
                vectors[item["id"]] = vector
        contract = _runtime_contract(embedding, dimension if dimension is not None else 0)
        result = {
            "ok": True,
            "status": "VECTORIZED",
            "schema_version": "query-vectors/v1",
            "vectors": vectors,
            "query_order": [item["id"] for item in queries],
            "embedding_contract": contract,
            **locks,
        }
        self.status = f"Vectorized {len(vectors)} queries with {contract['model_id']} ({contract['dimension']}d)."
        return Data(data=result)
