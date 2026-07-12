from __future__ import annotations

"""회의 후속 조치 줄을 구조화하는 읽기 전용 Standalone Skill Tool."""

import hashlib
import json
from datetime import date
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MessageTextInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message


CATALOG_CONTRACT = "agent_ground.demo_skill_catalog.v1"
TOOL_NAME = "meeting_action_skill"
REQUIRED_ACTION = "meeting_action_extract"
DISCLAIMER = "교육용 구조화 결과입니다. 실제 메일 발송, 캘린더 등록, 업무 생성 또는 외부 시스템 저장을 수행하지 않습니다."


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
        "parser_version": "meeting_action.v1",
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
        return None, "회의 후속 조치 Skill이 카탈로그에서 사용 중지되어 있습니다."
    allowed = entry.get("allowed_actions")
    forbidden = entry.get("forbidden_actions")
    if not isinstance(allowed, list) or REQUIRED_ACTION not in allowed:
        return None, f"카탈로그에 필수 허용 action '{REQUIRED_ACTION}'이 없습니다."
    if not isinstance(forbidden, list) or set(allowed) & set(forbidden):
        return None, "카탈로그의 허용 action과 금지 action 정책이 올바르지 않습니다."
    rules = entry.get("rules")
    if not isinstance(rules, dict) or str(rules.get("line_format") or "").strip() != "담당자 | 할 일 | YYYY-MM-DD":
        return None, "회의 후속 조치 Skill의 입력 형식 규칙이 올바르지 않습니다."
    return entry, ""


def _blocked_result(request: str, reason: str) -> dict[str, Any]:
    return {
        "status": "blocked",
        "skill": {"skill_id": "meeting_action", "tool_name": TOOL_NAME, "name": "회의 후속 조치 정리"},
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


def _valid_due_date(value: str) -> bool:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return False
    return parsed.isoformat() == value


def _is_optional_command_header(line: str, line_number: int) -> bool:
    """첫 줄의 짧은 자연어 실행 요청만 허용하고 중간의 비정형 줄은 숨기지 않습니다."""

    if line_number != 1 or "|" in line or len(line) > 100:
        return False
    return "회의" in line and any(keyword in line for keyword in ("후속", "액션", "할 일", "정리"))


def run_meeting_action(request: Any, skill_catalog: Any) -> dict[str, Any]:
    """`담당자 | 할 일 | YYYY-MM-DD` 줄을 순서대로 구조화합니다."""

    request_text = _text(request)
    entry, catalog_error = _catalog_entry(skill_catalog)
    if entry is None:
        return _blocked_result(request_text, catalog_error)

    skill = {
        "skill_id": str(entry.get("skill_id") or "meeting_action"),
        "tool_name": TOOL_NAME,
        "name": str(entry.get("name") or "회의 후속 조치 정리"),
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
            "skill": skill,
            "result": {
                "executed": False,
                "reason": "'담당자 | 할 일 | YYYY-MM-DD' 형식으로 한 줄 이상 입력해 주세요.",
                "action_items": [],
                "count": 0,
                "invalid_lines": [],
                "ignored_header_line_count": 0,
            },
            "governance": governance,
            "trace": _request_trace(request_text),
            "disclaimer": DISCLAIMER,
        }

    action_items: list[dict[str, Any]] = []
    invalid_lines: list[dict[str, Any]] = []
    ignored_header_line_count = 0
    for line_number, raw_line in enumerate(request_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if _is_optional_command_header(line, line_number):
            ignored_header_line_count += 1
            continue
        fields = [field.strip() for field in line.split("|")]
        if len(fields) != 3:
            invalid_lines.append({"line_number": line_number, "reason": "구분자 | 로 나눈 필드가 세 개가 아닙니다."})
            continue
        assignee, action, due_date = fields
        if not assignee:
            invalid_lines.append({"line_number": line_number, "reason": "담당자가 비어 있습니다."})
            continue
        if not action:
            invalid_lines.append({"line_number": line_number, "reason": "할 일이 비어 있습니다."})
            continue
        if len(assignee) > 100:
            invalid_lines.append({"line_number": line_number, "reason": "담당자는 100자 이하여야 합니다."})
            continue
        if len(action) > 500:
            invalid_lines.append({"line_number": line_number, "reason": "할 일은 500자 이하여야 합니다."})
            continue
        if not _valid_due_date(due_date):
            invalid_lines.append({"line_number": line_number, "reason": "기한은 유효한 YYYY-MM-DD 날짜여야 합니다."})
            continue
        action_items.append(
            {
                "sequence": len(action_items) + 1,
                "assignee": assignee,
                "action": action,
                "due_date": due_date,
                "source_line_number": line_number,
            }
        )

    ready = bool(action_items) and not invalid_lines
    if not action_items and not invalid_lines:
        invalid_lines.append({"line_number": 0, "reason": "처리할 수 있는 내용이 없습니다."})
    return {
        "status": "completed" if ready else "needs_input",
        "skill": skill,
        "result": {
            "executed": ready,
            "format": "담당자 | 할 일 | YYYY-MM-DD",
            "action_items": action_items,
            "count": len(action_items),
            "invalid_lines": invalid_lines,
            "invalid_line_count": len(invalid_lines),
            "ignored_header_line_count": ignored_header_line_count,
            "ready_for_human_review": ready,
            "reason": "" if ready else "형식이 맞지 않는 줄을 수정한 뒤 다시 요청해 주세요.",
        },
        "governance": governance,
        "trace": _request_trace(request_text),
        "disclaimer": DISCLAIMER,
    }


class MeetingActionSkillTool(Component):
    display_name = "회의 후속 조치 Skill"
    description = "담당자·할 일·ISO 기한 형식의 여러 줄을 구조화하며 실제 발송이나 시스템 등록은 하지 않습니다."
    name = "MeetingActionSkillTool"
    icon = "ListChecks"

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
            display_name="회의 후속 조치 요청",
            info="각 줄을 '담당자 | 할 일 | YYYY-MM-DD' 형식으로 입력합니다.",
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
        result = run_meeting_action(
            request=getattr(self, "request", ""),
            skill_catalog=getattr(self, "skill_catalog", None),
        )
        self.status = f"회의 후속 조치 정리: {result['status']}"
        return Data(data=result)

    def run_skill_message(self) -> Message:
        """Run Flow/MCP로 공개하기 쉬운 한글 JSON 메시지를 반환합니다."""

        result = run_meeting_action(
            request=getattr(self, "request", ""),
            skill_catalog=getattr(self, "skill_catalog", None),
        )
        self.status = f"회의 후속 조치 정리: {result['status']}"
        return Message(text=json.dumps(result, ensure_ascii=False, indent=2))

    async def _get_tools(self):
        tools = await super()._get_tools()
        if len(tools) != 1:
            raise ValueError("회의 후속 조치 Skill은 Agent Tool 출력을 정확히 하나 제공해야 합니다.")
        tool = tools[0]
        tool.name = TOOL_NAME
        tool.description = (
            "'담당자 | 할 일 | YYYY-MM-DD' 형식의 회의 후속 조치를 구조화할 때 사용합니다. "
            "메일 발송, 캘린더 등록, 업무 생성 또는 외부 시스템 저장에는 사용하지 않습니다."
        )
        tool.tags = [TOOL_NAME]
        tool.return_direct = True
        return [tool]
