from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MessageTextInput, Output
from lfx.schema.data import Data


def normalize_catalog_items(
    catalog_source_value: Any,
    llm_catalog_response: Any = "",
    default_status: str = "draft",
) -> dict[str, Any]:
    """Agent/LLM 카탈로그 응답을 검증하고 MongoDB 저장 가능한 항목 배열로 정리합니다."""
    payload = _payload(catalog_source_value)
    source = _dict(payload.get("catalog_source"))
    parsed = _parse_json_like(llm_catalog_response)
    items = []
    if isinstance(parsed, dict):
        items = _as_list(parsed.get("items") or parsed.get("catalog_items"))
    elif isinstance(parsed, list):
        items = parsed

    source_mode = "llm"
    if not items:
        items = [_fallback_item(source.get("raw_catalog_text", ""))]
        source_mode = "fallback"

    normalized = []
    issues = []
    for item in items:
        row, row_issues = _normalize_item(_dict(item), default_status)
        normalized.append(row)
        issues.extend(row_issues)

    validation = {
        "valid": bool(normalized) and not issues,
        "issues": issues,
        "item_count": len(normalized),
        "source": source_mode,
        "validated_at": _now_iso(),
    }
    return {
        **payload,
        "catalog_items": normalized,
        "catalog_validation": validation,
    }


def _fallback_item(raw_text: str) -> dict[str, Any]:
    links = re.findall(r"https?://\S+", raw_text or "")
    return {
        "item_type": "capability",
        "canonical_key": _slug_key(raw_text[:80] or "catalog_item"),
        "title_ko": (raw_text or "카탈로그 항목")[:40],
        "summary_ko": (raw_text or "운영자가 입력한 카탈로그 항목입니다.")[:300],
        "categories": ["운영자 입력"],
        "trigger_signals": sorted(_tokens(raw_text))[:10],
        "recommended_when": [],
        "not_recommended_when": [],
        "langflow_building_blocks": [],
        "risk_level": "medium",
        "human_review_required": _needs_review(raw_text),
        "source_links": links,
    }


def _normalize_item(item: dict[str, Any], default_status: str) -> tuple[dict[str, Any], list[str]]:
    issues = []
    title = str(item.get("title_ko") or item.get("title") or "").strip()
    summary = str(item.get("summary_ko") or item.get("summary") or "").strip()
    if not title:
        title = "제목 미입력 항목"
        issues.append("title_ko가 없는 항목이 있습니다.")
    if not summary:
        summary = title
        issues.append(f"{title}: summary_ko가 없어 title로 대체했습니다.")

    item_type = str(item.get("item_type") or "capability").strip()
    if item_type not in {"capability", "case", "pattern"}:
        item_type = "capability"
        issues.append(f"{title}: item_type이 잘못되어 capability로 보정했습니다.")

    risk_level = str(item.get("risk_level") or "medium").strip()
    if risk_level not in {"low", "medium", "high"}:
        risk_level = "medium"
        issues.append(f"{title}: risk_level이 잘못되어 medium으로 보정했습니다.")

    canonical_key = str(item.get("canonical_key") or "").strip() or _slug_key(title)
    row = {
        "item_type": item_type,
        "canonical_key": canonical_key,
        "title_ko": title,
        "summary_ko": summary,
        "categories": _string_list(item.get("categories")),
        "trigger_signals": _string_list(item.get("trigger_signals"), 30),
        "recommended_when": _string_list(item.get("recommended_when")),
        "not_recommended_when": _string_list(item.get("not_recommended_when")),
        "langflow_building_blocks": _string_list(item.get("langflow_building_blocks")),
        "risk_level": risk_level,
        "human_review_required": bool(item.get("human_review_required")) or risk_level == "high",
        "source_links": _string_list(item.get("source_links"), 12),
        "status": str(default_status or "draft").strip() or "draft",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    if not row["trigger_signals"]:
        row["trigger_signals"] = sorted(_tokens(f"{title} {summary}"))[:12]
    return row, issues


def _parse_json_like(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    text = _extract_text(value).strip()
    if not text:
        return None
    if "```" in text:
        for block in re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.I | re.S):
            parsed = _parse_json_like(block)
            if parsed is not None:
                return parsed
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"(\{.*\}|\[.*\])", text, flags=re.S)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            return None
    return None


def _extract_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if hasattr(value, "text"):
        return str(value.text)
    if hasattr(value, "data"):
        return _extract_text(value.data)
    if isinstance(value, dict):
        for key in ("text", "message", "content", "input_value"):
            if key in value:
                return _extract_text(value[key])
        return json.dumps(value, ensure_ascii=False)
    return str(value)


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


def _string_list(value: Any, limit: int = 12) -> list[str]:
    result = []
    seen = set()
    for item in _as_list(value):
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[A-Za-z0-9가-힣_]+", text or "") if len(token) >= 2}


def _slug_key(text: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9가-힣_]+", str(text or "").lower())
    key = "_".join(tokens[:6])[:60]
    return key or f"catalog_{abs(hash(text)) % 100000}"


def _needs_review(text: str) -> bool:
    return any(key in str(text or "") for key in ["발송", "수정", "삭제", "승인", "개인정보", "외부", "등록"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class CatalogJsonNormalizer(Component):
    display_name = "2.3 카탈로그 JSON 검증"
    description = "Agent/LLM 카탈로그 응답을 검증하고 MongoDB 저장용 항목 배열로 정리합니다."
    icon = "ListChecks"
    inputs = [
        DataInput(name="catalog_source", display_name="카탈로그 원문 데이터", required=True),
        MessageTextInput(name="llm_catalog_response", display_name="Agent/LLM 카탈로그 응답", required=False),
        MessageTextInput(name="default_status", display_name="기본 저장 상태", value="draft", advanced=True),
    ]
    outputs = [Output(name="catalog_items", display_name="카탈로그 항목", method="build_payload")]

    def build_payload(self) -> Data:
        result = normalize_catalog_items(
            getattr(self, "catalog_source", None),
            getattr(self, "llm_catalog_response", ""),
            getattr(self, "default_status", "draft"),
        )
        validation = result.get("catalog_validation", {})
        self.status = {
            "처리 방식": validation.get("source"),
            "항목 수": validation.get("item_count"),
            "검증 상태": "정상" if validation.get("valid") else "확인 필요",
        }
        return Data(data=result)
