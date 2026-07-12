from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, Output
from lfx.schema.message import Message


def build_catalog_template_variables(catalog_source_value: Any) -> dict[str, str]:
    """Langflow 기본 Prompt Template에 연결할 카탈로그 변환 변수들을 만듭니다."""
    payload = _payload(catalog_source_value)
    source = _dict(payload.get("catalog_source"))
    instructions = "\n".join(
        [
            "- 기능, 개선 사례, 안전 패턴을 구분해 item_type을 capability, case, pattern 중 하나로 지정하세요.",
            "- 실제 참고 링크가 원문에 있으면 source_links에 보존하세요.",
            "- 자동 발송, 시스템 변경, 승인, 개인정보 처리와 관련되면 human_review_required를 true로 두세요.",
            "- trigger_signals에는 업무 설명과 매칭될 만한 짧은 키워드를 넣으세요.",
            "- JSON 외의 설명 문장은 반환하지 마세요.",
        ]
    )
    return {
        "raw_catalog_text": str(source.get("raw_catalog_text") or ""),
        "operator_note": str(source.get("operator_note") or ""),
        "catalog_instructions": instructions,
        "catalog_output_schema": json.dumps(_catalog_schema(), ensure_ascii=False, indent=2),
    }


def _catalog_schema() -> dict[str, Any]:
    return {
        "items": [
            {
                "item_type": "capability | case | pattern",
                "canonical_key": "영문/숫자/언더스코어 기반 고유 키",
                "title_ko": "항목 제목",
                "summary_ko": "항목 설명",
                "categories": ["분류"],
                "trigger_signals": ["업무 설명 매칭 키워드"],
                "recommended_when": ["추천 조건"],
                "not_recommended_when": ["비추천 조건"],
                "langflow_building_blocks": ["Langflow 구성 요소"],
                "risk_level": "low | medium | high",
                "human_review_required": False,
                "source_links": ["https://..."],
            }
        ]
    }


def _payload(value: Any) -> dict[str, Any]:
    data = getattr(value, "data", value)
    return deepcopy(data) if isinstance(data, dict) else {}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


class CatalogJsonPromptBuilder(Component):
    display_name = "2.2 카탈로그 JSON 프롬프트 변수 준비"
    description = "Langflow 기본 Prompt Template에 연결할 카탈로그 원문, 운영자 메모, 변환 지침, 출력 스키마 변수를 제공합니다."
    icon = "Braces"
    inputs = [DataInput(name="catalog_source", display_name="카탈로그 원문 데이터", required=True)]
    outputs = [
        Output(name="raw_catalog_text", display_name="카탈로그 원문", method="build_raw_catalog_text", types=["Message"], group_outputs=True),
        Output(name="operator_note", display_name="운영자 메모", method="build_operator_note", types=["Message"], group_outputs=True),
        Output(name="catalog_instructions", display_name="카탈로그 변환 지침", method="build_catalog_instructions", types=["Message"], group_outputs=True),
        Output(name="catalog_output_schema", display_name="출력 스키마 JSON", method="build_catalog_output_schema", types=["Message"], group_outputs=True),
    ]

    def build_raw_catalog_text(self) -> Message:
        variables = build_catalog_template_variables(getattr(self, "catalog_source", None))
        self.status = {"Prompt Template 변수": "카탈로그 원문 / 운영자 메모 / 변환 지침 / 출력 스키마"}
        return Message(text=variables["raw_catalog_text"])

    def build_operator_note(self) -> Message:
        variables = build_catalog_template_variables(getattr(self, "catalog_source", None))
        self.status = {"Prompt Template 변수": "카탈로그 원문 / 운영자 메모 / 변환 지침 / 출력 스키마"}
        return Message(text=variables["operator_note"])

    def build_catalog_instructions(self) -> Message:
        variables = build_catalog_template_variables(getattr(self, "catalog_source", None))
        self.status = {"Prompt Template 변수": "카탈로그 원문 / 운영자 메모 / 변환 지침 / 출력 스키마"}
        return Message(text=variables["catalog_instructions"])

    def build_catalog_output_schema(self) -> Message:
        variables = build_catalog_template_variables(getattr(self, "catalog_source", None))
        self.status = {"Prompt Template 변수": "카탈로그 원문 / 운영자 메모 / 변환 지침 / 출력 스키마"}
        return Message(text=variables["catalog_output_schema"])
