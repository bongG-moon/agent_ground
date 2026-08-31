from __future__ import annotations

"""Validate one sealed F20 report handoff and expose F30's typed inputs."""

import copy
import hashlib
import hmac
import json
import re
from typing import Any

from lfx.custom import Component
from lfx.io import BoolInput, DataInput, Output
from lfx.schema import Data


HANDOFF_SCHEMA_VERSION = "f20-report-handoff/v1"
F30_TERMINAL_SCHEMA_VERSION = "f30-terminal-result/v1"
IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_HANDOFF_TEXT_CHARS = 2_000_000
HANDOFF_KEYS = {
    "ok",
    "status",
    "schema_version",
    "work_definition",
    "agent_blueprint",
    "retrieval_trace",
    "execution_context",
    "design_scope_sha256",
    "query_plan_sha256",
    "candidate_allowlist_sha256",
    "handoff_sha256",
    "trace_id",
}


def _safe_trace_id(value: Any, *, stage: str) -> str:
    """Keep a correlation id without returning untrusted handoff text."""

    candidate = None
    data = getattr(value, "data", None)
    for source in (value, data):
        if isinstance(source, dict):
            candidate = source.get("trace_id")
            if candidate is None and isinstance(source.get("data"), dict):
                candidate = source["data"].get("trace_id")
        if isinstance(candidate, str) and IDENTITY_PATTERN.fullmatch(candidate.strip()):
            return candidate.strip()
    digest = hashlib.sha256(stage.encode("utf-8")).hexdigest()[:24]
    return f"trace-f30-{digest}"


def _terminal_failure(value: Any, *, stage: str, code: str, message: str) -> dict[str, Any]:
    """Return a JSON-safe terminal result for F30's one Chat Output.

    The strict loader remains available through :func:`load_f20_report_handoff`.
    The optional component mode is only used by F30 so an invalid child-flow
    input cannot become a generic ``Run Flow`` runtime error in F10.
    """

    return {
        "ok": False,
        "status": "BLOCKED",
        "schema_version": F30_TERMINAL_SCHEMA_VERSION,
        "stage": stage,
        "error": {
            "code": code,
            "message": message,
            "retryable": False,
            "details": {},
        },
        "trace_id": _safe_trace_id(value, stage=stage),
    }


def _parse_handoff_text(value: str) -> dict[str, Any]:
    text = value.lstrip("\ufeff").strip()
    if not text or len(text) > MAX_HANDOFF_TEXT_CHARS:
        raise ValueError("F20 report handoff text is missing or exceeds the size limit")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("F20 report handoff must be JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("F20 report handoff must be an object")
    return parsed


def _unwrap_handoff(value: Any) -> Any:
    """Read the explicit Langflow Message/Data shapes without loosening F20's seal.

    F30 can be called directly from Playground or as F10's Run Flow child.
    Depending on the Langflow 1.11 bridge, the same Chat Output reaches this
    component as ``Message.text``, a JSON ``Data.data`` object, or the
    unparsed ``Data({"text": ...})`` fallback.  Only those three known shapes
    are unwrapped; the final exact-key/hash verification remains unchanged.
    """

    text = getattr(value, "text", None)
    if isinstance(text, str):
        return _parse_handoff_text(text)

    data = getattr(value, "data", None)
    if isinstance(data, str):
        return _parse_handoff_text(data)
    if isinstance(data, dict):
        if set(data) == HANDOFF_KEYS:
            return data
        text = data.get("text")
        if isinstance(text, str):
            return _parse_handoff_text(text)
        nested = data.get("data")
        if isinstance(nested, dict) and set(nested) == HANDOFF_KEYS:
            return nested
        if isinstance(nested, str):
            return _parse_handoff_text(nested)
        return data

    if isinstance(value, str):
        return _parse_handoff_text(value)
    return value


def _payload(value: Any) -> dict[str, Any]:
    value = _unwrap_handoff(value)
    if not isinstance(value, dict):
        raise ValueError("F20 report handoff must be an object")
    return copy.deepcopy(value)


def _canonical_hash(value: Any) -> str:
    material = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _identity(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not IDENTITY_PATTERN.fullmatch(text):
        raise ValueError(f"F20 report handoff {field} is invalid")
    return text


def _sha256(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not SHA256_PATTERN.fullmatch(text):
        raise ValueError(f"F20 report handoff {field} is invalid")
    return text


def _revision(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"F20 report handoff {field} is invalid")
    return value


def load_f20_report_handoff(value: Any) -> dict[str, Any]:
    handoff = _payload(value)
    if set(handoff) != HANDOFF_KEYS:
        raise ValueError("F20 report handoff fields are invalid")
    if handoff.get("ok") is not True or handoff.get("status") != "COMPLETED":
        raise ValueError("F20 report handoff is not successful")
    if handoff.get("schema_version") != HANDOFF_SCHEMA_VERSION:
        raise ValueError("F20 report handoff schema_version is invalid")
    work = handoff.get("work_definition")
    terminal = handoff.get("agent_blueprint")
    trace = handoff.get("retrieval_trace")
    context = handoff.get("execution_context")
    if not isinstance(work, dict) or not isinstance(terminal, dict) or not isinstance(trace, dict) or not isinstance(context, dict):
        raise ValueError("F20 report handoff artifacts are invalid")

    core = {key: copy.deepcopy(handoff[key]) for key in HANDOFF_KEYS - {"ok", "status", "handoff_sha256", "trace_id"}}
    expected_handoff_sha256 = _canonical_hash(core)
    supplied_handoff_sha256 = _sha256(handoff.get("handoff_sha256"), "handoff_sha256")
    if not hmac.compare_digest(supplied_handoff_sha256, expected_handoff_sha256):
        raise ValueError("F20 report handoff hash is invalid")

    tenant_id = _identity(context.get("tenant_id"), "execution_context.tenant_id")
    actor_id = _identity(context.get("actor_id"), "execution_context.actor_id")
    work_id = _identity(context.get("work_definition_id"), "execution_context.work_definition_id")
    revision = _revision(context.get("work_definition_revision"), "execution_context.work_definition_revision")
    approved_hash = _sha256(context.get("approved_hash"), "execution_context.approved_hash")
    design_scope_sha256 = _sha256(handoff.get("design_scope_sha256"), "design_scope_sha256")
    query_plan_sha256 = _sha256(handoff.get("query_plan_sha256"), "query_plan_sha256")
    candidate_allowlist_sha256 = _sha256(handoff.get("candidate_allowlist_sha256"), "candidate_allowlist_sha256")
    if (
        work.get("schema_version") != "work-definition/v1"
        or work.get("status") != "APPROVED"
        or _identity(work.get("tenant_id"), "work_definition.tenant_id") != tenant_id
        or _identity(work.get("owner_id"), "work_definition.owner_id") != actor_id
        or _identity(work.get("work_definition_id"), "work_definition.work_definition_id") != work_id
        or _revision(work.get("revision"), "work_definition.revision") != revision
        or _sha256(work.get("approved_hash"), "work_definition.approved_hash") != approved_hash
    ):
        raise ValueError("F20 report handoff WorkDefinition binding is invalid")

    blueprint = terminal.get("blueprint") if isinstance(terminal.get("blueprint"), dict) else {}
    if (
        terminal.get("ok") is not True
        or terminal.get("status") != "COMPLETED"
        or not blueprint
        or blueprint.get("schema_version") != "agent-blueprint.v1"
        or blueprint.get("terminal_contract") is not True
        or _identity(blueprint.get("tenant_id"), "blueprint.tenant_id") != tenant_id
        or _identity(blueprint.get("work_definition_id"), "blueprint.work_definition_id") != work_id
        or _revision(blueprint.get("work_definition_revision"), "blueprint.work_definition_revision") != revision
        or _sha256(blueprint.get("approved_hash"), "blueprint.approved_hash") != approved_hash
        or _sha256(blueprint.get("design_scope_sha256"), "blueprint.design_scope_sha256") != design_scope_sha256
        or _sha256(blueprint.get("query_plan_sha256"), "blueprint.query_plan_sha256") != query_plan_sha256
        or _sha256(blueprint.get("candidate_allowlist_sha256"), "blueprint.candidate_allowlist_sha256") != candidate_allowlist_sha256
    ):
        raise ValueError("F20 report handoff Blueprint binding is invalid")

    snapshot_id = _identity(blueprint.get("catalog_snapshot_id"), "blueprint.catalog_snapshot_id")
    if (
        _identity(trace.get("tenant_id"), "retrieval_trace.tenant_id") != tenant_id
        or _identity(trace.get("snapshot_id"), "retrieval_trace.snapshot_id") != snapshot_id
        or _identity(trace.get("work_definition_id"), "retrieval_trace.work_definition_id") != work_id
        or _revision(trace.get("work_definition_revision"), "retrieval_trace.work_definition_revision") != revision
        or _sha256(trace.get("approved_hash"), "retrieval_trace.approved_hash") != approved_hash
        or _sha256(trace.get("design_scope_sha256"), "retrieval_trace.design_scope_sha256") != design_scope_sha256
        or _sha256(trace.get("query_plan_sha256"), "retrieval_trace.query_plan_sha256") != query_plan_sha256
        or _sha256(trace.get("candidate_allowlist_sha256"), "retrieval_trace.candidate_allowlist_sha256") != candidate_allowlist_sha256
    ):
        raise ValueError("F20 report handoff Retrieval Trace binding is invalid")
    return {
        "work_definition": work,
        "agent_blueprint": terminal,
        "retrieval_trace": trace,
        "execution_context": {
            "tenant_id": tenant_id,
            "actor_id": actor_id,
            "work_definition_id": work_id,
            "work_definition_revision": revision,
            "approved_hash": approved_hash,
            "handoff_sha256": supplied_handoff_sha256,
        },
    }


class F30ReportHandoffLoaderComponent(Component):
    display_name = "33 F30 Report Handoff Loader"
    description = "F20의 sealed report handoff를 검증하고 F30의 WorkDefinition·Blueprint·trace 입력으로 분리합니다."
    icon = "ShieldCheck"
    name = "F30ReportHandoffLoader"

    inputs = [
        DataInput(name="report_handoff", display_name="F20 Report Handoff", required=True),
        BoolInput(
            name="safe_failure_envelope",
            display_name="F30 오류를 결과로 반환",
            value=False,
            advanced=True,
            info="F30 Flow에서는 켜 둡니다. sealed handoff 검증 실패를 Chat Output의 BLOCKED JSON으로 반환합니다.",
        ),
    ]
    outputs = [
        Output(name="work_definition", display_name="Approved Work Definition", method="build_work_definition", types=["Data"], group_outputs=True),
        Output(name="agent_blueprint", display_name="Terminal Agent Blueprint", method="build_agent_blueprint", types=["Data"], group_outputs=True),
        Output(name="retrieval_trace", display_name="Retrieval Trace", method="build_retrieval_trace", types=["Data"], group_outputs=True),
        Output(name="report_context", display_name="Report Execution Context", method="build_report_context", types=["Data"], group_outputs=True),
    ]

    def _validated(self) -> dict[str, Any]:
        cached = getattr(self, "_validated_handoff", None)
        if not isinstance(cached, dict):
            handoff = getattr(self, "report_handoff", None)
            try:
                cached = load_f20_report_handoff(handoff)
            except (TypeError, ValueError, json.JSONDecodeError):
                if not bool(getattr(self, "safe_failure_envelope", False)):
                    raise
                cached = _terminal_failure(
                    handoff,
                    stage="f30_handoff_loader",
                    code="F30_REPORT_HANDOFF_INVALID",
                    message="F20에서 전달된 보고서 설계 정보를 검증하지 못했습니다. F20 완료 결과를 그대로 연결한 뒤 다시 실행하세요.",
                )
            self._validated_handoff = cached
        return cached

    def _value_or_failure(self, field: str) -> Data:
        validated = self._validated()
        if validated.get("ok") is False:
            self.status = "F30 report handoff blocked: F30_REPORT_HANDOFF_INVALID"
            return Data(data=copy.deepcopy(validated))
        return Data(data=copy.deepcopy(validated[field]))

    def build_work_definition(self) -> Data:
        return self._value_or_failure("work_definition")

    def build_agent_blueprint(self) -> Data:
        return self._value_or_failure("agent_blueprint")

    def build_retrieval_trace(self) -> Data:
        return self._value_or_failure("retrieval_trace")

    def build_report_context(self) -> Data:
        return self._value_or_failure("execution_context")
