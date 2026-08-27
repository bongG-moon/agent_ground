from __future__ import annotations

"""Standalone Langflow 1.11 client for server-issued activation evidence."""

import hashlib
import json
import re
import urllib.error
import urllib.request
import uuid
from typing import Any
from urllib.parse import quote, urlsplit

from lfx.custom import Component
from lfx.io import DataInput, IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema import Data


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise urllib.error.HTTPError(req.full_url, code, "Redirects are forbidden", headers, fp)


def _secret(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    return str(getter() if callable(getter) else value or "").strip()


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return dict(data)
    if isinstance(value, str):
        parsed = json.loads(value)
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _approved_hosts(value: Any) -> set[str]:
    hosts = {item.strip().lower() for item in str(value or "").split(",") if item.strip()}
    if not hosts or any("/" in item or "@" in item for item in hosts):
        raise ValueError("approved_server_hosts must contain exact hostname or hostname:port entries")
    return hosts


def _validate_server_url(value: str, approved: set[str]) -> str:
    parsed = urlsplit(value.strip())
    hostname = (parsed.hostname or "").lower()
    host_port = f"{hostname}:{parsed.port}" if parsed.port else hostname
    loopback = hostname in {"localhost", "127.0.0.1", "::1"}
    if not hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("worker_server_url must be absolute and contain no credentials, query, or fragment")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise ValueError("worker_server_url must use HTTPS outside loopback development")
    if hostname not in approved and host_port not in approved:
        raise ValueError("worker_server_url host is not in the explicit allowlist")
    return value.strip().rstrip("/")


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _post_json(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int, max_bytes: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    opener = urllib.request.build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > max_bytes:
                raise ValueError("approval response exceeds the configured byte limit")
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ValueError("approval response exceeds the configured byte limit")
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise ValueError("approval redirects are forbidden") from exc
        if 400 <= exc.code < 500 and exc.code not in {408, 429}:
            raise ValueError(f"approval service rejected a non-retryable request with HTTP {exc.code}") from exc
        raise RuntimeError(f"approval service rejected the request with HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("approval service request could not be completed") from exc
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("approval response must be a JSON object")
    return value


def _failure(code: str, message: str, trace_id: str = "trace-unassigned", retryable: bool = False) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "BLOCKED",
        "route": "blocked_path",
        "artifact_refs": [],
        "error": {"code": code, "message": message, "retryable": retryable, "details": {}},
        "resume": None,
        "trace_id": trace_id,
    }


class CatalogActivationApprovalClientComponent(Component):
    display_name = "Catalog Activation Approval Client"
    description = "Approve and activate a validated snapshot server-side so one-time evidence never crosses a Langflow Data edge."
    icon = "ShieldCheck"
    name = "CatalogActivationApprovalClient"

    inputs = [
        DataInput(name="validation_report", display_name="Validated Snapshot Report", required=True),
        MessageTextInput(name="approval_trigger", display_name="Approved Human Branch Trigger", required=True),
        StrInput(name="worker_server_url", display_name="Catalog Worker Base URL", value="http://127.0.0.1:8092/api", required=True),
        StrInput(
            name="approved_server_hosts",
            display_name="Approved Worker Hosts",
            value="127.0.0.1:8092,localhost:8092",
            required=True,
            advanced=True,
        ),
        SecretStrInput(name="worker_bearer_token", display_name="Catalog Worker Bearer Token", required=True),
        SecretStrInput(
            name="approval_attestation",
            display_name="Gateway-signed Activation Attestation",
            required=True,
            info="Short-lived catalog-activation-attestation/v1 claim issued only after the admin HITL decision.",
        ),
        StrInput(name="tenant_id", display_name="Tenant ID", required=True),
        StrInput(name="actor_id", display_name="Approver Actor ID", required=True),
        StrInput(name="idempotency_key", display_name="Idempotency Key", required=False),
        IntInput(name="request_timeout_seconds", display_name="Approval Request Timeout", value=30, advanced=True),
        IntInput(name="max_response_kb", display_name="Maximum Approval Response KiB", value=64, advanced=True),
    ]
    outputs = [
        Output(name="approval_path", display_name="Sanitized Active Pointer", method="route_result", types=["Data"], group_outputs=True),
        Output(name="blocked_path", display_name="Approval Blocked", method="route_result", types=["Data"], group_outputs=True),
    ]

    def route_result(self) -> Data:
        cached = getattr(self, "_approval_result", None)
        if not isinstance(cached, dict):
            trace_id = "trace-unassigned"
            try:
                report = _payload(getattr(self, "validation_report", None))
                trace_id = str(report.get("trace_id") or trace_id)
                if report.get("ok") is not True or report.get("status") != "VALIDATED":
                    raise ValueError("validation_report must be successful and VALIDATED")
                tenant_id = str(getattr(self, "tenant_id", "") or "").strip()
                actor_id = str(getattr(self, "actor_id", "") or "").strip()
                snapshot_id = str(report.get("snapshot_id") or "").strip()
                trigger = str(getattr(self, "approval_trigger", "") or "").strip()
                token = _secret(getattr(self, "worker_bearer_token", ""))
                attestation = _secret(getattr(self, "approval_attestation", ""))
                if not _IDENTIFIER.fullmatch(tenant_id) or tenant_id != str(report.get("tenant_id") or ""):
                    raise ValueError("tenant_id must match validation_report.tenant_id")
                if not _IDENTIFIER.fullmatch(actor_id) or not _IDENTIFIER.fullmatch(snapshot_id):
                    raise ValueError("actor_id and snapshot_id must be valid identifiers")
                if not trigger or len(trigger) > 128 or not token or len(attestation) < 80:
                    raise ValueError("approved branch trigger, worker bearer token, and gateway-signed attestation are required")
                base = _validate_server_url(
                    str(getattr(self, "worker_server_url", "") or ""),
                    _approved_hosts(getattr(self, "approved_server_hosts", "")),
                )
                idempotency_key = str(getattr(self, "idempotency_key", "") or "").strip()
                if not idempotency_key:
                    basis = f"{tenant_id}\n{actor_id}\n{snapshot_id}\n{report.get('validation_hash')}\n{trigger}"
                    idempotency_key = (
                        "catalog-activation-"
                        + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]
                        + "-"
                        + uuid.uuid4().hex
                    )
                response = _post_json(
                    base + "/catalog/snapshots/" + quote(snapshot_id, safe="") + "/activate",
                    {
                        "Authorization": f"Bearer {token}",
                        "X-Tenant-ID": tenant_id,
                        "X-Actor-ID": actor_id,
                        "Idempotency-Key": idempotency_key,
                        "Content-Type": "application/json",
                    },
                    {
                        "validation_report": report,
                        "approval_trigger": trigger,
                        "expected_previous_snapshot_id": None,
                        "approval_attestation": attestation,
                    },
                    _bounded_int(getattr(self, "request_timeout_seconds", 30), 30, 5, 120),
                    _bounded_int(getattr(self, "max_response_kb", 64), 64, 4, 256) * 1024,
                )
                active_snapshot_id = str(response.get("active_snapshot_id") or "")
                if response.get("ok") is not True or response.get("status") != "ACTIVE" or active_snapshot_id != snapshot_id:
                    raise RuntimeError("a sanitized active pointer was not returned")
                cached = {
                    "ok": True,
                    "status": "ACTIVE",
                    "route": "approval_path",
                    "active_pointer": response,
                    "trace_id": trace_id,
                }
            except ValueError as exc:
                cached = _failure("CATALOG_APPROVAL_CLIENT_INPUT_INVALID", str(exc), trace_id)
            except (RuntimeError, json.JSONDecodeError, UnicodeError):
                cached = _failure(
                    "CATALOG_APPROVAL_NOT_ISSUED",
                    "Fresh server-bound activation evidence was not issued.",
                    trace_id,
                    retryable=True,
                )
            self._approval_result = cached
        selected = str(cached.get("route") or "blocked_path")
        for output_name in ("approval_path", "blocked_path"):
            if output_name != selected:
                self.stop(output_name)
        self.status = {
            "ok": cached.get("ok"),
            "status": cached.get("status"),
            "route": selected,
            "active_snapshot_id": (cached.get("active_pointer") or {}).get("active_snapshot_id"),
        }
        return Data(data=dict(cached))
