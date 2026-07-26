from __future__ import annotations

"""사용자 스타일 기반 회의록 작성 Flow를 Langflow 1.9.2 JSON으로 생성한다."""

import argparse
import importlib.metadata
import importlib.util
import json
import uuid
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from lfx.custom.eval import eval_custom_component_code
    from lfx.custom.utils import create_component_template
except ImportError as exc:  # pragma: no cover - 잘못된 Python 환경에서만 실행됩니다.
    raise SystemExit(
        "Langflow 1.9.2 전용 Python으로 실행해야 합니다: "
        "scripts\\build_meeting_minutes_writer_flow.py"
    ) from exc

try:
    from langflow_1_9_2_compat import TARGET_LANGFLOW_VERSION, assert_target_runtime
except ModuleNotFoundError:  # 테스트가 파일 경로로 모듈을 읽는 경우
    from scripts.langflow_1_9_2_compat import TARGET_LANGFLOW_VERSION, assert_target_runtime


ROOT = Path(__file__).resolve().parents[1]
FLOW_ROOT = ROOT / "flows" / "meeting_minutes_writer_flow"
FLOW_TARGET = FLOW_ROOT / "meeting_minutes_writer_flow.json"
BUNDLE_TARGET = ROOT / "flows" / "00_AGENT_GROUND_ALL_FLOWS.json"

PROJECT_FLOW_SOURCES = (
    # reusable_data_flow는 export 불일치가 해결되기 전까지 전체 Bundle에서 제외합니다.
    ROOT / "flows" / "html_report_flow" / "html_report_flow.json",
    ROOT / "flows" / "enterprise_document_rag_flow" / "enterprise_document_rag_flow.json",
    ROOT / "flows" / "skill_based_agent_flow" / "skill_based_agent_flow.json",
    ROOT / "flows" / "ppt_reference_html_flow" / "ppt_reference_html_flow.json",
    ROOT / "flows" / "drm_document_text_extraction_flow" / "drm_document_text_extraction_flow.json",
    FLOW_TARGET,
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
        "historical_transcripts",
        "components/drm_document_text_extractor/drm_document_text_extractor.py",
        "DrmDocumentTextExtractor-minutesHistoryTranscript",
        (0.0, 0.0),
    ),
    ComponentSpec(
        "historical_minutes",
        "components/drm_document_text_extractor/drm_document_text_extractor.py",
        "DrmDocumentTextExtractor-minutesHistoryMinutes",
        (0.0, 410.0),
    ),
    ComponentSpec(
        "current_transcript",
        "components/drm_document_text_extractor/drm_document_text_extractor.py",
        "DrmDocumentTextExtractor-minutesCurrentTranscript",
        (0.0, 820.0),
    ),
    ComponentSpec(
        "request",
        "flows/meeting_minutes_writer_flow/nodes/meeting_minutes_request_builder.py",
        "MeetingMinutesRequestBuilder-minutesWriter",
        (520.0, 320.0),
    ),
    ComponentSpec(
        "style",
        "flows/meeting_minutes_writer_flow/nodes/meeting_minutes_style_analyzer.py",
        "MeetingMinutesStyleAnalyzer-minutesWriter",
        (1050.0, 180.0),
    ),
    ComponentSpec(
        "draft",
        "flows/meeting_minutes_writer_flow/nodes/meeting_minutes_draft_writer.py",
        "MeetingMinutesDraftWriter-minutesWriter",
        (1540.0, 180.0),
    ),
    ComponentSpec(
        "review",
        "flows/meeting_minutes_writer_flow/nodes/meeting_minutes_reviewer.py",
        "MeetingMinutesReviewer-minutesWriter",
        (2030.0, 180.0),
    ),
)


EDGE_SPECS = (
    ("historical_transcripts", "extracted_text", "request", "historical_transcripts"),
    ("historical_minutes", "extracted_text", "request", "historical_minutes"),
    ("current_transcript", "extracted_text", "request", "current_transcript"),
    ("chat_input", "message", "request", "additional_instructions"),
    ("request", "request", "style", "request"),
    ("model", "model_output", "style", "model"),
    ("request", "request", "draft", "request"),
    ("style", "style_profile", "draft", "style_profile"),
    ("model", "model_output", "draft", "model"),
    ("request", "request", "review", "request"),
    ("style", "style_profile", "review", "style_profile"),
    ("draft", "draft", "review", "draft"),
    ("model", "model_output", "review", "model"),
    ("review", "final_minutes", "chat_output", "input_value"),
)


def _starter_path() -> Path:
    spec = importlib.util.find_spec("langflow")
    if spec is None or spec.origin is None:
        raise RuntimeError("현재 Python 환경에 Langflow가 없습니다.")
    path = Path(spec.origin).resolve().parent / "initial_setup" / "starter_projects" / "Image Sentiment Analysis.json"
    if not path.is_file():
        raise FileNotFoundError(f"Langflow starter를 찾을 수 없습니다: {path}")
    return path


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, value: dict[str, Any], *, compact: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if compact
        else json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    )
    path.write_bytes(text.encode("utf-8"))


def _find_first_node_by_type(flow: dict[str, Any], node_type: str) -> dict[str, Any]:
    for node in flow.get("data", {}).get("nodes", []):
        if node.get("data", {}).get("type") == node_type:
            return node
    raise KeyError(f"starter node type을 찾을 수 없습니다: {node_type}")


def _clone_node(prototype: dict[str, Any], node_id: str, position: tuple[float, float]) -> dict[str, Any]:
    node = deepcopy(prototype)
    node["id"] = node_id
    node.setdefault("data", {})["id"] = node_id
    node["position"] = {"x": position[0], "y": position[1]}
    node["selected"] = False
    node["dragging"] = False
    node.pop("measured", None)
    if isinstance(node.get("data", {}).get("node"), dict):
        node["data"]["node"]["lf_version"] = TARGET_LANGFLOW_VERSION
    return node


def _set_template_value(node: dict[str, Any], field_name: str, value: Any) -> None:
    field = node.get("data", {}).get("node", {}).get("template", {}).get(field_name)
    if isinstance(field, dict):
        field["value"] = value


def _configure_unselected_model(node: dict[str, Any]) -> None:
    template = node["data"]["node"].get("template", {})
    model_field = template.get("model")
    if not isinstance(model_field, dict):
        raise ValueError("Language Model starter의 model field가 없습니다.")
    model_field["value"] = ""
    model_field["selected_metadata"] = None
    _set_template_value(node, "api_key", "")
    _set_template_value(node, "stream", False)
    _set_template_value(node, "temperature", 0.1)
    node["data"]["selected_output"] = "model_output"


def _build_custom_node(
    wrapper: dict[str, Any],
    spec: ComponentSpec,
    sources: dict[str, str],
) -> dict[str, Any]:
    source_path = ROOT / spec.relative_path
    if not source_path.is_file():
        raise FileNotFoundError(f"Custom source를 찾을 수 없습니다: {source_path}")
    code = source_path.read_text(encoding="utf-8")
    component_class = eval_custom_component_code(code)
    config, instance = create_component_template(
        {"code": code, "output_types": []},
        module_name=f"agent_ground.meeting_minutes_writer.{source_path.stem}.{spec.key}",
    )
    if component_class.__name__ != instance.__class__.__name__:
        raise ValueError(f"Component 평가 결과가 다릅니다: {source_path}")
    config["lf_version"] = TARGET_LANGFLOW_VERSION
    node = _clone_node(wrapper, spec.node_id, spec.position)
    node["data"]["type"] = instance.__class__.__name__
    node["data"]["node"] = config
    node["data"]["showNode"] = True
    node["data"]["display_name"] = config.get("display_name") or instance.__class__.__name__
    node["data"]["description"] = config.get("description") or ""
    outputs = config.get("outputs") or []
    node["data"]["selected_output"] = outputs[0].get("name") if outputs else None
    sources[spec.node_id] = code
    return node


def _build_note(
    prototype: dict[str, Any],
    node_id: str,
    position: tuple[float, float],
    text: str,
    color: str,
) -> dict[str, Any]:
    note = _clone_node(prototype, node_id, position)
    note["data"]["type"] = "note"
    note["data"]["node"]["description"] = text
    note["data"]["node"]["display_name"] = ""
    note["data"]["node"].setdefault("template", {})["backgroundColor"] = color
    note["style"] = {"height": 350, "width": 360}
    note["height"] = 350
    note["width"] = 360
    note["positionAbsolute"] = {"x": position[0], "y": position[1]}
    note["resizing"] = False
    return note


def _handle_text(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).replace('"', "œ")


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
    output_types = source_output.get("types") or [source_output.get("selected") or "Data"]
    input_types = target_input.get("input_types") or (
        ["Message"] if target_input.get("type") == "str" else ["Data"]
    )
    source_handle = {
        "dataType": source["data"]["type"],
        "id": source["id"],
        "name": output_name,
        "output_types": output_types,
    }
    target_handle = {
        "fieldName": input_name,
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
    starter = _read_json(_starter_path())
    chat_input_donor = _find_first_node_by_type(starter, "ChatInput")
    chat_output_donor = _find_first_node_by_type(starter, "ChatOutput")
    note_donor = _find_first_node_by_type(starter, "note")
    model_donor = _find_first_node_by_type(starter, "LanguageModelComponent")

    chat_input = _clone_node(chat_input_donor, "ChatInput-minutesWriter", (0.0, 1240.0))
    chat_output = _clone_node(chat_output_donor, "ChatOutput-minutesWriter", (2520.0, 180.0))
    model = _clone_node(model_donor, "LanguageModelComponent-minutesWriter", (520.0, 900.0))
    _set_template_value(
        chat_input,
        "input_value",
        "의사결정과 담당자·기한이 있는 후속 조치 위주로 작성하고, 인사말과 반복 설명은 제외해줘.",
    )
    _set_template_value(chat_input, "should_store_message", False)
    _set_template_value(chat_output, "sender_name", "사용자 스타일 회의록")
    _set_template_value(chat_output, "should_store_message", False)
    _configure_unselected_model(model)

    sources: dict[str, str] = {}
    nodes_by_key: dict[str, dict[str, Any]] = {
        "chat_input": chat_input,
        "chat_output": chat_output,
        "model": model,
    }
    custom_nodes: list[dict[str, Any]] = []
    for spec in COMPONENT_SPECS:
        node = _build_custom_node(chat_input_donor, spec, sources)
        nodes_by_key[spec.key] = node
        custom_nodes.append(node)

    _set_template_value(nodes_by_key["historical_transcripts"], "processing_mode", "DRM 미사용")
    _set_template_value(nodes_by_key["historical_transcripts"], "max_files", 10)
    _set_template_value(nodes_by_key["historical_transcripts"], "max_file_size_mb", 10)
    _set_template_value(nodes_by_key["historical_transcripts"], "max_total_size_mb", 60)
    _set_template_value(nodes_by_key["historical_minutes"], "processing_mode", "자동(로컬 우선)")
    _set_template_value(nodes_by_key["historical_minutes"], "max_files", 10)
    _set_template_value(nodes_by_key["historical_minutes"], "max_file_size_mb", 20)
    _set_template_value(nodes_by_key["historical_minutes"], "max_total_size_mb", 120)
    _set_template_value(nodes_by_key["current_transcript"], "processing_mode", "DRM 미사용")
    _set_template_value(nodes_by_key["current_transcript"], "max_files", 1)
    _set_template_value(nodes_by_key["current_transcript"], "max_file_size_mb", 20)
    _set_template_value(nodes_by_key["current_transcript"], "max_total_size_mb", 20)
    _set_template_value(nodes_by_key["request"], "meeting_title", "운영 개선 주간 회의")
    _set_template_value(nodes_by_key["request"], "output_language", "ko")

    first_note = _build_note(
        note_donor,
        "note-minutesWriter-firstRun",
        (-420.0, 260.0),
        (
            "## 첫 실행\n\n"
            "1. 과거 녹취 TXT와 실제 회의록을 같은 순서·개수로 넣습니다.\n"
            "2. 현재 녹취 TXT는 한 개만 넣습니다.\n"
            "3. Word 회의록이 보호되어 있으면 과거 회의록 DRM 설정을 입력합니다.\n"
            "4. Chat Input에 포함·제외 지시를 작성합니다.\n"
            "5. Language Model에 조직 승인 모델을 선택하고 Chat Output까지 실행합니다."
        ),
        "blue",
    )
    pairing_note = _build_note(
        note_donor,
        "note-minutesWriter-pairing",
        (1030.0, 650.0),
        (
            "## 스타일 학습의 핵심\n\n"
            "과거 녹취 1번과 실제 회의록 1번이 한 쌍입니다. 비교를 통해 어떤 발언을 남기고 "
            "무엇을 생략하는지, 섹션·문장·액션아이템 형식을 일반화합니다. 과거 회의의 사실은 "
            "현재 회의록 작성 단계로 전달하지 않습니다."
        ),
        "amber",
    )
    review_note = _build_note(
        note_donor,
        "note-minutesWriter-review",
        (2010.0, 650.0),
        (
            "## 최종 검토와 사람 확인\n\n"
            "초안을 현재 녹취와 다시 비교해 환각·누락·제외 지시 위반을 수정합니다. 검토 보고서도 "
            "별도 Data 출력으로 확인할 수 있습니다. 모델 검토를 거쳐도 대외 공유·승인 전에는 "
            "담당자가 원문과 최종 회의록을 확인해야 합니다."
        ),
        "green",
    )

    flow: dict[str, Any] = {
        "data": {
            "edges": [],
            "nodes": [
                first_note,
                *custom_nodes,
                model,
                pairing_note,
                review_note,
                chat_input,
                chat_output,
            ],
            "viewport": {"x": 65.0, "y": 95.0, "zoom": 0.31},
        },
        "description": (
            "Langflow 1.9.2 workflow that pairs historical transcript TXT files with actual Word/TXT meeting minutes, "
            "learns the user's writing style without carrying historical facts forward, drafts minutes from a current "
            "transcript and explicit include/exclude instructions, then reviews the result against the source."
        ),
        "endpoint_name": "meeting-minutes-writer",
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "agent-ground/meeting-minutes-writer-flow/0.1.0")),
        "is_component": False,
        "last_tested_version": TARGET_LANGFLOW_VERSION,
        "locked": False,
        "name": "meeting_minutes_writer_flow",
        "tags": ["meeting", "minutes", "style", "drm", "document", "llm", "standalone"],
    }
    for source_key, output_name, target_key, input_name in EDGE_SPECS:
        _add_edge(flow, nodes_by_key[source_key], output_name, nodes_by_key[target_key], input_name)
    return flow, sources


def _decode_handle(value: str) -> dict[str, Any]:
    if "┇" in value:
        raise ValueError("과거 잘못된 edge handle 구분자가 포함되어 있습니다.")
    return json.loads(value.replace("œ", '"'))


def validate_flow(flow: dict[str, Any], sources: dict[str, str]) -> None:
    assert_target_runtime()
    nodes = flow.get("data", {}).get("nodes", [])
    edges = flow.get("data", {}).get("edges", [])
    if len(nodes) != 13 or len(edges) != len(EDGE_SPECS):
        raise ValueError(f"예상하지 못한 graph 크기입니다: nodes={len(nodes)}, edges={len(edges)}")
    node_by_id = {node.get("id"): node for node in nodes}
    if len(node_by_id) != len(nodes) or None in node_by_id:
        raise ValueError("모든 node ID는 존재하고 서로 달라야 합니다.")

    for spec in COMPONENT_SPECS:
        config = node_by_id[spec.node_id]["data"]["node"]
        embedded_code = config["template"]["code"]["value"]
        if embedded_code != sources[spec.node_id]:
            raise ValueError(f"Flow 내장 코드가 Python 원본과 다릅니다: {spec.relative_path}")
        component_class = eval_custom_component_code(embedded_code)
        rebuilt, _ = create_component_template(
            {"code": embedded_code, "output_types": []},
            module_name=f"agent_ground.meeting_minutes_writer.validation.{spec.key}",
        )
        expected_inputs = [item.name for item in component_class.inputs]
        expected_outputs = [item.name for item in component_class.outputs]
        actual_inputs = list(config.get("field_order", []))
        actual_outputs = [item.get("name") for item in config.get("outputs", [])]
        if expected_inputs != actual_inputs or expected_outputs != actual_outputs:
            raise ValueError(
                f"직렬화 schema 불일치 {spec.relative_path}: "
                f"inputs={actual_inputs}/{expected_inputs}, outputs={actual_outputs}/{expected_outputs}"
            )
        if rebuilt.get("field_order") != config.get("field_order"):
            raise ValueError(f"runtime input template이 달라졌습니다: {spec.relative_path}")

    edge_ids: set[str] = set()
    for edge in edges:
        if edge.get("id") in edge_ids:
            raise ValueError(f"중복 edge ID: {edge.get('id')}")
        edge_ids.add(edge["id"])
        if edge.get("source") not in node_by_id or edge.get("target") not in node_by_id:
            raise ValueError(f"연결 대상이 없는 edge: {edge.get('id')}")
        for key in ("sourceHandle", "targetHandle"):
            if _decode_handle(edge[key]) != edge["data"][key]:
                raise ValueError(f"edge handle/data 불일치: {edge.get('id')} {key}")
            if edge[key] != _handle_text(edge["data"][key]):
                raise ValueError(f"Langflow 1.9.2 handle 형식이 아닙니다: {edge.get('id')} {key}")

    model_node = node_by_id["LanguageModelComponent-minutesWriter"]
    if model_node["data"].get("selected_output") != "model_output":
        raise ValueError("Language Model은 LanguageModel output으로 설정해야 합니다.")
    review_outputs = [
        item.get("name")
        for item in node_by_id["MeetingMinutesReviewer-minutesWriter"]["data"]["node"].get("outputs", [])
    ]
    if review_outputs != ["final_minutes", "quality_report"]:
        raise ValueError(f"최종 검토 출력 계약이 다릅니다: {review_outputs}")


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
        "skill_based_agent_flow",
        "ppt_reference_html_flow",
        "drm_document_text_extraction_flow",
        "meeting_minutes_writer_flow",
        "business_agent_design_complete",
    ]
    if names != expected:
        raise ValueError(f"전체 Bundle 순서가 다릅니다: {names}")


def main() -> int:
    parser = argparse.ArgumentParser(description="사용자 스타일 기반 회의록 작성 Flow를 생성합니다.")
    parser.add_argument("--check", action="store_true", help="파일을 다시 쓰지 않고 동기화 상태만 확인합니다.")
    args = parser.parse_args()

    flow, sources = build_flow()
    validate_flow(flow, sources)
    if args.check:
        bundle = build_project_bundle()
        if not FLOW_TARGET.is_file() or _read_json(FLOW_TARGET) != flow:
            raise ValueError(f"생성 Flow가 최신 원본과 다릅니다: {FLOW_TARGET}")
        if not BUNDLE_TARGET.is_file() or _read_json(BUNDLE_TARGET) != bundle:
            raise ValueError(f"전체 Bundle이 최신 Flow 집합과 다릅니다: {BUNDLE_TARGET}")
    else:
        _write_json(FLOW_TARGET, flow, compact=False)
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
                "custom_python_nodes": len(COMPONENT_SPECS),
                "shared_components": 1,
                "internal_nodes": 4,
                "bundle_flows": len(bundle["flows"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
