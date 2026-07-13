from __future__ import annotations

"""HTML 프레젠테이션 원문 또는 안전한 실패 요약을 Message로 출력한다."""

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import BoolInput, DataInput, Output
from lfx.schema.message import Message


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return deepcopy(value)
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return deepcopy(data)
    text = getattr(value, "text", None) or getattr(value, "content", None)
    if isinstance(text, str) and text.strip():
        try:
            parsed = json.loads(text)
            return deepcopy(parsed) if isinstance(parsed, dict) else {"text": text}
        except Exception:
            return {"text": text}
    return {}


def _extract_html(payload: dict[str, Any]) -> str:
    for key in ("presentation_artifact", "html_presentation", "html_report", "presentation", "artifact"):
        container = payload.get(key)
        if isinstance(container, dict) and isinstance(container.get("html"), str):
            return container["html"]
    return str(payload.get("html") or "") if isinstance(payload.get("html"), str) else ""


def _quality(payload: dict[str, Any], quality_value: Any) -> dict[str, Any]:
    explicit = _payload(quality_value)
    report = explicit.get("quality_report")
    if isinstance(report, dict):
        return report
    report = payload.get("quality_report")
    return deepcopy(report) if isinstance(report, dict) else {}


def _safe_error(value: Any) -> str:
    """경로, Data URL과 긴 내부 예외를 사용자 실패 요약에서 제거한다."""

    text = str(value or "")
    text = re.sub(r"data:image/[^;\s]+;base64,[A-Za-z0-9+/=\s]+", "[이미지 데이터 생략]", text, flags=re.I)
    text = re.sub(r"[A-Za-z]:\\[^\r\n]+", lambda match: Path(match.group(0)).name, text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:500]


def build_html_message(payload_value: Any, quality_value: Any = None, fail_closed: Any = True) -> str:
    payload = _payload(payload_value)
    report = _quality(payload, quality_value)
    status = str(report.get("status") or "").lower()
    html_source = _extract_html(payload)
    if bool(fail_closed) and status == "fail":
        failures = report.get("blocking_errors") if isinstance(report.get("blocking_errors"), list) else []
        lines = ["HTML 프레젠테이션 생성 결과가 품질 검사를 통과하지 못했습니다.", "- 단계: presentation_quality_gate"]
        for item in failures[:5]:
            safe = _safe_error(item)
            if safe:
                lines.append(f"- 오류: {safe}")
        if len(lines) == 2:
            lines.append("- 오류: 품질 검사 실패 원인을 quality_report에서 확인해 주세요.")
        return "\n".join(lines)
    if not html_source.strip():
        errors = payload.get("errors") if isinstance(payload.get("errors"), list) else []
        lines = ["HTML 프레젠테이션 원문이 비어 있습니다."]
        for item in errors[:5]:
            message = item.get("message") if isinstance(item, dict) else item
            safe = _safe_error(message)
            if safe:
                lines.append(f"- 오류: {safe}")
        return "\n".join(lines)
    return f"````html\n{html_source.rstrip()}\n````"


class PresentationHtmlSourceOutput(Component):
    """Renderer 결과를 Chat Output에서 확인할 수 있는 Message로 바꾼다."""

    display_name = "08 HTML 프레젠테이션 원문 출력"
    description = "품질 검사를 통과한 HTML 원문을 Message로 출력하고 실패 시 Base64나 내부 경로가 없는 오류 요약을 반환합니다."
    icon = "Code2"
    name = "PresentationHtmlSourceOutput"

    inputs = [
        DataInput(name="payload", display_name="HTML 프레젠테이션", required=True),
        DataInput(name="quality_report", display_name="품질 검사 결과", required=False),
        BoolInput(name="fail_closed", display_name="품질 실패 시 HTML 차단", value=True, advanced=True),
    ]
    outputs = [Output(name="message", display_name="HTML 원문 Message", method="build_message", types=["Message"])]

    def build_message(self) -> Message:
        text = build_html_message(
            getattr(self, "payload", None),
            getattr(self, "quality_report", None),
            getattr(self, "fail_closed", True),
        )
        self.status = {"message_chars": len(text), "contains_html": text.startswith("````html")}
        return Message(text=text)
