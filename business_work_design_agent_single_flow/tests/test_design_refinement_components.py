"""Focused contracts for the optional, non-blocking second LLM design pass."""

import importlib.util
import json
import sys
from pathlib import Path

from lfx.schema import Data, Message


ROOT = Path(__file__).resolve().parents[1]
PROMPT_COMPONENT = ROOT / "components" / "single_flow" / "06_design_quality_refinement_prompt.py"
OUTPUT_COMPONENT = ROOT / "components" / "single_flow" / "07_business_design_refinement_structured_output.py"


def _module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _initial_design_result():
    return {
        "schema_version": "business-design-result/v2",
        "status": "COMPLETED_WITH_GAPS",
        "request": {
            "request_sha256": "sha256:request-identity",
            "description_display_redacted": "매주 금요일에 메일과 JIRA를 확인하여 주간 보고 초안을 만들고 팀장 승인 후 게시합니다. 오류와 누락은 게시하지 않습니다. api_key=secret-value-that-must-not-leak",
            "final_refinement_instructions": "승인 전 누락·오류 분기와 담당자 확인 항목을 특히 구체화해 주세요.",
        },
        "work_analysis": {
            "title": "주간 업무보고",
            "goal": "근거가 포함된 주간 업무보고 초안을 게시한다.",
            "actors": ["보고 담당자", "팀장"],
            "systems": ["Outlook", "JIRA", "보고 포털"],
            "inputs": ["메일", "JIRA 이슈"],
            "outputs": ["주간 업무보고"],
            "constraints": ["승인 전 게시 금지"],
            "success_criteria": ["근거와 링크가 포함된 승인 보고서"],
            "current_steps": [
                {
                    "step_ref": "as-is-01",
                    "sequence": 1,
                    "title": "메일과 JIRA 확인",
                    "description": "담당자가 수동으로 항목을 취합한다.",
                    "actor": "보고 담당자",
                    "system": "Outlook/JIRA",
                }
            ],
            "current_branches": [],
            "current_exceptions": [],
        },
        "information_gaps": [
            {
                "gap_id": "gap-01",
                "field": "누락 기준",
                "severity": "important",
                "question": "누락 메일과 이슈를 판정하는 기준은 무엇인가요?",
                "why_needed": "게시 차단 기준을 만들기 위해 필요합니다.",
                "design_impact": "예외 분기와 검토 화면에 반영됩니다.",
                "suggested_description_text": "누락으로 판단할 메일과 JIRA 항목 기준은 ... 입니다.",
            }
        ],
        "as_is_graph": {
            "nodes": [
                {"node_id": "start", "node_kind": "start", "title": "시작"},
                {"node_id": "as-is-collect", "node_kind": "work_step", "title": "수동 취합", "summary": "메일과 JIRA를 확인"},
                {"node_id": "end", "node_kind": "end", "title": "종료"},
            ],
            "edges": [],
        },
        "to_be_design": {
            "summary": "근거 수집과 분류를 자동화하고 승인 후 게시한다.",
            "principles": ["사람 승인 유지"],
            "nodes": [
                {"node_id": "start", "node_kind": "start", "title": "시작"},
                {
                    "node_id": "to-be-collect",
                    "node_kind": "system_call",
                    "title": "근거 수집",
                    "summary": "메일과 JIRA를 수집",
                    "implementation_source": "catalog_flow",
                    "catalog_asset_refs": [{"asset_id": "11111111-1111-1111-1111-111111111111", "version": "v1"}],
                },
                {"node_id": "end", "node_kind": "end", "title": "종료"},
            ],
            "edges": [{"edge_id": "to-be-e-01", "source_node_id": "start", "target_node_id": "to-be-collect", "edge_kind": "control", "label": "수집"}],
            "implementation_roadmap": [],
            "risks_and_controls": [],
            "test_scenarios": [],
        },
        "catalog_application": {
            "candidate_count": 2,
            "selected": [
                {
                    "asset_id": "11111111-1111-1111-1111-111111111111",
                    "version": "v1",
                    "title": "메일·JIRA 통합 Flow",
                    "asset_type": "flow",
                    "target_node_ids": ["to-be-collect"],
                    "reason": "메일과 이슈를 함께 수집합니다.",
                    "required_verification": ["권한과 입력 포트 확인"],
                }
            ],
            "considered": [],
            "not_used": [],
        },
        "catalog_candidate_shortlist": {
            "schema_version": "catalog-shortlist/v1",
            "policy": {
                "max_shortlisted_catalog_items": 12,
                "selection_scope": "candidate_shortlist_only",
                "selection_source": "llm_catalog_shortlister",
            },
            "candidates": [
                {
                    "asset_id": "11111111-1111-1111-1111-111111111111",
                    "version": "v1",
                    "shortlist_rank": 1,
                    "asset_type": "flow",
                    "title": "메일·JIRA 통합 Flow",
                    "reason": "메일과 이슈를 함께 수집하는 업무 단계와 관련됩니다.",
                    "technical_contract_status": "metadata_only",
                }
            ],
        },
        "warnings": [],
        "trace": {},
    }


def _retrieval_result():
    return {
        "schema_version": "local-catalog-retrieval/v1",
        "request_sha256": "sha256:request-identity",
        "candidate_set_sha256": "sha256:candidate-set",
        "candidates": [
            {
                "rank": 1,
                "asset_id": "11111111-1111-1111-1111-111111111111",
                "version": "v1",
                "asset_type": "flow",
                "title": "메일·JIRA 통합 Flow",
                "category": "보고",
                "description": "메일과 JIRA 근거를 취합합니다.",
                "capabilities": ["메일 수집", "JIRA 수집"],
                "matched_terms": ["메일", "JIRA"],
                "match_level": "strong",
                "technical_contract_status": "metadata_only",
            },
            {
                "rank": 2,
                "asset_id": "22222222-2222-2222-2222-222222222222",
                "version": "v2",
                "asset_type": "component",
                "title": "게시 전 결과 점검",
                "category": "검증",
                "description": "누락과 오류가 있는 결과의 게시를 막습니다.",
                "capabilities": ["결과 검증"],
                "matched_terms": ["누락", "오류"],
                "match_level": "moderate",
                "technical_contract_status": "ports_extracted",
            },
        ],
    }


def _draft_payload(title="보완된 주간 업무보고"):
    return {
        "schema_version": "business-design-draft/v1",
        "work_analysis": {"title": title},
        "information_gaps": [],
        "as_is_graph": {"nodes": [], "edges": []},
        "to_be_design": {"nodes": [], "edges": []},
        "catalog_decisions": [],
    }


class _FakeRunnable:
    def __init__(self, schema):
        self.schema = schema
        self.messages = None
        self.config = None

    def invoke(self, messages, *, config):
        self.messages = messages
        self.config = config
        return self.schema(**_draft_payload())


class _FakeModel:
    def __init__(self):
        self.schema = None
        self.runnable = None

    def with_structured_output(self, schema):
        self.schema = schema
        self.runnable = _FakeRunnable(schema)
        return self.runnable


class _UnsupportedThenPlainJsonModel:
    def with_structured_output(self, schema):
        raise NotImplementedError("response_schema unsupported by this provider")

    def invoke(self, messages, *, config):
        return json.dumps(_draft_payload("호환 JSON 초안"), ensure_ascii=False)


class _ProviderFailureRunnable:
    def invoke(self, messages, *, config):
        raise RuntimeError("HTTP 429 api_key=secret-that-must-not-leak")


class _ProviderFailureModel:
    def with_structured_output(self, schema):
        return _ProviderFailureRunnable()


def test_refinement_prompt_is_bounded_structured_and_uses_final_instruction_only():
    module = _module(PROMPT_COMPONENT, "design_refinement_prompt_component_test")
    component = module.DesignQualityRefinementPromptComponent()
    component.initial_design_result = Data(data=_initial_design_result())
    component.retrieval_result = Data(data=_retrieval_result())

    message = component.build_refinement_prompt()

    assert message.data["schema_version"] == "business-design-refinement-prompt/v1"
    assert message.data["candidate_count"] == 1
    assert message.data["final_refinement_instruction_present"] is True
    assert "<quality_findings>" in message.text
    assert "branch-exception-coverage" in message.text
    assert "11111111-1111-1111-1111-111111111111" in message.text
    assert "22222222-2222-2222-2222-222222222222" not in message.text
    assert "<locked_catalog_candidate_shortlist>" in message.text
    assert "하나도 selected로 적용하지 않아도 됩니다" in message.text
    assert "<candidate_pool_index>" not in message.text
    assert "승인 전 누락·오류 분기" in message.text
    assert "secret-value-that-must-not-leak" not in message.text
    assert "[REDACTED]" in message.text
    assert len(message.text) <= module._MAX_PROMPT_CHARS


def test_refinement_prompt_declares_portable_data_inputs_and_no_sibling_imports():
    module = _module(PROMPT_COMPONENT, "design_refinement_prompt_component_source_test")
    inputs = {item.name: item for item in module.DesignQualityRefinementPromptComponent.inputs}
    assert set(inputs) == {"initial_design_result", "retrieval_result"}
    assert set(inputs["initial_design_result"].input_types) == {"Data", "JSON"}
    assert set(inputs["retrieval_result"].input_types) == {"Data", "JSON"}
    source = PROMPT_COMPONENT.read_text(encoding="utf-8")
    assert "from ." not in source
    assert "import components" not in source


def test_refinement_prompt_does_not_reexpose_unselected_candidates_to_the_second_llm():
    module = _module(PROMPT_COMPONENT, "design_refinement_prompt_component_100_candidates_test")
    retrieval = _retrieval_result()
    prototype = retrieval["candidates"][0]
    for number in range(3, 101):
        candidate = dict(prototype)
        candidate["rank"] = number
        candidate["asset_id"] = f"{number:08d}-0000-4000-8000-000000000000"
        candidate["version"] = f"v{number}"
        candidate["title"] = f"후보 자산 {number}"
        retrieval["candidates"].append(candidate)
    component = module.DesignQualityRefinementPromptComponent()
    component.initial_design_result = _initial_design_result()
    component.retrieval_result = retrieval

    message = component.build_refinement_prompt()

    assert message.data["candidate_count"] == 1
    assert "11111111-1111-1111-1111-111111111111" in message.text
    assert "00000003-0000-4000-8000-000000000000" not in message.text
    assert "00000100-0000-4000-8000-000000000000" not in message.text
    assert "<candidate_pool_index>" not in message.text
    assert "<locked_catalog_candidate_shortlist>" in message.text
    assert len(message.text) <= module._MAX_PROMPT_CHARS


def test_refinement_structured_output_returns_a_valid_second_draft_from_native_mock_model():
    module = _module(OUTPUT_COMPONENT, "design_refinement_output_component_test")
    model = _FakeModel()
    component = module.BusinessDesignRefinementStructuredOutputComponent()
    component.model = model
    component.input_value = Message(text="<required_output>JSON only</required_output>")

    result = component.build_refined_design_draft().data

    assert result == _draft_payload()
    assert model.schema is module.BusinessDesignDraftV1
    assert model.runnable.messages[0].content == module.FIXED_REFINEMENT_SYSTEM_PROMPT
    assert model.runnable.messages[1].content == "<required_output>JSON only</required_output>"
    assert model.runnable.config == {"callbacks": []}
    assert "native" in component.status


def test_refinement_system_prompt_locks_only_shortlist_and_allows_not_used_decisions():
    module = _module(OUTPUT_COMPONENT, "design_refinement_output_component_shortlist_contract_test")

    prompt = module.FIXED_REFINEMENT_SYSTEM_PROMPT

    assert "catalog_candidate_shortlist" in prompt
    assert "후보 밖 자산" in prompt
    assert "모든 후보를 not_used" in prompt
    assert "decision·target_node_ids를 동일하게 유지" not in prompt


def test_refinement_structured_output_uses_strict_json_compatibility_when_native_schema_is_unsupported():
    module = _module(OUTPUT_COMPONENT, "design_refinement_output_component_compat_test")
    component = module.BusinessDesignRefinementStructuredOutputComponent()
    component.model = _UnsupportedThenPlainJsonModel()
    component.input_value = Data(data={"text": "second pass request"})

    result = component.build_refined_design_draft().data

    assert result["schema_version"] == "business-design-draft/v1"
    assert result["work_analysis"]["title"] == "호환 JSON 초안"
    assert "호환성 JSON" in component.status


def test_refinement_structured_output_never_raises_or_leaks_provider_errors():
    module = _module(OUTPUT_COMPONENT, "design_refinement_output_component_fallback_test")
    component = module.BusinessDesignRefinementStructuredOutputComponent()
    component.model = _ProviderFailureModel()
    component.input_value = "safe refinement request"

    result = component.build_refined_design_draft().data

    assert result == {
        "schema_version": "business-design-refinement-fallback/v1",
        "status": "FALLBACK_TO_INITIAL",
        "reason_code": "REFINEMENT_NATIVE_CALL_FAILED",
        "message": "최종 보완 모델 호출을 완료하지 못해 1차 정규화 설계를 그대로 사용합니다.",
    }
    assert "secret-that-must-not-leak" not in json.dumps(result, ensure_ascii=False)


def test_refinement_structured_output_handles_missing_model_as_a_fallback_envelope():
    module = _module(OUTPUT_COMPONENT, "design_refinement_output_component_missing_model_test")
    component = module.BusinessDesignRefinementStructuredOutputComponent()
    component.input_value = "safe refinement request"

    result = component.build_refined_design_draft().data

    assert result["schema_version"] == "business-design-refinement-fallback/v1"
    assert result["reason_code"] == "REFINEMENT_MODEL_MISSING"


def test_refinement_contract_rebuilds_without_postponed_annotation_imports():
    source = OUTPUT_COMPONENT.read_text(encoding="utf-8")
    assert "from __future__ import annotations" not in source
    namespace = {}
    exec(compile(source, str(OUTPUT_COMPONENT), "exec"), namespace, namespace)  # noqa: S102
    draft_type = namespace["BusinessDesignDraftV1"]
    assert draft_type.model_validate(_draft_payload()).model_dump(mode="json") == _draft_payload()
