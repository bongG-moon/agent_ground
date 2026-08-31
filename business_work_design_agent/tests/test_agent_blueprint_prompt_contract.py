from __future__ import annotations

import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = PROJECT_ROOT / "prompts" / "agent_blueprint.md"


def _port_contract_example() -> dict[str, object]:
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"## Port/edge contract example\s+.*?```json\s*(\{.*?\})\s*```",
        prompt,
        flags=re.DOTALL,
    )
    assert match, "Agent Blueprint prompt must retain the executable port/edge example"
    return json.loads(match.group(1))


def test_agent_blueprint_prompt_requires_declared_port_contracts() -> None:
    prompt = PROMPT_PATH.read_text(encoding="utf-8")

    assert "`source_port_id`는 반드시 source node의 `outputs[].port_id`" in prompt
    assert "`target_port_id`는 반드시 target node의 `inputs[].port_id`" in prompt
    assert "빈 문자열(`\"\"`)이나 공백 문자열은 절대 사용하지 않는다" in prompt
    assert "`source_port_id: null`, `target_port_id: null`" in prompt
    assert "`connection_validation_status: \"unverified\"`" in prompt


def test_agent_blueprint_prompt_example_connects_declared_ports() -> None:
    example = _port_contract_example()
    nodes = example["nodes"]
    edges = example["edges"]
    assert isinstance(nodes, list)
    assert isinstance(edges, list)

    inputs = {
        str(node["node_id"]): {str(port["port_id"]) for port in node["inputs"]}
        for node in nodes
        if isinstance(node, dict)
    }
    outputs = {
        str(node["node_id"]): {str(port["port_id"]) for port in node["outputs"]}
        for node in nodes
        if isinstance(node, dict)
    }
    for edge in edges:
        assert isinstance(edge, dict)
        source_port_id = edge["source_port_id"]
        target_port_id = edge["target_port_id"]
        assert isinstance(source_port_id, str) and source_port_id.strip()
        assert isinstance(target_port_id, str) and target_port_id.strip()
        assert source_port_id in outputs[edge["source_node_id"]]
        assert target_port_id in inputs[edge["target_node_id"]]
