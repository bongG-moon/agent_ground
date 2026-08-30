from __future__ import annotations

"""Persist workflow runtime state without mutating semantic WorkDefinition revision."""

import copy
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from lfx.custom import Component
from lfx.io import BoolInput, DataInput, DropdownInput, IntInput, MessageTextInput, Output, SecretStrInput
from lfx.schema import Data
from pymongo import ASCENDING, MongoClient
from pymongo.errors import ConfigurationError, ConnectionFailure, OperationFailure, PyMongoError, ServerSelectionTimeoutError


RUNTIME_STATES = {
    "EXTRACTING",
    "WAITING_ANSWER",
    "MERGING",
    "READY_FOR_REVIEW",
    "WAITING_APPROVAL",
    "BLOCKED",
    "CANCELLED",
}
SAME_REVISION_TRANSITIONS = {
    "EXTRACTING": {"EXTRACTING", "WAITING_ANSWER", "READY_FOR_REVIEW", "BLOCKED", "CANCELLED"},
    "WAITING_ANSWER": {"WAITING_ANSWER", "MERGING", "BLOCKED", "CANCELLED"},
    "MERGING": {"MERGING", "WAITING_ANSWER", "READY_FOR_REVIEW", "BLOCKED", "CANCELLED"},
    "READY_FOR_REVIEW": {"READY_FOR_REVIEW", "WAITING_APPROVAL", "BLOCKED", "CANCELLED"},
    "WAITING_APPROVAL": {"WAITING_APPROVAL", "BLOCKED", "CANCELLED"},
    "BLOCKED": {"BLOCKED", "EXTRACTING", "WAITING_ANSWER", "MERGING", "CANCELLED"},
    "CANCELLED": {"CANCELLED"},
}
REVISION_ADVANCE_TRANSITIONS = {
    "EXTRACTING": {"EXTRACTING", "WAITING_ANSWER", "READY_FOR_REVIEW", "BLOCKED", "CANCELLED"},
    "WAITING_ANSWER": {"WAITING_ANSWER", "MERGING", "BLOCKED", "CANCELLED"},
    "MERGING": {"MERGING", "WAITING_ANSWER", "READY_FOR_REVIEW", "BLOCKED", "CANCELLED"},
    "READY_FOR_REVIEW": {"READY_FOR_REVIEW", "WAITING_APPROVAL", "BLOCKED", "CANCELLED"},
    "WAITING_APPROVAL": {"WAITING_APPROVAL", "BLOCKED", "CANCELLED"},
    "BLOCKED": {"EXTRACTING", "WAITING_ANSWER", "MERGING", "READY_FOR_REVIEW", "BLOCKED", "CANCELLED"},
    "CANCELLED": {"CANCELLED"},
}


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return copy.deepcopy(value)
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return copy.deepcopy(data)
    text = getattr(value, "text", value if isinstance(value, str) else "")
    if not isinstance(text, str) or not text.strip():
        return {}
    try:
        parsed = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE))
    except json.JSONDecodeError:
        return {"message": text[:8000]}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _work(value: Any) -> dict[str, Any]:
    payload = _payload(value)
    nested = payload.get("work_definition")
    return copy.deepcopy(nested) if isinstance(nested, dict) else payload


def _secret(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    return str(getter() if callable(getter) else value or "").strip()


def _utc(value: Any) -> datetime:
    text = str(value or "").strip()
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00")) if text else datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _failure(code: str, message: str, trace_id: str, *, retryable: bool = False) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "BLOCKED",
        "artifact_refs": [],
        "error": {"code": code, "message": message, "retryable": retryable, "details": {}},
        "resume": None,
        "trace_id": trace_id,
    }


def _public(document: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(document)
    result.pop("_id", None)
    result.pop("mutation_receipts", None)
    return result


def _prepare_runtime_documents(
    current: dict[str, Any] | None,
    work: dict[str, Any],
    *,
    runtime_status: str,
    phase: str,
    actor_id: str,
    idempotency_key: str,
    request_sha256: str,
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    required = ("tenant_id", "work_definition_id", "session_id", "owner_id", "revision")
    if any(work.get(key) in (None, "") for key in required):
        raise ValueError("RUNTIME_WORK_IDENTITY_INVALID")
    if runtime_status not in RUNTIME_STATES:
        raise ValueError("RUNTIME_STATE_INVALID")
    semantic_revision = int(work["revision"])
    if semantic_revision < 0:
        raise ValueError("RUNTIME_WORK_IDENTITY_INVALID")
    if actor_id != str(work["owner_id"]):
        raise ValueError("RUNTIME_ACTOR_MISMATCH")

    if current is not None:
        receipts = current.get("mutation_receipts") if isinstance(current.get("mutation_receipts"), list) else []
        replay = next((item for item in receipts if isinstance(item, dict) and item.get("idempotency_key") == idempotency_key), None)
        if replay is not None:
            if replay.get("request_sha256") != request_sha256:
                raise ValueError("RUNTIME_IDEMPOTENCY_KEY_REUSED")
            return current, {}, True
        current_semantic_revision = int(current.get("semantic_revision", -1))
        if current_semantic_revision > semantic_revision:
            raise ValueError("RUNTIME_SEMANTIC_REVISION_STALE")
        current_status = str(current.get("runtime_status") or "EXTRACTING")
        if semantic_revision > current_semantic_revision + 1:
            raise ValueError("RUNTIME_SEMANTIC_REVISION_GAP")
        allowed = (
            SAME_REVISION_TRANSITIONS.get(current_status, set())
            if current_semantic_revision == semantic_revision
            else REVISION_ADVANCE_TRANSITIONS.get(current_status, set())
        )
        if runtime_status not in allowed:
            raise ValueError("RUNTIME_STATE_TRANSITION_INVALID")
    else:
        current_semantic_revision = -1
        current_status = "EXTRACTING"

    runtime_revision = int((current or {}).get("runtime_revision") or 0) + 1
    identity_material = f"{work['tenant_id']}|{work['work_definition_id']}|{work['session_id']}"
    document = {
        "_id": "work-runtime:" + hashlib.sha256(identity_material.encode("utf-8")).hexdigest(),
        "tenant_id": str(work["tenant_id"]),
        "work_definition_id": str(work["work_definition_id"]),
        "session_id": str(work["session_id"]),
        "owner_id": str(work["owner_id"]),
        "semantic_revision": semantic_revision,
        "runtime_revision": runtime_revision,
        "runtime_status": runtime_status,
        "phase": phase[:200],
        "work_status_snapshot": str(work.get("status") or work.get("state") or ""),
        "created_at": (current or {}).get("created_at", now),
        "updated_at": now,
    }
    receipts = copy.deepcopy((current or {}).get("mutation_receipts", [])) if isinstance((current or {}).get("mutation_receipts"), list) else []
    receipts.append(
        {
            "idempotency_key": idempotency_key,
            "request_sha256": request_sha256,
            "runtime_revision": runtime_revision,
            "recorded_at": now,
        }
    )
    document["mutation_receipts"] = receipts[-100:]
    event = {
        "event_id": f"wre-{uuid.uuid4()}",
        "tenant_id": document["tenant_id"],
        "work_definition_id": document["work_definition_id"],
        "session_id": document["session_id"],
        "semantic_revision": semantic_revision,
        "runtime_revision": runtime_revision,
        "previous_runtime_status": current_status if current else None,
        "runtime_status": runtime_status,
        "phase": document["phase"],
        "actor_id": actor_id,
        "idempotency_key": idempotency_key,
        "occurred_at": now,
    }
    document["last_event"] = copy.deepcopy(event)
    return document, event, False


def store_work_runtime_state(
    work_value: Any,
    route_value: Any,
    *,
    runtime_status: Any,
    phase: Any,
    actor_id: Any,
    idempotency_key: Any,
    mongodb_uri: Any,
    mongo_database: Any,
    state_collection: Any = "work_runtime_states",
    event_collection: Any = "work_runtime_events",
    now_utc: Any = "",
    timeout_ms: Any = 5000,
    require_transactions: Any = True,
    trace_id: Any = "",
    client_factory: Callable[..., Any] = MongoClient,
) -> dict[str, Any]:
    safe_trace = str(trace_id or f"trace-{uuid.uuid4()}")[:200]
    try:
        work = _work(work_value)
        route_payload = _payload(route_value)
        status = str(runtime_status or "").strip().upper()
        safe_phase = str(phase or status.lower()).strip()[:200]
        actor = str(actor_id or work.get("owner_id") or "").strip()[:200]
        uri = _secret(mongodb_uri)
        database_name = str(mongo_database or "").strip()[:200]
        state_name = str(state_collection or "work_runtime_states").strip()[:200]
        event_name = str(event_collection or "work_runtime_events").strip()[:200]
        timeout = max(1000, min(int(timeout_ms), 30000))
        transactions = bool(require_transactions)
        now = _utc(now_utc)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _failure("RUNTIME_STATE_INPUT_INVALID", "Runtime state 입력을 해석할 수 없습니다.", safe_trace)
    if not uri or not database_name or not actor:
        return _failure("RUNTIME_STATE_CONFIG_MISSING", "MongoDB와 actor 설정이 필요합니다.", safe_trace)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", state_name) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", event_name):
        return _failure("RUNTIME_COLLECTION_INVALID", "Runtime collection 이름이 허용 형식이 아닙니다.", safe_trace)
    request_core = {
        "tenant_id": work.get("tenant_id"),
        "work_definition_id": work.get("work_definition_id"),
        "session_id": work.get("session_id"),
        "semantic_revision": work.get("revision"),
        "runtime_status": status,
        "phase": safe_phase,
        "route_payload": route_payload,
    }
    request_sha256 = hashlib.sha256(_canonical(request_core).encode("utf-8")).hexdigest()
    idem = str(idempotency_key or "").strip()[:300]
    if not idem:
        idem = "runtime-" + request_sha256
    client = None
    try:
        client = client_factory(
            uri,
            serverSelectionTimeoutMS=timeout,
            connectTimeoutMS=timeout,
            socketTimeoutMS=timeout,
            retryWrites=True,
        )
        client.admin.command("ping")
        database = client[database_name]
        states = database[state_name]
        events = database[event_name]
        states.create_index(
            [("tenant_id", ASCENDING), ("work_definition_id", ASCENDING), ("session_id", ASCENDING)],
            unique=True,
            name="uq_work_runtime_identity",
        )

        def mutate(session: Any = None) -> tuple[dict[str, Any], dict[str, Any] | None, bool]:
            identity = {
                "tenant_id": work.get("tenant_id"),
                "work_definition_id": work.get("work_definition_id"),
                "session_id": work.get("session_id"),
            }
            current = states.find_one(identity, session=session)
            document, event, replayed = _prepare_runtime_documents(
                current,
                work,
                runtime_status=status,
                phase=safe_phase,
                actor_id=actor,
                idempotency_key=idem,
                request_sha256=request_sha256,
                now=now,
            )
            if replayed:
                return document, None, True
            if current is None:
                states.insert_one(document, session=session)
            else:
                result = states.replace_one(
                    {**identity, "runtime_revision": int(current.get("runtime_revision") or 0)},
                    document,
                    session=session,
                )
                if int(getattr(result, "matched_count", 0)) != 1:
                    raise ValueError("RUNTIME_REVISION_CONFLICT")
            events.insert_one(event, session=session)
            return document, event, False

        if transactions:
            with client.start_session() as mongo_session:
                with mongo_session.start_transaction():
                    stored, event, replayed = mutate(mongo_session)
        else:
            stored, event, replayed = mutate(None)
        public = _public(stored)
        return {
            "ok": True,
            "status": status,
            "artifact_refs": [
                {
                    "kind": "work_runtime_state",
                    "id": public["work_definition_id"],
                    "runtime_revision": public["runtime_revision"],
                }
            ],
            "runtime_state": public,
            "work_definition": copy.deepcopy(work),
            "forwarded_payload": route_payload,
            "store_result": {
                "idempotent_replay": replayed,
                "event_id": None if event is None else event["event_id"],
                "transactional": transactions,
            },
            "trace_id": safe_trace,
        }
    except ValueError as exc:
        code = str(exc)
        messages = {
            "RUNTIME_WORK_IDENTITY_INVALID": "WorkDefinition runtime 식별자가 유효하지 않습니다.",
            "RUNTIME_ACTOR_MISMATCH": "Runtime actor가 WorkDefinition owner와 일치하지 않습니다.",
            "RUNTIME_STATE_INVALID": "Runtime 상태가 유효하지 않습니다.",
            "RUNTIME_STATE_TRANSITION_INVALID": "허용되지 않은 runtime 상태 전이입니다.",
            "RUNTIME_SEMANTIC_REVISION_STALE": "더 최신 semantic revision의 runtime 상태가 이미 저장되어 있습니다.",
            "RUNTIME_SEMANTIC_REVISION_GAP": "Runtime state는 semantic revision을 한 단계씩만 따라갈 수 있습니다.",
            "RUNTIME_IDEMPOTENCY_KEY_REUSED": "Runtime idempotency key가 다른 요청에 재사용되었습니다.",
            "RUNTIME_REVISION_CONFLICT": "Runtime state CAS 충돌이 발생했습니다.",
        }
        return _failure(code if code in messages else "RUNTIME_STATE_VALIDATION_FAILED", messages.get(code, "Runtime state 검증에 실패했습니다."), safe_trace, retryable=code == "RUNTIME_REVISION_CONFLICT")
    except (ConfigurationError, ConnectionFailure, ServerSelectionTimeoutError):
        return _failure("RUNTIME_MONGODB_UNAVAILABLE", "Runtime MongoDB에 연결할 수 없습니다.", safe_trace, retryable=True)
    except (OperationFailure, PyMongoError):
        return _failure("RUNTIME_MONGODB_WRITE_FAILED", "Runtime state와 event를 저장하지 못했습니다.", safe_trace, retryable=True)
    finally:
        if client is not None:
            client.close()


class WorkRuntimeStateStoreComponent(Component):
    display_name = "34 Work Runtime State Store"
    description = "질문 대기·병합·차단 상태를 semantic WorkDefinition revision과 분리해 MongoDB state/event로 저장합니다."
    icon = "Workflow"
    name = "WorkRuntimeStateStore"

    inputs = [
        DataInput(name="work_definition", display_name="WorkDefinition", input_types=["Data", "JSON"], required=True),
        DataInput(name="route_trigger", display_name="Route Payload/Trigger", input_types=["Data", "JSON", "Message"], required=True),
        DropdownInput(name="runtime_status", display_name="Runtime Status", options=sorted(RUNTIME_STATES), value="WAITING_ANSWER"),
        MessageTextInput(name="phase", display_name="Runtime Phase", value="", required=False),
        MessageTextInput(name="actor_id", display_name="Actor ID (기본 owner_id)", value="", required=False),
        MessageTextInput(name="idempotency_key", display_name="Idempotency Key (비우면 파생)", value="", required=False),
        SecretStrInput(name="mongodb_uri", display_name="MongoDB URI", required=True),
        MessageTextInput(name="mongo_database", display_name="MongoDB Database", value="business_work_design", required=True),
        MessageTextInput(name="state_collection", display_name="Runtime State Collection", value="work_runtime_states", advanced=True),
        MessageTextInput(name="event_collection", display_name="Runtime Event Collection", value="work_runtime_events", advanced=True),
        MessageTextInput(name="now_utc", display_name="기준 시각(ISO-8601)", value="", advanced=True),
        IntInput(name="timeout_ms", display_name="MongoDB Timeout(ms)", value=5000, advanced=True),
        BoolInput(name="require_transactions", display_name="Transaction 필수", value=True, advanced=True),
        MessageTextInput(name="trace_id", display_name="Trace ID", value="", advanced=True),
    ]
    outputs = [
        Output(name="success_path", display_name="Runtime State Persisted", method="route_state", types=["Data"], group_outputs=True),
        Output(name="blocked_path", display_name="Runtime Persistence Blocked", method="route_state", types=["Data"], group_outputs=True),
    ]

    def route_state(self) -> Data:
        result = getattr(self, "_runtime_result", None)
        if not isinstance(result, dict):
            result = store_work_runtime_state(
                getattr(self, "work_definition", None),
                getattr(self, "route_trigger", None),
                runtime_status=getattr(self, "runtime_status", "WAITING_ANSWER"),
                phase=getattr(self, "phase", ""),
                actor_id=getattr(self, "actor_id", ""),
                idempotency_key=getattr(self, "idempotency_key", ""),
                mongodb_uri=getattr(self, "mongodb_uri", ""),
                mongo_database=getattr(self, "mongo_database", ""),
                state_collection=getattr(self, "state_collection", "work_runtime_states"),
                event_collection=getattr(self, "event_collection", "work_runtime_events"),
                now_utc=getattr(self, "now_utc", ""),
                timeout_ms=getattr(self, "timeout_ms", 5000),
                require_transactions=getattr(self, "require_transactions", True),
                trace_id=getattr(self, "trace_id", ""),
                client_factory=MongoClient,
            )
            self._runtime_result = result
        selected = "success_path" if result.get("ok") is True else "blocked_path"
        for output_name in ("success_path", "blocked_path"):
            if output_name != selected:
                self.stop(output_name)
        self.status = {"ok": result.get("ok"), "status": result.get("status"), "route": selected}
        return Data(data=dict(result))
