from __future__ import annotations

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


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FLOW_ROOT = PROJECT_ROOT / "flows"
FLOW_FILES = {
    "F00": "F00_catalog_ingestion_admin.json",
    "F10": "F10_work_definition_parent.json",
    "F11": "F11_work_definition_chat_turn.json",
    "F20": "F20_agent_blueprint_design.json",
    "F30": "F30_responsive_report.json",
    "F90": "F90_search_evaluation.json",
}
BUNDLE_FILE = FLOW_ROOT / "00_business_work_design_ALL_FLOWS.json"


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
    assert version("langflow") == "1.11.1"
    assert version("lfx") == "1.11.5"


@pytest.mark.parametrize("flow_key", tuple(FLOW_FILES))
def test_langflow_graph_deserializes_every_export(flows: dict[str, dict[str, Any]], flow_key: str) -> None:
    flow = flows[flow_key]
    graph = Graph.from_payload(
        flow["data"],
        flow_id=flow["id"],
        flow_name=flow["name"],
        user_id="flow-export-contract-test",
    )
    assert len(graph.vertices) == len(flow["data"]["nodes"])
    assert len(graph.edges) == len(flow["data"]["edges"])


def test_expected_exports_exist_and_have_unique_ids(flows: dict[str, dict[str, Any]]) -> None:
    assert set(flows) == set(FLOW_FILES)
    assert all((FLOW_ROOT / filename).is_file() for filename in FLOW_FILES.values())
    flow_ids = [flow["id"] for flow in flows.values()]
    assert len(flow_ids) == len(set(flow_ids))
    assert all(flow["last_tested_version"] == "1.11.1" for flow in flows.values())


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
        assert source_handle["output_types"] == outputs[source_handle["name"]]["types"]
        assert target_handle["inputTypes"] == target_field["input_types"]
        assert edge["sourceHandle"].startswith("{œdataTypeœ:")
        assert edge["targetHandle"].startswith("{œfieldNameœ:")


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
        assert node["lf_version"] == "1.11.1"


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
    assert _types(flows["F00"]).count("HumanInput") == 1
    # Three bounded answer rounds plus one final approval gate.
    assert _types(flows["F10"]).count("HumanInput") == 4
    for flow_key in ("F11", "F20", "F30", "F90"):
        assert "HumanInput" not in _types(flows[flow_key])
        assert flows[flow_key]["metadata"]["contains_native_hitl"] is False


def test_f00_uses_bounded_worker_and_server_side_activation_after_human_gate(flows: dict[str, dict[str, Any]]) -> None:
    flow = flows["F00"]
    expected_files = [
        "00_catalog_file_intake.py",
        "01_catalog_secret_scanner.py",
        "09_catalog_pipeline_worker_client.py",
    ]
    assert all(len(_node_by_source(flow, filename)) == 1 for filename in expected_files)
    assert not _node_by_source(flow, "33_catalog_activation_approval_client.py")
    for worker_owned_stage in (
        "02_catalog_stream_parser.py",
        "03_catalog_record_normalizer.py",
        "04_catalog_embedding_text_builder.py",
        "05_catalog_embedding_batcher.py",
        "06_mongodb_snapshot_writer.py",
        "07_catalog_snapshot_validator.py",
        "08_catalog_snapshot_activator.py",
    ):
        assert not _node_by_source(flow, worker_owned_stage)
    assert _has_edge(flow, "CatalogFileIntake", "job_ref", "CatalogSecretScanner", "job_ref")
    assert _has_edge(flow, "CatalogSecretScanner", "scanned_job_ref", "CatalogPipelineWorkerClient", "scanned_job_ref")
    assert _has_edge(flow, "HumanInput", "branch_activate_snapshot", "Prompt Template", "approval_decision")
    contract = flow["metadata"]["activation_handoff_contract"]
    assert contract["flow_performs_activation"] is False
    assert contract["required_attestation"] == "catalog-activation-attestation/v1"
    assert contract["raw_nonce_in_langflow"] is False


def test_f10_contains_extraction_clarification_merge_preview_and_approval_store(
    flows: dict[str, dict[str, Any]]
) -> None:
    flow = flows["F10"]
    for filename in (
        "10_work_request_envelope.py",
        "11_work_definition_normalizer.py",
        "12_work_completeness_evaluator.py",
        "13_clarification_batch_builder.py",
        "14_work_answer_loader.py",
        "15_work_answer_merger.py",
        "16_work_graph_normalizer.py",
        "17_work_preview_hasher.py",
        "18_work_definition_store.py",
        "27_work_clarification_router.py",
        "28_work_definition_branch_joiner.py",
        "34_work_runtime_state_store.py",
        "35_result_gate.py",
    ):
        assert _node_by_source(flow, filename), filename
    assert len(_node_by_source(flow, "12_work_completeness_evaluator.py")) == 4
    assert len(_node_by_source(flow, "13_clarification_batch_builder.py")) == 4
    assert len(_node_by_source(flow, "14_work_answer_loader.py")) == 3
    assert len(_node_by_source(flow, "15_work_answer_merger.py")) == 3
    assert len(_node_by_source(flow, "17_work_preview_hasher.py")) == 1
    assert len(_node_by_source(flow, "27_work_clarification_router.py")) == 4
    assert len(_node_by_source(flow, "28_work_definition_branch_joiner.py")) == 3
    assert len(_node_by_source(flow, "34_work_runtime_state_store.py")) == 27
    assert len(_node_by_source(flow, "35_result_gate.py")) == 21
    assert _types(flow).count("LanguageModel") == 4
    rounds = {
        node["data"]["node"]["template"]["round_number"]["value"]
        for node in _node_by_source(flow, "13_clarification_batch_builder.py")
    }
    assert rounds == {1, 2, 3, 4}
    round_four = _nodes_with_value(flow, "13_clarification_batch_builder.py", "round_number", 4)
    assert len(round_four) == 1
    round_four_id = round_four[0]["id"]
    assert not any(
        edge["target"] == round_four_id and edge["data"]["targetHandle"]["fieldName"] == "candidate_questions"
        for edge in flow["data"]["edges"]
    )

    initial_stores = [
        node
        for node in _nodes_with_value(flow, "18_work_definition_store.py", "command", "save")
        if node["data"]["node"]["template"]["derive_expected_revision"]["value"] is False
    ]
    assert len(initial_stores) == 1
    answered_stores = [
        node
        for node in _nodes_with_value(flow, "18_work_definition_store.py", "command", "save")
        if node["data"]["node"]["template"]["incoming_revision_is_next"]["value"] is True
    ]
    assert len(answered_stores) == 3
    assert len(_nodes_with_value(flow, "18_work_definition_store.py", "command", "request_approval")) == 1
    assert _has_edge(flow, "HumanInput", "branch_submit_answers", "WorkAnswerLoader", "human_action")
    assert _has_edge(flow, "WorkRuntimeStateStore", "success_path", "WorkAnswerLoader", "route_trigger")
    nodes = _nodes(flow)
    for edge in flow["data"]["edges"]:
        if (
            nodes[edge["source"]]["data"]["type"] == "WorkRuntimeStateStore"
            and edge["data"]["sourceHandle"]["name"] == "blocked_path"
        ):
            assert nodes[edge["target"]]["data"]["type"] not in {"HumanInput", "WorkAnswerLoader"}
    assert _has_edge(flow, "WorkClarificationRouter", "review_path", "WorkDefinitionBranchJoiner", "review_work_definition")
    assert _has_edge(flow, "WorkDefinitionBranchJoiner", "joined_work_definition", "ResultGate", "result")
    assert _has_edge(flow, "ResultGate", "success_path", "WorkGraphNormalizer", "work_definition")
    assert _has_edge(flow, "WorkPreviewHasher", "preview", "ResultGate", "result")
    assert _has_edge(flow, "ResultGate", "success_path", "WorkDefinitionStore", "work_definition")
    assert _has_edge(flow, "HumanInput", "branch_approve", "WorkDefinitionStore", "route_trigger")
    assert _has_edge(flow, "HumanInput", "branch_reject", "WorkDefinitionStore", "route_trigger")

    assert len(_nodes_with_value(flow, "34_work_runtime_state_store.py", "runtime_status", "MERGING")) == 6
    assert len(_nodes_with_value(flow, "34_work_runtime_state_store.py", "runtime_status", "READY_FOR_REVIEW")) == 1
    assert len(_nodes_with_value(flow, "34_work_runtime_state_store.py", "runtime_status", "WAITING_APPROVAL")) == 1
    assert len(_nodes_with_value(flow, "34_work_runtime_state_store.py", "runtime_status", "CANCELLED")) == 4
    for round_number in (1, 2, 3):
        store_gate = _node_by_key(flow, f"answered_store_r{round_number}_result_gate")
        checkpoint = _node_by_key(flow, f"answered_store_r{round_number}_reconciled_runtime_state")
        assert any(
            edge["source"] == store_gate["id"]
            and edge["target"] == checkpoint["id"]
            and edge["data"]["sourceHandle"]["name"] == "success_path"
            and edge["data"]["targetHandle"]["fieldName"] == "work_definition"
            for edge in flow["data"]["edges"]
        )
    ready_runtime = _node_by_key(flow, "review_ready_runtime_state")
    request_approval_store = _node_by_key(flow, "request_approval_store")
    assert any(
        edge["source"] == ready_runtime["id"]
        and edge["target"] == request_approval_store["id"]
        and edge["data"]["sourceHandle"]["name"] == "success_path"
        and edge["data"]["targetHandle"]["fieldName"] == "work_definition"
        for edge in flow["data"]["edges"]
    )

    request_store_gate = _node_by_key(flow, "request_approval_store_result_gate")
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
            edge["source"] == request_store_gate["id"]
            and edge["data"]["sourceHandle"]["name"] == "success_path"
            and edge["data"]["targetHandle"]["fieldName"] == "work_definition"
            for edge in incoming
        )
        assert any(
            edge["data"]["sourceHandle"]["name"] == branch
            and edge["data"]["targetHandle"]["fieldName"] == "route_trigger"
            for edge in incoming
        )


def test_f11_is_explicit_playground_turn_without_native_pause(flows: dict[str, dict[str, Any]]) -> None:
    flow = flows["F11"]
    assert _types(flow).count("ChatInput") == 1
    assert _types(flow).count("ConditionalRouter") == 0
    assert _types(flow).count("ChatOutput") == 21
    assert "HumanInput" not in _types(flow)
    assert flow["metadata"]["operational_readiness"] == "structured_command_external_state_required"
    contract = flow["metadata"]["playground_turn_contract"]
    assert contract["schema_version"] == "playground-structured-command/v1"
    assert contract["commands"] == ["start", "submit_answers", "approve", "reject", "cancel"]
    assert contract["follow_up_state_source"] == "explicit_node_tweaks"
    assert contract["silent_state_fallback"] is False
    assert contract["native_hitl"] is False
    assert _node_by_key(flow, "existing_work_input")["id"] == contract["existing_work_input_node_id"]
    assert _node_by_key(flow, "existing_batch_input")["id"] == contract["existing_batch_input_node_id"]
    assert _node_by_key(flow, "answer_clarification_batch")["id"] == contract["round_number_node_id"]
    assert contract["round_number_mode"] == "derived_from_processed_answer_batches"
    assert contract["top_level_command_only"] is True
    assert contract["duplicate_json_keys_rejected"] is True
    command_router = _node_by_key(flow, "command_router")
    assert command_router["id"] == contract["command_parser_node_id"]
    assert command_router["data"]["type"] == "PlaygroundCommandRouter"
    assert len(_node_by_source(flow, "36_playground_command_router.py")) == 1
    assert _node_by_key(flow, "answer_clarification_batch")["data"]["node"]["template"]["round_number"]["value"] == 0
    loaders = _node_by_source(flow, "14_work_answer_loader.py")
    assert len(loaders) == 1
    template = loaders[0]["data"]["node"]["template"]
    assert template["channel_mode"]["value"] == "playground"
    assert template["answer_source_mode"]["value"] == "direct_payload"
    chat_input = _node_by_key(flow, "chat_input")
    chat_edges = [edge for edge in flow["data"]["edges"] if edge["source"] == chat_input["id"]]
    assert len(chat_edges) == 1
    assert _nodes(flow)[chat_edges[0]["target"]]["data"]["type"] == "PlaygroundCommandRouter"
    assert _has_edge(flow, "PlaygroundCommandRouter", "submit_answers_path", "WorkAnswerLoader", "playground_payload")
    assert _has_edge(flow, "PlaygroundCommandRouter", "start_path", "WorkRequestEnvelope", "request_text")
    assert _has_edge(flow, "TypeConverter", "data_output", "WorkAnswerLoader", "work_definition")
    assert _has_edge(flow, "TypeConverter", "data_output", "WorkAnswerMerger", "work_definition")
    assert len(_node_by_source(flow, "27_work_clarification_router.py")) == 2
    assert len(_node_by_source(flow, "28_work_definition_branch_joiner.py")) == 1
    assert len(_node_by_source(flow, "35_result_gate.py")) == 12
    assert len(_nodes_with_value(flow, "18_work_definition_store.py", "command", "request_approval")) == 1
    for command in ("approve", "reject", "cancel"):
        action_store = _node_by_key(flow, f"action_{command}_store")
        assert action_store["id"] == contract["action_store_node_ids"][command]
        assert action_store["data"]["node"]["template"]["command"]["value"] == command
        expected_output = f"{command}_path"
        assert any(
            edge["target"] == action_store["id"]
            and edge["source"] == command_router["id"]
            and edge["data"]["sourceHandle"]["name"] == expected_output
            and edge["data"]["targetHandle"]["fieldName"] == "route_trigger"
            for edge in flow["data"]["edges"]
        )


def test_f10_and_f11_result_gates_are_fail_closed(flows: dict[str, dict[str, Any]]) -> None:
    forbidden_blocked_targets = {
        "HumanInput",
        "LanguageModel",
        "WorkAnswerLoader",
        "WorkAnswerMerger",
        "WorkDefinitionStore",
        "WorkGraphNormalizer",
        "WorkPreviewHasher",
    }
    for flow_key in ("F10", "F11"):
        flow = flows[flow_key]
        nodes = _nodes(flow)
        gates = _node_by_source(flow, "35_result_gate.py")
        assert gates
        for gate in gates:
            blocked_edges = [
                edge
                for edge in flow["data"]["edges"]
                if edge["source"] == gate["id"] and edge["data"]["sourceHandle"]["name"] == "blocked_path"
            ]
            assert len(blocked_edges) == 1
            assert nodes[blocked_edges[0]["target"]]["data"]["type"] not in forbidden_blocked_targets
        for edge in flow["data"]["edges"]:
            if nodes[edge["source"]]["data"]["type"] in {
                "WorkAnswerLoader",
                "WorkAnswerMerger",
                "WorkGraphNormalizer",
                "WorkPreviewHasher",
            }:
                assert nodes[edge["target"]]["data"]["type"] == "ResultGate"


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
    ):
        assert len(_node_by_source(flow, filename)) == 1, filename
    assert _has_edge(flow, "SearchQueryPlanner", "query_plan", "SearchQueryEmbeddingBatcher", "query_plan")
    assert _has_edge(flow, "ChatInput", "message", "SearchQueryPlanner", "design_prompt")
    assert _has_edge(flow, "SearchQueryPlanner", "design_scope", "SkillContextResolver", "design_scope")
    assert _has_edge(flow, "SearchQueryPlanner", "design_scope", "AgentBlueprintNormalizer", "design_scope")
    assert _has_edge(flow, "SearchQueryEmbeddingBatcher", "query_vectors", "CatalogHybridRetriever", "query_vectors")
    assert _has_edge(flow, "CatalogHybridRetriever", "retrieval_result", "CandidateContextBuilder", "retrieval_result")
    assert _has_edge(flow, "AgentBlueprintNormalizer", "normalized_blueprint", "PortContractValidator", "normalized_blueprint")
    assert _has_edge(flow, "PortContractValidator", "validated_blueprint", "BlueprintReadinessClassifier", "validated_blueprint")
    assert _has_edge(flow, "BlueprintReadinessClassifier", "classified_blueprint", "ComponentGenerationPromptBuilder", "classified_blueprint")
    contract = flow["metadata"]["design_input_contract"]
    assert contract["schema_version"] == "agent-design-scope/v1"
    assert contract["independent_downstream_scope_tweaks"] is False
    query_plan = _node_by_source(flow, "20_search_query_planner.py")[0]
    assert query_plan["id"] == contract["single_scope_node_id"]
    scope_consumers = {
        edge["target"]
        for edge in flow["data"]["edges"]
        if edge["source"] == query_plan["id"] and edge["data"]["sourceHandle"]["name"] == "design_scope"
    }
    assert len(scope_consumers) == 3


def test_f30_is_exact_report_chain_without_hitl(flows: dict[str, dict[str, Any]]) -> None:
    flow = flows["F30"]
    assert len(flow["data"]["nodes"]) == 3
    assert len(flow["data"]["edges"]) == 2
    assert _has_edge(flow, "ReportViewModelBuilder", "report_view_model", "ResponsiveReportRenderer", "report_view_model")
    assert _has_edge(flow, "ResponsiveReportRenderer", "render_result", "ReportPublisher", "render_result")
    publishers = _node_by_source(flow, "32_report_publisher.py")
    assert len(publishers) == 1
    publisher_inputs = publishers[0]["data"]["node"]["template"]
    assert publisher_inputs["report_api_url"]["value"] == "http://127.0.0.1:8091/api"
    assert publisher_inputs["dry_run"]["value"] is True


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
    assert _types(flow)[-1] == "ChatOutput"


def test_no_secret_values_are_baked_into_exports(flows: dict[str, dict[str, Any]]) -> None:
    for flow in flows.values():
        for wrapper in flow["data"]["nodes"]:
            for field in wrapper["data"]["node"]["template"].values():
                if not isinstance(field, dict):
                    continue
                if field.get("password") is True or field.get("_input_type") == "SecretStrInput":
                    assert field.get("value") in (None, ""), (wrapper["id"], field.get("name"))


def test_bundle_contains_byte_equivalent_individual_flows(flows: dict[str, dict[str, Any]]) -> None:
    bundle = _load(BUNDLE_FILE)
    assert bundle["bundle_schema_version"] == "business-work-design-flow-bundle/v1"
    assert bundle["langflow_version"] == "1.11.1"
    assert bundle["lfx_version"] == "1.11.5"
    bundled = {flow["name"].split("_")[0]: flow for flow in bundle["flows"]}
    assert bundled == flows


def test_build_manifest_hashes_match_files() -> None:
    manifest = _load(FLOW_ROOT / "build_manifest.json")
    assert manifest["langflow_version"] == "1.11.1"
    assert manifest["lfx_version"] == "1.11.5"
    for record in manifest["flows"]:
        payload = (FLOW_ROOT / record["filename"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == record["sha256"]
    bundle = BUNDLE_FILE.read_bytes()
    assert hashlib.sha256(bundle).hexdigest() == manifest["bundle"]["sha256"]


def test_generator_check_mode_reports_no_drift() -> None:
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
    assert "Verified 6 Flow JSON files" in result.stdout
