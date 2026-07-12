from __future__ import annotations

"""Build Langflow 1.8.2 import files for Business Agent Design.

The donor contains the visually arranged, fully connected 24-node canvas.  This
script replaces every embedded Standalone component and prompt from the project
sources, then verifies the exact handle encoding expected by Langflow 1.8.2.
"""

import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BUSINESS = ROOT / "business_agent_design"
DONOR = BUSINESS / "flow" / "source" / "business_agent_builder_legacy_donor.json"
INDIVIDUAL = BUSINESS / "flow" / "business_agent_design_complete.json"
BUSINESS_BUNDLE = BUSINESS / "flow" / "00_business_agent_design_ALL_FLOWS.json"
PROJECT_BUNDLE = ROOT / "flows" / "00_AGENT_GROUND_ALL_FLOWS.json"

COMPONENT_BY_NODE_ID = {
    "CustomComponent-pdsMH": "main/00_business_work_input_loader.py",
    "CustomComponent-j3urH": "main/01_business_profile_prompt_builder.py",
    "CustomComponent-EM7HA": "main/02_business_profile_normalizer.py",
    "CustomComponent-9B0mr": "main/03_mongodb_catalog_retriever.py",
    "CustomComponent-DaLTn": "main/04_agent_design_prompt_builder.py",
    "CustomComponent-QtDKj": "main/05_agent_design_normalizer.py",
    "CustomComponent-PHx2x": "main/06_secure_html_renderer.py",
    "CustomComponent-wNSrT": "main/07_user_summary_output.py",
    "CustomComponent-tW4vx": "main/08_html_source_output.py",
    "CustomComponent-VGml1": "main/09_report_api_publisher.py",
    "CustomComponent-QNeAC": "catalog_admin/02_1_catalog_source_input.py",
    "CustomComponent-zpNAD": "catalog_admin/02_2_catalog_json_prompt_variables.py",
    "CustomComponent-De9Fv": "catalog_admin/02_3_catalog_json_normalizer.py",
    "CustomComponent-FaFpj": "catalog_admin/02_4_mongodb_catalog_store.py",
    "CustomComponent-SJUig": "catalog_admin/02_5_catalog_store_summary.py",
}

PROMPT_BY_NODE_ID = {
    "Prompt Template-ZYJyc": "01_BUSINESS_PROFILE_PROMPT_TEMPLATE.md",
    "Prompt Template-H92Yr": "04_AGENT_DESIGN_PROMPT_TEMPLATE.md",
    "Prompt Template-VCTvB": "02_2_CATALOG_JSON_PROMPT_TEMPLATE.md",
}

PROJECT_FLOW_FILES = [
    ROOT / "flows" / "reusable_data_flow" / "reusable_data_flow.json",
    ROOT / "flows" / "html_report_flow" / "html_report_flow.json",
    ROOT / "flows" / "enterprise_document_rag_flow" / "enterprise_document_rag_flow.json",
    ROOT / "flows" / "skill_based_agent_flow" / "meeting_action_skill_flow.json",
    ROOT / "flows" / "skill_based_agent_flow" / "skill_based_agent_flow.json",
    INDIVIDUAL,
]


def main() -> None:
    flow = json.loads(DONOR.read_text(encoding="utf-8-sig"))
    flow["id"] = "7fb52a77-3551-4c31-b0ae-8e6fbf8af89a"
    flow["name"] = "business_agent_design_complete"
    flow["endpoint_name"] = "business-agent-design"
    flow["description"] = "업무 설명을 BEFORE/AFTER 분기형 Flow Chart와 구현 가이드 HTML로 변환합니다."
    flow["last_tested_version"] = "1.8.2"

    nodes = flow.get("data", {}).get("nodes", [])
    patched_components = set()
    patched_prompts = set()
    for graph_node in nodes:
        node_id = str(graph_node.get("id") or "")
        node = graph_node.get("data", {}).get("node", {})
        template = node.get("template", {})
        if node_id in COMPONENT_BY_NODE_ID:
            source_path = BUSINESS / "components" / COMPONENT_BY_NODE_ID[node_id]
            source = source_path.read_text(encoding="utf-8")
            template.setdefault("code", {})["value"] = source
            patched_components.add(node_id)
        if node_id in PROMPT_BY_NODE_ID:
            prompt_path = BUSINESS / "prompts" / PROMPT_BY_NODE_ID[node_id]
            template.setdefault("template", {})["value"] = _fenced_prompt(prompt_path)
            patched_prompts.add(node_id)

    if patched_components != set(COMPONENT_BY_NODE_ID):
        raise AssertionError(f"custom component node mismatch: {set(COMPONENT_BY_NODE_ID) - patched_components}")
    if patched_prompts != set(PROMPT_BY_NODE_ID):
        raise AssertionError(f"prompt node mismatch: {set(PROMPT_BY_NODE_ID) - patched_prompts}")

    _rewire_default_chat_output(flow)
    validate_flow(flow)
    _write_json(INDIVIDUAL, flow, pretty=True)
    _write_bundle(BUSINESS_BUNDLE, [flow])

    project_flows = [json.loads(path.read_text(encoding="utf-8-sig")) for path in PROJECT_FLOW_FILES]
    for project_flow in project_flows:
        validate_flow(project_flow)
    _write_bundle(PROJECT_BUNDLE, project_flows)

    print(f"built: {INDIVIDUAL.relative_to(ROOT)}")
    print(f"built: {BUSINESS_BUNDLE.relative_to(ROOT)}")
    print(f"built: {PROJECT_BUNDLE.relative_to(ROOT)}")
    print(f"business nodes={len(nodes)} edges={len(flow['data']['edges'])}")


def validate_flow(flow: dict[str, Any]) -> None:
    if not isinstance(flow.get("data"), dict):
        raise AssertionError("individual flow must have a top-level data object")
    nodes = flow["data"].get("nodes", [])
    edges = flow["data"].get("edges", [])
    node_ids = {node.get("id") for node in nodes}
    if not nodes or not edges:
        raise AssertionError("flow must contain nodes and edges")
    for edge in edges:
        if edge.get("source") not in node_ids or edge.get("target") not in node_ids:
            raise AssertionError(f"dangling Langflow edge: {edge.get('id')}")
        for key in ("sourceHandle", "targetHandle"):
            encoded = edge.get(key)
            if not isinstance(encoded, str) or not encoded.startswith("{œ"):
                raise AssertionError(f"{edge.get('id')} has invalid {key} encoding")
            if "┇" in encoded:
                raise AssertionError(f"{edge.get('id')} uses the obsolete handle delimiter")
            try:
                decoded = json.loads(encoded.replace("œ", '"'))
            except json.JSONDecodeError as exc:
                raise AssertionError(f"{edge.get('id')} {key} cannot be decoded by Langflow UI") from exc
            expected = edge.get("data", {}).get(key)
            if decoded != expected:
                raise AssertionError(f"{edge.get('id')} {key} string/data mismatch")


def _rewire_default_chat_output(flow: dict[str, Any]) -> None:
    """Make first-run output independent from the optional Report API."""
    edges = flow.get("data", {}).get("edges", [])
    chat_edges = [edge for edge in edges if edge.get("target") == "ChatOutput-QCUMW"]
    if len(chat_edges) != 1:
        raise AssertionError(f"expected one Chat Output edge, found {len(chat_edges)}")
    edge = chat_edges[0]
    source = "CustomComponent-wNSrT"
    source_handle = {
        "dataType": "UserSummaryOutput",
        "id": source,
        "name": "summary_message",
        "output_types": ["Message"],
    }
    encoded_source = _handle_text(source_handle)
    edge["source"] = source
    edge["sourceHandle"] = encoded_source
    edge.setdefault("data", {})["sourceHandle"] = source_handle
    edge["id"] = f"xy-edge__{source}{encoded_source}-{edge['target']}{edge['targetHandle']}"


def _handle_text(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).replace('"', "œ")


def _fenced_prompt(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"```text\s*(.*?)```", text, flags=re.S | re.I)
    if not match:
        raise AssertionError(f"text prompt block missing: {path}")
    return match.group(1).strip()


def _write_json(path: Path, value: Any, pretty: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if pretty:
        text = json.dumps(value, ensure_ascii=False, indent=2)
    else:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    path.write_bytes(text.encode("utf-8"))


def _write_bundle(path: Path, flows: list[dict[str, Any]]) -> None:
    # Deliberately assemble the prefix: Langflow's multi-flow importer expects
    # this exact top-level shape, no BOM and no leading whitespace.
    payload = '{"flows":' + json.dumps(flows, ensure_ascii=False, separators=(",", ":")) + "}"
    if not payload.startswith('{"flows":['):
        raise AssertionError("bundle must start exactly with {\"flows\":[")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload.encode("utf-8"))


if __name__ == "__main__":
    main()
