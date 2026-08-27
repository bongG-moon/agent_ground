from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, Output
from lfx.schema import Data


SCHEMA_VERSION = "work-definition/v1"
FACT_STATUSES = {"confirmed", "inferred", "unknown", "conflicting"}
SCALAR_FACT_FIELDS = ("goal", "trigger", "frequency_volume", "sla", "automation_intent")
LIST_FIELDS = (
    "scope_in",
    "scope_out",
    "actors",
    "systems",
    "inputs",
    "outputs",
    "steps",
    "decisions",
    "exceptions",
    "pains",
    "risks_controls",
    "constraints",
    "success_criteria",
    "assumptions",
    "unresolved",
)


def _payload(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return copy.deepcopy(value)
    data = getattr(value, "data", None)
    if isinstance(data, (dict, list)):
        return copy.deepcopy(data)
    text = getattr(value, "text", value if isinstance(value, str) else "")
    if isinstance(text, str) and text.strip():
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
        return json.loads(candidate)
    return None


def _unwrap_named(payload: Any, *names: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    for name in names:
        nested = payload.get(name)
        if isinstance(nested, dict):
            return copy.deepcopy(nested)
    return copy.deepcopy(payload)


def _safe_id(value: Any, prefix: str, material: str) -> str:
    supplied = re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(value or "").strip())[:200].strip("-")
    if supplied:
        return supplied
    return f"{prefix}-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _evidence_ids(value: Any, fallback_turn: str) -> list[str]:
    raw = value if isinstance(value, list) else []
    result: list[str] = []
    for item in raw + ([fallback_turn] if fallback_turn else []):
        text = str(item or "").strip()[:200]
        if text and text not in result:
            result.append(text)
    return result[:50]


def _fact(value: Any, *, revision: int, turn_id: str, existing: Any = None) -> dict[str, Any]:
    if isinstance(existing, dict) and existing.get("status") in FACT_STATUSES and "value" in existing:
        prior = copy.deepcopy(existing)
    else:
        prior = None
    source = value if isinstance(value, dict) and "value" in value else {"value": value}
    supplied_value = source.get("value")
    if supplied_value in (None, "", [], {}):
        if prior is not None:
            return prior
        return {
            "value": None,
            "status": "unknown",
            "evidence_turn_ids": [],
            "confidence": 0.0,
            "last_updated_revision": revision,
        }
    if prior is not None and prior.get("status") in {"confirmed", "conflicting"}:
        # A model-normalization stage cannot replace user-confirmed evidence.
        return prior
    requested_status = str(source.get("status") or "inferred").lower()
    status = requested_status if requested_status in {"inferred", "unknown", "conflicting"} else "inferred"
    try:
        confidence = float(source.get("confidence", 0.7 if status == "inferred" else 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "value": copy.deepcopy(supplied_value),
        "status": status,
        "evidence_turn_ids": _evidence_ids(source.get("evidence_turn_ids"), turn_id),
        "confidence": max(0.0, min(confidence, 1.0)),
        "last_updated_revision": revision,
    }


def _list_item(item: Any, *, field: str, index: int, work_id: str, revision: int, turn_id: str) -> dict[str, Any]:
    record = copy.deepcopy(item) if isinstance(item, dict) else {"value": copy.deepcopy(item)}
    identity = record.get("id") or record.get(f"{field[:-1]}_id") or record.get("step_id") or record.get("name") or record.get("label") or record.get("title") or record.get("value")
    item_id = _safe_id(identity, field.rstrip("s") or "item", f"{work_id}|{field}|{index}|{json.dumps(record, sort_keys=True, ensure_ascii=False, default=str)}")
    record.setdefault("id", item_id)
    supplied_provenance = record.get("provenance") if isinstance(record.get("provenance"), dict) else {}
    requested_status = str(supplied_provenance.get("status") or record.pop("status", "inferred")).lower()
    try:
        confidence = float(supplied_provenance.get("confidence", 0.7))
    except (TypeError, ValueError):
        confidence = 0.0
    record["provenance"] = {
        "status": requested_status if requested_status in {"inferred", "unknown", "conflicting"} else "inferred",
        "evidence_turn_ids": _evidence_ids(supplied_provenance.get("evidence_turn_ids") or record.pop("evidence_turn_ids", []), turn_id),
        "confidence": max(0.0, min(confidence, 1.0)),
        "last_updated_revision": revision,
    }
    return record


def _merge_lists(candidate: Any, existing: Any, *, field: str, work_id: str, revision: int, turn_id: str) -> list[dict[str, Any]]:
    prior = copy.deepcopy(existing) if isinstance(existing, list) else []
    incoming = candidate if isinstance(candidate, list) else ([] if candidate in (None, "") else [candidate])
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for index, item in enumerate(prior):
        if not isinstance(item, dict):
            item = {"value": item}
        normalized = copy.deepcopy(item)
        item_id = _safe_id(normalized.get("id"), field.rstrip("s") or "item", f"{work_id}|{field}|prior|{index}")
        normalized["id"] = item_id
        by_id[item_id] = normalized
        order.append(item_id)
    for index, item in enumerate(incoming):
        normalized = _list_item(item, field=field, index=index, work_id=work_id, revision=revision, turn_id=turn_id)
        item_id = normalized["id"]
        prior_item = by_id.get(item_id)
        prior_status = ((prior_item or {}).get("provenance") or {}).get("status") if isinstance(prior_item, dict) else None
        if prior_item is not None and prior_status in {"confirmed", "conflicting"}:
            continue
        by_id[item_id] = normalized
        if item_id not in order:
            order.append(item_id)
    return [by_id[item_id] for item_id in order]


def normalize_work_definition(candidate_value: Any, envelope_value: Any, existing_value: Any = None) -> dict[str, Any]:
    try:
        candidate = _unwrap_named(_payload(candidate_value), "work_definition", "candidate")
        envelope = _unwrap_named(_payload(envelope_value), "envelope")
        existing = _unwrap_named(_payload(existing_value), "work_definition") if existing_value is not None else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        candidate, envelope, existing = {}, {}, {}
    trace_id = f"trace-{uuid.uuid4()}"
    required_envelope = ("work_definition_id", "tenant_id", "owner_id", "session_id", "channel_mode", "source_request")
    missing = [key for key in required_envelope if not envelope.get(key)]
    if missing or not isinstance(candidate, dict):
        return {
            "ok": False,
            "status": "BLOCKED",
            "artifact_refs": [],
            "error": {
                "code": "WORK_DEFINITION_INPUT_INVALID",
                "message": "업무 정의 후보 또는 요청 Envelope가 유효하지 않습니다.",
                "retryable": False,
                "details": {"missing_envelope_fields": missing},
            },
            "resume": None,
            "trace_id": trace_id,
        }

    work_id = str(envelope["work_definition_id"])
    if existing and any(str(existing.get(key, "")) != str(envelope.get(key, "")) for key in ("work_definition_id", "tenant_id", "owner_id", "session_id", "channel_mode")):
        return {
            "ok": False,
            "status": "BLOCKED",
            "artifact_refs": [],
            "error": {"code": "WORK_DEFINITION_IDENTITY_MISMATCH", "message": "기존 업무 정의와 요청의 tenant/session 식별자가 다릅니다.", "retryable": False, "details": {}},
            "resume": None,
            "trace_id": trace_id,
        }
    try:
        revision = int(existing.get("revision", envelope.get("expected_revision", 0)) or 0)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "status": "BLOCKED",
            "artifact_refs": [],
            "error": {"code": "WORK_DEFINITION_REVISION_INVALID", "message": "WorkDefinition revision은 0 이상의 정수여야 합니다.", "retryable": False, "details": {}},
            "resume": None,
            "trace_id": trace_id,
        }
    if revision < 0:
        return {
            "ok": False,
            "status": "BLOCKED",
            "artifact_refs": [],
            "error": {"code": "WORK_DEFINITION_REVISION_INVALID", "message": "WorkDefinition revision은 0 이상의 정수여야 합니다.", "retryable": False, "details": {}},
            "resume": None,
            "trace_id": trace_id,
        }
    source = envelope.get("source_request") if isinstance(envelope.get("source_request"), dict) else {}
    turn_id = str(source.get("turn_id") or "")
    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "work_definition_id": work_id,
        "tenant_id": str(envelope["tenant_id"]),
        "owner_id": str(envelope["owner_id"]),
        "session_id": str(envelope["session_id"]),
        "channel_mode": str(envelope["channel_mode"]),
        "revision": revision,
        "status": "EXTRACTING",
        "source_requests": copy.deepcopy(existing.get("source_requests", [])) if isinstance(existing.get("source_requests"), list) else [],
        "preview_hash": existing.get("preview_hash"),
        "approved_hash": existing.get("approved_hash"),
        "as_is_graph": copy.deepcopy(existing.get("as_is_graph") or candidate.get("as_is_graph") or {"nodes": [], "edges": []}),
        "processed_answer_batches": copy.deepcopy(existing.get("processed_answer_batches", [])),
    }
    if source and not any(item.get("turn_id") == turn_id for item in document["source_requests"] if isinstance(item, dict)):
        document["source_requests"].append(copy.deepcopy(source))

    for field in SCALAR_FACT_FIELDS:
        document[field] = _fact(candidate.get(field), revision=revision, turn_id=turn_id, existing=existing.get(field))
    for field in LIST_FIELDS:
        document[field] = _merge_lists(candidate.get(field), existing.get(field), field=field, work_id=work_id, revision=revision, turn_id=turn_id)

    # Keep only explicitly namespaced extensions; arbitrary model output does
    # not become an executable or trusted WorkDefinition field.
    extensions = candidate.get("extensions")
    if isinstance(extensions, dict):
        document["extensions"] = copy.deepcopy(extensions)

    return {
        "ok": True,
        "status": document["status"],
        "artifact_refs": [{"kind": "work_definition", "id": work_id, "revision": revision}],
        "work_definition": document,
        "trace_id": trace_id,
    }


class WorkDefinitionNormalizerComponent(Component):
    display_name = "11 업무 정의 정규화"
    description = "모델의 JSON 후보를 stable ID와 provenance가 있는 WorkDefinition v1으로 결정론적으로 정규화합니다."
    icon = "Braces"
    name = "WorkDefinitionNormalizer"

    inputs = [
        DataInput(name="candidate", display_name="모델 추출 후보", input_types=["Data", "Message", "JSON"], required=True),
        DataInput(name="request_envelope", display_name="업무 요청 Envelope", input_types=["Data", "JSON"], required=True),
        DataInput(name="existing_work_definition", display_name="기존 WorkDefinition", input_types=["Data", "JSON"], required=False, advanced=True),
    ]
    outputs = [Output(name="work_definition", display_name="정규화 WorkDefinition", method="build_work_definition", types=["Data"])]

    def build_work_definition(self) -> Data:
        result = normalize_work_definition(
            getattr(self, "candidate", None),
            getattr(self, "request_envelope", None),
            getattr(self, "existing_work_definition", None),
        )
        self.status = {"ok": result["ok"], "status": result["status"]}
        return Data(data=result)
