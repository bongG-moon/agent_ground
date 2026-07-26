from __future__ import annotations

"""Agent Ground의 Flow export를 Langflow 1.9.2 계약으로 맞추는 공통 도구."""

import asyncio
import importlib.metadata
import importlib.util
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from lfx.custom.eval import eval_custom_component_code
from lfx.custom.utils import create_component_template


TARGET_LANGFLOW_VERSION = "1.9.2"
TARGET_LANGFLOW_BASE_VERSION = "0.9.2"
TARGET_LFX_VERSION = "0.4.2"
HANDLE_QUOTE = "œ"
OBSOLETE_HANDLE_QUOTE = "┇"

_RUNTIME_VALUE_KEYS = (
    "value",
    "file_path",
    "selected",
    "selected_metadata",
    "selected_model",
)


def assert_target_runtime() -> None:
    """생성기가 정확한 대상 버전에서 실행되는지 확인합니다."""

    expected = {
        "langflow": TARGET_LANGFLOW_VERSION,
        "langflow-base": TARGET_LANGFLOW_BASE_VERSION,
        "lfx": TARGET_LFX_VERSION,
    }
    actual = {name: importlib.metadata.version(name) for name in expected}
    mismatches = [
        f"{name}={actual[name]}(필요 {version})"
        for name, version in expected.items()
        if actual[name] != version
    ]
    if mismatches:
        raise RuntimeError(
            "Langflow 1.9.2 전용 격리 환경에서 실행해야 합니다: " + ", ".join(mismatches)
        )


def _component_index_path() -> Path:
    spec = importlib.util.find_spec("lfx")
    if spec is None or spec.origin is None:
        raise RuntimeError("현재 Python 환경에서 LFX를 찾지 못했습니다.")
    path = Path(spec.origin).resolve().parent / "_assets" / "component_index.json"
    if not path.is_file():
        raise FileNotFoundError(f"LFX Component index를 찾지 못했습니다: {path}")
    return path


def load_component_index() -> dict[str, dict[str, Any]]:
    """LFX 0.4.2가 제공하는 기본 Component template을 이름별로 읽습니다."""

    payload = json.loads(_component_index_path().read_text(encoding="utf-8"))
    if payload.get("version") != TARGET_LFX_VERSION:
        raise RuntimeError(
            f"Component index 버전이 다릅니다: {payload.get('version')} "
            f"(필요 {TARGET_LFX_VERSION})"
        )
    index: dict[str, dict[str, Any]] = {}
    for entry in payload.get("entries", []):
        if not isinstance(entry, list) or len(entry) != 2 or not isinstance(entry[1], dict):
            continue
        for component_name, template in entry[1].items():
            if isinstance(template, dict):
                index[str(component_name)] = template
    return index


def _copy_runtime_values(
    target_template: dict[str, Any],
    old_template: dict[str, Any],
) -> None:
    """새 UI 계약은 유지하면서 사용자가 저장한 입력값만 옮깁니다."""

    for field_name, target_field in target_template.items():
        old_field = old_template.get(field_name)
        if not isinstance(target_field, dict) or not isinstance(old_field, dict):
            continue
        for key in _RUNTIME_VALUE_KEYS:
            if key in old_field:
                target_field[key] = deepcopy(old_field[key])

    # Prompt Template의 동적 변수처럼 Component 기본 template에 없는 연결
    # 필드는 Flow마다 생성되므로 그대로 보존해야 edge가 끊기지 않습니다.
    for field_name, old_field in old_template.items():
        if field_name in target_template or field_name in {"_type", "code"}:
            continue
        if isinstance(old_field, dict):
            target_template[field_name] = deepcopy(old_field)


def _custom_component_config(
    old_config: dict[str, Any],
    *,
    module_name: str,
) -> tuple[dict[str, Any], str]:
    old_template = old_config.get("template", {})
    code_field = old_template.get("code", {}) if isinstance(old_template, dict) else {}
    code = code_field.get("value") if isinstance(code_field, dict) else None
    if not isinstance(code, str) or not code.strip():
        raise ValueError(f"Standalone Component 코드가 없습니다: {module_name}")

    component_class = eval_custom_component_code(code)
    config, instance = create_component_template(
        {"code": code, "output_types": []},
        module_name=module_name,
    )
    if instance.__class__.__name__ != component_class.__name__:
        raise ValueError(
            f"Component 평가 결과가 다릅니다: {component_class.__name__} != "
            f"{instance.__class__.__name__}"
        )

    if old_config.get("tool_mode"):
        config = asyncio.run(
            instance.run_and_validate_update_outputs(config, "tool_mode", True)
        )

    _copy_runtime_values(
        config.setdefault("template", {}),
        old_template if isinstance(old_template, dict) else {},
    )
    config["lf_version"] = TARGET_LANGFLOW_VERSION
    return config, instance.__class__.__name__


def _built_in_config(
    old_config: dict[str, Any],
    *,
    component_type: str,
    component_index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if component_type not in component_index:
        raise KeyError(f"Langflow 1.9.2 기본 Component index에 없습니다: {component_type}")

    config = deepcopy(component_index[component_type])
    old_template = old_config.get("template", {})
    _copy_runtime_values(
        config.setdefault("template", {}),
        old_template if isinstance(old_template, dict) else {},
    )

    # Flow JSON에서 사용되는 보조 속성은 LFX index에 없는 경우가 있으므로
    # UI 배치·표시용 값만 이전 export에서 보존합니다.
    for key in (
        "full_path",
        "is_composition",
        "is_input",
        "is_output",
        "name",
        "priority",
        "replacement",
    ):
        if key not in config and key in old_config:
            config[key] = deepcopy(old_config[key])
    config["lf_version"] = TARGET_LANGFLOW_VERSION
    return config


def _handle_text(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).replace('"', HANDLE_QUOTE)


def _output_contract(node: dict[str, Any], output_name: str) -> dict[str, Any]:
    outputs = node.get("data", {}).get("node", {}).get("outputs", [])
    output = next(
        (item for item in outputs if isinstance(item, dict) and item.get("name") == output_name),
        None,
    )
    if output is None:
        raise KeyError(f"출력 포트를 찾지 못했습니다: {node.get('id')}.{output_name}")
    return output


def _input_contract(node: dict[str, Any], field_name: str) -> dict[str, Any]:
    field = (
        node.get("data", {})
        .get("node", {})
        .get("template", {})
        .get(field_name)
    )
    if not isinstance(field, dict):
        raise KeyError(f"입력 포트를 찾지 못했습니다: {node.get('id')}.{field_name}")
    return field


def rebuild_edge_handles(flow: dict[str, Any]) -> None:
    """1.9.2 프런트엔드가 사용하는 정렬 JSON + œ handle을 다시 만듭니다."""

    nodes = {
        str(node.get("id")): node
        for node in flow.get("data", {}).get("nodes", [])
        if isinstance(node, dict)
    }
    for edge in flow.get("data", {}).get("edges", []):
        source_id = str(edge.get("source") or "")
        target_id = str(edge.get("target") or "")
        if source_id not in nodes or target_id not in nodes:
            raise ValueError(f"연결 대상이 없는 edge입니다: {edge.get('id')}")

        old_data = edge.get("data", {})
        old_source = old_data.get("sourceHandle", {}) if isinstance(old_data, dict) else {}
        old_target = old_data.get("targetHandle", {}) if isinstance(old_data, dict) else {}
        source_name = old_source.get("name")
        target_name = old_target.get("fieldName")
        if not source_name or not target_name:
            raise ValueError(f"edge 포트 이름이 없습니다: {edge.get('id')}")

        source_node = nodes[source_id]
        target_node = nodes[target_id]
        source_output = _output_contract(source_node, str(source_name))
        target_input = _input_contract(target_node, str(target_name))

        output_types = source_output.get("types") or (
            [source_output.get("selected")] if source_output.get("selected") else []
        )
        input_types = target_input.get("input_types") or old_target.get("inputTypes") or []
        source_handle = {
            "dataType": source_node.get("data", {}).get("type"),
            "id": source_id,
            "name": str(source_name),
            "output_types": output_types,
        }
        target_handle = {
            "fieldName": str(target_name),
            "id": target_id,
            "inputTypes": input_types,
            "type": target_input.get("type") or old_target.get("type") or "other",
        }
        source_text = _handle_text(source_handle)
        target_text = _handle_text(target_handle)
        edge["sourceHandle"] = source_text
        edge["targetHandle"] = target_text
        edge.setdefault("data", {})["sourceHandle"] = source_handle
        edge["data"]["targetHandle"] = target_handle
        edge["id"] = (
            f"xy-edge__{source_id}{source_text}-{target_id}{target_text}"
        )


def validate_handle_contract(flow: dict[str, Any]) -> None:
    for edge in flow.get("data", {}).get("edges", []):
        for key in ("sourceHandle", "targetHandle"):
            encoded = edge.get(key)
            if (
                not isinstance(encoded, str)
                or not encoded.startswith("{" + HANDLE_QUOTE)
                or OBSOLETE_HANDLE_QUOTE in encoded
            ):
                raise ValueError(f"Langflow 1.9.2 handle 형식이 아닙니다: {edge.get('id')} {key}")
            decoded = json.loads(encoded.replace(HANDLE_QUOTE, '"'))
            if decoded != edge.get("data", {}).get(key):
                raise ValueError(f"handle 문자열과 edge.data가 다릅니다: {edge.get('id')} {key}")


def upgrade_flow(
    flow: dict[str, Any],
    *,
    module_prefix: str,
    component_index: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Flow 안의 모든 실행 node를 1.9.2 template으로 재생성합니다."""

    assert_target_runtime()
    index = component_index or load_component_index()
    upgraded = deepcopy(flow)
    upgraded["last_tested_version"] = TARGET_LANGFLOW_VERSION

    for graph_node in upgraded.get("data", {}).get("nodes", []):
        data = graph_node.get("data", {})
        component_type = str(data.get("type") or "")
        if component_type == "note":
            continue
        old_config = data.get("node", {})
        if not isinstance(old_config, dict):
            raise ValueError(f"node 설정이 없습니다: {graph_node.get('id')}")

        if component_type in index:
            new_config = _built_in_config(
                old_config,
                component_type=component_type,
                component_index=index,
            )
            resolved_type = component_type
        else:
            safe_id = re.sub(r"[^A-Za-z0-9_]", "_", str(graph_node.get("id") or "node"))
            new_config, resolved_type = _custom_component_config(
                old_config,
                module_name=f"{module_prefix}.{safe_id}",
            )

        data["type"] = resolved_type
        data["node"] = new_config
        data["description"] = new_config.get("description") or data.get("description") or ""
        if not data.get("display_name"):
            data["display_name"] = new_config.get("display_name") or resolved_type
        outputs = new_config.get("outputs") or []
        if outputs and not any(
            item.get("name") == data.get("selected_output")
            for item in outputs
            if isinstance(item, dict)
        ):
            data["selected_output"] = outputs[0].get("name")

    rebuild_edge_handles(upgraded)
    validate_handle_contract(upgraded)
    return upgraded


def write_json(path: Path, payload: Any, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    path.write_bytes(text.encode("utf-8"))
