from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from lfx.custom import Component
from lfx.io import IntInput, MessageTextInput, Output
from lfx.schema import Data, Message


ALLOWED_CHANNELS = {"native_hitl"}
SCHEMA_VERSION = "work-request-envelope/v1"
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)\b(?:api[_-]?key|password|passwd|secret|access[_-]?token|client[_-]?secret|authorization|cookie|session)\s*[:=]\s*[\"']?[^\s,;]{8,}"),
    re.compile(r"(?i)\b(?:bearer|basic)\s+\S{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^/@\s]+:[^/@\s]+@"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def _safe_text(value: Any, maximum: int, *, strip: bool = True) -> str:
    text = "" if value is None else str(value)
    if strip:
        text = text.strip()
    return text[:maximum]


def _input_text(value: Any, maximum: int, *, strip: bool = True) -> str:
    """Read a Langflow Message/Data value without serializing its wrapper."""

    if isinstance(value, str):
        return _safe_text(value, maximum, strip=strip)
    text = getattr(value, "text", None)
    if isinstance(text, str):
        return _safe_text(text, maximum, strip=strip)
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        for key in ("text", "value", "input_value"):
            candidate = data.get(key)
            if isinstance(candidate, str):
                return _safe_text(candidate, maximum, strip=strip)
    return _safe_text(value, maximum, strip=strip)


def _stable_id(prefix: str, *parts: str) -> str:
    material = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(material).hexdigest()[:24]}"


def _utc_text(value: Any) -> str:
    supplied = _safe_text(value, 64)
    if supplied:
        parsed = datetime.fromisoformat(supplied.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone is required")
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_work_request_envelope(
    request_text: Any,
    *,
    additional_prompt: Any = "",
    team_name: Any,
    employee_id: Any,
    catalog_scope_id: Any = "default",
    session_id: Any = "",
    channel_mode: Any = "native_hitl",
    work_definition_id: Any = "",
    turn_id: Any = "",
    language: Any = "ko",
    submitted_at: Any = "",
    max_request_chars: Any = 50_000,
    max_prompt_chars: Any = 20_000,
) -> dict[str, Any]:
    """Create a bounded envelope without concatenating or rewriting user text."""
    try:
        request_limit = max(1, min(int(max_request_chars), 200_000))
        prompt_limit = max(0, min(int(max_prompt_chars), 100_000))
    except (TypeError, ValueError):
        request_limit, prompt_limit = 50_000, 20_000

    request_source = request_text
    prompt_source = additional_prompt
    bounded_request = _input_text(request_source, request_limit + 1, strip=False)
    bounded_prompt = _input_text(prompt_source, prompt_limit + 1, strip=False)
    raw_request = bounded_request[:request_limit]
    raw_prompt = bounded_prompt[:prompt_limit]
    team = _input_text(team_name, 200)
    employee = _input_text(employee_id, 128)
    # The catalog has one active shared scope in the current F00/F20 design.
    # Keep that internal partition stable while retaining the human-readable
    # team name on the WorkDefinition for reporting and audit.
    tenant = _input_text(catalog_scope_id, 129) or "default"
    owner = employee if ID_PATTERN.fullmatch(employee) else _stable_id("employee", employee)
    session = _input_text(session_id, 129)
    channel = _safe_text(channel_mode, 40).lower()
    trace_id = f"trace-{uuid.uuid4()}"

    missing = [
        name
        for name, value in (("request_text", raw_request.strip()), ("team_name", team), ("employee_id", employee), ("session_id", session))
        if not value
    ]
    if missing == ["session_id"]:
        return {
            "ok": False,
            "status": "BLOCKED",
            "artifact_refs": [],
            "error": {
                "code": "RUNTIME_SESSION_REQUIRED",
                "message": "native HITL을 재개하려면 Langflow 실행 session이 필요합니다. 실행 화면에서 새 세션으로 다시 시작하세요.",
                "retryable": True,
                "details": {},
            },
            "resume": None,
            "trace_id": trace_id,
        }
    if missing:
        return {
            "ok": False,
            "status": "BLOCKED",
            "artifact_refs": [],
            "error": {
                "code": "WORK_REQUEST_REQUIRED_FIELD_MISSING",
                "message": "업무 요청에 필요한 필드가 없습니다.",
                "retryable": False,
                "details": {"fields": missing},
            },
            "resume": None,
            "trace_id": trace_id,
        }
    supplied_work_id = str(work_definition_id or "").strip()
    supplied_turn_id = str(turn_id or "").strip()
    invalid_ids = [
        name
        for name, value in (
            ("catalog_scope_id", tenant),
            ("owner_id", owner),
            ("session_id", session),
            ("work_definition_id", supplied_work_id),
            ("turn_id", supplied_turn_id),
        )
        if value and not ID_PATTERN.fullmatch(value)
    ]
    if invalid_ids:
        return {
            "ok": False,
            "status": "BLOCKED",
            "artifact_refs": [],
            "error": {
                "code": "WORK_IDENTITY_INVALID",
                "message": "업무 식별자는 ASCII 영문·숫자로 시작하고 . _ : - 만 사용해 128자 이하여야 합니다.",
                "retryable": False,
                "details": {"fields": invalid_ids, "maximum_length": 128},
            },
            "resume": None,
            "trace_id": trace_id,
        }
    if len(bounded_request) > request_limit or len(bounded_prompt) > prompt_limit:
        return {
            "ok": False,
            "status": "BLOCKED",
            "artifact_refs": [],
            "error": {
                "code": "WORK_REQUEST_SIZE_LIMIT_EXCEEDED",
                "message": "업무 요청 또는 추가 프롬프트가 허용 길이를 초과했습니다.",
                "retryable": False,
                "details": {"max_request_chars": request_limit, "max_prompt_chars": prompt_limit},
            },
            "resume": None,
            "trace_id": trace_id,
        }
    if channel not in ALLOWED_CHANNELS:
        return {
            "ok": False,
            "status": "BLOCKED",
            "artifact_refs": [],
            "error": {
                "code": "WORK_CHANNEL_INVALID",
                "message": "지원하지 않는 업무 정의 채널입니다.",
                "retryable": False,
                "details": {"allowed": sorted(ALLOWED_CHANNELS)},
            },
            "resume": None,
            "trace_id": trace_id,
        }
    secret_fields = [
        field
        for field, text in (("request_text", raw_request), ("additional_prompt", raw_prompt))
        if any(pattern.search(text) for pattern in SECRET_VALUE_PATTERNS)
    ]
    if secret_fields:
        return {
            "ok": False,
            "status": "BLOCKED",
            "artifact_refs": [],
            "error": {
                "code": "WORK_REQUEST_SECRET_MATERIAL_DETECTED",
                "message": "업무 요청 원문에 credential 또는 secret 원문이 포함되어 저장을 중단했습니다.",
                "retryable": False,
                "details": {"fields": secret_fields},
            },
            "resume": None,
            "trace_id": trace_id,
        }

    try:
        submitted = _utc_text(submitted_at)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "status": "BLOCKED",
            "artifact_refs": [],
            "error": {
                "code": "WORK_REQUEST_TIMESTAMP_INVALID",
                "message": "submitted_at은 timezone을 포함한 ISO-8601 시각이어야 합니다.",
                "retryable": False,
                "details": {},
            },
            "resume": None,
            "trace_id": trace_id,
        }
    request_digest = hashlib.sha256(raw_request.encode("utf-8")).hexdigest()
    work_id = supplied_work_id or _stable_id("wd", tenant, owner, session)
    source_turn_id = supplied_turn_id or _stable_id("turn", session, request_digest)
    envelope = {
        "schema_version": SCHEMA_VERSION,
        "work_definition_id": work_id,
        "tenant_id": tenant,
        "owner_id": owner,
        "session_id": session,
        "team_name": team,
        "employee_id": employee,
        "channel_mode": channel,
        "expected_revision": 0,
        "source_request": {
            "turn_id": source_turn_id,
            "raw_text": raw_request,
            "language": _safe_text(language, 20).lower() or "und",
            "submitted_at": submitted,
            "sha256": request_digest,
        },
        # Keep supplemental instructions in a separate trust/data field.  It is
        # never concatenated with the user's source request by this component.
        "additional_prompt": {
            "raw_text": raw_prompt,
            "sha256": hashlib.sha256(raw_prompt.encode("utf-8")).hexdigest(),
        },
    }
    return {
        "ok": True,
        "status": "INTAKE",
        "artifact_refs": [{"kind": "work_definition", "id": work_id}],
        "envelope": envelope,
        "trace_id": trace_id,
    }


class WorkRequestEnvelopeComponent(Component):
    display_name = "10 업무 요청 Envelope"
    description = "업무 원문·추가 설계 프롬프트와 팀 명·사번을 분리해 bounded envelope로 만듭니다. HITL session은 실행 문맥에서 자동으로 받습니다."
    icon = "Inbox"
    name = "WorkRequestEnvelope"

    inputs = [
        MessageTextInput(
            name="request_text",
            display_name="업무 설명 원문",
            required=True,
            input_types=["Message", "Data", "JSON"],
        ),
        MessageTextInput(
            name="additional_prompt",
            display_name="추가 설계 프롬프트",
            value="",
            required=False,
            input_types=["Message", "Data", "JSON"],
        ),
        MessageTextInput(name="team_name", display_name="팀 명", required=True),
        MessageTextInput(name="employee_id", display_name="사번", required=True),
        MessageTextInput(
            name="catalog_scope_id",
            display_name="카탈로그 데이터 영역 ID (관리자용)",
            value="default",
            required=False,
            advanced=True,
            info="현재 공용 카탈로그 snapshot/Skill 영역입니다. 팀 명과는 별개이며 운영 관리자만 변경합니다.",
        ),
        MessageTextInput(name="work_definition_id", display_name="기존 WorkDefinition ID", value="", advanced=True),
        MessageTextInput(name="turn_id", display_name="Turn ID", value="", advanced=True),
        MessageTextInput(name="language", display_name="언어", value="ko", advanced=True),
        MessageTextInput(name="submitted_at", display_name="제출 시각(ISO-8601)", value="", advanced=True),
        IntInput(name="max_request_chars", display_name="업무 원문 최대 글자", value=50000, advanced=True),
        IntInput(name="max_prompt_chars", display_name="추가 프롬프트 최대 글자", value=20000, advanced=True),
    ]
    outputs = [
        Output(name="request_envelope", display_name="업무 요청 Envelope", method="build_envelope", types=["Data"]),
        Output(name="request_message", display_name="업무 요청 메시지", method="build_request_message", types=["Message"]),
        Output(name="employee_actor_id", display_name="사번 기반 실행자 ID", method="build_employee_actor_id", types=["Message"]),
    ]

    def _runtime_session_id(self) -> str:
        """Resolve one stable identity for an initial run and its HITL resume.

        ``graph.session_id`` can be the persisted Flow/canvas session rather
        than an individual execution.  Using it first makes a new Playground
        run reuse a prior WorkDefinition and native-HITL answer batch.  The
        Langflow graph ``run_id`` is unique for an initial execution and is
        restored for its native Human Input checkpoint/resume, so it is the
        correct first choice here.  Keep the older values only as compatibility
        fallbacks for graph adapters that do not expose ``run_id``.
        """

        def runtime_value(value: Any) -> str:
            try:
                return _input_text(value, 129)
            # Some Langflow graph adapters lazily resolve runtime fields. A
            # missing or failed value must not prevent compatible fallbacks.
            except Exception:  # noqa: BLE001 - third-party graph accessor
                return ""

        try:
            graph = getattr(self, "graph", None)
        except Exception:  # noqa: BLE001 - third-party component accessor
            graph = None
        try:
            run_id = runtime_value(getattr(graph, "run_id", ""))
        except Exception:  # noqa: BLE001 - third-party graph accessor
            run_id = ""
        try:
            graph_session_id = runtime_value(getattr(graph, "session_id", ""))
        except Exception:  # noqa: BLE001 - third-party graph accessor
            graph_session_id = ""
        try:
            component_session_id = runtime_value(getattr(self, "_session_id", ""))
        except Exception:  # noqa: BLE001 - third-party component accessor
            component_session_id = ""
        return run_id or graph_session_id or component_session_id

    def _result(self) -> dict[str, Any]:
        """Build once so multiple Canvas outputs do not create divergent IDs."""

        result = getattr(self, "_envelope_result", None)
        if isinstance(result, dict):
            return result
        result = build_work_request_envelope(
            getattr(self, "request_text", ""),
            additional_prompt=getattr(self, "additional_prompt", ""),
            team_name=getattr(self, "team_name", ""),
            employee_id=getattr(self, "employee_id", ""),
            catalog_scope_id=getattr(self, "catalog_scope_id", "default"),
            session_id=self._runtime_session_id(),
            channel_mode="native_hitl",
            work_definition_id=getattr(self, "work_definition_id", ""),
            turn_id=getattr(self, "turn_id", ""),
            language=getattr(self, "language", "ko"),
            submitted_at=getattr(self, "submitted_at", ""),
            max_request_chars=getattr(self, "max_request_chars", 50000),
            max_prompt_chars=getattr(self, "max_prompt_chars", 20000),
        )
        self._envelope_result = result
        self.status = {"ok": result["ok"], "status": result["status"]}
        return result

    def build_envelope(self) -> Data:
        return Data(data=self._result())

    def build_request_message(self) -> Message:
        result = self._result()
        if not result.get("ok"):
            self.stop("request_message")
            return Message(text="")
        envelope = result.get("envelope")
        if not isinstance(envelope, dict):
            self.stop("request_message")
            return Message(text="")
        return Message(text=json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))[:80_000])

    def build_employee_actor_id(self) -> Message:
        result = self._result()
        if not result.get("ok"):
            self.stop("employee_actor_id")
            return Message(text="")
        envelope = result.get("envelope") if isinstance(result.get("envelope"), dict) else {}
        return Message(text=str(envelope.get("owner_id") or ""))
