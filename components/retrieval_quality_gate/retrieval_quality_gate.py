from __future__ import annotations

"""Fail-closed evidence quality gate for the enterprise RAG query path."""

import json
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, Output
from lfx.schema.data import Data


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


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _evidence(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        evidence_id = str(item.get("evidence_id") or "").strip()
        content = str(item.get("content") or "").strip()
        if not evidence_id or not content or evidence_id in seen:
            continue
        try:
            score = max(0.0, min(1.0, float(item.get("score", 0.0))))
        except Exception:
            score = 0.0
        normalized = dict(item)
        normalized["evidence_id"] = evidence_id
        normalized["content"] = content[:6000]
        normalized["score"] = score
        result.append(normalized)
        seen.add(evidence_id)
    return result


def apply_quality_gate(retrieval_value: Any) -> dict[str, Any]:
    retrieval = _payload(retrieval_value)
    candidates = _evidence(retrieval.get("evidence"))
    security = _dict(retrieval.get("security"))

    top_score = max((float(item.get("score", 0.0)) for item in candidates), default=0.0)
    injection_signal = bool(security.get("injection_signal_detected"))
    retrieval_valid = bool(
        retrieval.get("success")
        and security.get("acl_applied_before_scoring") is True
        and security.get("denied_information_exposed") is False
    )
    allowed = bool(retrieval_valid and candidates and top_score >= 0.12 and not injection_signal)

    # Evidence is removed entirely on a failed gate so an accidentally wired
    # downstream LLM still cannot receive low-confidence or suspicious text.
    passed_evidence = candidates if allowed else []
    allowlist = [item["evidence_id"] for item in passed_evidence]
    return {
        "contract": "agent_ground.enterprise_rag.gate.v1",
        "success": True,
        "request_id": str(retrieval.get("request_id") or "")[:96],
        "question": str(retrieval.get("question") or "")[:8000],
        "allowed": allowed,
        "decision": "answer" if allowed else "abstain",
        # Deliberately identical for missing evidence, ACL denial, low score,
        # and document-injection detection.  End users cannot probe document
        # existence by comparing denial messages.
        "reason_code": "sufficient_authorized_evidence" if allowed else "insufficient_authorized_evidence",
        "abstention_message": ABSTENTION_MESSAGE,
        "evidence": passed_evidence,
        "citation_allowlist": allowlist,
        "quality": {
            "minimum_top_score": 0.12,
            "top_score": round(top_score, 6) if allowed else 0.0,
            "evidence_count": len(passed_evidence),
        },
        "security": {
            "acl_verified": bool(security.get("acl_applied_before_scoring")),
            "injection_free": not injection_signal,
            "fail_closed": True,
        },
        "errors": [],
        "warnings": [],
    }


class RetrievalQualityGate(Component):
    display_name = "05 검색 품질 판정"
    description = "ACL 적용 여부·검색 점수·문서 지시 신호를 확인하고 근거가 부족하면 LLM 앞에서 답변을 차단합니다."
    icon = "ShieldAlert"
    name = "RetrievalQualityGate"

    inputs = [DataInput(name="retrieval", display_name="검색 결과", input_types=["Data", "JSON"], required=True)]
    outputs = [Output(name="gate", display_name="품질 판정 결과", method="build_gate", types=["Data"])]

    def build_gate(self) -> Data:
        result = apply_quality_gate(getattr(self, "retrieval", None))
        self.status = "답변 생성 허용" if result["allowed"] else "근거 부족으로 답변 중단"
        return Data(data=result)
