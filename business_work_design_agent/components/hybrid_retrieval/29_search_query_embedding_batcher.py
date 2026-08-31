from __future__ import annotations

"""Create locked query vectors through the connected Langflow Embedding Model."""

import hashlib
import hmac
import json
import math
import re
import time
import uuid
from collections.abc import Callable
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, FloatInput, HandleInput, IntInput, Output
from lfx.schema import Data


_RUNTIME_CONTRACT_SCHEMA = "embedding-runtime-contract/v2"
_MAX_EMBEDDING_DIMENSION = 65536
_MIN_EMBEDDING_CALL_INTERVAL_SECONDS = 1.0
_MAX_EMBEDDING_CALL_INTERVAL_SECONDS = 60.0
_RUNTIME_CLASS_PATTERN = re.compile(
    r"^<class '([A-Za-z_][A-Za-z0-9_]*(?:\.(?:[A-Za-z_][A-Za-z0-9_]*|<locals>))*)'>$"
)
_SAFE_EXCEPTION_TYPE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")


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


class _ProviderEmbeddingError(RuntimeError):
    """A provider failure with a safe, non-secret diagnostic summary.

    Provider exception strings can include request URLs, credential hints, or
    other operational details.  Keep the exception chained for local traces,
    but expose only its class, an HTTP status when available, and a coarse
    category to Langflow users.
    """

    pass


def _safe_exception_type(cause: Exception) -> str:
    # Standalone components intentionally avoid introspection such as
    # ``__module__``.  A bounded class name is enough for operator diagnostics.
    value = type(cause).__name__
    return value if _SAFE_EXCEPTION_TYPE_PATTERN.fullmatch(value) else "provider_exception"


def _provider_http_status(cause: Exception) -> int | None:
    """Read a status field without copying an arbitrary provider message."""
    try:
        response = cause.response
    except Exception:  # noqa: BLE001 - third-party error objects can use arbitrary properties
        response = None
    candidates: list[Any] = []
    for target in (cause, response):
        if target is None:
            continue
        try:
            candidates.append(target.status_code)
        except Exception:  # noqa: BLE001 - third-party error objects can use arbitrary properties
            pass
        try:
            candidates.append(target.status)
        except Exception:  # noqa: BLE001 - third-party error objects can use arbitrary properties
            pass
    for value in candidates:
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    return None


def _provider_error_category(cause: Exception, http_status: int | None) -> str:
    """Classify known transient/configuration families without emitting raw text."""
    try:
        material = str(cause).casefold()
    except Exception:  # noqa: BLE001 - defensive only; do not surface provider text
        material = ""
    if http_status in {401, 403} or any(token in material for token in ("api key", "credential", "unauthenticated", "permission denied")):
        return "authentication_or_authorization"
    if http_status == 429 or any(token in material for token in ("quota", "rate limit", "resource exhausted", "too many requests")):
        return "rate_limited_or_quota"
    if http_status is not None and 400 <= http_status < 500:
        return "provider_request_rejected"
    if http_status is not None and http_status >= 500:
        return "provider_temporary_failure"
    if any(token in material for token in ("timeout", "temporarily", "connection", "unavailable", "network")):
        return "provider_temporary_failure"
    return "provider_runtime_failure"


def _provider_embedding_error(cause: Exception) -> _ProviderEmbeddingError:
    """Build a marker error whose ordinary args contain only safe diagnostics."""
    http_status = _provider_http_status(cause)
    return _ProviderEmbeddingError(
        _safe_exception_type(cause),
        http_status,
        _provider_error_category(cause, http_status),
    )


def _provider_embedding_error_summary(error: _ProviderEmbeddingError) -> tuple[str, int | None, str]:
    args = error.args if isinstance(error.args, tuple) else ()
    exception_type = args[0] if len(args) >= 1 and isinstance(args[0], str) else "provider_exception"
    http_status = args[1] if len(args) >= 2 and isinstance(args[1], int) and not isinstance(args[1], bool) else None
    category = args[2] if len(args) >= 3 and isinstance(args[2], str) else "provider_runtime_failure"
    if not _SAFE_EXCEPTION_TYPE_PATTERN.fullmatch(exception_type):
        exception_type = "provider_exception"
    if category not in {
        "authentication_or_authorization",
        "rate_limited_or_quota",
        "provider_request_rejected",
        "provider_temporary_failure",
        "provider_runtime_failure",
    }:
        category = "provider_runtime_failure"
    return exception_type, http_status, category


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
        raise _provider_embedding_error(exc) from exc
    return _vectors(raw_vectors, len(texts), dimension, query_ids)


def _query_embedding_method(embedding: Any, underlying: Any) -> Callable[[str], Any] | None:
    """Prefer the provider's query-specific API without requiring one from every adapter."""
    for candidate in (embedding, underlying):
        try:
            method = getattr(candidate, "embed_query", None)
        except Exception:  # noqa: BLE001 - provider wrappers can expose arbitrary properties
            continue
        if callable(method):
            return method
    return None


def _embed_query(
    method: Callable[[str], Any],
    text: str,
    query_id: str,
    dimension: int | None,
) -> tuple[list[list[float]], int]:
    """Embed exactly one retrieval query and normalize the one-vector contract."""
    try:
        raw_vector = method(text)
    except Exception as exc:  # noqa: BLE001 - providers expose heterogeneous error types
        raise _provider_embedding_error(exc) from exc
    # A few adapters wrap the single vector once more.  That shape is
    # unambiguous for an exactly-one query call and is safe to normalize.
    if isinstance(raw_vector, list) and len(raw_vector) == 1 and isinstance(raw_vector[0], list):
        raw_vector = raw_vector[0]
    return _vectors([raw_vector], 1, dimension, [query_id])


def _run_embedding_call_with_retries(
    action: Callable[[], tuple[list[list[float]], int]],
    *,
    interval_seconds: float,
    max_retries: int,
    is_first_provider_call: bool,
) -> tuple[list[list[float]], int, int, int]:
    """Run one logical embedding action with the shared provider rate floor."""
    retries = 0
    while True:
        if not is_first_provider_call or retries:
            time.sleep(interval_seconds)
        try:
            vectors, dimension = action()
            return vectors, dimension, retries + 1, retries
        except _ProviderEmbeddingError:
            if retries >= max_retries:
                raise
            retries += 1


def _bounded_interval(value: Any) -> float:
    """Return the shared 1-second floor used by catalog and query embedding.

    The catalog ingest flow deliberately serializes one provider call at a
    time because the approved embedding service can reject rapid requests.
    Search-query embedding has the same provider boundary, so it must not
    bypass that protection simply because it has fewer texts.
    """
    try:
        interval = float(value)
    except (TypeError, ValueError):
        interval = _MIN_EMBEDDING_CALL_INTERVAL_SECONDS
    if not math.isfinite(interval):
        interval = _MIN_EMBEDDING_CALL_INTERVAL_SECONDS
    return max(_MIN_EMBEDDING_CALL_INTERVAL_SECONDS, min(_MAX_EMBEDDING_CALL_INTERVAL_SECONDS, interval))


def _error(
    trace_id: str,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "BLOCKED",
        "artifact_refs": [],
        "error": {"code": code, "message": message, "retryable": retryable, "details": details or {}},
        "resume": None,
        "trace_id": trace_id,
    }


def _configuration_error(trace_id: str, exc: Exception) -> dict[str, Any]:
    """Convert expected provider/model faults to an actionable safe envelope.

    The raw provider exception may contain endpoint details or credentials, so
    it is intentionally not surfaced in the Flow result.  The explicit action
    list is enough for a Langflow operator to fix a model binding.
    """
    text = str(exc)
    model_markers = (
        "Embedding Model",
        "stable model identity",
        "runtime class",
        "embed_documents",
        "more than one configured model",
    )
    vector_markers = (
        "embedding response",
        "vector is missing",
        "dimension is invalid",
        "boolean",
        "non-numeric",
        "non-finite",
    )
    if any(marker in text for marker in model_markers):
        return _error(
            trace_id,
            "EMBEDDING_MODEL_CONFIGURATION_REQUIRED",
            "승인 카탈로그와 같은 Langflow Embedding Model을 연결하고, provider가 안정적인 모델 ID와 embed_query 또는 embed_documents를 제공하는지 확인하세요.",
            details={
                "next_actions": [
                    "F00에 사용한 동일 provider/model을 Search Query Embedding Batcher에 연결합니다.",
                    "Embedding Model의 API credential과 모델 선택을 확인합니다.",
                ]
            },
        )
    if any(marker in text for marker in vector_markers):
        return _error(
            trace_id,
            "EMBEDDING_RESPONSE_INVALID",
            "Embedding Model 응답이 검색 query 수 또는 vector 차원 계약과 일치하지 않습니다.",
            details={"next_actions": ["Embedding provider의 batch 응답 수와 vector dimension을 확인합니다."]},
        )
    return _error(
        trace_id,
        "QUERY_EMBEDDING_INPUT_INVALID",
        "검색 query plan 또는 embedding 실행 설정이 유효하지 않습니다.",
        details={"next_actions": ["F20/F90의 sealed design invocation과 query 수 제한을 확인합니다."]},
    )


class SearchQueryEmbeddingBatcherComponent(Component):
    display_name = "29 Search Query Embedding Batcher"
    description = "Vectorize locked search queries through the connected Langflow Embedding Model in bounded batches, with at least one second between provider calls."
    icon = "Binary"
    name = "SearchQueryEmbeddingBatcher"

    inputs = [
        DataInput(name="query_plan", display_name="Search Query Plan", required=True),
        HandleInput(name="embedding", display_name="Embedding Model", input_types=["Embeddings"], required=True),
        IntInput(name="batch_size", display_name="Embedding Batch Size", value=16, advanced=True),
        IntInput(name="max_queries", display_name="Maximum Queries", value=30, advanced=True),
        IntInput(
            name="max_embedding_retries",
            display_name="Maximum Embedding Retries",
            value=2,
            advanced=True,
            info="Retries only transient provider failures. Each retry also waits at least the configured call interval.",
        ),
        FloatInput(
            name="embedding_call_interval_seconds",
            display_name="Embedding Call Interval (seconds)",
            value=_MIN_EMBEDDING_CALL_INTERVAL_SECONDS,
            advanced=True,
            info="The first provider call starts immediately. Later batches wait at least 1 second; values below 1 are raised to 1.",
        ),
    ]
    outputs = [Output(name="query_vectors", display_name="ID-preserving Query Vectors", method="build_query_vectors", types=["Data"])]

    def build_query_vectors(self) -> Data:
        trace_id = str(uuid.uuid4())
        try:
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
            interval = _bounded_interval(
                getattr(self, "embedding_call_interval_seconds", _MIN_EMBEDDING_CALL_INTERVAL_SECONDS)
            )
            max_retries = max(0, min(int(getattr(self, "max_embedding_retries", 2) or 0), 4))
            vectors: dict[str, list[float]] = {}
            dimension: int | None = None
            call_count = 0
            retry_count = 0
            query_method = _query_embedding_method(embedding, underlying)
            if query_method is not None:
                # Query-specific APIs such as Google Gemini's embed_query use a
                # RETRIEVAL_QUERY task type.  This is semantically correct for
                # F20's search terms and avoids treating a query as catalog text.
                for item in queries:
                    batch_vectors, dimension, calls, retries = _run_embedding_call_with_retries(
                        lambda item=item, dimension=dimension: _embed_query(
                            query_method,
                            item["text"],
                            item["id"],
                            dimension,
                        ),
                        interval_seconds=interval,
                        max_retries=max_retries,
                        is_first_provider_call=call_count == 0,
                    )
                    call_count += calls
                    retry_count += retries
                    vectors[item["id"]] = batch_vectors[0]
            else:
                # Not every Langflow Embeddings adapter exposes embed_query.
                # Keep the sealed batch contract as a compatible fallback.
                for start in range(0, len(queries), batch_size):
                    batch = queries[start : start + batch_size]
                    batch_vectors, dimension, calls, retries = _run_embedding_call_with_retries(
                        lambda batch=batch, dimension=dimension: _embed_documents(
                            embedding,
                            [item["text"] for item in batch],
                            [item["id"] for item in batch],
                            dimension,
                        ),
                        interval_seconds=interval,
                        max_retries=max_retries,
                        is_first_provider_call=call_count == 0,
                    )
                    call_count += calls
                    retry_count += retries
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
                "embedding_execution": {
                    "calls": call_count,
                    "retry_attempts": retry_count,
                    "minimum_interval_seconds": interval,
                    "batch_size": batch_size,
                },
                **locks,
                "trace_id": trace_id,
            }
            self.status = (
                f"Vectorized {len(vectors)} queries with {contract['model_id']} ({contract['dimension']}d); "
                f"{call_count} provider calls at >= {interval:.1f}s intervals."
            )
            return Data(data=result)
        except _ProviderEmbeddingError as exc:
            exception_type, http_status, category = _provider_embedding_error_summary(exc)
            retryable = category not in {"authentication_or_authorization", "provider_request_rejected"}
            details: dict[str, Any] = {
                "provider_failure_category": category,
                "provider_exception_type": exception_type,
                "next_actions": [
                    "F00과 같은 Embedding Model의 credential, provider 상태, quota를 확인합니다.",
                    "일시 장애 또는 quota 제한이면 잠시 후 같은 F20 입력으로 다시 실행합니다.",
                ],
            }
            if http_status is not None:
                details["provider_http_status"] = http_status
            result = _error(
                trace_id,
                "EMBEDDING_PROVIDER_ERROR",
                "Embedding Model이 검색 query를 벡터화하지 못했습니다. provider 상태와 credential을 확인한 뒤 다시 실행하세요.",
                retryable=retryable,
                details=details,
            )
        except (TypeError, ValueError) as exc:
            result = _configuration_error(trace_id, exc)
        self.status = f"Query embedding blocked: {result['error']['code']}"
        return Data(data=result)
