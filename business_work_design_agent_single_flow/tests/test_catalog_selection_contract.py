"""Contracts for the fixed LLM catalog-shortlist boundary.

02 owns the lexical retrieval pool, 03 owns the LLM shortlist, and the two
design-model passes can only make actual apply/consider/not-use decisions
inside that fixed shortlist. A shortlist therefore never means that an asset
must be used in the final design.
"""

from __future__ import annotations

import importlib.util
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


def _catalog_shortlist(numbers: list[int], *, maximum: int = 2) -> dict:
    """The authoritative Data envelope emitted by node 03.

    Keep this independent of the initial design response: the decision LLM
    cannot manufacture or expand the candidate scope by echoing a policy.
    """

    retrieval = _retrieval()
    return {
        "ok": True,
        "status": "COMPLETED",
        "schema_version": "catalog-shortlist/v1",
        "request_sha256": _request()["request_sha256"],
        "candidate_set_sha256": retrieval["candidate_set_sha256"],
        "catalog_file_sha256": retrieval["catalog_file_sha256"],
        "selection_policy": {
            "max_shortlisted_catalog_items": maximum,
            "zero_shortlist_allowed": True,
            "selection_scope": "candidate_shortlist_only",
            "selection_method": "llm-structured-shortlist/v1",
            "selection_source": "canvas_node_03",
        },
        "shortlisted_candidates": [
            {
                "asset_id": _candidate(number)["asset_id"],
                "version": _candidate(number)["version"],
                "shortlist_rank": rank,
                "reason": f"후보 {number}를 설계 검토 대상으로 선별했습니다.",
            }
            for rank, number in enumerate(numbers, start=1)
        ],
        "shortlisted_count": len(numbers),
        "unshortlisted_candidate_count": len(retrieval["candidates"]) - len(numbers),
        "warnings": [],
        "trace": {},
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


def _normalized_result(model_response: dict, *, catalog_shortlist: dict | None = None) -> dict:
    module = _module("05_business_design_result_normalizer.py")
    component = module.BusinessDesignResultNormalizerComponent()
    component.request = Data(data=_request())
    component.retrieval_result = Data(data=_retrieval())
    component.model_response = Data(data=model_response)
    component.catalog_shortlist = Data(data=catalog_shortlist or _catalog_shortlist([1, 2]))
    return component.normalize_design().data


def _identities(application: dict, bucket: str) -> list[tuple[str, str]]:
    return [(item["asset_id"], item["version"]) for item in application[bucket]]


def _shortlist_identities(result: dict) -> list[tuple[str, str]]:
    return [
        (item["asset_id"], item["version"])
        for item in result["catalog_candidate_shortlist"]["candidates"]
    ]


def test_prompt_builder_consumes_the_fixed_llm_shortlist_not_a_visible_limit_of_its_own():
    module = _module("03_business_design_prompt_builder.py")
    inputs = {item.name: item for item in module.BusinessDesignPromptBuilderComponent.inputs}

    assert "max_shortlisted_catalog_items" not in inputs
    shortlist_input = inputs["catalog_shortlist"]
    assert shortlist_input.required is True
    assert {"Data", "JSON"}.issubset(set(shortlist_input.input_types))

    component = module.BusinessDesignPromptBuilderComponent()
    component.request = Data(data=_request())
    component.retrieval_result = Data(data=_retrieval())
    component.catalog_shortlist = Data(data=_catalog_shortlist([4, 2]))
    component.max_prompt_chars = 64_000
    component.max_estimated_tokens = 20_000

    prompt = component.build_prompt()

    assert prompt.data["catalog_shortlist_policy"] == {
        "max_shortlisted_catalog_items": 2,
        "selection_scope": "candidate_shortlist_only",
        "selection_source": "llm_catalog_shortlister",
    }
    assert prompt.data["candidate_count"] == 2
    assert prompt.data["retrieval_candidate_count"] == 4
    assert [item["asset_id"] for item in prompt.data["catalog_shortlist"]["shortlisted_candidates"]] == [
        _candidate(4)["asset_id"],
        _candidate(2)["asset_id"],
    ]
    assert _candidate(4)["asset_id"] in prompt.text
    assert _candidate(2)["asset_id"] in prompt.text
    assert _candidate(1)["asset_id"] not in prompt.text
    assert _candidate(3)["asset_id"] not in prompt.text
    assert "catalog_decisions" in prompt.text


def test_prompt_builder_rejects_a_shortlist_with_an_identity_outside_the_retrieval_pool():
    module = _module("03_business_design_prompt_builder.py")
    component = module.BusinessDesignPromptBuilderComponent()
    component.request = Data(data=_request())
    component.retrieval_result = Data(data=_retrieval())
    component.catalog_shortlist = Data(data=_catalog_shortlist([1, 2]))
    component.catalog_shortlist.data["shortlisted_candidates"][1]["version"] = "v-not-in-retrieval"
    component.max_prompt_chars = 64_000
    component.max_estimated_tokens = 20_000

    with pytest.raises(ValueError, match="CATALOG_SHORTLIST_INVALID"):
        component.build_prompt()


def test_first_structured_output_preserves_the_shortlist_policy_from_04_for_the_normalizer():
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
        data={
            "catalog_shortlist_policy": {
                "max_shortlisted_catalog_items": 2,
                "selection_scope": "candidate_shortlist_only",
                "selection_source": "llm_catalog_shortlister",
            }
        },
    )

    result = component.build_structured_output().data

    assert result["catalog_shortlist_policy"] == {
        "max_shortlisted_catalog_items": 2,
        "selection_scope": "candidate_shortlist_only",
        "selection_source": "llm_catalog_shortlister",
    }
    assert result["schema_version"] == "business-design-draft/v1"


def test_first_normalizer_uses_03_shortlist_as_the_only_allowed_design_decision_scope():
    # The direct 03 output, not the order or policy echoed by the design LLM,
    # determines which candidates it may apply. LLM order remains meaningful
    # within the scope, but no lexical-rank replacement happens here.
    response = _draft(
        [
            _decision(4, "selected", target=4),
            _decision(2, "selected", target=2),
            _decision(1, "selected", target=1),
            _decision(3, "considered"),
        ]
    )
    response["catalog_shortlist_policy"] = {"max_shortlisted_catalog_items": 30}

    result = _normalized_result(response, catalog_shortlist=_catalog_shortlist([4, 2]))
    application = result["catalog_application"]

    assert _identities(application, "selected") == [
        (_candidate(2)["asset_id"], "v2"),
        (_candidate(4)["asset_id"], "v4"),
    ]
    report_identities = {
        identity
        for bucket in ("selected", "considered", "not_used")
        for identity in _identities(application, bucket)
    }
    assert {(_candidate(1)["asset_id"], "v1"), (_candidate(3)["asset_id"], "v3")}.isdisjoint(report_identities)
    assert application["candidate_count"] == 2
    assert application["retrieval_candidate_count"] == 4
    assert "CATALOG_DECISION_OUTSIDE_SHORTLIST" in result["warnings"]
    assert result["trace"]["catalog_shortlist_policy"]["max_shortlisted_catalog_items"] == 2
    assert _shortlist_identities(result) == [
        (_candidate(4)["asset_id"], "v4"),
        (_candidate(2)["asset_id"], "v2"),
    ]


def test_final_normalizer_keeps_first_shortlist_fixed_but_allows_every_shortlisted_asset_to_be_unused():
    fixed_shortlist = _catalog_shortlist([1, 2])
    first_response = _draft(
        [
            _decision(1, "selected", target=1),
            _decision(2, "selected", target=2),
            _decision(3, "not_used"),
            _decision(4, "not_used"),
        ]
    )
    first = _normalized_result(first_response, catalog_shortlist=fixed_shortlist)
    assert _shortlist_identities(first) == [
        (_candidate(1)["asset_id"], "v1"),
        (_candidate(2)["asset_id"], "v2"),
    ]

    # The second LLM may conclude that neither of the fixed candidates helps
    # the refined design. It must not turn either into selected merely because
    # 03 shortlisted it. Its attempted use of 3/4 is safely downgraded.
    refinement_response = _draft(
        [
            _decision(1, "not_used"),
            _decision(2, "not_used"),
            _decision(3, "considered"),
            _decision(4, "selected", target=4),
        ]
    )
    refinement_response["catalog_shortlist_policy"] = {"max_shortlisted_catalog_items": 30}
    refined = _normalized_result(refinement_response, catalog_shortlist=fixed_shortlist)
    application = refined["catalog_application"]

    assert _identities(application, "selected") == []
    assert {(_candidate(1)["asset_id"], "v1"), (_candidate(2)["asset_id"], "v2")} <= set(
        _identities(application, "not_used")
    )
    report_identities = {
        identity
        for bucket in ("selected", "considered", "not_used")
        for identity in _identities(application, bucket)
    }
    assert {(_candidate(3)["asset_id"], "v3"), (_candidate(4)["asset_id"], "v4")}.isdisjoint(report_identities)
    assert _shortlist_identities(refined) == _shortlist_identities(first)
    assert "CATALOG_CANDIDATE_SHORTLIST_PRESERVED" in refined["warnings"]
    assert "CATALOG_DECISION_OUTSIDE_SHORTLIST" in refined["warnings"]
    assert application["selection_policy"]["max_shortlisted_catalog_items"] == 2
