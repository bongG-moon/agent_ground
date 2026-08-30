from __future__ import annotations

"""Publish a rendered report to the companion Report API with fail-closed checks."""

import hashlib
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from lfx.custom import Component
from lfx.io import BoolInput, DataInput, IntInput, MultilineInput, Output, SecretStrInput, StrInput
from lfx.schema import Data


MAX_REPORT_BYTES = 15 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201, ARG002
        return None


def _raw(value: Any) -> Any:
    data = getattr(value, "data", None)
    return data if isinstance(data, (dict, list)) else value


def _payload(value: Any, field: str) -> dict[str, Any]:
    value = _raw(value)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field} must be a JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return dict(value)


def _secret(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        return str(getter()).strip()
    return str(value or "").strip()


def _allowed_hosts(value: Any) -> set[str]:
    value = _raw(value)
    if value in (None, ""):
        return set()
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            value = parsed
        except json.JSONDecodeError:
            value = [item.strip() for item in value.split(",") if item.strip()]
    if not isinstance(value, list):
        raise ValueError("allowed_hosts_json must be a JSON array or comma-separated host list")
    result = {str(item).strip().lower().rstrip(".") for item in value if str(item).strip()}
    if any("/" in item or "://" in item or "@" in item for item in result):
        raise ValueError("allowed_hosts_json entries must contain hostnames only")
    return result


def _validate_url(url: str, hosts: set[str], *, response_url: bool = False) -> str:
    try:
        parsed = urllib.parse.urlsplit(url.strip())
    except ValueError as exc:
        raise ValueError("invalid report API URL") from exc
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if not hostname or parsed.username or parsed.password:
        raise ValueError("report URL must have a hostname and must not contain credentials")
    is_loopback = hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme not in ({"http", "https"} if is_loopback else {"https"}):
        raise ValueError("HTTPS is required except for explicit loopback development URLs")
    if not hosts and not is_loopback:
        raise ValueError("allowed_hosts_json must explicitly allow every non-loopback report host")
    if hosts and hostname not in hosts:
        kind = "returned report" if response_url else "report API"
        raise ValueError(f"{kind} host is not in allowed_hosts_json")
    if not response_url and (parsed.query or parsed.fragment):
        raise ValueError("report API base URL must not contain a query or fragment")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _validate_report_link(url: str, hosts: set[str], report_id: str, *, download: bool) -> str:
    _validate_url(url, hosts, response_url=True)
    parsed = urllib.parse.urlsplit(url.strip())
    expected_suffix = "/reports/" + urllib.parse.quote(report_id, safe="")
    if download:
        expected_suffix += "/download"
    if parsed.fragment or not parsed.path.endswith(expected_suffix):
        raise ValueError("report API returned a URL that is not bound to the requested report")
    try:
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise ValueError("report API returned an invalid capability URL") from exc
    if len(query) != 1 or query[0][0] != "capability" or not query[0][1]:
        raise ValueError("report API returned an invalid capability URL")
    return url.strip()


class ReportPublisherComponent(Component):
    display_name = "Business Flow Report Publisher"
    description = "Publishes verified report HTML to the authenticated companion Report API."
    icon = "CloudUpload"
    name = "ReportPublisher"

    inputs = [
        DataInput(name="render_result", display_name="Rendered Report", required=True),
        DataInput(
            name="report_context",
            display_name="Report Execution Context",
            required=False,
            info="F30 handoff가 자동 연결하는 tenant/actor/approval identity입니다.",
        ),
        StrInput(name="report_api_url", display_name="Report API Base URL", required=True),
        SecretStrInput(name="bearer_token", display_name="Report API Bearer Token", required=False),
        StrInput(
            name="tenant_id",
            display_name="Tenant ID",
            required=False,
            info="F30 handoff 연결 시 자동 적용됩니다. 단독 실행에서만 직접 입력합니다.",
        ),
        StrInput(
            name="actor_id",
            display_name="Actor ID",
            value="langflow-service",
            required=False,
            info="F30 handoff 연결 시 자동 적용됩니다. 단독 실행에서만 직접 입력합니다.",
        ),
        StrInput(
            name="idempotency_key",
            display_name="Idempotency Key",
            value="",
            required=False,
            advanced=True,
            info="Leave blank to derive a stable key from tenant, report ID, and content hash.",
        ),
        MultilineInput(
            name="allowed_hosts_json",
            display_name="Allowed Hosts",
            value='["localhost","127.0.0.1","::1"]',
            info="JSON array (or comma-separated list) of exact report API and returned-link hostnames.",
        ),
        IntInput(name="timeout_seconds", display_name="Timeout (seconds)", value=30, advanced=True),
        BoolInput(
            name="dry_run",
            display_name="테스트 실행 (저장하지 않음)",
            value=True,
            advanced=True,
            info="켜면 Report API에 게시하지 않고 HTML, hash, URL 허용 범위만 검증합니다.",
        ),
    ]
    outputs = [Output(name="publish_result", display_name="Publish Result", method="publish_report")]

    def publish_report(self) -> Data:
        rendered = _payload(self.render_result, "render_result")
        html = rendered.get("html")
        supplied_hash = str(rendered.get("content_sha256") or "").strip()
        report_id = str(rendered.get("report_id") or "").strip()
        supplied_context = getattr(self, "report_context", None)
        context = _payload(supplied_context, "report_context") if supplied_context not in (None, "") else {}
        context_tenant_id = str(context.get("tenant_id") or "").strip()
        context_actor_id = str(context.get("actor_id") or "").strip()
        configured_tenant_id = str(getattr(self, "tenant_id", "") or "").strip()
        configured_actor_id = str(getattr(self, "actor_id", "") or "").strip()
        if context_tenant_id and configured_tenant_id and context_tenant_id != configured_tenant_id:
            raise ValueError("report_context tenant_id does not match tenant_id")
        if context_actor_id and configured_actor_id and context_actor_id != configured_actor_id:
            raise ValueError("report_context actor_id does not match actor_id")
        tenant_id = context_tenant_id or configured_tenant_id
        actor_id = context_actor_id or configured_actor_id
        if not isinstance(html, str) or not html.strip():
            raise ValueError("render_result.html is required")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", report_id):
            raise ValueError("render_result.report_id is required and must be a canonical identity")
        if not tenant_id or len(tenant_id) > 128:
            raise ValueError("tenant_id is required and must be at most 128 characters")
        if not actor_id or len(actor_id) > 128:
            raise ValueError("actor_id is required and must be at most 128 characters")
        html_bytes = html.encode("utf-8")
        if len(html_bytes) > MAX_REPORT_BYTES:
            raise ValueError("report HTML exceeds the 15 MiB publisher limit")
        actual_hash = _sha256_text(html)
        if supplied_hash != actual_hash:
            raise ValueError("render_result.content_sha256 does not match the HTML")

        hosts = _allowed_hosts(getattr(self, "allowed_hosts_json", ""))
        api_base = _validate_url(str(getattr(self, "report_api_url", "") or ""), hosts)
        target_url = api_base + "/reports"
        timeout = max(1, min(int(getattr(self, "timeout_seconds", 30) or 30), 120))
        supplied_metadata = rendered.get("metadata") if isinstance(rendered.get("metadata"), dict) else {}
        metadata = {
            **supplied_metadata,
            "renderer_version": rendered.get("renderer_version"),
            "script_csp_hash": rendered.get("script_csp_hash"),
            "style_csp_hash": rendered.get("style_csp_hash"),
            "byte_count": len(html_bytes),
            "accessibility_summary": rendered.get("accessibility_summary"),
        }
        body = {
            "report_id": report_id or None,
            "content_sha256": actual_hash,
            "html": html,
            "metadata": metadata,
        }
        if bool(getattr(self, "dry_run", False)):
            self.status = (
                f"테스트 실행 완료: {len(html_bytes)} bytes를 검증했습니다. "
                "Report API에는 게시하지 않았습니다."
            )
            return Data(
                data={
                    "ok": True,
                    "status": "would_publish",
                    "execution_mode_display": "테스트 실행 (저장하지 않음)",
                    "message": "테스트 실행입니다. Report API에는 게시하지 않았습니다.",
                    "report_id": report_id or None,
                    "content_sha256": actual_hash,
                    "content_bytes": len(html_bytes),
                    "target_url": target_url,
                }
            )

        token = _secret(getattr(self, "bearer_token", ""))
        parsed_target = urllib.parse.urlsplit(target_url)
        if parsed_target.hostname not in {"localhost", "127.0.0.1", "::1"} and not token:
            raise ValueError("bearer_token is required for non-loopback publication")
        request_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "X-Tenant-ID": tenant_id,
            "X-Actor-ID": actor_id,
            "Idempotency-Key": str(getattr(self, "idempotency_key", "") or "").strip()
            or hashlib.sha256(f"{tenant_id}\n{report_id}\n{actual_hash}".encode("utf-8")).hexdigest(),
            "User-Agent": "business-work-design-agent/1.0",
        }
        if token:
            request_headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(
            target_url,
            data=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers=request_headers,
            method="POST",
        )
        try:
            opener = urllib.request.build_opener(_NoRedirect())
            with opener.open(request, timeout=timeout) as response:
                response_bytes = response.read(1_048_577)
                if len(response_bytes) > 1_048_576:
                    raise ValueError("report API response exceeds 1 MiB")
                response_payload = json.loads(response_bytes.decode("utf-8"))
                status_code = int(response.status)
        except urllib.error.HTTPError as exc:
            message = ""
            try:
                error_payload = json.loads(exc.read(16_385).decode("utf-8", errors="replace"))
                message = str(error_payload.get("detail") or error_payload.get("error") or "")[:500]
            except (json.JSONDecodeError, AttributeError):
                message = ""
            raise ValueError(f"report API rejected publication with HTTP {exc.code}: {message or 'no safe detail'}") from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            raise ValueError("report API could not be reached before the timeout") from exc
        except json.JSONDecodeError as exc:
            raise ValueError("report API returned invalid JSON") from exc
        if not isinstance(response_payload, dict) or status_code not in {200, 201}:
            raise ValueError("report API returned an invalid success response")
        required_response = ("report_id", "content_sha256", "view_url", "download_url")
        if any(type(response_payload.get(key)) is not str or not response_payload[key] for key in required_response):
            raise ValueError("report API success response is missing required artifact bindings")
        returned_report_id = response_payload["report_id"]
        returned_hash = response_payload["content_sha256"]
        if returned_report_id != report_id:
            raise ValueError("report API returned a different report identity")
        if returned_hash != actual_hash:
            raise ValueError("report API returned a different content hash")
        view_url = _validate_report_link(response_payload["view_url"], hosts, report_id, download=False)
        download_url = _validate_report_link(response_payload["download_url"], hosts, report_id, download=True)
        safe_result = {
            "ok": True,
            "status": "published",
            "report_id": report_id,
            "content_sha256": actual_hash,
            "view_url": view_url,
            "download_url": download_url,
            "created_at": response_payload.get("created_at"),
        }
        self.status = f"Report published: {safe_result['report_id']}"
        return Data(data=safe_result)
