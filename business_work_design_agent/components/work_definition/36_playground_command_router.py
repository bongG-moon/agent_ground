from __future__ import annotations

"""Strict, top-level-only Playground command parsing and routing."""

import copy
import json
import uuid
from typing import Any

from lfx.custom import Component
from lfx.io import IntInput, MessageTextInput, Output
from lfx.schema import Data


SCHEMA_VERSION = "playground-command/v1"
ALLOWED_COMMANDS = {"start", "submit_answers", "approve", "reject", "cancel"}
START_KEYS = {"schema_version", "command", "request_text", "additional_prompt"}
ANSWER_KEYS = {
    "schema_version", "command", "channel_mode", "work_definition_id", "batch_id", "session_id",
    "expected_revision", "idempotency_key", "answers", "submitted_at",
}
ACTION_KEYS = {"schema_version", "command"}


def _failure(code: str, message: str, trace_id: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "BLOCKED",
        "route": "blocked_path",
        "artifact_refs": [],
        "error": {"code": code, "message": message, "retryable": False, "details": {}},
        "resume": None,
        "trace_id": trace_id,
    }


def _reject_constant(_: str) -> None:
    raise ValueError("non-finite JSON number")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _input_text(value: Any) -> str:
    text = getattr(value, "text", value if isinstance(value, str) else "")
    return text if isinstance(text, str) else ""


def parse_playground_command(value: Any, *, max_input_chars: Any = 200_000) -> dict[str, Any]:
    trace_id = f"trace-{uuid.uuid4()}"
    try:
        maximum = max(1_000, min(int(max_input_chars), 500_000))
    except (TypeError, ValueError):
        maximum = 200_000
    raw = _input_text(value)
    if not raw.strip() or len(raw) > maximum:
        return _failure("PLAYGROUND_COMMAND_SIZE_INVALID", "명령 JSON이 비어 있거나 허용 크기를 초과했습니다.", trace_id)
    try:
        payload = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _failure("PLAYGROUND_COMMAND_JSON_INVALID", "중복 key가 없는 단일 JSON object가 필요합니다.", trace_id)
    if not isinstance(payload, dict):
        return _failure("PLAYGROUND_COMMAND_OBJECT_REQUIRED", "Playground 명령은 JSON object여야 합니다.", trace_id)
    if payload.get("schema_version") not in (None, SCHEMA_VERSION):
        return _failure("PLAYGROUND_COMMAND_SCHEMA_INVALID", "지원하지 않는 Playground 명령 schema입니다.", trace_id)
    command = payload.get("command")
    if type(command) is not str or command not in ALLOWED_COMMANDS:
        return _failure("PLAYGROUND_COMMAND_INVALID", "지원하는 최상위 command가 필요합니다.", trace_id)

    allowed_keys = START_KEYS if command == "start" else ANSWER_KEYS if command == "submit_answers" else ACTION_KEYS
    if set(payload) - allowed_keys:
        return _failure("PLAYGROUND_COMMAND_FIELDS_INVALID", "command에 허용되지 않은 최상위 필드가 있습니다.", trace_id)
    if command == "start":
        request_text = payload.get("request_text")
        additional_prompt = payload.get("additional_prompt", "")
        if type(request_text) is not str or not request_text.strip() or len(request_text) > 50_000:
            return _failure("PLAYGROUND_START_REQUEST_INVALID", "start에는 1~50000자의 request_text가 필요합니다.", trace_id)
        if type(additional_prompt) is not str or len(additional_prompt) > 20_000:
            return _failure("PLAYGROUND_START_PROMPT_INVALID", "additional_prompt는 20000자 이하 문자열이어야 합니다.", trace_id)
    elif command == "submit_answers":
        required = {
            "channel_mode", "work_definition_id", "batch_id", "session_id", "expected_revision",
            "idempotency_key", "answers",
        }
        if required - set(payload) or payload.get("channel_mode") != "playground":
            return _failure("PLAYGROUND_ANSWER_FIELDS_REQUIRED", "submit_answers 필수 identity와 playground channel이 필요합니다.", trace_id)
        for field in ("work_definition_id", "batch_id", "session_id", "idempotency_key"):
            if type(payload.get(field)) is not str or not payload[field] or len(payload[field]) > 300:
                return _failure("PLAYGROUND_ANSWER_IDENTITY_INVALID", "submit_answers identity가 유효하지 않습니다.", trace_id)
        revision = payload.get("expected_revision")
        if type(revision) is not int or revision < 0:
            return _failure("PLAYGROUND_ANSWER_REVISION_INVALID", "expected_revision은 0 이상의 정수여야 합니다.", trace_id)
        answers = payload.get("answers")
        if not isinstance(answers, (dict, list)):
            return _failure("PLAYGROUND_ANSWER_LIST_INVALID", "answers는 object 또는 array여야 합니다.", trace_id)
        if "submitted_at" in payload and type(payload["submitted_at"]) is not str:
            return _failure("PLAYGROUND_ANSWER_TIMESTAMP_INVALID", "submitted_at은 문자열이어야 합니다.", trace_id)

    routed = copy.deepcopy(payload)
    routed.update(
        {
            "schema_version": SCHEMA_VERSION,
            "ok": True,
            "status": "ROUTED",
            "route": f"{command}_path",
            "trace_id": trace_id,
        }
    )
    return routed


class PlaygroundCommandRouterComponent(Component):
    display_name = "36 Playground Command Router"
    description = "중복 key와 nested command 우회를 거부하고 검증된 최상위 command 경로 하나만 엽니다."
    icon = "GitBranch"
    name = "PlaygroundCommandRouter"

    inputs = [
        MessageTextInput(name="input_text", display_name="Structured Command JSON", required=True),
        IntInput(name="max_input_chars", display_name="Maximum Input Characters", value=200_000, advanced=True),
    ]
    outputs = [
        Output(name="start_path", display_name="Start", method="route_command", types=["Data"], group_outputs=True),
        Output(name="submit_answers_path", display_name="Submit Answers", method="route_command", types=["Data"], group_outputs=True),
        Output(name="approve_path", display_name="Approve", method="route_command", types=["Data"], group_outputs=True),
        Output(name="reject_path", display_name="Reject", method="route_command", types=["Data"], group_outputs=True),
        Output(name="cancel_path", display_name="Cancel", method="route_command", types=["Data"], group_outputs=True),
        Output(name="blocked_path", display_name="Blocked", method="route_command", types=["Data"], group_outputs=True),
    ]

    def route_command(self) -> Data:
        result = getattr(self, "_parsed_command", None)
        if not isinstance(result, dict):
            result = parse_playground_command(
                getattr(self, "input_text", ""),
                max_input_chars=getattr(self, "max_input_chars", 200_000),
            )
            self._parsed_command = result
        selected = str(result.get("route") or "blocked_path")
        for output_name in (
            "start_path", "submit_answers_path", "approve_path", "reject_path", "cancel_path", "blocked_path",
        ):
            if output_name != selected:
                self.stop(output_name)
        self.status = {"ok": result.get("ok"), "status": result.get("status"), "route": selected}
        return Data(data=copy.deepcopy(result))
