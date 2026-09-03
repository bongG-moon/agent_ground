"""Contracts for the explicit LLM catalog-candidate shortlisting stage."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from lfx.schema import Data


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "components" / "single_flow" / "03_catalog_candidate_shortlister.py"


def _module():
    name = "single_flow_catalog_candidate_shortlister_contract_test"
    spec = importlib.util.spec_from_file_location(name, COMPONENT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
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


def _choice(number: int, *, version: str | None = None, reason: str | None = None) -> dict:
    candidate = _candidate(number)
    return {
        "asset_id": candidate["asset_id"],
        "version": version if version is not None else candidate["version"],
        "reason": reason or f"후보 {number}는 업무 설명과 관련 있습니다.",
    }


class _Runnable:
    def __init__(self, schema, choices: list[dict]):
        self.schema = schema
        self.choices = choices
        self.messages = None
        self.config = None

    def invoke(self, messages, *, config):
        self.messages = messages
        self.config = config
        return self.schema(
            schema_version="catalog-shortlist-draft/v1",
            shortlisted_candidates=self.choices,
        )


class _Model:
    def __init__(self, choices: list[dict]):
        self.choices = choices
        self.schema = None
        self.runnable = None

    def with_structured_output(self, schema):
        self.schema = schema
        self.runnable = _Runnable(schema, self.choices)
        return self.runnable


def _run(choices: list[dict], *, maximum: int = 12):
    module = _module()
    model = _Model(choices)
    component = module.CatalogCandidateShortlisterComponent()
    component.request = Data(data=_request())
    component.retrieval_result = Data(data=_retrieval())
    component.model = model
    component.max_shortlisted_catalog_items = maximum
    return module, model, component.build_catalog_shortlist().data


@pytest.mark.parametrize(
    ("choices", "maximum", "expected_numbers"),
    [([], 2, []), ([_choice(4), _choice(2)], 2, [4, 2])],
)
def test_shortlister_emits_an_authoritative_zero_to_n_shortlist_in_llm_order(
    choices: list[dict], maximum: int, expected_numbers: list[int]
):
    module, model, result = _run(choices, maximum=maximum)

    assert result["ok"] is True
    assert result["status"] == "COMPLETED"
    assert result["schema_version"] == "catalog-shortlist/v1"
    assert result["shortlisted_count"] == len(expected_numbers)
    assert [item["asset_id"] for item in result["shortlisted_candidates"]] == [
        _candidate(number)["asset_id"] for number in expected_numbers
    ]
    assert [item["shortlist_rank"] for item in result["shortlisted_candidates"]] == list(
        range(1, len(expected_numbers) + 1)
    )
    assert result["selection_policy"] == {
        "max_shortlisted_catalog_items": maximum,
        "zero_shortlist_allowed": True,
        "selection_scope": "candidate_shortlist_only",
        "selection_method": "llm-structured-shortlist/v1",
        "selection_source": "canvas_node_03",
    }
    assert model.schema is module.CatalogShortlistDraftV1
    assert model.runnable.messages[0].content == module.FIXED_SHORTLIST_SYSTEM_PROMPT
    assert model.runnable.config == {"callbacks": []}


def test_shortlister_rejects_outside_candidate_identities_and_caps_the_llm_output():
    # Candidate 4 and 2 are valid and preserve their LLM-defined order.  The
    # invalid version does not belong to the retrieval registry, and candidate
    # 1 overflows the visible shortlist cap.
    module, _, result = _run(
        [
            _choice(4),
            _choice(3, version="v-not-in-retrieval"),
            _choice(2),
            _choice(1),
        ],
        maximum=2,
    )

    assert [(item["asset_id"], item["version"]) for item in result["shortlisted_candidates"]] == [
        (_candidate(4)["asset_id"], "v4"),
        (_candidate(2)["asset_id"], "v2"),
    ]
    assert result["shortlisted_count"] == 2
    assert result["unshortlisted_candidate_count"] == 2
    assert "CATALOG_SHORTLIST_OUTSIDE_CANDIDATE_IGNORED" in result["warnings"]
    assert "CATALOG_SHORTLIST_LIMIT_APPLIED" in result["warnings"]
    assert _choice(3, version="v-not-in-retrieval")["asset_id"] not in {
        item["asset_id"] for item in result["shortlisted_candidates"]
    }
    assert module._OUTPUT_SCHEMA == "catalog-shortlist/v1"


@pytest.mark.parametrize("maximum", [0, 31])
def test_shortlister_rejects_an_unsafe_visible_shortlist_cap(maximum: int):
    module = _module()
    component = module.CatalogCandidateShortlisterComponent()
    component.request = Data(data=_request())
    component.retrieval_result = Data(data=_retrieval())
    component.model = _Model([])
    component.max_shortlisted_catalog_items = maximum

    with pytest.raises(ValueError, match="CATALOG_SHORTLIST_LIMIT_INVALID"):
        component.build_catalog_shortlist()
