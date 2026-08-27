from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import gridfs
from pymongo import ASCENDING, MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError

from lfx.custom import Component
from lfx.io import BoolInput, FileInput, IntInput, Output, SecretStrInput, StrInput
from lfx.schema import Data


_JOB_REF_KEYS = ("tenant_id", "job_id", "snapshot_id", "stage", "expected_cursor", "trace_id")
_TENANT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_ALLOWED_SUFFIXES = {".json", ".jsonl"}


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


def _normalize_tenant(value: Any) -> str:
    tenant_id = str(value or "").strip().lower()
    if not _TENANT_PATTERN.fullmatch(tenant_id):
        raise ValueError("tenant_id must contain 1-64 lowercase letters, numbers, dots, underscores, or hyphens.")
    return tenant_id


def _uploaded_file_value(value: Any) -> str:
    candidate = getattr(value, "path", None) or getattr(value, "file_path", None) or value
    if not isinstance(candidate, str) or not candidate.strip():
        raise ValueError("A Langflow FileInput upload is required.")
    return candidate.strip()


def _resolve_allowed_upload(upload_path: Any, allowed_upload_root: Any) -> Path:
    root_text = str(allowed_upload_root or "").strip()
    if not root_text:
        raise ValueError("allowed_upload_root is required and must point to the Langflow upload directory.")
    try:
        root = Path(root_text).resolve(strict=True)
        candidate = Path(str(upload_path)).resolve(strict=True)
    except OSError as exc:
        raise ValueError("The upload root or catalog file is unavailable.") from exc
    if not root.is_dir():
        raise ValueError("allowed_upload_root must be an existing directory.")
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("The catalog file is outside the approved Langflow upload root.") from exc
    if not candidate.is_file():
        raise ValueError("The uploaded catalog file is unavailable.")
    return candidate


def _hash_file(path: Path, max_bytes: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("The uploaded catalog exceeds the configured size limit.")
            digest.update(chunk)
    if size == 0:
        raise ValueError("The uploaded catalog is empty.")
    return digest.hexdigest(), size


def _stable_ids(tenant_id: str, source_sha256: str, idempotency_key: str) -> tuple[str, str, str, str]:
    key_text = idempotency_key.strip() or source_sha256
    key_hash = hashlib.sha256(key_text.encode("utf-8")).hexdigest()
    seed = f"catalog-ingest:{tenant_id}:{key_hash}:{source_sha256}"
    job_uuid = uuid.uuid5(uuid.NAMESPACE_URL, seed)
    snapshot_hash = hashlib.sha256(f"snapshot:{seed}".encode("utf-8")).hexdigest()
    job_id = f"job-{job_uuid}"
    snapshot_id = f"snap-{snapshot_hash[:24]}"
    trace_id = f"trace-{hashlib.sha256(f'trace:{seed}'.encode('utf-8')).hexdigest()[:24]}"
    return job_id, snapshot_id, trace_id, key_hash


def _job_ref(
    tenant_id: str,
    job_id: str,
    snapshot_id: str,
    stage: str,
    expected_cursor: int,
    trace_id: str,
) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "job_id": job_id,
        "snapshot_id": snapshot_id,
        "stage": stage,
        "expected_cursor": max(0, int(expected_cursor)),
        "trace_id": trace_id,
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


def _storage_documents(
    *,
    tenant_id: str,
    job_id: str,
    snapshot_id: str,
    trace_id: str,
    blob_id: Any,
    source_sha256: str,
    source_size: int,
    source_format: str,
    idempotency_hash: str,
    uploader_id: str,
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_document = {
        "_id": job_id,
        "tenant_id": tenant_id,
        "job_id": job_id,
        "blob_id": blob_id,
        "source_sha256": source_sha256,
        "size_bytes": source_size,
        "encoding": "utf-8",
        "format_hint": source_format,
        "status": "RESTRICTED_STORED_PENDING_SCAN",
        "uploader_id_hash": hashlib.sha256(uploader_id.encode("utf-8")).hexdigest(),
        "created_at": now,
    }
    job_document = {
        "_id": job_id,
        "tenant_id": tenant_id,
        "job_id": job_id,
        "snapshot_id": snapshot_id,
        "trace_id": trace_id,
        "source_file_id": job_id,
        "source_blob_id": blob_id,
        "source_sha256": source_sha256,
        "source_size_bytes": source_size,
        "source_format_hint": source_format,
        "idempotency_hash": idempotency_hash,
        "stage": "INTAKE_STORED",
        "expected_cursor": 0,
        "stage_cursors": {"parse": 0, "normalize": 0, "text": 0, "embedding": 0, "write": 0},
        "completed": False,
        "created_at": now,
        "updated_at": now,
    }
    return source_document, job_document


def _recover_duplicate_intake(
    *,
    bucket: Any,
    sources: Any,
    jobs: Any,
    uploaded_blob_id: Any,
    source_inserted_by_this_run: bool,
    tenant_id: str,
    job_id: str,
    snapshot_id: str,
    trace_id: str,
    source_sha256: str,
    source_size: int,
    source_format: str,
    idempotency_hash: str,
    uploader_id: str,
    now: datetime,
) -> Any:
    """Reconcile a concurrent duplicate without deleting the canonical blob."""
    if not source_inserted_by_this_run and bucket.exists(uploaded_blob_id):
        bucket.delete(uploaded_blob_id)
    winning_source = sources.find_one(
        {"_id": job_id, "tenant_id": tenant_id, "source_sha256": source_sha256}
    )
    winning_blob_id = winning_source.get("blob_id") if winning_source else None
    if winning_blob_id is None or not bucket.exists(winning_blob_id):
        raise DuplicateKeyError("Canonical concurrent catalog source is missing.")
    existing = jobs.find_one({"_id": job_id, "tenant_id": tenant_id})
    if existing:
        if str(existing.get("source_blob_id")) != str(winning_blob_id):
            raise ValueError("Concurrent intake job points to a non-canonical source blob.")
        return winning_blob_id
    _, recovered_job = _storage_documents(
        tenant_id=tenant_id,
        job_id=job_id,
        snapshot_id=snapshot_id,
        trace_id=trace_id,
        blob_id=winning_blob_id,
        source_sha256=source_sha256,
        source_size=source_size,
        source_format=source_format,
        idempotency_hash=idempotency_hash,
        uploader_id=uploader_id,
        now=now,
    )
    jobs.update_one({"_id": job_id}, {"$setOnInsert": recovered_job}, upsert=True)
    return winning_blob_id


class CatalogFileIntakeComponent(Component):
    display_name = "Catalog File Intake"
    description = "Validate an uploaded JSON/JSONL catalog, preserve it in restricted GridFS, and create an idempotent job."
    icon = "FileUp"
    name = "CatalogFileIntake"

    inputs = [
        FileInput(
            name="catalog_file",
            display_name="Catalog File",
            file_types=["json", "jsonl"],
            required=True,
            info="A JSON array, {items:[...]}, or JSONL file uploaded through Langflow.",
        ),
        StrInput(name="tenant_id", display_name="Tenant ID", required=True),
        StrInput(name="uploader_id", display_name="Uploader ID", required=True),
        StrInput(
            name="allowed_upload_root",
            display_name="Approved Langflow Upload Root",
            required=True,
            info="Resolved catalog paths, including symlinks, must stay under this dedicated upload directory.",
        ),
        StrInput(
            name="idempotency_key",
            display_name="Idempotency Key",
            required=False,
            info="Optional caller key. If omitted, the file SHA-256 is used.",
        ),
        SecretStrInput(name="mongodb_uri", display_name="MongoDB URI", required=False),
        StrInput(name="mongodb_database", display_name="MongoDB Database", value="business_work_design", required=True),
        BoolInput(
            name="restricted_encrypted_store_confirmed",
            display_name="Restricted Encrypted Store Confirmed",
            value=False,
            info="Confirm that this MongoDB/GridFS deployment has the required access controls and encryption at rest.",
        ),
        IntInput(name="max_file_size_mb", display_name="Maximum File Size (MiB)", value=100, advanced=True),
        IntInput(name="connect_timeout_ms", display_name="MongoDB Connect Timeout (ms)", value=5000, advanced=True),
        BoolInput(
            name="dry_run",
            display_name="Dry Run",
            value=False,
            advanced=True,
            info="Explicitly validate and hash without creating MongoDB records.",
        ),
    ]

    outputs = [Output(name="job_ref", display_name="Catalog Ingest Job Reference", method="create_job", types=["Data"])]

    def create_job(self) -> Data:
        trace_id = "trace-unassigned"
        try:
            tenant_id = _normalize_tenant(getattr(self, "tenant_id", ""))
            uploader_id = str(getattr(self, "uploader_id", "") or "").strip()
            if not uploader_id or len(uploader_id) > 128:
                raise ValueError("uploader_id is required and must be at most 128 characters.")

            upload_value = _uploaded_file_value(getattr(self, "catalog_file", None))
            resolved_path = _resolve_allowed_upload(
                self.resolve_path(upload_value),
                getattr(self, "allowed_upload_root", ""),
            )
            if resolved_path.suffix.lower() not in _ALLOWED_SUFFIXES:
                raise ValueError("Only .json and .jsonl catalog uploads are supported.")

            max_mb = _bounded_int(getattr(self, "max_file_size_mb", 100), 100, 1, 200)
            source_sha256, source_size = _hash_file(resolved_path, max_mb * 1024 * 1024)
            job_id, snapshot_id, trace_id, idempotency_hash = _stable_ids(
                tenant_id,
                source_sha256,
                str(getattr(self, "idempotency_key", "") or ""),
            )

            if bool(getattr(self, "dry_run", False)):
                result = _job_ref(tenant_id, job_id, snapshot_id, "DRY_RUN_INTAKE_VALIDATED", 0, trace_id)
                self.status = f"Dry-run intake validated: job_id={job_id}, bytes={source_size}"
                return Data(data=result)

            if not bool(getattr(self, "restricted_encrypted_store_confirmed", False)):
                return _failure(
                    "RESTRICTED_STORE_NOT_CONFIRMED",
                    "Restricted access and encryption-at-rest must be confirmed before preserving the source file.",
                    trace_id,
                )

            mongodb_uri = _secret_value(getattr(self, "mongodb_uri", ""))
            database_name = str(getattr(self, "mongodb_database", "") or "").strip()
            if not mongodb_uri or not database_name:
                return _failure("MONGODB_CONFIG_MISSING", "MongoDB configuration is required.", trace_id)

            timeout_ms = _bounded_int(getattr(self, "connect_timeout_ms", 5000), 5000, 1000, 30000)
            client = MongoClient(
                mongodb_uri,
                connectTimeoutMS=timeout_ms,
                serverSelectionTimeoutMS=timeout_ms,
                socketTimeoutMS=max(timeout_ms, 5000),
                retryWrites=True,
            )
            try:
                client.admin.command("ping")
                database = client[database_name]
                jobs = database["catalog_ingest_jobs"]
                sources = database["catalog_source_files"]
                jobs.create_index(
                    [("tenant_id", ASCENDING), ("idempotency_hash", ASCENDING), ("source_sha256", ASCENDING)],
                    unique=True,
                    name="uq_catalog_job_idempotency",
                )
                sources.create_index(
                    [("tenant_id", ASCENDING), ("source_sha256", ASCENDING), ("job_id", ASCENDING)],
                    unique=True,
                    name="uq_catalog_source_job",
                )

                existing = jobs.find_one({"_id": job_id, "tenant_id": tenant_id})
                if existing:
                    result = _job_ref(
                        tenant_id,
                        job_id,
                        str(existing.get("snapshot_id") or snapshot_id),
                        str(existing.get("stage") or "INTAKE_STORED"),
                        int(existing.get("expected_cursor") or 0),
                        str(existing.get("trace_id") or trace_id),
                    )
                    self.status = f"Existing ingest job returned: job_id={job_id}"
                    return Data(data=result)

                bucket = gridfs.GridFS(database, collection="catalog_source_files_blob")
                now = _utc_now()
                source_format = resolved_path.suffix.lower().lstrip(".")

                # Reconcile failures that happened after GridFS/source storage
                # but before the idempotent job document was inserted.
                recoverable_source = sources.find_one(
                    {"_id": job_id, "tenant_id": tenant_id, "source_sha256": source_sha256}
                )
                recoverable_blob_id = recoverable_source.get("blob_id") if recoverable_source else None
                if recoverable_blob_id is None:
                    orphan_blob = database["catalog_source_files_blob.files"].find_one(
                        {
                            "metadata.tenant_id": tenant_id,
                            "metadata.job_id": job_id,
                            "metadata.sha256": source_sha256,
                        },
                        sort=[("uploadDate", ASCENDING)],
                    )
                    recoverable_blob_id = orphan_blob.get("_id") if orphan_blob else None
                if recoverable_blob_id is not None and bucket.exists(recoverable_blob_id):
                    source_document, job_document = _storage_documents(
                        tenant_id=tenant_id,
                        job_id=job_id,
                        snapshot_id=snapshot_id,
                        trace_id=trace_id,
                        blob_id=recoverable_blob_id,
                        source_sha256=source_sha256,
                        source_size=source_size,
                        source_format=source_format,
                        idempotency_hash=idempotency_hash,
                        uploader_id=uploader_id,
                        now=now,
                    )
                    sources.update_one({"_id": job_id}, {"$setOnInsert": source_document}, upsert=True)
                    jobs.update_one({"_id": job_id}, {"$setOnInsert": job_document}, upsert=True)
                    self.status = f"Recovered catalog intake job: job_id={job_id}, bytes={source_size}"
                    return Data(data=_job_ref(tenant_id, job_id, snapshot_id, "INTAKE_STORED", 0, trace_id))

                with resolved_path.open("rb") as source_handle:
                    blob_id = bucket.put(
                        source_handle,
                        filename=f"catalog-{job_id}{resolved_path.suffix.lower()}",
                        metadata={
                            "tenant_id": tenant_id,
                            "job_id": job_id,
                            "sha256": source_sha256,
                            "restricted": True,
                        },
                    )

                source_document, job_document = _storage_documents(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    snapshot_id=snapshot_id,
                    trace_id=trace_id,
                    blob_id=blob_id,
                    source_sha256=source_sha256,
                    source_size=source_size,
                    source_format=source_format,
                    idempotency_hash=idempotency_hash,
                    uploader_id=uploader_id,
                    now=now,
                )
                source_inserted_by_this_run = False
                try:
                    sources.insert_one(source_document)
                    source_inserted_by_this_run = True
                    jobs.insert_one(job_document)
                except DuplicateKeyError:
                    _recover_duplicate_intake(
                        bucket=bucket,
                        sources=sources,
                        jobs=jobs,
                        uploaded_blob_id=blob_id,
                        source_inserted_by_this_run=source_inserted_by_this_run,
                        tenant_id=tenant_id,
                        job_id=job_id,
                        snapshot_id=snapshot_id,
                        trace_id=trace_id,
                        source_sha256=source_sha256,
                        source_size=source_size,
                        source_format=source_format,
                        idempotency_hash=idempotency_hash,
                        uploader_id=uploader_id,
                        now=now,
                    )

                result = _job_ref(tenant_id, job_id, snapshot_id, "INTAKE_STORED", 0, trace_id)
                self.status = f"Catalog intake stored: job_id={job_id}, bytes={source_size}"
                return Data(data=result)
            finally:
                client.close()
        except ValueError as exc:
            self.status = "Catalog intake rejected by input validation."
            return _failure("CATALOG_INTAKE_INVALID", str(exc), trace_id)
        except (OSError, PyMongoError, gridfs.errors.GridFSError):
            self.status = "Catalog intake failed during restricted storage."
            return _failure(
                "CATALOG_INTAKE_STORAGE_FAILED",
                "The catalog source could not be stored. Verify the approved MongoDB/GridFS service.",
                trace_id,
                retryable=True,
            )
