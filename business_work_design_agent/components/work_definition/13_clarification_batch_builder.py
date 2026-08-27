from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, IntInput, MessageTextInput, Output, SecretStrInput
from lfx.schema import Data
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError


ALLOWED_ANSWER_TYPES = {"text", "single_choice", "single_choice_with_text", "multi_choice", "boolean", "number"}
PRIORITY_ORDER = {"safety": 0, "branch": 1, "contract": 2, "quality": 3}


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


def _parse_now(value: Any) -> datetime:
    text = str(value or "").strip()
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00")) if text else datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _secret(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    return str(getter() if callable(getter) else value or "")


def _get_path(root: Any, path: str) -> Any:
    current = root
    for part in [token for token in re.split(r"\.|\[|\]", path) if token]:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def _is_confirmed(root: dict[str, Any], paths: list[str]) -> bool:
    if not paths:
        return False
    for path in paths:
        value = _get_path(root, path)
        if isinstance(value, dict) and value.get("status") == "confirmed" and "value" in value:
            continue
        if isinstance(value, list) and value:
            statuses = [((item.get("provenance") or {}).get("status")) for item in value if isinstance(item, dict)]
            if statuses and all(status == "confirmed" for status in statuses):
                continue
        return False
    return True


def _question_text(gap: dict[str, Any]) -> str:
    code = str(gap.get("reason_code") or "")
    templates = {
        "GOAL_UNKNOWN": "이 업무가 완료됐다고 판단할 수 있는 최종 결과는 무엇인가요?",
        "TRIGGER_UNKNOWN": "이 업무는 어떤 사건, 요청 또는 주기에 시작되나요?",
        "INPUT_CONTRACT_UNKNOWN": "업무에 반드시 들어오는 입력은 무엇이며 형식은 무엇인가요?",
        "OUTPUT_CONTRACT_UNKNOWN": "업무 결과는 무엇이며 누구 또는 어느 시스템에 전달되나요?",
        "PRIMARY_ACTOR_UNKNOWN": "이 업무의 주 수행자와 최종 책임자는 누구인가요?",
        "WRITE_APPROVAL_UNKNOWN": "데이터 저장·수정·발송 전에 담당자 확인이 필요한가요?",
        "SENSITIVE_REVIEW_UNKNOWN": "민감정보 처리 전후에 누가 확인하고 승인해야 하나요?",
        "STEP_SEQUENCE_UNKNOWN": "업무가 시작된 뒤 완료될 때까지의 단계를 순서대로 설명해 주세요.",
        "BRANCH_CONDITION_UNKNOWN": "어떤 조건에서 처리 경로가 나뉘며 각 경로의 결과는 무엇인가요? condition과 branches(2개 이상)를 가진 JSON object로 답해 주세요. 예: {\"condition\":\"오류가 있는가\",\"branches\":[\"예\",\"아니오\"]}",
        "FAILURE_POLICY_UNKNOWN": "조회·처리·전송이 실패하면 재시도, 담당자 전달, 종료 중 어떻게 처리해야 하나요?",
        "SLA_UNKNOWN": "이 업무의 완료 기한 또는 허용 처리 시간은 얼마인가요?",
        "SUCCESS_CRITERIA_UNKNOWN": "결과의 품질과 개선 효과를 어떤 수치나 기준으로 확인할까요?",
        "SCOPE_CONFLICT": "포함 범위와 제외 범위가 겹칩니다. 어느 범위로 확정할까요?",
    }
    return templates.get(code, str(gap.get("message") or "부족한 업무 정보를 알려 주세요."))


def build_clarification_batch(
    work_value: Any,
    completeness_value: Any,
    candidate_questions_value: Any = None,
    *,
    round_number: Any = 1,
    max_questions: Any = 3,
    expiry_minutes: Any = 60,
    now_utc: Any = "",
) -> dict[str, Any]:
    trace_id = f"trace-{uuid.uuid4()}"
    try:
        work = _named(work_value, "work_definition")
        completeness = _named(completeness_value, "completeness")
        candidate_payload = _payload(candidate_questions_value) if candidate_questions_value is not None else {}
        candidates = candidate_payload.get("questions") if isinstance(candidate_payload.get("questions"), list) else []
        supplied_round = int(round_number)
        question_limit = max(1, min(int(max_questions), 3))
        expiry = max(5, min(int(expiry_minutes), 1440))
        now = _parse_now(now_utc)
    except (TypeError, ValueError, json.JSONDecodeError):
        work, completeness, candidates, supplied_round, question_limit, expiry, now = {}, {}, [], -1, 3, 60, datetime.now(timezone.utc)
    processed = work.get("processed_answer_batches") if isinstance(work.get("processed_answer_batches"), list) else []
    processed_batch_ids = {
        str(item.get("batch_id") or "")
        for item in processed[:100]
        if isinstance(item, dict) and str(item.get("batch_id") or "")
    }
    expected_round = min(len(processed_batch_ids) + 1, 4)
    current_round = expected_round if supplied_round == 0 else supplied_round
    identity_fields = ("work_definition_id", "tenant_id", "owner_id", "session_id", "revision", "channel_mode")
    missing = [field for field in identity_fields if work.get(field) in (None, "")]
    # Round 4 is a non-interactive post-round gate: it may return
    # READY_FOR_REVIEW, but it must never create a fourth human question batch.
    if missing or current_round not in {1, 2, 3, 4}:
        code = "CLARIFICATION_ROUND_LIMIT" if current_round not in {1, 2, 3, 4} else "CLARIFICATION_INPUT_INVALID"
        return {
            "ok": False,
            "status": "BLOCKED",
            "artifact_refs": [],
            "error": {"code": code, "message": "질문 batch 입력 또는 허용 회차가 유효하지 않습니다.", "retryable": False, "details": {"missing_fields": missing, "round_number": current_round}},
            "resume": None,
            "trace_id": trace_id,
        }
    if current_round != expected_round:
        return {
            "ok": False,
            "status": "BLOCKED",
            "artifact_refs": [],
            "error": {
                "code": "CLARIFICATION_ROUND_SEQUENCE_MISMATCH",
                "message": "질문 회차가 처리 완료된 answer batch 이력과 일치하지 않습니다.",
                "retryable": False,
                "details": {
                    "supplied_round": supplied_round,
                    "expected_round": expected_round,
                    "processed_batch_count": len(processed_batch_ids),
                },
            },
            "resume": None,
            "trace_id": trace_id,
        }
    try:
        work_revision = int(work["revision"])
        completeness_revision = int(completeness.get("revision", -1))
    except (TypeError, ValueError):
        work_revision, completeness_revision = -1, -2
    if completeness.get("work_definition_id") != work.get("work_definition_id") or completeness_revision != work_revision or work_revision < 0:
        return {
            "ok": False,
            "status": "BLOCKED",
            "artifact_refs": [],
            "error": {"code": "CLARIFICATION_REVISION_MISMATCH", "message": "완전성 평가와 WorkDefinition revision이 다릅니다.", "retryable": False, "details": {}},
            "resume": None,
            "trace_id": trace_id,
        }

    gaps = completeness.get("blocking_gaps") if isinstance(completeness.get("blocking_gaps"), list) else []
    gaps = sorted((gap for gap in gaps if isinstance(gap, dict)), key=lambda gap: (PRIORITY_ORDER.get(str(gap.get("priority")), 99), str(gap.get("reason_code"))))
    candidate_by_code = {str(item.get("reason_code")): item for item in candidates if isinstance(item, dict) and item.get("reason_code")}
    questions: list[dict[str, Any]] = []
    seen_paths: set[tuple[str, ...]] = set()
    for gap in gaps:
        paths = [str(path)[:300] for path in gap.get("target_paths", []) if str(path).strip()][:10]
        if not paths or _is_confirmed(work, paths):
            continue
        path_key = tuple(sorted(paths))
        if path_key in seen_paths:
            continue
        model_question = candidate_by_code.get(str(gap.get("reason_code")), {})
        text = str(model_question.get("text") or _question_text(gap)).strip()[:1000]
        if not text:
            continue
        answer_type = str(model_question.get("answer_type") or "text")
        if answer_type not in ALLOWED_ANSWER_TYPES:
            answer_type = "text"
        choices = []
        for choice in model_question.get("choices", []) if isinstance(model_question.get("choices"), list) else []:
            safe = str(choice).strip()[:300]
            if safe and safe not in choices:
                choices.append(safe)
        if answer_type in {"single_choice", "single_choice_with_text", "multi_choice"} and not choices:
            answer_type = "text"
        if str(gap.get("reason_code")) == "BRANCH_CONDITION_UNKNOWN":
            answer_type = "text"
            choices = []
            if "condition" not in text or "branches" not in text:
                text = _question_text(gap)
        material = json.dumps({"work_definition_id": work["work_definition_id"], "revision": work_revision, "round": current_round, "reason": gap.get("reason_code"), "paths": paths}, sort_keys=True, ensure_ascii=False)
        questions.append(
            {
                "question_id": f"q-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:16]}",
                "text": text,
                "target_paths": paths,
                "answer_type": answer_type,
                "choices": choices[:20],
                "required": bool(model_question.get("required", True)),
                "reason_code": str(gap.get("reason_code") or "CLARIFICATION_REQUIRED")[:100],
                "priority": str(gap.get("priority") or "quality"),
            }
        )
        seen_paths.add(path_key)
        if len(questions) >= question_limit:
            break

    if not questions:
        return {
            "ok": True,
            "status": "READY_FOR_REVIEW",
            "artifact_refs": [{"kind": "work_definition", "id": work["work_definition_id"], "revision": work_revision}],
            "clarification_batch": None,
            "trace_id": trace_id,
        }

    if current_round == 4:
        return {
            "ok": False,
            "status": "BLOCKED",
            "artifact_refs": [{"kind": "work_definition", "id": work["work_definition_id"], "revision": work_revision}],
            "clarification_batch": None,
            "error": {
                "code": "CLARIFICATION_ROUND_LIMIT",
                "message": "세 번의 보완 회차 후에도 필수 업무 계약이 남아 있어 자동 승인을 차단했습니다.",
                "retryable": False,
                "details": {"remaining_reason_codes": [question["reason_code"] for question in questions]},
            },
            "resume": None,
            "trace_id": trace_id,
        }

    canonical = json.dumps(questions, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    batch_material = f"{work['work_definition_id']}|{work['revision']}|{current_round}|{canonical}"
    batch_id = f"qb-{hashlib.sha256(batch_material.encode('utf-8')).hexdigest()[:24]}"
    batch = {
        "schema_version": "clarification-question-batch/v1",
        "batch_id": batch_id,
        "work_definition_id": work["work_definition_id"],
        "tenant_id": work["tenant_id"],
        "owner_id": work["owner_id"],
        "session_id": work["session_id"],
        "channel_mode": work["channel_mode"],
        "revision": work_revision,
        "round_number": current_round,
        "created_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(minutes=expiry)).isoformat().replace("+00:00", "Z"),
        "status": "WAITING_ANSWER",
        "questions": questions,
    }
    return {
        "ok": True,
        "status": "WAITING_ANSWER",
        "artifact_refs": [{"kind": "clarification_batch", "id": batch_id}],
        "clarification_batch": batch,
        "trace_id": trace_id,
    }


def persist_clarification_batch(
    result: dict[str, Any],
    *,
    mongodb_uri: Any,
    mongo_database: Any,
    collection_name: Any = "clarification_batches",
    timeout_ms: Any = 5000,
    client_factory: Any = MongoClient,
) -> dict[str, Any]:
    """Persist one immutable question contract; Answer Form may add answers later."""
    if not result.get("ok") or result.get("clarification_batch") is None:
        return result
    uri = _secret(mongodb_uri)
    database_name = str(mongo_database or "").strip()[:200]
    collection = str(collection_name or "clarification_batches").strip()[:200]
    try:
        timeout = max(1000, min(int(timeout_ms), 30_000))
    except (TypeError, ValueError):
        timeout = 5000
    if not uri or not database_name or not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", collection):
        failed = copy.deepcopy(result)
        failed.update({"ok": False, "status": "BLOCKED", "error": {"code": "CLARIFICATION_STORE_CONFIG_MISSING", "message": "질문 batch를 저장할 production MongoDB 설정이 필요합니다.", "retryable": False, "details": {}}, "resume": None})
        return failed
    batch = copy.deepcopy(result["clarification_batch"])
    contract_material = json.dumps({key: batch.get(key) for key in ("batch_id", "work_definition_id", "tenant_id", "owner_id", "session_id", "channel_mode", "revision", "round_number", "questions")}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    contract_hash = hashlib.sha256(contract_material.encode("utf-8")).hexdigest()
    document = copy.deepcopy(batch)
    document["_id"] = "clarification-batch:" + hashlib.sha256(f"{batch['tenant_id']}|{batch['batch_id']}".encode("utf-8")).hexdigest()
    document["contract_sha256"] = contract_hash
    document["created_at"] = _parse_now(batch["created_at"])
    document["expires_at"] = _parse_now(batch["expires_at"])
    document["answers"] = None
    document["answer_idempotency_key"] = None
    document["answered_at"] = None
    client = None
    try:
        client = client_factory(uri, serverSelectionTimeoutMS=timeout, connectTimeoutMS=timeout, socketTimeoutMS=timeout, retryWrites=True)
        client.admin.command("ping")
        batches = client[database_name][collection]
        existing = batches.find_one({"_id": document["_id"]})
        if existing is not None:
            if str(existing.get("contract_sha256")) != contract_hash:
                failed = copy.deepcopy(result)
                failed.update({"ok": False, "status": "BLOCKED", "error": {"code": "CLARIFICATION_BATCH_CONFLICT", "message": "같은 batch ID에 다른 질문 계약이 이미 저장되어 있습니다.", "retryable": False, "details": {}}, "resume": None})
                return failed
            persisted = copy.deepcopy(result)
            persisted["store_result"] = {"persisted": True, "idempotent_replay": True, "contract_sha256": contract_hash}
            return persisted
        try:
            batches.insert_one(document)
        except DuplicateKeyError:
            existing = batches.find_one({"_id": document["_id"]})
            if existing is None or str(existing.get("contract_sha256")) != contract_hash:
                raise
        persisted = copy.deepcopy(result)
        persisted["store_result"] = {"persisted": True, "idempotent_replay": False, "contract_sha256": contract_hash}
        return persisted
    except PyMongoError as exc:
        failed = copy.deepcopy(result)
        failed.update({"ok": False, "status": "BLOCKED", "error": {"code": "CLARIFICATION_STORE_FAILED", "message": "질문 batch를 MongoDB에 저장하지 못했습니다.", "retryable": True, "details": {"exception_type": type(exc).__name__}}, "resume": None})
        return failed
    finally:
        if client is not None:
            client.close()


class ClarificationBatchBuilderComponent(Component):
    display_name = "13 재질문 Batch 생성"
    description = "완전성 gap과 질문 후보를 검증해 confirmed 항목을 제외한 최대 3개 질문 batch를 만들며, 세 회차 뒤에는 비대화형 final gate로 차단합니다."
    icon = "MessageCircleQuestion"
    name = "ClarificationBatchBuilder"

    inputs = [
        DataInput(name="work_definition", display_name="WorkDefinition", input_types=["Data", "JSON"], required=True),
        DataInput(name="completeness", display_name="완전성 평가", input_types=["Data", "JSON"], required=True),
        DataInput(name="candidate_questions", display_name="모델 질문 후보", input_types=["Data", "Message", "JSON"], required=False),
        IntInput(name="round_number", display_name="질문 회차(0=이력에서 자동 계산)", value=0),
        IntInput(name="max_questions", display_name="회차당 최대 질문", value=3, advanced=True),
        IntInput(name="expiry_minutes", display_name="응답 만료(분)", value=60, advanced=True),
        MessageTextInput(name="now_utc", display_name="기준 시각(ISO-8601)", value="", advanced=True),
        SecretStrInput(name="mongodb_uri", display_name="MongoDB URI", required=True),
        MessageTextInput(name="mongo_database", display_name="MongoDB Database", required=True),
        MessageTextInput(name="collection_name", display_name="질문 Batch Collection", value="clarification_batches", advanced=True),
        IntInput(name="timeout_ms", display_name="MongoDB Timeout(ms)", value=5000, advanced=True),
    ]
    outputs = [Output(name="clarification_batch", display_name="재질문 Batch", method="build_batch", types=["Data"])]

    def build_batch(self) -> Data:
        built = build_clarification_batch(
            getattr(self, "work_definition", None),
            getattr(self, "completeness", None),
            getattr(self, "candidate_questions", None),
            round_number=getattr(self, "round_number", 1),
            max_questions=getattr(self, "max_questions", 3),
            expiry_minutes=getattr(self, "expiry_minutes", 60),
            now_utc=getattr(self, "now_utc", ""),
        )
        result = persist_clarification_batch(
            built,
            mongodb_uri=getattr(self, "mongodb_uri", ""),
            mongo_database=getattr(self, "mongo_database", ""),
            collection_name=getattr(self, "collection_name", "clarification_batches"),
            timeout_ms=getattr(self, "timeout_ms", 5000),
            client_factory=MongoClient,
        )
        self.status = {"ok": result["ok"], "status": result["status"], "question_count": len((result.get("clarification_batch") or {}).get("questions", []))}
        return Data(data=result)
