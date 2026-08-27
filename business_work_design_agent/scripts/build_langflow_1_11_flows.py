from __future__ import annotations

"""Build deterministic Langflow 1.11.1 flow exports from standalone sources.

Run this script with the pinned Langflow 1.11.1 environment.  Every custom
component node is introspected by Langflow itself, and the exact source bytes
read from ``components/`` are embedded in the resulting Flow JSON.
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


LANGFLOW_VERSION = "1.11.1"
LFX_VERSION = "1.11.5"
BUNDLE_SCHEMA_VERSION = "business-work-design-flow-bundle/v1"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_ROOT = PROJECT_ROOT / "components"
PROMPT_ROOT = PROJECT_ROOT / "prompts"
FLOW_ROOT = PROJECT_ROOT / "flows"

FLOW_FILES = {
    "F00": "F00_catalog_ingestion_admin.json",
    "F10": "F10_work_definition_parent.json",
    "F11": "F11_work_definition_chat_turn.json",
    "F20": "F20_agent_blueprint_design.json",
    "F30": "F30_responsive_report.json",
    "F90": "F90_search_evaluation.json",
}
BUNDLE_FILE = "00_business_work_design_ALL_FLOWS.json"
MANIFEST_FILE = "build_manifest.json"

FLOW_NAMES = {
    "F00": "F00_catalog_ingestion_admin",
    "F10": "F10_work_definition_parent",
    "F11": "F11_work_definition_chat_turn",
    "F20": "F20_agent_blueprint_design",
    "F30": "F30_responsive_report",
    "F90": "F90_search_evaluation",
}

FLOW_DESCRIPTIONS = {
    "F00": "관리자 전용 catalog ingest를 bounded worker로 실행하고 검증·HITL 결정을 trusted gateway activation handoff로 반환하는 Flow.",
    "F10": "자연어 업무 추출, 최대 3문항 HITL 보완, graph preview와 최종 승인 저장을 수행하는 top-level Flow.",
    "F11": "Human Input 없이 structured command와 외부 existing-state/batch 계약으로 시작·답변·승인 action을 분리하는 Playground Flow.",
    "F20": "trusted backend가 봉인한 승인 업무·ACL·Skill scope와 hybrid catalog 근거로 Agent Blueprint를 설계하는 backend-only Flow.",
    "F30": "승인된 업무 정의와 Agent Blueprint를 반응형 HTML report로 만들고 저장 API에 발행하는 child-safe Flow.",
    "F90": "고정 평가 입력으로 query 계획, embedding, hybrid retrieval과 bounded context를 점검하는 검색 QA Flow.",
}

FLOW_READINESS = {
    "F00": "trusted_admin_gateway_required",
    "F10": "configuration_required",
    "F11": "structured_command_external_state_required",
    "F20": "trusted_backend_only_configuration_required",
    "F30": "configuration_required",
    "F90": "evaluation_configuration_required",
}

FLOW_REQUIRED_CONFIG = {
    "F00": [
        "catalog file",
        "allowed upload root",
        "tenant/uploader identifiers",
        "MongoDB URI",
        "approved embedding endpoint/model/version/dimension",
        "Catalog Worker URL/allowlist/bearer token",
        "trusted admin gateway activation attestation signer",
    ],
    "F10": [
        "tenant/owner/session",
        "approved extraction and clarification language models",
        "MongoDB URI",
        "trusted HITL answer form backend",
    ],
    "F11": [
        "canonical JSON command: start, submit_answers, approve, reject, or cancel",
        "existing WorkDefinition and clarification batch node tweaks for follow-up turns",
        "approved extraction and clarification language models",
        "MongoDB URI",
        "one-time action token for approval commands",
    ],
    "F20": [
        "trusted backend scope assembler; do not expose raw node tweaks to end users",
        "approved WorkDefinition",
        "ACL context",
        "approved immutable Skill registry",
        "tenant and active catalog snapshot",
        "embedding endpoint/model/version/dimension",
        "MongoDB URI and search indexes",
        "approved blueprint language model",
        "target new-custom node id",
    ],
    "F30": [
        "approved WorkDefinition",
        "validated Agent Blueprint",
        "Report API URL/tenant/actor",
        "bearer token for non-loopback publication",
        "report view signing secret and short capability TTL",
    ],
    "F90": [
        "evaluation WorkDefinition/ACL",
        "tenant and active catalog snapshot",
        "embedding endpoint/model/version/dimension",
        "MongoDB URI and search indexes",
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
    "ChatOutput": ("lfx.components.input_output.chat_output", "ChatOutput"),
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
    "pathlib", "pymongo", "re", "requests", "socket", "typing", "unicodedata",
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


class FlowBuilder:
    def __init__(self, flow_key: str) -> None:
        self.flow_key = flow_key
        self.nodes: dict[str, NodeRef] = {}
        self.edges: list[dict[str, Any]] = []

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
                "nodes": [item.wrapper for item in self.nodes.values()],
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
            "tags": ["business-work-design", "langflow-1.11.1", "standalone-custom-components"],
            "webhook": False,
            "metadata": {
                "flow_contract": f"business-work-design/{self.flow_key.lower()}/v1",
                "generated_by": "scripts/build_langflow_1_11_flows.py",
                "langflow_version": LANGFLOW_VERSION,
                "operational_readiness": FLOW_READINESS[self.flow_key],
                "required_configuration": FLOW_REQUIRED_CONFIG[self.flow_key],
                "contains_native_hitl": any(node.type_name == "HumanInput" for node in self.nodes.values()),
                "custom_sources_embedded": True,
            },
        }
        _validate_flow_contract(result, self.flow_key)
        return result


def _read_prompt(filename: str) -> str:
    return (PROMPT_ROOT / filename).read_text(encoding="utf-8")


def _build_f00() -> dict[str, Any]:
    flow = FlowBuilder("F00")
    flow.custom("intake", "00_catalog_file_intake.py", (0, 0), {"dry_run": False})
    flow.custom("secret_scan", "01_catalog_secret_scanner.py", (380, 0))
    flow.custom(
        "pipeline_worker",
        "09_catalog_pipeline_worker_client.py",
        (760, 0),
        {"worker_server_url": "http://127.0.0.1:8092/api", "max_stage_invocations": 400},
    )
    flow.data_to_message("validation_message", (1140, -260))
    flow.human("activation_gate", (1520, -260), ["Activate Snapshot", "Reject"])
    flow.prompt(
        "activation_handoff",
        (1900, -260),
        (
            "CATALOG_ACTIVATION_GATEWAY_HANDOFF\n"
            "The snapshot remains inactive. A trusted admin gateway must verify this completed "
            "Langflow job/request/decision, issue a short-lived catalog-activation-attestation/v1 "
            "claim, and call the Catalog Worker /activate endpoint."
        ),
        ["validation_report", "approval_decision"],
    )
    flow.builtin("activation_handoff_output", "ChatOutput", (2280, -520))
    flow.data_to_message("worker_blocked_message", (1140, 420))
    flow.builtin("worker_blocked_output", "ChatOutput", (1520, 420))
    flow.builtin("activation_rejected_output", "ChatOutput", (1900, 520))

    flow.connect("intake", "job_ref", "secret_scan", "job_ref")
    flow.connect("secret_scan", "scanned_job_ref", "pipeline_worker", "scanned_job_ref")
    flow.connect("pipeline_worker", "activation_path", "validation_message", "data")
    flow.connect("validation_message", "text", "activation_gate", "prompt")
    flow.connect("validation_message", "text", "activation_handoff", "validation_report")
    flow.connect("activation_gate", "branch_activate_snapshot", "activation_handoff", "approval_decision")
    flow.connect("activation_handoff", "prompt", "activation_handoff_output", "input_value")
    flow.connect("pipeline_worker", "blocked_path", "worker_blocked_message", "data")
    flow.connect("worker_blocked_message", "text", "worker_blocked_output", "input_value")
    flow.connect("activation_gate", "branch_reject", "activation_rejected_output", "input_value")
    result = flow.build()
    result["metadata"]["activation_handoff_contract"] = {
        "schema_version": "catalog-activation-gateway-handoff/v1",
        "flow_performs_activation": False,
        "required_attestation": "catalog-activation-attestation/v1",
        "attestation_issuer": "trusted_admin_gateway",
        "raw_nonce_in_langflow": False,
        "activation_client_component": "33_catalog_activation_approval_client.py",
    }
    return result


def _add_work_extraction_nodes(
    flow: FlowBuilder,
    *,
    chat_input: bool,
    durable_initial_store: bool = False,
    y: float = 0,
) -> tuple[str, str]:
    if chat_input:
        flow.builtin("chat_input", "ChatInput", (0, y))
        request_x = 380
    else:
        request_x = 0
    flow.custom(
        "request_envelope",
        "10_work_request_envelope.py",
        (request_x, y),
        {"channel_mode": "playground" if chat_input else "native_hitl"},
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

    if chat_input:
        flow.connect("chat_input", "message", "request_envelope", "request_text")
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


def _build_f10() -> dict[str, Any]:
    flow = FlowBuilder("F10")
    work_source_key, work_source_output = _add_work_extraction_nodes(
        flow,
        chat_input=False,
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
            {"channel_mode": "native_hitl", "answer_source_mode": "mongodb"},
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
    return flow.build()


def _build_f11() -> dict[str, Any]:
    flow = FlowBuilder("F11")

    def add_result_gate(
        prefix: str,
        source_key: str,
        source_output: str,
        *,
        required_field: str,
        x: int,
        y: int,
    ) -> tuple[str, str]:
        gate_key = f"{prefix}_result_gate"
        message_key = f"{prefix}_blocked_message"
        output_key = f"{prefix}_blocked_output"
        flow.custom(gate_key, "35_result_gate.py", (x, y), {"required_field": required_field})
        flow.data_to_message(message_key, (x + 380, y + 260))
        flow.builtin(output_key, "ChatOutput", (x + 760, y + 260))
        flow.connect(source_key, source_output, gate_key, "result")
        flow.connect(gate_key, "blocked_path", message_key, "data")
        flow.connect(message_key, "text", output_key, "input_value")
        return gate_key, "success_path"

    flow.builtin("chat_input", "ChatInput", (-1900, 0))
    flow.custom("command_router", "36_playground_command_router.py", (-1520, 0))
    flow.connect("chat_input", "message", "command_router", "input_text")

    # Start lane. It is reached only by command=start and persists the initial
    # definition before any question batch can be returned.
    start_work_key, start_work_output = _add_work_extraction_nodes(
        flow,
        chat_input=False,
        durable_initial_store=True,
        y=-900,
    )
    _set_value(flow.nodes["request_envelope"].node, "channel_mode", "playground")
    flow.connect("command_router", "start_path", "request_envelope", "request_text")
    flow.connect("command_router", "start_path", "request_envelope", "additional_prompt")
    flow.custom("start_clarification_router", "27_work_clarification_router.py", (3800, -900))
    flow.connect(start_work_key, start_work_output, "start_clarification_router", "work_definition")
    flow.connect("clarification_batch", "clarification_batch", "start_clarification_router", "clarification_result")
    flow.data_to_message("start_question_message", (4180, -1220))
    flow.builtin("start_question_output", "ChatOutput", (4560, -1220))
    flow.connect("start_clarification_router", "clarification_path", "start_question_message", "data")
    flow.connect("start_question_message", "text", "start_question_output", "input_value")
    flow.data_to_message("start_router_blocked_message", (4180, -620))
    flow.builtin("start_router_blocked_output", "ChatOutput", (4560, -620))
    flow.connect("start_clarification_router", "blocked_path", "start_router_blocked_message", "data")
    flow.connect("start_router_blocked_message", "text", "start_router_blocked_output", "input_value")

    # External state hubs are intentionally unconnected inputs. Follow-up API
    # callers must provide the same persisted WorkDefinition and pending batch
    # by node tweak; the Flow never fabricates or silently falls back to state.
    flow.builtin(
        "existing_work_input",
        "TypeConverter",
        (-760, 900),
        {"input_data": {}, "auto_parse": True, "output_type": "JSON"},
    )
    flow.builtin(
        "existing_batch_input",
        "TypeConverter",
        (-380, 1200),
        {"input_data": {}, "auto_parse": True, "output_type": "JSON"},
    )
    flow.custom(
        "answer_loader",
        "14_work_answer_loader.py",
        (0, 900),
        {"channel_mode": "playground", "answer_source_mode": "direct_payload"},
    )
    flow.custom("answer_merger", "15_work_answer_merger.py", (380, 900))
    flow.custom(
        "answered_work_store",
        "18_work_definition_store.py",
        (760, 900),
        {
            "command": "save",
            "derive_expected_revision": True,
            "incoming_revision_is_next": True,
            "derive_idempotency_key": True,
            "require_transactions": True,
        },
    )
    answer_loader_gate_key, answer_loader_gate_output = add_result_gate(
        "answer_loader",
        "answer_loader",
        "answer_submission",
        required_field="answer_submission",
        x=190,
        y=1220,
    )
    answer_merger_gate_key, answer_merger_gate_output = add_result_gate(
        "answer_merger",
        "answer_merger",
        "merged_work_definition",
        required_field="work_definition",
        x=570,
        y=1220,
    )
    answered_store_gate_key, answered_store_gate_output = add_result_gate(
        "answered_store",
        "answered_work_store",
        "stored_work_definition",
        required_field="work_definition",
        x=950,
        y=1220,
    )
    flow.connect("existing_work_input", "data_output", "answer_loader", "work_definition")
    flow.connect("existing_batch_input", "data_output", "answer_loader", "clarification_batch")
    flow.connect("command_router", "submit_answers_path", "answer_loader", "playground_payload")
    flow.connect("existing_work_input", "data_output", "answer_merger", "work_definition")
    flow.connect(answer_loader_gate_key, answer_loader_gate_output, "answer_merger", "answer_submission")
    flow.connect(answer_merger_gate_key, answer_merger_gate_output, "answered_work_store", "work_definition")

    flow.custom("answer_completeness", "12_work_completeness_evaluator.py", (1140, 900))
    flow.data_to_message("answer_work_message", (1140, 500))
    flow.data_to_message("answer_completeness_message", (1140, 1300))
    flow.prompt(
        "answer_clarification_prompt",
        (1520, 700),
        _read_prompt("clarification_planner.md"),
        ["work_definition", "completeness"],
    )
    flow.builtin(
        "answer_clarification_model",
        "LanguageModel",
        (1900, 700),
        {"system_message": "Return one JSON object with at most three questions.", "stream": False, "temperature": 0.0},
    )
    flow.custom(
        "answer_clarification_batch",
        "13_clarification_batch_builder.py",
        (2280, 900),
        {"round_number": 0},
    )
    flow.custom("answer_clarification_router", "27_work_clarification_router.py", (2660, 900))
    flow.connect(answered_store_gate_key, answered_store_gate_output, "answer_completeness", "work_definition")
    flow.connect(answered_store_gate_key, answered_store_gate_output, "answer_work_message", "data")
    flow.connect("answer_completeness", "completeness", "answer_completeness_message", "data")
    flow.connect("answer_work_message", "text", "answer_clarification_prompt", "work_definition")
    flow.connect("answer_completeness_message", "text", "answer_clarification_prompt", "completeness")
    flow.connect("answer_clarification_prompt", "prompt", "answer_clarification_model", "input_value")
    flow.connect(answered_store_gate_key, answered_store_gate_output, "answer_clarification_batch", "work_definition")
    flow.connect("answer_completeness", "completeness", "answer_clarification_batch", "completeness")
    flow.connect("answer_clarification_model", "text_output", "answer_clarification_batch", "candidate_questions")
    flow.connect(answered_store_gate_key, answered_store_gate_output, "answer_clarification_router", "work_definition")
    flow.connect("answer_clarification_batch", "clarification_batch", "answer_clarification_router", "clarification_result")
    flow.data_to_message("answer_question_message", (3040, 620))
    flow.builtin("answer_question_output", "ChatOutput", (3420, 620))
    flow.connect("answer_clarification_router", "clarification_path", "answer_question_message", "data")
    flow.connect("answer_question_message", "text", "answer_question_output", "input_value")
    flow.data_to_message("answer_router_blocked_message", (3040, 1180))
    flow.builtin("answer_router_blocked_output", "ChatOutput", (3420, 1180))
    flow.connect("answer_clarification_router", "blocked_path", "answer_router_blocked_message", "data")
    flow.connect("answer_router_blocked_message", "text", "answer_router_blocked_output", "input_value")

    # Start and answer review exits are mutually exclusive command branches.
    flow.custom("review_branch_joiner", "28_work_definition_branch_joiner.py", (4940, 0))
    flow.connect("answer_clarification_router", "review_path", "review_branch_joiner", "answered_work_definition")
    flow.connect("start_clarification_router", "review_path", "review_branch_joiner", "review_work_definition")
    flow.custom("graph_normalizer", "16_work_graph_normalizer.py", (5320, 0))
    flow.custom("preview", "17_work_preview_hasher.py", (5700, 0))
    flow.custom(
        "review_work_store",
        "18_work_definition_store.py",
        (6080, 0),
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
        (6460, 0),
        {
            "command": "request_approval",
            "derive_expected_revision": True,
            "derive_idempotency_key": True,
            "require_transactions": True,
        },
    )
    review_join_gate_key, review_join_gate_output = add_result_gate(
        "review_join",
        "review_branch_joiner",
        "joined_work_definition",
        required_field="work_definition",
        x=5130,
        y=360,
    )
    review_graph_gate_key, review_graph_gate_output = add_result_gate(
        "review_graph",
        "graph_normalizer",
        "normalized_graph",
        required_field="work_definition",
        x=5510,
        y=360,
    )
    review_preview_gate_key, review_preview_gate_output = add_result_gate(
        "review_preview",
        "preview",
        "preview",
        required_field="work_definition.preview_hash",
        x=5890,
        y=360,
    )
    review_store_gate_key, review_store_gate_output = add_result_gate(
        "review_store",
        "review_work_store",
        "stored_work_definition",
        required_field="work_definition",
        x=6270,
        y=360,
    )
    approval_store_gate_key, approval_store_gate_output = add_result_gate(
        "approval_store",
        "request_approval_store",
        "stored_work_definition",
        required_field="work_definition",
        x=6650,
        y=360,
    )
    flow.data_to_message("preview_message", (6840, 0))
    flow.builtin("preview_output", "ChatOutput", (7220, 0))
    flow.connect(review_join_gate_key, review_join_gate_output, "graph_normalizer", "work_definition")
    flow.connect(review_graph_gate_key, review_graph_gate_output, "preview", "work_definition")
    flow.connect(review_preview_gate_key, review_preview_gate_output, "review_work_store", "work_definition")
    flow.connect(review_store_gate_key, review_store_gate_output, "request_approval_store", "work_definition")
    flow.connect(approval_store_gate_key, approval_store_gate_output, "preview_message", "data")
    flow.connect("preview_message", "text", "preview_output", "input_value")

    # Structured approval actions use explicit static Store commands.  The
    # existing state and one-time action token remain external required tweaks.
    action_specs = (
        ("approve_path", "approve", 1400),
        ("reject_path", "reject", 1880),
        ("cancel_path", "cancel", 2360),
    )
    for route_output, command, action_y in action_specs:
        store_key = f"action_{command}_store"
        message_key = f"action_{command}_message"
        output_key = f"action_{command}_output"
        flow.custom(
            store_key,
            "18_work_definition_store.py",
            (380, action_y),
            {
                "command": command,
                "derive_expected_revision": True,
                "derive_idempotency_key": True,
                "require_transactions": True,
            },
        )
        flow.data_to_message(message_key, (760, action_y))
        flow.builtin(output_key, "ChatOutput", (1140, action_y))
        action_gate_key, action_gate_output = add_result_gate(
            f"action_{command}",
            store_key,
            "stored_work_definition",
            required_field="work_definition",
            x=570,
            y=action_y + 240,
        )
        flow.connect("existing_work_input", "data_output", store_key, "work_definition")
        flow.connect("command_router", route_output, store_key, "route_trigger")
        flow.connect(action_gate_key, action_gate_output, message_key, "data")
        flow.connect(message_key, "text", output_key, "input_value")

    flow.data_to_message("invalid_command_message", (380, -100))
    flow.builtin("invalid_command_output", "ChatOutput", (760, -100))
    flow.connect("command_router", "blocked_path", "invalid_command_message", "data")
    flow.connect("invalid_command_message", "text", "invalid_command_output", "input_value")
    result = flow.build()
    result["metadata"]["playground_turn_contract"] = {
        "schema_version": "playground-structured-command/v1",
        "commands": ["start", "submit_answers", "approve", "reject", "cancel"],
        "command_parser_node_key": "command_router",
        "command_parser_node_id": flow.nodes["command_router"].node_id,
        "top_level_command_only": True,
        "duplicate_json_keys_rejected": True,
        "follow_up_state_source": "explicit_node_tweaks",
        "existing_work_input_node_key": "existing_work_input",
        "existing_work_input_node_id": flow.nodes["existing_work_input"].node_id,
        "existing_batch_input_node_key": "existing_batch_input",
        "existing_batch_input_node_id": flow.nodes["existing_batch_input"].node_id,
        "round_number_node_key": "answer_clarification_batch",
        "round_number_node_id": flow.nodes["answer_clarification_batch"].node_id,
        "round_number_mode": "derived_from_processed_answer_batches",
        "action_store_node_ids": {
            command: flow.nodes[f"action_{command}_store"].node_id
            for command in ("approve", "reject", "cancel")
        },
        "silent_state_fallback": False,
        "native_hitl": False,
    }
    return result


def _add_search_pipeline(flow: FlowBuilder, *, y: float = 0, include_skill: bool) -> None:
    if include_skill:
        flow.custom("skill_context", "19_skill_context_resolver.py", (0, y - 420))
        planner_x = 0
    else:
        planner_x = 0
    flow.custom("query_plan", "20_search_query_planner.py", (planner_x, y))
    flow.custom(
        "query_embedding",
        "29_search_query_embedding_batcher.py",
        (planner_x + 380, y),
        {"provider_mode": "http_json", "allow_insecure_loopback": False},
    )
    flow.custom(
        "hybrid_retrieval",
        "21_catalog_hybrid_retriever.py",
        (planner_x + 760, y),
        {"provider_mode": "application_rrf"},
    )
    flow.custom("candidate_context", "22_candidate_context_builder.py", (planner_x + 1140, y))
    flow.connect("query_plan", "query_plan", "query_embedding", "query_plan")
    flow.connect("query_plan", "query_plan", "hybrid_retrieval", "query_plan")
    flow.connect("query_embedding", "query_vectors", "hybrid_retrieval", "query_vectors")
    flow.connect("hybrid_retrieval", "retrieval_result", "candidate_context", "retrieval_result")


def _build_f20() -> dict[str, Any]:
    flow = FlowBuilder("F20")
    _add_search_pipeline(flow, include_skill=True)
    flow.builtin("design_prompt_input", "ChatInput", (-420, -760))
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
    flow.builtin(
        "blueprint_json",
        "TypeConverter",
        (2660, -180),
        {"auto_parse": True, "output_type": "JSON"},
    )
    flow.custom("blueprint_normalizer", "23_agent_blueprint_normalizer.py", (3040, 0))
    flow.custom("port_validator", "24_port_contract_validator.py", (3420, 0))
    flow.custom("readiness", "25_blueprint_readiness_classifier.py", (3800, 0))
    flow.custom("generation_prompt", "26_component_generation_prompt_builder.py", (4180, 0))

    flow.connect("candidate_context", "candidate_context", "candidate_message", "data")
    flow.connect("design_prompt_input", "message", "query_plan", "design_prompt")
    flow.connect("query_plan", "design_scope", "skill_context", "design_scope")
    flow.connect("query_plan", "design_scope", "design_scope_message", "data")
    flow.connect("skill_context", "skill_context", "skill_message", "data")
    flow.connect("design_scope_message", "text", "blueprint_prompt", "design_scope")
    flow.connect("skill_message", "text", "blueprint_prompt", "approved_skill_context")
    flow.connect("candidate_message", "text", "blueprint_prompt", "candidate_context")
    flow.connect("blueprint_prompt", "prompt", "blueprint_model", "input_value")
    flow.connect("blueprint_model", "text_output", "blueprint_json", "input_data")
    flow.connect("blueprint_json", "data_output", "blueprint_normalizer", "blueprint_draft")
    flow.connect("query_plan", "design_scope", "blueprint_normalizer", "design_scope")
    flow.connect("candidate_context", "candidate_context", "blueprint_normalizer", "candidate_context")
    flow.connect("skill_context", "skill_context", "blueprint_normalizer", "applied_skill_context")
    flow.connect("blueprint_normalizer", "normalized_blueprint", "port_validator", "normalized_blueprint")
    flow.connect("port_validator", "validated_blueprint", "readiness", "validated_blueprint")
    flow.connect("readiness", "classified_blueprint", "generation_prompt", "classified_blueprint")

    result = flow.build()
    result["metadata"]["design_input_contract"] = {
        "schema_version": "agent-design-scope/v1",
        "single_scope_node_id": flow.nodes["query_plan"].node_id,
        "additional_design_prompt_node_id": flow.nodes["design_prompt_input"].node_id,
        "downstream_scope_source": "query_plan.design_scope",
        "independent_downstream_scope_tweaks": False,
    }
    return result


def _build_f30() -> dict[str, Any]:
    flow = FlowBuilder("F30")
    flow.custom("view_model", "30_report_view_model_builder.py", (0, 0))
    flow.custom("renderer", "31_responsive_report_renderer.py", (420, 0))
    flow.custom(
        "publisher",
        "32_report_publisher.py",
        (840, 0),
        {"report_api_url": "http://127.0.0.1:8091/api", "dry_run": True},
    )
    flow.connect("view_model", "report_view_model", "renderer", "report_view_model")
    flow.connect("renderer", "render_result", "publisher", "render_result")
    return flow.build()


def _build_f90() -> dict[str, Any]:
    flow = FlowBuilder("F90")
    _add_search_pipeline(flow, include_skill=False)
    flow.data_to_message("evaluation_message", (1520, 0))
    flow.builtin("evaluation_output", "ChatOutput", (1900, 0))
    flow.connect("candidate_context", "candidate_context", "evaluation_message", "data")
    flow.connect("evaluation_message", "text", "evaluation_output", "input_value")
    return flow.build()


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
    for edge in edges:
        if edge.get("source") not in node_by_id or edge.get("target") not in node_by_id:
            raise ValueError(f"{flow_key}: dangling edge {edge.get('id')}")
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
    if flow_key in {"F20", "F30", "F90", "F11"} and hitl_nodes:
        raise ValueError(f"{flow_key}: child/playground/evaluation Flow must not contain HumanInput")
    if flow_key in {"F00", "F10"} and not hitl_nodes:
        raise ValueError(f"{flow_key}: top-level Flow requires a HumanInput gate")
    if flow_key == "F20" and flow.get("metadata", {}).get("operational_readiness") == "import_ready":
        raise ValueError("F20 must remain fail-closed until external contracts are configured and validated")


def build_all() -> dict[str, dict[str, Any]]:
    installed_langflow = version("langflow")
    installed_lfx = version("lfx")
    if installed_langflow != LANGFLOW_VERSION or installed_lfx != LFX_VERSION:
        raise RuntimeError(
            "This generator requires the resolved Langflow 1.11.1 runtime "
            f"(langflow=={LANGFLOW_VERSION}, lfx=={LFX_VERSION}); found "
            f"langflow=={installed_langflow}, lfx=={installed_lfx}"
        )
    builders = {
        "F00": _build_f00,
        "F10": _build_f10,
        "F11": _build_f11,
        "F20": _build_f20,
        "F30": _build_f30,
        "F90": _build_f90,
    }
    return {flow_key: builder() for flow_key, builder in builders.items()}


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
