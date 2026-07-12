from __future__ import annotations

"""09 공유 링크 발행 노드.

이 노드는 `06 HTML 업무 Flow 렌더링`이 만든 HTML 결과를
기존 `html_report_flow/report_api/server.py` 서버로 전송합니다.
서버가 HTML 파일을 로컬 저장소에 저장하면, 사용자가 클릭할 수 있는 다운로드 링크와
만료 시간을 받아서 Chat Output용 메시지로 반환합니다.
"""

import json
import urllib.error
import urllib.request
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MessageTextInput, Output
from lfx.schema.message import Message


DEFAULT_REPORT_API_URL = "http://127.0.0.1:8010"
DEFAULT_TIMEOUT_SECONDS = 30


def publish_business_html_report(
    html_result_value: Any,
    report_api_url: Any = DEFAULT_REPORT_API_URL,
    ttl_hours: Any = "24",
) -> dict[str, Any]:
    """HTML 업무 Flow 결과를 Report API에 저장하고 링크 정보를 붙입니다."""

    payload = _payload(html_result_value)
    if not payload:
        return {"report_publish": {"status": "error", "errors": ["HTML 생성 결과가 비어 있습니다."]}}

    html_result = _dict(payload.get("html_result"))
    html = str(html_result.get("html") or "").strip()
    if not html:
        return {**payload, "report_publish": {"status": "error", "errors": ["html_result.html 값이 비어 있습니다."]}}

    security = _dict(html_result.get("security_report"))
    if security and security.get("passed") is False:
        return {
            **payload,
            "report_publish": {
                "status": "error",
                "errors": ["HTML 보안 검사를 통과하지 못해 공유 링크를 만들지 않았습니다."],
            },
        }

    post_url = _reports_post_url(report_api_url)
    if not post_url:
        return {**payload, "report_publish": {"status": "error", "errors": ["Report API 주소가 비어 있습니다."]}}

    title = _clean(html_result.get("title") or "업무 AI Agent 개선 설계")
    request = _dict(payload.get("business_request"))
    design = _dict(payload.get("agent_design"))
    trace = _dict(payload.get("recommendation_trace"))

    create_request = {
        "html": html,
        "title": title,
        "question": _clean(request.get("work_description")),
        "view_request": "업무 AI Agent 개선 Flow HTML",
        "available_datasets": [],
        "report_plan": {
            "source_flow": "business_agent_design_flow",
            "report_title": _clean(design.get("report_title") or title),
            "trace_id": _clean(trace.get("trace_id")),
            "security_policy": _clean(security.get("policy")),
        },
        "ttl_hours": _positive_int(ttl_hours, 24),
        "filename_hint": _filename_hint(title),
    }

    try:
        response = _post_json(post_url, create_request, timeout=DEFAULT_TIMEOUT_SECONDS)
    except Exception as exc:
        return {**payload, "report_publish": {"status": "error", "errors": [str(exc)], "post_url": post_url}}

    return {
        **payload,
        "report_publish": {
            "status": "published",
            "errors": [],
            "report_id": _clean(response.get("report_id")),
            "view_url": _clean(response.get("view_url")),
            "download_url": _clean(response.get("download_url")),
            "expires_at": _clean(response.get("expires_at")),
            "ttl_hours": response.get("ttl_hours"),
        },
    }


def build_download_link_message(publish_value: Any) -> str:
    """Report API 발행 결과를 Chat Output에 보여줄 짧은 메시지로 바꿉니다."""

    payload = _payload(publish_value)
    html_result = _dict(payload.get("html_result"))
    publish = _dict(payload.get("report_publish"))
    title = _clean(html_result.get("title") or "업무 AI Agent 개선 설계")
    download_url = _clean(publish.get("download_url"))
    expires_at = _clean(publish.get("expires_at"))

    if download_url:
        lines = [f"HTML 업무 Flow 설계가 생성되었습니다: {title}", "", f"- 다운로드 링크: {download_url}"]
        if expires_at:
            lines.append(f"- 만료 시간: {_format_expires_at(expires_at)}")
        return "\n".join(lines)

    errors = "; ".join(str(item) for item in _list(publish.get("errors"))) or "알 수 없는 오류"
    return (
        "HTML은 생성했지만 다운로드 링크 생성에 실패했습니다.\n\n"
        f"- 사유: {errors}\n"
        "- 확인: html_report_flow/report_api/server.py 서버가 실행 중인지, Report API 주소가 맞는지 확인하세요."
    )


def _reports_post_url(value: Any) -> str:
    """사용자가 base URL만 넣어도 POST /reports 주소로 맞춥니다."""

    url = _clean(value or DEFAULT_REPORT_API_URL).rstrip("/")
    if not url:
        return ""
    return url if url.endswith("/reports") else f"{url}/reports"


def _post_json(url: str, body: dict[str, Any], timeout: int) -> dict[str, Any]:
    """외부 패키지 없이 표준 라이브러리로 JSON POST 요청을 보냅니다."""

    data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Report API HTTP {exc.code}: {detail}") from exc
    parsed = json.loads(raw or "{}")
    if not isinstance(parsed, dict):
        raise RuntimeError("Report API 응답이 JSON object 형식이 아닙니다.")
    return parsed


def _payload(value: Any) -> dict[str, Any]:
    """Langflow Data/Message/dict/JSON 문자열을 일반 dict로 맞춥니다."""

    if isinstance(value, dict):
        return deepcopy(value)
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return deepcopy(data)
    text = getattr(value, "text", None) or getattr(value, "content", None)
    if isinstance(text, str):
        try:
            parsed = json.loads(text)
        except Exception:
            return {"text": text}
        return deepcopy(parsed) if isinstance(parsed, dict) else {"text": text}
    return {}


def _dict(value: Any) -> dict[str, Any]:
    return deepcopy(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(1, parsed)


def _format_expires_at(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    kst = parsed.astimezone(timezone(timedelta(hours=9)))
    return kst.strftime("%Y-%m-%d %H:%M KST")


def _filename_hint(value: Any) -> str:
    title = _clean(value) or "업무_AI_Agent_개선_설계"
    return title.replace(" ", "_")[:80]


class BusinessReportApiPublisher(Component):
    display_name = "09 공유 링크 발행"
    description = "생성된 HTML 업무 Flow를 기존 Report API 서버에 저장하고 다운로드 링크 메시지를 출력합니다."
    icon = "ExternalLink"
    inputs = [
        DataInput(name="html_result", display_name="HTML 생성 결과", required=True),
        MessageTextInput(name="report_api_url", display_name="Report API 주소", value=DEFAULT_REPORT_API_URL, advanced=False),
        MessageTextInput(name="ttl_hours", display_name="링크 유효시간", value="24", advanced=False),
    ]
    outputs = [Output(name="download_link_message", display_name="다운로드 링크 메시지", method="build_message")]

    def _published_payload(self) -> dict[str, Any]:
        cached = getattr(self, "_cached_publish_result", None)
        if isinstance(cached, dict):
            return cached
        result = publish_business_html_report(
            getattr(self, "html_result", None),
            getattr(self, "report_api_url", DEFAULT_REPORT_API_URL),
            getattr(self, "ttl_hours", "24"),
        )
        self._cached_publish_result = result
        return result

    def build_message(self) -> Message:
        result = self._published_payload()
        publish = _dict(result.get("report_publish")) if isinstance(result, dict) else {}
        self.status = {
            "상태": publish.get("status"),
            "다운로드 링크": publish.get("download_url", ""),
            "만료 시간": publish.get("expires_at", ""),
            "오류 수": len(_list(publish.get("errors"))),
        }
        return Message(text=build_download_link_message(result))
