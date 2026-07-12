from __future__ import annotations

"""Validate an optional LLM answer against the gate's authorized evidence.

Malformed, unsupported, or citation-free model output is replaced with a
deterministic evidence excerpt.  Raw LLM source metadata is never accepted.
"""

import json
import re
import unicodedata
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MessageTextInput, Output
from lfx.schema.data import Data


def _payload(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return data
    text = _text(value)
    if text:
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        for key in ("answer", "text", "content", "output"):
            if isinstance(data.get(key), str):
                return data[key].strip()
    for attr in ("text", "content"):
        candidate = getattr(value, attr, None)
        if isinstance(candidate, str):
            return candidate.strip()
    return str(value).strip()


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _parse_llm(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    data = getattr(value, "data", None)
    if isinstance(data, dict) and any(key in data for key in ("answer", "used_evidence_ids", "unsupported")):
        return data
    raw = _text(value)
    if not raw:
        return {}
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE)
    candidates = [cleaned]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if 0 <= start < end:
        candidates.append(cleaned[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue
    ids = re.findall(r"\bE\d+\b", raw)
    return {"answer": raw, "used_evidence_ids": ids, "unsupported": False}


def _tokens(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", text).lower()
    stop = {"그리고", "그러나", "대한", "관련", "입니다", "합니다", "있습니다", "the", "and", "that", "with"}
    return {token for token in re.findall(r"[a-z0-9가-힣_]{2,}", normalized) if token not in stop}


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"(?<![A-Za-z])\d+(?:[.,]\d+)?%?", text))


def _safe_llm_answer(answer: str, evidence_text: str) -> bool:
    if not answer or len(answer) > 4000:
        return False
    lowered = unicodedata.normalize("NFKC", answer).lower()
    if any(marker in lowered for marker in ("ignore previous", "system prompt", "developer message", "<script", "시스템 프롬프트")):
        return False
    answer_without_ids = re.sub(r"\[?E\d+\]?", " ", answer)
    answer_tokens = _tokens(answer_without_ids)
    evidence_tokens = _tokens(evidence_text)
    if not answer_tokens or not evidence_tokens:
        return False
    overlap = len(answer_tokens & evidence_tokens) / max(len(answer_tokens), 1)
    if overlap < 0.16:
        return False
    # Numbers are high-risk factual claims.  Every number in a model answer
    # must occur verbatim in at least one authorized evidence chunk.
    if not _numbers(answer_without_ids).issubset(_numbers(evidence_text)):
        return False
    return True


def _deterministic_answer(evidence: list[dict[str, Any]]) -> tuple[str, list[str]]:
    bullets: list[str] = []
    used: list[str] = []
    for item in evidence[:2]:
        evidence_id = str(item.get("evidence_id") or "")
        content = " ".join(str(item.get("content") or "").split())[:420]
        if evidence_id and content:
            bullets.append(f"- {content} [{evidence_id}]")
            used.append(evidence_id)
    if not bullets:
        return "확인 가능한 사내 문서 근거가 충분하지 않아 답변할 수 없습니다. 질문을 구체화하거나 문서 담당자에게 확인해 주세요.", []
    return "확인된 사내 문서 근거는 다음과 같습니다.\n" + "\n".join(bullets), used


def build_grounded_answer(gate_value: Any, llm_value: Any = "") -> dict[str, Any]:
    gate = _payload(gate_value)
    abstention = str(
        gate.get("abstention_message")
        or "확인 가능한 사내 문서 근거가 충분하지 않아 답변할 수 없습니다. 질문을 구체화하거나 문서 담당자에게 확인해 주세요."
    )
    if not gate.get("allowed"):
        return {
            "contract": "agent_ground.enterprise_rag.answer.v1",
            "success": True,
            "supported": False,
            "answer_text": abstention,
            "used_evidence_ids": [],
            "evidence": [],
            "answer_mode": "abstention",
        }

    evidence = [item for item in gate.get("evidence", []) if isinstance(item, dict)]
    allowed_by_id = {
        str(item.get("evidence_id")): item
        for item in evidence
        if str(item.get("evidence_id") or "") in set(str(value) for value in gate.get("citation_allowlist", []))
    }
    parsed = _parse_llm(llm_value)
    raw_ids = parsed.get("used_evidence_ids")
    if isinstance(raw_ids, str):
        raw_ids = re.findall(r"\bE\d+\b", raw_ids)
    if not isinstance(raw_ids, list):
        raw_ids = []
    # The model can only narrow the server-created allowlist; it cannot add a
    # source by naming an arbitrary ID.
    used_ids: list[str] = []
    for value in raw_ids:
        evidence_id = str(value or "").strip()
        if evidence_id in allowed_by_id and evidence_id not in used_ids:
            used_ids.append(evidence_id)

    candidate_answer = str(parsed.get("answer") or "").strip()
    candidate_answer = re.sub(r"\[?E\d+\]?", "", candidate_answer).strip()
    selected_text = "\n".join(str(allowed_by_id[item].get("content") or "") for item in used_ids)
    llm_is_grounded = bool(
        used_ids
        and not _bool(parsed.get("unsupported"))
        and _safe_llm_answer(candidate_answer, selected_text)
    )

    if llm_is_grounded:
        answer_text = candidate_answer
        # Evidence markers are reintroduced from the intersected IDs, not from
        # arbitrary model text.  The citation component will map them to [1].
        answer_text += " " + " ".join(f"[{item}]" for item in used_ids)
        mode = "llm_grounded"
    else:
        answer_text, used_ids = _deterministic_answer(evidence)
        mode = "deterministic_fallback"

    selected = [allowed_by_id[item] for item in used_ids if item in allowed_by_id]
    return {
        "contract": "agent_ground.enterprise_rag.answer.v1",
        "success": True,
        "supported": bool(selected),
        "answer_text": answer_text,
        "used_evidence_ids": used_ids,
        "evidence": selected,
        "answer_mode": mode,
    }


class GroundedAnswerBuilder(Component):
    display_name = "Grounded Answer Builder"
    description = "LLM의 근거 ID를 서버 allowlist와 교차 검증하고, 부적합 응답은 허용 근거의 deterministic 답변으로 대체합니다."
    icon = "BadgeCheck"
    name = "GroundedAnswerBuilder"

    inputs = [
        DataInput(name="gate", display_name="Gate", input_types=["Data", "JSON"], required=True),
        MessageTextInput(name="llm_response", display_name="LLM Response", required=False),
    ]
    outputs = [Output(name="answer", display_name="Answer", method="build_answer", types=["Data"])]

    def build_answer(self) -> Data:
        result = build_grounded_answer(getattr(self, "gate", None), getattr(self, "llm_response", ""))
        self.status = result["answer_mode"]
        return Data(data=result)
