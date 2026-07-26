from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.data import Data


def build_business_request(work_description: Any) -> dict[str, Any]:
    """사용자가 입력한 업무 설명 한 칸을 이후 노드들이 공통으로 쓰는 요청 데이터로 바꿉니다."""
    description = str(work_description or "").strip()
    request_id = _stable_id("business_request", description or _now_iso())
    return {
        "business_request": {
            "request_id": request_id,
            "work_description": description,
            "created_at": _now_iso(),
            "input_mode": "single_natural_language_text",
        },
        "workflow_profile": {},
        "catalog_context": {},
        "agent_design": {},
        "html_result": {},
        "trace": {
            "warnings": [] if description else ["업무 설명이 비어 있습니다."],
            "errors": [],
        },
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stable_id(prefix: str, text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


class BusinessWorkInputLoader(Component):
    display_name = "00 업무 설명 입력"
    description = "사용자가 자연어로 적은 업무 설명을 Flow 전체에서 사용할 표준 요청 데이터로 변환합니다."
    icon = "FileInput"
    inputs = [
        MessageTextInput(
            name="work_description",
            display_name="업무 설명",
            info="현재 하는 업무, 사용하는 데이터/시스템, 제약, 원하는 결과를 자연스럽게 적습니다.",
            required=True,
            tool_mode=True,
        )
    ]
    outputs = [Output(name="business_request", display_name="업무 요청", method="build_payload")]

    def build_payload(self) -> Data:
        result = build_business_request(getattr(self, "work_description", ""))
        request = result.get("business_request", {})
        self.status = {
            "요청 ID": request.get("request_id"),
            "입력 글자 수": len(request.get("work_description", "")),
        }
        return Data(data=result)
