from __future__ import annotations

"""FastAPI worker for durable, bounded catalog ingestion and approval issuance."""

import hashlib
import hmac
import base64
import importlib.util
import json
import multiprocessing
import os
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from queue import Empty
from threading import RLock
from typing import Any, Callable, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import PyMongoError


IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,255}$")
JOB_REF_KEYS = ("tenant_id", "job_id", "snapshot_id", "stage", "expected_cursor", "trace_id")
FINAL_SUCCESS = {"VALIDATED"}
FINAL_FAILURE = {"BLOCKED", "FAILED", "VALIDATION_FAILED"}
STAGE_ROUTES = {
    "SECRET_SCAN_PASSED": "parse",
    "PARSE_PARTIAL": "parse",
    "PARSE_COMPLETED": "normalize",
    "NORMALIZE_PARTIAL": "normalize",
    "NORMALIZE_COMPLETED": "text",
    "TEXT_BUILD_PARTIAL": "text",
    "TEXT_BUILD_COMPLETED": "embed",
    "EMBEDDING_PARTIAL": "embed",
    "EMBEDDING_COMPLETED": "write",
    "SNAPSHOT_WRITE_PARTIAL": "write",
    "SNAPSHOT_WRITE_COMPLETED": "validate",
}
ALLOWED_RESULT_STAGES = {
    "parse": {"PARSE_PARTIAL", "PARSE_COMPLETED"},
    "normalize": {"NORMALIZE_PARTIAL", "NORMALIZE_COMPLETED"},
    "text": {"TEXT_BUILD_PARTIAL", "TEXT_BUILD_COMPLETED"},
    "embed": {"EMBEDDING_PARTIAL", "EMBEDDING_COMPLETED"},
    "write": {"SNAPSHOT_WRITE_PARTIAL", "SNAPSHOT_WRITE_COMPLETED"},
}
COMPONENT_SPECS = {
    "parse": ("02_catalog_stream_parser.py", "CatalogStreamParserComponent", "parse_catalog", "job_ref"),
    "normalize": ("03_catalog_record_normalizer.py", "CatalogRecordNormalizerComponent", "normalize_records", "job_ref"),
    "text": ("04_catalog_embedding_text_builder.py", "CatalogEmbeddingTextBuilderComponent", "build_text", "job_ref"),
    "embed": ("05_catalog_embedding_batcher.py", "CatalogEmbeddingBatcherComponent", "embed_chunks", "job_ref"),
    "write": ("06_mongodb_snapshot_writer.py", "MongoDBSnapshotWriterComponent", "write_snapshot", "job_ref"),
    "validate": ("07_catalog_snapshot_validator.py", "CatalogSnapshotValidatorComponent", "validate_snapshot", "snapshot_ref"),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _validate_identity(value: str, label: str) -> str:
    cleaned = str(value or "").strip()
    if not IDENTIFIER_PATTERN.fullmatch(cleaned):
        raise ValueError(f"{label} is invalid")
    return cleaned


def _job_ref(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("job_ref must be an object")
    result = {key: value.get(key) for key in JOB_REF_KEYS}
    for key in ("tenant_id", "job_id", "snapshot_id", "stage", "trace_id"):
        result[key] = _validate_identity(str(result.get(key) or ""), f"job_ref.{key}")
    try:
        result["expected_cursor"] = max(0, int(result.get("expected_cursor") or 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("job_ref.expected_cursor must be an integer") from exc
    return result


def _public_failure(code: str, message: str, trace_id: str, *, retryable: bool = False) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "BLOCKED",
        "artifact_refs": [],
        "error": {"code": code, "message": message, "retryable": retryable, "details": {}},
        "resume": None,
        "trace_id": trace_id,
    }


def _extract_result(value: Any) -> dict[str, Any]:
    data = getattr(value, "data", value)
    if not isinstance(data, dict):
        raise ValueError("stage output must be an object")
    return dict(data)


def _next_job_ref(result: dict[str, Any]) -> dict[str, Any] | None:
    nested = result.get("job_ref")
    candidate = nested if isinstance(nested, dict) else result
    try:
        return _job_ref(candidate)
    except ValueError:
        return None


def run_catalog_pipeline(
    initial_job_ref: dict[str, Any],
    invoke_stage: Callable[[str, dict[str, Any]], dict[str, Any]],
    *,
    max_stage_invocations: int,
    max_total_seconds: float,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Pure orchestration helper; durable cursors remain owned by stage components."""

    current = _job_ref(initial_job_ref)
    trace_id = current["trace_id"]
    invocation_limit = _bounded_int(max_stage_invocations, 400, 1, 1000)
    total_limit = max(1.0, min(float(max_total_seconds), 7200.0))
    started = monotonic()
    history: list[dict[str, Any]] = []

    for invocation_number in range(1, invocation_limit + 1):
        if monotonic() - started >= total_limit:
            failure = _public_failure(
                "CATALOG_PIPELINE_DEADLINE_EXCEEDED",
                "The bounded catalog run reached its total execution deadline; resume with the latest durable job reference.",
                trace_id,
                retryable=True,
            )
            failure["job_ref"] = current
            failure["invocation_count"] = invocation_number - 1
            return failure

        route = STAGE_ROUTES.get(current["stage"])
        if route is None:
            return _public_failure(
                "CATALOG_PIPELINE_STAGE_INVALID",
                "The durable catalog job is not at a worker-runnable stage.",
                trace_id,
            )
        try:
            remaining_seconds = max(0.001, total_limit - (monotonic() - started))
            timed_invoker = getattr(invoke_stage, "invoke_with_timeout", None)
            if callable(timed_invoker):
                result = _extract_result(timed_invoker(route, current, remaining_seconds))
            else:
                result = _extract_result(invoke_stage(route, current))
        except TimeoutError:
            failure = _public_failure(
                "CATALOG_STAGE_TIMEOUT",
                "A catalog stage exceeded its per-invocation deadline; resume from the durable cursor.",
                trace_id,
                retryable=True,
            )
            failure["job_ref"] = current
            failure["invocation_count"] = invocation_number - 1
            return failure
        except Exception:
            failure = _public_failure(
                "CATALOG_STAGE_EXECUTION_FAILED",
                "A catalog stage failed before a valid bounded result was returned.",
                trace_id,
                retryable=True,
            )
            failure["job_ref"] = current
            failure["invocation_count"] = invocation_number - 1
            return failure

        status_value = str(result.get("status") or "").upper()
        history.append({"invocation": invocation_number, "stage": route, "status": status_value or None})
        if result.get("ok") is False or status_value in FINAL_FAILURE:
            safe = dict(result)
            safe.setdefault("ok", False)
            safe.setdefault("status", "BLOCKED")
            safe["invocation_count"] = invocation_number
            safe["last_durable_job_ref"] = current
            safe["stage_history"] = history
            return safe
        if status_value in FINAL_SUCCESS and result.get("ok") is True:
            safe = dict(result)
            safe["invocation_count"] = invocation_number
            safe["stage_history"] = history
            return safe

        next_ref = _next_job_ref(result)
        if next_ref is None:
            failure = _public_failure(
                "CATALOG_STAGE_OUTPUT_INVALID",
                "A catalog stage did not return a valid durable job reference.",
                trace_id,
            )
            failure["invocation_count"] = invocation_number
            failure["last_durable_job_ref"] = current
            failure["stage_history"] = history
            return failure
        if (
            next_ref["tenant_id"] != current["tenant_id"]
            or next_ref["job_id"] != current["job_id"]
            or next_ref["snapshot_id"] != current["snapshot_id"]
            or next_ref["trace_id"] != current["trace_id"]
        ):
            failure = _public_failure(
                "CATALOG_STAGE_SCOPE_MISMATCH",
                "A catalog stage attempted to change the durable job scope.",
                trace_id,
            )
            failure["invocation_count"] = invocation_number
            failure["last_durable_job_ref"] = current
            failure["stage_history"] = history
            return failure
        allowed_stages = ALLOWED_RESULT_STAGES.get(route)
        if allowed_stages is not None and next_ref["stage"] not in allowed_stages:
            failure = _public_failure(
                "CATALOG_STAGE_TRANSITION_INVALID",
                "A catalog stage returned a state outside its allowed transition contract.",
                trace_id,
            )
            failure["invocation_count"] = invocation_number
            failure["last_durable_job_ref"] = current
            failure["stage_history"] = history
            return failure
        if next_ref["stage"].endswith("_PARTIAL") and next_ref["expected_cursor"] <= current["expected_cursor"]:
            failure = _public_failure(
                "CATALOG_STAGE_NO_PROGRESS",
                "A partial catalog stage did not advance its durable cursor.",
                trace_id,
            )
            failure["invocation_count"] = invocation_number
            failure["last_durable_job_ref"] = current
            failure["stage_history"] = history
            return failure
        current = next_ref

    failure = _public_failure(
        "CATALOG_PIPELINE_INVOCATION_LIMIT",
        "The bounded catalog run reached its invocation limit; resume with the latest durable job reference.",
        trace_id,
        retryable=True,
    )
    failure["job_ref"] = current
    failure["invocation_count"] = invocation_limit
    failure["stage_history"] = history
    return failure


@dataclass(frozen=True)
class Settings:
    environment: str
    storage_mode: str
    mongodb_uri: str
    mongodb_database: str
    collection_prefix: str
    bearer_token: str
    allow_insecure_local: bool
    component_root: str
    max_stage_invocations: int
    max_total_seconds: int
    stage_timeout_seconds: int
    approval_ttl_seconds: int
    embedding_endpoint: str
    embedding_approved_hosts: str
    embedding_api_key: str
    embedding_model: str
    embedding_version: str
    embedding_dimension: int
    embedding_allow_insecure_http: bool
    approval_attestation_secret: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        default_root = Path(__file__).resolve().parents[2] / "components" / "catalog_ingestion"
        settings = cls(
            environment=os.getenv("APP_ENV", "development").strip().lower(),
            storage_mode=os.getenv("CATALOG_WORKER_STORAGE_MODE", "mongodb").strip().lower(),
            mongodb_uri=os.getenv("MONGODB_URI", "").strip(),
            mongodb_database=os.getenv("MONGODB_DATABASE", "business_work_design").strip(),
            collection_prefix=os.getenv("MONGODB_COLLECTION_PREFIX", "").strip(),
            bearer_token=os.getenv("CATALOG_WORKER_API_BEARER_TOKEN", "").strip(),
            allow_insecure_local=_truthy(os.getenv("ALLOW_INSECURE_LOCAL")),
            component_root=os.getenv("CATALOG_COMPONENT_ROOT", str(default_root)).strip(),
            max_stage_invocations=_bounded_int(os.getenv("CATALOG_MAX_STAGE_INVOCATIONS"), 400, 1, 1000),
            max_total_seconds=_bounded_int(os.getenv("CATALOG_MAX_TOTAL_SECONDS"), 1800, 30, 7200),
            stage_timeout_seconds=_bounded_int(os.getenv("CATALOG_STAGE_TIMEOUT_SECONDS"), 120, 5, 900),
            approval_ttl_seconds=_bounded_int(os.getenv("CATALOG_APPROVAL_TTL_SECONDS"), 900, 60, 3600),
            embedding_endpoint=os.getenv("EMBEDDING_ENDPOINT", "").strip(),
            embedding_approved_hosts=os.getenv("EMBEDDING_APPROVED_HOSTS", "").strip(),
            embedding_api_key=os.getenv("EMBEDDING_API_KEY", "").strip(),
            embedding_model=os.getenv("EMBEDDING_MODEL", "").strip(),
            embedding_version=os.getenv("EMBEDDING_VERSION", "").strip(),
            embedding_dimension=_bounded_int(os.getenv("EMBEDDING_DIMENSION"), 1024, 1, 65536),
            embedding_allow_insecure_http=_truthy(os.getenv("EMBEDDING_ALLOW_INSECURE_HTTP")),
            approval_attestation_secret=os.getenv("CATALOG_APPROVAL_ATTESTATION_SECRET", "").strip(),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.storage_mode not in {"mongodb", "memory"}:
            raise RuntimeError("CATALOG_WORKER_STORAGE_MODE must be mongodb or memory")
        production = self.environment in {"production", "prod"}
        if production and self.storage_mode != "mongodb":
            raise RuntimeError("production Catalog Worker requires MongoDB storage")
        if self.storage_mode == "mongodb" and (not self.mongodb_uri or not self.mongodb_database):
            raise RuntimeError("MongoDB configuration is required")
        if production and (not self.bearer_token or self.allow_insecure_local):
            raise RuntimeError("production Catalog Worker requires bearer authentication")
        if not self.bearer_token and not self.allow_insecure_local:
            raise RuntimeError("set CATALOG_WORKER_API_BEARER_TOKEN or explicitly enable local insecure mode")
        if len(self.approval_attestation_secret.encode("utf-8")) < 32:
            raise RuntimeError("CATALOG_APPROVAL_ATTESTATION_SECRET must contain at least 32 UTF-8 bytes")
        if self.collection_prefix:
            raise RuntimeError("MONGODB_COLLECTION_PREFIX must be empty because catalog stages use fixed core collection names")
        root = Path(self.component_root).resolve()
        if not root.is_dir():
            raise RuntimeError("CATALOG_COMPONENT_ROOT must be an existing directory")
        for filename, _, _, _ in COMPONENT_SPECS.values():
            candidate = (root / filename).resolve()
            if candidate.parent != root or not candidate.is_file():
                raise RuntimeError(f"required standalone component is missing: {filename}")
        if not all((self.embedding_endpoint, self.embedding_approved_hosts, self.embedding_api_key, self.embedding_model, self.embedding_version)):
            if production:
                raise RuntimeError("production Catalog Worker requires the complete embedding provider configuration")


class PipelineRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_ref: dict[str, Any]
    max_stage_invocations: int | None = Field(default=None, ge=1, le=1000)


class ApprovalCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    validation_report: dict[str, Any]
    approval_trigger: str = Field(min_length=1, max_length=128)


class ActivateRequest(ApprovalCreateRequest):
    expected_previous_snapshot_id: str | None = Field(default=None, max_length=128)
    approval_attestation: str = Field(min_length=80, max_length=4096)


class ApprovalRepository(Protocol):
    def snapshot_for_approval(self, tenant_id: str, snapshot_id: str, job_id: str) -> dict[str, Any] | None: ...

    def issue_approval(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        snapshot_id: str,
        job_id: str,
        validation_hash: str,
        idempotency_key: str,
        request_hash: str,
        ttl_seconds: int,
        attestation_jti: str,
    ) -> tuple[dict[str, Any], str | None, bool]: ...

    def active_pointer(self, tenant_id: str) -> dict[str, Any] | None: ...

    def reconcile_active_projection(
        self,
        tenant_id: str,
        snapshot_id: str,
        job_id: str,
        validation_hash: str,
        approval_id: str,
    ) -> dict[str, Any]: ...

    def acquire_pipeline_lease(self, tenant_id: str, job_id: str, owner_id: str, ttl_seconds: int) -> bool: ...

    def heartbeat_pipeline_lease(self, tenant_id: str, job_id: str, owner_id: str, ttl_seconds: int) -> bool: ...

    def release_pipeline_lease(self, tenant_id: str, job_id: str, owner_id: str) -> None: ...

    def health(self) -> dict[str, Any]: ...

    def close(self) -> None: ...


class MemoryApprovalRepository:
    """Explicit test adapter. Seed validated snapshots before issuing approvals."""

    def __init__(self) -> None:
        self._snapshots: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._approvals: dict[tuple[str, str], dict[str, Any]] = {}
        self._idempotency: dict[tuple[str, str], tuple[str, str]] = {}
        self._attestation_jtis: dict[tuple[str, str], str] = {}
        self._pointers: dict[str, dict[str, Any]] = {}
        self._leases: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = RLock()

    def seed_snapshot(self, document: dict[str, Any]) -> None:
        key = (str(document["tenant_id"]), str(document["snapshot_id"]), str(document["job_id"]))
        with self._lock:
            self._snapshots[key] = dict(document)

    def snapshot_for_approval(self, tenant_id: str, snapshot_id: str, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._snapshots.get((tenant_id, snapshot_id, job_id))
            return dict(value) if value else None

    def issue_approval(self, **kwargs: Any) -> tuple[dict[str, Any], str | None, bool]:
        tenant_id = kwargs["tenant_id"]
        idem_key = (tenant_id, kwargs["idempotency_key"])
        with self._lock:
            previous = self._idempotency.get(idem_key)
            if previous:
                previous_hash, approval_id = previous
                if previous_hash != kwargs["request_hash"]:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return dict(self._approvals[(tenant_id, approval_id)]), None, False
            jti_key = (tenant_id, str(kwargs["attestation_jti"]))
            if jti_key in self._attestation_jtis:
                raise ValueError("ATTESTATION_REPLAY")
            raw_nonce = secrets.token_urlsafe(32)
            now = _now()
            approval_id = f"cap-{uuid.uuid4()}"
            document = _approval_document(raw_nonce=raw_nonce, approval_id=approval_id, now=now, **kwargs)
            actor_hash = document["approver_id_hash"]
            for existing in self._approvals.values():
                if (
                    existing.get("tenant_id") == tenant_id
                    and existing.get("snapshot_id") == kwargs["snapshot_id"]
                    and existing.get("approver_id_hash") == actor_hash
                    and existing.get("status") == "APPROVED"
                ):
                    existing["status"] = "REVOKED"
                    existing["revoked_at"] = now
            self._approvals[(tenant_id, approval_id)] = document
            self._idempotency[idem_key] = (kwargs["request_hash"], approval_id)
            self._attestation_jtis[jti_key] = approval_id
            return dict(document), raw_nonce, True

    def active_pointer(self, tenant_id: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._pointers.get(tenant_id)
            return dict(value) if value else None

    def seed_active_pointer(self, document: dict[str, Any]) -> None:
        with self._lock:
            self._pointers[str(document["tenant_id"])] = dict(document)

    def reconcile_active_projection(
        self,
        tenant_id: str,
        snapshot_id: str,
        job_id: str,
        validation_hash: str,
        approval_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            pointer = self._pointers.get(tenant_id)
            if (
                not pointer
                or str(pointer.get("active_snapshot_id") or "") != snapshot_id
                or str(pointer.get("validation_hash") or "") != validation_hash
            ):
                raise ValueError("ACTIVE_POINTER_RECONCILIATION_MISMATCH")
            snapshot = self._snapshots.get((tenant_id, snapshot_id, job_id))
            if snapshot is not None:
                snapshot["status"] = "ACTIVE"
                snapshot["updated_at"] = _now()
            approval = self._approvals.get((tenant_id, approval_id))
            if approval is not None:
                approval["status"] = "CONSUMED"
                approval["consumed_at"] = _now()
            return dict(pointer)

    def acquire_pipeline_lease(self, tenant_id: str, job_id: str, owner_id: str, ttl_seconds: int) -> bool:
        now = _now()
        key = (tenant_id, job_id)
        with self._lock:
            existing = self._leases.get(key)
            if existing and existing["expires_at"] > now and existing["owner_id"] != owner_id:
                return False
            self._leases[key] = {"owner_id": owner_id, "expires_at": now + timedelta(seconds=ttl_seconds)}
            return True

    def heartbeat_pipeline_lease(self, tenant_id: str, job_id: str, owner_id: str, ttl_seconds: int) -> bool:
        key = (tenant_id, job_id)
        with self._lock:
            existing = self._leases.get(key)
            if not existing or existing["owner_id"] != owner_id:
                return False
            existing["expires_at"] = _now() + timedelta(seconds=ttl_seconds)
            return True

    def release_pipeline_lease(self, tenant_id: str, job_id: str, owner_id: str) -> None:
        key = (tenant_id, job_id)
        with self._lock:
            existing = self._leases.get(key)
            if existing and existing["owner_id"] == owner_id:
                self._leases.pop(key, None)

    def health(self) -> dict[str, Any]:
        return {"ready": True, "storage": "memory", "approvals": len(self._approvals)}

    def close(self) -> None:
        return None


def _approval_document(
    *,
    raw_nonce: str,
    approval_id: str,
    now: datetime,
    tenant_id: str,
    actor_id: str,
    snapshot_id: str,
    job_id: str,
    validation_hash: str,
    idempotency_key: str,
    request_hash: str,
    ttl_seconds: int,
    attestation_jti: str,
) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "approval_id": approval_id,
        "snapshot_id": snapshot_id,
        "job_id": job_id,
        "validation_hash": validation_hash,
        "approver_id_hash": hashlib.sha256(actor_id.encode("utf-8")).hexdigest(),
        "nonce_sha256": hashlib.sha256(raw_nonce.encode("utf-8")).hexdigest(),
        "idempotency_key_hash": hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest(),
        "request_hash": request_hash,
        "attestation_jti_hash": hashlib.sha256(attestation_jti.encode("utf-8")).hexdigest(),
        "status": "APPROVED",
        "created_at": now,
        "expires_at": now + timedelta(seconds=ttl_seconds),
    }


class MongoApprovalRepository:
    def __init__(self, settings: Settings) -> None:
        from pymongo import ASCENDING, MongoClient

        self._client = MongoClient(
            settings.mongodb_uri,
            connectTimeoutMS=5000,
            serverSelectionTimeoutMS=5000,
            retryReads=True,
            retryWrites=True,
        )
        self._client.admin.command("ping")
        database = self._client[settings.mongodb_database]
        # These are shared with standalone stages 07 and 08, whose durable
        # collection contract intentionally uses these exact core names.
        self._snapshots = database["catalog_snapshots"]
        self._approvals = database["catalog_activation_approvals"]
        self._pointers = database["catalog_active_pointers"]
        self._leases = database["catalog_worker_leases"]
        self._approvals.create_index([("tenant_id", ASCENDING), ("approval_id", ASCENDING)], unique=True)
        self._approvals.create_index("expires_at", expireAfterSeconds=0)
        self._approvals.create_index([("tenant_id", ASCENDING), ("idempotency_key_hash", ASCENDING)], unique=True)
        self._approvals.create_index([("tenant_id", ASCENDING), ("attestation_jti_hash", ASCENDING)], unique=True)
        self._leases.create_index("expires_at", expireAfterSeconds=0)

    def snapshot_for_approval(self, tenant_id: str, snapshot_id: str, job_id: str) -> dict[str, Any] | None:
        return self._snapshots.find_one({"tenant_id": tenant_id, "snapshot_id": snapshot_id, "job_id": job_id})

    def issue_approval(self, **kwargs: Any) -> tuple[dict[str, Any], str | None, bool]:
        from pymongo.errors import DuplicateKeyError

        tenant_id = kwargs["tenant_id"]
        idem_hash = hashlib.sha256(kwargs["idempotency_key"].encode("utf-8")).hexdigest()
        jti_hash = hashlib.sha256(kwargs["attestation_jti"].encode("utf-8")).hexdigest()
        existing = self._approvals.find_one({"tenant_id": tenant_id, "idempotency_key_hash": idem_hash})
        if existing:
            if existing.get("request_hash") != kwargs["request_hash"]:
                raise ValueError("IDEMPOTENCY_CONFLICT")
            return existing, None, False
        raw_nonce = secrets.token_urlsafe(32)
        document = _approval_document(raw_nonce=raw_nonce, approval_id=f"cap-{uuid.uuid4()}", now=_now(), **kwargs)
        try:
            self._approvals.insert_one(document)
            self._approvals.update_many(
                {
                    "tenant_id": tenant_id,
                    "snapshot_id": kwargs["snapshot_id"],
                    "approver_id_hash": document["approver_id_hash"],
                    "approval_id": {"$ne": document["approval_id"]},
                    "status": "APPROVED",
                },
                {"$set": {"status": "REVOKED", "revoked_at": _now()}},
            )
            return document, raw_nonce, True
        except DuplicateKeyError:
            existing = self._approvals.find_one({"tenant_id": tenant_id, "idempotency_key_hash": idem_hash})
            if existing:
                if existing.get("request_hash") != kwargs["request_hash"]:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return existing, None, False
            if self._approvals.find_one({"tenant_id": tenant_id, "attestation_jti_hash": jti_hash}):
                raise ValueError("ATTESTATION_REPLAY")
            raise ValueError("IDEMPOTENCY_CONFLICT")

    def active_pointer(self, tenant_id: str) -> dict[str, Any] | None:
        return self._pointers.find_one({"_id": tenant_id})

    def reconcile_active_projection(
        self,
        tenant_id: str,
        snapshot_id: str,
        job_id: str,
        validation_hash: str,
        approval_id: str,
    ) -> dict[str, Any]:
        pointer = self._pointers.find_one({"_id": tenant_id})
        if (
            not pointer
            or str(pointer.get("active_snapshot_id") or "") != snapshot_id
            or str(pointer.get("validation_hash") or "") != validation_hash
        ):
            raise ValueError("ACTIVE_POINTER_RECONCILIATION_MISMATCH")
        now = _now()
        database = self._pointers.database
        database["catalog_snapshots"].update_one(
            {"tenant_id": tenant_id, "snapshot_id": snapshot_id},
            {"$set": {"status": "ACTIVE", "activated_at": pointer.get("activated_at") or now, "updated_at": now}},
        )
        database["catalog_assets"].update_many(
            {"tenant_id": tenant_id, "snapshot_id": snapshot_id},
            {"$set": {"snapshot_status": "active"}},
        )
        database["catalog_asset_chunks"].update_many(
            {"tenant_id": tenant_id, "snapshot_id": snapshot_id},
            {"$set": {"snapshot_status": "active"}},
        )
        previous = str(pointer.get("rollback_snapshot_id") or "")
        if previous and previous != snapshot_id:
            database["catalog_snapshots"].update_one(
                {"tenant_id": tenant_id, "snapshot_id": previous},
                {"$set": {"status": "INACTIVE_ROLLBACK", "updated_at": now}},
            )
            database["catalog_assets"].update_many(
                {"tenant_id": tenant_id, "snapshot_id": previous},
                {"$set": {"snapshot_status": "inactive"}},
            )
            database["catalog_asset_chunks"].update_many(
                {"tenant_id": tenant_id, "snapshot_id": previous},
                {"$set": {"snapshot_status": "inactive"}},
            )
        database["catalog_ingest_jobs"].update_one(
            {"_id": job_id, "tenant_id": tenant_id},
            {"$set": {"stage": "SNAPSHOT_ACTIVE", "completed": True, "updated_at": now}},
        )
        self._approvals.update_one(
            {"tenant_id": tenant_id, "approval_id": approval_id},
            {"$set": {"status": "CONSUMED", "consumed_at": now}},
        )
        return self._pointers.find_one({"_id": tenant_id}) or pointer

    def acquire_pipeline_lease(self, tenant_id: str, job_id: str, owner_id: str, ttl_seconds: int) -> bool:
        from pymongo import ReturnDocument
        from pymongo.errors import DuplicateKeyError

        now = _now()
        lease_id = f"{tenant_id}:{job_id}"
        try:
            document = self._leases.find_one_and_update(
                {
                    "_id": lease_id,
                    "tenant_id": tenant_id,
                    "job_id": job_id,
                    "$or": [{"expires_at": {"$lte": now}}, {"owner_id": owner_id}],
                },
                {
                    "$set": {
                        "tenant_id": tenant_id,
                        "job_id": job_id,
                        "owner_id": owner_id,
                        "heartbeat_at": now,
                        "expires_at": now + timedelta(seconds=ttl_seconds),
                    }
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            return False
        return bool(document and document.get("owner_id") == owner_id)

    def heartbeat_pipeline_lease(self, tenant_id: str, job_id: str, owner_id: str, ttl_seconds: int) -> bool:
        now = _now()
        result = self._leases.update_one(
            {"_id": f"{tenant_id}:{job_id}", "tenant_id": tenant_id, "job_id": job_id, "owner_id": owner_id},
            {"$set": {"heartbeat_at": now, "expires_at": now + timedelta(seconds=ttl_seconds)}},
        )
        return result.matched_count == 1

    def release_pipeline_lease(self, tenant_id: str, job_id: str, owner_id: str) -> None:
        self._leases.delete_one(
            {"_id": f"{tenant_id}:{job_id}", "tenant_id": tenant_id, "job_id": job_id, "owner_id": owner_id}
        )

    def health(self) -> dict[str, Any]:
        self._client.admin.command("ping")
        return {"ready": True, "storage": "mongodb"}

    def close(self) -> None:
        self._client.close()


def _component_payload_settings(settings: Settings) -> dict[str, Any]:
    return {
        "mongodb_uri": settings.mongodb_uri,
        "mongodb_database": settings.mongodb_database,
        "embedding_endpoint": settings.embedding_endpoint,
        "approved_embedding_hosts": settings.embedding_approved_hosts,
        "embedding_api_key": settings.embedding_api_key,
        "embedding_model": settings.embedding_model,
        "embedding_version": settings.embedding_version,
        "embedding_dimension": settings.embedding_dimension,
        "allow_insecure_http": settings.embedding_allow_insecure_http,
    }


def _load_component(component_root: str, route: str) -> tuple[type[Any], str, str]:
    filename, class_name, method_name, input_name = COMPONENT_SPECS[route]
    root = Path(component_root).resolve()
    path = (root / filename).resolve()
    if path.parent != root or not path.is_file():
        raise RuntimeError("standalone component path is invalid")
    module_name = f"catalog_worker_{route}_{hashlib.sha256(str(path).encode()).hexdigest()[:12]}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("standalone component could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    component_class = getattr(module, class_name, None)
    if not isinstance(component_class, type):
        raise RuntimeError("standalone component class is missing")
    return component_class, method_name, input_name


def _component_process_target(
    queue: Any,
    component_root: str,
    route: str,
    current_ref: dict[str, Any],
    component_settings: dict[str, Any],
) -> None:
    try:
        component_class, method_name, input_name = _load_component(component_root, route)
        component = component_class()
        setattr(component, input_name, current_ref)
        for key, value in component_settings.items():
            setattr(component, key, value)
        result = _extract_result(getattr(component, method_name)())
        queue.put({"ok": True, "result": result})
    except BaseException:
        queue.put({"ok": False})


class ProcessStageRunner:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def __call__(self, route: str, current_ref: dict[str, Any]) -> dict[str, Any]:
        return self.invoke_with_timeout(route, current_ref, float(self._settings.stage_timeout_seconds))

    def invoke_with_timeout(
        self,
        route: str,
        current_ref: dict[str, Any],
        remaining_total_seconds: float,
    ) -> dict[str, Any]:
        context = multiprocessing.get_context("spawn")
        queue = context.Queue(maxsize=1)
        process = context.Process(
            target=_component_process_target,
            args=(
                queue,
                self._settings.component_root,
                route,
                current_ref,
                _component_payload_settings(self._settings),
            ),
            daemon=True,
        )
        process.start()
        timeout = max(0.001, min(float(self._settings.stage_timeout_seconds), remaining_total_seconds))
        process.join(timeout)
        if process.is_alive():
            process.terminate()
            process.join(1)
            raise TimeoutError("catalog stage timed out")
        try:
            envelope = queue.get(timeout=1.0)
        except Empty as exc:
            raise RuntimeError("catalog stage returned no result") from exc
        finally:
            queue.close()
        if process.exitcode != 0 or not envelope.get("ok") or not isinstance(envelope.get("result"), dict):
            raise RuntimeError("catalog stage failed")
        return dict(envelope["result"])


class LeasedStageRunner:
    def __init__(
        self,
        runner: Callable[[str, dict[str, Any]], dict[str, Any]],
        repository: ApprovalRepository,
        tenant_id: str,
        job_id: str,
        owner_id: str,
        ttl_seconds: int,
    ) -> None:
        self._runner = runner
        self._repository = repository
        self._tenant_id = tenant_id
        self._job_id = job_id
        self._owner_id = owner_id
        self._ttl_seconds = ttl_seconds

    def _heartbeat(self) -> None:
        if not self._repository.heartbeat_pipeline_lease(
            self._tenant_id, self._job_id, self._owner_id, self._ttl_seconds
        ):
            raise RuntimeError("catalog pipeline lease was lost")

    def __call__(self, route: str, current_ref: dict[str, Any]) -> dict[str, Any]:
        self._heartbeat()
        result = self._runner(route, current_ref)
        self._heartbeat()
        return result

    def invoke_with_timeout(self, route: str, current_ref: dict[str, Any], remaining: float) -> dict[str, Any]:
        self._heartbeat()
        timed = getattr(self._runner, "invoke_with_timeout", None)
        result = timed(route, current_ref, remaining) if callable(timed) else self._runner(route, current_ref)
        self._heartbeat()
        return result


def _approval_request_hash(
    tenant_id: str,
    actor_id: str,
    report: dict[str, Any],
    approval_trigger: str,
    expected_previous_snapshot_id: str = "",
    attestation_jti: str = "",
) -> str:
    basis = {
        "tenant_id": tenant_id,
        "actor_id": actor_id,
        "snapshot_id": report["snapshot_id"],
        "job_id": report["job_id"],
        "validation_hash": report["validation_hash"],
        "approval_trigger": approval_trigger,
        "expected_previous_snapshot_id": expected_previous_snapshot_id,
        "attestation_jti": attestation_jti,
    }
    return hashlib.sha256(json.dumps(basis, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("approval attestation encoding is invalid")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign_activation_attestation(secret: str, claims: dict[str, Any]) -> str:
    """Gateway helper used by integration tests and trusted approval gateways.

    This function does not expose an issuing endpoint. The signing secret must
    remain outside Langflow; only the short-lived signed claim is injected into
    the approved F00 resume.
    """
    body = _b64url_encode(json.dumps(claims, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = _b64url_encode(hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{signature}"


def _verify_activation_attestation(
    token: str,
    secret: str,
    *,
    tenant_id: str,
    actor_id: str,
    report: dict[str, str],
    maximum_ttl_seconds: int,
    expected_previous_snapshot_id: str,
) -> dict[str, Any]:
    try:
        body, supplied_signature = token.split(".", 1)
        expected_signature = _b64url_encode(hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise ValueError("approval attestation signature is invalid")
        claims = json.loads(_b64url_decode(body).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("approval attestation is invalid") from exc
    if not isinstance(claims, dict):
        raise ValueError("approval attestation claims are invalid")
    expected = {
        "schema_version": "catalog-activation-attestation/v1",
        "decision": "activate_snapshot",
        "tenant_id": tenant_id,
        "actor_id": actor_id,
        "snapshot_id": report["snapshot_id"],
        "job_id": report["job_id"],
        "validation_hash": report["validation_hash"],
        "expected_previous_snapshot_id": expected_previous_snapshot_id,
    }
    if any(str(claims.get(key) or "") != value for key, value in expected.items()):
        raise ValueError("approval attestation scope does not match the activation request")
    try:
        issued_at = int(claims["iat"])
        expires_at = int(claims["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("approval attestation timestamps are invalid") from exc
    now_epoch = int(_now().timestamp())
    if issued_at > now_epoch + 60 or issued_at < now_epoch - maximum_ttl_seconds or expires_at <= now_epoch:
        raise ValueError("approval attestation is expired or outside the clock-skew window")
    if expires_at <= issued_at or expires_at - issued_at > maximum_ttl_seconds:
        raise ValueError("approval attestation lifetime is invalid")
    jti = str(claims.get("jti") or "")
    if not IDEMPOTENCY_PATTERN.fullmatch(jti):
        raise ValueError("approval attestation jti is invalid")
    return claims


def _validate_report_for_approval(report: dict[str, Any], tenant_id: str, snapshot_id: str) -> dict[str, str]:
    if not isinstance(report, dict) or report.get("ok") is not True or report.get("status") != "VALIDATED":
        raise ValueError("validation_report must be successful and VALIDATED")
    cleaned = {
        "tenant_id": _validate_identity(str(report.get("tenant_id") or ""), "validation_report.tenant_id"),
        "snapshot_id": _validate_identity(str(report.get("snapshot_id") or ""), "validation_report.snapshot_id"),
        "job_id": _validate_identity(str(report.get("job_id") or ""), "validation_report.job_id"),
        "validation_hash": str(report.get("validation_hash") or "").strip(),
    }
    if cleaned["tenant_id"] != tenant_id or cleaned["snapshot_id"] != snapshot_id:
        raise ValueError("validation_report scope does not match the authenticated request")
    if not re.fullmatch(r"[a-f0-9]{64}", cleaned["validation_hash"]):
        raise ValueError("validation_report.validation_hash is invalid")
    return cleaned


def _default_activation_runner(
    settings: Settings,
    report: dict[str, Any],
    trigger: str,
    actor_id: str,
    approval_id: str,
    raw_nonce: str,
    expected_previous_snapshot_id: str,
) -> dict[str, Any]:
    root = Path(settings.component_root).resolve()
    path = (root / "08_catalog_snapshot_activator.py").resolve()
    if path.parent != root or not path.is_file():
        raise RuntimeError("standalone activation component is missing")
    module_name = f"catalog_worker_activate_{hashlib.sha256(str(path).encode()).hexdigest()[:12]}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("standalone activation component could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    component_class = getattr(module, "CatalogSnapshotActivatorComponent", None)
    if not isinstance(component_class, type):
        raise RuntimeError("standalone activation component class is missing")
    component = component_class()
    component.validation_report = report
    component.approval_trigger = trigger
    component.approved = True
    component.approver_id = actor_id
    component.approval_id = approval_id
    component.approval_nonce = raw_nonce
    component.expected_previous_snapshot_id = expected_previous_snapshot_id
    component.mongodb_uri = settings.mongodb_uri
    component.mongodb_database = settings.mongodb_database
    result = _extract_result(component.activate_snapshot())
    if result.get("ok") is False:
        raise RuntimeError("snapshot activation was rejected")
    return result


def _public_active_pointer(value: dict[str, Any], *, idempotent_replay: bool = False) -> dict[str, Any]:
    active_snapshot_id = str(value.get("active_snapshot_id") or value.get("snapshot_id") or "")
    if not active_snapshot_id:
        raise ValueError("activation result did not contain an active snapshot")
    return {
        "ok": True,
        "status": "ACTIVE",
        "tenant_id": str(value.get("tenant_id") or value.get("_id") or ""),
        "active_snapshot_id": active_snapshot_id,
        "rollback_snapshot_id": value.get("rollback_snapshot_id"),
        "embedding_contract": value.get("embedding_contract") if isinstance(value.get("embedding_contract"), dict) else {},
        "updated_at": value.get("updated_at"),
        "idempotent_replay": idempotent_replay,
        "trace_id": str(value.get("trace_id") or ""),
    }


def create_app(
    settings: Settings | None = None,
    repository: ApprovalRepository | None = None,
    stage_runner: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    activation_runner: Callable[[dict[str, Any], str, str, str, str, str], dict[str, Any]] | None = None,
) -> FastAPI:
    resolved = settings or Settings.from_env()
    resolved.validate()
    repo = repository or (MongoApprovalRepository(resolved) if resolved.storage_mode == "mongodb" else MemoryApprovalRepository())
    runner = stage_runner or ProcessStageRunner(resolved)
    activate = activation_runner or (
        lambda report, trigger, actor, approval_id, nonce, expected_previous: _default_activation_runner(
            resolved, report, trigger, actor, approval_id, nonce, expected_previous
        )
    )
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            repo.close()

    app = FastAPI(title="Business Work Design Catalog Worker", version="1.0.0", lifespan=lifespan)
    active_pipeline_jobs: set[tuple[str, str]] = set()
    active_pipeline_lock = RLock()

    def auth_context(
        request: Request,
        authorization: str | None = Header(default=None),
        x_tenant_id: str | None = Header(default=None),
        x_actor_id: str | None = Header(default=None),
    ) -> tuple[str, str]:
        try:
            tenant_id = _validate_identity(str(x_tenant_id or ""), "X-Tenant-ID")
            actor_id = _validate_identity(str(x_actor_id or ""), "X-Actor-ID")
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        if resolved.bearer_token:
            supplied = str(authorization or "")
            expected = f"Bearer {resolved.bearer_token}"
            if not hmac.compare_digest(supplied, expected):
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token")
        elif not resolved.allow_insecure_local:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="authentication is not configured")
        elif not request.client or request.client.host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insecure local mode is loopback-only")
        return tenant_id, actor_id

    @app.get("/healthz")
    def health() -> dict[str, Any]:
        return {"ready": True, "service": "catalog-worker", "approval_repository": repo.health()}

    @app.post("/api/catalog/pipeline/run")
    def run_pipeline(payload: PipelineRunRequest, identity: tuple[str, str] = Depends(auth_context)) -> dict[str, Any]:
        tenant_id, _ = identity
        try:
            incoming = _job_ref(payload.job_ref)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        if incoming["tenant_id"] != tenant_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="job_ref tenant does not match authenticated tenant")
        requested_limit = payload.max_stage_invocations or resolved.max_stage_invocations
        limit = min(requested_limit, resolved.max_stage_invocations)
        claim = (tenant_id, incoming["job_id"])
        with active_pipeline_lock:
            if claim in active_pipeline_jobs:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="catalog job is already running on this worker")
            active_pipeline_jobs.add(claim)
        lease_owner = f"worker-{uuid.uuid4()}"
        lease_ttl = min(1860, max(60, resolved.stage_timeout_seconds * 2 + 30))
        if not repo.acquire_pipeline_lease(tenant_id, incoming["job_id"], lease_owner, lease_ttl):
            with active_pipeline_lock:
                active_pipeline_jobs.discard(claim)
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="catalog job is leased by another worker")
        try:
            leased_runner = LeasedStageRunner(
                runner, repo, tenant_id, incoming["job_id"], lease_owner, lease_ttl
            )
            return run_catalog_pipeline(
                incoming,
                leased_runner,
                max_stage_invocations=limit,
                max_total_seconds=resolved.max_total_seconds,
            )
        finally:
            repo.release_pipeline_lease(tenant_id, incoming["job_id"], lease_owner)
            with active_pipeline_lock:
                active_pipeline_jobs.discard(claim)

    @app.post("/api/catalog/snapshots/{snapshot_id}/activate")
    def approve_and_activate_snapshot(
        snapshot_id: str,
        payload: ActivateRequest,
        identity: tuple[str, str] = Depends(auth_context),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        tenant_id, actor_id = identity
        try:
            snapshot_id = _validate_identity(snapshot_id, "snapshot_id")
            if not idempotency_key or not IDEMPOTENCY_PATTERN.fullmatch(idempotency_key):
                raise ValueError("Idempotency-Key is required and must be 8-256 safe characters")
            report = _validate_report_for_approval(payload.validation_report, tenant_id, snapshot_id)
            expected_previous = str(payload.expected_previous_snapshot_id or "").strip()
            if expected_previous:
                _validate_identity(expected_previous, "expected_previous_snapshot_id")
            attestation = _verify_activation_attestation(
                payload.approval_attestation,
                resolved.approval_attestation_secret,
                tenant_id=tenant_id,
                actor_id=actor_id,
                report=report,
                maximum_ttl_seconds=resolved.approval_ttl_seconds,
                expected_previous_snapshot_id=expected_previous,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        snapshot = repo.snapshot_for_approval(tenant_id, snapshot_id, report["job_id"])
        persisted_validation = snapshot.get("validation") if isinstance(snapshot, dict) and isinstance(snapshot.get("validation"), dict) else {}
        if (
            not snapshot
            or snapshot.get("status") not in {"VALIDATED", "ACTIVE"}
            or snapshot.get("validation_hash") != report["validation_hash"]
            or persisted_validation.get("ok") is not True
            or persisted_validation.get("status") != "VALIDATED"
        ):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="persisted snapshot is not validated for activation")
        request_hash = _approval_request_hash(
            tenant_id,
            actor_id,
            report,
            payload.approval_trigger,
            expected_previous,
            str(attestation["jti"]),
        )
        try:
            approval, raw_nonce, _ = repo.issue_approval(
                tenant_id=tenant_id,
                actor_id=actor_id,
                snapshot_id=snapshot_id,
                job_id=report["job_id"],
                validation_hash=report["validation_hash"],
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                ttl_seconds=resolved.approval_ttl_seconds,
                attestation_jti=str(attestation["jti"]),
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        if raw_nonce is None:
            pointer = repo.active_pointer(tenant_id)
            if pointer and str(pointer.get("active_snapshot_id") or "") == snapshot_id:
                try:
                    reconciled = repo.reconcile_active_projection(
                        tenant_id,
                        snapshot_id,
                        report["job_id"],
                        report["validation_hash"],
                        str(approval["approval_id"]),
                    )
                except (RuntimeError, ValueError, PyMongoError) as exc:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="active pointer exists but projection reconciliation failed; retry the same idempotency key",
                    ) from exc
                return _public_active_pointer(reconciled, idempotent_replay=True)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "approval nonce was consumed or lost before pointer activation; the trusted gateway must re-verify "
                    "the decision and issue a new attestation JTI with a new idempotency key"
                ),
            )
        try:
            active_pointer = activate(
                payload.validation_report,
                payload.approval_trigger,
                actor_id,
                str(approval["approval_id"]),
                raw_nonce,
                expected_previous,
            )
            return _public_active_pointer(active_pointer)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="snapshot activation failed closed") from exc

    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8092)
