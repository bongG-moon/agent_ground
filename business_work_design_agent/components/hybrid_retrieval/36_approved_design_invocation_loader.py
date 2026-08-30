from __future__ import annotations

"""Load the immutable, authorized inputs for an F20 agent-design invocation.

This Langflow 1.11 component is intentionally standalone.  It does not trust
the WorkDefinition body carried on a graph edge: the edge is used only to
identify the approval receipt, and the canonical approved record is read back
from MongoDB before any design input is emitted.
"""

import copy
import hashlib
import hmac
import json
import math
import re
import uuid
from datetime import date, datetime, timezone
from typing import Any, Callable

from lfx.custom import Component
from lfx.io import DataInput, IntInput, MessageTextInput, Output, SecretStrInput
from lfx.schema import Data
from pymongo import MongoClient
from pymongo.errors import (
    ConfigurationError,
    ConnectionFailure,
    OperationFailure,
    PyMongoError,
    ServerSelectionTimeoutError,
)


SCHEMA_VERSION = "agent-design-invocation/v1"
WORK_REQUEST_SCHEMA_VERSION = "work-request-envelope/v1"
WORK_DEFINITION_SCHEMA_VERSION = "work-definition/v1"
IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
COLLECTION_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,200}$")
APPROVED_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_GROUPS = 100
MAX_GROUP_INPUT_CHARS = 20_000
MAX_SKILL_ENTRIES = 500
MAX_DESIGN_PROMPT_CHARS = 20_000
SEMANTIC_FIELDS = (
    "goal",
    "trigger",
    "scope_in",
    "scope_out",
    "actors",
    "systems",
    "inputs",
    "outputs",
    "steps",
    "decisions",
    "exceptions",
    "frequency_volume",
    "sla",
    "pains",
    "risks_controls",
    "constraints",
    "success_criteria",
    "automation_intent",
    "assumptions",
    "unresolved",
    "as_is_graph",
)
UNORDERED_LIST_KEYS = {
    "scope_in",
    "scope_out",
    "actors",
    "systems",
    "inputs",
    "outputs",
    "pains",
    "risks_controls",
    "constraints",
    "success_criteria",
    "assumptions",
    "unresolved",
    "nodes",
    "edges",
    "evidence_turn_ids",
    "conflicting_values",
}
NON_SEMANTIC_KEYS = {
    "x",
    "y",
    "position",
    "position_absolute",
    "style",
    "selected",
    "expanded",
    "display_order",
    "created_at",
    "updated_at",
    "submitted_at",
    "expires_at",
    "trace_id",
    "run_id",
    "job_id",
    "last_updated_revision",
    "confidence",
    "evidence_turn_ids",
    "processed_answer_batches",
}
SKILL_PUBLIC_KEYS = {
    "tenant_id",
    "skill_id",
    "name",
    "version",
    "prompt_sha256",
    "trigger_rules",
    "near_miss_rules",
    "prompt_text",
    "forbidden_actions",
    "status",
    "acl",
    "approved_by",
    "approved_at",
    "match_reason",
    "target_stage",
}
SECRET_VALUE_PATTERNS = (
    re.compile(
        r"(?i)\b(?:api[_-]?key|password|passwd|secret|access[_-]?token|client[_-]?secret|authorization|cookie|session)"
        r"\s*[:=]\s*[\"']?[^\s,;]{8,}"
    ),
    re.compile(r"(?i)\b(?:bearer|basic)\s+\S{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^/@\s]+:[^/@\s]+@"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
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
    return copy.deepcopy(parsed) if isinstance(parsed, dict) else {}


def _secret(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    return str(getter() if callable(getter) else value or "").strip()


def _json_safe(value: Any) -> Any:
    """Return a strict JSON-compatible projection of public MongoDB data.

    PyMongo rehydrates timestamps as ``datetime`` objects, while F20 receives
    this component's output as strict JSON text through Run Flow.  Normalizing
    the complete public payload here keeps the Data output and its ``text``
    projection identical; merely adding ``default=str`` to ``json.dumps``
    would leave non-serializable values in the Data object itself.
    """

    if value is None or type(value) in {str, int, bool}:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("DESIGN_INVOCATION_NONFINITE_NUMBER")
        return value
    if isinstance(value, datetime):
        normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return normalized.isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        projected: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError("DESIGN_INVOCATION_JSON_KEY_INVALID")
            projected[key] = _json_safe(item)
        return projected
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    raise TypeError("DESIGN_INVOCATION_JSON_VALUE_INVALID")


def _failure(
    code: str,
    message: str,
    trace_id: str,
    *,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Details are deliberately restricted to non-sensitive labels and counts.
    return {
        "ok": False,
        "status": "BLOCKED",
        "artifact_refs": [],
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": copy.deepcopy(details or {}),
        },
        "resume": None,
        "trace_id": trace_id,
    }


def _identity(value: Any) -> str:
    return value if type(value) is str and IDENTITY_PATTERN.fullmatch(value) is not None else ""


def _canonicalize(value: Any, parent_key: str = "") -> Any:
    if isinstance(value, dict):
        return {
            key: _canonicalize(value[key], key)
            for key in sorted(value)
            if key not in NON_SEMANTIC_KEYS and not key.startswith("ui_") and not key.startswith("render_")
        }
    if isinstance(value, list):
        items = [_canonicalize(item, parent_key) for item in value]
        if parent_key in UNORDERED_LIST_KEYS:
            items.sort(
                key=lambda item: json.dumps(
                    item,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
            )
        return items
    if isinstance(value, float):
        return float(format(value, ".15g"))
    return value


def _approved_semantic_hash(work: dict[str, Any]) -> str:
    semantic = {field: copy.deepcopy(work.get(field)) for field in SEMANTIC_FIELDS}
    canonical = _canonicalize(semantic)
    text = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _request_envelope(value: Any) -> dict[str, Any]:
    payload = _payload(value)
    if "envelope" in payload:
        if payload.get("ok") is not True or not isinstance(payload.get("envelope"), dict):
            return {}
        return copy.deepcopy(payload["envelope"])
    return payload


def _approval_work(value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = _payload(value)
    work = payload.get("work_definition") if isinstance(payload.get("work_definition"), dict) else {}
    return payload, copy.deepcopy(work)


def _group_source(value: Any) -> Any:
    data = getattr(value, "data", None)
    if isinstance(data, (dict, list, tuple, set)):
        value = data
    if isinstance(value, dict):
        if set(value) - {"groups"}:
            raise ValueError("AUTHENTICATED_GROUPS_INVALID")
        return value.get("groups", [])
    if isinstance(value, str):
        if len(value) > MAX_GROUP_INPUT_CHARS:
            raise ValueError("AUTHENTICATED_GROUPS_LIMIT_EXCEEDED")
        text = value.strip()
        if not text:
            return []
        if text.startswith("[") or text.startswith("{"):
            parsed = json.loads(text)
            return _group_source(parsed)
        return re.split(r"[,;\n]", text)
    return value


def _authenticated_groups(value: Any) -> list[str]:
    try:
        source = _group_source(value)
    except json.JSONDecodeError as exc:
        raise ValueError("AUTHENTICATED_GROUPS_INVALID") from exc
    if not isinstance(source, (list, tuple, set)):
        raise ValueError("AUTHENTICATED_GROUPS_INVALID")
    if len(source) > MAX_GROUPS:
        raise ValueError("AUTHENTICATED_GROUPS_LIMIT_EXCEEDED")
    groups: list[str] = []
    for item in source:
        group = _identity(item)
        if not group:
            raise ValueError("AUTHENTICATED_GROUPS_INVALID")
        normalized = group.lower()
        if normalized not in groups:
            groups.append(normalized)
    return sorted(groups)


def _additional_prompt(envelope: dict[str, Any]) -> str:
    supplied = envelope.get("additional_prompt")
    if supplied is None:
        return ""
    if isinstance(supplied, str):
        text = supplied
        expected_hash = ""
    elif isinstance(supplied, dict) and set(supplied) <= {"raw_text", "sha256"}:
        text = supplied.get("raw_text", "")
        expected_hash = supplied.get("sha256", "")
    else:
        raise ValueError("DESIGN_PROMPT_INVALID")
    if not isinstance(text, str) or len(text) > MAX_DESIGN_PROMPT_CHARS:
        raise ValueError("DESIGN_PROMPT_INVALID")
    actual_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if expected_hash and (
        type(expected_hash) is not str
        or not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", expected_hash)
        or not hmac.compare_digest(expected_hash.removeprefix("sha256:"), actual_hash)
    ):
        raise ValueError("DESIGN_PROMPT_HASH_MISMATCH")
    if any(pattern.search(text) for pattern in SECRET_VALUE_PATTERNS):
        raise ValueError("DESIGN_PROMPT_SECRET_MATERIAL_DETECTED")
    return text


def _public_work(document: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(document)
    for key in ("_id", "mutation_receipts", "pending_action"):
        result.pop(key, None)
    return _json_safe(result)


def _public_skill(document: dict[str, Any]) -> dict[str, Any]:
    return _json_safe({key: copy.deepcopy(document[key]) for key in SKILL_PUBLIC_KEYS if key in document})


def _bounded_skills(collection: Any, tenant_id: str, maximum: int) -> tuple[list[dict[str, Any]], bool]:
    cursor = collection.find({"tenant_id": tenant_id, "status": "active"})
    try:
        cursor = cursor.sort([("skill_id", 1), ("version", 1)])
    except TypeError:
        cursor = cursor.sort("skill_id", 1)
    cursor = cursor.limit(maximum + 1)
    loaded = list(cursor)
    skills: list[dict[str, Any]] = []
    for document in loaded[:maximum]:
        if not isinstance(document, dict):
            continue
        if document.get("tenant_id") != tenant_id or document.get("status") != "active":
            continue
        skills.append(_public_skill(document))
    return skills, len(loaded) > maximum


def load_approved_design_invocation(
    approval_result: Any,
    request_envelope: Any,
    *,
    authenticated_subject_id: Any,
    authenticated_groups: Any = None,
    mongodb_uri: Any,
    mongo_database: Any,
    work_collection: Any = "work_definitions",
    pointer_collection: Any = "catalog_active_pointers",
    skill_registry_collection: Any = "skill_registry",
    timeout_ms: Any = 5000,
    max_skill_entries: Any = 200,
    trace_id: Any = "",
    client_factory: Callable[..., Any] = MongoClient,
) -> dict[str, Any]:
    """Build a fail-closed, Mongo-backed invocation for the agent-design flow."""

    safe_trace = str(trace_id or f"trace-{uuid.uuid4()}")[:200]
    try:
        approval, approved_edge_work = _approval_work(approval_result)
        request = _request_envelope(request_envelope)
        subject_id = _identity(authenticated_subject_id)
        groups = _authenticated_groups(authenticated_groups)
        uri = _secret(mongodb_uri)
        database_name = str(mongo_database or "").strip()
        work_name = str(work_collection or "work_definitions").strip()
        pointer_name = str(pointer_collection or "catalog_active_pointers").strip()
        skills_name = str(skill_registry_collection or "skill_registry").strip()
        timeout = max(1000, min(int(timeout_ms), 30_000))
        skill_limit = max(1, min(int(max_skill_entries), MAX_SKILL_ENTRIES))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        code = str(exc) if str(exc) in {
            "AUTHENTICATED_GROUPS_INVALID",
            "AUTHENTICATED_GROUPS_LIMIT_EXCEEDED",
        } else "DESIGN_INVOCATION_INPUT_INVALID"
        return _failure(code, "설계 호출 입력 또는 인증 group 형식이 유효하지 않습니다.", safe_trace)

    if approval.get("ok") is not True or approval.get("status") != "APPROVED" or not approved_edge_work:
        return _failure(
            "APPROVAL_RESULT_INVALID",
            "명시적인 APPROVED 승인 결과가 필요합니다.",
            safe_trace,
        )
    if request.get("schema_version") != WORK_REQUEST_SCHEMA_VERSION:
        return _failure(
            "WORK_REQUEST_ENVELOPE_INVALID",
            "검증 가능한 업무 요청 envelope가 필요합니다.",
            safe_trace,
        )
    if not subject_id:
        return _failure(
            "AUTHENTICATED_SUBJECT_INVALID",
            "trusted authenticated_subject_id가 필요합니다.",
            safe_trace,
        )
    if not uri or not COLLECTION_PATTERN.fullmatch(database_name):
        return _failure(
            "DESIGN_INVOCATION_CONFIG_MISSING",
            "MongoDB 설계 호출 설정이 필요합니다.",
            safe_trace,
        )
    invalid_collections = [
        label
        for label, name in (
            ("work_collection", work_name),
            ("pointer_collection", pointer_name),
            ("skill_registry_collection", skills_name),
        )
        if COLLECTION_PATTERN.fullmatch(name) is None
    ]
    if invalid_collections:
        return _failure(
            "DESIGN_INVOCATION_COLLECTION_INVALID",
            "MongoDB collection 이름이 허용 형식이 아닙니다.",
            safe_trace,
            details={"fields": invalid_collections},
        )

    approval_identity = {
        "tenant_id": approved_edge_work.get("tenant_id"),
        "work_definition_id": approved_edge_work.get("work_definition_id"),
        "owner_id": approved_edge_work.get("owner_id"),
        "session_id": approved_edge_work.get("session_id"),
    }
    request_identity = {key: request.get(key) for key in approval_identity}
    if any(not _identity(value) for value in approval_identity.values()):
        return _failure(
            "APPROVAL_IDENTITY_INVALID",
            "승인 결과의 업무 identity가 유효하지 않습니다.",
            safe_trace,
        )
    mismatch_fields = [key for key in approval_identity if request_identity.get(key) != approval_identity[key]]
    if mismatch_fields:
        return _failure(
            "WORK_REQUEST_APPROVAL_MISMATCH",
            "업무 요청과 승인 결과의 identity가 일치하지 않습니다.",
            safe_trace,
            details={"fields": mismatch_fields},
        )
    if subject_id != str(approval_identity["owner_id"]):
        return _failure(
            "AUTHENTICATED_SUBJECT_OWNER_MISMATCH",
            "인증 주체가 승인 업무의 owner와 일치하지 않습니다.",
            safe_trace,
        )
    try:
        design_prompt = _additional_prompt(request)
    except ValueError as exc:
        code = str(exc)
        return _failure(code, "추가 설계 프롬프트 검증에 실패했습니다.", safe_trace)

    client = None
    try:
        client = client_factory(
            uri,
            serverSelectionTimeoutMS=timeout,
            connectTimeoutMS=timeout,
            socketTimeoutMS=timeout,
            retryReads=True,
        )
        client.admin.command("ping")
        database = client[database_name]
        canonical = database[work_name].find_one(
            {
                "tenant_id": approval_identity["tenant_id"],
                "work_definition_id": approval_identity["work_definition_id"],
            }
        )
        if not isinstance(canonical, dict):
            return _failure(
                "APPROVED_WORK_DEFINITION_NOT_FOUND",
                "MongoDB에서 승인 업무 정의를 찾을 수 없습니다.",
                safe_trace,
            )

        exact_fields = (
            "tenant_id",
            "work_definition_id",
            "revision",
            "approved_hash",
            "status",
            "owner_id",
            "session_id",
        )
        mismatched = [key for key in exact_fields if canonical.get(key) != approved_edge_work.get(key)]
        if mismatched:
            return _failure(
                "APPROVED_WORK_DEFINITION_MISMATCH",
                "승인 결과와 MongoDB canonical 업무 정의가 일치하지 않습니다.",
                safe_trace,
                details={"fields": mismatched},
            )
        if canonical.get("schema_version") != WORK_DEFINITION_SCHEMA_VERSION:
            return _failure(
                "APPROVED_WORK_DEFINITION_SCHEMA_INVALID",
                "MongoDB canonical 업무 정의 schema가 유효하지 않습니다.",
                safe_trace,
            )
        revision = canonical.get("revision")
        approved_hash = canonical.get("approved_hash")
        if (
            type(revision) is not int
            or revision < 0
            or canonical.get("status") != "APPROVED"
            or type(approved_hash) is not str
            or APPROVED_HASH_PATTERN.fullmatch(approved_hash) is None
        ):
            return _failure(
                "APPROVED_WORK_DEFINITION_INVALID",
                "MongoDB canonical 업무 정의가 유효한 APPROVED 상태가 아닙니다.",
                safe_trace,
            )
        actual_hash = _approved_semantic_hash(canonical)
        if not hmac.compare_digest(approved_hash, actual_hash):
            return _failure(
                "APPROVED_WORK_DEFINITION_HASH_MISMATCH",
                "승인 이후 업무 의미가 변경되어 설계를 중단했습니다.",
                safe_trace,
            )
        if canonical.get("owner_id") != subject_id:
            return _failure(
                "AUTHENTICATED_SUBJECT_OWNER_MISMATCH",
                "인증 주체가 canonical 업무 owner와 일치하지 않습니다.",
                safe_trace,
            )
        if canonical.get("channel_mode") != "native_hitl" or request.get("channel_mode") != "native_hitl":
            return _failure(
                "WORK_REQUEST_CHANNEL_INVALID",
                "업무 요청과 canonical 업무 정의는 native HITL channel이어야 합니다.",
                safe_trace,
            )

        pointer = database[pointer_name].find_one({"tenant_id": canonical["tenant_id"]})
        if not isinstance(pointer, dict) or pointer.get("tenant_id") != canonical["tenant_id"]:
            return _failure(
                "ACTIVE_CATALOG_POINTER_NOT_FOUND",
                "tenant 활성 catalog snapshot을 찾을 수 없습니다.",
                safe_trace,
            )
        snapshot_id = pointer.get("active_snapshot_id") or pointer.get("snapshot_id")
        if not _identity(snapshot_id):
            return _failure(
                "ACTIVE_CATALOG_SNAPSHOT_INVALID",
                "tenant 활성 catalog snapshot identity가 유효하지 않습니다.",
                safe_trace,
            )
        skills, skills_truncated = _bounded_skills(
            database[skills_name],
            str(canonical["tenant_id"]),
            skill_limit,
        )
        work = _public_work(canonical)
        return {
            "ok": True,
            "status": "READY_FOR_DESIGN",
            "schema_version": SCHEMA_VERSION,
            "artifact_refs": [
                {
                    "kind": "work_definition",
                    "id": work["work_definition_id"],
                    "revision": revision,
                    "sha256": approved_hash,
                },
                {"kind": "catalog_snapshot", "id": snapshot_id},
            ],
            "tenant_id": work["tenant_id"],
            "work_definition_id": work["work_definition_id"],
            "work_definition_revision": revision,
            "approved_hash": approved_hash,
            "owner_id": work["owner_id"],
            "session_id": work["session_id"],
            "work_definition": work,
            "acl_context": {"subject_id": subject_id, "groups": groups},
            "catalog_snapshot_id": snapshot_id,
            "skill_registry": {
                "skills": skills,
                "count": len(skills),
                "truncated": skills_truncated,
                "maximum": skill_limit,
            },
            "design_prompt": design_prompt,
            "trust_boundary": {
                "work_definition_source": "mongodb-canonical-approved",
                "catalog_snapshot_source": "mongodb-active-pointer",
                "skill_registry_source": "mongodb-active-only",
                "authenticated_subject_verified": True,
            },
            "trace_id": safe_trace,
        }
    except (TypeError, ValueError, json.JSONDecodeError):
        return _failure(
            "DESIGN_INVOCATION_DATA_INVALID",
            "MongoDB 설계 호출 데이터를 검증할 수 없습니다.",
            safe_trace,
        )
    except (ConfigurationError, ConnectionFailure, ServerSelectionTimeoutError):
        return _failure(
            "DESIGN_INVOCATION_MONGODB_UNAVAILABLE",
            "설계 호출 MongoDB에 연결할 수 없습니다.",
            safe_trace,
            retryable=True,
        )
    except (OperationFailure, PyMongoError):
        return _failure(
            "DESIGN_INVOCATION_MONGODB_READ_FAILED",
            "설계 호출에 필요한 MongoDB 데이터를 읽지 못했습니다.",
            safe_trace,
            retryable=True,
        )
    except Exception:  # noqa: BLE001 - fail closed without echoing driver/runtime details.
        return _failure(
            "DESIGN_INVOCATION_LOAD_FAILED",
            "설계 호출 입력을 안전하게 구성하지 못했습니다.",
            safe_trace,
        )
    finally:
        if client is not None:
            client.close()


class ApprovedDesignInvocationLoaderComponent(Component):
    display_name = "36 Approved Design Invocation Loader"
    description = "F10 승인 receipt를 MongoDB canonical 승인본·활성 snapshot·Skill registry와 재검증해 F20 입력을 만듭니다."
    icon = "ShieldCheck"
    name = "ApprovedDesignInvocationLoader"

    inputs = [
        DataInput(name="approval_result", display_name="F10 Approved Result", input_types=["Data", "JSON"], required=True),
        DataInput(name="request_envelope", display_name="Original Work Request Envelope", input_types=["Data", "JSON"], required=True),
        MessageTextInput(
            name="authenticated_subject_id",
            display_name="Trusted Authenticated Subject ID",
            required=True,
            info="로컬 F10 데모에서는 Envelope의 사번 기반 actor를 연결할 수 있습니다. production에서는 반드시 인증 gateway가 주입한 subject로 교체합니다.",
        ),
        MessageTextInput(
            name="authenticated_groups",
            display_name="Trusted Authenticated Groups",
            value="[]",
            required=False,
            input_types=["Data", "JSON"],
            info="bounded JSON list, list, 또는 comma/newline 구분 문자열입니다.",
        ),
        SecretStrInput(name="mongodb_uri", display_name="MongoDB URI", required=True),
        MessageTextInput(name="mongo_database", display_name="MongoDB Database", value="business_work_design", required=True),
        MessageTextInput(name="work_collection", display_name="WorkDefinition Collection", value="work_definitions", advanced=True),
        MessageTextInput(name="pointer_collection", display_name="Active Pointer Collection", value="catalog_active_pointers", advanced=True),
        MessageTextInput(name="skill_registry_collection", display_name="Skill Registry Collection", value="skill_registry", advanced=True),
        IntInput(name="timeout_ms", display_name="MongoDB Timeout(ms)", value=5000, advanced=True),
        IntInput(name="max_skill_entries", display_name="Maximum Active Skill Entries", value=200, advanced=True),
        MessageTextInput(name="trace_id", display_name="Trace ID", value="", advanced=True),
    ]
    outputs = [
        Output(name="success_path", display_name="Verified Design Invocation", method="route_invocation", types=["Data"], group_outputs=True),
        Output(name="blocked_path", display_name="Blocked Design Invocation", method="route_invocation", types=["Data"], group_outputs=True),
    ]

    def _component_id(self) -> str:
        return str(getattr(self, "_id", "") or self.name)[:200]

    def _select_output_route(self, selected: str) -> None:
        output_names = ("success_path", "blocked_path")
        non_selected = [output_name for output_name in output_names if output_name != selected]
        for output_name in non_selected:
            self.stop(output_name)
        graph = getattr(self, "graph", None)
        exclude = getattr(graph, "exclude_branches_conditionally", None) if graph is not None else None
        if callable(exclude):
            exclude(self._component_id(), non_selected)

    def _is_nonselected_group_output(self, selected: str) -> bool:
        current_output = str(getattr(self, "_current_output", "") or "")
        return bool(current_output and current_output in {"success_path", "blocked_path"} and current_output != selected)

    def route_invocation(self) -> Data:
        result = getattr(self, "_invocation_result", None)
        if not isinstance(result, dict):
            result = load_approved_design_invocation(
                getattr(self, "approval_result", None),
                getattr(self, "request_envelope", None),
                authenticated_subject_id=getattr(self, "authenticated_subject_id", ""),
                authenticated_groups=getattr(self, "authenticated_groups", None),
                mongodb_uri=getattr(self, "mongodb_uri", ""),
                mongo_database=getattr(self, "mongo_database", ""),
                work_collection=getattr(self, "work_collection", "work_definitions"),
                pointer_collection=getattr(self, "pointer_collection", "catalog_active_pointers"),
                skill_registry_collection=getattr(self, "skill_registry_collection", "skill_registry"),
                timeout_ms=getattr(self, "timeout_ms", 5000),
                max_skill_entries=getattr(self, "max_skill_entries", 200),
                trace_id=getattr(self, "trace_id", ""),
                client_factory=MongoClient,
            )
            self._invocation_result = result
        try:
            routed = _json_safe(result)
            routed["text"] = json.dumps(
                routed,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError):
            result = _failure(
                "DESIGN_INVOCATION_SERIALIZATION_INVALID",
                "설계 호출 입력을 JSON 형식으로 안전하게 만들 수 없습니다.",
                str(getattr(self, "trace_id", "") or f"trace-{uuid.uuid4()}")[:200],
            )
            self._invocation_result = result
            routed = copy.deepcopy(result)
            routed["text"] = json.dumps(routed, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

        selected = "success_path" if result.get("ok") is True else "blocked_path"
        self._select_output_route(selected)
        self.status = {
            "ok": result.get("ok"),
            "status": result.get("status"),
            "route": selected,
            "work_definition_id": result.get("work_definition_id"),
        }
        # Run Flow converts a Data input through Data.get_text().  ``routed``
        # was already normalized above, so structured downstream consumers and
        # F20's strict JSON text see the same timestamp representation.
        if self._is_nonselected_group_output(selected):
            return Data(data={})
        return Data(data=routed)
