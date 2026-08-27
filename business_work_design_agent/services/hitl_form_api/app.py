from __future__ import annotations

"""FastAPI backend for structured clarification answers and safe HITL resume."""

import copy
import hashlib
import hmac
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field


ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
ALLOWED_ANSWER_TYPES = {"text", "single_choice", "single_choice_with_text", "multi_choice", "boolean", "number"}
MAX_ANSWER_BYTES = 256 * 1024
MAX_ANSWER_VALUE_BYTES = 64 * 1024
MAX_FREE_TEXT_CHARS = 16_000
ANSWER_RETENTION = timedelta(days=7)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_datetime(value: Any) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _request_hash(work_id: str, batch_id: str, expected_revision: int, answers: list[dict[str, Any]]) -> str:
    return hashlib.sha256(_canonical({"work_definition_id": work_id, "batch_id": batch_id, "expected_revision": expected_revision, "answers": answers}).encode("utf-8")).hexdigest()


def _safe_langflow_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.strip())
    host = (parsed.hostname or "").lower()
    loopback = host in {"localhost", "127.0.0.1", "::1"}
    if not host or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError("LANGFLOW_BASE_URL must be an absolute URL without credentials, query, or fragment")
    if parsed.scheme not in ({"http", "https"} if loopback else {"https"}):
        raise RuntimeError("LANGFLOW_BASE_URL must use HTTPS outside loopback development")
    return value.strip().rstrip("/")


@dataclass(frozen=True)
class Settings:
    environment: str
    storage_mode: str
    mongodb_uri: str
    mongodb_database: str
    collection_prefix: str
    bearer_token: str
    allow_insecure_local: bool
    langflow_base_url: str
    langflow_api_key: str
    langflow_f10_flow_id: str
    resume_enabled: bool

    @classmethod
    def from_env(cls) -> "Settings":
        settings = cls(
            environment=os.getenv("APP_ENV", "development").strip().lower(),
            storage_mode=os.getenv("HITL_STORAGE_MODE", "mongodb").strip().lower(),
            mongodb_uri=os.getenv("MONGODB_URI", "").strip(),
            mongodb_database=os.getenv("MONGODB_DATABASE", "business_work_design").strip(),
            collection_prefix=os.getenv("MONGODB_COLLECTION_PREFIX", "").strip(),
            bearer_token=os.getenv("HITL_API_BEARER_TOKEN", "").strip(),
            allow_insecure_local=_truthy(os.getenv("ALLOW_INSECURE_LOCAL")),
            langflow_base_url=os.getenv("LANGFLOW_BASE_URL", "http://localhost:7860").strip(),
            langflow_api_key=os.getenv("LANGFLOW_API_KEY", "").strip(),
            langflow_f10_flow_id=os.getenv("LANGFLOW_F10_FLOW_ID", "").strip(),
            resume_enabled=_truthy(os.getenv("LANGFLOW_RESUME_ENABLED", "true")),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        _safe_langflow_url(self.langflow_base_url)
        if self.collection_prefix:
            raise RuntimeError(
                "MONGODB_COLLECTION_PREFIX must be empty: Langflow standalone components and the HITL API share canonical collection names"
            )
        if self.storage_mode not in {"mongodb", "memory"}:
            raise RuntimeError("HITL_STORAGE_MODE must be mongodb or memory")
        production = self.environment in {"production", "prod"}
        if production and self.storage_mode != "mongodb":
            raise RuntimeError("production HITL API requires MongoDB storage")
        if self.storage_mode == "mongodb" and (not self.mongodb_uri or not self.mongodb_database):
            raise RuntimeError("MongoDB configuration is required for HITL_STORAGE_MODE=mongodb")
        if production and (not self.bearer_token or self.allow_insecure_local):
            raise RuntimeError("production HITL API requires bearer authentication")
        if not self.bearer_token and not self.allow_insecure_local:
            raise RuntimeError("set HITL_API_BEARER_TOKEN or explicitly enable local insecure mode")
        if production and (not self.resume_enabled or not self.langflow_api_key or not self.langflow_f10_flow_id):
            raise RuntimeError("production HITL API requires authenticated resume and LANGFLOW_F10_FLOW_ID")


class BatchRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clarification_batch: dict[str, Any]
    workflow_job_id: str
    workflow_request_id: str


class AnswerItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1, max_length=200)
    value: Any
    resolve_conflict: bool = False
    evidence_turn_id: str | None = Field(default=None, max_length=200)


class AnswerSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    answers: list[AnswerItem] = Field(min_length=1, max_length=3)


class HitlRepository(Protocol):
    def register_batch(self, document: dict[str, Any], idempotency_key: str, request_hash: str) -> tuple[dict[str, Any], bool]: ...

    def get_batch(self, tenant_id: str, work_id: str, batch_id: str) -> dict[str, Any] | None: ...

    def submit_answers(self, tenant_id: str, work_id: str, batch_id: str, expected_revision: int, answers: list[dict[str, Any]], actor_id: str, idempotency_key: str, request_hash: str) -> tuple[dict[str, Any], bool]: ...

    def mark_resumed(self, tenant_id: str, work_id: str, batch_id: str, submission_id: str, resume_result: dict[str, Any]) -> None: ...

    def get_work_definition(self, tenant_id: str, work_id: str) -> dict[str, Any] | None: ...

    def health(self) -> dict[str, Any]: ...

    def close(self) -> None: ...


def _public_batch(document: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(document)
    for key in (
        "_id",
        "workflow_job_id",
        "workflow_request_id",
        "workflow_pending_verification",
        "submission_request_hash",
        "registration_request_hash",
        "submission_idempotency_key",
        "registration_idempotency_key",
        "contract_sha256",
    ):
        result.pop(key, None)
    if isinstance(result.get("resume_result"), dict):
        result["resume_result"] = {
            key: copy.deepcopy(value)
            for key, value in result["resume_result"].items()
            if key not in {"job_id", "request_id"}
        }
    return result


def _public_work_definition(
    document: dict[str, Any] | None,
    runtime_state: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not document:
        return None
    result = copy.deepcopy(document)
    for key in ("_id", "mutation_receipts", "pending_action"):
        result.pop(key, None)
    public_runtime = copy.deepcopy(runtime_state) if isinstance(runtime_state, dict) else None
    if public_runtime:
        for key in ("_id", "mutation_receipts"):
            public_runtime.pop(key, None)
    if public_runtime and int(public_runtime.get("semantic_revision", -1)) == int(result.get("revision", -2)):
        result["effective_status"] = str(public_runtime.get("runtime_status") or result.get("status") or "")
        result["runtime_state"] = public_runtime
    else:
        result["effective_status"] = str(result.get("status") or "")
    return result


def _validate_batch(batch: dict[str, Any], job_id: str, request_id: str) -> dict[str, Any]:
    document = copy.deepcopy(batch)
    required = ("batch_id", "work_definition_id", "tenant_id", "session_id", "channel_mode", "revision", "expires_at", "status", "questions")
    missing = [key for key in required if document.get(key) in (None, "")]
    if missing:
        raise ValueError("BATCH_INVALID:" + ",".join(missing))
    for key in ("batch_id", "work_definition_id", "tenant_id", "session_id"):
        if not ID_PATTERN.fullmatch(str(document[key])):
            raise ValueError("BATCH_ID_INVALID")
    if document.get("channel_mode") != "native_hitl" or document.get("status") != "WAITING_ANSWER":
        raise ValueError("BATCH_STATE_INVALID")
    questions = document.get("questions")
    if not isinstance(questions, list) or not 1 <= len(questions) <= 3:
        raise ValueError("BATCH_QUESTION_COUNT_INVALID")
    ids: set[str] = set()
    for question in questions:
        if not isinstance(question, dict) or not ID_PATTERN.fullmatch(str(question.get("question_id") or "")):
            raise ValueError("BATCH_QUESTION_INVALID")
        question_id = str(question["question_id"])
        if question_id in ids or question.get("answer_type") not in ALLOWED_ANSWER_TYPES:
            raise ValueError("BATCH_QUESTION_INVALID")
        answer_type = str(question.get("answer_type"))
        raw_choices = question.get("choices", [])
        if not isinstance(raw_choices, list):
            raise ValueError("BATCH_QUESTION_INVALID")
        choices = [str(item).strip() for item in raw_choices]
        if any(not item or len(item) > 300 for item in choices) or len(choices) != len(set(choices)) or len(choices) > 20:
            raise ValueError("BATCH_QUESTION_INVALID")
        if answer_type in {"single_choice", "single_choice_with_text", "multi_choice"} and not choices:
            raise ValueError("BATCH_QUESTION_INVALID")
        if answer_type in {"text", "boolean", "number"} and choices:
            raise ValueError("BATCH_QUESTION_INVALID")
        ids.add(question_id)
    answer_deadline = _parse_datetime(document["expires_at"])
    if answer_deadline <= _now():
        raise ValueError("BATCH_EXPIRED")
    if not ID_PATTERN.fullmatch(job_id) or not request_id or len(request_id) > 500:
        raise ValueError("WORKFLOW_REFERENCE_INVALID")
    document["revision"] = int(document["revision"])
    # `answer_deadline_at` is immutable business semantics. `expires_at` is the
    # MongoDB TTL field and is moved to a retention horizon after a valid answer
    # has been accepted, so an answer cannot disappear while Langflow resumes.
    document["answer_deadline_at"] = answer_deadline.isoformat()
    document["expires_at"] = answer_deadline.isoformat()
    document["workflow_job_id"] = job_id
    document["workflow_request_id"] = request_id
    document["registered_at"] = _now().isoformat()
    return document


def _normalize_answer_value(question: dict[str, Any], value: Any) -> Any:
    answer_type = str(question.get("answer_type") or "text")
    choices = [str(item) for item in question.get("choices", []) if isinstance(item, str)]
    required = bool(question.get("required", True))
    if value in (None, "", []):
        if required:
            raise ValueError("ANSWER_REQUIRED_VALUE_MISSING")
        return None
    if answer_type == "text":
        if not isinstance(value, str) or len(value) > MAX_FREE_TEXT_CHARS:
            raise ValueError("ANSWER_VALUE_TYPE_INVALID")
        normalized: Any = value
    elif answer_type == "single_choice":
        if not isinstance(value, str) or value not in choices:
            raise ValueError("ANSWER_CHOICE_INVALID")
        normalized = value
    elif answer_type == "single_choice_with_text":
        if isinstance(value, str) and value in choices:
            normalized = {"choice": value, "text": ""}
        elif isinstance(value, dict) and set(value) <= {"choice", "text"}:
            choice = value.get("choice")
            text = value.get("text", "")
            if not isinstance(choice, str) or not isinstance(text, str) or len(text) > MAX_FREE_TEXT_CHARS:
                raise ValueError("ANSWER_VALUE_TYPE_INVALID")
            if choice == "__other__":
                if not text.strip():
                    raise ValueError("ANSWER_REQUIRED_VALUE_MISSING")
            elif choice not in choices:
                raise ValueError("ANSWER_CHOICE_INVALID")
            normalized = {"choice": choice, "text": text}
        else:
            raise ValueError("ANSWER_VALUE_TYPE_INVALID")
    elif answer_type == "multi_choice":
        if not isinstance(value, list) or not value or any(not isinstance(item, str) or item not in choices for item in value):
            raise ValueError("ANSWER_CHOICE_INVALID")
        normalized = list(dict.fromkeys(value))
    elif answer_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError("ANSWER_VALUE_TYPE_INVALID")
        normalized = value
    elif answer_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or abs(float(value)) > 1e15:
            raise ValueError("ANSWER_VALUE_TYPE_INVALID")
        normalized = value
    else:
        raise ValueError("ANSWER_VALUE_TYPE_INVALID")
    if len(_canonical(normalized).encode("utf-8")) > MAX_ANSWER_VALUE_BYTES:
        raise ValueError("ANSWER_VALUE_TOO_LARGE")
    return normalized


def _normalize_answers(batch: dict[str, Any], answers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    questions = {str(item["question_id"]): item for item in batch.get("questions", []) if isinstance(item, dict)}
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for answer in answers:
        question_id = str(answer.get("question_id") or "")
        if question_id not in questions or question_id in seen:
            raise ValueError("ANSWER_QUESTION_INVALID")
        value = _normalize_answer_value(questions[question_id], copy.deepcopy(answer.get("value")))
        normalized.append(
            {
                "question_id": question_id,
                "value": value,
                "target_paths": copy.deepcopy(questions[question_id].get("target_paths", [])),
                "reason_code": questions[question_id].get("reason_code"),
                "resolve_conflict": bool(answer.get("resolve_conflict", False)),
                "evidence_turn_id": str(answer.get("evidence_turn_id") or f"answer-{question_id}")[:200],
            }
        )
        seen.add(question_id)
    missing = [qid for qid, question in questions.items() if question.get("required", True) and qid not in seen]
    if missing:
        raise ValueError("ANSWER_REQUIRED_QUESTIONS_MISSING")
    return normalized


class MemoryHitlRepository:
    def __init__(self) -> None:
        self._batches: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._registration_keys: dict[tuple[str, str], tuple[str, tuple[str, str, str]]] = {}
        self._submission_keys: dict[tuple[str, str], tuple[str, tuple[str, str, str]]] = {}
        self._work: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = RLock()

    def register_batch(self, document: dict[str, Any], idempotency_key: str, request_hash: str) -> tuple[dict[str, Any], bool]:
        with self._lock:
            idem = (str(document["tenant_id"]), idempotency_key)
            previous = self._registration_keys.get(idem)
            if previous:
                if previous[0] != request_hash:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return copy.deepcopy(self._batches[previous[1]]), False
            key = (str(document["tenant_id"]), str(document["work_definition_id"]), str(document["batch_id"]))
            existing = self._batches.get(key)
            if existing and _canonical(existing) != _canonical(document):
                raise ValueError("BATCH_CONFLICT")
            self._batches[key] = copy.deepcopy(existing or document)
            self._registration_keys[idem] = (request_hash, key)
            return copy.deepcopy(self._batches[key]), existing is None

    def get_batch(self, tenant_id: str, work_id: str, batch_id: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._batches.get((tenant_id, work_id, batch_id))
            return copy.deepcopy(value) if value else None

    def submit_answers(self, tenant_id: str, work_id: str, batch_id: str, expected_revision: int, answers: list[dict[str, Any]], actor_id: str, idempotency_key: str, request_hash: str) -> tuple[dict[str, Any], bool]:
        with self._lock:
            key = (tenant_id, work_id, batch_id)
            document = self._batches.get(key)
            if not document:
                raise ValueError("BATCH_NOT_FOUND")
            idem = (tenant_id, idempotency_key)
            previous = self._submission_keys.get(idem)
            if previous:
                if previous[0] != request_hash or previous[1] != key:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return copy.deepcopy(document), False
            if int(document.get("revision", -1)) != expected_revision:
                raise ValueError("REVISION_CONFLICT")
            submitted_at = _now()
            answer_deadline = _parse_datetime(document.get("answer_deadline_at") or document.get("expires_at"))
            if document.get("status") != "WAITING_ANSWER" or answer_deadline <= submitted_at:
                raise ValueError("BATCH_NOT_PENDING")
            submission_id = "answer-" + hashlib.sha256(f"{batch_id}|{idempotency_key}|{request_hash}".encode()).hexdigest()[:24]
            document["status"] = "ANSWERED_PENDING_RESUME"
            document["answer_submission"] = {
                "schema_version": "work-answer-submission/v1",
                "submission_id": submission_id,
                "idempotency_key": idempotency_key,
                "channel_mode": "native_hitl",
                "work_definition_id": work_id,
                "tenant_id": tenant_id,
                "session_id": document.get("session_id"),
                "batch_id": batch_id,
                "expected_revision": expected_revision,
                "answers": copy.deepcopy(answers),
                "submitted_at": submitted_at.isoformat(),
                "actor_id": actor_id,
                "payload_sha256": request_hash,
            }
            retention_expires_at = submitted_at + ANSWER_RETENTION
            document["expires_at"] = retention_expires_at.isoformat()
            document["purge_at"] = retention_expires_at.isoformat()
            self._submission_keys[idem] = (request_hash, key)
            return copy.deepcopy(document), True

    def mark_resumed(self, tenant_id: str, work_id: str, batch_id: str, submission_id: str, resume_result: dict[str, Any]) -> None:
        with self._lock:
            document = self._batches[(tenant_id, work_id, batch_id)]
            if (document.get("answer_submission") or {}).get("submission_id") != submission_id:
                raise ValueError("SUBMISSION_CONFLICT")
            document["status"] = "RESUMED"
            document["resume_result"] = copy.deepcopy(resume_result)
            document["resumed_at"] = _now().isoformat()

    def get_work_definition(self, tenant_id: str, work_id: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._work.get((tenant_id, work_id))
            return _public_work_definition(value)

    def seed_work_definition(self, document: dict[str, Any]) -> None:
        """Test/development helper; production state remains owned by MongoDB."""
        with self._lock:
            self._work[(str(document["tenant_id"]), str(document["work_definition_id"]))] = copy.deepcopy(document)

    def health(self) -> dict[str, Any]:
        return {"ready": True, "storage": "memory", "batches": len(self._batches)}

    def close(self) -> None:
        return None


class MongoHitlRepository:
    def __init__(self, settings: Settings) -> None:
        from pymongo import ASCENDING, MongoClient

        self._client = MongoClient(settings.mongodb_uri, connectTimeoutMS=5000, serverSelectionTimeoutMS=5000, retryReads=True, retryWrites=True)
        self._client.admin.command("ping")
        database = self._client[settings.mongodb_database]
        self._batches = database[settings.collection_prefix + "clarification_batches"]
        self._work = database[settings.collection_prefix + "work_definitions"]
        self._runtime = database["work_runtime_states"]
        self._batches.create_index([("tenant_id", ASCENDING), ("work_definition_id", ASCENDING), ("batch_id", ASCENDING)], unique=True)
        self._batches.create_index("expires_at", expireAfterSeconds=0)
        self._batches.create_index([("tenant_id", ASCENDING), ("registration_idempotency_key", ASCENDING)], unique=True, sparse=True)
        self._batches.create_index([("tenant_id", ASCENDING), ("submission_idempotency_key", ASCENDING)], unique=True, sparse=True)

    @staticmethod
    def _clean(document: dict[str, Any] | None) -> dict[str, Any] | None:
        if document:
            document.pop("_id", None)
        return document

    def register_batch(self, document: dict[str, Any], idempotency_key: str, request_hash: str) -> tuple[dict[str, Any], bool]:
        from pymongo.errors import DuplicateKeyError

        query = {"tenant_id": document["tenant_id"], "work_definition_id": document["work_definition_id"], "batch_id": document["batch_id"]}
        document = copy.deepcopy(document)
        document["expires_at"] = _parse_datetime(document["expires_at"])
        document["answer_deadline_at"] = _parse_datetime(document.get("answer_deadline_at") or document["expires_at"])
        document["registration_idempotency_key"] = idempotency_key
        document["registration_request_hash"] = request_hash
        contract_fields = (
            "batch_id",
            "work_definition_id",
            "tenant_id",
            "owner_id",
            "session_id",
            "channel_mode",
            "revision",
            "round_number",
            "questions",
        )
        contract_hash = hashlib.sha256(
            _canonical({key: document.get(key) for key in contract_fields}).encode("utf-8")
        ).hexdigest()
        document["contract_sha256"] = contract_hash
        # Component 13 may persist the immutable batch before the Workflow API
        # exposes its job/request IDs. Attach only those orchestration fields;
        # never replace the question contract or a previously attached request.
        existing_contract = self._batches.find_one(query)
        if existing_contract:
            if existing_contract.get("contract_sha256") != contract_hash:
                raise ValueError("BATCH_CONFLICT")
            existing_job = str(existing_contract.get("workflow_job_id") or "")
            existing_request = str(existing_contract.get("workflow_request_id") or "")
            if existing_job and existing_job != str(document.get("workflow_job_id")):
                raise ValueError("BATCH_WORKFLOW_CONFLICT")
            if existing_request and existing_request != str(document.get("workflow_request_id")):
                raise ValueError("BATCH_WORKFLOW_CONFLICT")
            previous_key = str(existing_contract.get("registration_idempotency_key") or "")
            previous_hash = str(existing_contract.get("registration_request_hash") or "")
            if previous_key and (previous_key != idempotency_key or previous_hash != request_hash):
                raise ValueError("IDEMPOTENCY_CONFLICT")
            try:
                attached_result = self._batches.update_one(
                    {
                        **query,
                        "contract_sha256": contract_hash,
                        "$and": [
                            {"$or": [{"workflow_job_id": {"$exists": False}}, {"workflow_job_id": None}, {"workflow_job_id": document["workflow_job_id"]}]},
                            {"$or": [{"workflow_request_id": {"$exists": False}}, {"workflow_request_id": None}, {"workflow_request_id": document["workflow_request_id"]}]},
                            {"$or": [{"registration_idempotency_key": {"$exists": False}}, {"registration_idempotency_key": None}, {"registration_idempotency_key": idempotency_key}]},
                            {"$or": [{"registration_request_hash": {"$exists": False}}, {"registration_request_hash": None}, {"registration_request_hash": request_hash}]},
                        ],
                    },
                    {
                        "$set": {
                            "workflow_job_id": document["workflow_job_id"],
                            "workflow_request_id": document["workflow_request_id"],
                            "registration_idempotency_key": idempotency_key,
                            "registration_request_hash": request_hash,
                            "registered_at": document["registered_at"],
                            "owner_id": document.get("owner_id"),
                        }
                    },
                )
            except DuplicateKeyError as exc:
                raise ValueError("IDEMPOTENCY_CONFLICT") from exc
            attached = self._batches.find_one(query)
            if not attached:
                raise RuntimeError("batch disappeared during workflow attachment")
            if int(getattr(attached_result, "matched_count", 0)) != 1:
                raise ValueError("BATCH_WORKFLOW_CONFLICT")
            if (
                attached.get("contract_sha256") != contract_hash
                or str(attached.get("workflow_job_id") or "") != str(document["workflow_job_id"])
                or str(attached.get("workflow_request_id") or "") != str(document["workflow_request_id"])
                or str(attached.get("registration_idempotency_key") or "") != idempotency_key
                or str(attached.get("registration_request_hash") or "") != request_hash
            ):
                raise ValueError("BATCH_WORKFLOW_CONFLICT")
            return self._clean(attached) or {}, False
        try:
            self._batches.insert_one(document)
            return self._clean(document) or {}, True
        except DuplicateKeyError:
            existing = self._batches.find_one({"tenant_id": document["tenant_id"], "registration_idempotency_key": idempotency_key})
            if existing:
                if existing.get("registration_request_hash") != request_hash:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return self._clean(existing) or {}, False
            existing = self._batches.find_one(query)
            if existing and existing.get("registration_request_hash") == request_hash:
                return self._clean(existing) or {}, False
            raise ValueError("BATCH_CONFLICT")

    def get_batch(self, tenant_id: str, work_id: str, batch_id: str) -> dict[str, Any] | None:
        return self._clean(self._batches.find_one({"tenant_id": tenant_id, "work_definition_id": work_id, "batch_id": batch_id}))

    def submit_answers(self, tenant_id: str, work_id: str, batch_id: str, expected_revision: int, answers: list[dict[str, Any]], actor_id: str, idempotency_key: str, request_hash: str) -> tuple[dict[str, Any], bool]:
        from pymongo import ReturnDocument
        from pymongo.errors import DuplicateKeyError

        submitted_at = _now()
        retention_expires_at = submitted_at + ANSWER_RETENTION
        submission_id = "answer-" + hashlib.sha256(f"{batch_id}|{idempotency_key}|{request_hash}".encode()).hexdigest()[:24]
        submission = {
            "schema_version": "work-answer-submission/v1",
            "submission_id": submission_id,
            "idempotency_key": idempotency_key,
            "channel_mode": "native_hitl",
            "work_definition_id": work_id,
            "tenant_id": tenant_id,
            "batch_id": batch_id,
            "expected_revision": expected_revision,
            "answers": copy.deepcopy(answers),
            "submitted_at": submitted_at,
            "actor_id": actor_id,
            "payload_sha256": request_hash,
        }
        query = {
            "tenant_id": tenant_id,
            "work_definition_id": work_id,
            "batch_id": batch_id,
            "revision": expected_revision,
            "status": "WAITING_ANSWER",
            "$or": [
                {"answer_deadline_at": {"$gt": submitted_at}},
                {"answer_deadline_at": {"$exists": False}, "expires_at": {"$gt": submitted_at}},
            ],
        }
        try:
            updated = self._batches.find_one_and_update(
                query,
                {
                    "$set": {
                        "status": "ANSWERED_PENDING_RESUME",
                        "answer_submission": submission,
                        "submission_idempotency_key": idempotency_key,
                        "submission_request_hash": request_hash,
                        "expires_at": retention_expires_at,
                        "purge_at": retention_expires_at,
                    }
                },
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError as exc:
            updated = None
        if updated:
            return self._clean(updated) or {}, True
        existing = self._batches.find_one({"tenant_id": tenant_id, "submission_idempotency_key": idempotency_key})
        if existing:
            if existing.get("submission_request_hash") != request_hash or existing.get("batch_id") != batch_id:
                raise ValueError("IDEMPOTENCY_CONFLICT")
            return self._clean(existing) or {}, False
        current = self._batches.find_one({"tenant_id": tenant_id, "work_definition_id": work_id, "batch_id": batch_id})
        if not current:
            raise ValueError("BATCH_NOT_FOUND")
        if int(current.get("revision", -1)) != expected_revision:
            raise ValueError("REVISION_CONFLICT")
        raise ValueError("BATCH_NOT_PENDING")

    def mark_resumed(self, tenant_id: str, work_id: str, batch_id: str, submission_id: str, resume_result: dict[str, Any]) -> None:
        result = self._batches.update_one(
            {"tenant_id": tenant_id, "work_definition_id": work_id, "batch_id": batch_id, "status": "ANSWERED_PENDING_RESUME", "answer_submission.submission_id": submission_id},
            {"$set": {"status": "RESUMED", "resume_result": copy.deepcopy(resume_result), "resumed_at": _now()}},
        )
        if result.modified_count != 1:
            current = self._batches.find_one({"tenant_id": tenant_id, "work_definition_id": work_id, "batch_id": batch_id})
            if not current or current.get("status") != "RESUMED" or (current.get("answer_submission") or {}).get("submission_id") != submission_id:
                raise ValueError("SUBMISSION_CONFLICT")

    def get_work_definition(self, tenant_id: str, work_id: str) -> dict[str, Any] | None:
        document = self._work.find_one(
            {"tenant_id": tenant_id, "work_definition_id": work_id},
            {"mutation_receipts": 0, "pending_action": 0},
        )
        runtime = None
        if document:
            runtime = self._runtime.find_one(
                {
                    "tenant_id": tenant_id,
                    "work_definition_id": work_id,
                    "session_id": document.get("session_id"),
                },
                {"mutation_receipts": 0},
            )
        return _public_work_definition(document, runtime)

    def health(self) -> dict[str, Any]:
        self._client.admin.command("ping")
        return {"ready": True, "storage": "mongodb"}

    def close(self) -> None:
        self._client.close()


def _repository(settings: Settings) -> HitlRepository:
    return MemoryHitlRepository() if settings.storage_mode == "memory" else MongoHitlRepository(settings)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201, ARG002
        return None


def _verified_pending_request(settings: Settings, *, job_id: str, request_id: str, session_id: str) -> dict[str, Any]:
    if not settings.resume_enabled:
        return {"verified": False, "reason": "resume_disabled"}
    if not settings.langflow_api_key or not settings.langflow_f10_flow_id:
        raise RuntimeError("Langflow pending-request verification is not configured")
    payload = _pending_workflows(settings)
    match = next(
        (
            item
            for item in payload
            if isinstance(item, dict)
            and str(item.get("job_id")) == job_id
            and str(item.get("request_id")) == request_id
        ),
        None,
    )
    if not match:
        raise ValueError("WORKFLOW_PENDING_REQUEST_NOT_FOUND")
    if str(match.get("flow_id")) != settings.langflow_f10_flow_id or str(match.get("session_id")) != session_id:
        raise ValueError("WORKFLOW_PENDING_SCOPE_MISMATCH")
    if match.get("kind") != "node_input" or "submit_answers" not in (match.get("allowed_decisions") or []):
        raise ValueError("WORKFLOW_PENDING_DECISION_INVALID")
    return {"verified": True, "flow_id": settings.langflow_f10_flow_id, "job_id": job_id, "request_id": request_id}


def _pending_workflows(settings: Settings) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"flow_id": settings.langflow_f10_flow_id})
    url = _safe_langflow_url(settings.langflow_base_url) + "/api/v2/workflows/pending?" + query
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "x-api-key": settings.langflow_api_key},
        method="GET",
    )
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=20) as response:
            raw = response.read(1_048_577)
            if response.status != 200 or len(raw) > 1_048_576:
                raise RuntimeError("Langflow pending response is invalid or oversized")
            payload = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Langflow pending verification failed with HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Langflow pending verification is unavailable or returned invalid JSON") from exc
    if not isinstance(payload, list):
        raise RuntimeError("Langflow pending response must be a list")
    return [item for item in payload if isinstance(item, dict)]


def _workflow_job_status(settings: Settings, job_id: str) -> str:
    query = urllib.parse.urlencode({"job_id": job_id})
    url = _safe_langflow_url(settings.langflow_base_url) + "/api/v2/workflows?" + query
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "x-api-key": settings.langflow_api_key},
        method="GET",
    )
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=20) as response:
            raw = response.read(1_048_577)
            if response.status != 200 or len(raw) > 1_048_576:
                raise RuntimeError("Langflow workflow status response is invalid or oversized")
            payload = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Langflow workflow status failed with HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Langflow workflow status is unavailable or returned invalid JSON") from exc
    if not isinstance(payload, dict) or str(payload.get("job_id")) != job_id:
        raise RuntimeError("Langflow workflow status scope is invalid")
    return str(payload.get("status") or "").lower()


def _reconcile_resume_conflict(settings: Settings, batch: dict[str, Any]) -> dict[str, Any] | None:
    job_id = str(batch.get("workflow_job_id") or "")
    request_id = str(batch.get("workflow_request_id") or "")
    pending = _pending_workflows(settings)
    same_job = [item for item in pending if str(item.get("job_id")) == job_id]
    if any(str(item.get("request_id")) == request_id for item in same_job):
        return None
    if any(str(item.get("request_id")) != request_id for item in same_job):
        return {
            "status": "resumed_reconciled",
            "job_id": job_id,
            "message": "The original request was consumed and the workflow reached a later human-input request.",
        }
    job_status = _workflow_job_status(settings, job_id)
    if job_status in {"in_progress", "completed"}:
        return {
            "status": "resumed_reconciled",
            "job_id": job_id,
            "message": f"The original request was consumed; durable workflow status is {job_status}.",
        }
    return None


def _resume_langflow(settings: Settings, batch: dict[str, Any]) -> dict[str, Any]:
    if not settings.resume_enabled:
        return {"status": "resume_disabled", "job_id": batch.get("workflow_job_id")}
    url = _safe_langflow_url(settings.langflow_base_url) + "/api/v2/workflows/" + urllib.parse.quote(str(batch["workflow_job_id"]), safe="") + "/resume"
    body = json.dumps({"request_id": batch["workflow_request_id"], "decision": {"action_id": "submit_answers"}}, separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if settings.langflow_api_key:
        headers["x-api-key"] = settings.langflow_api_key
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=30) as response:
            raw = response.read(1_048_577)
            if len(raw) > 1_048_576:
                raise RuntimeError("Langflow resume response is oversized")
            payload = json.loads(raw.decode("utf-8"))
            if response.status not in {200, 202} or not isinstance(payload, dict):
                raise RuntimeError("invalid Langflow resume response")
            return {"status": str(payload.get("status") or "resuming"), "job_id": str(payload.get("job_id") or batch["workflow_job_id"]), "message": str(payload.get("message") or "")[:500]}
    except urllib.error.HTTPError as exc:
        detail = exc.read(16_385).decode("utf-8", errors="replace")[:500]
        if exc.code == 409:
            reconciled = _reconcile_resume_conflict(settings, batch)
            if reconciled is not None:
                return reconciled
        raise RuntimeError(f"Langflow resume rejected with HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError("Langflow resume endpoint is unavailable or returned invalid JSON") from exc


def create_app(settings: Settings | None = None, repository: HitlRepository | None = None) -> FastAPI:
    resolved = settings

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        nonlocal resolved
        resolved = resolved or Settings.from_env()
        resolved.validate()
        application.state.settings = resolved
        application.state.repository = repository or _repository(resolved)
        try:
            yield
        finally:
            application.state.repository.close()

    application = FastAPI(title="Business Work Design HITL Form API", version="1.0.0", lifespan=lifespan)

    async def actor(
        request: Request,
        authorization: str | None = Header(default=None),
        x_tenant_id: str | None = Header(default=None),
        x_actor_id: str | None = Header(default=None),
    ) -> dict[str, str]:
        current: Settings = request.app.state.settings
        tenant_id = str(x_tenant_id or "").strip()
        actor_id = str(x_actor_id or "").strip()
        if not ID_PATTERN.fullmatch(tenant_id) or not ID_PATTERN.fullmatch(actor_id):
            raise HTTPException(status_code=400, detail="Valid X-Tenant-ID and X-Actor-ID are required")
        if current.allow_insecure_local and not current.bearer_token:
            client_host = request.client.host if request.client else ""
            if client_host not in {"127.0.0.1", "::1", "testclient"}:
                raise HTTPException(status_code=401, detail="Local insecure mode accepts loopback clients only")
        else:
            expected = "Bearer " + current.bearer_token
            if not authorization or not hmac.compare_digest(authorization, expected):
                raise HTTPException(status_code=401, detail="Invalid bearer token")
        return {"tenant_id": tenant_id, "actor_id": actor_id}

    def repo(request: Request) -> HitlRepository:
        return request.app.state.repository

    def require_owner(document: dict[str, Any], identity: dict[str, str]) -> None:
        """Fail closed when a shared service credential is used for another actor.

        The gateway-authenticated actor header is still checked against durable
        ownership; possession of the bearer token alone never grants access to
        another employee's work definition or answers.
        """

        if str(document.get("owner_id") or "") != identity["actor_id"]:
            raise HTTPException(status_code=404, detail="Work definition not found")

    @application.middleware("http")
    async def body_limit(request: Request, call_next):
        length = request.headers.get("content-length")
        if length:
            try:
                if int(length) > MAX_ANSWER_BYTES:
                    return Response(status_code=413, content="Request body too large")
            except ValueError:
                return Response(status_code=400, content="Invalid Content-Length")
        return await call_next(request)

    @application.get("/api/health")
    def health(request: Request):
        try:
            return {"service": "hitl_form_api", **request.app.state.repository.health(), "resume_enabled": request.app.state.settings.resume_enabled}
        except Exception:
            return Response(status_code=503, content='{"service":"hitl_form_api","ready":false}', media_type="application/json")

    @application.post("/api/work-definitions/{work_id}/question-batches", status_code=status.HTTP_201_CREATED)
    def register_batch(
        work_id: str,
        payload: BatchRegistration,
        request: Request,
        response: Response,
        identity: dict[str, str] = Depends(actor),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        storage: HitlRepository = Depends(repo),
    ):
        if not idempotency_key or len(idempotency_key) > 200:
            raise HTTPException(status_code=400, detail="Idempotency-Key is required")
        try:
            document = _validate_batch(payload.clarification_batch, payload.workflow_job_id, payload.workflow_request_id)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if document["tenant_id"] != identity["tenant_id"] or document["work_definition_id"] != work_id:
            raise HTTPException(status_code=404, detail="Work definition not found")
        work_definition = storage.get_work_definition(identity["tenant_id"], work_id)
        if not work_definition:
            raise HTTPException(status_code=404, detail="Work definition not found")
        require_owner(work_definition, identity)
        identity_fields = ("tenant_id", "work_definition_id", "session_id", "channel_mode")
        if any(str(work_definition.get(key)) != str(document.get(key)) for key in identity_fields) or int(work_definition.get("revision", -1)) != int(document["revision"]):
            raise HTTPException(status_code=409, detail="WORK_BATCH_REVISION_OR_SCOPE_CONFLICT")
        document["owner_id"] = str(work_definition.get("owner_id") or identity["actor_id"])
        try:
            pending_verification = _verified_pending_request(
                request.app.state.settings,
                job_id=payload.workflow_job_id,
                request_id=payload.workflow_request_id,
                session_id=str(document["session_id"]),
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail={"code": "LANGFLOW_PENDING_VERIFICATION_FAILED", "message": str(exc)}) from exc
        document["workflow_pending_verification"] = pending_verification
        digest = hashlib.sha256(
            _canonical(
                {
                    "batch": payload.clarification_batch,
                    "job": payload.workflow_job_id,
                    "request": payload.workflow_request_id,
                }
            ).encode()
        ).hexdigest()
        try:
            stored, created = storage.register_batch(document, idempotency_key, digest)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not created:
            response.status_code = status.HTTP_200_OK
        return _public_batch(stored)

    @application.get("/api/work-definitions/{work_id}/question-batches/{batch_id}")
    def get_batch(work_id: str, batch_id: str, identity: dict[str, str] = Depends(actor), storage: HitlRepository = Depends(repo)):
        document = storage.get_batch(identity["tenant_id"], work_id, batch_id)
        if not document:
            raise HTTPException(status_code=404, detail="Question batch not found")
        require_owner(document, identity)
        return _public_batch(document)

    @application.post("/api/work-definitions/{work_id}/question-batches/{batch_id}/answers")
    def submit_answers(
        work_id: str,
        batch_id: str,
        payload: AnswerSubmission,
        request: Request,
        identity: dict[str, str] = Depends(actor),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        storage: HitlRepository = Depends(repo),
    ):
        if not idempotency_key or len(idempotency_key) > 200:
            raise HTTPException(status_code=400, detail="Idempotency-Key is required")
        current = storage.get_batch(identity["tenant_id"], work_id, batch_id)
        if not current:
            raise HTTPException(status_code=404, detail="Question batch not found")
        work_definition = storage.get_work_definition(identity["tenant_id"], work_id)
        if not work_definition:
            raise HTTPException(status_code=404, detail="Work definition not found")
        require_owner(work_definition, identity)
        require_owner(current, identity)
        if (
            int(work_definition.get("revision", -1)) != payload.expected_revision
            or str(work_definition.get("session_id")) != str(current.get("session_id"))
            or str(work_definition.get("channel_mode")) != "native_hitl"
        ):
            raise HTTPException(status_code=409, detail="WORK_BATCH_REVISION_OR_SCOPE_CONFLICT")
        try:
            normalized = _normalize_answers(current, [item.model_dump() for item in payload.answers])
            digest = _request_hash(work_id, batch_id, payload.expected_revision, normalized)
            stored, created = storage.submit_answers(identity["tenant_id"], work_id, batch_id, payload.expected_revision, normalized, identity["actor_id"], idempotency_key, digest)
        except ValueError as exc:
            code = str(exc)
            http_status = 409 if code in {"REVISION_CONFLICT", "IDEMPOTENCY_CONFLICT", "BATCH_NOT_PENDING"} else 422
            raise HTTPException(status_code=http_status, detail=code) from exc
        submission = stored.get("answer_submission") or {}
        if stored.get("status") == "RESUMED":
            return {"ok": True, "status": "RESUMED", "idempotent_replay": True, "answer_submission": submission, "resume_result": stored.get("resume_result")}
        try:
            resume_result = _resume_langflow(request.app.state.settings, stored)
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail={"code": "LANGFLOW_RESUME_FAILED", "message": str(exc), "submission_id": submission.get("submission_id"), "retryable": True}) from exc
        if resume_result.get("status") != "resume_disabled":
            storage.mark_resumed(identity["tenant_id"], work_id, batch_id, str(submission.get("submission_id")), resume_result)
            result_status = "RESUMED"
        else:
            result_status = "ANSWERED_PENDING_RESUME"
        return {"ok": True, "status": result_status, "idempotent_replay": not created, "answer_submission": submission, "resume_result": resume_result}

    @application.get("/api/work-definitions/{work_id}")
    def get_work_definition(work_id: str, identity: dict[str, str] = Depends(actor), storage: HitlRepository = Depends(repo)):
        document = storage.get_work_definition(identity["tenant_id"], work_id)
        if not document:
            raise HTTPException(status_code=404, detail="Work definition not found")
        require_owner(document, identity)
        return document

    return application


app = create_app()
