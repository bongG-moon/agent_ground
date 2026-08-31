from __future__ import annotations

"""Build one explicit authentication context for the F10 -> F20 boundary.

The work-request ``employee_id`` is an audit/owner hint, not an operating
authentication assertion.  This standalone component makes the distinction
visible on the F10 canvas:

* ``local_demo_fixture`` keeps the sample runnable, but marks the result as
  unverified and must never be described as production authentication.
* ``trusted_gateway`` accepts only the independently supplied gateway subject
  and groups.  It intentionally never falls back to the employee actor.

The downstream invocation loader consumes the sealed Data envelope rather
than a direct employee-id edge, so an imported production Flow has one
obvious identity boundary to replace with its gateway adapter.
"""

import copy
import json
import re
import uuid
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, DropdownInput, MessageTextInput, Output
from lfx.schema import Data


SCHEMA_VERSION = "f10-authentication-context/v1"
SOURCES = {"local_demo_fixture", "trusted_gateway"}
IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MAX_GROUPS = 100
MAX_GROUP_INPUT_CHARS = 20_000
_OUTPUTS = ("success_path", "blocked_path")


def _payload(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple, set)):
        return copy.deepcopy(value)
    data = getattr(value, "data", None)
    if isinstance(data, (dict, list, tuple, set)):
        return copy.deepcopy(data)
    text = getattr(value, "text", value if isinstance(value, str) else "")
    return text if isinstance(text, str) else ""


def _identity(value: Any) -> str:
    candidate = _payload(value)
    if isinstance(candidate, dict):
        for key in ("subject_id", "employee_id", "owner_id", "value", "text", "input_value"):
            if key in candidate:
                candidate = candidate[key]
                break
    if not isinstance(candidate, str):
        return ""
    text = candidate.strip()
    return text if IDENTITY_PATTERN.fullmatch(text) is not None else ""


def _groups(value: Any) -> list[str]:
    candidate = _payload(value)
    if isinstance(candidate, dict):
        if set(candidate) - {"groups"}:
            raise ValueError("AUTHENTICATION_GROUPS_INVALID")
        candidate = candidate.get("groups", [])
    if isinstance(candidate, str):
        if len(candidate) > MAX_GROUP_INPUT_CHARS:
            raise ValueError("AUTHENTICATION_GROUPS_LIMIT_EXCEEDED")
        text = candidate.strip()
        if not text:
            candidate = []
        elif text.startswith("[") or text.startswith("{"):
            try:
                candidate = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError("AUTHENTICATION_GROUPS_INVALID") from exc
            return _groups(candidate)
        else:
            candidate = re.split(r"[,;\n]", text)
    if not isinstance(candidate, (list, tuple, set)):
        raise ValueError("AUTHENTICATION_GROUPS_INVALID")
    if len(candidate) > MAX_GROUPS:
        raise ValueError("AUTHENTICATION_GROUPS_LIMIT_EXCEEDED")
    result: list[str] = []
    for item in candidate:
        identity = _identity(item)
        if not identity:
            raise ValueError("AUTHENTICATION_GROUPS_INVALID")
        normalized = identity.lower()
        if normalized not in result:
            result.append(normalized)
    return sorted(result)


def _failure(code: str, message: str, trace_id: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "BLOCKED",
        "schema_version": SCHEMA_VERSION,
        "artifact_refs": [],
        "error": {"code": code, "message": message, "retryable": False, "details": {}},
        "resume": None,
        "trace_id": trace_id,
    }


def build_f10_authentication_context(
    *,
    authentication_source: Any,
    local_demo_employee_actor_id: Any = "",
    trusted_gateway_subject_id: Any = "",
    trusted_gateway_groups: Any = None,
    trace_id: Any = "",
) -> dict[str, Any]:
    """Create a bounded identity envelope without silently changing sources."""

    safe_trace = str(trace_id or f"trace-{uuid.uuid4()}")[:200]
    source = str(authentication_source or "").strip().lower()
    if source not in SOURCES:
        return _failure(
            "AUTHENTICATION_SOURCE_INVALID",
            "인증 source는 local_demo_fixture 또는 trusted_gateway여야 합니다.",
            safe_trace,
        )
    try:
        groups = _groups(trusted_gateway_groups)
    except ValueError as exc:
        return _failure(str(exc), "인증 group 형식이 유효하지 않습니다.", safe_trace)

    if source == "local_demo_fixture":
        subject_id = _identity(local_demo_employee_actor_id)
        if not subject_id:
            return _failure(
                "LOCAL_DEMO_SUBJECT_REQUIRED",
                "로컬 데모를 실행하려면 자동 연결된 사번 기반 실행자 ID가 필요합니다.",
                safe_trace,
            )
        if groups:
            return _failure(
                "LOCAL_DEMO_GROUPS_NOT_ALLOWED",
                "로컬 데모 인증에는 gateway group을 넣을 수 없습니다.",
                safe_trace,
            )
        verified = False
    else:
        subject_id = _identity(trusted_gateway_subject_id)
        if not subject_id:
            return _failure(
                "TRUSTED_GATEWAY_SUBJECT_REQUIRED",
                "운영 모드에서는 인증 gateway가 연결한 subject ID가 필요합니다.",
                safe_trace,
            )
        verified = True

    return {
        "ok": True,
        "status": "AUTHENTICATION_READY",
        "schema_version": SCHEMA_VERSION,
        "artifact_refs": [],
        "source": source,
        "subject_id": subject_id,
        "groups": groups,
        "authenticated_subject_verified": verified,
        "trace_id": safe_trace,
    }


class F10AuthenticationContextComponent(Component):
    display_name = "45 F10 인증 Context 경계"
    description = "로컬 데모 fixture와 운영 gateway 인증을 명시적으로 분리해 F10→F20 인증 context 하나를 만듭니다."
    icon = "ShieldUser"
    name = "F10AuthenticationContext"

    inputs = [
        DropdownInput(
            name="authentication_source",
            display_name="인증 Source",
            options=["local_demo_fixture", "trusted_gateway"],
            value="local_demo_fixture",
            info="local_demo_fixture는 예제 확인 전용이며 운영 인증으로 취급되지 않습니다. 운영에서는 trusted_gateway로 바꾸고 아래 gateway 포트를 연결합니다.",
        ),
        DataInput(
            name="local_demo_employee_actor_id",
            display_name="로컬 데모 사번 기반 실행자 (자동 연결)",
            input_types=["Data", "JSON", "Message"],
            required=False,
            info="Component 10의 사번 기반 실행자 ID가 자동 연결됩니다. local_demo_fixture에서만 사용하며 운영 gateway source에서는 무시됩니다.",
        ),
        MessageTextInput(
            name="trusted_gateway_subject_id",
            display_name="Trusted Gateway Subject ID",
            required=False,
            input_types=["Data", "JSON", "Message"],
            info="운영에서는 인증 gateway/SSO adapter의 subject output만 연결합니다. Chat Input·사번·고정 문자열을 직접 연결하지 않습니다.",
        ),
        MessageTextInput(
            name="trusted_gateway_groups",
            display_name="Trusted Gateway Groups",
            value="[]",
            required=False,
            input_types=["Data", "JSON", "Message"],
            info="운영 gateway가 제공한 bounded group 목록입니다. local_demo_fixture에서는 비워 둡니다.",
        ),
        MessageTextInput(name="trace_id", display_name="Trace ID", value="", advanced=True),
    ]
    outputs = [
        Output(name="success_path", display_name="인증 Context 준비", method="route_context", types=["Data"], group_outputs=True),
        Output(name="blocked_path", display_name="인증 Context 차단", method="route_context", types=["Data"], group_outputs=True),
    ]

    def _component_id(self) -> str:
        return str(getattr(self, "_id", "") or self.name)[:200]

    def _result(self) -> dict[str, Any]:
        result = getattr(self, "_authentication_context_result", None)
        if not isinstance(result, dict):
            result = build_f10_authentication_context(
                authentication_source=getattr(self, "authentication_source", "local_demo_fixture"),
                local_demo_employee_actor_id=getattr(self, "local_demo_employee_actor_id", None),
                trusted_gateway_subject_id=getattr(self, "trusted_gateway_subject_id", ""),
                trusted_gateway_groups=getattr(self, "trusted_gateway_groups", None),
                trace_id=getattr(self, "trace_id", ""),
            )
            self._authentication_context_result = result
        return result

    def route_context(self) -> Data:
        result = self._result()
        selected = "success_path" if result.get("ok") is True else "blocked_path"
        for output_name in _OUTPUTS:
            if output_name != selected:
                self.stop(output_name)
        graph = getattr(self, "graph", None)
        exclude = getattr(graph, "exclude_branches_conditionally", None) if graph is not None else None
        if callable(exclude):
            exclude(self._component_id(), [output_name for output_name in _OUTPUTS if output_name != selected])
        self.status = {
            "ok": result.get("ok"),
            "status": result.get("status"),
            "source": result.get("source"),
            "route": selected,
        }
        current_output = str(getattr(self, "_current_output", "") or "")
        if current_output and current_output != selected:
            return Data(data={})
        return Data(data=copy.deepcopy(result))
