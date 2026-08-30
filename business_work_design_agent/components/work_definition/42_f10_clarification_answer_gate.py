from __future__ import annotations

"""Native Langflow 1.11.1 HITL answer form for F10.

This is intentionally a standalone custom component.  It does not import
another local component or call a Flow/API: it turns a persisted clarification
batch into Langflow's native ``node_input`` pause contract, then converts the
resumed decision values back to the original question ids.

Langflow 1.11.1 renders every entry in ``schema`` as a text field and sends
the typed values back in ``decision.values``.  Therefore this component uses
safe, deterministic field keys (``answer_01`` …) rather than exposing an
arbitrary ``question_id`` as a browser form key.
"""

import copy
import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, Output
from lfx.schema import Data


HUMAN_INPUT_REQUIRED = "human_input_required"
KIND_NODE_INPUT = "node_input"
SUBMIT_ACTION = "submit_answers"
SKIP_ACTION = "skip_additional_input"
CANCEL_ACTION = "cancel"
# Rounds 1 and 2 use at most three fields.  The third and final native HITL
# card may carry a fourth field so a ten-gap definition fits inside the
# promised three-round HITL limit.
MAX_QUESTIONS = 4
MAX_FREE_TEXT_CHARS = 16_000
MAX_ANSWER_VALUE_BYTES = 64 * 1024
ALLOWED_ANSWER_TYPES = {
    "text",
    "single_choice",
    "single_choice_with_text",
    "multi_choice",
    "boolean",
    "number",
}


def _payload(value: Any) -> dict[str, Any]:
    """Read a Data/dict/JSON value without relying on another component."""

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
    """Accept either F13's result envelope or its nested batch itself."""

    payload = _payload(value)
    nested = payload.get("clarification_batch")
    return copy.deepcopy(nested) if isinstance(nested, dict) else payload


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _bounded_text(value: Any, maximum: int) -> str:
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


def _safe_field_name(index: int) -> str:
    """A browser-safe, stable key; it deliberately never contains question_id."""

    return f"answer_{index:02d}"


def _question_mappings(batch: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate the batch and bind each original question id to a safe field key."""

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
    missing_identity = [name for name in required_identity if batch.get(name) in (None, "")]
    if missing_identity:
        raise ValueError("CLARIFICATION_BATCH_IDENTITY_INVALID")
    if str(batch.get("channel_mode") or "") != "native_hitl":
        raise ValueError("CLARIFICATION_BATCH_CHANNEL_INVALID")
    if str(batch.get("status") or "") != "WAITING_ANSWER":
        raise ValueError("CLARIFICATION_BATCH_STATE_INVALID")
    try:
        if int(batch.get("revision")) < 0 or int(batch.get("round_number")) not in {1, 2, 3}:
            raise ValueError("CLARIFICATION_BATCH_IDENTITY_INVALID")
    except (TypeError, ValueError) as exc:
        if str(exc) == "CLARIFICATION_BATCH_IDENTITY_INVALID":
            raise
        raise ValueError("CLARIFICATION_BATCH_IDENTITY_INVALID") from exc

    questions = batch.get("questions")
    if not isinstance(questions, list) or not 1 <= len(questions) <= MAX_QUESTIONS:
        raise ValueError("CLARIFICATION_QUESTION_CONTRACT_INVALID")
    mappings: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw_question in enumerate(questions, start=1):
        if not isinstance(raw_question, dict):
            raise ValueError("CLARIFICATION_QUESTION_CONTRACT_INVALID")
        question_id = _bounded_text(raw_question.get("question_id"), 200)
        question_text = _bounded_text(raw_question.get("text"), 4_000)
        if not question_id or not question_text or question_id in seen_ids:
            raise ValueError("CLARIFICATION_QUESTION_CONTRACT_INVALID")
        answer_type = str(raw_question.get("answer_type") or "text")
        if answer_type not in ALLOWED_ANSWER_TYPES:
            raise ValueError("CLARIFICATION_QUESTION_TYPE_INVALID")
        raw_choices = raw_question.get("choices") if isinstance(raw_question.get("choices"), list) else []
        choices = []
        for choice in raw_choices[:20]:
            if not isinstance(choice, str):
                raise ValueError("CLARIFICATION_QUESTION_CHOICES_INVALID")
            cleaned = choice.strip()[:300]
            if cleaned and cleaned not in choices:
                choices.append(cleaned)
        if answer_type in {"single_choice", "single_choice_with_text", "multi_choice"} and not choices:
            raise ValueError("CLARIFICATION_QUESTION_CHOICES_INVALID")
        mappings.append(
            {
                "field_name": _safe_field_name(index),
                "question_id": question_id,
                "question": copy.deepcopy(raw_question),
                "required": bool(raw_question.get("required", True)),
                "answer_type": answer_type,
                "choices": choices,
            }
        )
        seen_ids.add(question_id)
    return mappings


def _field_instruction(mapping: dict[str, Any]) -> str:
    """Show type-specific guidance because Langflow 1.11.1 fields are text-only."""

    answer_type = mapping["answer_type"]
    choices = mapping["choices"]
    if answer_type == "single_choice":
        return "선택지 중 하나를 정확히 입력: " + " / ".join(choices)
    if answer_type == "single_choice_with_text":
        return (
            "선택지는 "
            + " / ".join(choices)
            + ". 기타 설명은 {\"choice\":\"__other__\",\"text\":\"설명\"} 형식으로 입력"
        )
    if answer_type == "multi_choice":
        return "복수 선택은 쉼표로 구분해 입력: " + " / ".join(choices)
    if answer_type == "boolean":
        return "true/false 또는 예/아니오로 입력"
    if answer_type == "number":
        return "숫자로 입력"
    return "자유롭게 서술해 입력"


def build_pause_request(batch_value: Any, *, component_id: Any = "F10ClarificationAnswerGate", run_id: Any = "") -> dict[str, Any]:
    """Build the exact ``node_input`` pause data consumed by Langflow 1.11.1."""

    batch = _batch(batch_value)
    mappings = _question_mappings(batch)
    component = _bounded_text(component_id, 200) or "F10ClarificationAnswerGate"
    run = _bounded_text(run_id, 200) or "run"
    batch_id = _bounded_text(batch.get("batch_id"), 200)
    request_id = f"{component}:{run}:{batch_id}"
    lines = [
        f"업무 정의를 위해 {batch.get('round_number')}차 보완이 필요합니다.",
        "각 질문 아래에 표시된 입력 항목에 답변한 뒤 Submit Answers를 선택해 주세요.",
        "추가 정보가 없으면 추가 입력 건너뛰기를 선택할 수 있습니다. 이 경우 답변하지 않은 항목은 미확정으로 기록된 채 검토 단계로 넘어갑니다.",
    ]
    schema: list[dict[str, Any]] = []
    for index, mapping in enumerate(mappings, start=1):
        question = mapping["question"]
        lines.extend(
            [
                "",
                f"{index}. {_bounded_text(question.get('text'), 4_000)}",
                f"   입력 항목: {mapping['field_name']} — {_field_instruction(mapping)}",
            ]
        )
        # The current Playground renders name + required as a real text field.
        # Extra metadata is retained for a compatible future renderer and for
        # deterministic recovery, but the browser only needs name/required.
        schema.append(
            {
                "name": mapping["field_name"],
                "required": mapping["required"],
                "type": "text",
                "question_id": mapping["question_id"],
                "answer_type": mapping["answer_type"],
                "description": _bounded_text(question.get("text"), 4_000),
            }
        )
    return {
        "request_id": request_id,
        "kind": KIND_NODE_INPUT,
        "prompt": "\n".join(lines)[:16_000],
        "schema": schema,
        "options": [
            {"action_id": SUBMIT_ACTION, "label": "Submit Answers"},
            {"action_id": SKIP_ACTION, "label": "추가 입력 건너뛰기"},
            {"action_id": CANCEL_ACTION, "label": "Cancel"},
        ],
        "allowed_decisions": [SUBMIT_ACTION, SKIP_ACTION, CANCEL_ACTION],
        "batch_id": batch_id,
        "field_mappings": [
            {"field_name": item["field_name"], "question_id": item["question_id"]}
            for item in mappings
        ],
        "paused_at": _iso(_utc_now()),
    }


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
    """Convert the text-only native form values to the canonical answer type."""

    answer_type = str(question.get("answer_type") or "text")
    if answer_type not in ALLOWED_ANSWER_TYPES:
        raise ValueError("ANSWER_VALUE_TYPE_INVALID")
    required = bool(question.get("required", True))
    if _is_blank(raw_value):
        if required:
            raise ValueError("ANSWER_REQUIRED_VALUE_MISSING")
        return None
    choices = [str(choice).strip() for choice in (question.get("choices") or []) if isinstance(choice, str) and str(choice).strip()]

    if answer_type == "text":
        if not isinstance(raw_value, str) or len(raw_value) > MAX_FREE_TEXT_CHARS:
            raise ValueError("ANSWER_VALUE_TYPE_INVALID")
        normalized: Any = raw_value.strip()
    elif answer_type == "single_choice":
        if not isinstance(raw_value, str) or raw_value.strip() not in choices:
            raise ValueError("ANSWER_CHOICE_INVALID")
        normalized = raw_value.strip()
    elif answer_type == "single_choice_with_text":
        candidate = raw_value
        if isinstance(candidate, str):
            candidate = candidate.strip()
            if candidate in choices:
                normalized = {"choice": candidate, "text": ""}
            else:
                candidate = _parse_json_object(candidate)
                if candidate is None:
                    raise ValueError("ANSWER_VALUE_TYPE_INVALID")
                normalized = None
        else:
            normalized = None
        if normalized is None:
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
            candidate = parsed if parsed is not None else [part.strip() for part in re.split(r"[,\n]", text) if part.strip()]
        if not isinstance(candidate, list) or not candidate:
            raise ValueError("ANSWER_CHOICE_INVALID")
        if any(not isinstance(item, str) or item.strip() not in choices for item in candidate):
            raise ValueError("ANSWER_CHOICE_INVALID")
        normalized = list(dict.fromkeys(item.strip() for item in candidate))
    elif answer_type == "boolean":
        if isinstance(raw_value, bool):
            normalized = raw_value
        elif isinstance(raw_value, str):
            text = raw_value.strip().lower()
            if text in {"true", "1", "yes", "y", "예", "네"}:
                normalized = True
            elif text in {"false", "0", "no", "n", "아니오", "아니요"}:
                normalized = False
            else:
                raise ValueError("ANSWER_VALUE_TYPE_INVALID")
        else:
            raise ValueError("ANSWER_VALUE_TYPE_INVALID")
    else:  # number
        if isinstance(raw_value, bool):
            raise ValueError("ANSWER_VALUE_TYPE_INVALID")
        try:
            numeric = float(raw_value.strip() if isinstance(raw_value, str) else raw_value)
        except (TypeError, ValueError):
            raise ValueError("ANSWER_VALUE_TYPE_INVALID") from None
        if not math.isfinite(numeric) or abs(numeric) > 1e15:
            raise ValueError("ANSWER_VALUE_TYPE_INVALID")
        normalized = int(numeric) if numeric.is_integer() else numeric
    if len(_canonical(normalized).encode("utf-8")) > MAX_ANSWER_VALUE_BYTES:
        raise ValueError("ANSWER_VALUE_TOO_LARGE")
    return normalized


def build_resumed_submission(
    batch_value: Any,
    decision: Any,
    *,
    request_id: Any = "",
    now_utc: Any = "",
) -> dict[str, Any]:
    """Map one resumed native decision to an F10 answer/cancel result envelope."""

    trace_id = f"trace-{uuid.uuid4()}"
    try:
        batch = _batch(batch_value)
        mappings = _question_mappings(batch)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        code = str(exc) or "CLARIFICATION_BATCH_INVALID"
        messages = {
            "CLARIFICATION_BATCH_IDENTITY_INVALID": "질문 batch의 업무 식별자 또는 회차 정보가 유효하지 않습니다.",
            "CLARIFICATION_BATCH_CHANNEL_INVALID": "이 HITL 게이트는 native_hitl 질문 batch만 처리합니다.",
            "CLARIFICATION_BATCH_STATE_INVALID": "답변을 기다리는 상태의 질문 batch만 Playground HITL로 열 수 있습니다.",
            "CLARIFICATION_QUESTION_CONTRACT_INVALID": "질문 batch의 질문 수, 질문 ID 또는 질문 내용이 유효하지 않습니다.",
            "CLARIFICATION_QUESTION_TYPE_INVALID": "질문의 answer_type이 지원 범위를 벗어났습니다.",
            "CLARIFICATION_QUESTION_CHOICES_INVALID": "선택형 질문의 선택지가 유효하지 않습니다.",
        }
        return _failure(code, messages.get(code, "질문 batch를 읽을 수 없습니다."), trace_id)
    if not isinstance(decision, dict):
        return _failure("HUMAN_DECISION_INVALID", "재개된 Human Input 결정값이 object가 아닙니다.", trace_id)
    action_id = str(decision.get("action_id") or "").strip()
    if action_id not in {SUBMIT_ACTION, SKIP_ACTION, CANCEL_ACTION}:
        return _failure("HUMAN_DECISION_INVALID", "Submit Answers, 추가 입력 건너뛰기 또는 Cancel 중 하나를 선택해야 합니다.", trace_id)
    if action_id == CANCEL_ACTION:
        return {
            "ok": True,
            "status": "CANCELLED",
            "route": "branch_cancel",
            "artifact_refs": [{"kind": "clarification_batch", "id": batch["batch_id"]}],
            "clarification_batch": copy.deepcopy(batch),
            "human_decision": {"action_id": CANCEL_ACTION, "values": {}},
            "answer_submission": None,
            "trace_id": trace_id,
        }
    if action_id == SKIP_ACTION:
        try:
            revision = int(batch["revision"])
            round_number = int(batch["round_number"])
        except (TypeError, ValueError):
            return _failure("CLARIFICATION_BATCH_IDENTITY_INVALID", "질문 batch revision 또는 회차가 유효하지 않습니다.", trace_id)
        now = _utc_now()
        if str(now_utc or "").strip():
            try:
                parsed = datetime.fromisoformat(str(now_utc).replace("Z", "+00:00"))
                now = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
            except ValueError:
                return _failure("SUBMITTED_AT_INVALID", "제출 시각은 ISO-8601 형식이어야 합니다.", trace_id)
        identity = {
            "batch_id": batch["batch_id"],
            "work_definition_id": batch["work_definition_id"],
            "tenant_id": batch["tenant_id"],
            "owner_id": batch["owner_id"],
            "session_id": batch["session_id"],
            "channel_mode": batch["channel_mode"],
            "revision": revision,
            "round_number": round_number,
        }
        skipped_question_ids = [item["question_id"] for item in mappings]
        idempotency_material = _canonical(
            {
                "request_id": str(request_id or ""),
                **identity,
                "action_id": SKIP_ACTION,
                "skipped_question_ids": skipped_question_ids,
            }
        )
        idempotency_key = "hitl-skip-" + hashlib.sha256(idempotency_material.encode("utf-8")).hexdigest()[:32]
        skip_id = "skip-" + hashlib.sha256((idempotency_material + "|skip").encode("utf-8")).hexdigest()[:24]
        skip_submission = {
            "schema_version": "native-clarification-skip-submission/v1",
            "skip_id": skip_id,
            **identity,
            "request_id": str(request_id or ""),
            "action_id": SKIP_ACTION,
            "skipped_question_ids": skipped_question_ids,
            "idempotency_key": idempotency_key,
            "skipped_at": _iso(now),
            "source": "langflow_native_node_input",
        }
        return {
            "ok": True,
            "status": "CLARIFICATION_SKIPPED",
            "route": "branch_skip_additional_input",
            "artifact_refs": [
                {"kind": "clarification_batch", "id": batch["batch_id"]},
                {"kind": "clarification_skip", "id": skip_id},
            ],
            "clarification_batch": copy.deepcopy(batch),
            "skip_submission": skip_submission,
            "answer_submission": None,
            "human_decision": {"action_id": SKIP_ACTION, "values": {}},
            "trace_id": trace_id,
        }

    values = decision.get("values")
    if not isinstance(values, dict):
        return _failure("HUMAN_DECISION_VALUES_INVALID", "제출된 답변 값이 object 형식이 아닙니다.", trace_id)
    answers: list[dict[str, Any]] = []
    field_values: dict[str, Any] = {}
    try:
        for mapping in mappings:
            field_name = mapping["field_name"]
            raw_value = values.get(field_name)
            normalized = _normalize_answer(mapping["question"], raw_value)
            field_values[field_name] = normalized
            if normalized is not None:
                question = mapping["question"]
                answers.append(
                    {
                        "question_id": mapping["question_id"],
                        "value": normalized,
                        "evidence_turn_id": f"native-hitl-{batch['batch_id']}-{mapping['question_id']}",
                    }
                )
    except ValueError as exc:
        code = str(exc) or "ANSWER_VALUE_TYPE_INVALID"
        messages = {
            "ANSWER_REQUIRED_VALUE_MISSING": "필수 질문의 답변이 비어 있습니다. 모든 필수 입력 항목을 채운 뒤 제출해 주세요.",
            "ANSWER_CHOICE_INVALID": "선택형 질문은 안내된 선택지와 일치해야 합니다.",
            "ANSWER_VALUE_TYPE_INVALID": "답변 형식이 질문의 입력 형식과 일치하지 않습니다.",
            "ANSWER_VALUE_TOO_LARGE": "답변 길이가 허용 범위를 초과했습니다.",
        }
        return _failure(code, messages.get(code, "답변 값이 유효하지 않습니다."), trace_id)
    try:
        revision = int(batch["revision"])
        round_number = int(batch["round_number"])
    except (TypeError, ValueError):
        return _failure("CLARIFICATION_BATCH_IDENTITY_INVALID", "질문 batch revision 또는 회차가 유효하지 않습니다.", trace_id)
    now = _utc_now()
    if str(now_utc or "").strip():
        try:
            parsed = datetime.fromisoformat(str(now_utc).replace("Z", "+00:00"))
            now = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except ValueError:
            return _failure("SUBMITTED_AT_INVALID", "제출 시각은 ISO-8601 형식이어야 합니다.", trace_id)
    identity = {
        "batch_id": batch["batch_id"],
        "work_definition_id": batch["work_definition_id"],
        "tenant_id": batch["tenant_id"],
        "owner_id": batch["owner_id"],
        "session_id": batch["session_id"],
        "channel_mode": batch["channel_mode"],
        "revision": revision,
        "round_number": round_number,
    }
    idempotency_material = _canonical({"request_id": str(request_id or ""), **identity, "answers": answers})
    idempotency_key = "hitl-" + hashlib.sha256(idempotency_material.encode("utf-8")).hexdigest()[:32]
    submission_id = "answer-" + hashlib.sha256((idempotency_material + "|submission").encode("utf-8")).hexdigest()[:24]
    answer_submission = {
        "schema_version": "native-clarification-answer-submission/v1",
        "submission_id": submission_id,
        **identity,
        "request_id": str(request_id or ""),
        "action_id": SUBMIT_ACTION,
        "answers": answers,
        "field_values": field_values,
        "idempotency_key": idempotency_key,
        "submitted_at": _iso(now),
        "source": "langflow_native_node_input",
    }
    return {
        "ok": True,
        "status": "ANSWER_SUBMITTED",
        "route": "branch_submit_answers",
        "artifact_refs": [
            {"kind": "clarification_batch", "id": batch["batch_id"]},
            {"kind": "answer_submission", "id": submission_id},
        ],
        "clarification_batch": copy.deepcopy(batch),
        "answer_submission": answer_submission,
        "human_decision": {"action_id": SUBMIT_ACTION, "values": copy.deepcopy(values)},
        "trace_id": trace_id,
    }


class F10ClarificationAnswerGateComponent(Component):
    """Pause F10 with real answer fields, then expose a selected native branch."""

    display_name = "42 보완 답변 HITL"
    description = "질문 Batch를 Langflow Playground의 답변 입력칸으로 표시하고 Submit Answers, 추가 입력 건너뛰기 또는 Cancel 결과를 원래 question_id 기준 Data로 반환합니다."
    icon = "FormInput"
    name = "F10ClarificationAnswerGate"

    inputs = [
        DataInput(
            name="clarification_batch",
            display_name="질문 Batch",
            input_types=["Data", "JSON"],
            required=True,
            info="13 재질문 Batch 생성의 재질문 Batch 출력을 연결합니다. 외부 Answer Form이나 API가 필요하지 않습니다.",
        ),
    ]
    outputs = [
        Output(name="answer_submission", display_name="답변 제출 Data", method="build_submission", types=["Data"]),
        Output(
            name="branch_submit_answers",
            display_name="Submit Answers",
            method="route_submission",
            types=["Data"],
            group_outputs=True,
        ),
        Output(
            name="branch_skip_additional_input",
            display_name="추가 입력 건너뛰기",
            method="route_submission",
            types=["Data"],
            group_outputs=True,
        ),
        Output(
            name="branch_cancel",
            display_name="Cancel",
            method="route_submission",
            types=["Data"],
            group_outputs=True,
        ),
        Output(
            name="blocked_path",
            display_name="입력/질문 오류",
            method="route_submission",
            types=["Data"],
            group_outputs=True,
        ),
    ]

    def _component_id(self) -> str:
        return _bounded_text(getattr(self, "_id", ""), 200) or self.name

    def _is_nonselected_group_output(self, selected: Any) -> bool:
        output_names = {"branch_submit_answers", "branch_skip_additional_input", "branch_cancel", "blocked_path"}
        current_output = str(getattr(self, "_current_output", "") or "")
        return bool(current_output and selected in output_names and current_output in output_names and current_output != selected)

    def _pause_request(self) -> dict[str, Any]:
        return build_pause_request(
            getattr(self, "clarification_batch", None),
            component_id=self._component_id(),
            run_id=getattr(getattr(self, "graph", None), "run_id", ""),
        )

    def _injected_decision(self, request_id: str) -> dict[str, Any] | None:
        graph = getattr(self, "graph", None)
        decisions = getattr(graph, "human_input_decisions", None) if graph is not None else None
        return decisions.get(request_id) if isinstance(decisions, dict) and isinstance(decisions.get(request_id), dict) else None

    def _result(self) -> dict[str, Any]:
        cached = getattr(self, "_answer_gate_result", None)
        if isinstance(cached, dict):
            # The checkpoint may restore the component instance after a pause.
            # Re-check its request id so a cached waiting envelope never hides
            # the decision injected by Langflow's resume endpoint.
            if cached.get("status") != "WAITING_ANSWER":
                return cached
            cached_request_id = ((cached.get("resume") or {}).get("request_id"))
            if self._injected_decision(str(cached_request_id or "")) is None:
                return cached
            self._answer_gate_result = None
        trace_id = f"trace-{uuid.uuid4()}"
        try:
            request = self._pause_request()
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            code = str(exc) or "CLARIFICATION_BATCH_INVALID"
            result = _failure(code, "질문 batch를 Playground HITL 입력폼으로 변환하지 못했습니다.", trace_id)
            self._answer_gate_result = result
            return result
        decision = self._injected_decision(request["request_id"])
        if decision is None:
            graph = getattr(self, "graph", None)
            if graph is None or not callable(getattr(graph, "request_pause", None)):
                result = _failure("HITL_GRAPH_UNAVAILABLE", "Langflow graph 세션이 없어 Human Input을 일시 중지할 수 없습니다.", trace_id)
            else:
                graph.request_pause(reason=HUMAN_INPUT_REQUIRED, data=request)
                result = {
                    "ok": True,
                    "status": "WAITING_ANSWER",
                    "route": None,
                    "artifact_refs": [{"kind": "clarification_batch", "id": request["batch_id"]}],
                    "clarification_batch": _batch(getattr(self, "clarification_batch", None)),
                    "answer_submission": None,
                    "resume": {"reason": HUMAN_INPUT_REQUIRED, "request_id": request["request_id"]},
                    "trace_id": trace_id,
                }
                self.status = "Awaiting clarification answers"
            self._answer_gate_result = result
            return result
        result = build_resumed_submission(
            getattr(self, "clarification_batch", None),
            decision,
            request_id=request["request_id"],
        )
        self._answer_gate_result = result
        return result

    def build_submission(self) -> Data:
        result = self._result()
        if result.get("route") in {"branch_cancel", "branch_skip_additional_input"}:
            # Cancel and explicit skip never carry answer data.  Each has a
            # dedicated branch so downstream answer persistence cannot run.
            self.stop("answer_submission")
        self.status = {"ok": result.get("ok"), "status": result.get("status"), "route": result.get("route")}
        return Data(data=copy.deepcopy(result))

    def route_submission(self) -> Data:
        result = self._result()
        selected = result.get("route")
        if selected is None:
            # The first build creates the native checkpoint.  Langflow snapshots
            # every connected group output before the user resumes the run, so
            # returning the non-empty WAITING_ANSWER envelope here would make
            # both Submit and Cancel look selected to the downstream component.
            # Keep those trigger ports explicitly empty until a real decision
            # exists; build_submission above still owns the pause request.
            self.status = {"ok": result.get("ok"), "status": result.get("status"), "route": None}
            return Data(data={})
        # On the first pass the graph pauses at its checkpoint boundary.  Do
        # not stop either branch before the user actually selects an action.
        if selected in {"branch_submit_answers", "branch_skip_additional_input", "branch_cancel", "blocked_path"}:
            non_selected = [
                output_name
                for output_name in ("branch_submit_answers", "branch_skip_additional_input", "branch_cancel", "blocked_path")
                if output_name != selected
            ]
            for output_name in ("branch_submit_answers", "branch_skip_additional_input", "branch_cancel", "blocked_path"):
                if output_name != selected:
                    self.stop(output_name)
            # Built-in Human Input persists the branches it did not select
            # before a later HITL checkpoint.  This is a custom component, so
            # record the equivalent conditional exclusion ourselves whenever
            # Langflow exposes that public graph method.
            graph = getattr(self, "graph", None)
            exclude = getattr(graph, "exclude_branches_conditionally", None) if graph is not None else None
            if callable(exclude):
                exclude(self._component_id(), non_selected)
        self.status = {"ok": result.get("ok"), "status": result.get("status"), "route": selected}
        if self._is_nonselected_group_output(selected):
            return Data(data={})
        return Data(data=copy.deepcopy(result))
