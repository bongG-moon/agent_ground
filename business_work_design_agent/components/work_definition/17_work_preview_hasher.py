from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, Output
from lfx.schema import Data


SEMANTIC_FIELDS = (
    "goal",
    "trigger",
    "scope_in",
    "scope_out",
    "actors",
    "systems",
    "inputs",
    "outputs",
    "steps",
    "decisions",
    "exceptions",
    "frequency_volume",
    "sla",
    "pains",
    "risks_controls",
    "constraints",
    "success_criteria",
    "automation_intent",
    "assumptions",
    "unresolved",
    "as_is_graph",
)
UNORDERED_LIST_KEYS = {"scope_in", "scope_out", "actors", "systems", "inputs", "outputs", "pains", "risks_controls", "constraints", "success_criteria", "assumptions", "unresolved", "nodes", "edges", "evidence_turn_ids", "conflicting_values"}
NON_SEMANTIC_KEYS = {
    "x",
    "y",
    "position",
    "position_absolute",
    "style",
    "selected",
    "expanded",
    "display_order",
    "created_at",
    "updated_at",
    "submitted_at",
    "expires_at",
    "trace_id",
    "run_id",
    "job_id",
    "last_updated_revision",
    "confidence",
    "evidence_turn_ids",
    "processed_answer_batches",
}


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        payload = copy.deepcopy(value)
    else:
        data = getattr(value, "data", None)
        if isinstance(data, dict):
            payload = copy.deepcopy(data)
        else:
            text = getattr(value, "text", value if isinstance(value, str) else "")
            payload = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)) if isinstance(text, str) and text.strip() else {}
    return payload


def _canonicalize(value: Any, parent_key: str = "") -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key in sorted(value):
            if key in NON_SEMANTIC_KEYS or key.startswith("ui_") or key.startswith("render_"):
                continue
            result[key] = _canonicalize(value[key], key)
        return result
    if isinstance(value, list):
        items = [_canonicalize(item, parent_key) for item in value]
        if parent_key in UNORDERED_LIST_KEYS:
            items.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str))
        return items
    if isinstance(value, float):
        return float(format(value, ".15g"))
    return value


def build_work_preview_hash(value: Any) -> dict[str, Any]:
    trace_id = f"trace-{uuid.uuid4()}"
    try:
        envelope = _payload(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        envelope = {}
    if envelope.get("ok") is not True:
        upstream_error = envelope.get("error") if isinstance(envelope.get("error"), dict) else {}
        return {
            "ok": False,
            "status": "BLOCKED",
            "artifact_refs": [],
            "error": {
                "code": "WORK_PREVIEW_UPSTREAM_REJECTED",
                "message": "성공한 업무 graph 검증 결과만 승인 Preview로 만들 수 있습니다.",
                "retryable": False,
                "details": {"upstream_error_code": upstream_error.get("code")},
            },
            "resume": None,
            "trace_id": trace_id,
        }
    graph_validation = envelope.get("graph_validation")
    work = envelope.get("work_definition")
    if not isinstance(work, dict) or not isinstance(graph_validation, dict) or graph_validation.get("valid") is not True:
        return {
            "ok": False,
            "status": "BLOCKED",
            "artifact_refs": [],
            "error": {
                "code": "WORK_PREVIEW_GRAPH_ATTESTATION_REQUIRED",
                "message": "유효한 WorkGraphNormalizer 검증 증거가 필요합니다.",
                "retryable": False,
                "details": {},
            },
            "resume": None,
            "trace_id": trace_id,
        }
    missing = [key for key in ("work_definition_id", "tenant_id", "revision", "as_is_graph") if work.get(key) in (None, "")]
    if missing:
        return {
            "ok": False,
            "status": "BLOCKED",
            "artifact_refs": [],
            "error": {"code": "WORK_PREVIEW_INPUT_INVALID", "message": "Preview hash를 만들 필수 WorkDefinition 필드가 없습니다.", "retryable": False, "details": {"fields": missing}},
            "resume": None,
            "trace_id": trace_id,
        }
    try:
        work_revision = int(work["revision"])
    except (TypeError, ValueError):
        return {
            "ok": False,
            "status": "BLOCKED",
            "artifact_refs": [],
            "error": {"code": "WORK_PREVIEW_REVISION_INVALID", "message": "Preview 대상 revision은 정수여야 합니다.", "retryable": False, "details": {}},
            "resume": None,
            "trace_id": trace_id,
        }
    graph = work.get("as_is_graph")
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges"), list):
        return {
            "ok": False,
            "status": "BLOCKED",
            "artifact_refs": [],
            "error": {"code": "WORK_PREVIEW_GRAPH_INVALID", "message": "검증 가능한 AS-IS graph가 없습니다.", "retryable": False, "details": {}},
            "resume": None,
            "trace_id": trace_id,
        }

    semantic = {field: copy.deepcopy(work.get(field)) for field in SEMANTIC_FIELDS}
    # New F10 records preserve a sealed intake fragment so an ordinary
    # Playground chat reply can safely resume after Component 10 is excluded.
    # Keep its presence conditional: older approved records do not have this
    # field and must retain their already-published approval hash.
    if "f10_design_context" in work:
        semantic["f10_design_context"] = copy.deepcopy(work.get("f10_design_context"))
    canonical_preview = _canonicalize(semantic)
    try:
        canonical_text = json.dumps(canonical_preview, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "status": "BLOCKED",
            "artifact_refs": [],
            "error": {"code": "WORK_PREVIEW_NON_CANONICAL_VALUE", "message": "업무 의미 필드에 canonical JSON으로 표현할 수 없는 값이 있습니다.", "retryable": False, "details": {}},
            "resume": None,
            "trace_id": trace_id,
        }
    preview_hash = "sha256:" + hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()
    previous_preview = work.get("preview_hash")
    previous_approved = work.get("approved_hash")
    changed = bool(previous_preview and previous_preview != preview_hash)
    updated = copy.deepcopy(work)
    updated["preview_hash"] = preview_hash
    approval_invalidated = bool(previous_approved and previous_approved != preview_hash)
    if approval_invalidated:
        updated["approved_hash"] = None
        updated["status"] = "READY_FOR_REVIEW"
    elif previous_approved == preview_hash:
        updated["status"] = "APPROVED"
    else:
        updated["status"] = "READY_FOR_REVIEW"
    return {
        "ok": True,
        "status": updated["status"],
        "artifact_refs": [{"kind": "work_definition_preview", "id": work["work_definition_id"], "revision": work_revision, "sha256": preview_hash}],
        "work_definition": updated,
        "preview": {
            "schema_version": "work-definition-preview/v1",
            "work_definition_id": work["work_definition_id"],
            "revision": work_revision,
            "preview_hash": preview_hash,
            "canonical_json": canonical_text,
            "changed": changed,
            "approval_invalidated": approval_invalidated,
        },
        "trace_id": trace_id,
    }


class WorkPreviewHasherComponent(Component):
    display_name = "17 업무 Preview Hash"
    description = "유효한 WorkGraphNormalizer 증거를 확인한 뒤 UI 좌표와 시각·시간·trace 필드를 제외한 업무 의미 JSON을 canonicalize하고 승인 SHA-256을 계산합니다."
    icon = "Fingerprint"
    name = "WorkPreviewHasher"

    inputs = [
        DataInput(name="work_definition", display_name="WorkGraphNormalizer 검증 결과", input_types=["Data", "JSON"], required=True),
        DataInput(
            name="route_trigger",
            display_name="READY_FOR_REVIEW Route Trigger",
            input_types=["Data", "JSON", "Message"],
            required=False,
            advanced=True,
            info="Optional control dependency from a conditional router; excluded from the canonical hash.",
        ),
    ]
    outputs = [
        Output(name="preview", display_name="Canonical Preview", method="build_preview", types=["Data"]),
        Output(name="success_path", display_name="Preview 생성 성공", method="route_preview", types=["Data"], group_outputs=True),
        Output(name="blocked_path", display_name="Preview 생성 차단", method="route_preview", types=["Data"], group_outputs=True),
    ]

    def _result(self) -> dict[str, Any]:
        result = getattr(self, "_preview_result", None)
        if isinstance(result, dict):
            return result
        result = build_work_preview_hash(getattr(self, "work_definition", None))
        self._preview_result = result
        self.status = {
            "ok": result["ok"],
            "status": result["status"],
            "preview_hash": (result.get("preview") or {}).get("preview_hash"),
        }
        return result

    def build_preview(self) -> Data:
        return Data(data=self._result())

    def _component_id(self) -> str:
        return str(getattr(self, "_id", "") or self.name)[:200]

    def _select_output_route(self, selected: str) -> None:
        output_names = ("success_path", "blocked_path")
        non_selected = [output_name for output_name in output_names if output_name != selected]
        for output_name in non_selected:
            self.stop(output_name)
        graph = getattr(self, "graph", None)
        exclude = getattr(graph, "exclude_branches_conditionally", None) if graph is not None else None
        if callable(exclude):
            exclude(self._component_id(), non_selected)

    def _is_nonselected_group_output(self, selected: str) -> bool:
        current_output = str(getattr(self, "_current_output", "") or "")
        return bool(current_output and current_output in {"success_path", "blocked_path"} and current_output != selected)

    def route_preview(self) -> Data:
        result = self._result()
        selected = "success_path" if result.get("ok") is True else "blocked_path"
        self._select_output_route(selected)
        if self._is_nonselected_group_output(selected):
            return Data(data={})
        return Data(data=result)
