"""Parse human-friendly F10 clarification answers from a Langflow Chat Input.

Langflow 1.11.0's built-in Human Input node can pause and select a branch, but
it does not render the dynamic text fields used by Component 42.  This
standalone compatibility component accepts the next Playground Chat Input in a
human-readable form instead::

    질문 묶음: qb-예시-1
    1번: 메일과 JIRA를 수집한 뒤 프로젝트별로 분류합니다.
    2번: 팀장 승인 후 보고 포털에 게시된 링크가 최종 결과입니다.

Question numbers are resolved only against the persisted clarification batch in
their displayed order. ``1번:``, ``1.``, and ``1)`` are accepted. A bracketed
question ID may also be used, for example ``질문 ID [q-123]: 답변``. There is
deliberately no fuzzy matching, so a mistyped label cannot update a different
WorkDefinition field. The optional leading ``질문 묶음: ...`` line is displayed
for a person and does not alter the loaded batch identity.

The successful output wraps ``native-clarification-answer-submission/v1`` and
``route=branch_submit_answers``.  It can therefore connect directly to
Component 39's ``native_answer_submission`` and ``submit_trigger`` inputs.
All output timestamps are ISO strings; datetime instances are never emitted.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, MessageTextInput, Output
from lfx.schema import Data


MAX_QUESTIONS = 4
MAX_INPUT_CHARS = 64_000
MAX_FREE_TEXT_CHARS = 16_000
MAX_ANSWER_VALUE_BYTES = 64 * 1024
MAX_ID_CHARS = 200
NATIVE_ANSWER_SCHEMA = "native-clarification-answer-submission/v1"
SUBMIT_ROUTE = "branch_submit_answers"
SUBMIT_ACTION = "submit_answers"
ALLOWED_ANSWER_TYPES = {
    "text",
    "single_choice",
    "single_choice_with_text",
    "multi_choice",
    "boolean",
    "number",
}

# The public instruction uses ``1번: ...``. ``1. ...``, ``1) ...``, Q1:,
# 질문 1:, and answer_01: are accepted as equivalent, but only when the
# number maps to this batch.
_NUMBER_LABEL = re.compile(
    r"^\s*(?:(?:질문|question|q)\s*)?([1-9][0-9]*)\s*(?:(?:번\s*)?[:：]|[.)])\s*(.*)$",
    re.IGNORECASE,
)
_FIELD_LABEL = re.compile(r"^\s*answer_([0-9]{1,2})\s*[:：]\s*(.*)$", re.IGNORECASE)
_EXPLICIT_ID_LABEL = re.compile(
    r"^\s*(?:질문\s*(?:id)?|question\s*id)\s*\[([^\]\r\n]{1,200})\]\s*[:：]\s*(.*)$",
    re.IGNORECASE,
)
_BRACKET_ID_LABEL = re.compile(r"^\s*\[([^\]\r\n]{1,200})\]\s*[:：]\s*(.*)$")
_BATCH_HEADER = re.compile(r"^\s*(?:질문\s*묶음|question\s*batch|batch(?:_id)?)\s*[:：].*$", re.IGNORECASE)


def _payload(value: Any) -> dict[str, Any]:
    """Read an envelope from Langflow Data, a dict, or a JSON message."""

    if isinstance(value, dict):
        return copy.deepcopy(value)
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return copy.deepcopy(data)
    text = getattr(value, "text", value if isinstance(value, str) else "")
    if isinstance(text, str) and text.strip():
        parsed = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE))
        return copy.deepcopy(parsed) if isinstance(parsed, dict) else {}
    return {}


def _batch(value: Any) -> dict[str, Any]:
    payload = _payload(value)
    nested = payload.get("clarification_batch")
    return copy.deepcopy(nested) if isinstance(nested, dict) else payload


def _input_text(value: Any, *preferred_keys: str) -> str:
    """Accept a ChatInput Message without trusting arbitrary object reprs."""

    if isinstance(value, str):
        text = value
    elif isinstance(value, dict):
        text = ""
        for key in (*preferred_keys, "text", "message"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                text = candidate
                break
    else:
        text = getattr(value, "text", "")
        if not isinstance(text, str):
            data = getattr(value, "data", None)
            if isinstance(data, str):
                text = data
            elif isinstance(data, dict):
                text = ""
                for key in (*preferred_keys, "text", "message"):
                    candidate = data.get(key)
                    if isinstance(candidate, str):
                        text = candidate
                        break
            else:
                text = ""
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc(value: Any = "") -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _bounded_text(value: Any, maximum: int = MAX_ID_CHARS) -> str:
    return str(value or "").strip()[:maximum]


def _failure(code: str, message: str, trace_id: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "BLOCKED",
        "route": "blocked_path",
        "artifact_refs": [],
        "error": {
            "code": code,
            "message": message,
            "retryable": False,
            "details": details or {},
        },
        "resume": None,
        "trace_id": trace_id,
    }


def _question_mappings(batch: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate the stored batch and bind its display order to labels."""

    required_identity = (
        "batch_id",
        "work_definition_id",
        "tenant_id",
        "owner_id",
        "session_id",
        "channel_mode",
        "revision",
        "round_number",
    )
    missing = [name for name in required_identity if batch.get(name) in (None, "")]
    if missing:
        raise ValueError("CLARIFICATION_BATCH_IDENTITY_INVALID")
    if str(batch.get("channel_mode") or "") != "native_hitl":
        raise ValueError("CLARIFICATION_BATCH_CHANNEL_INVALID")
    if str(batch.get("status") or "") != "WAITING_ANSWER":
        raise ValueError("CLARIFICATION_BATCH_STATE_INVALID")
    try:
        revision = int(batch.get("revision"))
        round_number = int(batch.get("round_number"))
    except (TypeError, ValueError) as exc:
        raise ValueError("CLARIFICATION_BATCH_IDENTITY_INVALID") from exc
    if revision < 0 or round_number not in {1, 2, 3}:
        raise ValueError("CLARIFICATION_BATCH_IDENTITY_INVALID")

    questions = batch.get("questions")
    if not isinstance(questions, list) or not 1 <= len(questions) <= MAX_QUESTIONS:
        raise ValueError("CLARIFICATION_QUESTION_CONTRACT_INVALID")
    mappings: list[dict[str, Any]] = []
    seen_question_ids: set[str] = set()
    for index, raw_question in enumerate(questions, start=1):
        if not isinstance(raw_question, dict):
            raise ValueError("CLARIFICATION_QUESTION_CONTRACT_INVALID")
        question_id = _bounded_text(raw_question.get("question_id"))
        question_text = _bounded_text(raw_question.get("text"), 4_000)
        answer_type = str(raw_question.get("answer_type") or "text")
        if not question_id or not question_text or question_id in seen_question_ids:
            raise ValueError("CLARIFICATION_QUESTION_CONTRACT_INVALID")
        if answer_type not in ALLOWED_ANSWER_TYPES:
            raise ValueError("CLARIFICATION_QUESTION_TYPE_INVALID")
        raw_choices = raw_question.get("choices") if isinstance(raw_question.get("choices"), list) else []
        choices: list[str] = []
        for raw_choice in raw_choices[:20]:
            if not isinstance(raw_choice, str):
                raise ValueError("CLARIFICATION_QUESTION_CHOICES_INVALID")
            choice = raw_choice.strip()[:300]
            if choice and choice not in choices:
                choices.append(choice)
        if answer_type in {"single_choice", "single_choice_with_text", "multi_choice"} and not choices:
            raise ValueError("CLARIFICATION_QUESTION_CHOICES_INVALID")
        mappings.append(
            {
                "number": index,
                "field_name": f"answer_{index:02d}",
                "question_id": question_id,
                "question": copy.deepcopy(raw_question),
                "required": bool(raw_question.get("required", True)),
                "answer_type": answer_type,
                "choices": choices,
            }
        )
        seen_question_ids.add(question_id)
    return mappings


def _parse_response(text: str, mappings: list[dict[str, Any]]) -> dict[str, str]:
    """Parse labels exactly, retaining multiline answer text under each label."""

    mapping_by_number = {item["number"]: item for item in mappings}
    mapping_by_question_id = {item["question_id"]: item for item in mappings}
    values: dict[str, list[str]] = {}
    current: dict[str, Any] | None = None

    for line_number, raw_line in enumerate(text.split("\n"), start=1):
        line = raw_line.strip()
        if not line:
            if current is not None:
                values.setdefault(current["question_id"], []).append("")
            continue

        # This line is intentionally informational only.  The loader supplies
        # the authoritative batch, so a copied or stale display header can
        # never change its identity.
        if current is None and _BATCH_HEADER.fullmatch(line):
            continue

        number_match = _NUMBER_LABEL.fullmatch(line)
        field_match = _FIELD_LABEL.fullmatch(line)
        explicit_id_match = _EXPLICIT_ID_LABEL.fullmatch(line)
        bracket_id_match = _BRACKET_ID_LABEL.fullmatch(line)
        matched: dict[str, Any] | None = None
        initial_value = ""

        if number_match is not None:
            number = int(number_match.group(1))
            matched = mapping_by_number.get(number)
            if matched is None:
                raise ValueError(f"ANSWER_LABEL_UNKNOWN:{line_number}")
            initial_value = number_match.group(2)
        elif field_match is not None:
            number = int(field_match.group(1))
            matched = mapping_by_number.get(number)
            if matched is None:
                raise ValueError(f"ANSWER_LABEL_UNKNOWN:{line_number}")
            initial_value = field_match.group(2)
        elif explicit_id_match is not None:
            question_id = explicit_id_match.group(1).strip()
            matched = mapping_by_question_id.get(question_id)
            if matched is None:
                raise ValueError(f"ANSWER_LABEL_UNKNOWN:{line_number}")
            initial_value = explicit_id_match.group(2)
        elif bracket_id_match is not None and bracket_id_match.group(1).strip() in mapping_by_question_id:
            matched = mapping_by_question_id[bracket_id_match.group(1).strip()]
            initial_value = bracket_id_match.group(2)

        if matched is not None:
            question_id = matched["question_id"]
            if question_id in values:
                raise ValueError(f"ANSWER_LABEL_DUPLICATED:{line_number}")
            current = matched
            values[question_id] = [initial_value]
            continue

        if current is None:
            raise ValueError(f"ANSWER_FORMAT_INVALID:{line_number}")
        values.setdefault(current["question_id"], []).append(raw_line.rstrip())

    if not values:
        raise ValueError("ANSWER_FORMAT_INVALID:1")
    return {question_id: "\n".join(lines).strip() for question_id, lines in values.items()}


def _is_blank(value: Any) -> bool:
    return value is None or value == [] or (isinstance(value, str) and not value.strip())


def _parse_json_object(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _parse_json_list(value: str) -> list[Any] | None:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, list) else None


def _normalize_answer(question: dict[str, Any], raw_value: Any) -> Any:
    """Use the same typed answer contract enforced again by Component 39."""

    answer_type = str(question.get("answer_type") or "text")
    if answer_type not in ALLOWED_ANSWER_TYPES:
        raise ValueError("ANSWER_VALUE_TYPE_INVALID")
    if _is_blank(raw_value):
        if bool(question.get("required", True)):
            raise ValueError("ANSWER_REQUIRED_VALUE_MISSING")
        return None
    choices = [str(item).strip() for item in (question.get("choices") or []) if isinstance(item, str) and str(item).strip()]

    if answer_type == "text":
        if not isinstance(raw_value, str) or len(raw_value) > MAX_FREE_TEXT_CHARS:
            raise ValueError("ANSWER_VALUE_TYPE_INVALID")
        normalized: Any = raw_value.strip()
    elif answer_type == "single_choice":
        if not isinstance(raw_value, str) or raw_value.strip() not in choices:
            raise ValueError("ANSWER_CHOICE_INVALID")
        normalized = raw_value.strip()
    elif answer_type == "single_choice_with_text":
        candidate = raw_value.strip() if isinstance(raw_value, str) else raw_value
        if isinstance(candidate, str) and candidate in choices:
            normalized = {"choice": candidate, "text": ""}
        else:
            if isinstance(candidate, str) and ":" in candidate:
                choice_text, detail = candidate.split(":", 1)
                if choice_text.strip().casefold() in {"기타", "other", "__other__"}:
                    candidate = {"choice": "__other__", "text": detail.strip()}
            if isinstance(candidate, str):
                candidate = _parse_json_object(candidate)
            if not isinstance(candidate, dict) or set(candidate) - {"choice", "text"}:
                raise ValueError("ANSWER_VALUE_TYPE_INVALID")
            choice = candidate.get("choice")
            detail = candidate.get("text", "")
            if not isinstance(choice, str) or not isinstance(detail, str) or len(detail) > MAX_FREE_TEXT_CHARS:
                raise ValueError("ANSWER_VALUE_TYPE_INVALID")
            if choice == "__other__":
                if not detail.strip():
                    raise ValueError("ANSWER_REQUIRED_VALUE_MISSING")
            elif choice not in choices:
                raise ValueError("ANSWER_CHOICE_INVALID")
            normalized = {"choice": choice, "text": detail.strip()}
    elif answer_type == "multi_choice":
        candidate = raw_value
        if isinstance(candidate, str):
            text = candidate.strip()
            parsed = _parse_json_list(text) if text.startswith("[") else None
            candidate = parsed if parsed is not None else [
                re.sub(r"^[\s•*-]+", "", part).strip()
                for part in re.split(r"[,，\n]", text)
                if part.strip()
            ]
        if not isinstance(candidate, list) or not candidate:
            raise ValueError("ANSWER_CHOICE_INVALID")
        if any(not isinstance(item, str) or item.strip() not in choices for item in candidate):
            raise ValueError("ANSWER_CHOICE_INVALID")
        normalized = list(dict.fromkeys(item.strip() for item in candidate))
    elif answer_type == "boolean":
        if isinstance(raw_value, bool):
            normalized = raw_value
        elif isinstance(raw_value, str):
            value = raw_value.strip().casefold()
            if value in {"true", "1", "yes", "y", "예", "네"}:
                normalized = True
            elif value in {"false", "0", "no", "n", "아니오", "아니요"}:
                normalized = False
            else:
                raise ValueError("ANSWER_VALUE_TYPE_INVALID")
        else:
            raise ValueError("ANSWER_VALUE_TYPE_INVALID")
    else:
        if isinstance(raw_value, bool):
            raise ValueError("ANSWER_VALUE_TYPE_INVALID")
        try:
            numeric_text = raw_value.replace(",", "") if isinstance(raw_value, str) else raw_value
            numeric = float(numeric_text)
        except (TypeError, ValueError):
            raise ValueError("ANSWER_VALUE_TYPE_INVALID") from None
        if not math.isfinite(numeric) or abs(numeric) > 1e15:
            raise ValueError("ANSWER_VALUE_TYPE_INVALID")
        normalized = int(numeric) if numeric.is_integer() else numeric

    if len(_canonical(normalized).encode("utf-8")) > MAX_ANSWER_VALUE_BYTES:
        raise ValueError("ANSWER_VALUE_TOO_LARGE")
    return normalized


def build_chat_answer_submission(
    clarification_batch_value: Any,
    answer_text: Any,
    *,
    actor_id: Any = "",
    request_id: Any = "",
    now_utc: Any = "",
) -> dict[str, Any]:
    """Build Component 39-compatible native submission from a readable reply."""

    trace_id = f"trace-{uuid.uuid4()}"
    try:
        batch = _batch(clarification_batch_value)
        mappings = _question_mappings(batch)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        code = str(exc) or "CLARIFICATION_BATCH_INVALID"
        messages = {
            "CLARIFICATION_BATCH_IDENTITY_INVALID": "질문 Batch의 업무 식별자 또는 회차 정보가 유효하지 않습니다.",
            "CLARIFICATION_BATCH_CHANNEL_INVALID": "이 답변 Parser는 native_hitl 질문 Batch만 처리합니다.",
            "CLARIFICATION_BATCH_STATE_INVALID": "답변을 기다리는 상태의 질문 Batch만 처리할 수 있습니다.",
            "CLARIFICATION_QUESTION_CONTRACT_INVALID": "질문 Batch의 질문 수, 질문 ID 또는 질문 내용이 유효하지 않습니다.",
            "CLARIFICATION_QUESTION_TYPE_INVALID": "질문의 answer_type이 지원 범위를 벗어났습니다.",
            "CLARIFICATION_QUESTION_CHOICES_INVALID": "선택형 질문의 선택지가 유효하지 않습니다.",
        }
        return _failure(code, messages.get(code, "질문 Batch를 읽을 수 없습니다."), trace_id)

    text = _input_text(answer_text, "answer_text")
    if not text:
        return _failure(
            "ANSWER_TEXT_REQUIRED",
            "답변을 입력해 주세요. 예: 1번: 첫 번째 답변",
            trace_id,
        )
    if len(text) > MAX_INPUT_CHARS:
        return _failure(
            "ANSWER_TEXT_TOO_LARGE",
            "답변이 허용된 최대 길이를 초과했습니다.",
            trace_id,
            {"maximum_characters": MAX_INPUT_CHARS},
        )
    try:
        raw_values = _parse_response(text, mappings)
    except ValueError as exc:
        raw_code, _, line_number = str(exc).partition(":")
        messages = {
            "ANSWER_FORMAT_INVALID": "답변은 각 줄을 `1번: 답변` 형식으로 시작해 주세요.",
            "ANSWER_LABEL_UNKNOWN": "질문 번호 또는 질문 ID가 현재 질문 Batch와 일치하지 않습니다.",
            "ANSWER_LABEL_DUPLICATED": "같은 질문 번호 또는 질문 ID에 답변을 두 번 입력할 수 없습니다.",
        }
        details = {"line_number": int(line_number) if line_number.isdigit() else None}
        return _failure(raw_code or "ANSWER_FORMAT_INVALID", messages.get(raw_code, "답변 형식을 읽을 수 없습니다."), trace_id, details)

    answers: list[dict[str, Any]] = []
    field_values: dict[str, Any] = {}
    try:
        for mapping in mappings:
            raw_value = raw_values.get(mapping["question_id"])
            normalized = _normalize_answer(mapping["question"], raw_value)
            field_values[mapping["field_name"]] = normalized
            if normalized is not None:
                answers.append(
                    {
                        "question_id": mapping["question_id"],
                        "value": normalized,
                        "evidence_turn_id": f"chat-input-{batch['batch_id']}-{mapping['question_id']}",
                    }
                )
    except ValueError as exc:
        code = str(exc) or "ANSWER_VALUE_TYPE_INVALID"
        messages = {
            "ANSWER_REQUIRED_VALUE_MISSING": "필수 질문의 답변이 비어 있습니다. 모든 필수 질문에 답변하거나 추가 입력 건너뛰기를 선택해 주세요.",
            "ANSWER_CHOICE_INVALID": "선택형 질문은 안내된 선택지 중 하나 또는 여러 개를 입력해야 합니다.",
            "ANSWER_VALUE_TYPE_INVALID": "답변 형식이 질문의 입력 형식과 일치하지 않습니다.",
            "ANSWER_VALUE_TOO_LARGE": "답변 길이가 허용 범위를 초과했습니다.",
        }
        return _failure(code, messages.get(code, "답변 값이 유효하지 않습니다."), trace_id)

    try:
        now = _utc(now_utc)
        revision = int(batch["revision"])
        round_number = int(batch["round_number"])
    except (TypeError, ValueError):
        return _failure("SUBMITTED_AT_INVALID", "제출 시각은 ISO-8601 형식이어야 합니다.", trace_id)

    identity = {
        "batch_id": str(batch["batch_id"]),
        "work_definition_id": str(batch["work_definition_id"]),
        "tenant_id": str(batch["tenant_id"]),
        "owner_id": str(batch["owner_id"]),
        "session_id": str(batch["session_id"]),
        "channel_mode": str(batch["channel_mode"]),
        "revision": revision,
        "round_number": round_number,
    }
    supplied_actor = _bounded_text(_input_text(actor_id, "actor_id"))
    if supplied_actor and supplied_actor != identity["owner_id"]:
        return _failure(
            "ACTION_ACTOR_MISMATCH",
            "질문 Batch의 사번과 답변 제출 사번이 일치하지 않습니다.",
            trace_id,
        )
    supplied_request_id = _bounded_text(_input_text(request_id, "request_id"))
    resolved_request_id = supplied_request_id or f"f10-chat-input:{identity['batch_id']}"
    idempotency_material = _canonical({"request_id": resolved_request_id, **identity, "answers": answers})
    idempotency_key = "chat-input-" + hashlib.sha256(idempotency_material.encode("utf-8")).hexdigest()[:32]
    submission_id = "answer-" + hashlib.sha256((idempotency_material + "|submission").encode("utf-8")).hexdigest()[:24]
    native_submission = {
        "schema_version": NATIVE_ANSWER_SCHEMA,
        "submission_id": submission_id,
        **identity,
        "request_id": resolved_request_id,
        "action_id": SUBMIT_ACTION,
        "answers": answers,
        "field_values": field_values,
        "idempotency_key": idempotency_key,
        "submitted_at": _iso(now),
        "source": "langflow_1_11_chat_input",
    }
    return {
        "ok": True,
        "status": "ANSWER_SUBMITTED",
        "route": SUBMIT_ROUTE,
        "artifact_refs": [
            {"kind": "clarification_batch", "id": identity["batch_id"]},
            {"kind": "answer_submission", "id": submission_id},
        ],
        "answer_submission": native_submission,
        "human_decision": {"action_id": SUBMIT_ACTION, "values": field_values},
        "input_format": "numbered_multiline",
        "trace_id": trace_id,
    }


class F10NumberedChatAnswerParserComponent(Component):
    """Turn a 1.11.0 Playground Chat Input into an F10 native submission."""

    display_name = "46 번호형 대화 답변 Parser"
    description = "Langflow 1.11.0 호환: `1번: 답변`, `1. 답변`, `1) 답변` 형식의 Playground 채팅 입력을 검증된 F10 답변 제출로 바꿉니다. 질문 번호·ID는 현재 질문 Batch와 정확히 일치해야 합니다."
    icon = "MessageSquareText"
    name = "F10NumberedChatAnswerParser"

    inputs = [
        DataInput(
            name="clarification_batch",
            display_name="질문 Batch",
            input_types=["Data", "JSON"],
            required=True,
            info="13 재질문 Batch 생성에서 저장된 현재 회차의 질문 Batch를 연결합니다.",
        ),
        MessageTextInput(
            name="answer_text",
            display_name="사용자 답변",
            input_types=["Message", "Data", "JSON"],
            required=True,
            info="Playground Chat Input을 연결합니다. 예: `질문 묶음: qb-...` 다음 줄에 `1번: 첫 번째 답변`, `2번: 두 번째 답변`을 입력합니다.",
        ),
        MessageTextInput(
            name="actor_id",
            display_name="답변 제출 사번 (자동 연결)",
            value="",
            required=False,
            info="F10 왼쪽의 사번 Text Input이 자동으로 연결됩니다. 질문을 받은 뒤에는 같은 사번을 유지합니다.",
        ),
        MessageTextInput(name="request_id", display_name="답변 요청 ID", value="", required=False, advanced=True),
        MessageTextInput(name="now_utc", display_name="제출 시각(ISO-8601)", value="", advanced=True),
    ]
    outputs = [
        Output(name="answer_submission", display_name="검증된 답변 제출", method="build_submission", types=["Data"]),
        Output(name="submit_trigger", display_name="답변 제출 Trigger", method="route_submission", types=["Data"], group_outputs=True),
        Output(name="blocked_path", display_name="답변 형식 차단", method="route_submission", types=["Data"], group_outputs=True),
    ]

    def _result(self) -> dict[str, Any]:
        result = getattr(self, "_chat_answer_result", None)
        if not isinstance(result, dict):
            result = build_chat_answer_submission(
                getattr(self, "clarification_batch", None),
                getattr(self, "answer_text", None),
                actor_id=getattr(self, "actor_id", ""),
                request_id=getattr(self, "request_id", ""),
                now_utc=getattr(self, "now_utc", ""),
            )
            self._chat_answer_result = result
        self.status = {"ok": result.get("ok"), "status": result.get("status"), "route": result.get("route")}
        return result

    def build_submission(self) -> Data:
        return Data(data=copy.deepcopy(self._result()))

    def _select_output_route(self, selected: str) -> None:
        output_names = ("submit_trigger", "blocked_path")
        for output_name in output_names:
            if output_name != selected:
                self.stop(output_name)
        graph = getattr(self, "graph", None)
        exclude = getattr(graph, "exclude_branches_conditionally", None) if graph is not None else None
        if callable(exclude):
            exclude(getattr(self, "_id", self.name), [name for name in output_names if name != selected])

    def route_submission(self) -> Data:
        """Emit exactly one branch: Component 39 submit or a visible format error."""

        result = self._result()
        selected = "submit_trigger" if result.get("route") == SUBMIT_ROUTE else "blocked_path"
        self._select_output_route(selected)
        current_output = str(getattr(self, "_current_output", "") or "")
        if current_output and current_output != selected:
            return Data(data={})
        return Data(data=copy.deepcopy(result))
