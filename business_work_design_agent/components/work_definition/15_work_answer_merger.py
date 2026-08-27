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


FACT_STATUSES = {"confirmed", "inferred", "unknown", "conflicting"}
LIST_FIELDS = {
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
}


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return copy.deepcopy(value)
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return copy.deepcopy(data)
    text = getattr(value, "text", value if isinstance(value, str) else "")
    if isinstance(text, str) and text.strip():
        return json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE))
    return {}


def _named(value: Any, *keys: str) -> dict[str, Any]:
    payload = _payload(value)
    for key in keys:
        nested = payload.get(key)
        if isinstance(nested, dict):
            return copy.deepcopy(nested)
    return payload


def _failure(code: str, message: str, trace_id: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"ok": False, "status": "BLOCKED", "artifact_refs": [], "error": {"code": code, "message": message, "retryable": False, "details": details or {}}, "resume": None, "trace_id": trace_id}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _same(left: Any, right: Any) -> bool:
    return _canonical(left) == _canonical(right)


def _path_tokens(path: str) -> list[str | int]:
    tokens: list[str | int] = []
    for key, index in re.findall(r"([A-Za-z_][A-Za-z0-9_-]*)|\[(\d+)\]", path):
        tokens.append(int(index) if index else key)
    return tokens


def _get_path(root: Any, tokens: list[str | int]) -> Any:
    current = root
    for token in tokens:
        if isinstance(token, int) and isinstance(current, list) and token < len(current):
            current = current[token]
        elif isinstance(token, str) and isinstance(current, dict):
            current = current.get(token)
        else:
            return None
    return current


def _set_path(root: dict[str, Any], tokens: list[str | int], value: Any) -> bool:
    if not tokens or not isinstance(tokens[0], str):
        return False
    current: Any = root
    for position, token in enumerate(tokens[:-1]):
        next_token = tokens[position + 1]
        if isinstance(token, str):
            if not isinstance(current, dict):
                return False
            if token not in current or current[token] is None:
                current[token] = [] if isinstance(next_token, int) else {}
            current = current[token]
        else:
            if not isinstance(current, list) or token < 0 or token >= len(current):
                return False
            current = current[token]
    last = tokens[-1]
    if isinstance(last, str) and isinstance(current, dict):
        current[last] = value
        return True
    if isinstance(last, int) and isinstance(current, list) and 0 <= last < len(current):
        current[last] = value
        return True
    return False


def _evidence(existing: Any, turn_id: str) -> list[str]:
    current = existing if isinstance(existing, list) else []
    result: list[str] = []
    for item in current + [turn_id]:
        text = str(item or "").strip()[:200]
        if text and text not in result:
            result.append(text)
    return result[:100]


def _merge_fact(current: Any, incoming: Any, *, revision: int, evidence_turn_id: str, resolve_conflict: bool) -> tuple[dict[str, Any], bool]:
    fact = copy.deepcopy(current) if isinstance(current, dict) and current.get("status") in FACT_STATUSES and "value" in current else {
        "value": copy.deepcopy(current) if current not in (None, "") else None,
        "status": "unknown" if current in (None, "") else "inferred",
        "evidence_turn_ids": [],
        "confidence": 0.0 if current in (None, "") else 0.7,
        "last_updated_revision": max(0, revision - 1),
    }
    old_value = copy.deepcopy(fact.get("value"))
    evidence = _evidence(fact.get("evidence_turn_ids"), evidence_turn_id)
    if fact.get("status") in {"confirmed", "conflicting"} and not _same(old_value, incoming) and not resolve_conflict:
        candidates = copy.deepcopy(fact.get("conflicting_values", [])) if isinstance(fact.get("conflicting_values"), list) else []
        for candidate in (old_value, copy.deepcopy(incoming)):
            if not any(_same(candidate, known) for known in candidates):
                candidates.append(candidate)
        return {
            "value": old_value,
            "status": "conflicting",
            "conflicting_values": candidates[:20],
            "evidence_turn_ids": evidence,
            "confidence": 0.0,
            "last_updated_revision": revision,
        }, True
    return {
        "value": copy.deepcopy(incoming),
        "status": "confirmed",
        "evidence_turn_ids": evidence,
        "confidence": 1.0,
        "last_updated_revision": revision,
    }, False


def _confirmed_list(value: Any, *, path: str, revision: int, evidence_turn_id: str) -> list[dict[str, Any]]:
    raw_items = value if isinstance(value, list) else [value]
    result: list[dict[str, Any]] = []
    for index, item in enumerate(raw_items[:500]):
        record = copy.deepcopy(item) if isinstance(item, dict) else {"value": copy.deepcopy(item)}
        identity = record.get("id") or record.get("name") or record.get("label") or record.get("title") or record.get("value") or index
        identity_material = f"{path}|{identity}"
        record.setdefault("id", f"{path.rstrip('s') or 'item'}-{hashlib.sha256(identity_material.encode('utf-8')).hexdigest()[:16]}")
        previous = record.get("provenance") if isinstance(record.get("provenance"), dict) else {}
        record["provenance"] = {
            "status": "confirmed",
            "evidence_turn_ids": _evidence(previous.get("evidence_turn_ids"), evidence_turn_id),
            "confidence": 1.0,
            "last_updated_revision": revision,
        }
        result.append(record)
    return result


def _merge_decision(
    current: Any,
    incoming: Any,
    *,
    revision: int,
    evidence_turn_id: str,
    resolve_conflict: bool,
) -> tuple[dict[str, Any], bool]:
    if isinstance(incoming, str):
        if len(incoming) > 20_000:
            raise ValueError("decision answer is too large")
        try:
            incoming = json.loads(incoming)
        except json.JSONDecodeError as exc:
            raise ValueError("decision answer must be a JSON object") from exc
    if not isinstance(incoming, dict):
        raise ValueError("decision answer must be a JSON object")
    condition = str(incoming.get("condition") or "").strip()[:2_000]
    branches = incoming.get("branches") if isinstance(incoming.get("branches"), list) else incoming.get("outcomes")
    if not condition or not isinstance(branches, list) or not 2 <= len(branches) <= 20:
        raise ValueError("decision answer requires condition and 2 to 20 branches")
    normalized_branches: list[dict[str, Any]] = []
    for index, branch in enumerate(branches):
        record = copy.deepcopy(branch) if isinstance(branch, dict) else {"label": str(branch)}
        label = str(record.get("label") or record.get("name") or record.get("value") or "").strip()[:500]
        if not label:
            raise ValueError("every decision branch requires a label")
        record["label"] = label
        record.setdefault("branch_id", f"branch-{index + 1}")
        normalized_branches.append(record)
    base = copy.deepcopy(current) if isinstance(current, dict) else {}
    previous_contract = {"condition": base.get("condition"), "branches": base.get("branches") or base.get("outcomes")}
    incoming_contract = {"condition": condition, "branches": normalized_branches}
    previous_provenance = base.get("provenance") if isinstance(base.get("provenance"), dict) else {}
    if (
        previous_provenance.get("status") == "confirmed"
        and not _same(previous_contract, incoming_contract)
        and not resolve_conflict
    ):
        base["provenance"] = {
            "status": "conflicting",
            "evidence_turn_ids": _evidence(previous_provenance.get("evidence_turn_ids"), evidence_turn_id),
            "confidence": 0.0,
            "last_updated_revision": revision,
        }
        return base, True
    base.pop("outcomes", None)
    base["condition"] = condition
    base["branches"] = normalized_branches
    base["provenance"] = {
        "status": "confirmed",
        "evidence_turn_ids": _evidence(previous_provenance.get("evidence_turn_ids"), evidence_turn_id),
        "confidence": 1.0,
        "last_updated_revision": revision,
    }
    return base, False


def merge_work_answers(work_value: Any, submission_value: Any) -> dict[str, Any]:
    trace_id = f"trace-{uuid.uuid4()}"
    try:
        work = _named(work_value, "work_definition")
        submission = _named(submission_value, "answer_submission")
    except (TypeError, ValueError, json.JSONDecodeError):
        return _failure("ANSWER_MERGE_INPUT_INVALID", "WorkDefinition 또는 검증된 답변을 해석할 수 없습니다.", trace_id)
    required = ("work_definition_id", "tenant_id", "session_id", "revision", "channel_mode")
    missing = [key for key in required if work.get(key) in (None, "")]
    if missing:
        return _failure("WORK_DEFINITION_SCHEMA_INVALID", "병합할 WorkDefinition 식별자가 없습니다.", trace_id, {"fields": missing})
    if any(str(work.get(key)) != str(submission.get(key)) for key in ("work_definition_id", "tenant_id", "session_id", "channel_mode")):
        return _failure("ANSWER_MERGE_IDENTITY_MISMATCH", "답변과 WorkDefinition의 tenant/session/channel이 다릅니다.", trace_id)
    try:
        current_revision = int(work.get("revision"))
    except (TypeError, ValueError):
        return _failure("ANSWER_REVISION_INVALID", "WorkDefinition revision을 확인할 수 없습니다.", trace_id)

    batch_id = str(submission.get("batch_id") or "")
    idempotency_key = str(submission.get("idempotency_key") or "")
    payload_hash = str(submission.get("payload_sha256") or "")
    if not batch_id or not idempotency_key or not payload_hash:
        return _failure("ANSWER_RECEIPT_INVALID", "batch와 idempotency receipt 정보가 없습니다.", trace_id)
    processed = work.get("processed_answer_batches") if isinstance(work.get("processed_answer_batches"), list) else []
    same_batch = [item for item in processed if isinstance(item, dict) and item.get("batch_id") == batch_id]
    if same_batch:
        receipt = same_batch[-1]
        if receipt.get("idempotency_key") == idempotency_key and receipt.get("payload_sha256") == payload_hash:
            return {
                "ok": True,
                "status": str(work.get("status") or "EXTRACTING"),
                "artifact_refs": [{"kind": "work_definition", "id": work["work_definition_id"], "revision": current_revision}],
                "work_definition": work,
                "merge_result": {"idempotent_replay": True, "conflicts": copy.deepcopy(receipt.get("conflicts", [])), "resulting_revision": current_revision},
                "trace_id": trace_id,
            }
        return _failure("ANSWER_BATCH_ALREADY_PROCESSED", "같은 batch가 다른 idempotency key 또는 내용으로 이미 처리되었습니다.", trace_id)
    try:
        expected_revision = int(submission.get("expected_revision"))
    except (TypeError, ValueError):
        return _failure("ANSWER_REVISION_INVALID", "답변 revision을 확인할 수 없습니다.", trace_id)
    if expected_revision != current_revision:
        return _failure("REVISION_CONFLICT", "답변이 최신 WorkDefinition revision을 대상으로 하지 않습니다.", trace_id, {"expected_revision": expected_revision, "current_revision": current_revision})

    answers = submission.get("answers")
    if not isinstance(answers, list) or not answers:
        return _failure("ANSWER_LIST_INVALID", "병합할 답변이 없습니다.", trace_id)
    updated = copy.deepcopy(work)
    new_revision = current_revision + 1
    conflicts: list[dict[str, Any]] = []
    changed_paths: list[str] = []
    for answer in answers[:100]:
        if not isinstance(answer, dict):
            return _failure("ANSWER_ITEM_INVALID", "답변 항목 형식이 올바르지 않습니다.", trace_id)
        paths = answer.get("target_paths") if isinstance(answer.get("target_paths"), list) else []
        if not paths:
            return _failure("ANSWER_TARGET_PATH_MISSING", "답변이 갱신할 target path가 없습니다.", trace_id, {"question_id": answer.get("question_id")})
        for raw_path in paths[:10]:
            path = str(raw_path)
            tokens = _path_tokens(path)
            if not tokens or tokens[0] in {"work_definition_id", "tenant_id", "owner_id", "session_id", "revision", "channel_mode", "approved_hash", "preview_hash", "source_requests"}:
                return _failure("ANSWER_TARGET_PATH_FORBIDDEN", "답변이 변경할 수 없는 경로입니다.", trace_id, {"path": path})
            current = _get_path(updated, tokens)
            if len(tokens) == 1 and str(tokens[0]) in LIST_FIELDS:
                incoming_list = _confirmed_list(answer.get("value"), path=path, revision=new_revision, evidence_turn_id=str(answer.get("evidence_turn_id") or ""))
                existing_confirmed = isinstance(current, list) and current and all(isinstance(item, dict) and ((item.get("provenance") or {}).get("status") == "confirmed") for item in current)
                if existing_confirmed and not _same(current, incoming_list) and not bool(answer.get("resolve_conflict")):
                    conflicts.append({"path": path, "question_id": answer.get("question_id"), "reason": "CONFIRMED_VALUE_CHANGED"})
                    unresolved = updated.get("unresolved") if isinstance(updated.get("unresolved"), list) else []
                    conflict_material = f"{batch_id}|{path}"
                    unresolved.append({"id": f"conflict-{hashlib.sha256(conflict_material.encode('utf-8')).hexdigest()[:16]}", "path": path, "existing_value": copy.deepcopy(current), "incoming_value": incoming_list, "provenance": {"status": "conflicting", "evidence_turn_ids": [str(answer.get("evidence_turn_id") or "")], "confidence": 0.0, "last_updated_revision": new_revision}})
                    updated["unresolved"] = unresolved
                    continue
                merged_value = incoming_list
                conflict = False
            elif len(tokens) == 2 and tokens[0] == "decisions" and isinstance(tokens[1], int):
                try:
                    merged_value, conflict = _merge_decision(
                        current,
                        answer.get("value"),
                        revision=new_revision,
                        evidence_turn_id=str(answer.get("evidence_turn_id") or ""),
                        resolve_conflict=bool(answer.get("resolve_conflict")),
                    )
                except ValueError as exc:
                    return _failure(
                        "ANSWER_DECISION_INVALID",
                        "분기 답변은 condition과 두 개 이상의 branches를 가진 JSON object여야 합니다.",
                        trace_id,
                        {"path": path, "reason": str(exc)},
                    )
            else:
                merged_value, conflict = _merge_fact(current, answer.get("value"), revision=new_revision, evidence_turn_id=str(answer.get("evidence_turn_id") or ""), resolve_conflict=bool(answer.get("resolve_conflict")))
            if not _set_path(updated, tokens, merged_value):
                return _failure("ANSWER_TARGET_PATH_INVALID", "답변 target path를 WorkDefinition에 적용할 수 없습니다.", trace_id, {"path": path})
            changed_paths.append(path)
            if conflict:
                conflicts.append({"path": path, "question_id": answer.get("question_id"), "reason": "CONFIRMED_VALUE_CHANGED"})

    updated["revision"] = new_revision
    updated["approved_hash"] = None
    updated["status"] = "NEEDS_CLARIFICATION" if conflicts else "EXTRACTING"
    receipt = {
        "batch_id": batch_id,
        "submission_id": submission.get("submission_id"),
        "idempotency_key": idempotency_key,
        "payload_sha256": payload_hash,
        "resulting_revision": new_revision,
        "changed_paths": sorted(set(changed_paths)),
        "conflicts": copy.deepcopy(conflicts),
    }
    processed.append(receipt)
    updated["processed_answer_batches"] = processed[-100:]
    return {
        "ok": True,
        "status": updated["status"],
        "artifact_refs": [{"kind": "work_definition", "id": updated["work_definition_id"], "revision": new_revision}],
        "work_definition": updated,
        "merge_result": {"idempotent_replay": False, "conflicts": conflicts, "changed_paths": sorted(set(changed_paths)), "resulting_revision": new_revision},
        "trace_id": trace_id,
    }


class WorkAnswerMergerComponent(Component):
    display_name = "15 업무 답변 병합"
    description = "검증된 답변을 provenance와 confirmed 충돌을 보존하며 한 revision으로 idempotent 병합합니다."
    icon = "GitMerge"
    name = "WorkAnswerMerger"

    inputs = [
        DataInput(name="work_definition", display_name="WorkDefinition", input_types=["Data", "JSON"], required=True),
        DataInput(name="answer_submission", display_name="검증된 답변", input_types=["Data", "JSON"], required=True),
    ]
    outputs = [Output(name="merged_work_definition", display_name="병합 WorkDefinition", method="build_merged_definition", types=["Data"])]

    def build_merged_definition(self) -> Data:
        result = merge_work_answers(getattr(self, "work_definition", None), getattr(self, "answer_submission", None))
        self.status = {"ok": result["ok"], "status": result["status"], "revision": (result.get("work_definition") or {}).get("revision")}
        return Data(data=result)
