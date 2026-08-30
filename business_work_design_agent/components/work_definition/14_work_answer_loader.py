from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from lfx.custom import Component
from lfx.io import DataInput, DropdownInput, IntInput, MessageTextInput, Output, SecretStrInput
from lfx.schema import Data
from pymongo import MongoClient
from pymongo.errors import PyMongoError


ALLOWED_CHANNELS = {"native_hitl"}
ALLOWED_ANSWER_TYPES = {"text", "single_choice", "single_choice_with_text", "multi_choice", "boolean", "number"}
MAX_ANSWER_VALUE_BYTES = 64 * 1024
MAX_FREE_TEXT_CHARS = 16_000


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


_ANSWER_API_OPENER = build_opener(_NoRedirectHandler).open


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return copy.deepcopy(value)
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return copy.deepcopy(data)
    text = getattr(value, "text", value if isinstance(value, str) else "")
    if isinstance(text, str) and text.strip():
        return json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE))
    return {}


def _named(value: Any, *keys: str) -> dict[str, Any]:
    payload = _payload(value)
    for key in keys:
        nested = payload.get(key)
        if isinstance(nested, dict):
            return copy.deepcopy(nested)
    return payload


def _utc(value: Any) -> datetime:
    text = str(value or "").strip()
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00")) if text else datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _failure(code: str, message: str, trace_id: str, details: dict[str, Any] | None = None, *, retryable: bool = False) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "BLOCKED",
        "artifact_refs": [],
        "error": {"code": code, "message": message, "retryable": retryable, "details": details or {}},
        "resume": None,
        "trace_id": trace_id,
    }


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _normalize_answer_value(question: dict[str, Any], value: Any) -> Any:
    answer_type = str(question.get("answer_type") or "text")
    if answer_type not in ALLOWED_ANSWER_TYPES:
        raise ValueError("ANSWER_VALUE_TYPE_INVALID")
    raw_choices = question.get("choices", [])
    if not isinstance(raw_choices, list):
        raise ValueError("ANSWER_CHOICE_INVALID")
    choices = [str(item) for item in raw_choices if isinstance(item, str)]
    required = bool(question.get("required", True))
    if value in (None, "", []):
        if required:
            raise ValueError("ANSWER_REQUIRED_VALUE_MISSING")
        return None
    if answer_type == "text":
        if not isinstance(value, str) or len(value) > MAX_FREE_TEXT_CHARS:
            raise ValueError("ANSWER_VALUE_TYPE_INVALID")
        normalized: Any = value
    elif answer_type == "single_choice":
        if not isinstance(value, str) or value not in choices:
            raise ValueError("ANSWER_CHOICE_INVALID")
        normalized = value
    elif answer_type == "single_choice_with_text":
        if isinstance(value, str) and value in choices:
            normalized = {"choice": value, "text": ""}
        elif isinstance(value, dict) and set(value) <= {"choice", "text"}:
            choice = value.get("choice")
            text = value.get("text", "")
            if not isinstance(choice, str) or not isinstance(text, str) or len(text) > MAX_FREE_TEXT_CHARS:
                raise ValueError("ANSWER_VALUE_TYPE_INVALID")
            if choice == "__other__":
                if not text.strip():
                    raise ValueError("ANSWER_REQUIRED_VALUE_MISSING")
            elif choice not in choices:
                raise ValueError("ANSWER_CHOICE_INVALID")
            normalized = {"choice": choice, "text": text}
        else:
            raise ValueError("ANSWER_VALUE_TYPE_INVALID")
    elif answer_type == "multi_choice":
        if not isinstance(value, list) or not value or any(not isinstance(item, str) or item not in choices for item in value):
            raise ValueError("ANSWER_CHOICE_INVALID")
        normalized = list(dict.fromkeys(value))
    elif answer_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError("ANSWER_VALUE_TYPE_INVALID")
        normalized = value
    else:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or abs(float(value)) > 1e15:
            raise ValueError("ANSWER_VALUE_TYPE_INVALID")
        normalized = value
    if len(_canonical(normalized).encode("utf-8")) > MAX_ANSWER_VALUE_BYTES:
        raise ValueError("ANSWER_VALUE_TOO_LARGE")
    return normalized


def load_work_answers(
    work_value: Any,
    batch_value: Any,
    *,
    channel_mode: Any,
    native_form_payload: Any = None,
    human_action: Any = "",
    now_utc: Any = "",
) -> dict[str, Any]:
    trace_id = f"trace-{uuid.uuid4()}"
    try:
        work = _named(work_value, "work_definition")
        batch = _named(batch_value, "clarification_batch")
        native = _payload(native_form_payload)
        now = _utc(now_utc)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _failure("ANSWER_PAYLOAD_INVALID", "답변 payload를 해석할 수 없습니다.", trace_id)

    channel = str(channel_mode or "").strip().lower()
    if channel not in ALLOWED_CHANNELS:
        return _failure("ANSWER_CHANNEL_INVALID", "지원하지 않는 답변 channel입니다.", trace_id, {"allowed": sorted(ALLOWED_CHANNELS)})
    if not native:
        return _failure("ANSWER_CHANNEL_PAYLOAD_MISSING", "선택한 channel의 답변 payload가 없습니다.", trace_id)
    if any(str(work.get(key, "")) != str(batch.get(key, "")) for key in ("work_definition_id", "tenant_id", "owner_id", "session_id", "channel_mode")):
        return _failure("ANSWER_BATCH_IDENTITY_MISMATCH", "질문 batch와 WorkDefinition 식별자 또는 channel이 다릅니다.", trace_id)
    if str(work.get("channel_mode")) != channel:
        return _failure("ANSWER_WORK_CHANNEL_MISMATCH", "기존 작업의 channel을 다른 channel로 전환할 수 없습니다.", trace_id)

    selected = native
    selected_channel = str(selected.get("channel_mode") or channel).strip().lower()
    command = str(selected.get("command") or selected.get("action") or human_action or "").strip().lower()
    if selected_channel != channel or command != "submit_answers":
        return _failure("ANSWER_ACTION_INVALID", "답변 제출은 현재 channel의 submit_answers action이어야 합니다.", trace_id, {"command": command})
    if str(human_action or "").strip().lower() != "submit_answers":
        return _failure("ANSWER_HITL_ACTION_REQUIRED", "F10에서는 Human Input의 submit_answers action 확인이 필요합니다.", trace_id)

    expected_identity = {
        "work_definition_id": work.get("work_definition_id"),
        "batch_id": batch.get("batch_id"),
        "session_id": work.get("session_id"),
    }
    identity_mismatch = [key for key, expected in expected_identity.items() if str(selected.get(key, "")) != str(expected)]
    if identity_mismatch:
        return _failure("ANSWER_PAYLOAD_IDENTITY_MISMATCH", "답변 payload가 현재 작업·batch·session과 일치하지 않습니다.", trace_id, {"fields": identity_mismatch})
    try:
        submitted_revision = int(selected.get("expected_revision"))
        work_revision = int(work.get("revision"))
        batch_revision = int(batch.get("revision"))
    except (TypeError, ValueError):
        return _failure("ANSWER_REVISION_INVALID", "expected_revision이 올바르지 않습니다.", trace_id)
    if submitted_revision != work_revision or batch_revision != work_revision:
        return _failure("REVISION_CONFLICT", "답변 대상 revision이 최신 WorkDefinition과 다릅니다.", trace_id, {"expected_revision": submitted_revision, "current_revision": work_revision})
    batch_status = str(batch.get("status"))
    if batch_status not in {"WAITING_ANSWER", "ANSWERED"}:
        return _failure("ANSWER_BATCH_NOT_PENDING", "답변을 기다리는 batch가 아닙니다.", trace_id)
    try:
        answer_deadline = _utc(batch.get("answer_deadline_at") or batch.get("expires_at"))
    except (TypeError, ValueError):
        return _failure("ANSWER_BATCH_EXPIRY_INVALID", "질문 batch의 만료 시각이 올바르지 않습니다.", trace_id)
    if batch_status == "ANSWERED":
        # Store/API modes authenticate the persisted submission. A resume may
        # happen after the answer deadline; what matters is that the durable
        # submitted_at timestamp was on time.
        try:
            durable_submitted_at = _utc(selected.get("submitted_at"))
        except (TypeError, ValueError):
            return _failure("ANSWER_SUBMITTED_AT_INVALID", "저장된 답변 제출 시각이 올바르지 않습니다.", trace_id)
        if durable_submitted_at >= answer_deadline:
            return _failure("ANSWER_BATCH_EXPIRED", "답변이 질문 batch 마감 이후 제출되었습니다.", trace_id)
    elif now >= answer_deadline:
        # Direct-payload mode is intentionally test-only and cannot
        # backdate an untrusted submitted_at value to bypass the deadline.
        return _failure("ANSWER_BATCH_EXPIRED", "질문 batch가 만료되었습니다.", trace_id)

    idempotency_key = str(selected.get("idempotency_key") or "").strip()[:300]
    if not idempotency_key:
        return _failure("ANSWER_IDEMPOTENCY_KEY_REQUIRED", "중복 제출 방지를 위한 idempotency_key가 필요합니다.", trace_id)
    raw_answers = selected.get("answers")
    if isinstance(raw_answers, dict):
        raw_answers = [{"question_id": key, "value": value} for key, value in raw_answers.items()]
    if not isinstance(raw_answers, list):
        return _failure("ANSWER_LIST_INVALID", "answers는 question_id와 value를 가진 목록 또는 object여야 합니다.", trace_id)

    question_by_id = {str(item.get("question_id")): item for item in batch.get("questions", []) if isinstance(item, dict) and item.get("question_id")}
    normalized_answers: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_answers[:100]:
        if not isinstance(item, dict):
            return _failure("ANSWER_ITEM_INVALID", "각 답변은 object여야 합니다.", trace_id)
        question_id = str(item.get("question_id") or "")
        if question_id not in question_by_id or question_id in seen:
            return _failure("ANSWER_QUESTION_INVALID", "알 수 없거나 중복된 question_id가 있습니다.", trace_id, {"question_id": question_id})
        question = question_by_id[question_id]
        try:
            value = _normalize_answer_value(question, copy.deepcopy(item.get("value")))
        except ValueError as exc:
            code = str(exc)
            messages = {
                "ANSWER_REQUIRED_VALUE_MISSING": "필수 질문의 답변이 없습니다.",
                "ANSWER_CHOICE_INVALID": "선택형 답변이 허용된 선택지와 일치하지 않습니다.",
                "ANSWER_VALUE_TYPE_INVALID": "답변 형식이 질문의 answer_type과 일치하지 않습니다.",
                "ANSWER_VALUE_TOO_LARGE": "답변 값이 허용 크기를 초과했습니다.",
            }
            return _failure(code, messages.get(code, "답변 값이 유효하지 않습니다."), trace_id, {"question_id": question_id})
        normalized_answers.append(
            {
                "question_id": question_id,
                "value": value,
                "target_paths": copy.deepcopy(question.get("target_paths", [])),
                "reason_code": question.get("reason_code"),
                "resolve_conflict": bool(item.get("resolve_conflict", False)),
                "evidence_turn_id": str(item.get("evidence_turn_id") or selected.get("turn_id") or f"answer-{question_id}")[:200],
            }
        )
        seen.add(question_id)
    missing_required = [question_id for question_id, question in question_by_id.items() if question.get("required", True) and question_id not in seen]
    if missing_required:
        return _failure("ANSWER_REQUIRED_QUESTIONS_MISSING", "필수 질문 일부가 제출되지 않았습니다.", trace_id, {"question_ids": missing_required})

    receipt_material = json.dumps({"batch_id": batch["batch_id"], "idempotency_key": idempotency_key, "answers": normalized_answers}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    submission = {
        "schema_version": "work-answer-submission/v1",
        "submission_id": f"answer-{hashlib.sha256(receipt_material.encode('utf-8')).hexdigest()[:24]}",
        "idempotency_key": idempotency_key,
        "channel_mode": channel,
        "work_definition_id": work["work_definition_id"],
        "tenant_id": work["tenant_id"],
        "session_id": work["session_id"],
        "batch_id": batch["batch_id"],
        "expected_revision": work_revision,
        "answers": normalized_answers,
        "submitted_at": str(selected.get("submitted_at") or now.isoformat().replace("+00:00", "Z")),
        "payload_sha256": hashlib.sha256(receipt_material.encode("utf-8")).hexdigest(),
    }
    return {
        "ok": True,
        "status": "MERGING",
        "artifact_refs": [{"kind": "answer_submission", "id": submission["submission_id"]}],
        "answer_submission": submission,
        "trace_id": trace_id,
    }


def _secret(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    return str(getter() if callable(getter) else value or "")


def _validate_loaded_batch(
    work: dict[str, Any],
    stored: dict[str, Any],
    *,
    channel_mode: Any,
    native_form_payload: Any,
    human_action: Any,
    now_utc: Any,
    source: str,
) -> dict[str, Any]:
    trace_id = f"trace-{uuid.uuid4()}"
    canonical_batch = copy.deepcopy(stored)
    canonical_batch.pop("_id", None)
    canonical_batch["created_at"] = str(canonical_batch.get("created_at") or "")
    canonical_batch["expires_at"] = str(canonical_batch.get("expires_at") or "")
    canonical_batch["answer_deadline_at"] = str(
        canonical_batch.get("answer_deadline_at") or canonical_batch.get("expires_at") or ""
    )
    channel = str(channel_mode or "").lower()
    if channel != "native_hitl":
        return _failure("ANSWER_CHANNEL_INVALID", "지원하지 않는 답변 channel입니다.", trace_id, {"allowed": sorted(ALLOWED_CHANNELS)})
    if str(canonical_batch.get("status")) != "ANSWERED" or canonical_batch.get("answers") is None:
        return _failure("ANSWER_FORM_NOT_SUBMITTED", "Answer Form의 자유서술 답변이 아직 저장되지 않았습니다.", trace_id)
    try:
        supplied_ref = _payload(native_form_payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _failure("ANSWER_FORM_REFERENCE_INVALID", "Answer Form 참조 payload를 해석할 수 없습니다.", trace_id)
    native_payload = {
        "channel_mode": "native_hitl",
        "command": "submit_answers",
        "work_definition_id": work["work_definition_id"],
        "batch_id": canonical_batch.get("batch_id"),
        "session_id": work["session_id"],
        "expected_revision": canonical_batch.get("revision"),
        "idempotency_key": canonical_batch.get("answer_idempotency_key"),
        "answers": copy.deepcopy(canonical_batch.get("answers")),
        "submitted_at": str(canonical_batch.get("answered_at") or ""),
        "turn_id": str(canonical_batch.get("answer_turn_id") or supplied_ref.get("turn_id") or ""),
    }
    # Human Input branch outputs carry the rendered prompt, not the action_id.
    # Reaching this store/API loader through a non-empty connected branch is the
    # execution proof; the persisted payload itself is forced to
    # command=submit_answers above. Direct-payload mode does not pass through
    # this adapter and still requires the literal action_id.
    canonical_human_action = "submit_answers" if str(human_action or "").strip() else human_action
    result = load_work_answers(
        work,
        canonical_batch,
        channel_mode=channel,
        native_form_payload=native_payload,
        human_action=canonical_human_action,
        now_utc=now_utc,
    )
    if result.get("ok"):
        result["store_result"] = {"batch_loaded": True, "source": source}
    return result


def _batch_from_api_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("clarification_batch"), dict):
        batch = copy.deepcopy(payload["clarification_batch"])
    elif isinstance(payload.get("batch"), dict):
        batch = copy.deepcopy(payload["batch"])
    else:
        batch = copy.deepcopy(payload)
    answer = payload.get("answer_submission") if isinstance(payload.get("answer_submission"), dict) else None
    if answer is None and isinstance(batch.get("answer_submission"), dict):
        answer = batch.get("answer_submission")
    if answer is not None:
        batch["answers"] = copy.deepcopy(answer.get("answers"))
        batch["answer_idempotency_key"] = answer.get("idempotency_key")
        batch["answered_at"] = answer.get("submitted_at")
        batch["answer_turn_id"] = answer.get("turn_id")
        batch["status"] = "ANSWERED"
    elif str(batch.get("status")) in {"ANSWERED_PENDING_RESUME", "RESUMED"} and batch.get("answers") is not None:
        batch["status"] = "ANSWERED"
    batch.pop("answer_submission", None)
    return batch


def load_work_answers_from_companion_api(
    work_value: Any,
    batch_value: Any,
    *,
    channel_mode: Any,
    native_form_payload: Any = None,
    human_action: Any = "",
    now_utc: Any = "",
    answer_api_base_url: Any,
    answer_api_token: Any,
    timeout_seconds: Any = 10,
    max_response_bytes: Any = 1_000_000,
    opener: Any = None,
) -> dict[str, Any]:
    """Fetch one authenticated Answer Form record without relying on HITL action payload text."""
    trace_id = f"trace-{uuid.uuid4()}"
    try:
        work = _named(work_value, "work_definition")
        batch_ref = _named(batch_value, "clarification_batch")
        base_url = str(answer_api_base_url or "").strip().rstrip("/")
        token = _secret(answer_api_token)
        timeout = max(1, min(int(timeout_seconds), 60))
        response_limit = max(1_024, min(int(max_response_bytes), 5_000_000))
    except (TypeError, ValueError, json.JSONDecodeError):
        return _failure("ANSWER_API_INPUT_INVALID", "Answer Form API 조회 입력을 해석할 수 없습니다.", trace_id)
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or not token:
        return _failure("ANSWER_API_CONFIG_INVALID", "Answer Form API는 credential이 없는 HTTPS base URL과 인증 token이 필요합니다.", trace_id)
    batch_id = str(batch_ref.get("batch_id") or "")
    work_id = str(work.get("work_definition_id") or "")
    if not batch_id or not work_id or not work.get("tenant_id") or not work.get("owner_id"):
        return _failure("ANSWER_BATCH_REFERENCE_INVALID", "Answer Form API 조회에 필요한 tenant/owner/work/batch 식별자가 없습니다.", trace_id)
    endpoint = f"{base_url}/api/work-definitions/{quote(work_id, safe='')}/question-batches/{quote(batch_id, safe='')}"
    request = Request(
        endpoint,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID": str(work["tenant_id"]),
            "X-Actor-ID": str(work["owner_id"]),
        },
        method="GET",
    )
    try:
        open_request = opener or _ANSWER_API_OPENER
        with open_request(request, timeout=timeout) as response:
            final_url = str(getattr(response, "geturl", lambda: endpoint)())
            if urlsplit(final_url).scheme != "https":
                return _failure("ANSWER_API_REDIRECT_INVALID", "Answer Form API가 허용되지 않은 URL로 이동했습니다.", trace_id)
            response_headers = getattr(response, "headers", {})
            content_type = str(response_headers.get("Content-Type", "") or "").lower()
            if content_type and "json" not in content_type:
                return _failure("ANSWER_API_SCHEMA_INVALID", "Answer Form API 응답 Content-Type이 JSON이 아닙니다.", trace_id)
            length_text = str(response_headers.get("Content-Length", "") or "")
            if length_text.isdigit() and int(length_text) > response_limit:
                return _failure("ANSWER_API_RESPONSE_TOO_LARGE", "Answer Form API 응답이 허용 크기를 초과했습니다.", trace_id)
            raw = response.read(response_limit + 1)
        if len(raw) > response_limit:
            return _failure("ANSWER_API_RESPONSE_TOO_LARGE", "Answer Form API 응답이 허용 크기를 초과했습니다.", trace_id)
        decoded = json.loads(raw.decode("utf-8"))
        if not isinstance(decoded, dict):
            return _failure("ANSWER_API_SCHEMA_INVALID", "Answer Form API 응답은 JSON object여야 합니다.", trace_id)
        stored = _batch_from_api_payload(decoded)
        return _validate_loaded_batch(
            work,
            stored,
            channel_mode=channel_mode,
            native_form_payload=native_form_payload,
            human_action=human_action,
            now_utc=now_utc,
            source="companion_api",
        )
    except HTTPError as exc:
        retryable = int(getattr(exc, "code", 0)) >= 500 or int(getattr(exc, "code", 0)) == 429
        return _failure("ANSWER_API_HTTP_FAILED", "Answer Form API가 조회 요청을 완료하지 못했습니다.", trace_id, {"http_status": int(getattr(exc, "code", 0))}, retryable=retryable)
    except (URLError, TimeoutError, OSError) as exc:
        return _failure("ANSWER_API_UNAVAILABLE", "Answer Form API에 연결할 수 없습니다.", trace_id, {"exception_type": type(exc).__name__}, retryable=True)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _failure("ANSWER_API_SCHEMA_INVALID", "Answer Form API 응답을 UTF-8 JSON으로 해석할 수 없습니다.", trace_id)


def load_work_answers_from_store(
    work_value: Any,
    batch_value: Any,
    *,
    channel_mode: Any,
    native_form_payload: Any = None,
    human_action: Any = "",
    now_utc: Any = "",
    mongodb_uri: Any,
    mongo_database: Any,
    collection_name: Any = "clarification_batches",
    timeout_ms: Any = 5000,
    client_factory: Any = MongoClient,
) -> dict[str, Any]:
    """Load the canonical batch (and F10 form answers) from durable MongoDB."""
    trace_id = f"trace-{uuid.uuid4()}"
    try:
        work = _named(work_value, "work_definition")
        batch_ref = _named(batch_value, "clarification_batch")
        uri = _secret(mongodb_uri)
        database_name = str(mongo_database or "").strip()[:200]
        collection = str(collection_name or "clarification_batches").strip()[:200]
        timeout = max(1000, min(int(timeout_ms), 30_000))
    except (TypeError, ValueError, json.JSONDecodeError):
        return _failure("ANSWER_STORE_INPUT_INVALID", "답변 저장소 조회 입력을 해석할 수 없습니다.", trace_id)
    if not uri or not database_name or not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", collection):
        return _failure("ANSWER_STORE_CONFIG_MISSING", "답변을 조회할 production MongoDB 설정이 필요합니다.", trace_id)
    batch_id = str(batch_ref.get("batch_id") or "")
    if not batch_id or not work.get("tenant_id") or not work.get("work_definition_id"):
        return _failure("ANSWER_BATCH_REFERENCE_INVALID", "답변 조회에 필요한 tenant/work/batch 식별자가 없습니다.", trace_id)
    client = None
    try:
        client = client_factory(uri, serverSelectionTimeoutMS=timeout, connectTimeoutMS=timeout, socketTimeoutMS=timeout, retryWrites=True)
        client.admin.command("ping")
        stored = client[database_name][collection].find_one({"tenant_id": work["tenant_id"], "owner_id": work.get("owner_id"), "work_definition_id": work["work_definition_id"], "batch_id": batch_id})
        if stored is None:
            return _failure("ANSWER_BATCH_NOT_FOUND", "저장된 질문 batch를 찾을 수 없습니다.", trace_id)
        return _validate_loaded_batch(
            work,
            _batch_from_api_payload(stored),
            channel_mode=channel_mode,
            native_form_payload=native_form_payload,
            human_action=human_action,
            now_utc=now_utc,
            source="clarification_batches",
        )
    except PyMongoError as exc:
        return _failure("ANSWER_STORE_READ_FAILED", "질문 batch 또는 답변을 MongoDB에서 읽지 못했습니다.", trace_id, {"exception_type": type(exc).__name__}, retryable=True)
    finally:
        if client is not None:
            client.close()


class WorkAnswerLoaderComponent(Component):
    display_name = "14 업무 답변 Loader"
    description = "MongoDB 또는 인증된 companion API에서 F10 form 답변을 다시 읽고 batch/session/revision/native HITL 계약을 검증합니다."
    icon = "Import"
    name = "WorkAnswerLoader"

    inputs = [
        DataInput(name="work_definition", display_name="WorkDefinition", input_types=["Data", "JSON"], required=True),
        DataInput(name="clarification_batch", display_name="질문 Batch", input_types=["Data", "JSON"], required=True),
        DataInput(
            name="route_trigger",
            display_name="Runtime State Persist Trigger",
            input_types=["Data", "JSON"],
            required=False,
            # Langflow 1.11.1 removes a saved edge when its target input is
            # marked advanced during Flow import.  This is an execution
            # dependency in F10, so it must remain a normal connectable input.
            advanced=False,
            info="F10에서 MERGING runtime state 저장 완료를 보장하는 실행 의존성입니다.",
        ),
        DataInput(name="native_form_payload", display_name="F10 Answer Form Payload", input_types=["Data", "JSON"], required=False),
        MessageTextInput(name="human_action", display_name="F10 Human Input Action", value="", required=False),
        MessageTextInput(name="now_utc", display_name="검증 기준 시각", value="", advanced=True),
        DropdownInput(name="answer_source_mode", display_name="답변 조회 Source", options=["mongodb", "companion_api", "direct_payload"], value="mongodb", info="direct_payload는 자동 fallback이 아닌 Flow contract test용 명시 모드입니다."),
        SecretStrInput(name="mongodb_uri", display_name="MongoDB URI", required=False),
        MessageTextInput(name="mongo_database", display_name="MongoDB Database", value="business_work_design", required=False),
        MessageTextInput(name="collection_name", display_name="질문 Batch Collection", value="clarification_batches", advanced=True),
        IntInput(name="timeout_ms", display_name="MongoDB Timeout(ms)", value=5000, advanced=True),
        MessageTextInput(name="answer_api_base_url", display_name="Answer Form API HTTPS Base URL", value="", required=False),
        SecretStrInput(name="answer_api_token", display_name="Answer Form API Token", required=False),
        IntInput(name="answer_api_timeout_seconds", display_name="Answer API Timeout(s)", value=10, advanced=True),
        IntInput(name="answer_api_max_response_bytes", display_name="Answer API 최대 응답 Byte", value=1000000, advanced=True),
    ]
    outputs = [Output(name="answer_submission", display_name="검증된 답변", method="build_submission", types=["Data"])]

    def build_submission(self) -> Data:
        common = {
            "channel_mode": "native_hitl",
            "native_form_payload": getattr(self, "native_form_payload", None),
            "human_action": getattr(self, "human_action", ""),
            "now_utc": getattr(self, "now_utc", ""),
        }
        source_mode = getattr(self, "answer_source_mode", "mongodb")
        if source_mode == "companion_api":
            result = load_work_answers_from_companion_api(
                getattr(self, "work_definition", None),
                getattr(self, "clarification_batch", None),
                **common,
                answer_api_base_url=getattr(self, "answer_api_base_url", ""),
                answer_api_token=getattr(self, "answer_api_token", ""),
                timeout_seconds=getattr(self, "answer_api_timeout_seconds", 10),
                max_response_bytes=getattr(self, "answer_api_max_response_bytes", 1000000),
                opener=_ANSWER_API_OPENER,
            )
        elif source_mode == "direct_payload":
            result = load_work_answers(
                getattr(self, "work_definition", None),
                getattr(self, "clarification_batch", None),
                **common,
            )
            if result.get("ok"):
                result["store_result"] = {"batch_loaded": False, "source": "direct_payload"}
        else:
            result = load_work_answers_from_store(
                getattr(self, "work_definition", None),
                getattr(self, "clarification_batch", None),
                **common,
                mongodb_uri=getattr(self, "mongodb_uri", ""),
                mongo_database=getattr(self, "mongo_database", ""),
                collection_name=getattr(self, "collection_name", "clarification_batches"),
                timeout_ms=getattr(self, "timeout_ms", 5000),
                client_factory=MongoClient,
            )
        self.status = {"ok": result["ok"], "status": result["status"], "channel_mode": "native_hitl"}
        return Data(data=result)
