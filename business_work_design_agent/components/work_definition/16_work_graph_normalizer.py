from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from collections import deque
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, IntInput, Output
from lfx.schema import Data


NODE_KINDS = {"start", "task", "decision", "human_review", "system_call", "subflow", "end", "exception"}


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
    nested = payload.get("work_definition")
    return copy.deepcopy(nested) if isinstance(nested, dict) else payload


def _value(value: Any) -> Any:
    return value.get("value") if isinstance(value, dict) and "value" in value and "status" in value else value


def _safe_id(value: Any, prefix: str, material: str) -> str:
    supplied = re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(value or "").strip())[:200].strip("-")
    if supplied:
        return supplied
    return f"{prefix}-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:16]}"


def _detail_text(value: Any, maximum: int = 20_000) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(_value(value) or "")).strip()[:maximum]


def _problem_items(value: Any) -> list[Any]:
    source = value if isinstance(value, list) else ([value] if value not in (None, "") else [])
    result: list[Any] = []
    for item in source[:50]:
        if isinstance(item, dict):
            clean = {
                key: _detail_text(item.get(key), 5_000)
                for key in ("id", "title", "description", "impact", "evidence_ref")
                if item.get(key) not in (None, "")
            }
            if clean:
                result.append(clean)
        else:
            text = _detail_text(item, 5_000)
            if text:
                result.append(text)
    return result


def _build_linear_graph(work: dict[str, Any]) -> dict[str, Any]:
    work_id = str(work.get("work_definition_id") or "work")
    steps = work.get("steps") if isinstance(work.get("steps"), list) else []
    decisions = work.get("decisions") if isinstance(work.get("decisions"), list) else []
    nodes: list[dict[str, Any]] = [{"id": "start", "kind": "start", "label": "업무 시작", "detail_ref": "detail-start"}]
    edges: list[dict[str, Any]] = []
    step_nodes: list[dict[str, Any]] = []
    step_ref_to_node: dict[str, str] = {}
    for index, raw_step in enumerate(steps[:500], start=1):
        step = raw_step if isinstance(raw_step, dict) else {"value": raw_step}
        node_id = _safe_id(step.get("id") or step.get("step_id"), "step", f"{work_id}|step|{index}|{json.dumps(step, sort_keys=True, ensure_ascii=False, default=str)}")
        label = str(_value(step.get("title") or step.get("label") or step.get("action") or step.get("value")) or f"업무 단계 {index}")[:300]
        kind = str(step.get("kind") or "task").lower()
        if kind not in NODE_KINDS or kind in {"start", "end", "decision"}:
            kind = "task"
        step_ref = str(step.get("id") or step.get("step_id") or node_id)
        node = {
            "id": node_id,
            "kind": kind,
            "label": label,
            "actor_ref": step.get("actor_ref") or step.get("actor"),
            "step_ref": step_ref,
            "detail_ref": f"detail-{node_id}",
            "current_work": _detail_text(step.get("current_work") or step.get("capability") or step.get("description")),
            "problems": _problem_items(step.get("problems")),
            "improvement": _detail_text(step.get("improvement")),
        }
        nodes.append(node)
        step_nodes.append(node)
        step_ref_to_node[step_ref] = node_id

    decision_records: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for index, raw_decision in enumerate(decisions[:200], start=1):
        decision = raw_decision if isinstance(raw_decision, dict) else {"value": raw_decision}
        node_id = _safe_id(decision.get("id") or decision.get("decision_id"), "decision", f"{work_id}|decision|{index}|{json.dumps(decision, sort_keys=True, ensure_ascii=False, default=str)}")
        label = str(_value(decision.get("label") or decision.get("title") or decision.get("question") or decision.get("condition") or decision.get("value")) or f"업무 판단 {index}")[:300]
        node = {"id": node_id, "kind": "decision", "label": label, "actor_ref": decision.get("actor_ref") or decision.get("actor"), "step_ref": decision.get("id") or decision.get("decision_id") or node_id, "detail_ref": f"detail-{node_id}"}
        nodes.append(node)
        decision_records.append((decision, node))

    nodes.append({"id": "end", "kind": "end", "label": "업무 종료", "detail_ref": "detail-end"})

    decisions_after: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    unattached: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for decision, node in decision_records:
        after_ref = str(decision.get("after_step_ref") or decision.get("after_step_id") or "")
        after_node = step_ref_to_node.get(after_ref)
        if after_node:
            decisions_after.setdefault(after_node, []).append((decision, node))
        else:
            unattached.append((decision, node))

    first_target = step_nodes[0]["id"] if step_nodes else (unattached[0][1]["id"] if unattached else "end")
    edges.append({"id": _safe_id("", "edge", f"start|{first_target}"), "source": "start", "target": first_target, "branch_label": "시작", "condition": None})
    for index, step_node in enumerate(step_nodes):
        next_target = step_nodes[index + 1]["id"] if index + 1 < len(step_nodes) else (unattached[0][1]["id"] if unattached else "end")
        attached = decisions_after.get(step_node["id"], [])
        if attached:
            for attached_index, (_, decision_node) in enumerate(attached, start=1):
                decision_target = decision_node["id"]
                label = "판단" if len(attached) == 1 else f"판단 {attached_index}"
                edges.append({"id": _safe_id("", "edge", f"{step_node['id']}|{decision_target}|{attached_index}"), "source": step_node["id"], "target": decision_target, "branch_label": label, "condition": None})
        else:
            edges.append({"id": _safe_id("", "edge", f"{step_node['id']}|{next_target}"), "source": step_node["id"], "target": next_target, "branch_label": "다음" if next_target != "end" else "완료", "condition": None})

    for decision_index, (decision, node) in enumerate(decision_records):
        branches = decision.get("branches") or decision.get("outcomes")
        branches = branches if isinstance(branches, list) else []
        after_ref = str(decision.get("after_step_ref") or decision.get("after_step_id") or "")
        after_node_id = step_ref_to_node.get(after_ref)
        default_target = "end"
        if after_node_id:
            position = next((idx for idx, step_node in enumerate(step_nodes) if step_node["id"] == after_node_id), -1)
            if 0 <= position + 1 < len(step_nodes):
                default_target = step_nodes[position + 1]["id"]
        elif decision_index + 1 < len(decision_records):
            default_target = decision_records[decision_index + 1][1]["id"]
        if not branches:
            material = f"{node['id']}|{default_target}|implicit-default"
            edges.append({
                "id": _safe_id("", "edge", material),
                "source": node["id"],
                "target": default_target,
                "branch_label": "다음",
                "condition": None,
                "default": True,
            })
        for branch_index, raw_branch in enumerate(branches):
            branch = raw_branch if isinstance(raw_branch, dict) else {"label": str(raw_branch)}
            target_ref = str(branch.get("target_ref") or branch.get("target_step_ref") or branch.get("target") or "")
            known_decision_ids = {item[1]["id"] for item in decision_records}
            target = step_ref_to_node.get(target_ref) or (target_ref if target_ref in known_decision_ids or target_ref == "end" else "") or default_target
            label = str(branch.get("branch_label") or branch.get("label") or branch.get("name") or "").strip()[:300]
            condition = copy.deepcopy(branch.get("condition"))
            is_default = bool(branch.get("default", False))
            material = f"{node['id']}|{target}|{branch_index}|{label}|{condition}|{is_default}"
            edges.append({"id": _safe_id("", "edge", material), "source": node["id"], "target": target, "branch_label": label, "condition": condition, "default": is_default})
    return {"nodes": nodes, "edges": edges, "loop_policy": None}


def _normalize_node(raw: dict[str, Any], index: int, work_id: str) -> dict[str, Any]:
    node_id = _safe_id(raw.get("id") or raw.get("node_id"), "node", f"{work_id}|node|{index}|{json.dumps(raw, sort_keys=True, ensure_ascii=False, default=str)}")
    kind = str(raw.get("kind") or raw.get("node_type") or "task").strip().lower()
    label = str(_value(raw.get("label") or raw.get("title") or raw.get("name")) or node_id).strip()[:300]
    result = {
        "id": node_id,
        "kind": kind,
        "label": label,
        "actor_ref": raw.get("actor_ref"),
        "step_ref": raw.get("step_ref"),
        "change_state": str(raw.get("change_state") or "unchanged")[:40],
        "detail_ref": str(raw.get("detail_ref") or f"detail-{node_id}")[:300],
        "current_work": _detail_text(raw.get("current_work") or raw.get("as_is") or raw.get("description")),
        "problems": _problem_items(raw.get("problems")),
        "improvement": _detail_text(raw.get("improvement") or raw.get("to_be")),
    }
    if isinstance(raw.get("loop_policy"), dict):
        result["loop_policy"] = copy.deepcopy(raw["loop_policy"])
    return result


def _normalize_edge(raw: dict[str, Any], index: int) -> dict[str, Any]:
    source = str(raw.get("source") or raw.get("source_id") or "").strip()[:200]
    target = str(raw.get("target") or raw.get("target_id") or "").strip()[:200]
    edge_id = _safe_id(raw.get("id") or raw.get("edge_id"), "edge", f"{source}|{target}|{index}|{raw.get('branch_label')}|{raw.get('condition')}")
    result = {
        "id": edge_id,
        "source": source,
        "target": target,
        "branch_label": str(raw.get("branch_label") or "").strip()[:300],
        "condition": copy.deepcopy(raw.get("condition")),
        "default": bool(raw.get("default", False)),
    }
    if isinstance(raw.get("loop_policy"), dict):
        result["loop_policy"] = copy.deepcopy(raw["loop_policy"])
    return result


def _has_bounded_loop_policy(graph: dict[str, Any], cycle_edges: list[dict[str, Any]]) -> bool:
    policies = [graph.get("loop_policy")] + [edge.get("loop_policy") for edge in cycle_edges]
    for policy in policies:
        if not isinstance(policy, dict):
            continue
        try:
            bounded = 1 <= int(policy.get("max_iterations", 0)) <= 10_000
        except (TypeError, ValueError):
            bounded = False
        exit_condition = bool(str(policy.get("exit_condition") or "").strip())
        if bounded or exit_condition:
            return True
    return False


def normalize_work_graph(value: Any, *, max_nodes: Any = 500, max_edges: Any = 2000) -> dict[str, Any]:
    trace_id = f"trace-{uuid.uuid4()}"
    try:
        work = _payload(value)
        node_limit = max(2, min(int(max_nodes), 2000))
        edge_limit = max(1, min(int(max_edges), 10_000))
    except (TypeError, ValueError, json.JSONDecodeError):
        work, node_limit, edge_limit = {}, 500, 2000
    if not work.get("work_definition_id") or work.get("revision") is None:
        return {"ok": False, "status": "BLOCKED", "artifact_refs": [], "error": {"code": "WORK_GRAPH_INPUT_INVALID", "message": "Graph 정규화에 필요한 WorkDefinition 식별자가 없습니다.", "retryable": False, "details": {}}, "resume": None, "trace_id": trace_id}
    if isinstance(work.get("revision"), bool):
        return {"ok": False, "status": "BLOCKED", "artifact_refs": [], "error": {"code": "WORK_GRAPH_REVISION_INVALID", "message": "Graph 대상 revision은 정수여야 합니다.", "retryable": False, "details": {}}, "resume": None, "trace_id": trace_id}
    try:
        work_revision = int(work["revision"])
    except (TypeError, ValueError):
        return {"ok": False, "status": "BLOCKED", "artifact_refs": [], "error": {"code": "WORK_GRAPH_REVISION_INVALID", "message": "Graph 대상 revision은 정수여야 합니다.", "retryable": False, "details": {}}, "resume": None, "trace_id": trace_id}
    if work_revision < 0:
        return {"ok": False, "status": "BLOCKED", "artifact_refs": [], "error": {"code": "WORK_GRAPH_REVISION_INVALID", "message": "Graph 대상 revision은 0 이상의 정수여야 합니다.", "retryable": False, "details": {}}, "resume": None, "trace_id": trace_id}

    raw_graph = work.get("as_is_graph") if isinstance(work.get("as_is_graph"), dict) else {}
    if not isinstance(raw_graph.get("nodes"), list) or not raw_graph.get("nodes"):
        raw_graph = _build_linear_graph(work)
    raw_nodes = raw_graph.get("nodes") if isinstance(raw_graph.get("nodes"), list) else []
    raw_edges = raw_graph.get("edges") if isinstance(raw_graph.get("edges"), list) else []
    errors: list[dict[str, Any]] = []
    if len(raw_nodes) > node_limit or len(raw_edges) > edge_limit:
        errors.append({"code": "GRAPH_SIZE_LIMIT_EXCEEDED", "details": {"node_count": len(raw_nodes), "edge_count": len(raw_edges), "max_nodes": node_limit, "max_edges": edge_limit}})
        raw_nodes, raw_edges = raw_nodes[:node_limit], raw_edges[:edge_limit]

    nodes = [_normalize_node(raw, index, str(work["work_definition_id"])) for index, raw in enumerate(raw_nodes) if isinstance(raw, dict)]
    edges = [_normalize_edge(raw, index) for index, raw in enumerate(raw_edges) if isinstance(raw, dict)]
    node_ids = [node["id"] for node in nodes]
    duplicate_nodes = sorted({node_id for node_id in node_ids if node_ids.count(node_id) > 1})
    edge_ids = [edge["id"] for edge in edges]
    duplicate_edges = sorted({edge_id for edge_id in edge_ids if edge_ids.count(edge_id) > 1})
    if duplicate_nodes:
        errors.append({"code": "GRAPH_DUPLICATE_NODE_ID", "details": {"node_ids": duplicate_nodes[:50]}})
    if duplicate_edges:
        errors.append({"code": "GRAPH_DUPLICATE_EDGE_ID", "details": {"edge_ids": duplicate_edges[:50]}})
    invalid_kinds = sorted({node["kind"] for node in nodes if node["kind"] not in NODE_KINDS})
    if invalid_kinds:
        errors.append({"code": "GRAPH_NODE_KIND_INVALID", "details": {"kinds": invalid_kinds}})
    node_set = set(node_ids)
    bad_refs = [edge["id"] for edge in edges if edge["source"] not in node_set or edge["target"] not in node_set]
    if bad_refs:
        errors.append({"code": "GRAPH_EDGE_REFERENCE_INVALID", "details": {"edge_ids": bad_refs[:100]}})

    starts = [node["id"] for node in nodes if node["kind"] == "start"]
    ends = [node["id"] for node in nodes if node["kind"] == "end"]
    if len(starts) != 1:
        errors.append({"code": "GRAPH_START_COUNT_INVALID", "details": {"count": len(starts)}})
    if not ends:
        errors.append({"code": "GRAPH_END_MISSING", "details": {}})
    outgoing: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in node_set}
    incoming: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in node_set}
    for edge in edges:
        if edge["source"] in node_set and edge["target"] in node_set:
            outgoing[edge["source"]].append(edge)
            incoming[edge["target"]].append(edge)

    for node in nodes:
        if node["kind"] == "decision":
            branches = outgoing.get(node["id"], [])
            if len(branches) < 2:
                errors.append({"code": "GRAPH_DECISION_BRANCH_COUNT", "details": {"node_id": node["id"], "count": len(branches)}})
            for edge in branches:
                if not edge["branch_label"] or (edge.get("condition") in (None, "", {}) and not edge.get("default")):
                    errors.append({"code": "GRAPH_DECISION_BRANCH_CONTRACT", "details": {"node_id": node["id"], "edge_id": edge["id"]}})

    if len(starts) == 1:
        reachable: set[str] = set()
        queue: deque[str] = deque(starts)
        while queue:
            node_id = queue.popleft()
            if node_id in reachable:
                continue
            reachable.add(node_id)
            queue.extend(edge["target"] for edge in outgoing.get(node_id, []))
        unreachable = sorted(node_set - reachable)
        if unreachable:
            errors.append({"code": "GRAPH_UNREACHABLE_NODE", "details": {"node_ids": unreachable[:100]}})

    if ends:
        can_end: set[str] = set()
        queue = deque(ends)
        while queue:
            node_id = queue.popleft()
            if node_id in can_end:
                continue
            can_end.add(node_id)
            queue.extend(edge["source"] for edge in incoming.get(node_id, []))
        non_terminating = sorted(node_set - can_end)
        if non_terminating:
            errors.append({"code": "GRAPH_NON_TERMINATING_BRANCH", "details": {"node_ids": non_terminating[:100]}})

    colors: dict[str, int] = {node_id: 0 for node_id in node_set}
    cycle_edge_ids: set[str] = set()

    def visit(node_id: str) -> None:
        colors[node_id] = 1
        for edge in outgoing.get(node_id, []):
            target = edge["target"]
            if colors.get(target) == 1:
                cycle_edge_ids.add(edge["id"])
            elif colors.get(target) == 0:
                visit(target)
        colors[node_id] = 2

    for node_id in sorted(node_set):
        if colors[node_id] == 0:
            visit(node_id)
    cycle_edges = [edge for edge in edges if edge["id"] in cycle_edge_ids]
    if cycle_edges and not _has_bounded_loop_policy(raw_graph, cycle_edges):
        errors.append({"code": "GRAPH_UNBOUNDED_CYCLE", "details": {"edge_ids": sorted(cycle_edge_ids)}})

    normalized_graph = {"schema_version": "work-graph/v1", "nodes": nodes, "edges": edges, "loop_policy": copy.deepcopy(raw_graph.get("loop_policy"))}
    validation = {"valid": not errors, "errors": errors, "node_count": len(nodes), "edge_count": len(edges), "has_bounded_cycle": bool(cycle_edges) and not any(error["code"] == "GRAPH_UNBOUNDED_CYCLE" for error in errors)}
    updated = copy.deepcopy(work)
    updated["as_is_graph"] = normalized_graph
    if errors:
        updated["status"] = "NEEDS_CLARIFICATION"
        return {
            "ok": False,
            "status": "NEEDS_CLARIFICATION",
            "artifact_refs": [{"kind": "work_definition", "id": work["work_definition_id"], "revision": work_revision}],
            "work_definition": updated,
            "graph_validation": validation,
            "error": {"code": "WORK_GRAPH_VALIDATION_FAILED", "message": "AS-IS 업무 graph의 연결 또는 종료 계약이 유효하지 않습니다.", "retryable": False, "details": {"codes": sorted({item["code"] for item in errors})}},
            "resume": None,
            "trace_id": trace_id,
        }
    return {
        "ok": True,
        "status": str(updated.get("status") or "EXTRACTING"),
        "artifact_refs": [{"kind": "work_definition", "id": work["work_definition_id"], "revision": work_revision}],
        "work_definition": updated,
        "graph_validation": validation,
        "trace_id": trace_id,
    }


class WorkGraphNormalizerComponent(Component):
    display_name = "16 AS-IS 업무 Graph 정규화"
    description = "업무 단계 graph를 제한된 node/edge schema로 정규화하고 orphan, branch, 종료, cycle 계약을 검증합니다."
    icon = "Workflow"
    name = "WorkGraphNormalizer"

    inputs = [
        DataInput(name="work_definition", display_name="WorkDefinition", input_types=["Data", "JSON"], required=True),
        IntInput(name="max_nodes", display_name="최대 Node 수", value=500, advanced=True),
        IntInput(name="max_edges", display_name="최대 Edge 수", value=2000, advanced=True),
    ]
    outputs = [Output(name="normalized_graph", display_name="정규화 AS-IS Graph", method="build_graph", types=["Data"])]

    def build_graph(self) -> Data:
        result = normalize_work_graph(getattr(self, "work_definition", None), max_nodes=getattr(self, "max_nodes", 500), max_edges=getattr(self, "max_edges", 2000))
        self.status = {"ok": result["ok"], "status": result["status"], "node_count": result.get("graph_validation", {}).get("node_count", 0), "error_count": len(result.get("graph_validation", {}).get("errors", []))}
        return Data(data=result)
