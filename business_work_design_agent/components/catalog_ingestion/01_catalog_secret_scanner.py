from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, BinaryIO

import gridfs
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from lfx.custom import Component
from lfx.io import DataInput, IntInput, Output, SecretStrInput, StrInput
from lfx.schema import Data


_JOB_REF_KEYS = ("tenant_id", "job_id", "snapshot_id", "stage", "expected_cursor", "trace_id")
_SECRET_PATTERNS = (
    (
        "PRIVATE_KEY",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----", re.IGNORECASE),
    ),
    (
        "BEARER_TOKEN",
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE),
    ),
    (
        "ASSIGNED_CREDENTIAL",
        re.compile(
            r"\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|password|passwd)\b"
            r"\s*[:=]\s*[\"']?[A-Za-z0-9._~+/=-]{8,}",
            re.IGNORECASE,
        ),
    ),
    (
        "AWS_ACCESS_KEY",
        re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    ),
)


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


def _scan_text(text: str) -> dict[str, int]:
    return {code: len(pattern.findall(text)) for code, pattern in _SECRET_PATTERNS if pattern.search(text)}


def _scan_stream(handle: BinaryIO, chunk_bytes: int, max_bytes: int) -> tuple[dict[str, int], int]:
    counts: dict[str, int] = {}
    total = 0
    tail = ""
    while True:
        chunk = handle.read(chunk_bytes)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValueError("The restricted source exceeds the configured scan limit.")
        current = chunk.decode("utf-8", errors="replace")
        window = tail + current
        window_counts = _scan_text(window)
        tail_counts = _scan_text(tail)
        for code, count in window_counts.items():
            counts[code] = counts.get(code, 0) + max(0, count - tail_counts.get(code, 0))
        tail = window[-512:]
    return counts, total


def _job_ref(source: dict[str, Any], stage: str) -> dict[str, Any]:
    return {
        "tenant_id": source["tenant_id"],
        "job_id": source["job_id"],
        "snapshot_id": source["snapshot_id"],
        "stage": stage,
        "expected_cursor": int(source.get("expected_cursor") or 0),
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


class CatalogSecretScannerComponent(Component):
    display_name = "Catalog Secret Scanner"
    description = "Scan the restricted catalog source for high-confidence credentials and quarantine matching jobs."
    icon = "ShieldAlert"
    name = "CatalogSecretScanner"

    inputs = [
        DataInput(name="job_ref", display_name="Catalog Ingest Job Reference", required=True),
        SecretStrInput(name="mongodb_uri", display_name="MongoDB URI", required=True),
        StrInput(name="mongodb_database", display_name="MongoDB Database", value="business_work_design", required=True),
        IntInput(name="max_scan_mb", display_name="Maximum Scan Size (MiB)", value=100, advanced=True),
        IntInput(name="scan_chunk_kb", display_name="Scan Chunk Size (KiB)", value=256, advanced=True),
        IntInput(name="connect_timeout_ms", display_name="MongoDB Connect Timeout (ms)", value=5000, advanced=True),
    ]

    outputs = [Output(name="scanned_job_ref", display_name="Scanned Job Reference", method="scan_source", types=["Data"])]

    def scan_source(self) -> Data:
        trace_id = "trace-unassigned"
        try:
            incoming = _validate_job_ref(getattr(self, "job_ref", None))
            trace_id = incoming["trace_id"]
            mongodb_uri = _secret_value(getattr(self, "mongodb_uri", ""))
            database_name = str(getattr(self, "mongodb_database", "") or "").strip()
            if not mongodb_uri or not database_name:
                return _failure("MONGODB_CONFIG_MISSING", "MongoDB configuration is required.", trace_id)

            timeout_ms = _bounded_int(getattr(self, "connect_timeout_ms", 5000), 5000, 1000, 30000)
            chunk_bytes = _bounded_int(getattr(self, "scan_chunk_kb", 256), 256, 64, 1024) * 1024
            max_bytes = _bounded_int(getattr(self, "max_scan_mb", 100), 100, 1, 200) * 1024 * 1024
            client = MongoClient(
                mongodb_uri,
                connectTimeoutMS=timeout_ms,
                serverSelectionTimeoutMS=timeout_ms,
                socketTimeoutMS=max(timeout_ms, 5000),
                retryReads=True,
                retryWrites=True,
            )
            try:
                client.admin.command("ping")
                database = client[database_name]
                jobs = database["catalog_ingest_jobs"]
                query = {
                    "_id": incoming["job_id"],
                    "tenant_id": incoming["tenant_id"],
                    "snapshot_id": incoming["snapshot_id"],
                }
                job = jobs.find_one(query)
                if not job:
                    return _failure("CATALOG_JOB_NOT_FOUND", "The catalog ingest job was not found for this tenant.", trace_id)

                current_stage = str(job.get("stage") or "")
                if current_stage in {"SECRET_SCAN_PASSED", "QUARANTINED_SECRET"}:
                    self.status = f"Secret scan already completed: job_id={incoming['job_id']}, stage={current_stage}"
                    return Data(data=_job_ref(incoming, current_stage))
                if current_stage != "INTAKE_STORED":
                    return _failure("CATALOG_STAGE_CONFLICT", "Secret scan requires an intake-stored job.", trace_id)
                if incoming["stage"] != "INTAKE_STORED" or incoming["expected_cursor"] != 0:
                    return _failure("CATALOG_CURSOR_CONFLICT", "The secret-scan job reference is stale.", trace_id)

                blob_id = job.get("source_blob_id")
                if blob_id is None:
                    return _failure("CATALOG_SOURCE_MISSING", "The restricted catalog source is unavailable.", trace_id)
                bucket = gridfs.GridFS(database, collection="catalog_source_files_blob")
                source = bucket.get(blob_id)
                counts, scanned_bytes = _scan_stream(source, chunk_bytes, max_bytes)
                now = _utc_now()
                quarantined = bool(counts)
                next_stage = "QUARANTINED_SECRET" if quarantined else "SECRET_SCAN_PASSED"
                scan_summary = {
                    "completed": True,
                    "quarantined": quarantined,
                    "match_counts": counts,
                    "scanned_bytes": scanned_bytes,
                    "scanner_version": "catalog-secret-v1",
                    "scanned_at": now,
                }
                update = jobs.update_one(
                    {**query, "stage": "INTAKE_STORED"},
                    {"$set": {"stage": next_stage, "secret_scan": scan_summary, "updated_at": now}},
                )
                if update.modified_count != 1:
                    return _failure("CATALOG_STAGE_CONFLICT", "The ingest job changed while secret scan was running.", trace_id, True)
                database["catalog_source_files"].update_one(
                    {"_id": str(job.get("source_file_id") or incoming["job_id"]), "tenant_id": incoming["tenant_id"]},
                    {"$set": {"status": next_stage, "secret_scan": scan_summary, "updated_at": now}},
                )
                self.status = (
                    f"Catalog source quarantined: job_id={incoming['job_id']}, finding_types={len(counts)}"
                    if quarantined
                    else f"Secret scan passed: job_id={incoming['job_id']}, bytes={scanned_bytes}"
                )
                return Data(data=_job_ref(incoming, next_stage))
            finally:
                client.close()
        except ValueError as exc:
            self.status = "Secret scan rejected by input validation."
            return _failure("SECRET_SCAN_INPUT_INVALID", str(exc), trace_id)
        except (PyMongoError, gridfs.errors.GridFSError, OSError, UnicodeError):
            self.status = "Secret scan could not complete."
            return _failure(
                "SECRET_SCAN_FAILED",
                "The restricted source could not be scanned. The job remains blocked.",
                trace_id,
                retryable=True,
            )
