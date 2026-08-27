from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, MongoClient, ReplaceOne, UpdateOne
from pymongo.errors import BulkWriteError, PyMongoError

from lfx.custom import Component
from lfx.io import DataInput, IntInput, Output, SecretStrInput, StrInput
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


def _stable_document_id(parts: list[str]) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _validate_staged_asset(document: dict[str, Any], expected_embedding: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    asset = dict(document.get("asset_document") or {})
    chunks = [dict(item) for item in (document.get("asset_chunks") or []) if isinstance(item, dict)]
    if document.get("embedding_status") != "EMBEDDED" or not asset or not chunks:
        raise ValueError("Only fully embedded staging records can be written to a snapshot.")
    tenant_id = str(asset.get("tenant_id") or "")
    snapshot_id = str(asset.get("snapshot_id") or "")
    asset_id = str(asset.get("asset_id") or "")
    version = str(asset.get("version") or "")
    if not all((tenant_id, snapshot_id, asset_id, version)):
        raise ValueError("A staged asset is missing identity fields.")
    manifest = asset.get("embedding_manifest") if isinstance(asset.get("embedding_manifest"), dict) else {}
    if (
        manifest.get("model") != expected_embedding.get("model")
        or manifest.get("version") != expected_embedding.get("version")
        or int(manifest.get("dimension") or 0) != int(expected_embedding.get("dimension") or 0)
        or not manifest.get("complete")
        or int(manifest.get("chunk_count") or 0) != len(chunks)
    ):
        raise ValueError("The staged asset embedding manifest does not match the job contract.")
    seen_chunk_ids: set[str] = set()
    for chunk in chunks:
        embedding = chunk.get("embedding") if isinstance(chunk.get("embedding"), dict) else {}
        vector = embedding.get("vector")
        chunk_id = str(chunk.get("chunk_id") or "").strip()
        embedding_text = str(chunk.get("embedding_text_redacted") or "")
        expected_input_hash = hashlib.sha256(embedding_text.encode("utf-8")).hexdigest()
        if (
            not chunk_id
            or len(chunk_id) > 128
            or chunk_id in seen_chunk_ids
            or not embedding_text
            or len(embedding_text) > 20000
            or chunk.get("tenant_id") != tenant_id
            or chunk.get("snapshot_id") != snapshot_id
            or chunk.get("asset_id") != asset_id
            or str(chunk.get("version") or "") != version
            or embedding.get("model") != manifest.get("model")
            or embedding.get("version") != manifest.get("version")
            or int(embedding.get("dimension") or 0) != int(manifest.get("dimension") or 0)
            or embedding.get("input_sha256") != expected_input_hash
            or chunk.get("embedding_input_sha256") != expected_input_hash
            or not isinstance(vector, list)
            or len(vector) != int(manifest.get("dimension") or 0)
            or any(isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(float(number)) for number in vector)
        ):
            raise ValueError("A staged chunk does not match its parent or embedding manifest.")
        seen_chunk_ids.add(chunk_id)
    return asset, chunks


class MongoDBSnapshotWriterComponent(Component):
    display_name = "MongoDB Snapshot Writer"
    description = "Bulk-upsert fully embedded parent and chunk documents into an inactive tenant snapshot."
    icon = "DatabaseZap"
    name = "MongoDBSnapshotWriter"

    inputs = [
        DataInput(name="job_ref", display_name="Embedded Job Reference", required=True),
        SecretStrInput(name="mongodb_uri", display_name="MongoDB URI", required=True),
        StrInput(name="mongodb_database", display_name="MongoDB Database", value="business_work_design", required=True),
        IntInput(name="max_records_per_run", display_name="Maximum Records Per Run", value=200, advanced=True),
        IntInput(name="max_write_operations", display_name="Maximum Bulk Operations Per Run", value=5000, advanced=True),
        IntInput(name="connect_timeout_ms", display_name="MongoDB Connect Timeout (ms)", value=5000, advanced=True),
    ]

    outputs = [Output(name="ingest_result", display_name="Inactive Snapshot Write Result", method="write_snapshot", types=["Data"])]

    def write_snapshot(self) -> Data:
        trace_id = "trace-unassigned"
        try:
            incoming = _validate_job_ref(getattr(self, "job_ref", None))
            trace_id = incoming["trace_id"]
            mongodb_uri = _secret_value(getattr(self, "mongodb_uri", ""))
            database_name = str(getattr(self, "mongodb_database", "") or "").strip()
            if not mongodb_uri or not database_name:
                return _failure("MONGODB_CONFIG_MISSING", "MongoDB configuration is required.", trace_id)
            max_records = _bounded_int(getattr(self, "max_records_per_run", 200), 200, 1, 1000)
            max_operations = _bounded_int(getattr(self, "max_write_operations", 5000), 5000, 100, 10000)
            timeout_ms = _bounded_int(getattr(self, "connect_timeout_ms", 5000), 5000, 1000, 30000)
            client = MongoClient(
                mongodb_uri,
                connectTimeoutMS=timeout_ms,
                serverSelectionTimeoutMS=timeout_ms,
                socketTimeoutMS=max(timeout_ms, 15000),
                retryReads=True,
                retryWrites=True,
            )
            try:
                client.admin.command("ping")
                database = client[database_name]
                jobs = database["catalog_ingest_jobs"]
                staging = database["catalog_ingest_chunks"]
                assets = database["catalog_assets"]
                chunks_collection = database["catalog_asset_chunks"]
                snapshots = database["catalog_snapshots"]
                snapshots.create_index(
                    [("tenant_id", ASCENDING), ("snapshot_id", ASCENDING)],
                    unique=True,
                    name="uq_catalog_snapshot",
                )
                query = {"_id": incoming["job_id"], "tenant_id": incoming["tenant_id"], "snapshot_id": incoming["snapshot_id"]}
                job = jobs.find_one(query)
                if not job:
                    return _failure("CATALOG_JOB_NOT_FOUND", "The catalog ingest job was not found for this tenant.", trace_id)
                current_stage = str(job.get("stage") or "")
                if current_stage == "SNAPSHOT_WRITE_COMPLETED":
                    cursor = int((job.get("stage_cursors") or {}).get("write") or 0)
                    reconciled = snapshots.update_one(
                        {
                            "tenant_id": incoming["tenant_id"],
                            "snapshot_id": incoming["snapshot_id"],
                            "job_id": incoming["job_id"],
                            "status": {"$in": ["BUILDING", "INACTIVE_PENDING_VALIDATION"]},
                        },
                        {
                            "$set": {
                                "status": "INACTIVE_PENDING_VALIDATION",
                                "write_counts": job.get("write_counts") or {},
                                "write_completed_at": job.get("updated_at") or _utc_now(),
                                "updated_at": _utc_now(),
                            }
                        },
                    )
                    if reconciled.matched_count != 1:
                        return _failure(
                            "SNAPSHOT_RECONCILIATION_FAILED",
                            "The completed job could not reconcile its inactive snapshot projection.",
                            trace_id,
                            retryable=True,
                        )
                    job_reference = _job_ref(incoming, current_stage, cursor)
                    return Data(data={"ok": True, "status": "INACTIVE_PENDING_VALIDATION", "job_ref": job_reference, "counts": job.get("write_counts") or {}, "trace_id": trace_id})
                if current_stage not in {"EMBEDDING_COMPLETED", "SNAPSHOT_WRITE_PARTIAL"}:
                    return _failure("CATALOG_STAGE_CONFLICT", "Snapshot writing requires a fully embedded job.", trace_id)
                embedding_contract = job.get("embedding_contract") if isinstance(job.get("embedding_contract"), dict) else {}
                if not embedding_contract.get("model") or not embedding_contract.get("version") or not embedding_contract.get("dimension"):
                    return _failure("EMBEDDING_CONTRACT_MISSING", "The completed job has no embedding contract.", trace_id)

                snapshot = snapshots.find_one({"tenant_id": incoming["tenant_id"], "snapshot_id": incoming["snapshot_id"]})
                if snapshot and snapshot.get("job_id") != incoming["job_id"]:
                    return _failure("SNAPSHOT_ID_CONFLICT", "The snapshot ID belongs to a different ingest job.", trace_id)
                if snapshot and snapshot.get("status") not in {"BUILDING", "INACTIVE_PENDING_VALIDATION"}:
                    return _failure("SNAPSHOT_STATE_CONFLICT", "This snapshot is not writable.", trace_id)
                now = _utc_now()
                snapshots.update_one(
                    {"tenant_id": incoming["tenant_id"], "snapshot_id": incoming["snapshot_id"]},
                    {
                        "$setOnInsert": {
                            "tenant_id": incoming["tenant_id"],
                            "snapshot_id": incoming["snapshot_id"],
                            "job_id": incoming["job_id"],
                            "source_sha256": job.get("source_sha256"),
                            "created_at": now,
                        },
                        "$set": {"status": "BUILDING", "embedding_contract": embedding_contract, "updated_at": now},
                    },
                    upsert=True,
                )

                assets.create_index(
                    [("tenant_id", ASCENDING), ("snapshot_id", ASCENDING), ("asset_id", ASCENDING), ("version", ASCENDING)],
                    unique=True,
                    name="uq_catalog_asset_version",
                )
                chunks_collection.create_index(
                    [("tenant_id", ASCENDING), ("snapshot_id", ASCENDING), ("asset_id", ASCENDING), ("version", ASCENDING), ("chunk_id", ASCENDING)],
                    unique=True,
                    name="uq_catalog_asset_chunk",
                )
                durable_cursor = int((job.get("stage_cursors") or {}).get("write") or 0)
                embedding_cursor = int((job.get("stage_cursors") or {}).get("embedding") or 0)
                valid_transition = (
                    current_stage == "EMBEDDING_COMPLETED"
                    and incoming["stage"] == "EMBEDDING_COMPLETED"
                    and incoming["expected_cursor"] == embedding_cursor
                ) or (
                    current_stage == "SNAPSHOT_WRITE_PARTIAL"
                    and incoming["stage"] == "SNAPSHOT_WRITE_PARTIAL"
                    and incoming["expected_cursor"] == durable_cursor
                )
                if not valid_transition:
                    return _failure("CATALOG_CURSOR_CONFLICT", "The snapshot-write cursor is stale.", trace_id)
                documents = list(
                    staging.find(
                        {"tenant_id": incoming["tenant_id"], "job_id": incoming["job_id"], "record_index": {"$gte": durable_cursor}}
                    ).sort("record_index", 1).limit(max_records + 1)
                )
                has_more = len(documents) > max_records
                selected = documents[:max_records]
                asset_operations: list[ReplaceOne] = []
                chunk_operations: list[ReplaceOne] = []
                staging_operations: list[UpdateOne] = []
                next_cursor = durable_cursor
                written_assets = 0
                written_chunks = 0
                skipped = 0
                duplicate_count = 0
                operation_budget_hit = False
                batch_keys: set[tuple[str, str]] = set()
                for document in selected:
                    record_index = int(document.get("record_index") or 0)
                    if document.get("embedding_status") != "EMBEDDED":
                        staging_operations.append(
                            UpdateOne(
                                {"_id": document["_id"], "tenant_id": incoming["tenant_id"]},
                                {"$set": {"snapshot_write_status": "SKIPPED_QUARANTINE", "updated_at": now}},
                            )
                        )
                        skipped += 1
                        next_cursor = record_index + 1
                        continue
                    asset, chunk_documents = _validate_staged_asset(document, embedding_contract)
                    asset_key = (str(asset["asset_id"]), str(asset["version"]))
                    earlier_duplicate = staging.find_one(
                        {
                            "tenant_id": incoming["tenant_id"],
                            "job_id": incoming["job_id"],
                            "record_index": {"$lt": record_index},
                            "asset_document.asset_id": asset_key[0],
                            "asset_document.version": asset_key[1],
                        },
                        {"_id": 1},
                    )
                    if asset_key in batch_keys or earlier_duplicate:
                        staging_operations.append(
                            UpdateOne(
                                {"_id": document["_id"], "tenant_id": incoming["tenant_id"]},
                                {"$set": {"snapshot_write_status": "QUARANTINED_DUPLICATE_ASSET", "updated_at": now}},
                            )
                        )
                        duplicate_count += 1
                        next_cursor = record_index + 1
                        continue
                    required_operations = 1 + len(chunk_documents)
                    if len(asset_operations) + len(chunk_operations) + required_operations > max_operations:
                        operation_budget_hit = True
                        has_more = True
                        break
                    batch_keys.add(asset_key)
                    asset["_id"] = _stable_document_id([incoming["tenant_id"], incoming["snapshot_id"], asset_key[0], asset_key[1]])
                    asset["ingest_job_id"] = incoming["job_id"]
                    asset["ingest_record_index"] = record_index
                    asset["snapshot_status"] = "inactive"
                    asset["stored_at"] = now
                    asset_operations.append(
                        ReplaceOne(
                            {"tenant_id": incoming["tenant_id"], "snapshot_id": incoming["snapshot_id"], "asset_id": asset_key[0], "version": asset_key[1]},
                            asset,
                            upsert=True,
                        )
                    )
                    for chunk in chunk_documents:
                        chunk_id = str(chunk.get("chunk_id") or "")
                        chunk["_id"] = _stable_document_id([incoming["tenant_id"], incoming["snapshot_id"], asset_key[0], asset_key[1], chunk_id])
                        chunk["ingest_job_id"] = incoming["job_id"]
                        chunk["snapshot_status"] = "inactive"
                        chunk["stored_at"] = now
                        chunk_operations.append(
                            ReplaceOne(
                                {
                                    "tenant_id": incoming["tenant_id"],
                                    "snapshot_id": incoming["snapshot_id"],
                                    "asset_id": asset_key[0],
                                    "version": asset_key[1],
                                    "chunk_id": chunk_id,
                                },
                                chunk,
                                upsert=True,
                            )
                        )
                    staging_operations.append(
                        UpdateOne(
                            {"_id": document["_id"], "tenant_id": incoming["tenant_id"]},
                            {"$set": {"snapshot_write_status": "WRITTEN_INACTIVE", "updated_at": now}},
                        )
                    )
                    written_assets += 1
                    written_chunks += len(chunk_documents)
                    next_cursor = record_index + 1

                try:
                    if asset_operations:
                        assets.bulk_write(asset_operations, ordered=False)
                    if chunk_operations:
                        chunks_collection.bulk_write(chunk_operations, ordered=False)
                    if staging_operations:
                        staging.bulk_write(staging_operations, ordered=False)
                except BulkWriteError as exc:
                    if (exc.details or {}).get("writeErrors"):
                        raise

                next_stage = "SNAPSHOT_WRITE_PARTIAL" if has_more or operation_budget_hit else "SNAPSHOT_WRITE_COMPLETED"
                write_counts = {
                    "assets": int((job.get("write_counts") or {}).get("assets") or 0) + written_assets,
                    "chunks": int((job.get("write_counts") or {}).get("chunks") or 0) + written_chunks,
                    "skipped": int((job.get("write_counts") or {}).get("skipped") or 0) + skipped,
                    "duplicates": int((job.get("write_counts") or {}).get("duplicates") or 0) + duplicate_count,
                }
                update = jobs.update_one(
                    {**query, "stage": current_stage, "stage_cursors.write": durable_cursor},
                    {
                        "$set": {
                            "stage": next_stage,
                            "expected_cursor": next_cursor,
                            "stage_cursors.write": next_cursor,
                            "snapshot_write_completed": next_stage == "SNAPSHOT_WRITE_COMPLETED",
                            "write_counts": write_counts,
                            "updated_at": now,
                        }
                    },
                )
                if update.modified_count != 1:
                    return _failure("CATALOG_CURSOR_CONFLICT", "The snapshot-write cursor changed during this batch.", trace_id, True)
                snapshot_status = "BUILDING"
                if next_stage == "SNAPSHOT_WRITE_COMPLETED":
                    snapshot_status = "INACTIVE_PENDING_VALIDATION"
                    snapshots.update_one(
                        {"tenant_id": incoming["tenant_id"], "snapshot_id": incoming["snapshot_id"], "job_id": incoming["job_id"]},
                        {"$set": {"status": snapshot_status, "write_counts": write_counts, "write_completed_at": now, "updated_at": now}},
                    )
                job_reference = _job_ref(incoming, next_stage, next_cursor)
                self.status = (
                    f"Inactive snapshot batch written: job_id={incoming['job_id']}, assets={written_assets}, "
                    f"chunks={written_chunks}, cursor={next_cursor}, complete={next_stage == 'SNAPSHOT_WRITE_COMPLETED'}"
                )
                return Data(data={"ok": True, "status": snapshot_status, "job_ref": job_reference, "counts": write_counts, "trace_id": trace_id})
            finally:
                client.close()
        except ValueError as exc:
            self.status = "Snapshot writing stopped on a contract mismatch."
            return _failure("SNAPSHOT_WRITE_INVALID", str(exc), trace_id)
        except PyMongoError:
            self.status = "Inactive snapshot writing failed before the durable cursor advanced."
            return _failure(
                "SNAPSHOT_WRITE_FAILED",
                "The inactive snapshot batch could not be written. Resume from the last durable cursor.",
                trace_id,
                retryable=True,
            )
