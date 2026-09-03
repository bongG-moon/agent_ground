"""Regression tests for post-model design draft size and cardinality bounds."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "components" / "single_flow" / "05_business_design_result_normalizer.py"


def _module():
    name = "single_flow_normalizer_bounds_test"
    spec = importlib.util.spec_from_file_location(name, COMPONENT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_work_analysis_clips_oversized_lists_and_projects_branch_exception_fields():
    module = _module()
    warnings: list[str] = []
    long_text = "가" * (module._MAX_COLLECTION_ITEM_CHARS + 100)
    work = module._normalize_work_analysis(
        {
            "goal": "목표" * (module._MAX_NARRATIVE_CHARS + 100),
            "scope_in": [long_text] * (module._MAX_COLLECTION_ITEMS + 5),
            "current_steps": [
                {
                    "step_ref": f"step-{index}",
                    "title": f"단계 {index}",
                    "description": "설명" * (module._MAX_DETAIL_CHARS + 10),
                }
                for index in range(module._MAX_CURRENT_STEPS + 4)
            ],
            "current_branches": [
                {
                    "source_step_ref": "step-1",
                    "condition": "조건" * (module._MAX_DETAIL_CHARS + 10),
                    "target_step_ref": "step-2",
                    "is_default": index == 0,
                    "unbounded_extra": {"nested": ["x" * 10_000]},
                }
                for index in range(module._MAX_CURRENT_BRANCHES + 3)
            ],
            "current_exceptions": [
                {
                    "source_step_ref": "step-1",
                    "condition": "예외" * (module._MAX_DETAIL_CHARS + 10),
                    "handling": "처리" * (module._MAX_DETAIL_CHARS + 10),
                    "target_step_ref": "step-2",
                    "unbounded_extra": {"nested": ["x" * 10_000]},
                }
                for _ in range(module._MAX_CURRENT_EXCEPTIONS + 3)
            ],
        },
        warnings,
    )

    assert len(work["goal"]) == module._MAX_NARRATIVE_CHARS
    assert len(work["scope_in"]) == module._MAX_COLLECTION_ITEMS
    assert len(work["scope_in"][0]) == module._MAX_COLLECTION_ITEM_CHARS
    assert len(work["current_steps"]) == module._MAX_CURRENT_STEPS
    assert len(work["current_steps"][0]["description"]) == module._MAX_DETAIL_CHARS
    assert len(work["current_branches"]) == module._MAX_CURRENT_BRANCHES
    assert set(work["current_branches"][0]) == {
        "source_step_ref",
        "condition",
        "target_step_ref",
        "is_default",
    }
    assert len(work["current_exceptions"]) == module._MAX_CURRENT_EXCEPTIONS
    assert set(work["current_exceptions"][0]) == {
        "source_step_ref",
        "condition",
        "target_step_ref",
        "handling",
    }
    assert {
        "CURRENT_STEPS_TRUNCATED",
        "CURRENT_BRANCHES_TRUNCATED",
        "CURRENT_EXCEPTIONS_TRUNCATED",
    }.issubset(warnings)


def test_graph_and_tobe_collections_are_bounded_before_projection():
    module = _module()
    warnings: list[str] = []
    gaps: list[dict[str, str]] = []
    nodes = [
        {
            "node_id": f"node-{index}",
            "title": f"업무 단계 {index}",
            "summary": "상세 설명" * (module._MAX_DETAIL_CHARS + 10),
        }
        for index in range(module._MAX_GRAPH_NODES + 6)
    ]
    edges = [
        {
            "source_node_id": f"node-{index % module._MAX_GRAPH_NODES}",
            "target_node_id": f"node-{(index + 1) % module._MAX_GRAPH_NODES}",
            "label": f"연결 {index}",
        }
        for index in range(module._MAX_GRAPH_EDGES + 6)
    ]
    graph, _ = module._graph_from_raw(
        {"nodes": nodes, "edges": edges},
        prefix="to-be",
        fallback_steps=[],
        warnings=warnings,
        add_gap=gaps,
    )
    tobe, _, _ = module._normalize_tobe(
        {
            "summary": "요약" * (module._MAX_NARRATIVE_CHARS + 10),
            "nodes": nodes,
            "edges": edges,
            "implementation_roadmap": [
                {"phase": str(index), "title": "도입", "actions": ["작업"]}
                for index in range(module._MAX_IMPLEMENTATION_ROADMAP_ITEMS + 3)
            ],
            "risks_and_controls": [
                {"risk_id": f"risk-{index}", "risk": "위험", "impact": "영향", "control": "통제"}
                for index in range(module._MAX_RISKS_AND_CONTROLS + 3)
            ],
            "test_scenarios": [
                {"test_id": f"test-{index}", "title": "검증", "given": "조건", "when": "실행", "then": "결과"}
                for index in range(module._MAX_TEST_SCENARIOS + 3)
            ],
        },
        warnings,
        gaps,
    )

    # Missing start/end may add two deterministic terminals, but the LLM-owned
    # node collection itself stays capped.
    assert len(graph["nodes"]) <= module._MAX_GRAPH_NODES + 2
    assert len(graph["edges"]) <= module._MAX_GRAPH_EDGES
    assert max(len(node["summary"]) for node in graph["nodes"]) <= module._MAX_DETAIL_CHARS
    assert len(tobe["summary"]) == module._MAX_NARRATIVE_CHARS
    assert len(tobe["implementation_roadmap"]) == module._MAX_IMPLEMENTATION_ROADMAP_ITEMS
    assert len(tobe["risks_and_controls"]) == module._MAX_RISKS_AND_CONTROLS
    assert len(tobe["test_scenarios"]) == module._MAX_TEST_SCENARIOS
    assert {
        "TO_BE_GRAPH_NODES_TRUNCATED",
        "TO_BE_GRAPH_EDGES_TRUNCATED",
        "IMPLEMENTATION_ROADMAP_TRUNCATED",
        "RISKS_AND_CONTROLS_TRUNCATED",
        "TEST_SCENARIOS_TRUNCATED",
    }.issubset(warnings)


def test_normalizer_preserves_a_32_node_48_edge_high_level_process_graph():
    """The concise 04 model target must not be clipped by 05 safety caps.

    This represents a complex business process at the *business-stage* level:
    one normal path plus extra branch/error routes.  It deliberately remains
    below the independent 60-node/120-edge malformed-payload boundary.
    """

    module = _module()
    warnings: list[str] = []
    gaps: list[dict[str, str]] = []
    nodes = [
        {
            "node_id": "start",
            "node_kind": "start",
            "title": "업무 시작",
            "summary": "시작 조건 확인",
        },
        *[
            {
                "node_id": f"stage-{index:02d}",
                "node_kind": "decision" if index in {6, 14, 22} else "work_step",
                "title": f"업무 단계 {index}",
                "summary": "필요한 입력을 검증하고 다음 업무 데이터로 전달",
                "inputs": ["업무 데이터"],
                "outputs": ["검증된 업무 데이터"],
            }
            for index in range(1, 31)
        ],
        {
            "node_id": "end",
            "node_kind": "end",
            "title": "업무 종료",
            "summary": "결과를 전달하고 종료",
        },
    ]
    ordered_ids = [item["node_id"] for item in nodes]
    edges = [
        {
            "edge_id": f"normal-{index}",
            "source_node_id": source,
            "target_node_id": target,
            "edge_kind": "control",
            "label": "다음 단계",
        }
        for index, (source, target) in enumerate(zip(ordered_ids, ordered_ids[1:]), start=1)
    ]
    # Add 17 visible non-linear routes without making any normal node orphaned.
    edges.extend(
        {
            "edge_id": f"branch-{index}",
            "source_node_id": f"stage-{index:02d}",
            "target_node_id": "end",
            "edge_kind": "branch" if index % 2 else "error",
            "label": f"분기 또는 예외 {index}",
            "condition": "업무 조건 또는 오류 발생",
        }
        for index in range(1, 18)
    )

    graph, _ = module._graph_from_raw(
        {"nodes": nodes, "edges": edges},
        prefix="to-be",
        fallback_steps=[],
        warnings=warnings,
        add_gap=gaps,
    )

    assert len(nodes) == 32
    assert len(edges) == 48
    assert len(graph["nodes"]) == 32
    assert len(graph["edges"]) == 48
    assert not any("TRUNCATED" in warning for warning in warnings)


def test_oversized_post_parse_draft_is_rejected_without_echoing_contents():
    module = _module()
    secret_like_payload = "business-sensitive-text-should-not-be-echoed"
    draft = {"payload": secret_like_payload + "x" * module._MAX_DRAFT_RESPONSE_BYTES}

    with pytest.raises(ValueError, match=r"\[DESIGN_RESULT_TOO_LARGE\]") as raised:
        module._assert_draft_response_size(draft)

    assert secret_like_payload not in str(raised.value)
    assert str(module._MAX_DRAFT_RESPONSE_BYTES) in str(raised.value)
