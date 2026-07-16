from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BUSINESS = ROOT / "business_agent_design"


LFX_STUB_MODULE_NAMES = (
    "lfx",
    "lfx.custom",
    "lfx.custom.custom_component",
    "lfx.custom.custom_component.component",
    "lfx.io",
    "lfx.schema",
    "lfx.schema.data",
    "lfx.schema.message",
)


def _install_lfx_stubs() -> None:
    class Component:
        pass

    class Port:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.__dict__.update(kwargs)

    class Data:
        def __init__(self, data=None):
            self.data = data or {}

    class Message:
        def __init__(self, text=""):
            self.text = text

    modules = {
        "lfx": types.ModuleType("lfx"),
        "lfx.custom": types.ModuleType("lfx.custom"),
        "lfx.custom.custom_component": types.ModuleType("lfx.custom.custom_component"),
        "lfx.custom.custom_component.component": types.ModuleType("lfx.custom.custom_component.component"),
        "lfx.io": types.ModuleType("lfx.io"),
        "lfx.schema": types.ModuleType("lfx.schema"),
        "lfx.schema.data": types.ModuleType("lfx.schema.data"),
        "lfx.schema.message": types.ModuleType("lfx.schema.message"),
    }
    modules["lfx.custom.custom_component.component"].Component = Component
    for name in ("DataInput", "MessageTextInput", "Output"):
        setattr(modules["lfx.io"], name, Port)
    modules["lfx.schema.data"].Data = Data
    modules["lfx.schema.message"].Message = Message
    sys.modules.update(modules)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


_original_lfx_modules = {name: sys.modules.get(name) for name in LFX_STUB_MODULE_NAMES}
try:
    _install_lfx_stubs()
    NORMALIZER = _load("business_design_normalizer", BUSINESS / "components" / "main" / "05_agent_design_normalizer.py")
    RENDERER = _load("business_design_renderer", BUSINESS / "components" / "main" / "06_secure_html_renderer.py")
    BUILDER = _load("business_design_builder", ROOT / "scripts" / "build_business_agent_design_flow.py")
finally:
    # 이 파일의 경량 Stub이 다른 테스트의 실제 Langflow/LFX import를 가리지 않도록 복원한다.
    for _module_name, _original_module in _original_lfx_modules.items():
        if _original_module is None:
            sys.modules.pop(_module_name, None)
        else:
            sys.modules[_module_name] = _original_module


def _sample_payload(malicious: bool = False) -> dict:
    suspicious = "</script><script>alert(1)</script>" if malicious else "생산 데이터 조회"
    profile = {
        "business_goal": "생산 실적과 불량을 확인하고 이상 설비의 정비 이력을 검토합니다.",
        "current_flow": [
            {"step_id": "S1", "title": suspicious, "description": "MES 실적을 내려받습니다.", "actor": "생산 담당자"},
            {"step_id": "S2", "title": "이상 여부 판단", "description": "불량률이 기준을 넘는지 확인합니다.", "actor": "품질 담당자"},
            {"step_id": "S3", "title": "정비 이력 조회", "description": "이상 설비의 정비 이력을 조회합니다.", "actor": "품질 담당자"},
            {"step_id": "S4", "title": "팀장에게 메일 발송", "description": "결과를 팀장에게 메일로 발송합니다.", "actor": "품질 담당자"},
        ],
        "risk_signals": ["외부 발송 전 사람 검토 필요"],
        "assumptions": [],
        "open_questions": [],
    }
    items = [
        {
            "canonical_key": "prompt_template",
            "title_ko": "Prompt Template",
            "summary_ko": "업무 입력을 구조화합니다.",
            "langflow_building_blocks": ["Prompt Template"],
            "source_links": ["https://docs.langflow.org/components-prompts"],
            "risk_level": "low",
        },
        {
            "canonical_key": "human_review_gate",
            "title_ko": "사람 검토 Gate",
            "summary_ko": "영향이 큰 실행 전에 사람이 확인합니다.",
            "langflow_building_blocks": ["Human Review"],
            "source_links": ["internal:safety-pattern"],
            "risk_level": "high",
            "human_review_required": True,
        },
    ]
    return {
        "workflow_profile": profile,
        "catalog_context": {
            "business_profile": profile,
            "ranked_catalog_items": items,
            "catalog_meta": {"source": "test", "selected_count": len(items)},
            "recommendation_trace": {"trace_id": "trace_test_001"},
        },
    }


class BusinessAgentDesignComponentTests(unittest.TestCase):
    def test_fallback_builds_branching_before_and_after_graphs(self):
        result = NORMALIZER.normalize_agent_design(_sample_payload(), "")
        self.assertTrue(result["design_validation"]["passed"])
        visual = result["agent_design"]["flow_visualization"]
        for graph_name in ("before", "after"):
            graph = visual[graph_name]
            decisions = [node for node in graph["nodes"] if node["node_type"] == "decision"]
            self.assertTrue(decisions, graph_name)
            for decision in decisions:
                outgoing = [edge for edge in graph["edges"] if edge["source"] == decision["node_id"]]
                self.assertGreaterEqual(len(outgoing), 2)
                self.assertTrue(all(edge["branch_label"] and edge["condition"] for edge in outgoing))

        changed = [node for node in visual["after"]["nodes"] if node["change_state"] in {"modified", "added", "human_review"}]
        detail_ids = {item["improvement_detail_id"] for item in result["agent_design"]["improvement_details"]}
        self.assertTrue(changed)
        self.assertTrue(all(node["improvement_detail_id"] in detail_ids for node in changed))

    def test_renderer_outputs_real_chart_connectors_and_buttons(self):
        normalized = NORMALIZER.normalize_agent_design(_sample_payload(), "")
        result = RENDERER.render_business_design_html(normalized)
        document = result["html_result"]["html"]
        security = result["html_result"]["security_report"]
        self.assertTrue(security["passed"])
        self.assertEqual(document.count('<script id="agent-ground-flow-runtime">'), 1)
        self.assertIn('class="flowchart"', document)
        self.assertIn('class="connectors"', document)
        self.assertIn('class="edge-meta"', document)
        self.assertIn('type-decision', document)
        self.assertIn('class="detail-button"', document)
        self.assertIn('data-detail-panel=', document)
        self.assertIn("branch_label", (BUSINESS / "components" / "main" / "05_agent_design_normalizer.py").read_text(encoding="utf-8"))

    def test_renderer_escapes_untrusted_content(self):
        normalized = NORMALIZER.normalize_agent_design(_sample_payload(malicious=True), "")
        result = RENDERER.render_business_design_html(normalized)
        document = result["html_result"]["html"]
        self.assertTrue(result["html_result"]["security_report"]["passed"])
        self.assertEqual(document.count("<script"), 1)
        self.assertIn("&lt;/script&gt;&lt;script&gt;alert(1)&lt;/script&gt;", document)


class LangflowImportTests(unittest.TestCase):
    def test_individual_and_bundle_shapes_are_bom_free(self):
        individual_path = BUSINESS / "flow" / "business_agent_design_complete.json"
        bundle_path = BUSINESS / "flow" / "00_business_agent_design_ALL_FLOWS.json"
        project_bundle_path = ROOT / "flows" / "00_AGENT_GROUND_ALL_FLOWS.json"
        individual_raw = individual_path.read_bytes()
        bundle_raw = bundle_path.read_bytes()
        project_raw = project_bundle_path.read_bytes()
        for raw in (individual_raw, bundle_raw, project_raw):
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(individual_raw.startswith(b"{"))
        self.assertFalse(individual_raw.startswith(b'{"flows":['))
        self.assertTrue(bundle_raw.startswith(b'{"flows":['))
        self.assertTrue(project_raw.startswith(b'{"flows":['))
        project_flows = json.loads(project_raw)["flows"]
        self.assertEqual(len(project_flows), 7)
        self.assertNotIn("업무분석flow", {flow.get("name") for flow in project_flows})

    def test_handles_decode_with_langflow_1_8_2_contract(self):
        flow = json.loads((BUSINESS / "flow" / "business_agent_design_complete.json").read_text(encoding="utf-8"))
        BUILDER.validate_flow(flow)
        for edge in flow["data"]["edges"]:
            self.assertNotIn("┇", edge["sourceHandle"] + edge["targetHandle"])
            self.assertEqual(json.loads(edge["sourceHandle"].replace("œ", '"')), edge["data"]["sourceHandle"])
            self.assertEqual(json.loads(edge["targetHandle"].replace("œ", '"')), edge["data"]["targetHandle"])

    def test_embedded_standalone_sources_and_graph_prompt_are_current(self):
        flow = json.loads((BUSINESS / "flow" / "business_agent_design_complete.json").read_text(encoding="utf-8"))
        nodes = {node["id"]: node for node in flow["data"]["nodes"]}
        for node_id, relative in BUILDER.COMPONENT_BY_NODE_ID.items():
            embedded = nodes[node_id]["data"]["node"]["template"]["code"]["value"]
            source = (BUSINESS / "components" / relative).read_text(encoding="utf-8")
            self.assertEqual(embedded, source)
        prompt = nodes["Prompt Template-H92Yr"]["data"]["node"]["template"]["template"]["value"]
        self.assertIn("Flow Chart", prompt)
        self.assertIn("flow_visualization", prompt)
        self.assertIn("branch_label", prompt)
        self.assertNotIn("as_is_flow", prompt)
        chat_edges = [edge for edge in flow["data"]["edges"] if edge["target"] == "ChatOutput-QCUMW"]
        self.assertEqual(len(chat_edges), 1)
        self.assertEqual(chat_edges[0]["source"], "CustomComponent-wNSrT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
