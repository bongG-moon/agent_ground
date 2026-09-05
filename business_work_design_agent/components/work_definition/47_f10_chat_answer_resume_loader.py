from __future__ import annotations

"""Load one pending F10 question batch for a numbered Playground chat reply.

This component is deliberately standalone.  It does not import the F10
question/answer components or call a Flow/API.  It reads the authoritative
MongoDB batch and WorkDefinition, validates their shared identity, and emits a
small JSON-safe resume envelope for the numbered-answer parser and Component
39.  The Human Input node remains a choice-only checkpoint, which is the
contract available in the deployed Langflow 1.11.0 Playground.
"""

import copy
import hashlib
import json
import re
import uuid
from datetime import date, datetime, time, timezone
from typing import Any, Callable

from lfx.custom import Component
from lfx.io import DataInput, IntInput, MessageTextInput, Output, SecretStrInput
from lfx.schema import Data
from pymongo import MongoClient
from pymongo.errors import ConfigurationError, ConnectionFailure, PyMongoError, ServerSelectionTimeoutError


ALLOWED_CHANNELS = {"native_hitl"}
MAX_REPLY_CHARS = 16_000
MAX_ID_CHARS = 200
MAX_COLLECTION_CHARS = 200
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_COLLECTION_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,200}$")
_BATCH_HEADER_PATTERN = re.compile(
    r"(?im)^\s*(?:질문\s*(?:묶음|배치|batch)(?:\s*(?:id|아이디))?|batch[_\s-]?id)\s*[:：]\s*(qb-[A-Za-z0-9._:-]{1,180})\s*$"
)
_BATCH_TOKEN_PATTERN = re.compile(r"(?i)\b(qb-[a-z0-9][a-z0-9._:-]{1,180})\b")


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return copy.deepcopy(value)
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return copy.deepcopy(data)
    text = getattr(value, "text", value if isinstance(value, str) else "")
    if isinstance(text, str) and text.strip():
        return {"answer_text": text}
    return {}


def _text(value: Any, maximum: int = MAX_ID_CHARS) -> str:
    return str(value or "").strip()[:maximum]


def _secret(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    return str(getter() if callable(getter) else value or "").strip()


def _utc(value: Any = None) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00")) if text else datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_safe(value: Any) -> Any:
    """Return Data-safe primitives; PyMongo commonly returns datetime/ObjectId."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else None
    if isinstance(value, datetime):
        return _iso(_utc(value))
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items() if str(key) != "_id"}
    return str(value)


def _failure(code: str, message: str, trace_id: str, details: dict[str, Any] | None = None, *, retryable: bool = False) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "BLOCKED",
        "route": "blocked_path",
        "artifact_refs": [],
        "error": {"code": code, "message": message, "retryable": retryable, "details": details or {}},
        "resume": None,
        "trace_id": trace_id,
    }


def _batch_id_from_reply(reply: str) -> tuple[str, bool]:
    """Return an explicit batch id and whether the reply named several ids."""

    header_ids = list(dict.fromkeys(match.casefold() for match in _BATCH_HEADER_PATTERN.findall(reply)))
    token_ids = list(dict.fromkeys(match.casefold() for match in _BATCH_TOKEN_PATTERN.findall(reply)))
    values = header_ids or token_ids
    if len(values) > 1:
        return "", True
    return (values[0] if values else ""), False


def _safe_collection(value: Any, default: str) -> str:
    name = _text(value, MAX_COLLECTION_CHARS) or default
    return name if _COLLECTION_PATTERN.fullmatch(name) else ""


def _find_pending_batch(batches: Any, *, tenant_id: str, actor_id: str, explicit_batch_id: str) -> dict[str, Any]:
    base = {"tenant_id": tenant_id, "owner_id": actor_id, "channel_mode": {"$in": sorted(ALLOWED_CHANNELS)}}
    if explicit_batch_id:
        document = batches.find_one({**base, "batch_id": explicit_batch_id})
        if not isinstance(document, dict):
            raise ValueError("CHAT_ANSWER_BATCH_NOT_FOUND")
        if str(document.get("status") or "") != "WAITING_ANSWER":
            raise ValueError("CHAT_ANSWER_BATCH_NOT_PENDING")
        return copy.deepcopy(document)

    candidates = list(batches.find({**base, "status": "WAITING_ANSWER"}))
    if not candidates:
        raise ValueError("CHAT_ANSWER_BATCH_NOT_FOUND")
    if len(candidates) != 1:
        raise ValueError("CHAT_ANSWER_BATCH_AMBIGUOUS")
    return copy.deepcopy(candidates[0])


def _validate_identity(batch: dict[str, Any], work: dict[str, Any], *, actor_id: str, now: datetime) -> None:
    required = ("batch_id", "work_definition_id", "tenant_id", "owner_id", "session_id", "channel_mode", "revision", "round_number")
    if any(batch.get(name) in (None, "") for name in required):
        raise ValueError("CHAT_ANSWER_BATCH_INVALID")
    if str(batch.get("owner_id") or "") != actor_id:
        raise ValueError("CHAT_ANSWER_ACTOR_MISMATCH")
    if str(batch.get("channel_mode") or "") not in ALLOWED_CHANNELS:
        raise ValueError("CHAT_ANSWER_CHANNEL_INVALID")
    if not isinstance(work, dict):
        raise ValueError("CHAT_ANSWER_WORK_NOT_FOUND")
    for name in ("work_definition_id", "tenant_id", "owner_id", "session_id", "channel_mode", "revision"):
        if str(work.get(name) or "") != str(batch.get(name) or ""):
            raise ValueError("CHAT_ANSWER_WORK_MISMATCH")
    try:
        if int(batch.get("round_number")) not in {1, 2, 3} or int(batch.get("revision")) < 0:
            raise ValueError("CHAT_ANSWER_BATCH_INVALID")
        deadline = _utc(batch.get("answer_deadline_at") or batch.get("expires_at"))
    except (TypeError, ValueError) as exc:
        if str(exc) == "CHAT_ANSWER_BATCH_INVALID":
            raise
        raise ValueError("CHAT_ANSWER_BATCH_EXPIRY_INVALID") from exc
    if now >= deadline:
        raise ValueError("CHAT_ANSWER_BATCH_EXPIRED")


def build_chat_answer_resume(
    answer_text_value: Any,
    *,
    employee_id: Any,
    mongodb_uri: Any,
    mongo_database: Any,
    tenant_id: Any = "default",
    work_collection: Any = "work_definitions",
    batch_collection: Any = "clarification_batches",
    timeout_ms: Any = 5000,
    now_utc: Any = "",
    client_factory: Callable[..., Any] = MongoClient,
) -> dict[str, Any]:
    """Resolve a numbered reply to one authoritative pending question batch."""

    trace_id = f"trace-{uuid.uuid4()}"
    try:
        payload = _payload(answer_text_value)
        reply = _text(payload.get("answer_text") or payload.get("text"), MAX_REPLY_CHARS)
        actor_id = _text(employee_id)
        uri = _secret(mongodb_uri)
        database_name = _text(mongo_database)
        scope = _text(tenant_id) or "default"
        work_name = _safe_collection(work_collection, "work_definitions")
        batch_name = _safe_collection(batch_collection, "clarification_batches")
        timeout = max(1_000, min(int(timeout_ms), 30_000))
        now = _utc(now_utc)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _failure("CHAT_ANSWER_RESUME_INPUT_INVALID", "채팅 답변 재개 입력 또는 환경 설정이 올바르지 않습니다.", trace_id)
    if not reply:
        return _failure("CHAT_ANSWER_TEXT_REQUIRED", "채팅창에 번호형 답변을 입력해 주세요.", trace_id)
    if not _ID_PATTERN.fullmatch(actor_id):
        return _failure("CHAT_ANSWER_ACTOR_INVALID", "답변자 사번 형식이 올바르지 않습니다.", trace_id)
    if not _ID_PATTERN.fullmatch(scope):
        return _failure("CHAT_ANSWER_SCOPE_INVALID", "카탈로그 scope 설정이 올바르지 않습니다.", trace_id)
    if not uri or not database_name or not work_name or not batch_name:
        return _failure("CHAT_ANSWER_RESUME_CONFIG_MISSING", "채팅 답변 재개용 MongoDB 설정이 필요합니다.", trace_id)

    explicit_batch_id, ambiguous_ids = _batch_id_from_reply(reply)
    if ambiguous_ids:
        return _failure("CHAT_ANSWER_BATCH_ID_AMBIGUOUS", "답변에는 질문 묶음 ID를 하나만 넣어 주세요.", trace_id)
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
        batch = _find_pending_batch(database[batch_name], tenant_id=scope, actor_id=actor_id, explicit_batch_id=explicit_batch_id)
        work = database[work_name].find_one(
            {"tenant_id": batch.get("tenant_id"), "work_definition_id": batch.get("work_definition_id")}
        )
        _validate_identity(batch, work, actor_id=actor_id, now=now)
    except (ConfigurationError, ConnectionFailure, ServerSelectionTimeoutError):
        return _failure("MONGODB_UNAVAILABLE", "MongoDB에 연결할 수 없습니다.", trace_id, retryable=True)
    except PyMongoError as exc:
        return _failure("CHAT_ANSWER_RESUME_MONGODB_FAILED", "채팅 답변 재개 정보를 MongoDB에서 읽지 못했습니다.", trace_id, {"exception_type": type(exc).__name__}, retryable=True)
    except ValueError as exc:
        code = str(exc) or "CHAT_ANSWER_RESUME_INVALID"
        messages = {
            "CHAT_ANSWER_BATCH_NOT_FOUND": "현재 사번으로 답변을 기다리는 질문이 없습니다. 질문 안내에서 질문 묶음 ID를 확인해 주세요.",
            "CHAT_ANSWER_BATCH_NOT_PENDING": "이 질문 묶음은 이미 처리됐거나 답변 대기 상태가 아닙니다.",
            "CHAT_ANSWER_BATCH_AMBIGUOUS": "답변 대기 중인 질문이 여러 개입니다. 안내문에 표시된 `질문 묶음: qb-...` 줄을 답변 맨 위에 붙여 주세요.",
            "CHAT_ANSWER_BATCH_INVALID": "저장된 질문 묶음의 식별자 또는 회차 정보가 올바르지 않습니다.",
            "CHAT_ANSWER_BATCH_EXPIRY_INVALID": "저장된 질문 묶음의 응답 기한 형식이 올바르지 않습니다.",
            "CHAT_ANSWER_BATCH_EXPIRED": "질문 응답 기한이 지나 새 업무 정의 실행이 필요합니다.",
            "CHAT_ANSWER_WORK_NOT_FOUND": "질문에 연결된 업무 정의를 찾을 수 없습니다.",
            "CHAT_ANSWER_WORK_MISMATCH": "질문 묶음과 현재 업무 정의의 revision 또는 식별자가 다릅니다.",
            "CHAT_ANSWER_ACTOR_MISMATCH": "질문을 요청한 사번과 답변자 사번이 다릅니다.",
            "CHAT_ANSWER_CHANNEL_INVALID": "이 질문 묶음은 채팅 답변 재개 경로에서 처리할 수 없습니다.",
        }
        return _failure(code, messages.get(code, "채팅 답변 재개를 준비할 수 없습니다."), trace_id)
    finally:
        if client is not None:
            client.close()

    safe_batch = _json_safe(batch)
    safe_work = _json_safe(work)
    request_material = json.dumps({"batch_id": safe_batch["batch_id"], "actor_id": actor_id, "reply": reply}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    request_id = "chat-answer-" + hashlib.sha256(request_material.encode("utf-8")).hexdigest()[:24]
    context = {
        "work_definition": safe_work,
        # Component 39 independently recomputes completeness after the merge.
        # It only needs this immutable identity/revision projection here to
        # protect the durable answer-resume boundary.
        "completeness": {
            "work_definition_id": safe_work["work_definition_id"],
            "tenant_id": safe_work["tenant_id"],
            "session_id": safe_work["session_id"],
            "revision": safe_work["revision"],
        },
        "round_number": int(safe_batch["round_number"]),
    }
    return {
        "ok": True,
        "status": "CHAT_ANSWER_READY",
        "route": "success_path",
        "artifact_refs": [
            {"kind": "clarification_batch", "id": safe_batch["batch_id"]},
            {"kind": "work_definition", "id": safe_work["work_definition_id"], "revision": safe_work["revision"]},
        ],
        "clarification_context": context,
        "clarification_batch": safe_batch,
        "answer_text": reply,
        "actor_id": actor_id,
        "request_id": request_id,
        "trace_id": trace_id,
    }


class F10ChatAnswerResumeLoaderComponent(Component):
    display_name = "47 채팅 답변 재개 준비"
    description = "Playground 채팅의 `1번: 답변`을 현재 사번의 대기 중 질문 Batch와 안전하게 연결합니다. 질문 묶음 ID를 포함하면 여러 동시 작업도 구분합니다."
    icon = "MessageSquareText"
    name = "F10ChatAnswerResumeLoader"

    inputs = [
        MessageTextInput(
            name="answer_text",
            display_name="채팅 답변 (자동 연결)",
            value="",
            input_types=["Message", "Data", "JSON"],
            required=True,
            info="Playground Chat Input의 메시지를 연결합니다. `질문 묶음: qb-...`와 `1번: ...` 형식을 사용합니다.",
        ),
        MessageTextInput(
            name="employee_id",
            display_name="답변자 사번",
            value="employee-demo",
            required=True,
            info="질문 Batch를 만든 사번과 같아야 합니다. 운영에서는 trusted gateway가 주입한 사번을 사용합니다.",
        ),
        SecretStrInput(
            name="mongodb_uri",
            display_name="MongoDB URI (환경 설정)",
            required=True,
            info="질문 Batch와 WorkDefinition을 다시 읽는 공통 Secret입니다.",
        ),
        MessageTextInput(
            name="mongo_database",
            display_name="MongoDB Database (환경 설정)",
            value="business_work_design",
            required=True,
        ),
        MessageTextInput(name="tenant_id", display_name="Catalog Scope (내부값)", value="default", advanced=True),
        MessageTextInput(name="work_collection", display_name="WorkDefinition Collection", value="work_definitions", advanced=True),
        MessageTextInput(name="batch_collection", display_name="질문 Batch Collection", value="clarification_batches", advanced=True),
        IntInput(name="timeout_ms", display_name="MongoDB Timeout(ms)", value=5000, advanced=True),
        MessageTextInput(name="now_utc", display_name="기준 시각(ISO-8601)", value="", advanced=True),
    ]
    outputs = [
        Output(name="success_path", display_name="채팅 답변 준비 완료", method="route_resume", types=["Data"], group_outputs=True),
        Output(name="blocked_path", display_name="채팅 답변 재개 차단", method="route_resume", types=["Data"], group_outputs=True),
    ]

    def _component_id(self) -> str:
        return _text(getattr(self, "_id", "")) or self.name

    def _result(self) -> dict[str, Any]:
        result = getattr(self, "_resume_result", None)
        if isinstance(result, dict):
            return result
        result = build_chat_answer_resume(
            getattr(self, "answer_text", None),
            employee_id=getattr(self, "employee_id", ""),
            mongodb_uri=getattr(self, "mongodb_uri", ""),
            mongo_database=getattr(self, "mongo_database", ""),
            tenant_id=getattr(self, "tenant_id", "default"),
            work_collection=getattr(self, "work_collection", "work_definitions"),
            batch_collection=getattr(self, "batch_collection", "clarification_batches"),
            timeout_ms=getattr(self, "timeout_ms", 5000),
            now_utc=getattr(self, "now_utc", ""),
            client_factory=MongoClient,
        )
        self._resume_result = result
        return result

    def route_resume(self) -> Data:
        result = self._result()
        selected = str(result.get("route") or "blocked_path")
        output_names = ("success_path", "blocked_path")
        for output_name in output_names:
            if output_name != selected:
                self.stop(output_name)
        graph = getattr(self, "graph", None)
        exclude = getattr(graph, "exclude_branches_conditionally", None) if graph is not None else None
        if callable(exclude):
            exclude(self._component_id(), [name for name in output_names if name != selected])
        self.status = {"ok": result.get("ok"), "status": result.get("status"), "route": selected}
        current_output = str(getattr(self, "_current_output", "") or "")
        if current_output in output_names and current_output != selected:
            return Data(data={})
        return Data(data=copy.deepcopy(result))
