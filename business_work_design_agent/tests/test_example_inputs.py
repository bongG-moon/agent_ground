from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path
from types import ModuleType
from typing import Any

import jsonschema
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLES = PROJECT_ROOT / "samples"
SCHEMAS = PROJECT_ROOT / "schemas"
F00_LOADER = PROJECT_ROOT / "components" / "catalog_ingestion" / "00_catalog_json_loader.py"
F00_CHUNKER = PROJECT_ROOT / "components" / "catalog_ingestion" / "01_catalog_deterministic_chunker.py"
F00_WRITER = PROJECT_ROOT / "components" / "catalog_ingestion" / "02_catalog_mongodb_vector_writer.py"
F10_COMPONENT = PROJECT_ROOT / "components" / "work_definition" / "10_work_request_envelope.py"
SKILL_COMPONENT = PROJECT_ROOT / "components" / "hybrid_retrieval" / "19_skill_context_resolver.py"
SEED_SCRIPT = PROJECT_ROOT / "scripts" / "seed_example_skill_registry.py"
VERIFY_SCRIPT = PROJECT_ROOT / "scripts" / "verify_example_mongodb.py"


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


def _flow_node(flow: dict[str, Any], key: str) -> dict[str, Any]:
    for wrapper in flow["data"]["nodes"]:
        node = wrapper["data"]["node"]
        if node.get("metadata", {}).get("flow_node_key") == key:
            return node
    raise AssertionError(f"Flow node {key!r} not found")


def test_f00_catalog_example_matches_source_and_stored_parent_contracts() -> None:
    sample_path = SAMPLES / "f00_catalog_assets_example.json"
    upload = _json(sample_path)
    upload_schema = _json(SCHEMAS / "catalog_upload.schema.json")
    jsonschema.Draft202012Validator(upload_schema).validate(upload)

    loader = _load_module("test_example_f00_loader", F00_LOADER)
    chunker = _load_module("test_example_f00_chunker", F00_CHUNKER)
    writer = _load_module("test_example_f00_writer", F00_WRITER)
    records, source_sha256, source_size = loader._read_catalog_records(
        sample_path,
        max_file_bytes=10 * 1024 * 1024,
        max_records=100,
        max_record_chars=200_000,
    )
    assert len(records) == 100
    normalized_records = loader._normalize_records(
        records,
        tenant_id=loader._TENANT_ID,
        catalog_id=loader._CATALOG_ID,
        source_sha256=source_sha256,
        source_size_bytes=source_size,
        max_record_chars=200_000,
        max_text_chars=60_000,
    )
    catalog_bundle = {
        "ok": True,
        "status": "LOADED",
        "schema_version": "catalog-normalized-bundle/v1",
        "ingest_contract_version": "catalog-file-vector-ingest/v1",
        "tenant_id": loader._TENANT_ID,
        "catalog_id": loader._CATALOG_ID,
        "source_sha256": source_sha256,
        "source_size_bytes": source_size,
        "max_text_chars": 60_000,
        "records": normalized_records,
        "counts": {"records": len(normalized_records)},
    }
    chunk_bundle = chunker._build_chunk_bundle(
        catalog_bundle,
        chunk_chars=6000,
        overlap_chars=200,
        max_chunks_per_record=16,
        max_total_chunks=100,
    )
    class ExampleEmbedding:
        model_name = "example-embedding"

    embedding_contract = writer._embedding_runtime_contract(ExampleEmbedding(), 8)
    writer._validate_chunk_bundle(chunk_bundle)
    snapshot_id = writer._snapshot_id(chunk_bundle, embedding_contract)
    vectors = writer._vectors(
        [[0.125] * embedding_contract["dimension"] for _ in chunk_bundle["chunks"]],
        len(chunk_bundle["chunks"]),
        embedding_contract["dimension"],
    )
    parents, chunks = writer._build_stored_documents(
        chunk_bundle,
        embedding_contract,
        snapshot_id,
        vectors,
    )
    assert len(parents) == 100
    assert len(chunks) >= len(parents)
    assert len(chunk_bundle["ingest_sha256"]) == 64
    assert all(chunk["embedding"]["vector"] == [0.125] * 8 for chunk in chunks)
    assert all(chunk["embedding"]["contract"] == embedding_contract for chunk in chunks)
    stored_schema = _json(SCHEMAS / "catalog_asset.schema.json")
    validator = jsonschema.Draft202012Validator(stored_schema)
    verifier = _load_module("test_example_mongodb_verifier", VERIFY_SCRIPT)
    assert verifier._validate_embedding_runtime_contract(embedding_contract) == embedding_contract
    invalid_embedding_contract = deepcopy(embedding_contract)
    invalid_embedding_contract["fingerprint"] = "sha256:" + "0" * 64
    with pytest.raises(RuntimeError):
        verifier._validate_embedding_runtime_contract(invalid_embedding_contract)
    expected_records = {
        (str(item.get("id") or item.get("asset_id")), str(item.get("version") or "unversioned")): item
        for item in upload["items"]
    }
    for parent in parents:
        validator.validate(parent)
        assert parent["tenant_id"] == loader._TENANT_ID
        assert parent["technical_contract_status"] == "metadata_only"
        assert parent["raw_text_redacted"]
        assert parent["lexical_text_redacted"]
        assert parent["embedding_manifest"]["embedding_contract"] == embedding_contract
        verifier._validate_parent_content(
            parent,
            expected_records[(parent["asset_id"], parent["version"])],
            source_sha256,
            source_size,
        )
    tampered_parent = deepcopy(parents[0])
    tampered_parent["raw_record_redacted"]["title"] = "tampered"
    with pytest.raises(RuntimeError):
        verifier._validate_parent_content(
            tampered_parent,
            expected_records[(parents[0]["asset_id"], parents[0]["version"])],
            source_sha256,
            source_size,
        )


def test_f10_example_is_safe_and_is_embedded_only_in_the_exported_f10_node() -> None:
    example = _json(SAMPLES / "f10_work_request_example.json")
    component = _load_module("test_example_f10_component", F10_COMPONENT)
    result = component.build_work_request_envelope(
        example["request_text"],
        additional_prompt=example["additional_prompt"],
        team_name=example["team_name"],
        employee_id=example["employee_id"],
        session_id="session-example-runtime",
        channel_mode="native_hitl",
        submitted_at="2026-08-29T00:00:00Z",
    )
    assert result["ok"] is True
    assert result["status"] == "INTAKE"
    assert result["envelope"]["channel_mode"] == "native_hitl"
    assert result["envelope"]["tenant_id"] == "default"
    assert result["envelope"]["team_name"] == example["team_name"]
    assert result["envelope"]["employee_id"] == example["employee_id"]

    answer_examples = example["clarification_answer_examples"]
    assert isinstance(answer_examples, list)
    assert len(answer_examples) == 4
    topics = {item.get("topic") for item in answer_examples}
    assert len(topics) == 4
    assert all(isinstance(topic, str) and topic.strip() and len(topic) <= 200 for topic in topics)
    assert example["scenario_label"] == "주간 생산·프로젝트 리스크 및 실행계획 승인·게시 업무"
    design_signals = example["expected_design_signals"]
    assert {"as_is_focus", "to_be_required_branches", "catalog_search_keywords"} == set(design_signals)
    assert all(design_signals[key] for key in design_signals)
    unsafe_markers = ("password", "api_key", "bearer ", "sk-", "-----begin")
    for item in answer_examples:
        assert set(item) == {"topic", "likely_question", "answer_to_enter"}
        for value in item.values():
            assert isinstance(value, str)
            assert value.strip()
            assert len(value) <= 1_000
            assert not any(marker in value.lower() for marker in unsafe_markers)

    skip_guidance = example["clarification_skip_guidance"]
    assert set(skip_guidance) == {"action_label", "when_to_use", "result"}
    assert skip_guidance["action_label"] == "추가 입력 건너뛰기"
    assert "Cancel" in skip_guidance["result"]
    assert all(isinstance(value, str) and value.strip() for value in skip_guidance.values())

    flow = _json(PROJECT_ROOT / "flows" / "F10_work_definition_parent.json")
    request_node = _flow_node(flow, "request_envelope")
    assert request_node["template"]["team_name"]["value"] == example["team_name"]
    assert request_node["template"]["employee_id"]["value"] == example["employee_id"]
    assert request_node["template"]["catalog_scope_id"]["value"] == "default"
    assert request_node["template"]["request_text"]["value"] in {"", None}
    assert request_node["template"]["additional_prompt"]["value"] in {"", None}
    assert "tenant_id" not in request_node["template"]
    assert "owner_id" not in request_node["template"]
    assert "session_id" not in request_node["template"]

    work_input = _flow_node(flow, "work_description_text_input")
    prompt_input = _flow_node(flow, "additional_design_prompt_text_input")
    assert work_input["template"]["input_value"]["value"] == example["request_text"]
    assert prompt_input["template"]["input_value"]["value"] == example["additional_prompt"]


def test_f00_flow_keeps_file_upload_empty_and_points_to_the_committed_example() -> None:
    flow = _json(PROJECT_ROOT / "flows" / "F00_catalog_file_vector_ingest.json")
    loader_node = _flow_node(flow, "catalog_loader")
    catalog_file = loader_node["template"]["catalog_file"]
    assert catalog_file["value"] in {"", None}
    assert "samples/f00_catalog_assets_example.json" in catalog_file["info"]

    embedding_node = _flow_node(flow, "embedding_model")
    assert embedding_node["display_name"] == "Embedding Model"

    writer_node = _flow_node(flow, "mongodb_vector_writer")
    assert writer_node["display_name"] == "02 MongoDB Catalog Vector Writer"
    assert writer_node["template"]["mongodb_uri"]["value"] == "MONGO_URL"
    assert writer_node["template"]["mongodb_uri"]["load_from_db"] is True
    assert {"embedding_model", "embedding_version", "embedding_dimension"}.isdisjoint(writer_node["template"])


def test_skill_example_matches_runtime_contract_and_seed_validation() -> None:
    payload = _json(SAMPLES / "skill_registry_example.json")
    skills = payload["items"]
    assert len(skills) == 1
    skill = skills[0]
    assert skill["tenant_id"] == "default"
    actual_hash = "sha256:" + hashlib.sha256(skill["prompt_text"].encode("utf-8")).hexdigest()
    assert skill["prompt_sha256"] == actual_hash

    resolver = _load_module("test_example_skill_component", SKILL_COMPONENT)
    assert resolver._registry_contract_error(skill) == ""
    seed = _load_module("test_example_skill_seed", SEED_SCRIPT)
    verifier = _load_module("test_example_skill_verifier", VERIFY_SCRIPT)
    assert seed.validate_skill(skill) == skill
    verifier._validate_skill_content(skill, skill)
    tampered_stored_skill = deepcopy(skill)
    tampered_stored_skill["trigger_rules"] = [{"kind": "contains", "value": "tampered"}]
    with pytest.raises(RuntimeError):
        verifier._validate_skill_content(tampered_stored_skill, skill)

    invalid_forbidden = deepcopy(skill)
    invalid_forbidden["forbidden_actions"] = ["x" * 129]
    invalid_acl = deepcopy(skill)
    invalid_acl["acl"]["groups"] = ["x" * 129]
    invalid_rule_count = deepcopy(skill)
    invalid_rule_count["trigger_rules"] = [{"kind": "contains", "value": f"rule-{index}"} for index in range(101)]
    for invalid in (invalid_forbidden, invalid_acl, invalid_rule_count):
        with pytest.raises(ValueError):
            seed.validate_skill(invalid)
