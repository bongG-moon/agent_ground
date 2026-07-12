from __future__ import annotations

"""05-2 공유 링크 출력 노드.

이 파일은 04번 렌더러가 만든 HTML을 로컬 Report API 서버에 POST하고,
사용자에게 다운로드 링크와 만료 시간을 간단한 메시지로 보여줍니다.
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


DEFAULT_TIMEOUT_SECONDS = 30


def publish_html_report(
    payload_value: Any,
    report_api_url: Any = "",
    ttl_hours: Any = "24",
) -> dict[str, Any]:
    """HTML 생성 결과를 Report API에 저장하고 링크 정보를 payload에 추가합니다."""

    payload = _payload(payload_value)
    if not payload:
        return {"report_publish": {"status": "error", "errors": ["empty payload"]}}

    html_report = _dict(payload.get("html_report"))
    report_plan = _dict(payload.get("report_plan"))
    request = _dict(payload.get("request"))
    html = _first_text(payload, html_report, ["html", "rendered_html", "html_string", "document_html"])
    if not html.strip():
        return {**payload, "report_publish": {"status": "error", "errors": ["html is empty"]}}

    base_url = _clean(report_api_url)
    if not base_url:
        return {**payload, "report_publish": {"status": "error", "errors": ["report_api_url is empty"]}}

    post_url = base_url.rstrip("/")
    if not post_url.endswith("/reports"):
        # 사용자가 `http://127.0.0.1:8010`만 넣어도 자동으로 `/reports`를 붙입니다.
        post_url = post_url + "/reports"

    create_request = {
        # server.py의 CreateReportRequest 모델과 같은 형식으로 맞춥니다.
        "html": html,
        "title": _clean(html_report.get("title") or report_plan.get("title") or payload.get("title") or "HTML Report"),
        "question": _clean(request.get("question") or payload.get("question")),
        "view_request": _clean(request.get("view_request") or payload.get("view_request") or html_report.get("view_request")),
        "available_datasets": _list(payload.get("available_datasets") or request.get("available_datasets")),
        "report_plan": report_plan,
        "ttl_hours": _positive_int(ttl_hours, 24),
        "filename_hint": _clean(
            html_report.get("filename_hint")
            or report_plan.get("filename_hint")
            or report_plan.get("title")
            or html_report.get("title")
            or "report"
        ),
    }

    try:
        response = _post_json(post_url, create_request, timeout=DEFAULT_TIMEOUT_SECONDS)
    except Exception as exc:
        # API 서버가 꺼져 있거나 URL이 틀려도 flow 전체가 죽지 않게 오류 payload를 반환합니다.
        return {**payload, "report_publish": {"status": "error", "errors": [str(exc)], "post_url": post_url}}

    next_html_report = _public_html_report(html_report, response)
    next_payload = _compact_publish_payload(payload, next_html_report)
    next_payload["report_publish"] = {
        "status": "published",
        "errors": [],
        "download_url": response.get("download_url", ""),
        "expires_at": response.get("expires_at", ""),
    }
    return next_payload


def build_report_link_message(payload_value: Any) -> str:
    """Report API 응답 payload를 사용자가 읽을 수 있는 짧은 링크 메시지로 바꿉니다."""

    payload = _payload(payload_value)
    html_report = _dict(payload.get("html_report"))
    publish = _dict(payload.get("report_publish"))
    title = _clean(html_report.get("title") or "HTML Report")
    download_url = _clean(html_report.get("download_url") or publish.get("download_url"))
    expires_at = _clean(html_report.get("expires_at") or publish.get("expires_at"))
    if download_url:
        lines = [f"HTML 리포트가 생성되었습니다: {title}", "", f"- 다운로드 링크: {download_url}"]
        if expires_at:
            lines.append(f"- 만료 시간: {_format_expires_at(expires_at)}")
        return "\n".join(lines)
    if publish.get("status") == "error":
        errors = "; ".join(str(item) for item in _list(publish.get("errors"))) or "알 수 없는 오류"
        return f"HTML은 생성했지만 Report API 링크 생성에 실패했습니다: {errors}"
    return "공유 링크가 아직 없습니다. 04 HTML 렌더링 결과와 Report API URL 연결을 확인하세요."


def _public_html_report(html_report: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    """뒤 노드/메시지에 필요한 공개 링크 정보만 남긴 html_report를 만듭니다."""

    return {
        "title": html_report.get("title", "HTML Report"),
        "download_url": response.get("download_url", ""),
        "expires_at": response.get("expires_at", ""),
    }


def _compact_publish_payload(payload: dict[str, Any], html_report: dict[str, Any]) -> dict[str, Any]:
    """링크 출력 이후 필요한 최소 정보만 담은 payload로 줄입니다."""

    return {
        "payload_version": payload.get("payload_version", "html-report-demo-v1"),
        "flow_type": payload.get("flow_type", "html_report_demo"),
        "status": payload.get("status", "ok"),
        "request": _dict(payload.get("request")),
        "html_report": html_report,
        "data_summary": _dict(payload.get("data_summary")),
        "warnings": _list(payload.get("warnings")),
        "errors": _list(payload.get("errors")),
    }


def _post_json(url: str, body: dict[str, Any], timeout: int) -> dict[str, Any]:
    """표준 라이브러리만 사용해 JSON POST 요청을 보냅니다.

    외부 패키지 `requests`가 없어도 동작하도록 `urllib.request`를 사용합니다.
    """

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
        raise RuntimeError("Report API did not return a JSON object")
    return parsed


def _first_text(payload: dict[str, Any], html_report: dict[str, Any], keys: list[str]) -> str:
    """여러 후보 key 중 가장 먼저 발견되는 HTML 문자열을 찾습니다."""

    for source in (html_report, payload):
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""


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
    """dict면 복사본을, 아니면 빈 dict를 반환합니다."""

    return deepcopy(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    """list면 복사본을, 아니면 빈 list를 반환합니다."""

    return deepcopy(value) if isinstance(value, list) else []


def _clean(value: Any) -> str:
    """값을 문자열로 바꾸고 앞뒤 공백을 제거합니다."""

    return str(value or "").strip()


def _format_expires_at(value: Any) -> str:
    """서버가 준 UTC 만료 시간을 한국 시간 문자열로 바꿉니다."""

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


def _positive_int(value: Any, default: int) -> int:
    """값을 양의 정수로 바꾸고 실패하면 default를 사용합니다."""

    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(1, parsed)


class ReportApiPublisher(Component):
    """Langflow 화면에 표시되는 05-2 커스텀 컴포넌트 클래스."""

    display_name = "05-2 공유 링크 출력"
    description = "생성된 HTML을 Report API에 저장하고 간단한 다운로드 링크 메시지를 출력합니다."
    icon = "ExternalLink"
    inputs = [
        DataInput(name="payload", display_name="HTML 생성 결과", required=True),
        MessageTextInput(name="report_api_url", display_name="Report API 주소", value="", advanced=False),
        MessageTextInput(name="ttl_hours", display_name="링크 유효시간", value="24", advanced=False),
    ]
    outputs = [Output(name="link_message", display_name="링크 메시지", method="build_message")]

    def _published_payload(self) -> dict[str, Any]:
        """같은 노드 실행 중 API POST가 중복 호출되지 않도록 결과를 캐시합니다."""

        cached = getattr(self, "_cached_publish_result", None)
        if isinstance(cached, dict):
            return cached
        result = publish_html_report(
            getattr(self, "payload", None),
            getattr(self, "report_api_url", ""),
            getattr(self, "ttl_hours", "24"),
        )
        self._cached_publish_result = result
        return result

    def build_message(self) -> Message:
        """공유 링크 메시지를 생성하고 Langflow status에 간단한 실행 상태를 표시합니다."""

        result = self._published_payload()
        publish = result.get("report_publish", {}) if isinstance(result, dict) else {}
        self.status = {
            "status": publish.get("status"),
            "download_url": publish.get("download_url", ""),
            "expires_at": publish.get("expires_at", ""),
            "errors": len(publish.get("errors", [])) if isinstance(publish.get("errors"), list) else 0,
        }
        return Message(text=build_report_link_message(result))
