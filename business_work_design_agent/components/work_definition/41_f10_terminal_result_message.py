from __future__ import annotations

"""Render a safe, short terminal F10 status message without local imports."""

import copy
import json
import re
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, Output
from lfx.schema import Message


_SENSITIVE_PATTERN = re.compile(
    r"(?i)(?:mongodb(?:\+srv)?://[^\s,;]+|https?://[^\s,;]+|"
    r"[\"']?(?:api[_ -]?key|token|secret|password|authorization|bearer|"
    r"subject(?:_id)?|employee(?:_id)?|owner(?:_id)?|session(?:_id)?|tenant(?:_id)?)[\"']?\s*[:=]\s*"
    r"[\"']?[^\s,;\]\}\"']+[\"']?)"
)
_SAFE_CODE_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")
_HIDE_DETAIL_CODE_PREFIXES = (
    "AUTHENTICATION_",
    "AUTHENTICATED_",
    "TRUSTED_GATEWAY_",
    "LOCAL_DEMO_",
    "MONGODB_",
    "DESIGN_INVOCATION_",
)


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return copy.deepcopy(value)
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return copy.deepcopy(data)
    text = getattr(value, "text", value if isinstance(value, str) else "")
    if not isinstance(text, str) or not text.strip():
        return {}
    parsed = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE))
    return copy.deepcopy(parsed) if isinstance(parsed, dict) else {"message": text}


def _has_value(value: Any, payload: dict[str, Any]) -> bool:
    if payload:
        return True
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return bool(data)
    text = getattr(value, "text", None)
    return isinstance(text, str) and bool(text.strip())


def _safe_code(value: Any) -> str:
    code = _SAFE_CODE_PATTERN.sub("_", str(value or "").strip()).strip("_.-")
    return code[:80]


def _safe_message(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return ""
    text = _SENSITIVE_PATTERN.sub("[민감정보 제거]", text)
    return text[:240]


def _base_message(payload: dict[str, Any]) -> str:
    """Infer a short outcome without relying on which fan-in edge supplied it."""

    status = _safe_code(payload.get("status")).upper()
    route = _safe_code(payload.get("route")).lower()
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    error_code = _safe_code(error.get("code")).upper()
    if status == "REJECTED":
        return "업무 정의 검토가 반려되었습니다. 내용을 보완한 뒤 다시 진행해 주세요."
    if status == "CANCELLED" or route == "cancelled_path":
        return "업무 정의 작성을 취소했습니다."
    if error_code.startswith("ANSWER_") or error_code.startswith("CLARIFICATION_") or error_code.startswith("F10_ANSWER_"):
        return "질문 카드의 답변을 처리할 수 없어 중단되었습니다."
    if error_code.startswith("QUESTION_"):
        return "추가 질문을 준비할 수 없어 중단되었습니다."
    if error_code.startswith("AUTHENTICATION_") or error_code.startswith("AUTHENTICATED_") or error_code.startswith("TRUSTED_GATEWAY_") or error_code.startswith("LOCAL_DEMO_"):
        return "인증된 사용자 정보를 확인할 수 없어 중단되었습니다."
    if error_code.startswith("MONGODB_") or error_code.startswith("DESIGN_INVOCATION_MONGODB"):
        return "MongoDB 설정 또는 연결을 확인할 수 없어 중단되었습니다."
    if error_code.startswith("F10_REVIEW") or error_code.startswith("WORK_GRAPH") or error_code.startswith("WORK_PREVIEW"):
        return "업무 정의 검토를 진행할 수 없어 중단되었습니다."
    if error_code.startswith("F20_REPORT_HANDOFF") or error_code.startswith("F20_"):
        return "F20 설계 결과를 보고서 단계로 전달할 수 없어 중단되었습니다."
    if error_code.startswith(
        (
            "TERMINAL_BLUEPRINT_",
            "BLUEPRINT_",
            "PORT_",
            "EDGE_",
            "GENERATION_",
            "INVALID_BLUEPRINT_",
            "CLASSIFIED_BLUEPRINT_",
            "UPSTREAM_BLUEPRINT_",
        )
    ):
        return "Agent 설계 초안의 노드 연결 또는 입출력 정보를 확인할 수 없어 중단되었습니다."
    if error_code.startswith("DESIGN_") or error_code.startswith("APPROVED_"):
        return "에이전트 설계 단계를 진행할 수 없어 중단되었습니다."
    return "업무 정의 처리 중 문제가 발생해 중단되었습니다."


def _as_values(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def render_f10_terminal_result_message(terminal_events: Any = None) -> str:
    """Return the first supplied terminal outcome in a UI-safe Korean sentence.

    A single list input deliberately replaces many optional fan-in fields.
    Langflow can therefore skip conditionally excluded, never-built branch
    vertices before this renderer is called.
    """

    for value in _as_values(terminal_events):
        try:
            payload = _payload(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            if value is not None:
                return "업무 정의 최종 결과 형식을 읽을 수 없습니다."
            continue
        if not _has_value(value, payload):
            continue

        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        parts = [_base_message(payload)]
        status = _safe_code(payload.get("status"))
        error_code = _safe_code(error.get("code"))
        # Authentication and MongoDB failures can contain a gateway claim,
        # URI, or driver detail even when an upstream component attempted to
        # redact it.  The user-facing terminal keeps the stable code and a
        # Korean next-step message, but intentionally drops the free-text
        # detail for those categories.
        error_message = "" if error_code.upper().startswith(_HIDE_DETAIL_CODE_PREFIXES) else _safe_message(
            error.get("message") or payload.get("message")
        )
        if status:
            parts.append(f"상태: {status}.")
        if error_code:
            parts.append(f"오류 코드: {error_code}.")
        if error_message:
            parts.append(f"내용: {error_message}")
        return " ".join(parts)
    return "표시할 최종 처리 결과가 아직 없습니다."


class F10TerminalResultMessageComponent(Component):
    display_name = "41 F10 결과 메시지"
    description = "취소·반려·차단 결과 중 먼저 도착한 하나를 민감정보 없이 짧은 안내 메시지로 표시합니다."
    icon = "MessageSquareWarning"
    name = "F10TerminalResultMessage"

    inputs = [
        DataInput(
            name="terminal_events",
            display_name="최종 처리 이벤트 (자동 연결)",
            input_types=["Data", "JSON"],
            required=False,
            is_list=True,
            advanced=False,
            info="취소·반려·차단 경로가 하나의 이벤트 목록으로 자동 연결됩니다. 여러 선택 입력을 직접 채우지 않습니다.",
        )
    ]
    outputs = [Output(name="message", display_name="결과 메시지", method="build_message", types=["Message"])]

    def build_message(self) -> Message:
        return Message(text=render_f10_terminal_result_message(getattr(self, "terminal_events", None)))
