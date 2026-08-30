from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import jsonschema


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLES = PROJECT_ROOT / "samples"
SCHEMAS = PROJECT_ROOT / "schemas"
BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build_sample_contracts.py"
PREVIEW_COMPONENT = PROJECT_ROOT / "components" / "work_definition" / "17_work_preview_hasher.py"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_committed_samples_are_exact_deterministic_pipeline_outputs() -> None:
    builder = _load_module("test_build_sample_contracts", BUILD_SCRIPT)
    generated = builder.build_sample_documents()

    assert _json(SAMPLES / "approved_work_definition.json") == generated["work_definition"]
    assert _json(SAMPLES / "approved_agent_blueprint.json") == generated["blueprint"]
    assert _json(SAMPLES / "agent_blueprint_terminal.json") == generated["terminal"]
    assert _json(SAMPLES / "candidate_context.json") == generated["candidate_context"]
    assert _json(SAMPLES / "f20_report_handoff.json") == generated["report_handoff"]


def test_approved_work_definition_matches_schema_and_component17_hash() -> None:
    work = _json(SAMPLES / "approved_work_definition.json")
    schema = _json(SCHEMAS / "work_definition.schema.json")
    jsonschema.Draft202012Validator(schema).validate(work)

    preview = _load_module("test_sample_preview_component", PREVIEW_COMPONENT)
    result = preview.build_work_preview_hash(
        {"ok": True, "work_definition": work, "graph_validation": {"valid": True}}
    )
    assert result["ok"] is True
    assert result["status"] == "APPROVED"
    assert result["preview"]["preview_hash"] == work["approved_hash"] == work["preview_hash"]
    assert work["schema_version"] == "work-definition/v1"
    assert "goal" in work and "systems" in work
    assert "purpose" not in work and "systems_tools" not in work


def test_terminal_envelope_contains_the_schema_validated_nested_blueprint() -> None:
    terminal = _json(SAMPLES / "agent_blueprint_terminal.json")
    blueprint = _json(SAMPLES / "approved_agent_blueprint.json")
    schema = _json(SCHEMAS / "agent_blueprint.schema.json")
    jsonschema.Draft202012Validator(schema).validate(blueprint)

    assert terminal["ok"] is True
    assert terminal["status"] == "COMPLETED"
    assert terminal["trace_id"] == "trace-sample-f20-terminal"
    assert terminal["blueprint"] == blueprint
    assert terminal["generation_request_count"] == 2
    assert terminal["generation_request"] == {}
    assert len(terminal["generation_requests"]) == 2
    assert blueprint["generation_requests"] == terminal["generation_requests"]
    assert blueprint["readiness_assessment"]["status_axis"] == "build_readiness"
    assert blueprint["build_readiness"] == "proposed_unverified"
    assert all(item["template_version"] == "ccp-base-2026-08-27.v1" for item in terminal["generation_requests"])
    request_by_node = {item["target_node_id"]: item for item in terminal["generation_requests"]}
    for node in blueprint["nodes"]:
        if node["implementation_source"] == "new_standalone_component":
            assert node["generation_request_ref"] == request_by_node[node["node_id"]]["generation_request_id"]


def test_f20_report_handoff_binds_the_sample_artifacts() -> None:
    handoff = _json(SAMPLES / "f20_report_handoff.json")
    assert handoff["ok"] is True
    assert handoff["status"] == "COMPLETED"
    assert handoff["schema_version"] == "f20-report-handoff/v1"
    assert handoff["trace_id"] == "trace-sample-f20-report-handoff"
    approved_work = _json(SAMPLES / "approved_work_definition.json")
    for field in (
        "schema_version",
        "status",
        "tenant_id",
        "owner_id",
        "work_definition_id",
        "revision",
        "approved_hash",
    ):
        assert handoff["work_definition"][field] == approved_work[field]
    assert handoff["agent_blueprint"] == _json(SAMPLES / "agent_blueprint_terminal.json")
    assert handoff["retrieval_trace"] == _json(SAMPLES / "candidate_context.json")["retrieval_trace"]
    assert handoff["execution_context"]["tenant_id"] == handoff["work_definition"]["tenant_id"]
    assert handoff["execution_context"]["actor_id"] == handoff["work_definition"]["owner_id"]
