from __future__ import annotations

"""Standalone Langflow 1.11 client for the bounded catalog worker service."""

import json
import re
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from lfx.custom import Component
from lfx.io import DataInput, IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema import Data


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
_JOB_REF_KEYS = ("tenant_id", "job_id", "snapshot_id", "stage", "expected_cursor", "trace_id")


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


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _validate_job_ref(value: Any) -> dict[str, Any]:
    source = _payload(value)
    result = {key: source.get(key) for key in _JOB_REF_KEYS}
    for key in ("tenant_id", "job_id", "snapshot_id", "stage", "trace_id"):
        cleaned = str(result.get(key) or "").strip()
        if not _IDENTIFIER.fullmatch(cleaned):
            raise ValueError(f"job_ref.{key} is invalid")
        result[key] = cleaned
    try:
        result["expected_cursor"] = max(0, int(result.get("expected_cursor") or 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("job_ref.expected_cursor must be an integer") from exc
    return result


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
        raise ValueError("worker_server_url must be an absolute URL without credentials, query, or fragment")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise ValueError("worker_server_url must use HTTPS outside loopback development")
    if hostname not in approved and host_port not in approved:
        raise ValueError("worker_server_url host is not in the explicit allowlist")
    return value.strip().rstrip("/")


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


def _post_json(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int, max_bytes: int) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    opener = urllib.request.build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > max_bytes:
                raise ValueError("worker response exceeds the configured byte limit")
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ValueError("worker response exceeds the configured byte limit")
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise ValueError("worker redirects are forbidden") from exc
        if 400 <= exc.code < 500 and exc.code not in {408, 409, 429}:
            raise ValueError(f"worker rejected a non-retryable request with HTTP {exc.code}") from exc
        raise RuntimeError(f"worker rejected the request with HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("worker request could not be completed") from exc
    decoded = json.loads(raw.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("worker response must be a JSON object")
    return decoded


class CatalogPipelineWorkerClientComponent(Component):
    display_name = "Catalog Pipeline Worker Client"
    description = "Run standalone catalog stages through a bounded companion worker and route only validated snapshots onward."
    icon = "Workflow"
    name = "CatalogPipelineWorkerClient"

    inputs = [
        DataInput(name="scanned_job_ref", display_name="Scanned Job Reference", required=True),
        StrInput(name="worker_server_url", display_name="Catalog Worker Base URL", value="http://127.0.0.1:8092/api", required=True),
        StrInput(
            name="approved_server_hosts",
            display_name="Approved Worker Hosts",
            value="127.0.0.1:8092,localhost:8092",
            required=True,
            advanced=True,
        ),
        SecretStrInput(name="worker_bearer_token", display_name="Catalog Worker Bearer Token", required=True),
        StrInput(name="tenant_id", display_name="Tenant ID", required=True),
        StrInput(name="actor_id", display_name="Actor ID", required=True),
        IntInput(name="max_stage_invocations", display_name="Maximum Stage Invocations", value=400, advanced=True),
        IntInput(name="request_timeout_seconds", display_name="Whole Worker Request Timeout", value=1800, advanced=True),
        IntInput(name="max_response_mb", display_name="Maximum Worker Response MiB", value=4, advanced=True),
    ]
    outputs = [
        Output(name="activation_path", display_name="Validated for Approval", method="route_result", types=["Data"], group_outputs=True),
        Output(name="blocked_path", display_name="Blocked or Incomplete", method="route_result", types=["Data"], group_outputs=True),
    ]

    def route_result(self) -> Data:
        cached = getattr(self, "_worker_result", None)
        if not isinstance(cached, dict):
            trace_id = "trace-unassigned"
            try:
                incoming = _validate_job_ref(getattr(self, "scanned_job_ref", None))
                trace_id = incoming["trace_id"]
                tenant_id = str(getattr(self, "tenant_id", "") or "").strip()
                actor_id = str(getattr(self, "actor_id", "") or "").strip()
                token = _secret(getattr(self, "worker_bearer_token", ""))
                if not _IDENTIFIER.fullmatch(tenant_id) or tenant_id != incoming["tenant_id"]:
                    raise ValueError("tenant_id must match scanned_job_ref.tenant_id")
                if not _IDENTIFIER.fullmatch(actor_id) or not token:
                    raise ValueError("actor_id and worker_bearer_token are required")
                base = _validate_server_url(
                    str(getattr(self, "worker_server_url", "") or ""),
                    _approved_hosts(getattr(self, "approved_server_hosts", "")),
                )
                timeout = _bounded_int(getattr(self, "request_timeout_seconds", 1800), 1800, 5, 7200)
                max_bytes = _bounded_int(getattr(self, "max_response_mb", 4), 4, 1, 16) * 1024 * 1024
                result = _post_json(
                    base + "/catalog/pipeline/run",
                    {
                        "Authorization": f"Bearer {token}",
                        "X-Tenant-ID": tenant_id,
                        "X-Actor-ID": actor_id,
                        "Content-Type": "application/json",
                    },
                    {
                        "job_ref": incoming,
                        "max_stage_invocations": _bounded_int(getattr(self, "max_stage_invocations", 400), 400, 1, 1000),
                    },
                    timeout,
                    max_bytes,
                )
                route = "activation_path" if result.get("ok") is True and result.get("status") == "VALIDATED" else "blocked_path"
                result = dict(result)
                result["route"] = route
                cached = result
            except ValueError as exc:
                cached = _failure("CATALOG_WORKER_CLIENT_INPUT_INVALID", str(exc), trace_id)
            except (RuntimeError, json.JSONDecodeError, UnicodeError):
                cached = _failure(
                    "CATALOG_WORKER_UNAVAILABLE",
                    "The bounded catalog worker did not return a trusted response.",
                    trace_id,
                    retryable=True,
                )
            self._worker_result = cached
        selected = str(cached.get("route") or "blocked_path")
        for output_name in ("activation_path", "blocked_path"):
            if output_name != selected:
                self.stop(output_name)
        self.status = {"ok": cached.get("ok"), "status": cached.get("status"), "route": selected}
        return Data(data=dict(cached))
