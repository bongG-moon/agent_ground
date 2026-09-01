from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, HandleInput, IntInput, Output
from lfx.schema import Data


IMPLEMENTATION_SOURCES = {
    "builtin",
    "catalog_component",
    "catalog_flow",
    "new_standalone_component",
    "companion_service",
    "human_task",
}
TECHNICAL_STATUSES = {"metadata_only", "ports_extracted", "flow_graph_extracted", "verified_runtime"}
PATTERNS = {
    "deterministic_sequential",
    "single_agent_allowlisted_tools",
    "parent_with_child_flows",
    "producer_reviewer",
    "bounded_fan_out",
    "flow_without_agent",
}
LEGACY_PATTERN_ALIASES = {
    "sequential_flow": "deterministic_sequential",
    "single_agent": "single_agent_allowlisted_tools",
    "fan_out_fan_in": "bounded_fan_out",
    "no_agent_flow": "flow_without_agent",
}
NODE_TYPES = {"start", "task", "decision", "human_review", "system_call", "subflow", "end", "exception"}
APPLIED_SKILL_FIELDS = (
    "skill_id",
    "name",
    "version",
    "prompt_sha256",
    "match_reason",
    "target_stage",
    "source_ref",
)
GENERATION_CONTRACT_KEYS = {
    "component_filename",
    "class_name",
    "display_name",
    "responsibility",
    "input_contract",
    "output_contract",
    "secret_inputs",
    "dependencies",
    "timeout_limits",
    "error_codes",
    "deployment_mode",
    "prompt_pack",
}
SECRET_DECLARATION_KEYS = frozenset({"name", "ref", "port_id", "required", "configured"})
# A bare ``secret_inputs`` string is intentionally treated as a declaration
# *name*, never as a secret value or URI.  This narrow form is useful for LLM
# drafts such as ``["outlook_credential_ref"]`` while preventing arbitrary
# prose, JSON, shell fragments, or credential material from entering the
# sealed generation contract.
SECRET_DECLARATION_LABEL_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,299}$")
# Object-form ``ref`` is allowed only as a declaration label or an approved
# reference URI.  The schemes are labels for secret stores; they are not URLs
# to be fetched by this component.
SECRET_REFERENCE_PATTERN = re.compile(
    r"^(?:vault|secret|env|keyvault|aws-sm|gcp-sm|azure-keyvault)://[A-Za-z0-9._:/@-]{1,260}$",
    flags=re.IGNORECASE,
)
SECRET_KEY_TOKENS = {
    "apikey",
    "authorization",
    "clientsecret",
    "cookie",
    "credential",
    "password",
    "passwd",
    "privatekey",
    "pwd",
    "session",
    "smsession",
    "secret",
    "token",
}
SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)\b(?:api[_-]?key|password|passwd|secret|access[_-]?token|client[_-]?secret|authorization|cookie|session)\s*[:=]\s*[\"']?[^\s,;]{8,}"),
    re.compile(r"(?i)\b(?:bearer|basic)\s+\S{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^/@\s]+:[^/@\s]+@"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


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


def _forward_blocked_envelope(value: Any, *, trace_id: str) -> dict[str, Any] | None:
    """Do not relabel an earlier F20 retrieval/model failure as a lock error."""
    payload = _payload(value)
    error = payload.get("error")
    if payload.get("ok") is not False or str(payload.get("status") or "") != "BLOCKED" or not isinstance(error, dict):
        return None
    details = error.get("details")
    forwarded_details = dict(details) if isinstance(details, dict) else {}
    upstream_trace_id = str(payload.get("trace_id") or "").strip()
    if upstream_trace_id:
        forwarded_details.setdefault("upstream_trace_id", upstream_trace_id)
    return _error(
        trace_id,
        str(error.get("code") or "UPSTREAM_BLUEPRINT_STAGE_BLOCKED"),
        str(error.get("message") or "이전 Blueprint 단계가 차단되었습니다."),
        details=forwarded_details,
    )


def _blueprint_draft_payload(value: Any) -> dict[str, Any]:
    """Read a direct Blueprint object or a single JSON/fenced-JSON model reply.

    The upstream Type Convert node normally supplies a dictionary.  Some model
    providers, however, keep a valid JSON answer under ``text`` or wrap it in
    one Markdown JSON fence.  Accept only that narrow representation rather
    than extracting arbitrary text from a model response.
    """
    # A direct Language Model edge supplies a Message object in Langflow.  Read
    # only its text field; do not inspect arbitrary metadata or extract JSON
    # fragments from prose, which would make an accidental model explanation
    # look like an approved blueprint.
    text = getattr(value, "text", None)
    payload = _payload(value)
    if not isinstance(text, str):
        text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str):
        return payload
    normalized = text.strip()
    if not normalized or len(normalized) > 500_000:
        return {}
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", normalized, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        normalized = fenced.group(1).strip()
    try:
        parsed = json.loads(normalized)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe(value: Any, maximum: int = 1000) -> str:
    return re.sub(r"[\x00-\x1f]", " ", str(value or "")).strip()[:maximum]


def _default_node_responsibility(title: str, source: str) -> str:
    """Supply deterministic text when an F20 draft omitted node responsibility."""

    source_text = {
        "builtin": "Langflow 기본 기능으로",
        "catalog_component": "승인된 카탈로그 Component로",
        "catalog_flow": "승인된 카탈로그 Flow로",
        "new_standalone_component": "신규 Standalone Custom Component로",
        "companion_service": "승인된 연계 서비스로",
        "human_task": "담당자의 판단으로",
    }.get(source, "정의된 방식으로")
    return f"{title} 단계를 {source_text} 수행하고 다음 단계에 필요한 결과를 전달합니다."


def _default_reuse_decision_reason(source: str) -> str:
    """Give every node an auditable, non-empty reuse decision rationale."""

    return {
        "builtin": "표준 Langflow 기본 기능으로 구현 가능한 단계입니다.",
        "catalog_component": "승인된 카탈로그 Component 계약을 재사용합니다.",
        "catalog_flow": "승인된 카탈로그 Flow 계약을 재사용합니다.",
        "new_standalone_component": "현재 승인 후보에 직접 재사용할 자산이 없어 Standalone Custom Component 생성 후보로 설계했습니다.",
        "companion_service": "외부 또는 사내 연계 서비스의 명시적 계약이 필요한 단계입니다.",
        "human_task": "업무 판단·승인 책임을 자동화하지 않고 담당자가 수행해야 하는 단계입니다.",
    }.get(source, "선택한 구현 방식과 검증 범위를 설계 단계에서 명시합니다.")


def _optional_safe(value: Any, maximum: int = 1000) -> str | None:
    if value is None:
        return None
    return _safe(value, maximum)


def _secret_key(value: Any) -> bool:
    text = str(value or "").casefold()
    compact = re.sub(r"[^a-z0-9]", "", text)
    parts = {item for item in re.split(r"[^a-z0-9]+", text) if item}
    # Runtime sizing keys such as max_tokens are not credentials.  Credential
    # labels remain fail-closed, including compound keys such as access_token.
    if ("token" in parts and parts & {"max", "limit", "budget", "count"}) or (
        "session" in parts and parts & {"timeout", "ttl"}
    ):
        return False
    if "token" in compact and any(marker in compact for marker in {"maxtoken", "tokenlimit", "tokenbudget", "tokencount"}):
        return False
    if "session" in compact and any(marker in compact for marker in {"sessiontimeout", "sessionttl"}):
        return False
    strong_markers = SECRET_KEY_TOKENS
    return (
        compact in SECRET_KEY_TOKENS
        or bool(parts & {"token", "session", "pwd"})
        or any(marker in compact for marker in strong_markers)
    )


def _secret_material_path(value: Any, path: str = "config") -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            safe_key = (
                key_text
                if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", key_text) and not _secret_key(key_text)
                else "<field>"
            )
            child_path = f"{path}.{safe_key}"
            if _secret_key(key) and item not in (None, "", False):
                return child_path
            found = _secret_material_path(item, child_path)
            if found:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = _secret_material_path(item, f"{path}[{index}]")
            if found:
                return found
    elif isinstance(value, str):
        text = value.strip()
        if any(pattern.search(text) for pattern in SECRET_VALUE_PATTERNS):
            return path
    return None


def _secret_value_path(value: Any, path: str) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            safe_key = (
                key_text
                if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", key_text) and not _secret_key(key_text)
                else "<field>"
            )
            found = _secret_value_path(item, f"{path}.{safe_key}")
            if found:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = _secret_value_path(item, f"{path}[{index}]")
            if found:
                return found
    elif isinstance(value, str) and any(pattern.search(value.strip()) for pattern in SECRET_VALUE_PATTERNS):
        return path
    return None


def _normalize_required_secrets(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value[:50]:
        if not isinstance(item, dict):
            continue
        safe_item: dict[str, Any] = {}
        for key in ("name", "ref", "port_id"):
            if key in item:
                safe_item[key] = _safe(item.get(key), 300)
        for key in ("required", "configured"):
            if key in item:
                safe_item[key] = bool(item.get(key))
        if safe_item.get("name") or safe_item.get("ref") or safe_item.get("port_id"):
            normalized.append(safe_item)
    return normalized


def _safe_secret_declaration_text(value: Any, *, field_name: str) -> str | None:
    """Return a canonical declaration label/reference, never a secret value."""

    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 300:
        return None
    if SECRET_DECLARATION_LABEL_PATTERN.fullmatch(text):
        return text
    if field_name == "ref" and SECRET_REFERENCE_PATTERN.fullmatch(text):
        return text
    return None


def _normalize_generation_secret_inputs(value: Any) -> tuple[list[dict[str, Any]] | None, tuple[str, dict[str, Any]] | None]:
    """Normalize only safe declaration-only ``secret_inputs`` entries.

    A model may naturally emit a declaration name as a string.  Normalize that
    narrow convenience form before validating the generation contract, but do
    not silently discard malformed fields: unknown object keys, invalid flags,
    and non-declaration text are all fail-closed.  Secret-looking content is
    classified before shape errors so it is never preserved or echoed.
    """

    if not isinstance(value, list):
        return None, ("GENERATION_CONTRACT_INVALID", {"reason": "secret_inputs must be an array"})
    if len(value) > 50:
        return None, ("GENERATION_CONTRACT_INVALID", {"reason": "secret_inputs exceeds the bounded limit"})

    normalized: list[dict[str, Any]] = []
    for index, declaration in enumerate(value):
        path = f"generation_contract.secret_inputs[{index}]"
        secret_path = _secret_material_path(declaration, path)
        if secret_path:
            return None, ("BLUEPRINT_SECRET_MATERIAL_DETECTED", {"field": secret_path})
        if isinstance(declaration, str):
            name = _safe_secret_declaration_text(declaration, field_name="name")
            if not name:
                return None, (
                    "GENERATION_CONTRACT_INVALID",
                    {"reason": f"secret_inputs[{index}] must be a declaration label"},
                )
            normalized.append({"name": name, "required": True})
            continue
        if not isinstance(declaration, dict) or set(declaration) - SECRET_DECLARATION_KEYS:
            return None, (
                "GENERATION_CONTRACT_INVALID",
                {"reason": f"secret_inputs[{index}] must be a declaration-only object"},
            )
        clean: dict[str, Any] = {}
        for key in ("name", "ref", "port_id"):
            if key not in declaration:
                continue
            text = _safe_secret_declaration_text(declaration.get(key), field_name=key)
            if not text:
                return None, (
                    "GENERATION_CONTRACT_INVALID",
                    {"reason": f"secret_inputs[{index}].{key} must be a safe declaration label or reference"},
                )
            clean[key] = text
        if not clean:
            return None, (
                "GENERATION_CONTRACT_INVALID",
                {"reason": f"secret_inputs[{index}] requires name, ref, or port_id"},
            )
        for key in ("required", "configured"):
            if key in declaration:
                if type(declaration[key]) is not bool:
                    return None, (
                        "GENERATION_CONTRACT_INVALID",
                        {"reason": f"secret_inputs[{index}].{key} must be a boolean"},
                    )
                clean[key] = declaration[key]
        normalized.append(clean)
    return normalized, None


def _normalize_problems(value: Any) -> list[Any]:
    source = value if isinstance(value, list) else ([value] if value not in (None, "") else [])
    result: list[Any] = []
    for item in source[:50]:
        if isinstance(item, dict):
            clean = {
                key: _safe(item.get(key), 5_000)
                for key in ("id", "title", "description", "impact", "evidence_ref")
                if item.get(key) not in (None, "")
            }
            if clean:
                result.append(clean)
        else:
            text = _safe(item, 5_000)
            if text:
                result.append(text)
    return result


def _generation_contract_error(value: Any) -> tuple[str, dict[str, Any]] | None:
    if not isinstance(value, dict):
        return "GENERATION_CONTRACT_MISSING", {"missing_fields": sorted(GENERATION_CONTRACT_KEYS)}
    missing = sorted(key for key in GENERATION_CONTRACT_KEYS if key not in value)
    unexpected_count = sum(1 for key in value if key not in GENERATION_CONTRACT_KEYS)
    if missing:
        return "GENERATION_CONTRACT_INCOMPLETE", {"missing_fields": missing}
    if unexpected_count:
        return "GENERATION_CONTRACT_INVALID", {"unexpected_field_count": unexpected_count}
    if not all(isinstance(value.get(key), str) and str(value.get(key)).strip() for key in ("component_filename", "class_name", "display_name", "responsibility", "deployment_mode", "prompt_pack")):
        return "GENERATION_CONTRACT_INVALID", {"reason": "required text fields must be non-empty strings"}
    if not isinstance(value.get("input_contract"), dict) or not value.get("input_contract"):
        return "GENERATION_CONTRACT_INVALID", {"reason": "input_contract must be a non-empty object"}
    if not isinstance(value.get("output_contract"), dict) or not value.get("output_contract"):
        return "GENERATION_CONTRACT_INVALID", {"reason": "output_contract must be a non-empty object"}
    if not isinstance(value.get("secret_inputs"), list) or not isinstance(value.get("dependencies"), list):
        return "GENERATION_CONTRACT_INVALID", {"reason": "secret_inputs and dependencies must be arrays"}
    if len(value["secret_inputs"]) > 50:
        return "GENERATION_CONTRACT_INVALID", {"reason": "secret_inputs exceeds the bounded limit"}
    for index, declaration in enumerate(value["secret_inputs"]):
        if not isinstance(declaration, dict) or set(declaration) - SECRET_DECLARATION_KEYS:
            return "GENERATION_CONTRACT_INVALID", {"reason": f"secret_inputs[{index}] must be a declaration-only object"}
        if not any(isinstance(declaration.get(key), str) and declaration.get(key).strip() for key in ("name", "ref", "port_id")):
            return "GENERATION_CONTRACT_INVALID", {"reason": f"secret_inputs[{index}] requires name, ref, or port_id"}
        if any(key in declaration and not isinstance(declaration.get(key), bool) for key in ("required", "configured")):
            return "GENERATION_CONTRACT_INVALID", {"reason": f"secret_inputs[{index}] boolean flags are invalid"}
    if not isinstance(value.get("timeout_limits"), dict) or not value.get("timeout_limits"):
        return "GENERATION_CONTRACT_INVALID", {"reason": "timeout_limits must be a non-empty object"}
    if not isinstance(value.get("error_codes"), list) or not value.get("error_codes"):
        return "GENERATION_CONTRACT_INVALID", {"reason": "error_codes must be a non-empty array"}
    executable_contract = {key: item for key, item in value.items() if key != "secret_inputs"}
    secret_path = _secret_material_path(executable_contract, "generation_contract")
    if secret_path:
        return "BLUEPRINT_SECRET_MATERIAL_DETECTED", {"field": secret_path}
    secret_path = _secret_material_path(value.get("secret_inputs"), "generation_contract.secret_inputs")
    if secret_path:
        return "BLUEPRINT_SECRET_MATERIAL_DETECTED", {"field": secret_path}
    return None


def _canonical_hash(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _design_scope_hash(scope: dict[str, Any]) -> str:
    keys = [
        "schema_version",
        "tenant_id",
        "catalog_snapshot_id",
        "work_definition_id",
        "work_definition_revision",
        "approved_hash",
        "work_definition",
        "acl_context",
        "design_prompt",
    ]
    # A current scope may bind the retrieval-only original request by hash.
    # Preserve backward compatibility for a direct, older scope with no seed.
    if "search_seed_sha256" in scope:
        keys.append("search_seed_sha256")
    return _canonical_hash({key: scope.get(key) for key in keys})


def _normalize_port(item: Any, index: int, direction: str) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    port_id = _safe(item.get("port_id") or item.get("name") or f"{direction}-{index}", 100)
    data_type = _safe(item.get("data_type") or item.get("type") or "", 100)
    if not port_id:
        return None
    cardinality = _safe(item.get("cardinality") or "one", 20).lower()
    if cardinality not in {"one", "many"}:
        cardinality = "one"
    return {
        "port_id": port_id,
        "name": _safe(item.get("name") or port_id, 100),
        "data_type": data_type,
        "semantic_role": _safe(item.get("semantic_role"), 100),
        "schema_ref": _safe(item.get("schema_ref"), 500),
        "cardinality": cardinality,
        "required": bool(item.get("required", direction == "input")),
        "has_default": bool(item.get("has_default", False)),
        "secret": bool(item.get("secret", False)),
        "permission": _safe(item.get("permission"), 200),
        "network_zone": _safe(item.get("network_zone"), 100),
        "streaming": bool(item.get("streaming", False)),
    }


def _normalize_port_contract(value: Any) -> dict[str, list[dict[str, Any]]]:
    raw = value if isinstance(value, dict) else {}
    result: dict[str, list[dict[str, Any]]] = {"inputs": [], "outputs": []}
    for key, direction in (("inputs", "input"), ("outputs", "output")):
        items = raw.get(key) if isinstance(raw.get(key), list) else []
        result[key] = [
            port
            for index, item in enumerate(items[:100], start=1)
            if (port := _normalize_port(item, index, direction)) is not None
        ]
    return result


def _project_applied_skill(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    projected = {
        "skill_id": _safe(value.get("skill_id"), 128),
        "name": _safe(value.get("name"), 256),
        "version": _safe(value.get("version"), 128),
        "prompt_sha256": str(value.get("prompt_sha256") or ""),
        "match_reason": _safe(value.get("match_reason"), 5_000),
        "target_stage": _safe(value.get("target_stage"), 128),
        "source_ref": str(value.get("source_ref") or ""),
    }
    if (
        any(not projected[field] for field in APPLIED_SKILL_FIELDS)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", projected["prompt_sha256"])
        or projected["source_ref"] != "approved-skill-registry"
        or _secret_value_path(projected, "applied_skill")
    ):
        return None
    return projected


def normalize_agent_blueprint(
    blueprint_draft: Any,
    work_definition: Any,
    candidate_context: Any,
    applied_skill_context: Any,
    *,
    tenant_id: str,
    catalog_snapshot_id: str,
    max_nodes: int = 300,
    max_edges: int = 600,
) -> dict[str, Any]:
    trace_id = str(uuid.uuid4())
    draft = _blueprint_draft_payload(blueprint_draft)
    work = _payload(work_definition)
    candidates = _payload(candidate_context)
    skill_context = _payload(applied_skill_context)
    tenant = _safe(tenant_id, 200)
    snapshot = _safe(catalog_snapshot_id, 200)
    if not tenant or not snapshot:
        return _error(trace_id, "BLUEPRINT_SCOPE_MISSING", "tenant_id와 catalog_snapshot_id가 필요합니다.")
    if not draft:
        return _error(
            trace_id,
            "INVALID_BLUEPRINT_DRAFT",
            "Blueprint Model 응답은 단일 JSON object 또는 하나의 json code fence여야 합니다.",
            details={
                "next_actions": [
                    "Blueprint Language Model의 system message를 유지하고 JSON object만 반환하도록 설정합니다.",
                    "모델의 설명문·여러 code fence·부분 JSON은 제거합니다.",
                ]
            },
        )
    if draft.get("generation_requests"):
        return _error(
            trace_id,
            "GENERATION_REQUEST_PREMATURE",
            "generation_request는 정규화·검증 뒤 전용 builder가 만드는 사후 산출물입니다.",
        )
    approved_hash = str(work.get("approved_hash") or "")
    state = str(work.get("state") or work.get("status") or "").lower()
    raw_work_revision = work.get("revision") if work.get("revision") is not None else work.get("work_definition_revision")
    if isinstance(raw_work_revision, bool):
        return _error(trace_id, "WORK_DEFINITION_REVISION_INVALID", "승인 업무 정의 revision이 유효하지 않습니다.")
    try:
        work_revision = int(raw_work_revision)
    except (TypeError, ValueError):
        return _error(trace_id, "WORK_DEFINITION_REVISION_INVALID", "승인 업무 정의 revision이 유효하지 않습니다.")
    if work_revision < 0:
        return _error(trace_id, "WORK_DEFINITION_REVISION_INVALID", "승인 업무 정의 revision이 유효하지 않습니다.")
    if state not in {"approved", "active"} or not approved_hash.startswith("sha256:"):
        return _error(trace_id, "WORK_DEFINITION_NOT_APPROVED", "승인 업무 정의와 approved_hash가 필요합니다.")
    if draft.get("approved_hash") and str(draft.get("approved_hash")) != approved_hash:
        return _error(trace_id, "APPROVED_HASH_MISMATCH", "blueprint draft의 approved_hash가 현재 승인본과 다릅니다.")
    if draft.get("catalog_snapshot_id") and str(draft.get("catalog_snapshot_id")) != snapshot:
        return _error(trace_id, "SNAPSHOT_MISMATCH", "blueprint draft의 catalog snapshot이 현재 snapshot과 다릅니다.")
    if candidates.get("tenant_id") != tenant or candidates.get("snapshot_id") != snapshot:
        return _error(trace_id, "CANDIDATE_SCOPE_MISMATCH", "candidate context의 tenant 또는 snapshot이 다릅니다.")

    raw_allowlist = candidates.get("candidate_allowlist")
    if not isinstance(raw_allowlist, list):
        return _error(trace_id, "CANDIDATE_ALLOWLIST_INVALID", "candidate allowlist는 list여야 합니다.")
    allowlist_items = raw_allowlist
    asset_allowlist: dict[tuple[str, str], dict[str, Any]] = {}
    allowlist_projection: list[dict[str, str]] = []
    for item in allowlist_items:
        if not isinstance(item, dict) or set(item) != {
            "asset_id", "version", "asset_type", "technical_contract_status", "ports", "port_contract_sha256"
        }:
            return _error(trace_id, "CANDIDATE_ALLOWLIST_INVALID", "candidate asset 계약 형식이 유효하지 않습니다.")
        asset_id = _safe(item.get("asset_id"), 200)
        version = _safe(item.get("version"), 100)
        asset_type = str(item.get("asset_type") or "")
        status = str(item.get("technical_contract_status") or "")
        port_contract = _normalize_port_contract(item.get("ports"))
        port_contract_sha256 = str(item.get("port_contract_sha256") or "")
        identity = (asset_id, version)
        if (
            not asset_id
            or not version
            or asset_type not in {"component", "flow"}
            or status not in TECHNICAL_STATUSES
            or identity in asset_allowlist
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", port_contract_sha256)
            or not hmac.compare_digest(port_contract_sha256, _canonical_hash(port_contract))
        ):
            return _error(trace_id, "CANDIDATE_ALLOWLIST_INVALID", "candidate asset 또는 port 계약이 유효하지 않습니다.")
        clean_item = {
            "asset_id": asset_id,
            "version": version,
            "asset_type": asset_type,
            "technical_contract_status": status,
            "ports": port_contract,
            "port_contract_sha256": port_contract_sha256,
        }
        asset_allowlist[identity] = clean_item
        allowlist_projection.append({key: clean_item[key] for key in (
            "asset_id", "version", "asset_type", "technical_contract_status", "port_contract_sha256"
        )})
    candidate_allowlist_sha256 = str(candidates.get("candidate_allowlist_sha256") or "")
    if (
        not re.fullmatch(r"sha256:[0-9a-f]{64}", candidate_allowlist_sha256)
        or not hmac.compare_digest(candidate_allowlist_sha256, _canonical_hash(allowlist_projection))
    ):
        return _error(trace_id, "CANDIDATE_ALLOWLIST_INVALID", "candidate allowlist hash가 asset/port 계약과 일치하지 않습니다.")

    applied_items = skill_context.get("applied_skills") if isinstance(skill_context.get("applied_skills"), list) else []
    clean_applied_items: list[dict[str, str]] = []
    skill_allowlist: dict[tuple[str, str, str], dict[str, str]] = {}
    for item in applied_items:
        clean_skill = _project_applied_skill(item)
        if clean_skill is None:
            return _error(trace_id, "INVALID_APPLIED_SKILL_CONTEXT", "승인 Skill projection이 유효하지 않습니다.")
        identity = (clean_skill["skill_id"], clean_skill["version"], clean_skill["prompt_sha256"])
        if identity in skill_allowlist:
            return _error(trace_id, "DUPLICATE_SKILL_IDENTITY", "승인 Skill identity가 중복되었습니다.")
        skill_allowlist[identity] = clean_skill
        clean_applied_items.append(clean_skill)
    raw_nodes = draft.get("nodes") if isinstance(draft.get("nodes"), list) else []
    raw_edges = draft.get("edges") if isinstance(draft.get("edges"), list) else []
    max_nodes = max(1, min(500, int(max_nodes or 300)))
    max_edges = max(0, min(1000, int(max_edges or 600)))
    if not raw_nodes or len(raw_nodes) > max_nodes or len(raw_edges) > max_edges:
        return _error(trace_id, "BLUEPRINT_SIZE_INVALID", "node 또는 edge 수가 허용 범위를 벗어났습니다.")

    normalized_nodes: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    for index, raw in enumerate(raw_nodes, start=1):
        if not isinstance(raw, dict):
            return _error(trace_id, "INVALID_BLUEPRINT_NODE", "모든 blueprint node는 object여야 합니다.", details={"index": index})
        node_id = _safe(raw.get("node_id") or raw.get("id"), 100)
        source = str(raw.get("implementation_source") or "").lower()
        node_type = str(raw.get("node_type") or "task").strip().lower()
        if not node_id or node_id in node_ids:
            return _error(trace_id, "DUPLICATE_OR_MISSING_NODE_ID", "node_id가 없거나 중복되었습니다.", details={"index": index})
        if source not in IMPLEMENTATION_SOURCES:
            return _error(trace_id, "INVALID_IMPLEMENTATION_SOURCE", "허용되지 않는 implementation_source입니다.", details={"node_id": node_id})
        if node_type not in NODE_TYPES:
            return _error(
                trace_id,
                "INVALID_NODE_TYPE",
                "허용되지 않는 node_type입니다.",
                details={"node_id": node_id, "allowed": sorted(NODE_TYPES)},
            )
        if bool(raw.get("builtin_satisfies")) and source != "builtin":
            return _error(trace_id, "BUILTIN_PRIORITY_VIOLATION", "built-in으로 충족되는 기능에 다른 구현 출처를 선택할 수 없습니다.", details={"node_id": node_id})
        raw_asset_ref = raw.get("asset_ref") if isinstance(raw.get("asset_ref"), dict) else None
        asset_ref: dict[str, str] | None = None
        port_contract_sha256: str | None = None
        technical_status: str | None = None
        if source in {"catalog_component", "catalog_flow"}:
            key = (str((raw_asset_ref or {}).get("asset_id") or ""), str((raw_asset_ref or {}).get("version") or ""))
            allowed = asset_allowlist.get(key)
            if allowed is None:
                return _error(trace_id, "UNKNOWN_CATALOG_ASSET", "candidate set에 없는 asset ID/version을 참조했습니다.", details={"node_id": node_id})
            expected_type = "component" if source == "catalog_component" else "flow"
            if str(allowed.get("asset_type")) != expected_type:
                return _error(trace_id, "CATALOG_ASSET_TYPE_MISMATCH", "asset type과 implementation_source가 다릅니다.", details={"node_id": node_id})
            technical_status = str(allowed.get("technical_contract_status") or "metadata_only")
            if technical_status not in TECHNICAL_STATUSES:
                technical_status = "metadata_only"
            # Never preserve LLM-supplied extra fields from asset_ref.  The
            # candidate allowlist is the sole authority for catalog identity.
            asset_ref = {"asset_id": allowed["asset_id"], "version": allowed["version"]}
            port_contract_sha256 = str(allowed["port_contract_sha256"])
            # Candidate contract is authoritative; draft ports may not invent a
            # different runtime contract for a catalog asset.
            candidate_ports = allowed.get("ports") if isinstance(allowed.get("ports"), dict) else {}
            raw_input_ports = candidate_ports.get("inputs", [])
            raw_output_ports = candidate_ports.get("outputs", [])
        else:
            asset_ref = None
            raw_input_ports = (
                raw.get("inputs")
                if isinstance(raw.get("inputs"), list)
                else raw.get("input_ports")
                if isinstance(raw.get("input_ports"), list)
                else []
            )
            raw_output_ports = (
                raw.get("outputs")
                if isinstance(raw.get("outputs"), list)
                else raw.get("output_ports")
                if isinstance(raw.get("output_ports"), list)
                else []
            )
        if raw.get("generation_request_ref") or raw.get("generation_request"):
            code = "GENERATION_REQUEST_PREMATURE" if source == "new_standalone_component" else "GENERATION_REQUEST_NOT_ALLOWED"
            return _error(
                trace_id,
                code,
                "generation_request는 정규화·검증 뒤 신규 Standalone node에 대해 전용 builder가 만듭니다.",
                details={"node_id": node_id},
            )
        if source == "new_standalone_component":
            raw_generation_contract = raw.get("generation_contract")
            if not isinstance(raw_generation_contract, dict):
                contract_error = _generation_contract_error(raw_generation_contract)
                normalized_generation_contract: dict[str, Any] | None = None
            elif "secret_inputs" not in raw_generation_contract:
                contract_error = _generation_contract_error(raw_generation_contract)
                normalized_generation_contract = None
            else:
                normalized_secret_inputs, secret_input_error = _normalize_generation_secret_inputs(
                    raw_generation_contract.get("secret_inputs")
                )
                if secret_input_error:
                    code, details = secret_input_error
                    return _error(
                        trace_id,
                        code,
                        "신규 Standalone Component 생성 계약에 안전하지 않은 secret 선언이 있습니다.",
                        details={"node_id": node_id, **details},
                    )
                normalized_generation_contract = dict(raw_generation_contract)
                normalized_generation_contract["secret_inputs"] = normalized_secret_inputs
                contract_error = _generation_contract_error(normalized_generation_contract)
            if contract_error:
                code, details = contract_error
                return _error(trace_id, code, "신규 Standalone Component 생성 계약이 유효하지 않습니다.", details={"node_id": node_id, **details})
            generation_contract = json.loads(json.dumps(normalized_generation_contract, ensure_ascii=False))
        else:
            if raw.get("generation_contract") not in (None, {}):
                return _error(
                    trace_id,
                    "GENERATION_CONTRACT_NOT_ALLOWED",
                    "generation_contract는 신규 Standalone node에만 둘 수 있습니다.",
                    details={"node_id": node_id},
                )
            generation_contract = None

        for field_name in ("title", "responsibility", "reuse_decision_reason", "network_zone", "current_work", "improvement"):
            secret_path = _secret_value_path(raw.get(field_name), f"node[{node_id}].{field_name}")
            if secret_path:
                return _error(
                    trace_id,
                    "BLUEPRINT_SECRET_MATERIAL_DETECTED",
                    "blueprint 자유 서술 필드에는 secret 원문을 넣을 수 없습니다.",
                    details={"node_id": node_id, "field": secret_path},
                )
        for field_name in ("required_permissions", "timeout_policy", "failure_policy", "tests", "problems"):
            secret_path = _secret_material_path(raw.get(field_name), f"node[{node_id}].{field_name}")
            if secret_path:
                return _error(
                    trace_id,
                    "BLUEPRINT_SECRET_MATERIAL_DETECTED",
                    "blueprint 실행·검증 정책에는 secret 원문을 넣을 수 없습니다.",
                    details={"node_id": node_id, "field": secret_path},
                )
        for field_name, field_value in (("inputs", raw_input_ports), ("outputs", raw_output_ports)):
            secret_path = _secret_value_path(field_value, f"node[{node_id}].{field_name}")
            if secret_path:
                return _error(
                    trace_id,
                    "BLUEPRINT_SECRET_MATERIAL_DETECTED",
                    "port 계약에는 secret 원문을 넣을 수 없습니다.",
                    details={"node_id": node_id, "field": secret_path},
                )

        config = raw.get("config") if isinstance(raw.get("config"), dict) else {}
        secret_path = _secret_material_path(config)
        if secret_path:
            return _error(
                trace_id,
                "BLUEPRINT_SECRET_MATERIAL_DETECTED",
                "node config에는 secret 원문을 넣을 수 없습니다. required_secrets의 secret reference를 사용하세요.",
                details={"node_id": node_id, "field": secret_path},
            )

        node_skills: list[dict[str, Any]] = []
        for skill in raw.get("applied_skills", []) if isinstance(raw.get("applied_skills"), list) else []:
            if not isinstance(skill, dict):
                continue
            key = (str(skill.get("skill_id") or ""), str(skill.get("version") or ""), str(skill.get("prompt_sha256") or ""))
            allowed_skill = skill_allowlist.get(key)
            if allowed_skill is None:
                return _error(trace_id, "UNAPPROVED_SKILL_REFERENCE", "승인·적용된 set에 없는 Skill을 참조했습니다.", details={"node_id": node_id})
            node_skills.append({field: allowed_skill[field] for field in APPLIED_SKILL_FIELDS})

        input_ports = [port for idx, item in enumerate(raw_input_ports[:100], start=1) if (port := _normalize_port(item, idx, "input"))]
        output_ports = [port for idx, item in enumerate(raw_output_ports[:100], start=1) if (port := _normalize_port(item, idx, "output"))]
        title = _safe(raw.get("title") or raw.get("name") or node_id, 300)
        responsibility = _safe(raw.get("responsibility") or raw.get("description"), 2000)
        if not responsibility:
            responsibility = _default_node_responsibility(title, source)
        reuse_decision_reason = _safe(raw.get("reuse_decision_reason"), 1000)
        if not reuse_decision_reason:
            reuse_decision_reason = _default_reuse_decision_reason(source)
        node = {
            "node_id": node_id,
            "title": title,
            "node_type": node_type,
            "responsibility": responsibility,
            "current_work": _safe(raw.get("current_work") or raw.get("as_is"), 20_000),
            "problems": _normalize_problems(raw.get("problems")),
            "improvement": _safe(raw.get("improvement") or raw.get("to_be") or raw.get("responsibility"), 20_000),
            "implementation_source": source,
            "implementation_label": {
                "builtin": "기본 요소",
                "catalog_component": "기존 Component",
                "catalog_flow": "기존 Flow",
                "new_standalone_component": "신규 Custom",
                "companion_service": "외부 서비스",
                "human_task": "Human",
            }[source],
            "reuse_decision_reason": reuse_decision_reason,
            "asset_ref": asset_ref,
            "port_contract_sha256": port_contract_sha256,
            "technical_contract_status": technical_status,
            "runtime_validation_status": _safe(raw.get("runtime_validation_status") or "unverified", 50),
            "inputs": input_ports,
            "outputs": output_ports,
            "config": config,
            "required_secrets": _normalize_required_secrets(raw.get("required_secrets")),
            "required_permissions": [item for item in raw.get("required_permissions", [])[:50] if isinstance(item, dict)] if isinstance(raw.get("required_permissions"), list) else [],
            "network_zone": _safe(raw.get("network_zone"), 100),
            "timeout_policy": raw.get("timeout_policy") if isinstance(raw.get("timeout_policy"), dict) else {},
            "failure_policy": raw.get("failure_policy") if isinstance(raw.get("failure_policy"), dict) else {},
            "applied_skills": node_skills,
            "generation_contract": generation_contract,
            "tests": raw.get("tests")[:50] if isinstance(raw.get("tests"), list) else [],
        }
        normalized_nodes.append(node)
        node_ids.add(node_id)

    normalized_edges: list[dict[str, Any]] = []
    edge_ids: set[str] = set()
    for index, raw in enumerate(raw_edges, start=1):
        if not isinstance(raw, dict):
            return _error(trace_id, "INVALID_BLUEPRINT_EDGE", "모든 blueprint edge는 object여야 합니다.", details={"index": index})
        edge_id = _safe(raw.get("edge_id") or raw.get("id") or f"edge-{index}", 100)
        source_node = _safe(raw.get("source_node_id") or raw.get("source"), 100)
        target_node = _safe(raw.get("target_node_id") or raw.get("target"), 100)
        if edge_id in edge_ids or source_node not in node_ids or target_node not in node_ids:
            return _error(trace_id, "DANGLING_OR_DUPLICATE_EDGE", "edge ID가 중복되었거나 node 참조가 없습니다.", details={"edge_id": edge_id})
        for field_name in ("label", "branch_label", "condition", "mapping"):
            secret_path = _secret_material_path(raw.get(field_name), f"edge[{edge_id}].{field_name}")
            if secret_path:
                return _error(
                    trace_id,
                    "BLUEPRINT_SECRET_MATERIAL_DETECTED",
                    "edge 계약에는 secret 원문을 넣을 수 없습니다.",
                    details={"edge_id": edge_id, "field": secret_path},
                )
        normalized_edges.append(
            {
                "edge_id": edge_id,
                "source_node_id": source_node,
                "source_port_id": _safe(raw.get("source_port_id"), 100),
                "target_node_id": target_node,
                "target_port_id": _safe(raw.get("target_port_id"), 100),
                "edge_kind": _safe(raw.get("edge_kind") or "data", 50),
                "label": _safe(raw.get("label") if raw.get("label") is not None else raw.get("branch_label"), 500),
                "branch_label": _safe(raw.get("branch_label") if raw.get("branch_label") is not None else raw.get("label"), 500),
                "condition": _optional_safe(raw.get("condition"), 1000),
                "is_default": bool(raw.get("is_default") if "is_default" in raw else raw.get("default", False)),
                "default": bool(raw.get("default") if "default" in raw else raw.get("is_default", False)),
                "mapping": raw.get("mapping") if isinstance(raw.get("mapping"), dict) else {},
                "connection_validation_status": "unverified",
            }
        )
        edge_ids.add(edge_id)

    pattern = str(draft.get("pattern") or "deterministic_sequential").strip()
    pattern = LEGACY_PATTERN_ALIASES.get(pattern, pattern)
    if pattern not in PATTERNS:
        pattern = "deterministic_sequential"
    for field_name in ("roles", "human_gates", "failure_policy", "observability", "tests", "assumptions", "unresolved"):
        secret_path = _secret_material_path(draft.get(field_name), f"blueprint.{field_name}")
        if secret_path:
            return _error(
                trace_id,
                "BLUEPRINT_SECRET_MATERIAL_DETECTED",
                "blueprint 자유형 section에는 secret 원문을 넣을 수 없습니다.",
                details={"field": secret_path},
            )
    secret_path = _secret_value_path(draft.get("secrets_permissions"), "blueprint.secrets_permissions")
    if secret_path:
        return _error(
            trace_id,
            "BLUEPRINT_SECRET_MATERIAL_DETECTED",
            "secret/permission 선언에는 secret 원문을 넣을 수 없습니다.",
            details={"field": secret_path},
        )
    blueprint_core = {
        "schema_version": "agent-blueprint.v1",
        "tenant_id": tenant,
        "blueprint_id": _safe(draft.get("blueprint_id"), 200)
        or "bp-" + hashlib.sha256(f"{work.get('work_definition_id')}|{approved_hash}|{snapshot}".encode("utf-8")).hexdigest()[:20],
        "work_definition_id": str(work.get("work_definition_id") or ""),
        "work_definition_revision": work_revision,
        "approved_hash": approved_hash,
        "catalog_snapshot_id": snapshot,
        "pattern": pattern,
        "pattern_reason": _safe(draft.get("pattern_reason"), 1000),
        "roles": draft.get("roles")[:100] if isinstance(draft.get("roles"), list) else [],
        "nodes": normalized_nodes,
        "edges": normalized_edges,
        "applied_skills": [
            {field: item[field] for field in APPLIED_SKILL_FIELDS}
            for item in clean_applied_items[:50]
        ],
        "recommended_assets": [
            {key: item[key] for key in (
                "asset_id", "version", "asset_type", "technical_contract_status", "port_contract_sha256"
            )}
            for item in asset_allowlist.values()
        ],
        "generation_requests": [],
        "human_gates": draft.get("human_gates")[:100] if isinstance(draft.get("human_gates"), list) else [],
        "secrets_permissions": draft.get("secrets_permissions")[:200] if isinstance(draft.get("secrets_permissions"), list) else [],
        "failure_policy": draft.get("failure_policy") if isinstance(draft.get("failure_policy"), dict) else {},
        "observability": draft.get("observability") if isinstance(draft.get("observability"), dict) else {},
        "tests": draft.get("tests")[:200] if isinstance(draft.get("tests"), list) else [],
        "assumptions": draft.get("assumptions")[:200] if isinstance(draft.get("assumptions"), list) else [],
        "unresolved": draft.get("unresolved")[:200] if isinstance(draft.get("unresolved"), list) else [],
        "flow_import_verified": False,
        "build_readiness": "design_only",
        "readiness_assessment": {
            "status_axis": "build_readiness",
            "technical_status_axis": "technical_contract_status",
            "connection_status_axis": "connection_validation_status",
            "blockers": [{"code": "PORT_VALIDATION_PENDING", "ref": None}],
            "warnings": [],
            "import_requirements": [{"code": "VERIFY_FLOW_IMPORT", "ref": None}],
            "flow_import_verified": False,
        },
    }
    blueprint_core["to_be_graph"] = {"nodes": [item["node_id"] for item in normalized_nodes], "edges": [item["edge_id"] for item in normalized_edges]}
    return {
        "ok": True,
        "status": "COMPLETED",
        "blueprint": blueprint_core,
        "blueprint_sha256": _canonical_hash(blueprint_core),
        "trace_id": trace_id,
    }


def _error(trace_id: str, code: str, message: str, *, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "BLOCKED",
        "artifact_refs": [],
        "error": {"code": code, "message": message, "retryable": False, "details": details or {}},
        "resume": None,
        "trace_id": trace_id,
    }


def normalize_agent_blueprint_from_scope(
    blueprint_draft: Any,
    design_scope: Any,
    candidate_context: Any,
    applied_skill_context: Any,
    *,
    max_nodes: int = 300,
    max_edges: int = 600,
) -> dict[str, Any]:
    """Normalize only when every downstream artifact has the same scope lock."""
    trace_id = str(uuid.uuid4())
    for upstream in (design_scope, candidate_context, applied_skill_context):
        blocked = _forward_blocked_envelope(upstream, trace_id=trace_id)
        if blocked is not None:
            return blocked
    scope = _payload(design_scope)
    candidates = _payload(candidate_context)
    skills = _payload(applied_skill_context)
    draft = _blueprint_draft_payload(blueprint_draft)
    required = (
        "tenant_id",
        "catalog_snapshot_id",
        "work_definition_id",
        "work_definition_revision",
        "approved_hash",
        "design_scope_sha256",
        "work_definition",
    )
    supplied_scope_hash = str(scope.get("design_scope_sha256") or "")
    if (
        scope.get("schema_version") != "agent-design-scope/v1"
        or scope.get("ok") is not True
        or any(scope.get(key) in (None, "") for key in required)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", supplied_scope_hash)
        or not hmac.compare_digest(supplied_scope_hash, _design_scope_hash(scope))
    ):
        return _error(trace_id, "DESIGN_SCOPE_INVALID", "검증·봉인된 design scope가 필요합니다.")
    if not draft:
        return _error(
            trace_id,
            "INVALID_BLUEPRINT_DRAFT",
            "Blueprint Model 응답은 단일 JSON object 또는 하나의 json code fence여야 합니다.",
            details={
                "next_actions": [
                    "Blueprint Language Model의 system message를 유지하고 JSON object만 반환하도록 설정합니다.",
                    "모델의 설명문·여러 code fence·부분 JSON은 제거합니다.",
                ]
            },
        )
    expected = {
        "tenant_id": str(scope["tenant_id"]),
        "snapshot_id": str(scope["catalog_snapshot_id"]),
        "work_definition_id": str(scope["work_definition_id"]),
        "work_definition_revision": str(scope["work_definition_revision"]),
        "approved_hash": str(scope["approved_hash"]),
        "design_scope_sha256": str(scope["design_scope_sha256"]),
    }
    # The trusted scope owns these values.  A model may omit identity locks
    # despite the prompt contract; attach only absent values and keep an
    # explicit conflicting value fail-closed below.
    if draft:
        draft = dict(draft)
        for field, expected_value in {
            "catalog_snapshot_id": expected["snapshot_id"],
            "work_definition_id": expected["work_definition_id"],
            "work_definition_revision": scope["work_definition_revision"],
            "approved_hash": expected["approved_hash"],
        }.items():
            if draft.get(field) in (None, ""):
                draft[field] = expected_value
    query_plan_sha256 = str(candidates.get("query_plan_sha256") or "")
    raw_allowlist = candidates.get("candidate_allowlist")
    allowlist_items = raw_allowlist if isinstance(raw_allowlist, list) else []
    allowlist_projection: list[dict[str, str]] = []
    allowlist_valid = isinstance(raw_allowlist, list)
    seen_allowlist: set[tuple[str, str]] = set()
    for item in allowlist_items:
        if not isinstance(item, dict):
            allowlist_valid = False
            break
        projection = {
            "asset_id": str(item.get("asset_id") or ""),
            "version": str(item.get("version") or ""),
            "asset_type": str(item.get("asset_type") or ""),
            "technical_contract_status": str(item.get("technical_contract_status") or ""),
            "port_contract_sha256": str(item.get("port_contract_sha256") or ""),
        }
        canonical_ports = _normalize_port_contract(item.get("ports"))
        identity = (projection["asset_id"], projection["version"])
        if (
            not projection["asset_id"]
            or not projection["version"]
            or projection["asset_type"] not in {"component", "flow"}
            or projection["technical_contract_status"] not in TECHNICAL_STATUSES
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", projection["port_contract_sha256"])
            or not hmac.compare_digest(projection["port_contract_sha256"], _canonical_hash(canonical_ports))
            or identity in seen_allowlist
        ):
            allowlist_valid = False
            break
        seen_allowlist.add(identity)
        allowlist_projection.append(projection)
    expected_allowlist_sha256 = _canonical_hash(allowlist_projection) if allowlist_valid else ""
    candidate_allowlist_sha256 = str(candidates.get("candidate_allowlist_sha256") or "")
    mismatches: list[str] = []
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", query_plan_sha256):
        mismatches.append("candidate_context.query_plan_sha256")
    if (
        not allowlist_valid
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", candidate_allowlist_sha256)
        or not hmac.compare_digest(candidate_allowlist_sha256, expected_allowlist_sha256)
    ):
        mismatches.append("candidate_context.candidate_allowlist_sha256")
    for label, context in (("candidate_context", candidates), ("skill_context", skills)):
        actual = {
            "tenant_id": str(context.get("tenant_id") or ""),
            "snapshot_id": str(context.get("snapshot_id") or context.get("catalog_snapshot_id") or ""),
            "work_definition_id": str(context.get("work_definition_id") or ""),
            "work_definition_revision": str(context.get("work_definition_revision") if context.get("work_definition_revision") is not None else ""),
            "approved_hash": str(context.get("approved_hash") or ""),
            "design_scope_sha256": str(context.get("design_scope_sha256") or ""),
        }
        mismatches.extend(f"{label}.{key}" for key, value in expected.items() if actual.get(key) != value)
    draft_locks = {
        "snapshot_id": str(draft.get("catalog_snapshot_id") or ""),
        "work_definition_id": str(draft.get("work_definition_id") or ""),
        "work_definition_revision": str(draft.get("work_definition_revision") if draft.get("work_definition_revision") is not None else ""),
        "approved_hash": str(draft.get("approved_hash") or ""),
    }
    mismatches.extend(f"blueprint_draft.{key}" for key in draft_locks if draft_locks[key] != expected[key])
    if mismatches:
        return _error(
            trace_id,
            "DESIGN_CONTEXT_LOCK_MISMATCH",
            "승인 업무, Skill, 검색 후보 또는 blueprint draft의 identity lock이 일치하지 않습니다.",
            details={"fields": sorted(mismatches)},
        )
    result = normalize_agent_blueprint(
        draft,
        scope["work_definition"],
        candidates,
        skills,
        tenant_id=scope["tenant_id"],
        catalog_snapshot_id=scope["catalog_snapshot_id"],
        max_nodes=max_nodes,
        max_edges=max_edges,
    )
    if result.get("ok") is True and isinstance(result.get("blueprint"), dict):
        result["blueprint"]["design_scope_sha256"] = scope["design_scope_sha256"]
        result["blueprint"]["query_plan_sha256"] = query_plan_sha256
        result["blueprint"]["candidate_allowlist_sha256"] = candidate_allowlist_sha256
        result["blueprint_sha256"] = _canonical_hash(result["blueprint"])
    return result


class AgentBlueprintNormalizerComponent(Component):
    display_name = "23 Agent Blueprint Normalizer"
    description = "TO-BE blueprint를 구현 출처와 승인 asset/Skill allowlist에 맞춰 결정론적으로 정규화합니다."
    icon = "Workflow"
    name = "AgentBlueprintNormalizer"

    inputs = [
        HandleInput(
            name="blueprint_draft",
            display_name="Blueprint Draft",
            input_types=["Data", "JSON", "Message"],
            required=True,
            info="Connect Blueprint Language Model directly. Accepts one JSON object or one complete json code fence only.",
        ),
        DataInput(name="design_scope", display_name="Sealed Design Scope", required=True),
        DataInput(name="candidate_context", display_name="Verified Candidate Context", required=True),
        DataInput(name="applied_skill_context", display_name="Verified Skill Context", required=True),
        IntInput(name="max_nodes", display_name="Maximum Nodes", value=300, advanced=True),
        IntInput(name="max_edges", display_name="Maximum Edges", value=600, advanced=True),
    ]
    outputs = [Output(name="normalized_blueprint", display_name="Normalized Agent Blueprint", method="build_blueprint", types=["Data"])]

    def build_blueprint(self) -> Data:
        result = normalize_agent_blueprint_from_scope(
            self.blueprint_draft,
            self.design_scope,
            self.candidate_context,
            self.applied_skill_context,
            max_nodes=getattr(self, "max_nodes", 300),
            max_edges=getattr(self, "max_edges", 600),
        )
        node_count = len((result.get("blueprint") or {}).get("nodes", []))
        self.status = f"Blueprint normalize: {result.get('status')} / nodes={node_count}"
        return Data(data=result)
