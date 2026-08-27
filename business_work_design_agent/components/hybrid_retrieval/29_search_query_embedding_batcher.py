from __future__ import annotations

"""Create ID-preserving query vectors for hybrid retrieval without local imports."""

import hashlib
import hmac
import json
import math
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from lfx.custom import Component
from lfx.io import BoolInput, DataInput, DropdownInput, IntInput, Output, SecretStrInput, StrInput
from lfx.schema import Data


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201, ARG002
        return None


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


def _secret(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    return str(getter() if callable(getter) else value or "").strip()


def _canonical_hash(value: Any) -> str:
    material = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
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


def _vector(value: Any, dimension: int, query_id: str) -> list[float]:
    if not isinstance(value, list) or len(value) != dimension:
        raise ValueError(f"vector dimension mismatch for query_id={query_id}")
    result: list[float] = []
    for item in value:
        if isinstance(item, bool):
            raise ValueError(f"boolean vector value for query_id={query_id}")
        try:
            number = float(item)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"non-numeric vector value for query_id={query_id}") from exc
        if not math.isfinite(number):
            raise ValueError(f"non-finite vector value for query_id={query_id}")
        result.append(number)
    return result


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


def _endpoint(value: str, allow_loopback: bool, allowed_host: str) -> str:
    parsed = urllib.parse.urlsplit(value.strip())
    hostname = (parsed.hostname or "").lower().rstrip(".")
    loopback = hostname in {"localhost", "127.0.0.1", "::1"}
    if not hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("embedding_endpoint must be an absolute URL without credentials or fragment")
    if parsed.scheme != "https" and not (allow_loopback and loopback and parsed.scheme == "http"):
        raise ValueError("HTTPS is required; HTTP is allowed only for explicit loopback development")
    expected_host = allowed_host.strip().lower().rstrip(".")
    if not expected_host:
        raise ValueError("allowed_host is required for the embedding endpoint")
    if hostname != expected_host:
        raise ValueError("embedding endpoint hostname does not match allowed_host")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def _response_vectors(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("vectors"), dict):
        return dict(payload["vectors"])
    data = payload.get("data")
    if isinstance(data, list):
        result: dict[str, Any] = {}
        for item in data:
            if isinstance(item, dict) and item.get("id") is not None:
                result[str(item["id"])] = item.get("embedding") or item.get("vector")
        return result
    raise ValueError("embedding response must contain vectors or data")


class SearchQueryEmbeddingBatcherComponent(Component):
    display_name = "29 검색 Query Embedding Batcher"
    description = "Vectorizes every planned query with stable IDs using an approved endpoint or explicit precomputed vectors."
    icon = "Binary"
    name = "SearchQueryEmbeddingBatcher"

    inputs = [
        DataInput(name="query_plan", display_name="Search Query Plan", required=True),
        DropdownInput(name="provider_mode", display_name="Provider Mode", options=["http_json", "precomputed"], value="http_json"),
        DataInput(name="precomputed_vectors", display_name="Precomputed Vectors", required=False, advanced=True),
        StrInput(name="embedding_endpoint", display_name="Embedding Endpoint", value="", required=False),
        SecretStrInput(name="api_token", display_name="Embedding API Token", required=False),
        StrInput(name="model", display_name="Embedding Model", required=True),
        StrInput(name="model_version", display_name="Embedding Model Version", required=True),
        IntInput(name="dimension", display_name="Vector Dimension", value=1024, required=True),
        IntInput(name="batch_size", display_name="Batch Size", value=16, advanced=True),
        IntInput(name="max_queries", display_name="Maximum Queries", value=30, advanced=True),
        IntInput(name="timeout_seconds", display_name="Timeout (seconds)", value=30, advanced=True),
        IntInput(name="max_response_bytes", display_name="Maximum Response Bytes", value=5000000, advanced=True),
        StrInput(name="allowed_host", display_name="Exact Allowed Host", value="", advanced=True),
        BoolInput(name="allow_insecure_loopback", display_name="Allow HTTP Loopback", value=False, advanced=True),
    ]
    outputs = [Output(name="query_vectors", display_name="ID-preserving Query Vectors", method="build_query_vectors", types=["Data"])]

    def build_query_vectors(self) -> Data:
        plan = _payload(self.query_plan, "query_plan")
        locks = _verified_plan_locks(plan)
        maximum = max(1, min(int(getattr(self, "max_queries", 30) or 30), 100))
        queries = _queries(plan, maximum)
        dimension = max(1, min(int(getattr(self, "dimension", 1024) or 1024), 65536))
        model = str(getattr(self, "model", "") or "").strip()
        model_version = str(getattr(self, "model_version", "") or "").strip()
        if not model or not model_version:
            raise ValueError("model and model_version are required")
        mode = str(getattr(self, "provider_mode", "http_json") or "")
        raw_vectors: dict[str, Any] = {}
        provider_receipts: list[dict[str, Any]] = []
        if mode == "precomputed":
            supplied = _payload(getattr(self, "precomputed_vectors", None), "precomputed_vectors")
            raw_vectors = supplied.get("vectors") if isinstance(supplied.get("vectors"), dict) else supplied
        elif mode == "http_json":
            endpoint = _endpoint(
                str(getattr(self, "embedding_endpoint", "") or ""),
                bool(getattr(self, "allow_insecure_loopback", False)),
                str(getattr(self, "allowed_host", "") or ""),
            )
            token = _secret(getattr(self, "api_token", ""))
            hostname = (urllib.parse.urlsplit(endpoint).hostname or "").lower()
            if hostname not in {"localhost", "127.0.0.1", "::1"} and not token:
                raise ValueError("api_token is required outside loopback development")
            batch_size = max(1, min(int(getattr(self, "batch_size", 16) or 16), 64))
            timeout = max(1, min(int(getattr(self, "timeout_seconds", 30) or 30), 120))
            response_limit = max(1024, min(int(getattr(self, "max_response_bytes", 5_000_000) or 5_000_000), 25_000_000))
            opener = urllib.request.build_opener(_NoRedirect())
            for start in range(0, len(queries), batch_size):
                batch = queries[start : start + batch_size]
                request_payload = {"model": model, "version": model_version, "dimension": dimension, "inputs": batch}
                body = json.dumps(request_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "business-work-design-agent/1.0"}
                if token:
                    headers["Authorization"] = "Bearer " + token
                request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
                try:
                    with opener.open(request, timeout=timeout) as response:
                        response_body = response.read(response_limit + 1)
                        status_code = int(response.status)
                except urllib.error.HTTPError as exc:
                    raise ValueError(f"embedding provider rejected batch with HTTP {exc.code}") from exc
                except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
                    raise ValueError("embedding provider is unavailable") from exc
                if status_code not in {200, 201} or len(response_body) > response_limit:
                    raise ValueError("embedding provider returned an invalid or oversized response")
                try:
                    response_payload = json.loads(response_body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError("embedding provider response must be UTF-8 JSON") from exc
                if not isinstance(response_payload, dict):
                    raise ValueError("embedding provider response must be an object")
                raw_vectors.update(_response_vectors(response_payload))
                provider_receipts.append({"start": start, "count": len(batch), "response_sha256": hashlib.sha256(response_body).hexdigest()})
        else:
            raise ValueError("unsupported provider_mode")
        expected_ids = {item["id"] for item in queries}
        if set(raw_vectors) != expected_ids:
            raise ValueError("embedding response query IDs do not exactly match the query plan")
        vectors = {item["id"]: _vector(raw_vectors[item["id"]], dimension, item["id"]) for item in queries}
        result = {
            "ok": True,
            "status": "VECTORIZED",
            "schema_version": "query-vectors/v1",
            "vectors": vectors,
            "query_order": [item["id"] for item in queries],
            "embedding_contract": {"provider_mode": mode, "model": model, "version": model_version, "dimension": dimension},
            "provider_receipts": provider_receipts,
            **locks,
        }
        self.status = f"Vectorized {len(vectors)} queries with {model}@{model_version}"
        return Data(data=result)
