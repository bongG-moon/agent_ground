from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import sys
import uuid
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from lfx.custom.eval import eval_custom_component_code
    from lfx.custom.utils import create_component_template
except ImportError as exc:  # pragma: no cover - exercised only with a wrong interpreter
    raise SystemExit(
        "This generator must run with Langflow Desktop's Python runtime. "
        "Example: %LOCALAPPDATA%\\com.LangflowDesktop\\.langflow-venv\\Scripts\\python.exe "
        "scripts\\build_enterprise_document_rag_flow.py"
    ) from exc


ROOT = Path(__file__).resolve().parents[1]
FLOW_ROOT = ROOT / "flows" / "enterprise_document_rag_flow"
FLOW_TARGET = FLOW_ROOT / "enterprise_document_rag_flow.json"
BUNDLE_TARGET = ROOT / "flows" / "00_AGENT_GROUND_ALL_FLOWS.json"

FLOW_SOURCES = (
    ROOT / "flows" / "reusable_data_flow" / "reusable_data_flow.json",
    ROOT / "flows" / "html_report_flow" / "html_report_flow.json",
    FLOW_TARGET,
    ROOT / "flows" / "skill_based_agent_flow" / "meeting_action_skill_flow.json",
    ROOT / "flows" / "skill_based_agent_flow" / "skill_based_agent_flow.json",
    ROOT / "business_agent_design" / "flow" / "business_agent_design_complete.json",
)


@dataclass(frozen=True)
class ComponentSpec:
    key: str
    relative_path: str
    node_id: str
    position: tuple[float, float]


COMPONENT_SPECS = (
    ComponentSpec(
        "document",
        "components/document_input_normalizer/document_input_normalizer.py",
        "DocumentInputNormalizer-enterpriseRag",
        (0.0, 0.0),
    ),
    ComponentSpec(
        "pii",
        "components/pii_confidential_data_guard/pii_confidential_data_guard.py",
        "PIIConfidentialDataGuard-enterpriseRag",
        (360.0, 0.0),
    ),
    ComponentSpec(
        "chunk",
        "components/document_chunk_index_builder/document_chunk_index_builder.py",
        "DocumentChunkIndexBuilder-enterpriseRag",
        (720.0, 0.0),
    ),
    ComponentSpec(
        "request",
        "components/rag_request_context_normalizer/rag_request_context_normalizer.py",
        "RAGRequestContextNormalizer-enterpriseRag",
        (360.0, 430.0),
    ),
    ComponentSpec(
        "retriever",
        "components/acl_evidence_retriever/acl_evidence_retriever.py",
        "ACLEvidenceRetriever-enterpriseRag",
        (1080.0, 220.0),
    ),
    ComponentSpec(
        "gate",
        "components/retrieval_quality_gate/retrieval_quality_gate.py",
        "RetrievalQualityGate-enterpriseRag",
        (1440.0, 220.0),
    ),
    ComponentSpec(
        "prompt",
        "components/rag_prompt_builder/rag_prompt_builder.py",
        "RAGPromptBuilder-enterpriseRag",
        (1800.0, 0.0),
    ),
    ComponentSpec(
        "answer",
        "components/grounded_answer_builder/grounded_answer_builder.py",
        "GroundedAnswerBuilder-enterpriseRag",
        (1800.0, 440.0),
    ),
    ComponentSpec(
        "citation",
        "components/citation_response_builder/citation_response_builder.py",
        "CitationResponseBuilder-enterpriseRag",
        (2160.0, 440.0),
    ),
)


EDGE_SPECS = (
    ("document", "documents", "pii", "documents"),
    ("pii", "safe_documents", "chunk", "documents"),
    ("chat_input", "message", "request", "question"),
    ("request", "request", "retriever", "request"),
    ("chunk", "document_index", "retriever", "document_index"),
    ("retriever", "retrieval", "gate", "retrieval"),
    ("gate", "gate", "prompt", "gate"),
    ("gate", "gate", "answer", "gate"),
    ("answer", "answer", "citation", "answer"),
    ("citation", "message", "chat_output", "input_value"),
)


def _installed_starter_path() -> Path:
    langflow_spec = importlib.util.find_spec("langflow")
    if langflow_spec is None or langflow_spec.origin is None:
        raise RuntimeError("The active interpreter does not contain Langflow.")
    path = Path(langflow_spec.origin).resolve().parent / "initial_setup" / "starter_projects" / "Vector Store RAG.json"
    if not path.is_file():
        raise FileNotFoundError(f"Installed Vector Store RAG starter not found: {path}")
    return path


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, value: dict[str, Any], *, compact: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    path.write_bytes(text.encode("utf-8"))


def _clone_node(prototype: dict[str, Any], node_id: str, position: tuple[float, float]) -> dict[str, Any]:
    node = deepcopy(prototype)
    node["id"] = node_id
    node.setdefault("data", {})["id"] = node_id
    node["position"] = {"x": position[0], "y": position[1]}
    node["selected"] = False
    node["dragging"] = False
    node.pop("measured", None)
    return node


def _build_custom_node(
    wrapper_prototype: dict[str, Any],
    spec: ComponentSpec,
    sources: dict[str, str],
) -> dict[str, Any]:
    source_path = ROOT / spec.relative_path
    if not source_path.is_file():
        raise FileNotFoundError(f"Custom component source not found: {source_path}")
    code = source_path.read_text(encoding="utf-8")
    component_class = eval_custom_component_code(code)
    config, instance = create_component_template(
        {"code": code, "output_types": []},
        module_name=f"agent_ground.enterprise_document_rag.{source_path.stem}",
    )
    if instance.__class__.__name__ != component_class.__name__:
        raise ValueError(
            f"Component evaluation mismatch for {source_path}: "
            f"{component_class.__name__} != {instance.__class__.__name__}"
        )
    config["lf_version"] = "1.8.2"
    node = _clone_node(wrapper_prototype, spec.node_id, spec.position)
    node["data"]["type"] = instance.__class__.__name__
    node["data"]["node"] = config
    node["data"]["showNode"] = True
    node["data"]["display_name"] = config.get("display_name") or instance.__class__.__name__
    node["data"]["description"] = config.get("description") or ""
    outputs = config.get("outputs") or []
    node["data"]["selected_output"] = outputs[0].get("name") if outputs else None
    sources[spec.node_id] = code
    return node


def _find_node(flow: dict[str, Any], node_id: str) -> dict[str, Any]:
    for node in flow.get("data", {}).get("nodes", []):
        if node.get("id") == node_id:
            return node
    raise KeyError(f"Donor node not found: {node_id}")


def _set_template_value(node: dict[str, Any], field_name: str, value: Any) -> None:
    field = node["data"]["node"].get("template", {}).get(field_name)
    if isinstance(field, dict):
        field["value"] = value


def _build_note(
    prototype: dict[str, Any],
    node_id: str,
    position: tuple[float, float],
    description: str,
    background_color: str,
) -> dict[str, Any]:
    note = _clone_node(prototype, node_id, position)
    note["data"]["type"] = "note"
    note["data"]["node"]["description"] = description
    note["data"]["node"]["display_name"] = ""
    note["data"]["node"].setdefault("template", {})["backgroundColor"] = background_color
    note["style"] = {"height": 324, "width": 324}
    note["height"] = 324
    note["width"] = 324
    note["positionAbsolute"] = {"x": position[0], "y": position[1]}
    note["resizing"] = False
    return note


def _handle_text(value: dict[str, Any]) -> str:
    """Mirror Langflow 1.8.2's stable JSON handle encoding."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).replace('"', "œ")


def _add_edge(
    flow: dict[str, Any],
    source: dict[str, Any],
    source_name: str,
    target: dict[str, Any],
    target_name: str,
) -> None:
    source_outputs = source["data"]["node"].get("outputs", [])
    source_output = next((item for item in source_outputs if item.get("name") == source_name), None)
    if source_output is None:
        raise ValueError(f"Missing output {source['id']}.{source_name}")
    target_input = target["data"]["node"].get("template", {}).get(target_name)
    if not isinstance(target_input, dict):
        raise ValueError(f"Missing input {target['id']}.{target_name}")

    output_types = source_output.get("types") or [source_output.get("selected") or "Data"]
    input_types = target_input.get("input_types") or (["Message"] if target_input.get("type") == "str" else ["Data"])
    source_handle = {
        "dataType": source["data"]["type"],
        "id": source["id"],
        "name": source_name,
        "output_types": output_types,
    }
    target_handle = {
        "fieldName": target_name,
        "id": target["id"],
        "inputTypes": input_types,
        "type": target_input.get("type") or "other",
    }
    source_text = _handle_text(source_handle)
    target_text = _handle_text(target_handle)
    flow["data"]["edges"].append(
        {
            "animated": False,
            "className": "",
            "data": {"sourceHandle": source_handle, "targetHandle": target_handle},
            "id": f"xy-edge__{source['id']}{source_text}-{target['id']}{target_text}",
            "selected": False,
            "source": source["id"],
            "sourceHandle": source_text,
            "target": target["id"],
            "targetHandle": target_text,
        }
    )


def build_flow() -> tuple[dict[str, Any], dict[str, str]]:
    starter = _read_json(_installed_starter_path())
    chat_input_donor = _find_node(starter, "ChatInput-z5C0I")
    chat_output_donor = _find_node(starter, "ChatOutput-vauHc")
    note_donor = _find_node(starter, "note-CtXyf")
    wrapper_donor = chat_input_donor

    chat_input = _clone_node(chat_input_donor, "ChatInput-enterpriseRag", (0.0, 430.0))
    chat_output = _clone_node(chat_output_donor, "ChatOutput-enterpriseRag", (2520.0, 440.0))
    _set_template_value(chat_input, "input_value", "RAG를 왜 문서 적재 flow와 사용자 질문 flow로 나눠야 해?")
    _set_template_value(chat_input, "should_store_message", False)
    _set_template_value(chat_output, "sender_name", "Enterprise RAG")
    _set_template_value(chat_output, "should_store_message", False)

    sources: dict[str, str] = {}
    nodes_by_key: dict[str, dict[str, Any]] = {
        "chat_input": chat_input,
        "chat_output": chat_output,
    }
    custom_nodes: list[dict[str, Any]] = []
    for spec in COMPONENT_SPECS:
        node = _build_custom_node(wrapper_donor, spec, sources)
        nodes_by_key[spec.key] = node
        custom_nodes.append(node)

    first_run_note = _build_note(
        note_donor,
        "note-enterpriseRag-firstRun",
        (-390.0, 20.0),
        (
            "## 첫 실행\n\n"
            "1. 별도 문서 입력이 없으면 내장 demo corpus를 사용합니다.\n"
            "2. Request Context의 demo identity는 검증용 employee 권한입니다.\n"
            "3. **Chat Input** 질문을 바꾼 뒤 Chat Output까지 실행하세요.\n\n"
            "운영에서는 신뢰된 인증 context와 승인된 문서 저장소를 연결해야 합니다."
        ),
        "blue",
    )
    optional_llm_note = _build_note(
        note_donor,
        "note-enterpriseRag-optionalLlm",
        (1780.0, 790.0),
        (
            "## 선택적 LLM 연결\n\n"
            "`RAG Prompt Builder.prompt → 사내 승인 모델 → Grounded Answer Builder.llm_response`로 연결할 수 있습니다.\n\n"
            "현재 기본 경로는 모델 없이 근거 기반 답변을 만듭니다. `payload_lexical` index는 실행 예시용이며 "
            "영구 저장소나 vector index가 아닙니다."
        ),
        "amber",
    )

    flow: dict[str, Any] = {
        "data": {
            "edges": [],
            "nodes": [first_run_note, chat_input, *custom_nodes, optional_llm_note, chat_output],
            "viewport": {"x": 75.0, "y": 95.0, "zoom": 0.42},
        },
        "description": (
            "Standalone Langflow 1.8.2 enterprise document RAG example with document normalization, "
            "PII/confidential-data protection, deterministic chunk indexing, ACL-first retrieval, "
            "retrieval quality gating, grounded fallback answering, and citation rendering."
        ),
        "endpoint_name": "enterprise-document-rag",
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "agent-ground/enterprise-document-rag-flow/0.1.0")),
        "is_component": False,
        "last_tested_version": "1.8.2",
        "locked": False,
        "name": "enterprise_document_rag_flow",
        "tags": ["enterprise", "rag", "document", "acl", "pii", "citation", "standalone"],
    }
    for source_key, source_name, target_key, target_name in EDGE_SPECS:
        _add_edge(flow, nodes_by_key[source_key], source_name, nodes_by_key[target_key], target_name)
    return flow, sources


def _decode_handle(value: str) -> dict[str, Any]:
    if "┇" in value:
        raise ValueError("Legacy invalid handle delimiter found")
    return json.loads(value.replace("œ", '"'))


def validate_flow(flow: dict[str, Any], sources: dict[str, str]) -> None:
    if importlib.metadata.version("langflow") != "1.8.2":
        raise ValueError(f"Expected Langflow 1.8.2, got {importlib.metadata.version('langflow')}")
    if importlib.metadata.version("lfx") != "0.3.4":
        raise ValueError(f"Expected LFX 0.3.4, got {importlib.metadata.version('lfx')}")

    nodes = flow.get("data", {}).get("nodes", [])
    edges = flow.get("data", {}).get("edges", [])
    if len(nodes) != 13 or len(edges) != len(EDGE_SPECS):
        raise ValueError(f"Unexpected graph size: nodes={len(nodes)}, edges={len(edges)}")
    node_by_id = {node.get("id"): node for node in nodes}
    if len(node_by_id) != len(nodes) or None in node_by_id:
        raise ValueError("Node IDs must be present and unique")

    for spec in COMPONENT_SPECS:
        node = node_by_id[spec.node_id]
        config = node["data"]["node"]
        embedded_code = config["template"]["code"]["value"]
        if embedded_code != sources[spec.node_id]:
            raise ValueError(f"Embedded code differs from source: {spec.relative_path}")
        component_class = eval_custom_component_code(embedded_code)
        rebuilt, _ = create_component_template(
            {"code": embedded_code, "output_types": []},
            module_name=f"agent_ground.enterprise_document_rag.validation.{Path(spec.relative_path).stem}",
        )
        expected_inputs = [item.name for item in component_class.inputs]
        expected_outputs = [item.name for item in component_class.outputs]
        actual_inputs = list(config.get("field_order", []))
        actual_outputs = [item.get("name") for item in config.get("outputs", [])]
        if expected_inputs != actual_inputs or expected_outputs != actual_outputs:
            raise ValueError(
                f"Serialized schema mismatch for {spec.relative_path}: "
                f"inputs {actual_inputs} != {expected_inputs}; outputs {actual_outputs} != {expected_outputs}"
            )
        if rebuilt.get("field_order") != config.get("field_order"):
            raise ValueError(f"Runtime input template changed during validation: {spec.relative_path}")
        if [item.get("name") for item in rebuilt.get("outputs", [])] != actual_outputs:
            raise ValueError(f"Runtime output template changed during validation: {spec.relative_path}")

    edge_ids: set[str] = set()
    for edge in edges:
        if edge.get("id") in edge_ids:
            raise ValueError(f"Duplicate edge ID: {edge.get('id')}")
        edge_ids.add(edge["id"])
        if edge.get("source") not in node_by_id or edge.get("target") not in node_by_id:
            raise ValueError(f"Dangling edge: {edge.get('id')}")
        for key in ("sourceHandle", "targetHandle"):
            decoded = _decode_handle(edge[key])
            if decoded != edge["data"][key]:
                raise ValueError(f"Handle/data mismatch in {edge.get('id')}: {key}")
            if edge[key] != _handle_text(edge["data"][key]):
                raise ValueError(f"Handle is not in stable Langflow 1.8.2 form: {edge.get('id')} {key}")

    actual_edges = {
        (
            edge["source"],
            edge["data"]["sourceHandle"]["name"],
            edge["target"],
            edge["data"]["targetHandle"]["fieldName"],
        )
        for edge in edges
    }
    expected_edges = {
        (nodes_by_key_id(flow, source_key), source_name, nodes_by_key_id(flow, target_key), target_name)
        for source_key, source_name, target_key, target_name in EDGE_SPECS
    }
    if actual_edges != expected_edges:
        raise ValueError(f"Flow wiring mismatch: {actual_edges ^ expected_edges}")


def nodes_by_key_id(flow: dict[str, Any], key: str) -> str:
    if key == "chat_input":
        return "ChatInput-enterpriseRag"
    if key == "chat_output":
        return "ChatOutput-enterpriseRag"
    return next(spec.node_id for spec in COMPONENT_SPECS if spec.key == key)


def build_bundle() -> dict[str, Any]:
    flows: list[dict[str, Any]] = []
    for path in FLOW_SOURCES:
        if not path.is_file():
            raise FileNotFoundError(f"Bundle source not found: {path}")
        flow = _read_json(path)
        if "flows" in flow:
            raise ValueError(f"Bundle source must be an individual flow: {path}")
        flows.append(flow)
    if len({flow.get("name") for flow in flows}) != len(flows):
        raise ValueError("Bundle flow names must be unique")
    return {"flows": flows}


def _validate_written_files() -> None:
    flow_bytes = FLOW_TARGET.read_bytes()
    bundle_bytes = BUNDLE_TARGET.read_bytes()
    for path, raw in ((FLOW_TARGET, flow_bytes), (BUNDLE_TARGET, bundle_bytes)):
        if raw.startswith(b"\xef\xbb\xbf"):
            raise ValueError(f"UTF-8 BOM is not allowed: {path}")
        json.loads(raw.decode("utf-8"))
    if not bundle_bytes.startswith(b'{"flows":['):
        raise ValueError('Bundle must begin exactly with {"flows":[')
    bundle = json.loads(bundle_bytes.decode("utf-8"))
    expected_names = [
        "업무분석flow",
        "html_flow_0624",
        "enterprise_document_rag_flow",
        "meeting_action_skill_flow",
        "skill_based_agent_flow",
        "business_agent_design_complete",
    ]
    actual_names = [flow.get("name") for flow in bundle.get("flows", [])]
    if actual_names != expected_names:
        raise ValueError(f"Unexpected bundle order: {actual_names}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Langflow 1.8.2 enterprise document RAG flow export.")
    parser.add_argument("--check", action="store_true", help="Validate generated content without rewriting files.")
    args = parser.parse_args()

    flow, sources = build_flow()
    validate_flow(flow, sources)
    bundle_flows: dict[str, Any] | None = None
    if not args.check:
        _write_json(FLOW_TARGET, flow, compact=False)
        bundle_flows = build_bundle()
        _write_json(BUNDLE_TARGET, bundle_flows, compact=True)
    else:
        if not FLOW_TARGET.is_file() or _read_json(FLOW_TARGET) != flow:
            raise ValueError(f"Generated flow is stale: {FLOW_TARGET}")
        bundle_flows = build_bundle()
        if _read_json(BUNDLE_TARGET) != bundle_flows:
            raise ValueError(f"Generated bundle is stale: {BUNDLE_TARGET}")

    _validate_written_files()
    print(
        json.dumps(
            {
                "langflow_version": importlib.metadata.version("langflow"),
                "lfx_version": importlib.metadata.version("lfx"),
                "flow": str(FLOW_TARGET),
                "bundle": str(BUNDLE_TARGET),
                "nodes": len(flow["data"]["nodes"]),
                "edges": len(flow["data"]["edges"]),
                "custom_components": len(COMPONENT_SPECS),
                "bundle_flows": len(bundle_flows["flows"]),
                "status": "ok",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
