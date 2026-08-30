from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from lfx.custom import Component
from lfx.io import BoolInput, DataInput, DropdownInput, IntInput, MessageTextInput, Output, SecretStrInput
from lfx.schema import Data, Message
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
    "review_and_request_approval": "WAITING_APPROVAL",
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
) -> tuple[dict[str, Any], dict[str, Any]]:
    current_state = str((current or {}).get("status") or "INTAKE")
    document = copy.deepcopy(incoming)
    document.pop("_id", None)
    document.pop("pending_action", None)
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
    if command in {"approve", "request_approval", "review_and_request_approval"} and not preview_hash:
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
        now = _utc(now_utc)
        timeout = max(1000, min(int(timeout_ms), 30_000))
        transactions_required = _as_bool(require_transactions, True)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _failure("WORK_STORE_INPUT_INVALID", "WorkDefinition 저장 입력을 해석할 수 없습니다.", safe_trace)
    missing = [key for key in ("work_definition_id", "tenant_id", "owner_id", "session_id", "revision", "status", "channel_mode") if incoming.get(key) in (None, "")]
    config_missing = [name for name, val in (("mongodb_uri", uri), ("mongo_database", database_name), ("actor_id", actor), ("idempotency_key", idem)) if not val]
    if missing or config_missing or expected < 0 or selected_command not in ({"save"} | set(COMMAND_STATES)):
        return _failure("WORK_STORE_INPUT_INVALID", "저장 계약 또는 production MongoDB 설정이 유효하지 않습니다.", safe_trace, {"missing_fields": missing + config_missing, "command": selected_command})
    if str(incoming.get("channel_mode")) != "native_hitl":
        return _failure("WORK_CHANNEL_INVALID", "WorkDefinition 저장은 native_hitl channel만 지원합니다.", safe_trace, {"allowed": ["native_hitl"]})
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", work_name) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", event_name):
        return _failure("WORK_STORE_COLLECTION_INVALID", "MongoDB collection 이름이 허용 형식이 아닙니다.", safe_trace)

    request_source = copy.deepcopy(incoming)
    request_source.pop("mutation_receipts", None)
    request_source.pop("last_event", None)
    request_hash = hashlib.sha256(
        _canonical(
            {
                "command": selected_command,
                "expected_revision": expected,
                "work_definition": request_source,
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
                )
                result = definitions.replace_one({**identity, "revision": expected}, document, session=session)
                if int(getattr(result, "matched_count", 0)) != 1:
                    raise ValueError("REVISION_CONFLICT")
            else:
                # A review can reach this component without an earlier HITL
                # answer round.  In that case there is no durable document
                # yet, but the validated Preview is already READY_FOR_REVIEW.
                # Allow the single ``request_approval`` operation to create
                # revision 0 directly, rather than forcing a visible
                # ``save -> request_approval`` pair on the Canvas.  The
                # explicit review-and-request command also leaves a single,
                # readable audit event for this combined Flow action.
                if (
                    expected != 0
                    or selected_command not in {"save", "request_approval", "review_and_request_approval"}
                    or int(incoming.get("revision", -1)) != 0
                ):
                    raise ValueError("REVISION_CONFLICT")
                if selected_command in {"request_approval", "review_and_request_approval"} and str(incoming.get("status") or "") != "READY_FOR_REVIEW":
                    raise ValueError("WORK_STATE_TRANSITION_INVALID")
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
            },
            "trace_id": safe_trace,
        }
    except ValueError as exc:
        code = str(exc)
        messages = {
            "REVISION_CONFLICT": "저장된 WorkDefinition revision이 expected_revision과 다릅니다.",
            "IDEMPOTENCY_KEY_REUSED": "같은 idempotency key가 다른 요청에 사용되었습니다.",
            "WORK_CHANNEL_SESSION_MISMATCH": "저장된 작업의 owner, session 또는 native HITL channel을 변경할 수 없습니다.",
            "WORK_INCOMING_REVISION_INVALID": "입력 WorkDefinition revision은 expected_revision 또는 그 다음 revision이어야 합니다.",
            "WORK_STATE_INVALID": "저장하려는 업무 상태가 유효하지 않습니다.",
            "WORK_STATE_TRANSITION_INVALID": "허용되지 않은 업무 상태 전이입니다.",
            "PREVIEW_HASH_REQUIRED": "승인하려면 현재 preview_hash가 필요합니다.",
            "ACTION_ACTOR_MISMATCH": "승인 action actor가 durable WorkDefinition owner와 일치하지 않습니다.",
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
    display_name = "18 업무 정의 상태 저장"
    description = "업무 정의를 MongoDB의 내부 고정 컬렉션에 저장하고 상태 변경 이력을 남깁니다. revision과 중복 실행 방지는 자동 처리할 수 있습니다."
    icon = "Database"
    name = "WorkDefinitionStore"

    inputs = [
        DataInput(
            name="work_definition",
            display_name="업무 정의 (자동 연결)",
            input_types=["Data", "JSON"],
            required=True,
            info="앞 단계에서 검증된 WorkDefinition이 자동으로 전달됩니다. 직접 입력하지 않습니다.",
        ),
        IntInput(
            name="expected_revision",
            display_name="현재 Revision (자동 계산)",
            value=0,
            required=False,
            advanced=True,
            info="동시 저장 충돌을 막는 비교값입니다. F10에서는 WorkDefinition에서 자동 계산합니다.",
        ),
        BoolInput(name="derive_expected_revision", display_name="WorkDefinition Revision 자동 사용", value=True, advanced=True),
        BoolInput(
            name="incoming_revision_is_next",
            display_name="입력 Revision이 저장값의 다음 Revision",
            value=False,
            advanced=True,
            info="Answer Merger처럼 revision을 먼저 증가시킨 입력을 저장할 때만 사용합니다.",
        ),
        DropdownInput(
            name="command",
            display_name="상태 처리 명령",
            options=["save", "review_and_request_approval", "request_approval", "accept_assumptions", "approve", "request_changes", "reject", "cancel"],
            value="save",
            info="각 Flow 노드에 미리 정해진 처리 단계입니다. 일반 실행 중 변경하지 않습니다.",
        ),
        DataInput(
            name="route_trigger",
            display_name="상태 전이 신호 (자동 연결)",
            input_types=["Data", "JSON", "Message"],
            required=False,
            # This input receives Human Input branch edges.  Marking it
            # advanced makes Langflow 1.11.1 prune those edges on import.
            advanced=False,
            info="Human Input 또는 조건 분기에서 자동 연결되는 실행 신호입니다. 저장 내용에는 포함되지 않습니다.",
        ),
        MessageTextInput(
            name="actor_id",
            display_name="사번 (자동 연결)",
            required=True,
            info="시작 단계의 employee_id가 자동 전달됩니다. 업무 정의 owner와 일치해야 합니다.",
        ),
        MessageTextInput(
            name="idempotency_key",
            display_name="중복 실행 방지 키 (자동 생성)",
            required=False,
            advanced=True,
            info="응답 유실 후 재시도되어도 같은 저장을 한 번만 처리하도록 만드는 내부 키입니다.",
        ),
        BoolInput(name="derive_idempotency_key", display_name="중복 실행 방지 키 자동 생성", value=True, advanced=True),
        SecretStrInput(
            name="mongodb_uri",
            display_name="MongoDB URI (환경 설정)",
            required=True,
            info="운영 환경의 Secret/Global Variable로 설정합니다. 업무 실행마다 바꾸지 않습니다.",
        ),
        MessageTextInput(
            name="mongo_database",
            display_name="MongoDB Database (환경 설정)",
            value="business_work_design",
            required=True,
            info="기본값은 business_work_design입니다. 컬렉션은 내부 고정값을 사용합니다.",
        ),
        MessageTextInput(name="now_utc", display_name="기준 시각(ISO-8601)", value="", advanced=True),
        IntInput(name="timeout_ms", display_name="MongoDB Timeout(ms)", value=5000, advanced=True),
        BoolInput(name="require_transactions", display_name="Transaction 필수", value=True, advanced=True),
        MessageTextInput(name="trace_id", display_name="Trace ID", value="", advanced=True),
    ]
    outputs = [
        Output(name="stored_work_definition", display_name="저장 결과", method="store_definition", types=["Data"]),
        Output(name="success_path", display_name="저장 성공", method="route_store", types=["Data"], group_outputs=True),
        Output(name="blocked_path", display_name="저장 차단", method="route_store", types=["Data"], group_outputs=True),
        Output(name="stored_work_message", display_name="저장 상태 메시지", method="build_store_message", types=["Message"]),
    ]

    def _result(self) -> dict[str, Any]:
        """Write at most once when both the Data and Message outputs are used."""

        cached = getattr(self, "_store_result_cache", None)
        if isinstance(cached, dict):
            return cached
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
            # Keep the operational collections fixed for this Flow.  The
            # names are intentionally not exposed as Canvas inputs: changing
            # one state node independently would split the audit trail.
            work_collection="work_definitions",
            event_collection="work_definition_events",
            now_utc=getattr(self, "now_utc", ""),
            timeout_ms=getattr(self, "timeout_ms", 5000),
            require_transactions=getattr(self, "require_transactions", True),
            trace_id=getattr(self, "trace_id", ""),
            client_factory=MongoClient,
        )
        self._store_result_cache = result
        self.status = {"ok": result["ok"], "status": result["status"], "revision": (result.get("store_result") or {}).get("revision")}
        return result

    def store_definition(self) -> Data:
        return Data(data=self._result())

    def _component_id(self) -> str:
        return str(getattr(self, "_id", "") or self.name)[:200]

    def _select_output_route(self, selected: str) -> None:
        output_names = ("success_path", "blocked_path")
        non_selected = [output_name for output_name in output_names if output_name != selected]
        for output_name in non_selected:
            self.stop(output_name)
        if selected == "blocked_path":
            self.stop("stored_work_message")
            non_selected.append("stored_work_message")
        graph = getattr(self, "graph", None)
        exclude = getattr(graph, "exclude_branches_conditionally", None) if graph is not None else None
        if callable(exclude):
            exclude(self._component_id(), non_selected)

    def _is_nonselected_group_output(self, selected: str) -> bool:
        current_output = str(getattr(self, "_current_output", "") or "")
        return bool(current_output and current_output in {"success_path", "blocked_path"} and current_output != selected)

    def route_store(self) -> Data:
        result = self._result()
        selected = "success_path" if result.get("ok") is True else "blocked_path"
        self._select_output_route(selected)
        if self._is_nonselected_group_output(selected):
            return Data(data={})
        return Data(data=result)

    def build_store_message(self) -> Message:
        result = self._result()
        if result.get("ok") is not True:
            self.stop("stored_work_message")
            return Message(text="")
        work = result.get("work_definition") if isinstance(result.get("work_definition"), dict) else {}
        command = str(getattr(self, "command", "save") or "save")
        text = (
            f"업무 정의가 저장되었습니다. 상태: {str(result.get('status') or '')[:80]}, "
            f"revision: {str(work.get('revision') or '')[:30]}."
        )
        if command == "review_and_request_approval":
            text += " 검토본을 저장하고 승인 대기 상태로 전환했습니다."
        elif command == "request_approval":
            text += " 아래 업무 설계를 검토한 뒤 Approve, Reject 또는 Cancel을 선택하세요."
        return Message(text=text)
