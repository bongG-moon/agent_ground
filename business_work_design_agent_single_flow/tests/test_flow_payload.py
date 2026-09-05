from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_single_flow  # noqa: E402


class SingleFlowPayloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.flow = build_single_flow.build_flow()

    def test_one_flow_has_expected_graph_contract(self) -> None:
        summary = build_single_flow.validate_flow_payload(self.flow, check_graph=True)
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["execution_nodes"], 18)
        self.assertEqual(summary["sticky_notes"], 4)
        self.assertEqual(summary["edges"], 30)
        self.assertEqual(summary["standalone_components"], 15)

    def test_exact_edges_and_terminal_data_leaf(self) -> None:
        by_id, by_key = build_single_flow._node_map(self.flow)
        actual = tuple(build_single_flow._edge_tuple(edge, by_id) for edge in self.flow["data"]["edges"])
        self.assertEqual(actual, build_single_flow.EXPECTED_EDGES)
        artifact = by_key["artifact_output"]["data"]["node"]
        self.assertEqual(artifact["outputs"][0]["name"], "result")
        self.assertEqual(artifact["outputs"][0]["types"], ["Data", "JSON"])
        self.assertFalse(any(edge[2] == "artifact_output" for edge in actual if edge[0] == "artifact_output"))

    def test_chat_output_is_non_persistent_and_context_free(self) -> None:
        _, by_key = build_single_flow._node_map(self.flow)
        template = by_key["chat_output"]["data"]["node"]["template"]
        self.assertFalse(template["should_store_message"]["value"])
        self.assertEqual(template["session_id"]["value"], "")
        self.assertEqual(template["context_id"]["value"], "")

    def test_chat_input_is_non_persistent_and_wired_to_business_description(self) -> None:
        by_id, by_key = build_single_flow._node_map(self.flow)
        actual = tuple(build_single_flow._edge_tuple(edge, by_id) for edge in self.flow["data"]["edges"])
        self.assertIn(("chat_input", "message", "business_input", "playground_description"), actual)

        template = by_key["chat_input"]["data"]["node"]["template"]
        self.assertFalse(template["should_store_message"]["value"])
        self.assertEqual(template["input_value"]["value"], "")
        self.assertEqual(template["session_id"]["value"], "")
        self.assertEqual(template["context_id"]["value"], "")

        business_template = by_key["business_input"]["data"]["node"]["template"]
        self.assertFalse(business_template["description"]["required"])
        playground_description = business_template["playground_description"]
        self.assertFalse(playground_description["required"])
        self.assertFalse(playground_description["advanced"])
        self.assertNotEqual(playground_description["show"], False)
        self.assertIn("Message", playground_description["input_types"])

    def test_fixed_structured_output_enforces_the_embedded_json_contract(self) -> None:
        _, by_key = build_single_flow._node_map(self.flow)
        model_template = by_key["language_model"]["data"]["node"]["template"]
        self.assertEqual(model_template["system_message"]["value"], "")
        self.assertFalse(model_template["system_message"]["show"])
        self.assertTrue(model_template["system_message"]["advanced"])
        self.assertFalse(model_template["stream"]["value"])
        self.assertEqual(model_template["max_tokens"]["value"], 8192)

        structured_wrapper = by_key["structured_output"]
        self.assertEqual(structured_wrapper["data"]["type"], "BusinessDesignStructuredOutput")
        structured = structured_wrapper["data"]["node"]
        template = structured["template"]
        self.assertNotIn("system_prompt", template)
        self.assertNotIn("schema_name", template)
        self.assertNotIn("output_schema", template)
        self.assertEqual(template["model"]["input_types"], ["LanguageModel"])
        self.assertEqual(set(template["input_value"]["input_types"]), {"Message", "Data", "JSON"})
        output = next(item for item in structured["outputs"] if item["name"] == "structured_output")
        self.assertIn("JSON", output["types"])
        source = (PROJECT_ROOT / "components" / "single_flow" / build_single_flow.CUSTOM_COMPONENTS["structured_output"]).read_text(encoding="utf-8")
        self.assertIn("FIXED_SYSTEM_PROMPT", source)
        self.assertIn("SystemMessage(content=FIXED_SYSTEM_PROMPT)", source)
        self.assertNotIn("from __future__ import annotations", source)
        self.assertIn("_BUSINESS_DESIGN_DRAFT_SCHEMA_READY = BusinessDesignDraftV1.model_rebuild", source)

        refinement_wrapper = by_key["refinement_output"]
        self.assertEqual(refinement_wrapper["data"]["type"], "BusinessDesignRefinementStructuredOutput")
        refinement = refinement_wrapper["data"]["node"]
        refinement_template = refinement["template"]
        self.assertNotIn("system_prompt", refinement_template)
        self.assertNotIn("schema_name", refinement_template)
        self.assertNotIn("output_schema", refinement_template)
        self.assertEqual(refinement_template["model"]["input_types"], ["LanguageModel"])
        self.assertEqual(set(refinement_template["input_value"]["input_types"]), {"Message", "Data", "JSON"})
        refined_output = next(item for item in refinement["outputs"] if item["name"] == "refined_design_draft")
        self.assertIn("JSON", refined_output["types"])
        refinement_source = (
            PROJECT_ROOT
            / "components"
            / "single_flow"
            / build_single_flow.CUSTOM_COMPONENTS["refinement_output"]
        ).read_text(encoding="utf-8")
        self.assertIn("FIXED_REFINEMENT_SYSTEM_PROMPT", refinement_source)
        self.assertIn("SystemMessage(content=FIXED_REFINEMENT_SYSTEM_PROMPT)", refinement_source)
        self.assertNotIn("from __future__ import annotations", refinement_source)
        self.assertIn("_BUSINESS_DESIGN_REFINEMENT_SCHEMA_READY = BusinessDesignDraftV1.model_rebuild", refinement_source)

    def test_two_pass_model_path_revalidates_second_draft_and_has_safe_fallback_input(self) -> None:
        by_id, by_key = build_single_flow._node_map(self.flow)
        actual = tuple(build_single_flow._edge_tuple(edge, by_id) for edge in self.flow["data"]["edges"])
        self.assertIn(("business_input", "request", "catalog_shortlister", "request"), actual)
        self.assertIn(("catalog_ranker", "retrieval_result", "catalog_shortlister", "retrieval_result"), actual)
        self.assertIn(("language_model", "model_output", "catalog_shortlister", "model"), actual)
        self.assertIn(("catalog_shortlister", "catalog_shortlist", "prompt_builder", "catalog_shortlist"), actual)
        self.assertIn(("catalog_shortlister", "catalog_shortlist", "result_normalizer", "catalog_shortlist"), actual)
        self.assertIn(("catalog_shortlister", "catalog_shortlist", "final_normalizer", "catalog_shortlist"), actual)
        self.assertIn(("prompt_builder", "prompt", "structured_output", "input_value"), actual)
        self.assertIn(("language_model", "model_output", "structured_output", "model"), actual)
        self.assertIn(("structured_output", "structured_output", "result_normalizer", "model_response"), actual)
        self.assertIn(("result_normalizer", "design_result", "quality_prompt", "initial_design_result"), actual)
        self.assertIn(("catalog_ranker", "retrieval_result", "quality_prompt", "retrieval_result"), actual)
        self.assertIn(("quality_prompt", "refinement_prompt", "refinement_output", "input_value"), actual)
        self.assertIn(("language_model", "model_output", "refinement_output", "model"), actual)
        self.assertIn(("refinement_output", "refined_design_draft", "final_normalizer", "model_response"), actual)
        self.assertIn(("result_normalizer", "design_result", "final_normalizer", "fallback_design_result"), actual)
        self.assertNotIn(("result_normalizer", "design_result", "final_normalizer", "fixed_catalog_shortlist"), actual)
        self.assertIn(("final_normalizer", "design_result", "view_model", "design_result"), actual)
        self.assertNotIn(("language_model", "text_output", "result_normalizer", "model_response"), actual)
        self.assertNotIn(("language_model", "text_output", "final_normalizer", "model_response"), actual)
        normalizer_input = by_key["result_normalizer"]["data"]["node"]["template"]["model_response"]
        self.assertTrue({"Data", "JSON"}.issubset(set(normalizer_input["input_types"])))
        fallback_input = by_key["final_normalizer"]["data"]["node"]["template"]["fallback_design_result"]
        self.assertFalse(fallback_input["required"])
        self.assertTrue({"Data", "JSON"}.issubset(set(fallback_input["input_types"])))
        for normalizer_key in ("result_normalizer", "final_normalizer"):
            catalog_shortlist_input = by_key[normalizer_key]["data"]["node"]["template"]["catalog_shortlist"]
            self.assertTrue(catalog_shortlist_input["required"])
            self.assertTrue({"Data", "JSON"}.issubset(set(catalog_shortlist_input["input_types"])))

    def test_candidate_pool_and_shortlist_limit_are_visible_canvas_inputs(self) -> None:
        _, by_key = build_single_flow._node_map(self.flow)
        ranker = by_key["catalog_ranker"]["data"]["node"]["template"]
        business_input = by_key["business_input"]["data"]["node"]["template"]

        self.assertEqual(ranker["top_n"]["value"], 100)
        self.assertNotIn("expanded_detail_count", ranker)
        selection_limit = by_key["catalog_shortlister"]["data"]["node"]["template"]["max_shortlisted_catalog_items"]
        self.assertEqual(selection_limit["value"], 12)
        self.assertFalse(selection_limit["advanced"])
        self.assertNotEqual(selection_limit["show"], False)
        prompt_builder = by_key["prompt_builder"]["data"]["node"]["template"]
        self.assertNotIn("max_shortlisted_catalog_items", prompt_builder)
        self.assertTrue(prompt_builder["catalog_shortlist"]["required"])
        self.assertTrue({"Data", "JSON"}.issubset(set(prompt_builder["catalog_shortlist"]["input_types"])))
        self.assertEqual(business_input["final_refinement_instructions"]["required"], False)
        self.assertNotEqual(business_input["final_refinement_instructions"]["value"], "")

    def test_embedded_components_are_standalone_and_source_equal(self) -> None:
        _, by_key = build_single_flow._node_map(self.flow)
        for node_key, filename in build_single_flow.CUSTOM_COMPONENTS.items():
            node = by_key[node_key]["data"]["node"]
            metadata = node["metadata"]
            source_path = PROJECT_ROOT / metadata["standalone_source_path"]
            source = source_path.read_text(encoding="utf-8")
            self.assertTrue(metadata["standalone"])
            self.assertEqual(source_path.name, filename)
            self.assertEqual(node["template"]["code"]["value"], source)
            self.assertEqual(metadata["standalone_source_sha256"], build_single_flow._sha256_text(source))
            self.assertEqual(build_single_flow._standalone_violation(source, source_path), "")

    def test_validator_rejects_forbidden_nested_or_stateful_node(self) -> None:
        changed = copy.deepcopy(self.flow)
        _, by_key = build_single_flow._node_map(changed)
        by_key["catalog_ranker"]["data"]["type"] = "RunFlow"
        with self.assertRaisesRegex(ValueError, "Forbidden node type"):
            build_single_flow.validate_flow_payload(changed, check_graph=False)

    def test_validator_rejects_multiple_upstreams(self) -> None:
        changed = copy.deepcopy(self.flow)
        duplicate = copy.deepcopy(changed["data"]["edges"][0])
        duplicate["id"] += "-duplicate"
        duplicate["source"] = changed["data"]["edges"][2]["source"]
        duplicate["data"]["sourceHandle"] = copy.deepcopy(changed["data"]["edges"][2]["data"]["sourceHandle"])
        duplicate["sourceHandle"] = changed["data"]["edges"][2]["sourceHandle"]
        changed["data"]["edges"].append(duplicate)
        with self.assertRaises(ValueError):
            build_single_flow.validate_flow_payload(changed, check_graph=False)

    def test_checked_in_export_is_current_after_generation(self) -> None:
        self.assertTrue(build_single_flow.FLOW_PATH.is_file())
        checked_in = json.loads(build_single_flow.FLOW_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            (json.dumps(checked_in, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            (json.dumps(self.flow, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
