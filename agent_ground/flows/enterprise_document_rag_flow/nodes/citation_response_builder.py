from __future__ import annotations

"""Create the final Message and rebuild citations from authorized evidence."""

import json
import re
import unicodedata
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, Output
from lfx.schema.message import Message


ABSTENTION_MESSAGE = "확인 가능한 사내 문서 근거가 충분하지 않아 답변할 수 없습니다. 질문을 구체화하거나 문서 담당자에게 확인해 주세요."


def _payload(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return data
    text = getattr(value, "text", None) or getattr(value, "content", None)
    if isinstance(text, str):
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _safe(value: Any, limit: int = 240) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    return " ".join(text.split())[:limit]


def build_citation_message(answer_value: Any) -> str:
    answer = _payload(answer_value)
    if answer.get("supported") is not True:
        return ABSTENTION_MESSAGE
    answer_text = str(answer.get("answer_text") or "").strip()
    if not answer_text:
        return ABSTENTION_MESSAGE

    evidence_by_id: dict[str, dict[str, Any]] = {}
    for item in answer.get("evidence", []):
        if not isinstance(item, dict):
            continue
        evidence_id = str(item.get("evidence_id") or "").strip()
        source = item.get("source") if isinstance(item.get("source"), dict) else {}
        title = _safe(source.get("title"))
        page = _safe(source.get("page"), 40)
        locator = _safe(source.get("locator"), 180)
        if re.fullmatch(r"E\d+", evidence_id) and title and (page or locator):
            evidence_by_id[evidence_id] = item

    used_ids: list[str] = []
    raw_ids = answer.get("used_evidence_ids")
    if isinstance(raw_ids, list):
        for value in raw_ids:
            evidence_id = str(value or "").strip()
            if evidence_id in evidence_by_id and evidence_id not in used_ids:
                used_ids.append(evidence_id)

    # A supported answer without at least one server-verifiable citation is
    # unsafe.  Do not return the unsupported answer text.
    if not used_ids:
        return ABSTENTION_MESSAGE

    citation_number = {evidence_id: index for index, evidence_id in enumerate(used_ids, start=1)}
    for evidence_id, number in citation_number.items():
        answer_text = re.sub(rf"\[\s*{re.escape(evidence_id)}\s*\]", f"[{number}]", answer_text)
    # Unknown model-created evidence tokens are removed.  They can never create
    # a citation or expose a source not carried by the authorized answer Data.
    answer_text = re.sub(r"\[\s*E\d+\s*\]", "", answer_text)
    answer_text = re.sub(r"[ \t]+\n", "\n", answer_text).strip()

    if used_ids and not any(f"[{number}]" in answer_text for number in citation_number.values()):
        answer_text += " " + " ".join(f"[{citation_number[item]}]" for item in used_ids)

    citation_lines: list[str] = []
    for evidence_id in used_ids:
        item = evidence_by_id[evidence_id]
        source = item.get("source") if isinstance(item.get("source"), dict) else {}
        title = _safe(source.get("title") or "사내 문서")
        version = _safe(source.get("version"), 80)
        page = _safe(source.get("page"), 40)
        locator = _safe(source.get("locator"), 180)
        details = [title]
        if version:
            details.append(f"version {version}")
        if page:
            details.append(f"p.{page}")
        if locator and locator.lower() not in {f"page:{page}".lower(), f"p.{page}".lower()}:
            details.append(locator)
        citation_lines.append(f"[{citation_number[evidence_id]}] " + " · ".join(details))

    if citation_lines:
        return answer_text + "\n\n근거\n" + "\n".join(citation_lines)
    return answer_text


class CitationResponseBuilder(Component):
    display_name = "Citation Response Builder"
    description = "검증된 authorized evidence에서만 문서명·버전·페이지 Citation을 다시 만들어 최종 Message로 반환합니다."
    icon = "TextQuote"
    name = "CitationResponseBuilder"

    inputs = [DataInput(name="answer", display_name="Answer", input_types=["Data", "JSON"], required=True)]
    outputs = [Output(name="message", display_name="Message", method="build_message", types=["Message"])]

    def build_message(self) -> Message:
        answer = _payload(getattr(self, "answer", None))
        text = build_citation_message(answer)
        self.status = "citation response ready" if answer.get("used_evidence_ids") else "safe abstention ready"
        return Message(text=text)
