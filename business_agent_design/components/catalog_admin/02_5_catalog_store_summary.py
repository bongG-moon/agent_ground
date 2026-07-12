from __future__ import annotations

from copy import deepcopy
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, Output
from lfx.schema.message import Message


def build_catalog_store_summary(store_result_value: Any) -> str:
    """카탈로그 검증/저장 결과를 Chat Output용 Markdown 메시지로 만듭니다."""
    payload = _payload(store_result_value)
    validation = _dict(payload.get("catalog_validation"))
    store_result = _dict(payload.get("store_result"))
    item_count = validation.get("item_count", len(_as_list(payload.get("catalog_items"))))

    lines = [
        "# 카탈로그 저장 결과",
        "",
        f"- 상태: {store_result.get('status', 'unknown')}",
        f"- 항목 수: {item_count}",
        f"- upsert: {store_result.get('upserted', 0)}",
        f"- matched: {store_result.get('matched', 0)}",
    ]
    if store_result.get("reason"):
        lines.append(f"- 사유: {store_result.get('reason')}")
    if validation.get("issues"):
        lines.extend(["", "## 검증 이슈"])
        lines.extend(f"- {issue}" for issue in _as_list(validation.get("issues"))[:20])
    else:
        lines.extend(["", "검증 이슈는 없습니다."])
    return "\n".join(lines)


def _payload(value: Any) -> dict[str, Any]:
    data = getattr(value, "data", value)
    return deepcopy(data) if isinstance(data, dict) else {}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, "", {}):
        return []
    return [value]


class CatalogStoreSummary(Component):
    display_name = "2.5 카탈로그 저장 결과 출력"
    description = "MongoDB 카탈로그 저장 결과를 사람이 읽기 쉬운 메시지로 출력합니다."
    icon = "MessageCircle"
    inputs = [DataInput(name="store_result", display_name="저장 결과", required=True)]
    outputs = [Output(name="summary_message", display_name="저장 결과 메시지", method="build_message")]

    def build_message(self) -> Message:
        text = build_catalog_store_summary(getattr(self, "store_result", None))
        self.status = {"요약 글자 수": len(text)}
        return Message(text=text)
