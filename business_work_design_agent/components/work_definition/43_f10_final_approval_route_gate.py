from __future__ import annotations

"""Route F10's final native Human Input decision without eager branch reads.

The preceding built-in ``Human Input`` still owns the user-facing
Approve/Reject/Cancel card.  This small standalone bridge reads its resolved
decision from the Langflow graph, immediately marks the two unchosen branches
as conditionally excluded, and sends a Data trigger only to the selected
MongoDB state-store node.

That extra exclusion is required in Langflow 1.11: ``HumanInput.stop()``
controls the current scheduler pass but does not always make an unbuilt sibling
safe for a later fan-in node to read during the same resumed run.
"""

import copy
import uuid
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, Output
from lfx.schema import Data


_ACTIONS = ("approve", "reject", "cancel")
_OUTPUTS = tuple(f"branch_{action}" for action in _ACTIONS) + ("blocked_path",)


def _component_id(component: Any) -> str:
    return str(getattr(component, "_id", "") or getattr(component, "name", "") or "F10FinalApprovalRouteGate")[:200]


def _edge_field_name(edge: Any) -> str:
    handle = getattr(edge, "target_handle", None)
    return str(getattr(handle, "field_name", "") or "")


def _resolved_action(graph: Any, component_id: str) -> str:
    """Return the decision chosen on the upstream built-in Human Input node."""

    decisions = getattr(graph, "human_input_decisions", None) if graph is not None else None
    if not isinstance(decisions, dict):
        return ""
    run_id = str(getattr(graph, "run_id", "") or "")
    upstream_ids: list[str] = []
    for edge in getattr(graph, "edges", []) or []:
        if str(getattr(edge, "target_id", "") or "") != component_id:
            continue
        if _edge_field_name(edge) != "approval_triggers":
            continue
        source_id = str(getattr(edge, "source_id", "") or "")
        if source_id and source_id not in upstream_ids:
            upstream_ids.append(source_id)
    for source_id in upstream_ids:
        decision = decisions.get(f"{source_id}:{run_id}")
        if not isinstance(decision, dict):
            continue
        action = str(decision.get("action_id") or "").strip().lower()
        if action in _ACTIONS:
            return action
    # The direct component test path has no serialized edge objects.  A scan is
    # safe because the only accepted values are this gate's three final actions.
    matches = [
        str(value.get("action_id") or "").strip().lower()
        for value in decisions.values()
        if isinstance(value, dict) and str(value.get("action_id") or "").strip().lower() in _ACTIONS
    ]
    return matches[-1] if len(matches) == 1 else ""


def build_final_approval_result(graph: Any, component_id: str) -> dict[str, Any]:
    action = _resolved_action(graph, component_id)
    trace_id = f"trace-{uuid.uuid4()}"
    if not action:
        return {
            "ok": False,
            "status": "BLOCKED",
            "route": "blocked_path",
            "artifact_refs": [],
            "error": {
                "code": "FINAL_APPROVAL_DECISION_UNAVAILABLE",
                "message": "최종 승인 선택 결과를 확인하지 못했습니다. 새 실행에서 Approve, Reject 또는 Cancel을 다시 선택해 주세요.",
                "retryable": False,
                "details": {},
            },
            "trace_id": trace_id,
        }
    return {
        "ok": True,
        "status": "FINAL_APPROVAL_SELECTED",
        "route": f"branch_{action}",
        "selected_action": action,
        "artifact_refs": [],
        "trace_id": trace_id,
    }


class F10FinalApprovalRouteGateComponent(Component):
    display_name = "43 최종 승인 경로 Gate"
    description = "내장 Human Input의 최종 승인 선택을 하나의 경로로 고정하고, 선택하지 않은 저장 경로를 즉시 제외합니다. 직접 입력하지 않습니다."
    icon = "GitBranch"
    name = "F10FinalApprovalRouteGate"

    inputs = [
        DataInput(
            name="approval_triggers",
            display_name="최종 승인 신호 (자동 연결)",
            input_types=["Data", "JSON", "Message"],
            required=False,
            is_list=True,
            advanced=False,
            info="내장 Human Input의 Approve·Reject·Cancel 출력이 모두 자동 연결됩니다. 실제 선택은 Langflow HITL 결정에서 판별하므로 직접 입력하지 않습니다.",
        )
    ]
    outputs = [
        Output(name="branch_approve", display_name="Approve", method="route_final_action", types=["Data"], group_outputs=True),
        Output(name="branch_reject", display_name="Reject", method="route_final_action", types=["Data"], group_outputs=True),
        Output(name="branch_cancel", display_name="Cancel", method="route_final_action", types=["Data"], group_outputs=True),
        Output(name="blocked_path", display_name="승인 선택 차단", method="route_final_action", types=["Data"], group_outputs=True),
    ]

    def _result(self) -> dict[str, Any]:
        cached = getattr(self, "_approval_route_result", None)
        if isinstance(cached, dict):
            return cached
        result = build_final_approval_result(getattr(self, "graph", None), _component_id(self))
        self._approval_route_result = result
        return result

    def route_final_action(self) -> Data:
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
        if getattr(self, "_current_output", "") and getattr(self, "_current_output", "") != selected:
            return Data(data={})
        return Data(data=copy.deepcopy(result))
