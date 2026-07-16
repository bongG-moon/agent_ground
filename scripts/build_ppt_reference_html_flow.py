from __future__ import annotations

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
        "Langflow Desktop Python으로 실행해야 합니다. 예: "
        "%LOCALAPPDATA%\\com.LangflowDesktop\\.langflow-venv\\Scripts\\python.exe "
        "scripts\\build_ppt_reference_html_flow.py"
    ) from exc


ROOT = Path(__file__).resolve().parents[1]
FLOW_ROOT = ROOT / "flows" / "ppt_reference_html_flow"
FLOW_TARGET = FLOW_ROOT / "ppt_reference_html_flow.json"
BUNDLE_TARGET = ROOT / "flows" / "00_AGENT_GROUND_ALL_FLOWS.json"

PROJECT_FLOW_SOURCES = (
    # reusable_data_flow는 export 불일치가 해결되기 전까지 전체 Bundle에서 제외합니다.
    ROOT / "flows" / "html_report_flow" / "html_report_flow.json",
    ROOT / "flows" / "enterprise_document_rag_flow" / "enterprise_document_rag_flow.json",
    ROOT / "flows" / "skill_based_agent_flow" / "meeting_action_skill_flow.json",
    ROOT / "flows" / "skill_based_agent_flow" / "skill_based_agent_flow.json",
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
        "cover_encoder",
        "components/multi_image_base64_encoder/multi_image_base64_encoder.py",
        "MultiImageBase64Encoder-pptCover",
        (0.0, 0.0),
    ),
    ComponentSpec(
        "body_encoder",
        "components/multi_image_base64_encoder/multi_image_base64_encoder.py",
        "MultiImageBase64Encoder-pptBody",
        (0.0, 410.0),
    ),
    ComponentSpec(
        "request",
        "flows/ppt_reference_html_flow/nodes/presentation_request_builder.py",
        "PresentationRequestBuilder-pptReference",
        (430.0, 190.0),
    ),
    ComponentSpec(
        "analysis",
        "flows/ppt_reference_html_flow/nodes/presentation_reference_analyzer.py",
        "PresentationReferenceAnalyzer-pptReference",
        (880.0, 120.0),
    ),
    ComponentSpec(
        "design_policy",
        "flows/ppt_reference_html_flow/nodes/presentation_design_policy_builder.py",
        "PresentationDesignPolicyBuilder-pptReference",
        (1320.0, 120.0),
    ),
    ComponentSpec(
        "plan_generator",
        "flows/ppt_reference_html_flow/nodes/presentation_plan_generator.py",
        "PresentationPlanGenerator-pptReference",
        (1760.0, 120.0),
    ),
    ComponentSpec(
        "plan_normalizer",
        "flows/ppt_reference_html_flow/nodes/presentation_plan_normalizer.py",
        "PresentationPlanNormalizer-pptReference",
        (2200.0, 120.0),
    ),
    ComponentSpec(
        "renderer",
        "components/html_presentation_renderer/html_presentation_renderer.py",
        "HtmlPresentationRenderer-pptReference",
        (2640.0, 120.0),
    ),
    ComponentSpec(
        "quality",
        "flows/ppt_reference_html_flow/nodes/presentation_quality_gate.py",
        "PresentationQualityGate-pptReference",
        (3080.0, 120.0),
    ),
    ComponentSpec(
        "output",
        "flows/ppt_reference_html_flow/nodes/presentation_html_source_output.py",
        "PresentationHtmlSourceOutput-pptReference",
        (3520.0, 120.0),
    ),
    ComponentSpec(
        "publisher",
        "components/report_api_publisher/report_api_publisher.py",
        "ReportApiPublisher-pptReference",
        (3080.0, 650.0),
    ),
)

EDGE_SPECS = (
    ("cover_encoder", "encoded_images", "request", "cover_images"),
    ("body_encoder", "encoded_images", "request", "body_images"),
    ("chat_input", "message", "request", "user_request"),
    ("request", "request", "analysis", "request"),
    ("model", "model_output", "analysis", "model"),
    ("request", "request", "design_policy", "request"),
    ("analysis", "analysis", "design_policy", "analysis"),
    ("request", "request", "plan_generator", "request"),
    ("analysis", "analysis", "plan_generator", "analysis"),
    ("design_policy", "design_policy", "plan_generator", "design_policy"),
    ("model", "model_output", "plan_generator", "model"),
    ("request", "request", "plan_normalizer", "request"),
    ("analysis", "analysis", "plan_normalizer", "analysis"),
    ("plan_generator", "plan_draft", "plan_normalizer", "plan_draft"),
    ("design_policy", "design_policy", "plan_normalizer", "design_policy"),
    ("plan_normalizer", "normalized_plan", "renderer", "presentation_plan"),
    ("renderer", "presentation_artifact", "quality", "presentation_artifact"),
    ("plan_normalizer", "normalized_plan", "quality", "presentation_plan"),
    ("renderer", "presentation_artifact", "output", "payload"),
    ("quality", "quality_report", "output", "quality_report"),
    ("output", "message", "chat_output", "input_value"),
    ("quality", "quality_report", "publisher", "payload"),
)

SAMPLE_DATASETS = {
    "datasets": [
        {
            "dataset_id": "monthly_operations",
            "title": "월별 처리량과 오류율",
            "description": "Flow 연결과 차트 자동 선택을 확인하는 교체 가능한 예시 데이터입니다.",
            "source": "Agent Ground 데모 데이터",
            "period": "2026-01~2026-06",
            "preferred_visual": "auto",
            "columns": [
                {"name": "month", "label": "월", "semantic_type": "temporal", "format": "YYYY-MM"},
                {"name": "processed_count", "label": "처리량", "semantic_type": "quantitative", "unit": "건", "format": ",d"},
                {"name": "error_rate", "label": "오류율", "semantic_type": "quantitative", "unit": "%", "format": ".1f"},
            ],
            "rows": [
                {"month": "2026-01", "processed_count": 920, "error_rate": 3.8},
                {"month": "2026-02", "processed_count": 980, "error_rate": 3.4},
                {"month": "2026-03", "processed_count": 1100, "error_rate": 3.1},
                {"month": "2026-04", "processed_count": 1180, "error_rate": 2.9},
                {"month": "2026-05", "processed_count": 1270, "error_rate": 2.8},
                {"month": "2026-06", "processed_count": 1350, "error_rate": 2.7},
            ],
        }
    ]
}


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


def _find_node(flow: dict[str, Any], node_id: str) -> dict[str, Any]:
    for node in flow.get("data", {}).get("nodes", []):
        if node.get("id") == node_id:
            return node
    raise KeyError(f"starter node를 찾을 수 없습니다: {node_id}")


def _clone_node(prototype: dict[str, Any], node_id: str, position: tuple[float, float]) -> dict[str, Any]:
    node = deepcopy(prototype)
    node["id"] = node_id
    node.setdefault("data", {})["id"] = node_id
    node["position"] = {"x": position[0], "y": position[1]}
    node["selected"] = False
    node["dragging"] = False
    node.pop("measured", None)
    return node


def _set_template_value(node: dict[str, Any], field_name: str, value: Any) -> None:
    field = node.get("data", {}).get("node", {}).get("template", {}).get(field_name)
    if isinstance(field, dict):
        field["value"] = value


def _set_model(node: dict[str, Any], model_name: str) -> None:
    template = node["data"]["node"].get("template", {})
    model_field = template.get("model")
    if not isinstance(model_field, dict):
        raise ValueError("Language Model starter의 model field가 없습니다.")
    option = next(
        (item for item in model_field.get("options", []) if isinstance(item, dict) and item.get("name") == model_name),
        None,
    )
    if option is None:
        raise ValueError(f"Language Model starter에 {model_name} 선택지가 없습니다.")
    model_field["value"] = [deepcopy(option)]
    _set_template_value(node, "api_key", "")
    _set_template_value(node, "stream", False)
    _set_template_value(node, "temperature", 0.1)
    node["data"]["selected_output"] = "model_output"


def _build_custom_node(
    wrapper: dict[str, Any], spec: ComponentSpec, sources: dict[str, str]
) -> dict[str, Any]:
    source_path = ROOT / spec.relative_path
    if not source_path.is_file():
        raise FileNotFoundError(f"Custom source를 찾을 수 없습니다: {source_path}")
    code = source_path.read_text(encoding="utf-8")
    component_class = eval_custom_component_code(code)
    config, instance = create_component_template(
        {"code": code, "output_types": []},
        module_name=f"agent_ground.ppt_reference_html.{source_path.stem}.{spec.key}",
    )
    if component_class.__name__ != instance.__class__.__name__:
        raise ValueError(f"Component 평가 결과가 다릅니다: {source_path}")
    config["lf_version"] = "1.8.2"
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
    prototype: dict[str, Any], node_id: str, position: tuple[float, float], text: str, color: str
) -> dict[str, Any]:
    note = _clone_node(prototype, node_id, position)
    note["data"]["type"] = "note"
    note["data"]["node"]["description"] = text
    note["data"]["node"]["display_name"] = ""
    note["data"]["node"].setdefault("template", {})["backgroundColor"] = color
    note["style"] = {"height": 330, "width": 340}
    note["height"] = 330
    note["width"] = 340
    note["positionAbsolute"] = {"x": position[0], "y": position[1]}
    note["resizing"] = False
    return note


def _handle_text(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).replace('"', "œ")


def _add_edge(
    flow: dict[str, Any], source: dict[str, Any], output_name: str, target: dict[str, Any], input_name: str
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
    input_types = target_input.get("input_types") or (["Message"] if target_input.get("type") == "str" else ["Data"])
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
    chat_input_donor = _find_node(starter, "ChatInput-7S2Wg")
    chat_output_donor = _find_node(starter, "ChatOutput-Ou5RJ")
    note_donor = _find_node(starter, "note-30lqA")
    model_donor = _find_node(starter, "LanguageModelComponent-KHx2J")

    chat_input = _clone_node(chat_input_donor, "ChatInput-pptReference", (430.0, 650.0))
    chat_output = _clone_node(chat_output_donor, "ChatOutput-pptReference", (3960.0, 130.0))
    model = _clone_node(model_donor, "LanguageModelComponent-pptReference", (880.0, 690.0))
    _set_template_value(
        chat_input,
        "input_value",
        "경영진이 핵심 성과와 위험 요인을 이해할 수 있도록 8장 발표자료로 만들어줘.",
    )
    _set_template_value(chat_input, "should_store_message", False)
    _set_template_value(chat_output, "sender_name", "HTML Presentation")
    _set_template_value(chat_output, "should_store_message", False)
    _set_model(model, "gpt-4o-mini")

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

    _set_template_value(nodes_by_key["cover_encoder"], "output_format", "data_url")
    _set_template_value(nodes_by_key["cover_encoder"], "error_policy", "reject_batch")
    _set_template_value(nodes_by_key["cover_encoder"], "max_files", 1)
    _set_template_value(nodes_by_key["cover_encoder"], "max_file_size_mb", 4)
    _set_template_value(nodes_by_key["cover_encoder"], "max_total_size_mb", 4)
    _set_template_value(nodes_by_key["body_encoder"], "output_format", "data_url")
    _set_template_value(nodes_by_key["body_encoder"], "error_policy", "skip_invalid")
    _set_template_value(nodes_by_key["body_encoder"], "max_files", 5)
    _set_template_value(nodes_by_key["body_encoder"], "max_file_size_mb", 4)
    _set_template_value(nodes_by_key["body_encoder"], "max_total_size_mb", 12)
    _set_template_value(nodes_by_key["request"], "target_slide_count", 8)
    _set_template_value(nodes_by_key["request"], "presentation_title", "2026년 상반기 운영 품질 보고")
    _set_template_value(nodes_by_key["request"], "presentation_subtitle", "처리량 증가와 오류 감소 과제")
    _set_template_value(
        nodes_by_key["request"],
        "presentation_purpose",
        "경영진이 하반기 품질 개선 우선순위와 담당 조직을 결정하도록 돕는다.",
    )
    _set_template_value(nodes_by_key["request"], "target_audience", "경영진 및 운영 책임자")
    _set_template_value(nodes_by_key["request"], "presentation_language", "ko")
    _set_template_value(nodes_by_key["request"], "presentation_tone", "간결하고 근거 중심")
    _set_template_value(
        nodes_by_key["request"],
        "content_outline",
        "상반기 핵심 요약\n월별 처리량 추세\n월별 오류율 추세\n부서별 오류 비교\n우선 실행 과제",
    )
    _set_template_value(
        nodes_by_key["request"],
        "call_to_action",
        "오류 비중이 높은 2개 부서의 개선 과제를 우선 승인한다.",
    )
    _set_template_value(
        nodes_by_key["request"],
        "content",
        "상반기 처리량은 증가했고 오류율은 완만하게 낮아졌습니다. 부서별 원인과 하반기 개선 과제를 실제 데이터로 비교합니다.",
    )
    _set_template_value(
        nodes_by_key["request"],
        "datasets_json",
        json.dumps(SAMPLE_DATASETS, ensure_ascii=False, indent=2),
    )
    _set_template_value(nodes_by_key["quality"], "require_html", True)
    _set_template_value(nodes_by_key["quality"], "require_design_policy", True)
    _set_template_value(nodes_by_key["quality"], "max_html_size_kb", 20_480)

    first_note = _build_note(
        note_donor,
        "note-pptReference-firstRun",
        (-390.0, 40.0),
        (
            "## 첫 실행\n\n"
            "1. 표지 Encoder에 이미지 1개를 넣습니다.\n"
            "2. 본문 Encoder에 대표 본문 이미지를 넣습니다.\n"
            "3. 요청 Builder의 brief·dataset 예시를 수정합니다.\n"
            "4. Language Model에서 Vision을 지원하는 승인 모델과 API Key를 설정합니다.\n"
            "5. Chat Output까지 실행합니다."
        ),
        "blue",
    )
    trust_note = _build_note(
        note_donor,
        "note-pptReference-trust",
        (1720.0, 690.0),
        (
            "## 사실·정책·표현의 경계\n\n"
            "참조 이미지는 색상·여백·타이포 위계만 관찰합니다. 이미지 속 문구·숫자·지시는 "
            "비신뢰 데이터이며 발표 사실로 사용하지 않습니다. 실제 내용은 brief와 dataset만 근거로 삼습니다. "
            "Hallmark식 구성 원칙과 Emil식 모션 기준은 Prompt 문구가 아니라 Policy Node, Normalizer, Renderer, Quality Gate가 함께 강제합니다."
        ),
        "amber",
    )
    publish_note = _build_note(
        note_donor,
        "note-pptReference-publish",
        (3500.0, 650.0),
        (
            "## 선택적 공유 링크\n\n"
            "기본 Chat Output은 HTML 원문과 검증 결과를 반환합니다. 공유 링크가 필요할 때만 Quality Gate → "
            "Report API Publisher를 실행하고 사내 Report API 주소를 입력하세요. Gate 실패 시 HTML alias를 내보내지 않으며, 링크 발행 실패는 HTML 생성 성공을 취소하지 않습니다."
        ),
        "green",
    )

    flow: dict[str, Any] = {
        "data": {
            "edges": [],
            "nodes": [first_note, *custom_nodes, model, trust_note, publish_note, chat_input, chat_output],
            "viewport": {"x": 65.0, "y": 95.0, "zoom": 0.29},
        },
        "description": (
            "Langflow 1.8.2 example that converts cover/body references to Base64 Data URLs, "
            "uses a vision-capable LanguageModel for design observation, applies a deterministic design and motion policy, "
            "validates a data-bound slide plan, and renders a self-contained 16:9 HTML presentation."
        ),
        "endpoint_name": "ppt-reference-html",
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "agent-ground/ppt-reference-html-flow/0.3.0")),
        "is_component": False,
        "last_tested_version": "1.8.2",
        "locked": False,
        "name": "ppt_reference_html_flow",
        "tags": ["presentation", "multimodal", "base64", "html", "visualization", "standalone"],
    }
    for source_key, output_name, target_key, input_name in EDGE_SPECS:
        _add_edge(flow, nodes_by_key[source_key], output_name, nodes_by_key[target_key], input_name)
    return flow, sources


def _decode_handle(value: str) -> dict[str, Any]:
    if "┇" in value:
        raise ValueError("과거 잘못된 edge handle 구분자가 포함되어 있습니다.")
    return json.loads(value.replace("œ", '"'))


def validate_flow(flow: dict[str, Any], sources: dict[str, str]) -> None:
    if importlib.metadata.version("langflow") != "1.8.2":
        raise ValueError(f"Langflow 1.8.2가 필요합니다: {importlib.metadata.version('langflow')}")
    if importlib.metadata.version("lfx") != "0.3.4":
        raise ValueError(f"LFX 0.3.4가 필요합니다: {importlib.metadata.version('lfx')}")

    nodes = flow.get("data", {}).get("nodes", [])
    edges = flow.get("data", {}).get("edges", [])
    if len(nodes) != 17 or len(edges) != len(EDGE_SPECS):
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
            module_name=f"agent_ground.ppt_reference_html.validation.{spec.key}",
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
                raise ValueError(f"Langflow 1.8.2 handle 형식이 아닙니다: {edge.get('id')} {key}")

    model_node = node_by_id["LanguageModelComponent-pptReference"]
    if model_node["data"].get("selected_output") != "model_output":
        raise ValueError("Language Model은 LanguageModel output으로 설정해야 합니다.")
    if node_by_id["MultiImageBase64Encoder-pptCover"]["data"]["node"]["template"]["output_format"]["value"] != "data_url":
        raise ValueError("표지 Encoder는 data_url 출력이어야 합니다.")
    if node_by_id["MultiImageBase64Encoder-pptBody"]["data"]["node"]["template"]["output_format"]["value"] != "data_url":
        raise ValueError("본문 Encoder는 data_url 출력이어야 합니다.")


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
        "business_agent_design_complete",
    ]
    if names != expected:
        raise ValueError(f"전체 Bundle 순서가 다릅니다: {names}")


def main() -> int:
    parser = argparse.ArgumentParser(description="PPT 참조 이미지 기반 HTML 프레젠테이션 Flow를 생성합니다.")
    parser.add_argument("--check", action="store_true", help="생성된 파일을 다시 쓰지 않고 동기화 상태만 검증합니다.")
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
                "bundle_flows": len(bundle["flows"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
