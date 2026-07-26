from __future__ import annotations

"""04 Agent design prompt variables.

Standalone Langflow component: it intentionally imports no sibling module.  The
component gives the LLM a graph contract, while the following normalizer remains
the final authority for IDs, edges, change states, and safe fallback output.
"""

import json
from copy import deepcopy
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, Output
from lfx.schema.message import Message


NODE_TYPES = ["start", "process", "decision", "merge", "human_review", "end"]
CHANGE_STATES = ["unchanged", "modified", "added", "human_review", "removed"]


def build_agent_design_template_variables(catalog_context_value: Any) -> dict[str, str]:
    payload = _payload(catalog_context_value)
    context = _dict(payload.get("catalog_context"))
    profile = _dict(context.get("business_profile")) or _dict(payload.get("workflow_profile"))
    items = _list(context.get("ranked_catalog_items"))
    trace = _dict(context.get("recommendation_trace"))

    schema = {
        "report_title": "string",
        "executive_summary": "string",
        "flow_visualization": {
            "before": {
                "nodes": [
                    {
                        "node_id": "before_step_id",
                        "comparison_key": "stable_business_step_key",
                        "label": "string",
                        "description": "string",
                        "actor": "string",
                        "node_type": NODE_TYPES,
                        "change_state": CHANGE_STATES,
                        "recommended_asset_ids": ["catalog canonical_key only"],
                        "improvement_detail_id": "string or empty",
                    }
                ],
                "edges": [
                    {
                        "edge_id": "string",
                        "source": "existing node_id",
                        "target": "existing node_id",
                        "branch_label": "required on decision outgoing edges",
                        "condition": "required on decision outgoing edges",
                        "change_state": CHANGE_STATES,
                    }
                ],
            },
            "after": {"nodes": ["same node schema"], "edges": ["same edge schema"]},
            "change_map": [
                {
                    "comparison_key": "string",
                    "before_node_id": "string or empty",
                    "after_node_id": "string or empty",
                    "change_state": CHANGE_STATES,
                    "summary": "string",
                }
            ],
        },
        "improvement_details": [
            {
                "improvement_detail_id": "string",
                "title": "string",
                "why_change": "string",
                "how_to_improve": "string",
                "recommended_asset_ids": ["catalog canonical_key only"],
                "connection_guidance": ["specific node and port wiring"],
                "acceptance_checks": ["observable test"],
                "human_review": "string",
                "recommendation_trace_ids": ["trace id"],
            }
        ],
        "recommended_capabilities": [
            {
                "catalog_id": "canonical_key from supplied catalog only",
                "title": "string",
                "why_recommended": "string",
                "applies_to_steps": ["comparison_key"],
                "building_blocks": ["string"],
                "source_links": ["string"],
                "risk_level": "low|medium|high",
                "human_review_required": "boolean",
            }
        ],
        "implementation_roadmap": [
            {"phase": "string", "goal": "string", "tasks": ["string"], "success_check": "string"}
        ],
        "risk_controls": [
            {"risk": "string", "control": "string", "human_checkpoint": "string"}
        ],
        "assumptions": ["string"],
        "questions_to_confirm": ["string"],
        "unverified_suggestions": ["catalog 밖의 아이디어만 분리"],
    }

    instructions = """
업무의 현재 상태와 개선 후 상태를 카드 목록이 아닌 두 개의 방향성 있는 Flow Chart로 설계하세요.

필수 규칙:
1. before와 after 각각 start, end node와 유효한 edge를 포함합니다.
2. 조건 판단은 decision node로 만들고, decision에서 나가는 모든 edge에 branch_label과 condition을 씁니다.
3. 분기 경로가 다시 합쳐지면 merge node를 명시합니다.
4. BEFORE/AFTER의 같은 업무 단계에는 같은 comparison_key를 씁니다.
5. AFTER의 변경 node는 modified, added, human_review 중 하나를 사용하고 improvement_detail_id를 연결합니다.
6. BEFORE에서 사라지는 수작업은 removed로 표시합니다. 유지 단계는 unchanged입니다.
7. 추천 자산 ID는 제공된 catalog_items의 canonical_key만 사용합니다. 없는 기능은 unverified_suggestions에만 씁니다.
8. 외부 발송, 승인, 시스템 쓰기/삭제에는 human_review node와 risk control을 둡니다.
9. 각 improvement detail에는 왜 바꾸는지, 구체적 구현 방법, 연결 순서, 성공 확인 기준을 씁니다.
10. HTML/CSS/JavaScript나 좌표를 생성하지 말고 JSON object만 반환합니다.
11. 모르는 내용은 assumptions 또는 questions_to_confirm에 두고 사실처럼 단정하지 않습니다.
""".strip()

    return {
        "business_profile_json": _json(profile),
        "catalog_items_json": _json(items),
        "recommendation_trace_json": _json(trace),
        "design_instructions": instructions,
        "design_output_schema": _json(schema),
    }


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return deepcopy(value)
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return deepcopy(data)
    text = getattr(value, "text", None) or getattr(value, "content", None)
    if isinstance(text, str):
        try:
            parsed = json.loads(text)
        except Exception:
            return {}
        return deepcopy(parsed) if isinstance(parsed, dict) else {}
    return {}


def _dict(value: Any) -> dict[str, Any]:
    return deepcopy(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


class AgentDesignPromptBuilder(Component):
    display_name = "04 AI Agent 설계 프롬프트 변수 준비"
    description = "업무 구조와 카탈로그를 BEFORE/AFTER graph JSON 설계용 Prompt 변수로 변환합니다."
    icon = "Workflow"
    inputs = [DataInput(name="catalog_context", display_name="추천 컨텍스트", required=True)]
    outputs = [
        Output(name="business_profile_json", display_name="업무 프로필 JSON", method="build_business_profile_json", types=["Message"], group_outputs=True),
        Output(name="catalog_items_json", display_name="추천 카탈로그 JSON", method="build_catalog_items_json", types=["Message"], group_outputs=True),
        Output(name="recommendation_trace_json", display_name="추천 근거 JSON", method="build_recommendation_trace_json", types=["Message"], group_outputs=True),
        Output(name="design_instructions", display_name="설계 지침", method="build_design_instructions", types=["Message"], group_outputs=True),
        Output(name="design_output_schema", display_name="출력 스키마 JSON", method="build_design_output_schema", types=["Message"], group_outputs=True),
    ]

    def _variables(self) -> dict[str, str]:
        cached = getattr(self, "_cached_variables", None)
        if isinstance(cached, dict):
            return cached
        result = build_agent_design_template_variables(getattr(self, "catalog_context", None))
        self._cached_variables = result
        self.status = {"카탈로그 항목": len(json.loads(result["catalog_items_json"])), "출력 방식": "BEFORE/AFTER graph JSON"}
        return result

    def build_business_profile_json(self) -> Message:
        return Message(text=self._variables()["business_profile_json"])

    def build_catalog_items_json(self) -> Message:
        return Message(text=self._variables()["catalog_items_json"])

    def build_recommendation_trace_json(self) -> Message:
        return Message(text=self._variables()["recommendation_trace_json"])

    def build_design_instructions(self) -> Message:
        return Message(text=self._variables()["design_instructions"])

    def build_design_output_schema(self) -> Message:
        return Message(text=self._variables()["design_output_schema"])
