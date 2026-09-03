"""Focused contract tests for the TO-BE Langflow 1.11 I/O blueprint."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from lfx.schema import Data


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "components" / "single_flow" / "06_report_view_model_builder_v2.py"


def _module():
    spec = importlib.util.spec_from_file_location("single_flow_io_plan_builder", COMPONENT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _design_result() -> dict:
    asset_id = "11111111-1111-1111-1111-111111111111"
    return {
        "schema_version": "business-design-result/v2",
        "status": "COMPLETED",
        "request": {"description_display_redacted": "주간 업무를 수집하고 검토한 뒤 결과를 안내합니다."},
        "work_analysis": {"title": "주간 업무 안내", "goal": "업무 결과를 정리해 안내합니다."},
        "information_gaps": [],
        "as_is_graph": {
            "nodes": [{"node_id": "as-is-start", "node_kind": "start", "title": "현재 업무 시작", "summary": "수동 업무를 시작합니다."}],
            "edges": [],
        },
        "to_be_design": {
            "summary": "업무 설명을 수집·정리하고 검토 결과를 안내합니다.",
            "principles": [],
            "nodes": [
                {
                    "node_id": "start",
                    "node_kind": "start",
                    "title": "설계 요청 입력",
                    "summary": "업무 설명을 입력받습니다.",
                    "inputs": ["업무 설명"],
                    "outputs": ["정규화된 업무 요청"],
                    "implementation_source": "human_task",
                    "catalog_asset_refs": [],
                },
                {
                    "node_id": "collect",
                    "node_kind": "system_call",
                    "title": "업무 근거 수집",
                    "summary": "업무 요청을 기준으로 근거를 수집합니다.",
                    "inputs": ["정규화된 업무 요청"],
                    "outputs": ["근거 목록"],
                    "implementation_source": "catalog_component",
                    "catalog_asset_refs": [{"asset_id": asset_id, "version": "v1"}],
                },
                {
                    "node_id": "review",
                    "node_kind": "human_review",
                    "title": "담당자 검토",
                    "summary": "근거를 검토해 승인 여부를 판단합니다.",
                    "inputs": ["근거 목록"],
                    "outputs": ["승인 판단"],
                    "implementation_source": "human_task",
                    "catalog_asset_refs": [],
                },
                {
                    "node_id": "end",
                    "node_kind": "end",
                    "title": "결과 안내",
                    "summary": "최종 결과를 사용자에게 안내합니다.",
                    "inputs": ["승인 판단"],
                    "outputs": [],
                    "implementation_source": "builtin",
                    "catalog_asset_refs": [],
                },
            ],
            "edges": [
                {"edge_id": "e-01", "source_node_id": "start", "target_node_id": "collect", "edge_kind": "control", "label": "업무 요청 전달"},
                {"edge_id": "e-02", "source_node_id": "collect", "target_node_id": "review", "edge_kind": "control", "label": "근거 전달"},
                {"edge_id": "e-03", "source_node_id": "review", "target_node_id": "end", "edge_kind": "branch", "label": "승인"},
            ],
            "implementation_roadmap": [],
            "risks_and_controls": [],
            "test_scenarios": [],
        },
        "catalog_application": {
            "selected": [
                {
                    "asset_id": asset_id,
                    "version": "v1",
                    "title": "업무 근거 수집 Component",
                    "asset_type": "component",
                    "technical_contract_status": "metadata_only",
                    "target_node_ids": ["collect"],
                    "reason": "근거 수집 단계에 맞습니다.",
                    "required_verification": ["실제 입출력 포트 확인"],
                    "decision_source": "llm",
                }
            ],
            "considered": [],
            "not_used": [],
        },
        "warnings": [],
        "trace": {},
    }


def _detail(view_model: dict, node_id: str) -> dict:
    node = next(item for item in view_model["to_be_graph"]["nodes"] if item["node_id"] == node_id)
    return view_model["to_be_graph"]["details"][node["detail_ref"]]


def test_to_be_details_have_a_closed_langflow_io_blueprint_with_exact_edge_bindings():
    module = _module()
    builder = module.ReportViewModelBuilderV2Component()
    builder.design_result = Data(data=_design_result())
    view_model = builder.build_view_model().data

    # This is an implementation plan for proposed TO-BE work only: it does
    # not rewrite the reader-facing AS-IS details with fictional port maps.
    assert all("implementation_io_plan" not in detail for detail in view_model["as_is_graph"]["details"].values())
    assert all("implementation_io_plan" in detail for detail in view_model["to_be_graph"]["details"].values())

    collect_plan = _detail(view_model, "collect")["implementation_io_plan"]
    assert collect_plan["schema_version"] == "langflow-implementation-io-plan/v1"
    assert collect_plan["langflow_version"] == "1.11.0"
    assert collect_plan["plan_status"] == "METADATA_ONLY"
    assert "실제 포트 확인" in collect_plan["plan_status_label"]
    assert "구현 청사진" in collect_plan["plan_note"]
    assert collect_plan["component_type"] == "카탈로그 Component"

    collect_input = collect_plan["inputs"][0]
    assert collect_input["binding_kind"] == "upstream_output"
    assert collect_input["source_node_id"] == "start"
    assert collect_input["source_output_port_id"] == "start:out:1"
    assert collect_input["source_output_label"] == "정규화된 업무 요청"
    assert collect_input["source_output_data_type"] == "Data"
    assert collect_input["data_type"] == "Data"
    assert "설계 요청 입력" in collect_input["connection_label"]
    assert "업무 근거 수집" in collect_input["connection_label"]

    collect_output = collect_plan["outputs"][0]
    binding = collect_output["downstream_bindings"][0]
    assert binding["binding_kind"] == "downstream_input"
    assert binding["target_node_id"] == "review"
    assert binding["target_input_port_id"] == "review:in:1"
    assert binding["target_input_data_type"] == "Data"
    assert binding["target_node_type"] == "Chat Input 또는 Form 입력 + 조건 분기"

    start_plan = _detail(view_model, "start")["implementation_io_plan"]
    assert start_plan["external_inputs"] == [
        {
            "input_port_id": "start:in:1",
            "label": "업무 설명",
            "data_type": "Message",
            "type_label": "Message · 대화/설명 텍스트",
            "required": True,
            "recommended_node_type": "Chat Input",
            "recommended_input_name": "input_value",
            "note": "앞 단계 연결이 없으므로 이 Flow 실행 시 외부 입력으로 제공합니다.",
        }
    ]

    end_plan = _detail(view_model, "end")["implementation_io_plan"]
    terminal = end_plan["outputs"][0]["downstream_bindings"][0]
    assert terminal["binding_kind"] == "external_output"
    assert terminal["target_node_type"] == "Chat Output"
    assert terminal["target_input_data_type"] == "Message"

    # No raw dict/list text or sensitive value is carried into the closed plan.
    rendered_plan = str(collect_plan)
    assert "asset_id" not in rendered_plan
    assert "{" not in collect_input["label"]
    assert "API_KEY" not in rendered_plan


def test_unrelated_declared_input_stays_external_instead_of_receiving_a_false_edge_binding():
    module = _module()
    design = _design_result()
    collect = next(item for item in design["to_be_design"]["nodes"] if item["node_id"] == "collect")
    collect["inputs"] = ["기간", "프로젝트 목록"]
    builder = module.ReportViewModelBuilderV2Component()
    builder.design_result = Data(data=design)
    view_model = builder.build_view_model().data

    plan = _detail(view_model, "collect")["implementation_io_plan"]
    upstream = next(item for item in plan["inputs"] if item["binding_kind"] == "upstream_output")
    assert upstream["label"] == "업무 요청 전달 결과"
    assert upstream["source_output_label"] == "정규화된 업무 요청"
    assert {item["label"] for item in plan["external_inputs"]} == {"기간", "프로젝트 목록"}


def test_detail_current_work_includes_summary_and_concrete_handoff_context():
    module = _module()
    builder = module.ReportViewModelBuilderV2Component()
    builder.design_result = Data(data=_design_result())
    view_model = builder.build_view_model().data

    start_work = _detail(view_model, "start")["current_work"]
    assert "업무 설명을 입력받습니다." in start_work
    assert "실행 시 제공되는 입력값은 ‘업무 설명’입니다." in start_work
    assert "처리 결과는 ‘정규화된 업무 요청’입니다." in start_work
    assert "결과를 업무 근거 수집 단계에 전달합니다." in start_work

    collect_work = _detail(view_model, "collect")["current_work"]
    assert "업무 요청을 기준으로 근거를 수집합니다." in collect_work
    assert "앞 단계 설계 요청 입력에서 전달된 입력값은 ‘정규화된 업무 요청’입니다." in collect_work
    assert "처리 결과는 ‘근거 목록’입니다." in collect_work
    assert "결과를 담당자 검토 단계에 전달합니다." in collect_work
