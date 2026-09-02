"""Contract tests for safe handling of non-JSON Language Model responses."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from lfx.schema import Data, Message


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "components" / "single_flow" / "05_business_design_result_normalizer.py"


def _module():
    name = "single_flow_normalizer_contract_test"
    spec = importlib.util.spec_from_file_location(name, COMPONENT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_model_response_accepts_plain_or_entire_fenced_json_document():
    module = _module()
    draft = {"schema_version": "business-design-draft/v1", "work_analysis": {}, "to_be_design": {}}

    assert module._transport_object(Message(text=json.dumps(draft)), "model_response") == draft
    assert module._transport_object(
        Message(text="```json\n" + json.dumps(draft) + "\n```"), "model_response"
    ) == draft
    assert module._transport_object(Data(data=draft), "model_response") == draft


def test_model_response_safely_unwraps_only_one_complete_structured_output_draft():
    module = _module()
    draft = {
        "schema_version": "business-design-draft/v1",
        "work_analysis": {"title": "업무"},
        "to_be_design": {"summary": "개선"},
    }

    assert module._transport_object(Data(data={"results": [draft]}), "model_response") == draft


def test_model_response_rejects_langflow_default_field_result_list_without_disclosing_contents():
    module = _module()
    raw_value = "internal-work-description-that-must-not-appear"

    with pytest.raises(ValueError, match=r"\[STRUCTURED_OUTPUT_SCHEMA_MISMATCH\]") as raised:
        module._transport_object(
            Data(data={"results": [{"field": raw_value}, {"field": "another-value"}]}),
            "model_response",
        )

    message = str(raised.value)
    assert "기본 Output Schema(field)" in message
    assert "업무 설계 JSON 생성" in message
    assert raw_value not in message


def test_model_response_does_not_choose_one_of_multiple_structured_drafts():
    module = _module()
    draft = {
        "schema_version": "business-design-draft/v1",
        "work_analysis": {"title": "업무"},
        "to_be_design": {"summary": "개선"},
    }

    with pytest.raises(ValueError, match=r"여러 개의 결과"):
        module._transport_object(Data(data={"results": [draft, draft]}), "model_response")


def test_normalizer_input_is_data_or_json_for_langflow_structured_output():
    module = _module()
    model_input = next(item for item in module.BusinessDesignResultNormalizerComponent.inputs if item.name == "model_response")

    # In lfx 1.11, ``DataInput`` is materialized as the JSONInput runtime
    # class.  The portable contract is its allowed connection types.
    assert set(model_input.input_types) == {"Data", "JSON"}


def test_model_response_does_not_extract_json_fragment_from_prose():
    module = _module()
    response = "설계 설명을 먼저 드립니다.\n" + json.dumps({"schema_version": "business-design-draft/v1"})

    with pytest.raises(ValueError, match=r"\[MODEL_OUTPUT_NOT_JSON\]") as raised:
        module._transport_object(Message(text=response), "model_response")

    message = str(raised.value)
    assert "설명문을 설계 결과로 추정·변환하지 않고" in message
    assert "재시도 지시문" in message


def test_model_response_error_is_actionable_and_does_not_disclose_raw_prose_or_secret():
    module = _module()
    raw_secret = "very-secret-value-should-never-appear"
    prose = "### 설계안\n일반 설명을 반환합니다.\napi_key=" + raw_secret

    with pytest.raises(ValueError, match=r"\[MODEL_OUTPUT_NOT_JSON\]") as raised:
        module._transport_object(Message(text=prose), "model_response")

    message = str(raised.value)
    assert "설명문 또는 Markdown" in message
    assert "JSON/Structured Output" in message
    assert "business-design-draft/v1" in message
    assert raw_secret not in message
    assert prose not in message


def test_non_json_fence_is_not_treated_as_json_document():
    module = _module()
    response = "```python\n{'schema_version': 'business-design-draft/v1'}\n```"

    with pytest.raises(ValueError, match=r"\[MODEL_OUTPUT_NOT_JSON\]"):
        module._transport_object(Message(text=response), "model_response")


def test_refinement_fallback_keeps_only_matching_verified_initial_result():
    module = _module()
    request = {
        "request_sha256": "sha256:" + "a" * 64,
        "final_refinement_instructions": "분기와 예외 처리를 더 구체적으로 보여 주세요.",
    }
    retrieval = {"candidate_set_sha256": "sha256:" + "b" * 64}
    initial = {
        "schema_version": "business-design-result/v2",
        "status": "COMPLETED_WITH_GAPS",
        "request": dict(request),
        "trace": {"candidate_set_sha256": retrieval["candidate_set_sha256"]},
        "warnings": [],
        "work_analysis": {"title": "초안"},
    }

    result = module._use_verified_initial_result(
        initial,
        request=request,
        retrieval=retrieval,
        fallback={
            "schema_version": "business-design-refinement-fallback/v1",
            "reason_code": "MODEL_UNAVAILABLE",
            "message": "provider internals must not be shown",
        },
    )

    assert result["work_analysis"] == {"title": "초안"}
    assert result["refinement"] == {
        "status": "SKIPPED",
        "reason_code": "MODEL_UNAVAILABLE",
        "operator_instruction_provided": True,
        "message": "최종 보완 모델을 사용할 수 없어 1차 검증 설계 결과를 표시했습니다.",
    }
    assert result["warnings"] == ["REFINEMENT_SKIPPED_MODEL_UNAVAILABLE"]


def test_refinement_fallback_rejects_different_request_or_candidate_set():
    module = _module()
    request = {"request_sha256": "sha256:" + "a" * 64}
    retrieval = {"candidate_set_sha256": "sha256:" + "b" * 64}
    initial = {
        "schema_version": "business-design-result/v2",
        "request": {"request_sha256": "sha256:" + "c" * 64},
        "trace": {"candidate_set_sha256": retrieval["candidate_set_sha256"]},
    }

    with pytest.raises(ValueError, match=r"\[REFINEMENT_FALLBACK_INVALID\]"):
        module._use_verified_initial_result(
            initial,
            request=request,
            retrieval=retrieval,
            fallback={"schema_version": "business-design-refinement-fallback/v1"},
        )


def test_normalizer_exposes_optional_verified_initial_result_input():
    module = _module()
    fallback_input = next(
        item
        for item in module.BusinessDesignResultNormalizerComponent.inputs
        if item.name == "fallback_design_result"
    )

    assert fallback_input.required is False
    assert set(fallback_input.input_types) == {"Data", "JSON"}


def test_final_normalizer_component_uses_fallback_envelope_without_stopping_report_path():
    module = _module()
    request = {
        "request_sha256": "sha256:" + "1" * 64,
        "final_refinement_instructions": "분기 처리를 보강해 주세요.",
    }
    retrieval = {"candidate_set_sha256": "sha256:" + "2" * 64}
    initial = {
        "schema_version": "business-design-result/v2",
        "status": "COMPLETED",
        "request": dict(request),
        "trace": {"candidate_set_sha256": retrieval["candidate_set_sha256"]},
        "warnings": [],
        "work_analysis": {"title": "1차 설계"},
    }
    component = module.BusinessDesignResultNormalizerComponent()
    component.model_response = Data(
        data={
            "schema_version": "business-design-refinement-fallback/v1",
            "reason_code": "REFINEMENT_NATIVE_CALL_FAILED",
            "message": "internal provider message must never be rendered",
        }
    )
    component.request = Data(data=request)
    component.retrieval_result = Data(data=retrieval)
    component.fallback_design_result = Data(data=initial)

    result = component.normalize_design().data

    assert result["refinement"]["status"] == "SKIPPED"
    assert result["refinement"]["reason_code"] == "MODEL_UNAVAILABLE"
    assert "internal provider" not in json.dumps(result, ensure_ascii=False)
