from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, Output
from lfx.schema.message import Message


def build_profile_template_variables(business_request_value: Any) -> dict[str, str]:
    """Langflow 기본 Prompt Template에 연결할 변수들을 만듭니다."""
    payload = _payload(business_request_value)
    request = _dict(payload.get("business_request"))
    work_description = str(request.get("work_description") or "").strip()

    instructions = "\n".join(
        [
            "- 사용자가 항목을 나누어 쓰지 않아도 목적, 현재 절차, 데이터/시스템, 제약, 원하는 결과를 추론하세요.",
            "- 확실하지 않은 내용은 invented detail로 만들지 말고 assumptions 또는 open_questions에 적으세요.",
            "- AI Agent 설계에 중요한 위험 신호를 risk_signals에 넣으세요. 예: 자동 발송, 시스템 쓰기, 승인, 개인정보, 외부 공유.",
            "- 초보 Langflow 개발자가 이해할 수 있도록 단계명과 시스템명을 짧고 명확한 한국어로 작성하세요.",
            "- JSON 외의 설명 문장은 반환하지 마세요.",
        ]
    )

    return {
        "work_description": work_description,
        "profile_instructions": instructions,
        "profile_output_schema": json.dumps(_profile_schema(), ensure_ascii=False, indent=2),
    }


def _profile_schema() -> dict[str, Any]:
    return {
        "business_profile": {
            "business_goal": "업무의 목적",
            "current_flow": [
                {
                    "step_id": "S1",
                    "title": "현재 업무 단계명",
                    "description": "무엇을 하는지",
                    "actor": "사람 또는 조직",
                    "systems": ["사용 시스템"],
                    "data": ["사용 데이터"],
                }
            ],
            "data_and_systems": [
                {"name": "데이터 또는 시스템명", "role": "업무에서의 용도"}
            ],
            "constraints": ["제약 또는 반드시 지켜야 하는 규칙"],
            "desired_outputs": ["원하는 산출물"],
            "risk_signals": ["자동화 시 주의할 위험 신호"],
            "assumptions": ["추정한 내용"],
            "open_questions": ["추가 확인이 필요한 질문"],
        }
    }


def _payload(value: Any) -> dict[str, Any]:
    data = getattr(value, "data", value)
    return deepcopy(data) if isinstance(data, dict) else {}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


class BusinessProfilePromptBuilder(Component):
    display_name = "01 업무 구조화 프롬프트 변수 준비"
    description = "Langflow 기본 Prompt Template에 연결할 업무 설명, 작성 지침, 출력 스키마 변수를 제공합니다."
    icon = "Braces"
    inputs = [DataInput(name="business_request", display_name="업무 요청", required=True)]
    outputs = [
        Output(name="work_description", display_name="업무 설명", method="build_work_description", types=["Message"], group_outputs=True),
        Output(name="profile_instructions", display_name="구조화 지침", method="build_profile_instructions", types=["Message"], group_outputs=True),
        Output(name="profile_output_schema", display_name="출력 스키마 JSON", method="build_profile_output_schema", types=["Message"], group_outputs=True),
    ]

    def build_work_description(self) -> Message:
        variables = build_profile_template_variables(getattr(self, "business_request", None))
        self.status = {"Prompt Template 변수": "업무 설명 / 구조화 지침 / 출력 스키마 JSON"}
        return Message(text=variables["work_description"])

    def build_profile_instructions(self) -> Message:
        variables = build_profile_template_variables(getattr(self, "business_request", None))
        self.status = {"Prompt Template 변수": "업무 설명 / 구조화 지침 / 출력 스키마 JSON"}
        return Message(text=variables["profile_instructions"])

    def build_profile_output_schema(self) -> Message:
        variables = build_profile_template_variables(getattr(self, "business_request", None))
        self.status = {"Prompt Template 변수": "업무 설명 / 구조화 지침 / 출력 스키마 JSON"}
        return Message(text=variables["profile_output_schema"])
