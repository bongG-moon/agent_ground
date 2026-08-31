from __future__ import annotations

"""Route only a sealed successful F20 report handoff into the F30 Run Flow."""

import copy
import hashlib
import hmac
import json
import re
import uuid
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, Output
from lfx.schema import Data, Message


HANDOFF_SCHEMA_VERSION = "f20-report-handoff/v1"
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_OUTPUTS = ("success_message", "blocked_path")


def _payload(value: Any) -> dict[str, Any]:
    """Extract a handoff from Data, JSON, or an actual Langflow Message.

    ``lfx.schema.Message.data`` is a message metadata dictionary rather than
    the JSON emitted by a Chat Output.  Reading it before ``Message.text``
    makes a valid sealed F20 handoff look like a malformed payload in a real
    Run Flow execution.  Prefer non-empty message text, then fall back to a
    Data payload.
    """

    if not isinstance(value, dict):
        text = getattr(value, "text", None)
        data = getattr(value, "data", None)
        value = text if isinstance(text, str) and text.strip() else (data if isinstance(data, dict) else value)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def _canonical_hash(value: Any) -> str:
    material = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _component_id(component: Any) -> str:
    return str(getattr(component, "_id", "") or getattr(component, "name", "") or "F10ReportHandoffGate")[:200]


def _forward_upstream_blocked(handoff: dict[str, Any], trace_id: str) -> dict[str, Any] | None:
    """Keep an F20 structured failure visible to the F10 terminal message.

    F20 may correctly return a blocked envelope before it can create the
    sealed report-handoff contract.  That envelope deliberately has a
    different shape, so it must be forwarded before validating the successful
    handoff schema.  Otherwise the useful F20 code is overwritten with a
    generic ``F20_REPORT_HANDOFF_FIELDS_INVALID`` error.
    """

    error = handoff.get("error")
    if (
        handoff.get("ok") is not False
        or handoff.get("status") != "BLOCKED"
        or handoff.get("schema_version") != HANDOFF_SCHEMA_VERSION
        or not isinstance(error, dict)
    ):
        return None
    result: dict[str, Any] = {
        "ok": False,
        "status": "BLOCKED",
        "route": "blocked_path",
        "error": copy.deepcopy(error),
        "trace_id": trace_id,
    }
    upstream_trace_id = handoff.get("trace_id")
    if isinstance(upstream_trace_id, str) and upstream_trace_id.strip():
        result["upstream_trace_id"] = upstream_trace_id.strip()[:200]
    return result


def validate_f20_report_handoff(value: Any) -> dict[str, Any]:
    trace_id = f"trace-{uuid.uuid4()}"
    handoff = _payload(value)
    upstream_blocked = _forward_upstream_blocked(handoff, trace_id)
    if upstream_blocked is not None:
        return upstream_blocked
    required = {
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
    if set(handoff) != required:
        return {
            "ok": False,
            "status": "BLOCKED",
            "route": "blocked_path",
            "error": {"code": "F20_REPORT_HANDOFF_FIELDS_INVALID", "message": "F20 report handoff 형식이 유효하지 않습니다."},
            "trace_id": trace_id,
        }
    if handoff.get("ok") is not True or handoff.get("status") != "COMPLETED" or handoff.get("schema_version") != HANDOFF_SCHEMA_VERSION:
        return {
            "ok": False,
            "status": "BLOCKED",
            "route": "blocked_path",
            "error": {"code": "F20_REPORT_HANDOFF_NOT_READY", "message": "완료된 F20 report handoff가 필요합니다."},
            "trace_id": trace_id,
        }
    supplied_hash = str(handoff.get("handoff_sha256") or "").strip()
    core = {key: copy.deepcopy(handoff[key]) for key in required - {"ok", "status", "handoff_sha256", "trace_id"}}
    try:
        expected_hash = _canonical_hash(core)
    except (TypeError, ValueError):
        expected_hash = ""
    if not SHA256_PATTERN.fullmatch(supplied_hash) or not expected_hash or not hmac.compare_digest(supplied_hash, expected_hash):
        return {
            "ok": False,
            "status": "BLOCKED",
            "route": "blocked_path",
            "error": {"code": "F20_REPORT_HANDOFF_HASH_INVALID", "message": "F20 report handoff 무결성 검증에 실패했습니다."},
            "trace_id": trace_id,
        }
    return {
        "ok": True,
        "status": "READY_FOR_REPORT",
        "route": "success_message",
        "handoff": handoff,
        "trace_id": trace_id,
    }


class F10ReportHandoffGateComponent(Component):
    display_name = "44 F20→F30 Report Handoff Gate"
    description = "F20 report handoff의 schema·hash를 검증하고 성공한 결과만 F30 Run Flow로 전달합니다."
    icon = "ShieldCheck"
    name = "F10ReportHandoffGate"

    inputs = [
        DataInput(
            name="f20_report_handoff",
            display_name="F20 Report Handoff (자동 연결)",
            input_types=["Data", "JSON", "Message"],
            required=True,
            info="F20의 sealed report handoff만 자동 연결합니다. 직접 입력하지 않습니다.",
        )
    ]
    outputs = [
        Output(name="success_message", display_name="Verified F30 Input", method="build_success_message", types=["Message"], group_outputs=True),
        Output(name="blocked_path", display_name="Report Handoff Blocked", method="build_blocked_path", types=["Data"], group_outputs=True),
    ]

    def _result(self) -> dict[str, Any]:
        cached = getattr(self, "_handoff_result", None)
        if not isinstance(cached, dict):
            cached = validate_f20_report_handoff(getattr(self, "f20_report_handoff", None))
            self._handoff_result = cached
        return cached

    def _route(self) -> tuple[dict[str, Any], str]:
        result = self._result()
        selected = str(result.get("route") or "blocked_path")
        if selected not in _OUTPUTS:
            selected = "blocked_path"
        non_selected = [output_name for output_name in _OUTPUTS if output_name != selected]
        for output_name in non_selected:
            self.stop(output_name)
        graph = getattr(self, "graph", None)
        exclude = getattr(graph, "exclude_branches_conditionally", None) if graph is not None else None
        if callable(exclude):
            exclude(_component_id(self), non_selected)
        self.status = {"ok": result.get("ok"), "status": result.get("status"), "route": selected}
        return result, selected

    def build_success_message(self) -> Message:
        result, selected = self._route()
        if selected != "success_message":
            return Message(text="")
        handoff = result.get("handoff") if isinstance(result.get("handoff"), dict) else {}
        return Message(text=json.dumps(handoff, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))

    def build_blocked_path(self) -> Data:
        result, selected = self._route()
        if selected != "blocked_path":
            return Data(data={})
        return Data(data={key: value for key, value in result.items() if key != "handoff"})
