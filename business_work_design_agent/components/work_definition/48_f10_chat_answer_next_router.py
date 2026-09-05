from __future__ import annotations

"""Route a committed Playground-chat answer to its exact later-question round.

The component stays standalone and only inspects Component 39's sealed
``next_round_path`` envelope.  Component 39 sends its ``review_path`` directly
to the review Joiner, rather than combining two grouped outputs into one
non-list input.  This prevents a round-one answer from accidentally starting
both the second and third clarification planners when a later Chat Input run
resumes the parent Flow.
"""

import copy
import json
import re
import uuid
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, Output
from lfx.schema import Data


def _payload(value: Any) -> dict[str, Any]:
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


def _failure(code: str, message: str, trace_id: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "BLOCKED",
        "route": "blocked_path",
        "artifact_refs": [],
        "error": {"code": code, "message": message, "retryable": False, "details": details or {}},
        "resume": None,
        "trace_id": trace_id,
    }


def route_chat_answer_commit(answer_commit: Any) -> dict[str, Any]:
    """Choose the next F10 planner/review exit from one Component 39 result."""

    trace_id = f"trace-{uuid.uuid4()}"
    try:
        payload = _payload(answer_commit)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _failure("CHAT_ANSWER_COMMIT_PAYLOAD_INVALID", "채팅 답변 반영 결과를 읽을 수 없습니다.", trace_id)
    if payload.get("ok") is not True:
        return _failure(
            "CHAT_ANSWER_COMMIT_FAILED",
            "채팅 답변을 업무 정의에 반영하지 못했습니다.",
            str(payload.get("trace_id") or trace_id)[:200],
            {"upstream_code": ((payload.get("error") or {}).get("code") if isinstance(payload.get("error"), dict) else None)},
        )
    route = str(payload.get("route") or "")
    if route != "next_round_path":
        return _failure(
            "CHAT_ANSWER_NEXT_ROUTE_INVALID",
            "채팅 답변 반영 결과가 다음 질문 회차로 연결되지 않았습니다.",
            str(payload.get("trace_id") or trace_id)[:200],
            {"route": route},
        )
    try:
        next_round = int(payload.get("next_round_number"))
    except (TypeError, ValueError):
        return _failure("CHAT_ANSWER_NEXT_ROUND_INVALID", "다음 질문 회차를 확인할 수 없습니다.", str(payload.get("trace_id") or trace_id)[:200])
    if next_round == 2:
        selected = "round2_path"
    elif next_round == 3:
        selected = "round3_path"
    else:
        return _failure(
            "CHAT_ANSWER_NEXT_ROUND_INVALID",
            "세 번을 초과하는 추가 질문은 만들 수 없습니다.",
            str(payload.get("trace_id") or trace_id)[:200],
            {"next_round_number": next_round},
        )
    result = copy.deepcopy(payload)
    result["route"] = selected
    return result


class F10ChatAnswerNextRouterComponent(Component):
    display_name = "48 다음 질문 회차 선택"
    description = "번호형 채팅 답변 반영 후 2차 또는 3차 질문 중 하나만 열어 중복 실행을 막습니다. 검토 완료는 39번에서 Joiner로 직접 연결됩니다."
    icon = "Route"
    name = "F10ChatAnswerNextRouter"

    inputs = [
        DataInput(
            name="answer_commit",
            display_name="채팅 답변 반영 결과",
            input_types=["Data", "JSON"],
            required=True,
            info="39 답변 반영·다음 단계의 다음 질문/검토 결과가 자동 연결됩니다.",
        )
    ]
    outputs = [
        Output(name="round2_path", display_name="2차 질문", method="route_next", types=["Data"], group_outputs=True),
        Output(name="round3_path", display_name="3차 질문", method="route_next", types=["Data"], group_outputs=True),
        Output(name="blocked_path", display_name="다음 단계 차단", method="route_next", types=["Data"], group_outputs=True),
    ]

    def _component_id(self) -> str:
        return str(getattr(self, "_id", "") or self.name)[:200]

    def _result(self) -> dict[str, Any]:
        result = getattr(self, "_router_result", None)
        if isinstance(result, dict):
            return result
        result = route_chat_answer_commit(getattr(self, "answer_commit", None))
        self._router_result = result
        return result

    def route_next(self) -> Data:
        result = self._result()
        selected = str(result.get("route") or "blocked_path")
        outputs = ("round2_path", "round3_path", "blocked_path")
        if selected not in outputs:
            selected = "blocked_path"
            result = _failure("CHAT_ANSWER_NEXT_ROUTE_INVALID", "채팅 답변의 다음 단계를 선택할 수 없습니다.", str(result.get("trace_id") or f"trace-{uuid.uuid4()}"))
            self._router_result = result
        for output_name in outputs:
            if output_name != selected:
                self.stop(output_name)
        graph = getattr(self, "graph", None)
        exclude = getattr(graph, "exclude_branches_conditionally", None) if graph is not None else None
        if callable(exclude):
            exclude(self._component_id(), [name for name in outputs if name != selected])
        self.status = {"ok": result.get("ok"), "status": result.get("status"), "route": selected}
        current_output = str(getattr(self, "_current_output", "") or "")
        if current_output in outputs and current_output != selected:
            return Data(data={})
        return Data(data=copy.deepcopy(result))
