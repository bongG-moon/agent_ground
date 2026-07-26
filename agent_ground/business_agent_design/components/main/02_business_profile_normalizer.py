from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MessageTextInput, Output
from lfx.schema.data import Data


def normalize_business_profile(business_request_value: Any, llm_profile_response: Any = "") -> dict[str, Any]:
    """Agent/LLM 구조화 응답을 검증하고, 없으면 업무 설명 기반 fallback 프로필을 만듭니다."""
    payload = _payload(business_request_value)
    request = _dict(payload.get("business_request"))
    work_description = str(request.get("work_description") or "").strip()

    parsed = _parse_json_like(llm_profile_response)
    profile = _dict(parsed.get("business_profile")) if isinstance(parsed, dict) else {}
    source = "llm" if profile else "fallback"
    if not profile:
        profile = _fallback_profile(work_description)

    normalized = _normalize_profile(profile, work_description)
    issues = _validation_issues(normalized)
    trace = _dict(payload.get("trace"))
    warnings = list(trace.get("warnings") or [])
    warnings.extend(issues)

    return {
        **payload,
        "workflow_profile": normalized,
        "profile_validation": {
            "valid": len(issues) == 0,
            "issues": issues,
            "source": source,
            "normalized_at": _now_iso(),
        },
        "trace": {
            **trace,
            "warnings": warnings,
        },
    }


def _fallback_profile(text: str) -> dict[str, Any]:
    sentences = _sentences(text)
    steps = []
    for index, sentence in enumerate(sentences[:8], 1):
        steps.append(
            {
                "step_id": f"S{index}",
                "title": _short_title(sentence, f"현재 업무 {index}"),
                "description": sentence,
                "actor": "업무 담당자",
                "systems": _keyword_hits(sentence, ["엑셀", "메일", "ERP", "MES", "SQL", "DB", "대시보드", "Slack", "Teams"]),
                "data": _keyword_hits(sentence, ["생산", "불량", "재공", "품질", "정비", "작업 이력", "고객", "주문", "재고"]),
            }
        )

    if not steps:
        steps = [
            {
                "step_id": "S1",
                "title": "업무 설명 확인",
                "description": "입력된 업무 설명이 부족하여 기본 단계만 생성했습니다.",
                "actor": "업무 담당자",
                "systems": [],
                "data": [],
            }
        ]

    return {
        "business_goal": _infer_goal(text),
        "current_flow": steps,
        "data_and_systems": _infer_data_systems(text, steps),
        "constraints": _infer_constraints(text),
        "desired_outputs": _infer_outputs(text),
        "risk_signals": _infer_risks(text),
        "assumptions": ["Agent/LLM 구조화 응답이 없어 입력 문장 기반 fallback으로 구성했습니다."],
        "open_questions": [],
    }


def _normalize_profile(profile: dict[str, Any], raw_text: str) -> dict[str, Any]:
    current_flow = []
    for index, item in enumerate(_as_list(profile.get("current_flow")), 1):
        step = _dict(item)
        current_flow.append(
            {
                "step_id": str(step.get("step_id") or f"S{index}"),
                "title": str(step.get("title") or f"현재 업무 {index}").strip(),
                "description": str(step.get("description") or "").strip(),
                "actor": str(step.get("actor") or "업무 담당자").strip(),
                "systems": _string_list(step.get("systems")),
                "data": _string_list(step.get("data")),
            }
        )

    if not current_flow:
        current_flow = _fallback_profile(raw_text)["current_flow"]

    return {
        "business_goal": str(profile.get("business_goal") or _infer_goal(raw_text)).strip(),
        "current_flow": current_flow,
        "data_and_systems": _normalize_name_role_items(profile.get("data_and_systems")),
        "constraints": _string_list(profile.get("constraints")) or _infer_constraints(raw_text),
        "desired_outputs": _string_list(profile.get("desired_outputs")) or _infer_outputs(raw_text),
        "risk_signals": _string_list(profile.get("risk_signals")) or _infer_risks(raw_text),
        "assumptions": _string_list(profile.get("assumptions")),
        "open_questions": _string_list(profile.get("open_questions")),
        "raw_work_description": raw_text,
    }


def _validation_issues(profile: dict[str, Any]) -> list[str]:
    issues = []
    if not profile.get("business_goal"):
        issues.append("업무 목적이 비어 있습니다.")
    if not profile.get("current_flow"):
        issues.append("현재 업무 단계가 비어 있습니다.")
    if len(str(profile.get("raw_work_description") or "")) < 20:
        issues.append("업무 설명이 짧아 추천 정확도가 낮을 수 있습니다.")
    return issues


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


def _sentences(text: str) -> list[str]:
    parts = re.split(r"[\n\r]+|(?<=[.!?。])\s+|(?<=다\.)\s*", text)
    result = [part.strip(" -\t") for part in parts if part.strip(" -\t")]
    if len(result) <= 1:
        result = [part.strip() for part in re.split(r"[.;]", text) if part.strip()]
    return result


def _short_title(sentence: str, fallback: str) -> str:
    text = re.sub(r"\s+", " ", sentence).strip()
    return text[:28] + ("..." if len(text) > 28 else "") if text else fallback


def _keyword_hits(text: str, keywords: list[str]) -> list[str]:
    lowered = text.lower()
    return [key for key in keywords if key.lower() in lowered]


def _infer_goal(text: str) -> str:
    if any(key in text for key in ["위험", "이상", "불량", "품질"]):
        return "업무 데이터를 확인해 위험 징후를 빠르게 파악하고 필요한 조치를 준비합니다."
    if any(key in text for key in ["보고", "공유", "회의"]):
        return "반복 보고 업무를 정리하고 필요한 산출물을 안정적으로 준비합니다."
    return "반복 업무를 구조화하고 AI Agent 적용 가능 영역을 찾습니다."


def _infer_data_systems(text: str, steps: list[dict[str, Any]]) -> list[dict[str, str]]:
    names = set()
    for step in steps:
        names.update(step.get("systems") or [])
        names.update(step.get("data") or [])
    if not names:
        names.update(_keyword_hits(text, ["엑셀", "메일", "ERP", "MES", "DB", "생산 데이터", "품질 데이터"]))
    return [{"name": name, "role": "업무 설명에서 언급된 데이터 또는 시스템"} for name in sorted(names)]


def _infer_constraints(text: str) -> list[str]:
    constraints = []
    if any(key in text for key in ["자동 발송하지", "사람이 확인", "승인", "검토"]):
        constraints.append("중요 작업은 사람 검토 후 실행해야 합니다.")
    if any(key in text for key in ["개인정보", "보안", "민감"]):
        constraints.append("민감 정보와 접근 권한을 통제해야 합니다.")
    return constraints


def _infer_outputs(text: str) -> list[str]:
    outputs = []
    for key in ["보고서", "대시보드", "메일 초안", "알림", "리스트", "요약"]:
        if key in text:
            outputs.append(key)
    return outputs or ["업무 개선 설계서", "현재/개선 업무 Flow"]


def _infer_risks(text: str) -> list[str]:
    risks = []
    if any(key in text for key in ["메일", "발송", "공유", "알림"]):
        risks.append("외부 또는 타인에게 전달되는 커뮤니케이션")
    if any(key in text for key in ["수정", "등록", "삭제", "업데이트", "승인"]):
        risks.append("원본 시스템 변경 가능 작업")
    if any(key in text for key in ["개인정보", "고객", "민감", "보안"]):
        risks.append("민감 정보 처리")
    return risks


def _normalize_name_role_items(value: Any) -> list[dict[str, str]]:
    result = []
    for item in _as_list(value):
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("title") or "").strip()
            role = str(item.get("role") or item.get("description") or "").strip()
        else:
            name = str(item or "").strip()
            role = ""
        if name:
            result.append({"name": name, "role": role or "업무에서 사용하는 데이터 또는 시스템"})
    return result


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class BusinessProfileNormalizer(Component):
    display_name = "02 업무 구조화 결과 정리"
    description = "Agent/LLM 구조화 응답을 검증해 표준 업무 프로필로 정리합니다. 응답이 없으면 기본 추정값을 만듭니다."
    icon = "ListChecks"
    inputs = [
        DataInput(name="business_request", display_name="업무 요청", required=True),
        MessageTextInput(name="llm_profile_response", display_name="Agent/LLM 구조화 응답", required=False),
    ]
    outputs = [Output(name="business_profile", display_name="업무 구조화 결과", method="build_payload")]

    def build_payload(self) -> Data:
        result = normalize_business_profile(
            getattr(self, "business_request", None),
            getattr(self, "llm_profile_response", ""),
        )
        validation = result.get("profile_validation", {})
        profile = result.get("workflow_profile", {})
        self.status = {
            "처리 방식": validation.get("source"),
            "현재 단계 수": len(profile.get("current_flow", [])),
            "검증 상태": "정상" if validation.get("valid") else "확인 필요",
        }
        return Data(data=result)
