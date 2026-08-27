from __future__ import annotations

"""Route a WorkDefinition around the clarification pause without local imports."""

import copy
import json
import re
import uuid
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, Output
from lfx.schema import Data


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return copy.deepcopy(value)
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return copy.deepcopy(data)
    text = getattr(value, "text", value if isinstance(value, str) else "")
    if isinstance(text, str) and text.strip():
        return json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE))
    return {}


def _named(value: Any, key: str) -> dict[str, Any]:
    payload = _payload(value)
    nested = payload.get(key)
    return copy.deepcopy(nested) if isinstance(nested, dict) else payload


def route_work_clarification(work_value: Any, clarification_value: Any) -> dict[str, Any]:
    """Return one and only one route plus the payload for that route."""

    trace_id = f"trace-{uuid.uuid4()}"
    try:
        work = _named(work_value, "work_definition")
        clarification = _payload(clarification_value)
    except (TypeError, ValueError, json.JSONDecodeError):
        work, clarification = {}, {}
    missing = [
        key
        for key in ("work_definition_id", "tenant_id", "owner_id", "session_id", "channel_mode", "revision")
        if work.get(key) in (None, "")
    ]
    if missing:
        return {
            "ok": False,
            "status": "BLOCKED",
            "route": "blocked_path",
            "artifact_refs": [],
            "error": {
                "code": "WORK_ROUTE_INPUT_INVALID",
                "message": "Clarification 분기에 필요한 WorkDefinition 식별자가 없습니다.",
                "retryable": False,
                "details": {"fields": missing},
            },
            "resume": None,
            "trace_id": trace_id,
        }
    if not clarification.get("ok"):
        blocked = copy.deepcopy(clarification)
        blocked.update({"ok": False, "status": "BLOCKED", "route": "blocked_path"})
        blocked.setdefault("artifact_refs", [])
        blocked.setdefault(
            "error",
            {
                "code": "CLARIFICATION_ROUTE_BLOCKED",
                "message": "질문 생성 결과가 유효하지 않아 분기를 진행할 수 없습니다.",
                "retryable": False,
                "details": {},
            },
        )
        blocked.setdefault("resume", None)
        blocked.setdefault("trace_id", trace_id)
        return blocked

    batch = clarification.get("clarification_batch")
    if clarification.get("status") == "WAITING_ANSWER" and isinstance(batch, dict):
        identity_fields = ("work_definition_id", "tenant_id", "owner_id", "session_id", "channel_mode")
        mismatch = [key for key in identity_fields if str(batch.get(key) or "") != str(work.get(key) or "")]
        try:
            revision_matches = int(batch.get("revision", -1)) == int(work.get("revision", -2))
        except (TypeError, ValueError):
            revision_matches = False
        if mismatch or not revision_matches:
            return {
                "ok": False,
                "status": "BLOCKED",
                "route": "blocked_path",
                "artifact_refs": [],
                "error": {
                    "code": "CLARIFICATION_ROUTE_SCOPE_MISMATCH",
                    "message": "질문 batch와 저장된 WorkDefinition의 scope 또는 revision이 다릅니다.",
                    "retryable": False,
                    "details": {"fields": mismatch, "revision_matches": revision_matches},
                },
                "resume": None,
                "trace_id": trace_id,
            }
        routed = copy.deepcopy(clarification)
        routed["route"] = "clarification_path"
        routed["work_definition"] = copy.deepcopy(work)
        return routed

    if clarification.get("status") == "READY_FOR_REVIEW" and batch is None:
        return {
            "ok": True,
            "status": "READY_FOR_REVIEW",
            "route": "review_path",
            "artifact_refs": [
                {
                    "kind": "work_definition",
                    "id": work["work_definition_id"],
                    "revision": int(work["revision"]),
                }
            ],
            "work_definition": copy.deepcopy(work),
            "trace_id": trace_id,
        }

    return {
        "ok": False,
        "status": "BLOCKED",
        "route": "blocked_path",
        "artifact_refs": [],
        "error": {
            "code": "CLARIFICATION_ROUTE_STATE_INVALID",
            "message": "질문 생성 결과가 WAITING_ANSWER 또는 READY_FOR_REVIEW 계약과 일치하지 않습니다.",
            "retryable": False,
            "details": {"clarification_status": clarification.get("status")},
        },
        "resume": None,
        "trace_id": trace_id,
    }


class WorkClarificationRouterComponent(Component):
    display_name = "27 업무 보완 분기 Router"
    description = "질문 batch가 있으면 native/playground 보완 경로로, 없으면 review 경로로 정확히 하나만 실행합니다."
    icon = "GitBranch"
    name = "WorkClarificationRouter"

    inputs = [
        DataInput(name="work_definition", display_name="저장된 WorkDefinition", input_types=["Data", "JSON"], required=True),
        DataInput(name="clarification_result", display_name="질문 Batch 생성 결과", input_types=["Data", "JSON"], required=True),
    ]
    outputs = [
        Output(
            name="clarification_path",
            display_name="질문 필요",
            method="route_branch",
            types=["Data"],
            group_outputs=True,
        ),
        Output(
            name="review_path",
            display_name="바로 Review",
            method="route_branch",
            types=["Data"],
            group_outputs=True,
        ),
        Output(
            name="blocked_path",
            display_name="차단됨",
            method="route_branch",
            types=["Data"],
            group_outputs=True,
        ),
    ]

    def route_branch(self) -> Data:
        result = route_work_clarification(
            getattr(self, "work_definition", None),
            getattr(self, "clarification_result", None),
        )
        selected = str(result.get("route") or "blocked_path")
        for output_name in ("clarification_path", "review_path", "blocked_path"):
            if output_name != selected:
                self.stop(output_name)
        self.status = {"ok": result.get("ok"), "status": result.get("status"), "route": selected}
        return Data(data=result)
