from __future__ import annotations

"""경비 금액을 교육용 한도와 비교하는 읽기 전용 Standalone Skill Tool."""

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MessageTextInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message


CATALOG_CONTRACT = "agent_ground.demo_skill_catalog.v1"
TOOL_NAME = "expense_precheck_skill"
REQUIRED_ACTION = "expense_precheck"
CATEGORIES = ("식대", "교통비", "숙박비", "기타")
DISCLAIMER = "교육용 사전 점검 결과입니다. 실제 비용 승인, 전표 등록, 지급 또는 외부 전송을 수행하지 않습니다."
AMOUNT_PATTERN = re.compile(
    r"(?P<category>식대|교통비|숙박비|기타)\s*(?:[:：=]|은|는)?\s*"
    r"(?P<amount>[0-9][0-9,]*(?:\.[0-9]+)?)\s*(?P<unit>만원|천원|원)?"
)


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
        "parser_version": "expense_precheck.v1",
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
        return None, "경비 사전 점검 Skill이 카탈로그에서 사용 중지되어 있습니다."
    allowed = entry.get("allowed_actions")
    forbidden = entry.get("forbidden_actions")
    if not isinstance(allowed, list) or REQUIRED_ACTION not in allowed:
        return None, f"카탈로그에 필수 허용 action '{REQUIRED_ACTION}'이 없습니다."
    if not isinstance(forbidden, list) or set(allowed) & set(forbidden):
        return None, "카탈로그의 허용 action과 금지 action 정책이 올바르지 않습니다."
    return entry, ""


def _limits(entry: dict[str, Any]) -> tuple[dict[str, int] | None, str]:
    rules = entry.get("rules")
    limits = rules.get("category_limits") if isinstance(rules, dict) else None
    if not isinstance(limits, dict) or set(limits) != set(CATEGORIES):
        return None, "경비 항목별 데모 한도 설정이 올바르지 않습니다."
    normalized: dict[str, int] = {}
    for category in CATEGORIES:
        value = limits.get(category)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 1_000_000_000:
            return None, f"{category} 데모 한도는 1 이상 10억 이하의 정수여야 합니다."
        normalized[category] = value
    return normalized, ""


def _blocked_result(request: str, reason: str) -> dict[str, Any]:
    return {
        "status": "blocked",
        "skill": {
            "skill_id": "expense_precheck",
            "tool_name": TOOL_NAME,
            "name": "경비 사전 점검",
        },
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


def _parse_amount(raw_amount: str, unit: str) -> int:
    try:
        number = Decimal(raw_amount.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError("경비 금액을 숫자로 해석할 수 없습니다.") from exc
    multiplier = {"": Decimal(1), "원": Decimal(1), "천원": Decimal(1000), "만원": Decimal(10000)}[unit]
    amount = number * multiplier
    if amount != amount.to_integral_value() or amount < 0 or amount > 1_000_000_000_000:
        raise ValueError("경비 금액은 0 이상 1조 원 이하의 정수 금액이어야 합니다.")
    return int(amount)


def run_expense_precheck(request: Any, skill_catalog: Any) -> dict[str, Any]:
    """경비 요청을 파싱해 카탈로그의 교육용 항목별 한도와 비교합니다."""

    request_text = _text(request)
    entry, catalog_error = _catalog_entry(skill_catalog)
    if entry is None:
        return _blocked_result(request_text, catalog_error)
    limits, limit_error = _limits(entry)
    if limits is None:
        return _blocked_result(request_text, limit_error)

    base = {
        "skill_id": str(entry.get("skill_id") or "expense_precheck"),
        "tool_name": TOOL_NAME,
        "name": str(entry.get("name") or "경비 사전 점검"),
    }
    governance = {
        "authorized": True,
        "required_action": REQUIRED_ACTION,
        "catalog_enabled": True,
        "external_write_performed": False,
        "external_send_performed": False,
        "decision_effect": "advisory_only",
    }
    if not request_text:
        return {
            "status": "needs_input",
            "skill": base,
            "result": {
                "executed": False,
                "reason": "식대, 교통비, 숙박비, 기타 중 한 개 이상의 항목과 금액을 입력해 주세요.",
            },
            "governance": governance,
            "trace": _request_trace(request_text),
            "disclaimer": DISCLAIMER,
        }

    totals = {category: 0 for category in CATEGORIES}
    item_counts = {category: 0 for category in CATEGORIES}
    parse_errors: list[str] = []
    for match in AMOUNT_PATTERN.finditer(request_text):
        category = match.group("category")
        try:
            amount = _parse_amount(match.group("amount"), match.group("unit") or "")
        except ValueError as exc:
            parse_errors.append(str(exc))
            continue
        totals[category] += amount
        item_counts[category] += 1

    recognized_item_count = sum(item_counts.values())
    if recognized_item_count == 0 or parse_errors:
        reason = parse_errors[0] if parse_errors else "'식대 15000원'처럼 항목 뒤에 금액을 입력해 주세요."
        return {
            "status": "needs_input",
            "skill": base,
            "result": {"executed": False, "reason": reason, "recognized_item_count": recognized_item_count},
            "governance": governance,
            "trace": _request_trace(request_text),
            "disclaimer": DISCLAIMER,
        }

    category_amounts = {category: totals[category] for category in CATEGORIES}
    limit_checks: dict[str, dict[str, Any]] = {}
    exceeded_categories: list[str] = []
    for category in CATEGORIES:
        amount = totals[category]
        limit = limits[category]
        exceeded = amount > limit
        if exceeded:
            exceeded_categories.append(category)
        limit_checks[category] = {
            "amount": amount,
            "demo_limit": limit,
            "exceeded": exceeded,
            "difference_from_limit": amount - limit,
            "item_count": item_counts[category],
        }

    requires_review = bool(exceeded_categories)
    return {
        "status": "completed",
        "skill": base,
        "result": {
            "executed": True,
            "currency": "KRW",
            "category_amounts": category_amounts,
            "total_amount": sum(totals.values()),
            "recognized_item_count": recognized_item_count,
            "limit_checks": limit_checks,
            "exceeded_categories": exceeded_categories,
            "overall_decision": "담당자 검토 필요" if requires_review else "데모 한도 내",
            "decision_code": "manual_review_required" if requires_review else "within_demo_limit",
        },
        "governance": governance,
        "trace": _request_trace(request_text),
        "disclaimer": DISCLAIMER,
    }


class ExpensePrecheckSkillTool(Component):
    display_name = "경비 사전 점검 Skill"
    description = "식대·교통비·숙박비·기타 금액을 교육용 한도와 비교하며 실제 승인이나 저장은 하지 않습니다."
    name = "ExpensePrecheckSkillTool"
    icon = "Receipt"

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
            display_name="경비 점검 요청",
            info="예: 식대 25,000원, 교통비 12,000원",
            required=True,
            tool_mode=True,
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
        result = run_expense_precheck(
            request=getattr(self, "request", ""),
            skill_catalog=getattr(self, "skill_catalog", None),
        )
        self.status = f"경비 사전 점검: {result['status']}"
        return Data(data=result)

    def run_skill_message(self) -> Message:
        """Run Flow/MCP로 공개하기 쉬운 한글 JSON 메시지를 반환합니다."""

        result = run_expense_precheck(
            request=getattr(self, "request", ""),
            skill_catalog=getattr(self, "skill_catalog", None),
        )
        self.status = f"경비 사전 점검: {result['status']}"
        return Message(text=json.dumps(result, ensure_ascii=False, indent=2))

    async def _get_tools(self):
        tools = await super()._get_tools()
        if len(tools) != 1:
            raise ValueError("경비 사전 점검 Skill은 Agent Tool 출력을 정확히 하나 제공해야 합니다.")
        tool = tools[0]
        tool.name = TOOL_NAME
        tool.description = (
            "식대, 교통비, 숙박비, 기타 경비의 금액을 교육용 한도와 비교할 때 사용합니다. "
            "실제 승인, 전표 등록, 지급 또는 외부 전송 요청에는 사용하지 않습니다."
        )
        tool.tags = [TOOL_NAME]
        tool.return_direct = True
        return [tool]
