from __future__ import annotations

"""Publish F30 HTML through the shared HTML Report API.

This component deliberately uses the same small HTTP contract as the proven
reference report flow: a single base URL, a POST to ``/reports``, and a
server-generated view/download link.  F30's sealed handoff and HTML renderer
stay unchanged; only the final external publishing boundary is simplified.
"""

import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from lfx.custom import Component
from lfx.io import BoolInput, DataInput, IntInput, Output, StrInput
from lfx.schema import Data


DEFAULT_REPORT_API_URL = "http://127.0.0.1:5000"
DEFAULT_REPORT_TTL_HOURS = 4
DEFAULT_TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 65_536
F30_TERMINAL_SCHEMA_VERSION = "f30-terminal-result/v1"
TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


class _PublishError(ValueError):
    """A publish failure that should be shown as Flow data, not a build error."""

    pass


def _error(code: str, message: str, *, retryable: bool = False) -> _PublishError:
    return _PublishError(code, message, retryable)


def _error_code(error: _PublishError) -> str:
    return str(error.args[0]) if error.args else "REPORT_PUBLISHER_UNEXPECTED"


def _error_message(error: _PublishError) -> str:
    return str(error.args[1]) if len(error.args) > 1 else "Report publication failed"


def _error_retryable(error: _PublishError) -> bool:
    return bool(error.args[2]) if len(error.args) > 2 else False


def _raw(value: Any) -> Any:
    data = getattr(value, "data", None)
    return data if isinstance(data, (dict, list)) else value


def _payload(value: Any, field: str) -> dict[str, Any]:
    value = _raw(value)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise _error("REPORT_INPUT_INVALID", f"{field} must be a JSON object") from exc
    if not isinstance(value, dict):
        raise _error("REPORT_INPUT_INVALID", f"{field} must be an object")
    return dict(value)


def _bounded_int(value: Any, *, field: str, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(default if value in (None, "") else value)
    except (TypeError, ValueError) as exc:
        raise _error("REPORT_CONFIGURATION_INVALID", f"{field} must be a whole number") from exc
    return max(minimum, min(parsed, maximum))


def _reports_post_url(value: Any) -> str:
    """Accept a report-service base URL or an already-complete /reports URL."""

    raw = str(value or "").strip()
    if not raw:
        raise _error("REPORT_API_URL_REQUIRED", "Report API URL is required")
    if any(ord(character) < 32 for character in raw):
        raise _error("REPORT_API_URL_INVALID", "Report API URL must not contain control characters")
    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError as exc:
        raise _error("REPORT_API_URL_INVALID", "Report API URL is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise _error("REPORT_API_URL_INVALID", "Report API URL must be an absolute http(s) URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise _error(
            "REPORT_API_URL_INVALID",
            "Report API URL must not include credentials or a fragment",
        )
    path = parsed.path.rstrip("/")
    reports_path = path if path.endswith("/reports") else (path + "/reports" if path else "/reports")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, reports_path, parsed.query, ""))


def _safe_result_url(value: Any, field: str) -> str:
    url = str(value or "").strip()
    if any(ord(character) < 32 for character in url):
        raise _error("REPORT_API_INVALID_RESPONSE", f"Report API returned an invalid {field}")
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise _error("REPORT_API_INVALID_RESPONSE", f"Report API returned an invalid {field}") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise _error("REPORT_API_INVALID_RESPONSE", f"Report API returned an invalid {field}")
    return url


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read(2_049)
    except OSError:
        return ""
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text[:300]
    if isinstance(payload, dict):
        return str(payload.get("detail") or payload.get("message") or payload.get("error") or "")[:300]
    return text[:300]


def _post_report_json(
    target_url: str,
    body: dict[str, Any],
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    try:
        encoded = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _error("REPORT_PAYLOAD_INVALID", "Report payload could not be converted to JSON") from exc
    request = urllib.request.Request(
        target_url,
        data=encoded,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            status_value = getattr(response, "status", None)
            status_code = int(status_value if status_value is not None else response.getcode())
    except urllib.error.HTTPError as exc:
        detail = _http_error_detail(exc)
        message = f"Report API HTTP {exc.code}"
        if detail:
            message += f": {detail}"
        raise _error("REPORT_API_HTTP_ERROR", message, retryable=exc.code >= 500 or exc.code in {408, 429}) from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        reason = str(getattr(exc, "reason", exc) or "connection failed")[:300]
        raise _error("REPORT_API_CONNECTION_FAILED", f"Report API connection failed: {reason}", retryable=True) from exc

    if len(raw) > MAX_RESPONSE_BYTES:
        raise _error("REPORT_API_INVALID_RESPONSE", "Report API response exceeds 64 KiB")
    if status_code not in {200, 201}:
        raise _error("REPORT_API_HTTP_ERROR", f"Report API returned unexpected HTTP {status_code}", retryable=status_code >= 500)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _error("REPORT_API_INVALID_RESPONSE", "Report API returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise _error("REPORT_API_INVALID_RESPONSE", "Report API response must be a JSON object")
    return payload


def _failure(error: _PublishError, *, target_url: str | None = None) -> Data:
    result: dict[str, Any] = {
        "ok": False,
        "status": "PUBLISH_FAILED",
        "message": _error_message(error),
        "error": {
            "code": _error_code(error),
            "message": _error_message(error),
            "retryable": _error_retryable(error),
        },
    }
    if target_url:
        result["target_url"] = target_url
    return Data(data=result)


def _f30_upstream_failure(rendered: dict[str, Any]) -> Data:
    """Forward an F30 validation/render block without trying to publish HTML."""

    source_error = rendered.get("error") if isinstance(rendered.get("error"), dict) else {}
    code = str(source_error.get("code") or "F30_RENDER_RESULT_BLOCKED").strip()[:128]
    message = str(source_error.get("message") or "F30 이전 단계에서 보고서 생성을 중단했습니다.").strip()[:500]
    trace_id = str(rendered.get("trace_id") or "").strip()
    if TRACE_ID_PATTERN.fullmatch(trace_id) is None:
        trace_id = "trace-f30-publisher-blocked"
    result = {
        "ok": False,
        "status": "BLOCKED",
        "schema_version": F30_TERMINAL_SCHEMA_VERSION,
        "stage": str(rendered.get("stage") or "f30_publisher")[:128],
        "error": {
            "code": code or "F30_RENDER_RESULT_BLOCKED",
            "message": message or "F30 이전 단계에서 보고서 생성을 중단했습니다.",
            "retryable": False,
            "details": {},
        },
        "trace_id": trace_id,
    }
    return Data(data=result)


class ReportPublisherComponent(Component):
    display_name = "Business Flow Report Publisher"
    description = "Posts F30's rendered HTML to the shared Report API. Test runs validate only and never send a network request."
    icon = "CloudUpload"
    name = "ReportPublisher"

    inputs = [
        DataInput(name="render_result", display_name="Rendered Report", required=True),
        StrInput(
            name="report_api_url",
            display_name="Report API URL",
            value=DEFAULT_REPORT_API_URL,
            required=True,
            info="예: http://127.0.0.1:5000. /reports endpoint까지 입력해도 됩니다.",
        ),
        IntInput(
            name="report_ttl_hours",
            display_name="HTML Link TTL (hours)",
            value=DEFAULT_REPORT_TTL_HOURS,
            required=True,
            info="Report API가 보고서 링크 보관/만료 정책에 사용하는 시간입니다.",
        ),
        BoolInput(
            name="dry_run",
            display_name="테스트 실행 (저장하지 않음)",
            value=True,
            info="켜면 HTML과 API URL만 검증하고 Report API에 요청을 보내지 않습니다.",
        ),
        IntInput(name="timeout_seconds", display_name="API Timeout (seconds)", value=DEFAULT_TIMEOUT_SECONDS, advanced=True),
    ]
    outputs = [
        Output(name="publish_result", display_name="Publish Result", method="publish_report", types=["Data"]),
    ]

    def publish_report(self) -> Data:
        target_url: str | None = None
        try:
            rendered = _payload(self.render_result, "render_result")
            if rendered.get("ok") is False:
                result = _f30_upstream_failure(rendered)
                self.status = f"Report publication blocked: {result.data['error']['code']}"
                return result
            html = rendered.get("html")
            if not isinstance(html, str) or not html.strip():
                raise _error("REPORT_HTML_REQUIRED", "render_result.html is required")
            target_url = _reports_post_url(getattr(self, "report_api_url", ""))
            ttl_hours = _bounded_int(
                getattr(self, "report_ttl_hours", DEFAULT_REPORT_TTL_HOURS),
                field="HTML Link TTL (hours)",
                default=DEFAULT_REPORT_TTL_HOURS,
                minimum=1,
                maximum=168,
            )
            renderer_report_id = str(rendered.get("report_id") or "").strip()
            filename_part = renderer_report_id or "report"
            body = {
                "html": html,
                "title": str(rendered.get("title") or "Business Work Design Report"),
                "question": "Business Work Design F30 responsive report",
                "view_request": "business_work_design_agent F30 report",
                "available_datasets": [],
                "report_plan": {
                    "source_flow": "F30_responsive_report",
                    "renderer_report_id": renderer_report_id or None,
                    "renderer_version": rendered.get("renderer_version"),
                },
                "ttl_hours": ttl_hours,
                "filename_hint": f"business-work-design-{filename_part}.html",
            }
            if bool(getattr(self, "dry_run", False)):
                result = {
                    "ok": True,
                    "status": "would_publish",
                    "execution_mode_display": "테스트 실행 (저장하지 않음)",
                    "message": "테스트 실행입니다. Report API에는 게시하지 않았습니다.",
                    "renderer_report_id": renderer_report_id or None,
                    "content_bytes": len(html.encode("utf-8")),
                    "target_url": target_url,
                    "ttl_hours": ttl_hours,
                }
                self.status = "테스트 실행 완료: Report API에는 게시하지 않았습니다."
                return Data(data=result)

            timeout = _bounded_int(
                getattr(self, "timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
                field="API Timeout (seconds)",
                default=DEFAULT_TIMEOUT_SECONDS,
                minimum=1,
                maximum=120,
            )
            response = _post_report_json(target_url, body, timeout_seconds=timeout)
            view_url = _safe_result_url(response.get("view_url"), "view_url")
            download_url = _safe_result_url(response.get("download_url"), "download_url")
            result = {
                "ok": True,
                "status": "published",
                "message": "Report API에 보고서를 게시했습니다.",
                "renderer_report_id": renderer_report_id or None,
                "report_id": response.get("report_id"),
                "view_url": view_url,
                "download_url": download_url,
                "expires_at": response.get("expires_at"),
                "ttl_hours": response.get("ttl_hours", ttl_hours),
                "storage": response.get("storage"),
                "target_url": target_url,
            }
            self.status = f"Report published: {response.get('report_id') or renderer_report_id or 'server report'}"
            return Data(data=result)
        except _PublishError as exc:
            self.status = f"Report publication failed: {_error_code(exc)}"
            return _failure(exc, target_url=target_url)
        except Exception as exc:  # noqa: BLE001 - the Flow must expose a readable failure envelope.
            error = _error("REPORT_PUBLISHER_UNEXPECTED", f"Report publication failed unexpectedly: {str(exc)[:300]}")
            self.status = f"Report publication failed: {_error_code(error)}"
            return _failure(error, target_url=target_url)
