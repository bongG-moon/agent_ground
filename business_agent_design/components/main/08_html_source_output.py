from __future__ import annotations

from copy import deepcopy
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, Output
from lfx.schema.message import Message


def get_html_source(html_result_value: Any) -> str:
    """HTML 원문만 Chat Output으로 보낼 때 사용합니다."""
    payload = _payload(html_result_value)
    html_result = _dict(payload.get("html_result"))
    return str(html_result.get("html") or "")


def _payload(value: Any) -> dict[str, Any]:
    data = getattr(value, "data", value)
    return deepcopy(data) if isinstance(data, dict) else {}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


class HtmlSourceOutput(Component):
    display_name = "08 HTML 원문 출력"
    description = "생성된 HTML 전체 코드를 Playground/Chat Output에 표시합니다."
    icon = "Code"
    inputs = [DataInput(name="html_result", display_name="HTML 생성 결과", required=True)]
    outputs = [Output(name="html_message", display_name="HTML 원문", method="build_message")]

    def build_message(self) -> Message:
        html_source = get_html_source(getattr(self, "html_result", None))
        self.status = {"HTML 글자 수": len(html_source)}
        return Message(text=html_source)
