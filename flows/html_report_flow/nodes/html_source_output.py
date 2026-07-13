from __future__ import annotations

"""05-1 HTML 원문 출력 노드.

이 파일은 Report API 서버를 쓰지 않는 경우를 위한 마지막 노드입니다.
04번 렌더러가 만든 HTML 전체 코드를 Playground 답변 영역에 그대로 출력합니다.
"""

import json
from copy import deepcopy
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, Output
from lfx.schema.message import Message


def build_html_source_message(payload_value: Any) -> str:
    """payload에서 HTML 원문을 꺼내 markdown 코드블록 형태의 메시지로 만듭니다."""

    payload = _payload(payload_value)
    html_report = _dict(payload.get("html_report"))
    html = str(html_report.get("html") or "")
    if not html.strip():
        return "HTML 원문이 비어 있습니다. 04 HTML 렌더링의 HTML 생성 결과를 05-1의 HTML 생성 결과 입력에 연결하세요."
    return f"````html\n{html.rstrip()}\n````"


def _payload(value: Any) -> dict[str, Any]:
    """Langflow Data/Message/dict/JSON 문자열을 일반 dict로 맞춥니다."""

    if isinstance(value, dict):
        return deepcopy(value)
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return deepcopy(data)
    text = getattr(value, "text", None) or getattr(value, "content", None)
    if isinstance(text, str) and text.strip():
        try:
            parsed = json.loads(text)
        except Exception:
            return {"text": text}
        return deepcopy(parsed) if isinstance(parsed, dict) else {"text": text}
    return {}


def _dict(value: Any) -> dict[str, Any]:
    """dict면 복사본을, 아니면 빈 dict를 반환합니다."""

    return deepcopy(value) if isinstance(value, dict) else {}


class HtmlSourceOutput(Component):
    """Langflow 화면에 표시되는 05-1 커스텀 컴포넌트 클래스."""

    display_name = "05-1 HTML 원문 출력"
    description = "Report API 없이 04에서 생성된 전체 HTML 코드를 Playground에 그대로 출력합니다."
    icon = "Code2"
    inputs = [DataInput(name="payload", display_name="HTML 생성 결과", required=True)]
    outputs = [Output(name="html_message", display_name="HTML 원문", method="build_message")]

    def build_message(self) -> Message:
        """Playground에 보여줄 HTML 원문 메시지를 생성합니다."""

        text = build_html_source_message(getattr(self, "payload", None))
        self.status = {"html_chars": len(text)}
        return Message(text=text)
