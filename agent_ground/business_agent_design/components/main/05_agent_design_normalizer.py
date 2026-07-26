from __future__ import annotations

"""05 Normalize and validate an Agent design as two connected flow graphs.

This file is a Standalone Langflow component.  It does not import project files
or sibling nodes, and it always produces a usable deterministic fallback when
the LLM output is empty or malformed.
"""

import hashlib
import json
import re
from copy import deepcopy
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MessageTextInput, Output
from lfx.schema.data import Data


NODE_TYPES = {"start", "process", "decision", "merge", "human_review", "end"}
CHANGE_STATES = {"unchanged", "modified", "added", "human_review", "removed"}
HIGH_IMPACT_WORDS = ("발송", "승인", "반려", "등록", "수정", "삭제", "write", "메일", "전송")
DECISION_WORDS = ("판단", "확인", "검토", "이상", "정상", "조건", "승인", "여부", "분류")


def normalize_agent_design(catalog_context_value: Any, llm_design_response: Any = "") -> dict[str, Any]:
    payload = _payload(catalog_context_value)
    context = _dict(payload.get("catalog_context"))
    profile = _dict(context.get("business_profile")) or _dict(payload.get("workflow_profile"))
    items = [_dict(item) for item in _list(context.get("ranked_catalog_items")) if isinstance(item, dict)]
    trace = _dict(context.get("recommendation_trace"))
    catalog_by_id = {str(item.get("canonical_key") or ""): item for item in items if item.get("canonical_key")}

    parsed = _parse_json_like(llm_design_response)
    source_mode = "llm_normalized" if isinstance(parsed, dict) else "deterministic_fallback"
    raw_design = _dict(parsed)
    if not _has_graphs(raw_design):
        raw_design = _fallback_design(profile, items, trace)
        source_mode = "deterministic_fallback"

    design = _normalize_design(raw_design, profile, catalog_by_id, trace)
    issues = _validate_design(design)
    if issues:
        # The fallback is intentionally rebuilt and normalized rather than
        # allowing an invalid graph to reach the renderer.
        design = _normalize_design(_fallback_design(profile, items, trace), profile, catalog_by_id, trace)
        source_mode = "validation_fallback"
        issues = _validate_design(design)

    design["design_meta"] = {
        "source_mode": source_mode,
        "graph_contract": "agent_ground.business_flow_graph.v1",
        "node_types": sorted(NODE_TYPES),
        "change_states": sorted(CHANGE_STATES),
    }
    validation = {
        "passed": not issues,
        "issues": issues,
        "before_nodes": len(design["flow_visualization"]["before"]["nodes"]),
        "before_edges": len(design["flow_visualization"]["before"]["edges"]),
        "after_nodes": len(design["flow_visualization"]["after"]["nodes"]),
        "after_edges": len(design["flow_visualization"]["after"]["edges"]),
        "changed_nodes": sum(
            1
            for node in design["flow_visualization"]["after"]["nodes"]
            if node.get("change_state") in {"modified", "added", "human_review"}
        ),
    }
    return {
        **payload,
        "agent_design": design,
        "recommendation_trace": trace,
        "design_validation": validation,
    }


def _fallback_design(profile: dict[str, Any], items: list[dict[str, Any]], trace: dict[str, Any]) -> dict[str, Any]:
    goal = _clean(profile.get("business_goal")) or "반복 업무를 안전하게 개선합니다."
    raw_steps = [_dict(step) for step in _list(profile.get("current_flow")) if isinstance(step, dict)]
    if not raw_steps:
        raw_steps = [{"step_id": "S1", "title": "업무 내용 확인", "description": goal, "actor": "업무 담당자"}]

    catalog_ids = [str(item.get("canonical_key")) for item in items if item.get("canonical_key")][:4]
    trace_id = _clean(trace.get("trace_id"))
    before_nodes = [_node("before_start", "start", "업무 시작", "업무 담당자", "start", "unchanged")]
    after_nodes = [_node("after_start", "start", "업무 시작", "업무 담당자", "start", "unchanged")]
    decision_index = _decision_index(raw_steps)
    improvement_details = []

    for index, step in enumerate(raw_steps, 1):
        key = _safe_key(step.get("step_id") or f"step_{index}")
        title = _clean(step.get("title")) or f"현재 업무 {index}"
        description = _clean(step.get("description"))
        actor = _clean(step.get("actor")) or "업무 담당자"
        before_type = "decision" if index - 1 == decision_index else "process"
        before_nodes.append(
            _node(
                f"before_{key}", key, title, actor, before_type, "modified",
                description=description, detail_id=f"imp_{key}",
            )
        )

        high_impact = _has_word(f"{title} {description}", HIGH_IMPACT_WORDS)
        after_type = "human_review" if high_impact else before_type
        state = "human_review" if high_impact else "modified"
        after_title = f"사람 확인: {title}" if high_impact else f"Agent 지원: {title}"
        after_nodes.append(
            _node(
                f"after_{key}", key, after_title, "업무 담당자" if high_impact else "AI Agent",
                after_type, state, description=description,
                assets=catalog_ids[:2], detail_id=f"imp_{key}",
            )
        )
        improvement_details.append(
            {
                "improvement_detail_id": f"imp_{key}",
                "title": after_title,
                "why_change": "반복 확인과 수작업 전환을 줄이고 처리 근거를 남기기 위해 개선합니다.",
                "how_to_improve": (
                    "Agent가 필요한 정보를 준비하되 영향이 큰 실행은 담당자가 확인한 뒤 진행합니다."
                    if high_impact else
                    "업무 입력을 구조화하고 추천된 Flow/Component로 조회·정리 결과를 다음 단계에 전달합니다."
                ),
                "recommended_asset_ids": catalog_ids[:2],
                "connection_guidance": ["이전 단계의 Data 출력을 이 단계의 Data 입력에 연결", "정규화된 결과만 다음 판단 단계에 전달"],
                "acceptance_checks": ["동일 입력에서 단계 순서와 분기 조건이 유지되는지 확인", "실패 시 원인과 사람이 할 일을 표시하는지 확인"],
                "human_review": "외부 발송·승인·시스템 변경은 자동 실행하지 않고 담당자가 최종 확인합니다." if high_impact else "결과의 업무 타당성을 담당자가 표본 검토합니다.",
                "recommendation_trace_ids": [trace_id] if trace_id else [],
            }
        )

    result_key = "agent_result_review"
    after_nodes.append(
        _node(
            "after_result_review", result_key, "Agent 결과 검토", "업무 담당자", "human_review", "added",
            description="요약, 추천 근거, 예외 사항을 확인합니다.", assets=catalog_ids,
            detail_id="imp_agent_result_review",
        )
    )
    improvement_details.append(
        {
            "improvement_detail_id": "imp_agent_result_review",
            "title": "Agent 결과 검토",
            "why_change": "자동화 결과가 실제 업무 판단과 다른 경우를 차단합니다.",
            "how_to_improve": "Flow Chart와 추천 trace를 함께 보여 주고 사용자가 승인하거나 보완하도록 구성합니다.",
            "recommended_asset_ids": catalog_ids,
            "connection_guidance": ["최종 설계 Data를 HTML Renderer에 연결", "공유가 필요할 때만 Report API Publisher를 연결"],
            "acceptance_checks": ["모든 변경 node에 개선 설명 버튼이 있는지 확인", "외부 공유 전에 사람이 결과를 확인하는지 확인"],
            "human_review": "업무 담당자가 결과와 민감 정보 포함 여부를 확인합니다.",
            "recommendation_trace_ids": [trace_id] if trace_id else [],
        }
    )
    before_nodes.append(_node("before_end", "end", "업무 종료", "업무 담당자", "end", "unchanged"))
    after_nodes.append(_node("after_end", "end", "업무 종료", "업무 담당자", "end", "unchanged"))

    before_edges = _fallback_edges(before_nodes, decision_index + 1 if decision_index >= 0 else -1, "before")
    after_decision_position = decision_index + 1 if decision_index >= 0 else -1
    after_edges = _fallback_edges(after_nodes, after_decision_position, "after")

    recommendations = [_recommendation(item, raw_steps) for item in items[:4]]
    return {
        "report_title": "업무 AI Agent 개선 설계",
        "executive_summary": goal,
        "flow_visualization": {"before": {"nodes": before_nodes, "edges": before_edges}, "after": {"nodes": after_nodes, "edges": after_edges}},
        "improvement_details": improvement_details,
        "recommended_capabilities": recommendations,
        "implementation_roadmap": [
            {"phase": "1. 입력과 기준 확정", "goal": "업무 범위와 성공 기준 합의", "tasks": ["대표 입력 3건 준비", "사람 확인 지점 표시"], "success_check": "담당자가 BEFORE Flow를 승인"},
            {"phase": "2. 조회·판단 MVP", "goal": "읽기 전용 Agent Flow 검증", "tasks": ["추천 Component 연결", "분기별 결과 확인"], "success_check": "대표 입력이 올바른 분기로 이동"},
            {"phase": "3. 결과와 공유", "goal": "안전한 HTML 결과 제공", "tasks": ["Flow Chart 확인", "선택적 공유 링크 연결"], "success_check": "모바일·데스크톱에서 결과와 개선 설명 확인"},
        ],
        "risk_controls": [
            {"risk": "잘못된 자동 실행", "control": "기본값을 읽기·초안 생성으로 제한", "human_checkpoint": "외부 발송 또는 시스템 쓰기 직전"},
            {"risk": "추천 근거 누락", "control": "catalog ID와 trace ID를 결과에 유지", "human_checkpoint": "설계 승인 전"},
        ],
        "assumptions": _strings(profile.get("assumptions")),
        "questions_to_confirm": _strings(profile.get("open_questions")) or ["자동 실행이 허용되는 범위와 반드시 사람이 확인할 단계를 알려주세요."],
        "unverified_suggestions": [],
    }


def _fallback_edges(nodes: list[dict[str, Any]], decision_position: int, prefix: str) -> list[dict[str, Any]]:
    edges = []
    for index in range(len(nodes) - 1):
        source = nodes[index]
        target = nodes[index + 1]
        if index == decision_position:
            edges.append(_edge(f"{prefix}_edge_{index}_main", source["node_id"], target["node_id"], "조건 충족", "업무 조건이 충족됨", "modified"))
            edges.append(_edge(f"{prefix}_edge_{index}_skip", source["node_id"], nodes[-1]["node_id"], "조건 미충족", "업무 조건이 충족되지 않음", "modified"))
        else:
            edges.append(_edge(f"{prefix}_edge_{index}", source["node_id"], target["node_id"], "", "", "modified" if source.get("change_state") != "unchanged" else "unchanged"))
    return edges


def _normalize_design(raw: dict[str, Any], profile: dict[str, Any], catalog: dict[str, dict[str, Any]], trace: dict[str, Any]) -> dict[str, Any]:
    fallback = _fallback_design(profile, list(catalog.values()), trace)
    raw_visual = _dict(raw.get("flow_visualization"))
    before = _normalize_graph(_dict(raw_visual.get("before")), "before")
    after = _normalize_graph(_dict(raw_visual.get("after")), "after")
    if not before["nodes"] or not before["edges"]:
        before = fallback["flow_visualization"]["before"]
    if not after["nodes"] or not after["edges"]:
        after = fallback["flow_visualization"]["after"]

    allowed_ids = set(catalog)
    for graph in (before, after):
        for node in graph["nodes"]:
            node["recommended_asset_ids"] = [item for item in _strings(node.get("recommended_asset_ids")) if item in allowed_ids]

    details = _normalize_details(raw.get("improvement_details"), allowed_ids, trace)
    detail_by_id = {item["improvement_detail_id"]: item for item in details}
    trace_id = _clean(trace.get("trace_id"))
    for node in after["nodes"]:
        if node.get("change_state") not in {"modified", "added", "human_review"}:
            continue
        detail_id = _safe_key(node.get("improvement_detail_id") or f"imp_{node['node_id']}")
        node["improvement_detail_id"] = detail_id
        if detail_id not in detail_by_id:
            item = {
                "improvement_detail_id": detail_id,
                "title": node.get("label"),
                "why_change": "반복 작업과 오류 가능성을 줄이기 위한 변경입니다.",
                "how_to_improve": "검증된 입력·출력 계약으로 이 단계를 연결하고 실패 결과를 명시적으로 처리합니다.",
                "recommended_asset_ids": node.get("recommended_asset_ids", []),
                "connection_guidance": ["이전 node 출력을 이 node 입력에 연결", "검증 결과를 다음 node에 전달"],
                "acceptance_checks": ["정상·오류 입력을 각각 실행해 결과를 확인"],
                "human_review": "업무 담당자가 결과의 타당성을 확인합니다.",
                "recommendation_trace_ids": [trace_id] if trace_id else [],
            }
            details.append(item)
            detail_by_id[detail_id] = item

    recommendations = _normalize_recommendations(raw.get("recommended_capabilities"), catalog)
    if not recommendations:
        recommendations = fallback["recommended_capabilities"]

    design = {
        "report_title": _clean(raw.get("report_title")) or fallback["report_title"],
        "executive_summary": _clean(raw.get("executive_summary")) or _clean(profile.get("business_goal")) or fallback["executive_summary"],
        "flow_visualization": {
            "before": before,
            "after": after,
            "change_map": _change_map(before, after),
            "legend": [
                {"state": "unchanged", "label": "기존과 동일"}, {"state": "modified", "label": "변경"},
                {"state": "added", "label": "신규"}, {"state": "human_review", "label": "사람 검토"},
                {"state": "removed", "label": "제거"},
            ],
        },
        "improvement_details": details,
        "recommended_capabilities": recommendations,
        "implementation_roadmap": _normalize_roadmap(raw.get("implementation_roadmap")) or fallback["implementation_roadmap"],
        "risk_controls": _normalize_risks(raw.get("risk_controls")) or fallback["risk_controls"],
        "assumptions": _strings(raw.get("assumptions")) or fallback["assumptions"],
        "questions_to_confirm": _strings(raw.get("questions_to_confirm")) or fallback["questions_to_confirm"],
        "unverified_suggestions": _strings(raw.get("unverified_suggestions")),
    }
    return design


def _normalize_graph(raw_graph: dict[str, Any], prefix: str) -> dict[str, list[dict[str, Any]]]:
    nodes = []
    seen = set()
    id_map = {}
    for index, raw_node in enumerate(_list(raw_graph.get("nodes")), 1):
        item = _dict(raw_node)
        old_id = _clean(item.get("node_id")) or f"node_{index}"
        node_id = _unique_id(f"{prefix}_{_safe_key(old_id.removeprefix(prefix + '_'))}", seen)
        id_map[old_id] = node_id
        node_type = _clean(item.get("node_type"))
        state = _clean(item.get("change_state"))
        nodes.append(
            _node(
                node_id,
                _safe_key(item.get("comparison_key") or old_id),
                _clean(item.get("label")) or f"업무 단계 {index}",
                _clean(item.get("actor")) or "업무 담당자",
                node_type if node_type in NODE_TYPES else "process",
                state if state in CHANGE_STATES else ("modified" if prefix == "after" else "unchanged"),
                description=_clean(item.get("description")),
                assets=_strings(item.get("recommended_asset_ids")),
                detail_id=_clean(item.get("improvement_detail_id")),
            )
        )
    if not nodes:
        return {"nodes": [], "edges": []}

    if not any(node["node_type"] == "start" for node in nodes):
        nodes.insert(0, _node(f"{prefix}_start", "start", "업무 시작", "업무 담당자", "start", "unchanged"))
    if not any(node["node_type"] == "end" for node in nodes):
        nodes.append(_node(f"{prefix}_end", "end", "업무 종료", "업무 담당자", "end", "unchanged"))

    valid_ids = {node["node_id"] for node in nodes}
    edges = []
    used_edges = set()
    for index, raw_edge in enumerate(_list(raw_graph.get("edges")), 1):
        item = _dict(raw_edge)
        source = id_map.get(_clean(item.get("source")), _clean(item.get("source")))
        target = id_map.get(_clean(item.get("target")), _clean(item.get("target")))
        if source not in valid_ids or target not in valid_ids or source == target:
            continue
        pair = (source, target)
        if pair in used_edges or _would_cycle(edges, source, target):
            continue
        used_edges.add(pair)
        state = _clean(item.get("change_state"))
        edges.append(_edge(_clean(item.get("edge_id")) or f"{prefix}_edge_{index}", source, target, _clean(item.get("branch_label")), _clean(item.get("condition")), state if state in CHANGE_STATES else "unchanged"))

    # Connect isolated nodes in their declared order. This also repairs the
    # common LLM error where start/end nodes are present but not wired.
    for index, node in enumerate(nodes):
        node_id = node["node_id"]
        incoming = {edge["target"] for edge in edges}
        outgoing = {edge["source"] for edge in edges}
        pairs = {(edge["source"], edge["target"]) for edge in edges}
        if node["node_type"] != "start" and node_id not in incoming:
            previous = nodes[index - 1]["node_id"] if index else nodes[0]["node_id"]
            if previous != node_id and (previous, node_id) not in pairs and not _would_cycle(edges, previous, node_id):
                edges.append(_edge(f"{prefix}_repair_in_{index}", previous, node_id))
        outgoing = {edge["source"] for edge in edges}
        pairs = {(edge["source"], edge["target"]) for edge in edges}
        if node["node_type"] != "end" and node_id not in outgoing and index + 1 < len(nodes):
            following = nodes[index + 1]["node_id"]
            if (node_id, following) not in pairs and not _would_cycle(edges, node_id, following):
                edges.append(_edge(f"{prefix}_repair_out_{index}", node_id, following))

    by_id = {node["node_id"]: node for node in nodes}
    for node in nodes:
        if node["node_type"] != "decision":
            continue
        outgoing_edges = [edge for edge in edges if edge["source"] == node["node_id"]]
        if len(outgoing_edges) < 2:
            end_id = next(item["node_id"] for item in reversed(nodes) if item["node_type"] == "end")
            if not any(edge["target"] == end_id for edge in outgoing_edges):
                edges.append(_edge(f"{prefix}_{node['node_id']}_else", node["node_id"], end_id, "조건 미충족", "판단 조건이 충족되지 않음", "modified"))
            outgoing_edges = [edge for edge in edges if edge["source"] == node["node_id"]]
        for index, edge in enumerate(outgoing_edges, 1):
            edge["branch_label"] = edge["branch_label"] or ("조건 충족" if index == 1 else f"대안 {index}")
            edge["condition"] = edge["condition"] or f"{node['label']} 분기 조건 {index}"
            if edge["target"] in by_id and edge["change_state"] == "unchanged" and prefix == "after":
                edge["change_state"] = by_id[edge["target"]].get("change_state", "modified")
    return {"nodes": nodes, "edges": edges}


def _normalize_details(value: Any, allowed_ids: set[str], trace: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for index, raw in enumerate(_list(value), 1):
        item = _dict(raw)
        detail_id = _unique_id(_safe_key(item.get("improvement_detail_id") or f"improvement_{index}"), seen)
        result.append({
            "improvement_detail_id": detail_id,
            "title": _clean(item.get("title")) or f"개선 항목 {index}",
            "why_change": _clean(item.get("why_change")) or "반복 작업과 오류 가능성을 줄이기 위한 변경입니다.",
            "how_to_improve": _clean(item.get("how_to_improve")) or "검증된 Flow/Component의 입출력 계약으로 연결합니다.",
            "recommended_asset_ids": [asset for asset in _strings(item.get("recommended_asset_ids")) if asset in allowed_ids],
            "connection_guidance": _strings(item.get("connection_guidance")),
            "acceptance_checks": _strings(item.get("acceptance_checks")),
            "human_review": _clean(item.get("human_review")) or "업무 담당자가 결과를 확인합니다.",
            "recommendation_trace_ids": _strings(item.get("recommendation_trace_ids")) or ([_clean(trace.get("trace_id"))] if trace.get("trace_id") else []),
        })
    return result


def _normalize_recommendations(value: Any, catalog: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    requested = []
    for raw in _list(value):
        item = _dict(raw)
        catalog_id = _clean(item.get("catalog_id") or item.get("asset_id"))
        if catalog_id in catalog and catalog_id not in requested:
            requested.append(catalog_id)
    return [_recommendation(catalog[catalog_id], []) for catalog_id in requested[:8]]


def _recommendation(item: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
    catalog_id = _clean(item.get("canonical_key"))
    return {
        "catalog_id": catalog_id,
        "title": _clean(item.get("title_ko")) or catalog_id,
        "why_recommended": _clean(item.get("summary_ko")) or "업무 단계 구현에 사용할 수 있는 카탈로그 항목입니다.",
        "applies_to_steps": [_safe_key(step.get("step_id")) for step in steps[:4] if step.get("step_id")],
        "building_blocks": _strings(item.get("langflow_building_blocks")),
        "source_links": _strings(item.get("source_links")),
        "risk_level": _clean(item.get("risk_level")) or "medium",
        "human_review_required": bool(item.get("human_review_required")),
    }


def _change_map(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    before_by_key = {node["comparison_key"]: node for node in before["nodes"]}
    after_by_key = {node["comparison_key"]: node for node in after["nodes"]}
    result = []
    for key in dict.fromkeys([*before_by_key, *after_by_key]):
        old = before_by_key.get(key)
        new = after_by_key.get(key)
        state = (new or old or {}).get("change_state", "unchanged")
        if old and not new:
            state = "removed"
        elif new and not old and state == "unchanged":
            state = "added"
        result.append({
            "comparison_key": key,
            "before_node_id": old.get("node_id", "") if old else "",
            "after_node_id": new.get("node_id", "") if new else "",
            "change_state": state,
            "summary": f"{(new or old or {}).get('label', key)}: {state}",
        })
    return result


def _validate_design(design: dict[str, Any]) -> list[str]:
    issues = []
    visual = _dict(design.get("flow_visualization"))
    detail_ids = {item.get("improvement_detail_id") for item in _list(design.get("improvement_details")) if isinstance(item, dict)}
    for graph_name in ("before", "after"):
        graph = _dict(visual.get(graph_name))
        nodes = [_dict(item) for item in _list(graph.get("nodes"))]
        edges = [_dict(item) for item in _list(graph.get("edges"))]
        ids = [node.get("node_id") for node in nodes]
        if len(ids) != len(set(ids)):
            issues.append(f"{graph_name}: duplicate node_id")
        if not any(node.get("node_type") == "start" for node in nodes):
            issues.append(f"{graph_name}: start node missing")
        if not any(node.get("node_type") == "end" for node in nodes):
            issues.append(f"{graph_name}: end node missing")
        id_set = set(ids)
        for edge in edges:
            if edge.get("source") not in id_set or edge.get("target") not in id_set:
                issues.append(f"{graph_name}: dangling edge")
        for node in nodes:
            if node.get("node_type") == "decision":
                outgoing = [edge for edge in edges if edge.get("source") == node.get("node_id")]
                if len(outgoing) < 2 or any(not edge.get("branch_label") or not edge.get("condition") for edge in outgoing):
                    issues.append(f"{graph_name}: invalid decision branches")
            if graph_name == "after" and node.get("change_state") in {"modified", "added", "human_review"}:
                if node.get("improvement_detail_id") not in detail_ids:
                    issues.append(f"after: improvement detail missing for {node.get('node_id')}")
    return sorted(set(issues))


def _normalize_roadmap(value: Any) -> list[dict[str, Any]]:
    return [{"phase": _clean(item.get("phase")), "goal": _clean(item.get("goal")), "tasks": _strings(item.get("tasks")), "success_check": _clean(item.get("success_check"))} for item in map(_dict, _list(value)) if item]


def _normalize_risks(value: Any) -> list[dict[str, Any]]:
    return [{"risk": _clean(item.get("risk")), "control": _clean(item.get("control")), "human_checkpoint": _clean(item.get("human_checkpoint"))} for item in map(_dict, _list(value)) if item]


def _node(node_id: str, comparison_key: str, label: str, actor: str, node_type: str, state: str, description: str = "", assets: list[str] | None = None, detail_id: str = "") -> dict[str, Any]:
    return {"node_id": node_id, "comparison_key": comparison_key, "label": label, "description": description, "actor": actor, "node_type": node_type, "change_state": state, "recommended_asset_ids": assets or [], "improvement_detail_id": detail_id}


def _edge(edge_id: str, source: str, target: str, label: str = "", condition: str = "", state: str = "unchanged") -> dict[str, Any]:
    return {"edge_id": _safe_key(edge_id), "source": source, "target": target, "branch_label": label, "condition": condition, "change_state": state}


def _decision_index(steps: list[dict[str, Any]]) -> int:
    for index, step in enumerate(steps):
        if _has_word(f"{step.get('title', '')} {step.get('description', '')}", DECISION_WORDS):
            return index
    return -1


def _would_cycle(edges: list[dict[str, Any]], source: str, target: str) -> bool:
    graph: dict[str, list[str]] = {}
    for edge in edges:
        graph.setdefault(str(edge.get("source")), []).append(str(edge.get("target")))
    graph.setdefault(source, []).append(target)
    stack = [target]
    seen = set()
    while stack:
        current = stack.pop()
        if current == source:
            return True
        if current in seen:
            continue
        seen.add(current)
        stack.extend(graph.get(current, []))
    return False


def _has_graphs(value: dict[str, Any]) -> bool:
    visual = _dict(value.get("flow_visualization"))
    return bool(_dict(visual.get("before")).get("nodes") and _dict(visual.get("after")).get("nodes"))


def _parse_json_like(value: Any) -> Any:
    if isinstance(value, dict):
        return deepcopy(value)
    text = _extract_text(value).strip()
    if not text:
        return None
    if "```" in text:
        for block in re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.I | re.S):
            parsed = _parse_json_like(block)
            if parsed is not None:
                return parsed
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            return None
    return None


def _payload(value: Any) -> dict[str, Any]:
    data = getattr(value, "data", value)
    return deepcopy(data) if isinstance(data, dict) else {}


def _extract_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if hasattr(value, "text"):
        return str(value.text)
    if hasattr(value, "data"):
        return _extract_text(value.data)
    if isinstance(value, dict):
        for key in ("text", "message", "content", "input_value"):
            if key in value:
                return _extract_text(value[key])
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _dict(value: Any) -> dict[str, Any]:
    return deepcopy(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _strings(value: Any, limit: int = 20) -> list[str]:
    values = value if isinstance(value, list) else ([] if value in (None, "", {}) else [value])
    result = []
    for item in values:
        text = _clean(item)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _safe_key(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9가-힣_-]+", "_", _clean(value)).strip("_").lower()
    if text:
        return text[:72]
    return "id_" + hashlib.sha1(_clean(value).encode("utf-8")).hexdigest()[:10]


def _unique_id(value: str, seen: set[str]) -> str:
    candidate = value
    counter = 2
    while candidate in seen:
        candidate = f"{value}_{counter}"
        counter += 1
    seen.add(candidate)
    return candidate


def _has_word(text: str, words: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(word.lower() in lowered for word in words)


class AgentDesignNormalizer(Component):
    display_name = "05 AI Agent 설계 결과 검증"
    description = "LLM 설계를 연결된 BEFORE/AFTER graph로 정규화하고 분기·변경 설명·카탈로그 ID를 검증합니다."
    icon = "GitCompareArrows"
    inputs = [
        DataInput(name="catalog_context", display_name="추천 컨텍스트", required=True),
        MessageTextInput(name="llm_design_response", display_name="Agent/LLM 설계 응답", required=False),
    ]
    outputs = [Output(name="agent_design", display_name="AI Agent 설계 결과", method="build_payload")]

    def build_payload(self) -> Data:
        result = normalize_agent_design(getattr(self, "catalog_context", None), getattr(self, "llm_design_response", ""))
        validation = _dict(result.get("design_validation"))
        self.status = {
            "검증": "통과" if validation.get("passed") else "확인 필요",
            "BEFORE": f"{validation.get('before_nodes', 0)} nodes / {validation.get('before_edges', 0)} edges",
            "AFTER": f"{validation.get('after_nodes', 0)} nodes / {validation.get('after_edges', 0)} edges",
            "변경 node": validation.get("changed_nodes", 0),
        }
        return Data(data=result)
