from __future__ import annotations

"""Fail-closed routing for standalone Langflow component result envelopes."""

import copy
import json
import re
import uuid
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, MessageTextInput, Output
from lfx.schema import Data


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
    return copy.deepcopy(parsed) if isinstance(parsed, dict) else {}


def _field(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for token in [item.strip() for item in path.split(".") if item.strip()]:
        if not isinstance(current, dict) or token not in current:
            return None
        current = current[token]
    return current


def gate_result(value: Any, *, required_field: Any = "") -> dict[str, Any]:
    """Return a canonical success/failure envelope without trusting truthy values."""

    trace_id = f"trace-{uuid.uuid4()}"
    try:
        payload = _payload(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    required = str(required_field or "").strip()
    if payload.get("ok") is True and (not required or _field(payload, required) not in (None, "")):
        return payload
    if payload.get("ok") is False and isinstance(payload.get("error"), dict):
        return payload
    code = "RESULT_REQUIRED_FIELD_MISSING" if payload.get("ok") is True and required else "RESULT_ENVELOPE_INVALID"
    message = (
        f"성공 결과에 필수 필드 '{required}'가 없습니다."
        if code == "RESULT_REQUIRED_FIELD_MISSING"
        else "이전 단계가 명시적인 ok=true 결과를 반환하지 않았습니다."
    )
    return {
        "ok": False,
        "status": "BLOCKED",
        "artifact_refs": [],
        "error": {
            "code": code,
            "message": message,
            "retryable": False,
            "details": {"required_field": required} if required else {},
        },
        "resume": None,
        "trace_id": str(payload.get("trace_id") or trace_id)[:200],
    }


class ResultGateComponent(Component):
    display_name = "35 Result Gate"
    description = "ok=true와 필수 payload 필드를 확인하고 성공·차단 경로를 물리적으로 분리합니다."
    icon = "ShieldCheck"
    name = "ResultGate"

    inputs = [
        DataInput(name="result", display_name="Result Envelope", input_types=["Data", "JSON"], required=True),
        MessageTextInput(
            name="required_field",
            display_name="Required Success Field",
            value="",
            required=False,
            advanced=True,
            info="성공 경로에서 반드시 존재해야 하는 점 표기 payload 필드입니다.",
        ),
    ]
    outputs = [
        Output(name="success_path", display_name="Verified Success", method="route_result", types=["Data"], group_outputs=True),
        Output(name="blocked_path", display_name="Blocked Result", method="route_result", types=["Data"], group_outputs=True),
    ]

    def route_result(self) -> Data:
        result = getattr(self, "_gated_result", None)
        if not isinstance(result, dict):
            result = gate_result(
                getattr(self, "result", None),
                required_field=getattr(self, "required_field", ""),
            )
            self._gated_result = result
        selected = "success_path" if result.get("ok") is True else "blocked_path"
        for output_name in ("success_path", "blocked_path"):
            if output_name != selected:
                self.stop(output_name)
        self.status = {
            "ok": result.get("ok"),
            "status": result.get("status"),
            "route": selected,
            "required_field": str(getattr(self, "required_field", "") or ""),
        }
        return Data(data=copy.deepcopy(result))
