from __future__ import annotations

import json
import math
import hashlib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from pymongo import MongoClient, UpdateOne
from pymongo.errors import BulkWriteError, PyMongoError

from lfx.custom import Component
from lfx.io import BoolInput, DataInput, IntInput, Output, SecretStrInput, StrInput
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


def _parse_host_allowlist(value: Any) -> set[str]:
    return {item.strip().lower() for item in str(value or "").split(",") if item.strip()}


def _validate_endpoint(endpoint: str, allow_insecure_http: bool, approved_hosts: set[str]) -> str:
    if not endpoint or len(endpoint) > 2048:
        raise ValueError("An embedding endpoint is required and must be at most 2048 characters.")
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.username or parsed.password:
        raise ValueError("Credentials must not be embedded in the endpoint URL.")
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("The embedding endpoint must be an absolute HTTP(S) URL.")
    if parsed.scheme != "https" and not allow_insecure_http:
        raise ValueError("HTTPS is required unless insecure HTTP is explicitly enabled for an approved internal endpoint.")
    if not approved_hosts:
        raise ValueError("At least one approved embedding hostname must be configured.")
    hostname = str(parsed.hostname or "").lower()
    default_port = 443 if parsed.scheme == "https" else 80
    authority = f"{hostname}:{parsed.port or default_port}"
    if hostname not in approved_hosts and authority not in approved_hosts:
        raise ValueError("The embedding endpoint hostname is not in the approved allowlist.")
    return endpoint


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _parse_embedding_response(payload: Any, expected_count: int, dimension: int) -> list[list[float]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("The embedding provider response does not contain a data array.")
    indexed: dict[int, list[float]] = {}
    for fallback_index, item in enumerate(payload["data"]):
        if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
            raise ValueError("The embedding provider returned an invalid vector item.")
        raw_index = item.get("index", fallback_index)
        if isinstance(raw_index, bool):
            raise ValueError("The embedding provider returned an invalid vector index.")
        try:
            index = int(raw_index)
        except (TypeError, ValueError) as exc:
            raise ValueError("The embedding provider returned an invalid vector index.") from exc
        if index in indexed or index < 0 or index >= expected_count:
            raise ValueError("The embedding provider returned duplicate or out-of-range indexes.")
        vector: list[float] = []
        for raw_number in item["embedding"]:
            if isinstance(raw_number, bool):
                raise ValueError("The embedding provider returned a non-numeric vector value.")
            try:
                number = float(raw_number)
            except (TypeError, ValueError) as exc:
                raise ValueError("The embedding provider returned a non-numeric vector value.") from exc
            if not math.isfinite(number):
                raise ValueError("The embedding provider returned a non-finite vector value.")
            vector.append(number)
        if len(vector) != dimension:
            raise ValueError("The embedding vector dimension does not match the configured dimension.")
        indexed[index] = vector
    if len(indexed) != expected_count:
        raise ValueError("The embedding provider returned a different number of vectors than requested.")
    return [indexed[index] for index in range(expected_count)]


def _request_embeddings(
    endpoint: str,
    api_key: str,
    model: str,
    texts: list[str],
    dimension: int,
    timeout_seconds: int,
    max_response_bytes: int,
) -> list[list[float]]:
    request_body = json.dumps({"model": model, "input": texts}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    request = urllib.request.Request(endpoint, data=request_body, headers=headers, method="POST")
    opener = urllib.request.build_opener(_NoRedirectHandler())
    with opener.open(request, timeout=timeout_seconds) as response:  # noqa: S310 - endpoint host is allowlisted above
        body = response.read(max_response_bytes + 1)
        if len(body) > max_response_bytes:
            raise ValueError("The embedding provider response exceeds the configured size limit.")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("The embedding provider returned invalid JSON.") from exc
    return _parse_embedding_response(payload, len(texts), dimension)


def _valid_reused_vector(document: dict[str, Any] | None, model: str, version: str, dimension: int) -> list[float] | None:
    if not isinstance(document, dict):
        return None
    embedding = document.get("embedding") if isinstance(document.get("embedding"), dict) else {}
    vector = embedding.get("vector")
    if (
        embedding.get("model") != model
        or embedding.get("version") != version
        or int(embedding.get("dimension") or 0) != dimension
        or not isinstance(vector, list)
        or len(vector) != dimension
    ):
        return None
    if any(isinstance(item, bool) for item in vector):
        return None
    try:
        numbers = [float(item) for item in vector]
    except (TypeError, ValueError):
        return None
    return numbers if all(math.isfinite(item) for item in numbers) else None


class CatalogEmbeddingBatcherComponent(Component):
    display_name = "Catalog Embedding Batcher"
    description = "Reuse unchanged vectors and call an approved OpenAI-compatible embedding endpoint in bounded batches."
    icon = "Binary"
    name = "CatalogEmbeddingBatcher"

    inputs = [
        DataInput(name="job_ref", display_name="Text-Built Job Reference", required=True),
        SecretStrInput(name="mongodb_uri", display_name="MongoDB URI", required=True),
        StrInput(name="mongodb_database", display_name="MongoDB Database", value="business_work_design", required=True),
        StrInput(name="embedding_endpoint", display_name="Embedding Endpoint", required=True),
        StrInput(
            name="approved_embedding_hosts",
            display_name="Approved Embedding Hosts",
            required=True,
            info="Comma-separated exact hostnames or hostname:port entries. Redirects are rejected.",
        ),
        SecretStrInput(name="embedding_api_key", display_name="Embedding API Key", required=True),
        StrInput(name="embedding_model", display_name="Embedding Model", required=True),
        StrInput(name="embedding_version", display_name="Embedding Version", required=True),
        IntInput(name="embedding_dimension", display_name="Embedding Dimension", value=1024, required=True),
        IntInput(name="provider_batch_size", display_name="Provider Batch Size", value=64, advanced=True),
        IntInput(name="max_assets_per_run", display_name="Maximum Assets Per Run", value=500, advanced=True),
        IntInput(name="max_chunks_per_run", display_name="Maximum Chunks Per Run", value=2000, advanced=True),
        IntInput(name="provider_timeout_seconds", display_name="Provider Timeout (seconds)", value=60, advanced=True),
        IntInput(name="max_response_mb", display_name="Maximum Provider Response (MiB)", value=32, advanced=True),
        IntInput(name="connect_timeout_ms", display_name="MongoDB Connect Timeout (ms)", value=5000, advanced=True),
        BoolInput(name="allow_insecure_http", display_name="Allow Insecure Internal HTTP", value=False, advanced=True),
    ]

    outputs = [Output(name="embedded_job_ref", display_name="Embedded Job Reference", method="embed_chunks", types=["Data"])]

    def embed_chunks(self) -> Data:
        trace_id = "trace-unassigned"
        try:
            incoming = _validate_job_ref(getattr(self, "job_ref", None))
            trace_id = incoming["trace_id"]
            mongodb_uri = _secret_value(getattr(self, "mongodb_uri", ""))
            api_key = _secret_value(getattr(self, "embedding_api_key", ""))
            database_name = str(getattr(self, "mongodb_database", "") or "").strip()
            model = str(getattr(self, "embedding_model", "") or "").strip()
            version = str(getattr(self, "embedding_version", "") or "").strip()
            approved_hosts = _parse_host_allowlist(getattr(self, "approved_embedding_hosts", ""))
            endpoint = _validate_endpoint(
                str(getattr(self, "embedding_endpoint", "") or "").strip(),
                bool(getattr(self, "allow_insecure_http", False)),
                approved_hosts,
            )
            if not mongodb_uri or not api_key or not database_name or not model or not version:
                return _failure("EMBEDDING_CONFIG_MISSING", "MongoDB and embedding provider configuration are required.", trace_id)

            dimension = _bounded_int(getattr(self, "embedding_dimension", 1024), 1024, 8, 8192)
            batch_size = _bounded_int(getattr(self, "provider_batch_size", 64), 64, 16, 256)
            max_assets = _bounded_int(getattr(self, "max_assets_per_run", 500), 500, 1, 2000)
            max_chunks = _bounded_int(getattr(self, "max_chunks_per_run", 2000), 2000, 1, 5000)
            timeout_seconds = _bounded_int(getattr(self, "provider_timeout_seconds", 60), 60, 5, 120)
            max_response_bytes = _bounded_int(getattr(self, "max_response_mb", 32), 32, 1, 128) * 1024 * 1024
            timeout_ms = _bounded_int(getattr(self, "connect_timeout_ms", 5000), 5000, 1000, 30000)
            client = MongoClient(
                mongodb_uri,
                connectTimeoutMS=timeout_ms,
                serverSelectionTimeoutMS=timeout_ms,
                socketTimeoutMS=max(timeout_ms, timeout_seconds * 1000),
                retryReads=True,
                retryWrites=True,
            )
            try:
                client.admin.command("ping")
                database = client[database_name]
                jobs = database["catalog_ingest_jobs"]
                staging = database["catalog_ingest_chunks"]
                existing_chunks = database["catalog_asset_chunks"]
                query = {"_id": incoming["job_id"], "tenant_id": incoming["tenant_id"], "snapshot_id": incoming["snapshot_id"]}
                job = jobs.find_one(query)
                if not job:
                    return _failure("CATALOG_JOB_NOT_FOUND", "The catalog ingest job was not found for this tenant.", trace_id)
                current_stage = str(job.get("stage") or "")
                if current_stage == "EMBEDDING_COMPLETED":
                    cursor = int((job.get("stage_cursors") or {}).get("embedding") or 0)
                    return Data(data=_job_ref(incoming, current_stage, cursor))
                if current_stage not in {"TEXT_BUILD_COMPLETED", "EMBEDDING_PARTIAL"}:
                    return _failure("CATALOG_STAGE_CONFLICT", "Embedding requires a fully text-built job.", trace_id)

                parsed_endpoint = urllib.parse.urlparse(endpoint)
                requested_contract = {
                    "model": model,
                    "version": version,
                    "dimension": dimension,
                    "provider_host": str(parsed_endpoint.hostname or "").lower(),
                    "provider_endpoint_sha256": hashlib.sha256(endpoint.encode("utf-8")).hexdigest(),
                }
                persisted_contract = job.get("embedding_contract") if isinstance(job.get("embedding_contract"), dict) else None
                if persisted_contract is None:
                    frozen = jobs.update_one(
                        {**query, "stage": current_stage, "embedding_contract": {"$exists": False}},
                        {"$set": {"embedding_contract": requested_contract, "updated_at": _utc_now()}},
                    )
                    if frozen.modified_count != 1:
                        job = jobs.find_one(query) or {}
                        persisted_contract = job.get("embedding_contract") if isinstance(job.get("embedding_contract"), dict) else None
                    else:
                        persisted_contract = requested_contract
                if persisted_contract != requested_contract:
                    return _failure(
                        "EMBEDDING_CONTRACT_CHANGED",
                        "Embedding model, version, dimension, or approved provider endpoint changed during this job.",
                        trace_id,
                    )

                durable_cursor = int((job.get("stage_cursors") or {}).get("embedding") or 0)
                text_cursor = int((job.get("stage_cursors") or {}).get("text") or 0)
                valid_transition = (
                    current_stage == "TEXT_BUILD_COMPLETED"
                    and incoming["stage"] == "TEXT_BUILD_COMPLETED"
                    and incoming["expected_cursor"] == text_cursor
                ) or (
                    current_stage == "EMBEDDING_PARTIAL"
                    and incoming["stage"] == "EMBEDDING_PARTIAL"
                    and incoming["expected_cursor"] == durable_cursor
                )
                if not valid_transition:
                    return _failure("CATALOG_CURSOR_CONFLICT", "The embedding cursor is stale.", trace_id)
                documents = list(
                    staging.find(
                        {"tenant_id": incoming["tenant_id"], "job_id": incoming["job_id"], "record_index": {"$gte": durable_cursor}}
                    ).sort("record_index", 1).limit(max_assets + 1)
                )
                has_more_documents = len(documents) > max_assets
                selected = documents[:max_assets]
                working: dict[str, dict[str, Any]] = {}
                provider_tasks: list[tuple[str, int, str]] = []
                reused_count = 0
                skipped_count = 0
                next_cursor = durable_cursor
                budget_exhausted = False

                for document in selected:
                    record_index = int(document.get("record_index") or 0)
                    if document.get("text_build_status") != "TEXT_BUILT":
                        working[document["_id"]] = {"document": document, "chunks": document.get("asset_chunks") or [], "complete": True, "skipped": True}
                        next_cursor = record_index + 1
                        skipped_count += 1
                        continue
                    chunks = [dict(item) for item in (document.get("asset_chunks") or []) if isinstance(item, dict)]
                    if not chunks:
                        raise ValueError("A text-built asset has no embedding chunks.")
                    working[document["_id"]] = {"document": document, "chunks": chunks, "complete": True, "skipped": False}
                    for chunk_index, chunk in enumerate(chunks):
                        embedding = chunk.get("embedding") if isinstance(chunk.get("embedding"), dict) else {}
                        if _valid_reused_vector({"embedding": embedding}, model, version, dimension) is not None:
                            continue
                        if len(provider_tasks) + reused_count >= max_chunks:
                            working[document["_id"]]["complete"] = False
                            budget_exhausted = True
                            break
                        prior = existing_chunks.find_one(
                            {
                                "tenant_id": incoming["tenant_id"],
                                "asset_id": chunk.get("asset_id"),
                                "version": chunk.get("version"),
                                "chunk_id": chunk.get("chunk_id"),
                                "embedding.input_sha256": chunk.get("embedding_input_sha256"),
                                "embedding.model": model,
                                "embedding.version": version,
                                "embedding.dimension": dimension,
                            },
                            {"embedding": 1},
                        )
                        reused_vector = _valid_reused_vector(prior, model, version, dimension)
                        if reused_vector is not None:
                            chunk["embedding"] = {
                                "vector": reused_vector,
                                "model": model,
                                "version": version,
                                "dimension": dimension,
                                "input_sha256": chunk.get("embedding_input_sha256"),
                                "reused": True,
                            }
                            reused_count += 1
                        else:
                            provider_tasks.append((document["_id"], chunk_index, str(chunk.get("embedding_text_redacted") or "")))
                    if budget_exhausted:
                        break
                    next_cursor = record_index + 1

                for batch_start in range(0, len(provider_tasks), batch_size):
                    batch = provider_tasks[batch_start : batch_start + batch_size]
                    texts = [item[2] for item in batch]
                    if any(not text or len(text) > 20000 for text in texts):
                        raise ValueError("An embedding chunk is empty or exceeds the configured text limit.")
                    vectors = _request_embeddings(endpoint, api_key, model, texts, dimension, timeout_seconds, max_response_bytes)
                    for (document_id, chunk_index, _), vector in zip(batch, vectors, strict=True):
                        chunk = working[document_id]["chunks"][chunk_index]
                        chunk["embedding"] = {
                            "vector": vector,
                            "model": model,
                            "version": version,
                            "dimension": dimension,
                            "input_sha256": chunk.get("embedding_input_sha256"),
                            "reused": False,
                        }

                now = _utc_now()
                operations: list[UpdateOne] = []
                completed_assets = 0
                for document_id, item in working.items():
                    document = item["document"]
                    if item["skipped"]:
                        operations.append(
                            UpdateOne(
                                {"_id": document_id, "tenant_id": incoming["tenant_id"]},
                                {"$set": {"embedding_status": "SKIPPED_QUARANTINE", "updated_at": now}},
                            )
                        )
                        continue
                    chunks = item["chunks"]
                    all_embedded = all(
                        _valid_reused_vector({"embedding": chunk.get("embedding")}, model, version, dimension) is not None
                        for chunk in chunks
                    )
                    item["complete"] = bool(item["complete"] and all_embedded)
                    if item["complete"]:
                        completed_assets += 1
                    asset_document = dict(document.get("asset_document") or {})
                    asset_document["embedding_manifest"] = {
                        "model": model,
                        "version": version,
                        "dimension": dimension,
                        "chunk_count": len(chunks),
                        "complete": all_embedded,
                    }
                    operations.append(
                        UpdateOne(
                            {"_id": document_id, "tenant_id": incoming["tenant_id"]},
                            {
                                "$set": {
                                    "asset_document": asset_document,
                                    "asset_chunks": chunks,
                                    "embedding_status": "EMBEDDED" if item["complete"] else "EMBEDDING_PARTIAL",
                                    "updated_at": now,
                                }
                            },
                        )
                    )
                if operations:
                    try:
                        staging.bulk_write(operations, ordered=False)
                    except BulkWriteError as exc:
                        if (exc.details or {}).get("writeErrors"):
                            raise

                any_incomplete = any(not item["complete"] for item in working.values())
                has_more = has_more_documents or budget_exhausted or any_incomplete
                next_stage = "EMBEDDING_PARTIAL" if has_more else "EMBEDDING_COMPLETED"
                update = jobs.update_one(
                    {**query, "stage": current_stage, "stage_cursors.embedding": durable_cursor},
                    {
                        "$set": {
                            "stage": next_stage,
                            "expected_cursor": next_cursor,
                            "stage_cursors.embedding": next_cursor,
                            "embedding_completed": not has_more,
                            "embedding_contract": requested_contract,
                            "updated_at": now,
                        },
                        "$inc": {
                            "counts.embedding_assets_completed": completed_assets,
                            "counts.embedding_chunks_created": len(provider_tasks),
                            "counts.embedding_chunks_reused": reused_count,
                            "counts.embedding_skipped": skipped_count,
                        },
                    },
                )
                if update.modified_count != 1:
                    return _failure("CATALOG_CURSOR_CONFLICT", "The embedding cursor changed during this batch.", trace_id, True)
                self.status = (
                    f"Catalog embedding batch stored: job_id={incoming['job_id']}, created={len(provider_tasks)}, "
                    f"reused={reused_count}, cursor={next_cursor}, complete={not has_more}"
                )
                return Data(data=_job_ref(incoming, next_stage, next_cursor))
            finally:
                client.close()
        except ValueError as exc:
            self.status = "Embedding stopped on a provider or input contract mismatch."
            return _failure("EMBEDDING_CONTRACT_INVALID", str(exc), trace_id)
        except urllib.error.HTTPError as exc:
            self.status = "Embedding provider returned an HTTP failure."
            return _failure(
                "EMBEDDING_PROVIDER_FAILED",
                "The embedding provider rejected the bounded batch.",
                trace_id,
                retryable=exc.code == 429 or 500 <= exc.code < 600,
            )
        except (urllib.error.URLError, TimeoutError):
            self.status = "Embedding provider was unavailable."
            return _failure("EMBEDDING_PROVIDER_UNAVAILABLE", "The embedding provider is unavailable.", trace_id, retryable=True)
        except PyMongoError:
            self.status = "Embedding progress could not be stored."
            return _failure(
                "EMBEDDING_STORAGE_FAILED",
                "Embedding progress could not be stored. The snapshot remains incomplete.",
                trace_id,
                retryable=True,
            )
