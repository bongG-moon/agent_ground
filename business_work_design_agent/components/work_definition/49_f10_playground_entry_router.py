from __future__ import annotations

"""Choose the human-readable F10 Playground entry mode without local imports.

F10 intentionally keeps its initial 업무 설명 원문 and 추가 설계 프롬프트 as
Text Input fields. Langflow, however, gives a Chat Input priority whenever a
flow contains one. This small standalone router makes the single Chat Input
an explicit entry switch:

* leave it empty (or type 새 업무 시작) to run the Text Input intake;
* send 1번: 답변 or 질문 묶음: qb-... to resume a pending answer batch.

The router emits only one grouped path. This prevents a later answer from
starting a new WorkDefinition at the same time, while still making the normal
Playground answer box available in Langflow 1.11.0.
"""

import copy
import json
import re
import uuid
from typing import Any

from lfx.custom import Component
from lfx.io import MessageTextInput, Output
from lfx.schema import Data


MAX_MESSAGE_CHARS = 16_000
_START_PATTERN = re.compile(r"^\s*(?:새\s*업무\s*(?:시작)?|new\s*work|start)\s*$", re.IGNORECASE)
_ANSWER_NUMBER_PATTERN = re.compile(
    r"(?m)^\s*(?:(?:질문|question|q)\s*)?[1-9][0-9]*\s*(?:(?:번\s*)?[:：]|[.)])"
)
_ANSWER_BATCH_PATTERN = re.compile(
    r"(?im)^\s*(?:질문\s*(?:묶음|배치|batch)(?:\s*(?:id|아이디))?|batch[_\s-]?id)\s*[:：]\s*qb-[A-Za-z0-9._:-]{1,180}\s*$"
)


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return copy.deepcopy(value)
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return copy.deepcopy(data)
    text = getattr(value, "text", value if isinstance(value, str) else "")
    return {"message": text} if isinstance(text, str) else {}


def _message_text(value: Any) -> str:
    payload = _payload(value)
    for key in ("answer_text", "message", "text", "input_value"):
        candidate = payload.get(key)
        if isinstance(candidate, str):
            return candidate.replace("\r\n", "\n").replace("\r", "\n").strip()[:MAX_MESSAGE_CHARS]
    return ""


def _failure(code: str, message: str, trace_id: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "BLOCKED",
        "route": "blocked_path",
        "artifact_refs": [],
        "error": {"code": code, "message": message, "retryable": False, "details": {}},
        "resume": None,
        "trace_id": trace_id,
    }


def route_f10_playground_entry(message: Any = None) -> dict[str, Any]:
    """Return the only safe initial-work or pending-answer route."""

    trace_id = f"trace-{uuid.uuid4()}"
    try:
        text = _message_text(message)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _failure("F10_ENTRY_MESSAGE_INVALID", "Playground 입력을 읽을 수 없습니다.", trace_id)

    if not text or _START_PATTERN.fullmatch(text):
        return {
            "ok": True,
            "status": "START_NEW_WORK",
            "route": "new_work_path",
            "artifact_refs": [],
            "entry_text": text,
            "trace_id": trace_id,
        }
    if _ANSWER_BATCH_PATTERN.search(text) or _ANSWER_NUMBER_PATTERN.search(text):
        return {
            "ok": True,
            "status": "CHAT_ANSWER_RECEIVED",
            "route": "answer_path",
            "artifact_refs": [],
            "answer_text": text,
            "trace_id": trace_id,
        }
    return _failure(
        "F10_ENTRY_MODE_UNCLEAR",
        "새 업무를 시작하려면 채팅 입력을 비우거나 새 업무 시작을 보내세요. 기존 질문에 답할 때는 1번: 답변 형식으로 입력해 주세요.",
        trace_id,
    )


class F10PlaygroundEntryRouterComponent(Component):
    display_name = "49 F10 Playground 입력 구분"
    description = "빈 채팅/새 업무 시작은 Text Input 업무 설명 실행으로, 1번: 답변은 대기 중 질문 답변 재개로 보냅니다. 두 경로는 동시에 실행되지 않습니다."
    icon = "Route"
    name = "F10PlaygroundEntryRouter"

    inputs = [
        MessageTextInput(
            name="message",
            display_name="Playground 입력 (자동 연결)",
            input_types=["Message", "Data", "JSON"],
            required=False,
            info="새 업무 실행에는 비움 또는 새 업무 시작, 질문 답변에는 1번: ... 형식을 사용합니다.",
        )
    ]
    outputs = [
        Output(name="new_work_path", display_name="새 업무 실행", method="route_entry", types=["Data"], group_outputs=True),
        Output(name="answer_path", display_name="번호형 답변 재개", method="route_entry", types=["Data"], group_outputs=True),
        Output(name="blocked_path", display_name="입력 안내", method="route_entry", types=["Data"], group_outputs=True),
    ]

    def _component_id(self) -> str:
        return str(getattr(self, "_id", "") or self.name)[:200]

    def _result(self) -> dict[str, Any]:
        result = getattr(self, "_entry_result", None)
        if not isinstance(result, dict):
            result = route_f10_playground_entry(getattr(self, "message", None))
            self._entry_result = result
        return result

    def route_entry(self) -> Data:
        result = self._result()
        selected = str(result.get("route") or "blocked_path")
        outputs = ("new_work_path", "answer_path", "blocked_path")
        if selected not in outputs:
            result = _failure("F10_ENTRY_ROUTE_INVALID", "F10 시작 경로를 선택할 수 없습니다.", str(result.get("trace_id") or f"trace-{uuid.uuid4()}"))
            self._entry_result = result
            selected = "blocked_path"
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
