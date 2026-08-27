from __future__ import annotations

import copy
import json
import re
import uuid
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, Output
from lfx.schema import Data


PRIORITY_ORDER = {"safety": 0, "branch": 1, "contract": 2, "quality": 3}
FACT_STATUSES = {"confirmed", "inferred", "unknown", "conflicting"}


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


def _fact_status(value: Any) -> str:
    if isinstance(value, dict) and value.get("status") in FACT_STATUSES and "value" in value:
        return str(value["status"])
    if value in (None, "", [], {}):
        return "unknown"
    if isinstance(value, list):
        item_statuses = []
        for item in value:
            provenance = item.get("provenance", {}) if isinstance(item, dict) else {}
            status = provenance.get("status") if isinstance(provenance, dict) else None
            if status in FACT_STATUSES:
                item_statuses.append(status)
        if "conflicting" in item_statuses:
            return "conflicting"
        if item_statuses and all(status == "confirmed" for status in item_statuses):
            return "confirmed"
        return "inferred"
    return "inferred"


def _plain_text(value: Any) -> str:
    if isinstance(value, dict) and "value" in value:
        value = value.get("value")
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str).lower()
    return str(value or "").lower()


def _gap(code: str, path: str, priority: str, message: str, status: str = "unknown") -> dict[str, Any]:
    return {
        "reason_code": code,
        "target_paths": [path],
        "priority": priority,
        "current_status": status,
        "message": message,
    }


def evaluate_work_completeness(value: Any) -> dict[str, Any]:
    trace_id = f"trace-{uuid.uuid4()}"
    try:
        work = _payload(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        work = {}
    required_identity = ("work_definition_id", "tenant_id", "session_id", "revision")
    missing_identity = [key for key in required_identity if key not in work or work.get(key) in (None, "")]
    if missing_identity:
        return {
            "ok": False,
            "status": "BLOCKED",
            "artifact_refs": [],
            "error": {"code": "WORK_DEFINITION_SCHEMA_INVALID", "message": "완전성 평가에 필요한 WorkDefinition 식별자가 없습니다.", "retryable": False, "details": {"fields": missing_identity}},
            "resume": None,
            "trace_id": trace_id,
        }
    try:
        revision = int(work["revision"])
    except (TypeError, ValueError):
        return {
            "ok": False,
            "status": "BLOCKED",
            "artifact_refs": [],
            "error": {"code": "WORK_DEFINITION_REVISION_INVALID", "message": "완전성 평가 revision은 정수여야 합니다.", "retryable": False, "details": {}},
            "resume": None,
            "trace_id": trace_id,
        }

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
        gaps.append(_gap("WRITE_APPROVAL_UNKNOWN" if write_or_send else "SENSITIVE_REVIEW_UNKNOWN", "risks_controls", "safety", "외부 변경·발송 또는 민감정보 처리 전에 필요한 권한과 사람 검토 위치가 확인되지 않았습니다."))

    if not steps:
        gaps.append(_gap("STEP_SEQUENCE_UNKNOWN", "steps", "branch", "업무 단계의 순서가 확인되지 않았습니다."))
    for decision_index, decision in enumerate(decisions):
        if not isinstance(decision, dict):
            gaps.append(_gap("DECISION_SCHEMA_INVALID", "decisions", "branch", "분기 정보 형식이 올바르지 않습니다.", "conflicting"))
            continue
        branches = decision.get("branches") or decision.get("outcomes")
        condition = decision.get("condition")
        if not condition or not isinstance(branches, list) or len(branches) < 2:
            gaps.append(_gap("BRANCH_CONDITION_UNKNOWN", f"decisions[{decision_index}]", "branch", "판단 조건과 두 개 이상의 분기 결과가 확인되지 않았습니다."))

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
    overlap = sorted(text for text in scope_in & scope_out if text)
    if overlap:
        gaps.append({**_gap("SCOPE_CONFLICT", "scope_in", "branch", "포함 범위와 제외 범위가 충돌합니다.", "conflicting"), "conflicting_values": overlap[:20]})

    gaps.sort(key=lambda item: (PRIORITY_ORDER.get(item["priority"], 99), item["reason_code"], item["target_paths"][0]))
    blocking = bool(gaps)
    result_status = "NEEDS_CLARIFICATION" if blocking else "READY_FOR_REVIEW"
    evaluation = {
        "work_definition_id": work["work_definition_id"],
        "tenant_id": work["tenant_id"],
        "session_id": work["session_id"],
        "revision": revision,
        "channel_mode": work.get("channel_mode"),
        "needs_clarification": blocking,
        "blocking_gap_count": len(gaps),
        "blocking_gaps": gaps,
        "question_policy": {"max_questions_per_round": 3, "priority_order": ["safety", "branch", "contract", "quality"]},
    }
    return {
        "ok": True,
        "status": result_status,
        "artifact_refs": [{"kind": "work_definition", "id": work["work_definition_id"], "revision": revision}],
        "completeness": evaluation,
        "trace_id": trace_id,
    }


class WorkCompletenessEvaluatorComponent(Component):
    display_name = "12 업무 정의 완전성 평가"
    description = "필수 계약, 위험, 분기 충돌을 규칙으로 평가해 blocking gap과 재질문 필요 여부를 반환합니다."
    icon = "ListChecks"
    name = "WorkCompletenessEvaluator"

    inputs = [DataInput(name="work_definition", display_name="WorkDefinition", input_types=["Data", "JSON"], required=True)]
    outputs = [Output(name="completeness", display_name="완전성 평가", method="build_evaluation", types=["Data"])]

    def build_evaluation(self) -> Data:
        result = evaluate_work_completeness(getattr(self, "work_definition", None))
        self.status = {"ok": result["ok"], "status": result["status"], "gap_count": result.get("completeness", {}).get("blocking_gap_count", 0)}
        return Data(data=result)
