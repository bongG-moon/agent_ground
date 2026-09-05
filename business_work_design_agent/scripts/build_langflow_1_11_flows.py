from __future__ import annotations

"""Build deterministic Langflow 1.11.x Flow exports from standalone sources.

Run this script with a supported Langflow 1.11.x runtime.  Every custom
component node is introspected by the local Langflow runtime itself, and the
exact source bytes read from ``components/`` are embedded in the resulting
Flow JSON.  The generated metadata records the exact runtime that built it.
"""

import argparse
import ast
import hashlib
import importlib
import inspect
import json
import sys
import uuid
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.custom.utils import build_custom_component_template
from lfx.graph.graph.base import Graph


LANGFLOW_MINOR_FAMILY = (1, 11)
LFX_MINOR_FAMILY = (1, 11)
BUNDLE_SCHEMA_VERSION = "business-work-design-flow-bundle/v1"
MONGODB_URI_GLOBAL_VARIABLE = "MONGO_URL"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_ROOT = PROJECT_ROOT / "components"
PROMPT_ROOT = PROJECT_ROOT / "prompts"
SAMPLE_ROOT = PROJECT_ROOT / "samples"
FLOW_ROOT = PROJECT_ROOT / "flows"

FLOW_FILES = {
    "F00": "F00_catalog_file_vector_ingest.json",
    "F10": "F10_work_definition_parent.json",
    "F20": "F20_agent_blueprint_design.json",
    "F30": "F30_responsive_report.json",
    "F90": "F90_search_evaluation.json",
}
BUNDLE_FILE = "00_business_work_design_ALL_FLOWS.json"
MANIFEST_FILE = "build_manifest.json"


def _minor_version(value: str) -> tuple[int, int] | None:
    """Return the numeric major/minor pair without accepting prereleases."""

    parts = value.split(".")
    if len(parts) < 3 or any(not part.isdigit() for part in parts):
        return None
    return int(parts[0]), int(parts[1])


def _require_supported_runtime() -> tuple[str, str]:
    """Resolve the installed public Langflow 1.11 runtime used for export."""

    installed_langflow = version("langflow")
    installed_lfx = version("lfx")
    if _minor_version(installed_langflow) != LANGFLOW_MINOR_FAMILY or _minor_version(installed_lfx) != LFX_MINOR_FAMILY:
        raise RuntimeError(
            "This generator requires Langflow and LFX from the 1.11.x family; "
            f"found langflow=={installed_langflow}, lfx=={installed_lfx}"
        )
    return installed_langflow, installed_lfx


# Flow templates include the Langflow version in each serialized node.  Bind
# it to the runtime that actually introspects the standalone source instead of
# claiming a different patch release than the user's Desktop installation.
LANGFLOW_VERSION, LFX_VERSION = _require_supported_runtime()

FLOW_NAMES = {
    "F00": "F00_catalog_file_vector_ingest",
    "F10": "F10_work_definition_parent",
    "F20": "F20_agent_blueprint_design",
    "F30": "F30_responsive_report",
    "F90": "F90_search_evaluation",
}

FLOW_DESCRIPTIONS = {
    "F00": "전체 catalog 파일을 안전한 테스트 실행으로 먼저 검증하고, 명시 확인 후 deterministic snapshot으로 게시하는 적재 Flow.",
    "F10": "자연어 업무 추출, 최대 3회 선택형 HITL·번호형 Playground 채팅 보완, 최종 승인 후 trusted F20 설계와 F30 반응형 report 생성을 직접 실행하는 top-level Flow.",
    "F20": "trusted invocation의 승인 업무·ACL·Skill scope와 Canvas Embedding Model 기반 hybrid catalog 근거로 Agent Blueprint와 sealed F30 handoff를 만드는 child-safe Flow.",
    "F30": "F20의 sealed handoff를 검증해 반응형 HTML report를 만들고 저장 API에 발행 또는 dry-run 결과를 반환하는 child-safe Flow.",
    "F90": "고정 평가 입력과 Canvas Embedding Model로 query 계획, embedding, hybrid retrieval과 bounded context를 점검하는 검색 QA Flow.",
}

FLOW_READINESS = {
    "F00": "mongodb_vector_ingestion_configuration_required",
    "F10": "configuration_required",
    "F20": "trusted_backend_only_configuration_required",
    "F30": "configuration_required",
    "F90": "evaluation_configuration_required",
}

FLOW_CONTAINS_NATIVE_HITL = {
    "F00": True,
    "F10": True,
    "F20": False,
    "F30": False,
    "F90": False,
}

FLOW_NATIVE_HITL_EXECUTION_REQUIREMENTS = {
    "F00": "durable_langflow_background_job_required_for_continuation_card",
    "F10": "top_level_langflow_playground_hitl",
    "F20": "not_applicable",
    "F30": "not_applicable",
    "F90": "not_applicable",
}

FLOW_REQUIRED_CONFIG = {
    "F00": [
        "one complete current catalog JSON file (not a delta-only update)",
        "Langflow Secret Global Variable MONGO_URL, prefilled business_work_design database, and canonical catalog collections",
        "configured Langflow Embedding Model provider/model/API credential",
        "the writer derives its runtime embedding identity and vector dimension from the connected Embedding Model",
        "MongoDB Atlas Vector Search index (vector retrieval is recommended; F20 keyword fallback can still inspect a published snapshot without it)",
        "optional Continue/Stop card: run F00 as a Langflow durable background job; ordinary Canvas Run Flow saves a checkpoint and requires the same-file rerun",
    ],
    "F10": [
        "팀 명·사번 입력과 Langflow가 자동 제공하는 실행별 run ID (새 실행마다 새 WorkDefinition 생성)",
        "approved extraction and clarification language models",
        "Langflow Secret Global Variable MONGO_URL, Database business_work_design, transaction 지원 MongoDB replica set/Atlas, 그리고 F10 unique/TTL index",
        "Langflow Playground에서 HITL 카드의 답변 입력하기/건너뛰기 선택 후, 같은 Playground 채팅창에 `1번: 답변` 형식으로 답변 (외부 Answer Form/API 불필요)",
        "로컬 확인은 45의 local_demo_fixture, 운영은 trusted_gateway subject/group edge 연결 (사번/Chat Input 직접 연결 금지)",
        "F20 in the same Langflow project/folder; import 뒤에는 F10 Run Flow에서 F20을 다시 선택해 동적 포트를 갱신",
        "F30 in the same Langflow project/folder with the exported flow UUID preserved and its Report API configuration",
    ],
    "F20": [
        "trusted backend scope assembler; do not expose raw node tweaks to end users",
        "approved WorkDefinition",
        "ACL context",
        "approved immutable Skill registry",
        "tenant and active catalog snapshot",
        "configured Langflow Embedding Model provider/model/API credential matching the active catalog snapshot",
        "Langflow Secret Global Variable MONGO_URL and business_work_design database; Atlas lexical/vector indexes enable the full hybrid lane, while the scoped portable keyword lane remains available without them",
        "approved blueprint language model",
        "target new-custom node id",
    ],
    "F30": [
        "F20 sealed report handoff (`f20-report-handoff/v1`)",
        "Report API URL/tenant/actor",
        "bearer token for non-loopback publication",
        "report view signing secret and short capability TTL",
    ],
    "F90": [
        "evaluation WorkDefinition/ACL",
        "tenant and active catalog snapshot",
        "configured Langflow Embedding Model provider/model/API credential matching the active catalog snapshot",
        "Langflow Secret Global Variable MONGO_URL and business_work_design database; Atlas lexical/vector indexes enable the full hybrid lane, while the scoped portable keyword lane remains available without them",
    ],
}

BUILTINS = {
    "Prompt": ("lfx.components.models_and_agents.prompt", "Prompt Template"),
    "LanguageModel": ("lfx.components.models_and_agents.language_model", "LanguageModel"),
    "HumanInput": ("lfx.components.flow_controls.human_input", "HumanInput"),
    "ConditionalRouter": ("lfx.components.flow_controls.conditional_router", "ConditionalRouter"),
    "ParseData": ("lfx.components.processing.parse_data", "ParseData"),
    "MessageToData": ("lfx.components.processing.message_to_data", "MessagetoData"),
    "TypeConverter": ("lfx.components.processing.converter", "TypeConverter"),
    "ChatInput": ("lfx.components.input_output.chat", "ChatInput"),
    "TextInput": ("lfx.components.input_output.text", "TextInput"),
    "ChatOutput": ("lfx.components.input_output.chat_output", "ChatOutput"),
    "RunFlow": ("lfx.components.flow_controls.run_flow", "RunFlow"),
    "EmbeddingModel": ("lfx.components.models_and_agents.embedding_model", "EmbeddingModel"),
}
DYNAMIC_IMPORT_ROOTS = {
    "builtins", "ctypes", "importlib", "marshal", "pickle", "pkgutil", "pydoc",
    "runpy", "shelve", "sys", "zipimport",
}
DYNAMIC_CALL_NAMES = {"eval", "exec", "compile", "__import__", "vars", "globals", "locals"}
DYNAMIC_CALL_ATTRIBUTES = {"import_module", "run_path", "run_module", "SourceFileLoader", "__import__"}
DANGEROUS_EXECUTION_ATTRIBUTES = {"os", "popen", "subprocess", "system"}
FORBIDDEN_INTROSPECTION_ATTRIBUTES = {
    "__base__", "__bases__", "__builtins__", "__class__", "__dict__", "__globals__",
    "__closure__", "__code__", "__getattr__", "__getattribute__", "__import__", "__mro__",
    "__subclasses__", "__traceback__", "ag_frame", "cell_contents", "cr_frame", "f_builtins",
    "f_globals", "f_locals", "func_globals", "gi_frame", "modules", "tb_frame",
}
ALLOWED_IMPORT_ROOTS = {
    "__future__", "base64", "bson", "codecs", "collections", "copy", "datetime",
    "gridfs", "hashlib", "hmac", "html", "httpx", "json", "lfx", "math", "numpy",
    "pathlib", "pymongo", "re", "requests", "socket", "time", "typing", "unicodedata",
    "urllib", "uuid",
}


def _joined_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _joined_string(node.left)
        right = _joined_string(node.right)
        return left + right if left is not None and right is not None else None
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"lower", "casefold", "upper"}
        and not node.args
        and not node.keywords
    ):
        value = _joined_string(node.func.value)
        if value is not None:
            return value.lower() if node.func.attr in {"lower", "casefold"} else value.upper()
    return None


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _stable_suffix(flow_key: str, node_key: str, length: int = 7) -> str:
    digest = hashlib.sha256(f"{flow_key}:{node_key}".encode("utf-8")).hexdigest()
    return digest[:length]


def _flow_uuid(flow_key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"business-work-design-agent/langflow-1.11.1/{flow_key}"))


def _standalone_violation(tree: ast.AST) -> str:
    for node in ast.walk(tree):
        if _joined_string(node) == "__import__":
            return "dynamic import string construction is prohibited"
        if isinstance(node, ast.ImportFrom) and (node.module or "") == "builtins" and any(
            alias.name in DYNAMIC_CALL_NAMES for alias in node.names
        ):
            return "builtins dynamic code/import aliases are prohibited"
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".", 1)[0] in DYNAMIC_IMPORT_ROOTS:
            return "dynamic import helper modules are prohibited"
        if isinstance(node, ast.Import) and any(alias.name.split(".", 1)[0] in DYNAMIC_IMPORT_ROOTS for alias in node.names):
            return "dynamic import helper modules are prohibited"
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in DYNAMIC_CALL_NAMES:
                return "dynamic code/import execution is prohibited"
            if isinstance(node.func, ast.Attribute) and node.func.attr in DYNAMIC_CALL_ATTRIBUTES:
                return "dynamic code/import attribute calls are prohibited"
            if (
                isinstance(node.func, ast.Subscript)
                and isinstance(node.func.value, ast.Call)
                and isinstance(node.func.value.func, ast.Name)
                and node.func.value.func.id == "globals"
                and isinstance(node.func.slice, ast.Constant)
                and node.func.slice.value == "__import__"
            ):
                return "globals dynamic import is prohibited"
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
            ):
                attribute = _joined_string(node.args[1]) if len(node.args) >= 2 else None
                if (
                    attribute is None
                    or attribute in DYNAMIC_CALL_ATTRIBUTES | FORBIDDEN_INTROSPECTION_ATTRIBUTES | DANGEROUS_EXECUTION_ATTRIBUTES
                    or attribute.startswith(("exec", "spawn"))
                ):
                    return "getattr dynamic code/import access is prohibited"
        if isinstance(node, ast.Name) and node.id in {"__builtins__", *DYNAMIC_CALL_NAMES}:
            return "dynamic builtin access is prohibited"
        if isinstance(node, ast.Constant) and node.value == "__import__":
            return "dynamic import string access is prohibited"
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value == "__import__":
            return "subscript dynamic import is prohibited"
        if isinstance(node, ast.Attribute) and (
            node.attr in FORBIDDEN_INTROSPECTION_ATTRIBUTES
            or node.attr in DANGEROUS_EXECUTION_ATTRIBUTES
            or node.attr.startswith(("exec", "spawn"))
            or (node.attr.startswith("__") and node.attr.endswith("__") and node.attr != "__name__")
        ):
            return "dynamic builtin attribute access is prohibited"
        if isinstance(node, ast.Subscript):
            key = _joined_string(node.slice)
            if key in FORBIDDEN_INTROSPECTION_ATTRIBUTES or (
                isinstance(key, str) and key.startswith("__") and key.endswith("__") and key != "__name__"
            ):
                return "dynamic builtin subscript access is prohibited"
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "sys"
            and isinstance(node.value, ast.Name)
            and node.value.id in {"os", "pathlib"}
        ):
            return "indirect sys access is prohibited"
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "sys" and node.attr == "path":
            return "sys.path access is prohibited"
    return ""


def _import_violation(tree: ast.AST) -> str:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if node.level or (root and root not in ALLOWED_IMPORT_ROOTS):
                return f"local, relative, or unapproved import is not standalone: {node.module}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] not in ALLOWED_IMPORT_ROOTS:
                    return f"local or unapproved import is not standalone: {alias.name}"
    return ""


def _source_for_component(filename: str) -> tuple[Path, str]:
    matches = sorted(COMPONENT_ROOT.glob(f"*/{filename}"))
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one component source named {filename!r}, found {len(matches)}")
    path = matches[0]
    source = path.read_bytes().decode("utf-8")
    tree = ast.parse(source, filename=str(path))
    import_violation = _import_violation(tree)
    if import_violation:
        raise ValueError(f"{path}: {import_violation}")
    standalone_violation = _standalone_violation(tree)
    if standalone_violation:
        raise ValueError(f"{path}: {standalone_violation}")
    return path, source


def _builtin_source(kind: str) -> tuple[str, str]:
    if kind not in BUILTINS:
        raise KeyError(f"Unknown builtin kind: {kind}")
    module_name, type_name = BUILTINS[kind]
    module = importlib.import_module(module_name)
    return inspect.getsource(module), type_name


def _component_template(source: str) -> tuple[dict[str, Any], Any]:
    template, instance = build_custom_component_template(Component(_code=source))
    if not isinstance(template, dict) or "template" not in template or "outputs" not in template:
        raise TypeError("Langflow did not return a valid component template")
    return template, instance


def _set_value(node_template: dict[str, Any], field_name: str, value: Any) -> None:
    field = node_template.get("template", {}).get(field_name)
    if not isinstance(field, dict):
        raise KeyError(f"Input field {field_name!r} does not exist on {node_template.get('display_name')!r}")
    field["value"] = value


def _bind_mongodb_uri_global_variable(node_template: dict[str, Any]) -> None:
    """Bind every MongoDB URI input to the shared Langflow Secret variable.

    Langflow 1.11 serializes a Global Variable binding by keeping the variable
    name in ``value`` and setting ``load_from_db``.  The actual connection
    string must never be written into a Flow export.
    """

    field = node_template.get("template", {}).get("mongodb_uri")
    if field is None:
        return
    if not isinstance(field, dict):
        raise TypeError(f"Invalid mongodb_uri template on {node_template.get('display_name')!r}")
    if field.get("_input_type") != "SecretStrInput":
        raise ValueError(
            f"MongoDB URI input on {node_template.get('display_name')!r} must be a SecretStrInput"
        )
    field["value"] = MONGODB_URI_GLOBAL_VARIABLE
    field["load_from_db"] = True


def _serializable_output(
    *,
    name: str,
    display_name: str,
    types: list[str],
    group_outputs: bool = False,
) -> dict[str, Any]:
    return {
        "types": types,
        "selected": types[0] if types else None,
        "name": name,
        "display_name": display_name,
        "method": "route_branch",
        "value": "__UNDEFINED__",
        "cache": True,
        "allows_loop": False,
        "group_outputs": group_outputs,
        "tool_mode": True,
    }


@dataclass
class NodeRef:
    key: str
    node_id: str
    wrapper: dict[str, Any]
    source_path: str | None = None
    source_sha256: str | None = None

    @property
    def node(self) -> dict[str, Any]:
        return self.wrapper["data"]["node"]

    @property
    def type_name(self) -> str:
        return str(self.wrapper["data"]["type"])

    def relabel(self, display_name: str, description: str | None = None) -> "NodeRef":
        """Give a built-in Canvas node a task-oriented, human-readable title.

        Langflow's stock Text Input and Chat Input templates otherwise display
        their generic component names, even when the field is carrying a
        business-critical value.  This changes only the saved Canvas label;
        the component type and its input/output contract remain unchanged.
        """

        title = str(display_name or "").strip()
        if not title:
            raise ValueError("Canvas display name must not be blank")
        self.node["display_name"] = title
        self.wrapper["data"]["display_name"] = title
        if description is not None:
            text = str(description).strip()
            self.node["description"] = text
            self.wrapper["data"]["description"] = text
        return self


class FlowBuilder:
    def __init__(self, flow_key: str) -> None:
        self.flow_key = flow_key
        self.nodes: dict[str, NodeRef] = {}
        # Sticky Notes are Canvas-only annotations.  Keep them separate from
        # executable nodes so they can never become an edge endpoint or affect
        # flow-level handle validation.
        self.notes: list[dict[str, Any]] = []
        self.edges: list[dict[str, Any]] = []

    def note(
        self,
        key: str,
        description: str,
        position: tuple[float, float],
        *,
        width: float,
        height: float,
        background_color: str = "blue",
    ) -> None:
        """Add a Langflow 1.11 Canvas-only Sticky Note.

        The shape deliberately follows Langflow's exported ``noteNode``
        wrapper rather than a generic component wrapper.  Notes have no ports
        and must remain detached from executable graph edges.
        """

        if not key.strip():
            raise ValueError(f"{self.flow_key}: Sticky Note key must not be empty")
        if not description.strip():
            raise ValueError(f"{self.flow_key}: Sticky Note {key!r} must have a description")
        if background_color not in {"blue", "amber"}:
            raise ValueError(f"{self.flow_key}: unsupported Sticky Note color {background_color!r}")
        if width <= 0 or height <= 0:
            raise ValueError(f"{self.flow_key}: Sticky Note dimensions must be positive")

        node_id = f"note-{self.flow_key.lower()}-{key.strip().lower()}"
        if any(note["id"] == node_id for note in self.notes):
            raise ValueError(f"{self.flow_key}: duplicate Sticky Note key {key!r}")
        if any(node.node_id == node_id for node in self.nodes.values()):
            raise ValueError(f"{self.flow_key}: Sticky Note id conflicts with executable node {node_id!r}")

        x, y = float(position[0]), float(position[1])
        canvas_width, canvas_height = float(width), float(height)
        self.notes.append(
            {
                "data": {
                    "id": node_id,
                    "node": {
                        "description": description.strip(),
                        "display_name": "",
                        "documentation": "",
                        "template": {"backgroundColor": background_color},
                        "lf_version": LANGFLOW_VERSION,
                    },
                    "type": "note",
                },
                "dragging": False,
                "height": canvas_height,
                "id": node_id,
                "position": {"x": x, "y": y},
                "positionAbsolute": {"x": x, "y": y},
                "resizing": False,
                "selected": False,
                "style": {"height": canvas_height, "width": canvas_width},
                "type": "noteNode",
                "width": canvas_width,
            }
        )

    def _wrap(
        self,
        *,
        key: str,
        node_template: dict[str, Any],
        type_name: str,
        position: tuple[float, float],
        source_path: str | None = None,
        source_sha256: str | None = None,
    ) -> NodeRef:
        if key in self.nodes:
            raise ValueError(f"Duplicate node key {key!r} in {self.flow_key}")
        prefix = "CustomComponent" if source_path else type_name.replace(" ", "")
        node_id = f"{prefix}-{_stable_suffix(self.flow_key, key)}"
        node_template["lf_version"] = LANGFLOW_VERSION
        metadata = node_template.setdefault("metadata", {})
        metadata["flow_build_target"] = LANGFLOW_VERSION
        metadata["flow_node_key"] = key
        if source_path:
            metadata.update(
                {
                    "standalone": True,
                    "standalone_source_path": source_path,
                    "standalone_source_sha256": source_sha256,
                }
            )
        outputs = node_template.get("outputs") or []
        selected_output = outputs[0].get("name") if outputs else None
        wrapper = {
            "data": {
                "id": node_id,
                "node": node_template,
                "showNode": True,
                "type": type_name,
                "description": node_template.get("description", ""),
                "display_name": node_template.get("display_name", type_name),
                "selected_output": selected_output,
            },
            "dragging": False,
            "id": node_id,
            "measured": {"height": 260, "width": 320},
            "position": {"x": position[0], "y": position[1]},
            "selected": False,
            "type": "genericNode",
        }
        ref = NodeRef(
            key=key,
            node_id=node_id,
            wrapper=wrapper,
            source_path=source_path,
            source_sha256=source_sha256,
        )
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
        _bind_mongodb_uri_global_variable(template)
        relative_path = path.relative_to(PROJECT_ROOT).as_posix()
        source_sha256 = _sha256_text(source)
        type_name = str(getattr(instance, "name", "") or type(instance).__name__.removesuffix("Component"))
        if not type_name:
            raise ValueError(f"Could not determine component type for {filename}")
        return self._wrap(
            key=key,
            node_template=template,
            type_name=type_name,
            position=position,
            source_path=relative_path,
            source_sha256=source_sha256,
        )

    def builtin(
        self,
        key: str,
        kind: str,
        position: tuple[float, float],
        values: dict[str, Any] | None = None,
    ) -> NodeRef:
        source, type_name = _builtin_source(kind)
        template, _instance = _component_template(source)
        for field_name, value in (values or {}).items():
            _set_value(template, field_name, value)
        return self._wrap(key=key, node_template=template, type_name=type_name, position=position)

    def run_flow(
        self,
        key: str,
        position: tuple[float, float],
        child_flow: dict[str, Any],
    ) -> NodeRef:
        """Serialize Langflow 1.11.1 Run Flow in direct (non-tool) mode.

        Run Flow stores its selected child inputs and outputs as dynamic
        ``<child-node-id>~<field>`` ports.  Materializing those ports here makes
        the generated F10 export deterministic and connectable without an HTTP
        call or an Agent-selected tool invocation.

        A Flow JSON import receives a new persistent database ID in Langflow
        Desktop.  Therefore the export must *not* retain the deterministic
        source Flow ID in ``flow_id_selected``: Langflow gives an ID precedence
        over a name and would otherwise try to execute an ID that does not
        exist in the importing user's database.  The dynamic port names remain
        stable because they are child-canvas node IDs, while runtime lookup is
        deliberately by the imported child Flow name.
        """

        source, type_name = _builtin_source("RunFlow")
        template, instance = _component_template(source)
        graph = Graph.from_payload(
            payload=child_flow["data"],
            flow_id=child_flow["id"],
            flow_name=child_flow["name"],
        )
        instance.update_build_config_from_graph(template["template"], graph)
        dynamic_outputs = instance._format_flow_outputs(graph)  # noqa: SLF001 - Langflow's serialization contract
        template["outputs"] = [output.model_dump() for output in dynamic_outputs]
        _set_value(template, "flow_name_selected", child_flow["name"])
        _set_value(template, "flow_id_selected", "")
        _set_value(template, "cache_flow", False)
        flow_name_field = template["template"]["flow_name_selected"]
        flow_name_field["options"] = [child_flow["name"]]
        flow_name_field["options_metadata"] = []
        flow_name_field["selected_metadata"] = {}
        template["tool_mode"] = False
        return self._wrap(key=key, node_template=template, type_name=type_name, position=position)

    def prompt(
        self,
        key: str,
        position: tuple[float, float],
        prompt_text: str,
        variables: list[str],
    ) -> NodeRef:
        source, type_name = _builtin_source("Prompt")
        template, instance = _component_template(source)
        # PromptComponent's f-string parser treats braces in the documented JSON
        # examples as replacement fields.  Escape those literal braces first,
        # then append only the explicit runtime fields as real variables.
        escaped_prompt = prompt_text.rstrip().replace("{", "{{").replace("}", "}}")
        runtime_lines = ["", "## Runtime inputs"]
        runtime_lines.extend(f"- {name}: {{{name}}}" for name in variables)
        rendered_template = escaped_prompt + "\n" + "\n".join(runtime_lines) + "\n"
        _set_value(template, "template", rendered_template)
        _set_value(template, "use_double_brackets", False)
        template = instance._update_template(template)
        for name in variables:
            field = template.get("template", {}).get(name)
            if not isinstance(field, dict) or "Message" not in (field.get("input_types") or []):
                raise ValueError(f"Prompt variable {name!r} was not materialized as a Message input")
        return self._wrap(key=key, node_template=template, type_name=type_name, position=position)

    def human(
        self,
        key: str,
        position: tuple[float, float],
        decisions: list[str],
        *,
        timeout: dict[str, Any] | None = None,
    ) -> NodeRef:
        source, type_name = _builtin_source("HumanInput")
        template, _instance = _component_template(source)
        _set_value(template, "decisions", decisions)
        _set_value(template, "enable_fallback", False)
        if timeout is not None:
            _set_value(template, "timeout", timeout)
        template["outputs"] = [
            _serializable_output(
                name=f"branch_{label.strip().lower().replace(' ', '_')}",
                display_name=label.strip(),
                types=["Message"],
                group_outputs=True,
            )
            for label in decisions
        ]
        return self._wrap(key=key, node_template=template, type_name=type_name, position=position)

    def data_to_message(self, key: str, position: tuple[float, float]) -> NodeRef:
        return self.builtin(key, "ParseData", position, {"template": "{data}", "sep": "\n"})

    def connect(self, source_key: str, output_name: str, target_key: str, field_name: str) -> None:
        source = self.nodes[source_key]
        target = self.nodes[target_key]
        outputs = [item for item in source.node.get("outputs", []) if item.get("name") == output_name]
        if len(outputs) != 1:
            raise ValueError(
                f"{self.flow_key}: output {source_key}.{output_name} not found exactly once; "
                f"available={[item.get('name') for item in source.node.get('outputs', [])]}"
            )
        output = outputs[0]
        field = target.node.get("template", {}).get(field_name)
        if not isinstance(field, dict) or not field.get("show", True):
            raise ValueError(f"{self.flow_key}: target field {target_key}.{field_name} is missing or hidden")
        output_types = list(output.get("types") or [])
        input_types = list(field.get("input_types") or [])
        if not input_types:
            raise ValueError(f"{self.flow_key}: target field {target_key}.{field_name} is not connectable")
        if not set(output_types).intersection(input_types):
            raise TypeError(
                f"{self.flow_key}: incompatible edge {source_key}.{output_name} {output_types} -> "
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

        def encoded(value: dict[str, Any]) -> str:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace('"', "œ")

        encoded_source = encoded(source_handle)
        encoded_target = encoded(target_handle)
        edge_id = f"xy-edge__{source.node_id}{encoded_source}-{target.node_id}{encoded_target}"
        if any(edge["id"] == edge_id for edge in self.edges):
            raise ValueError(f"Duplicate edge {edge_id}")
        self.edges.append(
            {
                "animated": False,
                "className": "",
                "data": {"sourceHandle": source_handle, "targetHandle": target_handle},
                "id": edge_id,
                "selected": False,
                "source": source.node_id,
                "sourceHandle": encoded_source,
                "target": target.node_id,
                "targetHandle": encoded_target,
            }
        )

    def build(self) -> dict[str, Any]:
        name = FLOW_NAMES[self.flow_key]
        endpoint = name.lower().replace("_", "-")
        result = {
            "data": {
                "edges": self.edges,
                "nodes": [*self.notes, *(item.wrapper for item in self.nodes.values())],
                "viewport": {"x": 80, "y": 80, "zoom": 0.55},
            },
            "description": FLOW_DESCRIPTIONS[self.flow_key],
            "endpoint_name": endpoint,
            "icon": None,
            "icon_bg_color": None,
            "id": _flow_uuid(self.flow_key),
            "is_component": False,
            "last_tested_version": LANGFLOW_VERSION,
            "locked": False,
            "mcp_enabled": False,
            "name": name,
            "tags": ["business-work-design", "langflow-1.11.x", "standalone-custom-components"],
            "webhook": False,
            "metadata": {
                "flow_contract": f"business-work-design/{self.flow_key.lower()}/v1",
                "generated_by": "scripts/build_langflow_1_11_flows.py",
                "langflow_version": LANGFLOW_VERSION,
                "operational_readiness": FLOW_READINESS[self.flow_key],
                "required_configuration": FLOW_REQUIRED_CONFIG[self.flow_key],
                "contains_native_hitl": FLOW_CONTAINS_NATIVE_HITL[self.flow_key],
                "native_hitl_execution_requirement": FLOW_NATIVE_HITL_EXECUTION_REQUIREMENTS[self.flow_key],
                "custom_sources_embedded": True,
                "sticky_note_count": len(self.notes),
            },
        }
        _validate_flow_contract(result, self.flow_key)
        return result


def _read_prompt(filename: str) -> str:
    return (PROMPT_ROOT / filename).read_text(encoding="utf-8")


def _read_work_request_example() -> dict[str, Any]:
    path = SAMPLE_ROOT / "f10_work_request_example.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"{path}: expected one JSON object")
    required = ("request_text", "additional_prompt", "team_name", "employee_id")
    values: dict[str, Any] = {}
    for field in required:
        value = payload.get(field)
        if not isinstance(value, str) or (field != "additional_prompt" and not value.strip()):
            raise ValueError(f"{path}: {field} must be a non-empty string")
        values[field] = value
    answer_examples = payload.get("clarification_answer_examples")
    if not isinstance(answer_examples, list) or not 3 <= len(answer_examples) <= 4:
        raise ValueError(f"{path}: clarification_answer_examples must contain three or four examples")
    normalized_answer_examples: list[dict[str, str]] = []
    for index, item in enumerate(answer_examples, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"{path}: clarification_answer_examples[{index}] must be an object")
        normalized: dict[str, str] = {}
        for field in ("topic", "likely_question", "answer_to_enter"):
            value = item.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > 1_000:
                raise ValueError(f"{path}: clarification_answer_examples[{index}].{field} must be a bounded non-empty string")
            normalized[field] = value.strip()
        normalized_answer_examples.append(normalized)
    values["clarification_answer_examples"] = normalized_answer_examples
    skip_guidance = payload.get("clarification_skip_guidance")
    if not isinstance(skip_guidance, dict):
        raise ValueError(f"{path}: clarification_skip_guidance must be an object")
    normalized_skip_guidance: dict[str, str] = {}
    for field in ("action_label", "when_to_use", "result"):
        value = skip_guidance.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > 1_000:
            raise ValueError(f"{path}: clarification_skip_guidance.{field} must be a bounded non-empty string")
        normalized_skip_guidance[field] = value.strip()
    values["clarification_skip_guidance"] = normalized_skip_guidance
    return values


def _build_f00() -> dict[str, Any]:
    flow = FlowBuilder("F00")
    flow.note(
        "01-upload-normalize-chunk",
        """## ① 파일 업로드·정규화·청킹

- JSON/JSONL/NDJSON **현재 전체 카탈로그** 파일 하나를 업로드합니다. 신규분만 든 delta 파일은 올리지 않습니다.
- Loader가 스키마를 검사하고 민감정보를 제거한 canonical 원문을 만듭니다.
- Chunker가 검색 가능한 텍스트 조각과 hash를 결정론적으로 생성합니다.""",
        (-80, -960),
        width=800,
        height=330,
    )
    flow.note(
        "02-embed-store-publish",
        """## ② 테스트 실행 → 임베딩·MongoDB 저장·게시

- Writer는 기본값인 **테스트 실행(저장하지 않음)**으로 file/chunk/hash만 먼저 확인합니다. 다만 Langflow는 연결된 Embedding Model을 먼저 build할 수 있으므로, 실행 전 승인 provider/model(해당 provider가 요구하면 Secret 포함)은 선택해야 합니다. 테스트 실행에서는 실제 embedding 요청·대기·MongoDB 저장은 하지 않습니다.
- 실제 저장은 테스트 실행을 끄고 **전체 카탈로그 파일 확인**을 켠 경우에만 시작합니다. 새 active snapshot은 업로드 파일 전체를 기준으로 교체됩니다.
- Embedding Model은 chunk 하나씩 vector를 생성하고, Writer는 모든 호출·재시도 사이에 최소 1초를 기다립니다. 기본 한 번 실행은 새 청크 최대 80개 또는 내부 180초까지만 처리하고 10개마다 checkpoint를 저장합니다.
- 청크가 남으면 MongoDB checkpoint를 저장합니다. **Langflow durable background job**으로 실행한 경우에만 native HITL 카드가 열려 처리 수·남은 수와 **계속 적재**/**중단하고 나중에 실행**을 선택할 수 있습니다. 계속 적재는 저장된 검증 chunk를 건너뛰고 다음 bounded batch를 처리하며, 중단은 active pointer를 바꾸지 않고 checkpoint만 보존합니다.
- 일반 Canvas **Run Flow** 실행은 durable pause/resume job을 만들지 않으므로 카드가 열리지 않습니다. 이 경우 `PARTIAL_EMBEDDINGS_SAVED` 결과의 진행률을 확인하고 같은 전체 파일·같은 Embedding Model 설정으로 F00을 다시 실행해 이어갑니다. 다른 Flow/API 호출이나 graph Loop는 사용하지 않습니다.
- Writer가 parent와 `catalog_asset_chunks.embedding.vector`를 묶어서 저장하고, concurrent 실행이 pointer를 덮어쓰지 않도록 마지막 게시를 CAS로 확인합니다.
- MongoDB Database는 `business_work_design`으로 미리 채워져 있으며 URI만 환경에 맞게 설정합니다.
- 검증이 모두 성공한 경우에만 active pointer를 갱신합니다. 실패·충돌 시 이전 snapshot은 유지됩니다.""",
        (760, -960),
        width=1200,
        height=430,
        background_color="amber",
    )
    flow.custom("catalog_loader", "00_catalog_json_loader.py", (0, 0))
    flow.custom("catalog_chunker", "01_catalog_deterministic_chunker.py", (420, 0))
    flow.builtin("embedding_model", "EmbeddingModel", (840, -380))
    flow.custom(
        "mongodb_vector_writer",
        "02_catalog_mongodb_vector_writer.py",
        (840, 0),
        {
            "dry_run": True,
            "confirm_complete_catalog_snapshot": False,
            "resume_verified_partial_snapshot": True,
            "pause_for_next_batch": True,
            "max_embedding_chunks_per_run": 80,
            "embedding_run_time_budget_seconds": 180,
            "mongo_write_batch_size": 10,
        },
    )
    flow.data_to_message("ingestion_message", (1260, 0))
    flow.builtin("ingestion_output", "ChatOutput", (1680, 0))

    flow.connect("catalog_loader", "catalog_bundle", "catalog_chunker", "catalog_bundle")
    flow.connect("catalog_chunker", "chunk_bundle", "mongodb_vector_writer", "chunk_bundle")
    flow.connect("embedding_model", "embeddings", "mongodb_vector_writer", "embedding")
    flow.connect("mongodb_vector_writer", "ingestion_result", "ingestion_message", "data")
    flow.connect("ingestion_message", "text", "ingestion_output", "input_value")
    return flow.build()


def _add_work_extraction_nodes(
    flow: FlowBuilder,
    *,
    durable_initial_store: bool = False,
    y: float = 0,
) -> tuple[str, str]:
    request_x = 0
    flow.custom(
        "request_envelope",
        "10_work_request_envelope.py",
        (request_x, y),
        _read_work_request_example(),
    )
    flow.data_to_message("request_message", (request_x + 380, y - 280))
    flow.prompt(
        "extraction_prompt",
        (request_x + 760, y - 280),
        _read_prompt("work_extraction.md"),
        ["request_envelope"],
    )
    flow.builtin(
        "extraction_model",
        "LanguageModel",
        (request_x + 1140, y - 280),
        {"system_message": "Return exactly one JSON object. Do not execute tools.", "stream": False, "temperature": 0.0},
    )
    flow.custom("work_normalizer", "11_work_definition_normalizer.py", (request_x + 1520, y))
    downstream_offset = 380 if durable_initial_store else 0
    work_source_key = "work_normalizer"
    work_source_output = "work_definition"
    if durable_initial_store:
        flow.custom(
            "initial_work_store",
            "18_work_definition_store.py",
            (request_x + 1900, y),
            {
                "command": "save",
                "expected_revision": 0,
                "derive_expected_revision": False,
                "derive_idempotency_key": True,
                "require_transactions": True,
            },
        )
        flow.custom(
            "initial_work_gate",
            "35_result_gate.py",
            (request_x + 2100, y + 360),
            {"required_field": "work_definition"},
        )
        flow.data_to_message("initial_work_failure_message", (request_x + 2480, y + 680))
        flow.builtin("initial_work_failure_output", "ChatOutput", (request_x + 2860, y + 680))
        work_source_key = "initial_work_gate"
        work_source_output = "success_path"
    flow.custom("completeness", "12_work_completeness_evaluator.py", (request_x + 1900 + downstream_offset, y))
    flow.data_to_message("work_message", (request_x + 1900 + downstream_offset, y - 520))
    flow.data_to_message("completeness_message", (request_x + 1900 + downstream_offset, y + 420))
    flow.prompt(
        "clarification_prompt",
        (request_x + 2280 + downstream_offset, y - 280),
        _read_prompt("clarification_planner.md"),
        ["work_definition", "completeness"],
    )
    flow.builtin(
        "clarification_model",
        "LanguageModel",
        (request_x + 2660 + downstream_offset, y - 280),
        {"system_message": "Return one JSON object with at most three questions.", "stream": False, "temperature": 0.0},
    )
    flow.custom(
        "clarification_batch",
        "13_clarification_batch_builder.py",
        (request_x + 3040 + downstream_offset, y),
        {"round_number": 1},
    )

    flow.connect("request_envelope", "request_envelope", "request_message", "data")
    flow.connect("request_message", "text", "extraction_prompt", "request_envelope")
    flow.connect("extraction_prompt", "prompt", "extraction_model", "input_value")
    flow.connect("extraction_model", "text_output", "work_normalizer", "candidate")
    flow.connect("request_envelope", "request_envelope", "work_normalizer", "request_envelope")
    if durable_initial_store:
        flow.connect("work_normalizer", "work_definition", "initial_work_store", "work_definition")
        flow.connect("initial_work_store", "stored_work_definition", "initial_work_gate", "result")
        flow.connect("initial_work_gate", "blocked_path", "initial_work_failure_message", "data")
        flow.connect("initial_work_failure_message", "text", "initial_work_failure_output", "input_value")
    flow.connect(work_source_key, work_source_output, "completeness", "work_definition")
    flow.connect(work_source_key, work_source_output, "work_message", "data")
    flow.connect("completeness", "completeness", "completeness_message", "data")
    flow.connect("work_message", "text", "clarification_prompt", "work_definition")
    flow.connect("completeness_message", "text", "clarification_prompt", "completeness")
    flow.connect("clarification_prompt", "prompt", "clarification_model", "input_value")
    flow.connect(work_source_key, work_source_output, "clarification_batch", "work_definition")
    flow.connect("completeness", "completeness", "clarification_batch", "completeness")
    flow.connect("clarification_model", "text_output", "clarification_batch", "candidate_questions")
    return work_source_key, work_source_output


def _build_f10_legacy(f20_flow: dict[str, Any]) -> dict[str, Any]:
    flow = FlowBuilder("F10")
    flow.note(
        "01-extract-initial-store",
        """## ① 업무 설명 추출·초기 저장

- 자연어 업무 설명과 추가 요구를 WorkDefinition으로 정규화합니다.
- 최초 revision을 MongoDB에 저장한 뒤 부족한 항목을 평가합니다.
- 오류·누락 결과는 Result Gate에서 다음 단계로 전달하지 않습니다.""",
        (-120, -2200),
        width=3650,
        height=500,
    )
    flow.note(
        "02-hitl-round-one",
        """## ② 1차 HITL 보완

- 완성도가 부족하면 최대 세 개의 명확화 질문을 만듭니다.
- Human Input 답변을 로드·병합·저장하고 runtime state를 남깁니다.
- 취소 또는 저장 실패는 안전하게 이 경로를 중단합니다.""",
        (3650, -2200),
        width=3100,
        height=500,
    )
    flow.note(
        "03-hitl-round-two",
        """## ③ 2차 HITL 보완

- 갱신된 WorkDefinition을 다시 평가해 필요한 질문만 이어서 제시합니다.
- 답변과 merge 결과는 revision·idempotency 검사를 통과해야 저장됩니다.
- 차단 결과는 Human Input 또는 후속 LLM으로 진행하지 않습니다.""",
        (6750, -2200),
        width=4550,
        height=500,
    )
    flow.note(
        "04-hitl-round-three-limit",
        """## ④ 3차 HITL 보완·완성도 상한

- 세 번째 답변까지 같은 저장·검증 절차를 적용합니다.
- 네 번째 평가는 질문을 추가로 만들지 않고 review 또는 round-limit 차단을 결정합니다.
- 따라서 무한 재질문 없이 bounded HITL로 업무 정의를 확정합니다.""",
        (11300, -2200),
        width=4500,
        height=500,
        background_color="amber",
    )
    flow.note(
        "05-review-join",
        """## ⑤ 검토 경로 결합

- 각 라운드의 review path를 최신 WorkDefinition 하나로 결합합니다.
- 업무 graph와 preview를 생성·검증한 revision만 승인 대기로 보냅니다.""",
        (15800, -2200),
        width=2200,
        height=500,
        background_color="amber",
    )
    flow.note(
        "06-final-approval",
        """## ⑥ 최종 Human 승인

- Preview를 보고 Approve / Reject / Cancel을 선택합니다.
- 선택 결과는 canonical 상태와 runtime event에 기록됩니다.
- 승인 성공 경로만 다음 설계 단계로 전달됩니다.""",
        (18000, -2200),
        width=3500,
        height=500,
        background_color="amber",
    )
    flow.note(
        "07-f20-direct-run",
        """## ⑦ F20 direct 실행

- 승인 receipt를 다시 검증해 trusted invocation 하나를 만듭니다.
- Run Flow direct mode로 F20을 실행하며 다른 Flow HTTP API를 호출하지 않습니다.""",
        (21500, -2200),
        width=1500,
        height=500,
        background_color="amber",
    )
    work_source_key, work_source_output = _add_work_extraction_nodes(
        flow,
        durable_initial_store=True,
    )

    def add_result_gate(
        prefix: str,
        source_key: str,
        source_output: str,
        *,
        required_field: str,
        x: int,
        y: int,
        authoritative_work: tuple[str, str] | None = None,
        phase: str = "",
    ) -> tuple[str, str]:
        gate_key = f"{prefix}_result_gate"
        flow.custom(gate_key, "35_result_gate.py", (x, y), {"required_field": required_field})
        flow.connect(source_key, source_output, gate_key, "result")
        if authoritative_work is None:
            message_key = f"{prefix}_blocked_message"
            output_key = f"{prefix}_blocked_output"
            flow.data_to_message(message_key, (x + 380, y + 300))
            flow.builtin(output_key, "ChatOutput", (x + 760, y + 300))
            flow.connect(gate_key, "blocked_path", message_key, "data")
            flow.connect(message_key, "text", output_key, "input_value")
            return gate_key, "success_path"

        runtime_key = f"{prefix}_blocked_runtime_state"
        message_key = f"{prefix}_blocked_message"
        output_key = f"{prefix}_blocked_output"
        persist_message_key = f"{prefix}_blocked_persist_failure_message"
        persist_output_key = f"{prefix}_blocked_persist_failure_output"
        flow.custom(
            runtime_key,
            "34_work_runtime_state_store.py",
            (x + 380, y + 300),
            {
                "runtime_status": "BLOCKED",
                "phase": phase or f"{prefix}_blocked",
                "require_transactions": True,
            },
        )
        flow.data_to_message(message_key, (x + 760, y + 180))
        flow.builtin(output_key, "ChatOutput", (x + 1140, y + 180))
        flow.data_to_message(persist_message_key, (x + 760, y + 500))
        flow.builtin(persist_output_key, "ChatOutput", (x + 1140, y + 500))
        flow.connect(authoritative_work[0], authoritative_work[1], runtime_key, "work_definition")
        flow.connect(gate_key, "blocked_path", runtime_key, "route_trigger")
        flow.connect(runtime_key, "success_path", message_key, "data")
        flow.connect(message_key, "text", output_key, "input_value")
        flow.connect(runtime_key, "blocked_path", persist_message_key, "data")
        flow.connect(persist_message_key, "text", persist_output_key, "input_value")
        return gate_key, "success_path"

    def add_runtime_checkpoint(
        prefix: str,
        *,
        work_source: tuple[str, str],
        trigger_source: tuple[str, str],
        runtime_status: str,
        phase: str,
        x: int,
        y: int,
    ) -> tuple[str, str]:
        """Persist a runtime transition and expose only its success branch."""
        runtime_key = f"{prefix}_runtime_state"
        blocked_message_key = f"{prefix}_runtime_failure_message"
        blocked_output_key = f"{prefix}_runtime_failure_output"
        flow.custom(
            runtime_key,
            "34_work_runtime_state_store.py",
            (x, y),
            {
                "runtime_status": runtime_status,
                "phase": phase,
                "require_transactions": True,
            },
        )
        flow.data_to_message(blocked_message_key, (x + 380, y + 260))
        flow.builtin(blocked_output_key, "ChatOutput", (x + 760, y + 260))
        flow.connect(work_source[0], work_source[1], runtime_key, "work_definition")
        flow.connect(trigger_source[0], trigger_source[1], runtime_key, "route_trigger")
        flow.connect(runtime_key, "blocked_path", blocked_message_key, "data")
        flow.connect(blocked_message_key, "text", blocked_output_key, "input_value")
        return runtime_key, "success_path"

    def add_router(round_number: int, current_work_key: str, current_work_output: str, batch_key: str, x: int) -> str:
        router_key = f"clarification_router_r{round_number}"
        flow.custom(router_key, "27_work_clarification_router.py", (x, 0))
        flow.connect(current_work_key, current_work_output, router_key, "work_definition")
        flow.connect(batch_key, "clarification_batch", router_key, "clarification_result")
        blocked_state_key = f"blocked_runtime_state_r{round_number}"
        blocked_message_key = f"blocked_runtime_message_r{round_number}"
        blocked_output_key = f"blocked_runtime_output_r{round_number}"
        persistence_failure_message_key = f"blocked_runtime_persist_failure_message_r{round_number}"
        persistence_failure_output_key = f"blocked_runtime_persist_failure_output_r{round_number}"
        flow.custom(
            blocked_state_key,
            "34_work_runtime_state_store.py",
            (x, 620),
            {
                "runtime_status": "BLOCKED",
                "phase": f"clarification_round_{round_number}_blocked",
                "require_transactions": True,
            },
        )
        flow.data_to_message(blocked_message_key, (x + 380, 620))
        flow.builtin(blocked_output_key, "ChatOutput", (x + 760, 620))
        flow.data_to_message(persistence_failure_message_key, (x + 380, 900))
        flow.builtin(persistence_failure_output_key, "ChatOutput", (x + 760, 900))
        flow.connect(current_work_key, current_work_output, blocked_state_key, "work_definition")
        flow.connect(router_key, "blocked_path", blocked_state_key, "route_trigger")
        flow.connect(blocked_state_key, "success_path", blocked_message_key, "data")
        flow.connect(blocked_message_key, "text", blocked_output_key, "input_value")
        flow.connect(blocked_state_key, "blocked_path", persistence_failure_message_key, "data")
        flow.connect(persistence_failure_message_key, "text", persistence_failure_output_key, "input_value")
        return router_key

    def add_answer_round(round_number: int, router_key: str, x: int) -> tuple[str, str]:
        suffix = f"r{round_number}"
        message_key = f"batch_message_{suffix}"
        gate_key = f"answer_gate_{suffix}"
        waiting_state_key = f"waiting_runtime_state_{suffix}"
        merging_state_key = f"merging_runtime_state_{suffix}"
        waiting_failure_message_key = f"waiting_runtime_failure_message_{suffix}"
        waiting_failure_output_key = f"waiting_runtime_failure_output_{suffix}"
        merging_failure_message_key = f"merging_runtime_failure_message_{suffix}"
        merging_failure_output_key = f"merging_runtime_failure_output_{suffix}"
        loader_key = f"answer_loader_{suffix}"
        merger_key = f"answer_merger_{suffix}"
        store_key = f"answered_work_store_{suffix}"
        cancel_store_key = f"answer_cancel_store_{suffix}"
        flow.custom(
            waiting_state_key,
            "34_work_runtime_state_store.py",
            (x, -440),
            {
                "runtime_status": "WAITING_ANSWER",
                "phase": f"clarification_round_{round_number}_waiting",
                "require_transactions": True,
            },
        )
        flow.data_to_message(waiting_failure_message_key, (x + 380, -820))
        flow.builtin(waiting_failure_output_key, "ChatOutput", (x + 760, -820))
        flow.data_to_message(message_key, (x + 380, -440))
        flow.human(gate_key, (x + 760, -440), ["Submit Answers", "Cancel"])
        flow.custom(
            merging_state_key,
            "34_work_runtime_state_store.py",
            (x + 1140, -760),
            {
                "runtime_status": "MERGING",
                "phase": f"clarification_round_{round_number}_resume",
                "require_transactions": True,
            },
        )
        flow.data_to_message(merging_failure_message_key, (x + 1520, -980))
        flow.builtin(merging_failure_output_key, "ChatOutput", (x + 1900, -980))
        flow.custom(
            loader_key,
            "14_work_answer_loader.py",
            (x + 1520, -440),
            {"answer_source_mode": "mongodb"},
        )
        flow.custom(merger_key, "15_work_answer_merger.py", (x + 1900, -440))
        flow.custom(
            store_key,
            "18_work_definition_store.py",
            (x + 2280, -440),
            {
                "command": "save",
                "derive_expected_revision": True,
                "incoming_revision_is_next": True,
                "derive_idempotency_key": True,
                "require_transactions": True,
            },
        )
        flow.custom(
            cancel_store_key,
            "18_work_definition_store.py",
            (x + 1140, 400),
            {
                "command": "cancel",
                "derive_expected_revision": True,
                "derive_idempotency_key": True,
                "require_transactions": True,
            },
        )
        loader_gate_key, loader_gate_output = add_result_gate(
            f"answer_loader_{suffix}",
            loader_key,
            "answer_submission",
            required_field="answer_submission",
            x=x + 1710,
            y=20,
            authoritative_work=(router_key, "clarification_path"),
            phase=f"clarification_round_{round_number}_answer_invalid",
        )
        merger_gate_key, merger_gate_output = add_result_gate(
            f"answer_merger_{suffix}",
            merger_key,
            "merged_work_definition",
            required_field="work_definition",
            x=x + 2090,
            y=20,
            authoritative_work=(router_key, "clarification_path"),
            phase=f"clarification_round_{round_number}_merge_blocked",
        )
        # A Store timeout can mean that MongoDB committed but the response was
        # lost. Stop the Flow and retry the same idempotency key; do not write a
        # stale BLOCKED revision before reconciliation.
        store_gate_key, store_gate_output = add_result_gate(
            f"answered_store_{suffix}",
            store_key,
            "stored_work_definition",
            required_field="work_definition",
            x=x + 2470,
            y=20,
        )
        cancel_gate_key, cancel_gate_output = add_result_gate(
            f"cancel_store_{suffix}",
            cancel_store_key,
            "stored_work_definition",
            required_field="work_definition",
            x=x + 1520,
            y=620,
        )
        cancel_runtime_key, cancel_runtime_output = add_runtime_checkpoint(
            f"answer_cancel_{suffix}",
            work_source=(cancel_gate_key, cancel_gate_output),
            trigger_source=(cancel_gate_key, cancel_gate_output),
            runtime_status="CANCELLED",
            phase=f"clarification_round_{round_number}_cancelled",
            x=x + 1900,
            y=620,
        )
        cancel_success_message_key = f"cancel_success_message_{suffix}"
        cancel_success_output_key = f"cancel_success_output_{suffix}"
        flow.data_to_message(cancel_success_message_key, (x + 2280, 620))
        flow.builtin(cancel_success_output_key, "ChatOutput", (x + 2660, 620))
        flow.connect(cancel_runtime_key, cancel_runtime_output, cancel_success_message_key, "data")
        flow.connect(cancel_success_message_key, "text", cancel_success_output_key, "input_value")
        flow.connect(router_key, "clarification_path", waiting_state_key, "work_definition")
        flow.connect(router_key, "clarification_path", waiting_state_key, "route_trigger")
        flow.connect(waiting_state_key, "success_path", message_key, "data")
        flow.connect(waiting_state_key, "blocked_path", waiting_failure_message_key, "data")
        flow.connect(waiting_failure_message_key, "text", waiting_failure_output_key, "input_value")
        flow.connect(message_key, "text", gate_key, "prompt")
        flow.connect(router_key, "clarification_path", loader_key, "work_definition")
        flow.connect(router_key, "clarification_path", loader_key, "clarification_batch")
        flow.connect(gate_key, "branch_submit_answers", loader_key, "human_action")
        flow.connect(router_key, "clarification_path", merging_state_key, "work_definition")
        flow.connect(gate_key, "branch_submit_answers", merging_state_key, "route_trigger")
        flow.connect(merging_state_key, "success_path", loader_key, "route_trigger")
        flow.connect(merging_state_key, "blocked_path", merging_failure_message_key, "data")
        flow.connect(merging_failure_message_key, "text", merging_failure_output_key, "input_value")
        flow.connect(router_key, "clarification_path", merger_key, "work_definition")
        flow.connect(loader_gate_key, loader_gate_output, merger_key, "answer_submission")
        flow.connect(merger_gate_key, merger_gate_output, store_key, "work_definition")
        flow.connect(router_key, "clarification_path", cancel_store_key, "work_definition")
        flow.connect(gate_key, "branch_cancel", cancel_store_key, "route_trigger")
        reconciled_runtime_key, reconciled_runtime_output = add_runtime_checkpoint(
            f"answered_store_{suffix}_reconciled",
            work_source=(store_gate_key, store_gate_output),
            trigger_source=(store_gate_key, store_gate_output),
            runtime_status="MERGING",
            phase=f"clarification_round_{round_number}_stored",
            x=x + 2850,
            y=20,
        )
        return reconciled_runtime_key, reconciled_runtime_output

    def add_question_round(
        round_number: int,
        current_work_key: str,
        current_work_output: str,
        x: int,
    ) -> str:
        suffix = f"r{round_number}"
        completeness_key = f"completeness_{suffix}"
        work_message_key = f"work_message_{suffix}"
        completeness_message_key = f"completeness_message_{suffix}"
        prompt_key = f"clarification_prompt_{suffix}"
        model_key = f"clarification_model_{suffix}"
        batch_key = f"clarification_batch_{suffix}"
        flow.custom(completeness_key, "12_work_completeness_evaluator.py", (x, 0))
        flow.data_to_message(work_message_key, (x, -520))
        flow.data_to_message(completeness_message_key, (x, 480))
        flow.prompt(
            prompt_key,
            (x + 380, -280),
            _read_prompt("clarification_planner.md"),
            ["work_definition", "completeness"],
        )
        flow.builtin(
            model_key,
            "LanguageModel",
            (x + 760, -280),
            {"system_message": "Return one JSON object with at most three questions.", "stream": False, "temperature": 0.0},
        )
        flow.custom(
            batch_key,
            "13_clarification_batch_builder.py",
            (x + 1140, 0),
            {"round_number": round_number},
        )
        flow.connect(current_work_key, current_work_output, completeness_key, "work_definition")
        flow.connect(current_work_key, current_work_output, work_message_key, "data")
        flow.connect(completeness_key, "completeness", completeness_message_key, "data")
        flow.connect(work_message_key, "text", prompt_key, "work_definition")
        flow.connect(completeness_message_key, "text", prompt_key, "completeness")
        flow.connect(prompt_key, "prompt", model_key, "input_value")
        flow.connect(current_work_key, current_work_output, batch_key, "work_definition")
        flow.connect(completeness_key, "completeness", batch_key, "completeness")
        flow.connect(model_key, "text_output", batch_key, "candidate_questions")
        return add_router(round_number, current_work_key, current_work_output, batch_key, x + 1520)

    router_r1 = add_router(1, work_source_key, work_source_output, "clarification_batch", 3800)
    answer_r1_key, answer_r1_output = add_answer_round(1, router_r1, 4180)
    router_r2 = add_question_round(2, answer_r1_key, answer_r1_output, 6840)
    answer_r2_key, answer_r2_output = add_answer_round(2, router_r2, 8740)
    router_r3 = add_question_round(3, answer_r2_key, answer_r2_output, 11400)
    answer_r3_key, answer_r3_output = add_answer_round(3, router_r3, 13300)

    # Round 4 is deliberately non-interactive: it can only release a complete
    # definition to review or stop on CLARIFICATION_ROUND_LIMIT.
    flow.custom("completeness_r4", "12_work_completeness_evaluator.py", (15960, 0))
    flow.custom(
        "clarification_batch_r4",
        "13_clarification_batch_builder.py",
        (16340, 0),
        {"round_number": 4},
    )
    flow.connect(answer_r3_key, answer_r3_output, "completeness_r4", "work_definition")
    flow.connect(answer_r3_key, answer_r3_output, "clarification_batch_r4", "work_definition")
    flow.connect("completeness_r4", "completeness", "clarification_batch_r4", "completeness")
    router_r4 = add_router(4, answer_r3_key, answer_r3_output, "clarification_batch_r4", 16720)

    # Fold the mutually exclusive review exits from the deepest round outward.
    flow.custom("branch_joiner_r3", "28_work_definition_branch_joiner.py", (17100, 0))
    flow.custom("branch_joiner_r2", "28_work_definition_branch_joiner.py", (17480, 0))
    flow.custom("branch_joiner_r1", "28_work_definition_branch_joiner.py", (17860, 0))
    flow.connect(router_r4, "review_path", "branch_joiner_r3", "answered_work_definition")
    flow.connect(router_r3, "review_path", "branch_joiner_r3", "review_work_definition")
    flow.connect("branch_joiner_r3", "joined_work_definition", "branch_joiner_r2", "answered_work_definition")
    flow.connect(router_r2, "review_path", "branch_joiner_r2", "review_work_definition")
    flow.connect("branch_joiner_r2", "joined_work_definition", "branch_joiner_r1", "answered_work_definition")
    flow.connect(router_r1, "review_path", "branch_joiner_r1", "review_work_definition")

    flow.custom("graph_normalizer", "16_work_graph_normalizer.py", (18240, 0))
    flow.custom("preview", "17_work_preview_hasher.py", (18620, 0))
    flow.custom(
        "review_work_store",
        "18_work_definition_store.py",
        (19000, 0),
        {
            "command": "save",
            "derive_expected_revision": True,
            "derive_idempotency_key": True,
            "require_transactions": True,
        },
    )
    flow.custom(
        "request_approval_store",
        "18_work_definition_store.py",
        (19380, 0),
        {
            "command": "request_approval",
            "derive_expected_revision": True,
            "derive_idempotency_key": True,
            "require_transactions": True,
        },
    )
    flow.data_to_message("preview_message", (19760, -360))
    flow.human("approval_gate", (20140, -360), ["Approve", "Reject", "Cancel"])
    final_store_specs = (
        ("approved_work_store", "approve", -540),
        ("rejected_work_store", "reject", 0),
        ("final_cancel_store", "cancel", 540),
    )
    for key, command, store_y in final_store_specs:
        flow.custom(
            key,
            "18_work_definition_store.py",
            (20520, store_y),
            {
                "command": command,
                "derive_expected_revision": True,
                "derive_idempotency_key": True,
                "require_transactions": True,
            },
        )

    join_gate_key, join_gate_output = add_result_gate(
        "review_join",
        "branch_joiner_r1",
        "joined_work_definition",
        required_field="work_definition",
        x=18050,
        y=420,
    )
    graph_gate_key, graph_gate_output = add_result_gate(
        "review_graph",
        "graph_normalizer",
        "normalized_graph",
        required_field="work_definition",
        x=18430,
        y=420,
        authoritative_work=(join_gate_key, join_gate_output),
        phase="review_graph_blocked",
    )
    preview_gate_key, preview_gate_output = add_result_gate(
        "review_preview",
        "preview",
        "preview",
        required_field="work_definition.preview_hash",
        x=18810,
        y=420,
        authoritative_work=(graph_gate_key, graph_gate_output),
        phase="review_preview_blocked",
    )
    review_store_gate_key, review_store_gate_output = add_result_gate(
        "review_store",
        "review_work_store",
        "stored_work_definition",
        required_field="work_definition",
        x=19190,
        y=420,
    )
    approval_store_gate_key, approval_store_gate_output = add_result_gate(
        "request_approval_store",
        "request_approval_store",
        "stored_work_definition",
        required_field="work_definition",
        x=19570,
        y=420,
    )
    ready_runtime_key, ready_runtime_output = add_runtime_checkpoint(
        "review_ready",
        work_source=(review_store_gate_key, review_store_gate_output),
        trigger_source=(review_store_gate_key, review_store_gate_output),
        runtime_status="READY_FOR_REVIEW",
        phase="review_ready",
        x=19570,
        y=960,
    )
    waiting_approval_runtime_key, waiting_approval_runtime_output = add_runtime_checkpoint(
        "approval_waiting",
        work_source=(approval_store_gate_key, approval_store_gate_output),
        trigger_source=(approval_store_gate_key, approval_store_gate_output),
        runtime_status="WAITING_APPROVAL",
        phase="approval_waiting",
        x=19950,
        y=960,
    )
    final_gate_specs: list[tuple[str, str, str]] = []
    for key, command, store_y in final_store_specs:
        final_gate_key, final_gate_output = add_result_gate(
            f"final_{command}",
            key,
            "stored_work_definition",
            required_field="work_definition",
            x=20900,
            y=store_y,
        )
        if command != "approve":
            success_message_key = f"final_{command}_success_message"
            success_output_key = f"final_{command}_success_output"
            success_source = (final_gate_key, final_gate_output)
            if command == "cancel":
                success_source = add_runtime_checkpoint(
                    "final_cancelled",
                    work_source=(final_gate_key, final_gate_output),
                    trigger_source=(final_gate_key, final_gate_output),
                    runtime_status="CANCELLED",
                    phase="approval_cancelled",
                    x=21280,
                    y=store_y,
                )
            flow.data_to_message(success_message_key, (21660, store_y))
            flow.builtin(success_output_key, "ChatOutput", (22040, store_y))
            flow.connect(success_source[0], success_source[1], success_message_key, "data")
            flow.connect(success_message_key, "text", success_output_key, "input_value")
        final_gate_specs.append((key, command, final_gate_key))

    flow.connect(join_gate_key, join_gate_output, "graph_normalizer", "work_definition")
    flow.connect(graph_gate_key, graph_gate_output, "preview", "work_definition")
    flow.connect(preview_gate_key, preview_gate_output, "review_work_store", "work_definition")
    flow.connect(ready_runtime_key, ready_runtime_output, "request_approval_store", "work_definition")
    flow.connect(waiting_approval_runtime_key, waiting_approval_runtime_output, "preview_message", "data")
    flow.connect("preview_message", "text", "approval_gate", "prompt")
    for key, command, _final_gate_key in final_gate_specs:
        flow.connect(approval_store_gate_key, approval_store_gate_output, key, "work_definition")
        flow.connect("approval_gate", f"branch_{command}", key, "route_trigger")

    # Approval is the only path allowed to invoke F20.  Component 36 re-reads
    # the canonical approved revision, active catalog pointer, and approved
    # Skill registry from MongoDB before emitting one bounded child input.
    approved_gate_key = next(
        gate_key for _store_key, command, gate_key in final_gate_specs if command == "approve"
    )
    flow.custom(
        "design_invocation_loader",
        "36_approved_design_invocation_loader.py",
        (21660, -760),
    )
    flow.builtin(
        "design_invocation_message",
        "TypeConverter",
        (22040, -760),
        {"auto_parse": False, "output_type": "Message"},
    )
    flow.run_flow("run_agent_blueprint_design", (22420, -760), f20_flow)
    flow.builtin("final_approve_design_output", "ChatOutput", (22800, -760))
    flow.builtin("design_invocation_blocked_output", "ChatOutput", (22040, -1120))

    invocation_input_field = f"{f20_flow['metadata']['design_invocation_input_node_id']}~input_value"
    report_handoff_output_name = f"{f20_flow['metadata']['report_handoff_output_node_id']}~message"
    flow.connect(approved_gate_key, "success_path", "design_invocation_loader", "approval_result")
    flow.connect("request_envelope", "request_envelope", "design_invocation_loader", "request_envelope")
    flow.connect("design_invocation_loader", "success_path", "design_invocation_message", "input_data")
    flow.connect("design_invocation_message", "message_output", "run_agent_blueprint_design", invocation_input_field)
    flow.connect("run_agent_blueprint_design", report_handoff_output_name, "final_approve_design_output", "input_value")
    flow.connect("design_invocation_loader", "blocked_path", "design_invocation_blocked_output", "input_value")
    return flow.build()


def _build_f10(f20_flow: dict[str, Any], f30_flow: dict[str, Any]) -> dict[str, Any]:
    """Build the compact, bounded native-HITL parent Flow.

    The old expanded graph is intentionally retained above as an implementation
    history reference, but is no longer exported.  Its state-store, result
    gate, message conversion, and answer-loader plumbing now live inside a
    few standalone components.  The Canvas keeps the three native question
    cards visible while staying readable at normal zoom.  The deployed 1.11.0
    Human Input contract exposes choice buttons but cannot render dynamic
    answer fields, so each card lets the user choose **답변 입력하기** and then
    sends a copyable numbered template to the normal Playground Chat Input.
    No companion Answer Form/API is required.
    """

    flow = FlowBuilder("F10")
    example = _read_work_request_example()
    employee_id = example["employee_id"]
    answer_examples = example["clarification_answer_examples"]
    skip_guidance = example["clarification_skip_guidance"]
    clarification_example_lines = [
        "### 이 데모에서 Playground 채팅창에 보낼 답변 예시",
        "- 실제 질문의 문구와 순서는 LLM 결과에 따라 달라질 수 있습니다. HITL 카드에서 **답변 입력하기**를 누른 뒤, 안내문에 있는 번호를 유지해 `1번: ...` 형식으로 한 번에 보냅니다.",
    ]
    for index, item in enumerate(answer_examples, start=1):
        clarification_example_lines.extend(
            [
                f"{index}. **{item['topic']}**",
                f"   - 예상 질문: {item['likely_question']}",
                f"   - 채팅 입력 예시: {index}번: {item['answer_to_enter']}",
            ]
        )
    clarification_example_note = "\n".join(clarification_example_lines)

    flow.note(
        "01-intake-and-extract",
        """## ① 업무 설명 입력·추출

- 직접 입력: 업무 설명 원문, 추가 설계 프롬프트, 팀 명, 사번입니다.
- 자동·내부: 실행별 run ID/session, WorkDefinition ID, 기준 시각, catalog scope(`default`)입니다. 입력하지 않습니다. 새 전체 실행은 새 WorkDefinition과 질문 Batch를 만듭니다.
- Canvas의 왼쪽 Text Input 네 개에는 복합 생산·프로젝트 리스크 보고 데모 원문, 설계 프롬프트, 팀 명, 사번이 이미 채워져 있습니다. 사번은 새 업무 실행과 이후 질문 답변 재개가 함께 사용하므로, 질문을 받은 뒤에는 같은 값을 유지합니다.
- 실제 긴 문장은 `samples/f10_work_request_example.json`과 각 Text Input 필드에서 그대로 확인·수정합니다.
- F10에는 Playground Chat Input이 하나 있습니다. **새 업무를 시작할 때는 채팅 입력을 비우거나 `새 업무 시작`이라고 보내면** Text Input의 업무 설명을 사용합니다. 질문에 답할 때만 이 채팅 입력에 `1번: 답변`을 보냅니다. 49번 노드가 두 실행을 분리하므로 새 업무와 기존 답변이 함께 처리되지 않습니다.
- 첫 질문이 필요한 경우에만 질문 Batch가 revision 0 WorkDefinition을 준비합니다.""",
        (-100, -850),
        width=1780,
        height=470,
    )
    flow.note(
        "02-three-round-hitl",
        f"""## ② 최대 3회 HITL 보완 — 1.11.0 호환 입력 방식

- 각 회차는 완전성 평가 → 질문 생성 → **선택형 질문 카드** → 번호형 채팅 답변 반영 순서입니다.
- 질문 카드에서 **답변 입력하기**를 누르면, 이 Flow의 결과 메시지에 질문과 복사 가능한 답변 양식이 나옵니다. 그 양식을 같은 Playground 하단 채팅창에 붙여 넣고 `1번: ...`, `2번: ...`처럼 답한 뒤 다시 전송합니다.
- 질문 카드 자체에는 입력칸이 없습니다. 이는 현재 운영 Langflow 1.11.0의 Human Input이 선택 버튼만 지원하기 때문입니다. 답변 텍스트는 `46 번호형 대화 답변 Parser`가 검증한 뒤 `39 답변 반영·다음 단계`로 자동 전달합니다.
- 답변 중인 질문이 한 개라면 `질문 묶음: qb-...` 줄은 생략해도 됩니다. 동시에 여러 건이 열려 있으면 안내문에 나온 질문 묶음 ID를 답변 맨 위에 그대로 넣어 구분합니다.
- **{skip_guidance['action_label']}**: {skip_guidance['when_to_use']}
- 동작: {skip_guidance['result']}
- 13/39/47 노드의 MongoDB URI는 공통 Langflow Secret `MONGO_URL`에 자동 연결됩니다. Database는 `business_work_design`으로 미리 채워져 있고, 세 회차에 같은 값을 사용합니다.
- `clarification_batches`는 질문·답변 이력을 보관하는 내부 MongoDB 컬렉션입니다. 사람이 컬렉션에 직접 입력할 필요는 없습니다.
- 1·2차 질문은 최대 3개, 마지막 3차 질문은 최대 4개입니다. HITL 보완 회차는 여전히 최대 3회입니다.
- 세 번째 답변 뒤에도 필수 정보가 남으면 추가 질문 없이 차단합니다.

{clarification_example_note}""",
        (1750, -850),
        width=5350,
        height=820,
        background_color="amber",
    )
    flow.note(
        "03-review-preview",
        """## ③ 업무 Graph·Preview 검토

- 유효한 검토 진입 결과 하나만 합칩니다.
- Joiner의 여러 입력은 각 회차 결과가 자동으로 한 경로만 들어오는 연결입니다. 직접 입력하지 않습니다.
- AS-IS Graph와 Preview hash를 검증한 뒤, 하나의 저장 노드에서 승인 대기로 전환합니다.""",
        (7160, -850),
        width=1650,
        height=320,
    )
    flow.note(
        "03a-review-save-approval-input-guide",
        """## ③-1 검토 저장·승인 요청: 입력 안내

- 역할: 검증된 업무 정의를 저장하고 `WAITING_APPROVAL`로 전환합니다.
- 자동 연결(환경): MongoDB URI는 공통 Secret `MONGO_URL`입니다. Database는 `business_work_design`으로 미리 채워져 있습니다.
- 실행 전 확인: transaction 가능한 MongoDB replica set/Atlas와 `work_definitions(tenant_id,work_definition_id)` unique, `clarification_batches(tenant_id,batch_id)` unique/`expires_at` TTL index가 필요합니다. 준비되지 않으면 저장은 차단되고 Component 41이 안전한 안내만 표시합니다.
- 자동 연결: WorkDefinition, 사번, 실행 신호입니다.
- 자동값: Revision(동시 수정 덮어쓰기 방지), 중복 실행 방지 키(재시도 중복 저장 방지)입니다.
- 내부 고정: `work_definitions`(현재 업무 정의), `work_definition_events`(변경 이력), 처리 명령·Transaction·Timeout입니다.""",
        (8550, -520),
        width=1780,
        height=500,
        background_color="amber",
    )
    flow.note(
        "04-final-approval",
        """## ④ 최종 Human 승인

- Preview를 검토하고 Approve / Reject / Cancel을 선택합니다.
- 사용자는 세 선택지 중 하나만 선택합니다. 후속 상태 저장의 사번·revision·중복 방지 키는 자동 처리됩니다.
- `43 최종 승인 경로 Gate`는 선택하지 않은 두 저장 경로를 즉시 제외합니다. Human Input의 버튼을 누른 뒤에는 이 노드에 값을 입력하지 않습니다.
- 승인 성공 경로만 F20 설계 실행으로 연결됩니다.""",
        (8860, -850),
        width=1680,
        height=320,
        background_color="amber",
    )
    flow.note(
        "05-direct-f20-f30",
        """## ⑤ F20 설계 → F30 보고서 직접 실행

- 승인본을 MongoDB에서 다시 검증해 strict JSON invocation으로 만듭니다.
- `45 인증 Context 경계`가 로컬 데모 fixture와 운영 gateway 인증을 구분합니다. 기본 `local_demo_fixture`는 예제 확인용이며 결과에 **미검증**으로 남습니다. 운영 전에는 `trusted_gateway`로 바꾸고 gateway subject/group 포트만 연결합니다.
- 승인 결과·인증 Context는 자동 연결됩니다. Component 36은 최초 업무 원문·추가 설계 프롬프트를 승인된 MongoDB WorkDefinition에서 다시 읽어 복원하므로, 답변 재개 실행에서도 초기 입력 노드에 의존하지 않습니다. 활성 catalog pointer·Skill Registry 컬렉션은 내부 고정입니다.
- Component 36의 MongoDB URI도 앞 저장 노드와 같은 공통 Secret `MONGO_URL`에 자동 연결되며, Database는 `business_work_design` 기본값을 사용합니다.
- F20은 Blueprint·검색 trace·승인 WorkDefinition을 sealed handoff로 만들고, F10 Gate가 무결성을 확인한 경우에만 F30을 직접 실행합니다.
- F30은 반응형 HTML을 생성합니다. 기본값은 **테스트 실행(저장하지 않음)**이며, 실제 게시에는 F30의 Report API 설정과 명시적 dry-run 해제가 필요합니다.
- 두 Run Flow 모두 HTTP API나 Agent Tool Call 없이 직접 실행합니다.""",
        (10600, -850),
        width=1560,
        height=320,
        background_color="amber",
    )

    # ① Intake / extract.  When round 1 actually needs a question, Component
    # 13 atomically/idempotently prepares the revision-0 WorkDefinition that
    # the native Playground answer card uses.  A no-question path
    # creates revision 0 later at the review save, avoiding an extra Canvas
    # storage node.
    flow.builtin(
        "team_name_text_input",
        "TextInput",
        (-1140, -180),
        {"input_value": example["team_name"], "use_global_variable": False},
    ).relabel("팀 명 입력", "이 업무를 수행하는 팀 이름입니다. 최초 실행과 질문 답변 재개에 같은 값을 사용합니다.")
    flow.builtin(
        "work_description_text_input",
        "TextInput",
        (-760, -180),
        {"input_value": example["request_text"], "use_global_variable": False},
    ).relabel("업무 설명 원문", "자동화하려는 업무를 가능한 한 구체적으로 입력합니다. 질문에 답할 때는 이 값을 다시 입력하지 않습니다.")
    flow.builtin(
        "employee_id_text_input",
        "TextInput",
        (-1140, 180),
        {"input_value": employee_id, "use_global_variable": False},
    ).relabel("사번 입력", "업무 정의의 작성자 식별자입니다. 추가 질문에 답할 때도 반드시 같은 사번을 유지합니다.")
    flow.builtin(
        "additional_design_prompt_text_input",
        "TextInput",
        (-760, 180),
        {"input_value": example["additional_prompt"], "use_global_variable": False},
    ).relabel("추가 설계 요청", "카탈로그 자산 활용, 승인 기준, 보고서에 포함할 내용을 보완합니다.")
    flow.builtin("playground_entry_input", "ChatInput", (-760, 520), {"should_store_message": False}).relabel(
        "Playground 시작·답변 입력",
        "새 업무는 비우거나 `새 업무 시작`을 보내고, 질문 답변은 `1번: 답변` 형식으로 같은 입력창에 보냅니다.",
    )
    flow.custom("playground_entry_router", "49_f10_playground_entry_router.py", (-380, 520))
    flow.custom(
        "request_envelope",
        "10_work_request_envelope.py",
        (0, 0),
        {"catalog_scope_id": "default"},
    )
    flow.prompt("extraction_prompt", (380, -260), _read_prompt("work_extraction.md"), ["request_envelope"])
    flow.builtin(
        "extraction_model",
        "LanguageModel",
        (760, -260),
        {"system_message": "Return exactly one JSON object. Do not execute tools.", "stream": False, "temperature": 0.0},
    )
    flow.custom("work_normalizer", "11_work_definition_normalizer.py", (1140, 0))
    flow.connect("playground_entry_input", "message", "playground_entry_router", "message")
    flow.connect("playground_entry_router", "new_work_path", "request_envelope", "start_trigger")
    flow.connect("team_name_text_input", "text", "request_envelope", "team_name")
    flow.connect("work_description_text_input", "text", "request_envelope", "request_text")
    flow.connect("employee_id_text_input", "text", "request_envelope", "employee_id")
    flow.connect("additional_design_prompt_text_input", "text", "request_envelope", "additional_prompt")
    flow.connect("request_envelope", "request_message", "extraction_prompt", "request_envelope")
    flow.connect("extraction_prompt", "prompt", "extraction_model", "input_value")
    flow.connect("extraction_model", "text_output", "work_normalizer", "candidate")
    flow.connect("request_envelope", "request_envelope", "work_normalizer", "request_envelope")

    # ② Three visible, bounded clarification rounds.  Component 42 creates a
    # native choice-only node_input pause.  Selecting "답변 입력하기" displays
    # a numbered template; a later Playground Chat Input goes through
    # Components 47 → 46 → 39 to load the authoritative batch, parse the
    # readable response, CAS-save it, and recheck completeness.  The commit's
    # review exit goes directly to the Joiner below; only its next-round exit
    # enters 48, so two grouped outputs never feed one scalar input.
    def add_round(
        round_number: int,
        *,
        work_source_key: str | None,
        work_source_output: str = "work_definition",
        x: int,
        max_questions: int = 3,
    ) -> dict[str, str]:
        suffix = f"r{round_number}"
        planner = f"clarification_planner_{suffix}"
        model = f"clarification_model_{suffix}"
        batch = f"clarification_batch_{suffix}"
        gate = f"answer_gate_{suffix}"
        commit = f"answer_commit_{suffix}"
        flow.custom(planner, "12_work_completeness_evaluator.py", (x, 0), {"round_number": round_number})
        flow.builtin(
            model,
            "LanguageModel",
            (x + 380, -280),
            {"system_message": f"Return one JSON object with at most {max_questions} questions.", "stream": False, "temperature": 0.0},
        )
        flow.custom(batch, "13_clarification_batch_builder.py", (x + 760, 0), {"round_number": round_number, "max_questions": max_questions})
        flow.custom(gate, "42_f10_clarification_answer_gate.py", (x + 1140, -10))
        flow.custom(commit, "39_f10_answer_commit.py", (x + 1520, 0))

        if work_source_key is not None:
            flow.connect(work_source_key, work_source_output, planner, "work_definition")
        flow.connect(planner, "clarification_prompt", model, "input_value")
        flow.connect(planner, "clarification_path", batch, "work_definition")
        flow.connect(planner, "clarification_path", batch, "completeness")
        flow.connect(model, "text_output", batch, "candidate_questions")
        flow.connect(batch, "waiting_path", gate, "clarification_batch")
        flow.connect(planner, "clarification_path", commit, "clarification_context")
        flow.connect(batch, "waiting_path", commit, "clarification_batch")
        flow.connect(gate, "branch_skip_additional_input", commit, "skip_trigger")
        flow.connect(gate, "branch_cancel", commit, "cancel_trigger")
        # The initial request node is deliberately excluded when a later
        # numbered Playground answer resumes F10.  Keep the actor on the
        # shared, visible employee-ID input so every clarification round can
        # still save/skip/cancel with the same durable owner identity.
        flow.connect("employee_id_text_input", "text", commit, "actor_id")
        return {"planner": planner, "batch": batch, "gate": gate, "commit": commit}

    round1 = add_round(
        1,
        work_source_key="work_normalizer",
        work_source_output="work_definition",
        x=1520,
    )
    round2 = add_round(
        2,
        work_source_key=None,
        x=3800,
    )
    round3 = add_round(
        3,
        work_source_key=None,
        x=5700,
        max_questions=4,
    )

    # The first run starts with the two Text Inputs above.  After an answer
    # card has paused, a person selects "답변 입력하기" and sends the numbered
    # reply through this ordinary Playground Chat Input.  Loader 47 resolves
    # only that person's pending Batch from MongoDB; Parser 46 then performs
    # exact question-number/type validation before the existing Component 39
    # applies the answer.  This keeps F10 compatible with the deployed
    # 1.11.0 Human Input contract without pretending that the card has fields.
    flow.custom(
        "chat_answer_resume_loader",
        "47_f10_chat_answer_resume_loader.py",
        (2080, 740),
        {"mongo_database": "business_work_design"},
    )
    flow.custom(
        "numbered_chat_answer_parser",
        "46_f10_numbered_chat_answer_parser.py",
        (2460, 740),
        {},
    )
    flow.custom(
        "chat_answer_commit",
        "39_f10_answer_commit.py",
        (2840, 740),
        {},
    )
    flow.custom("chat_answer_next_router", "48_f10_chat_answer_next_router.py", (3220, 740))
    flow.connect("playground_entry_router", "answer_path", "chat_answer_resume_loader", "answer_text")
    flow.connect("playground_entry_router", "answer_path", "numbered_chat_answer_parser", "answer_text")
    flow.connect("employee_id_text_input", "text", "chat_answer_resume_loader", "employee_id")
    flow.connect("employee_id_text_input", "text", "numbered_chat_answer_parser", "actor_id")
    flow.connect("employee_id_text_input", "text", "chat_answer_commit", "actor_id")
    flow.connect("chat_answer_resume_loader", "success_path", "numbered_chat_answer_parser", "clarification_batch")
    flow.connect("chat_answer_resume_loader", "success_path", "chat_answer_commit", "clarification_context")
    flow.connect("chat_answer_resume_loader", "success_path", "chat_answer_commit", "clarification_batch")
    flow.connect("numbered_chat_answer_parser", "answer_submission", "chat_answer_commit", "native_answer_submission")
    flow.connect("numbered_chat_answer_parser", "submit_trigger", "chat_answer_commit", "submit_trigger")
    flow.connect("chat_answer_commit", "next_round_path", "chat_answer_next_router", "answer_commit")
    flow.connect("chat_answer_next_router", "round2_path", round2["planner"], "work_definition")
    flow.connect("chat_answer_next_router", "round3_path", round3["planner"], "work_definition")

    # ③ Combine every mutually-exclusive review exit, validate the graph and
    # preview, then atomically create/update the durable approval request.
    flow.custom("review_entry_joiner", "40_f10_review_entry_joiner.py", (7600, 0))
    flow.custom("graph_normalizer", "16_work_graph_normalizer.py", (7980, 0))
    flow.custom("preview", "17_work_preview_hasher.py", (8360, 0))
    flow.custom(
        "review_approval_store",
        "18_work_definition_store.py",
        (8740, 0),
        {
            "command": "review_and_request_approval",
            "derive_expected_revision": True,
            "derive_idempotency_key": True,
            "require_transactions": True,
        },
    )
    flow.human("approval_gate", (9120, 0), ["Approve", "Reject", "Cancel"])
    flow.custom("final_approval_route_gate", "43_f10_final_approval_route_gate.py", (9500, 0))

    flow.connect(round1["planner"], "review_path", "review_entry_joiner", "initial_review")
    flow.connect(round1["batch"], "review_path", "review_entry_joiner", "round1_review")
    flow.connect(round2["planner"], "review_path", "review_entry_joiner", "round2_planner_review")
    flow.connect(round2["batch"], "review_path", "review_entry_joiner", "round2_review")
    flow.connect(round3["planner"], "review_path", "review_entry_joiner", "round3_planner_review")
    flow.connect(round3["batch"], "review_path", "review_entry_joiner", "round3_review")
    flow.connect(round1["commit"], "review_path", "review_entry_joiner", "round1_answer_review")
    flow.connect(round2["commit"], "review_path", "review_entry_joiner", "round2_answer_review")
    flow.connect(round3["commit"], "review_path", "review_entry_joiner", "round3_answer_review")
    flow.connect("chat_answer_commit", "review_path", "review_entry_joiner", "chat_answer_review")
    flow.connect("review_entry_joiner", "review_work_definition", "graph_normalizer", "work_definition")
    flow.connect("graph_normalizer", "success_path", "preview", "work_definition")
    flow.connect("preview", "success_path", "review_approval_store", "work_definition")
    flow.connect("review_approval_store", "stored_work_message", "approval_gate", "prompt")
    flow.connect("employee_id_text_input", "text", "review_approval_store", "actor_id")
    for command in ("approve", "reject", "cancel"):
        flow.connect("approval_gate", f"branch_{command}", "final_approval_route_gate", "approval_triggers")

    # ④ The native Human Input owns the visible final decision.  The small
    # standalone route gate directly after it persistently excludes the two
    # non-selected MongoDB stores in the same resumed run.  No branch other
    # than approve can reach the F20 Run Flow.
    final_store_specs = (
        ("approved_work_store", "approve", -420),
        ("rejected_work_store", "reject", 0),
        ("final_cancel_store", "cancel", 420),
    )
    for key, command, y in final_store_specs:
        flow.custom(
            key,
            "18_work_definition_store.py",
            (9880, y),
            {
                "command": command,
                "derive_expected_revision": True,
                "derive_idempotency_key": True,
                "require_transactions": True,
            },
        )
        flow.connect("review_approval_store", "success_path", key, "work_definition")
        flow.connect("final_approval_route_gate", f"branch_{command}", key, "route_trigger")
        flow.connect("employee_id_text_input", "text", key, "actor_id")

    # ⑤ Trust boundary and direct child Flow invocation.  F20 creates one
    # sealed report handoff; F10 verifies its envelope before F30 runs.
    # Neither child is invoked through a Flow HTTP endpoint or tool wrapper.
    flow.custom("authentication_context", "45_f10_authentication_context.py", (10260, 0))
    flow.custom("design_invocation_loader", "36_approved_design_invocation_loader.py", (10260, -430))
    flow.builtin("design_invocation_message", "TypeConverter", (10640, -430), {"auto_parse": False, "output_type": "Message"})
    flow.run_flow("run_agent_blueprint_design", (11020, -430), f20_flow)
    flow.custom("report_handoff_gate", "44_f10_report_handoff_gate.py", (11400, -430))
    flow.run_flow("run_responsive_report", (11780, -430), f30_flow)
    flow.builtin("final_approve_design_output", "ChatOutput", (12160, -430))
    invocation_input_field = f"{f20_flow['metadata']['design_invocation_input_node_id']}~input_value"
    report_handoff_output_name = f"{f20_flow['metadata']['report_handoff_output_node_id']}~message"
    report_input_field = f"{f30_flow['metadata']['report_handoff_input_node_id']}~input_value"
    report_output_name = f"{f30_flow['metadata']['report_output_node_id']}~message"
    flow.connect("approved_work_store", "success_path", "design_invocation_loader", "approval_result")
    # Component 36 always reloads the approved canonical record.  This makes
    # a post-HITL chat-answer run independent of Component 10, which is
    # intentionally excluded by the entry router on that path.
    flow.connect("employee_id_text_input", "text", "authentication_context", "local_demo_employee_actor_id")
    flow.connect("authentication_context", "success_path", "design_invocation_loader", "authentication_context")
    flow.connect("design_invocation_loader", "success_path", "design_invocation_message", "input_data")
    flow.connect("design_invocation_message", "message_output", "run_agent_blueprint_design", invocation_input_field)
    flow.connect("run_agent_blueprint_design", report_handoff_output_name, "report_handoff_gate", "f20_report_handoff")
    flow.connect("report_handoff_gate", "success_message", "run_responsive_report", report_input_field)
    flow.connect("run_responsive_report", report_output_name, "final_approve_design_output", "input_value")

    # One compact terminal presenter handles every intentional F10 terminal
    # block/cancel/reject outcome.  Every connected edge ends at a display-only
    # list input; no blocked payload is allowed to open an LLM, HITL, storage,
    # graph-normalization, or child-Flow branch.
    flow.custom("terminal_result_message", "41_f10_terminal_result_message.py", (10260, 670))
    flow.builtin("terminal_result_output", "ChatOutput", (10640, 670))
    for source_key, output_name in (
        (round1["planner"], "blocked_path"),
        (round2["planner"], "blocked_path"),
        (round3["planner"], "blocked_path"),
        (round1["batch"], "blocked_path"),
        (round2["batch"], "blocked_path"),
        (round3["batch"], "blocked_path"),
        (round1["commit"], "cancelled_path"),
        (round2["commit"], "cancelled_path"),
        (round3["commit"], "cancelled_path"),
        (round1["gate"], "blocked_path"),
        (round2["gate"], "blocked_path"),
        (round3["gate"], "blocked_path"),
        # A selected 1.11.0-compatible "답변 입력하기" action terminates this
        # run with a human-readable numbered template.  The next normal
        # Playground Chat Input runs the 47 → 46 → 39 resume path above.
        (round1["gate"], "branch_continue_chat"),
        (round2["gate"], "branch_continue_chat"),
        (round3["gate"], "branch_continue_chat"),
        (round1["commit"], "blocked_path"),
        (round2["commit"], "blocked_path"),
        (round3["commit"], "blocked_path"),
        ("chat_answer_resume_loader", "blocked_path"),
        ("numbered_chat_answer_parser", "blocked_path"),
        ("chat_answer_commit", "blocked_path"),
        ("chat_answer_next_router", "blocked_path"),
        ("playground_entry_router", "blocked_path"),
        ("review_entry_joiner", "blocked_path"),
        ("graph_normalizer", "blocked_path"),
        ("preview", "blocked_path"),
        ("review_approval_store", "blocked_path"),
        ("final_approval_route_gate", "blocked_path"),
        ("approved_work_store", "blocked_path"),
        ("rejected_work_store", "success_path"),
        ("rejected_work_store", "blocked_path"),
        ("final_cancel_store", "success_path"),
        ("final_cancel_store", "blocked_path"),
        ("authentication_context", "blocked_path"),
        ("design_invocation_loader", "blocked_path"),
        ("report_handoff_gate", "blocked_path"),
    ):
        flow.connect(source_key, output_name, "terminal_result_message", "terminal_events")
    flow.connect("terminal_result_message", "message", "terminal_result_output", "input_value")
    return flow.build()


def _add_search_pipeline(flow: FlowBuilder, *, y: float = 0, include_skill: bool) -> None:
    if include_skill:
        flow.custom("skill_context", "19_skill_context_resolver.py", (0, y - 420))
        planner_x = 0
    else:
        planner_x = 0
    flow.custom("query_plan", "20_search_query_planner.py", (planner_x, y))
    # The same provider/model must be selected here as for F00.  Component 29
    # derives and carries the concrete runtime contract; it does not expose a
    # second endpoint/model/version/dimension configuration surface.
    flow.builtin("search_embedding_model", "EmbeddingModel", (planner_x + 380, y - 380))
    flow.custom("query_embedding", "29_search_query_embedding_batcher.py", (planner_x + 380, y))
    flow.custom(
        "hybrid_retrieval",
        "21_catalog_hybrid_retriever.py",
        (planner_x + 760, y),
        {"provider_mode": "application_rrf"},
    )
    flow.custom("candidate_context", "22_candidate_context_builder.py", (planner_x + 1140, y))
    flow.connect("query_plan", "query_plan", "query_embedding", "query_plan")
    flow.connect("search_embedding_model", "embeddings", "query_embedding", "embedding")
    flow.connect("query_plan", "query_plan", "hybrid_retrieval", "query_plan")
    flow.connect("query_embedding", "query_vectors", "hybrid_retrieval", "query_vectors")
    flow.connect("hybrid_retrieval", "retrieval_result", "candidate_context", "retrieval_result")


def _build_f20() -> dict[str, Any]:
    flow = FlowBuilder("F20")
    flow.note(
        "01-trusted-invocation",
        """## ① 승인된 설계 요청·Skill 문맥

- F10 승인 경로의 trusted invocation만 입력으로 받습니다.
- TypeConverter가 strict JSON으로 파싱하고, ChatInput은 대화 이력에 저장하지 않습니다.
- Query Planner가 권한·활성 snapshot·설계 범위를 고정합니다.""",
        (-840, -1350),
        width=1250,
        height=360,
    )
    flow.note(
        "02-hybrid-search",
        """## ② 하이브리드 카탈로그 검색

- query plan에서 검색 문장을 만들고 같은 승인 Embedding Model로 벡터를 생성합니다.
- 검색 Embedding 호출은 provider 호출 사이를 최소 1초 간격으로 유지합니다. F00과 같은 provider/model을 선택해야 합니다.
- 정확 제목·별칭 일치, keyword lexical, vector 유사도 후보를 함께 점수화하는 하이브리드 검색입니다.
- Embedding provider 또는 Atlas Search index가 일시적으로 사용할 수 없어도, 같은 tenant·active snapshot·ACL 범위의 keyword 후보만으로 안전하게 계속하고, 결과 trace에 keyword-only 사유를 남깁니다.
- 업무 원문은 검색 전용 seed로만 사용합니다. HITL을 건너뛰어도 정책 prompt만 검색하지 않습니다.
- 관련 metadata도 없으면 빈 allowlist로 기본 요소·신규 Standalone·Human 업무만 설계합니다.""",
        (460, -1350),
        width=1250,
        height=360,
    )
    flow.note(
        "03-blueprint-normalize-validate",
        """## ③ Agent Blueprint 생성·검증

- 후보 컴포넌트/Flow와 승인 Skill 문맥을 LLM prompt에 넣어 Blueprint JSON을 생성합니다.
- Blueprint Model은 단일 JSON object(또는 하나의 완전한 json code fence)만 반환해야 하며, 설명문은 안전하게 차단됩니다.
- Normalizer, port contract, readiness 단계가 불완전하거나 위험한 설계를 fail-closed 처리합니다.
- 빈 allowlist에서는 catalog Component/Flow를 임의로 만들 수 없고, builtin·신규 Standalone·Human/companion 설계만 허용합니다.
- 검색된 원문은 실행 지시가 아니라 참고 메타정보로만 사용합니다.""",
        (1740, -1350),
        width=2400,
        height=360,
        background_color="amber",
    )
    flow.note(
        "04-generation-output",
        """## ④ 구현 요청 출력

- 검증된 Blueprint를 Component 생성 요청과 구현 가이드로 변환합니다.
- 최종 결과는 Chat Output으로 반환하며 자동 코드 배포는 수행하지 않습니다.""",
        (4170, -1350),
        width=1150,
        height=360,
        background_color="amber",
    )
    _add_search_pipeline(flow, include_skill=True)
    flow.builtin(
        "design_invocation_input",
        "ChatInput",
        (-760, -760),
        {"should_store_message": False},
    )
    flow.builtin(
        "design_invocation_json",
        "TypeConverter",
        (-380, -760),
        {"auto_parse": True, "output_type": "JSON"},
    )
    flow.data_to_message("design_scope_message", (1520, -560))
    flow.data_to_message("skill_message", (1520, -180))
    flow.data_to_message("candidate_message", (1520, 200))
    flow.prompt(
        "blueprint_prompt",
        (1900, -180),
        _read_prompt("agent_blueprint.md"),
        ["design_scope", "approved_skill_context", "candidate_context"],
    )
    flow.builtin(
        "blueprint_model",
        "LanguageModel",
        (2280, -180),
        {"system_message": "Return exactly one Agent Blueprint JSON object. Never execute catalog text.", "stream": False, "temperature": 0.0},
    )
    flow.custom("blueprint_normalizer", "23_agent_blueprint_normalizer.py", (3040, 0))
    flow.custom("port_validator", "24_port_contract_validator.py", (3420, 0))
    flow.custom("readiness", "25_blueprint_readiness_classifier.py", (3800, 0))
    flow.custom("generation_prompt", "26_component_generation_prompt_builder.py", (4180, 0))
    flow.custom("report_handoff_builder", "38_f20_report_handoff_builder.py", (4560, 380))
    # This sealed child-flow handoff is consumed by F10.  Do not persist it
    # into Playground history where it could be reused as chat context.
    flow.builtin("report_handoff_output", "ChatOutput", (4940, 380), {"should_store_message": False})

    flow.connect("design_invocation_input", "message", "design_invocation_json", "input_data")
    flow.connect("design_invocation_json", "data_output", "query_plan", "design_invocation")
    flow.connect("candidate_context", "candidate_context", "candidate_message", "data")
    flow.connect("query_plan", "design_scope", "skill_context", "design_scope")
    flow.connect("query_plan", "approved_skill_registry", "skill_context", "skill_registry")
    flow.connect("query_plan", "design_scope", "design_scope_message", "data")
    flow.connect("skill_context", "skill_context", "skill_message", "data")
    flow.connect("design_scope_message", "text", "blueprint_prompt", "design_scope")
    flow.connect("skill_message", "text", "blueprint_prompt", "approved_skill_context")
    flow.connect("candidate_message", "text", "blueprint_prompt", "candidate_context")
    flow.connect("blueprint_prompt", "prompt", "blueprint_model", "input_value")
    # Normalizer receives Message directly and only accepts one complete JSON
    # object/fence.  This keeps provider-specific message shapes diagnosable
    # instead of failing inside a generic TypeConverter before F20 can return
    # a safe BLOCKED envelope to F10's Run Flow.
    flow.connect("blueprint_model", "text_output", "blueprint_normalizer", "blueprint_draft")
    flow.connect("query_plan", "design_scope", "blueprint_normalizer", "design_scope")
    flow.connect("candidate_context", "candidate_context", "blueprint_normalizer", "candidate_context")
    flow.connect("skill_context", "skill_context", "blueprint_normalizer", "applied_skill_context")
    flow.connect("blueprint_normalizer", "normalized_blueprint", "port_validator", "normalized_blueprint")
    flow.connect("port_validator", "validated_blueprint", "readiness", "validated_blueprint")
    flow.connect("readiness", "classified_blueprint", "generation_prompt", "classified_blueprint")
    flow.connect("query_plan", "design_scope", "report_handoff_builder", "design_scope")
    flow.connect("candidate_context", "candidate_context", "report_handoff_builder", "candidate_context")
    flow.connect("generation_prompt", "generation_request", "report_handoff_builder", "terminal_blueprint")
    flow.connect("report_handoff_builder", "report_handoff_message", "report_handoff_output", "input_value")

    result = flow.build()
    result["metadata"]["design_input_contract"] = {
        "schema_version": "agent-design-invocation/v1",
        "single_input_node_id": flow.nodes["design_invocation_input"].node_id,
        "single_input_field": "input_value",
        "downstream_scope_source": "query_plan.design_scope",
        "independent_downstream_scope_tweaks": False,
    }
    result["metadata"]["design_invocation_input_node_id"] = flow.nodes["design_invocation_input"].node_id
    result["metadata"]["report_handoff_output_node_id"] = flow.nodes["report_handoff_output"].node_id
    result["metadata"]["report_handoff_contract"] = {
        "schema_version": "f20-report-handoff/v1",
        "work_definition_source": "query_plan.design_scope",
        "retrieval_trace_source": "candidate_context.retrieval_trace",
        "blueprint_source": "generation_prompt.generation_request",
    }
    result["metadata"]["blueprint_model_output_contract"] = {
        "accepted": ["one JSON object", "one complete json code fence"],
        "rejected": ["prose", "multiple JSON blocks", "partial JSON"],
        "failure_code": "INVALID_BLUEPRINT_DRAFT",
    }
    return result


def _build_f30() -> dict[str, Any]:
    flow = FlowBuilder("F30")
    flow.note(
        "01-view-render-publish",
        """## ① F20 handoff → View Model → 반응형 HTML → 게시

- F10/F20의 sealed handoff만 Chat Input으로 받고, WorkDefinition·Blueprint·retrieval trace의 같은 승인 범위를 재검증합니다.
- 업무 정의와 Agent Blueprint를 보고서용 view model로 변환합니다.
- 노드·연결선·상세 업무 방식이 있는 반응형 HTML을 렌더링합니다.
- Publisher는 검증된 HTML을 공유 HTML Report API의 `/reports` endpoint에 POST합니다.
- 기본은 **테스트 실행**입니다. 이때 API 서버로 요청하지 않고 HTML·URL·TTL만 확인합니다.
- Playground에는 raw JSON 대신 보고서 열기·HTML 다운로드 링크와 다음 조치를 담은 읽기 쉬운 안내가 표시됩니다.
- sealed handoff·view model·renderer·게시 실패도 Flow 전체 오류가 아니라 하나의 안내 메시지로 표시됩니다.""",
        (-760, -700),
        width=2500,
        height=350,
        background_color="amber",
    )
    flow.builtin("report_handoff_input", "ChatInput", (-760, 0), {"should_store_message": False})
    flow.builtin("report_handoff_json", "TypeConverter", (-380, 0), {"auto_parse": True, "output_type": "JSON"})
    flow.custom(
        "report_handoff_loader",
        "33_f30_report_handoff_loader.py",
        (0, 0),
        {"safe_failure_envelope": True},
    )
    flow.custom("view_model", "30_report_view_model_builder.py", (420, 0), {"safe_failure_envelope": True})
    flow.custom("renderer", "31_responsive_report_renderer.py", (800, 0), {"safe_failure_envelope": True})
    flow.custom(
        "publisher",
        "32_report_publisher.py",
        (1180, 0),
        {"report_api_url": "http://127.0.0.1:5000", "report_ttl_hours": 4, "dry_run": True},
    )
    flow.custom("publication_message", "37_report_publication_message.py", (1560, 0))
    flow.builtin("report_output", "ChatOutput", (1940, 0), {"should_store_message": False})
    flow.connect("report_handoff_input", "message", "report_handoff_json", "input_data")
    flow.connect("report_handoff_json", "data_output", "report_handoff_loader", "report_handoff")
    flow.connect("report_handoff_loader", "work_definition", "view_model", "work_definition")
    flow.connect("report_handoff_loader", "agent_blueprint", "view_model", "agent_blueprint")
    flow.connect("report_handoff_loader", "retrieval_trace", "view_model", "retrieval_trace")
    flow.connect("view_model", "report_view_model", "renderer", "report_view_model")
    flow.connect("renderer", "render_result", "publisher", "render_result")
    flow.connect("publisher", "publish_result", "publication_message", "publish_result")
    flow.connect("publication_message", "message", "report_output", "input_value")
    result = flow.build()
    result["metadata"]["report_input_contract"] = {
        "schema_version": "f20-report-handoff/v1",
        "single_input_node_id": flow.nodes["report_handoff_input"].node_id,
        "single_input_field": "input_value",
        "should_store_message": False,
    }
    result["metadata"]["report_handoff_input_node_id"] = flow.nodes["report_handoff_input"].node_id
    result["metadata"]["report_output_node_id"] = flow.nodes["report_output"].node_id
    result["metadata"]["report_api_publish_contract"] = {
        "request_url": "Report API Base URL + /reports (or supplied /reports endpoint)",
        "request_body": ["html", "title", "question", "view_request", "available_datasets", "report_plan", "ttl_hours", "filename_hint"],
        "success_response": ["view_url", "download_url"],
        "test_run_default": True,
        "failure_output": "PUBLISH_FAILED or F30 BLOCKED envelope on publisher.publish_result",
    }
    return result


def _build_f90() -> dict[str, Any]:
    flow = FlowBuilder("F90")
    flow.note(
        "01-query-plan-embedding",
        """## ① 평가용 설계 요청·검색 계획·쿼리 임베딩

- Playground에는 **Component 36의 Verified Design Invocation 전체 JSON**을 붙여 넣습니다. 원문 업무 설명만 넣는 Flow가 아닙니다.
- Query Planner가 승인 업무·ACL·active snapshot lock을 다시 검증한 뒤 평가용 검색 조건을 고정합니다.
- 승인 Embedding Model과 Batcher가 검색 문장별 vector 및 runtime 계약을 만들고 provider 호출 사이를 최소 1초 간격으로 유지합니다.
- 이 Flow는 검색 품질 점검 전용이며 F10 승인 또는 F20 설계 실행을 대체하지 않습니다.""",
        (-840, -1050),
        width=1900,
        height=350,
    )
    flow.note(
        "02-retrieve-candidate-output",
        """## ② 검색·후보 문맥 출력

- Hybrid Retriever가 lexical/vector 후보를 결합하고 active snapshot을 검증합니다.
- Candidate Context를 Chat Output으로 반환해 검색 품질과 선택 후보를 점검합니다.""",
        (1050, -1050),
        width=1350,
        height=350,
        background_color="amber",
    )
    _add_search_pipeline(flow, include_skill=False)
    flow.builtin("evaluation_invocation_input", "ChatInput", (-760, 0), {"should_store_message": False})
    flow.builtin(
        "evaluation_invocation_json",
        "TypeConverter",
        (-380, 0),
        {"auto_parse": True, "output_type": "JSON"},
    )
    flow.data_to_message("evaluation_message", (1520, 0))
    flow.builtin("evaluation_output", "ChatOutput", (1900, 0), {"should_store_message": False})
    flow.connect("evaluation_invocation_input", "message", "evaluation_invocation_json", "input_data")
    flow.connect("evaluation_invocation_json", "data_output", "query_plan", "design_invocation")
    flow.connect("candidate_context", "candidate_context", "evaluation_message", "data")
    flow.connect("evaluation_message", "text", "evaluation_output", "input_value")
    result = flow.build()
    result["metadata"]["evaluation_input_contract"] = {
        "schema_version": "agent-design-invocation/v1",
        "single_input_node_id": flow.nodes["evaluation_invocation_input"].node_id,
        "single_input_field": "input_value",
        "should_store_message": False,
        "accepts": "Verified Design Invocation JSON from Component 36",
        "evaluation_only": True,
    }
    result["metadata"]["evaluation_input_node_id"] = flow.nodes["evaluation_invocation_input"].node_id
    result["metadata"]["evaluation_output_node_id"] = flow.nodes["evaluation_output"].node_id
    return result


def _validate_flow_contract(flow: dict[str, Any], flow_key: str) -> None:
    data = flow.get("data")
    if not isinstance(data, dict):
        raise ValueError(f"{flow_key}: missing data object")
    nodes = data.get("nodes")
    edges = data.get("edges")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError(f"{flow_key}: flow must contain nodes")
    if not isinstance(edges, list):
        raise ValueError(f"{flow_key}: flow edges must be a list")
    node_by_id = {node.get("id"): node for node in nodes}
    if len(node_by_id) != len(nodes) or None in node_by_id:
        raise ValueError(f"{flow_key}: duplicate or missing node id")
    note_nodes = [
        wrapper
        for wrapper in nodes
        if wrapper.get("type") == "noteNode" and wrapper.get("data", {}).get("type") == "note"
    ]
    note_ids = {wrapper["id"] for wrapper in note_nodes}
    for wrapper in note_nodes:
        data_node = wrapper.get("data", {}).get("node")
        if not isinstance(data_node, dict):
            raise ValueError(f"{flow_key}: Sticky Note {wrapper['id']} is missing its note data")
        if not isinstance(data_node.get("description"), str) or not data_node["description"].strip():
            raise ValueError(f"{flow_key}: Sticky Note {wrapper['id']} has no description")
        if data_node.get("lf_version") != LANGFLOW_VERSION:
            raise ValueError(f"{flow_key}: Sticky Note {wrapper['id']} has an incompatible Langflow version")
        template = data_node.get("template")
        if not isinstance(template, dict) or template.get("backgroundColor") not in {"blue", "amber"}:
            raise ValueError(f"{flow_key}: Sticky Note {wrapper['id']} has an invalid background color")
        if wrapper.get("data", {}).get("id") != wrapper["id"]:
            raise ValueError(f"{flow_key}: Sticky Note {wrapper['id']} has inconsistent data id")
        if wrapper.get("positionAbsolute") != wrapper.get("position"):
            raise ValueError(f"{flow_key}: Sticky Note {wrapper['id']} must use an absolute Canvas position")
        if not isinstance(wrapper.get("width"), (int, float)) or not isinstance(wrapper.get("height"), (int, float)):
            raise ValueError(f"{flow_key}: Sticky Note {wrapper['id']} must have numeric dimensions")
    for edge in edges:
        if edge.get("source") not in node_by_id or edge.get("target") not in node_by_id:
            raise ValueError(f"{flow_key}: dangling edge {edge.get('id')}")
        if edge.get("source") in note_ids or edge.get("target") in note_ids:
            raise ValueError(f"{flow_key}: Sticky Note {edge.get('source')} or {edge.get('target')} must not have an edge")
        source_handle = edge.get("data", {}).get("sourceHandle", {})
        target_handle = edge.get("data", {}).get("targetHandle", {})
        source_node = node_by_id[edge["source"]]["data"]["node"]
        target_node = node_by_id[edge["target"]]["data"]["node"]
        source_outputs = {item.get("name"): item for item in source_node.get("outputs", [])}
        target_fields = target_node.get("template", {})
        output = source_outputs.get(source_handle.get("name"))
        target = target_fields.get(target_handle.get("fieldName"))
        if not isinstance(output, dict) or not isinstance(target, dict):
            raise ValueError(f"{flow_key}: edge handle does not resolve for {edge.get('id')}")
        # Langflow 1.11.1's Canvas loader prunes a connection when its target
        # input is marked advanced.  Fail at generation time instead of
        # exporting a Flow that silently loses a runtime dependency on import.
        if target.get("advanced") is True:
            raise ValueError(
                f"{flow_key}: edge {edge.get('id')} targets advanced input "
                f"{target_handle.get('fieldName')!r}; Langflow 1.11.1 will remove it on import"
            )
        if not set(output.get("types") or []).intersection(target.get("input_types") or []):
            raise TypeError(f"{flow_key}: edge types are incompatible for {edge.get('id')}")
    custom_nodes = []
    hitl_nodes = []
    for wrapper in nodes:
        node = wrapper["data"]["node"]
        if wrapper["data"].get("type") == "HumanInput":
            hitl_nodes.append(wrapper)
        metadata = node.get("metadata", {})
        if metadata.get("standalone"):
            custom_nodes.append(wrapper)
            source_path = metadata.get("standalone_source_path")
            expected_hash = metadata.get("standalone_source_sha256")
            source = (PROJECT_ROOT / source_path).read_bytes().decode("utf-8")
            embedded = node.get("template", {}).get("code", {}).get("value")
            if embedded != source or expected_hash != _sha256_text(source):
                raise ValueError(f"{flow_key}: embedded source mismatch for {source_path}")
    if not custom_nodes:
        raise ValueError(f"{flow_key}: expected at least one custom component")
    if flow_key in {"F20", "F30", "F90"} and hitl_nodes:
        raise ValueError(f"{flow_key}: child/evaluation Flow must not contain HumanInput")
    if flow_key == "F10" and not hitl_nodes:
        raise ValueError(f"{flow_key}: top-level HITL Flow requires a HumanInput gate")
    if flow_key == "F20" and flow.get("metadata", {}).get("operational_readiness") == "import_ready":
        raise ValueError("F20 must remain fail-closed until external contracts are configured and validated")


def build_all() -> dict[str, dict[str, Any]]:
    installed_langflow, installed_lfx = _require_supported_runtime()
    if (installed_langflow, installed_lfx) != (LANGFLOW_VERSION, LFX_VERSION):
        raise RuntimeError("Langflow runtime changed while this generator was running")
    f20_flow = _build_f20()
    f30_flow = _build_f30()
    return {
        "F00": _build_f00(),
        "F10": _build_f10(f20_flow, f30_flow),
        "F20": f20_flow,
        "F30": f30_flow,
        "F90": _build_f90(),
    }


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def write_outputs(flows: dict[str, dict[str, Any]], output_dir: Path = FLOW_ROOT) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[dict[str, Any]] = []
    for flow_key, flow in flows.items():
        filename = FLOW_FILES[flow_key]
        payload = _json_bytes(flow)
        (output_dir / filename).write_bytes(payload)
        written.append(
            {
                "flow_key": flow_key,
                "filename": filename,
                "flow_id": flow["id"],
                "sha256": _sha256_bytes(payload),
                "node_count": len(flow["data"]["nodes"]),
                "edge_count": len(flow["data"]["edges"]),
                "operational_readiness": flow["metadata"]["operational_readiness"],
            }
        )
    bundle = {
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "langflow_version": LANGFLOW_VERSION,
        "lfx_version": LFX_VERSION,
        "generated_by": "scripts/build_langflow_1_11_flows.py",
        "flows": [flows[key] for key in FLOW_FILES],
    }
    bundle_payload = _json_bytes(bundle)
    (output_dir / BUNDLE_FILE).write_bytes(bundle_payload)
    manifest = {
        "schema_version": "business-work-design-flow-build-manifest/v1",
        "langflow_version": LANGFLOW_VERSION,
        "lfx_version": LFX_VERSION,
        "python_version": ".".join(str(part) for part in sys.version_info[:3]),
        "generator_sha256": _sha256_bytes(Path(__file__).read_bytes()),
        "bundle": {"filename": BUNDLE_FILE, "sha256": _sha256_bytes(bundle_payload)},
        "flows": written,
    }
    (output_dir / MANIFEST_FILE).write_bytes(_json_bytes(manifest))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=FLOW_ROOT)
    parser.add_argument("--check", action="store_true", help="Build in memory and verify committed outputs are byte-identical")
    args = parser.parse_args()
    flows = build_all()
    if args.check:
        expected = {FLOW_FILES[key]: _json_bytes(value) for key, value in flows.items()}
        expected[BUNDLE_FILE] = _json_bytes(
            {
                "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
                "langflow_version": LANGFLOW_VERSION,
                "lfx_version": LFX_VERSION,
                "generated_by": "scripts/build_langflow_1_11_flows.py",
                "flows": [flows[key] for key in FLOW_FILES],
            }
        )
        mismatches = [name for name, payload in expected.items() if not (args.output_dir / name).is_file() or (args.output_dir / name).read_bytes() != payload]
        if mismatches:
            raise SystemExit(f"Generated Flow outputs are stale or missing: {', '.join(mismatches)}")
        print(f"Verified {len(flows)} Flow JSON files and bundle against Langflow {LANGFLOW_VERSION} sources.")
        return 0
    manifest = write_outputs(flows, args.output_dir)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
