from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, MongoClient
from pymongo.errors import OperationFailure, PyMongoError

from lfx.custom import Component
from lfx.io import BoolInput, DataInput, IntInput, Output, SecretStrInput, StrInput
from lfx.schema import Data


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


def _extract_snapshot_ref(value: Any) -> dict[str, str]:
    payload = _payload(value)
    if isinstance(payload.get("job_ref"), dict):
        payload = dict(payload["job_ref"])
    result = {
        "tenant_id": str(payload.get("tenant_id") or "").strip(),
        "snapshot_id": str(payload.get("snapshot_id") or "").strip(),
        "job_id": str(payload.get("job_id") or "").strip(),
        "trace_id": str(payload.get("trace_id") or "").strip(),
    }
    if not result["tenant_id"] or not result["snapshot_id"] or not result["job_id"] or not result["trace_id"]:
        raise ValueError("snapshot_ref requires tenant_id, snapshot_id, job_id, and trace_id.")
    return result


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


def _asset_content_hash(asset: dict[str, Any]) -> str:
    basis = {
        "asset_id": asset.get("asset_id"),
        "version": asset.get("version"),
        "asset_type": asset.get("asset_type"),
        "title_normalized": asset.get("title_normalized"),
        "aliases_normalized": asset.get("aliases_normalized"),
        "raw_text": asset.get("raw_text"),
        "acl": asset.get("acl"),
        "technical_contract": asset.get("technical_contract"),
    }
    return hashlib.sha256(json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _index_matches(index_information: dict[str, Any], expected_fields: list[str], unique: bool) -> bool:
    expected = [(field, ASCENDING) for field in expected_fields]
    for spec in index_information.values():
        keys = list(spec.get("key") or []) if isinstance(spec, dict) else []
        if keys == expected and (not unique or bool(spec.get("unique"))):
            return True
    return False


def _evaluate_validation(checks: dict[str, bool], counts: dict[str, int]) -> tuple[bool, list[str]]:
    failures = sorted(name for name, passed in checks.items() if not passed)
    if counts.get("assets", 0) <= 0:
        failures.append("ASSET_COUNT_ZERO")
    if counts.get("chunks", 0) <= 0:
        failures.append("CHUNK_COUNT_ZERO")
    return not failures, sorted(set(failures))


class CatalogSnapshotValidatorComponent(Component):
    display_name = "Catalog Snapshot Validator"
    description = "Validate inactive snapshot counts, hashes, vectors, uniqueness, parent linkage, and required indexes."
    icon = "BadgeCheck"
    name = "CatalogSnapshotValidator"

    inputs = [
        DataInput(name="snapshot_ref", display_name="Inactive Snapshot Reference", required=True),
        SecretStrInput(name="mongodb_uri", display_name="MongoDB URI", required=True),
        StrInput(name="mongodb_database", display_name="MongoDB Database", value="business_work_design", required=True),
        BoolInput(name="require_search_indexes", display_name="Require Search and Vector Indexes", value=True),
        StrInput(name="lexical_search_index", display_name="Lexical Search Index Name", value="catalog_lexical_search", advanced=True),
        StrInput(name="vector_search_index", display_name="Vector Search Index Name", value="catalog_vector_search", advanced=True),
        IntInput(name="max_hash_records", display_name="Maximum Hash Records", value=50000, advanced=True),
        IntInput(name="max_integrity_chunks", display_name="Maximum Integrity Chunks", value=100000, advanced=True),
        IntInput(name="connect_timeout_ms", display_name="MongoDB Connect Timeout (ms)", value=5000, advanced=True),
    ]

    outputs = [Output(name="validation_report", display_name="Snapshot Validation Report", method="validate_snapshot", types=["Data"])]

    def validate_snapshot(self) -> Data:
        trace_id = "trace-unassigned"
        try:
            snapshot_ref = _extract_snapshot_ref(getattr(self, "snapshot_ref", None))
            trace_id = snapshot_ref["trace_id"]
            mongodb_uri = _secret_value(getattr(self, "mongodb_uri", ""))
            database_name = str(getattr(self, "mongodb_database", "") or "").strip()
            if not mongodb_uri or not database_name:
                return _failure("MONGODB_CONFIG_MISSING", "MongoDB configuration is required.", trace_id)
            require_search_indexes = bool(getattr(self, "require_search_indexes", True))
            lexical_index = str(getattr(self, "lexical_search_index", "") or "").strip()
            vector_index = str(getattr(self, "vector_search_index", "") or "").strip()
            if require_search_indexes and (not lexical_index or not vector_index):
                return _failure("SEARCH_INDEX_CONFIG_MISSING", "Required lexical and vector index names must be configured.", trace_id)
            max_hash_records = _bounded_int(getattr(self, "max_hash_records", 50000), 50000, 1, 100000)
            max_integrity_chunks = _bounded_int(getattr(self, "max_integrity_chunks", 100000), 100000, 1, 500000)
            timeout_ms = _bounded_int(getattr(self, "connect_timeout_ms", 5000), 5000, 1000, 30000)
            client = MongoClient(
                mongodb_uri,
                connectTimeoutMS=timeout_ms,
                serverSelectionTimeoutMS=timeout_ms,
                socketTimeoutMS=max(timeout_ms, 30000),
                retryReads=True,
                retryWrites=True,
            )
            try:
                client.admin.command("ping")
                database = client[database_name]
                jobs = database["catalog_ingest_jobs"]
                assets = database["catalog_assets"]
                chunks = database["catalog_asset_chunks"]
                snapshots = database["catalog_snapshots"]
                snapshot_query = {"tenant_id": snapshot_ref["tenant_id"], "snapshot_id": snapshot_ref["snapshot_id"]}
                snapshot = snapshots.find_one(snapshot_query)
                job = jobs.find_one(
                    {"_id": snapshot_ref["job_id"], "tenant_id": snapshot_ref["tenant_id"], "snapshot_id": snapshot_ref["snapshot_id"]}
                )
                if not snapshot or not job:
                    return _failure("SNAPSHOT_NOT_FOUND", "The inactive snapshot or its ingest job was not found for this tenant.", trace_id)
                if snapshot.get("job_id") != snapshot_ref["job_id"]:
                    return _failure("SNAPSHOT_JOB_MISMATCH", "The snapshot does not belong to the supplied ingest job.", trace_id)
                if snapshot.get("status") == "VALIDATED" and isinstance(snapshot.get("validation"), dict):
                    self.status = f"Snapshot already validated: snapshot_id={snapshot_ref['snapshot_id']}"
                    return Data(data=dict(snapshot["validation"]))
                if snapshot.get("status") not in {"INACTIVE_PENDING_VALIDATION", "VALIDATION_FAILED"}:
                    return _failure("SNAPSHOT_STATE_CONFLICT", "Only a completed inactive snapshot can be validated.", trace_id)
                if job.get("stage") not in {"SNAPSHOT_WRITE_COMPLETED", "SNAPSHOT_VALIDATION_FAILED"}:
                    return _failure("CATALOG_JOB_INCOMPLETE", "The ingest job has not completed inactive snapshot writing.", trace_id)

                embedding_contract = snapshot.get("embedding_contract") if isinstance(snapshot.get("embedding_contract"), dict) else {}
                model = str(embedding_contract.get("model") or "")
                version = str(embedding_contract.get("version") or "")
                dimension = int(embedding_contract.get("dimension") or 0)
                if not model or not version or dimension <= 0:
                    return _failure("EMBEDDING_CONTRACT_MISSING", "The snapshot embedding contract is incomplete.", trace_id)

                counts = {
                    "assets": assets.count_documents(snapshot_query),
                    "chunks": chunks.count_documents(snapshot_query),
                    "missing_vectors": chunks.count_documents({**snapshot_query, "embedding.vector": {"$exists": False}}),
                    "embedding_mismatch": chunks.count_documents(
                        {
                            **snapshot_query,
                            "$or": [
                                {"embedding.model": {"$ne": model}},
                                {"embedding.version": {"$ne": version}},
                                {"embedding.dimension": {"$ne": dimension}},
                            ],
                        }
                    ),
                    "incomplete_manifests": assets.count_documents({**snapshot_query, "embedding_manifest.complete": {"$ne": True}}),
                    "stage_quarantined": int((job.get("counts") or {}).get("parse_quarantined") or 0)
                    + int((job.get("counts") or {}).get("normalize_quarantined") or 0)
                    + int((job.get("counts") or {}).get("text_quarantined") or 0),
                    "duplicate_assets": int((job.get("write_counts") or {}).get("duplicates") or 0),
                }
                expected_assets = int((job.get("write_counts") or {}).get("assets") or 0)
                expected_chunks = int((job.get("write_counts") or {}).get("chunks") or 0)

                duplicate_asset_pipeline = [
                    {"$match": snapshot_query},
                    {"$group": {"_id": {"asset_id": "$asset_id", "version": "$version"}, "count": {"$sum": 1}}},
                    {"$match": {"count": {"$gt": 1}}},
                    {"$limit": 1},
                ]
                duplicate_chunk_pipeline = [
                    {"$match": snapshot_query},
                    {"$group": {"_id": {"asset_id": "$asset_id", "version": "$version", "chunk_id": "$chunk_id"}, "count": {"$sum": 1}}},
                    {"$match": {"count": {"$gt": 1}}},
                    {"$limit": 1},
                ]
                db_duplicate_assets = bool(list(assets.aggregate(duplicate_asset_pipeline, allowDiskUse=True)))
                db_duplicate_chunks = bool(list(chunks.aggregate(duplicate_chunk_pipeline, allowDiskUse=True)))

                orphan_pipeline = [
                    {"$match": snapshot_query},
                    {
                        "$lookup": {
                            "from": "catalog_assets",
                            "let": {"tenant": "$tenant_id", "snapshot": "$snapshot_id", "asset": "$asset_id", "version": "$version"},
                            "pipeline": [
                                {
                                    "$match": {
                                        "$expr": {
                                            "$and": [
                                                {"$eq": ["$tenant_id", "$$tenant"]},
                                                {"$eq": ["$snapshot_id", "$$snapshot"]},
                                                {"$eq": ["$asset_id", "$$asset"]},
                                                {"$eq": ["$version", "$$version"]},
                                            ]
                                        }
                                    }
                                },
                                {"$limit": 1},
                            ],
                            "as": "parent",
                        }
                    },
                    {"$match": {"parent": {"$size": 0}}},
                    {"$limit": 1},
                ]
                has_orphan_chunk = bool(list(chunks.aggregate(orphan_pipeline, allowDiskUse=True)))

                hash_mismatch = False
                hash_limit_exceeded = counts["assets"] > max_hash_records
                chunk_integrity_mismatch = False
                chunk_integrity_limit_exceeded = counts["chunks"] > max_integrity_chunks
                parent_chunk_counts: dict[tuple[str, str], int] = {}
                if not chunk_integrity_limit_exceeded:
                    for chunk in chunks.find(snapshot_query).limit(max_integrity_chunks):
                        asset_key = (str(chunk.get("asset_id") or ""), str(chunk.get("version") or ""))
                        parent_chunk_counts[asset_key] = parent_chunk_counts.get(asset_key, 0) + 1
                        chunk_id = str(chunk.get("chunk_id") or "").strip()
                        embedding_text = str(chunk.get("embedding_text_redacted") or "")
                        expected_input_hash = hashlib.sha256(embedding_text.encode("utf-8")).hexdigest()
                        embedding = chunk.get("embedding") if isinstance(chunk.get("embedding"), dict) else {}
                        vector = embedding.get("vector")
                        if (
                            not chunk_id
                            or not embedding_text
                            or chunk.get("embedding_input_sha256") != expected_input_hash
                            or embedding.get("input_sha256") != expected_input_hash
                            or not isinstance(vector, list)
                            or len(vector) != dimension
                            or any(
                                isinstance(number, bool)
                                or not isinstance(number, (int, float))
                                or not math.isfinite(float(number))
                                for number in vector
                            )
                        ):
                            chunk_integrity_mismatch = True
                            break
                if not hash_limit_exceeded and not chunk_integrity_limit_exceeded and not chunk_integrity_mismatch:
                    for asset in assets.find(snapshot_query).limit(max_hash_records):
                        if asset.get("content_sha256") != _asset_content_hash(asset):
                            hash_mismatch = True
                            break
                        manifest = asset.get("embedding_manifest") if isinstance(asset.get("embedding_manifest"), dict) else {}
                        asset_key = (str(asset.get("asset_id") or ""), str(asset.get("version") or ""))
                        if int(manifest.get("chunk_count") or 0) != parent_chunk_counts.get(asset_key, 0):
                            chunk_integrity_mismatch = True
                            break
                counts["chunk_integrity_mismatch"] = int(chunk_integrity_mismatch)

                asset_indexes = assets.index_information()
                chunk_indexes = chunks.index_information()
                asset_unique_ready = _index_matches(asset_indexes, ["tenant_id", "snapshot_id", "asset_id", "version"], True)
                chunk_unique_ready = _index_matches(chunk_indexes, ["tenant_id", "snapshot_id", "asset_id", "version", "chunk_id"], True)

                search_indexes_ready = not require_search_indexes
                search_index_states: dict[str, str] = {}
                if require_search_indexes:
                    try:
                        index_documents = list(chunks.list_search_indexes())
                        for index_document in index_documents[:100]:
                            name = str(index_document.get("name") or "")
                            status = str(index_document.get("status") or "UNKNOWN").upper()
                            if name:
                                search_index_states[name] = status
                        search_indexes_ready = all(
                            search_index_states.get(name) in {"READY", "STEADY"}
                            for name in (lexical_index, vector_index)
                        )
                    except (AttributeError, OperationFailure):
                        search_indexes_ready = False

                checks = {
                    "ASSET_COUNT_MATCH": counts["assets"] == expected_assets and expected_assets > 0,
                    "CHUNK_COUNT_MATCH": counts["chunks"] == expected_chunks and expected_chunks > 0,
                    "NO_MISSING_VECTORS": counts["missing_vectors"] == 0,
                    "EMBEDDING_CONTRACT_MATCH": counts["embedding_mismatch"] == 0 and counts["incomplete_manifests"] == 0,
                    "NO_QUARANTINED_RECORDS": counts["stage_quarantined"] == 0,
                    "NO_DUPLICATE_ASSETS": counts["duplicate_assets"] == 0 and not db_duplicate_assets,
                    "NO_DUPLICATE_CHUNKS": not db_duplicate_chunks,
                    "NO_ORPHAN_CHUNKS": not has_orphan_chunk,
                    "CONTENT_HASH_VERIFIED": not hash_limit_exceeded and not hash_mismatch,
                    "CHUNK_INTEGRITY_VERIFIED": not chunk_integrity_limit_exceeded and not chunk_integrity_mismatch,
                    "ASSET_UNIQUE_INDEX_READY": asset_unique_ready,
                    "CHUNK_UNIQUE_INDEX_READY": chunk_unique_ready,
                    "SEARCH_INDEXES_READY": search_indexes_ready,
                }
                valid, failures = _evaluate_validation(checks, counts)
                report_basis = {
                    "tenant_id": snapshot_ref["tenant_id"],
                    "snapshot_id": snapshot_ref["snapshot_id"],
                    "job_id": snapshot_ref["job_id"],
                    "valid": valid,
                    "checks": checks,
                    "counts": counts,
                    "failures": failures,
                    "embedding_contract": embedding_contract,
                    "required_search_indexes": [lexical_index, vector_index] if require_search_indexes else [],
                    "search_index_states": search_index_states,
                }
                validation_hash = hashlib.sha256(
                    json.dumps(report_basis, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest()
                checked_at = _utc_now()
                report = {
                    "ok": valid,
                    "status": "VALIDATED" if valid else "VALIDATION_FAILED",
                    **report_basis,
                    "validation_hash": validation_hash,
                    "checked_at": checked_at.isoformat().replace("+00:00", "Z"),
                    "trace_id": trace_id,
                }
                snapshots.update_one(
                    snapshot_query,
                    {"$set": {"status": report["status"], "validation": report, "validation_hash": validation_hash, "updated_at": checked_at}},
                )
                jobs.update_one(
                    {"_id": snapshot_ref["job_id"], "tenant_id": snapshot_ref["tenant_id"]},
                    {"$set": {"stage": "SNAPSHOT_VALIDATED" if valid else "SNAPSHOT_VALIDATION_FAILED", "validation_hash": validation_hash, "updated_at": checked_at}},
                )
                self.status = (
                    f"Snapshot validated: snapshot_id={snapshot_ref['snapshot_id']}, assets={counts['assets']}, chunks={counts['chunks']}"
                    if valid
                    else f"Snapshot validation failed: snapshot_id={snapshot_ref['snapshot_id']}, checks_failed={len(failures)}"
                )
                return Data(data=report)
            finally:
                client.close()
        except ValueError as exc:
            self.status = "Snapshot validation rejected by input validation."
            return _failure("SNAPSHOT_VALIDATION_INPUT_INVALID", str(exc), trace_id)
        except PyMongoError:
            self.status = "Snapshot validation could not complete."
            return _failure(
                "SNAPSHOT_VALIDATION_FAILED",
                "Snapshot validation could not complete. The inactive snapshot was not approved.",
                trace_id,
                retryable=True,
            )
