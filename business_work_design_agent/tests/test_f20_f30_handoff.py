from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = PROJECT_ROOT / "samples"

COMPONENT_PATHS = {
    "planner": PROJECT_ROOT / "components" / "hybrid_retrieval" / "20_search_query_planner.py",
    "handoff_builder": PROJECT_ROOT / "components" / "agent_blueprint" / "38_f20_report_handoff_builder.py",
    "handoff_gate": PROJECT_ROOT / "components" / "work_definition" / "44_f10_report_handoff_gate.py",
    "handoff_loader": PROJECT_ROOT / "components" / "report" / "33_f30_report_handoff_loader.py",
    "view_model": PROJECT_ROOT / "components" / "report" / "30_report_view_model_builder.py",
    "renderer": PROJECT_ROOT / "components" / "report" / "31_responsive_report_renderer.py",
    "publisher": PROJECT_ROOT / "components" / "report" / "32_report_publisher.py",
}


def _load_component(name: str, path: Path) -> ModuleType:
    module_name = f"test_f20_f30_handoff_{name}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _read_sample(name: str) -> dict[str, Any]:
    value = json.loads((SAMPLES_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


@pytest.fixture(scope="module")
def modules() -> dict[str, ModuleType]:
    return {name: _load_component(name, path) for name, path in COMPONENT_PATHS.items()}


def _sealed_scope(modules: dict[str, ModuleType], work_definition: dict[str, Any]) -> dict[str, Any]:
    scope = modules["planner"].build_design_scope(
        copy.deepcopy(work_definition),
        tenant_id="default",
        catalog_snapshot_id="catalog-snapshot-20260827",
        acl_context={"subject_id": "employee-demo", "groups": ["business-automation"]},
        design_prompt="메일 근거를 보존하고 게시 전 사람의 승인을 강제한다.",
    )
    assert scope["ok"] is True
    assert scope["status"] == "COMPLETED"
    return scope


def _valid_handoff(modules: dict[str, ModuleType]) -> dict[str, Any]:
    work_definition = _read_sample("approved_work_definition.json")
    candidate_context = _read_sample("candidate_context.json")
    terminal_blueprint = _read_sample("agent_blueprint_terminal.json")
    handoff = modules["handoff_builder"].build_f20_report_handoff(
        _sealed_scope(modules, work_definition),
        candidate_context,
        terminal_blueprint,
    )
    assert handoff["ok"] is True
    assert handoff["status"] == "COMPLETED"
    return handoff


def _reseal_handoff(builder_module: ModuleType, handoff: dict[str, Any]) -> None:
    core = {
        key: copy.deepcopy(value)
        for key, value in handoff.items()
        if key not in {"ok", "status", "handoff_sha256", "trace_id"}
    }
    handoff["handoff_sha256"] = builder_module._canonical_hash(core)


def test_f20_handoff_json_roundtrips_through_f10_f30_and_dry_run_report(
    modules: dict[str, ModuleType],
) -> None:
    """The F20 Chat output is the only sealed input accepted by F30."""

    work_definition = _read_sample("approved_work_definition.json")
    candidate_context = _read_sample("candidate_context.json")
    terminal_blueprint = _read_sample("agent_blueprint_terminal.json")
    scope = _sealed_scope(modules, work_definition)

    builder_component = modules["handoff_builder"].F20ReportHandoffBuilderComponent(
        design_scope=scope,
        candidate_context=candidate_context,
        terminal_blueprint=terminal_blueprint,
    )
    handoff_message = builder_component.build_report_handoff_message()
    handoff = json.loads(handoff_message.text)
    assert handoff["ok"] is True
    assert handoff["status"] == "COMPLETED"
    assert handoff["schema_version"] == "f20-report-handoff/v1"
    assert handoff["handoff_sha256"].startswith("sha256:")

    builder_outputs = {output.name: output for output in modules["handoff_builder"].F20ReportHandoffBuilderComponent.outputs}
    assert set(builder_outputs) == {"report_handoff", "report_handoff_message"}
    assert all(output.group_outputs is True for output in builder_outputs.values())

    gate = modules["handoff_gate"].validate_f20_report_handoff(handoff)
    assert gate["ok"] is True
    assert gate["status"] == "READY_FOR_REPORT"
    assert gate["route"] == "success_message"

    loaded = modules["handoff_loader"].load_f20_report_handoff(handoff_message.text)
    assert loaded["work_definition"]["work_definition_id"] == work_definition["work_definition_id"]
    assert loaded["agent_blueprint"]["blueprint"]["terminal_contract"] is True
    assert loaded["retrieval_trace"]["snapshot_id"] == candidate_context["snapshot_id"]
    assert loaded["execution_context"] == {
        "tenant_id": "default",
        "actor_id": "employee-demo",
        "work_definition_id": work_definition["work_definition_id"],
        "work_definition_revision": work_definition["revision"],
        "approved_hash": work_definition["approved_hash"],
        "handoff_sha256": handoff["handoff_sha256"],
    }

    loader_outputs = {output.name: output for output in modules["handoff_loader"].F30ReportHandoffLoaderComponent.outputs}
    assert set(loader_outputs) == {"work_definition", "agent_blueprint", "retrieval_trace", "report_context"}
    assert all(output.group_outputs is True for output in loader_outputs.values())

    view_model = dict(
        modules["view_model"].ReportViewModelBuilderComponent(
            work_definition=loaded["work_definition"],
            agent_blueprint=loaded["agent_blueprint"],
            retrieval_trace=loaded["retrieval_trace"],
            report_title="주간 업무보고 업무 방식 및 Agent 설계",
            max_nodes=500,
            max_edges=1000,
        ).build_report_view_model().data
    )
    rendered = dict(
        modules["renderer"].ResponsiveReportRendererComponent(
            report_view_model=view_model,
            renderer_version="business-report-renderer.v1",
            allowed_hosts_json='["localhost"]',
            max_nodes=500,
            max_edges=1000,
            max_html_bytes=10_000_000,
        ).render_report().data
    )
    published = dict(
        modules["publisher"].ReportPublisherComponent(
            render_result=rendered,
            report_context=loaded["execution_context"],
            report_api_url="http://localhost:8080/internal/report-api",
            bearer_token="",
            tenant_id="",
            actor_id="",
            idempotency_key="",
            allowed_hosts_json='["localhost"]',
            timeout_seconds=1,
            dry_run=True,
        ).publish_report().data
    )
    assert published["ok"] is True
    assert published["status"] == "would_publish"
    assert published["report_id"] == rendered["report_id"]


def test_f20_f30_roundtrip_accepts_a_sealed_empty_catalog_allowlist(
    modules: dict[str, ModuleType],
) -> None:
    """No search hit may continue, but it must not authorize a catalog node."""

    work_definition = _read_sample("approved_work_definition.json")
    candidate_context = _read_sample("candidate_context.json")
    terminal_blueprint = _read_sample("agent_blueprint_terminal.json")
    empty_allowlist_hash = modules["handoff_builder"]._canonical_hash([])
    candidate_context.update(
        candidate_items=[],
        candidate_allowlist=[],
        candidate_allowlist_sha256=empty_allowlist_hash,
        untrusted_candidate_context="",
        context_char_count=0,
        catalog_reference_policy="deny_all_catalog_assets",
        catalog_candidate_status="none_available",
    )
    candidate_context["retrieval_trace"].update(
        candidate_allowlist=[],
        candidate_allowlist_sha256=empty_allowlist_hash,
        empty_result_reason="NO_CANDIDATES",
        catalog_reference_policy="deny_all_catalog_assets",
        catalog_candidate_status="none_available",
    )
    terminal_blueprint["blueprint"]["candidate_allowlist_sha256"] = empty_allowlist_hash
    assert all(
        node["implementation_source"] not in {"catalog_component", "catalog_flow"}
        for node in terminal_blueprint["blueprint"]["nodes"]
    )

    handoff = modules["handoff_builder"].build_f20_report_handoff(
        _sealed_scope(modules, work_definition),
        candidate_context,
        terminal_blueprint,
    )
    assert handoff["ok"] is True
    assert modules["handoff_gate"].validate_f20_report_handoff(handoff)["status"] == "READY_FOR_REPORT"

    loaded = modules["handoff_loader"].load_f20_report_handoff(json.dumps(handoff, ensure_ascii=False))
    view_model = dict(
        modules["view_model"].ReportViewModelBuilderComponent(
            work_definition=loaded["work_definition"],
            agent_blueprint=loaded["agent_blueprint"],
            retrieval_trace=loaded["retrieval_trace"],
            report_title="카탈로그 후보 없는 업무 설계",
            max_nodes=500,
            max_edges=1000,
        ).build_report_view_model().data
    )
    assert view_model["retrieval_trace"]["candidate_allowlist"] == []
    catalog_section = next(section for section in view_model["sections"] if section["section_id"] == "catalog_reuse")
    assert catalog_section["items"][0]["status"] == "no_authorized_candidates"
    rendered = dict(
        modules["renderer"].ResponsiveReportRendererComponent(
            report_view_model=view_model,
            renderer_version="business-report-renderer.v1",
            allowed_hosts_json='["localhost"]',
            max_nodes=500,
            max_edges=1000,
            max_html_bytes=10_000_000,
        ).render_report().data
    )
    assert rendered["ok"] is True
    assert rendered["status"] == "RENDERED"


def test_tampered_handoff_is_blocked_by_f10_or_f30_binding_validation(
    modules: dict[str, ModuleType],
) -> None:
    original = _valid_handoff(modules)

    unsealed_change = copy.deepcopy(original)
    unsealed_change["execution_context"]["actor_id"] = "attacker"
    blocked = modules["handoff_gate"].validate_f20_report_handoff(unsealed_change)
    assert blocked["ok"] is False
    assert blocked["route"] == "blocked_path"
    assert blocked["error"]["code"] == "F20_REPORT_HANDOFF_HASH_INVALID"

    resealed_cross_binding_change = copy.deepcopy(original)
    resealed_cross_binding_change["retrieval_trace"]["snapshot_id"] = "catalog-snapshot-tampered"
    _reseal_handoff(modules["handoff_builder"], resealed_cross_binding_change)
    assert modules["handoff_gate"].validate_f20_report_handoff(resealed_cross_binding_change)["status"] == "READY_FOR_REPORT"
    with pytest.raises(ValueError, match="Retrieval Trace binding is invalid"):
        modules["handoff_loader"].load_f20_report_handoff(resealed_cross_binding_change)
