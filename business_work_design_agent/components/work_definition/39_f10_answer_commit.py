from __future__ import annotations

"""Commit one F10 human-answer or cancel action without sibling imports.

This compact component intentionally owns the durable answer-resume boundary:
it reads the canonical answer batch from MongoDB, validates it against the
stored WorkDefinition, applies only an allowlisted answer target, and advances
the WorkDefinition through a revision-CAS write.  It does not call another
Flow, an HTTP API, or a sibling custom component.
"""

import copy
import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from lfx.custom import Component
from lfx.io import DataInput, IntInput, MessageTextInput, Output, SecretStrInput
from lfx.schema import Data
from pymongo import MongoClient
from pymongo.errors import ConfigurationError, ConnectionFailure, DuplicateKeyError, PyMongoError, ServerSelectionTimeoutError


ALLOWED_CHANNELS = {"native_hitl"}
ALLOWED_ANSWER_TYPES = {"text", "single_choice", "single_choice_with_text", "multi_choice", "boolean", "number"}
FACT_STATUSES = {"confirmed", "inferred", "unknown", "conflicting"}
PRIORITY_ORDER = {"safety": 0, "branch": 1, "contract": 2, "quality": 3}
FACT_FIELDS = {"goal", "trigger", "frequency_volume", "sla", "automation_intent"}
LIST_FIELDS = {
    "scope_in",
    "scope_out",
    "actors",
    "systems",
    "inputs",
    "outputs",
    "steps",
    "exceptions",
    "pains",
    "risks_controls",
    "constraints",
    "success_criteria",
    "assumptions",
}
MAX_ID_CHARS = 200
MAX_COLLECTION_CHARS = 200
MAX_FREE_TEXT_CHARS = 16_000
MAX_ANSWER_VALUE_BYTES = 64 * 1024
MAX_WORK_DOCUMENT_BYTES = 1_000_000
MAX_RECEIPTS = 100
# F10 keeps three HITL rounds.  The final round may expose one additional
# required field so a ten-gap first extraction can still be completed without
# silently weakening the completion contract or opening a fourth pause.
MAX_QUESTIONS = 4
MAX_TARGET_PATHS_PER_QUESTION = 10
SKIP_ACTION = "skip_additional_input"
SKIP_ROUTE = "branch_skip_additional_input"
NATIVE_SKIP_SCHEMA = "native-clarification-skip-submission/v1"
WORK_SKIP_SCHEMA = "work-clarification-skip/v1"


def _payload(value: Any) -> dict[str, Any]:
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


def _named(value: Any, *keys: str) -> dict[str, Any]:
    payload = _payload(value)
    for key in keys:
        nested = payload.get(key)
        if isinstance(nested, dict):
            return copy.deepcopy(nested)
    return payload


def _secret(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    return str(getter() if callable(getter) else value or "").strip()


def _utc(value: Any) -> datetime:
    text = str(value or "").strip()
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00")) if text else datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _failure(
    code: str,
    message: str,
    trace_id: str,
    details: dict[str, Any] | None = None,
    *,
    retryable: bool = False,
    work_definition: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "status": "BLOCKED",
        "artifact_refs": [],
        "error": {"code": code, "message": message, "retryable": retryable, "details": details or {}},
        "resume": None,
        "trace_id": trace_id,
        "route": "blocked_path",
    }
    if isinstance(work_definition, dict):
        result["work_definition"] = _public_work(work_definition)
    return result


def _public_work(value: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(value)
    result.pop("_id", None)
    result.pop("pending_action", None)
    return result


def _bounded_text(value: Any, *, max_chars: int = MAX_ID_CHARS) -> str:
    text = str(value or "").strip()
    return text if 0 < len(text) <= max_chars else ""


def _safe_collection(value: Any, default: str) -> str:
    name = str(value or default).strip()
    return name if re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", name or "") else ""


def _trigger_matches(value: Any, expected_route: str) -> bool:
    """Return true only for the HITL branch this input represents.

    Langflow evaluates every connected group output when a checkpoint is
    resumed.  Component 42 therefore returns the same result envelope to its
    Submit and Cancel output methods, even though one branch is marked
    inactive.  Truthiness alone would interpret that one envelope as both
    actions.  The explicit route is the durable action discriminator.

    The small text fallback preserves direct/unit callers of the legacy public
    function, but Flow execution must use the structured route.
    """
    if value is None:
        return False
    expected_actions = {
        "branch_submit_answers": {"submit", "submit_answers", "submit answers"},
        SKIP_ROUTE: {"skip", "skip_additional_input", "skip additional input"},
        "branch_cancel": {"cancel"},
    }
    if isinstance(value, str):
        return value.strip().lower() in expected_actions.get(expected_route, set())
    try:
        payload = _payload(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    route = str(payload.get("route") or "").strip()
    if route:
        return route == expected_route
    decision = payload.get("human_decision")
    if isinstance(decision, dict):
        action_id = str(decision.get("action_id") or "").strip().lower()
        return action_id in expected_actions.get(expected_route, set())
    # A direct native answer is necessarily a Submit action.  This protects
    # standalone callers that provide only the native answer contract.
    nested = payload.get("native_answer_submission") or payload.get("answer_submission")
    if expected_route == "branch_submit_answers" and isinstance(nested, dict):
        return str(nested.get("action_id") or "").strip().lower() == "submit_answers"
    skip_submission = payload.get("native_skip_submission") or payload.get("skip_submission")
    if expected_route == SKIP_ROUTE and isinstance(skip_submission, dict):
        return str(skip_submission.get("action_id") or "").strip().lower() == SKIP_ACTION
    return False


def _context(value: Any) -> tuple[dict[str, Any], dict[str, Any], int]:
    payload = _named(value, "clarification_context")
    work = payload.get("work_definition")
    completeness = payload.get("completeness")
    if not isinstance(work, dict) or not isinstance(completeness, dict):
        raise ValueError("CONTEXT_SCHEMA_INVALID")
    round_number = int(payload.get("round_number"))
    if round_number not in {1, 2, 3}:
        raise ValueError("ROUND_NUMBER_INVALID")
    return copy.deepcopy(work), copy.deepcopy(completeness), round_number


def _work_identity(work: dict[str, Any]) -> tuple[dict[str, Any], int]:
    required = ("work_definition_id", "tenant_id", "owner_id", "session_id", "channel_mode", "revision")
    missing = [name for name in required if work.get(name) in (None, "")]
    if missing:
        raise ValueError("WORK_IDENTITY_INVALID")
    identity = {name: _bounded_text(work.get(name)) for name in ("work_definition_id", "tenant_id", "owner_id", "session_id")}
    if not all(identity.values()):
        raise ValueError("WORK_IDENTITY_INVALID")
    channel = _bounded_text(work.get("channel_mode"))
    if channel not in ALLOWED_CHANNELS:
        raise ValueError("WORK_CHANNEL_INVALID")
    try:
        revision = int(work.get("revision"))
    except (TypeError, ValueError) as exc:
        raise ValueError("WORK_REVISION_INVALID") from exc
    if revision < 0:
        raise ValueError("WORK_REVISION_INVALID")
    identity["channel_mode"] = channel
    return identity, revision


def _require_same_identity(reference: dict[str, Any], current: dict[str, Any]) -> None:
    for name in ("work_definition_id", "tenant_id", "owner_id", "session_id", "channel_mode"):
        if str(reference.get(name) or "") != str(current.get(name) or ""):
            raise ValueError("WORK_CONTEXT_MISMATCH")


def _validate_context_completeness(work: dict[str, Any], completeness: dict[str, Any]) -> None:
    if str(completeness.get("work_definition_id") or "") != str(work.get("work_definition_id") or ""):
        raise ValueError("COMPLETENESS_CONTEXT_MISMATCH")
    try:
        if int(completeness.get("revision")) != int(work.get("revision")):
            raise ValueError("COMPLETENESS_CONTEXT_MISMATCH")
    except (TypeError, ValueError) as exc:
        if str(exc) == "COMPLETENESS_CONTEXT_MISMATCH":
            raise
        raise ValueError("COMPLETENESS_CONTEXT_MISMATCH") from exc


def _fact_status(value: Any) -> str:
    if isinstance(value, dict) and value.get("status") in FACT_STATUSES and "value" in value:
        return str(value.get("status"))
    if value in (None, "", [], {}):
        return "unknown"
    if isinstance(value, list):
        statuses: list[str] = []
        for item in value:
            provenance = item.get("provenance") if isinstance(item, dict) else None
            status = provenance.get("status") if isinstance(provenance, dict) else None
            if status in FACT_STATUSES:
                statuses.append(str(status))
        if "conflicting" in statuses:
            return "conflicting"
        if statuses and all(status == "confirmed" for status in statuses):
            return "confirmed"
        return "inferred"
    return "inferred"


def _plain_text(value: Any) -> str:
    if isinstance(value, dict) and "value" in value:
        value = value.get("value")
    return _canonical(value).lower() if isinstance(value, (dict, list)) else str(value or "").lower()


def _gap(code: str, path: str, priority: str, message: str, status: str = "unknown") -> dict[str, Any]:
    return {
        "reason_code": code,
        "target_paths": [path],
        "priority": priority,
        "current_status": status,
        "message": message,
    }


def evaluate_work_completeness(value: Any) -> dict[str, Any]:
    """Standalone copy of the F10 completeness contract used after merging."""
    trace_id = f"trace-{uuid.uuid4()}"
    try:
        work = _named(value, "work_definition")
        _, revision = _work_identity(work)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _failure("WORK_DEFINITION_SCHEMA_INVALID", "완전성 평가에 필요한 WorkDefinition 식별자가 없습니다.", trace_id)

    gaps: list[dict[str, Any]] = []
    for field, code, message in (
        ("goal", "GOAL_UNKNOWN", "업무 목적과 완료 결과가 확인되지 않았습니다."),
        ("trigger", "TRIGGER_UNKNOWN", "업무 시작 조건 또는 주기가 확인되지 않았습니다."),
        ("inputs", "INPUT_CONTRACT_UNKNOWN", "핵심 입력과 형식이 확인되지 않았습니다."),
        ("outputs", "OUTPUT_CONTRACT_UNKNOWN", "핵심 출력과 전달 대상이 확인되지 않았습니다."),
        ("actors", "PRIMARY_ACTOR_UNKNOWN", "주요 수행자 또는 책임자가 확인되지 않았습니다."),
    ):
        status = _fact_status(work.get(field))
        if status in {"unknown", "conflicting"}:
            gaps.append(_gap(code, field, "contract", message, status))

    steps = work.get("steps") if isinstance(work.get("steps"), list) else []
    decisions = work.get("decisions") if isinstance(work.get("decisions"), list) else []
    all_text = " ".join(_plain_text(work.get(field)) for field in ("steps", "systems", "outputs", "risks_controls", "automation_intent"))
    write_or_send = bool(re.search(r"(저장|수정|삭제|발송|전송|등록|승인|write|update|delete|send|post)", all_text))
    sensitive = bool(re.search(r"(개인정보|민감|기밀|비밀번호|인증|토큰|secret|personal|restricted)", all_text))
    review_present = "human_review" in _plain_text(work.get("as_is_graph")) or bool(re.search(r"(사람|담당자|검토|승인|human.review)", _plain_text(work.get("risks_controls"))))
    if (write_or_send or sensitive) and not review_present:
        gaps.append(
            _gap(
                "WRITE_APPROVAL_UNKNOWN" if write_or_send else "SENSITIVE_REVIEW_UNKNOWN",
                "risks_controls",
                "safety",
                "외부 변경·발송 또는 민감정보 처리 전에 필요한 권한과 사람 검토 위치가 확인되지 않았습니다.",
            )
        )
    if not steps:
        gaps.append(_gap("STEP_SEQUENCE_UNKNOWN", "steps", "branch", "업무 단계의 순서가 확인되지 않았습니다."))
    for index, decision in enumerate(decisions):
        if not isinstance(decision, dict):
            gaps.append(_gap("DECISION_SCHEMA_INVALID", "decisions", "branch", "분기 정보 형식이 올바르지 않습니다.", "conflicting"))
            continue
        branches = decision.get("branches") or decision.get("outcomes")
        if not decision.get("condition") or not isinstance(branches, list) or len(branches) < 2:
            gaps.append(_gap("BRANCH_CONDITION_UNKNOWN", f"decisions[{index}]", "branch", "판단 조건과 두 개 이상의 분기 결과가 확인되지 않았습니다."))
    for field, code, message in (
        ("exceptions", "FAILURE_POLICY_UNKNOWN", "실패·예외 발생 시 처리 방식이 확인되지 않았습니다."),
        ("sla", "SLA_UNKNOWN", "기한 또는 허용 처리 시간이 확인되지 않았습니다."),
        ("success_criteria", "SUCCESS_CRITERIA_UNKNOWN", "완료 품질을 판단할 측정 기준이 확인되지 않았습니다."),
    ):
        status = _fact_status(work.get(field))
        if status in {"unknown", "conflicting"}:
            gaps.append(_gap(code, field, "quality", message, status))
    scope_in = {_plain_text(item) for item in (work.get("scope_in") or [])}
    scope_out = {_plain_text(item) for item in (work.get("scope_out") or [])}
    overlap = sorted(item for item in scope_in & scope_out if item)
    if overlap:
        gaps.append({**_gap("SCOPE_CONFLICT", "scope_in", "branch", "포함 범위와 제외 범위가 충돌합니다.", "conflicting"), "conflicting_values": overlap[:20]})
    gaps.sort(key=lambda item: (PRIORITY_ORDER.get(str(item.get("priority")), 99), str(item.get("reason_code")), str((item.get("target_paths") or [""])[0])))
    evaluation = {
        "work_definition_id": work["work_definition_id"],
        "tenant_id": work["tenant_id"],
        "session_id": work["session_id"],
        "revision": revision,
        "channel_mode": work.get("channel_mode"),
        "needs_clarification": bool(gaps),
        "blocking_gap_count": len(gaps),
        "blocking_gaps": gaps,
        "question_policy": {"max_questions_per_round": 3, "priority_order": ["safety", "branch", "contract", "quality"]},
    }
    return {
        "ok": True,
        "status": "NEEDS_CLARIFICATION" if gaps else "READY_FOR_REVIEW",
        "artifact_refs": [{"kind": "work_definition", "id": work["work_definition_id"], "revision": revision}],
        "completeness": evaluation,
        "trace_id": trace_id,
    }


def _normalize_answer_value(question: dict[str, Any], value: Any) -> Any:
    answer_type = str(question.get("answer_type") or "text")
    if answer_type not in ALLOWED_ANSWER_TYPES:
        raise ValueError("ANSWER_VALUE_TYPE_INVALID")
    raw_choices = question.get("choices") if isinstance(question.get("choices"), list) else []
    choices = [str(item) for item in raw_choices if isinstance(item, str)][:20]
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


def _path_tokens(path: Any) -> list[str | int]:
    text = str(path or "")
    if not text or len(text) > 300 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\[\d+\])?", text):
        return []
    tokens: list[str | int] = []
    for key, index in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]", text):
        tokens.append(int(index) if index else key)
    if len(tokens) > 2 or any(isinstance(token, int) and token >= 500 for token in tokens):
        return []
    return tokens


def _allowed_path(tokens: list[str | int]) -> bool:
    if len(tokens) == 1 and isinstance(tokens[0], str) and tokens[0] in FACT_FIELDS | LIST_FIELDS:
        return True
    return len(tokens) == 2 and tokens[0] == "decisions" and isinstance(tokens[1], int)


def _get_path(root: Any, tokens: list[str | int]) -> Any:
    current = root
    for token in tokens:
        if isinstance(token, str) and isinstance(current, dict):
            current = current.get(token)
        elif isinstance(token, int) and isinstance(current, list) and 0 <= token < len(current):
            current = current[token]
        else:
            return None
    return current


def _set_path(root: dict[str, Any], tokens: list[str | int], value: Any) -> bool:
    if not _allowed_path(tokens) or not isinstance(tokens[0], str):
        return False
    if len(tokens) == 1:
        root[tokens[0]] = value
        return True
    decisions = root.get("decisions")
    if not isinstance(decisions, list) or not isinstance(tokens[1], int) or tokens[1] < 0 or tokens[1] >= len(decisions):
        return False
    decisions[tokens[1]] = value
    return True


def _same(left: Any, right: Any) -> bool:
    return _canonical(left) == _canonical(right)


def _evidence(existing: Any, turn_id: str) -> list[str]:
    result: list[str] = []
    current = existing if isinstance(existing, list) else []
    for item in current + [turn_id]:
        text = _bounded_text(item)
        if text and text not in result:
            result.append(text)
    return result[:MAX_RECEIPTS]


def _merge_fact(current: Any, incoming: Any, *, revision: int, evidence_turn_id: str, resolve_conflict: bool) -> tuple[dict[str, Any], bool]:
    fact = copy.deepcopy(current) if isinstance(current, dict) and current.get("status") in FACT_STATUSES and "value" in current else {
        "value": copy.deepcopy(current) if current not in (None, "") else None,
        "status": "unknown" if current in (None, "") else "inferred",
        "evidence_turn_ids": [],
        "confidence": 0.0 if current in (None, "") else 0.7,
        "last_updated_revision": max(0, revision - 1),
    }
    old_value = copy.deepcopy(fact.get("value"))
    evidence = _evidence(fact.get("evidence_turn_ids"), evidence_turn_id)
    if fact.get("status") in {"confirmed", "conflicting"} and not _same(old_value, incoming) and not resolve_conflict:
        candidates = copy.deepcopy(fact.get("conflicting_values")) if isinstance(fact.get("conflicting_values"), list) else []
        for candidate in (old_value, copy.deepcopy(incoming)):
            if not any(_same(candidate, known) for known in candidates):
                candidates.append(candidate)
        return {
            "value": old_value,
            "status": "conflicting",
            "conflicting_values": candidates[:20],
            "evidence_turn_ids": evidence,
            "confidence": 0.0,
            "last_updated_revision": revision,
        }, True
    return {
        "value": copy.deepcopy(incoming),
        "status": "confirmed",
        "evidence_turn_ids": evidence,
        "confidence": 1.0,
        "last_updated_revision": revision,
    }, False


def _confirmed_list(value: Any, *, path: str, revision: int, evidence_turn_id: str) -> list[dict[str, Any]]:
    raw_items = value if isinstance(value, list) else [value]
    result: list[dict[str, Any]] = []
    for index, item in enumerate(raw_items[:500]):
        record = copy.deepcopy(item) if isinstance(item, dict) else {"value": copy.deepcopy(item)}
        identity = record.get("id") or record.get("name") or record.get("label") or record.get("title") or record.get("value") or index
        material = f"{path}|{identity}"
        record.setdefault("id", f"{path.rstrip('s') or 'item'}-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:16]}")
        existing = record.get("provenance") if isinstance(record.get("provenance"), dict) else {}
        record["provenance"] = {
            "status": "confirmed",
            "evidence_turn_ids": _evidence(existing.get("evidence_turn_ids"), evidence_turn_id),
            "confidence": 1.0,
            "last_updated_revision": revision,
        }
        result.append(record)
    return result


def _merge_decision(current: Any, incoming: Any, *, revision: int, evidence_turn_id: str, resolve_conflict: bool) -> tuple[dict[str, Any], bool]:
    if isinstance(incoming, str):
        if len(incoming) > MAX_FREE_TEXT_CHARS:
            raise ValueError("ANSWER_DECISION_INVALID")
        try:
            incoming = json.loads(incoming)
        except json.JSONDecodeError as exc:
            raise ValueError("ANSWER_DECISION_INVALID") from exc
    if not isinstance(incoming, dict):
        raise ValueError("ANSWER_DECISION_INVALID")
    condition = _bounded_text(incoming.get("condition"), max_chars=2_000)
    branches = incoming.get("branches") if isinstance(incoming.get("branches"), list) else incoming.get("outcomes")
    if not condition or not isinstance(branches, list) or not 2 <= len(branches) <= 20:
        raise ValueError("ANSWER_DECISION_INVALID")
    normalized: list[dict[str, Any]] = []
    for index, branch in enumerate(branches):
        record = copy.deepcopy(branch) if isinstance(branch, dict) else {"label": str(branch)}
        label = _bounded_text(record.get("label") or record.get("name") or record.get("value"), max_chars=500)
        if not label:
            raise ValueError("ANSWER_DECISION_INVALID")
        record["label"] = label
        record.setdefault("branch_id", f"branch-{index + 1}")
        normalized.append(record)
    base = copy.deepcopy(current) if isinstance(current, dict) else {}
    previous = {"condition": base.get("condition"), "branches": base.get("branches") or base.get("outcomes")}
    candidate = {"condition": condition, "branches": normalized}
    provenance = base.get("provenance") if isinstance(base.get("provenance"), dict) else {}
    if provenance.get("status") == "confirmed" and not _same(previous, candidate) and not resolve_conflict:
        base["provenance"] = {
            "status": "conflicting",
            "evidence_turn_ids": _evidence(provenance.get("evidence_turn_ids"), evidence_turn_id),
            "confidence": 0.0,
            "last_updated_revision": revision,
        }
        return base, True
    base.pop("outcomes", None)
    base["condition"] = condition
    base["branches"] = normalized
    base["provenance"] = {
        "status": "confirmed",
        "evidence_turn_ids": _evidence(provenance.get("evidence_turn_ids"), evidence_turn_id),
        "confidence": 1.0,
        "last_updated_revision": revision,
    }
    return base, False


def _batch_reference(value: Any) -> dict[str, Any]:
    batch = _named(value, "clarification_batch")
    batch_id = _bounded_text(batch.get("batch_id"), max_chars=MAX_ID_CHARS)
    if not batch_id:
        raise ValueError("CLARIFICATION_BATCH_REFERENCE_INVALID")
    return batch


def _canonical_answered_batch(value: dict[str, Any]) -> dict[str, Any]:
    """Normalize the trusted HITL service's resume records to one answer view.

    The Answer Form service persists a submission as
    ``ANSWERED_PENDING_RESUME`` and may subsequently mark it ``RESUMED``.  A
    compact Langflow resume reads that MongoDB record directly, so it must use
    the nested immutable submission rather than expect a synthetic flat
    ``ANSWERED`` record.  The original batch identity remains authoritative;
    every identity carried by the nested submission is checked before copying
    answer fields out of it.
    """
    batch = copy.deepcopy(value)
    status = str(batch.get("status") or "")
    if status not in {"ANSWERED_PENDING_RESUME", "RESUMED"}:
        return batch
    submission = batch.get("answer_submission")
    if isinstance(submission, dict):
        for name in ("work_definition_id", "tenant_id", "batch_id", "channel_mode"):
            if str(submission.get(name) or "") != str(batch.get(name) or ""):
                raise ValueError("ANSWER_SUBMISSION_IDENTITY_MISMATCH")
        submitted_session = submission.get("session_id")
        if submitted_session not in (None, "") and str(submitted_session) != str(batch.get("session_id") or ""):
            raise ValueError("ANSWER_SUBMISSION_IDENTITY_MISMATCH")
        submitted_actor = submission.get("actor_id")
        if submitted_actor not in (None, "") and str(submitted_actor) != str(batch.get("owner_id") or ""):
            raise ValueError("ANSWER_SUBMISSION_ACTOR_MISMATCH")
        try:
            if int(submission.get("expected_revision")) != int(batch.get("revision")):
                raise ValueError("REVISION_CONFLICT")
        except (TypeError, ValueError) as exc:
            if str(exc) == "REVISION_CONFLICT":
                raise
            raise ValueError("ANSWER_SUBMISSION_REVISION_INVALID") from exc
        batch["answers"] = copy.deepcopy(submission.get("answers"))
        batch["answer_idempotency_key"] = submission.get("idempotency_key")
        batch["answered_at"] = submission.get("submitted_at")
        batch["answer_turn_id"] = submission.get("turn_id") or batch.get("answer_turn_id")
    elif batch.get("answers") is None:
        return batch
    batch["status"] = "ANSWERED"
    batch.pop("answer_submission", None)
    return batch


def _validate_batch(
    batch: dict[str, Any],
    batch_reference: dict[str, Any],
    durable_work: dict[str, Any],
    *,
    round_number: int,
    now: datetime,
) -> tuple[list[dict[str, Any]], str, str]:
    batch = _canonical_answered_batch(batch)
    _require_same_identity(batch, durable_work)
    for name in ("batch_id", "work_definition_id", "tenant_id", "owner_id", "session_id", "channel_mode", "revision", "round_number"):
        incoming = batch_reference.get(name)
        if incoming not in (None, "") and str(incoming) != str(batch.get(name)):
            raise ValueError("CLARIFICATION_BATCH_REFERENCE_MISMATCH")
    if str(batch.get("status") or "") != "ANSWERED":
        raise ValueError("ANSWER_FORM_NOT_SUBMITTED")
    try:
        if int(batch.get("revision")) != int(durable_work.get("revision")):
            raise ValueError("REVISION_CONFLICT")
        if int(batch.get("round_number")) != round_number:
            raise ValueError("CLARIFICATION_ROUND_MISMATCH")
    except (TypeError, ValueError) as exc:
        if str(exc) in {"REVISION_CONFLICT", "CLARIFICATION_ROUND_MISMATCH"}:
            raise
        raise ValueError("ANSWER_BATCH_REVISION_INVALID") from exc
    try:
        submitted_at = _utc(batch.get("answered_at"))
        expires_at = _utc(batch.get("answer_deadline_at") or batch.get("expires_at"))
    except (TypeError, ValueError) as exc:
        raise ValueError("ANSWER_BATCH_EXPIRY_INVALID") from exc
    if submitted_at >= expires_at:
        raise ValueError("ANSWER_BATCH_EXPIRED")
    idempotency_key = _bounded_text(batch.get("answer_idempotency_key"), max_chars=300)
    if not idempotency_key:
        raise ValueError("ANSWER_IDEMPOTENCY_KEY_REQUIRED")
    questions = [copy.deepcopy(item) for item in batch.get("questions", []) if isinstance(item, dict) and _bounded_text(item.get("question_id"))]
    if not 1 <= len(questions) <= MAX_QUESTIONS:
        raise ValueError("CLARIFICATION_QUESTION_CONTRACT_INVALID")
    question_ids = [str(item["question_id"]) for item in questions]
    if len(question_ids) != len(set(question_ids)):
        raise ValueError("CLARIFICATION_QUESTION_CONTRACT_INVALID")
    raw_answers = batch.get("answers")
    if isinstance(raw_answers, dict):
        raw_answers = [{"question_id": key, "value": value} for key, value in raw_answers.items()]
    if not isinstance(raw_answers, list) or not raw_answers:
        raise ValueError("ANSWER_LIST_INVALID")
    question_by_id = {str(item["question_id"]): item for item in questions}
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_answers[:MAX_QUESTIONS]:
        if not isinstance(item, dict):
            raise ValueError("ANSWER_ITEM_INVALID")
        question_id = _bounded_text(item.get("question_id"))
        if question_id not in question_by_id or question_id in seen:
            raise ValueError("ANSWER_QUESTION_INVALID")
        question = question_by_id[question_id]
        paths = question.get("target_paths") if isinstance(question.get("target_paths"), list) else []
        safe_paths: list[str] = []
        for raw_path in paths[:MAX_TARGET_PATHS_PER_QUESTION]:
            tokens = _path_tokens(raw_path)
            if not tokens or not _allowed_path(tokens):
                raise ValueError("ANSWER_TARGET_PATH_FORBIDDEN")
            path = str(raw_path)
            if path not in safe_paths:
                safe_paths.append(path)
        if not safe_paths:
            raise ValueError("ANSWER_TARGET_PATH_MISSING")
        try:
            answer_value = _normalize_answer_value(question, copy.deepcopy(item.get("value")))
        except ValueError:
            raise
        normalized.append(
            {
                "question_id": question_id,
                "value": answer_value,
                "target_paths": safe_paths,
                "reason_code": _bounded_text(question.get("reason_code"), max_chars=100),
                "resolve_conflict": bool(item.get("resolve_conflict", False)),
                "evidence_turn_id": _bounded_text(item.get("evidence_turn_id") or batch.get("answer_turn_id") or f"answer-{question_id}"),
            }
        )
        seen.add(question_id)
    if len(raw_answers) > MAX_QUESTIONS:
        raise ValueError("ANSWER_LIST_INVALID")
    missing_required = [question_id for question_id, question in question_by_id.items() if bool(question.get("required", True)) and question_id not in seen]
    if missing_required:
        raise ValueError("ANSWER_REQUIRED_QUESTIONS_MISSING")
    material = _canonical({"batch_id": batch.get("batch_id"), "idempotency_key": idempotency_key, "answers": normalized})
    return normalized, idempotency_key, hashlib.sha256(material.encode("utf-8")).hexdigest()


def _apply_answers(work: dict[str, Any], answers: list[dict[str, Any]], *, batch_id: str, idempotency_key: str, payload_hash: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    updated = copy.deepcopy(work)
    current_revision = int(updated.get("revision"))
    new_revision = current_revision + 1
    conflicts: list[dict[str, Any]] = []
    changed_paths: list[str] = []
    for answer in answers:
        for path in answer["target_paths"]:
            tokens = _path_tokens(path)
            if not _allowed_path(tokens):
                raise ValueError("ANSWER_TARGET_PATH_FORBIDDEN")
            current = _get_path(updated, tokens)
            if len(tokens) == 1 and str(tokens[0]) in LIST_FIELDS:
                incoming = _confirmed_list(answer["value"], path=path, revision=new_revision, evidence_turn_id=answer["evidence_turn_id"])
                confirmed = isinstance(current, list) and current and all(isinstance(item, dict) and ((item.get("provenance") or {}).get("status") == "confirmed") for item in current)
                if confirmed and not _same(current, incoming) and not answer["resolve_conflict"]:
                    conflicts.append({"path": path, "question_id": answer["question_id"], "reason": "CONFIRMED_VALUE_CHANGED"})
                    continue
                merged, conflict = incoming, False
            elif len(tokens) == 2 and tokens[0] == "decisions":
                merged, conflict = _merge_decision(current, answer["value"], revision=new_revision, evidence_turn_id=answer["evidence_turn_id"], resolve_conflict=answer["resolve_conflict"])
            else:
                merged, conflict = _merge_fact(current, answer["value"], revision=new_revision, evidence_turn_id=answer["evidence_turn_id"], resolve_conflict=answer["resolve_conflict"])
            if not _set_path(updated, tokens, merged):
                raise ValueError("ANSWER_TARGET_PATH_INVALID")
            changed_paths.append(path)
            if conflict:
                conflicts.append({"path": path, "question_id": answer["question_id"], "reason": "CONFIRMED_VALUE_CHANGED"})
    updated["revision"] = new_revision
    updated["approved_hash"] = None
    receipts = copy.deepcopy(updated.get("processed_answer_batches")) if isinstance(updated.get("processed_answer_batches"), list) else []
    receipts.append(
        {
            "batch_id": batch_id,
            "idempotency_key": idempotency_key,
            "payload_sha256": payload_hash,
            "resulting_revision": new_revision,
            "changed_paths": sorted(set(changed_paths)),
            "conflicts": copy.deepcopy(conflicts),
        }
    )
    updated["processed_answer_batches"] = receipts[-MAX_RECEIPTS:]
    return updated, conflicts


def _check_bounded_document(document: dict[str, Any]) -> None:
    if len(_canonical(document).encode("utf-8")) > MAX_WORK_DOCUMENT_BYTES:
        raise ValueError("WORK_DOCUMENT_TOO_LARGE")


def _initial_work_document(context_work: dict[str, Any], identity: dict[str, str], *, now: datetime) -> dict[str, Any]:
    """Create the first canonical record only from the validated F10 context."""
    _, revision = _work_identity(context_work)
    if revision != 0:
        raise ValueError("WORK_DEFINITION_NOT_FOUND")
    document = copy.deepcopy(context_work)
    document["_id"] = "work-definition:" + hashlib.sha256(
        f"{identity['tenant_id']}|{identity['work_definition_id']}".encode("utf-8")
    ).hexdigest()
    document["revision"] = 0
    document["status"] = "WAITING_ANSWER"
    document["approved_hash"] = None
    document["created_at"] = now
    document["updated_at"] = now
    document["processed_answer_batches"] = []
    document["f10_action_receipts"] = []
    _check_bounded_document(document)
    return document


def _load_or_create_initial_work(
    definitions: Any,
    identity: dict[str, str],
    context_work: dict[str, Any],
    *,
    now: datetime,
) -> tuple[dict[str, Any], bool]:
    query = {"tenant_id": identity["tenant_id"], "work_definition_id": identity["work_definition_id"]}
    existing = definitions.find_one(query)
    if isinstance(existing, dict):
        return existing, False
    initial = _initial_work_document(context_work, identity, now=now)
    try:
        definitions.insert_one(initial)
        return initial, True
    except DuplicateKeyError:
        existing = definitions.find_one(query)
        if isinstance(existing, dict):
            return existing, False
        raise


def _action_receipt(document: dict[str, Any], *, action: str, now: datetime, actor_id: str) -> None:
    receipts = copy.deepcopy(document.get("f10_action_receipts")) if isinstance(document.get("f10_action_receipts"), list) else []
    receipts.append({"action": action, "actor_id": actor_id, "recorded_at": now})
    document["f10_action_receipts"] = receipts[-MAX_RECEIPTS:]


def _route_result(
    work: dict[str, Any],
    evaluation: dict[str, Any],
    *,
    route: str,
    round_number: int,
    trace_id: str,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    public = _public_work(work)
    result: dict[str, Any] = {
        "ok": True,
        "status": str(public.get("status") or ""),
        "artifact_refs": [{"kind": "work_definition", "id": public.get("work_definition_id"), "revision": public.get("revision")}],
        "work_definition": public,
        "completeness": copy.deepcopy(evaluation),
        "round_number": round_number,
        "route": route,
        "trace_id": trace_id,
    }
    if route == "next_round_path":
        result["next_round_number"] = round_number + 1
    if error is not None:
        result["error"] = error
    return result


def _commit_cancel(
    definitions: Any,
    identity: dict[str, str],
    context_work: dict[str, Any],
    *,
    actor_id: str,
    now: datetime,
    trace_id: str,
) -> dict[str, Any]:
    durable: dict[str, Any] | None = None
    if actor_id != identity["owner_id"]:
        return _failure("ACTION_ACTOR_MISMATCH", "업무 owner만 질문 세션을 취소할 수 있습니다.", trace_id, work_definition=context_work)
    try:
        durable, _ = _load_or_create_initial_work(definitions, identity, context_work, now=now)
        _require_same_identity(context_work, durable)
        _, expected_revision = _work_identity(durable)
        if expected_revision != int(context_work.get("revision")):
            raise ValueError("REVISION_CONFLICT")
    except ValueError as exc:
        return _failure(str(exc), "취소 대상 WorkDefinition의 식별자 또는 channel이 일치하지 않습니다.", trace_id, work_definition=durable)
    if actor_id != str(durable.get("owner_id")):
        return _failure("ACTION_ACTOR_MISMATCH", "업무 owner만 질문 세션을 취소할 수 있습니다.", trace_id, work_definition=durable)
    if str(durable.get("status")) == "CANCELLED":
        return {
            "ok": True,
            "status": "CANCELLED",
            "artifact_refs": [{"kind": "work_definition", "id": durable["work_definition_id"], "revision": durable["revision"]}],
            "work_definition": _public_work(durable),
            "route": "cancelled_path",
            "trace_id": trace_id,
            "store_result": {"idempotent_replay": True, "revision": durable["revision"]},
        }
    cancelled = copy.deepcopy(durable)
    cancelled["revision"] = expected_revision + 1
    cancelled["status"] = "CANCELLED"
    cancelled["approved_hash"] = None
    cancelled["updated_at"] = now
    cancelled["cancelled_at"] = now
    _action_receipt(cancelled, action="cancel", now=now, actor_id=actor_id)
    try:
        _check_bounded_document(cancelled)
    except ValueError:
        return _failure("WORK_DOCUMENT_TOO_LARGE", "취소 결과 WorkDefinition이 허용 크기를 초과합니다.", trace_id, work_definition=durable)
    write = definitions.replace_one(
        {"tenant_id": identity["tenant_id"], "work_definition_id": identity["work_definition_id"], "revision": expected_revision},
        cancelled,
    )
    if int(getattr(write, "matched_count", 0)) != 1:
        return _failure("REVISION_CONFLICT", "취소 중 WorkDefinition revision 충돌이 발생했습니다.", trace_id, retryable=True, work_definition=durable)
    return {
        "ok": True,
        "status": "CANCELLED",
        "artifact_refs": [{"kind": "work_definition", "id": cancelled["work_definition_id"], "revision": cancelled["revision"]}],
        "work_definition": _public_work(cancelled),
        "route": "cancelled_path",
        "trace_id": trace_id,
        "store_result": {"idempotent_replay": False, "revision": cancelled["revision"]},
    }


def _native_submission(value: Any) -> dict[str, Any] | None:
    """Read the direct Playground answer emitted by Component 42.

    A native Human Input resume returns the selected action plus ``values`` to
    the custom gate.  The gate turns it into a bounded answer contract so this
    component can keep MongoDB as the canonical audit source without requiring
    a separate Answer Form service.
    """

    payload = _payload(value)
    nested = payload.get("native_answer_submission") or payload.get("answer_submission")
    candidate = nested if isinstance(nested, dict) else payload
    if not isinstance(candidate, dict):
        return None
    if str(candidate.get("schema_version") or "") != "native-clarification-answer-submission/v1":
        return None
    return copy.deepcopy(candidate)


def _native_skip_submission(value: Any) -> dict[str, Any] | None:
    """Read Component 42's explicit native HITL skip event.

    A skip is deliberately not treated as an empty answer submission.  It has
    its own action and schema so a user cannot accidentally submit partial
    values while asking to continue without more input.
    """

    payload = _payload(value)
    nested = payload.get("native_skip_submission") or payload.get("skip_submission")
    candidate = nested if isinstance(nested, dict) else payload
    if not isinstance(candidate, dict):
        return None
    if str(candidate.get("schema_version") or "") != NATIVE_SKIP_SCHEMA:
        return None
    return copy.deepcopy(candidate)


def _canonical_native_skip_submission(
    batch: dict[str, Any],
    native: dict[str, Any],
    *,
    actor_id: str,
    round_number: int,
    now: datetime,
) -> tuple[dict[str, Any], str]:
    """Validate a native skip action and make it a durable audit record."""

    if str(native.get("action_id") or "") != SKIP_ACTION:
        raise ValueError("NATIVE_SKIP_ACTION_INVALID")
    for name in (
        "batch_id",
        "work_definition_id",
        "tenant_id",
        "owner_id",
        "session_id",
        "channel_mode",
        "revision",
        "round_number",
    ):
        if name not in native or str(native.get(name)) != str(batch.get(name)):
            raise ValueError("NATIVE_SKIP_CONTEXT_MISMATCH")
    try:
        if int(native.get("round_number", -1)) != int(round_number):
            raise ValueError("CLARIFICATION_ROUND_MISMATCH")
    except (TypeError, ValueError) as exc:
        if str(exc) == "CLARIFICATION_ROUND_MISMATCH":
            raise
        raise ValueError("NATIVE_SKIP_CONTEXT_MISMATCH") from exc
    if str(native.get("owner_id") or "") != actor_id:
        raise ValueError("ACTION_ACTOR_MISMATCH")

    request_id = _bounded_text(native.get("request_id"), max_chars=MAX_ID_CHARS)
    questions = batch.get("questions") if isinstance(batch.get("questions"), list) else []
    question_ids = [_bounded_text(item.get("question_id"), max_chars=MAX_ID_CHARS) for item in questions if isinstance(item, dict)]
    if not request_id or not 1 <= len(question_ids) <= MAX_QUESTIONS or not all(question_ids) or len(question_ids) != len(set(question_ids)):
        raise ValueError("NATIVE_SKIP_SUBMISSION_INVALID")
    raw_skipped = native.get("skipped_question_ids")
    if not isinstance(raw_skipped, list):
        raise ValueError("NATIVE_SKIP_SUBMISSION_INVALID")
    skipped_question_ids = [_bounded_text(item, max_chars=MAX_ID_CHARS) for item in raw_skipped]
    # A skip applies to the entire displayed card.  Requiring this exact,
    # ordered list prevents a crafted event from hiding only selected fields.
    if skipped_question_ids != question_ids:
        raise ValueError("NATIVE_SKIP_SUBMISSION_INVALID")

    material = _canonical(
        {
            "batch_id": batch.get("batch_id"),
            "request_id": request_id,
            "action_id": SKIP_ACTION,
            "skipped_question_ids": skipped_question_ids,
        }
    )
    request_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
    idempotency_key = "native-hitl-skip-" + request_hash
    skip_id = "skip-" + hashlib.sha256(f"{batch.get('batch_id')}|{idempotency_key}|{request_hash}".encode("utf-8")).hexdigest()[:24]
    submission = {
        "schema_version": WORK_SKIP_SCHEMA,
        "skip_id": skip_id,
        "idempotency_key": idempotency_key,
        "channel_mode": batch.get("channel_mode"),
        "work_definition_id": batch.get("work_definition_id"),
        "tenant_id": batch.get("tenant_id"),
        "session_id": batch.get("session_id"),
        "batch_id": batch.get("batch_id"),
        "expected_revision": int(batch.get("revision")),
        "round_number": int(batch.get("round_number")),
        "skipped_question_ids": skipped_question_ids,
        "skipped_at": now,
        "actor_id": actor_id,
        "request_id": request_id,
        "action_id": SKIP_ACTION,
    }
    return submission, request_hash


def _native_pending_skip_batch(
    batch: dict[str, Any],
    native: dict[str, Any],
    *,
    actor_id: str,
    round_number: int,
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Create, but do not persist, an immutable explicit-skip batch record."""

    submission, request_hash = _canonical_native_skip_submission(
        batch,
        native,
        actor_id=actor_id,
        round_number=round_number,
        now=now,
    )
    candidate = copy.deepcopy(batch)
    candidate["status"] = "SKIPPED_PENDING_REVIEW"
    candidate["skip_submission"] = copy.deepcopy(submission)
    candidate["skip_idempotency_key"] = str(submission["idempotency_key"])
    candidate["skip_request_hash"] = request_hash
    return candidate, submission, request_hash


def _persist_native_skip(
    batches: Any,
    batch: dict[str, Any],
    native: dict[str, Any],
    *,
    actor_id: str,
    round_number: int,
    now: datetime,
) -> tuple[dict[str, Any], str]:
    """CAS-persist one explicit skip so it is auditable and idempotent."""

    candidate, submission, request_hash = _native_pending_skip_batch(
        batch,
        native,
        actor_id=actor_id,
        round_number=round_number,
        now=now,
    )
    idempotency_key = str(submission["idempotency_key"])
    identity = {
        "tenant_id": batch.get("tenant_id"),
        "work_definition_id": batch.get("work_definition_id"),
        "batch_id": batch.get("batch_id"),
        "status": "WAITING_ANSWER",
    }
    write = batches.replace_one(identity, candidate)
    if int(getattr(write, "matched_count", 0)) == 1:
        return candidate, str(submission["skip_id"])
    current = batches.find_one(
        {
            "tenant_id": batch.get("tenant_id"),
            "work_definition_id": batch.get("work_definition_id"),
            "batch_id": batch.get("batch_id"),
        }
    )
    if not isinstance(current, dict):
        raise ValueError("ANSWER_BATCH_NOT_FOUND")
    current_submission = current.get("skip_submission") if isinstance(current.get("skip_submission"), dict) else {}
    if (
        str(current.get("status") or "") in {"SKIPPED_PENDING_REVIEW", "RESUMED"}
        and str(current_submission.get("idempotency_key") or "") == idempotency_key
        and str(current.get("skip_request_hash") or "") == request_hash
    ):
        return current, str(current_submission.get("skip_id") or submission["skip_id"])
    raise ValueError("NATIVE_SKIP_ALREADY_SUBMITTED")


def _validate_skip_batch(
    batch: dict[str, Any],
    batch_reference: dict[str, Any],
    durable_work: dict[str, Any],
    *,
    round_number: int,
    now: datetime,
) -> None:
    """Validate a still-waiting batch before recording a user-approved skip."""

    _require_same_identity(batch, durable_work)
    for name in ("batch_id", "work_definition_id", "tenant_id", "owner_id", "session_id", "channel_mode", "revision", "round_number"):
        incoming = batch_reference.get(name)
        if incoming not in (None, "") and str(incoming) != str(batch.get(name)):
            raise ValueError("CLARIFICATION_BATCH_REFERENCE_MISMATCH")
    if str(batch.get("status") or "") not in {"WAITING_ANSWER", "SKIPPED_PENDING_REVIEW", "RESUMED"}:
        raise ValueError("ANSWER_FORM_NOT_SUBMITTED")
    try:
        if int(batch.get("revision")) != int(durable_work.get("revision")):
            raise ValueError("REVISION_CONFLICT")
        if int(batch.get("round_number")) != round_number:
            raise ValueError("CLARIFICATION_ROUND_MISMATCH")
        expires_at = _utc(batch.get("answer_deadline_at") or batch.get("expires_at"))
    except (TypeError, ValueError) as exc:
        if str(exc) in {"REVISION_CONFLICT", "CLARIFICATION_ROUND_MISMATCH"}:
            raise
        raise ValueError("ANSWER_BATCH_EXPIRY_INVALID") from exc
    if now >= expires_at:
        raise ValueError("ANSWER_BATCH_EXPIRED")


def _mark_skip_unresolved(
    work: dict[str, Any],
    batch: dict[str, Any],
    skip_submission: dict[str, Any],
    *,
    revision: int,
) -> list[dict[str, Any]]:
    """Append human-readable unresolved records for every skipped card field."""

    existing = copy.deepcopy(work.get("unresolved")) if isinstance(work.get("unresolved"), list) else []
    questions = batch.get("questions") if isinstance(batch.get("questions"), list) else []
    skipped_ids = {str(item) for item in skip_submission.get("skipped_question_ids", [])}
    batch_id = str(batch.get("batch_id") or "")
    evidence_turn_id = "skip-" + str(skip_submission.get("skip_id") or batch_id)
    replacements: dict[str, dict[str, Any]] = {}
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = _bounded_text(question.get("question_id"), max_chars=MAX_ID_CHARS)
        if not question_id or question_id not in skipped_ids:
            continue
        target_paths = [str(path) for path in (question.get("target_paths") or []) if _path_tokens(path)]
        reason_code = _bounded_text(question.get("reason_code"), max_chars=100) or "CLARIFICATION_SKIPPED"
        question_text = _bounded_text(question.get("text"), max_chars=4_000)
        summary = question_text or f"{reason_code} 확인"
        record_id = "unresolved-skip-" + hashlib.sha256(f"{batch_id}|{question_id}".encode("utf-8")).hexdigest()[:24]
        replacements[record_id] = {
            "id": record_id,
            "value": f"추가 입력 건너뜀: {summary}",
            "kind": "clarification_skipped",
            "skip_action": SKIP_ACTION,
            "batch_id": batch_id,
            "question_id": question_id,
            "reason_code": reason_code,
            "target_paths": target_paths,
            "provenance": {
                "status": "unknown",
                "evidence_turn_ids": [evidence_turn_id],
                "confidence": 0.0,
                "last_updated_revision": revision,
            },
        }
    retained = [item for item in existing if not (isinstance(item, dict) and item.get("id") in replacements)]
    unresolved = (retained + list(replacements.values()))[-1_000:]
    work["unresolved"] = unresolved
    return list(replacements.values())


def _canonical_native_submission(
    batch: dict[str, Any],
    native: dict[str, Any],
    *,
    actor_id: str,
    round_number: int,
    now: datetime,
) -> tuple[dict[str, Any], str]:
    """Validate a direct Playground answer and shape it like the old form record."""

    if str(native.get("action_id") or "") != "submit_answers":
        raise ValueError("NATIVE_ANSWER_ACTION_INVALID")
    for name in (
        "batch_id",
        "work_definition_id",
        "tenant_id",
        "owner_id",
        "session_id",
        "channel_mode",
        "revision",
        "round_number",
    ):
        if name not in native or str(native.get(name)) != str(batch.get(name)):
            raise ValueError("NATIVE_ANSWER_CONTEXT_MISMATCH")
    if int(native.get("round_number", -1)) != int(round_number):
        raise ValueError("CLARIFICATION_ROUND_MISMATCH")
    if str(native.get("owner_id") or "") != actor_id:
        raise ValueError("ACTION_ACTOR_MISMATCH")
    request_id = _bounded_text(native.get("request_id"), max_chars=MAX_ID_CHARS)
    raw_answers = native.get("answers")
    if not request_id or not isinstance(raw_answers, list) or not raw_answers:
        raise ValueError("NATIVE_ANSWER_SUBMISSION_INVALID")
    answers: list[dict[str, Any]] = []
    for item in raw_answers[:MAX_QUESTIONS]:
        if not isinstance(item, dict):
            raise ValueError("NATIVE_ANSWER_SUBMISSION_INVALID")
        question_id = _bounded_text(item.get("question_id"), max_chars=MAX_ID_CHARS)
        if not question_id:
            raise ValueError("NATIVE_ANSWER_SUBMISSION_INVALID")
        answers.append(
            {
                "question_id": question_id,
                "value": copy.deepcopy(item.get("value")),
                "resolve_conflict": bool(item.get("resolve_conflict", False)),
                "evidence_turn_id": _bounded_text(item.get("evidence_turn_id") or request_id, max_chars=MAX_ID_CHARS),
            }
        )
    if len(raw_answers) > MAX_QUESTIONS:
        raise ValueError("NATIVE_ANSWER_SUBMISSION_INVALID")
    material = _canonical(
        {
            "batch_id": batch.get("batch_id"),
            "request_id": request_id,
            "answers": answers,
        }
    )
    request_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
    idempotency_key = "native-hitl-" + request_hash
    submission_id = "answer-" + hashlib.sha256(f"{batch.get('batch_id')}|{idempotency_key}|{request_hash}".encode("utf-8")).hexdigest()[:24]
    submission = {
        "schema_version": "work-answer-submission/v1",
        "submission_id": submission_id,
        "idempotency_key": idempotency_key,
        "channel_mode": batch.get("channel_mode"),
        "work_definition_id": batch.get("work_definition_id"),
        "tenant_id": batch.get("tenant_id"),
        "session_id": batch.get("session_id"),
        "batch_id": batch.get("batch_id"),
        "expected_revision": int(batch.get("revision")),
        "answers": answers,
        "submitted_at": now,
        "actor_id": actor_id,
        "request_id": request_id,
    }
    return submission, request_hash


def _native_pending_batch(
    batch: dict[str, Any],
    native: dict[str, Any],
    *,
    actor_id: str,
    round_number: int,
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Create, but do not persist, the canonical pending native submission."""

    submission, request_hash = _canonical_native_submission(
        batch,
        native,
        actor_id=actor_id,
        round_number=round_number,
        now=now,
    )
    candidate = copy.deepcopy(batch)
    candidate["status"] = "ANSWERED_PENDING_RESUME"
    candidate["answer_submission"] = copy.deepcopy(submission)
    candidate["submission_idempotency_key"] = str(submission["idempotency_key"])
    candidate["submission_request_hash"] = request_hash
    return candidate, submission, request_hash


def _persist_native_submission(
    batches: Any,
    batch: dict[str, Any],
    native: dict[str, Any],
    *,
    actor_id: str,
    round_number: int,
    now: datetime,
) -> tuple[dict[str, Any], str]:
    """Atomically attach the direct-card answer to the immutable question batch.

    ``replace_one`` with the pending status is a small compare-and-set: a
    second click/retry can only reuse the exact same idempotency key and never
    overwrite a different answer submitted for the same question batch.
    """

    candidate, submission, request_hash = _native_pending_batch(
        batch,
        native,
        actor_id=actor_id,
        round_number=round_number,
        now=now,
    )
    idempotency_key = str(submission["idempotency_key"])
    identity = {
        "tenant_id": batch.get("tenant_id"),
        "work_definition_id": batch.get("work_definition_id"),
        "batch_id": batch.get("batch_id"),
        "status": "WAITING_ANSWER",
    }
    write = batches.replace_one(identity, candidate)
    if int(getattr(write, "matched_count", 0)) == 1:
        return candidate, str(submission["submission_id"])
    current = batches.find_one(
        {
            "tenant_id": batch.get("tenant_id"),
            "work_definition_id": batch.get("work_definition_id"),
            "batch_id": batch.get("batch_id"),
        }
    )
    if not isinstance(current, dict):
        raise ValueError("ANSWER_BATCH_NOT_FOUND")
    current_submission = current.get("answer_submission") if isinstance(current.get("answer_submission"), dict) else {}
    if (
        str(current.get("status") or "") in {"ANSWERED_PENDING_RESUME", "RESUMED"}
        and str(current_submission.get("idempotency_key") or "") == idempotency_key
        and str(current.get("submission_request_hash") or "") == request_hash
    ):
        return current, str(current_submission.get("submission_id") or submission["submission_id"])
    raise ValueError("NATIVE_ANSWER_ALREADY_SUBMITTED")


def _mark_native_submission_resumed(
    batches: Any,
    batch: dict[str, Any],
    submission_id: str,
    result: dict[str, Any],
    *,
    now: datetime,
) -> None:
    """Best-effort terminal audit stamp after the WorkDefinition CAS succeeds."""

    try:
        current = batches.find_one(
            {
                "tenant_id": batch.get("tenant_id"),
                "work_definition_id": batch.get("work_definition_id"),
                "batch_id": batch.get("batch_id"),
            }
        )
        if not isinstance(current, dict) or str(current.get("status") or "") == "RESUMED":
            return
        submission = current.get("answer_submission") if isinstance(current.get("answer_submission"), dict) else {}
        if str(submission.get("submission_id") or "") != submission_id:
            return
        current["status"] = "RESUMED"
        current["resumed_at"] = now
        current["resume_result"] = {
            "ok": bool(result.get("ok")),
            "status": str(result.get("status") or ""),
            "route": result.get("route"),
            "revision": ((result.get("work_definition") or {}).get("revision") if isinstance(result.get("work_definition"), dict) else None),
        }
        batches.replace_one(
            {
                "tenant_id": batch.get("tenant_id"),
                "work_definition_id": batch.get("work_definition_id"),
                "batch_id": batch.get("batch_id"),
                "status": "ANSWERED_PENDING_RESUME",
            },
            current,
        )
    except PyMongoError:
        # The canonical WorkDefinition has already been safely written.  A
        # later retry with the same idempotency key can complete this audit mark.
        return


def _mark_native_skip_resumed(
    batches: Any,
    batch: dict[str, Any],
    skip_id: str,
    result: dict[str, Any],
    *,
    now: datetime,
) -> None:
    """Best-effort terminal stamp for a persisted explicit skip event."""

    try:
        current = batches.find_one(
            {
                "tenant_id": batch.get("tenant_id"),
                "work_definition_id": batch.get("work_definition_id"),
                "batch_id": batch.get("batch_id"),
            }
        )
        if not isinstance(current, dict) or str(current.get("status") or "") == "RESUMED":
            return
        submission = current.get("skip_submission") if isinstance(current.get("skip_submission"), dict) else {}
        if str(submission.get("skip_id") or "") != skip_id:
            return
        current["status"] = "RESUMED"
        current["resumed_at"] = now
        current["resume_result"] = {
            "ok": bool(result.get("ok")),
            "status": str(result.get("status") or ""),
            "route": result.get("route"),
            "revision": ((result.get("work_definition") or {}).get("revision") if isinstance(result.get("work_definition"), dict) else None),
            "clarification_skipped": bool(result.get("clarification_skipped")),
        }
        batches.replace_one(
            {
                "tenant_id": batch.get("tenant_id"),
                "work_definition_id": batch.get("work_definition_id"),
                "batch_id": batch.get("batch_id"),
                "status": "SKIPPED_PENDING_REVIEW",
            },
            current,
        )
    except PyMongoError:
        # The canonical WorkDefinition has already been safely written.  A
        # later retry with the same idempotency key can complete this audit mark.
        return


def _skip_review_result(
    work: dict[str, Any],
    evaluation: dict[str, Any],
    skip_submission: dict[str, Any],
    unresolved_records: list[dict[str, Any]],
    *,
    round_number: int,
    trace_id: str,
    idempotent_replay: bool,
) -> dict[str, Any]:
    """Project an explicit-skip outcome to the normal F10 review path."""

    result = _route_result(work, evaluation, route="review_path", round_number=round_number, trace_id=trace_id)
    result["clarification_skipped"] = True
    result["skip_summary"] = {
        "action_id": SKIP_ACTION,
        "skip_id": str(skip_submission.get("skip_id") or ""),
        "batch_id": str(skip_submission.get("batch_id") or ""),
        "skipped_question_ids": copy.deepcopy(skip_submission.get("skipped_question_ids") or []),
        "unresolved_record_ids": [str(item.get("id") or "") for item in unresolved_records if isinstance(item, dict)],
        "remaining_gap_count": len(evaluation.get("blocking_gaps") or []),
        "message": "추가 입력을 건너뛰었습니다. 미확정 항목은 WorkDefinition의 unresolved와 completeness에 표시된 상태로 검토할 수 있습니다.",
    }
    result["store_result"] = {"idempotent_replay": idempotent_replay, "revision": work.get("revision")}
    return result


def _commit_skip(
    definitions: Any,
    batches: Any,
    identity: dict[str, str],
    context_work: dict[str, Any],
    context_completeness: dict[str, Any],
    batch_reference: dict[str, Any],
    *,
    round_number: int,
    actor_id: str,
    now: datetime,
    trace_id: str,
    native_skip_submission: dict[str, Any] | None,
) -> dict[str, Any]:
    """Record explicit user consent to continue review without more answers."""

    durable: dict[str, Any] | None = None
    if actor_id != identity["owner_id"]:
        return _failure("ACTION_ACTOR_MISMATCH", "업무 owner만 추가 입력을 건너뛸 수 있습니다.", trace_id, work_definition=context_work)
    batch_id = _bounded_text(batch_reference.get("batch_id"))
    batch = batches.find_one(
        {
            "tenant_id": identity["tenant_id"],
            "owner_id": identity["owner_id"],
            "work_definition_id": identity["work_definition_id"],
            "session_id": identity["session_id"],
            "batch_id": batch_id,
        }
    )
    if not isinstance(batch, dict):
        return _failure("ANSWER_BATCH_NOT_FOUND", "저장된 clarification batch를 찾을 수 없습니다.", trace_id, work_definition=context_work)
    if native_skip_submission is None:
        return _failure("NATIVE_SKIP_SUBMISSION_INVALID", "추가 입력 건너뛰기 이벤트의 형식이 올바르지 않습니다.", trace_id, work_definition=context_work)
    try:
        _validate_context_completeness(context_work, context_completeness)
        _preview_batch, canonical_skip, request_hash = _native_pending_skip_batch(
            batch,
            native_skip_submission,
            actor_id=actor_id,
            round_number=round_number,
            now=now,
        )
    except ValueError as exc:
        code = str(exc)
        messages = {
            "NATIVE_SKIP_ACTION_INVALID": "추가 입력 건너뛰기 action이 올바르지 않습니다.",
            "NATIVE_SKIP_CONTEXT_MISMATCH": "추가 입력 건너뛰기 이벤트가 현재 질문 batch와 일치하지 않습니다.",
            "NATIVE_SKIP_SUBMISSION_INVALID": "추가 입력 건너뛰기 이벤트의 질문 목록 또는 형식이 올바르지 않습니다.",
            "CLARIFICATION_ROUND_MISMATCH": "추가 입력 건너뛰기 회차가 현재 컨텍스트와 다릅니다.",
            "ACTION_ACTOR_MISMATCH": "업무 owner만 추가 입력을 건너뛸 수 있습니다.",
        }
        return _failure(code or "NATIVE_SKIP_SUBMISSION_INVALID", messages.get(code, "추가 입력 건너뛰기 이벤트를 읽을 수 없습니다."), trace_id, work_definition=context_work)
    try:
        durable, _ = _load_or_create_initial_work(definitions, identity, context_work, now=now)
        _require_same_identity(context_work, durable)
        _, expected_revision = _work_identity(durable)
    except (TypeError, ValueError) as exc:
        code = str(exc) if str(exc) else "WORK_CONTEXT_MISMATCH"
        return _failure(code, "질문 컨텍스트가 현재 저장된 WorkDefinition과 일치하지 않습니다.", trace_id, work_definition=durable)
    if actor_id != str(durable.get("owner_id")):
        return _failure("ACTION_ACTOR_MISMATCH", "업무 owner만 추가 입력을 건너뛸 수 있습니다.", trace_id, work_definition=durable)
    if str(durable.get("status")) == "CANCELLED":
        return _failure("WORK_TERMINAL_STATE", "취소된 WorkDefinition에서는 추가 입력을 건너뛸 수 없습니다.", trace_id, work_definition=durable)

    # A retry after the skip's work CAS has completed must return the same
    # review projection instead of reopening or changing the question card.
    current_skip = batch.get("skip_submission") if isinstance(batch.get("skip_submission"), dict) else {}
    if str(batch.get("status") or "") == "RESUMED":
        if (
            str(current_skip.get("idempotency_key") or "") != str(canonical_skip.get("idempotency_key") or "")
            or str(batch.get("skip_request_hash") or "") != request_hash
        ):
            return _failure("NATIVE_SKIP_ALREADY_SUBMITTED", "같은 질문 batch에는 다른 추가 입력 건너뛰기 요청을 적용할 수 없습니다.", trace_id, work_definition=durable)
        evaluation_result = evaluate_work_completeness(durable)
        if not evaluation_result.get("ok"):
            return _failure("WORK_COMPLETENESS_FAILED", "저장된 WorkDefinition의 완전성을 다시 평가할 수 없습니다.", trace_id, work_definition=durable)
        recorded_ids = {"unresolved-skip-" + hashlib.sha256(f"{batch_id}|{question_id}".encode("utf-8")).hexdigest()[:24] for question_id in canonical_skip["skipped_question_ids"]}
        unresolved_records = [item for item in (durable.get("unresolved") or []) if isinstance(item, dict) and item.get("id") in recorded_ids]
        return _skip_review_result(
            durable,
            evaluation_result["completeness"],
            current_skip,
            unresolved_records,
            round_number=round_number,
            trace_id=trace_id,
            idempotent_replay=True,
        )
    if expected_revision != int(context_work.get("revision")):
        return _failure("REVISION_CONFLICT", "추가 입력 건너뛰기 중 WorkDefinition revision 충돌이 발생했습니다.", trace_id, retryable=True, work_definition=durable)

    try:
        _validate_skip_batch(batch, batch_reference, durable, round_number=round_number, now=now)
        batch, skip_id = _persist_native_skip(
            batches,
            batch,
            native_skip_submission,
            actor_id=actor_id,
            round_number=round_number,
            now=now,
        )
        persisted_skip = batch.get("skip_submission") if isinstance(batch.get("skip_submission"), dict) else canonical_skip
    except ValueError as exc:
        code = str(exc)
        messages = {
            "ANSWER_BATCH_EXPIRED": "질문 batch 마감 이후에는 추가 입력 건너뛰기를 적용할 수 없습니다.",
            "ANSWER_BATCH_EXPIRY_INVALID": "질문 batch의 만료 시각이 유효하지 않습니다.",
            "CLARIFICATION_BATCH_REFERENCE_MISMATCH": "입력 batch 참조가 MongoDB의 질문 계약과 다릅니다.",
            "CLARIFICATION_ROUND_MISMATCH": "추가 입력 건너뛰기 회차가 현재 컨텍스트와 다릅니다.",
            "REVISION_CONFLICT": "질문 batch가 최신 WorkDefinition revision을 대상으로 하지 않습니다.",
            "NATIVE_SKIP_ALREADY_SUBMITTED": "같은 질문 batch에는 다른 추가 입력 건너뛰기 요청을 적용할 수 없습니다.",
            "NATIVE_SKIP_SUBMISSION_INVALID": "추가 입력 건너뛰기 이벤트의 질문 목록 또는 형식이 올바르지 않습니다.",
        }
        return _failure(code or "CLARIFICATION_SKIP_INVALID", messages.get(code, "추가 입력 건너뛰기 상태를 저장하지 못했습니다."), trace_id, work_definition=durable)

    skipped = copy.deepcopy(durable)
    skipped["revision"] = expected_revision + 1
    skipped["approved_hash"] = None
    unresolved_records = _mark_skip_unresolved(
        skipped,
        batch,
        persisted_skip,
        revision=int(skipped["revision"]),
    )
    skip_history = copy.deepcopy(skipped.get("clarification_skip_history")) if isinstance(skipped.get("clarification_skip_history"), list) else []
    skip_history.append(
        {
            "batch_id": batch_id,
            "skip_id": str(persisted_skip.get("skip_id") or skip_id),
            "idempotency_key": str(persisted_skip.get("idempotency_key") or ""),
            "skipped_question_ids": copy.deepcopy(persisted_skip.get("skipped_question_ids") or []),
            "recorded_at": now,
        }
    )
    skipped["clarification_skip_history"] = skip_history[-MAX_RECEIPTS:]
    processed = copy.deepcopy(skipped.get("processed_answer_batches")) if isinstance(skipped.get("processed_answer_batches"), list) else []
    processed.append(
        {
            "batch_id": batch_id,
            "action": SKIP_ACTION,
            "idempotency_key": str(persisted_skip.get("idempotency_key") or ""),
            "payload_sha256": str(batch.get("skip_request_hash") or request_hash),
            "resulting_revision": skipped["revision"],
            "changed_paths": [],
            "conflicts": [],
            "unresolved_record_ids": [str(item.get("id") or "") for item in unresolved_records],
        }
    )
    skipped["processed_answer_batches"] = processed[-MAX_RECEIPTS:]
    skipped["status"] = "READY_FOR_REVIEW"
    skipped["updated_at"] = now
    _action_receipt(skipped, action=SKIP_ACTION, now=now, actor_id=actor_id)
    evaluation_result = evaluate_work_completeness(skipped)
    if not evaluation_result.get("ok"):
        return _failure("WORK_COMPLETENESS_FAILED", "건너뛴 뒤 WorkDefinition의 완전성을 평가하지 못했습니다.", trace_id, work_definition=durable)
    evaluation = evaluation_result["completeness"]
    try:
        _check_bounded_document(skipped)
    except ValueError:
        return _failure("WORK_DOCUMENT_TOO_LARGE", "추가 입력 건너뛰기 결과 WorkDefinition이 허용 크기를 초과합니다.", trace_id, work_definition=durable)
    write = definitions.replace_one(
        {"tenant_id": identity["tenant_id"], "work_definition_id": identity["work_definition_id"], "revision": expected_revision},
        skipped,
    )
    if int(getattr(write, "matched_count", 0)) != 1:
        return _failure("REVISION_CONFLICT", "추가 입력 건너뛰기 반영 중 WorkDefinition revision 충돌이 발생했습니다.", trace_id, retryable=True, work_definition=durable)
    result = _skip_review_result(
        skipped,
        evaluation,
        persisted_skip,
        unresolved_records,
        round_number=round_number,
        trace_id=trace_id,
        idempotent_replay=False,
    )
    _mark_native_skip_resumed(batches, batch, skip_id, result, now=now)
    return result


def _commit_answers(
    definitions: Any,
    batches: Any,
    identity: dict[str, str],
    context_work: dict[str, Any],
    context_completeness: dict[str, Any],
    batch_reference: dict[str, Any],
    *,
    round_number: int,
    actor_id: str,
    now: datetime,
    trace_id: str,
    native_answer_submission: dict[str, Any] | None = None,
) -> dict[str, Any]:
    durable: dict[str, Any] | None = None
    if actor_id != identity["owner_id"]:
        return _failure("ACTION_ACTOR_MISMATCH", "업무 owner만 질문 답변을 반영할 수 있습니다.", trace_id, work_definition=context_work)
    batch_id = _bounded_text(batch_reference.get("batch_id"))
    batch = batches.find_one(
        {
            "tenant_id": identity["tenant_id"],
            "owner_id": identity["owner_id"],
            "work_definition_id": identity["work_definition_id"],
            "session_id": identity["session_id"],
            "batch_id": batch_id,
        }
    )
    if not isinstance(batch, dict):
        return _failure("ANSWER_BATCH_NOT_FOUND", "저장된 clarification batch를 찾을 수 없습니다.", trace_id, work_definition=context_work)
    try:
        _validate_context_completeness(context_work, context_completeness)
        native_submission_id = ""
        if native_answer_submission is not None:
            preview_batch, _preview_submission, _preview_hash = _native_pending_batch(
                batch,
                native_answer_submission,
                actor_id=actor_id,
                round_number=round_number,
                now=now,
            )
            # Reject an empty, expired, or type-invalid card before it changes
            # the durable batch state, so the user can correct it and resubmit.
            _validate_batch(preview_batch, batch_reference, context_work, round_number=round_number, now=now)
            batch, native_submission_id = _persist_native_submission(
                batches,
                batch,
                native_answer_submission,
                actor_id=actor_id,
                round_number=round_number,
                now=now,
            )
        answers, idempotency_key, payload_hash = _validate_batch(batch, batch_reference, context_work, round_number=round_number, now=now)
    except ValueError as exc:
        code = str(exc)
        messages = {
            "ANSWER_FORM_NOT_SUBMITTED": "질문 카드의 답변이 아직 제출되지 않았습니다.",
            "REVISION_CONFLICT": "답변 batch가 최신 WorkDefinition revision을 대상으로 하지 않습니다.",
            "CLARIFICATION_ROUND_MISMATCH": "답변 batch의 질문 회차가 현재 컨텍스트와 다릅니다.",
            "ANSWER_BATCH_EXPIRED": "질문 batch 마감 이후 제출된 답변은 반영할 수 없습니다.",
            "ANSWER_IDEMPOTENCY_KEY_REQUIRED": "저장된 답변에 idempotency key가 없습니다.",
            "ANSWER_REQUIRED_QUESTIONS_MISSING": "필수 질문 일부가 제출되지 않았습니다.",
            "ANSWER_TARGET_PATH_FORBIDDEN": "답변 target path가 허용된 업무 정의 필드가 아닙니다.",
            "CLARIFICATION_BATCH_REFERENCE_MISMATCH": "입력 batch 참조가 MongoDB의 질문 계약과 다릅니다.",
            "NATIVE_ANSWER_ACTION_INVALID": "질문 카드의 제출 action이 올바르지 않습니다.",
            "NATIVE_ANSWER_CONTEXT_MISMATCH": "질문 카드 답변이 현재 질문 batch와 일치하지 않습니다.",
            "NATIVE_ANSWER_SUBMISSION_INVALID": "질문 카드의 답변 형식이 올바르지 않습니다.",
            "NATIVE_ANSWER_ALREADY_SUBMITTED": "같은 질문 batch에 다른 답변이 이미 제출되어 있습니다.",
        }
        return _failure(code or "ANSWER_BATCH_INVALID", messages.get(code, "저장된 질문 또는 답변 계약이 유효하지 않습니다."), trace_id, work_definition=context_work)
    try:
        durable, _ = _load_or_create_initial_work(definitions, identity, context_work, now=now)
        _require_same_identity(context_work, durable)
        _, expected_revision = _work_identity(durable)
        if expected_revision != int(context_work.get("revision")):
            raise ValueError("REVISION_CONFLICT")
    except (TypeError, ValueError) as exc:
        code = str(exc) if str(exc) else "WORK_CONTEXT_MISMATCH"
        return _failure(code, "질문 컨텍스트가 현재 저장된 WorkDefinition과 일치하지 않습니다.", trace_id, work_definition=durable)
    if actor_id != str(durable.get("owner_id")):
        return _failure("ACTION_ACTOR_MISMATCH", "업무 owner만 질문 답변을 반영할 수 있습니다.", trace_id, work_definition=durable)
    if str(durable.get("status")) == "CANCELLED":
        return _failure("WORK_TERMINAL_STATE", "취소된 WorkDefinition에는 답변을 반영할 수 없습니다.", trace_id, work_definition=durable)
    processed = durable.get("processed_answer_batches") if isinstance(durable.get("processed_answer_batches"), list) else []
    prior = next((item for item in reversed(processed) if isinstance(item, dict) and item.get("batch_id") == batch_id), None)
    if prior is not None:
        if prior.get("idempotency_key") != idempotency_key or prior.get("payload_sha256") != payload_hash:
            return _failure("ANSWER_BATCH_ALREADY_PROCESSED", "같은 batch가 다른 답변 또는 idempotency key로 이미 처리되었습니다.", trace_id, work_definition=durable)
        evaluation_result = evaluate_work_completeness(durable)
        if not evaluation_result.get("ok"):
            return _failure("WORK_COMPLETENESS_FAILED", "저장된 WorkDefinition의 완전성을 다시 평가할 수 없습니다.", trace_id, work_definition=durable)
        evaluation = evaluation_result["completeness"]
        route = "review_path" if not evaluation["needs_clarification"] else ("blocked_path" if round_number >= 3 else "next_round_path")
        replay = _route_result(durable, evaluation, route=route, round_number=round_number, trace_id=trace_id)
        replay["store_result"] = {"idempotent_replay": True, "revision": durable["revision"]}
        if route == "blocked_path":
            replay["error"] = {"code": "CLARIFICATION_ROUND_LIMIT", "message": "세 번째 보완 후에도 필수 업무 정보가 남아 자동 진행을 차단했습니다.", "retryable": False, "details": {"remaining_gaps": evaluation["blocking_gaps"]}}
        if native_submission_id:
            _mark_native_submission_resumed(batches, batch, native_submission_id, replay, now=now)
        return replay
    try:
        merged, merge_conflicts = _apply_answers(durable, answers, batch_id=batch_id, idempotency_key=idempotency_key, payload_hash=payload_hash)
        evaluation_result = evaluate_work_completeness(merged)
        if not evaluation_result.get("ok"):
            raise ValueError("WORK_COMPLETENESS_FAILED")
        evaluation = evaluation_result["completeness"]
    except ValueError as exc:
        code = str(exc)
        return _failure(code or "ANSWER_MERGE_FAILED", "답변을 WorkDefinition에 안전하게 반영하지 못했습니다.", trace_id, work_definition=durable)

    if merge_conflicts:
        merged["status"] = "NEEDS_CLARIFICATION"
    elif not evaluation["needs_clarification"]:
        merged["status"] = "READY_FOR_REVIEW"
    elif round_number < 3:
        merged["status"] = "WAITING_ANSWER"
    else:
        merged["status"] = "BLOCKED"
    merged["updated_at"] = now
    _action_receipt(merged, action="submit_answers", now=now, actor_id=actor_id)
    try:
        _check_bounded_document(merged)
    except ValueError:
        return _failure("WORK_DOCUMENT_TOO_LARGE", "답변 반영 결과 WorkDefinition이 허용 크기를 초과합니다.", trace_id, work_definition=durable)
    write = definitions.replace_one(
        {"tenant_id": identity["tenant_id"], "work_definition_id": identity["work_definition_id"], "revision": expected_revision},
        merged,
    )
    if int(getattr(write, "matched_count", 0)) != 1:
        return _failure("REVISION_CONFLICT", "답변 반영 중 WorkDefinition revision 충돌이 발생했습니다.", trace_id, retryable=True, work_definition=durable)
    if not evaluation["needs_clarification"]:
        result = _route_result(merged, evaluation, route="review_path", round_number=round_number, trace_id=trace_id)
    elif round_number < 3:
        result = _route_result(merged, evaluation, route="next_round_path", round_number=round_number, trace_id=trace_id)
    else:
        result = _route_result(
            merged,
            evaluation,
            route="blocked_path",
            round_number=round_number,
            trace_id=trace_id,
            error={
                "code": "CLARIFICATION_ROUND_LIMIT",
                "message": "세 번째 보완 후에도 필수 업무 정보가 남아 자동 진행을 차단했습니다.",
                "retryable": False,
                "details": {"remaining_gaps": evaluation["blocking_gaps"]},
            },
        )
    if native_submission_id:
        _mark_native_submission_resumed(batches, batch, native_submission_id, result, now=now)
    return result


def commit_answer_or_cancel(
    clarification_context_value: Any,
    clarification_batch_value: Any,
    *,
    native_answer_submission: Any = None,
    submit_trigger: Any = None,
    skip_trigger: Any = None,
    cancel_trigger: Any = None,
    actor_id: Any = "",
    mongodb_uri: Any = "",
    mongo_database: Any = "",
    work_collection: Any = "work_definitions",
    batch_collection: Any = "clarification_batches",
    timeout_ms: Any = 5000,
    now_utc: Any = "",
    trace_id: Any = "",
    client_factory: Callable[..., Any] = MongoClient,
) -> dict[str, Any]:
    """Commit exactly one Human Input route; no trigger is deliberately a no-op."""
    safe_trace = _bounded_text(trace_id, max_chars=MAX_ID_CHARS) or f"trace-{uuid.uuid4()}"
    # Do not use generic truthiness here.  A native HITL gate has two outgoing
    # trigger edges, and Langflow can materialize the same result envelope for
    # both during checkpoint resume.  Each input must match its own route.
    submit = _trigger_matches(submit_trigger, "branch_submit_answers")
    skip = _trigger_matches(skip_trigger, SKIP_ROUTE)
    cancel = _trigger_matches(cancel_trigger, "branch_cancel")
    if not submit and not skip and not cancel:
        try:
            waiting_work, _, waiting_round = _context(clarification_context_value)
            safe_work = _public_work(waiting_work)
        except (TypeError, ValueError, json.JSONDecodeError):
            safe_work, waiting_round = None, None
        result: dict[str, Any] = {
            "ok": True,
            "status": "WAITING_ANSWER",
            "artifact_refs": [],
            "error": None,
            "resume": {"reason": "human_input_required"},
            "trace_id": safe_trace,
            "route": None,
        }
        if isinstance(safe_work, dict):
            result["work_definition"] = safe_work
        if waiting_round is not None:
            result["round_number"] = waiting_round
        return result
    if sum(bool(action) for action in (submit, skip, cancel)) > 1:
        return _failure("HUMAN_ACTION_AMBIGUOUS", "답변 제출, 추가 입력 건너뛰기, 취소 중 하나만 처리할 수 있습니다.", safe_trace)
    try:
        context_work, context_completeness, round_number = _context(clarification_context_value)
        identity, _ = _work_identity(context_work)
        batch_reference = _batch_reference(clarification_batch_value)
        selected_actor = _bounded_text(actor_id) or identity["owner_id"]
        uri = _secret(mongodb_uri)
        database_name = _bounded_text(mongo_database)
        work_name = _safe_collection(work_collection, "work_definitions")
        batch_name = _safe_collection(batch_collection, "clarification_batches")
        timeout = max(1_000, min(int(timeout_ms), 30_000))
        now = _utc(now_utc)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _failure("F10_ANSWER_COMMIT_INPUT_INVALID", "답변 반영에 필요한 컨텍스트 또는 MongoDB 설정이 유효하지 않습니다.", safe_trace)
    if not uri or not database_name or not work_name or not batch_name:
        return _failure("F10_ANSWER_COMMIT_CONFIG_MISSING", "답변 반영용 MongoDB 설정이 필요합니다.", safe_trace)
    client = None
    try:
        client = client_factory(uri, serverSelectionTimeoutMS=timeout, connectTimeoutMS=timeout, socketTimeoutMS=timeout, retryWrites=True)
        client.admin.command("ping")
        database = client[database_name]
        definitions = database[work_name]
        batches = database[batch_name]
        if cancel:
            return _commit_cancel(definitions, identity, context_work, actor_id=selected_actor, now=now, trace_id=safe_trace)
        if skip:
            return _commit_skip(
                definitions,
                batches,
                identity,
                context_work,
                context_completeness,
                batch_reference,
                round_number=round_number,
                actor_id=selected_actor,
                now=now,
                trace_id=safe_trace,
                native_skip_submission=_native_skip_submission(skip_trigger),
            )
        return _commit_answers(
            definitions,
            batches,
            identity,
            context_work,
            context_completeness,
            batch_reference,
            round_number=round_number,
            actor_id=selected_actor,
            now=now,
            trace_id=safe_trace,
            native_answer_submission=_native_submission(native_answer_submission),
        )
    except (ConfigurationError, ConnectionFailure, ServerSelectionTimeoutError):
        return _failure("MONGODB_UNAVAILABLE", "MongoDB에 연결할 수 없습니다.", safe_trace, retryable=True)
    except PyMongoError as exc:
        return _failure("MONGODB_WRITE_FAILED", "답변 상태를 MongoDB에 반영하지 못했습니다.", safe_trace, {"exception_type": type(exc).__name__}, retryable=True)
    finally:
        if client is not None:
            client.close()


class F10AnswerCommitComponent(Component):
    display_name = "39 답변 반영·다음 단계"
    description = "질문 카드의 답변 제출·추가 입력 건너뛰기·취소 중 하나를 한 번만 반영합니다. 선택하지 않은 회차는 자동 제외해 검토 Joiner가 읽지 않게 하며, 건너뛰기는 미확정 항목을 기록한 채 검토로 보냅니다."
    icon = "CircleCheckBig"
    name = "F10AnswerCommit"

    inputs = [
        DataInput(name="clarification_context", display_name="질문 Context", input_types=["Data", "JSON"], required=True, advanced=False),
        DataInput(name="clarification_batch", display_name="질문 Batch", input_types=["Data", "JSON"], required=True, advanced=False),
        DataInput(
            name="native_answer_submission",
            display_name="질문 카드 답변 (자동 연결)",
            input_types=["Data", "JSON"],
            required=False,
            advanced=False,
            info="42 질문 카드 입력 노드가 Submit Answers 선택 뒤 전달하는 값입니다. 직접 입력하지 않습니다.",
        ),
        DataInput(
            name="submit_trigger",
            display_name="답변 제출 Trigger",
            input_types=["Data", "JSON", "Message"],
            required=False,
            advanced=False,
            info="42 질문 카드의 Submit Answers branch가 자동 연결됩니다.",
        ),
        DataInput(
            name="skip_trigger",
            display_name="추가 입력 건너뛰기 Trigger",
            input_types=["Data", "JSON", "Message"],
            required=False,
            advanced=False,
            info="42 질문 카드의 추가 입력 건너뛰기 branch가 자동 연결됩니다. 미확정 항목을 기록하고 검토 단계로 진행합니다.",
        ),
        DataInput(
            name="cancel_trigger",
            display_name="취소 Trigger",
            input_types=["Data", "JSON", "Message"],
            required=False,
            advanced=False,
            info="42 질문 카드의 Cancel branch가 자동 연결됩니다.",
        ),
        MessageTextInput(name="actor_id", display_name="Actor ID (기본 owner_id)", value="", required=False),
        SecretStrInput(
            name="mongodb_uri",
            display_name="MongoDB URI (환경 설정)",
            required=True,
            info="F10의 답변 반영·revision 저장에 필요한 환경 Secret입니다. 세 회차에 같은 URI를 설정합니다.",
        ),
        MessageTextInput(
            name="mongo_database",
            display_name="MongoDB Database (환경 설정)",
            value="business_work_design",
            required=True,
            info="F10의 답변 반영 저장에 사용하는 Database입니다. 기본값을 유지합니다.",
        ),
        MessageTextInput(name="work_collection", display_name="WorkDefinition Collection", value="work_definitions", advanced=True),
        MessageTextInput(name="batch_collection", display_name="질문 Batch Collection", value="clarification_batches", advanced=True),
        IntInput(name="timeout_ms", display_name="MongoDB Timeout(ms)", value=5000, advanced=True),
        MessageTextInput(name="now_utc", display_name="기준 시각(ISO-8601)", value="", advanced=True),
        MessageTextInput(name="trace_id", display_name="Trace ID", value="", advanced=True),
    ]
    outputs = [
        Output(name="next_round_path", display_name="다음 질문 회차", method="route_commit", types=["Data"], group_outputs=True),
        Output(name="review_path", display_name="검토 준비 완료", method="route_commit", types=["Data"], group_outputs=True),
        Output(name="cancelled_path", display_name="취소 완료", method="route_commit", types=["Data"], group_outputs=True),
        Output(name="blocked_path", display_name="답변 반영 차단", method="route_commit", types=["Data"], group_outputs=True),
    ]

    def _component_id(self) -> str:
        """Return the graph vertex id when available for branch exclusion."""

        return _bounded_text(getattr(self, "_id", ""), max_chars=MAX_ID_CHARS) or self.name

    def _select_output_route(self, selected: Any) -> None:
        """Keep only the selected route runnable across a HITL checkpoint resume.

        ``stop`` handles the current scheduling pass, but Langflow 1.11.1 only
        treats a never-built predecessor as an optional default after it is in
        ``conditionally_excluded_vertices``.  F10's review joiner has inputs
        from all three answer-commit nodes, so without the persistent
        exclusion it can try to read a future, unbuilt commit node when the
        current round goes directly to review (including explicit skip).
        """

        output_names = ("next_round_path", "review_path", "cancelled_path", "blocked_path")
        if selected not in output_names:
            # No native decision has selected a route yet.  Preserve the
            # existing no-op behavior, but do not persist an exclusion before
            # the user acts.
            for output_name in output_names:
                self.stop(output_name)
            return

        non_selected = [output_name for output_name in output_names if output_name != selected]
        for output_name in non_selected:
            self.stop(output_name)

        graph = getattr(self, "graph", None)
        exclude = getattr(graph, "exclude_branches_conditionally", None) if graph is not None else None
        if callable(exclude):
            # Langflow preserves shared downstream nodes that remain reachable
            # through the selected output, so the review/result joiners stay
            # active while future-round branches are excluded.
            exclude(self._component_id(), non_selected)

    def _is_nonselected_group_output(self, selected: Any) -> bool:
        output_names = {"next_round_path", "review_path", "cancelled_path", "blocked_path"}
        current_output = str(getattr(self, "_current_output", "") or "")
        return bool(current_output and selected in output_names and current_output in output_names and current_output != selected)

    def route_commit(self) -> Data:
        result = getattr(self, "_commit_result", None)
        if not isinstance(result, dict):
            result = commit_answer_or_cancel(
                getattr(self, "clarification_context", None),
                getattr(self, "clarification_batch", None),
                native_answer_submission=getattr(self, "native_answer_submission", None),
                submit_trigger=getattr(self, "submit_trigger", None),
                skip_trigger=getattr(self, "skip_trigger", None),
                cancel_trigger=getattr(self, "cancel_trigger", None),
                actor_id=getattr(self, "actor_id", ""),
                mongodb_uri=getattr(self, "mongodb_uri", ""),
                mongo_database=getattr(self, "mongo_database", ""),
                work_collection=getattr(self, "work_collection", "work_definitions"),
                batch_collection=getattr(self, "batch_collection", "clarification_batches"),
                timeout_ms=getattr(self, "timeout_ms", 5000),
                now_utc=getattr(self, "now_utc", ""),
                trace_id=getattr(self, "trace_id", ""),
                client_factory=MongoClient,
            )
            self._commit_result = result
        selected = result.get("route")
        self._select_output_route(selected)
        self.status = {"ok": result.get("ok"), "status": result.get("status"), "route": selected}
        if self._is_nonselected_group_output(selected):
            return Data(data={})
        return Data(data=copy.deepcopy(result))
