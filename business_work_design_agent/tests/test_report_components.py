from __future__ import annotations

import ast
import base64
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import jsonschema
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = PROJECT_ROOT / "samples"
REPORT_DIR = PROJECT_ROOT / "components" / "report"
COMPONENT_PATHS = [
    PROJECT_ROOT / "components" / "hybrid_retrieval" / "29_search_query_embedding_batcher.py",
    REPORT_DIR / "30_report_view_model_builder.py",
    REPORT_DIR / "31_responsive_report_renderer.py",
    REPORT_DIR / "32_report_publisher.py",
    REPORT_DIR / "37_report_publication_message.py",
]


def load_component(path: Path) -> ModuleType:
    module_name = "test_runtime_" + path.stem
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def read_json(name: str) -> dict[str, Any]:
    value = json.loads((SAMPLES_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


@pytest.fixture(scope="module")
def modules() -> dict[str, ModuleType]:
    return {path.stem: load_component(path) for path in COMPONENT_PATHS}


@pytest.fixture(scope="module")
def sample_contracts() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        read_json("approved_work_definition.json"),
        read_json("approved_agent_blueprint.json"),
        read_json("candidate_context.json"),
    )


def build_view_model(
    modules: dict[str, ModuleType],
    sample_contracts: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> dict[str, Any]:
    work, blueprint, candidate_context = copy.deepcopy(sample_contracts)
    terminal = read_json("agent_blueprint_terminal.json")
    component = modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
        work_definition=work,
        agent_blueprint=terminal,
        retrieval_trace=candidate_context["retrieval_trace"],
        report_title="주간 업무보고 업무 방식 및 Agent 설계",
        max_nodes=500,
        max_edges=1000,
    )
    return dict(component.build_report_view_model().data)


def test_30_uses_graph_edges_for_stable_as_is_presentation_order(
    modules: dict[str, ModuleType],
    sample_contracts: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    """Hash canonicalization must not turn the visual flow into lexical order."""

    work, _, candidate_context = copy.deepcopy(sample_contracts)
    work["as_is_graph"]["nodes"] = list(reversed(work["as_is_graph"]["nodes"]))
    terminal = read_json("agent_blueprint_terminal.json")
    view_model = dict(
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=terminal,
            retrieval_trace=candidate_context["retrieval_trace"],
            report_title="업무 Flow 순서 검증",
            max_nodes=500,
            max_edges=1000,
        ).build_report_view_model().data
    )
    assert [node["source_node_id"] for node in view_model["as_is_graph"]["nodes"]] == [
        "as-start",
        "as-search",
        "as-draft",
        "as-review",
        "as-end",
    ]


def test_30_renders_legacy_sealed_blueprint_with_blank_presentation_text(
    modules: dict[str, ModuleType],
    sample_contracts: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    """Already approved F20 handoffs remain reportable after a UI-text fix."""

    work, _, candidate_context = copy.deepcopy(sample_contracts)
    terminal = read_json("agent_blueprint_terminal.json")
    legacy_node = terminal["blueprint"]["nodes"][0]
    legacy_node["responsibility"] = ""
    legacy_node["reuse_decision_reason"] = ""

    view_model = dict(
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=terminal,
            retrieval_trace=candidate_context["retrieval_trace"],
            report_title="기존 설계 보고서 호환성 검증",
            max_nodes=500,
            max_edges=1000,
        ).build_report_view_model().data
    )

    detail = view_model["to_be_graph"]["details"][view_model["to_be_graph"]["nodes"][0]["detail_ref"]]
    assert detail["improvement"]
    assert detail["reuse_decision_reason"]


def test_30_uses_blueprint_current_work_for_an_empty_as_is_shell_and_localizes_machine_titles(
    modules: dict[str, ModuleType],
    sample_contracts: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    """A skipped F10 clarification must not reduce the report to Start → End.

    The fallback is intentionally presentation-only: it takes the sealed
    Blueprint's existing ``current_work`` text and labels the result as a
    design-reference flow.  It does not modify the approved WorkDefinition or
    turn a metadata candidate into an executable catalog asset.
    """

    module = modules["30_report_view_model_builder"]
    work, _, candidate_context = copy.deepcopy(sample_contracts)
    terminal = read_json("agent_blueprint_terminal.json")
    blueprint = terminal["blueprint"]
    work["steps"] = []
    work["as_is_graph"] = {
        "schema_version": "work-graph/v1",
        "nodes": [
            {"id": "start", "kind": "start", "label": "업무 시작"},
            {"id": "end", "kind": "end", "label": "업무 종료"},
        ],
        "edges": [{"id": "start-end", "source": "start", "target": "end", "branch_label": "시작"}],
    }
    approved_hash = module._approved_semantic_hash(work)
    work["approved_hash"] = approved_hash
    blueprint["approved_hash"] = approved_hash
    candidate_context["retrieval_trace"]["approved_hash"] = approved_hash
    # The presentation-only fixture changes the approved WorkDefinition.  Keep
    # the strict F20 request contract intact by re-sealing every generated
    # component request against that new approved hash; do not bypass the
    # production integrity validation for this test.
    for request in blueprint["generation_requests"]:
        target = next(
            node
            for node in blueprint["nodes"]
            if node.get("generation_request_ref") == request["generation_request_id"]
        )
        request_text = module._expected_generation_request_text(
            target["generation_contract"], target["node_id"], blueprint
        )
        digest = hashlib.sha256(request_text.encode("utf-8")).hexdigest()
        request["generation_request_id"] = "gen-" + digest[:20]
        request["target_node_id"] = target["node_id"]
        request["template_version"] = module.GENERATION_TEMPLATE_VERSION
        request["prompt_pack"] = target["generation_contract"]["prompt_pack"]
        request["component_filename"] = target["generation_contract"]["component_filename"]
        request["class_name"] = target["generation_contract"]["class_name"]
        request["request_text"] = request_text
        request["prompt_sha256"] = "sha256:" + digest
        target["generation_request_ref"] = request["generation_request_id"]
    terminal["generation_requests"] = blueprint["generation_requests"]
    candidate_context["retrieval_trace"]["catalog_presentation"][0]["detail_url"] = (
        "https://catalog.internal.example/assets/47d41a8d-9208-48c2-b79b-9d84d7ce199d?tab=details"
    )

    view_model = dict(
        module.ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=terminal,
            retrieval_trace=candidate_context["retrieval_trace"],
            report_title="AS-IS 보정 흐름 검증",
        ).build_report_view_model().data
    )

    assert len(view_model["as_is_graph"]["nodes"]) == len(blueprint["nodes"])
    assert any(node["node_kind"] == "human_gate" for node in view_model["as_is_graph"]["nodes"])
    machine_node = {
        "node_id": "node-mail-ingest-sanitize",
        "title": "node-mail-ingest-sanitize",
        "current_work": "담당자가 메일 본문과 첨부 문서를 수작업으로 읽고 필요한 문구를 복사하여 정리함",
        "improvement": "메일 파싱, 중복 제거 및 보안 마스킹을 일원화된 Custom Component에서 자동 처리",
    }
    assert module._presentation_title(machine_node, "to_be", 2) == "메일 수집·정제"
    assert "node-" not in module._presentation_summary(machine_node, "to_be")
    assert any("메일" in node["title"] for node in view_model["to_be_graph"]["nodes"])
    assert any("승인" in node["title"] for node in view_model["to_be_graph"]["nodes"])
    assert all("node-" not in node["summary"] for node in view_model["to_be_graph"]["nodes"])

    report = view_model["sections"][0]["items"][0]
    basis = report["as_is_analysis"]["procedure_basis"]
    assert basis["source"] == "blueprint_current_work_fallback"
    assert basis["requires_confirmation"] is True
    assert "설계 참고용" in basis["message"]

    catalog = next(section for section in view_model["sections"] if section["section_id"] == "catalog_recommendations")
    references = [item for item in catalog["items"] if item.get("status") == "reference_candidate_for_stage"]
    assert references
    first = next(item for item in references if item["asset_id"] == "47d41a8d-9208-48c2-b79b-9d84d7ce199d")
    assert first["stage_title"]
    assert first["version"] == "v1.0.0"
    assert first["catalog_url"] == "https://catalog.internal.example/assets/47d41a8d-9208-48c2-b79b-9d84d7ce199d?tab=details"
    assert first["catalog_link_status"] == "카탈로그 메타데이터에 등록된 상세 링크"
    assert "직접 재사용으로 확정하지 않습니다" in first["reuse_decision_reason"]
    stage_references = report["implementation_allocation"]["stage_catalog_references"]
    assert any(item["asset_id"] == first["asset_id"] and item["version"] == first["version"] for item in stage_references)


def test_30_keeps_metadata_candidates_out_of_unrelated_business_stages(
    modules: dict[str, ModuleType],
    sample_contracts: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    """Keyword overlap must not make a mail Flow look like approval/publish reuse.

    The sample email report Flow contains the generic word "업무보고".  It
    is a relevant *mail collection* reference, but it has no approved
    metadata evidence for draft, approval, publication, or result delivery.
    Those stages must remain unassigned rather than receiving a misleading
    catalog recommendation.
    """

    view_model = build_view_model(modules, sample_contracts)
    report = view_model["sections"][0]["items"][0]
    references = report["implementation_allocation"]["stage_catalog_references"]
    email_flow_id = "47d41a8d-9208-48c2-b79b-9d84d7ce199d"
    email_references = [item for item in references if item["asset_id"] == email_flow_id]

    assert [item["stage_title"] for item in email_references] == ["Outlook 업무 메일 수집"]
    assert email_references[0]["matched_catalog_terms"] == ["mail", "email", "메일"]
    assert email_references[0]["match_basis"] == "메일·첨부 수집 관련 핵심 용어 mail, email, 메일 일치"
    assert email_references[0]["selection_status"].startswith("참고 후보(직접 적용 미확정)")
    assert email_references[0]["technical_contract_label"].startswith("메타데이터만 확인됨")
    assert not {
        "근거 연결형 보고서 초안 생성",
        "민감정보 및 내용 승인",
        "승인 보고서 게시",
        "보고서 링크 전달",
    } & {item["stage_title"] for item in email_references}

    # The Outlook schedule candidate is not silently discarded.  It remains
    # visible as a general candidate because its metadata alone does not
    # establish that it reads mail.
    candidates = report["implementation_allocation"]["catalog_candidates"]
    assert any(item["asset_id"] == "e21931b2-1093-4f32-b55a-36ac66ef5b59" for item in candidates)
    assert "직접 재사용 0건" in report["implementation_allocation"]["summary"]

    # The reader-facing roadmap is deliberately short.  Node/edge-specific
    # warnings remain available as a technical appendix rather than becoming
    # a wall of implementation steps in the main report.
    assert 3 <= len(report["next_steps"]) <= 5
    assert any(item["title"] == "카탈로그 참고 후보 적합성 확인" for item in report["next_steps"])
    assert any(item["title"] == "승인·예외 경로 검증" for item in report["next_steps"])
    technical_preflight = report["technical_preflight_actions"]
    assert len(technical_preflight) > len(report["next_steps"])
    assert any(item["reference"] == "보안 설정 필요" for item in technical_preflight)
    assert "mail_api_credential_ref" not in json.dumps(technical_preflight, ensure_ascii=False)


def test_30_builds_complete_deterministic_business_report_section(
    modules: dict[str, ModuleType],
    sample_contracts: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    """F30 must carry a complete business report, not only graph geometry."""

    view_model = build_view_model(modules, sample_contracts)
    assert view_model["sections"][0]["section_id"] == "business_report"
    report_section = view_model["sections"][0]
    assert report_section["title"] == "업무 방식 및 개선 실행 보고서"
    assert len(report_section["items"]) == 1
    report = report_section["items"][0]
    assert report["report_type"] == "business_report"
    assert report["report_version"] == "business-report/v1"
    assert report["work_overview"]["goal"] == (
        "지난 한 주의 업무 메일을 근거로 주간 업무보고 초안을 만들고 담당자 검토 후 공유한다."
    )
    assert report["work_overview"]["trigger"] == "매주 금요일 15시 또는 담당자의 수동 실행"
    assert [item["title"] for item in report["operating_context"]["actors"]] == ["업무 담당자", "팀 리더"]
    assert [item["title"] for item in report["as_is_analysis"]["procedure"]] == [
        "금요일 보고 준비 시작",
        "Outlook 메일 수동 검색",
        "업무보고 수동 작성",
        "민감정보와 내용 검토",
        "업무보고 공유 완료",
    ]
    assert report["as_is_analysis"]["risks_controls"][0]["control"] == "게시 전 담당자 승인과 민감정보 확인을 필수화"
    assert report["improvement_direction"]["objectives"]
    assert report["to_be_operating_plan"]["human_review_points"][0]["title"] == "민감정보 및 내용 승인"
    assert {item["component_filename"] for item in report["implementation_allocation"]["new_standalone_components"]} == {
        "40_outlook_mail_collector.py",
        "41_evidence_linked_report_adapter.py",
    }
    assert report["implementation_allocation"]["catalog_candidates"]
    assert report["next_steps"]
    assert report["validation_plan"]
    assert report["open_items"] == []
    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
    assert "request_text" not in serialized
    assert "mail_api_credential_ref" not in serialized


def test_30_business_report_marks_missing_facts_as_needing_confirmation(
    modules: dict[str, ModuleType],
) -> None:
    module = modules["30_report_view_model_builder"]
    report_section = module._business_report_section(
        work={},
        blueprint={"readiness_assessment": {}},
        as_is_graph={},
        to_be_graph={},
        readiness="design_only",
        catalog_section=None,
    )
    report = report_section["items"][0]
    assert report["work_overview"]["goal"] == "미확정/추가 확인 필요"
    assert report["work_overview"]["trigger"] == "미확정/추가 확인 필요"
    assert report["as_is_analysis"]["procedure"][0]["title"] == "미확정/추가 확인 필요"
    assert report["to_be_operating_plan"]["recommended_procedure"][0]["title"] == "미확정/추가 확인 필요"
    assert {item["title"] for item in report["open_items"]} >= {"업무 목표 확인", "실행 시점 확인", "자동화 의도 확인"}


def render_view_model(modules: dict[str, ModuleType], view_model: dict[str, Any]) -> dict[str, Any]:
    component = modules["31_responsive_report_renderer"].ResponsiveReportRendererComponent(
        report_view_model=view_model,
        renderer_version="business-report-renderer.v1",
        allowed_hosts_json='["localhost"]',
        max_nodes=500,
        max_edges=1000,
        max_html_bytes=10_000_000,
    )
    return dict(component.render_report().data)


def reseal_view_model(view_model: dict[str, Any]) -> dict[str, Any]:
    """Recompute the capability identity after an intentional test mutation."""
    model = copy.deepcopy(view_model)
    model.pop("report_id", None)
    digest = hashlib.sha256(
        json.dumps(model, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    model["report_id"] = "report-" + digest[:24]
    return model


def canonical_hash(value: Any) -> str:
    material = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def terminal_with_authoritative_candidate_ports() -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a terminal envelope and trace sealed over the executable port contract."""
    candidate_context = read_json("candidate_context.json")
    terminal = read_json("agent_blueprint_terminal.json")
    trace = copy.deepcopy(candidate_context["retrieval_trace"])
    port_contract = {
        "inputs": [
            {
                "port_id": "request",
                "name": "request",
                "data_type": "Data",
                "semantic_role": "mail_query",
                "schema_ref": "",
                "cardinality": "one",
                "required": True,
                "has_default": False,
                "secret": False,
                "permission": "",
                "network_zone": "",
                "streaming": False,
            }
        ],
        "outputs": [
            {
                "port_id": "messages",
                "name": "messages",
                "data_type": "Data",
                "semantic_role": "mail_documents",
                "schema_ref": "",
                "cardinality": "one",
                "required": True,
                "has_default": False,
                "secret": False,
                "permission": "",
                "network_zone": "",
                "streaming": False,
            }
        ],
    }
    port_contract_sha256 = canonical_hash(port_contract)
    contract_items: list[dict[str, Any]] = []
    for index, item in enumerate(trace["candidate_allowlist"]):
        item_port_hash = port_contract_sha256 if index == 0 else canonical_hash({"inputs": [], "outputs": []})
        contract_items.append({**item, "port_contract_sha256": item_port_hash})
    trace["candidate_allowlist"] = contract_items
    trace["candidate_allowlist_sha256"] = canonical_hash(contract_items)
    contract_hashes = {
        (item["asset_id"], item["version"]): item["port_contract_sha256"]
        for item in contract_items
    }
    # ``catalog_presentation`` is display-only, but it must keep the same
    # port-contract binding as the executable allowlist it explains.
    trace["catalog_presentation"] = [
        {**item, "port_contract_sha256": contract_hashes[(item["asset_id"], item["version"])]}
        for item in trace.get("catalog_presentation", [])
        if isinstance(item, dict) and (item.get("asset_id"), item.get("version")) in contract_hashes
    ]
    terminal["blueprint"]["candidate_allowlist_sha256"] = trace["candidate_allowlist_sha256"]

    allowed = contract_items[0]
    catalog_node = copy.deepcopy(terminal["blueprint"]["nodes"][0])
    catalog_node.update(
        node_id="catalog-flow-isolated",
        node_type="task",
        title="승인된 카탈로그 Flow",
        responsibility="승인 후보에 봉인된 Flow를 호출한다.",
        implementation_source="catalog_flow",
        implementation_label="기존 Flow",
        reuse_decision_reason="승인된 후보 계약을 그대로 재사용한다.",
        asset_ref={"asset_id": allowed["asset_id"], "version": allowed["version"]},
        technical_contract_status=allowed["technical_contract_status"],
        runtime_validation_status="unverified",
        inputs=copy.deepcopy(port_contract["inputs"]),
        outputs=copy.deepcopy(port_contract["outputs"]),
        port_contract_sha256=port_contract_sha256,
        generation_contract=None,
        applied_skills=[],
    )
    terminal["blueprint"]["nodes"].append(catalog_node)
    terminal["blueprint"]["build_readiness"] = "design_only"
    terminal["blueprint"]["readiness_assessment"]["blockers"] = [
        {"code": "CATALOG_TECHNICAL_CONTRACT_BLOCKED", "ref": catalog_node["node_id"]}
    ]
    return terminal, trace


def sha256_csp(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return "sha256-" + base64.b64encode(digest).decode("ascii")


def test_report_component_sources_build_as_langflow_111_standalone_components() -> None:
    from lfx.custom.custom_component.component import Component as SourceComponent
    from lfx.custom.utils import build_custom_component_template

    expected_names = {
        "29_search_query_embedding_batcher": "SearchQueryEmbeddingBatcher",
        "30_report_view_model_builder": "ReportViewModelBuilder",
        "31_responsive_report_renderer": "ResponsiveReportRenderer",
        "32_report_publisher": "ReportPublisher",
        "37_report_publication_message": "ReportPublicationMessage",
    }
    for path in COMPONENT_PATHS:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        component_classes = [
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and any(isinstance(base, ast.Name) and base.id == "Component" for base in node.bases)
        ]
        assert len(component_classes) == 1, path
        assert "from lfx.custom import Component" in source
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.level == 0, path
                assert not (node.module or "").startswith("langflow"), path
        template, instance = build_custom_component_template(SourceComponent(_code=source))
        assert instance.name == expected_names[path.stem]
        assert template["template"]["code"]["value"] == source
        assert len(template["outputs"]) == 1
        assert all(item["method"] for item in template["outputs"])


def test_sample_work_blueprint_and_report_view_model_validate_against_schemas(
    modules: dict[str, ModuleType],
    sample_contracts: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    work, blueprint, candidate_context = sample_contracts
    pairs = [
        (work, "work_definition.schema.json"),
        (blueprint, "agent_blueprint.schema.json"),
    ]
    for instance, schema_name in pairs:
        schema = json.loads((PROJECT_ROOT / "schemas" / schema_name).read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(instance)
    assert work["status"] == "APPROVED"
    assert work["approved_hash"] == blueprint["approved_hash"]
    allowed_assets = {item["asset_id"] for item in candidate_context["candidate_allowlist"]}
    used_assets = {
        node["asset_ref"]["asset_id"]
        for node in blueprint["nodes"]
        if isinstance(node.get("asset_ref"), dict)
    }
    assert used_assets <= allowed_assets

    view_model = build_view_model(modules, sample_contracts)
    schema = json.loads((PROJECT_ROOT / "schemas" / "report_view_model.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(view_model)
    too_many_nodes = copy.deepcopy(view_model)
    too_many_nodes["to_be_graph"]["nodes"] = [
        copy.deepcopy(view_model["to_be_graph"]["nodes"][0]) for _ in range(2001)
    ]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(too_many_nodes)

    wrong_graph_kind = copy.deepcopy(view_model)
    wrong_graph_kind["as_is_graph"]["graph_kind"] = "to_be"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(wrong_graph_kind)

    oversized_node_id = copy.deepcopy(view_model)
    oversized_node_id["to_be_graph"]["nodes"][0]["node_id"] = "n" * 129
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(oversized_node_id)

    empty_edge_label = copy.deepcopy(view_model)
    empty_edge_label["to_be_graph"]["edges"][0]["label"] = ""
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(empty_edge_label)


def test_30_builds_reference_complete_as_is_and_to_be_graphs(
    modules: dict[str, ModuleType],
    sample_contracts: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    view_model = build_view_model(modules, sample_contracts)
    assert view_model["schema_version"] == "report_view_model.v1"
    assert view_model["summary"]["approval_status"] == "APPROVED"
    assert view_model["summary"]["build_readiness"] == "proposed_unverified"
    assert view_model["retrieval_trace"]["silent_fallback_used"] is False

    for graph in (view_model["as_is_graph"], view_model["to_be_graph"]):
        node_ids = {node["node_id"] for node in graph["nodes"]}
        port_ids = {
            port["port_id"]
            for node in graph["nodes"]
            for port in node["input_ports"] + node["output_ports"]
        }
        assert len(node_ids) == len(graph["nodes"])
        for node in graph["nodes"]:
            assert node["detail_ref"] in graph["details"]
            detail = graph["details"][node["detail_ref"]]
            assert detail["current_work"]
            assert detail["improvement"]
            if node["generation_request_ref"]:
                assert node["implementation_source"] == "new_standalone_component"
                assert node["generation_request_ref"] in graph["generation_requests"]
        for edge in graph["edges"]:
            assert edge["source_node_id"] in node_ids
            assert edge["target_node_id"] in node_ids
            assert edge["source_port_id"] is None or edge["source_port_id"] in port_ids
            assert edge["target_port_id"] is None or edge["target_port_id"] in port_ids
            assert edge["label"]

    custom = next(
        node
        for node in view_model["to_be_graph"]["nodes"]
        if node["implementation_source"] == "new_standalone_component"
    )
    request = view_model["to_be_graph"]["generation_requests"][custom["generation_request_ref"]]
    assert request["component_filename"].endswith(".py")
    assert "standalone" in request["request_text"].casefold()
    skilled_custom = next(
        node
        for node in view_model["to_be_graph"]["nodes"]
        if node["implementation_source"] == "new_standalone_component" and node["applied_skills"]
    )
    assert skilled_custom["applied_skills"][0]["source_ref"] == "approved-skill-registry"


def test_30_report_identity_changes_when_blueprint_content_changes(
    modules: dict[str, ModuleType],
    sample_contracts: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    work, blueprint, candidate_context = copy.deepcopy(sample_contracts)
    first = modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
        work_definition=work,
        agent_blueprint=blueprint,
        retrieval_trace=candidate_context["retrieval_trace"],
        report_title="설계 보고서",
        max_nodes=500,
        max_edges=1000,
    ).build_report_view_model().data
    changed = copy.deepcopy(blueprint)
    changed["pattern_reason"] = str(changed.get("pattern_reason") or "") + " 다른 추가 설계 프롬프트 결과"
    second = modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
        work_definition=work,
        agent_blueprint=changed,
        retrieval_trace=candidate_context["retrieval_trace"],
        report_title="설계 보고서",
        max_nodes=500,
        max_edges=1000,
    ).build_report_view_model().data
    assert first["summary"]["blueprint_id"] == second["summary"]["blueprint_id"]
    assert first["summary"]["blueprint_sha256"] != second["summary"]["blueprint_sha256"]
    assert first["report_id"] != second["report_id"]


def test_30_rejects_empty_or_overstated_blueprint_and_forged_port_refs(
    modules: dict[str, ModuleType],
    sample_contracts: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    work, _, candidate_context = copy.deepcopy(sample_contracts)
    terminal = read_json("agent_blueprint_terminal.json")

    empty_terminal = copy.deepcopy(terminal)
    empty_blueprint = empty_terminal["blueprint"]
    empty_blueprint["nodes"] = []
    empty_blueprint["edges"] = []
    empty_blueprint["generation_requests"] = []
    empty_blueprint["to_be_graph"] = {"nodes": [], "edges": []}
    empty_blueprint["build_readiness"] = "import_ready"
    empty_blueprint["flow_import_verified"] = True
    empty_blueprint["readiness_assessment"] = {
        "status_axis": "build_readiness",
        "technical_status_axis": "technical_contract_status",
        "connection_status_axis": "connection_validation_status",
        "blockers": [],
        "warnings": [],
        "import_requirements": [],
        "flow_import_verified": True,
    }
    empty_terminal["generation_requests"] = []
    with pytest.raises(ValueError, match="nodes must contain"):
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=empty_terminal,
            retrieval_trace=candidate_context["retrieval_trace"],
        ).build_report_view_model()


def test_30_rejects_retrieval_trace_from_another_snapshot_or_scope(
    modules: dict[str, ModuleType],
    sample_contracts: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    work, _, candidate_context = copy.deepcopy(sample_contracts)
    terminal = read_json("agent_blueprint_terminal.json")
    mismatched = copy.deepcopy(candidate_context["retrieval_trace"])
    mismatched["snapshot_id"] = "snapshot-from-other-scope"
    with pytest.raises(ValueError, match="snapshot_id"):
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=terminal,
            retrieval_trace=mismatched,
        ).build_report_view_model()

    mismatched_revision = copy.deepcopy(candidate_context["retrieval_trace"])
    mismatched_revision["work_definition_revision"] = work["revision"] + 1
    with pytest.raises(ValueError, match="revision"):
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=terminal,
            retrieval_trace=mismatched_revision,
        ).build_report_view_model()

    overstated_terminal = copy.deepcopy(terminal)
    overstated = overstated_terminal["blueprint"]
    overstated["build_readiness"] = "import_ready"
    overstated["flow_import_verified"] = True
    overstated["readiness_assessment"]["flow_import_verified"] = True
    overstated["readiness_assessment"]["blockers"] = []
    overstated["readiness_assessment"]["import_requirements"] = []
    with pytest.raises(ValueError, match="build_readiness"):
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=overstated_terminal,
            retrieval_trace=candidate_context["retrieval_trace"],
        ).build_report_view_model()

    forged_terminal = copy.deepcopy(terminal)
    forged_terminal["blueprint"]["edges"][0]["source_port_id"] = "forged-port"
    with pytest.raises(ValueError, match="source_port_id"):
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=forged_terminal,
            retrieval_trace=candidate_context["retrieval_trace"],
        ).build_report_view_model()


def test_30_recomputes_approval_and_binds_schema_tenant_identity_and_revision(
    modules: dict[str, ModuleType],
    sample_contracts: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    work, blueprint, candidate_context = copy.deepcopy(sample_contracts)

    semantic_mutation = copy.deepcopy(work)
    semantic_mutation["goal"]["value"] = "승인 후 바뀐 목표"
    with pytest.raises(ValueError, match="approved_hash does not match"):
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=semantic_mutation,
            agent_blueprint=blueprint,
            retrieval_trace=candidate_context["retrieval_trace"],
        ).build_report_view_model()

    fake_hash_work = copy.deepcopy(work)
    fake_hash_blueprint = copy.deepcopy(blueprint)
    fake_hash_work["approved_hash"] = "sha256:" + "a" * 64
    fake_hash_blueprint["approved_hash"] = fake_hash_work["approved_hash"]
    with pytest.raises(ValueError, match="approved_hash does not match"):
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=fake_hash_work,
            agent_blueprint=fake_hash_blueprint,
            retrieval_trace={},
        ).build_report_view_model()

    mutations = [
        ("schema_version", "agent-blueprint.v0", "schema_version"),
        ("tenant_id", "tenant-other", "tenant_id"),
        ("work_definition_id", "wd-other", "work_definition_id"),
        ("work_definition_revision", work["revision"] + 1, "revision"),
    ]
    for field, value, message in mutations:
        invalid_blueprint = copy.deepcopy(blueprint)
        invalid_blueprint[field] = value
        with pytest.raises(ValueError, match=message):
            modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
                work_definition=work,
                agent_blueprint=invalid_blueprint,
                retrieval_trace={},
            ).build_report_view_model()

    work_header_mutations = [
        ({"revision": 4.9}, "revision binding"),
        ({"revision": "4"}, "revision binding"),
        ({"schema_version": "evil/v1"}, "schema_version"),
        ({"tenant_id": None}, "tenant_id"),
        ({"tenant_id": 123}, "tenant_id"),
        ({"work_definition_id": "bad id"}, "work_definition_id"),
    ]
    for mutation, message in work_header_mutations:
        invalid_work = copy.deepcopy(work)
        invalid_work.update(mutation)
        with pytest.raises(ValueError, match=message):
            modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
                work_definition=invalid_work,
                agent_blueprint=blueprint,
                retrieval_trace={},
            ).build_report_view_model()


def test_30_and_31_reject_non_json_and_non_finite_values(
    modules: dict[str, ModuleType],
    sample_contracts: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    work, blueprint, _ = copy.deepcopy(sample_contracts)
    with pytest.raises(ValueError, match="non-finite"):
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=blueprint,
            retrieval_trace={"score": float("nan")},
        ).build_report_view_model()

    non_json_blueprint = copy.deepcopy(blueprint)
    non_json_blueprint["pattern_reason"] = {"not-json"}
    with pytest.raises(ValueError, match="non-JSON"):
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=non_json_blueprint,
            retrieval_trace={},
        ).build_report_view_model()

    non_finite_view = read_json("report_view_model.json")
    non_finite_view["retrieval_trace"]["score"] = float("inf")
    with pytest.raises(ValueError, match="non-finite"):
        render_view_model(modules, non_finite_view)

    non_json_view = read_json("report_view_model.json")
    non_json_view["sections"][0]["items"] = {"not-json"}
    with pytest.raises(ValueError, match="non-JSON"):
        render_view_model(modules, non_json_view)


def test_30_and_31_artifact_identity_covers_every_stable_render_input(
    modules: dict[str, ModuleType],
    sample_contracts: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    first = build_view_model(modules, sample_contracts)
    replay = build_view_model(modules, sample_contracts)
    assert first == replay
    assert first["renderer_version"] == modules["31_responsive_report_renderer"].RENDERER_VERSION
    first_render = render_view_model(modules, first)
    replay_render = render_view_model(modules, replay)
    assert first_render["report_id"] == replay_render["report_id"]
    assert first_render["content_sha256"] == replay_render["content_sha256"]
    assert first_render["html"] == replay_render["html"]

    reordered_summary = copy.deepcopy(first)
    reordered_summary["summary"] = dict(reversed(list(reordered_summary["summary"].items())))
    assert render_view_model(modules, reordered_summary)["html"] == first_render["html"]

    work, blueprint, candidate_context = copy.deepcopy(sample_contracts)
    title_changed = modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
        work_definition=work,
        agent_blueprint=blueprint,
        retrieval_trace=candidate_context["retrieval_trace"],
        report_title="다른 보고서 제목",
        max_nodes=500,
        max_edges=1000,
    ).build_report_view_model().data
    changed_trace = copy.deepcopy(candidate_context["retrieval_trace"])
    changed_trace["evaluation_note"] = "다른 검색 근거"
    trace_changed = modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
        work_definition=work,
        agent_blueprint=blueprint,
        retrieval_trace=changed_trace,
        report_title="주간 업무보고 업무 방식 및 Agent 설계",
        max_nodes=500,
        max_edges=1000,
    ).build_report_view_model().data
    assert len({first["report_id"], title_changed["report_id"], trace_changed["report_id"]}) == 3

    incompatible = copy.deepcopy(first)
    incompatible["renderer_version"] = "business-report-renderer.v2"
    with pytest.raises(ValueError, match="renderer_version"):
        render_view_model(modules, incompatible)


def test_f20_terminal_envelope_hands_off_to_30_and_secret_values_are_redacted(
    modules: dict[str, ModuleType],
    sample_contracts: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    work, blueprint, candidate_context = copy.deepcopy(sample_contracts)
    secret_value = "sk-super-secret-value-1234567890"
    opaque_secret = "opaque-raw-password"
    bearer_secret = "Bearer top-secret-token-1234567890"
    blueprint["nodes"][0]["config"] = {
        "api_key": secret_value,
        "nested": {"Authorization": bearer_secret},
        "safe_mode": "bounded",
    }
    blueprint["nodes"][0]["title"] = bearer_secret
    blueprint["nodes"][0]["problems"] = [{"password": opaque_secret}]
    blueprint["nodes"][0]["human_review"] = {"token": opaque_secret}
    blueprint["nodes"][0]["tests"] = [{"Authorization": opaque_secret}]
    blueprint["nodes"][0]["secrets_permissions"] = [
        {"name": "mail_api_key", "secret": secret_value, "configured": True}
    ]
    blueprint["edges"][0]["mapping"] = {"api_key": opaque_secret}
    blueprint["edges"][0]["retry_policy"] = {"credential": opaque_secret}
    blueprint.setdefault("to_be_graph", {})["groups"] = [{"password": opaque_secret}]
    blueprint["tests"] = [{"secret": opaque_secret}]
    blueprint["unresolved"] = [{"credential": opaque_secret, "blocking": False}]
    trace = copy.deepcopy(candidate_context["retrieval_trace"])
    trace["credential"] = opaque_secret
    trace["clientSecret"] = opaque_secret
    trace["accessToken"] = opaque_secret
    trace["sessionCookie"] = opaque_secret
    terminal = read_json("agent_blueprint_terminal.json")
    terminal["blueprint"] = blueprint
    terminal["generation_requests"] = blueprint["generation_requests"]
    component = modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
        work_definition={"ok": True, "work_definition": work},
        agent_blueprint=terminal,
        retrieval_trace=trace,
        report_title="설계 보고서",
        max_nodes=500,
        max_edges=1000,
    )
    view_model = dict(component.build_report_view_model().data)
    serialized = json.dumps(view_model, ensure_ascii=False)
    assert secret_value not in serialized
    assert opaque_secret not in serialized
    assert bearer_secret not in serialized
    assert "[REDACTED]" in serialized
    assert "bounded" in serialized
    rendered = render_view_model(modules, view_model)
    assert secret_value not in rendered["html"]

    with pytest.raises(ValueError, match="upstream envelope"):
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition={"ok": False, "status": "BLOCKED"},
            agent_blueprint={"ok": True, "blueprint": blueprint},
            retrieval_trace={},
            report_title="설계 보고서",
            max_nodes=500,
            max_edges=1000,
        ).build_report_view_model()


def test_30_rejects_secret_material_inside_generation_request(
    modules: dict[str, ModuleType],
    sample_contracts: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    work, _, candidate_context = copy.deepcopy(sample_contracts)
    terminal = read_json("agent_blueprint_terminal.json")
    request = terminal["blueprint"]["generation_requests"][0]
    request["request_text"] += "\nBearer top-secret-token-1234567890"
    request_digest = hashlib.sha256(request["request_text"].encode("utf-8")).hexdigest()
    request["prompt_sha256"] = "sha256:" + request_digest
    old_request_id = request["generation_request_id"]
    request["generation_request_id"] = "gen-" + request_digest[:20]
    for node in terminal["blueprint"]["nodes"]:
        if node.get("generation_request_ref") == old_request_id:
            node["generation_request_ref"] = request["generation_request_id"]
    terminal["generation_requests"] = terminal["blueprint"]["generation_requests"]

    with pytest.raises(ValueError, match="generation request contains secret material"):
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=terminal,
            retrieval_trace=candidate_context["retrieval_trace"],
        ).build_report_view_model()


@pytest.mark.parametrize(
    "secret_text",
    [
        "Basic dXNlcjpwYXNzd29yZA==",
        "-----BEGIN PRIVATE KEY-----",
    ],
)
def test_30_rejects_additional_generation_request_secret_formats(
    modules: dict[str, ModuleType],
    sample_contracts: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
    secret_text: str,
) -> None:
    work, _, candidate_context = copy.deepcopy(sample_contracts)
    terminal = read_json("agent_blueprint_terminal.json")
    request = terminal["blueprint"]["generation_requests"][0]
    request["request_text"] += "\n" + secret_text
    request_digest = hashlib.sha256(request["request_text"].encode("utf-8")).hexdigest()
    request["prompt_sha256"] = "sha256:" + request_digest
    old_request_id = request["generation_request_id"]
    request["generation_request_id"] = "gen-" + request_digest[:20]
    for node in terminal["blueprint"]["nodes"]:
        if node.get("generation_request_ref") == old_request_id:
            node["generation_request_ref"] = request["generation_request_id"]
    terminal["generation_requests"] = terminal["blueprint"]["generation_requests"]

    with pytest.raises(ValueError, match="generation request contains secret material"):
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=terminal,
            retrieval_trace=candidate_context["retrieval_trace"],
        ).build_report_view_model()


def test_30_rejects_uppercase_generation_hash_and_duplicate_ports(
    modules: dict[str, ModuleType],
    sample_contracts: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    work, _, candidate_context = copy.deepcopy(sample_contracts)
    uppercase_hash = read_json("agent_blueprint_terminal.json")
    request = uppercase_hash["blueprint"]["generation_requests"][0]
    request["prompt_sha256"] = request["prompt_sha256"].upper()
    uppercase_hash["generation_requests"] = uppercase_hash["blueprint"]["generation_requests"]
    with pytest.raises(ValueError, match="generation request integrity"):
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=uppercase_hash,
            retrieval_trace=candidate_context["retrieval_trace"],
        ).build_report_view_model()

    duplicate_ports = read_json("agent_blueprint_terminal.json")
    duplicated_port = copy.deepcopy(duplicate_ports["blueprint"]["nodes"][0]["outputs"][0])
    duplicated_port["port_id"] = "same"
    duplicate_ports["blueprint"]["nodes"][0]["outputs"] = [
        duplicated_port,
        copy.deepcopy(duplicated_port),
    ]
    with pytest.raises(ValueError, match="duplicate port id"):
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=duplicate_ports,
            retrieval_trace=candidate_context["retrieval_trace"],
        ).build_report_view_model()

    used_ids: set[str] = set()
    long_node_id = "n" * 128
    input_port = modules["30_report_view_model_builder"]._ports(
        long_node_id, [{"name": "input"}], "in", used_ids
    )[0]["port_id"]
    output_port = modules["30_report_view_model_builder"]._ports(
        long_node_id, [{"name": "output"}], "out", used_ids
    )[0]["port_id"]
    assert input_port != output_port
    assert len(input_port) <= 128 and len(output_port) <= 128


def test_generation_request_maximum_names_pass_builder_and_renderer(
    modules: dict[str, ModuleType],
    sample_contracts: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    work, _, candidate_context = copy.deepcopy(sample_contracts)
    terminal = read_json("agent_blueprint_terminal.json")
    request = terminal["blueprint"]["generation_requests"][0]
    request_id = request["generation_request_id"]
    filename = "12_a" + ("b" * 80) + ".py"
    class_name = "A" + ("B" * 100) + "Component"
    target = next(
        node for node in terminal["blueprint"]["nodes"]
        if node.get("generation_request_ref") == request_id
    )
    target["generation_contract"]["component_filename"] = filename
    target["generation_contract"]["class_name"] = class_name
    request["component_filename"] = filename
    request["class_name"] = class_name
    request["request_text"] = modules["30_report_view_model_builder"]._expected_generation_request_text(
        target["generation_contract"], target["node_id"], terminal["blueprint"]
    )
    digest = hashlib.sha256(request["request_text"].encode("utf-8")).hexdigest()
    request["prompt_sha256"] = "sha256:" + digest
    request["generation_request_id"] = "gen-" + digest[:20]
    target["generation_request_ref"] = request["generation_request_id"]
    terminal["generation_requests"] = terminal["blueprint"]["generation_requests"]

    view_model = dict(
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=terminal,
            retrieval_trace=candidate_context["retrieval_trace"],
        ).build_report_view_model().data
    )
    clean_request = view_model["to_be_graph"]["generation_requests"][request["generation_request_id"]]
    assert len(clean_request["component_filename"]) == 87
    assert len(clean_request["class_name"]) == 110
    assert render_view_model(modules, view_model)["status"] == "RENDERED"


def test_30_rejects_tampered_generation_request_integrity(
    modules: dict[str, ModuleType],
    sample_contracts: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    work, _, candidate_context = copy.deepcopy(sample_contracts)
    terminal = read_json("agent_blueprint_terminal.json")
    terminal["blueprint"]["generation_requests"][0]["request_text"] += "tampered"
    terminal["generation_requests"] = terminal["blueprint"]["generation_requests"]
    with pytest.raises(ValueError, match="generation request integrity"):
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=terminal,
            retrieval_trace=candidate_context["retrieval_trace"],
        ).build_report_view_model()


def test_30_rejects_report_schema_parity_violations_and_missing_custom_prompt(
    modules: dict[str, ModuleType],
    sample_contracts: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    work, _, candidate_context = copy.deepcopy(sample_contracts)

    negative_sequence = read_json("agent_blueprint_terminal.json")
    negative_sequence["blueprint"]["nodes"][0]["sequence"] = -1
    with pytest.raises(ValueError, match="sequence must be a non-negative integer"):
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=negative_sequence,
            retrieval_trace=candidate_context["retrieval_trace"],
        ).build_report_view_model()

    bad_skill = read_json("agent_blueprint_terminal.json")
    skill_node = next(node for node in bad_skill["blueprint"]["nodes"] if node.get("applied_skills"))
    skill_node["applied_skills"][0]["prompt_sha256"] = "not-a-hash"
    with pytest.raises(ValueError, match="prompt_sha256"):
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=bad_skill,
            retrieval_trace=candidate_context["retrieval_trace"],
        ).build_report_view_model()

    tampered_source = read_json("agent_blueprint_terminal.json")
    skill_node = next(node for node in tampered_source["blueprint"]["nodes"] if node.get("applied_skills"))
    secret_source = "api_key=do-not-echo-this-secret"
    skill_node["applied_skills"][0]["source_ref"] = secret_source
    with pytest.raises(ValueError, match="source_ref is invalid") as source_error:
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=tampered_source,
            retrieval_trace=candidate_context["retrieval_trace"],
        ).build_report_view_model()
    assert secret_source not in str(source_error.value)

    unknown_skill_field = read_json("agent_blueprint_terminal.json")
    skill_node = next(
        node for node in unknown_skill_field["blueprint"]["nodes"] if node.get("applied_skills")
    )
    secret_field = "api_key=do-not-echo-this-field"
    skill_node["applied_skills"][0][secret_field] = "opaque-secret-value"
    with pytest.raises(ValueError, match="shape is invalid") as shape_error:
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=unknown_skill_field,
            retrieval_trace=candidate_context["retrieval_trace"],
        ).build_report_view_model()
    assert secret_field not in str(shape_error.value)
    assert "opaque-secret-value" not in str(shape_error.value)

    tampered_identity = read_json("agent_blueprint_terminal.json")
    skill_node = next(node for node in tampered_identity["blueprint"]["nodes"] if node.get("applied_skills"))
    skill_node["applied_skills"][0]["skill_id"] = {"forged": "identity"}
    with pytest.raises(ValueError, match="required string field"):
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=tampered_identity,
            retrieval_trace=candidate_context["retrieval_trace"],
        ).build_report_view_model()

    scalar_group = read_json("agent_blueprint_terminal.json")
    scalar_group["blueprint"].setdefault("to_be_graph", {})["groups"] = ["invalid-group"]
    with pytest.raises(ValueError, match="group 0 must be an object"):
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=scalar_group,
            retrieval_trace=candidate_context["retrieval_trace"],
        ).build_report_view_model()

    missing_prompt = read_json("agent_blueprint_terminal.json")
    custom_node = next(
        node for node in missing_prompt["blueprint"]["nodes"]
        if node.get("implementation_source") == "new_standalone_component"
    )
    request_ref = custom_node.pop("generation_request_ref")
    missing_prompt["blueprint"]["generation_requests"] = [
        item for item in missing_prompt["blueprint"]["generation_requests"]
        if item.get("generation_request_id") != request_ref
    ]
    missing_prompt["generation_requests"] = missing_prompt["blueprint"]["generation_requests"]
    with pytest.raises(ValueError, match="requires generation_request_ref"):
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=missing_prompt,
            retrieval_trace=candidate_context["retrieval_trace"],
        ).build_report_view_model()


def test_31_rejects_schema_invalid_models_even_with_resealed_report_id(
    modules: dict[str, ModuleType],
) -> None:
    negative_sequence = read_json("report_view_model.json")
    negative_sequence["to_be_graph"]["nodes"][0]["sequence"] = -1
    with pytest.raises(ValueError, match="sequence"):
        render_view_model(modules, reseal_view_model(negative_sequence))

    missing_title = read_json("report_view_model.json")
    missing_title["to_be_graph"]["nodes"][0].pop("title")
    with pytest.raises(ValueError, match="missing required fields"):
        render_view_model(modules, reseal_view_model(missing_title))

    bad_skill = read_json("report_view_model.json")
    skill_node = next(node for node in bad_skill["to_be_graph"]["nodes"] if node.get("applied_skills"))
    skill_node["applied_skills"][0]["prompt_sha256"] = "not-a-hash"
    with pytest.raises(ValueError, match="prompt_sha256"):
        render_view_model(modules, reseal_view_model(bad_skill))

    scalar_group = read_json("report_view_model.json")
    scalar_group["to_be_graph"]["groups"] = ["invalid-group"]
    with pytest.raises(ValueError, match="must be an object"):
        render_view_model(modules, reseal_view_model(scalar_group))

    orphan_request = read_json("report_view_model.json")
    custom_node = next(
        node for node in orphan_request["to_be_graph"]["nodes"]
        if node.get("implementation_source") == "new_standalone_component"
    )
    custom_node["generation_request_ref"] = None
    with pytest.raises(ValueError, match="generation request ref"):
        render_view_model(modules, reseal_view_model(orphan_request))


def test_31_rejects_unknown_fields_and_unredacted_secret_material(
    modules: dict[str, ModuleType],
) -> None:
    unknown = read_json("report_view_model.json")
    unknown["unexpected_field"] = "ordinary-value"
    unknown = reseal_view_model(unknown)
    with pytest.raises(ValueError, match="unsupported fields"):
        render_view_model(modules, unknown)

    secret_unknown = read_json("report_view_model.json")
    secret_unknown["api_key"] = "opaque-raw-password"
    secret_unknown = reseal_view_model(secret_unknown)
    with pytest.raises(ValueError, match="REPORT_SECRET_MATERIAL_DETECTED"):
        render_view_model(modules, secret_unknown)

    nested = read_json("report_view_model.json")
    nested["retrieval_trace"]["accessToken"] = "opaque-raw-password"
    nested = reseal_view_model(nested)
    with pytest.raises(ValueError, match="unredacted secret"):
        render_view_model(modules, nested)


@pytest.mark.parametrize(
    "allowed_hosts_json",
    [
        '["Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ=="]',
        '["https://reports.example.com"]',
        '["reports.example.com:8443"]',
        '["reports.example.com", "reports.example.com"]',
        '["999.1.1.1"]',
    ],
)
def test_31_rejects_secret_or_non_host_allowed_host_values(
    modules: dict[str, ModuleType], allowed_hosts_json: str
) -> None:
    component = modules["31_responsive_report_renderer"].ResponsiveReportRendererComponent(
        report_view_model=read_json("report_view_model.json"),
        renderer_version="business-report-renderer.v1",
        allowed_hosts_json=allowed_hosts_json,
        max_nodes=500,
        max_edges=1000,
        max_html_bytes=10_000_000,
    )
    with pytest.raises(ValueError) as exc_info:
        component.render_report()
    assert "QWxhZGRpbjpvcGVuIHNlc2FtZQ" not in str(exc_info.value)


def test_report_pipeline_never_echoes_secret_literals_embedded_in_mapping_keys(
    modules: dict[str, ModuleType],
    sample_contracts: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    secret_key = "api_key=abcdefghijklmnop"
    work, _, candidate_context = copy.deepcopy(sample_contracts)
    trace = copy.deepcopy(candidate_context["retrieval_trace"])
    trace[secret_key] = "x"
    terminal = read_json("agent_blueprint_terminal.json")
    view_model = dict(
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=terminal,
            retrieval_trace=trace,
        ).build_report_view_model().data
    )
    serialized = json.dumps(view_model, ensure_ascii=False)
    assert secret_key not in serialized
    assert "redacted_key_" in serialized
    assert render_view_model(modules, view_model)["status"] == "RENDERED"

    malicious = read_json("report_view_model.json")
    malicious["retrieval_trace"][secret_key] = "x"
    malicious = reseal_view_model(malicious)
    with pytest.raises(ValueError, match="REPORT_SECRET_MATERIAL_DETECTED") as exc_info:
        render_view_model(modules, malicious)
    assert secret_key not in str(exc_info.value)

    non_json_secret_key = "api_key=DO-NOT-ECHO-THIS-SECRET"
    with pytest.raises(ValueError) as non_json_error:
        modules["30_report_view_model_builder"]._ensure_json_value(
            {non_json_secret_key: object()}, "agent_blueprint"
        )
    assert non_json_secret_key not in str(non_json_error.value)


def test_committed_view_model_and_html_are_reproducible_from_30_to_31(
) -> None:
    """The committed F30 samples must reproduce the actual handoff path.

    ``business_report`` deliberately includes approved WorkDefinition details
    that are not part of the old independent candidate-context fixture.  The
    canonical sample path is therefore the same sealed F20 handoff used by
    ``scripts/render_sample_report.py``, rather than a reconstructed mix of
    nearby sample files.
    """

    script = load_component(PROJECT_ROOT / "scripts" / "render_sample_report.py")
    view_model, rendered = script.build_sample_artifacts()
    assert view_model == read_json("report_view_model.json")
    assert rendered["html"] == (SAMPLES_DIR / "generated_sample_report.html").read_text(encoding="utf-8")


def test_31_returns_self_contained_hash_and_csp_verifiable_html(
    modules: dict[str, ModuleType],
) -> None:
    view_model = read_json("report_view_model.json")
    rendered = render_view_model(modules, view_model)
    document = rendered["html"]
    assert document.startswith("<!doctype html>")
    assert rendered["content_sha256"] == "sha256:" + hashlib.sha256(document.encode("utf-8")).hexdigest()
    assert rendered["byte_count"] == len(document.encode("utf-8"))
    assert not re.search(r"<(?:script|img|link)[^>]+(?:src|href)=[\"'](?:https?:)?//", document, re.I)
    assert "<script src=" not in document.lower()
    assert "<link " not in document.lower()

    style_match = re.search(r"<style>(.*?)</style>", document, re.S)
    scripts = re.findall(r"<script(?: [^>]*)?>(.*?)</script>", document, re.S)
    assert style_match and len(scripts) == 2
    assert rendered["style_csp_hash"] == sha256_csp(style_match.group(1))
    assert rendered["script_csp_hash"] == sha256_csp(scripts[1])
    assert json.loads(scripts[0])["report_id"] == view_model["report_id"]
    assert rendered["accessibility_summary"] == {
        "keyboard_node_selection": True,
        "focusable_edge_labels": True,
        "reduced_motion": True,
        "text_fallback": True,
        "print_expanded": True,
    }
    assert "검색 근거와 snapshot trace" in document


def test_31_accepts_branch_and_merge_graphs_for_interactive_layout(
    modules: dict[str, ModuleType],
) -> None:
    """A report must keep a real parallel branch visible, not flatten it to a list."""

    view_model = read_json("report_view_model.json")
    graph = view_model["to_be_graph"]
    mail_collector = next(node for node in graph["nodes"] if node["node_id"] == "mail-collector")
    human_approval = next(node for node in graph["nodes"] if node["node_id"] == "human-approval")
    risk_input = copy.deepcopy(mail_collector["output_ports"][0])
    risk_input.update(
        {
            "port_id": "risk-check:in:mail-messages",
            "label": "메일 원문",
            "name": "mail-messages",
            "semantic_role": "mail_documents",
        }
    )
    risk_output = copy.deepcopy(risk_input)
    risk_output.update(
        {
            "port_id": "risk-check:out:risk-status",
            "source_port_id": "risk-status",
            "label": "점검 결과",
            "name": "risk-status",
            "semantic_role": "risk_check_result",
        }
    )
    risk_node = {
        "node_id": "risk-check",
        "source_node_id": "risk-check",
        "node_kind": "decision",
        "title": "민감정보·누락 병렬 점검",
        "sequence": 3,
        "implementation_source": "builtin",
        "implementation_label": "기본 요소",
        "technical_contract_status": None,
        "port_contract_sha256": None,
        "summary": "보고서 초안 생성과 병렬로 민감정보와 누락 가능성을 점검합니다.",
        "input_ports": [risk_input],
        "output_ports": [risk_output],
        "applied_skills": [],
        "detail_ref": "detail-risk-check",
        "generation_request_ref": None,
    }
    graph["nodes"].append(risk_node)
    graph["details"]["detail-risk-check"] = {
        "title": risk_node["title"],
        "current_work": "담당자가 초안을 만든 뒤 별도로 민감정보와 누락을 확인한다.",
        "problems": ["점검이 늦어지면 승인 단계에서 재작업이 발생한다."],
        "improvement": "초안 생성과 병렬로 점검 결과를 모아 승인자가 함께 판단한다.",
        "reuse_decision_reason": "현재 업무 요구에는 Langflow 기본 분기 요소로 충분하다.",
        "asset_ref": None,
        "inputs": copy.deepcopy(risk_node["input_ports"]),
        "outputs": copy.deepcopy(risk_node["output_ports"]),
        "config": {},
        "secrets_permissions": [],
        "failure_policy": {},
        "human_review": None,
        "tests": [],
        "applied_skills": [],
    }
    graph["edges"].extend(
        [
            {
                "edge_id": "agent-e-risk-branch",
                "source_node_id": "mail-collector",
                "source_port_id": mail_collector["output_ports"][0]["port_id"],
                "target_node_id": "risk-check",
                "target_port_id": risk_input["port_id"],
                "edge_kind": "branch",
                "label": "병렬 점검",
                "condition": None,
                "is_default": False,
                "connection_validation_status": "contract_compatible",
                "mapping": {},
                "retry_policy": {},
            },
            {
                "edge_id": "agent-e-risk-merge",
                "source_node_id": "risk-check",
                "source_port_id": risk_output["port_id"],
                "target_node_id": "human-approval",
                "target_port_id": human_approval["input_ports"][0]["port_id"],
                "edge_kind": "branch",
                "label": "이슈가 있으면 함께 검토",
                "condition": "risk_found == true",
                "is_default": False,
                "connection_validation_status": "contract_compatible",
                "mapping": {},
                "retry_policy": {},
            },
        ]
    )
    rendered = render_view_model(modules, reseal_view_model(view_model))
    assert rendered["status"] == "RENDERED"
    assert "function graphLayout" in rendered["html"]
    assert ".edge-path.edge-branch" in rendered["html"]
    assert "카탈로그 기반 적용 계획" in rendered["html"]
    assert "function renderCatalog" in rendered["html"]
    assert "민감정보·누락 병렬 점검" in rendered["html"]


def test_31_renders_complete_business_narrative_before_flow_visuals(
    modules: dict[str, ModuleType],
) -> None:
    """The reader gets a Korean work-design report, not only a graph canvas."""

    view_model = read_json("report_view_model.json")
    view_model["sections"].append(
        {
            "section_id": "business_report",
            "title": "주간 업무보고 완성 업무 설계 보고서",
            "items": [
                {
                    "report_type": "business_report",
                    "report_version": "business-report/v1",
                    "executive_summary": {
                        "title": "주간 업무보고 자동화 설계",
                        "overview": "메일 수집부터 팀장 공유까지의 업무를 승인 가능한 Agent 운영 방식으로 정리합니다.",
                        "approval_basis": "업무 담당자 검토 후 승인",
                        "build_readiness": "proposed_unverified",
                    },
                    "work_overview": {
                        "goal": "금요일 오후까지 근거가 연결된 주간 업무보고 초안을 만듭니다.",
                        "trigger": "매주 금요일 14:00",
                        "scope_in": ["프로젝트 업무 메일", "승인된 보고 포털 게시"],
                        "scope_out": ["승인 없는 자동 게시"],
                    },
                    "operating_context": {
                        "actors": ["업무 담당자", "팀장"],
                        "systems": ["Outlook", "사내 보고 포털"],
                        "inputs": ["업무 메일", "프로젝트 분류 기준"],
                        "outputs": ["검토 가능한 주간 업무보고 초안"],
                    },
                    "as_is_analysis": {
                        "procedure": ["메일 검색", "프로젝트별 수작업 분류", "보고서 초안 작성"],
                        "pain_points": ["중복 메일과 자동 알림을 다시 확인해야 합니다."],
                        "decision_points": ["민감정보 포함 여부", "팀장 승인 여부"],
                        "exception_paths": ["메일 조회 실패 시 게시를 중단합니다."],
                    },
                    "improvement_direction": {
                        "summary": "반복 정리는 Agent가 수행하고, 게시 승인과 민감정보 판단은 사람이 담당합니다.",
                        "objectives": ["근거 메일 링크 보존", "재작업 감소"],
                        "principles": ["승인 전 게시 금지", "실패 건수 표시"],
                    },
                    "to_be_operating_plan": {
                        "recommended_procedure": ["메일 수집", "분류·초안 생성", "민감정보 점검", "담당자 승인", "게시·팀장 공유"],
                        "branch_and_exception_plan": ["조회 실패 또는 인증 만료 시 게시하지 않고 실패 원인을 표시"],
                        "human_review_points": ["담당자가 누락·민감정보·게시 여부를 최종 승인"],
                    },
                    "implementation_allocation": {
                        "catalog_reuse": ["Outlook 일정/메일 조회 Component 검토"],
                        "catalog_candidates": ["email기반 자동 업무보고 만들기 Flow"],
                        "new_standalone_components": ["메일 중복 제거·근거 링크 정리 Component"],
                        "builtin_components": ["조건 분기", "Human Input"],
                        "human_tasks": ["최종 게시 승인"],
                    },
                    "next_steps": ["카탈로그 포트 계약 확인", "샘플 메일로 분기 테스트", "운영 승인 후 배포"],
                    "validation_plan": ["승인 전 게시가 발생하지 않는지 확인", "조회 실패 시 실패 건수를 표시하는지 확인"],
                    "open_items": ["메일 접근 권한 신청 범위를 확정"],
                }
            ],
        }
    )

    rendered = render_view_model(modules, reseal_view_model(view_model))
    document = rendered["html"]

    assert rendered["status"] == "RENDERED"
    assert 'id="business-report"' in document
    assert "function renderBusinessReport" in document
    assert "업무 개요" in document
    assert "현행 절차 및 문제" in document
    assert "개선 방향" in document
    assert "권장 운영 방식" in document
    assert "구현 분담 및 카탈로그 적용" in document
    assert "구현 로드맵" in document
    assert "메일 수집부터 팀장 공유까지의 업무를 승인 가능한 Agent 운영 방식으로 정리합니다." in document
    body = document.split("</head>", 1)[1]
    technical_reference = body.index('<details class="technical-reference static-fallback">')
    static_report = body.index('<div class="business-report-static">')
    assert static_report > technical_reference
    assert (
        '<section id="business-report" class="business-report" '
        'aria-live="polite" aria-busy="true"></section>'
    ) in body


def test_31_escapes_untrusted_text_and_keeps_json_non_executable(
    modules: dict[str, ModuleType],
) -> None:
    view_model = read_json("report_view_model.json")
    malicious = '</script><script id="xss-proof">globalThis.__xss__=1</script><img src=x onerror=alert(1)>'
    view_model["title"] = malicious
    view_model["to_be_graph"]["nodes"][0]["title"] = malicious
    detail_ref = view_model["to_be_graph"]["nodes"][0]["detail_ref"]
    view_model["to_be_graph"]["details"][detail_ref]["improvement"] = malicious
    view_model["sections"].append(
        {
            "section_id": "business_report",
            "title": malicious,
            "items": [
                {
                    "report_type": "business_report",
                    "report_version": "business-report/v1",
                    "executive_summary": {"overview": malicious},
                }
            ],
        }
    )
    view_model = reseal_view_model(view_model)
    document = render_view_model(modules, view_model)["html"]

    assert '<script id="xss-proof">' not in document
    assert "<img src=x onerror=alert(1)>" not in document
    assert r'\u003c/script\u003e\u003cscript id=\"xss-proof\"\u003e' in document
    assert "&lt;/script&gt;&lt;script id=&quot;xss-proof&quot;&gt;" in document
    scripts = re.findall(r"<script(?: [^>]*)?>(.*?)</script>", document, re.S)
    assert len(scripts) == 2
    decoded = json.loads(scripts[0])
    assert decoded["title"] == malicious


def test_31_rejects_dangling_detail_edge_port_and_generation_refs(
    modules: dict[str, ModuleType],
) -> None:
    base = read_json("report_view_model.json")
    mutations = []

    missing_detail = copy.deepcopy(base)
    detail_ref = missing_detail["to_be_graph"]["nodes"][0]["detail_ref"]
    del missing_detail["to_be_graph"]["details"][detail_ref]
    mutations.append(missing_detail)

    dangling_edge = copy.deepcopy(base)
    dangling_edge["to_be_graph"]["edges"][0]["target_node_id"] = "missing-node"
    mutations.append(dangling_edge)

    dangling_port = copy.deepcopy(base)
    dangling_port["to_be_graph"]["edges"][0]["source_port_id"] = "missing-port"
    mutations.append(dangling_port)

    wrong_owner_port = copy.deepcopy(base)
    edge = wrong_owner_port["to_be_graph"]["edges"][0]
    other_source = next(
        node for node in wrong_owner_port["to_be_graph"]["nodes"]
        if node["node_id"] != edge["source_node_id"] and node["output_ports"]
    )
    edge["source_port_id"] = other_source["output_ports"][0]["port_id"]
    mutations.append(wrong_owner_port)

    dangling_request = copy.deepcopy(base)
    custom = next(
        node
        for node in dangling_request["to_be_graph"]["nodes"]
        if node["implementation_source"] == "new_standalone_component"
    )
    custom["generation_request_ref"] = "missing-generation-request"
    mutations.append(dangling_request)

    for invalid in mutations:
        with pytest.raises(ValueError):
            render_view_model(modules, invalid)


def test_30_rejects_noncanonical_blueprint_registry_skill_port_and_embedded_request(
    modules: dict[str, ModuleType],
    sample_contracts: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    work, _, candidate_context = copy.deepcopy(sample_contracts)

    def build(terminal: dict[str, Any]) -> None:
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=terminal,
            retrieval_trace=candidate_context["retrieval_trace"],
        ).build_report_view_model()

    registry_object = read_json("agent_blueprint_terminal.json")
    requests = registry_object["blueprint"]["generation_requests"]
    registry_object["blueprint"]["generation_requests"] = {
        item["generation_request_id"]: item for item in requests
    }
    registry_object["generation_requests"] = registry_object["blueprint"]["generation_requests"]
    with pytest.raises(ValueError, match="generation_requests"):
        build(registry_object)

    invalid_skill = read_json("agent_blueprint_terminal.json")
    invalid_skill["blueprint"]["applied_skills"] = [1]
    with pytest.raises(ValueError, match="applied skill"):
        build(invalid_skill)

    invalid_port = read_json("agent_blueprint_terminal.json")
    invalid_port["blueprint"]["nodes"][0]["inputs"] = ["bad-port"]
    with pytest.raises(ValueError, match="port contract"):
        build(invalid_port)

    embedded = read_json("agent_blueprint_terminal.json")
    custom = next(
        node for node in embedded["blueprint"]["nodes"]
        if node["implementation_source"] == "new_standalone_component"
    )
    custom["generation_request"] = copy.deepcopy(embedded["blueprint"]["generation_requests"][0])
    embedded["blueprint"]["generation_requests"] = []
    embedded["generation_requests"] = []
    with pytest.raises(ValueError, match="cannot embed"):
        build(embedded)

    terminal = read_json("agent_blueprint_terminal.json")
    with pytest.raises(ValueError, match="provenance locks are required"):
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=terminal,
            retrieval_trace={},
        ).build_report_view_model()

    malformed_lock = read_json("agent_blueprint_terminal.json")
    malformed_lock["blueprint"]["design_scope_sha256"] = "x"
    malformed_trace = copy.deepcopy(candidate_context["retrieval_trace"])
    malformed_trace["design_scope_sha256"] = "x"
    with pytest.raises(ValueError, match="design_scope_sha256"):
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=malformed_lock,
            retrieval_trace=malformed_trace,
        ).build_report_view_model()

    malformed_requirement = read_json("agent_blueprint_terminal.json")
    malformed_requirement["blueprint"]["nodes"][1]["required_secrets"] = ["mail_api_credential_ref"]
    with pytest.raises(ValueError, match="required_secret contract"):
        build(malformed_requirement)

    fabricated_skill = read_json("agent_blueprint_terminal.json")
    source_skill = copy.deepcopy(fabricated_skill["blueprint"]["applied_skills"][0])
    source_skill["skill_id"] = "fabricated-skill"
    source_skill["version"] = "v9"
    source_skill["prompt_sha256"] = "sha256:" + "f" * 64
    fabricated_skill["blueprint"]["nodes"][1]["applied_skills"] = [source_skill]
    with pytest.raises(ValueError, match="approved blueprint skill registry"):
        build(fabricated_skill)

    secret_skill = read_json("agent_blueprint_terminal.json")
    secret_skill["blueprint"]["applied_skills"][0]["match_reason"] = "password=1234567890"
    secret_skill["blueprint"]["nodes"][1]["applied_skills"] = [
        copy.deepcopy(secret_skill["blueprint"]["applied_skills"][0])
    ]
    with pytest.raises(ValueError, match="secret material"):
        build(secret_skill)

    invalid_contract = read_json("agent_blueprint_terminal.json")
    custom_node = next(
        node for node in invalid_contract["blueprint"]["nodes"]
        if node["implementation_source"] == "new_standalone_component"
    )
    custom_node["generation_contract"] = {"x": 1}
    with pytest.raises(ValueError, match="generation_contract"):
        build(invalid_contract)

    mismatched_contract = read_json("agent_blueprint_terminal.json")
    custom_node = next(
        node for node in mismatched_contract["blueprint"]["nodes"]
        if node["implementation_source"] == "new_standalone_component"
    )
    custom_node["generation_contract"]["component_filename"] = "99_other_component.py"
    custom_node["generation_contract"]["class_name"] = "OtherComponent"
    with pytest.raises(ValueError, match="generation request integrity"):
        build(mismatched_contract)

    invented_asset = read_json("agent_blueprint_terminal.json")
    catalog_node = invented_asset["blueprint"]["nodes"][1]
    catalog_node["implementation_source"] = "catalog_component"
    catalog_node["technical_contract_status"] = "verified_runtime"
    catalog_node["runtime_validation_status"] = "verified_runtime"
    catalog_node["asset_ref"] = {"asset_id": "invented-not-retrieved", "version": "v999"}
    catalog_node["port_contract_sha256"] = canonical_hash(
        {"inputs": catalog_node["inputs"], "outputs": catalog_node["outputs"]}
    )
    catalog_node["generation_contract"] = None
    catalog_node["generation_request_ref"] = None
    invented_asset["blueprint"]["generation_requests"] = [
        item for item in invented_asset["blueprint"]["generation_requests"]
        if item["target_node_id"] != catalog_node["node_id"]
    ]
    invented_asset["generation_requests"] = invented_asset["blueprint"]["generation_requests"]
    with pytest.raises(ValueError, match="sealed candidate allowlist"):
        build(invented_asset)

    arbitrary_id = read_json("agent_blueprint_terminal.json")
    request = arbitrary_id["blueprint"]["generation_requests"][0]
    old_id = request["generation_request_id"]
    request["generation_request_id"] = "gen-" + "a" * 20
    for node in arbitrary_id["blueprint"]["nodes"]:
        if node.get("generation_request_ref") == old_id:
            node["generation_request_ref"] = request["generation_request_id"]
    arbitrary_id["generation_requests"] = arbitrary_id["blueprint"]["generation_requests"]
    with pytest.raises(ValueError, match="generation request integrity"):
        build(arbitrary_id)

    unrelated_prompt = read_json("agent_blueprint_terminal.json")
    request = unrelated_prompt["blueprint"]["generation_requests"][0]
    old_id = request["generation_request_id"]
    request["request_text"] = "Generate an unrelated standalone component.\n"
    digest = hashlib.sha256(request["request_text"].encode("utf-8")).hexdigest()
    request["prompt_sha256"] = "sha256:" + digest
    request["generation_request_id"] = "gen-" + digest[:20]
    for node in unrelated_prompt["blueprint"]["nodes"]:
        if node.get("generation_request_ref") == old_id:
            node["generation_request_ref"] = request["generation_request_id"]
    unrelated_prompt["generation_requests"] = unrelated_prompt["blueprint"]["generation_requests"]
    with pytest.raises(ValueError, match="generation request integrity"):
        build(unrelated_prompt)


def test_30_accepts_only_canonical_catalog_asset_ref_fields(
    modules: dict[str, ModuleType],
    sample_contracts: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    work, _, _ = copy.deepcopy(sample_contracts)
    terminal, trace = terminal_with_authoritative_candidate_ports()
    catalog_node = next(
        node for node in terminal["blueprint"]["nodes"]
        if node["node_id"] == "catalog-flow-isolated"
    )

    def build(envelope: dict[str, Any]) -> dict[str, Any]:
        return dict(
            modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
                work_definition=work,
                agent_blueprint=envelope,
                retrieval_trace=trace,
            ).build_report_view_model().data
        )

    baseline = build(terminal)
    assert baseline["schema_version"] == "report_view_model.v1"
    smuggled = copy.deepcopy(terminal)
    target = next(
        node for node in smuggled["blueprint"]["nodes"]
        if node["node_id"] == catalog_node["node_id"]
    )
    secret_value = "opaque-raw-password"
    target["asset_ref"]["api_key"] = secret_value
    with pytest.raises(ValueError) as exc_info:
        build(smuggled)
    assert "asset" in str(exc_info.value)
    assert secret_value not in str(exc_info.value)


def test_f30_and_f31_share_the_port_bound_candidate_allowlist_hash(
    modules: dict[str, ModuleType],
    sample_contracts: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    work, _, _ = copy.deepcopy(sample_contracts)
    terminal, trace = terminal_with_authoritative_candidate_ports()
    expected_hash = canonical_hash(trace["candidate_allowlist"])
    assert trace["candidate_allowlist_sha256"] == expected_hash
    assert terminal["blueprint"]["candidate_allowlist_sha256"] == expected_hash

    view_model = dict(
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=terminal,
            retrieval_trace=trace,
        ).build_report_view_model().data
    )
    assert view_model["retrieval_trace"]["candidate_allowlist_sha256"] == expected_hash
    assert render_view_model(modules, view_model)["status"] == "RENDERED"

    stale_presentation = copy.deepcopy(trace)
    stale_presentation["catalog_presentation"][0]["port_contract_sha256"] = "sha256:" + "e" * 64
    with pytest.raises(ValueError, match="catalog_presentation port contract"):
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=terminal,
            retrieval_trace=stale_presentation,
        ).build_report_view_model()

    stale_terminal = copy.deepcopy(terminal)
    stale_catalog_node = next(
        node for node in stale_terminal["blueprint"]["nodes"]
        if node["node_id"] == "catalog-flow-isolated"
    )
    stale_catalog_node["inputs"][0]["semantic_role"] = "privileged_mail_query"
    with pytest.raises(ValueError, match="port contract"):
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=stale_terminal,
            retrieval_trace=trace,
        ).build_report_view_model()

    stale_trace = copy.deepcopy(trace)
    stale_trace["candidate_allowlist"][0]["port_contract_sha256"] = "sha256:" + "d" * 64
    with pytest.raises(ValueError, match="candidate allowlist hash|candidate_allowlist"):
        modules["30_report_view_model_builder"].ReportViewModelBuilderComponent(
            work_definition=work,
            agent_blueprint=terminal,
            retrieval_trace=stale_trace,
        ).build_report_view_model()

    stale_view_model = copy.deepcopy(view_model)
    stale_view_node = next(
        node for node in stale_view_model["to_be_graph"]["nodes"]
        if node["node_id"] == "catalog-flow-isolated"
    )
    stale_view_node["port_contract_sha256"] = "sha256:" + "d" * 64
    stale_view_model = reseal_view_model(stale_view_model)
    with pytest.raises(ValueError, match="display ports|catalog asset/port binding"):
        render_view_model(modules, stale_view_model)

    stale_trace_view_model = copy.deepcopy(view_model)
    stale_trace_view_model["retrieval_trace"]["candidate_allowlist"][0]["port_contract_sha256"] = (
        "sha256:" + "d" * 64
    )
    stale_trace_view_model = reseal_view_model(stale_trace_view_model)
    with pytest.raises(ValueError, match="candidate_allowlist_sha256|candidate allowlist hash"):
        render_view_model(modules, stale_trace_view_model)

    forged_display_port = copy.deepcopy(view_model)
    forged_display_node = next(
        node for node in forged_display_port["to_be_graph"]["nodes"]
        if node["node_id"] == "catalog-flow-isolated"
    )
    forged_display_node["input_ports"][0]["data_type"] = "ForgedAdminData"
    forged_display_detail = forged_display_port["to_be_graph"]["details"][
        forged_display_node["detail_ref"]
    ]
    forged_display_detail["inputs"] = copy.deepcopy(forged_display_node["input_ports"])
    forged_display_port = reseal_view_model(forged_display_port)
    with pytest.raises(ValueError, match="display ports|sealed catalog port contract"):
        render_view_model(modules, forged_display_port)

    inconsistent_detail = copy.deepcopy(view_model)
    inconsistent_node = next(
        node for node in inconsistent_detail["to_be_graph"]["nodes"]
        if node["node_id"] == "catalog-flow-isolated"
    )
    inconsistent_detail_record = inconsistent_detail["to_be_graph"]["details"][
        inconsistent_node["detail_ref"]
    ]
    inconsistent_detail_record["inputs"] = copy.deepcopy(inconsistent_detail_record["inputs"])
    inconsistent_detail_record["inputs"][0]["label"] = "표시 노드와 불일치하는 입력"
    inconsistent_detail = reseal_view_model(inconsistent_detail)
    with pytest.raises(ValueError, match="detail ports"):
        render_view_model(modules, inconsistent_detail)


def test_31_declares_layout_contract_for_360_through_1920_widths(
    modules: dict[str, ModuleType],
) -> None:
    rendered = render_view_model(modules, read_json("report_view_model.json"))
    document = rendered["html"]
    style = re.search(r"<style>(.*?)</style>", document, re.S)
    assert style
    css = style.group(1).replace(" ", "")
    assert '<metaname="viewport"content="width=device-width,initial-scale=1">' in document.replace(" ", "")
    assert "@media(max-width:900px)" in css
    assert "@media(max-width:520px)" in css
    assert ".intro{position:relative;overflow:hidden" in css
    assert ".graph-viewport{position:absolute;inset:0;overflow:auto" in css
    assert ".shell{max-width:1480px" in css
    assert ".catalog-more>summary" in css
    assert ".technical-reference.static-fallback{display:block}" in css
    assert "@media(prefers-reduced-motion:reduce)" in css
    assert "@mediaprint" in css
    assert "document.documentElement.classList.add('js')" in document
    assert 'id="flow-panel"' in document
    assert (
        '<section id="business-report" class="business-report" '
        'aria-live="polite" aria-busy="true"></section>'
    ) in document
    assert '<details class="technical-reference static-fallback">' in document
    assert "원시 데이터 및 순서형 전체 내용 (기술 참고)" in document
    assert '<div id="support"></div>' in document
    assert "Math.min(1,horizontal,vertical)" in document
    assert "requestAnimationFrame(fitAll)" in document
    assert "function compactGraphLayout" in document
    assert "Math.max(.62,allNodesScale)" in document
    assert "const stageTitles=" in document
    assert "상세 링크 미등록" in document
    assert document.index('id="flow-panel"') < document.index('id="business-report"')
    assert document.index('id="business-report"') < document.index('id="design-reference-title"')
    assert "min-width:1920px" not in css and "min-width:768px" not in css
    assert 360 <= 520 < 900 < 1920


def test_31_rejects_failed_envelope_and_cross_section_provenance_mismatch(
    modules: dict[str, ModuleType],
) -> None:
    renderer_module = modules["31_responsive_report_renderer"]
    canonical = read_json("report_view_model.json")
    with pytest.raises(ValueError, match="upstream envelope"):
        renderer_module.ResponsiveReportRendererComponent(
            report_view_model={"ok": False, "status": "BLOCKED", "report_view_model": canonical},
            allowed_hosts_json='["localhost"]',
        ).render_report()
    with pytest.raises(ValueError, match="upstream envelope"):
        renderer_module.ResponsiveReportRendererComponent(
            report_view_model={"ok": True, "status": "BLOCKED", "report_view_model": canonical},
            allowed_hosts_json='["localhost"]',
        ).render_report()

    trace_mismatch = copy.deepcopy(canonical)
    trace_mismatch["retrieval_trace"]["snapshot_id"] = "snapshot-other"
    trace_mismatch["report_id"] = renderer_module._expected_report_id(trace_mismatch)
    with pytest.raises(ValueError, match="snapshot_id"):
        render_view_model(modules, trace_mismatch)

    readiness_mismatch = copy.deepcopy(canonical)
    readiness_mismatch["to_be_graph"]["build_readiness"] = "design_only"
    readiness_mismatch["report_id"] = renderer_module._expected_report_id(readiness_mismatch)
    with pytest.raises(ValueError, match="build_readiness"):
        render_view_model(modules, readiness_mismatch)

    missing_lock = copy.deepcopy(canonical)
    missing_lock["retrieval_trace"].pop("design_scope_sha256")
    missing_lock["report_id"] = renderer_module._expected_report_id(missing_lock)
    with pytest.raises(ValueError, match="design_scope_sha256"):
        render_view_model(modules, missing_lock)

    arbitrary_request_id = copy.deepcopy(canonical)
    request_id, request = next(iter(arbitrary_request_id["to_be_graph"]["generation_requests"].items()))
    forged_id = "gen-" + "a" * 20
    request["generation_request_id"] = forged_id
    arbitrary_request_id["to_be_graph"]["generation_requests"] = {forged_id: request}
    for node in arbitrary_request_id["to_be_graph"]["nodes"]:
        if node.get("generation_request_ref") == request_id:
            node["generation_request_ref"] = forged_id
    arbitrary_request_id["report_id"] = renderer_module._expected_report_id(arbitrary_request_id)
    with pytest.raises(ValueError, match="not derived"):
        render_view_model(modules, arbitrary_request_id)


def test_32_dry_run_uses_shared_html_report_api_shape_and_never_uses_network(
    modules: dict[str, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = modules["32_report_publisher"]
    rendered = render_view_model(modules, read_json("report_view_model.json"))

    def unexpected_network(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("dry-run must not contact the Report API")

    monkeypatch.setattr(module.urllib.request, "urlopen", unexpected_network)
    publisher = module.ReportPublisherComponent(
        render_result=rendered,
        report_api_url="http://localhost:5000/internal/report-api",
        report_ttl_hours=4,
        timeout_seconds=1,
        dry_run=True,
    )
    result = dict(publisher.publish_report().data)
    assert result == {
        "ok": True,
        "status": "would_publish",
        "execution_mode_display": "테스트 실행 (저장하지 않음)",
        "message": "테스트 실행입니다. Report API에는 게시하지 않았습니다.",
        "renderer_report_id": rendered["report_id"],
        "content_bytes": len(rendered["html"].encode("utf-8")),
        "target_url": "http://localhost:5000/internal/report-api/reports",
        "ttl_hours": 4,
    }
    assert module._reports_post_url("http://localhost:5000/reports") == "http://localhost:5000/reports"
    assert module._reports_post_url("http://localhost:5000?token=api-key") == "http://localhost:5000/reports?token=api-key"
    publisher_inputs = {item.name: item for item in module.ReportPublisherComponent.inputs}
    assert set(publisher_inputs) == {"render_result", "report_api_url", "report_ttl_hours", "dry_run", "timeout_seconds"}
    assert publisher_inputs["report_api_url"].value == "http://127.0.0.1:5000"
    assert publisher_inputs["dry_run"].display_name == "테스트 실행 (저장하지 않음)"

    missing_html = dict(rendered)
    missing_html.pop("html")
    failed = dict(
        module.ReportPublisherComponent(
            render_result=missing_html,
            report_api_url="http://localhost:5000",
            dry_run=True,
        ).publish_report().data
    )
    assert failed["status"] == "PUBLISH_FAILED"
    assert failed["error"]["code"] == "REPORT_HTML_REQUIRED"


def test_32_posts_reference_report_api_contract_and_returns_structured_failures(
    modules: dict[str, ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = modules["32_report_publisher"]
    rendered = render_view_model(modules, read_json("report_view_model.json"))
    rendered["renderer_version"] = datetime(2026, 8, 31, tzinfo=timezone.utc)
    received: dict[str, Any] = {}
    response_payload: dict[str, Any] = {
        "report_id": "20260831000000_server-generated",
        "view_url": "http://localhost:5000/reports/view/server-generated?token=view-token",
        "download_url": "http://localhost:5000/reports/download/server-generated?token=download-token",
        "expires_at": "2026-08-31T04:00:00+09:00",
        "ttl_hours": 4,
        "storage": {"backend": "mongodb"},
    }

    class FakeResponse:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self, _limit: int) -> bytes:
            return json.dumps(response_payload).encode("utf-8")

    def fake_urlopen(request: Any, timeout: int) -> FakeResponse:
        received["url"] = request.full_url
        received["method"] = request.get_method()
        received["headers"] = dict(request.header_items())
        received["body"] = json.loads(request.data.decode("utf-8"))
        received["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    result = dict(
        module.ReportPublisherComponent(
            render_result=rendered,
            report_api_url="http://localhost:5000",
            report_ttl_hours=999,
            timeout_seconds=1,
            dry_run=False,
        ).publish_report().data
    )
    assert received["url"] == "http://localhost:5000/reports"
    assert received["method"] == "POST"
    assert received["timeout"] == 1
    assert received["headers"] == {
        "Content-type": "application/json; charset=utf-8",
        "Accept": "application/json",
    }
    assert set(received["body"]) == {
        "html",
        "title",
        "question",
        "view_request",
        "available_datasets",
        "report_plan",
        "ttl_hours",
        "filename_hint",
    }
    assert received["body"]["ttl_hours"] == 168
    assert received["body"]["report_plan"]["source_flow"] == "F30_responsive_report"
    assert received["body"]["report_plan"]["renderer_report_id"] == rendered["report_id"]
    assert received["body"]["report_plan"]["renderer_version"] == "2026-08-31 00:00:00+00:00"
    assert result["ok"] is True
    assert result["status"] == "published"
    assert result["report_id"] == response_payload["report_id"]
    assert result["view_url"] == response_payload["view_url"]
    assert result["download_url"] == response_payload["download_url"]
    assert result["storage"] == {"backend": "mongodb"}

    response_payload.pop("download_url")
    missing_link = dict(
        module.ReportPublisherComponent(
            render_result=rendered,
            report_api_url="http://localhost:5000/reports",
            dry_run=False,
        ).publish_report().data
    )
    assert missing_link["status"] == "PUBLISH_FAILED"
    assert missing_link["error"]["code"] == "REPORT_API_INVALID_RESPONSE"

    def offline(*_args: Any, **_kwargs: Any) -> Any:
        raise module.urllib.error.URLError("offline")

    monkeypatch.setattr(module.urllib.request, "urlopen", offline)
    connection_failure = dict(
        module.ReportPublisherComponent(
            render_result=rendered,
            report_api_url="http://localhost:5000",
            dry_run=False,
        ).publish_report().data
    )
    assert connection_failure["status"] == "PUBLISH_FAILED"
    assert connection_failure["error"] == {
        "code": "REPORT_API_CONNECTION_FAILED",
        "message": "Report API connection failed: offline",
        "retryable": True,
    }


def test_34_renders_publisher_result_as_concise_message_with_real_links(
    modules: dict[str, ModuleType],
) -> None:
    module = modules["37_report_publication_message"]
    published = module.ReportPublicationMessageComponent(
        publish_result={
            "ok": True,
            "status": "published",
            "report_id": "20260901205152_262cdb5318cd8546828493515534e1e8",
            # Accept the Markdown-looking legacy transport value too, but
            # render one normal Markdown link rather than nesting syntax.
            "view_url": "[**http://127.0.0.1:5000/reports/view/report-1**](http://127.0.0.1:5000/reports/view/report-1)",
            "download_url": "http://127.0.0.1:5000/reports/download/report-1",
            "expires_at": "2026-09-02T00:51:52+09:00",
            "ttl_hours": 4,
            "storage": {
                "backend": "mongodb_collection",
                "database": "datagov",
                "collection": "report_save_db",
            },
        }
    ).build_message().text
    assert "## 업무 Agent 설계 보고서가 게시되었습니다" in published
    assert "[보고서 열기](http://127.0.0.1:5000/reports/view/report-1)" in published
    assert "[HTML 다운로드](http://127.0.0.1:5000/reports/download/report-1)" in published
    assert "[**" not in published
    assert "보고서 ID: `20260901205152_262cdb5318cd8546828493515534e1e8`" in published
    assert "저장 위치: mongodb_collection · datagov.report_save_db" in published


def test_34_explains_test_and_failure_without_dumping_raw_payload(
    modules: dict[str, ModuleType],
) -> None:
    module = modules["37_report_publication_message"]
    test_run = module.render_report_publication_message(
        {
            "ok": True,
            "status": "would_publish",
            "renderer_report_id": "report-test",
            "content_bytes": 12345,
            "ttl_hours": 4,
        }
    )
    assert "테스트 실행이 완료되었습니다" in test_run
    assert "Report API에는 저장하지 않았고" in test_run
    assert "12,345 bytes" in test_run
    assert "{" not in test_run

    failed = module.render_report_publication_message(
        {
            "ok": False,
            "status": "PUBLISH_FAILED",
            "message": "mongodb://user:password@internal/report failed",
            "error": {
                "code": "REPORT_API_CONNECTION_FAILED",
                "message": "token=very-secret-value connection failed",
                "retryable": True,
            },
        }
    )
    assert "## 보고서 게시에 실패했습니다" in failed
    assert "REPORT_API_CONNECTION_FAILED" in failed
    assert "[민감정보 제거]" in failed
    assert "mongodb://" not in failed
    assert "{" not in failed


def test_34_omits_untrusted_links_and_handles_empty_output(
    modules: dict[str, ModuleType],
) -> None:
    module = modules["37_report_publication_message"]
    message = module.render_report_publication_message(
        {
            "ok": True,
            "status": "published",
            "view_url": "javascript:alert(1)",
            "download_url": "https://example.test/report#fragment-is-not-allowed",
        }
    )
    assert "보고서 링크를 받지 못했습니다" in message
    assert "javascript:" not in message
    assert "example.test" not in message
    assert "게시 결과가 없습니다" in module.render_report_publication_message()
