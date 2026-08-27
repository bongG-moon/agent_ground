from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from lfx.custom import Component
from lfx.io import BoolInput, DataInput, IntInput, MessageTextInput, Output, SecretStrInput, StrInput
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


def _validate_activation_request(value: Any, approved: bool, approver_id: str) -> dict[str, str]:
    payload = _payload(value)
    result = {
        "tenant_id": str(payload.get("tenant_id") or "").strip(),
        "snapshot_id": str(payload.get("snapshot_id") or "").strip(),
        "job_id": str(payload.get("job_id") or "").strip(),
        "validation_hash": str(payload.get("validation_hash") or "").strip(),
        "trace_id": str(payload.get("trace_id") or "").strip(),
        "status": str(payload.get("status") or "").strip(),
    }
    if not approved:
        raise ValueError("Snapshot activation requires an explicit approved=true Human gate result.")
    if not approver_id or len(approver_id) > 128:
        raise ValueError("approver_id is required and must be at most 128 characters.")
    for key in ("tenant_id", "snapshot_id", "job_id", "validation_hash", "trace_id"):
        if not result[key]:
            raise ValueError(f"validation_report.{key} is required.")
    if not bool(payload.get("ok")) or result["status"] != "VALIDATED":
        raise ValueError("Only an ok=true, status=VALIDATED report can be activated.")
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


def _pointer_result(pointer: dict[str, Any], trace_id: str, idempotent: bool, warning: str | None = None) -> dict[str, Any]:
    result = {
        "ok": True,
        "status": "ACTIVE",
        "tenant_id": str(pointer.get("tenant_id") or pointer.get("_id") or ""),
        "snapshot_id": str(pointer.get("active_snapshot_id") or ""),
        "rollback_snapshot_id": pointer.get("rollback_snapshot_id"),
        "validation_hash": str(pointer.get("validation_hash") or ""),
        "pointer_revision": int(pointer.get("revision") or 1),
        "activated_at": pointer.get("activated_at").isoformat().replace("+00:00", "Z")
        if isinstance(pointer.get("activated_at"), datetime)
        else str(pointer.get("activated_at") or ""),
        "idempotent": idempotent,
        "trace_id": trace_id,
    }
    if warning:
        result["warning"] = warning
    return result


def _reconcile_active_projection(
    database: Any,
    *,
    tenant_id: str,
    active_snapshot_id: str,
    previous_snapshot_id: str,
    job_id: str,
    now: datetime,
) -> None:
    database["catalog_snapshots"].update_one(
        {"tenant_id": tenant_id, "snapshot_id": active_snapshot_id},
        {"$set": {"status": "ACTIVE", "activated_at": now, "updated_at": now}},
    )
    database["catalog_assets"].update_many(
        {"tenant_id": tenant_id, "snapshot_id": active_snapshot_id},
        {"$set": {"snapshot_status": "active"}},
    )
    database["catalog_asset_chunks"].update_many(
        {"tenant_id": tenant_id, "snapshot_id": active_snapshot_id},
        {"$set": {"snapshot_status": "active"}},
    )
    if previous_snapshot_id and previous_snapshot_id != active_snapshot_id:
        database["catalog_snapshots"].update_one(
            {"tenant_id": tenant_id, "snapshot_id": previous_snapshot_id},
            {"$set": {"status": "INACTIVE_ROLLBACK", "updated_at": now}},
        )
        database["catalog_assets"].update_many(
            {"tenant_id": tenant_id, "snapshot_id": previous_snapshot_id},
            {"$set": {"snapshot_status": "inactive"}},
        )
        database["catalog_asset_chunks"].update_many(
            {"tenant_id": tenant_id, "snapshot_id": previous_snapshot_id},
            {"$set": {"snapshot_status": "inactive"}},
        )
    database["catalog_ingest_jobs"].update_one(
        {"_id": job_id, "tenant_id": tenant_id},
        {"$set": {"stage": "SNAPSHOT_ACTIVE", "completed": True, "updated_at": now}},
    )


class CatalogSnapshotActivatorComponent(Component):
    display_name = "Catalog Snapshot Activator"
    description = "Atomically switch a tenant pointer to an explicitly approved, validated inactive snapshot."
    icon = "ToggleRight"
    name = "CatalogSnapshotActivator"

    inputs = [
        DataInput(name="validation_report", display_name="Validated Snapshot Report", required=True),
        MessageTextInput(
            name="approval_trigger",
            display_name="Human Input Approval Branch Trigger",
            required=True,
            info="Connect only the approved Human Input branch. This non-empty value proves branch execution but is not authorization evidence.",
        ),
        BoolInput(name="approved", display_name="Admin Approved", value=False, required=True),
        StrInput(name="approver_id", display_name="Approver ID", required=True),
        StrInput(
            name="approval_id",
            display_name="Server Approval ID",
            required=True,
            info="ID of a non-expired approval record issued by the trusted HITL backend.",
        ),
        SecretStrInput(
            name="approval_nonce",
            display_name="Approval Nonce",
            required=True,
            info="One-time nonce bound to tenant, snapshot, validation hash, and approver.",
        ),
        StrInput(
            name="expected_previous_snapshot_id",
            display_name="Expected Previous Snapshot ID",
            required=False,
            info="Optional compare-and-set guard. Leave empty only when the current pointer may be read immediately before switching.",
        ),
        SecretStrInput(name="mongodb_uri", display_name="MongoDB URI", required=True),
        StrInput(name="mongodb_database", display_name="MongoDB Database", value="business_work_design", required=True),
        IntInput(name="connect_timeout_ms", display_name="MongoDB Connect Timeout (ms)", value=5000, advanced=True),
    ]

    outputs = [Output(name="active_pointer", display_name="Active Catalog Pointer", method="activate_snapshot", types=["Data"])]

    def activate_snapshot(self) -> Data:
        trace_id = "trace-unassigned"
        try:
            approval_trigger = str(getattr(self, "approval_trigger", "") or "").strip()
            if not approval_trigger or len(approval_trigger) > 128:
                return _failure(
                    "APPROVAL_BRANCH_NOT_TRIGGERED",
                    "The approved Human Input branch must provide a bounded non-empty trigger.",
                    trace_id,
                )
            approver_id = str(getattr(self, "approver_id", "") or "").strip()
            request = _validate_activation_request(
                getattr(self, "validation_report", None),
                bool(getattr(self, "approved", False)),
                approver_id,
            )
            trace_id = request["trace_id"]
            mongodb_uri = _secret_value(getattr(self, "mongodb_uri", ""))
            database_name = str(getattr(self, "mongodb_database", "") or "").strip()
            if not mongodb_uri or not database_name:
                return _failure("MONGODB_CONFIG_MISSING", "MongoDB configuration is required.", trace_id)
            expected_previous = str(getattr(self, "expected_previous_snapshot_id", "") or "").strip()
            approval_id = str(getattr(self, "approval_id", "") or "").strip()
            approval_nonce = _secret_value(getattr(self, "approval_nonce", ""))
            if not approval_id or len(approval_id) > 256 or not approval_nonce or len(approval_nonce) > 512:
                return _failure("APPROVAL_EVIDENCE_MISSING", "A valid server approval ID and one-time nonce are required.", trace_id)
            timeout_ms = _bounded_int(getattr(self, "connect_timeout_ms", 5000), 5000, 1000, 30000)
            client = MongoClient(
                mongodb_uri,
                connectTimeoutMS=timeout_ms,
                serverSelectionTimeoutMS=timeout_ms,
                socketTimeoutMS=max(timeout_ms, 10000),
                retryReads=True,
                retryWrites=True,
            )
            try:
                client.admin.command("ping")
                database = client[database_name]
                snapshots = database["catalog_snapshots"]
                pointers = database["catalog_active_pointers"]
                approvals = database["catalog_activation_approvals"]
                approvals.create_index(
                    [("tenant_id", ASCENDING), ("approval_id", ASCENDING)],
                    unique=True,
                    name="uq_catalog_activation_approval",
                )
                snapshot = snapshots.find_one(
                    {"tenant_id": request["tenant_id"], "snapshot_id": request["snapshot_id"], "job_id": request["job_id"]}
                )
                if not snapshot:
                    return _failure("SNAPSHOT_NOT_FOUND", "The approved snapshot was not found for this tenant.", trace_id)
                if snapshot.get("status") not in {"VALIDATED", "ACTIVE"}:
                    return _failure("SNAPSHOT_NOT_VALIDATED", "Only a persisted VALIDATED snapshot can be activated.", trace_id)
                if snapshot.get("validation_hash") != request["validation_hash"]:
                    return _failure("VALIDATION_HASH_MISMATCH", "The approval does not match the persisted validation report.", trace_id)
                validation = snapshot.get("validation") if isinstance(snapshot.get("validation"), dict) else {}
                if not validation.get("ok") or validation.get("status") != "VALIDATED":
                    return _failure("SNAPSHOT_NOT_VALIDATED", "The persisted validation report is not successful.", trace_id)

                now = _utc_now()
                approver_hash = hashlib.sha256(approver_id.encode("utf-8")).hexdigest()
                nonce_hash = hashlib.sha256(approval_nonce.encode("utf-8")).hexdigest()
                approval_identity = {
                    "tenant_id": request["tenant_id"],
                    "approval_id": approval_id,
                    "snapshot_id": request["snapshot_id"],
                    "validation_hash": request["validation_hash"],
                    "approver_id_hash": approver_hash,
                    "nonce_sha256": nonce_hash,
                }
                approval = approvals.find_one(approval_identity)
                if not approval or approval.get("status") not in {"APPROVED", "APPLYING", "CONSUMED"}:
                    return _failure(
                        "APPROVAL_EVIDENCE_INVALID",
                        "The server approval record is missing, expired, consumed, or does not match this snapshot.",
                        trace_id,
                    )

                current = pointers.find_one({"_id": request["tenant_id"]})
                if current and current.get("active_snapshot_id") == request["snapshot_id"]:
                    pointers.update_one(
                        {"_id": request["tenant_id"], "active_snapshot_id": request["snapshot_id"]},
                        {"$set": {"embedding_contract": snapshot.get("embedding_contract"), "validation_hash": request["validation_hash"]}},
                    )
                    current["embedding_contract"] = snapshot.get("embedding_contract")
                    current["validation_hash"] = request["validation_hash"]
                    warning: str | None = None
                    try:
                        _reconcile_active_projection(
                            database,
                            tenant_id=request["tenant_id"],
                            active_snapshot_id=request["snapshot_id"],
                            previous_snapshot_id=str(current.get("rollback_snapshot_id") or ""),
                            job_id=request["job_id"],
                            now=now,
                        )
                        approvals.update_one(
                            {**approval_identity, "status": {"$in": ["APPROVED", "APPLYING"]}},
                            {"$set": {"status": "CONSUMED", "consumed_at": now, "activation_trace_id": trace_id}},
                        )
                    except PyMongoError:
                        warning = "The pointer is active; projection or approval reconciliation should be retried."
                    self.status = f"Snapshot already active: snapshot_id={request['snapshot_id']}"
                    return Data(data=_pointer_result(current, trace_id, True, warning))
                actual_previous = str(current.get("active_snapshot_id") or "") if current else ""
                if expected_previous and expected_previous != actual_previous:
                    return _failure("ACTIVE_POINTER_CONFLICT", "The active snapshot changed before approval was applied.", trace_id, True)

                expires_at = approval.get("expires_at")
                if isinstance(expires_at, datetime) and expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if not isinstance(expires_at, datetime) or expires_at <= now:
                    return _failure("APPROVAL_EVIDENCE_INVALID", "The server approval record has expired.", trace_id)
                if approval.get("status") == "CONSUMED":
                    return _failure("APPROVAL_EVIDENCE_INVALID", "The server approval record was already consumed.", trace_id)

                if approval.get("status") == "APPROVED":
                    claimed = approvals.update_one(
                        {**approval_identity, "status": "APPROVED", "expires_at": {"$gt": now}},
                        {"$set": {"status": "APPLYING", "activation_trace_id": trace_id, "applying_at": now}},
                    )
                    if claimed.modified_count != 1:
                        return _failure("APPROVAL_EVIDENCE_CONFLICT", "The approval record was claimed by another activation.", trace_id, True)
                elif approval.get("activation_trace_id") != trace_id:
                    return _failure("APPROVAL_EVIDENCE_CONFLICT", "The approval record is already being applied by another activation.", trace_id, True)

                if current:
                    pointer = pointers.find_one_and_update(
                        {
                            "_id": request["tenant_id"],
                            "active_snapshot_id": actual_previous,
                            "revision": int(current.get("revision") or 1),
                        },
                        {
                            "$set": {
                                "tenant_id": request["tenant_id"],
                                "active_snapshot_id": request["snapshot_id"],
                                "rollback_snapshot_id": actual_previous or None,
                                "validation_hash": request["validation_hash"],
                                "embedding_contract": snapshot.get("embedding_contract"),
                                "activated_at": now,
                                "activated_by_hash": approver_hash,
                                "trace_id": trace_id,
                            },
                            "$inc": {"revision": 1},
                        },
                        return_document=ReturnDocument.AFTER,
                    )
                    if not pointer:
                        approvals.update_one(
                            {**approval_identity, "status": "APPLYING", "activation_trace_id": trace_id},
                            {"$set": {"status": "APPROVED"}, "$unset": {"activation_trace_id": "", "applying_at": ""}},
                        )
                        return _failure("ACTIVE_POINTER_CONFLICT", "The active pointer changed during the atomic switch.", trace_id, True)
                else:
                    pointer = {
                        "_id": request["tenant_id"],
                        "tenant_id": request["tenant_id"],
                        "active_snapshot_id": request["snapshot_id"],
                        "rollback_snapshot_id": None,
                        "validation_hash": request["validation_hash"],
                        "embedding_contract": snapshot.get("embedding_contract"),
                        "activated_at": now,
                        "activated_by_hash": approver_hash,
                        "trace_id": trace_id,
                        "revision": 1,
                    }
                    try:
                        pointers.insert_one(pointer)
                    except DuplicateKeyError:
                        approvals.update_one(
                            {**approval_identity, "status": "APPLYING", "activation_trace_id": trace_id},
                            {"$set": {"status": "APPROVED"}, "$unset": {"activation_trace_id": "", "applying_at": ""}},
                        )
                        return _failure("ACTIVE_POINTER_CONFLICT", "Another activation created the tenant pointer first.", trace_id, True)

                warning: str | None = None
                try:
                    _reconcile_active_projection(
                        database,
                        tenant_id=request["tenant_id"],
                        active_snapshot_id=request["snapshot_id"],
                        previous_snapshot_id=actual_previous,
                        job_id=request["job_id"],
                        now=now,
                    )
                    approvals.update_one(
                        {**approval_identity, "status": "APPLYING", "activation_trace_id": trace_id},
                        {"$set": {"status": "CONSUMED", "consumed_at": now}},
                    )
                except PyMongoError:
                    warning = "The active pointer switched successfully; ancillary snapshot status projection requires reconciliation."

                self.status = (
                    f"Catalog snapshot activated: tenant_id={request['tenant_id']}, snapshot_id={request['snapshot_id']}, "
                    f"pointer_revision={int(pointer.get('revision') or 1)}"
                )
                return Data(data=_pointer_result(pointer, trace_id, False, warning))
            finally:
                client.close()
        except ValueError as exc:
            self.status = "Snapshot activation rejected by approval validation."
            return _failure("SNAPSHOT_ACTIVATION_INPUT_INVALID", str(exc), trace_id)
        except PyMongoError:
            self.status = "Snapshot activation did not complete."
            return _failure(
                "SNAPSHOT_ACTIVATION_FAILED",
                "The active pointer could not be switched. The previous pointer remains authoritative.",
                trace_id,
                retryable=True,
            )
