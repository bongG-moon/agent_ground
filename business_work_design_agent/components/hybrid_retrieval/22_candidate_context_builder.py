from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, IntInput, Output
from lfx.schema import Data


TECHNICAL_STATUSES = {"metadata_only", "ports_extracted", "flow_graph_extracted", "verified_runtime"}
RECOMMENDATION_STATUSES = {"candidate", "recommended", "alternative", "rejected"}
IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
    """Preserve a concrete retrieval/configuration failure for F20/F90 output."""
    payload = _payload(value)
    error = payload.get("error")
    if payload.get("ok") is not False or str(payload.get("status") or "") != "BLOCKED" or not isinstance(error, dict):
        return None
    details = error.get("details")
    forwarded_details = dict(details) if isinstance(details, dict) else {}
    upstream_trace_id = str(payload.get("trace_id") or "").strip()
    if upstream_trace_id:
        forwarded_details.setdefault("upstream_trace_id", upstream_trace_id)
    return {
        "ok": False,
        "status": "BLOCKED",
        "artifact_refs": [],
        "error": {
            "code": str(error.get("code") or "UPSTREAM_RETRIEVAL_STAGE_BLOCKED"),
            "message": str(error.get("message") or "이전 검색 단계가 차단되었습니다."),
            "retryable": error.get("retryable") is True,
            "details": forwarded_details,
        },
        "resume": None,
        "trace_id": trace_id,
    }


def _safe_text(value: Any, maximum: int) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value or "")).strip()[:maximum]


def _normalize_port(item: Any, index: int, direction: str) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    port_id = _safe_text(item.get("port_id") or item.get("name") or f"{direction}-{index}", 100)
    if not port_id:
        return None
    cardinality = _safe_text(item.get("cardinality") or "one", 20).lower()
    if cardinality not in {"one", "many"}:
        cardinality = "one"
    return {
        "port_id": port_id,
        "name": _safe_text(item.get("name") or port_id, 100),
        "data_type": _safe_text(item.get("data_type") or item.get("type") or "", 100),
        "semantic_role": _safe_text(item.get("semantic_role"), 100),
        "schema_ref": _safe_text(item.get("schema_ref"), 500),
        "cardinality": cardinality,
        "required": bool(item.get("required", direction == "input")),
        "has_default": bool(item.get("has_default", False)),
        "secret": bool(item.get("secret", False)),
        "permission": _safe_text(item.get("permission"), 200),
        "network_zone": _safe_text(item.get("network_zone"), 100),
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


def _completed_context(
    *,
    trace_id: str,
    retrieval: dict[str, Any],
    retrieval_trace: dict[str, Any],
    max_items: int,
    per_item_chars: int,
    total_context_chars: int,
    items: list[dict[str, Any]],
    context_blocks: list[str],
    used_chars: int,
    dropped: dict[str, int],
    allowlist: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the sealed candidate context for non-empty and empty results.

    A successful search with no authorized catalog candidates is represented as
    an explicit empty allowlist.  It is intentionally different from a broken
    retrieval payload or an item-budget failure: downstream nodes can then
    design with built-ins, a new standalone component, or a human task, but
    cannot name any catalog asset.
    """
    allowlist_projection = [
        {
            "asset_id": item["asset_id"],
            "version": item["version"],
            "asset_type": item["asset_type"],
            "technical_contract_status": item["technical_contract_status"],
            "port_contract_sha256": item["port_contract_sha256"],
        }
        for item in allowlist
    ]
    candidate_allowlist_sha256 = _canonical_hash(allowlist_projection)
    catalog_reference_policy = "allow_candidate_allowlist" if allowlist else "deny_all_catalog_assets"
    catalog_candidate_status = "available" if allowlist else "none_available"
    sealed_retrieval_trace = {
        **retrieval_trace,
        "candidate_allowlist": allowlist_projection,
        "candidate_allowlist_sha256": candidate_allowlist_sha256,
        "catalog_reference_policy": catalog_reference_policy,
        "catalog_candidate_status": catalog_candidate_status,
    }
    return {
        "ok": True,
        "status": "COMPLETED",
        "tenant_id": str(retrieval["tenant_id"]),
        "snapshot_id": str(retrieval["snapshot_id"]),
        "work_definition_id": str(retrieval["work_definition_id"]),
        "work_definition_revision": retrieval["work_definition_revision"],
        "approved_hash": str(retrieval["approved_hash"]),
        "design_scope_sha256": str(retrieval["design_scope_sha256"]),
        "query_plan_sha256": str(retrieval.get("query_plan_sha256") or ""),
        "provider_mode": retrieval.get("provider_mode"),
        "candidate_items": items,
        "candidate_allowlist": allowlist,
        "candidate_allowlist_sha256": candidate_allowlist_sha256,
        "catalog_reference_policy": catalog_reference_policy,
        "catalog_candidate_status": catalog_candidate_status,
        "untrusted_candidate_context": "\n\n".join(context_blocks),
        "context_char_count": used_chars,
        "limits": {
            "max_items": max_items,
            "per_item_chars": per_item_chars,
            "total_context_chars": total_context_chars,
        },
        "dropped": dropped,
        "retrieval_trace": sealed_retrieval_trace,
        "trace_id": trace_id,
    }


def build_candidate_context(
    retrieval_result: Any,
    *,
    max_items: int = 20,
    per_item_chars: int = 3000,
    total_context_chars: int = 30000,
) -> dict[str, Any]:
    trace_id = str(uuid.uuid4())
    blocked = _forward_blocked_envelope(retrieval_result, trace_id=trace_id)
    if blocked is not None:
        return blocked
    retrieval = _payload(retrieval_result)
    if retrieval.get("ok") is not True:
        return _error(trace_id, "RETRIEVAL_NOT_READY", "성공한 retrieval_result가 필요합니다.")
    tenant_id = str(retrieval.get("tenant_id") or "")
    snapshot_id = str(retrieval.get("snapshot_id") or "")
    if not IDENTITY_PATTERN.fullmatch(tenant_id) or not IDENTITY_PATTERN.fullmatch(snapshot_id):
        return _error(trace_id, "RETRIEVAL_SCOPE_MISSING", "retrieval_result에 tenant와 snapshot이 없습니다.")
    lock_fields = (
        "work_definition_id", "work_definition_revision", "approved_hash", "design_scope_sha256", "query_plan_sha256"
    )
    if any(retrieval.get(field) in (None, "") for field in lock_fields):
        return _error(trace_id, "RETRIEVAL_LOCK_MISSING", "retrieval_result에 승인 업무와 design scope lock이 없습니다.")
    if (
        not IDENTITY_PATTERN.fullmatch(str(retrieval.get("work_definition_id") or ""))
        or type(retrieval.get("work_definition_revision")) is not int
        or retrieval["work_definition_revision"] < 0
        or any(
            not SHA256_PATTERN.fullmatch(str(retrieval.get(field) or ""))
            for field in ("approved_hash", "design_scope_sha256", "query_plan_sha256")
        )
    ):
        return _error(trace_id, "RETRIEVAL_LOCK_INVALID", "retrieval_result의 provenance lock 형식이 유효하지 않습니다.")
    raw_trace = retrieval.get("retrieval_trace") if isinstance(retrieval.get("retrieval_trace"), dict) else {}
    locked_trace = {
        "tenant_id": tenant_id,
        "snapshot_id": snapshot_id,
        "work_definition_id": str(retrieval["work_definition_id"]),
        "work_definition_revision": retrieval["work_definition_revision"],
        "approved_hash": str(retrieval["approved_hash"]),
        "design_scope_sha256": str(retrieval["design_scope_sha256"]),
        "query_plan_sha256": str(retrieval["query_plan_sha256"]),
    }
    if any(field in raw_trace and raw_trace.get(field) != expected for field, expected in locked_trace.items()):
        return _error(trace_id, "RETRIEVAL_TRACE_LOCK_MISMATCH", "retrieval trace가 승인 검색 범위와 일치하지 않습니다.")
    retrieval_trace = {**raw_trace, **locked_trace}
    raw_candidates = retrieval.get("candidates")
    if not isinstance(raw_candidates, list):
        return _error(trace_id, "RETRIEVAL_CANDIDATES_INVALID", "retrieval_result의 candidates는 list여야 합니다.")
    candidates = raw_candidates
    max_items = max(1, min(50, int(max_items or 20)))
    per_item_chars = max(300, min(12000, int(per_item_chars or 3000)))
    total_context_chars = max(per_item_chars, min(100000, int(total_context_chars or 30000)))

    seen: set[tuple[str, str]] = set()
    items: list[dict[str, Any]] = []
    context_blocks: list[str] = []
    used_chars = 0
    dropped = {"duplicate": 0, "invalid": 0, "budget": 0}
    allowlist: list[dict[str, Any]] = []
    if not candidates:
        return _completed_context(
            trace_id=trace_id,
            retrieval=retrieval,
            retrieval_trace=retrieval_trace,
            max_items=max_items,
            per_item_chars=per_item_chars,
            total_context_chars=total_context_chars,
            items=items,
            context_blocks=context_blocks,
            used_chars=used_chars,
            dropped=dropped,
            allowlist=allowlist,
        )
    for position, raw in enumerate(candidates, start=1):
        if not isinstance(raw, dict):
            dropped["invalid"] += 1
            continue
        asset_id = _safe_text(raw.get("asset_id"), 200)
        version = _safe_text(raw.get("version"), 100)
        asset_type = _safe_text(raw.get("asset_type"), 50)
        key = (asset_id, version)
        if not asset_id or not version or asset_type not in {"component", "flow"}:
            dropped["invalid"] += 1
            continue
        if key in seen:
            dropped["duplicate"] += 1
            continue
        seen.add(key)
        technical_status = str(raw.get("technical_contract_status") or "metadata_only")
        if technical_status not in TECHNICAL_STATUSES:
            technical_status = "metadata_only"
        recommendation_status = str(raw.get("recommendation_status") or "candidate")
        if recommendation_status not in RECOMMENDATION_STATUSES:
            recommendation_status = "candidate"
        description = _safe_text(raw.get("description"), min(1500, per_item_chars))
        readme_budget = max(0, per_item_chars - len(description) - 800)
        readme_excerpt = _safe_text(raw.get("readme"), readme_budget)
        port_contract = _normalize_port_contract(raw.get("ports"))
        port_contract_sha256 = _canonical_hash(port_contract)
        item = {
            "asset_id": asset_id,
            "version": version,
            "asset_type": asset_type,
            "title": _safe_text(raw.get("title"), 400),
            "category": _safe_text(raw.get("category"), 200),
            "description": description,
            "readme_excerpt": readme_excerpt,
            "recommendation_status": recommendation_status,
            "technical_contract_status": technical_status,
            "metadata_only": technical_status == "metadata_only",
            "ports": port_contract,
            "port_contract_sha256": port_contract_sha256,
            "limitations": [
                _safe_text(value, 500)
                for value in (raw.get("limitations") if isinstance(raw.get("limitations"), list) else [])[:10]
            ],
            "retrieval_trace": raw.get("retrieval_trace") if isinstance(raw.get("retrieval_trace"), dict) else {},
        }
        block = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        framed = f'<untrusted-catalog-candidate rank="{position}">\n{block}\n</untrusted-catalog-candidate>'
        if len(items) >= max_items or used_chars + len(framed) > total_context_chars:
            dropped["budget"] += 1
            continue
        items.append(item)
        context_blocks.append(framed)
        used_chars += len(framed)
        allowlist.append(
            {
                "asset_id": asset_id,
                "version": version,
                "asset_type": asset_type,
                "technical_contract_status": technical_status,
                "ports": item["ports"],
                "port_contract_sha256": port_contract_sha256,
            }
        )
    if not items:
        return _error(trace_id, "CONTEXT_BUDGET_EXHAUSTED", "제한 안에 포함할 수 있는 유효 후보가 없습니다.")
    return _completed_context(
        trace_id=trace_id,
        retrieval=retrieval,
        retrieval_trace=retrieval_trace,
        max_items=max_items,
        per_item_chars=per_item_chars,
        total_context_chars=total_context_chars,
        items=items,
        context_blocks=context_blocks,
        used_chars=used_chars,
        dropped=dropped,
        allowlist=allowlist,
    )


def _error(trace_id: str, code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "BLOCKED",
        "artifact_refs": [],
        "error": {"code": code, "message": message, "retryable": False, "details": {}},
        "resume": None,
        "trace_id": trace_id,
    }


class CandidateContextBuilderComponent(Component):
    display_name = "22 Candidate Context Builder"
    description = "검색 후보를 bounded untrusted context와 asset allowlist로 만듭니다. 정상 검색 후보가 0개면 catalog 참조를 전부 금지하는 빈 allowlist를 반환합니다."
    icon = "Rows3"
    name = "CandidateContextBuilder"

    inputs = [
        DataInput(name="retrieval_result", display_name="Retrieval Result", required=True),
        IntInput(name="max_items", display_name="Maximum Items", value=20, advanced=True),
        IntInput(name="per_item_chars", display_name="Characters per Item", value=3000, advanced=True),
        IntInput(name="total_context_chars", display_name="Total Context Characters", value=30000, advanced=True),
    ]
    outputs = [Output(name="candidate_context", display_name="Bounded Candidate Context", method="build_context", types=["Data"])]

    def build_context(self) -> Data:
        result = build_candidate_context(
            self.retrieval_result,
            max_items=getattr(self, "max_items", 20),
            per_item_chars=getattr(self, "per_item_chars", 3000),
            total_context_chars=getattr(self, "total_context_chars", 30000),
        )
        self.status = f"Candidate context: {result.get('status')} / items={len(result.get('candidate_items', []))}"
        return Data(data=result)
