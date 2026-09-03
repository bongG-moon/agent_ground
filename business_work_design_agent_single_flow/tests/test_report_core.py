"""Focused direct-import tests for the standalone single-flow report side."""

from __future__ import annotations

import ast
import copy
import importlib.util
import json
import sys
from pathlib import Path

from lfx.schema import Data, Message


ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ROOT / "components" / "single_flow"


def _module(filename: str):
    path = COMPONENTS / filename
    name = f"single_flow_test_{filename[:-3]}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _design_result():
    m05 = _module("05_business_design_result_normalizer.py")
    component = m05.BusinessDesignResultNormalizerComponent()
    component.request = Data(
        data={
            "schema_version": "business-design-request/v2",
            "description_original_sha256": "sha256:" + "1" * 64,
            "request_sha256": "sha256:" + "2" * 64,
            "description_display_redacted": "매주 금요일 업무 메일과 JIRA를 모아 주간보고 초안을 만들고 팀장 검토 후 게시합니다. <script>alert(1)</script>",
            "additional_instructions": "근거 링크와 실패 처리도 설계하세요.",
            "redactions": [],
            "redaction_count": 0,
        }
    )
    component.retrieval_result = Data(
        data={
            "catalog_file_sha256": "sha256:" + "3" * 64,
            "candidate_set_sha256": "sha256:" + "4" * 64,
            "top_n_returned": 2,
            "ranking_algorithm": "local-lexical-rrf/v1",
            "candidates": [
                {
                    "asset_id": "4deabfbd-b270-49ee-92e5-38b86cc5f908",
                    "version": "v1.1.1",
                    "type": "py",
                    "title": "메일 본문·첨부 텍스트 추출 Component",
                    "technical_contract_status": "metadata_only",
                },
                {
                    "asset_id": "a395f7e2-10ae-4d06-9b28-d79b49bc7e50",
                    "version": "v0.1.0",
                    "type": "json",
                    "title": "메일·JIRA 통합 주간보고 검토 Flow",
                    "technical_contract_status": "flow_graph_extracted",
                },
            ],
        }
    )
    component.catalog_shortlist = Data(
        data={
            "ok": True,
            "status": "COMPLETED",
            "schema_version": "catalog-shortlist/v1",
            "request_sha256": "sha256:" + "2" * 64,
            "candidate_set_sha256": "sha256:" + "4" * 64,
            "catalog_file_sha256": "sha256:" + "3" * 64,
            "selection_policy": {
                "max_shortlisted_catalog_items": 12,
                "zero_shortlist_allowed": True,
                "selection_scope": "candidate_shortlist_only",
                "selection_method": "llm-structured-shortlist/v1",
                "selection_source": "canvas_node_03",
            },
            "shortlisted_candidates": [
                {
                    "asset_id": "4deabfbd-b270-49ee-92e5-38b86cc5f908",
                    "version": "v1.1.1",
                    "shortlist_rank": 1,
                    "reason": "메일 본문과 첨부 텍스트를 업무보고 근거로 검토합니다.",
                },
                {
                    "asset_id": "a395f7e2-10ae-4d06-9b28-d79b49bc7e50",
                    "version": "v0.1.0",
                    "shortlist_rank": 2,
                    "reason": "메일과 JIRA를 통합하는 보고 Flow를 검토합니다.",
                },
            ],
            "shortlisted_count": 2,
            "unshortlisted_candidate_count": 0,
            "warnings": [],
            "trace": {},
        }
    )
    component.model_response = Message(
        text=json.dumps(
            {
                "schema_version": "business-design-draft/v1",
                "work_analysis": {
                    "title": "주간 업무보고 작성",
                    "goal": "메일과 JIRA 근거를 사용해 검토 가능한 주간보고 초안을 생성합니다.",
                    "actors": ["업무 담당자", "팀장"],
                    "systems": ["Outlook", "JIRA", "사내 보고 포털"],
                    "current_steps": [
                        {"step_ref": "mail", "title": "메일 수집", "description": "지난 한 주 업무 메일을 수집합니다."},
                        {"step_ref": "jira", "title": "JIRA 확인", "description": "프로젝트 이슈와 진행 상황을 확인합니다."},
                        {"step_ref": "review", "title": "팀장 검토", "description": "초안을 검토하고 승인 여부를 판단합니다."},
                    ],
                    "problems": ["근거 수집과 분류를 수작업으로 반복합니다."],
                },
                "information_gaps": [
                    {
                        "field": "posting_api",
                        "severity": "important",
                        "question": "사내 보고 포털의 게시 API와 승인자 식별 방식은 무엇인가요?",
                        "why_needed": "게시 및 승인 단계의 입력·권한 계약을 결정하기 위해 필요합니다.",
                        "design_impact": "현재 설계에서는 게시를 외부 연계 확인 단계로 남깁니다.",
                        "suggested_description_text": "보고 포털 API는 …이며 승인자는 … 방식으로 식별합니다.",
                    }
                ],
                "as_is_graph": {"nodes": [], "edges": []},
                "to_be_design": {
                    "summary": "메일·JIRA 근거를 수집·분류하고 담당자 검토 후 게시하도록 개선합니다.",
                    "principles": ["원본 근거를 보존합니다.", "승인 전에는 게시하지 않습니다."],
                    "nodes": [
                        {"node_id": "collect", "title": "메일·JIRA 근거 수집", "summary": "메일과 JIRA를 조회해 근거를 모읍니다.", "implementation_source": "catalog_flow"},
                        {"node_id": "classify", "title": "업무 항목 분류", "summary": "완료·진행·이슈·다음 계획으로 분류합니다.", "implementation_source": "builtin"},
                        {"node_id": "review", "node_kind": "human_review", "title": "담당자 검토", "summary": "민감정보와 누락을 검토합니다.", "implementation_source": "human_task"},
                        {"node_id": "post", "title": "보고 포털 게시", "summary": "승인된 초안만 게시합니다.", "implementation_source": "external_service"},
                    ],
                    "edges": [
                        {"source_node_id": "collect", "target_node_id": "classify", "edge_kind": "control", "label": "근거 전달"},
                        {"source_node_id": "classify", "target_node_id": "review", "edge_kind": "control", "label": "초안 생성"},
                        {"source_node_id": "review", "target_node_id": "post", "edge_kind": "branch", "label": "승인", "condition": "검토 완료"},
                    ],
                    "implementation_roadmap": [{"phase": "1", "title": "근거 수집 연결", "actions": ["메일·JIRA 권한 확인"], "dependencies": [], "completion_criteria": ["샘플 근거를 수집합니다."]}],
                    "risks_and_controls": [{"risk_id": "auth", "risk": "메일 접근 권한 부족", "impact": "근거 누락", "control": "권한 확인 실패 시 게시를 차단합니다.", "owner_role": "업무 담당자"}],
                    "test_scenarios": [{"test_id": "approved", "title": "승인 후 게시", "given": "근거와 초안이 준비됨", "when": "담당자가 검토를 완료함", "then": "승인된 초안만 게시 경로로 이동함"}],
                },
                "catalog_decisions": [
                    {"asset_id": "a395f7e2-10ae-4d06-9b28-d79b49bc7e50", "version": "v0.1.0", "decision": "selected", "target_node_ids": ["collect"], "reason": "메일과 JIRA 근거 수집 단계에 직접 대응합니다.", "required_verification": ["실제 입력·출력 port와 권한 확인"]}
                ],
            },
            ensure_ascii=False,
        )
    )
    return component.normalize_design().data


def test_report_pipeline_smoke_and_catalog_links():
    design = _design_result()
    assert design["status"] == "COMPLETED_WITH_GAPS"
    app = design["catalog_application"]
    assert len(app["selected"]) == 1
    assert len(app["not_used"]) == 1
    assert app["selected"][0]["catalog_url"] == "https://agent-hub.skhynix.com/#/flow/a395f7e2-10ae-4d06-9b28-d79b49bc7e50"
    assert app["not_used"][0]["catalog_url"] == "https://agent-hub.skhynix.com/#/component/4deabfbd-b270-49ee-92e5-38b86cc5f908"

    m06 = _module("06_report_view_model_builder_v2.py")
    builder = m06.ReportViewModelBuilderV2Component()
    builder.design_result = Data(data=design)
    vm = builder.build_view_model().data
    assert vm["schema_version"] == "report-view-model/v2"
    assert vm["completion_status"]["catalog_selected_count"] == 1
    assert vm["to_be_graph"]["nodes"]
    collect_node = next(node for node in vm["to_be_graph"]["nodes"] if node["catalog_refs"])
    collect_detail = vm["to_be_graph"]["details"][collect_node["detail_ref"]]
    assert set(collect_detail) == {
        "current_work",
        "problems",
        "improvement",
        "inputs",
        "outputs",
        "catalog_recommendations",
        "implementation_io_plan",
    }
    assert "catalog_application" not in collect_detail
    assert "actor" not in collect_detail
    assert "failure_policy" not in collect_detail
    assert collect_detail["catalog_recommendations"] == [
        {
            "asset_id": "a395f7e2-10ae-4d06-9b28-d79b49bc7e50",
            "title": "메일·JIRA 통합 주간보고 검토 Flow",
            "asset_type": "flow",
            "version": "v0.1.0",
            "catalog_url": "https://agent-hub.skhynix.com/#/flow/a395f7e2-10ae-4d06-9b28-d79b49bc7e50",
            "reason": "메일과 JIRA 근거 수집 단계에 직접 대응합니다.",
            "required_verification": ["실제 입력·출력 port와 권한 확인"],
        }
    ]
    # Safe traceability/accessibility data stays in the closed view model; the
    # renderer decides which of it is appropriate to show in the human UI.
    assert vm["to_be_graph"]["text_fallback"]
    assert vm["technical_trace"]["ranking_algorithm"] == "local-lexical-rrf/v1"

    m07 = _module("07_responsive_report_renderer_v2.py")
    scrubbed_vm = m07._scrub_catalog_urls(copy.deepcopy(vm))
    assert (
        scrubbed_vm["to_be_graph"]["details"][collect_node["detail_ref"]]["catalog_recommendations"][0]["catalog_url"]
        == "https://agent-hub.skhynix.com/#/flow/a395f7e2-10ae-4d06-9b28-d79b49bc7e50"
    )
    renderer = m07.ResponsiveReportRendererV2Component()
    renderer.report_view_model = Data(data=vm)
    first = renderer.render_report().data
    renderer.report_view_model = Data(data=vm)
    second = renderer.render_report().data
    assert first["status"] == "RENDERED"
    assert first["html"] == second["html"]
    assert first["content_sha256"] == second["content_sha256"]
    # The top of the report is an implementation brief only.  Detailed
    # analysis, catalog, roadmap, risks, tests, and gaps are shown once in
    # their dedicated sections below instead of being duplicated as 11 cards.
    assert "Agent 구현 한눈에 보기" in first["html"]
    assert "Agent가 자동으로 처리할 일" in first["html"]
    assert "Agent를 조립하는 방식" in first["html"]
    assert "사람 검토와 차단 경계" in first["html"]
    assert "업무 목적, 현재 문제, 개선 방향, 실행 분담을 짧게 확인합니다." not in first["html"]
    assert "const blockNames=" not in first["html"]
    assert "카탈로그 기반 적용 계획" in first["html"]
    assert "Agent Hub 상세 보기" in first["html"]
    assert "https://agent-hub.skhynix.com/#/flow/a395f7e2-10ae-4d06-9b28-d79b49bc7e50" in first["html"]
    assert "https://agent-hub.skhynix.com/#/component/4deabfbd-b270-49ee-92e5-38b86cc5f908" in first["html"]
    # The report is for business readers: generic raw-object inspection and
    # duplicated technical/fallback regions must never become visible again.
    assert "기술 trace 보기" not in first["html"]
    assert '<section class="static-fallback">' not in first["html"]
    # Without a second-pass instruction or result, the optional refinement
    # card is present only as an initially hidden JS target, never visible.
    assert '<section id="refinement-section" class="card section wide" hidden>' in first["html"]
    assert "이 단계에서 하는 일" in first["html"]
    assert "참고할 카탈로그" in first["html"]
    assert "선정 이유" in first["html"]
    assert 'role="dialog"' in first["html"]
    assert 'aria-modal="true"' in first["html"]
    assert "<script>alert(1)</script>" not in first["html"]
    assert "\\u003cscript" in first["html"]


def test_renderer_scrubs_invalid_catalog_url_before_embedding():
    design = _design_result()
    m06 = _module("06_report_view_model_builder_v2.py")
    builder = m06.ReportViewModelBuilderV2Component()
    builder.design_result = Data(data=design)
    vm = builder.build_view_model().data
    bad = copy.deepcopy(vm)
    bad["catalog_application_plan"]["selected"][0]["catalog_url"] = "https://evil.example/path?token=not-a-real-secret"
    m07 = _module("07_responsive_report_renderer_v2.py")
    bad["report_id"] = m07._expected_report_id(bad)
    renderer = m07.ResponsiveReportRendererV2Component()
    renderer.report_view_model = Data(data=bad)
    html = renderer.render_report().data["html"]
    assert "evil.example" not in html
    assert "Agent Hub 링크 검증 실패" in html


def test_report_refinement_applied_is_concise_and_reader_facing():
    design = _design_result()
    design["request"]["final_refinement_instructions"] = "승인 분기와 실패 시 게시 차단 기준을 더 구체적으로 설명해 주세요."
    design["refinement"] = {
        "status": "APPLIED",
        "provider": "internal-refinement-model",
        "execution_trace": {"attempt": 1, "latency_ms": 42},
        "raw_quality_findings": ["이 값은 보고서에 노출되면 안 됩니다."],
    }
    m06 = _module("06_report_view_model_builder_v2.py")
    builder = m06.ReportViewModelBuilderV2Component()
    builder.design_result = Data(data=design)
    vm = builder.build_view_model().data

    assert vm["refinement_summary"] == {
        "status": "APPLIED",
        "status_provided": True,
        "status_label": "보완 반영 완료",
        "summary": "초안 점검과 보완 지시를 반영해 최종 설계를 한 번 더 다듬었습니다.",
        "final_refinement_instructions_provided": True,
        "final_refinement_instructions": "승인 분기와 실패 시 게시 차단 기준을 더 구체적으로 설명해 주세요.",
    }
    assert "internal-refinement-model" not in json.dumps(vm, ensure_ascii=False)
    assert "execution_trace" not in json.dumps(vm, ensure_ascii=False)

    m07 = _module("07_responsive_report_renderer_v2.py")
    renderer = m07.ResponsiveReportRendererV2Component()
    renderer.report_view_model = Data(data=vm)
    html = renderer.render_report().data["html"]
    assert "설계 보완 반영" in html
    assert "보완 반영 완료" in html
    assert "초안 점검과 보완 지시를 반영해 최종 설계를 한 번 더 다듬었습니다." in html
    assert "요청한 보완 방향" in html
    assert "승인 분기와 실패 시 게시 차단 기준을 더 구체적으로 설명해 주세요." in html
    assert "internal-refinement-model" not in html
    assert "raw_quality_findings" not in html


def test_report_refinement_skipped_keeps_a_basic_draft_fallback():
    design = _design_result()
    design["request"]["final_refinement_instructions"] = "예외 처리와 검증 계획을 우선 보강해 주세요."
    design["refinement"] = {
        "status": "SKIPPED",
        "provider": "internal-refinement-model",
        "failure_reason": "provider timeout detail must not be displayed",
    }
    m06 = _module("06_report_view_model_builder_v2.py")
    builder = m06.ReportViewModelBuilderV2Component()
    builder.design_result = Data(data=design)
    vm = builder.build_view_model().data

    summary = vm["refinement_summary"]
    assert summary["status"] == "SKIPPED"
    assert summary["status_provided"] is True
    assert summary["final_refinement_instructions_provided"] is True
    assert summary["status_label"] == "기본 초안 사용"
    assert "2차 보완 결과를 적용하지 못해" in summary["summary"]
    assert "failure_reason" not in json.dumps(vm, ensure_ascii=False)

    m07 = _module("07_responsive_report_renderer_v2.py")
    renderer = m07.ResponsiveReportRendererV2Component()
    renderer.report_view_model = Data(data=vm)
    html = renderer.render_report().data["html"]
    assert "설계 보완 반영" in html
    assert "기본 초안 사용" in html
    assert "2차 보완 결과를 적용하지 못해 검증된 기본 초안을 기준으로 보고서를 작성했습니다." in html
    assert "예외 처리와 검증 계획을 우선 보강해 주세요." in html
    assert "provider timeout detail" not in html


def test_publish_failure_and_generated_only_preserve_renderer_html():
    m08 = _module("08_report_publisher.py")
    m09 = _module("09_report_result_message.py")
    m10 = _module("10_report_artifact_output.py")
    rendered = {
        "ok": True,
        "status": "RENDERED",
        "report_id": "report-1234567890abcdef12345678",
        "renderer_version": "business-report-renderer.v2",
        "content_sha256": "sha256:" + "f" * 64,
        "html": "<!doctype html><html><body>report</body></html>",
        "report_summary": {"information_gap_count": 2, "catalog_candidate_count": 30, "catalog_selected_count": 3, "catalog_considered_count": 4},
    }
    publisher = m08.ReportPublisherComponent()
    publisher.rendered_report = Data(data=rendered)
    publisher.report_api_url = ""
    generated = publisher.publish_report().data
    assert generated["status"] == "GENERATED_ONLY"
    assert generated["render_result"]["html"] == rendered["html"]
    artifact = m10.ReportArtifactOutputComponent()
    artifact.publish_result = Data(data=generated)
    assert artifact.preserve_artifact().data["render_result"]["html"] == rendered["html"]
    message = m09.ReportResultMessageComponent()
    message.publish_result = Data(data=generated)
    assert "HTML 보고서 생성 완료" in message.build_message().text

    failed_publisher = m08.ReportPublisherComponent()
    failed_publisher.rendered_report = Data(data=rendered)
    failed_publisher.report_api_url = "http://127.0.0.1:1"
    failed = failed_publisher.publish_report().data
    assert failed["status"] == "PUBLISH_FAILED"
    assert failed["render_result"]["html"] == rendered["html"]


def test_publisher_uses_the_closed_existing_report_api_contract():
    """Renderer provenance must be nested in report_plan, not sent as extra fields."""
    m08 = _module("08_report_publisher.py")
    rendered = {
        "ok": True,
        "status": "RENDERED",
        "report_id": "report-1234567890abcdef12345678",
        "renderer_version": "business-report-renderer.v2",
        "content_sha256": "sha256:" + "f" * 64,
        "title": "업무 방식 및 개선 실행 보고서",
        "html": "<!doctype html><html><body>report</body></html>",
        "report_summary": {},
    }
    captured: dict[str, object] = {}
    original_post = m08._post

    def fake_post(url, body, timeout_seconds):
        captured["url"] = url
        captured["body"] = body
        captured["timeout_seconds"] = timeout_seconds
        return {
            "report_id": "20260902000000_" + "a" * 32,
            "view_url": "http://127.0.0.1:5000/reports/view/report-1",
            "download_url": "http://127.0.0.1:5000/reports/download/report-1",
            "expires_at": "2026-09-03T00:00:00+00:00",
            "ttl_hours": 24,
        }

    try:
        m08._post = fake_post
        publisher = m08.ReportPublisherComponent()
        publisher.rendered_report = Data(data=rendered)
        publisher.report_api_url = "http://127.0.0.1:5000"
        publisher.ttl_hours = 24
        result = publisher.publish_report().data
    finally:
        m08._post = original_post

    assert result["status"] == "PUBLISHED"
    assert captured["url"] == "http://127.0.0.1:5000/reports"
    body = captured["body"]
    assert isinstance(body, dict)
    assert set(body) == {
        "html",
        "title",
        "question",
        "view_request",
        "available_datasets",
        "report_plan",
        "ttl_hours",
        "filename_hint",
    }
    assert "renderer_report_id" not in body
    assert "renderer_version" not in body
    assert body["report_plan"] == {
        "source_flow": "F01_business_work_design_single",
        "renderer_report_id": rendered["report_id"],
        "renderer_version": rendered["renderer_version"],
        "content_sha256": rendered["content_sha256"],
    }


def test_components_only_use_standard_library_and_lfx_imports():
    # This report-side contract deliberately has no model/provider dependency.
    # The separate 06/07 refinement components are allowed to use the Langflow
    # structured-output runtime, so do not accidentally apply this narrower
    # renderer/publisher import rule to them merely because their filenames
    # share the numeric prefix.
    filenames = (
        "05_business_design_result_normalizer.py",
        "06_report_view_model_builder_v2.py",
        "07_responsive_report_renderer_v2.py",
        "08_report_publisher.py",
        "09_report_result_message.py",
        "10_report_artifact_output.py",
    )
    for path in [COMPONENTS / filename for filename in filenames]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for item in ast.walk(tree):
            if isinstance(item, ast.ImportFrom) and item.module:
                assert item.level == 0
                assert item.module.startswith(("lfx", "__future__", "typing", "decimal")) or item.module in {"base64", "datetime", "hashlib", "html", "json", "math", "re", "socket", "urllib.parse", "urllib.error", "urllib.request"}
            if isinstance(item, ast.Import):
                for alias in item.names:
                    assert alias.name in {"base64", "datetime", "hashlib", "html", "json", "math", "re", "socket", "urllib.error", "urllib.parse", "urllib.request", "uuid"}


def test_owned_json_schemas_parse():
    for filename in ("business_design_draft.v1.schema.json", "business_design_result.v2.schema.json", "report_view_model.v2.schema.json"):
        parsed = json.loads((ROOT / "schemas" / filename).read_text(encoding="utf-8"))
        assert parsed["$schema"].startswith("https://json-schema.org/")
