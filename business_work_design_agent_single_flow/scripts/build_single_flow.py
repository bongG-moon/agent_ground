from __future__ import annotations

"""Build the one-file Langflow 1.11.0 business-work-design Flow.

The Flow intentionally has no nested Run Flow, Human Input, MongoDB,
embedding, or hidden execution-state dependency.  Every custom node embeds the
exact source of a standalone component so an imported Flow stays reviewable and
is not coupled to a local Python package layout.
"""

import argparse
import ast
import hashlib
import importlib
import inspect
import json
import re
import uuid
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.custom.utils import build_custom_component_template
from lfx.graph import Graph


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_ROOT = PROJECT_ROOT / "components" / "single_flow"
FLOW_PATH = PROJECT_ROOT / "flows" / "F01_business_work_design_single.json"
PROMPT_PATH = PROJECT_ROOT / "prompts" / "single_flow_business_design.md"

FLOW_ID = "d1ec59da-dfda-50eb-9105-98e437d44457"
FLOW_NAME = "F01_business_work_design_single"
FLOW_CONTRACT = "business-work-design-single/v1"
EXPECTED_RUNTIME = {
    "langflow": (1, 11),
    "langflow-base": (0, 11),
    "lfx": (1, 11),
}

CUSTOM_COMPONENTS = {
    "business_input": "00_business_design_input.py",
    "catalog_loader": "01_catalog_json_loader.py",
    "catalog_ranker": "02_local_catalog_ranker.py",
    "prompt_builder": "03_business_design_prompt_builder.py",
    "structured_output": "04_business_design_structured_output.py",
    "result_normalizer": "05_business_design_result_normalizer.py",
    "quality_prompt": "06_design_quality_refinement_prompt.py",
    "refinement_output": "07_business_design_refinement_structured_output.py",
    # Reuse the same standalone normalizer source for the final pass.  It
    # revalidates the second draft against the exact first-pass request and
    # candidate registry, and can safely retain the first result if the
    # optional refiner returns its bounded fallback envelope.
    "final_normalizer": "05_business_design_result_normalizer.py",
    "view_model": "06_report_view_model_builder_v2.py",
    "renderer": "07_responsive_report_renderer_v2.py",
    "publisher": "08_report_publisher.py",
    "result_message": "09_report_result_message.py",
    "artifact_output": "10_report_artifact_output.py",
}

EXPECTED_EDGES = (
    ("business_input", "request", "catalog_ranker", "request"),
    ("catalog_loader", "catalog_bundle", "catalog_ranker", "catalog_bundle"),
    ("business_input", "request", "prompt_builder", "request"),
    ("catalog_ranker", "retrieval_result", "prompt_builder", "retrieval_result"),
    # The Language Model node supplies only the configured model object.  The
    # Structured Output node owns the actual call so prose cannot be passed to
    # the result normalizer in place of the required JSON object.
    ("prompt_builder", "prompt", "structured_output", "input_value"),
    ("language_model", "model_output", "structured_output", "model"),
    ("structured_output", "structured_output", "result_normalizer", "model_response"),
    ("business_input", "request", "result_normalizer", "request"),
    ("catalog_ranker", "retrieval_result", "result_normalizer", "retrieval_result"),
    ("result_normalizer", "design_result", "quality_prompt", "initial_design_result"),
    ("catalog_ranker", "retrieval_result", "quality_prompt", "retrieval_result"),
    ("quality_prompt", "refinement_prompt", "refinement_output", "input_value"),
    ("language_model", "model_output", "refinement_output", "model"),
    ("refinement_output", "refined_design_draft", "final_normalizer", "model_response"),
    ("business_input", "request", "final_normalizer", "request"),
    ("catalog_ranker", "retrieval_result", "final_normalizer", "retrieval_result"),
    ("result_normalizer", "design_result", "final_normalizer", "fallback_design_result"),
    ("final_normalizer", "design_result", "view_model", "design_result"),
    ("view_model", "report_view_model", "renderer", "report_view_model"),
    ("renderer", "render_result", "publisher", "rendered_report"),
    ("publisher", "publish_result", "result_message", "publish_result"),
    ("publisher", "publish_result", "artifact_output", "publish_result"),
    ("result_message", "message", "chat_output", "input_value"),
)

BUILTIN_COMPONENTS = {"language_model", "chat_output"}

ALLOWED_IMPORT_ROOTS = {
    "__future__",
    "base64",
    "collections",
    "dataclasses",
    "datetime",
    "decimal",
    "hashlib",
    "html",
    "httpx",
    "json",
    "lfx",
    "math",
    "os",
    "pathlib",
    "pydantic",
    "re",
    "requests",
    "socket",
    "time",
    "typing",
    "unicodedata",
    "urllib",
    "uuid",
    "langchain_core",
}
FORBIDDEN_COMPONENT_TYPE_PARTS = (
    "runflow",
    "humaninput",
    "embedding",
    "mongodb",
)
FORBIDDEN_INPUT_NAME_PARTS = (
    "mongo",
    "tenant",
    "session",
    "revision",
    "idempotency",
)


def _minor(value: str) -> tuple[int, int] | None:
    parts = value.split(".")
    if len(parts) < 3 or any(not part.isdigit() for part in parts[:3]):
        return None
    return int(parts[0]), int(parts[1])


def runtime_versions() -> dict[str, str]:
    try:
        resolved = {name: version(name) for name in EXPECTED_RUNTIME}
    except PackageNotFoundError as exc:
        raise RuntimeError("Langflow 1.11.0 runtime packages are required to build this Flow") from exc
    incompatible = {
        name: actual
        for name, actual in resolved.items()
        if _minor(actual) != EXPECTED_RUNTIME[name]
    }
    if incompatible:
        raise RuntimeError(
            "Langflow 1.11.x runtime required; "
            f"expected={EXPECTED_RUNTIME}; actual={resolved}"
        )
    return resolved


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _stable_suffix(node_key: str) -> str:
    return hashlib.sha256(f"{FLOW_CONTRACT}:{node_key}".encode("utf-8")).hexdigest()[:7]


def _standalone_violation(source: str, path: Path) -> str:
    """Reject local imports and common dynamic-code escape hatches.

    This is deliberately source-local: custom components must run as one file
    after a Langflow import, not through a sibling helper module.
    """

    tree = ast.parse(source, filename=str(path))
    component_classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and any(isinstance(base, ast.Name) and base.id == "Component" for base in node.bases)
    ]
    if len(component_classes) != 1:
        return "must declare exactly one Component subclass"
    if "from lfx.custom import Component" not in source:
        return "must use the public `from lfx.custom import Component` import"
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if node.level or (root and root not in ALLOWED_IMPORT_ROOTS):
                return f"has a local or unapproved import: {node.module}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] not in ALLOWED_IMPORT_ROOTS:
                    return f"has a local or unapproved import: {alias.name}"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {
            "eval",
            "exec",
            "compile",
            "__import__",
        }:
            return f"uses prohibited dynamic code execution: {node.func.id}"
        if isinstance(node, ast.Attribute) and node.attr in {"system", "popen", "spawn", "spawnv"}:
            return f"uses prohibited process execution attribute: {node.attr}"
    return ""


def _source_for_component(filename: str) -> tuple[Path, str]:
    path = COMPONENT_ROOT / filename
    if not path.is_file():
        raise FileNotFoundError(f"Standalone component source is missing: {path}")
    source = path.read_text(encoding="utf-8")
    violation = _standalone_violation(source, path)
    if violation:
        raise ValueError(f"{path}: {violation}")
    return path, source


def _component_template(source: str) -> tuple[dict[str, Any], Any]:
    template, instance = build_custom_component_template(Component(_code=source))
    if not isinstance(template, dict) or not isinstance(template.get("template"), dict):
        raise TypeError("Langflow did not return a valid component template")
    if not isinstance(template.get("outputs"), list) or not template["outputs"]:
        raise ValueError("Component must expose at least one output")
    return template, instance


def _builtin_template(module_name: str) -> tuple[dict[str, Any], str]:
    module = importlib.import_module(module_name)
    source = inspect.getsource(module)
    template, instance = _component_template(source)
    type_name = str(getattr(instance, "name", "") or type(instance).__name__.removesuffix("Component"))
    if not type_name:
        raise ValueError(f"Could not resolve type name for built-in module {module_name}")
    return template, type_name


def _set_value(template: dict[str, Any], field_name: str, value: Any) -> None:
    field = template.get("template", {}).get(field_name)
    if not isinstance(field, dict):
        raise KeyError(f"Input field {field_name!r} is absent from {template.get('display_name')!r}")
    field["value"] = value


def _hide_build_controlled_field(template: dict[str, Any], field_name: str, value: Any) -> None:
    """Store a build-owned field without exposing it as an operator setting."""

    _set_value(template, field_name, value)
    field = template["template"][field_name]
    field["show"] = False
    field["advanced"] = True
    field["load_from_db"] = False


@dataclass(frozen=True)
class NodeRef:
    key: str
    node_id: str
    wrapper: dict[str, Any]

    @property
    def node(self) -> dict[str, Any]:
        return self.wrapper["data"]["node"]

    @property
    def type_name(self) -> str:
        return str(self.wrapper["data"]["type"])

    def relabel(self, name: str, description: str) -> "NodeRef":
        self.node["display_name"] = name
        self.node["description"] = description
        self.wrapper["data"]["display_name"] = name
        self.wrapper["data"]["description"] = description
        return self


class FlowBuilder:
    def __init__(self, runtime: dict[str, str]) -> None:
        self.runtime = runtime
        self.nodes: dict[str, NodeRef] = {}
        self.notes: list[dict[str, Any]] = []
        self.edges: list[dict[str, Any]] = []

    def note(
        self,
        key: str,
        text: str,
        position: tuple[float, float],
        *,
        width: float,
        height: float,
        color: str = "blue",
    ) -> None:
        if not text.strip() or color not in {"blue", "amber"}:
            raise ValueError("Sticky Note requires non-empty text and a supported color")
        node_id = f"note-f01-{key}"
        x, y = position
        self.notes.append(
            {
                "data": {
                    "id": node_id,
                    "node": {
                        "description": text,
                        "display_name": "",
                        "documentation": "",
                        "template": {"backgroundColor": color},
                        "lf_version": self.runtime["langflow"],
                    },
                    "type": "note",
                },
                "dragging": False,
                "height": float(height),
                "id": node_id,
                "position": {"x": float(x), "y": float(y)},
                "positionAbsolute": {"x": float(x), "y": float(y)},
                "resizing": False,
                "selected": False,
                "style": {"height": float(height), "width": float(width)},
                "type": "noteNode",
                "width": float(width),
            }
        )

    def _wrap(
        self,
        key: str,
        template: dict[str, Any],
        type_name: str,
        position: tuple[float, float],
        *,
        source_path: str | None = None,
        source_sha256: str | None = None,
    ) -> NodeRef:
        if key in self.nodes:
            raise ValueError(f"Duplicate Flow node key: {key}")
        prefix = "CustomComponent" if source_path else type_name.replace(" ", "")
        node_id = f"{prefix}-{_stable_suffix(key)}"
        template["lf_version"] = self.runtime["langflow"]
        metadata = template.setdefault("metadata", {})
        metadata.update(
            {
                "flow_build_target": self.runtime["langflow"],
                "flow_contract": FLOW_CONTRACT,
                "flow_node_key": key,
            }
        )
        if source_path is not None:
            metadata.update(
                {
                    "standalone": True,
                    "standalone_source_path": source_path,
                    "standalone_source_sha256": source_sha256,
                }
            )
        outputs = template.get("outputs") or []
        wrapper = {
            "data": {
                "id": node_id,
                "node": template,
                "showNode": True,
                "type": type_name,
                "description": template.get("description", ""),
                "display_name": template.get("display_name", type_name),
                "selected_output": outputs[0].get("name") if outputs else None,
            },
            "dragging": False,
            "id": node_id,
            "measured": {"height": 260, "width": 320},
            "position": {"x": float(position[0]), "y": float(position[1])},
            "selected": False,
            "type": "genericNode",
        }
        ref = NodeRef(key=key, node_id=node_id, wrapper=wrapper)
        self.nodes[key] = ref
        return ref

    def custom(
        self,
        key: str,
        filename: str,
        position: tuple[float, float],
        values: dict[str, Any] | None = None,
    ) -> NodeRef:
        path, source = _source_for_component(filename)
        template, instance = _component_template(source)
        for field_name, value in (values or {}).items():
            _set_value(template, field_name, value)
        type_name = str(getattr(instance, "name", "") or type(instance).__name__.removesuffix("Component"))
        return self._wrap(
            key,
            template,
            type_name,
            position,
            source_path=path.relative_to(PROJECT_ROOT).as_posix(),
            source_sha256=_sha256_text(source),
        )

    def builtin(
        self,
        key: str,
        module_name: str,
        position: tuple[float, float],
        values: dict[str, Any] | None = None,
    ) -> NodeRef:
        template, type_name = _builtin_template(module_name)
        for field_name, value in (values or {}).items():
            _set_value(template, field_name, value)
        return self._wrap(key, template, type_name, position)

    def connect(self, source_key: str, output_name: str, target_key: str, field_name: str) -> None:
        source = self.nodes[source_key]
        target = self.nodes[target_key]
        output = next((item for item in source.node.get("outputs", []) if item.get("name") == output_name), None)
        if not isinstance(output, dict):
            raise ValueError(f"Output {source_key}.{output_name} does not exist")
        field = target.node.get("template", {}).get(field_name)
        if not isinstance(field, dict):
            raise ValueError(f"Input {target_key}.{field_name} does not exist")
        output_types = list(output.get("types") or [])
        input_types = list(field.get("input_types") or [])
        if not input_types or not set(output_types).intersection(input_types):
            raise TypeError(
                f"Incompatible edge {source_key}.{output_name} {output_types} -> "
                f"{target_key}.{field_name} {input_types}"
            )
        source_handle = {
            "dataType": source.type_name,
            "id": source.node_id,
            "name": output_name,
            "output_types": output_types,
        }
        target_handle = {
            "fieldName": field_name,
            "id": target.node_id,
            "inputTypes": input_types,
            "type": field.get("type", "other"),
        }
        encode = lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace('"', "œ")
        encoded_source = encode(source_handle)
        encoded_target = encode(target_handle)
        edge = {
            "animated": False,
            "className": "",
            "data": {"sourceHandle": source_handle, "targetHandle": target_handle},
            "id": f"xy-edge__{source.node_id}{encoded_source}-{target.node_id}{encoded_target}",
            "selected": False,
            "source": source.node_id,
            "sourceHandle": encoded_source,
            "target": target.node_id,
            "targetHandle": encoded_target,
        }
        if any(existing["id"] == edge["id"] for existing in self.edges):
            raise ValueError(f"Duplicate edge: {edge['id']}")
        self.edges.append(edge)

    def build(self) -> dict[str, Any]:
        return {
            "data": {
                "edges": self.edges,
                "nodes": [*self.notes, *(node.wrapper for node in self.nodes.values())],
                "viewport": {"x": 80, "y": 80, "zoom": 0.58},
            },
            "description": "업무 설명과 로컬 기능 카탈로그 JSON을 한 번 입력해, 100개 후보의 1차 설계·품질 보완·최종 정규화를 거쳐 카탈로그 기반 개선안을 포함한 HTML 보고서를 생성하는 Langflow 1.11.0 단일 Flow.",
            "endpoint_name": "business-work-design-single",
            "icon": None,
            "icon_bg_color": None,
            "id": FLOW_ID,
            "is_component": False,
            "last_tested_version": self.runtime["langflow"],
            "locked": False,
            "mcp_enabled": False,
            "name": FLOW_NAME,
            "tags": [
                "business-work-design",
                "single-flow",
                "langflow-1.11.0",
                "standalone-components",
                "structured-output",
            ],
            "webhook": False,
            "metadata": {
                "flow_contract": FLOW_CONTRACT,
                "generated_by": "scripts/build_single_flow.py",
                "langflow_version": self.runtime["langflow"],
                "langflow_base_version": self.runtime["langflow-base"],
                "lfx_version": self.runtime["lfx"],
                "operational_readiness": "language_model_configuration_required",
                "required_configuration": [
                    "00 업무 설명 입력의 업무 설명과 선택적인 추가 설계 요청",
                    "01 기능 카탈로그 JSON 파일의 UTF-8 JSON 파일 하나",
                    "02 관련 기능 카탈로그 검색의 상위 후보 수(기본 100)",
                    "04 Language Model의 provider/model/credential (05 1차 생성과 08 최종 보완 생성에 같은 모델 객체를 사용)",
                    "05·08의 고정 Pydantic 계약·시스템 지시는 standalone source 안에 있으므로 별도 설정이 필요하지 않음",
                    "선택 사항: 00의 최종 설계 보완 지시(2차 보완 단계에만 반영)",
                    "선택 사항: 12 보고서 링크 게시의 Report API URL",
                ],
                "architecture": {
                    "single_flow": True,
                    "nested_run_flow": False,
                    "human_input": False,
                    "mongodb": False,
                    "embedding": False,
                    "stateful_resume": False,
                    "custom_components_standalone": True,
                    "structured_output": True,
                    "quality_refinement": True,
                },
                "custom_sources_embedded": True,
                "sticky_note_count": len(self.notes),
                "execution_node_count": len(self.nodes),
            },
        }


def _prompt_text() -> str:
    if not PROMPT_PATH.is_file():
        raise FileNotFoundError(f"System prompt is missing: {PROMPT_PATH}")
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    if not prompt.strip():
        raise ValueError("System prompt must not be blank")
    if len(prompt) > 12_000:
        raise ValueError("System prompt exceeds the 12,000-character contract")
    return prompt


def _example_description() -> str:
    return (
        "매주 금요일 오후에 Outlook과 JIRA에서 지난 주 업무 정보를 수집해 팀 주간보고 초안을 만들고 있습니다. "
        "프로젝트별로 완료 업무, 진행 중 업무, 이슈·리스크, 다음 주 계획을 정리하며 각 항목에 원본 메일 제목·링크와 JIRA 이슈 키를 근거로 남겨야 합니다.\n\n"
        "자동 알림·중복 메일은 제외합니다. 메일 조회 실패, 인증 만료, 필수 근거 누락 또는 민감정보 검토가 필요한 경우에는 게시하지 않고 원인과 누락 건수를 담당자에게 보여 줍니다. "
        "초안은 담당자가 검토·수정한 뒤 팀장 승인 후 사내 보고 포털에 게시하고 링크를 관련 팀에 알립니다. "
        "정상·반려·재작업·데이터 오류 경로까지 포함해 현재 업무와 개선 방향을 설계해 주세요."
    )


def build_flow() -> dict[str, Any]:
    runtime = runtime_versions()
    prompt = _prompt_text()
    builder = FlowBuilder(runtime)
    builder.note(
        "input",
        "## 입력\n\n업무 설명과 기능 카탈로그 JSON 파일을 넣고 Run을 누르세요. 실행 중 추가 질문은 나오지 않습니다.",
        (-760, -660),
        width=940,
        height=210,
    )
    builder.note(
        "retrieval",
        "## 검색\n\n상위 100개는 적용 확정 목록이 아니라 LLM이 검토할 후보입니다. 모든 후보의 식별 정보는 유지하고, 상세 정보는 우선순위에 따라 압축합니다. 실제 사용 개수는 0개 이상일 수 있습니다.",
        (260, -660),
        width=860,
        height=210,
    )
    builder.note(
        "design",
        "## 설계\n\n04에서 provider/model/credential을 선택합니다. 05는 1차 설계 JSON을 만들고, 07·08은 부족한 단계·분기·예외·카탈로그 적용 근거를 점검해 한 번 더 보완합니다. 2차 보완이 실패해도 이미 검증한 1차 결과만 안전하게 사용합니다. editable table schema·숨김 프롬프트 설정·자유형 설명문은 다음 단계로 전달되지 않습니다.",
        (1180, -660),
        width=920,
        height=210,
        color="amber",
    )
    builder.note(
        "report",
        "## 보고서\n\n결과의 보완 필요 항목을 업무 설명에 추가한 뒤 Flow 전체를 다시 실행하세요. 00의 선택적 ‘최종 설계 보완 지시’는 2차 보완 단계에만 적용됩니다. 이전 실행을 이어서 처리하지 않습니다.",
        (2920, -660),
        width=920,
        height=210,
        color="amber",
    )

    builder.custom(
        "business_input",
        CUSTOM_COMPONENTS["business_input"],
        (-720, 0),
        {
            "description": _example_description(),
            "additional_instructions": "카탈로그 후보를 참고하되 실제 적용할 항목만 명확한 이유와 함께 선택하고, 사람이 확인해야 하는 판단은 남겨 주세요.",
            "final_refinement_instructions": "보고서에서 현재 업무의 분기·예외·사람 확인 지점과 카탈로그를 적용하는 이유가 한눈에 보이도록 보완해 주세요.",
            "language": "ko",
            "max_model_description_chars": 16_000,
        },
    ).relabel("00 업무 설명 입력", "업무 설명 원문을 안전하게 보존하고 검색·모델용 문맥을 만듭니다.")
    builder.custom(
        "catalog_loader",
        CUSTOM_COMPONENTS["catalog_loader"],
        (-720, 420),
    ).relabel("01 기능 카탈로그 JSON 파일", "업로드한 기능 카탈로그 JSON 한 개를 정규화하고 Agent Hub 링크를 결정합니다.")
    builder.custom(
        "catalog_ranker",
        CUSTOM_COMPONENTS["catalog_ranker"],
        (-260, 180),
        {"top_n": 100, "expanded_detail_count": 12, "max_candidate_chars": 700, "max_context_chars": 56_000},
    ).relabel("02 관련 기능 카탈로그 검색", "로컬 JSON 안에서 업무 설명과 관련된 상위 100개 후보를 찾고, 상세 후보 최대 수(1~30)를 Canvas에서 조절합니다.")
    builder.custom(
        "prompt_builder",
        CUSTOM_COMPONENTS["prompt_builder"],
        (160, 180),
        {"max_prompt_chars": 64_000, "max_estimated_tokens": 20_000},
    ).relabel("03 1차 업무 설계 요청 구성", "안전한 업무 설명과 100개 카탈로그 후보를 압축해 LLM 1차 설계 요청으로 조립합니다.")
    language_model = builder.builtin(
        "language_model",
        "lfx.components.models_and_agents.language_model",
        (580, 180),
        {"stream": False, "temperature": 0.1, "max_tokens": 8192},
    )
    # 04 is deliberately a provider/model configuration node.  It only emits
    # its model object; the actual provider invocation and fixed prompt live in
    # the following standalone 05 component.
    _hide_build_controlled_field(language_model.node, "system_message", "")
    language_model.relabel("04 Language Model (모델 설정)", "provider, model, credential을 선택해 05 1차 생성과 08 최종 보완 생성에 같은 모델 객체를 전달합니다.")
    # Langflow 1.11's built-in Structured Output represents its schema as an
    # editable table of extraction rows.  After an import or model-settings
    # refresh it can revert to the stock ``field`` row, which produces
    # ``{"results": [{"field": ...}]}`` instead of this Flow's one design
    # object.  Use the standalone fixed-Pydantic component so that state is
    # neither editable nor represented as a list of fields.
    structured_output = builder.custom(
        "structured_output",
        CUSTOM_COMPONENTS["structured_output"],
        (980, 180),
    )
    structured_output.relabel(
        "05 1차 업무 설계 JSON 생성",
        "04의 모델 객체와 03의 업무 설계 요청을 고정 Pydantic 계약으로 호출해 business-design-draft/v1 JSON Data 하나만 생성합니다.",
    )
    builder.custom(
        "result_normalizer",
        CUSTOM_COMPONENTS["result_normalizer"],
        (1400, 180),
    ).relabel("06 1차 설계 정규화·검증", "1차 업무 설계 JSON을 검증하고 권위 있는 업무 요청·100개 카탈로그 registry를 다시 결합합니다.")
    builder.custom(
        "quality_prompt",
        CUSTOM_COMPONENTS["quality_prompt"],
        (1820, 180),
    ).relabel("07 설계 품질 점검·보완 요청", "1차 결과의 업무 단계·분기·예외·카탈로그 적용 근거를 점검하고 2차 보완 요청을 만듭니다.")
    refinement_output = builder.custom(
        "refinement_output",
        CUSTOM_COMPONENTS["refinement_output"],
        (2240, 180),
    )
    refinement_output.relabel(
        "08 최종 업무 설계 JSON 보완",
        "04의 같은 모델 객체로 07의 보완 요청을 고정 Pydantic 계약으로 실행합니다. 보완 호출이 불가능하면 1차 결과를 사용하도록 안전한 fallback envelope만 반환합니다.",
    )
    builder.custom(
        "final_normalizer",
        CUSTOM_COMPONENTS["final_normalizer"],
        (2660, 180),
    ).relabel("09 최종 설계 정규화·검증", "2차 설계 JSON을 다시 검증합니다. 보완 호출이 실패하면 동일 요청·후보 집합의 1차 검증 결과만 유지합니다.")
    builder.custom(
        "view_model",
        CUSTOM_COMPONENTS["view_model"],
        (3080, 180),
    ).relabel("10 Report View Model 생성", "최종 검증된 업무 분석, Flow, 카탈로그 적용 계획을 화면용 계약으로 투영합니다.")
    builder.custom(
        "renderer",
        CUSTOM_COMPONENTS["renderer"],
        (3500, 180),
    ).relabel("11 업무 설계 HTML 보고서", "고정 CSS·JS로 안전한 self-contained HTML 보고서를 생성합니다.")
    builder.custom(
        "publisher",
        CUSTOM_COMPONENTS["publisher"],
        (3920, 180),
        {"report_api_url": "", "ttl_hours": 24, "http_timeout_seconds": 30},
    ).relabel("12 보고서 링크 게시", "Report API URL이 있을 때만 보고서를 게시하며 실패해도 HTML은 보존합니다.")
    builder.custom(
        "result_message",
        CUSTOM_COMPONENTS["result_message"],
        (4340, 80),
    ).relabel("13 결과 안내 Message", "보고서 상태, 보완 항목 수, 카탈로그 적용 수와 링크를 사람이 읽기 쉽게 만듭니다.")
    builder.custom(
        "artifact_output",
        CUSTOM_COMPONENTS["artifact_output"],
        (4340, 440),
    ).relabel("14 보고서 결과 Data", "API와 자동 테스트가 HTML, hash, report ID를 회수하는 terminal Data output입니다.")
    chat_output = builder.builtin(
        "chat_output",
        "lfx.components.input_output.chat_output",
        (4760, 80),
        {"should_store_message": False, "session_id": "", "context_id": ""},
    )
    chat_output.relabel("15 Chat Output", "Playground에 짧고 읽기 쉬운 최종 결과 안내를 표시합니다.")

    for source, output, target, field in EXPECTED_EDGES:
        builder.connect(source, output, target, field)
    flow = builder.build()
    validate_flow_payload(flow, check_graph=True)
    return flow


def _node_map(flow: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    data = flow.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        raise ValueError("Flow data.nodes must be a list")
    by_id: dict[str, dict[str, Any]] = {}
    by_key: dict[str, dict[str, Any]] = {}
    for wrapper in data["nodes"]:
        if wrapper.get("type") == "noteNode":
            continue
        node_id = wrapper.get("id")
        node = wrapper.get("data", {}).get("node")
        if not isinstance(node_id, str) or not isinstance(node, dict):
            raise ValueError("Execution node is malformed")
        if node_id in by_id:
            raise ValueError("Duplicate Flow node id")
        key = node.get("metadata", {}).get("flow_node_key")
        if not isinstance(key, str) or not key:
            raise ValueError(f"Flow node {node_id} has no flow_node_key metadata")
        if key in by_key:
            raise ValueError(f"Duplicate Flow node key: {key}")
        by_id[node_id] = wrapper
        by_key[key] = wrapper
    return by_id, by_key


def _edge_tuple(edge: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> tuple[str, str, str, str]:
    source = by_id.get(edge.get("source"))
    target = by_id.get(edge.get("target"))
    if source is None or target is None:
        raise ValueError(f"Dangling edge: {edge.get('id')}")
    source_key = source["data"]["node"]["metadata"]["flow_node_key"]
    target_key = target["data"]["node"]["metadata"]["flow_node_key"]
    source_handle = edge.get("data", {}).get("sourceHandle", {})
    target_handle = edge.get("data", {}).get("targetHandle", {})
    return source_key, source_handle.get("name"), target_key, target_handle.get("fieldName")


def _validate_node_types(flow: dict[str, Any], by_id: dict[str, dict[str, Any]], by_key: dict[str, dict[str, Any]]) -> None:
    if set(by_key) != {*CUSTOM_COMPONENTS, *BUILTIN_COMPONENTS}:
        raise ValueError(f"Unexpected execution nodes: {sorted(by_key)}")
    for key, wrapper in by_key.items():
        node = wrapper["data"]["node"]
        type_name = str(wrapper.get("data", {}).get("type") or "").casefold()
        if any(part in type_name for part in FORBIDDEN_COMPONENT_TYPE_PARTS):
            raise ValueError(f"Forbidden node type in one-flow export: {type_name}")
        for input_name in (node.get("template") or {}):
            normalized = str(input_name).casefold()
            # Chat Output has stock, hidden session/context fields in Langflow
            # 1.11.0.  They are not Flow inputs here: `_validate_builtins`
            # below requires both to be blank and non-persistent.
            if key == "chat_output" and normalized in {"session_id", "context_id"}:
                continue
            if any(part in normalized for part in FORBIDDEN_INPUT_NAME_PARTS):
                raise ValueError(f"Forbidden operational input {input_name!r} on {key}")
        metadata = node.get("metadata") or {}
        standalone = metadata.get("standalone") is True
        if key in CUSTOM_COMPONENTS:
            if not standalone:
                raise ValueError(f"Custom node {key} is not marked standalone")
            source_path = metadata.get("standalone_source_path")
            expected_rel = f"components/single_flow/{CUSTOM_COMPONENTS[key]}"
            if source_path != expected_rel:
                raise ValueError(f"Custom node {key} points to {source_path!r}, not {expected_rel!r}")
            source_file = PROJECT_ROOT / source_path
            source = source_file.read_text(encoding="utf-8")
            if _standalone_violation(source, source_file):
                raise ValueError(f"Custom node {key} source is not standalone")
            embedded = node.get("template", {}).get("code", {}).get("value")
            if embedded != source:
                raise ValueError(f"Custom node {key} embedded source differs from source file")
            if metadata.get("standalone_source_sha256") != _sha256_text(source):
                raise ValueError(f"Custom node {key} embedded source hash differs")
        elif standalone:
            raise ValueError(f"Built-in node {key} must not be marked standalone")


def _validate_edges(flow: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> None:
    edges = flow.get("data", {}).get("edges")
    if not isinstance(edges, list):
        raise ValueError("Flow data.edges must be a list")
    actual = tuple(_edge_tuple(edge, by_id) for edge in edges)
    if actual != EXPECTED_EDGES:
        raise ValueError(f"Flow edge contract differs. actual={actual}")
    targets: set[tuple[str, str]] = set()
    for edge, contract in zip(edges, actual, strict=True):
        _, output_name, target_key, field_name = contract
        target_pair = (target_key, field_name)
        if target_pair in targets:
            raise ValueError(f"Multiple edges feed one input: {target_pair}")
        targets.add(target_pair)
        source_wrapper = by_id[edge["source"]]
        target_wrapper = by_id[edge["target"]]
        source_node = source_wrapper["data"]["node"]
        target_node = target_wrapper["data"]["node"]
        output = next((value for value in source_node.get("outputs", []) if value.get("name") == output_name), None)
        field = target_node.get("template", {}).get(field_name)
        if not isinstance(output, dict) or not isinstance(field, dict):
            raise ValueError(f"Edge handle cannot be resolved: {edge.get('id')}")
        if field.get("advanced") is True:
            raise ValueError(f"An execution edge targets hidden/advanced input: {target_pair}")
        if not set(output.get("types") or []).intersection(field.get("input_types") or []):
            raise ValueError(f"Edge types do not intersect: {edge.get('id')}")
    # Every data-bearing input declared as an execution connection by this
    # Flow contract has exactly one owner. Canvas configuration fields such as
    # 00.description, 01.catalog_json_file and 04.model are intentionally not
    # included: they are entered/selected directly by the operator.
    expected_targets = {(target, field) for _, _, target, field in EXPECTED_EDGES}
    if targets != expected_targets:
        raise ValueError("Connected input ownership differs from the Flow contract")


def _validate_notes(flow: dict[str, Any], runtime: dict[str, str]) -> None:
    notes = [node for node in flow.get("data", {}).get("nodes", []) if node.get("type") == "noteNode"]
    if len(notes) != 4:
        raise ValueError("The Flow must contain exactly four explanatory Sticky Notes")
    for note in notes:
        node = note.get("data", {}).get("node")
        if not isinstance(node, dict) or not str(node.get("description") or "").strip():
            raise ValueError("Sticky Note is malformed")
        if node.get("lf_version") != runtime["langflow"]:
            raise ValueError("Sticky Note runtime version differs")
        if note.get("position") != note.get("positionAbsolute"):
            raise ValueError("Sticky Note absolute position differs")
    note_ids = {note["id"] for note in notes}
    if any(
        edge.get("source") in note_ids or edge.get("target") in note_ids
        for edge in flow.get("data", {}).get("edges", [])
    ):
        raise ValueError("Sticky Notes must not have execution edges")


def _validate_builtins(by_key: dict[str, dict[str, Any]], prompt: str) -> None:
    language_model = by_key["language_model"]["data"]["node"]
    model_template = language_model.get("template", {})
    system_field = model_template.get("system_message")
    if not isinstance(system_field, dict) or system_field.get("value") != "":
        raise ValueError("Language Model system_message must be blank because standalone 05 owns the fixed prompt")
    if system_field.get("show") is not False or system_field.get("advanced") is not True:
        raise ValueError("Language Model system_message must be hidden advanced configuration")
    if model_template.get("stream", {}).get("value") is not False:
        raise ValueError("Language Model stream must be false")
    if model_template.get("max_tokens", {}).get("value") != 8192:
        raise ValueError("Language Model max_tokens must be 8192")
    if model_template.get("temperature", {}).get("value") != 0.1:
        raise ValueError("Language Model temperature must be 0.1")

    structured_output = by_key["structured_output"]["data"]["node"]
    if by_key["structured_output"]["data"].get("type") != "BusinessDesignStructuredOutput":
        raise ValueError("Structured Output must use the fixed standalone Pydantic component")
    structured_template = structured_output.get("template", {})
    if "system_prompt" in structured_template:
        raise ValueError("Business Design Structured Output must embed its fixed system prompt in standalone source, not a Flow template")
    if "output_schema" in structured_template or "schema_name" in structured_template:
        raise ValueError("Business Design Structured Output must not expose Langflow's editable table schema")
    model_input = structured_template.get("model")
    prompt_input = structured_template.get("input_value")
    if not isinstance(model_input, dict) or set(model_input.get("input_types") or []) != {"LanguageModel"}:
        raise ValueError("Business Design Structured Output model input must accept only LanguageModel")
    if not isinstance(prompt_input, dict) or set(prompt_input.get("input_types") or []) != {"Message", "Data", "JSON"}:
        raise ValueError("Business Design Structured Output prompt input must accept exactly Message, Data, and JSON")
    structured_outputs = structured_output.get("outputs") or []
    json_output = next((item for item in structured_outputs if item.get("name") == "structured_output"), None)
    if not isinstance(json_output, dict) or "JSON" not in set(json_output.get("types") or []):
        raise ValueError("Business Design Structured Output must expose a JSON structured_output handle")
    structured_source = (PROJECT_ROOT / "components" / "single_flow" / CUSTOM_COMPONENTS["structured_output"]).read_text(encoding="utf-8")
    if "FIXED_SYSTEM_PROMPT" not in structured_source or "SystemMessage(content=FIXED_SYSTEM_PROMPT)" not in structured_source:
        raise ValueError("Business Design Structured Output must embed and use its fixed standalone system prompt")
    if "from __future__ import annotations" in structured_source:
        raise ValueError("Business Design Structured Output must not use postponed annotations in Langflow's dynamic loader")
    if "_BUSINESS_DESIGN_DRAFT_SCHEMA_READY = BusinessDesignDraftV1.model_rebuild" not in structured_source:
        raise ValueError("Business Design Structured Output must rebuild its Pydantic contract in a loader-executed assignment")

    quality_prompt = by_key["quality_prompt"]
    if quality_prompt["data"].get("type") != "DesignQualityRefinementPrompt":
        raise ValueError("Quality Prompt must use the fixed standalone refinement-prompt component")
    quality_template = quality_prompt["data"]["node"].get("template", {})
    for field_name in ("initial_design_result", "retrieval_result"):
        field = quality_template.get(field_name)
        if not isinstance(field, dict) or not {"Data", "JSON"}.issubset(set(field.get("input_types") or [])):
            raise ValueError(f"Quality Prompt {field_name} must accept Data and JSON")
    quality_outputs = quality_prompt["data"]["node"].get("outputs") or []
    refinement_prompt = next((item for item in quality_outputs if item.get("name") == "refinement_prompt"), None)
    if not isinstance(refinement_prompt, dict) or "Message" not in set(refinement_prompt.get("types") or []):
        raise ValueError("Quality Prompt must expose a Message refinement_prompt handle")

    refinement_output = by_key["refinement_output"]
    if refinement_output["data"].get("type") != "BusinessDesignRefinementStructuredOutput":
        raise ValueError("Final refinement must use the fixed standalone Pydantic component")
    refinement_template = refinement_output["data"]["node"].get("template", {})
    if "system_prompt" in refinement_template or "output_schema" in refinement_template or "schema_name" in refinement_template:
        raise ValueError("Final refinement must not expose editable prompt or table schema fields")
    refinement_model = refinement_template.get("model")
    refinement_input = refinement_template.get("input_value")
    if not isinstance(refinement_model, dict) or set(refinement_model.get("input_types") or []) != {"LanguageModel"}:
        raise ValueError("Final refinement model input must accept only LanguageModel")
    if not isinstance(refinement_input, dict) or set(refinement_input.get("input_types") or []) != {"Message", "Data", "JSON"}:
        raise ValueError("Final refinement prompt input must accept exactly Message, Data, and JSON")
    refinement_outputs = refinement_output["data"]["node"].get("outputs") or []
    refined_draft = next((item for item in refinement_outputs if item.get("name") == "refined_design_draft"), None)
    if not isinstance(refined_draft, dict) or "JSON" not in set(refined_draft.get("types") or []):
        raise ValueError("Final refinement must expose a JSON refined_design_draft handle")
    refinement_source = (PROJECT_ROOT / "components" / "single_flow" / CUSTOM_COMPONENTS["refinement_output"]).read_text(encoding="utf-8")
    if "FIXED_REFINEMENT_SYSTEM_PROMPT" not in refinement_source or "SystemMessage(content=FIXED_REFINEMENT_SYSTEM_PROMPT)" not in refinement_source:
        raise ValueError("Final refinement must embed and use its fixed standalone system prompt")
    if "from __future__ import annotations" in refinement_source:
        raise ValueError("Final refinement must not use postponed annotations in Langflow's dynamic loader")
    if "_BUSINESS_DESIGN_REFINEMENT_SCHEMA_READY = BusinessDesignDraftV1.model_rebuild" not in refinement_source:
        raise ValueError("Final refinement must rebuild its Pydantic contract in a loader-executed assignment")

    for key in ("result_normalizer", "final_normalizer"):
        normalizer = by_key[key]["data"]["node"]
        normalizer_template = normalizer.get("template", {})
        fallback = normalizer_template.get("fallback_design_result")
        if not isinstance(fallback, dict) or fallback.get("required") is not False:
            raise ValueError("Both normalizer instances must expose the optional verified fallback input")
        if not {"Data", "JSON"}.issubset(set(fallback.get("input_types") or [])):
            raise ValueError("Normalizer fallback input must accept Data and JSON")
    ranker_template = by_key["catalog_ranker"]["data"]["node"].get("template", {})
    if ranker_template.get("top_n", {}).get("value") != 100:
        raise ValueError("Catalog Ranker default top_n must be 100")
    expanded_detail_count = ranker_template.get("expanded_detail_count")
    if not isinstance(expanded_detail_count, dict) or expanded_detail_count.get("value") != 12:
        raise ValueError("Catalog Ranker default expanded_detail_count must be 12")
    if expanded_detail_count.get("advanced") is True or expanded_detail_count.get("show") is False:
        raise ValueError("Catalog Ranker expanded_detail_count must be a visible Canvas input")
    prompt_builder_source = (PROJECT_ROOT / "components" / "single_flow" / CUSTOM_COMPONENTS["prompt_builder"]).read_text(encoding="utf-8")
    prompt_hash = "sha256:" + _sha256_text(prompt)
    if prompt_hash not in prompt_builder_source:
        raise ValueError("03 Prompt Builder must embed the fixed system-message SHA-256 constant")
    prompt_length_pattern = rf"SYSTEM_MESSAGE_CHAR_COUNT\s*=\s*{str(len(prompt))[:-3]}_?{str(len(prompt))[-3:]}\b"
    if not re.search(prompt_length_pattern, prompt_builder_source):
        raise ValueError("03 Prompt Builder must embed the fixed system-message character-count constant")
    chat_output = by_key["chat_output"]["data"]["node"].get("template", {})
    if chat_output.get("should_store_message", {}).get("value") is not False:
        raise ValueError("Chat Output should_store_message must be false")
    for field_name in ("session_id", "context_id"):
        if chat_output.get(field_name, {}).get("value") != "":
            raise ValueError(f"Chat Output {field_name} must be blank")


def validate_flow_payload(flow: dict[str, Any], *, check_graph: bool = True) -> dict[str, Any]:
    runtime = runtime_versions()
    if flow.get("id") != FLOW_ID or flow.get("name") != FLOW_NAME:
        raise ValueError("Flow identity differs from the one-flow contract")
    metadata = flow.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("flow_contract") != FLOW_CONTRACT:
        raise ValueError("Flow metadata contract is missing")
    architecture = metadata.get("architecture")
    expected_architecture = {
        "single_flow": True,
        "nested_run_flow": False,
        "human_input": False,
        "mongodb": False,
        "embedding": False,
        "stateful_resume": False,
        "custom_components_standalone": True,
        "structured_output": True,
        "quality_refinement": True,
    }
    if architecture != expected_architecture:
        raise ValueError("Flow architecture metadata differs")
    by_id, by_key = _node_map(flow)
    _validate_notes(flow, runtime)
    _validate_node_types(flow, by_id, by_key)
    _validate_edges(flow, by_id)
    _validate_builtins(by_key, _prompt_text())
    if check_graph:
        graph = Graph.from_payload(flow["data"], flow_id=flow["id"], flow_name=flow["name"], user_id="single-flow-validator")
        if len(graph.vertices) != len(by_key) or len(graph.edges) != len(EXPECTED_EDGES):
            raise ValueError("Langflow Graph import changed the one-flow execution graph")
    return {
        "ok": True,
        "flow": FLOW_NAME,
        "runtime": runtime,
        "execution_nodes": len(by_key),
        "sticky_notes": 4,
        "edges": len(EXPECTED_EDGES),
        "standalone_components": len(CUSTOM_COMPONENTS),
        "system_message_sha256": "sha256:" + _sha256_text(_prompt_text()),
    }


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify the committed JSON exactly matches regenerated content.")
    parser.add_argument("--output", type=Path, default=FLOW_PATH, help="Output path for the Flow JSON.")
    parser.add_argument("--skip-graph", action="store_true", help="Skip Langflow Graph import after structural checks.")
    args = parser.parse_args()

    flow = build_flow()
    payload = _json_bytes(flow)
    output_path = args.output.resolve()
    if args.check:
        if not output_path.is_file():
            raise SystemExit(f"Missing Flow export: {output_path}")
        if output_path.read_bytes() != payload:
            raise SystemExit(f"Flow export is stale: regenerate with {Path(__file__).name}")
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(payload)
    summary = validate_flow_payload(flow, check_graph=not args.skip_graph)
    summary.update({"output": str(output_path), "sha256": _sha256_bytes(payload), "checked": bool(args.check)})
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
