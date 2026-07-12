from __future__ import annotations

import json
from typing import Any

# 이 component는 Smart Router나 Structured Output이 만든 route 결정을
# Route Gate가 읽을 수 있는 사내 표준 payload로 정리하는 작은 adapter입니다.
from lfx.custom import Component
from lfx.io import BoolInput, DataInput, DropdownInput, MessageTextInput, Output
from lfx.schema import Data


VALID_ROUTES = {"data_retrieval", "document_rag", "time_answer", "final_answer"}


def _payload_from_value(value: Any) -> dict[str, Any]:
    # Smart Router/Structured Output/Parser의 결과는 Data, JSON dict, Message 문자열 등으로 들어올 수 있습니다.
    # Route Gate 앞에서는 어떤 형태든 dict로 통일해야 연결 실수가 줄어듭니다.
    if value is None:
        return {}
    if isinstance(value, dict):
        return value

    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return data

    text = getattr(value, "text", None) or getattr(value, "content", None)
    if isinstance(value, str):
        text = value
    if isinstance(text, str) and text.strip():
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {"items": parsed}
        except Exception:
            return {"text": text}

    return {}


def _normalize_route(route: Any) -> str:
    # LLM이 "document", "rag", "time"처럼 짧게 내보내도 표준 route로 맞춥니다.
    value = str(route or "").strip().lower()
    aliases = {
        "data": "data_retrieval",
        "retrieval": "data_retrieval",
        "table": "data_retrieval",
        "csv": "data_retrieval",
        "document": "document_rag",
        "docs": "document_rag",
        "rag": "document_rag",
        "time": "time_answer",
        "date": "time_answer",
        "calendar": "time_answer",
        "answer": "final_answer",
        "fallback": "final_answer",
    }
    value = aliases.get(value, value)
    return value if value in VALID_ROUTES else "final_answer"


def _keyword_route(request_text: str) -> str:
    # Smart Router/LLM 없이도 실습할 수 있도록 아주 작은 fallback rule을 둡니다.
    # 운영에서는 이 rule만 믿기보다 Structured Output 또는 Smart Router 결과를 우선 사용합니다.
    lowered = request_text.lower()
    if any(word in lowered for word in ["오늘", "현재", "지금", "마감", "이번 주", "날짜", "시간"]):
        return "time_answer"
    if any(word in lowered for word in ["pdf", "문서", "규정", "가이드", "rag", "매뉴얼"]):
        return "document_rag"
    if any(word in lowered for word in ["csv", "lot", "불량", "defect", "row", "표", "데이터"]):
        return "data_retrieval"
    return "final_answer"


class SmartRoutePayloadBuilder(Component):
    display_name = "Smart Route Payload Builder"
    description = "Normalize Smart Router or Structured Output decisions into a Route Gate payload."
    icon = "GitBranch"
    name = "SmartRoutePayloadBuilder"

    inputs = [
        MessageTextInput(
            name="request_text",
            display_name="Request Text",
            info="사용자 질문 원문입니다. Chat Input.Message 또는 Agent tool argument로 채울 수 있습니다.",
            value="",
            tool_mode=True,
        ),
        DataInput(
            name="router_decision",
            display_name="Router Decision",
            info="Smart Router 또는 Structured Output이 만든 route JSON/Data입니다. 예: {\"route\":\"document_rag\"}",
            input_types=["Data", "JSON", "Message"],
            required=False,
        ),
        DropdownInput(
            name="default_route",
            display_name="Default Route",
            info="route가 없거나 해석할 수 없을 때 사용할 fallback route입니다.",
            options=["final_answer", "data_retrieval", "document_rag", "time_answer"],
            value="final_answer",
            advanced=True,
        ),
        BoolInput(
            name="use_keyword_fallback",
            display_name="Use Keyword Fallback",
            info="router_decision이 비어 있을 때 질문 키워드로 route를 추정합니다. 초보 실습용으로 켜 두면 편합니다.",
            value=True,
            advanced=True,
        ),
    ]

    outputs = [
        Output(
            name="route_payload",
            display_name="Route Payload",
            method="build_route_payload",
            types=["Data"],
        )
    ]

    def build_route_payload(self) -> Data:
        request_text = str(getattr(self, "request_text", "") or "").strip()
        decision = _payload_from_value(getattr(self, "router_decision", None))

        # 우선순위는 1) Smart Router/Structured Output 결과, 2) 키워드 fallback, 3) default_route입니다.
        raw_route = decision.get("route") or decision.get("selected_route") or decision.get("category")
        if raw_route:
            route = _normalize_route(raw_route)
            route_source = "router_decision"
        elif bool(getattr(self, "use_keyword_fallback", True)):
            route = _keyword_route(request_text)
            route_source = "keyword_fallback"
        else:
            route = _normalize_route(getattr(self, "default_route", "final_answer"))
            route_source = "default_route"

        result = {
            "success": True,
            "route": route,
            "request_text": request_text,
            "payload": {
                "router_decision": decision,
                "route_source": route_source,
            },
            "errors": [],
            "warnings": [],
        }

        # status에는 판단된 route와 출처만 남겨 UI에서 빠르게 확인합니다.
        self.status = f"route={route} ({route_source})"
        return Data(data=result, text=json.dumps(result, ensure_ascii=False, default=str))
