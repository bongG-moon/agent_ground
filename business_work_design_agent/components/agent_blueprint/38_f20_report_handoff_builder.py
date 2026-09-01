from __future__ import annotations

"""Seal the verified F20 artifacts required by the report child Flow.

F20 produces three independently validated values on the Canvas: the approved
design scope, bounded retrieval context, and terminal Agent Blueprint result.
This standalone component joins them into one JSON-safe handoff so F10 can
invoke F30 through Langflow's ChatInput/ChatOutput-only Run Flow contract.
"""

import copy
import hashlib
import json
import re
import uuid
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from lfx.custom import Component
from lfx.io import DataInput, Output
from lfx.schema import Data, Message


HANDOFF_SCHEMA_VERSION = "f20-report-handoff/v1"
IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
ASSET_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
TECHNICAL_STATUSES = {"metadata_only", "ports_extracted", "flow_graph_extracted", "verified_runtime"}
_SECRET_URL_QUERY_KEY_PATTERN = re.compile(
    r"(?:^|[_-])(api[_-]?key|authorization|cookie|credential|password|passwd|secret|session|token)(?:$|[_-])",
    re.IGNORECASE,
)


def _has_secret_url_query_key(value: Any) -> bool:
    key = str(value or "").casefold()
    compact = re.sub(r"[^a-z0-9]", "", key)
    return bool(_SECRET_URL_QUERY_KEY_PATTERN.search(key)) or any(
        marker in compact
        for marker in ("apikey", "authorization", "cookie", "credential", "password", "passwd", "secret", "session", "token")
    )


def _payload(value: Any) -> dict[str, Any]:
    data = getattr(value, "data", None)
    value = data if isinstance(data, dict) else value
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def _canonical_hash(value: Any) -> str:
    material = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _identity(value: Any) -> str:
    text = str(value or "").strip()
    return text if IDENTITY_PATTERN.fullmatch(text) else ""


def _sha256(value: Any) -> str:
    text = str(value or "").strip()
    return text if SHA256_PATTERN.fullmatch(text) else ""


def _revision(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _safe_text(value: Any, maximum: int) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value or "")).strip()[:maximum]


def _catalog_asset_id(value: Any) -> str:
    text = _safe_text(value, 256)
    return text if ASSET_ID_PATTERN.fullmatch(text) else ""


def _safe_catalog_url(value: Any) -> str:
    """Validate a presentation-only catalog link before sealing it into F30.

    The report handoff can be opened in a browser, so catalog metadata must
    never introduce non-HTTP links, credentials, control characters, or a URL
    that appears to carry a secret.  An invalid optional link is omitted while
    the associated asset remains available as a text-only catalog reference.
    """

    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or len(text) > 2048 or any(ord(character) < 32 or ord(character) == 127 for character in text):
        return ""
    if any(character.isspace() for character in text):
        return ""
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError:
        return ""
    scheme = parsed.scheme.casefold()
    hostname = parsed.hostname
    if scheme not in {"http", "https"} or not hostname or parsed.username is not None or parsed.password is not None:
        return ""
    if len(hostname) > 253:
        return ""
    try:
        query_pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=False)
    except ValueError:
        return ""
    if any(_has_secret_url_query_key(key) for key, _ in query_pairs):
        return ""
    normalized_host = hostname.casefold()
    if ":" in normalized_host and not normalized_host.startswith("["):
        normalized_host = f"[{normalized_host}]"
    netloc = normalized_host if port is None else f"{normalized_host}:{port}"
    return urlunsplit((scheme, netloc, parsed.path, parsed.query, ""))


def _catalog_presentation_registry(candidate_context: dict[str, Any]) -> list[dict[str, Any]]:
    """Project bounded display metadata for assets that are already allowlisted.

    ``candidate_allowlist`` remains the authoritative, port-hash-sealed
    execution contract.  This separate list is deliberately presentation-only
    and cannot add an asset that was not included in that allowlist.  It gives
    F30 enough information to explain *which* catalog asset was reused and
    link to its detail page without reintroducing the whole untrusted catalog
    payload into the report handoff.
    """

    raw_allowlist = candidate_context.get("candidate_allowlist")
    if not isinstance(raw_allowlist, list):
        return []
    allowed: dict[tuple[str, str], tuple[str, str, str]] = {}
    for item in raw_allowlist[:50]:
        if not isinstance(item, dict):
            continue
        asset_id = _catalog_asset_id(item.get("asset_id"))
        version = _safe_text(item.get("version"), 100)
        asset_type = _safe_text(item.get("asset_type"), 32)
        technical_status = _safe_text(item.get("technical_contract_status"), 64)
        port_contract_sha256 = _sha256(item.get("port_contract_sha256"))
        if (
            asset_id
            and version
            and asset_type in {"component", "flow"}
            and technical_status in TECHNICAL_STATUSES
            and port_contract_sha256
        ):
            allowed[(asset_id, version)] = (asset_type, technical_status, port_contract_sha256)

    raw_items = candidate_context.get("candidate_items")
    if not isinstance(raw_items, list) or not allowed:
        return []
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw_items[:50]:
        if not isinstance(item, dict):
            continue
        asset_id = _catalog_asset_id(item.get("asset_id"))
        version = _safe_text(item.get("version"), 100)
        key = (asset_id, version)
        expected = allowed.get(key)
        if not asset_id or not version or expected is None or key in seen:
            continue
        asset_type, technical_status, port_contract_sha256 = expected
        if (
            _safe_text(item.get("asset_type"), 32) != asset_type
            or _safe_text(item.get("technical_contract_status"), 64) != technical_status
            or _sha256(item.get("port_contract_sha256")) != port_contract_sha256
        ):
            continue
        projected = {
            "asset_id": asset_id,
            "version": version,
            "asset_type": asset_type,
            "title": _safe_text(item.get("title"), 500) or asset_id,
            "category": _safe_text(item.get("category"), 200),
            "description": _safe_text(item.get("description"), 2_000),
            "technical_contract_status": technical_status,
            "port_contract_sha256": port_contract_sha256,
        }
        catalog_url = _safe_catalog_url(item.get("catalog_url"))
        if catalog_url:
            projected["catalog_url"] = catalog_url
        result.append(projected)
        seen.add(key)
    return result


def _error(
    trace_id: str,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "BLOCKED",
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": details or {},
        },
        "trace_id": trace_id,
    }


def _forward_blocked_stage(value: Any, *, trace_id: str) -> dict[str, Any] | None:
    """Keep a safe, actionable F20 stage failure visible at the sole output.

    The report handoff is intentionally the only F20 Chat Output.  Earlier
    versions converted a failed search/embedding stage into the generic
    ``CANDIDATE_CONTEXT_REQUIRED`` message, even though Component 22 had
    already produced a precise blocked envelope.  Preserve that envelope's
    code, message, retryability and safe details so a direct F20 run and an
    F10 Run Flow show the component that actually needs attention.
    """

    payload = _payload(value)
    error = payload.get("error")
    if (
        payload.get("ok") is not False
        or payload.get("status") != "BLOCKED"
        or not isinstance(error, dict)
    ):
        return None

    code = str(error.get("code") or "F20_UPSTREAM_STAGE_BLOCKED").strip()[:128]
    message = str(error.get("message") or "F20의 이전 단계가 차단되었습니다.").strip()[:2_000]
    if not code:
        code = "F20_UPSTREAM_STAGE_BLOCKED"
    if not message:
        message = "F20의 이전 단계가 차단되었습니다."
    details = copy.deepcopy(error.get("details")) if isinstance(error.get("details"), dict) else {}
    upstream_trace_id = str(payload.get("trace_id") or "").strip()[:200]
    if upstream_trace_id:
        details.setdefault("upstream_trace_id", upstream_trace_id)
    return _error(
        trace_id,
        code,
        message,
        retryable=error.get("retryable") is True,
        details=details,
    )


def _identity_tuple(scope: dict[str, Any], work: dict[str, Any]) -> tuple[str, str, int, str, str, str] | None:
    tenant_id = _identity(scope.get("tenant_id"))
    snapshot_id = _identity(scope.get("catalog_snapshot_id"))
    work_definition_id = _identity(scope.get("work_definition_id"))
    work_definition_revision = _revision(scope.get("work_definition_revision"))
    approved_hash = _sha256(scope.get("approved_hash"))
    design_scope_sha256 = _sha256(scope.get("design_scope_sha256"))
    if not all((tenant_id, snapshot_id, work_definition_id, approved_hash, design_scope_sha256)):
        return None
    if work_definition_revision is None:
        return None
    if (
        work.get("schema_version") != "work-definition/v1"
        or work.get("status") != "APPROVED"
        or _identity(work.get("tenant_id")) != tenant_id
        or _identity(work.get("work_definition_id")) != work_definition_id
        or _revision(work.get("revision")) != work_definition_revision
        or _sha256(work.get("approved_hash")) != approved_hash
        or not _identity(work.get("owner_id"))
    ):
        return None
    return tenant_id, snapshot_id, work_definition_revision, work_definition_id, approved_hash, design_scope_sha256


def build_f20_report_handoff(
    design_scope: Any,
    candidate_context: Any,
    terminal_blueprint: Any,
) -> dict[str, Any]:
    """Create the only F20 -> F30 payload accepted by the report child Flow."""

    trace_id = str(uuid.uuid4())
    scope = _payload(design_scope)
    candidates = _payload(candidate_context)
    terminal = _payload(terminal_blueprint)
    blocked_scope = _forward_blocked_stage(scope, trace_id=trace_id)
    if blocked_scope is not None:
        return blocked_scope
    if scope.get("ok") is not True or scope.get("status") != "COMPLETED":
        return _error(trace_id, "DESIGN_SCOPE_REQUIRED", "완료된 sealed design scope가 필요합니다.")
    work = scope.get("work_definition") if isinstance(scope.get("work_definition"), dict) else {}
    identity = _identity_tuple(scope, work)
    if identity is None:
        return _error(trace_id, "DESIGN_SCOPE_BINDING_INVALID", "승인 업무 정의와 design scope의 권위 식별자가 일치하지 않습니다.")
    tenant_id, snapshot_id, revision, work_id, approved_hash, design_scope_sha256 = identity

    blocked_candidates = _forward_blocked_stage(candidates, trace_id=trace_id)
    if blocked_candidates is not None:
        return blocked_candidates
    if candidates.get("ok") is not True or candidates.get("status") != "COMPLETED":
        return _error(trace_id, "CANDIDATE_CONTEXT_REQUIRED", "완료된 F20 candidate context가 필요합니다.")
    retrieval_trace = candidates.get("retrieval_trace") if isinstance(candidates.get("retrieval_trace"), dict) else {}
    query_plan_sha256 = _sha256(candidates.get("query_plan_sha256"))
    candidate_allowlist_sha256 = _sha256(candidates.get("candidate_allowlist_sha256"))
    if (
        not query_plan_sha256
        or not candidate_allowlist_sha256
        or _identity(candidates.get("tenant_id")) != tenant_id
        or _identity(candidates.get("snapshot_id")) != snapshot_id
        or _identity(candidates.get("work_definition_id")) != work_id
        or _revision(candidates.get("work_definition_revision")) != revision
        or _sha256(candidates.get("approved_hash")) != approved_hash
        or _sha256(candidates.get("design_scope_sha256")) != design_scope_sha256
        or _identity(retrieval_trace.get("tenant_id")) != tenant_id
        or _identity(retrieval_trace.get("snapshot_id")) != snapshot_id
        or _identity(retrieval_trace.get("work_definition_id")) != work_id
        or _revision(retrieval_trace.get("work_definition_revision")) != revision
        or _sha256(retrieval_trace.get("approved_hash")) != approved_hash
        or _sha256(retrieval_trace.get("design_scope_sha256")) != design_scope_sha256
        or _sha256(retrieval_trace.get("query_plan_sha256")) != query_plan_sha256
        or _sha256(retrieval_trace.get("candidate_allowlist_sha256")) != candidate_allowlist_sha256
    ):
        return _error(trace_id, "RETRIEVAL_TRACE_BINDING_INVALID", "F20 retrieval trace가 승인 design scope와 일치하지 않습니다.")

    blocked_terminal = _forward_blocked_stage(terminal, trace_id=trace_id)
    if blocked_terminal is not None:
        return blocked_terminal
    blueprint = terminal.get("blueprint") if isinstance(terminal.get("blueprint"), dict) else {}
    if (
        terminal.get("ok") is not True
        or terminal.get("status") != "COMPLETED"
        or not blueprint
        or blueprint.get("schema_version") != "agent-blueprint.v1"
        or blueprint.get("terminal_contract") is not True
        or _identity(blueprint.get("tenant_id")) != tenant_id
        or _identity(blueprint.get("work_definition_id")) != work_id
        or _revision(blueprint.get("work_definition_revision")) != revision
        or _sha256(blueprint.get("approved_hash")) != approved_hash
        or _identity(blueprint.get("catalog_snapshot_id")) != snapshot_id
        or _sha256(blueprint.get("design_scope_sha256")) != design_scope_sha256
        or _sha256(blueprint.get("query_plan_sha256")) != query_plan_sha256
        or _sha256(blueprint.get("candidate_allowlist_sha256")) != candidate_allowlist_sha256
    ):
        return _error(trace_id, "TERMINAL_BLUEPRINT_BINDING_INVALID", "완료된 Agent Blueprint가 F20 scope와 일치하지 않습니다.")

    execution_context = {
        "tenant_id": tenant_id,
        "actor_id": _identity(work.get("owner_id")),
        "work_definition_id": work_id,
        "work_definition_revision": revision,
        "approved_hash": approved_hash,
    }
    # Never forward a caller-supplied catalog_presentation field unchanged.
    # Rebuild it from the bounded candidate context and the existing allowlist
    # so it cannot extend the assets that F20 authorized for this design.
    sealed_retrieval_trace = copy.deepcopy(retrieval_trace)
    sealed_retrieval_trace["catalog_presentation"] = _catalog_presentation_registry(candidates)
    core = {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "work_definition": copy.deepcopy(work),
        "agent_blueprint": copy.deepcopy(terminal),
        "retrieval_trace": sealed_retrieval_trace,
        "execution_context": execution_context,
        "design_scope_sha256": design_scope_sha256,
        "query_plan_sha256": query_plan_sha256,
        "candidate_allowlist_sha256": candidate_allowlist_sha256,
    }
    return {
        "ok": True,
        "status": "COMPLETED",
        **core,
        "handoff_sha256": _canonical_hash(core),
        "trace_id": trace_id,
    }


class F20ReportHandoffBuilderComponent(Component):
    display_name = "38 F20 Report Handoff Builder"
    description = "F20의 sealed scope, retrieval trace, terminal Blueprint를 F30 호출용 단일 handoff로 고정합니다."
    icon = "Send"
    name = "F20ReportHandoffBuilder"

    inputs = [
        DataInput(name="design_scope", display_name="Sealed Design Scope", required=True),
        DataInput(name="candidate_context", display_name="Candidate Context", required=True),
        DataInput(name="terminal_blueprint", display_name="Terminal Blueprint Result", required=True),
    ]
    outputs = [
        Output(name="report_handoff", display_name="F30 Report Handoff", method="build_report_handoff", types=["Data"], group_outputs=True),
        Output(
            name="report_handoff_message",
            display_name="F30 Report Handoff Message",
            method="build_report_handoff_message",
            types=["Message"],
            group_outputs=True,
        ),
    ]

    def _result(self) -> dict[str, Any]:
        result = getattr(self, "_report_handoff_result", None)
        if not isinstance(result, dict):
            result = build_f20_report_handoff(
                getattr(self, "design_scope", None),
                getattr(self, "candidate_context", None),
                getattr(self, "terminal_blueprint", None),
            )
            self._report_handoff_result = result
        return result

    def build_report_handoff(self) -> Data:
        result = self._result()
        self.status = f"F30 handoff: {result.get('status')}"
        return Data(data=result)

    def build_report_handoff_message(self) -> Message:
        result = self._result()
        self.status = f"F30 handoff: {result.get('status')}"
        return Message(text=json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))
