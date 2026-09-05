from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from importlib.metadata import version
from lfx.custom.custom_component.component import Component
from lfx.custom.utils import build_custom_component_template
from lfx.graph import Graph
from lfx.schema import Data


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FLOW_ROOT = PROJECT_ROOT / "flows"
FLOW_FILES = {
    "F00": "F00_catalog_file_vector_ingest.json",
    "F10": "F10_work_definition_parent.json",
    "F20": "F20_agent_blueprint_design.json",
    "F30": "F30_responsive_report.json",
    "F90": "F90_search_evaluation.json",
}
BUNDLE_FILE = FLOW_ROOT / "00_business_work_design_ALL_FLOWS.json"
STICKY_NOTE_COUNTS = {
    "F00": 2,
    "F10": 6,
    "F20": 4,
    "F30": 1,
    "F90": 2,
}
MONGODB_URI_GLOBAL_VARIABLE = "MONGO_URL"
MONGODB_URI_NODE_COUNTS = {
    "F00": 1,
    "F10": 13,
    "F20": 1,
    "F30": 0,
    "F90": 1,
}


def _minor_version(value: str) -> tuple[int, int]:
    parts = value.split(".")
    assert len(parts) >= 3 and all(part.isdigit() for part in parts)
    return int(parts[0]), int(parts[1])


def _runtime_versions() -> tuple[str, str]:
    langflow_version = version("langflow")
    lfx_version = version("lfx")
    assert _minor_version(langflow_version) == (1, 11)
    assert _minor_version(lfx_version) == (1, 11)
    return langflow_version, lfx_version


def _generator_versions() -> tuple[str, str]:
    """Read the pinned runtime used to serialize the checked-in Flow exports.

    The production server deliberately validates import/build compatibility on
    Langflow 1.11.0, while the checked-in JSON is byte-for-byte generated in
    the pinned 1.11.1 template runtime.  These patch releases are compatible
    but produce different serialized template layouts, so tests must not treat
    the active validation runtime as the export generator.
    """

    manifest = _load(FLOW_ROOT / "build_manifest.json")
    langflow_version = str(manifest["langflow_version"])
    lfx_version = str(manifest["lfx_version"])
    assert _minor_version(langflow_version) == (1, 11)
    assert _minor_version(lfx_version) == (1, 11)
    return langflow_version, lfx_version


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


@pytest.fixture(scope="module")
def flows() -> dict[str, dict[str, Any]]:
    return {key: _load(FLOW_ROOT / filename) for key, filename in FLOW_FILES.items()}


def _nodes(flow: dict[str, Any]) -> dict[str, dict[str, Any]]:
    nodes = flow["data"]["nodes"]
    return {node["id"]: node for node in nodes}


def _execution_nodes(flow: dict[str, Any]) -> list[dict[str, Any]]:
    """Return nodes Langflow treats as executable graph vertices."""

    return [node for node in flow["data"]["nodes"] if node.get("type") != "noteNode"]


def _sticky_notes(flow: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        node
        for node in flow["data"]["nodes"]
        if node.get("type") == "noteNode" and node.get("data", {}).get("type") == "note"
    ]


def _types(flow: dict[str, Any]) -> list[str]:
    return [node["data"]["type"] for node in flow["data"]["nodes"]]


def _custom_nodes(flow: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        wrapper
        for wrapper in flow["data"]["nodes"]
        if wrapper["data"]["node"].get("metadata", {}).get("standalone") is True
    ]


def _node_by_source(flow: dict[str, Any], filename: str) -> list[dict[str, Any]]:
    return [
        wrapper
        for wrapper in _custom_nodes(flow)
        if wrapper["data"]["node"]["metadata"]["standalone_source_path"].endswith("/" + filename)
    ]


def _nodes_with_value(
    flow: dict[str, Any],
    filename: str,
    field_name: str,
    expected: Any,
) -> list[dict[str, Any]]:
    return [
        wrapper
        for wrapper in _node_by_source(flow, filename)
        if wrapper["data"]["node"]["template"].get(field_name, {}).get("value") == expected
    ]


def _node_by_key(flow: dict[str, Any], key: str) -> dict[str, Any]:
    matches = [
        wrapper
        for wrapper in flow["data"]["nodes"]
        if wrapper["data"]["node"].get("metadata", {}).get("flow_node_key") == key
    ]
    assert len(matches) == 1, key
    return matches[0]


def _has_edge(flow: dict[str, Any], source_type: str, output_name: str, target_type: str, field_name: str) -> bool:
    nodes = _nodes(flow)
    for edge in flow["data"]["edges"]:
        source = nodes[edge["source"]]["data"]["type"]
        target = nodes[edge["target"]]["data"]["type"]
        source_name = edge["data"]["sourceHandle"]["name"]
        target_name = edge["data"]["targetHandle"]["fieldName"]
        if (source, source_name, target, target_name) == (source_type, output_name, target_type, field_name):
            return True
    return False


def test_exact_resolved_runtime() -> None:
    _runtime_versions()


@pytest.mark.parametrize("flow_key", tuple(FLOW_FILES))
def test_langflow_graph_deserializes_every_export(flows: dict[str, dict[str, Any]], flow_key: str) -> None:
    flow = flows[flow_key]
    graph = Graph.from_payload(
        flow["data"],
        flow_id=flow["id"],
        flow_name=flow["name"],
        user_id="flow-export-contract-test",
    )
    # Langflow correctly ignores Canvas-only Sticky Notes when materializing
    # execution vertices, so compare the graph only to generic executable nodes.
    assert len(graph.vertices) == len(_execution_nodes(flow))
    assert len(graph.edges) == len(flow["data"]["edges"])


def test_expected_exports_exist_and_have_unique_ids(flows: dict[str, dict[str, Any]]) -> None:
    assert set(flows) == set(FLOW_FILES)
    assert all((FLOW_ROOT / filename).is_file() for filename in FLOW_FILES.values())
    flow_ids = [flow["id"] for flow in flows.values()]
    assert len(flow_ids) == len(set(flow_ids))
    langflow_version, _ = _generator_versions()
    assert all(flow["last_tested_version"] == langflow_version for flow in flows.values())


@pytest.mark.parametrize("flow_key", tuple(FLOW_FILES))
def test_edges_resolve_to_real_compatible_handles(flows: dict[str, dict[str, Any]], flow_key: str) -> None:
    flow = flows[flow_key]
    nodes = _nodes(flow)
    assert len(nodes) == len(flow["data"]["nodes"])
    edge_ids: set[str] = set()
    for edge in flow["data"]["edges"]:
        assert edge["id"] not in edge_ids
        edge_ids.add(edge["id"])
        assert edge["source"] in nodes
        assert edge["target"] in nodes
        source_handle = edge["data"]["sourceHandle"]
        target_handle = edge["data"]["targetHandle"]
        assert source_handle["id"] == edge["source"]
        assert target_handle["id"] == edge["target"]
        source_node = nodes[edge["source"]]["data"]["node"]
        target_node = nodes[edge["target"]]["data"]["node"]
        outputs = {item["name"]: item for item in source_node["outputs"]}
        assert source_handle["name"] in outputs
        target_field = target_node["template"][target_handle["fieldName"]]
        output_types = set(outputs[source_handle["name"]]["types"])
        input_types = set(target_field["input_types"])
        assert output_types.intersection(input_types), edge["id"]
        # Langflow 1.11.1 removes edges that target advanced inputs while a
        # Flow is loaded into the Canvas, so such exports are not import-safe.
        assert target_field.get("advanced") is not True, edge["id"]
        assert source_handle["output_types"] == outputs[source_handle["name"]]["types"]
        assert target_handle["inputTypes"] == target_field["input_types"]
        assert edge["sourceHandle"].startswith("{œdataTypeœ:")
        assert edge["targetHandle"].startswith("{œfieldNameœ:")


@pytest.mark.parametrize("flow_key", tuple(FLOW_FILES))
def test_sticky_notes_describe_stages_without_affecting_execution(
    flows: dict[str, dict[str, Any]], flow_key: str
) -> None:
    flow = flows[flow_key]
    notes = _sticky_notes(flow)
    assert len(notes) == STICKY_NOTE_COUNTS[flow_key]
    note_ids = {note["id"] for note in notes}
    assert len(note_ids) == len(notes)
    for note in notes:
        data = note["data"]
        node = data["node"]
        assert data["id"] == note["id"]
        assert data["type"] == "note"
        assert node["display_name"] == ""
        assert node["documentation"] == ""
        assert node["lf_version"] == _generator_versions()[0]
        assert node["description"].startswith("## ")
        assert node["template"]["backgroundColor"] in {"blue", "amber"}
        assert note["positionAbsolute"] == note["position"]
        assert note["style"] == {"height": note["height"], "width": note["width"]}
        assert note["width"] > 0 and note["height"] > 0
        assert note["dragging"] is False
        assert note["resizing"] is False
        assert note["selected"] is False
    assert all(edge["source"] not in note_ids and edge["target"] not in note_ids for edge in flow["data"]["edges"])
    assert flow["metadata"]["sticky_note_count"] == len(notes)


@pytest.mark.parametrize("flow_key", tuple(FLOW_FILES))
def test_custom_source_is_byte_identical_and_hash_bound(flows: dict[str, dict[str, Any]], flow_key: str) -> None:
    custom_nodes = _custom_nodes(flows[flow_key])
    assert custom_nodes
    for wrapper in custom_nodes:
        node = wrapper["data"]["node"]
        metadata = node["metadata"]
        source_path = PROJECT_ROOT / metadata["standalone_source_path"]
        source_bytes = source_path.read_bytes()
        embedded = node["template"]["code"]["value"].encode("utf-8")
        assert embedded == source_bytes
        assert metadata["standalone_source_sha256"] == hashlib.sha256(source_bytes).hexdigest()
        assert node["lf_version"] == _generator_versions()[0]


@pytest.mark.parametrize("flow_key", tuple(FLOW_FILES))
def test_every_embedded_custom_source_builds_with_langflow_1_11(
    flows: dict[str, dict[str, Any]], flow_key: str
) -> None:
    for wrapper in _custom_nodes(flows[flow_key]):
        exported = wrapper["data"]["node"]
        source = exported["template"]["code"]["value"]
        rebuilt, instance = build_custom_component_template(Component(_code=source))
        assert rebuilt["display_name"] == exported["display_name"]
        assert str(getattr(instance, "name", "") or type(instance).__name__.removesuffix("Component")) == wrapper["data"]["type"]
        expected_outputs = [(item["name"], item["types"]) for item in exported["outputs"]]
        actual_outputs = [(item["name"], item["types"]) for item in rebuilt["outputs"]]
        assert actual_outputs == expected_outputs


def test_hitl_is_top_level_only(flows: dict[str, dict[str, Any]]) -> None:
    assert "HumanInput" not in _types(flows["F00"])
    assert flows["F00"]["metadata"]["contains_native_hitl"] is True
    assert (
        flows["F00"]["metadata"]["native_hitl_execution_requirement"]
        == "durable_langflow_background_job_required_for_continuation_card"
    )
    # Three bounded answer cards plus one button-only final approval gate.
    assert _types(flows["F10"]).count("HumanInput") == 1
    assert len(_node_by_source(flows["F10"], "42_f10_clarification_answer_gate.py")) == 3
    for flow_key in ("F20", "F30", "F90"):
        assert "HumanInput" not in _types(flows[flow_key])
        assert flows[flow_key]["metadata"]["contains_native_hitl"] is False


def test_f00_visibly_loads_chunks_embeds_and_writes_mongodb(flows: dict[str, dict[str, Any]]) -> None:
    flow = flows["F00"]
    assert len(_execution_nodes(flow)) == 6
    assert len(_sticky_notes(flow)) == 2
    assert len(flow["data"]["edges"]) == 5
    assert len(_node_by_source(flow, "00_catalog_json_loader.py")) == 1
    assert len(_node_by_source(flow, "01_catalog_deterministic_chunker.py")) == 1
    assert len(_node_by_source(flow, "02_catalog_mongodb_vector_writer.py")) == 1
    writer = _node_by_source(flow, "02_catalog_mongodb_vector_writer.py")[0]
    writer_template = writer["data"]["node"]["template"]
    assert writer_template["dry_run"]["display_name"] == "테스트 실행 (저장하지 않음)"
    assert writer_template["dry_run"]["value"] is True
    assert writer_template["confirm_complete_catalog_snapshot"]["display_name"] == "전체 카탈로그 파일 확인 (실제 저장용)"
    assert writer_template["confirm_complete_catalog_snapshot"]["value"] is False
    assert writer_template["resume_verified_partial_snapshot"]["value"] is True
    assert writer_template["pause_for_next_batch"]["value"] is True
    assert writer_template["max_embedding_chunks_per_run"]["value"] == 80
    assert writer_template["embedding_run_time_budget_seconds"]["value"] == 180
    assert writer_template["mongo_write_batch_size"]["value"] == 10
    assert writer_template["mongodb_database"]["value"] == "business_work_design"
    assert [node["data"]["type"] for node in _execution_nodes(flow)] == [
        "CatalogJsonLoader",
        "CatalogDeterministicChunker",
        "EmbeddingModel",
        "CatalogMongoDBVectorWriter",
        "ParseData",
        "ChatOutput",
    ]
    assert _has_edge(flow, "CatalogJsonLoader", "catalog_bundle", "CatalogDeterministicChunker", "catalog_bundle")
    assert _has_edge(flow, "CatalogDeterministicChunker", "chunk_bundle", "CatalogMongoDBVectorWriter", "chunk_bundle")
    assert _has_edge(flow, "EmbeddingModel", "embeddings", "CatalogMongoDBVectorWriter", "embedding")
    assert _has_edge(flow, "CatalogMongoDBVectorWriter", "ingestion_result", "ParseData", "data")
    assert _has_edge(flow, "ParseData", "text", "ChatOutput", "input_value")
    assert "activation_handoff_contract" not in flow["metadata"]
    assert flow["metadata"]["operational_readiness"] == "mongodb_vector_ingestion_configuration_required"
    required = flow["metadata"]["required_configuration"]
    assert any("MongoDB" in item for item in required)
    assert any("embedding" in item for item in required)
    notes = "\n".join(note["data"]["node"]["description"] for note in _sticky_notes(flow))
    assert "native HITL" in notes
    assert "계속 적재" in notes
    assert "durable background job" in notes

    forbidden_sources = {
        "00_catalog_file_vector_ingest.py",
        "00_catalog_file_intake.py",
        "01_catalog_secret_scanner.py",
        "09_catalog_pipeline_worker_client.py",
        "33_catalog_activation_approval_client.py",
    }
    assert all(not _node_by_source(flow, filename) for filename in forbidden_sources)
    serialized = json.dumps(flow, ensure_ascii=False).lower()
    assert "humaninput" not in serialized
    assert "worker_server_url" not in serialized
    assert "activation gateway" not in serialized
    assert "other flow" not in serialized


def test_exported_mongodb_database_defaults_are_unified(flows: dict[str, dict[str, Any]]) -> None:
    expected_fields = {
        "F10": {
            "13_clarification_batch_builder.py": "mongo_database",
            "18_work_definition_store.py": "mongo_database",
            "36_approved_design_invocation_loader.py": "mongo_database",
            "39_f10_answer_commit.py": "mongo_database",
            "47_f10_chat_answer_resume_loader.py": "mongo_database",
        },
        "F20": {"21_catalog_hybrid_retriever.py": "database_name"},
        "F90": {"21_catalog_hybrid_retriever.py": "database_name"},
    }
    for flow_key, source_fields in expected_fields.items():
        for source_name, field_name in source_fields.items():
            nodes = _node_by_source(flows[flow_key], source_name)
            assert nodes, f"{flow_key}:{source_name}"
            for node in nodes:
                assert node["data"]["node"]["template"][field_name]["value"] == "business_work_design"

    for source_name in (
        "13_clarification_batch_builder.py",
        "39_f10_answer_commit.py",
        "47_f10_chat_answer_resume_loader.py",
    ):
        for node in _node_by_source(flows["F10"], source_name):
            template = node["data"]["node"]["template"]
            assert template["mongodb_uri"]["advanced"] is False
            assert template["mongo_database"]["advanced"] is False


def test_all_mongodb_uri_inputs_bind_the_shared_secret_global_variable(
    flows: dict[str, dict[str, Any]],
) -> None:
    """Every active MongoDB node must reuse the same exported secret binding."""

    for flow_key, expected_count in MONGODB_URI_NODE_COUNTS.items():
        matches: list[tuple[str, dict[str, Any]]] = []
        for wrapper in _execution_nodes(flows[flow_key]):
            template = wrapper["data"]["node"].get("template", {})
            field = template.get("mongodb_uri")
            if isinstance(field, dict):
                matches.append((wrapper["id"], field))
        assert len(matches) == expected_count, flow_key
        for node_id, field in matches:
            assert field.get("_input_type") == "SecretStrInput", node_id
            assert field.get("load_from_db") is True, node_id
            assert field.get("value") == MONGODB_URI_GLOBAL_VARIABLE, node_id


def test_f10_contains_extraction_clarification_merge_preview_and_approval_store(
    flows: dict[str, dict[str, Any]]
) -> None:
    flow = flows["F10"]
    for filename in (
        "10_work_request_envelope.py",
        "11_work_definition_normalizer.py",
        "12_work_completeness_evaluator.py",
        "13_clarification_batch_builder.py",
        "16_work_graph_normalizer.py",
        "17_work_preview_hasher.py",
        "18_work_definition_store.py",
        "45_f10_authentication_context.py",
        "36_approved_design_invocation_loader.py",
        "44_f10_report_handoff_gate.py",
        "39_f10_answer_commit.py",
        "40_f10_review_entry_joiner.py",
        "41_f10_terminal_result_message.py",
        "42_f10_clarification_answer_gate.py",
        "43_f10_final_approval_route_gate.py",
        "46_f10_numbered_chat_answer_parser.py",
        "47_f10_chat_answer_resume_loader.py",
        "48_f10_chat_answer_next_router.py",
        "49_f10_playground_entry_router.py",
    ):
        assert _node_by_source(flow, filename), filename
    assert len(_execution_nodes(flow)) == 47
    assert len(_sticky_notes(flow)) == 6
    assert len(flow["data"]["edges"]) == 126
    assert len(_node_by_source(flow, "12_work_completeness_evaluator.py")) == 3
    assert len(_node_by_source(flow, "13_clarification_batch_builder.py")) == 3
    assert len(_node_by_source(flow, "39_f10_answer_commit.py")) == 4
    assert len(_node_by_source(flow, "17_work_preview_hasher.py")) == 1
    assert len(_node_by_source(flow, "18_work_definition_store.py")) == 4
    # Team, work description, employee ID, and additional design prompt are
    # visible Canvas inputs.  The employee ID is shared by initial intake and
    # numbered-chat answer resume so another employee cannot consume the
    # pending clarification batch.
    assert _types(flow).count("TextInput") == 4
    assert _types(flow).count("ChatInput") == 1
    assert _types(flow).count("LanguageModel") == 4
    assert _types(flow).count("HumanInput") == 1
    assert len(_node_by_source(flow, "42_f10_clarification_answer_gate.py")) == 3
    assert len(_node_by_source(flow, "46_f10_numbered_chat_answer_parser.py")) == 1
    assert len(_node_by_source(flow, "47_f10_chat_answer_resume_loader.py")) == 1
    assert len(_node_by_source(flow, "48_f10_chat_answer_next_router.py")) == 1
    assert len(_node_by_source(flow, "49_f10_playground_entry_router.py")) == 1
    assert len(_node_by_source(flow, "43_f10_final_approval_route_gate.py")) == 1
    assert len(_node_by_source(flow, "45_f10_authentication_context.py")) == 1
    rounds = {
        node["data"]["node"]["template"]["round_number"]["value"]
        for node in _node_by_source(flow, "13_clarification_batch_builder.py")
    }
    assert rounds == {1, 2, 3}
    question_limits = {
        node["data"]["node"]["template"]["round_number"]["value"]: node["data"]["node"]["template"]["max_questions"]["value"]
        for node in _node_by_source(flow, "13_clarification_batch_builder.py")
    }
    assert question_limits == {1: 3, 2: 3, 3: 4}
    for round_number in (1, 2, 3):
        model = _node_by_key(flow, f"clarification_model_r{round_number}")
        expected_limit = 4 if round_number == 3 else 3
        assert model["data"]["node"]["template"]["system_message"]["value"] == (
            f"Return one JSON object with at most {expected_limit} questions."
        )
    request_envelope = _node_by_key(flow, "request_envelope")
    team_text_input = _node_by_key(flow, "team_name_text_input")
    work_text_input = _node_by_key(flow, "work_description_text_input")
    employee_text_input = _node_by_key(flow, "employee_id_text_input")
    additional_text_input = _node_by_key(flow, "additional_design_prompt_text_input")
    playground_input = _node_by_key(flow, "playground_entry_input")
    playground_router = _node_by_key(flow, "playground_entry_router")
    assert any(
        edge["source"] == playground_input["id"]
        and edge["target"] == playground_router["id"]
        and edge["data"]["sourceHandle"]["name"] == "message"
        and edge["data"]["targetHandle"]["fieldName"] == "message"
        for edge in flow["data"]["edges"]
    )
    assert any(
        edge["source"] == playground_router["id"]
        and edge["target"] == request_envelope["id"]
        and edge["data"]["sourceHandle"]["name"] == "new_work_path"
        and edge["data"]["targetHandle"]["fieldName"] == "start_trigger"
        for edge in flow["data"]["edges"]
    )
    assert any(
        edge["source"] == team_text_input["id"]
        and edge["target"] == request_envelope["id"]
        and edge["data"]["sourceHandle"]["name"] == "text"
        and edge["data"]["targetHandle"]["fieldName"] == "team_name"
        for edge in flow["data"]["edges"]
    )
    assert any(
        edge["source"] == work_text_input["id"]
        and edge["target"] == request_envelope["id"]
        and edge["data"]["sourceHandle"]["name"] == "text"
        and edge["data"]["targetHandle"]["fieldName"] == "request_text"
        for edge in flow["data"]["edges"]
    )
    assert any(
        edge["source"] == employee_text_input["id"]
        and edge["target"] == request_envelope["id"]
        and edge["data"]["sourceHandle"]["name"] == "text"
        and edge["data"]["targetHandle"]["fieldName"] == "employee_id"
        for edge in flow["data"]["edges"]
    )
    assert any(
        edge["source"] == additional_text_input["id"]
        and edge["target"] == request_envelope["id"]
        and edge["data"]["sourceHandle"]["name"] == "text"
        and edge["data"]["targetHandle"]["fieldName"] == "additional_prompt"
        for edge in flow["data"]["edges"]
    )
    assert len(_nodes_with_value(flow, "18_work_definition_store.py", "command", "review_and_request_approval")) == 1
    for round_number in (1, 2, 3):
        planner = _node_by_key(flow, f"clarification_planner_r{round_number}")
        batch = _node_by_key(flow, f"clarification_batch_r{round_number}")
        gate = _node_by_key(flow, f"answer_gate_r{round_number}")
        commit = _node_by_key(flow, f"answer_commit_r{round_number}")
        assert any(
            edge["source"] == planner["id"]
            and edge["target"] == batch["id"]
            and edge["data"]["sourceHandle"]["name"] == "clarification_path"
            and edge["data"]["targetHandle"]["fieldName"] == "work_definition"
            for edge in flow["data"]["edges"]
        )
        assert any(
            edge["source"] == batch["id"]
            and edge["target"] == gate["id"]
            and edge["data"]["sourceHandle"]["name"] == "waiting_path"
            and edge["data"]["targetHandle"]["fieldName"] == "clarification_batch"
            for edge in flow["data"]["edges"]
        )
        # The deployed 1.11.0 card has no dynamic answer fields.  It ends
        # the current run with a readable template; normal Chat Input later
        # goes through 49 → 47 → 46 → the dedicated chat answer commit.
        assert not any(
            edge["source"] == gate["id"]
            and edge["target"] == commit["id"]
            and edge["data"]["sourceHandle"]["name"] in {"answer_submission", "branch_submit_answers"}
            for edge in flow["data"]["edges"]
        )
        for source, output_name in ((planner, "blocked_path"), (batch, "blocked_path")):
            assert any(
                edge["source"] == source["id"]
                and edge["target"] == _node_by_key(flow, "terminal_result_message")["id"]
                and edge["data"]["sourceHandle"]["name"] == output_name
                and edge["data"]["targetHandle"]["fieldName"] == "terminal_events"
                for edge in flow["data"]["edges"]
            )
        assert any(
            edge["source"] == gate["id"]
            and edge["target"] == _node_by_key(flow, "terminal_result_message")["id"]
            and edge["data"]["sourceHandle"]["name"] == "branch_continue_chat"
            and edge["data"]["targetHandle"]["fieldName"] == "terminal_events"
            for edge in flow["data"]["edges"]
        )
        assert any(
            edge["source"] == gate["id"]
            and edge["target"] == commit["id"]
            and edge["data"]["sourceHandle"]["name"] == "branch_skip_additional_input"
            and edge["data"]["targetHandle"]["fieldName"] == "skip_trigger"
            for edge in flow["data"]["edges"]
        )
        # Skip is a deliberate review entry, not a silent cancel, a fourth
        # question round, or a direct approval bypass.  Component 39 owns the
        # persisted skip audit and emits its existing review_path.
        assert any(
            edge["source"] == commit["id"]
            and edge["target"] == _node_by_key(flow, "review_entry_joiner")["id"]
            and edge["data"]["sourceHandle"]["name"] == "review_path"
            and edge["data"]["targetHandle"]["fieldName"] == f"round{round_number}_answer_review"
            for edge in flow["data"]["edges"]
        )
        assert any(
            edge["source"] == gate["id"]
            and edge["target"] == _node_by_key(flow, "terminal_result_message")["id"]
            and edge["data"]["sourceHandle"]["name"] == "blocked_path"
            and edge["data"]["targetHandle"]["fieldName"] == "terminal_events"
            for edge in flow["data"]["edges"]
        )

    chat_loader = _node_by_key(flow, "chat_answer_resume_loader")
    chat_parser = _node_by_key(flow, "numbered_chat_answer_parser")
    chat_commit = _node_by_key(flow, "chat_answer_commit")
    chat_next_router = _node_by_key(flow, "chat_answer_next_router")
    review_joiner = _node_by_key(flow, "review_entry_joiner")
    expected_chat_edges = (
        (playground_router, "answer_path", chat_loader, "answer_text"),
        (playground_router, "answer_path", chat_parser, "answer_text"),
        (employee_text_input, "text", chat_loader, "employee_id"),
        (employee_text_input, "text", chat_parser, "actor_id"),
        (employee_text_input, "text", chat_commit, "actor_id"),
        (chat_loader, "success_path", chat_parser, "clarification_batch"),
        (chat_loader, "success_path", chat_commit, "clarification_context"),
        (chat_loader, "success_path", chat_commit, "clarification_batch"),
        (chat_parser, "answer_submission", chat_commit, "native_answer_submission"),
        (chat_parser, "submit_trigger", chat_commit, "submit_trigger"),
        (chat_commit, "next_round_path", chat_next_router, "answer_commit"),
        (chat_next_router, "round2_path", _node_by_key(flow, "clarification_planner_r2"), "work_definition"),
        (chat_next_router, "round3_path", _node_by_key(flow, "clarification_planner_r3"), "work_definition"),
        (chat_commit, "review_path", review_joiner, "chat_answer_review"),
    )
    for source, output_name, target, field_name in expected_chat_edges:
        assert any(
            edge["source"] == source["id"]
            and edge["target"] == target["id"]
            and edge["data"]["sourceHandle"]["name"] == output_name
            and edge["data"]["targetHandle"]["fieldName"] == field_name
            for edge in flow["data"]["edges"]
        ), (source["data"]["node"].get("metadata", {}).get("flow_node_key"), output_name, field_name)

    assert not _node_by_source(flow, "14_work_answer_loader.py")
    assert not _node_by_source(flow, "15_work_answer_merger.py")
    assert not _node_by_source(flow, "27_work_clarification_router.py")
    assert not _node_by_source(flow, "28_work_definition_branch_joiner.py")
    assert not _node_by_source(flow, "34_work_runtime_state_store.py")
    assert not _node_by_source(flow, "35_result_gate.py")

    assert _has_edge(flow, "F10ReviewEntryJoiner", "review_work_definition", "WorkGraphNormalizer", "work_definition")
    assert _has_edge(flow, "WorkGraphNormalizer", "success_path", "WorkPreviewHasher", "work_definition")
    assert _has_edge(flow, "WorkPreviewHasher", "success_path", "WorkDefinitionStore", "work_definition")
    review_approval_store = _node_by_key(flow, "review_approval_store")
    assert review_approval_store["data"]["node"]["display_name"] == "18 업무 정의 상태 저장"
    assert review_approval_store["data"]["node"]["template"]["command"]["value"] == "review_and_request_approval"
    assert any(
        edge["source"] == _node_by_key(flow, "preview")["id"]
        and edge["target"] == review_approval_store["id"]
        and edge["data"]["sourceHandle"]["name"] == "success_path"
        and edge["data"]["targetHandle"]["fieldName"] == "work_definition"
        for edge in flow["data"]["edges"]
    )
    review_template = review_approval_store["data"]["node"]["template"]
    assert review_template["expected_revision"]["advanced"] is True
    assert review_template["expected_revision"]["required"] is False
    assert review_template["idempotency_key"]["advanced"] is True
    assert "work_collection" not in review_template
    assert "event_collection" not in review_template
    guide = next(note for note in _sticky_notes(flow) if note["id"] == "note-f10-03a-review-save-approval-input-guide")
    assert "자동값" in guide["data"]["node"]["description"]
    assert "work_definition_events" in guide["data"]["node"]["description"]
    hitl_example_note = next(note for note in _sticky_notes(flow) if note["id"] == "note-f10-02-three-round-hitl")
    hitl_example_text = hitl_example_note["data"]["node"]["description"]
    assert "Playground 채팅창에 보낼 답변 예시" in hitl_example_text
    assert "답변 입력하기" in hitl_example_text
    assert "1번: ..." in hitl_example_text
    assert "추가 입력 건너뛰기" in hitl_example_text
    assert "Cancel" in hitl_example_text
    example = json.loads((PROJECT_ROOT / "samples" / "f10_work_request_example.json").read_text(encoding="utf-8"))
    for item in example["clarification_answer_examples"]:
        assert item["topic"] in hitl_example_text
        assert item["likely_question"] in hitl_example_text
        assert item["answer_to_enter"] in hitl_example_text
    for value in example["clarification_skip_guidance"].values():
        assert value in hitl_example_text
    for command, branch in (
        ("approve", "branch_approve"),
        ("reject", "branch_reject"),
        ("cancel", "branch_cancel"),
    ):
        target_key = {
            "approve": "approved_work_store",
            "reject": "rejected_work_store",
            "cancel": "final_cancel_store",
        }[command]
        target = _node_by_key(flow, target_key)
        assert target["data"]["node"]["template"]["command"]["value"] == command
        incoming = [edge for edge in flow["data"]["edges"] if edge["target"] == target["id"]]
        assert any(
            edge["source"] == review_approval_store["id"]
            and edge["data"]["sourceHandle"]["name"] == "success_path"
            and edge["data"]["targetHandle"]["fieldName"] == "work_definition"
            for edge in incoming
        )
        assert any(
            edge["source"] == _node_by_key(flow, "final_approval_route_gate")["id"]
            and edge["data"]["sourceHandle"]["name"] == branch
            and edge["data"]["targetHandle"]["fieldName"] == "route_trigger"
            for edge in incoming
        )
        assert any(
            edge["data"]["sourceHandle"]["name"] == branch
            and edge["data"]["targetHandle"]["fieldName"] == "route_trigger"
            for edge in incoming
        )
        assert any(
            edge["source"] == employee_text_input["id"]
            and edge["data"]["sourceHandle"]["name"] == "text"
            and edge["data"]["targetHandle"]["fieldName"] == "actor_id"
            for edge in incoming
        )

    approval_gate = _node_by_key(flow, "approval_gate")
    final_route_gate = _node_by_key(flow, "final_approval_route_gate")
    assert final_route_gate["data"]["node"]["template"]["approval_triggers"]["list"] is True
    for branch in ("branch_approve", "branch_reject", "branch_cancel"):
        assert any(
            edge["source"] == approval_gate["id"]
            and edge["target"] == final_route_gate["id"]
            and edge["data"]["sourceHandle"]["name"] == branch
            and edge["data"]["targetHandle"]["fieldName"] == "approval_triggers"
            for edge in flow["data"]["edges"]
        )

    terminal = _node_by_key(flow, "terminal_result_message")
    assert terminal["data"]["node"]["template"]["terminal_events"]["list"] is True
    terminal_incoming = [edge for edge in flow["data"]["edges"] if edge["target"] == terminal["id"]]
    assert len(terminal_incoming) == 36
    assert {edge["data"]["targetHandle"]["fieldName"] for edge in terminal_incoming} == {"terminal_events"}


def _build_f10_terminal_case(
    flow: dict[str, Any],
    *,
    source_key: str,
    source_output: str,
    event: dict[str, Any],
) -> str:
    """Build F10's real list fan-in with one selected terminal branch.

    The other 35 terminal predecessors are marked conditionally excluded,
    matching the grouped F10 route components at runtime.  The selected
    predecessor is seeded so this contract test exercises Langflow's list
    input resolver without requiring MongoDB, an LLM, or Human Input.  It
    protects against the prior ``has not been built yet`` error where a list
    input tried to pull a result from an unselected branch.
    """

    graph = Graph.from_payload(
        flow["data"],
        flow_id=flow["id"],
        flow_name=flow["name"],
        user_id="f10-terminal-branch-contract-test",
    )
    terminal = _node_by_key(flow, "terminal_result_message")
    source = _node_by_key(flow, source_key)
    terminal_vertex = graph.get_vertex(terminal["id"])
    source_vertex = graph.get_vertex(source["id"])
    predecessors = graph.get_predecessors(terminal_vertex)
    assert len(predecessors) == 36

    for predecessor in predecessors:
        if predecessor.id != source_vertex.id:
            graph.conditionally_excluded_vertices.add(predecessor.id)
    source_vertex.built = True
    source_vertex.results[source_output] = Data(data=event)

    asyncio.run(graph.build_vertex(terminal_vertex.id))
    assert terminal_vertex.built is True
    message = terminal_vertex.results["message"]
    return str(getattr(message, "text", ""))


def test_f10_terminal_graph_handles_early_block_without_unbuilt_predecessor(
    flows: dict[str, dict[str, Any]],
) -> None:
    message = _build_f10_terminal_case(
        flows["F10"],
        source_key="clarification_planner_r1",
        source_output="blocked_path",
        event={
            "ok": False,
            "status": "BLOCKED",
            "error": {"code": "QUESTION_GENERATION_BLOCKED", "message": "safe failure"},
            "trace_id": "early-block",
        },
    )
    assert "추가 질문을 준비할 수 없어" in message
    assert "QUESTION_GENERATION_BLOCKED" in message


def test_f10_terminal_graph_handles_final_rejection_without_unbuilt_predecessor(
    flows: dict[str, dict[str, Any]],
) -> None:
    message = _build_f10_terminal_case(
        flows["F10"],
        source_key="rejected_work_store",
        source_output="success_path",
        event={
            "ok": True,
            "status": "REJECTED",
            "work_definition": {"work_definition_id": "wd-terminal-test"},
            "trace_id": "final-reject",
        },
    )
    assert "반려" in message


def test_f10_approve_path_runs_f20_then_f30_directly_without_http(flows: dict[str, dict[str, Any]]) -> None:
    f10 = flows["F10"]
    f20 = flows["F20"]
    f30 = flows["F30"]
    run_f20 = _node_by_key(f10, "run_agent_blueprint_design")
    run_f30 = _node_by_key(f10, "run_responsive_report")
    invocation_loader = _node_by_key(f10, "design_invocation_loader")
    authentication_context = _node_by_key(f10, "authentication_context")
    invocation_message = _node_by_key(f10, "design_invocation_message")
    handoff_gate = _node_by_key(f10, "report_handoff_gate")
    approved_store = _node_by_key(f10, "approved_work_store")

    assert run_f20["data"]["type"] == "RunFlow"
    assert run_f30["data"]["type"] == "RunFlow"
    assert run_f20["data"]["node"]["tool_mode"] is False
    assert run_f30["data"]["node"]["tool_mode"] is False
    f20_template = run_f20["data"]["node"]["template"]
    f30_template = run_f30["data"]["node"]["template"]
    assert f20_template["flow_name_selected"]["value"] == f20["name"]
    assert f20_template["flow_id_selected"]["value"] == ""
    assert f20_template["flow_name_selected"]["selected_metadata"] == {}
    assert f30_template["flow_name_selected"]["value"] == f30["name"]
    assert f30_template["flow_id_selected"]["value"] == ""
    assert f30_template["flow_name_selected"]["selected_metadata"] == {}
    assert len(_node_by_source(f10, "36_approved_design_invocation_loader.py")) == 1
    assert len(_node_by_source(f10, "45_f10_authentication_context.py")) == 1
    assert len(_node_by_source(f10, "44_f10_report_handoff_gate.py")) == 1
    assert any(
        edge["source"] == approved_store["id"]
        and edge["target"] == invocation_loader["id"]
        and edge["data"]["sourceHandle"]["name"] == "success_path"
        and edge["data"]["targetHandle"]["fieldName"] == "approval_result"
        for edge in f10["data"]["edges"]
    )
    employee_text_input = _node_by_key(f10, "employee_id_text_input")
    assert any(
        edge["source"] == employee_text_input["id"]
        and edge["target"] == authentication_context["id"]
        and edge["data"]["sourceHandle"]["name"] == "text"
        and edge["data"]["targetHandle"]["fieldName"] == "local_demo_employee_actor_id"
        for edge in f10["data"]["edges"]
    )
    assert authentication_context["data"]["node"]["template"]["authentication_source"]["value"] == "local_demo_fixture"
    assert any(
        edge["source"] == authentication_context["id"]
        and edge["target"] == invocation_loader["id"]
        and edge["data"]["sourceHandle"]["name"] == "success_path"
        and edge["data"]["targetHandle"]["fieldName"] == "authentication_context"
        for edge in f10["data"]["edges"]
    )
    assert not any(
        edge["source"] == _node_by_key(f10, "request_envelope")["id"]
        and edge["target"] == invocation_loader["id"]
        and edge["data"]["sourceHandle"]["name"] == "employee_actor_id"
        for edge in f10["data"]["edges"]
    )
    f20_input_field = f"{f20['metadata']['design_invocation_input_node_id']}~input_value"
    f20_handoff_output = f"{f20['metadata']['report_handoff_output_node_id']}~message"
    f30_input_field = f"{f30['metadata']['report_handoff_input_node_id']}~input_value"
    f30_output = f"{f30['metadata']['report_output_node_id']}~message"
    assert f20_input_field in f20_template
    assert f30_input_field in f30_template
    f20_run_output_names = {item["name"] for item in run_f20["data"]["node"]["outputs"]}
    # F20 exposes exactly one child Chat Output: the sealed handoff for F30.
    # This prevents a design-preview message from being selected accidentally
    # as the parent Run Flow result.
    assert f20_run_output_names == {f20_handoff_output}
    assert any(item["name"] == f30_output for item in run_f30["data"]["node"]["outputs"])
    assert any(
        edge["source"] == invocation_loader["id"]
        and edge["target"] == invocation_message["id"]
        and edge["data"]["sourceHandle"]["name"] == "success_path"
        and edge["data"]["targetHandle"]["fieldName"] == "input_data"
        for edge in f10["data"]["edges"]
    )
    assert invocation_message["data"]["node"]["template"]["output_type"]["value"] == "Message"
    assert any(
        edge["source"] == invocation_message["id"]
        and edge["target"] == run_f20["id"]
        and edge["data"]["sourceHandle"]["name"] == "message_output"
        and edge["data"]["targetHandle"]["fieldName"] == f20_input_field
        for edge in f10["data"]["edges"]
    )
    assert any(
        edge["source"] == run_f20["id"]
        and edge["target"] == handoff_gate["id"]
        and edge["data"]["sourceHandle"]["name"] == f20_handoff_output
        and edge["data"]["targetHandle"]["fieldName"] == "f20_report_handoff"
        for edge in f10["data"]["edges"]
    )
    assert any(
        edge["source"] == handoff_gate["id"]
        and edge["target"] == run_f30["id"]
        and edge["data"]["sourceHandle"]["name"] == "success_message"
        and edge["data"]["targetHandle"]["fieldName"] == f30_input_field
        for edge in f10["data"]["edges"]
    )
    assert any(
        edge["source"] == run_f30["id"]
        and edge["data"]["sourceHandle"]["name"] == f30_output
        and _nodes(f10)[edge["target"]]["data"]["type"] == "ChatOutput"
        for edge in f10["data"]["edges"]
    )
    assert not any(
        edge["source"] == handoff_gate["id"]
        and edge["target"] == run_f30["id"]
        and edge["data"]["sourceHandle"]["name"] == "blocked_path"
        for edge in f10["data"]["edges"]
    )
    serialized = json.dumps(f10, ensure_ascii=False).lower()
    assert "/api/v1/run" not in serialized
    assert "http://" not in json.dumps(run_f20, ensure_ascii=False).lower()
    assert "http://" not in json.dumps(run_f30, ensure_ascii=False).lower()


def test_f10_compact_route_outputs_are_fail_closed(flows: dict[str, dict[str, Any]]) -> None:
    flow = flows["F10"]
    nodes = _nodes(flow)
    assert not _node_by_source(flow, "35_result_gate.py")

    # The compact components own their success/blocked routing.  A blocked
    # graph/preview/store outcome may feed the display-only terminal message,
    # but must never feed a later state-changing node.
    terminal = _node_by_key(flow, "terminal_result_message")
    for key in ("graph_normalizer", "preview", "review_approval_store"):
        node = _node_by_key(flow, key)
        outgoing = [edge for edge in flow["data"]["edges"] if edge["source"] == node["id"]]
        blocked = [edge for edge in outgoing if edge["data"]["sourceHandle"]["name"] == "blocked_path"]
        assert len(blocked) == 1
        assert blocked[0]["target"] == terminal["id"]
        assert blocked[0]["data"]["targetHandle"]["fieldName"] == "terminal_events"
        assert all(
            edge["data"]["sourceHandle"]["name"] != "blocked_path" or edge["target"] == terminal["id"]
            for edge in outgoing
        )

    forbidden = {"HumanInput", "LanguageModel", "WorkDefinitionStore", "WorkGraphNormalizer", "WorkPreviewHasher"}
    for round_number in (1, 2, 3):
        commit = _node_by_key(flow, f"answer_commit_r{round_number}")
        blocked_edges = [
            edge
            for edge in flow["data"]["edges"]
            if edge["source"] == commit["id"] and edge["data"]["sourceHandle"]["name"] == "blocked_path"
        ]
        assert len(blocked_edges) == 1
        assert nodes[blocked_edges[0]["target"]]["data"]["type"] not in forbidden
        assert blocked_edges[0]["target"] == terminal["id"]

    # A numbered reply must fail closed before it can reach an answer commit
    # or start a new work request.  The only public result is the terminal
    # explanation in Component 41.
    for key in (
        "playground_entry_router",
        "chat_answer_resume_loader",
        "numbered_chat_answer_parser",
        "chat_answer_commit",
        "chat_answer_next_router",
    ):
        node = _node_by_key(flow, key)
        blocked_edges = [
            edge
            for edge in flow["data"]["edges"]
            if edge["source"] == node["id"] and edge["data"]["sourceHandle"]["name"] == "blocked_path"
        ]
        assert len(blocked_edges) == 1, key
        assert blocked_edges[0]["target"] == terminal["id"]
        assert blocked_edges[0]["data"]["targetHandle"]["fieldName"] == "terminal_events"


def test_f20_search_and_blueprint_chain_is_fail_closed(flows: dict[str, dict[str, Any]]) -> None:
    flow = flows["F20"]
    assert flow["metadata"]["operational_readiness"] == "trusted_backend_only_configuration_required"
    assert flow["metadata"]["operational_readiness"] != "import_ready"
    assert flow["metadata"]["required_configuration"]
    assert _types(flow).count("ChatInput") == 1
    for filename in (
        "19_skill_context_resolver.py",
        "20_search_query_planner.py",
        "29_search_query_embedding_batcher.py",
        "21_catalog_hybrid_retriever.py",
        "22_candidate_context_builder.py",
        "23_agent_blueprint_normalizer.py",
        "24_port_contract_validator.py",
        "25_blueprint_readiness_classifier.py",
        "26_component_generation_prompt_builder.py",
        "38_f20_report_handoff_builder.py",
    ):
        assert len(_node_by_source(flow, filename)) == 1, filename
    assert _has_edge(flow, "SearchQueryPlanner", "query_plan", "SearchQueryEmbeddingBatcher", "query_plan")
    assert _types(flow).count("EmbeddingModel") == 1
    assert _has_edge(flow, "EmbeddingModel", "embeddings", "SearchQueryEmbeddingBatcher", "embedding")
    assert _has_edge(flow, "ChatInput", "message", "TypeConverter", "input_data")
    assert _has_edge(flow, "TypeConverter", "data_output", "SearchQueryPlanner", "design_invocation")
    assert _has_edge(flow, "SearchQueryPlanner", "design_scope", "SkillContextResolver", "design_scope")
    assert _has_edge(flow, "SearchQueryPlanner", "approved_skill_registry", "SkillContextResolver", "skill_registry")
    assert _has_edge(flow, "SearchQueryPlanner", "design_scope", "AgentBlueprintNormalizer", "design_scope")
    assert _has_edge(flow, "SearchQueryEmbeddingBatcher", "query_vectors", "CatalogHybridRetriever", "query_vectors")
    assert _has_edge(flow, "CatalogHybridRetriever", "retrieval_result", "CandidateContextBuilder", "retrieval_result")
    assert _has_edge(flow, "LanguageModel", "text_output", "AgentBlueprintNormalizer", "blueprint_draft")
    assert "blueprint_json" not in {wrapper["data"]["node"].get("metadata", {}).get("flow_node_key") for wrapper in flow["data"]["nodes"]}
    assert _has_edge(flow, "AgentBlueprintNormalizer", "normalized_blueprint", "PortContractValidator", "normalized_blueprint")
    assert _has_edge(flow, "PortContractValidator", "validated_blueprint", "BlueprintReadinessClassifier", "validated_blueprint")
    assert _has_edge(flow, "BlueprintReadinessClassifier", "classified_blueprint", "ComponentGenerationPromptBuilder", "classified_blueprint")
    assert "generation_message" not in {wrapper["data"]["node"].get("metadata", {}).get("flow_node_key") for wrapper in flow["data"]["nodes"]}
    assert "design_output" not in {wrapper["data"]["node"].get("metadata", {}).get("flow_node_key") for wrapper in flow["data"]["nodes"]}
    assert _types(flow).count("ChatOutput") == 1
    assert _has_edge(flow, "SearchQueryPlanner", "design_scope", "F20ReportHandoffBuilder", "design_scope")
    assert _has_edge(flow, "CandidateContextBuilder", "candidate_context", "F20ReportHandoffBuilder", "candidate_context")
    assert _has_edge(flow, "ComponentGenerationPromptBuilder", "generation_request", "F20ReportHandoffBuilder", "terminal_blueprint")
    assert _has_edge(flow, "F20ReportHandoffBuilder", "report_handoff_message", "ChatOutput", "input_value")
    contract = flow["metadata"]["design_input_contract"]
    assert contract["schema_version"] == "agent-design-invocation/v1"
    assert contract["independent_downstream_scope_tweaks"] is False
    invocation_input = _node_by_key(flow, "design_invocation_input")
    assert invocation_input["id"] == contract["single_input_node_id"]
    assert invocation_input["data"]["node"]["template"]["should_store_message"]["value"] is False
    query_plan = _node_by_source(flow, "20_search_query_planner.py")[0]
    planner_outputs = {item["name"]: item for item in query_plan["data"]["node"]["outputs"]}
    assert set(planner_outputs) == {"design_scope", "query_plan", "approved_skill_registry"}
    assert all(item["group_outputs"] is True for item in planner_outputs.values())
    scope_consumers = {
        edge["target"]
        for edge in flow["data"]["edges"]
        if edge["source"] == query_plan["id"] and edge["data"]["sourceHandle"]["name"] == "design_scope"
    }
    assert len(scope_consumers) == 4
    assert _node_by_key(flow, "report_handoff_builder")["id"] in scope_consumers
    assert flow["metadata"]["report_handoff_output_node_id"] == _node_by_key(flow, "report_handoff_output")["id"]
    assert _node_by_key(flow, "report_handoff_output")["data"]["node"]["template"]["should_store_message"]["value"] is False
    assert flow["metadata"]["report_handoff_contract"] == {
        "schema_version": "f20-report-handoff/v1",
        "work_definition_source": "query_plan.design_scope",
        "retrieval_trace_source": "candidate_context.retrieval_trace",
        "blueprint_source": "generation_prompt.generation_request",
    }
    assert flow["metadata"]["blueprint_model_output_contract"] == {
        "accepted": ["one JSON object", "one complete json code fence"],
        "rejected": ["prose", "multiple JSON blocks", "partial JSON"],
        "failure_code": "INVALID_BLUEPRINT_DRAFT",
    }


def test_f30_is_sealed_handoff_report_chain_without_hitl(flows: dict[str, dict[str, Any]]) -> None:
    flow = flows["F30"]
    assert len(_sticky_notes(flow)) == 1
    assert "HumanInput" not in _types(flow)
    assert _types(flow).count("ChatInput") == 1
    assert _types(flow).count("ChatOutput") == 1
    assert _types(flow).count("TypeConverter") == 1
    assert _types(flow).count("ParseData") == 0
    assert len(_node_by_source(flow, "33_f30_report_handoff_loader.py")) == 1
    assert len(_node_by_source(flow, "37_report_publication_message.py")) == 1
    handoff_input = _node_by_key(flow, "report_handoff_input")
    handoff_json = _node_by_key(flow, "report_handoff_json")
    handoff_loader = _node_by_key(flow, "report_handoff_loader")
    report_output = _node_by_key(flow, "report_output")
    assert handoff_input["data"]["node"]["template"]["should_store_message"]["value"] is False
    assert report_output["data"]["node"]["template"]["should_store_message"]["value"] is False
    assert handoff_json["data"]["node"]["template"]["auto_parse"]["value"] is True
    assert handoff_json["data"]["node"]["template"]["output_type"]["value"] == "JSON"
    assert _has_edge(flow, "ChatInput", "message", "TypeConverter", "input_data")
    assert _has_edge(flow, "TypeConverter", "data_output", "F30ReportHandoffLoader", "report_handoff")
    assert _has_edge(flow, "F30ReportHandoffLoader", "work_definition", "ReportViewModelBuilder", "work_definition")
    assert _has_edge(flow, "F30ReportHandoffLoader", "agent_blueprint", "ReportViewModelBuilder", "agent_blueprint")
    assert _has_edge(flow, "F30ReportHandoffLoader", "retrieval_trace", "ReportViewModelBuilder", "retrieval_trace")
    assert _has_edge(flow, "ReportViewModelBuilder", "report_view_model", "ResponsiveReportRenderer", "report_view_model")
    assert _has_edge(flow, "ResponsiveReportRenderer", "render_result", "ReportPublisher", "render_result")
    assert not _has_edge(flow, "F30ReportHandoffLoader", "report_context", "ReportPublisher", "report_context")
    assert _has_edge(flow, "ReportPublisher", "publish_result", "ReportPublicationMessage", "publish_result")
    assert _has_edge(flow, "ReportPublicationMessage", "message", "ChatOutput", "input_value")
    assert handoff_loader["data"]["node"]["template"]["safe_failure_envelope"]["value"] is True
    assert _node_by_key(flow, "view_model")["data"]["node"]["template"]["safe_failure_envelope"]["value"] is True
    assert _node_by_key(flow, "renderer")["data"]["node"]["template"]["safe_failure_envelope"]["value"] is True
    assert flow["metadata"]["report_input_contract"] == {
        "schema_version": "f20-report-handoff/v1",
        "single_input_node_id": handoff_input["id"],
        "single_input_field": "input_value",
        "should_store_message": False,
    }
    assert flow["metadata"]["report_handoff_input_node_id"] == handoff_input["id"]
    assert flow["metadata"]["report_output_node_id"] == report_output["id"]
    publishers = _node_by_source(flow, "32_report_publisher.py")
    assert len(publishers) == 1
    publisher_inputs = publishers[0]["data"]["node"]["template"]
    assert publisher_inputs["report_api_url"]["value"] == "http://127.0.0.1:5000"
    assert publisher_inputs["report_ttl_hours"]["value"] == 4
    assert publisher_inputs["dry_run"]["value"] is True
    assert publisher_inputs["dry_run"]["display_name"] == "테스트 실행 (저장하지 않음)"
    publisher_outputs = {item["name"]: item for item in publishers[0]["data"]["node"]["outputs"]}
    assert set(publisher_outputs) == {"publish_result"}
    assert flow["metadata"]["report_api_publish_contract"] == {
        "request_url": "Report API Base URL + /reports (or supplied /reports endpoint)",
        "request_body": ["html", "title", "question", "view_request", "available_datasets", "report_plan", "ttl_hours", "filename_hint"],
        "success_response": ["view_url", "download_url"],
        "test_run_default": True,
        "failure_output": "PUBLISH_FAILED or F30 BLOCKED envelope on publisher.publish_result",
    }


def test_f90_is_hybrid_search_evaluation_surface(flows: dict[str, dict[str, Any]]) -> None:
    flow = flows["F90"]
    assert "HumanInput" not in _types(flow)
    for filename in (
        "20_search_query_planner.py",
        "29_search_query_embedding_batcher.py",
        "21_catalog_hybrid_retriever.py",
        "22_candidate_context_builder.py",
    ):
        assert len(_node_by_source(flow, filename)) == 1
    assert _types(flow).count("ChatInput") == 1
    assert _types(flow).count("TypeConverter") == 1
    assert _types(flow).count("EmbeddingModel") == 1
    invocation_input = _node_by_key(flow, "evaluation_invocation_input")
    invocation_json = _node_by_key(flow, "evaluation_invocation_json")
    assert invocation_input["data"]["node"]["template"]["should_store_message"]["value"] is False
    assert invocation_json["data"]["node"]["template"]["auto_parse"]["value"] is True
    assert invocation_json["data"]["node"]["template"]["output_type"]["value"] == "JSON"
    assert _has_edge(flow, "ChatInput", "message", "TypeConverter", "input_data")
    assert _has_edge(flow, "TypeConverter", "data_output", "SearchQueryPlanner", "design_invocation")
    assert _has_edge(flow, "EmbeddingModel", "embeddings", "SearchQueryEmbeddingBatcher", "embedding")
    assert _types(flow)[-1] == "ChatOutput"
    assert _node_by_key(flow, "evaluation_output")["data"]["node"]["template"]["should_store_message"]["value"] is False
    assert flow["metadata"]["evaluation_input_contract"] == {
        "schema_version": "agent-design-invocation/v1",
        "single_input_node_id": invocation_input["id"],
        "single_input_field": "input_value",
        "should_store_message": False,
        "accepts": "Verified Design Invocation JSON from Component 36",
        "evaluation_only": True,
    }
    assert flow["metadata"]["evaluation_input_node_id"] == invocation_input["id"]
    assert flow["metadata"]["evaluation_output_node_id"] == _node_by_key(flow, "evaluation_output")["id"]


def test_no_secret_values_are_baked_into_exports(flows: dict[str, dict[str, Any]]) -> None:
    for flow in flows.values():
        for wrapper in flow["data"]["nodes"]:
            for field_name, field in wrapper["data"]["node"]["template"].items():
                if not isinstance(field, dict):
                    continue
                if field.get("password") is True or field.get("_input_type") == "SecretStrInput":
                    if field_name == "mongodb_uri":
                        assert field.get("value") == MONGODB_URI_GLOBAL_VARIABLE, wrapper["id"]
                        assert field.get("load_from_db") is True, wrapper["id"]
                    else:
                        assert field.get("value") in (None, ""), (wrapper["id"], field.get("name"))


def test_bundle_contains_byte_equivalent_individual_flows(flows: dict[str, dict[str, Any]]) -> None:
    bundle = _load(BUNDLE_FILE)
    assert bundle["bundle_schema_version"] == "business-work-design-flow-bundle/v1"
    langflow_version, lfx_version = _generator_versions()
    assert bundle["langflow_version"] == langflow_version
    assert bundle["lfx_version"] == lfx_version
    bundled = {flow["name"].split("_")[0]: flow for flow in bundle["flows"]}
    assert bundled == flows


def test_build_manifest_hashes_match_files() -> None:
    manifest = _load(FLOW_ROOT / "build_manifest.json")
    langflow_version, lfx_version = _generator_versions()
    assert manifest["langflow_version"] == langflow_version
    assert manifest["lfx_version"] == lfx_version
    for record in manifest["flows"]:
        payload = (FLOW_ROOT / record["filename"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == record["sha256"]
    bundle = BUNDLE_FILE.read_bytes()
    assert hashlib.sha256(bundle).hexdigest() == manifest["bundle"]["sha256"]


def test_generator_check_mode_reports_no_drift() -> None:
    if _runtime_versions() != _generator_versions():
        pytest.skip("byte-for-byte export drift checks require the pinned generator patch runtime")
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "build_langflow_1_11_flows.py"), "--check"],
        cwd=PROJECT_ROOT.parent,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Verified 5 Flow JSON files" in result.stdout
