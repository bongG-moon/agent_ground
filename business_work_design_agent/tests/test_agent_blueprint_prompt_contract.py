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


def _generation_contract_example() -> dict[str, object]:
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"## New standalone generation contract\s+.*?예시:\s*```json\s*(\{.*?\})\s*```\s*## Input variables",
        prompt,
        flags=re.DOTALL,
    )
    assert match, "Agent Blueprint prompt must retain the standalone generation contract example"
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


def test_agent_blueprint_prompt_defines_declaration_only_secret_inputs() -> None:
    prompt = PROMPT_PATH.read_text(encoding="utf-8")

    assert "`name`, `ref`, `port_id`, `required`, `configured`" in prompt
    assert "**string이 아닌 JSON object**" in prompt
    assert "`name`, `ref`, `port_id` 중 **적어도 하나는 비어 있지 않은 string**" in prompt
    assert "`value`, `type`, `description`" in prompt
    assert "실제 인증 값, 예시 값, 마스킹한 값도 포함하지 않는다" in prompt
    assert '{"secret_inputs": ["OUTLOOK_AUTH"]}' in prompt
    assert '"value": "[REDACTED]"' in prompt
    assert '"type": "SecretStr"' in prompt
    assert '"description": "Outlook authentication secret"' in prompt


def test_agent_blueprint_prompt_generation_contract_has_valid_nonempty_secret_example() -> None:
    example = _generation_contract_example()
    secret_inputs = example["secret_inputs"]

    assert isinstance(secret_inputs, list)
    assert secret_inputs
    declaration = secret_inputs[0]
    assert isinstance(declaration, dict)
    assert set(declaration) == {"name", "ref", "port_id", "required", "configured"}
    assert all(isinstance(declaration[key], str) and declaration[key].strip() for key in ("name", "ref", "port_id"))
    assert declaration["required"] is True
    assert declaration["configured"] is False
