from __future__ import annotations

"""Optionally publish a rendered report without ever discarding its HTML.

This is a standalone external boundary.  An empty API URL is the normal
generated-only path, and any HTTP error returns a PUBLISH_FAILED envelope that
still contains the original render_result unchanged.
"""

import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, IntInput, Output, StrInput
from lfx.schema import Data


# Match the established Report API's default request ceiling (10 MiB).  Keeping
# this boundary local produces a clear flow result before a server-side 413.
_MAX_HTML_BYTES = 10 * 1024 * 1024
_MAX_RESPONSE_BYTES = 65_536
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _payload(value: Any) -> dict[str, Any]:
    raw = getattr(value, "data", None)
    if isinstance(raw, dict):
        value = raw
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("[REPORT_PUBLISHER_INVALID] Rendered Report가 JSON object가 아닙니다.") from exc
    if not isinstance(value, dict):
        raise ValueError("[REPORT_PUBLISHER_INVALID] Rendered Report가 없습니다. 07 node 연결을 확인해 주세요.")
    return value


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def _reports_url(raw: Any) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    if _CONTROL.search(value):
        raise ValueError("[REPORT_API_URL_INVALID] Report API URL에는 제어문자를 넣을 수 없습니다. URL을 확인해 주세요.")
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError as exc:
        raise ValueError("[REPORT_API_URL_INVALID] Report API URL 형식이 올바르지 않습니다. http(s) URL을 입력해 주세요.") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("[REPORT_API_URL_INVALID] Report API URL은 credential·fragment 없는 절대 http(s) URL이어야 합니다.")
    path = parsed.path.rstrip("/")
    if not path.endswith("/reports"):
        path = (path + "/reports") if path else "/reports"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))


def _safe_result_url(value: Any) -> str | None:
    url = str(value or "").strip()
    if not url or _CONTROL.search(url):
        return None
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        return None
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def _read_error(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read(2049)
    except OSError:
        return ""
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text[:280]
    if isinstance(payload, dict):
        return str(payload.get("detail") or payload.get("message") or payload.get("error") or "")[:280]
    return text[:280]


def _post(url: str, body: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            status = int(getattr(response, "status", None) or response.getcode())
    except urllib.error.HTTPError as exc:
        detail = _read_error(exc)
        message = f"Report API가 HTTP {exc.code}을 반환했습니다."
        if detail:
            message += f" {detail}"
        raise RuntimeError("REPORT_API_HTTP_ERROR", message[:600], exc.code >= 500 or exc.code in {408, 429}) from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        raise RuntimeError("REPORT_API_CONNECTION_FAILED", "Report API에 연결하지 못했습니다. URL·서버 상태·네트워크를 확인해 주세요.", True) from exc
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise RuntimeError("REPORT_API_INVALID_RESPONSE", "Report API 응답이 너무 큽니다.", False)
    if status not in {200, 201}:
        raise RuntimeError("REPORT_API_HTTP_ERROR", f"Report API가 예상하지 않은 HTTP {status}을 반환했습니다.", status >= 500)
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("REPORT_API_INVALID_RESPONSE", "Report API 응답이 JSON object가 아닙니다.", False) from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("REPORT_API_INVALID_RESPONSE", "Report API 응답이 JSON object가 아닙니다.", False)
    return parsed


class ReportPublisherComponent(Component):
    """08. Optional report publisher; renderer output is preserved in every outcome."""

    display_name = "08 보고서 링크 게시"
    description = "Report API 주소가 있으면 HTML을 게시하고, 없거나 실패해도 생성된 HTML을 보존합니다."
    icon = "CloudUpload"
    name = "ReportPublisher"

    inputs = [
        DataInput(name="rendered_report", display_name="Rendered Report", required=True),
        StrInput(name="report_api_url", display_name="Report API URL", value="", required=False, info="비워 두면 HTML만 생성합니다. 예: http://127.0.0.1:5000"),
        IntInput(name="ttl_hours", display_name="링크 보관 시간 (시간)", value=24, required=True),
        IntInput(name="http_timeout_seconds", display_name="HTTP Timeout (seconds)", value=30, advanced=True),
    ]
    outputs = [Output(name="publish_result", display_name="게시 결과", method="publish_report", types=["Data"])]

    def publish_report(self) -> Data:
        rendered = _payload(self.rendered_report)
        html = rendered.get("html")
        if rendered.get("ok") is not True or rendered.get("status") != "RENDERED" or not isinstance(html, str) or not html.strip():
            raise ValueError("[REPORT_PUBLISHER_INVALID] 유효한 RENDERED HTML 결과가 필요합니다. 07 node 출력을 확인해 주세요.")
        if len(html.encode("utf-8")) > _MAX_HTML_BYTES:
            raise ValueError("[REPORT_PUBLISHER_INVALID] HTML 크기가 게시 한도를 초과했습니다. 07 node 설정을 확인해 주세요.")
        ttl_hours = _bounded_int(getattr(self, "ttl_hours", 24), 24, 1, 168)
        timeout_seconds = _bounded_int(getattr(self, "http_timeout_seconds", 30), 30, 1, 120)
        target_url = _reports_url(getattr(self, "report_api_url", ""))
        common = {
            "ok": True,
            "schema_version": "publish-result/v2",
            "report_id": rendered.get("report_id"),
            "render_result": rendered,
            "report_summary": rendered.get("report_summary") if isinstance(rendered.get("report_summary"), dict) else {},
            "ttl_hours": ttl_hours,
        }
        if not target_url:
            result = {**common, "status": "GENERATED_ONLY", "message": "HTML 보고서를 생성했습니다. Report API 주소가 없어 게시하지 않았습니다.", "publish": {"attempted": False, "target_url": None}}
            self.status = "HTML 생성 완료 · Report API 주소가 없어 게시하지 않음"
            return Data(data=result)
        renderer_report_id = str(rendered.get("report_id") or "").strip()
        # Keep the HTTP contract compatible with the established Report API.
        # Its request model is closed (extra fields are rejected), so renderer
        # provenance belongs in the accepted report_plan object rather than as
        # top-level renderer_* fields.
        body = {
            "html": html,
            "title": str(rendered.get("title") or "업무 방식 및 개선 실행 보고서")[:500],
            "question": "Business Work Design single-flow responsive report",
            "view_request": "business_work_design_agent_single_flow report",
            "available_datasets": [],
            "report_plan": {
                "source_flow": "F01_business_work_design_single",
                "renderer_report_id": renderer_report_id or None,
                "renderer_version": rendered.get("renderer_version"),
                "content_sha256": rendered.get("content_sha256"),
            },
            "ttl_hours": ttl_hours,
            "filename_hint": f"business-work-design-{(renderer_report_id or 'report')[:80]}.html",
        }
        try:
            response = _post(target_url, body, timeout_seconds)
        except RuntimeError as exc:
            code = str(exc.args[0]) if exc.args else "REPORT_API_FAILED"
            message = str(exc.args[1]) if len(exc.args) > 1 else "Report API 게시에 실패했습니다."
            retryable = bool(exc.args[2]) if len(exc.args) > 2 else False
            result = {**common, "status": "PUBLISH_FAILED", "message": "HTML 보고서는 생성되었지만 Report API 게시에 실패했습니다.", "publish": {"attempted": True, "target_url": target_url}, "error": {"code": code, "message": message, "retryable": retryable}}
            self.status = f"HTML 생성 완료 · 게시 실패 ({code})"
            return Data(data=result)
        result = {
            **common,
            "status": "PUBLISHED",
            "message": "Report API에 HTML 보고서를 게시했습니다.",
            "publish": {"attempted": True, "target_url": target_url},
            "external_report_id": response.get("report_id"),
            "view_url": _safe_result_url(response.get("view_url")),
            "download_url": _safe_result_url(response.get("download_url")),
            "expires_at": str(response.get("expires_at") or "")[:128] or None,
            "storage": response.get("storage") if isinstance(response.get("storage"), dict) else None,
        }
        self.status = "HTML 보고서 게시 완료"
        return Data(data=result)
