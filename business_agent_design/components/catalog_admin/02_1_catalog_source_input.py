from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.data import Data


def build_catalog_source(raw_catalog_text: Any, operator_note: Any = "") -> dict[str, Any]:
    """운영자가 자연어로 입력한 기능/사례 설명을 카탈로그 등록 요청으로 묶습니다."""
    raw_text = str(raw_catalog_text or "").strip()
    note = str(operator_note or "").strip()
    return {
        "catalog_source": {
            "source_id": _stable_id("catalog_source", raw_text or _now_iso()),
            "raw_catalog_text": raw_text,
            "operator_note": note,
            "created_at": _now_iso(),
        },
        "catalog_items": [],
        "catalog_validation": {},
        "store_result": {},
    }


def _stable_id(prefix: str, text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class CatalogSourceInput(Component):
    display_name = "2.1 카탈로그 원문 입력"
    description = "사용 가능한 기능 목록이나 기존 개선 사례를 자연어 원문으로 입력합니다."
    icon = "FileText"
    inputs = [
        MessageTextInput(
            name="raw_catalog_text",
            display_name="카탈로그 원문",
            info="기능 목록, 개선 사례, 사용 조건, 참고 링크를 자연스럽게 적습니다.",
            required=True,
            tool_mode=True,
        ),
        MessageTextInput(
            name="operator_note",
            display_name="운영자 메모",
            required=False,
            advanced=True,
        ),
    ]
    outputs = [Output(name="catalog_source", display_name="카탈로그 원문 데이터", method="build_payload")]

    def build_payload(self) -> Data:
        result = build_catalog_source(
            getattr(self, "raw_catalog_text", ""),
            getattr(self, "operator_note", ""),
        )
        source = result.get("catalog_source", {})
        self.status = {
            "원문 글자 수": len(source.get("raw_catalog_text", "")),
            "Source ID": source.get("source_id"),
        }
        return Data(data=result)
