from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from lfx.custom import Component
from lfx.io import BoolInput, DataInput, DropdownInput, IntInput, MessageTextInput, Output, SecretStrInput
from lfx.schema import Data
from pymongo import MongoClient
from pymongo.errors import ConfigurationError, ConnectionFailure, OperationFailure, PyMongoError, ServerSelectionTimeoutError


VALID_STATES = {
    "INTAKE",
    "EXTRACTING",
    "NEEDS_CLARIFICATION",
    "WAITING_ANSWER",
    "MERGING",
    "READY_FOR_REVIEW",
    "WAITING_APPROVAL",
    "APPROVED",
    "DESIGNING",
    "REPORT_READY",
    "REJECTED",
    "CANCELLED",
    "BLOCKED",
}
ALLOWED_TRANSITIONS = {
    "INTAKE": {"INTAKE", "EXTRACTING", "CANCELLED", "BLOCKED"},
    "EXTRACTING": {"EXTRACTING", "NEEDS_CLARIFICATION", "READY_FOR_REVIEW", "CANCELLED", "BLOCKED"},
    "NEEDS_CLARIFICATION": {"NEEDS_CLARIFICATION", "WAITING_ANSWER", "READY_FOR_REVIEW", "CANCELLED", "BLOCKED"},
    "WAITING_ANSWER": {"WAITING_ANSWER", "MERGING", "READY_FOR_REVIEW", "CANCELLED", "BLOCKED"},
    "MERGING": {"MERGING", "NEEDS_CLARIFICATION", "READY_FOR_REVIEW", "CANCELLED", "BLOCKED"},
    "READY_FOR_REVIEW": {"READY_FOR_REVIEW", "WAITING_APPROVAL", "NEEDS_CLARIFICATION", "CANCELLED"},
    "WAITING_APPROVAL": {"WAITING_APPROVAL", "APPROVED", "NEEDS_CLARIFICATION", "REJECTED", "CANCELLED", "BLOCKED"},
    "APPROVED": {"APPROVED", "NEEDS_CLARIFICATION", "DESIGNING", "CANCELLED", "BLOCKED"},
    "DESIGNING": {"DESIGNING", "REPORT_READY", "BLOCKED", "CANCELLED"},
    "REPORT_READY": {"REPORT_READY", "DESIGNING", "CANCELLED"},
    "REJECTED": {"REJECTED"},
    "CANCELLED": {"CANCELLED"},
    "BLOCKED": {"BLOCKED", "EXTRACTING", "NEEDS_CLARIFICATION", "DESIGNING", "CANCELLED"},
}
COMMAND_STATES = {
    "request_approval": "WAITING_APPROVAL",
    "approve": "APPROVED",
    "accept_assumptions": "READY_FOR_REVIEW",
    "request_changes": "NEEDS_CLARIFICATION",
    "reject": "REJECTED",
    "cancel": "CANCELLED",
}
USER_ACTION_COMMANDS = {"approve", "accept_assumptions", "request_changes", "reject", "cancel"}


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        payload = copy.deepcopy(value)
    else:
        data = getattr(value, "data", None)
        if isinstance(data, dict):
            payload = copy.deepcopy(data)
        else:
            text = getattr(value, "text", value if isinstance(value, str) else "")
            payload = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)) if isinstance(text, str) and text.strip() else {}
    nested = payload.get("work_definition")
    return copy.deepcopy(nested) if isinstance(nested, dict) else payload


def _secret(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    return str(getter() if callable(getter) else value or "")


def _as_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _utc(value: Any) -> datetime:
    text = str(value or "").strip()
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00")) if text else datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _public_document(value: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(value)
    result.pop("_id", None)
    result.pop("pending_action", None)
    return result


def _failure(code: str, message: str, trace_id: str, details: dict[str, Any] | None = None, *, retryable: bool = False) -> dict[str, Any]:
    return {"ok": False, "status": "BLOCKED", "artifact_refs": [], "error": {"code": code, "message": message, "retryable": retryable, "details": details or {}}, "resume": None, "trace_id": trace_id}


def _validate_playground_action(current: dict[str, Any], token: str, now: datetime, command: str, actor_id: str) -> str | None:
    pending = current.get("pending_action") if isinstance(current.get("pending_action"), dict) else {}
    if not token or not pending:
        return "ACTION_TOKEN_REQUIRED"
    if pending.get("used_at"):
        return "ACTION_TOKEN_ALREADY_USED"
    if str(pending.get("channel_mode")) != "playground" or str(pending.get("session_id")) != str(current.get("session_id")):
        return "ACTION_TOKEN_SCOPE_MISMATCH"
    if str(pending.get("actor_id")) != str(current.get("owner_id")) or actor_id != str(current.get("owner_id")):
        return "ACTION_ACTOR_MISMATCH"
    try:
        if int(pending.get("revision", -1)) != int(current.get("revision", -2)):
            return "ACTION_TOKEN_REVISION_MISMATCH"
    except (TypeError, ValueError):
        return "ACTION_TOKEN_INVALID"
    allowed_commands = pending.get("allowed_commands") if isinstance(pending.get("allowed_commands"), list) else []
    if command not in {str(item) for item in allowed_commands}:
        return "ACTION_TOKEN_COMMAND_NOT_ALLOWED"
    try:
        if now >= _utc(pending.get("expires_at")):
            return "ACTION_TOKEN_EXPIRED"
    except (TypeError, ValueError):
        return "ACTION_TOKEN_INVALID"
    supplied = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(supplied, str(pending.get("token_sha256") or "")):
        return "ACTION_TOKEN_INVALID"
    if str(pending.get("preview_hash") or "") != str(current.get("preview_hash") or ""):
        return "ACTION_TOKEN_SCOPE_MISMATCH"
    return None


def _prepare_document(
    current: dict[str, Any] | None,
    incoming: dict[str, Any],
    *,
    command: str,
    expected_revision: int,
    actor_id: str,
    idempotency_key: str,
    request_hash: str,
    now: datetime,
    pending_action_token_sha256: str = "",
    pending_action_expires_at: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current_state = str((current or {}).get("status") or "INTAKE")
    document = copy.deepcopy(incoming)
    document.pop("_id", None)
    if current is None:
        identity_material = f"{incoming['tenant_id']}|{incoming['work_definition_id']}"
        document["_id"] = "work-definition:" + hashlib.sha256(identity_material.encode("utf-8")).hexdigest()
    document["revision"] = expected_revision if current is None else expected_revision + 1
    target_state = COMMAND_STATES.get(command, str(document.get("status") or current_state))
    if target_state not in VALID_STATES:
        raise ValueError("WORK_STATE_INVALID")
    if current is not None and target_state not in ALLOWED_TRANSITIONS.get(current_state, set()):
        raise ValueError("WORK_STATE_TRANSITION_INVALID")

    preview_hash = str(document.get("preview_hash") or "")
    prior_approved = str((current or {}).get("approved_hash") or "")
    if command in {"approve", "request_approval"} and not preview_hash:
        raise ValueError("PREVIEW_HASH_REQUIRED")
    if command == "approve":
        if not preview_hash:
            raise ValueError("PREVIEW_HASH_REQUIRED")
        document["approved_hash"] = preview_hash
    elif command in {"request_changes", "reject", "cancel"}:
        document["approved_hash"] = None
    elif prior_approved and prior_approved == preview_hash:
        document["approved_hash"] = prior_approved
    else:
        # Saving cannot forge approval. A semantic change invalidates it.
        document["approved_hash"] = None
        if target_state == "APPROVED":
            target_state = "READY_FOR_REVIEW"
    document["status"] = target_state
    document["updated_at"] = now
    if current is None:
        document["created_at"] = now
    else:
        document["created_at"] = current.get("created_at", now)

    receipts = copy.deepcopy((current or {}).get("mutation_receipts", [])) if isinstance((current or {}).get("mutation_receipts"), list) else []
    receipt = {
        "idempotency_key": idempotency_key,
        "request_sha256": request_hash,
        "command": command,
        "resulting_revision": document["revision"],
        "recorded_at": now,
    }
    receipts.append(receipt)
    document["mutation_receipts"] = receipts[-100:]
    if current is not None and "pending_action" in current:
        document["pending_action"] = copy.deepcopy(current["pending_action"])
    if command == "request_approval" and document.get("channel_mode") == "playground":
        if not pending_action_token_sha256 or pending_action_expires_at is None:
            raise ValueError("ACTION_TOKEN_ISSUANCE_REQUIRED")
        document["pending_action"] = {
            "token_sha256": pending_action_token_sha256,
            "channel_mode": "playground",
            "session_id": document["session_id"],
            "actor_id": document["owner_id"],
            "revision": document["revision"],
            "preview_hash": preview_hash,
            "allowed_commands": sorted(USER_ACTION_COMMANDS),
            "issued_at": now,
            "expires_at": pending_action_expires_at,
            "used_at": None,
        }
    if command in USER_ACTION_COMMANDS and document.get("channel_mode") == "playground" and isinstance(document.get("pending_action"), dict):
        document["pending_action"]["used_at"] = now

    event = {
        "event_id": f"wde-{uuid.uuid4()}",
        "tenant_id": document["tenant_id"],
        "work_definition_id": document["work_definition_id"],
        "revision": document["revision"],
        "actor_id": actor_id,
        "command": command,
        "previous_status": current_state if current is not None else None,
        "new_status": target_state,
        "content_sha256": "sha256:" + hashlib.sha256(_canonical(_public_document(document)).encode("utf-8")).hexdigest(),
        "idempotency_key": idempotency_key,
        "occurred_at": now,
    }
    document["last_event"] = copy.deepcopy(event)
    return document, event


def store_work_definition(
    value: Any,
    *,
    expected_revision: Any,
    command: Any,
    actor_id: Any,
    idempotency_key: Any,
    mongodb_uri: Any,
    mongo_database: Any,
    work_collection: Any = "work_definitions",
    event_collection: Any = "work_definition_events",
    one_time_action_token: Any = "",
    action_token_ttl_seconds: Any = 900,
    now_utc: Any = "",
    timeout_ms: Any = 5000,
    require_transactions: Any = True,
    trace_id: Any = "",
    client_factory: Callable[..., Any] = MongoClient,
) -> dict[str, Any]:
    safe_trace = str(trace_id or f"trace-{uuid.uuid4()}")[:200]
    try:
        incoming = _payload(value)
        expected = int(expected_revision)
        selected_command = str(command or "save").strip().lower()
        actor = str(actor_id or "").strip()[:200]
        idem = str(idempotency_key or "").strip()[:300]
        uri = _secret(mongodb_uri)
        database_name = str(mongo_database or "").strip()[:200]
        work_name = str(work_collection or "work_definitions").strip()[:200]
        event_name = str(event_collection or "work_definition_events").strip()[:200]
        token = _secret(one_time_action_token)
        action_ttl = max(60, min(int(action_token_ttl_seconds), 3600))
        now = _utc(now_utc)
        timeout = max(1000, min(int(timeout_ms), 30_000))
        transactions_required = _as_bool(require_transactions, True)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _failure("WORK_STORE_INPUT_INVALID", "WorkDefinition 저장 입력을 해석할 수 없습니다.", safe_trace)
    missing = [key for key in ("work_definition_id", "tenant_id", "owner_id", "session_id", "revision", "status", "channel_mode") if incoming.get(key) in (None, "")]
    config_missing = [name for name, val in (("mongodb_uri", uri), ("mongo_database", database_name), ("actor_id", actor), ("idempotency_key", idem)) if not val]
    if missing or config_missing or expected < 0 or selected_command not in ({"save"} | set(COMMAND_STATES)):
        return _failure("WORK_STORE_INPUT_INVALID", "저장 계약 또는 production MongoDB 설정이 유효하지 않습니다.", safe_trace, {"missing_fields": missing + config_missing, "command": selected_command})
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", work_name) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", event_name):
        return _failure("WORK_STORE_COLLECTION_INVALID", "MongoDB collection 이름이 허용 형식이 아닙니다.", safe_trace)

    action_token_sha256 = hashlib.sha256(token.encode("utf-8")).hexdigest() if token else ""
    if selected_command == "request_approval" and str(incoming.get("channel_mode")) == "playground" and not action_token_sha256:
        return _failure("ACTION_TOKEN_ISSUANCE_REQUIRED", "Playground 승인 요청에는 호출자가 생성한 one-time token이 필요합니다.", safe_trace)
    if selected_command == "request_approval" and str(incoming.get("channel_mode")) == "playground":
        token_size = len(token.encode("utf-8"))
        if token_size < 32 or token_size > 512:
            return _failure(
                "ACTION_TOKEN_WEAK",
                "Playground one-time token은 trusted gateway가 생성한 32~512 byte 값이어야 합니다.",
                safe_trace,
            )
    request_source = copy.deepcopy(incoming)
    request_source.pop("mutation_receipts", None)
    request_source.pop("last_event", None)
    request_hash = hashlib.sha256(
        _canonical(
            {
                "command": selected_command,
                "expected_revision": expected,
                "work_definition": request_source,
                "action_token_sha256": action_token_sha256,
                "action_token_ttl_seconds": action_ttl if selected_command == "request_approval" else None,
            }
        ).encode("utf-8")
    ).hexdigest()
    client = None
    try:
        client = client_factory(uri, serverSelectionTimeoutMS=timeout, connectTimeoutMS=timeout, socketTimeoutMS=timeout, retryWrites=True)
        client.admin.command("ping")
        database = client[database_name]
        definitions = database[work_name]
        events = database[event_name]

        def mutate(session: Any = None) -> tuple[dict[str, Any], dict[str, Any] | None, bool]:
            identity = {"tenant_id": incoming["tenant_id"], "work_definition_id": incoming["work_definition_id"]}
            current = definitions.find_one(identity, session=session)
            if current is not None:
                receipts = current.get("mutation_receipts") if isinstance(current.get("mutation_receipts"), list) else []
                replay = next((item for item in receipts if isinstance(item, dict) and item.get("idempotency_key") == idem), None)
                if replay is not None:
                    if replay.get("request_sha256") != request_hash or replay.get("command") != selected_command:
                        raise ValueError("IDEMPOTENCY_KEY_REUSED")
                    return current, None, True
                if int(current.get("revision", -1)) != expected:
                    raise ValueError("REVISION_CONFLICT")
                incoming_revision = int(incoming.get("revision", -1))
                if incoming_revision not in {expected, expected + 1}:
                    raise ValueError("WORK_INCOMING_REVISION_INVALID")
                if str(current.get("owner_id")) != str(incoming.get("owner_id")) or str(current.get("session_id")) != str(incoming.get("session_id")) or str(current.get("channel_mode")) != str(incoming.get("channel_mode")):
                    raise ValueError("WORK_CHANNEL_SESSION_MISMATCH")
                if selected_command in USER_ACTION_COMMANDS and actor != str(current.get("owner_id")):
                    raise ValueError("ACTION_ACTOR_MISMATCH")
                if selected_command in USER_ACTION_COMMANDS and str(current.get("channel_mode")) == "playground":
                    token_error = _validate_playground_action(current, token, now, selected_command, actor)
                    if token_error:
                        raise ValueError(token_error)
                # User actions are commands over the durable, previously
                # reviewed revision.  Never let a caller smuggle a different
                # semantic body or preview hash in the action payload.
                document_source = current if selected_command in USER_ACTION_COMMANDS else incoming
                document, event = _prepare_document(
                    current,
                    document_source,
                    command=selected_command,
                    expected_revision=expected,
                    actor_id=actor,
                    idempotency_key=idem,
                    request_hash=request_hash,
                    now=now,
                    pending_action_token_sha256=action_token_sha256,
                    pending_action_expires_at=now + timedelta(seconds=action_ttl) if selected_command == "request_approval" and action_token_sha256 else None,
                )
                result = definitions.replace_one({**identity, "revision": expected}, document, session=session)
                if int(getattr(result, "matched_count", 0)) != 1:
                    raise ValueError("REVISION_CONFLICT")
            else:
                if expected != 0 or selected_command != "save" or int(incoming.get("revision", -1)) != 0:
                    raise ValueError("REVISION_CONFLICT")
                document, event = _prepare_document(None, incoming, command=selected_command, expected_revision=0, actor_id=actor, idempotency_key=idem, request_hash=request_hash, now=now)
                definitions.insert_one(document, session=session)
            events.insert_one(event, session=session)
            return document, event, False

        if transactions_required:
            with client.start_session() as mongo_session:
                with mongo_session.start_transaction():
                    stored, event, replayed = mutate(mongo_session)
        else:
            stored, event, replayed = mutate(None)
        public = _public_document(stored)
        return {
            "ok": True,
            "status": str(public.get("status")),
            "artifact_refs": [{"kind": "work_definition", "id": public["work_definition_id"], "revision": int(public["revision"])}],
            "work_definition": public,
            "store_result": {
                "idempotent_replay": replayed,
                "event_id": None if event is None else event["event_id"],
                "revision": int(public["revision"]),
                "transactional": transactions_required,
                "action_token_registered": bool(selected_command == "request_approval" and action_token_sha256),
                "action_token_expires_at": (stored.get("pending_action") or {}).get("expires_at") if selected_command == "request_approval" else None,
            },
            "trace_id": safe_trace,
        }
    except ValueError as exc:
        code = str(exc)
        messages = {
            "REVISION_CONFLICT": "저장된 WorkDefinition revision이 expected_revision과 다릅니다.",
            "IDEMPOTENCY_KEY_REUSED": "같은 idempotency key가 다른 요청에 사용되었습니다.",
            "WORK_CHANNEL_SESSION_MISMATCH": "저장된 작업의 owner, session 또는 F10/F11 channel을 변경할 수 없습니다.",
            "WORK_INCOMING_REVISION_INVALID": "입력 WorkDefinition revision은 expected_revision 또는 그 다음 revision이어야 합니다.",
            "WORK_STATE_INVALID": "저장하려는 업무 상태가 유효하지 않습니다.",
            "WORK_STATE_TRANSITION_INVALID": "허용되지 않은 업무 상태 전이입니다.",
            "PREVIEW_HASH_REQUIRED": "승인하려면 현재 preview_hash가 필요합니다.",
            "ACTION_TOKEN_REQUIRED": "Playground action에는 one-time token이 필요합니다.",
            "ACTION_TOKEN_ALREADY_USED": "이미 사용한 Playground action token입니다.",
            "ACTION_TOKEN_SCOPE_MISMATCH": "Playground action token의 channel/session 범위가 다릅니다.",
            "ACTION_TOKEN_REVISION_MISMATCH": "Playground action token이 현재 WorkDefinition revision에 바인딩되어 있지 않습니다.",
            "ACTION_TOKEN_COMMAND_NOT_ALLOWED": "Playground action token으로 요청한 command를 실행할 수 없습니다.",
            "ACTION_ACTOR_MISMATCH": "승인 action actor가 durable WorkDefinition owner와 일치하지 않습니다.",
            "ACTION_TOKEN_EXPIRED": "Playground action token이 만료되었습니다.",
            "ACTION_TOKEN_INVALID": "Playground action token이 유효하지 않습니다.",
            "ACTION_TOKEN_ISSUANCE_REQUIRED": "Playground 승인 요청에는 호출자가 생성한 one-time token이 필요합니다.",
            "ACTION_TOKEN_WEAK": "Playground one-time token은 trusted gateway가 생성한 32~512 byte 값이어야 합니다.",
        }
        return _failure(code if code in messages else "WORK_STORE_VALIDATION_FAILED", messages.get(code, "WorkDefinition 저장 검증에 실패했습니다."), safe_trace)
    except (ConfigurationError, ConnectionFailure, ServerSelectionTimeoutError) as exc:
        return _failure("MONGODB_UNAVAILABLE", "WorkDefinition 저장소에 연결할 수 없습니다.", safe_trace, {"exception_type": type(exc).__name__}, retryable=True)
    except OperationFailure as exc:
        code = "MONGODB_TRANSACTION_REQUIRED" if transactions_required else "MONGODB_OPERATION_FAILED"
        return _failure(code, "MongoDB transaction 또는 저장 작업을 완료할 수 없습니다.", safe_trace, {"exception_type": type(exc).__name__}, retryable=True)
    except PyMongoError as exc:
        return _failure("MONGODB_WRITE_FAILED", "WorkDefinition과 audit event를 저장하지 못했습니다.", safe_trace, {"exception_type": type(exc).__name__}, retryable=True)
    finally:
        if client is not None:
            client.close()


class WorkDefinitionStoreComponent(Component):
    display_name = "18 WorkDefinition Mongo Store"
    description = "revision CAS와 idempotency를 적용하고 WorkDefinition 및 append-only 상태 event를 MongoDB에 저장합니다."
    icon = "Database"
    name = "WorkDefinitionStore"

    inputs = [
        DataInput(name="work_definition", display_name="WorkDefinition", input_types=["Data", "JSON"], required=True),
        IntInput(name="expected_revision", display_name="Expected Revision", value=0, required=True),
        BoolInput(name="derive_expected_revision", display_name="WorkDefinition에서 Expected Revision 사용", value=False, advanced=True),
        BoolInput(
            name="incoming_revision_is_next",
            display_name="입력 Revision이 저장값의 다음 Revision",
            value=False,
            advanced=True,
            info="Answer Merger처럼 revision을 먼저 증가시킨 입력을 저장할 때만 사용합니다.",
        ),
        DropdownInput(name="command", display_name="저장 Command", options=["save", "request_approval", "accept_assumptions", "approve", "request_changes", "reject", "cancel"], value="save"),
        DataInput(
            name="route_trigger",
            display_name="상태 전이 Route Trigger",
            input_types=["Data", "JSON", "Message"],
            required=False,
            advanced=True,
            info="Human Input 또는 조건 분기의 실행 의존성입니다. 저장 hash에는 포함되지 않습니다.",
        ),
        MessageTextInput(name="actor_id", display_name="Actor ID", required=True),
        MessageTextInput(name="idempotency_key", display_name="Idempotency Key", required=False),
        BoolInput(name="derive_idempotency_key", display_name="내용에서 Idempotency Key 생성", value=False, advanced=True),
        SecretStrInput(name="mongodb_uri", display_name="MongoDB URI", required=True),
        MessageTextInput(name="mongo_database", display_name="MongoDB Database", required=True),
        MessageTextInput(name="work_collection", display_name="WorkDefinition Collection", value="work_definitions", advanced=True),
        MessageTextInput(name="event_collection", display_name="Event Collection", value="work_definition_events", advanced=True),
        SecretStrInput(name="one_time_action_token", display_name="F11 One-time Action Token", required=False, advanced=True),
        IntInput(name="action_token_ttl_seconds", display_name="F11 Action Token TTL(초)", value=900, advanced=True),
        MessageTextInput(name="now_utc", display_name="기준 시각(ISO-8601)", value="", advanced=True),
        IntInput(name="timeout_ms", display_name="MongoDB Timeout(ms)", value=5000, advanced=True),
        BoolInput(name="require_transactions", display_name="Transaction 필수", value=True, advanced=True),
        MessageTextInput(name="trace_id", display_name="Trace ID", value="", advanced=True),
    ]
    outputs = [Output(name="stored_work_definition", display_name="저장 결과", method="store_definition", types=["Data"])]

    def store_definition(self) -> Data:
        expected_revision = getattr(self, "expected_revision", 0)
        idempotency_key = getattr(self, "idempotency_key", "")
        if bool(getattr(self, "derive_expected_revision", False)) or bool(getattr(self, "derive_idempotency_key", False)):
            try:
                work_for_derivation = _payload(getattr(self, "work_definition", None))
            except (TypeError, ValueError, json.JSONDecodeError):
                work_for_derivation = {}
            if bool(getattr(self, "derive_expected_revision", False)):
                expected_revision = work_for_derivation.get("revision", expected_revision)
                if bool(getattr(self, "incoming_revision_is_next", False)):
                    try:
                        expected_revision = max(0, int(expected_revision) - 1)
                    except (TypeError, ValueError):
                        pass
            if bool(getattr(self, "derive_idempotency_key", False)) and not str(idempotency_key or "").strip():
                material = _canonical(
                    {
                        "tenant_id": work_for_derivation.get("tenant_id"),
                        "work_definition_id": work_for_derivation.get("work_definition_id"),
                        "revision": work_for_derivation.get("revision"),
                        "preview_hash": work_for_derivation.get("preview_hash"),
                        "command": getattr(self, "command", "save"),
                    }
                )
                idempotency_key = "work-" + hashlib.sha256(material.encode("utf-8")).hexdigest()
        result = store_work_definition(
            getattr(self, "work_definition", None),
            expected_revision=expected_revision,
            command=getattr(self, "command", "save"),
            actor_id=getattr(self, "actor_id", ""),
            idempotency_key=idempotency_key,
            mongodb_uri=getattr(self, "mongodb_uri", ""),
            mongo_database=getattr(self, "mongo_database", ""),
            work_collection=getattr(self, "work_collection", "work_definitions"),
            event_collection=getattr(self, "event_collection", "work_definition_events"),
            one_time_action_token=getattr(self, "one_time_action_token", ""),
            action_token_ttl_seconds=getattr(self, "action_token_ttl_seconds", 900),
            now_utc=getattr(self, "now_utc", ""),
            timeout_ms=getattr(self, "timeout_ms", 5000),
            require_transactions=getattr(self, "require_transactions", True),
            trace_id=getattr(self, "trace_id", ""),
            client_factory=MongoClient,
        )
        self.status = {"ok": result["ok"], "status": result["status"], "revision": (result.get("store_result") or {}).get("revision")}
        return Data(data=result)
