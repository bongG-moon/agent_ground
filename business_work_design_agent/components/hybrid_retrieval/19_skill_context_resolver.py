from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from datetime import datetime
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, IntInput, Output
from lfx.schema import Data


MAX_REGISTRY_ITEMS = 500
ALLOWED_VISIBILITY = {"tenant", "group", "private"}
FORBIDDEN_INSTRUCTION_PATTERNS = (
    r"(?i)\b(?:python|powershell|bash|shell|cmd\.exe)\b.{0,40}\b(?:run|execute|실행)\b",
    r"(?i)\b(?:eval|exec|subprocess|os\.system|tool\s*/?\s*add)\b",
    r"(?i)\b(?:secret|password|token|credential|비밀|암호)\b.{0,40}\b(?:read|send|print|조회|전송|출력)\b",
    r"(?i)\b(?:ignore|override|bypass|무시|우회)\b.{0,50}\b(?:policy|system|safety|acl|approval|정책|승인)\b",
)
SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)\b(?:api[_-]?key|password|passwd|secret|access[_-]?token|client[_-]?secret|authorization|cookie|session)\s*[:=]\s*[\"']?[^\s,;]{8,}"),
    re.compile(r"(?i)\b(?:bearer|basic)\s+\S{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^/@\s]+:[^/@\s]+@"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
REGISTRY_ENTRY_KEYS = {
    "tenant_id", "skill_id", "name", "version", "prompt_sha256", "trigger_rules", "near_miss_rules",
    "prompt_text", "forbidden_actions", "status", "acl", "approved_by", "approved_at", "match_reason",
    "target_stage",
}
ACL_KEYS = {"visibility", "groups", "subjects"}


def _payload(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    data = getattr(value, "data", None)
    if isinstance(data, (dict, list)):
        return data
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return {}


def _items(value: Any, *keys: str) -> list[dict[str, Any]]:
    payload = _payload(value)
    if isinstance(payload, list):
        raw = payload
    elif isinstance(payload, dict):
        raw = []
        for key in keys:
            if isinstance(payload.get(key), list):
                raw = payload[key]
                break
    else:
        raw = []
    return [item for item in raw[:MAX_REGISTRY_ITEMS] if isinstance(item, dict)]


def _approved_registry_items(value: Any) -> tuple[list[dict[str, Any]], str]:
    """Accept an explicitly supplied empty registry, but never infer one.

    An empty `skills` list is the valid representation when a team has no
    approved Skills.  In contrast, an omitted registry, a failed upstream
    envelope, or a non-list `skills` value must remain fail-closed: treating
    those as an empty registry would discard an upstream failure.
    """
    payload = _payload(value)
    if isinstance(payload, list):
        raw = payload
    elif isinstance(payload, dict):
        if "ok" in payload and payload.get("ok") is not True:
            return [], "SKILL_REGISTRY_UPSTREAM_FAILED"
        if "skills" not in payload:
            return [], "SKILL_REGISTRY_EMPTY"
        raw = payload.get("skills")
    else:
        return [], "SKILL_REGISTRY_EMPTY"
    if not isinstance(raw, list):
        return [], "SKILL_REGISTRY_CONTRACT_INVALID"
    if len(raw) > MAX_REGISTRY_ITEMS or any(not isinstance(item, dict) for item in raw):
        return [], "SKILL_REGISTRY_CONTRACT_INVALID"
    return list(raw), ""


def _strings(value: Any, maximum: int = 100) -> list[str]:
    if isinstance(value, str):
        source = re.split(r"[,;\n]", value)
    elif isinstance(value, (list, tuple, set)):
        source = list(value)
    else:
        source = []
    result: list[str] = []
    for item in source[:maximum]:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _canonical_hash(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _scope_hash(scope: dict[str, Any]) -> str:
    keys = (
        "schema_version",
        "tenant_id",
        "catalog_snapshot_id",
        "work_definition_id",
        "work_definition_revision",
        "approved_hash",
        "work_definition",
        "acl_context",
        "design_prompt",
    )
    material = json.dumps({key: scope.get(key) for key in keys}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _hash_matches(expected: Any, text: str) -> bool:
    supplied = str(expected or "").strip().lower()
    actual = _canonical_hash(text).lower()
    return supplied in {actual, actual.removeprefix("sha256:")}


def _safe_trace_identity(value: Any, *, skill_id: bool = False) -> str:
    if type(value) is not str or not value or len(value) > 128:
        return "[INVALID]"
    if skill_id and re.fullmatch(r"[a-z][a-z0-9_-]{1,127}", value) is None:
        return "[INVALID]"
    if any(pattern.search(value) for pattern in SECRET_VALUE_PATTERNS):
        return "[INVALID]"
    return value


def _registry_contract_error(entry: dict[str, Any]) -> str:
    if set(entry) - REGISTRY_ENTRY_KEYS:
        return "SKILL_REGISTRY_CONTRACT_INVALID"
    if any(not isinstance(entry.get(key), str) for key in ("tenant_id", "skill_id", "version", "prompt_sha256")):
        return "INVALID_REGISTRY_IDENTITY"
    tenant_id = str(entry.get("tenant_id") or "")
    skill_id = str(entry.get("skill_id") or "")
    name = entry.get("name")
    version = str(entry.get("version") or "")
    prompt_hash = str(entry.get("prompt_sha256") or "")
    prompt_text = entry.get("prompt_text")
    if not tenant_id or len(tenant_id) > 128 or not re.fullmatch(r"[a-z][a-z0-9_-]{1,127}", skill_id):
        return "INVALID_REGISTRY_IDENTITY"
    if not version or len(version) > 128 or not re.fullmatch(r"sha256:[0-9a-f]{64}", prompt_hash):
        return "INVALID_REGISTRY_IDENTITY"
    if not isinstance(name, str) or not name.strip() or len(name) > 256:
        return "INVALID_REGISTRY_IDENTITY"
    if not isinstance(prompt_text, str) or not prompt_text or len(prompt_text) > 50_000:
        return "INVALID_REGISTRY_PROMPT"
    approved_by = entry.get("approved_by")
    approved_at = entry.get("approved_at")
    if not isinstance(approved_by, str) or not approved_by.strip() or len(approved_by) > 256:
        return "SKILL_APPROVAL_EVIDENCE_INVALID"
    if not isinstance(approved_at, str) or len(approved_at) > 64:
        return "SKILL_APPROVAL_EVIDENCE_INVALID"
    try:
        parsed_approval_time = datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
    except ValueError:
        return "SKILL_APPROVAL_EVIDENCE_INVALID"
    if parsed_approval_time.tzinfo is None:
        return "SKILL_APPROVAL_EVIDENCE_INVALID"
    trigger_rules = entry.get("trigger_rules")
    near_miss_rules = entry.get("near_miss_rules")
    if not isinstance(trigger_rules, list) or not 1 <= len(trigger_rules) <= 100:
        return "SKILL_RULE_CONTRACT_INVALID"
    if not isinstance(near_miss_rules, list) or len(near_miss_rules) > 100:
        return "SKILL_RULE_CONTRACT_INVALID"
    if any(not _valid_rule(rule) for rule in [*trigger_rules, *near_miss_rules]):
        return "SKILL_RULE_CONTRACT_INVALID"
    if type(entry.get("status")) is not str or entry.get("status") not in {"active", "inactive", "revoked"}:
        return "INVALID_REGISTRY_STATUS"
    forbidden_actions = entry.get("forbidden_actions", [])
    if (
        not isinstance(forbidden_actions, list)
        or len(forbidden_actions) > 100
        or any(not isinstance(item, str) or len(item) > 128 for item in forbidden_actions)
    ):
        return "SKILL_REGISTRY_CONTRACT_INVALID"
    match_reason = entry.get("match_reason")
    target_stage = entry.get("target_stage")
    if match_reason is not None and (not isinstance(match_reason, str) or len(match_reason) > 500):
        return "SKILL_REGISTRY_CONTRACT_INVALID"
    if target_stage is not None and (not isinstance(target_stage, str) or len(target_stage) > 100):
        return "SKILL_REGISTRY_CONTRACT_INVALID"
    acl = entry.get("acl")
    if not isinstance(acl, dict) or str(acl.get("visibility") or "") not in ALLOWED_VISIBILITY:
        return "SKILL_ACL_INVALID"
    if set(acl) - ACL_KEYS:
        return "SKILL_ACL_INVALID"
    if "groups" not in acl:
        return "SKILL_ACL_INVALID"
    groups = acl.get("groups")
    subjects = acl.get("subjects", [])
    if not isinstance(groups, list) or len(groups) > 200 or any(not isinstance(item, str) or not item.strip() or len(item) > 128 for item in groups):
        return "SKILL_ACL_INVALID"
    if not isinstance(subjects, list) or len(subjects) > 200 or any(not isinstance(item, str) or not item.strip() or len(item) > 256 for item in subjects):
        return "SKILL_ACL_INVALID"
    visibility = str(acl["visibility"])
    if visibility == "group" and not groups:
        return "SKILL_ACL_INVALID"
    if visibility == "private" and not subjects:
        return "SKILL_ACL_INVALID"
    return ""


def _valid_rule(rule: Any) -> bool:
    if isinstance(rule, str):
        return bool(rule.strip()) and len(rule) <= 1000
    if not isinstance(rule, dict) or set(rule) - {"kind", "value", "values", "terms"}:
        return False
    kind = str(rule.get("kind") or "contains").lower()
    if kind not in {"contains", "all"}:
        return False
    value_keys = [key for key in ("value", "values", "terms") if key in rule]
    if len(value_keys) != 1:
        return False
    selected_key = value_keys[0]
    raw_values = [rule.get("value")] if selected_key == "value" else rule.get(selected_key)
    if not isinstance(raw_values, list) or not 1 <= len(raw_values) <= 100:
        return False
    values = [item for item in raw_values if isinstance(item, str) and item.strip() and len(item) <= 1000]
    if len(values) != len(raw_values):
        return False
    return True


def _acl_allows(entry: dict[str, Any], tenant_id: str, groups: set[str], subject_id: str) -> bool:
    if str(entry.get("tenant_id") or "") != tenant_id:
        return False
    acl = entry.get("acl") if isinstance(entry.get("acl"), dict) else {}
    visibility = str(acl.get("visibility") or "").lower()
    if visibility not in ALLOWED_VISIBILITY:
        return False
    if visibility == "tenant":
        return True
    if visibility == "group":
        allowed = {item.lower() for item in _strings(acl.get("groups"), maximum=200)}
        return bool(allowed & groups)
    subjects = set(_strings(acl.get("subjects"), maximum=200))
    return bool(subject_id) and subject_id in subjects


def _work_text(work_definition: dict[str, Any]) -> str:
    """Project only approval-bound semantic fields into deterministic trigger text."""
    fields: list[str] = []
    item_text_keys = (
        "value", "title", "name", "description", "capability", "action", "condition", "risk", "control",
        "label", "current_work", "problem", "problems", "improvement", "exception", "resolution", "criterion",
    )

    def append_semantic(value: Any, depth: int = 0) -> None:
        if depth > 4:
            return
        if isinstance(value, str):
            if value.strip():
                fields.append(value)
            return
        if isinstance(value, list):
            for item in value[:100]:
                append_semantic(item, depth + 1)
            return
        if isinstance(value, dict):
            for name in item_text_keys:
                if name in value:
                    append_semantic(value.get(name), depth + 1)

    for key in ("goal", "trigger", "frequency_volume", "sla", "automation_intent"):
        append_semantic(work_definition.get(key))
    for key in (
        "scope_in", "scope_out", "actors", "systems", "inputs", "outputs", "steps", "decisions", "exceptions",
        "pains", "risks_controls", "constraints", "success_criteria", "assumptions", "unresolved",
    ):
        value = work_definition.get(key)
        if isinstance(value, list):
            append_semantic(value)
    graph = work_definition.get("as_is_graph")
    if isinstance(graph, dict):
        for node in list(graph.get("nodes") or [])[:200]:
            append_semantic(node)
        for edge in list(graph.get("edges") or [])[:500]:
            append_semantic(edge)
    return "\n".join(fields).lower()[:100000]


def _rule_matches(rule: Any, text: str) -> bool:
    if isinstance(rule, str):
        return rule.strip().lower() in text if rule.strip() else False
    if not isinstance(rule, dict):
        return False
    kind = str(rule.get("kind") or "contains").lower()
    values = [item.lower() for item in _strings(rule.get("values") or rule.get("terms"))]
    if not values:
        value = str(rule.get("value") or "").strip().lower()
        values = [value] if value else []
    if kind == "all":
        return bool(values) and all(value in text for value in values)
    if kind == "regex":
        return False
    return any(value in text for value in values)


def resolve_skill_context(
    work_definition: Any,
    skill_registry: Any,
    *,
    tenant_id: str = "",
    acl_context: Any = None,
    requested_skill_refs: Any = None,
    max_skills: int = 8,
    max_context_chars: int = 24000,
) -> dict[str, Any]:
    trace_id = str(uuid.uuid4())
    supplied = _payload(work_definition)
    design_scope = supplied if isinstance(supplied, dict) and supplied.get("schema_version") == "agent-design-scope/v1" else {}
    if design_scope:
        supplied_hash = str(design_scope.get("design_scope_sha256") or "")
        if (
            design_scope.get("ok") is not True
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", supplied_hash)
            or not hmac.compare_digest(supplied_hash, _scope_hash(design_scope))
        ):
            return _error(trace_id, "DESIGN_SCOPE_INVALID", "검증·봉인된 design scope가 필요합니다.")
        work = _payload(design_scope.get("work_definition"))
        acl = _payload(design_scope.get("acl_context"))
        tenant = str(design_scope.get("tenant_id") or "").strip()
    else:
        work = supplied
        acl = _payload(acl_context)
        tenant = str(tenant_id or "").strip()
    registry, registry_error = _approved_registry_items(skill_registry)
    requested = _items(requested_skill_refs, "skills", "items", "requested_skills")
    groups = {item.lower() for item in _strings(acl.get("groups") if isinstance(acl, dict) else [])}
    subject_id = str(acl.get("subject_id") or "") if isinstance(acl, dict) else ""
    if not tenant:
        return _error(trace_id, "TENANT_REQUIRED", "tenant_id가 필요합니다.")
    if not isinstance(work, dict) or not work:
        return _error(trace_id, "INVALID_WORK_DEFINITION", "업무 정의가 비어 있거나 잘못되었습니다.")
    if not isinstance(acl, dict) or not acl.get("subject_id"):
        return _error(trace_id, "ACL_CONTEXT_MISSING", "검증 가능한 ACL context가 필요합니다.")
    if registry_error:
        messages = {
            "SKILL_REGISTRY_EMPTY": "승인 Skill registry가 제공되지 않았습니다.",
            "SKILL_REGISTRY_UPSTREAM_FAILED": "승인 Skill registry를 제공한 상위 단계가 실패했습니다.",
            "SKILL_REGISTRY_CONTRACT_INVALID": "승인 Skill registry 형식이 유효하지 않습니다.",
        }
        return _error(trace_id, registry_error, messages[registry_error])

    max_skills = max(1, min(20, int(max_skills or 8)))
    max_context_chars = max(1000, min(100000, int(max_context_chars or 24000)))
    requested_map = {
        (
            _safe_trace_identity(item.get("skill_id"), skill_id=True),
            _safe_trace_identity(item.get("version")),
        ): str(item.get("prompt_sha256") or "")[:80]
        for item in requested
    }
    text = _work_text(work)
    applied: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    context_parts: list[str] = []
    used_chars = 0
    identity_counts: dict[tuple[str, str], int] = {}
    for entry in registry:
        identity = (
            _safe_trace_identity(entry.get("skill_id"), skill_id=True),
            _safe_trace_identity(entry.get("version")),
        )
        identity_counts[identity] = identity_counts.get(identity, 0) + 1

    for entry in registry:
        skill_id = _safe_trace_identity(entry.get("skill_id"), skill_id=True)
        version = _safe_trace_identity(entry.get("version"))
        prompt_text = entry.get("prompt_text") if isinstance(entry.get("prompt_text"), str) else ""
        identity = (skill_id, version)
        reason = _registry_contract_error(entry)
        if not reason and identity_counts.get(identity, 0) > 1:
            reason = "DUPLICATE_SKILL_IDENTITY"
        if not reason and (not skill_id or not version):
            reason = "INVALID_REGISTRY_IDENTITY"
        if not reason and entry.get("status") != "active":
            reason = "SKILL_NOT_ACTIVE"
        if not reason and not _acl_allows(entry, tenant, groups, subject_id):
            reason = "SKILL_ACL_DENIED"
        if not reason and not _hash_matches(entry.get("prompt_sha256"), prompt_text):
            reason = "SKILL_HASH_MISMATCH"
        if not reason and any(pattern.search(prompt_text) for pattern in SECRET_VALUE_PATTERNS):
            reason = "SKILL_SECRET_MATERIAL_DETECTED"
        surfaced_values = (
            entry.get("name"), entry.get("version"), entry.get("match_reason"), entry.get("target_stage")
        )
        if not reason and any(
            isinstance(value, str) and any(pattern.search(value) for pattern in SECRET_VALUE_PATTERNS)
            for value in surfaced_values
        ):
            reason = "SKILL_SECRET_MATERIAL_DETECTED"
        if not reason and any(re.search(pattern, prompt_text) for pattern in FORBIDDEN_INSTRUCTION_PATTERNS):
            reason = "UNSAFE_SKILL_INSTRUCTION"
        if not reason:
            requested_hash = requested_map.get((skill_id, version))
            if requested and requested_hash is None:
                reason = "SKILL_NOT_REQUESTED"
            elif requested and not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", str(requested_hash).lower()):
                reason = "REQUESTED_SKILL_HASH_REQUIRED"
            elif requested_hash and requested_hash.lower() not in {
                str(entry.get("prompt_sha256") or "").lower(),
                str(entry.get("prompt_sha256") or "").lower().removeprefix("sha256:"),
            }:
                reason = "REQUESTED_SKILL_HASH_MISMATCH"

        trigger_rules = entry.get("trigger_rules") if isinstance(entry.get("trigger_rules"), list) else []
        near_miss_rules = entry.get("near_miss_rules") if isinstance(entry.get("near_miss_rules"), list) else []
        trigger_match = any(_rule_matches(rule, text) for rule in trigger_rules[:100]) if not reason and trigger_rules else bool(requested)
        near_miss_match = any(_rule_matches(rule, text) for rule in near_miss_rules[:100]) if not reason else False
        if not reason and not trigger_match:
            reason = "TRIGGER_NOT_MATCHED"
        if not reason and near_miss_match:
            reason = "NEAR_MISS_MATCHED"

        if reason:
            rejected.append({"skill_id": skill_id, "version": version, "reason": reason})
            continue
        framed = (
            f'<approved-skill id="{skill_id}" version="{version}" '
            f'hash="{entry.get("prompt_sha256")}">\n{prompt_text}\n</approved-skill>'
        )
        if len(applied) >= max_skills or used_chars + len(framed) > max_context_chars:
            rejected.append({"skill_id": skill_id, "version": version, "reason": "SKILL_CONTEXT_LIMIT"})
            continue
        match_reason = str(entry.get("match_reason") or "trigger rule matched")[:500]
        applied.append(
            {
                "skill_id": skill_id,
                "name": str(entry.get("name") or skill_id)[:200],
                "version": version,
                "prompt_sha256": str(entry.get("prompt_sha256")),
                "match_reason": match_reason,
                "target_stage": str(entry.get("target_stage") or "design")[:100],
                "source_ref": "approved-skill-registry",
            }
        )
        context_parts.append(framed)
        used_chars += len(framed)

    return {
        "ok": True,
        "status": "COMPLETED",
        "tenant_id": tenant,
        "catalog_snapshot_id": str(design_scope.get("catalog_snapshot_id") or ""),
        "work_definition_id": str(design_scope.get("work_definition_id") or work.get("work_definition_id") or ""),
        "work_definition_revision": design_scope.get("work_definition_revision") if design_scope else work.get("revision"),
        "approved_hash": str(design_scope.get("approved_hash") or work.get("approved_hash") or ""),
        "design_scope_sha256": str(design_scope.get("design_scope_sha256") or ""),
        "approved_skill_context": "\n\n".join(context_parts),
        "applied_skills": applied,
        "rejected_skills": rejected,
        "context_char_count": used_chars,
        "trust_boundary": {
            "source": "approved-skill-registry",
            "policy_precedence": "system_policy_over_skill_context",
            "dynamic_tool_addition": False,
            "secret_access": False,
        },
        "trace_id": trace_id,
    }


def _error(trace_id: str, code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "BLOCKED",
        "artifact_refs": [],
        "error": {"code": code, "message": message, "retryable": False, "details": {}},
        "resume": None,
        "trace_id": trace_id,
    }


class SkillContextResolverComponent(Component):
    display_name = "19 Approved Skill Context Resolver"
    description = "승인 registry의 Skill ID/version/hash, trigger, near-miss, ACL을 검증해 bounded context를 만듭니다."
    icon = "ShieldCheck"
    name = "SkillContextResolver"

    inputs = [
        DataInput(name="design_scope", display_name="Sealed Design Scope", required=True),
        DataInput(name="skill_registry", display_name="Approved Skill Registry", required=True),
        DataInput(name="requested_skill_refs", display_name="Requested Skill Refs", required=False, advanced=True),
        IntInput(name="max_skills", display_name="Maximum Skills", value=8, advanced=True),
        IntInput(name="max_context_chars", display_name="Maximum Context Characters", value=24000, advanced=True),
    ]
    outputs = [Output(name="skill_context", display_name="Verified Skill Context", method="build_skill_context", types=["Data"])]

    def build_skill_context(self) -> Data:
        result = resolve_skill_context(
            self.design_scope,
            self.skill_registry,
            requested_skill_refs=getattr(self, "requested_skill_refs", None),
            max_skills=getattr(self, "max_skills", 8),
            max_context_chars=getattr(self, "max_context_chars", 24000),
        )
        self.status = f"Skill resolve: {result.get('status')} / applied={len(result.get('applied_skills', []))}"
        return Data(data=result)
