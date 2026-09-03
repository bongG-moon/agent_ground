"""Contracts for the LLM-owned, bounded catalog-shortlist phase.

The retrieval ranker intentionally remains deterministic: it produces the
candidate pool.  These tests cover the separate policy that lets the first LLM
shortlist at most a visible, user-configured number of assets.  The second LLM
then decides whether each shortlisted asset is actually selected, merely
considered, or not used; only the candidate *scope* is fixed.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from lfx.schema import Data, Message


ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ROOT / "components" / "single_flow"


def _module(filename: str):
    module_name = "single_flow_catalog_selection_" + filename.replace(".py", "").replace("-", "_")
    path = COMPONENTS / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _request() -> dict:
    return {
        "schema_version": "business-design-request/v2",
        "request_sha256": "sha256:" + "1" * 64,
        "description_original_sha256": "sha256:" + "2" * 64,
        "description_for_model": "매주 메일과 JIRA를 수집해 검토 가능한 주간 보고서를 만들고 승인 후 게시합니다.",
        "description_display_redacted": "매주 메일과 JIRA를 수집해 검토 가능한 주간 보고서를 만들고 승인 후 게시합니다.",
        "additional_instructions": "기존 카탈로그는 실제 적용할 가치가 있는 경우에만 사용합니다.",
        "final_refinement_instructions": "분기와 예외 처리를 더 구체화해 주세요.",
        "redactions": [],
        "redaction_count": 0,
        "warnings": [],
    }


def _candidate(number: int) -> dict:
    return {
        "rank": number,
        "asset_id": f"0000000{number}-0000-4000-8000-000000000000",
        "version": f"v{number}",
        "asset_type": "flow" if number % 2 else "component",
        "title": f"카탈로그 후보 {number}",
        "category": "업무 자동화",
        "description": f"업무 단계 {number}에 사용하는 후보입니다.",
        "capabilities": [f"기능 {number}"],
        "matched_terms": ["메일", "JIRA"],
        "matched_fields": ["description"],
        "match_level": "strong",
        "score": float(100 - number),
        "technical_contract_status": "metadata_only",
        "retrieval_reason": "키워드와 업무 설명이 일치합니다.",
    }


def _retrieval(count: int = 4) -> dict:
    candidates = [_candidate(number) for number in range(1, count + 1)]
    return {
        "schema_version": "local-catalog-retrieval/v1",
        "request_sha256": _request()["request_sha256"],
        "candidate_set_sha256": "sha256:" + "3" * 64,
        "catalog_file_sha256": "sha256:" + "4" * 64,
        "top_n_requested": count,
        "top_n_returned": count,
        "ranking_algorithm": "local-lexical-rrf/v1",
        "candidates": candidates,
        "expanded_detail_count_requested": min(count, 4),
        "expanded_detail_count_returned": min(count, 4),
        "expanded_candidate_details": [dict(candidate) for candidate in candidates[:4]],
    }


def _draft(decisions: list[dict]) -> dict:
    nodes = [
        {
            "node_id": f"step-{number}",
            "node_kind": "work_step",
            "title": f"개선 단계 {number}",
            "summary": f"개선 작업 {number}",
            "implementation_source": "builtin",
        }
        for number in range(1, 5)
    ]
    return {
        "schema_version": "business-design-draft/v1",
        "work_analysis": {"title": "주간 업무 보고", "current_steps": []},
        "information_gaps": [],
        "as_is_graph": {"nodes": [], "edges": []},
        "to_be_design": {"summary": "주간 보고를 개선합니다.", "nodes": nodes, "edges": []},
        "catalog_decisions": decisions,
    }


def _decision(number: int, decision: str, *, target: int | None = None) -> dict:
    candidate = _candidate(number)
    return {
        "asset_id": candidate["asset_id"],
        "version": candidate["version"],
        "decision": decision,
        "target_node_ids": [f"step-{target}"] if target is not None else [],
        "reason": f"후보 {number} 선택 근거",
        "required_verification": [f"후보 {number} 연결 계약 확인"],
    }


def _normalized_result(model_response: dict, *, fixed_catalog_shortlist=None) -> dict:
    module = _module("05_business_design_result_normalizer.py")
    component = module.BusinessDesignResultNormalizerComponent()
    component.request = Data(data=_request())
    component.retrieval_result = Data(data=_retrieval())
    component.model_response = Data(data=model_response)
    if fixed_catalog_shortlist is not None:
        component.fixed_catalog_shortlist = Data(data=fixed_catalog_shortlist)
    return component.normalize_design().data


def _identities(application: dict, bucket: str) -> list[tuple[str, str]]:
    return [(item["asset_id"], item["version"]) for item in application[bucket]]


def test_first_prompt_exposes_visible_shortlist_limit_and_emits_a_machine_readable_policy():
    module = _module("03_business_design_prompt_builder.py")
    inputs = {item.name: item for item in module.BusinessDesignPromptBuilderComponent.inputs}

    shortlist_input = inputs["max_shortlisted_catalog_items"]
    assert shortlist_input.value == 12
    assert shortlist_input.advanced is False
    assert shortlist_input.show is not False

    component = module.BusinessDesignPromptBuilderComponent()
    component.request = Data(data=_request())
    component.retrieval_result = Data(data=_retrieval())
    component.max_prompt_chars = 64_000
    component.max_estimated_tokens = 20_000
    component.max_shortlisted_catalog_items = 2

    prompt = component.build_prompt()

    assert prompt.data["catalog_shortlist_policy"]["max_shortlisted_catalog_items"] == 2
    assert prompt.data["catalog_shortlist_policy"]["selection_scope"] == "shortlist_only"
    assert "<catalog_shortlist_policy>" in prompt.text
    assert "최대 2개" in prompt.text
    assert "catalog_decisions" in prompt.text


@pytest.mark.parametrize("limit", [0, 31])
def test_first_prompt_rejects_an_unsafe_catalog_shortlist_limit(limit: int):
    module = _module("03_business_design_prompt_builder.py")
    component = module.BusinessDesignPromptBuilderComponent()
    component.request = Data(data=_request())
    component.retrieval_result = Data(data=_retrieval())
    component.max_prompt_chars = 64_000
    component.max_estimated_tokens = 20_000
    component.max_shortlisted_catalog_items = limit

    with pytest.raises(ValueError, match="CATALOG_SHORTLIST_LIMIT_INVALID"):
        component.build_prompt()


def test_first_structured_output_preserves_the_shortlist_policy_from_03_for_the_normalizer():
    module = _module("04_business_design_structured_output.py")

    class Runnable:
        def invoke(self, messages, *, config):
            return module.BusinessDesignDraftV1(**_draft([]))

    class Model:
        def with_structured_output(self, schema):
            assert schema is module.BusinessDesignDraftV1
            return Runnable()

    component = module.BusinessDesignStructuredOutputComponent()
    component.model = Model()
    component.input_value = Message(
        text="<response_contract>JSON 하나만 반환</response_contract>",
        data={"catalog_shortlist_policy": {"max_shortlisted_catalog_items": 2}},
    )

    result = component.build_structured_output().data

    assert result["catalog_shortlist_policy"]["max_shortlisted_catalog_items"] == 2
    assert result["catalog_shortlist_policy"]["selection_scope"] == "shortlist_only"
    assert result["schema_version"] == "business-design-draft/v1"


def test_first_normalizer_caps_llm_shortlist_in_llm_priority_order_and_keeps_a_complete_partition():
    # The order here is the LLM's explicit recommendation priority, not the
    # retrieval rank.  The cap must retain the first two valid selections in
    # this order; a local ranker must not silently replace that judgment.
    response = _draft(
        [
            _decision(4, "selected", target=4),
            _decision(2, "selected", target=2),
            _decision(1, "selected", target=1),
            _decision(3, "selected", target=3),
        ]
    )
    response["catalog_shortlist_policy"] = {"max_shortlisted_catalog_items": 2}

    result = _normalized_result(response)
    application = result["catalog_application"]

    assert set(_identities(application, "selected")) == {
        (_candidate(4)["asset_id"], "v4"),
        (_candidate(2)["asset_id"], "v2"),
    }
    assert {(_candidate(1)["asset_id"], "v1"), (_candidate(3)["asset_id"], "v3")} <= set(
        _identities(application, "considered")
    )
    all_identities = [
        identity
        for bucket in ("selected", "considered", "not_used")
        for identity in _identities(application, bucket)
    ]
    assert len(all_identities) == 4
    assert len(set(all_identities)) == 4
    assert "CATALOG_SHORTLIST_LIMIT_APPLIED" in result["warnings"]
    assert result["trace"]["catalog_shortlist_policy"]["max_shortlisted_catalog_items"] == 2


def test_final_normalizer_locks_only_first_pass_shortlist_and_allows_every_shortlisted_asset_to_be_not_used():
    first_response = _draft(
        [
            _decision(1, "selected", target=1),
            _decision(2, "selected", target=2),
            _decision(3, "not_used"),
            _decision(4, "not_used"),
        ]
    )
    first_response["catalog_shortlist_policy"] = {"max_shortlisted_catalog_items": 2}
    shortlist = _normalized_result(first_response)
    assert _identities(shortlist["catalog_application"], "selected") == [
        (_candidate(1)["asset_id"], "v1"),
        (_candidate(2)["asset_id"], "v2"),
    ]

    # The final LLM is free to decide that the sole shortlisted candidate is
    # not useful after it sees the refined process.  It also tries to apply
    # candidates 3 and 4, which were outside the first LLM's shortlist.
    refinement_response = _draft(
        [
            _decision(1, "not_used"),
            _decision(2, "not_used"),
            _decision(3, "considered"),
            _decision(4, "selected", target=4),
        ]
    )
    # The policy is intentionally ignored on the second pass: the fixed first
    # result owns the candidate scope and prevents an LLM from expanding it.
    refinement_response["catalog_shortlist_policy"] = {"max_shortlisted_catalog_items": 30}
    refined = _normalized_result(refinement_response, fixed_catalog_shortlist=shortlist)
    application = refined["catalog_application"]

    # No candidate is forced into selected merely because it was on the first
    # shortlist.  Candidate 1 may be explicitly not_used by the final LLM.
    assert _identities(application, "selected") == []
    assert (_candidate(1)["asset_id"], "v1") in _identities(application, "not_used")

    # Both first-pass shortlisted candidates can remain unused. Candidates 3/4
    # are outside that shortlist and are ignored/downgraded even when the
    # final LLM attempts to use them.
    assert (_candidate(2)["asset_id"], "v2") in _identities(application, "not_used")
    assert (_candidate(3)["asset_id"], "v3") in _identities(application, "not_used")
    assert (_candidate(4)["asset_id"], "v4") in _identities(application, "not_used")
    assert "CATALOG_CANDIDATE_SHORTLIST_PRESERVED" in refined["warnings"]
    assert "CATALOG_DECISION_OUTSIDE_SHORTLIST" in refined["warnings"]
    assert application["selection_policy"]["max_shortlisted_catalog_items"] == 2
