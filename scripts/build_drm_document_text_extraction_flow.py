from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import sys
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

try:
    from lfx.custom.eval import eval_custom_component_code
    from lfx.custom.utils import create_component_template
except ImportError as exc:  # pragma: no cover - 잘못된 Python 환경에서만 실행됩니다.
    raise SystemExit(
        "Langflow Desktop Python으로 실행해야 합니다. 예: "
        "%LOCALAPPDATA%\\com.LangflowDesktop\\.langflow-venv\\Scripts\\python.exe "
        "scripts\\build_drm_document_text_extraction_flow.py"
    ) from exc


ROOT = Path(__file__).resolve().parents[1]
FLOW_ROOT = ROOT / "flows" / "drm_document_text_extraction_flow"
FLOW_TARGET = FLOW_ROOT / "drm_document_text_extraction_flow.json"
BUNDLE_TARGET = ROOT / "flows" / "00_AGENT_GROUND_ALL_FLOWS.json"
COMPONENT_SOURCE = (
    ROOT
    / "components"
    / "drm_document_text_extractor"
    / "drm_document_text_extractor.py"
)

LANGFLOW_VERSION = "1.8.2"
LFX_VERSION = "0.3.4"

PROJECT_FLOW_SOURCES = (
    # reusable_data_flow는 export 불일치가 해결되기 전까지 전체 Bundle에서 제외합니다.
    ROOT / "flows" / "html_report_flow" / "html_report_flow.json",
    ROOT / "flows" / "enterprise_document_rag_flow" / "enterprise_document_rag_flow.json",
    ROOT / "flows" / "skill_based_agent_flow" / "meeting_action_skill_flow.json",
    ROOT / "flows" / "skill_based_agent_flow" / "skill_based_agent_flow.json",
    ROOT / "flows" / "ppt_reference_html_flow" / "ppt_reference_html_flow.json",
    FLOW_TARGET,
    ROOT / "business_agent_design" / "flow" / "business_agent_design_complete.json",
)


def _starter_dir() -> Path:
    spec = importlib.util.find_spec("langflow")
    if spec is None or spec.origin is None:
        raise RuntimeError("Langflow Desktop Python에서 실행해 주세요.")
    path = Path(spec.origin).resolve().parent / "initial_setup" / "starter_projects"
    if not path.is_dir():
        raise FileNotFoundError(f"Langflow starter directory를 찾을 수 없습니다: {path}")
    return path


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, value: dict[str, Any], *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    path.write_bytes(text.encode("utf-8"))


def _load_starter(name: str) -> dict[str, Any]:
    path = _starter_dir() / name
    if not path.is_file():
        raise FileNotFoundError(f"Langflow starter를 찾을 수 없습니다: {path}")
    return _read_json(path)


def _find_node(flow: dict[str, Any], node_id: str) -> dict[str, Any]:
    for node in flow.get("data", {}).get("nodes", []):
        if node.get("id") == node_id:
            return node
    raise KeyError(f"starter node를 찾을 수 없습니다: {node_id}")


def _clone_node(
    prototype: dict[str, Any], node_id: str, position: tuple[float, float]
) -> dict[str, Any]:
    node = deepcopy(prototype)
    node["id"] = node_id
    node.setdefault("data", {})["id"] = node_id
    node["position"] = {"x": position[0], "y": position[1]}
    node["positionAbsolute"] = {"x": position[0], "y": position[1]}
    node["selected"] = False
    node["dragging"] = False
    node.pop("measured", None)
    return node


def _rename_node(node: dict[str, Any], display_name: str) -> None:
    node["data"]["display_name"] = display_name
    node["data"]["node"]["display_name"] = display_name


def _set_template_value(node: dict[str, Any], field_name: str, value: Any) -> None:
    field = node.get("data", {}).get("node", {}).get("template", {}).get(field_name)
    if not isinstance(field, dict):
        raise ValueError(f"입력 field가 없습니다: {node.get('id')}.{field_name}")
    field["value"] = value


def _build_component_node(wrapper: dict[str, Any]) -> tuple[dict[str, Any], str]:
    source = COMPONENT_SOURCE.read_text(encoding="utf-8")
    component_class = eval_custom_component_code(source)
    config, instance = create_component_template(
        {"code": source, "output_types": []},
        module_name="agent_ground.drm_document_text_extraction.component",
    )
    if component_class.__name__ != instance.__class__.__name__:
        raise ValueError("Component 평가 결과가 다릅니다.")
    config["lf_version"] = LANGFLOW_VERSION
    # 공용 Component는 EWS Data 입력도 지원하므로 파일 입력 자체는 optional이다.
    # 이 직접 업로드 Flow에서는 파일 입력만 사용하도록 UI 계약을 좁힌다.
    config["template"]["document_files"]["required"] = True
    config["template"]["file_record"]["show"] = False

    node = _clone_node(wrapper, "DrmDocumentTextExtractor-drmDocument", (0.0, 80.0))
    node["data"]["type"] = instance.__class__.__name__
    node["data"]["node"] = config
    node["data"]["showNode"] = True
    node["data"]["display_name"] = config.get("display_name") or instance.__class__.__name__
    node["data"]["description"] = config.get("description") or ""
    node["data"]["selected_output"] = "extracted_text"
    _rename_node(node, "01 문서 텍스트 추출 (DRM 자동)")
    return node, source


def _handle_text(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).replace(
        '"', "œ"
    )


def _add_edge(
    flow: dict[str, Any],
    source: dict[str, Any],
    output_name: str,
    target: dict[str, Any],
    input_name: str,
) -> None:
    source_output = next(
        (item for item in source["data"]["node"].get("outputs", []) if item.get("name") == output_name),
        None,
    )
    if source_output is None:
        raise ValueError(f"출력 포트가 없습니다: {source['id']}.{output_name}")
    target_input = target["data"]["node"].get("template", {}).get(input_name)
    if not isinstance(target_input, dict):
        raise ValueError(f"입력 포트가 없습니다: {target['id']}.{input_name}")

    source_handle = {
        "dataType": source["data"]["type"],
        "id": source["id"],
        "name": output_name,
        "output_types": source_output.get("types") or ["Message"],
    }
    target_handle = {
        "fieldName": input_name,
        "id": target["id"],
        "inputTypes": target_input.get("input_types") or ["Message"],
        "type": target_input.get("type") or "str",
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


def build_flow() -> tuple[dict[str, Any], str]:
    starter = _load_starter("Basic Prompting.json")
    wrapper = _find_node(starter, "ChatInput-b6UCc")
    chat_output_donor = _find_node(starter, "ChatOutput-yK0AU")

    extractor, source = _build_component_node(wrapper)
    chat_output = _clone_node(
        chat_output_donor, "ChatOutput-drmDocumentText", (520.0, 80.0)
    )
    _rename_node(chat_output, "02 추출 텍스트 출력")
    _set_template_value(chat_output, "sender_name", "DRM Document Text")
    _set_template_value(chat_output, "should_store_message", False)
    if "input_value" in chat_output["data"]["node"]["template"]:
        _set_template_value(chat_output, "input_value", "")

    flow: dict[str, Any] = {
        "data": {
            "edges": [],
            "nodes": [extractor, chat_output],
            "viewport": {"x": 110.0, "y": 170.0, "zoom": 0.72},
        },
        "description": (
            "Langflow 1.8.2 flow that extracts plain files locally, sends protected or unsupported files "
            "to an allowlisted DRM text API, and returns the combined text without an LLM."
        ),
        "endpoint_name": "drm-document-text-extraction",
        "id": str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "agent-ground/drm-document-text-extraction-flow/0.3.0",
            )
        ),
        "is_component": False,
        "last_tested_version": LANGFLOW_VERSION,
        "locked": False,
        "name": "drm_document_text_extraction_flow",
        "tags": ["document", "drm", "text-extraction", "file-upload", "on-prem"],
    }
    _add_edge(flow, extractor, "extracted_text", chat_output, "input_value")
    return flow, source


def _decode_handle(value: str) -> dict[str, Any]:
    if "┇" in value:
        raise ValueError("과거 잘못된 edge handle 구분자가 포함되어 있습니다.")
    return json.loads(value.replace("œ", '"'))


def validate_flow(flow: dict[str, Any], source: str) -> None:
    if importlib.metadata.version("langflow") != LANGFLOW_VERSION:
        raise ValueError(f"Langflow {LANGFLOW_VERSION}가 필요합니다.")
    if importlib.metadata.version("lfx") != LFX_VERSION:
        raise ValueError(f"LFX {LFX_VERSION}가 필요합니다.")

    nodes = flow.get("data", {}).get("nodes", [])
    edges = flow.get("data", {}).get("edges", [])
    if len(nodes) != 2 or len(edges) != 1:
        raise ValueError(f"예상하지 못한 graph 크기입니다: nodes={len(nodes)}, edges={len(edges)}")
    node_by_id = {node.get("id"): node for node in nodes}
    if len(node_by_id) != len(nodes) or None in node_by_id:
        raise ValueError("모든 node ID는 존재하고 서로 달라야 합니다.")

    component = node_by_id["DrmDocumentTextExtractor-drmDocument"]
    config = component["data"]["node"]
    if config["template"]["code"]["value"] != source:
        raise ValueError("Flow 내장 Component 코드가 Python 원본과 다릅니다.")
    component_class = eval_custom_component_code(source)
    rebuilt, _ = create_component_template(
        {"code": source, "output_types": []},
        module_name="agent_ground.drm_document_text_extraction.validation",
    )
    if list(config.get("field_order", [])) != [item.name for item in component_class.inputs]:
        raise ValueError("Component 입력 schema가 Python 원본과 다릅니다.")
    if [item.get("name") for item in config.get("outputs", [])] != [
        item.name for item in component_class.outputs
    ]:
        raise ValueError("Component 출력 schema가 Python 원본과 다릅니다.")
    if rebuilt.get("field_order") != config.get("field_order"):
        raise ValueError("Component runtime template이 다릅니다.")

    template = config["template"]
    for field_name in ("drm_api_url", "drm_token", "employee_no", "allowed_drm_hosts"):
        if template[field_name].get("value"):
            raise ValueError(f"배포 Flow에는 DRM 환경값을 포함할 수 없습니다: {field_name}")
    if template["allow_insecure_http"].get("value") is not False:
        raise ValueError("HTTP 허용 기본값은 false여야 합니다.")
    if template["verify_tls"].get("value") is not True:
        raise ValueError("TLS 검증 기본값은 true여야 합니다.")
    if template["processing_mode"].get("value") != "자동(로컬 우선)":
        raise ValueError("직접 업로드 Flow의 기본 처리 모드는 자동(로컬 우선)이어야 합니다.")
    if template["document_files"].get("file_path"):
        raise ValueError("배포 Flow에는 사용자 파일 경로를 포함할 수 없습니다.")
    if template["document_files"].get("required") is not True:
        raise ValueError("직접 업로드 Flow에서는 문서 파일이 필수여야 합니다.")
    if template["file_record"].get("show") is not False:
        raise ValueError("직접 업로드 Flow에서는 EWS file_record 입력을 숨겨야 합니다.")

    edge = edges[0]
    if edge.get("source") not in node_by_id or edge.get("target") not in node_by_id:
        raise ValueError("연결 대상이 없는 edge입니다.")
    for key in ("sourceHandle", "targetHandle"):
        if _decode_handle(edge[key]) != edge["data"][key]:
            raise ValueError(f"edge handle/data 불일치: {key}")
        if edge[key] != _handle_text(edge["data"][key]):
            raise ValueError(f"Langflow 1.8.2 handle 형식이 아닙니다: {key}")


def build_project_bundle() -> dict[str, Any]:
    flows: list[dict[str, Any]] = []
    for path in PROJECT_FLOW_SOURCES:
        if not path.is_file():
            raise FileNotFoundError(f"Bundle source가 없습니다: {path}")
        value = _read_json(path)
        if "flows" in value:
            raise ValueError(f"Bundle source는 개별 Flow여야 합니다: {path}")
        flows.append(value)
    if len({item.get("name") for item in flows}) != len(flows):
        raise ValueError("전체 Bundle의 Flow 이름은 중복될 수 없습니다.")
    return {"flows": flows}


def _validate_written_files() -> None:
    for path in (FLOW_TARGET, BUNDLE_TARGET):
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            raise ValueError(f"UTF-8 BOM은 허용하지 않습니다: {path}")
        json.loads(raw.decode("utf-8"))
    if not BUNDLE_TARGET.read_bytes().startswith(b'{"flows":['):
        raise ValueError('전체 Bundle은 정확히 {"flows":[ 로 시작해야 합니다.')
    names = [item.get("name") for item in _read_json(BUNDLE_TARGET).get("flows", [])]
    expected = [
        "html_flow_0624",
        "enterprise_document_rag_flow",
        "meeting_action_skill_flow",
        "skill_based_agent_flow",
        "ppt_reference_html_flow",
        "drm_document_text_extraction_flow",
        "business_agent_design_complete",
    ]
    if names != expected:
        raise ValueError(f"전체 Bundle 순서가 다릅니다: {names}")


def main() -> int:
    parser = argparse.ArgumentParser(description="DRM 문서 텍스트 추출 Flow를 생성합니다.")
    parser.add_argument(
        "--check", action="store_true", help="생성된 파일을 다시 쓰지 않고 동기화 상태만 검증합니다."
    )
    args = parser.parse_args()

    flow, source = build_flow()
    validate_flow(flow, source)
    if args.check:
        bundle = build_project_bundle()
        if not FLOW_TARGET.is_file() or _read_json(FLOW_TARGET) != flow:
            raise ValueError(f"생성 Flow가 최신 원본과 다릅니다: {FLOW_TARGET}")
        if not BUNDLE_TARGET.is_file() or _read_json(BUNDLE_TARGET) != bundle:
            raise ValueError(f"전체 Bundle이 최신 Flow 집합과 다릅니다: {BUNDLE_TARGET}")
    else:
        _write_json(FLOW_TARGET, flow)
        bundle = build_project_bundle()
        _write_json(BUNDLE_TARGET, bundle, compact=True)
    _validate_written_files()
    print(
        json.dumps(
            {
                "langflow_version": importlib.metadata.version("langflow"),
                "lfx_version": importlib.metadata.version("lfx"),
                "flow": str(FLOW_TARGET),
                "nodes": len(flow["data"]["nodes"]),
                "edges": len(flow["data"]["edges"]),
                "bundle_flows": len(bundle["flows"]),
                "status": "ok",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
