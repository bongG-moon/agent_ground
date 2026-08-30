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

from lfx.custom import Component
from lfx.io import DataInput, Output
from lfx.schema import Data, Message


HANDOFF_SCHEMA_VERSION = "f20-report-handoff/v1"
IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


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


def _error(trace_id: str, code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "BLOCKED",
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "error": {"code": code, "message": message},
        "trace_id": trace_id,
    }


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
    if scope.get("ok") is not True or scope.get("status") != "COMPLETED":
        return _error(trace_id, "DESIGN_SCOPE_REQUIRED", "완료된 sealed design scope가 필요합니다.")
    work = scope.get("work_definition") if isinstance(scope.get("work_definition"), dict) else {}
    identity = _identity_tuple(scope, work)
    if identity is None:
        return _error(trace_id, "DESIGN_SCOPE_BINDING_INVALID", "승인 업무 정의와 design scope의 권위 식별자가 일치하지 않습니다.")
    tenant_id, snapshot_id, revision, work_id, approved_hash, design_scope_sha256 = identity

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
    core = {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "work_definition": copy.deepcopy(work),
        "agent_blueprint": copy.deepcopy(terminal),
        "retrieval_trace": copy.deepcopy(retrieval_trace),
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
