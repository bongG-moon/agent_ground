from __future__ import annotations

from copy import deepcopy
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, Output
from lfx.schema.message import Message


def build_user_summary(html_result_value: Any) -> str:
    """Playground/Chat Output에 보여줄 짧은 사용자용 요약 메시지를 만듭니다."""
    payload = _payload(html_result_value)
    html_result = _dict(payload.get("html_result"))
    context = _dict(payload.get("catalog_context"))
    meta = _dict(context.get("catalog_meta"))
    trace = _dict(payload.get("recommendation_trace"))
    security = _dict(html_result.get("security_report"))

    lines = [
        f"HTML 업무 Flow 설계가 생성되었습니다: {html_result.get('title', '업무 AI Agent 개선 설계')}",
        "",
        f"- 카탈로그 소스: {meta.get('source', 'unknown')}",
        f"- 추천 Trace ID: {trace.get('trace_id', '-')}",
        f"- HTML 보안 검사: {'통과' if security.get('passed') else '확인 필요'}",
        "",
        "HTML 원문이 필요하면 `08 HTML 원문 출력` 노드를 연결하세요.",
    ]
    return "\n".join(lines)


def _payload(value: Any) -> dict[str, Any]:
    data = getattr(value, "data", value)
    return deepcopy(data) if isinstance(data, dict) else {}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


class UserSummaryOutput(Component):
    display_name = "07 사용자 요약 출력"
    description = "생성 결과를 사용자가 읽기 쉬운 짧은 메시지로 변환합니다."
    icon = "MessageCircle"
    inputs = [DataInput(name="html_result", display_name="HTML 생성 결과", required=True)]
    outputs = [Output(name="summary_message", display_name="요약 메시지", method="build_message")]

    def build_message(self) -> Message:
        text = build_user_summary(getattr(self, "html_result", None))
        self.status = {"요약 글자 수": len(text)}
        return Message(text=text)
