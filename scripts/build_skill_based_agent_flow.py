from __future__ import annotations

import argparse
import asyncio
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
except ImportError as exc:  # pragma: no cover - 잘못된 Python으로 실행할 때만 사용
    raise SystemExit(
        "이 생성기는 Langflow Desktop Python으로 실행해야 합니다. "
        "예: %LOCALAPPDATA%\\com.LangflowDesktop\\.langflow-venv\\Scripts\\python.exe "
        "scripts\\build_skill_based_agent_flow.py"
    ) from exc


ROOT = Path(__file__).resolve().parents[1]
FLOW_ROOT = ROOT / "flows" / "skill_based_agent_flow"
FLOW_TARGET = FLOW_ROOT / "skill_based_agent_flow.json"
MEETING_SUBFLOW_TARGET = FLOW_ROOT / "meeting_action_skill_flow.json"
SKILL_BUNDLE_TARGET = FLOW_ROOT / "00_SKILL_BASED_AGENT_ALL_FLOWS.json"
PROJECT_BUNDLE_TARGET = ROOT / "flows" / "00_AGENT_GROUND_ALL_FLOWS.json"

MEETING_SUBFLOW_NAME = "meeting_action_skill_flow"

PROJECT_FLOW_SOURCES = (
    ROOT / "flows" / "reusable_data_flow" / "reusable_data_flow.json",
    ROOT / "flows" / "html_report_flow" / "html_report_flow.json",
    ROOT / "flows" / "enterprise_document_rag_flow" / "enterprise_document_rag_flow.json",
    MEETING_SUBFLOW_TARGET,
    FLOW_TARGET,
    ROOT / "business_agent_design" / "flow" / "business_agent_design_complete.json",
)


@dataclass(frozen=True)
class ComponentSpec:
    key: str
    relative_path: str
    node_id: str
    position: tuple[float, float]
    mode: str = "plain"
    tool_name: str | None = None
    tool_description: str | None = None


PARENT_COMPONENT_SPECS = (
    ComponentSpec(
        "catalog",
        "components/demo_skill_catalog_builder/demo_skill_catalog_builder.py",
        "DemoSkillCatalogBuilder-skillAgent",
        (0.0, 20.0),
    ),
    ComponentSpec(
        "expense",
        "components/expense_precheck_skill_tool/expense_precheck_skill_tool.py",
        "ExpensePrecheckSkillTool-skillAgent",
        (440.0, -260.0),
        "direct_tool",
        "expense_precheck_skill",
        (
            "식대·교통비·숙박비·기타 경비의 금액을 합산하고 데모 기준과 비교할 때 사용합니다. "
            "휴가 계산이나 회의 액션아이템에는 사용하지 않습니다. 승인·결제·저장은 수행하지 않습니다."
        ),
    ),
    ComponentSpec(
        "leave",
        "components/leave_policy_skill_tool/leave_policy_skill_tool.py",
        "LeavePolicySkillTool-skillAgent",
        (440.0, 80.0),
        "direct_tool",
        "leave_policy_skill",
        (
            "두 ISO 날짜 사이의 주말·데모 공휴일을 제외한 평일 휴가 일수를 계산할 때 사용합니다. "
            "경비나 회의 요청에는 사용하지 않습니다. 실제 휴가 신청·승인은 수행하지 않습니다."
        ),
    ),
    ComponentSpec(
        "meeting_run_flow",
        "components/cached_named_run_flow_tool/cached_named_run_flow_tool.py",
        "CachedNamedRunFlowTool-meetingSkill",
        (440.0, 420.0),
        "run_flow_tool",
        "meeting_action_skill",
        (
            "`담당자 | 할 일 | YYYY-MM-DD` 형식의 회의 내용을 별도 회의 후속 조치 Skill Flow로 보내 "
            "액션아이템을 구조화할 때 사용합니다. 경비나 휴가 요청에는 사용하지 않으며, "
            "메일·알림·캘린더 등록은 수행하지 않습니다."
        ),
    ),
)

CHILD_COMPONENT_SPECS = (
    ComponentSpec(
        "catalog",
        "components/demo_skill_catalog_builder/demo_skill_catalog_builder.py",
        "DemoSkillCatalogBuilder-meetingSubflow",
        (-40.0, -220.0),
    ),
    ComponentSpec(
        "meeting",
        "components/meeting_action_skill_tool/meeting_action_skill_tool.py",
        "MeetingActionSkillTool-meetingSubflow",
        (420.0, 80.0),
    ),
)

PARENT_EDGE_SPECS = (
    ("catalog", "agent_instructions", "agent", "system_prompt"),
    ("catalog", "skill_catalog", "expense", "skill_catalog"),
    ("catalog", "skill_catalog", "leave", "skill_catalog"),
    ("chat_input", "message", "agent", "input_value"),
    ("expense", "component_as_tool", "agent", "tools"),
    ("leave", "component_as_tool", "agent", "tools"),
    ("meeting_run_flow", "component_as_tool", "agent", "tools"),
    ("agent", "response", "chat_output", "input_value"),
)

CHILD_EDGE_SPECS = (
    ("chat_input", "message", "meeting", "request"),
    ("catalog", "skill_catalog", "meeting", "skill_catalog"),
    ("meeting", "skill_message", "chat_output", "input_value"),
)


def _installed_starter_path() -> Path:
    langflow_spec = importlib.util.find_spec("langflow")
    if langflow_spec is None or langflow_spec.origin is None:
        raise RuntimeError("현재 Python 환경에 Langflow가 없습니다.")
    path = Path(langflow_spec.origin).resolve().parent / "initial_setup" / "starter_projects" / "Simple Agent.json"
    if not path.is_file():
        raise FileNotFoundError(f"설치된 Simple Agent 예제를 찾지 못했습니다: {path}")
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


def _find_single_node(flow: dict[str, Any], node_type: str) -> dict[str, Any]:
    matches = [node for node in flow.get("data", {}).get("nodes", []) if node.get("data", {}).get("type") == node_type]
    if len(matches) != 1:
        raise ValueError(f"Simple Agent 예제에서 {node_type} 노드를 정확히 하나 찾지 못했습니다: {len(matches)}")
    return matches[0]


def _find_note(flow: dict[str, Any]) -> dict[str, Any]:
    matches = [node for node in flow.get("data", {}).get("nodes", []) if node.get("data", {}).get("type") == "note"]
    if not matches:
        raise ValueError("Simple Agent 예제에서 Note 노드를 찾지 못했습니다.")
    return matches[0]


def _clone_node(prototype: dict[str, Any], node_id: str, position: tuple[float, float]) -> dict[str, Any]:
    node = deepcopy(prototype)
    node["id"] = node_id
    node.setdefault("data", {})["id"] = node_id
    node["position"] = {"x": position[0], "y": position[1]}
    node["selected"] = False
    node["dragging"] = False
    node.pop("measured", None)
    node.pop("positionAbsolute", None)
    return node


def _set_template_value(node: dict[str, Any], field_name: str, value: Any) -> None:
    field = node["data"]["node"].get("template", {}).get(field_name)
    if not isinstance(field, dict):
        raise ValueError(f"입력 필드를 찾지 못했습니다: {node['id']}.{field_name}")
    field["value"] = value


def _build_note(
    prototype: dict[str, Any],
    node_id: str,
    position: tuple[float, float],
    description: str,
    background_color: str,
    *,
    size: tuple[int, int] = (324, 324),
) -> dict[str, Any]:
    note = _clone_node(prototype, node_id, position)
    note["data"]["type"] = "note"
    note["data"]["node"]["description"] = description
    note["data"]["node"]["display_name"] = ""
    note["data"]["node"].setdefault("template", {})["backgroundColor"] = background_color
    note["style"] = {"height": size[1], "width": size[0]}
    note["height"] = size[1]
    note["width"] = size[0]
    note["positionAbsolute"] = {"x": position[0], "y": position[1]}
    note["resizing"] = False
    return note


def _enable_tool_mode(config: dict[str, Any], instance: Any, spec: ComponentSpec) -> dict[str, Any]:
    """Langflow 1.8.2가 Builder에서 Tool Mode를 켤 때와 같은 변환을 적용합니다."""

    updated = asyncio.run(instance.run_and_validate_update_outputs(config, "tool_mode", True))
    metadata_field = updated.get("template", {}).get("tools_metadata", {})
    metadata_rows = metadata_field.get("value") if isinstance(metadata_field, dict) else None
    if not isinstance(metadata_rows, list) or len(metadata_rows) != 1:
        raise ValueError(f"{spec.relative_path}: Tool metadata가 정확히 하나가 아닙니다.")
    metadata_rows[0].update(
        {
            "name": spec.tool_name,
            "display_name": spec.tool_name,
            "description": spec.tool_description,
            "display_description": spec.tool_description,
            "status": True,
            "tags": [spec.tool_name],
        }
    )
    return updated


def _component_template(spec: ComponentSpec) -> tuple[dict[str, Any], Any, str]:
    source_path = ROOT / spec.relative_path
    if not source_path.is_file():
        raise FileNotFoundError(f"Standalone Component 원본을 찾지 못했습니다: {source_path}")
    code = source_path.read_text(encoding="utf-8")
    component_class = eval_custom_component_code(code)
    config, instance = create_component_template(
        {"code": code, "output_types": []},
        module_name=f"agent_ground.skill_based_agent.{spec.node_id.replace('-', '_')}",
    )
    if instance.__class__.__name__ != component_class.__name__:
        raise ValueError(
            f"Component 평가 결과가 다릅니다: {source_path} "
            f"{component_class.__name__} != {instance.__class__.__name__}"
        )
    if spec.mode == "direct_tool":
        config = _enable_tool_mode(config, instance, spec)
    config["lf_version"] = "1.8.2"
    return config, instance, code


def _build_custom_node(
    wrapper_prototype: dict[str, Any],
    spec: ComponentSpec,
    sources: dict[str, str],
) -> dict[str, Any]:
    config, instance, code = _component_template(spec)
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


def _handle_text(value: dict[str, Any]) -> str:
    """Langflow 1.8.2의 안정적인 edge handle 인코딩을 사용합니다."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).replace('"', "œ")


def _add_edge(
    flow: dict[str, Any],
    source: dict[str, Any],
    source_name: str,
    target: dict[str, Any],
    target_name: str,
) -> None:
    source_output = next(
        (item for item in source["data"]["node"].get("outputs", []) if item.get("name") == source_name),
        None,
    )
    if source_output is None:
        raise ValueError(f"출력 포트를 찾지 못했습니다: {source['id']}.{source_name}")
    target_input = target["data"]["node"].get("template", {}).get(target_name)
    if not isinstance(target_input, dict):
        raise ValueError(f"입력 포트를 찾지 못했습니다: {target['id']}.{target_name}")

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


def _starter_parts() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    starter = _read_json(_installed_starter_path())
    return (
        _find_single_node(starter, "ChatInput"),
        _find_single_node(starter, "ChatOutput"),
        _find_single_node(starter, "Agent"),
        _find_note(starter),
    )


def build_parent_flow() -> tuple[dict[str, Any], dict[str, str]]:
    chat_input_donor, chat_output_donor, agent_donor, note_donor = _starter_parts()
    chat_input = _clone_node(chat_input_donor, "ChatInput-skillAgent", (0.0, 640.0))
    chat_output = _clone_node(chat_output_donor, "ChatOutput-skillAgent", (1360.0, 120.0))
    agent = _clone_node(agent_donor, "Agent-skillSupervisor", (940.0, 120.0))

    _set_template_value(chat_input, "input_value", "식대 28,000원과 교통비 17,000원의 합계와 데모 기준을 확인해줘")
    _set_template_value(chat_input, "should_store_message", False)
    _set_template_value(chat_output, "sender_name", "Hybrid Skill Agent Demo")
    _set_template_value(chat_output, "should_store_message", False)
    _set_template_value(agent, "model", "")
    _set_template_value(agent, "api_key", "")
    _set_template_value(agent, "system_prompt", "Skill Catalog Builder의 지침을 연결합니다.")
    _set_template_value(agent, "add_current_date_tool", False)
    _set_template_value(agent, "max_iterations", 3)
    _set_template_value(agent, "n_messages", 0)
    _set_template_value(agent, "verbose", False)
    agent["data"]["node"]["display_name"] = "Hybrid Skill Supervisor Agent"
    agent["data"]["display_name"] = "Hybrid Skill Supervisor Agent"

    sources: dict[str, str] = {}
    nodes_by_key: dict[str, dict[str, Any]] = {
        "chat_input": chat_input,
        "chat_output": chat_output,
        "agent": agent,
    }
    custom_nodes: list[dict[str, Any]] = []
    for spec in PARENT_COMPONENT_SPECS:
        node = _build_custom_node(chat_input_donor, spec, sources)
        if spec.mode == "run_flow_tool":
            _set_template_value(node, "flow_name_selected", MEETING_SUBFLOW_NAME)
            _set_template_value(node, "flow_id_selected", "")
            _set_template_value(node, "session_id", "")
            _set_template_value(node, "cache_flow", True)
            _set_template_value(node, "allow_cross_folder", False)
            _set_template_value(node, "tool_name", spec.tool_name)
            _set_template_value(node, "tool_description", spec.tool_description)
            _set_template_value(node, "return_direct", True)
        nodes_by_key[spec.key] = node
        custom_nodes.append(node)

    first_run_note = _build_note(
        note_donor,
        "note-skillAgent-firstRun",
        (-380.0, 20.0),
        (
            "## 먼저 일괄 Bundle을 가져오세요\n\n"
            "1. `00_SKILL_BASED_AGENT_ALL_FLOWS.json`을 같은 프로젝트에 가져옵니다.\n"
            "2. **Hybrid Skill Supervisor Agent**에서 승인된 Tool Calling 모델을 선택합니다.\n"
            "3. 경비·휴가·회의 예시를 각각 실행합니다.\n\n"
            "회의 Tool은 같은 폴더의 `meeting_action_skill_flow`를 이름으로 찾습니다."
        ),
        "blue",
    )
    governance_note = _build_note(
        note_donor,
        "note-skillAgent-governance",
        (900.0, 560.0),
        (
            "## 한 Flow에서 두 Tool 패턴 비교\n\n"
            "- 경비·휴가: 작은 계산 Component를 직접 Tool Mode로 연결\n"
            "- 회의 후속 조치: 별도 Skill Flow를 Run Flow Tool로 호출\n"
            "- Run Flow 외부 인자: provider-safe `question` 하나\n"
            "- 구조적 금지: 승인, 저장, 발송 Tool은 연결하지 않음\n\n"
            "MCP는 사내 공용 Tool 서버가 있을 때 같은 Agent Tools 포트에 교체 연결합니다."
        ),
        "amber",
    )

    flow: dict[str, Any] = {
        "data": {
            "edges": [],
            "nodes": [first_run_note, *custom_nodes, agent, governance_note, chat_input, chat_output],
            "viewport": {"x": 65.0, "y": 80.0, "zoom": 0.46},
        },
        "description": (
            "작은 경비·휴가 계산은 Standalone Component Tool로 직접 실행하고, 회의 후속 조치는 "
            "개선된 이름 기반 Run Flow Tool로 별도 Skill Flow를 호출하는 Langflow 1.8.2 하이브리드 예시입니다."
        ),
        "endpoint_name": "skill-based-agent-hybrid-demo",
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "agent-ground/skill-based-agent-flow/0.2.0")),
        "is_component": False,
        "last_tested_version": "1.8.2",
        "locked": False,
        "name": "skill_based_agent_flow",
        "tags": ["agent", "skill", "tool", "run-flow", "hybrid", "standalone", "demo"],
    }
    for source_key, source_name, target_key, target_name in PARENT_EDGE_SPECS:
        _add_edge(flow, nodes_by_key[source_key], source_name, nodes_by_key[target_key], target_name)
    return flow, sources


def build_meeting_subflow() -> tuple[dict[str, Any], dict[str, str]]:
    chat_input_donor, chat_output_donor, _, note_donor = _starter_parts()
    chat_input = _clone_node(chat_input_donor, "ChatInput-meetingSkill", (-40.0, 120.0))
    chat_output = _clone_node(chat_output_donor, "ChatOutput-meetingSkill", (900.0, 80.0))
    _set_template_value(
        chat_input,
        "input_value",
        "김민수 | 비용안 작성 | 2026-07-15\n이서연 | 비용안 검토 | 2026-07-16",
    )
    _set_template_value(chat_input, "should_store_message", False)
    _set_template_value(chat_output, "sender_name", "Meeting Skill Flow")
    _set_template_value(chat_output, "should_store_message", False)

    sources: dict[str, str] = {}
    nodes_by_key: dict[str, dict[str, Any]] = {"chat_input": chat_input, "chat_output": chat_output}
    custom_nodes: list[dict[str, Any]] = []
    for spec in CHILD_COMPONENT_SPECS:
        node = _build_custom_node(chat_input_donor, spec, sources)
        nodes_by_key[spec.key] = node
        custom_nodes.append(node)

    note = _build_note(
        note_donor,
        "note-meetingSkill-contract",
        (400.0, -260.0),
        (
            "## 회의 후속 조치 Skill Flow\n\n"
            "입력은 Chat Input 하나, 출력은 Chat Output 하나입니다.\n"
            "부모 Run Flow Tool은 `question`을 현재 Chat Input ID에 내부 매핑합니다.\n\n"
            "현재는 결정론적 구조화 단계 하나지만, 이후 검토·담당자 조회·초안 생성 단계를 이 Flow 안에 추가할 수 있습니다."
        ),
        "green",
        size=(360, 250),
    )

    flow: dict[str, Any] = {
        "data": {
            "edges": [],
            "nodes": [note, *custom_nodes, chat_input, chat_output],
            "viewport": {"x": 150.0, "y": 180.0, "zoom": 0.62},
        },
        "description": (
            "회의 후속 조치 문장을 구조화하는 독립 Skill Flow입니다. 상위 Agent에서는 "
            "Cached Named Run Flow Tool을 통해 provider-safe question 인자로 호출합니다."
        ),
        "endpoint_name": "meeting-action-skill-flow",
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "agent-ground/meeting-action-skill-flow/0.2.0")),
        "is_component": False,
        "last_tested_version": "1.8.2",
        "locked": False,
        "name": MEETING_SUBFLOW_NAME,
        "tags": ["skill", "subflow", "meeting", "run-flow", "standalone", "demo"],
    }
    for source_key, source_name, target_key, target_name in CHILD_EDGE_SPECS:
        _add_edge(flow, nodes_by_key[source_key], source_name, nodes_by_key[target_key], target_name)
    return flow, sources


def _decode_handle(value: str) -> dict[str, Any]:
    if "┇" in value:
        raise ValueError("구형 edge handle delimiter가 포함되어 있습니다.")
    return json.loads(value.replace("œ", '"'))


def _validate_edges(flow: dict[str, Any], expected_specs: tuple[tuple[str, str, str, str], ...], ids: dict[str, str]) -> None:
    nodes = flow.get("data", {}).get("nodes", [])
    edges = flow.get("data", {}).get("edges", [])
    node_by_id = {node.get("id"): node for node in nodes}
    if len(node_by_id) != len(nodes) or None in node_by_id:
        raise ValueError(f"{flow.get('name')}: 모든 node ID는 존재하고 고유해야 합니다.")
    edge_ids: set[str] = set()
    for edge in edges:
        if edge.get("id") in edge_ids:
            raise ValueError(f"중복 edge ID입니다: {edge.get('id')}")
        edge_ids.add(edge["id"])
        if edge.get("source") not in node_by_id or edge.get("target") not in node_by_id:
            raise ValueError(f"존재하지 않는 node에 연결된 edge입니다: {edge.get('id')}")
        for key in ("sourceHandle", "targetHandle"):
            decoded = _decode_handle(edge[key])
            if decoded != edge["data"][key] or edge[key] != _handle_text(edge["data"][key]):
                raise ValueError(f"Langflow 1.8.2 handle/data가 다릅니다: {edge.get('id')} {key}")
    actual = {
        (
            edge["source"],
            edge["data"]["sourceHandle"]["name"],
            edge["target"],
            edge["data"]["targetHandle"]["fieldName"],
        )
        for edge in edges
    }
    expected = {(ids[s], so, ids[t], ti) for s, so, t, ti in expected_specs}
    if actual != expected:
        raise ValueError(f"{flow.get('name')}: Flow 연결이 계약과 다릅니다: {actual ^ expected}")


def _validate_custom_nodes(
    flow: dict[str, Any],
    specs: tuple[ComponentSpec, ...],
    sources: dict[str, str],
) -> None:
    node_by_id = {node.get("id"): node for node in flow["data"]["nodes"]}
    for spec in specs:
        node = node_by_id[spec.node_id]
        config = node["data"]["node"]
        if config["template"]["code"]["value"] != sources[spec.node_id]:
            raise ValueError(f"Flow 내장 코드가 원본과 다릅니다: {spec.relative_path}")
        rebuilt, instance, _ = _component_template(spec)
        if rebuilt.get("field_order") != config.get("field_order"):
            raise ValueError(f"입력 template이 재생성 결과와 다릅니다: {spec.relative_path}")
        if [item.get("name") for item in rebuilt.get("outputs", [])] != [
            item.get("name") for item in config.get("outputs", [])
        ]:
            raise ValueError(f"출력 template이 재생성 결과와 다릅니다: {spec.relative_path}")
        if instance.__class__.__name__ != node["data"]["type"]:
            raise ValueError(f"직렬화된 Component type이 다릅니다: {spec.relative_path}")


def validate_flows(
    parent: dict[str, Any],
    parent_sources: dict[str, str],
    child: dict[str, Any],
    child_sources: dict[str, str],
) -> None:
    if importlib.metadata.version("langflow") != "1.8.2":
        raise ValueError(f"Langflow 1.8.2가 필요합니다: {importlib.metadata.version('langflow')}")
    if importlib.metadata.version("lfx") != "0.3.4":
        raise ValueError(f"LFX 0.3.4가 필요합니다: {importlib.metadata.version('lfx')}")

    if len(parent["data"]["nodes"]) != 9 or len(parent["data"]["edges"]) != 8:
        raise ValueError("상위 Hybrid Skill Agent Flow는 9 nodes / 8 edges여야 합니다.")
    _validate_custom_nodes(parent, PARENT_COMPONENT_SPECS, parent_sources)
    parent_ids = {
        "chat_input": "ChatInput-skillAgent",
        "chat_output": "ChatOutput-skillAgent",
        "agent": "Agent-skillSupervisor",
        **{spec.key: spec.node_id for spec in PARENT_COMPONENT_SPECS},
    }
    _validate_edges(parent, PARENT_EDGE_SPECS, parent_ids)
    parent_nodes = {node["id"]: node for node in parent["data"]["nodes"]}
    agent = parent_nodes["Agent-skillSupervisor"]["data"]["node"]
    if agent["template"]["model"].get("value") not in ("", None):
        raise ValueError("배포 JSON에는 특정 모델을 고정하지 않습니다.")
    if agent["template"]["add_current_date_tool"].get("value") is not False:
        raise ValueError("예제 범위 밖 Current Date Tool은 비활성화해야 합니다.")

    for key in ("expense", "leave"):
        spec = next(item for item in PARENT_COMPONENT_SPECS if item.key == key)
        config = parent_nodes[spec.node_id]["data"]["node"]
        if config.get("tool_mode") is not True:
            raise ValueError(f"직접 계산 Component가 Tool Mode가 아닙니다: {key}")
        outputs = config.get("outputs", [])
        if len(outputs) != 1 or outputs[0].get("name") != "component_as_tool" or outputs[0].get("types") != ["Tool"]:
            raise ValueError(f"직접 계산 Tool 출력 계약이 잘못되었습니다: {key}")

    run_spec = next(item for item in PARENT_COMPONENT_SPECS if item.key == "meeting_run_flow")
    run_config = parent_nodes[run_spec.node_id]["data"]["node"]
    run_template = run_config["template"]
    if run_config.get("tool_mode") is not False:
        raise ValueError("Cached Named Run Flow Tool은 자체 Tool 출력을 사용하므로 Tool Mode 변환을 다시 하면 안 됩니다.")
    run_outputs = run_config.get("outputs", [])
    if len(run_outputs) != 1 or run_outputs[0].get("name") != "component_as_tool" or run_outputs[0].get("types") != ["Tool"]:
        raise ValueError("Cached Named Run Flow Tool 출력 계약이 잘못되었습니다.")
    expected_run_values = {
        "flow_name_selected": MEETING_SUBFLOW_NAME,
        "flow_id_selected": "",
        "cache_flow": True,
        "allow_cross_folder": False,
        "tool_name": "meeting_action_skill",
        "return_direct": True,
    }
    for field, expected in expected_run_values.items():
        if run_template[field].get("value") != expected:
            raise ValueError(f"Run Flow Tool 설정이 잘못되었습니다: {field}")

    if child.get("name") != MEETING_SUBFLOW_NAME:
        raise ValueError("회의 하위 Flow 이름 계약이 다릅니다.")
    if len(child["data"]["nodes"]) != 5 or len(child["data"]["edges"]) != 3:
        raise ValueError("회의 하위 Flow는 5 nodes / 3 edges여야 합니다.")
    _validate_custom_nodes(child, CHILD_COMPONENT_SPECS, child_sources)
    child_ids = {
        "chat_input": "ChatInput-meetingSkill",
        "chat_output": "ChatOutput-meetingSkill",
        **{spec.key: spec.node_id for spec in CHILD_COMPONENT_SPECS},
    }
    _validate_edges(child, CHILD_EDGE_SPECS, child_ids)
    child_types = [node["data"].get("type") for node in child["data"]["nodes"]]
    if child_types.count("ChatInput") != 1 or child_types.count("ChatOutput") != 1 or "Agent" in child_types:
        raise ValueError("회의 하위 Flow는 Chat Input/Output이 각각 하나이고 Agent가 없어야 합니다.")


def build_skill_bundle(child: dict[str, Any], parent: dict[str, Any]) -> dict[str, Any]:
    return {"flows": [child, parent]}


def build_project_bundle() -> dict[str, Any]:
    flows: list[dict[str, Any]] = []
    for path in PROJECT_FLOW_SOURCES:
        if not path.is_file():
            raise FileNotFoundError(f"전체 Bundle 원본 Flow를 찾지 못했습니다: {path}")
        flow = _read_json(path)
        if "flows" in flow:
            raise ValueError(f"전체 Bundle 원본은 개별 Flow여야 합니다: {path}")
        flows.append(flow)
    if len({flow.get("name") for flow in flows}) != len(flows):
        raise ValueError("전체 Bundle의 Flow 이름은 고유해야 합니다.")
    return {"flows": flows}


def _validate_written_files() -> None:
    for path in (FLOW_TARGET, MEETING_SUBFLOW_TARGET, SKILL_BUNDLE_TARGET, PROJECT_BUNDLE_TARGET):
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            raise ValueError(f"Langflow JSON에는 UTF-8 BOM을 사용할 수 없습니다: {path}")
        json.loads(raw.decode("utf-8"))
    if not SKILL_BUNDLE_TARGET.read_bytes().startswith(b'{"flows":['):
        raise ValueError('Skill Bundle은 정확히 {"flows":[ 로 시작해야 합니다.')
    if not PROJECT_BUNDLE_TARGET.read_bytes().startswith(b'{"flows":['):
        raise ValueError('전체 Bundle은 정확히 {"flows":[ 로 시작해야 합니다.')

    skill_names = [
        flow.get("name")
        for flow in json.loads(SKILL_BUNDLE_TARGET.read_text(encoding="utf-8")).get("flows", [])
    ]
    if skill_names != [MEETING_SUBFLOW_NAME, "skill_based_agent_flow"]:
        raise ValueError(f"Skill Bundle 순서가 다릅니다: {skill_names}")

    expected_names = [
        "업무분석flow",
        "html_flow_0624",
        "enterprise_document_rag_flow",
        MEETING_SUBFLOW_NAME,
        "skill_based_agent_flow",
        "business_agent_design_complete",
    ]
    actual_names = [
        flow.get("name")
        for flow in json.loads(PROJECT_BUNDLE_TARGET.read_text(encoding="utf-8")).get("flows", [])
    ]
    if actual_names != expected_names:
        raise ValueError(f"전체 Bundle 순서가 다릅니다: {actual_names}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Langflow 1.8.2 하이브리드 Skill 기반 Agent 예시 Flow를 생성합니다.")
    parser.add_argument("--check", action="store_true", help="파일을 수정하지 않고 생성 결과와 현재 파일을 비교합니다.")
    args = parser.parse_args()

    parent, parent_sources = build_parent_flow()
    child, child_sources = build_meeting_subflow()
    validate_flows(parent, parent_sources, child, child_sources)
    skill_bundle = build_skill_bundle(child, parent)

    if args.check:
        if not FLOW_TARGET.is_file() or _read_json(FLOW_TARGET) != parent:
            raise ValueError(f"생성된 상위 Skill Flow가 현재 원본과 다릅니다: {FLOW_TARGET}")
        if not MEETING_SUBFLOW_TARGET.is_file() or _read_json(MEETING_SUBFLOW_TARGET) != child:
            raise ValueError(f"생성된 회의 하위 Flow가 현재 원본과 다릅니다: {MEETING_SUBFLOW_TARGET}")
        if not SKILL_BUNDLE_TARGET.is_file() or _read_json(SKILL_BUNDLE_TARGET) != skill_bundle:
            raise ValueError(f"생성된 Skill Bundle이 현재 원본과 다릅니다: {SKILL_BUNDLE_TARGET}")
        project_bundle = build_project_bundle()
        if _read_json(PROJECT_BUNDLE_TARGET) != project_bundle:
            raise ValueError(f"전체 Bundle이 현재 Flow 집합과 다릅니다: {PROJECT_BUNDLE_TARGET}")
    else:
        _write_json(FLOW_TARGET, parent, compact=False)
        _write_json(MEETING_SUBFLOW_TARGET, child, compact=False)
        _write_json(SKILL_BUNDLE_TARGET, skill_bundle, compact=True)
        project_bundle = build_project_bundle()
        _write_json(PROJECT_BUNDLE_TARGET, project_bundle, compact=True)

    _validate_written_files()
    print(
        json.dumps(
            {
                "langflow_version": importlib.metadata.version("langflow"),
                "lfx_version": importlib.metadata.version("lfx"),
                "parent_flow": str(FLOW_TARGET),
                "meeting_subflow": str(MEETING_SUBFLOW_TARGET),
                "skill_bundle": str(SKILL_BUNDLE_TARGET),
                "project_bundle": str(PROJECT_BUNDLE_TARGET),
                "parent_nodes": len(parent["data"]["nodes"]),
                "parent_edges": len(parent["data"]["edges"]),
                "direct_component_tools": 2,
                "run_flow_tools": 1,
                "skill_bundle_flows": len(skill_bundle["flows"]),
                "project_bundle_flows": len(project_bundle["flows"]),
                "status": "ok",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
