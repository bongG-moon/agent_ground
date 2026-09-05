from __future__ import annotations

"""Choose one successful F10 review entry without local imports."""

import copy
import json
import re
import uuid
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, Output
from lfx.schema import Data


_WORK_IDENTITY_FIELDS = (
    "work_definition_id",
    "tenant_id",
    "owner_id",
    "session_id",
    "channel_mode",
    "revision",
)
_INPUT_ORDER = (
    "initial_review",
    "round1_review",
    "round2_review",
    "round3_review",
    "round2_planner_review",
    "round3_planner_review",
    "round1_answer_review",
    "round2_answer_review",
    "round3_answer_review",
    "chat_answer_review",
)


def _payload(value: Any) -> dict[str, Any]:
    """Read a Data/JSON/Message value without treating empty ports as input."""

    if isinstance(value, dict):
        return copy.deepcopy(value)
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return copy.deepcopy(data)
    text = getattr(value, "text", value if isinstance(value, str) else "")
    if not isinstance(text, str) or not text.strip():
        return {}
    parsed = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE))
    return copy.deepcopy(parsed) if isinstance(parsed, dict) else {}


def _has_value(value: Any, payload: dict[str, Any]) -> bool:
    """Differentiate an unconnected optional port from a malformed supplied value."""

    if payload:
        return True
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return bool(data)
    text = getattr(value, "text", None)
    return isinstance(text, str) and bool(text.strip())


def _work_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("work_definition")
    return copy.deepcopy(nested) if isinstance(nested, dict) else copy.deepcopy(payload)


def _is_valid_work(work: dict[str, Any]) -> bool:
    return all(work.get(field) not in (None, "") for field in _WORK_IDENTITY_FIELDS)


def _is_explicit_failure(payload: dict[str, Any]) -> bool:
    if payload.get("ok") is False:
        return True
    return str(payload.get("status") or "").upper() in {
        "BLOCKED",
        "FAILED",
        "ERROR",
        "CANCELLED",
        "REJECTED",
    }


def _blocked_result(
    code: str,
    message: str,
    *,
    trace_id: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
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


def join_f10_review_entries(
    initial_review: Any = None,
    round1_review: Any = None,
    round2_review: Any = None,
    round3_review: Any = None,
    round2_planner_review: Any = None,
    round3_planner_review: Any = None,
    round1_answer_review: Any = None,
    round2_answer_review: Any = None,
    round3_answer_review: Any = None,
    chat_answer_review: Any = None,
) -> dict[str, Any]:
    """Route one valid review result; fail closed for stale or ambiguous branches."""

    trace_id = f"trace-{uuid.uuid4()}"
    values = (
        initial_review,
        round1_review,
        round2_review,
        round3_review,
        round2_planner_review,
        round3_planner_review,
        round1_answer_review,
        round2_answer_review,
        round3_answer_review,
        chat_answer_review,
    )
    candidates: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    explicit_failures: list[str] = []
    invalid_inputs: list[str] = []
    supplied_inputs: list[str] = []

    for input_name, value in zip(_INPUT_ORDER, values, strict=True):
        try:
            payload = _payload(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            if value is not None:
                supplied_inputs.append(input_name)
                invalid_inputs.append(input_name)
            continue

        if not _has_value(value, payload):
            continue
        supplied_inputs.append(input_name)
        if _is_explicit_failure(payload):
            explicit_failures.append(input_name)
            continue

        work = _work_from_payload(payload)
        is_success_envelope = payload.get("ok") is True
        is_direct_work = "ok" not in payload and _is_valid_work(work)
        if (is_success_envelope or is_direct_work) and _is_valid_work(work):
            candidates.append((input_name, payload, work))
        else:
            invalid_inputs.append(input_name)

    if len(candidates) == 1:
        selected_input, source, work = candidates[0]
        revision = work.get("revision")
        artifact_refs = source.get("artifact_refs")
        if not isinstance(artifact_refs, list):
            artifact_refs = [
                {
                    "kind": "work_definition",
                    "id": str(work["work_definition_id"]),
                    "revision": revision,
                }
            ]
        return {
            "ok": True,
            "status": str(source.get("status") or work.get("status") or "READY_FOR_REVIEW"),
            "route": "review_work_definition",
            "artifact_refs": copy.deepcopy(artifact_refs),
            "selected_input": selected_input,
            "work_definition": copy.deepcopy(work),
            "trace_id": str(source.get("trace_id") or trace_id)[:200],
        }

    if len(candidates) > 1:
        return _blocked_result(
            "F10_REVIEW_ENTRY_AMBIGUOUS",
            "검토 단계로 진입한 성공 결과가 둘 이상입니다.",
            trace_id=trace_id,
            details={
                "active_inputs": [input_name for input_name, _, _ in candidates],
                "failed_inputs": explicit_failures,
                "invalid_inputs": invalid_inputs,
            },
        )

    if explicit_failures:
        return _blocked_result(
            "F10_REVIEW_ENTRY_UPSTREAM_FAILED",
            "검토 전 단계가 실패하여 업무 정의 검토를 진행할 수 없습니다.",
            trace_id=trace_id,
            details={
                "failed_inputs": explicit_failures,
                "invalid_inputs": invalid_inputs,
            },
        )

    if not supplied_inputs:
        return {
            "ok": None,
            "status": "NO_INPUT",
            "route": "no_input",
            "artifact_refs": [],
            "trace_id": trace_id,
        }

    return _blocked_result(
        "F10_REVIEW_ENTRY_INVALID",
        "검토 단계로 전달된 결과에 유효한 WorkDefinition이 없습니다.",
        trace_id=trace_id,
        details={"supplied_inputs": supplied_inputs, "invalid_inputs": invalid_inputs},
    )


class F10ReviewEntryJoinerComponent(Component):
    display_name = "40 검토 진입 Joiner"
    description = "초기·1~3차 보완 또는 번호형 채팅 답변 결과 중 유효한 WorkDefinition 하나만 골라 최종 검토 단계로 보냅니다."
    icon = "GitMerge"
    name = "F10ReviewEntryJoiner"

    inputs = [
        DataInput(name="initial_review", display_name="초기 검토 결과", input_types=["Data", "JSON"], required=False, advanced=False),
        DataInput(name="round1_review", display_name="1차 보완 후 검토 결과", input_types=["Data", "JSON"], required=False, advanced=False),
        DataInput(name="round2_review", display_name="2차 보완 후 검토 결과", input_types=["Data", "JSON"], required=False, advanced=False),
        DataInput(name="round3_review", display_name="3차 보완 후 검토 결과", input_types=["Data", "JSON"], required=False, advanced=False),
        DataInput(name="round2_planner_review", display_name="2차 질문 전 검토 결과", input_types=["Data", "JSON"], required=False, advanced=False),
        DataInput(name="round3_planner_review", display_name="3차 질문 전 검토 결과", input_types=["Data", "JSON"], required=False, advanced=False),
        DataInput(name="round1_answer_review", display_name="1차 답변 검토 결과", input_types=["Data", "JSON"], required=False, advanced=False),
        DataInput(name="round2_answer_review", display_name="2차 답변 검토 결과", input_types=["Data", "JSON"], required=False, advanced=False),
        DataInput(name="round3_answer_review", display_name="3차 답변 검토 결과", input_types=["Data", "JSON"], required=False, advanced=False),
        DataInput(name="chat_answer_review", display_name="채팅 답변 검토 결과", input_types=["Data", "JSON"], required=False, advanced=False),
    ]
    outputs = [
        Output(name="review_work_definition", display_name="검토 WorkDefinition", method="route_review_entry", types=["Data"], group_outputs=True),
        Output(name="blocked_path", display_name="검토 진입 차단", method="route_review_entry", types=["Data"], group_outputs=True),
    ]

    def _component_id(self) -> str:
        return str(getattr(self, "_id", "") or self.name)[:200]

    def _select_output_route(self, selected: str) -> None:
        output_names = ("review_work_definition", "blocked_path")
        if selected not in output_names:
            for output_name in output_names:
                self.stop(output_name)
            return
        non_selected = [output_name for output_name in output_names if output_name != selected]
        for output_name in non_selected:
            self.stop(output_name)
        graph = getattr(self, "graph", None)
        exclude = getattr(graph, "exclude_branches_conditionally", None) if graph is not None else None
        if callable(exclude):
            exclude(self._component_id(), non_selected)

    def _is_nonselected_group_output(self, selected: str) -> bool:
        current_output = str(getattr(self, "_current_output", "") or "")
        return bool(
            current_output
            and selected in {"review_work_definition", "blocked_path"}
            and current_output in {"review_work_definition", "blocked_path"}
            and current_output != selected
        )

    def route_review_entry(self) -> Data:
        result = getattr(self, "_review_entry_result", None)
        if not isinstance(result, dict):
            result = join_f10_review_entries(
                getattr(self, "initial_review", None),
                getattr(self, "round1_review", None),
                getattr(self, "round2_review", None),
                getattr(self, "round3_review", None),
                getattr(self, "round2_planner_review", None),
                getattr(self, "round3_planner_review", None),
                getattr(self, "round1_answer_review", None),
                getattr(self, "round2_answer_review", None),
                getattr(self, "round3_answer_review", None),
                getattr(self, "chat_answer_review", None),
            )
            self._review_entry_result = result

        selected = str(result.get("route") or "blocked_path")
        self._select_output_route(selected)
        self.status = {
            "ok": result.get("ok"),
            "status": result.get("status"),
            "route": selected,
            "selected_input": result.get("selected_input"),
        }
        if self._is_nonselected_group_output(selected):
            return Data(data={})
        return Data(data=copy.deepcopy(result))
