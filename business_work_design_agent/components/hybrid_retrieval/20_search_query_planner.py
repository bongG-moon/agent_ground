from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
import uuid
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, IntInput, MessageTextInput, Output
from lfx.schema import Data


ALLOWED_ASSET_TYPES = {"component", "flow"}
SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)\b(?:api[_-]?key|password|passwd|secret|access[_-]?token|client[_-]?secret|authorization|cookie|session)\s*[:=]\s*[\"']?[^\s,;]{8,}"),
    re.compile(r"(?i)\b(?:bearer|basic)\s+\S{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^/@\s]+:[^/@\s]+@"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
SECRET_KEY_TOKENS = {
    "apikey", "authorization", "clientsecret", "cookie", "credential", "password", "passwd",
    "privatekey", "pwd", "session", "smsession", "secret", "token",
}
ALLOWED_QUERY_KINDS = {"purpose", "capability", "exact", "risk", "reporting"}
WORK_DEFINITION_SCHEMA_VERSION = "work-definition/v1"
IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
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
    "scope_in", "scope_out", "actors", "systems", "inputs", "outputs", "pains", "risks_controls",
    "constraints", "success_criteria", "assumptions", "unresolved", "nodes", "edges", "evidence_turn_ids",
    "conflicting_values",
}
NON_SEMANTIC_KEYS = {
    "x", "y", "position", "position_absolute", "style", "selected", "expanded", "display_order",
    "created_at", "updated_at", "submitted_at", "expires_at", "trace_id", "run_id", "job_id",
    "last_updated_revision", "confidence", "evidence_turn_ids", "processed_answer_batches",
}


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return data
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _text_field(value: Any) -> tuple[str, str]:
    if isinstance(value, str):
        return value.strip(), "unknown"
    if isinstance(value, dict):
        return str(value.get("value") or value.get("text") or "").strip(), str(value.get("status") or "unknown")
    return "", "unknown"


def _safe_text(value: Any, maximum: int = 2000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:maximum]


def _is_identity(value: Any) -> bool:
    return type(value) is str and IDENTITY_PATTERN.fullmatch(value) is not None


def _secret_key(value: Any) -> bool:
    text = str(value or "").casefold()
    compact = re.sub(r"[^a-z0-9]", "", text)
    parts = {item for item in re.split(r"[^a-z0-9]+", text) if item}
    if ("token" in parts and parts & {"max", "limit", "budget", "count"}) or (
        "session" in parts and parts & {"timeout", "ttl"}
    ):
        return False
    if "token" in compact and any(marker in compact for marker in {"maxtoken", "tokenlimit", "tokenbudget", "tokencount"}):
        return False
    if "session" in compact and any(marker in compact for marker in {"sessiontimeout", "sessionttl"}):
        return False
    return compact in SECRET_KEY_TOKENS or bool(parts & {"token", "session", "pwd"}) or any(
        marker in compact for marker in SECRET_KEY_TOKENS
    )


def _secret_material_kind(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if any(pattern.search(str(key).strip()) for pattern in SECRET_VALUE_PATTERNS):
                return "object_key"
            if _secret_key(key):
                return "secret_field"
            found = _secret_material_kind(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _secret_material_kind(item)
            if found:
                return found
    elif isinstance(value, str) and value != "[REDACTED]" and any(
        pattern.search(value.strip()) for pattern in SECRET_VALUE_PATTERNS
    ):
        return "string_value"
    return None


def _canonical_hash(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


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
            items.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str))
        return items
    if isinstance(value, float):
        return float(format(value, ".15g"))
    return value


def _approved_work_projection(work: dict[str, Any]) -> tuple[dict[str, Any], str]:
    semantic = {field: copy.deepcopy(work.get(field)) for field in SEMANTIC_FIELDS}
    canonical_semantic = _canonicalize(semantic)
    canonical_text = json.dumps(
        canonical_semantic,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    actual_hash = "sha256:" + hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()
    projection = {
        "schema_version": str(work.get("schema_version") or "work-definition/v1"),
        "work_definition_id": str(work.get("work_definition_id") or ""),
        "tenant_id": str(work.get("tenant_id") or ""),
        "owner_id": str(work.get("owner_id") or ""),
        "session_id": str(work.get("session_id") or ""),
        "channel_mode": str(work.get("channel_mode") or ""),
        "revision": work.get("revision"),
        "status": "APPROVED",
        "approved_hash": actual_hash,
        "preview_hash": actual_hash,
        **canonical_semantic,
    }
    return projection, actual_hash


def _confirmed_terms(value: Any, maximum: int = 30) -> list[str]:
    source = value if isinstance(value, list) else []
    result: list[str] = []
    for item in source[:maximum]:
        if isinstance(item, dict):
            provenance = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
            status = str(item.get("status") or item.get("provenance_status") or provenance.get("status") or "unknown").lower()
            if status not in {"confirmed", "approved"}:
                continue
            text = _safe_text(item.get("name") or item.get("title") or item.get("value"), 200)
        else:
            continue
        if text and text not in result:
            result.append(text)
    return result


def build_design_scope(
    work_definition: Any,
    *,
    tenant_id: str,
    catalog_snapshot_id: str,
    acl_context: Any,
    design_prompt: str = "",
) -> dict[str, Any]:
    """Validate and seal every approved design input into one typed scope.

    F20 exposes these values only on this component. Downstream components
    receive this immutable identity tuple instead of independent node tweaks.
    """
    trace_id = str(uuid.uuid4())
    work = _payload(work_definition)
    acl = _payload(acl_context)
    tenant = _safe_text(tenant_id, 200)
    snapshot = _safe_text(catalog_snapshot_id, 200)
    bounded_design_prompt = _safe_text(design_prompt, 4000)
    if not _is_identity(tenant_id):
        return _error(trace_id, "TENANT_REQUIRED", "tenant_id가 필요합니다.")
    if not _is_identity(catalog_snapshot_id):
        return _error(trace_id, "CATALOG_SNAPSHOT_ID_INVALID", "활성 catalog snapshot ID 형식이 유효하지 않습니다.")
    if set(acl) - {"subject_id", "groups"}:
        return _error(trace_id, "ACL_CONTEXT_FIELDS_INVALID", "ACL context에 허용되지 않은 필드가 있습니다.")
    raw_groups = acl.get("groups", [])
    if (
        not _is_identity(acl.get("subject_id"))
        or not isinstance(raw_groups, list)
        or len(raw_groups) > 100
        or any(not _is_identity(item) for item in raw_groups)
    ):
        return _error(trace_id, "ACL_CONTEXT_IDENTITY_INVALID", "ACL subject와 group identity 형식이 유효하지 않습니다.")
    if _secret_material_kind(acl):
        return _error(trace_id, "ACL_CONTEXT_SECRET_MATERIAL_DETECTED", "ACL context에 secret 원문을 넣을 수 없습니다.")
    canonical_acl = {
        "subject_id": str(acl["subject_id"]),
        "groups": sorted({str(item).lower() for item in raw_groups}),
    }
    if not work:
        return _error(trace_id, "INVALID_WORK_DEFINITION", "승인 업무 정의가 필요합니다.")
    if any(pattern.search(bounded_design_prompt) for pattern in SECRET_VALUE_PATTERNS):
        return _error(
            trace_id,
            "DESIGN_PROMPT_SECRET_MATERIAL_DETECTED",
            "추가 설계 프롬프트에는 secret 원문을 넣을 수 없습니다.",
        )
    if type(work.get("schema_version")) is not str or work.get("schema_version") != WORK_DEFINITION_SCHEMA_VERSION:
        return _error(
            trace_id,
            "WORK_DEFINITION_SCHEMA_INVALID",
            f"승인 업무 정의 schema_version은 {WORK_DEFINITION_SCHEMA_VERSION}이어야 합니다.",
        )
    for field in ("tenant_id", "owner_id", "session_id", "work_definition_id"):
        if not _is_identity(work.get(field)):
            return _error(
                trace_id,
                "WORK_DEFINITION_IDENTITY_INVALID",
                f"승인 업무 정의 {field}가 canonical identity 형식이 아닙니다.",
            )
    if work.get("channel_mode") not in {"native_hitl", "playground"}:
        return _error(trace_id, "WORK_DEFINITION_CHANNEL_INVALID", "승인 업무 정의 channel_mode가 유효하지 않습니다.")
    state = work.get("status")
    approved_hash = work.get("approved_hash")
    work_id = work.get("work_definition_id")
    raw_revision = work.get("revision")
    if type(raw_revision) is not int:
        return _error(trace_id, "WORK_DEFINITION_REVISION_INVALID", "승인 업무 정의 revision이 필요합니다.")
    work_revision = raw_revision
    if work_revision < 0:
        return _error(trace_id, "WORK_DEFINITION_REVISION_INVALID", "승인 업무 정의 revision이 필요합니다.")
    if state != "APPROVED" or type(approved_hash) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", approved_hash):
        return _error(trace_id, "WORK_DEFINITION_NOT_APPROVED", "approved 상태와 유효한 approved_hash가 필요합니다.")
    if work.get("tenant_id") != tenant:
        return _error(trace_id, "WORK_DEFINITION_TENANT_MISMATCH", "승인 업무 정의 tenant가 설계 tenant와 다릅니다.")
    try:
        approved_work, actual_approved_hash = _approved_work_projection(work)
    except (TypeError, ValueError):
        return _error(trace_id, "WORK_DEFINITION_CANONICALIZATION_FAILED", "승인 업무 정의의 의미 필드를 canonicalize할 수 없습니다.")
    if not hmac.compare_digest(approved_hash.lower(), actual_approved_hash.lower()):
        return _error(trace_id, "WORK_DEFINITION_APPROVAL_HASH_MISMATCH", "승인 이후 업무 의미가 변경되었거나 승인 hash가 유효하지 않습니다.")
    semantic_secret_kind = _secret_material_kind(
        {field: approved_work.get(field) for field in SEMANTIC_FIELDS}
    )
    if semantic_secret_kind:
        return _error(
            trace_id,
            "WORK_DEFINITION_SECRET_MATERIAL_DETECTED",
            "승인 업무 정의의 의미 필드에 secret 원문이 포함되어 설계를 중단했습니다.",
        )
    scope_core = {
        "schema_version": "agent-design-scope/v1",
        "tenant_id": tenant,
        "catalog_snapshot_id": snapshot,
        "work_definition_id": work_id,
        "work_definition_revision": work_revision,
        "approved_hash": approved_hash,
        "work_definition": approved_work,
        "acl_context": canonical_acl,
        "design_prompt": bounded_design_prompt,
    }
    return {
        "ok": True,
        "status": "COMPLETED",
        **scope_core,
        "design_scope_sha256": _canonical_hash(scope_core),
        "trace_id": trace_id,
    }


def _step_items(value: Any, maximum: int = 100) -> list[dict[str, str]]:
    source = value if isinstance(value, list) else []
    result: list[dict[str, str]] = []
    for index, item in enumerate(source[:maximum], start=1):
        if not isinstance(item, dict):
            continue
        step_id = _safe_text(item.get("step_id") or item.get("id") or f"step-{index}", 100)
        title = _safe_text(item.get("title") or item.get("name"), 300)
        capability = _safe_text(item.get("capability") or item.get("description") or item.get("action"), 1000)
        if title or capability:
            result.append({"step_id": step_id, "title": title, "capability": capability})
    return result


def build_search_query_plan(
    work_definition: Any,
    *,
    tenant_id: str,
    catalog_snapshot_id: str,
    acl_context: Any,
    design_prompt: str = "",
    max_queries: int = 30,
) -> dict[str, Any]:
    trace_id = str(uuid.uuid4())
    scope = build_design_scope(
        work_definition,
        tenant_id=tenant_id,
        catalog_snapshot_id=catalog_snapshot_id,
        acl_context=acl_context,
        design_prompt=design_prompt,
    )
    if not scope.get("ok"):
        return scope
    work = scope["work_definition"]
    acl = scope["acl_context"]
    tenant = scope["tenant_id"]
    snapshot = scope["catalog_snapshot_id"]
    approved_hash = scope["approved_hash"]

    max_queries = max(1, min(50, int(max_queries or 30)))
    goal, _ = _text_field(work.get("goal") or work.get("purpose"))
    title, _ = _text_field(work.get("title"))
    steps = _step_items(work.get("steps") or (work.get("as_is_graph") or {}).get("nodes"))
    systems = _confirmed_terms(work.get("systems"))
    inputs = _confirmed_terms(work.get("inputs"))
    outputs = _confirmed_terms(work.get("outputs"))
    risks = _confirmed_terms(work.get("risks_controls"))
    prompt = scope["design_prompt"]
    queries: list[dict[str, Any]] = []

    def add(kind: str, text: str, expected: list[str], purpose: str, step_ids: list[str] | None = None) -> None:
        clean = _safe_text(text, 2000)
        if not clean or kind not in ALLOWED_QUERY_KINDS or len(queries) >= max_queries:
            return
        identity = {"kind": kind, "text": clean, "expected": expected, "step_ids": step_ids or []}
        query_id = "q-" + hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
        if any(item["query_id"] == query_id for item in queries):
            return
        queries.append(
            {
                "query_id": query_id,
                "kind": kind,
                "text": clean,
                "purpose": _safe_text(purpose, 500),
                "expected_asset_types": [item for item in expected if item in ALLOWED_ASSET_TYPES],
                "matched_work_steps": step_ids or [],
                "required_filters": {
                    "tenant_id": tenant,
                    "snapshot_id": snapshot,
                    "acl_required": True,
                },
            }
        )

    add("purpose", " ".join(item for item in (title, goal, prompt) if item), ["component", "flow"], "전체 업무 목적과 유사한 자산 탐색")
    for step in steps:
        add(
            "capability",
            " ".join(item for item in (step["title"], step["capability"]) if item),
            ["component", "flow"],
            "업무 단계 capability 충족 자산 탐색",
            [step["step_id"]],
        )
    for system in systems:
        add("exact", system, ["component", "flow"], "사용자가 확인한 시스템/API/제품명 exact 또는 alias 탐색")
    for risk in risks:
        add("risk", risk, ["component", "flow"], "승인·보안·예외 처리 capability 탐색")
    if outputs:
        add("reporting", " ".join(outputs), ["component", "flow"], "업무 출력과 보고 capability 탐색")
    if not queries:
        return _error(trace_id, "NO_SEARCHABLE_REQUIREMENT", "검색 query를 만들 확인된 업무 정보가 없습니다.")

    plan_core = {
        "schema_version": "search-query-plan.v1",
        "tenant_id": tenant,
        "catalog_snapshot_id": snapshot,
        "work_definition_id": str(work.get("work_definition_id") or ""),
        "work_definition_revision": scope["work_definition_revision"],
        "approved_hash": approved_hash,
        "design_scope_sha256": scope["design_scope_sha256"],
        "acl": {
            "subject_id": str(acl.get("subject_id")),
            "groups": sorted(_confirmed_acl_groups(acl.get("groups"))),
        },
        "queries": queries,
        "confirmed_exact_terms": systems,
        "confirmed_inputs": inputs,
        "confirmed_outputs": outputs,
    }
    return {
        "ok": True,
        "status": "COMPLETED",
        **plan_core,
        "query_plan_sha256": _canonical_hash(plan_core),
        "trace_id": trace_id,
    }


def _confirmed_acl_groups(value: Any) -> set[str]:
    source = value if isinstance(value, list) else []
    return {str(item).strip().lower() for item in source[:100] if str(item).strip()}


def _error(trace_id: str, code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "BLOCKED",
        "artifact_refs": [],
        "error": {"code": code, "message": message, "retryable": False, "details": {}},
        "resume": None,
        "trace_id": trace_id,
    }


class SearchQueryPlannerComponent(Component):
    display_name = "20 Search Query Planner"
    description = "승인 업무 정의를 purpose, capability, exact, risk, reporting 검색 query plan으로 변환합니다."
    icon = "ListFilter"
    name = "SearchQueryPlanner"

    inputs = [
        DataInput(name="work_definition", display_name="Approved Work Definition", required=True),
        DataInput(name="acl_context", display_name="ACL Context", required=True),
        MessageTextInput(name="tenant_id", display_name="Tenant ID", required=True),
        MessageTextInput(name="catalog_snapshot_id", display_name="Active Catalog Snapshot ID", required=True),
        MessageTextInput(name="design_prompt", display_name="Additional Design Prompt", required=False),
        IntInput(name="max_queries", display_name="Maximum Queries", value=30, advanced=True),
    ]
    outputs = [
        Output(name="design_scope", display_name="Sealed Design Scope", method="build_scope", types=["Data"]),
        Output(name="query_plan", display_name="Search Query Plan", method="build_query_plan", types=["Data"]),
    ]

    def build_scope(self) -> Data:
        result = build_design_scope(
            self.work_definition,
            tenant_id=self.tenant_id,
            catalog_snapshot_id=self.catalog_snapshot_id,
            acl_context=self.acl_context,
            design_prompt=getattr(self, "design_prompt", ""),
        )
        self.status = f"Design scope: {result.get('status')}"
        return Data(data=result)

    def build_query_plan(self) -> Data:
        result = build_search_query_plan(
            self.work_definition,
            tenant_id=self.tenant_id,
            catalog_snapshot_id=self.catalog_snapshot_id,
            acl_context=self.acl_context,
            design_prompt=getattr(self, "design_prompt", ""),
            max_queries=getattr(self, "max_queries", 30),
        )
        self.status = f"Query plan: {result.get('status')} / queries={len(result.get('queries', []))}"
        return Data(data=result)
