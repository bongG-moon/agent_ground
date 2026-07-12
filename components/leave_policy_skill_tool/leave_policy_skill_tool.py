from __future__ import annotations

"""두 ISO 날짜의 휴가 평일을 계산하는 읽기 전용 Standalone Skill Tool."""

import hashlib
import json
import re
from datetime import date, timedelta
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MessageTextInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message


CATALOG_CONTRACT = "agent_ground.demo_skill_catalog.v1"
TOOL_NAME = "leave_policy_skill"
REQUIRED_ACTION = "leave_policy_check"
DISCLAIMER = "교육용 휴가 일수 계산입니다. 실제 사규 판정, 잔여 연차 확인, 휴가 신청 또는 승인 처리를 수행하지 않습니다."
ISO_DATE_PATTERN = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    for attribute in ("text", "content"):
        candidate = getattr(value, attribute, None)
        if isinstance(candidate, str):
            return candidate.strip()
    return str(value).strip()


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return data
    text = _text(value)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _request_trace(request: str) -> dict[str, Any]:
    encoded = request.encode("utf-8")
    return {
        "request_sha256": hashlib.sha256(encoded).hexdigest(),
        "request_length": len(request),
        "parser_version": "leave_policy.v1",
    }


def _catalog_entry(skill_catalog: Any) -> tuple[dict[str, Any] | None, str]:
    catalog = _payload(skill_catalog)
    if catalog.get("contract") != CATALOG_CONTRACT:
        return None, "검증된 데모 Skill 카탈로그가 연결되지 않았습니다."
    skills = catalog.get("skills")
    if not isinstance(skills, list):
        return None, "Skill 카탈로그의 skills 목록을 확인할 수 없습니다."
    matches = [entry for entry in skills if isinstance(entry, dict) and entry.get("tool_name") == TOOL_NAME]
    if len(matches) != 1:
        return None, f"카탈로그에서 {TOOL_NAME} 항목을 정확히 하나 확인해야 합니다."
    entry = matches[0]
    if entry.get("enabled") is not True:
        return None, "휴가 정책 점검 Skill이 카탈로그에서 사용 중지되어 있습니다."
    allowed = entry.get("allowed_actions")
    forbidden = entry.get("forbidden_actions")
    if not isinstance(allowed, list) or REQUIRED_ACTION not in allowed:
        return None, f"카탈로그에 필수 허용 action '{REQUIRED_ACTION}'이 없습니다."
    if not isinstance(forbidden, list) or set(allowed) & set(forbidden):
        return None, "카탈로그의 허용 action과 금지 action 정책이 올바르지 않습니다."
    return entry, ""


def _demo_maximum(entry: dict[str, Any]) -> tuple[int | None, str]:
    rules = entry.get("rules")
    maximum = rules.get("demo_max_chargeable_weekdays") if isinstance(rules, dict) else None
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 30:
        return None, "휴가 Skill의 데모 평일 기준이 올바르지 않습니다."
    return maximum, ""


def _parse_iso_date(value: str, label: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} '{value}'은 유효한 ISO 날짜가 아닙니다.") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{label}은 YYYY-MM-DD 형식이어야 합니다.")
    return parsed


def _parse_holidays(value: Any) -> list[date]:
    if value is None or value == "":
        return []
    data = getattr(value, "data", None)
    if data is not None:
        value = data
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"휴일 JSON을 해석할 수 없습니다: {exc.msg}") from exc
    if isinstance(value, dict):
        value = value.get("holiday_dates", value.get("holidays"))
    if not isinstance(value, list):
        raise ValueError("휴일 JSON은 ISO 날짜 문자열 목록이어야 합니다.")
    holidays: list[date] = []
    for index, item in enumerate(value, start=1):
        holiday = _parse_iso_date(str(item or "").strip(), f"{index}번째 휴일")
        if holiday not in holidays:
            holidays.append(holiday)
    return sorted(holidays)


def _blocked_result(request: str, reason: str) -> dict[str, Any]:
    return {
        "status": "blocked",
        "skill": {"skill_id": "leave_policy", "tool_name": TOOL_NAME, "name": "휴가 정책 점검"},
        "result": {"executed": False, "reason": reason},
        "governance": {
            "authorized": False,
            "required_action": REQUIRED_ACTION,
            "external_write_performed": False,
            "external_send_performed": False,
        },
        "trace": _request_trace(request),
        "disclaimer": DISCLAIMER,
    }


def run_leave_policy(request: Any, skill_catalog: Any, holiday_dates_json: Any = "") -> dict[str, Any]:
    """요청의 ISO 날짜 두 개를 포함 범위로 계산하고 평일·휴일을 구분합니다."""

    request_text = _text(request)
    entry, catalog_error = _catalog_entry(skill_catalog)
    if entry is None:
        return _blocked_result(request_text, catalog_error)
    maximum, rule_error = _demo_maximum(entry)
    if maximum is None:
        return _blocked_result(request_text, rule_error)

    skill = {
        "skill_id": str(entry.get("skill_id") or "leave_policy"),
        "tool_name": TOOL_NAME,
        "name": str(entry.get("name") or "휴가 정책 점검"),
    }
    governance = {
        "authorized": True,
        "required_action": REQUIRED_ACTION,
        "catalog_enabled": True,
        "external_write_performed": False,
        "external_send_performed": False,
        "decision_effect": "advisory_only",
    }

    date_tokens = ISO_DATE_PATTERN.findall(request_text)
    if len(date_tokens) != 2:
        return {
            "status": "needs_input",
            "skill": skill,
            "result": {
                "executed": False,
                "reason": "시작일과 종료일에 해당하는 ISO 날짜를 정확히 두 개 입력해 주세요.",
                "detected_iso_date_count": len(date_tokens),
            },
            "governance": governance,
            "trace": _request_trace(request_text),
            "disclaimer": DISCLAIMER,
        }
    try:
        start_date = _parse_iso_date(date_tokens[0], "시작일")
        end_date = _parse_iso_date(date_tokens[1], "종료일")
        holidays = _parse_holidays(holiday_dates_json)
    except ValueError as exc:
        return {
            "status": "needs_input",
            "skill": skill,
            "result": {"executed": False, "reason": str(exc)},
            "governance": governance,
            "trace": _request_trace(request_text),
            "disclaimer": DISCLAIMER,
        }
    if end_date < start_date:
        return {
            "status": "needs_input",
            "skill": skill,
            "result": {"executed": False, "reason": "종료일은 시작일과 같거나 이후여야 합니다."},
            "governance": governance,
            "trace": _request_trace(request_text),
            "disclaimer": DISCLAIMER,
        }

    cursor = start_date
    weekdays: list[date] = []
    weekend_days = 0
    while cursor <= end_date:
        if cursor.weekday() < 5:
            weekdays.append(cursor)
        else:
            weekend_days += 1
        cursor += timedelta(days=1)

    excluded_holidays = [holiday for holiday in holidays if start_date <= holiday <= end_date and holiday.weekday() < 5]
    ignored_holidays = [holiday for holiday in holidays if holiday not in excluded_holidays]
    chargeable_days = len(weekdays) - len(excluded_holidays)
    needs_review = chargeable_days > maximum
    return {
        "status": "completed",
        "skill": skill,
        "result": {
            "executed": True,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "calendar_days_inclusive": (end_date - start_date).days + 1,
            "weekday_count": len(weekdays),
            "weekend_day_count": weekend_days,
            "excluded_holidays": [value.isoformat() for value in excluded_holidays],
            "ignored_holidays": [value.isoformat() for value in ignored_holidays],
            "chargeable_days": chargeable_days,
            "demo_max_chargeable_weekdays": maximum,
            "policy_result": "담당자 검토 필요" if needs_review else "데모 기준 내",
            "decision_code": "manual_review_required" if needs_review else "within_demo_guideline",
        },
        "governance": governance,
        "trace": _request_trace(request_text),
        "disclaimer": DISCLAIMER,
    }


class LeavePolicySkillTool(Component):
    display_name = "휴가 정책 점검 Skill"
    description = "ISO 날짜 두 개의 평일과 선택적으로 연결한 휴일을 계산하며 실제 휴가 신청이나 승인은 하지 않습니다."
    name = "LeavePolicySkillTool"
    icon = "CalendarDays"

    inputs = [
        DataInput(
            name="skill_catalog",
            display_name="Skill 카탈로그",
            info="데모 Skill 카탈로그 빌더의 Skill 카탈로그 출력을 연결합니다. Agent Tool 인자로 노출되지 않습니다.",
            input_types=["Data", "JSON"],
            required=True,
        ),
        MessageTextInput(
            name="request",
            display_name="휴가 점검 요청",
            info="예: 2026-07-13부터 2026-07-17까지 휴가 일수를 확인해 줘.",
            required=True,
            tool_mode=True,
        ),
        MessageTextInput(
            name="holiday_dates_json",
            display_name="휴일 날짜 JSON",
            info='선택 입력입니다. 예: ["2026-07-15"] 또는 {"holiday_dates":["2026-07-15"]}',
            value="",
            required=False,
            advanced=True,
        ),
    ]
    outputs = [
        Output(
            name="skill_result",
            display_name="Skill 실행 결과",
            method="run_skill",
            types=["Data"],
            tool_mode=True,
        ),
        Output(
            name="skill_message",
            display_name="하위 Flow 응답",
            method="run_skill_message",
            types=["Message"],
            tool_mode=False,
        ),
    ]

    def run_skill(self) -> Data:
        result = run_leave_policy(
            request=getattr(self, "request", ""),
            skill_catalog=getattr(self, "skill_catalog", None),
            holiday_dates_json=getattr(self, "holiday_dates_json", ""),
        )
        self.status = f"휴가 정책 점검: {result['status']}"
        return Data(data=result)

    def run_skill_message(self) -> Message:
        """Run Flow/MCP로 공개하기 쉬운 한글 JSON 메시지를 반환합니다."""

        result = run_leave_policy(
            request=getattr(self, "request", ""),
            skill_catalog=getattr(self, "skill_catalog", None),
            holiday_dates_json=getattr(self, "holiday_dates_json", ""),
        )
        self.status = f"휴가 정책 점검: {result['status']}"
        return Message(text=json.dumps(result, ensure_ascii=False, indent=2))

    async def _get_tools(self):
        tools = await super()._get_tools()
        if len(tools) != 1:
            raise ValueError("휴가 정책 점검 Skill은 Agent Tool 출력을 정확히 하나 제공해야 합니다.")
        tool = tools[0]
        tool.name = TOOL_NAME
        tool.description = (
            "사용자가 시작일과 종료일 ISO 날짜 두 개로 휴가 평일 수를 확인하려 할 때 사용합니다. "
            "잔여 연차 조회, 실제 휴가 신청, 승인 또는 인사 시스템 변경에는 사용하지 않습니다."
        )
        tool.tags = [TOOL_NAME]
        tool.return_direct = True
        return [tool]
