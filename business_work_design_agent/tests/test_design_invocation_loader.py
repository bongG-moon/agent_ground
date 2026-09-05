from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pymongo.errors import ConnectionFailure


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_PATH = (
    PROJECT_ROOT
    / "components"
    / "hybrid_retrieval"
    / "36_approved_design_invocation_loader.py"
)
PLANNER_COMPONENT_PATH = PROJECT_ROOT / "components" / "hybrid_retrieval" / "20_search_query_planner.py"


def load_component() -> ModuleType:
    names = ("lfx", "lfx.custom", "lfx.io", "lfx.schema")
    originals = {name: sys.modules.get(name) for name in names}

    class Component:
        pass

    class Port:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.args = args
            self.__dict__.update(kwargs)

    class Data:
        def __init__(self, data: Any = None) -> None:
            self.data = data or {}

        def get_text(self) -> str:
            return str(self.data.get("text") or "")

    modules = {name: types.ModuleType(name) for name in names}
    modules["lfx.custom"].Component = Component
    for port_name in ("DataInput", "IntInput", "MessageTextInput", "Output", "SecretStrInput"):
        setattr(modules["lfx.io"], port_name, Port)
    modules["lfx.schema"].Data = Data
    sys.modules.update(modules)

    module_name = "test_approved_design_invocation_loader_runtime"
    spec = importlib.util.spec_from_file_location(module_name, COMPONENT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        for name, original in originals.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original
    return module


def load_search_query_planner() -> ModuleType:
    module_name = "test_approved_design_invocation_datetime_planner"
    spec = importlib.util.spec_from_file_location(module_name, PLANNER_COMPONENT_PATH)
    assert spec and spec.loader
    planner = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = planner
    spec.loader.exec_module(planner)
    return planner


@pytest.fixture(scope="module")
def module() -> ModuleType:
    return load_component()


def canonical_hash(work: dict[str, Any]) -> str:
    semantic_fields = (
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
        "f10_design_context",
        "as_is_graph",
    )
    fields = semantic_fields if "f10_design_context" in work else tuple(
        field for field in semantic_fields if field != "f10_design_context"
    )
    semantic = {field: copy.deepcopy(work.get(field)) for field in fields}
    text = json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def approved_work() -> dict[str, Any]:
    raw_request = "메일 보고 업무를 자동화한다"
    additional_prompt = "승인 단계를 유지한다"
    source_turn_id = "turn-001"
    work: dict[str, Any] = {
        "schema_version": "work-definition/v1",
        "work_definition_id": "wd-001",
        "tenant_id": "tenant-a",
        "owner_id": "employee-1",
        "session_id": "session-1",
        "team_name": "업무자동화팀",
        "employee_id": "employee-1",
        "channel_mode": "native_hitl",
        "revision": 3,
        "status": "APPROVED",
        "goal": {"value": "Outlook 메일로 주간 업무보고를 만든다", "status": "confirmed"},
        "trigger": {"value": "매주 금요일", "status": "confirmed"},
        "scope_in": [],
        "scope_out": [],
        "actors": [],
        "systems": [],
        "inputs": [],
        "outputs": [],
        "steps": [],
        "decisions": [],
        "exceptions": [],
        "frequency_volume": None,
        "sla": None,
        "pains": [],
        "risks_controls": [],
        "constraints": [],
        "success_criteria": [],
        "automation_intent": None,
        "assumptions": [],
        "unresolved": [],
        "source_requests": [
            {
                "turn_id": source_turn_id,
                "raw_text": raw_request,
                "language": "ko",
                "submitted_at": "2026-08-30T00:00:00Z",
                "sha256": hashlib.sha256(raw_request.encode("utf-8")).hexdigest(),
            }
        ],
        "f10_design_context": {
            "schema_version": "f10-design-context/v1",
            "source_request_turn_id": source_turn_id,
            "source_request_sha256": "sha256:" + hashlib.sha256(raw_request.encode("utf-8")).hexdigest(),
            "additional_prompt": {
                "raw_text": additional_prompt,
                "sha256": hashlib.sha256(additional_prompt.encode("utf-8")).hexdigest(),
            },
        },
        "as_is_graph": {"nodes": [], "edges": []},
    }
    work["approved_hash"] = canonical_hash(work)
    work["preview_hash"] = work["approved_hash"]
    return work


def approval_result(work: dict[str, Any] | None = None) -> dict[str, Any]:
    selected = copy.deepcopy(work or approved_work())
    return {
        "ok": True,
        "status": "APPROVED",
        "artifact_refs": [
            {
                "kind": "work_definition",
                "id": selected["work_definition_id"],
                "revision": selected["revision"],
            }
        ],
        "work_definition": selected,
        "trace_id": "approval-trace",
    }


def request_envelope(*, additional_prompt: str = "승인 단계를 유지한다") -> dict[str, Any]:
    work = approved_work()
    return {
        "ok": True,
        "status": "INTAKE",
        "envelope": {
            "schema_version": "work-request-envelope/v1",
            "work_definition_id": work["work_definition_id"],
            "tenant_id": work["tenant_id"],
            "owner_id": work["owner_id"],
            "session_id": work["session_id"],
            "channel_mode": work["channel_mode"],
            "source_request": {"raw_text": "메일 보고 업무를 자동화한다"},
            "additional_prompt": {
                "raw_text": additional_prompt,
                "sha256": hashlib.sha256(additional_prompt.encode("utf-8")).hexdigest(),
            },
        },
    }


def authentication_context(
    *,
    source: str = "trusted_gateway",
    subject_id: str = "employee-1",
    groups: Any = None,
    verified: bool | None = None,
) -> dict[str, Any]:
    if groups is None:
        groups = ["REPORTERS", "ops", "reporters"]
    if verified is None:
        verified = source == "trusted_gateway"
    return {
        "ok": True,
        "status": "AUTHENTICATION_READY",
        "schema_version": "f10-authentication-context/v1",
        "artifact_refs": [],
        "source": source,
        "subject_id": subject_id,
        "groups": groups,
        "authenticated_subject_verified": verified,
        "trace_id": "authentication-trace",
    }


def skill(skill_id: str, *, status: str = "active", tenant_id: str = "tenant-a") -> dict[str, Any]:
    prompt = f"{skill_id} 사용 기준"
    return {
        "_id": f"mongo-{skill_id}",
        "tenant_id": tenant_id,
        "skill_id": skill_id,
        "name": skill_id,
        "version": "v1",
        "prompt_sha256": "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "trigger_rules": [skill_id],
        "near_miss_rules": [],
        "prompt_text": prompt,
        "forbidden_actions": [],
        "status": status,
        "acl": {"visibility": "tenant", "groups": []},
        "approved_by": "admin-1",
        "approved_at": "2026-08-29T00:00:00Z",
        "internal_note": "must not escape projection",
    }


class FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = copy.deepcopy(rows)

    def sort(self, fields: Any, direction: int | None = None) -> "FakeCursor":
        if isinstance(fields, list):
            names = [field for field, _ in fields]
        else:
            names = [str(fields)]
        self.rows.sort(key=lambda row: tuple(str(row.get(name) or "") for name in names))
        return self

    def limit(self, maximum: int) -> "FakeCursor":
        self.rows = self.rows[:maximum]
        return self

    def __iter__(self):
        return iter(self.rows)


class FakeCollection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = copy.deepcopy(rows)
        self.find_one_queries: list[dict[str, Any]] = []
        self.find_queries: list[dict[str, Any]] = []

    def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        self.find_one_queries.append(copy.deepcopy(query))
        for row in self.rows:
            if all(row.get(key) == value for key, value in query.items()):
                return copy.deepcopy(row)
        return None

    def find(self, query: dict[str, Any]) -> FakeCursor:
        self.find_queries.append(copy.deepcopy(query))
        rows = [
            row
            for row in self.rows
            if all(row.get(key) == value for key, value in query.items())
        ]
        return FakeCursor(rows)


class FakeDatabase:
    def __init__(self, work: dict[str, Any] | None = None, *, pointer: bool = True) -> None:
        stored = copy.deepcopy(work or approved_work())
        stored["_id"] = "mongo-work-id"
        stored["mutation_receipts"] = [{"idempotency_key": "secret-internal-receipt"}]
        stored["pending_action"] = {"token_sha256": "must-not-escape"}
        self.collections = {
            "work_definitions": FakeCollection([stored]),
            "catalog_active_pointers": FakeCollection(
                [
                    {
                        "_id": "tenant-a",
                        "tenant_id": "tenant-a",
                        "snapshot_id": "snap-001",
                        "active_snapshot_id": "snap-001",
                    }
                ]
                if pointer
                else []
            ),
            "skill_registry": FakeCollection(
                [
                    skill("skill-c"),
                    skill("skill-a"),
                    skill("skill-b"),
                    skill("inactive-skill", status="inactive"),
                    skill("other-tenant", tenant_id="tenant-b"),
                ]
            ),
        }

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections[name]


class FakeAdmin:
    def __init__(self) -> None:
        self.pinged = False

    def command(self, name: str) -> None:
        assert name == "ping"
        self.pinged = True


class FakeClient:
    def __init__(self, database: FakeDatabase) -> None:
        self.database = database
        self.admin = FakeAdmin()
        self.closed = False

    def __getitem__(self, name: str) -> FakeDatabase:
        assert name == "business_work_design"
        return self.database

    def close(self) -> None:
        self.closed = True


class FakeFactory:
    def __init__(self, database: FakeDatabase) -> None:
        self.client = FakeClient(database)
        self.uri = ""
        self.kwargs: dict[str, Any] = {}
        self.calls = 0

    def __call__(self, uri: str, **kwargs: Any) -> FakeClient:
        self.calls += 1
        self.uri = uri
        self.kwargs = copy.deepcopy(kwargs)
        return self.client


def invoke(module: ModuleType, factory: Any, **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "authentication_context": authentication_context(),
        "mongodb_uri": "mongodb://db-user:super-secret@mongo.internal:27017",
        "mongo_database": "business_work_design",
        "work_collection": "work_definitions",
        "pointer_collection": "catalog_active_pointers",
        "skill_registry_collection": "skill_registry",
        "timeout_ms": 2500,
        "max_skill_entries": 2,
        "trace_id": "invocation-trace",
        "client_factory": factory,
    }
    kwargs.update(overrides)
    return module.load_approved_design_invocation(
        kwargs.pop("approval_result", approval_result()),
        kwargs.pop("request_envelope", request_envelope()),
        **kwargs,
    )


def test_success_reloads_canonical_work_pointer_skills_and_prompt(module: ModuleType) -> None:
    database = FakeDatabase()
    factory = FakeFactory(database)

    result = invoke(module, factory)

    assert result["ok"] is True
    assert result["status"] == "READY_FOR_DESIGN"
    assert result["schema_version"] == "agent-design-invocation/v1"
    assert result["tenant_id"] == "tenant-a"
    assert result["work_definition_id"] == "wd-001"
    assert result["work_definition_revision"] == 3
    assert result["approved_hash"] == approved_work()["approved_hash"]
    assert result["catalog_snapshot_id"] == "snap-001"
    assert result["design_prompt"] == "승인 단계를 유지한다"
    assert result["search_seed"] == {
        "text": "메일 보고 업무를 자동화한다",
        "sha256": "sha256:" + hashlib.sha256("메일 보고 업무를 자동화한다".encode("utf-8")).hexdigest(),
        "source": "validated_original_work_request",
        "truncated": False,
    }
    assert result["acl_context"] == {
        "subject_id": "employee-1",
        "groups": ["ops", "reporters"],
    }
    assert result["trust_boundary"]["authenticated_subject_verified"] is True
    assert result["trust_boundary"]["authentication_context_source"] == "trusted_gateway"
    registry = result["skill_registry"]
    assert [item["skill_id"] for item in registry["skills"]] == ["skill-a", "skill-b"]
    assert registry == {
        "skills": registry["skills"],
        "count": 2,
        "truncated": True,
        "maximum": 2,
    }
    assert all("_id" not in item and "internal_note" not in item for item in registry["skills"])
    assert "_id" not in result["work_definition"]
    assert "mutation_receipts" not in result["work_definition"]
    assert "pending_action" not in result["work_definition"]
    assert factory.client.admin.pinged is True
    assert factory.client.closed is True
    assert factory.kwargs == {
        "serverSelectionTimeoutMS": 2500,
        "connectTimeoutMS": 2500,
        "socketTimeoutMS": 2500,
        "retryReads": True,
    }
    assert database["work_definitions"].find_one_queries == [
        {"tenant_id": "tenant-a", "work_definition_id": "wd-001"}
    ]
    assert database["skill_registry"].find_queries == [
        {"tenant_id": "tenant-a", "status": "active"}
    ]
    serialized = json.dumps(result, ensure_ascii=False, default=str)
    assert "mongodb://" not in serialized
    assert "super-secret" not in serialized
    assert "must-not-escape" not in serialized


def test_later_chat_answer_run_recovers_f20_inputs_from_canonical_context(module: ModuleType) -> None:
    """A later Playground run has no Component 10 output to connect here."""

    work = approved_work()
    database = FakeDatabase(work)
    factory = FakeFactory(database)

    result = invoke(
        module,
        factory,
        approval_result=approval_result(work),
        request_envelope=None,
    )

    assert result["ok"] is True
    assert result["design_prompt"] == "승인 단계를 유지한다"
    assert result["search_seed"] == {
        "text": "메일 보고 업무를 자동화한다",
        "sha256": "sha256:" + hashlib.sha256("메일 보고 업무를 자동화한다".encode("utf-8")).hexdigest(),
        "source": "validated_original_work_request",
        "truncated": False,
    }
    assert result["trust_boundary"]["request_envelope_source"] == (
        "mongodb-canonical-approved-f10-design-context"
    )
    assert result["trust_boundary"]["design_prompt_source"] == (
        "mongodb-canonical-approved-f10-design-context"
    )
    # The reconstructed result uses the same sealed F10 -> F20 transport
    # contract as an ordinary first-pass invocation.
    planner = load_search_query_planner()
    assert planner.validate_design_invocation(result)["ok"] is True
    assert factory.client.closed is True


def test_chat_answer_resume_requires_sealed_durable_design_context(module: ModuleType) -> None:
    work = approved_work()
    work.pop("f10_design_context")
    # This represents a pre-migration approved record: its old hash remains
    # valid for an ordinary first-pass run, but it cannot safely resume later.
    work["approved_hash"] = canonical_hash(work)
    factory = FakeFactory(FakeDatabase(work))

    result = invoke(
        module,
        factory,
        approval_result=approval_result(work),
        request_envelope=None,
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "F10_DESIGN_CONTEXT_REQUIRED"
    assert factory.client.closed is True


def test_chat_answer_resume_rejects_source_replaced_under_same_turn_id(module: ModuleType) -> None:
    """The turn id alone must not authorize a different original request."""

    work = approved_work()
    replacement = "동일한 turn id에 다른 업무 원문을 주입한다"
    work["source_requests"][0]["raw_text"] = replacement
    work["source_requests"][0]["sha256"] = hashlib.sha256(replacement.encode("utf-8")).hexdigest()
    # source_requests itself is historical provenance, not a semantic work
    # field.  The sealed f10_design_context hash binding must catch this.
    factory = FakeFactory(FakeDatabase(work))

    result = invoke(
        module,
        factory,
        approval_result=approval_result(work),
        request_envelope=None,
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "F10_DESIGN_CONTEXT_SOURCE_INVALID"
    assert factory.client.closed is True


def test_nonempty_invalid_request_is_not_silently_treated_as_a_resume(module: ModuleType) -> None:
    factory = FakeFactory(FakeDatabase())

    result = invoke(module, factory, request_envelope={"ok": True, "envelope": {}})

    assert result["ok"] is False
    assert result["error"]["code"] == "WORK_REQUEST_ENVELOPE_INVALID"
    assert factory.calls == 0


@pytest.mark.parametrize(
    ("field", "replacement", "expected_code"),
    [
        ("tenant_id", "tenant-b", "APPROVED_WORK_DEFINITION_NOT_FOUND"),
        ("work_definition_id", "wd-002", "APPROVED_WORK_DEFINITION_NOT_FOUND"),
        ("revision", 4, "APPROVED_WORK_DEFINITION_MISMATCH"),
        ("approved_hash", "sha256:" + "a" * 64, "APPROVED_WORK_DEFINITION_MISMATCH"),
        ("status", "READY_FOR_REVIEW", "APPROVED_WORK_DEFINITION_MISMATCH"),
        ("owner_id", "employee-2", "APPROVED_WORK_DEFINITION_MISMATCH"),
        ("session_id", "session-2", "APPROVED_WORK_DEFINITION_MISMATCH"),
    ],
)
def test_canonical_approval_identity_and_lock_fields_must_match_exactly(
    module: ModuleType,
    field: str,
    replacement: Any,
    expected_code: str,
) -> None:
    canonical = approved_work()
    canonical[field] = replacement
    factory = FakeFactory(FakeDatabase(canonical))

    result = invoke(module, factory)

    assert result["ok"] is False
    assert result["error"]["code"] == expected_code
    if expected_code == "APPROVED_WORK_DEFINITION_MISMATCH":
        assert field in result["error"]["details"]["fields"]
    assert factory.client.closed is True


def test_canonical_semantic_body_must_still_match_approved_hash(module: ModuleType) -> None:
    canonical = approved_work()
    canonical["goal"] = {"value": "승인 이후 몰래 바뀐 목표", "status": "confirmed"}
    factory = FakeFactory(FakeDatabase(canonical))

    result = invoke(module, factory)

    assert result["ok"] is False
    assert result["error"]["code"] == "APPROVED_WORK_DEFINITION_HASH_MISMATCH"
    assert factory.client.closed is True


def test_legacy_playground_channel_is_blocked_at_authority_boundary(module: ModuleType) -> None:
    legacy = approved_work()
    legacy["channel_mode"] = "playground"
    request = request_envelope()
    request["envelope"]["channel_mode"] = "playground"
    factory = FakeFactory(FakeDatabase(legacy))

    result = invoke(
        module,
        factory,
        approval_result=approval_result(legacy),
        request_envelope=request,
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "WORK_REQUEST_CHANNEL_INVALID"
    assert factory.client.closed is True


def test_request_identity_and_trusted_subject_are_checked_before_mongodb(module: ModuleType) -> None:
    mismatched_request = request_envelope()
    mismatched_request["envelope"]["session_id"] = "session-other"
    first_factory = FakeFactory(FakeDatabase())
    request_result = invoke(module, first_factory, request_envelope=mismatched_request)
    assert request_result["error"]["code"] == "WORK_REQUEST_APPROVAL_MISMATCH"
    assert first_factory.calls == 0

    second_factory = FakeFactory(FakeDatabase())
    subject_result = invoke(module, second_factory, authentication_context=authentication_context(subject_id="employee-other"))
    assert subject_result["error"]["code"] == "AUTHENTICATED_SUBJECT_OWNER_MISMATCH"
    assert second_factory.calls == 0


@pytest.mark.parametrize(
    ("groups", "expected_code"),
    [
        ('{"groups":["ops"],"subject_id":"forged"}', "AUTHENTICATED_GROUPS_INVALID"),
        ('["valid", "bad group"]', "AUTHENTICATED_GROUPS_INVALID"),
        ("[not-json", "AUTHENTICATED_GROUPS_INVALID"),
        ([f"group-{index}" for index in range(101)], "AUTHENTICATED_GROUPS_LIMIT_EXCEEDED"),
    ],
)
def test_authenticated_groups_fail_closed_and_are_bounded(
    module: ModuleType,
    groups: Any,
    expected_code: str,
) -> None:
    factory = FakeFactory(FakeDatabase())
    result = invoke(module, factory, authentication_context=authentication_context(groups=groups))
    assert result["ok"] is False
    assert result["error"]["code"] == expected_code
    assert factory.calls == 0


@pytest.mark.parametrize(
    ("context", "expected_code"),
    [
        ({}, "AUTHENTICATION_CONTEXT_INVALID"),
        (authentication_context(source="browser", verified=False), "AUTHENTICATION_SOURCE_INVALID"),
        (authentication_context(source="trusted_gateway", verified=False), "AUTHENTICATION_VERIFICATION_INVALID"),
        (authentication_context(source="local_demo_fixture", groups=["ops"]), "LOCAL_DEMO_GROUPS_NOT_ALLOWED"),
    ],
)
def test_authentication_context_is_sealed_and_checked_before_mongodb(
    module: ModuleType,
    context: dict[str, Any],
    expected_code: str,
) -> None:
    factory = FakeFactory(FakeDatabase())
    result = invoke(module, factory, authentication_context=context)
    assert result["ok"] is False
    assert result["error"]["code"] == expected_code
    assert factory.calls == 0


def test_local_demo_authentication_context_is_supported_but_explicitly_unverified(module: ModuleType) -> None:
    factory = FakeFactory(FakeDatabase())
    result = invoke(
        module,
        factory,
        authentication_context=authentication_context(source="local_demo_fixture", groups=[], verified=False),
    )
    assert result["ok"] is True
    assert result["trust_boundary"]["authentication_context_source"] == "local_demo_fixture"
    assert result["trust_boundary"]["authenticated_subject_verified"] is False


def test_prompt_hash_secret_pointer_and_database_failures_do_not_echo_sensitive_values(
    module: ModuleType,
) -> None:
    bad_hash = request_envelope()
    bad_hash["envelope"]["additional_prompt"]["sha256"] = "0" * 64
    hash_factory = FakeFactory(FakeDatabase())
    hash_result = invoke(module, hash_factory, request_envelope=bad_hash)
    assert hash_result["error"]["code"] == "DESIGN_PROMPT_HASH_MISMATCH"
    assert hash_factory.calls == 0

    secret_prompt = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz"
    secret_request = request_envelope(additional_prompt=secret_prompt)
    secret_factory = FakeFactory(FakeDatabase())
    secret_result = invoke(module, secret_factory, request_envelope=secret_request)
    assert secret_result["error"]["code"] == "DESIGN_PROMPT_SECRET_MATERIAL_DETECTED"
    assert secret_prompt not in json.dumps(secret_result, ensure_ascii=False)
    assert secret_factory.calls == 0

    invalid_seed_request = request_envelope()
    invalid_seed_request["envelope"]["source_request"]["sha256"] = "0" * 64
    invalid_seed_factory = FakeFactory(FakeDatabase())
    invalid_seed_result = invoke(module, invalid_seed_factory, request_envelope=invalid_seed_request)
    assert invalid_seed_result["error"]["code"] == "SEARCH_SEED_HASH_MISMATCH"
    assert invalid_seed_factory.calls == 0

    pointer_factory = FakeFactory(FakeDatabase(pointer=False))
    pointer_result = invoke(module, pointer_factory)
    assert pointer_result["error"]["code"] == "ACTIVE_CATALOG_POINTER_NOT_FOUND"
    assert pointer_factory.client.closed is True

    mongodb_uri = "mongodb://db-user:do-not-echo@mongo.internal:27017"

    def unavailable(uri: str, **_: Any) -> Any:
        assert uri == mongodb_uri
        raise ConnectionFailure("driver detail contains do-not-echo")

    unavailable_result = invoke(module, unavailable, mongodb_uri=mongodb_uri)
    assert unavailable_result["error"]["code"] == "DESIGN_INVOCATION_MONGODB_UNAVAILABLE"
    serialized = json.dumps(unavailable_result, ensure_ascii=False)
    assert mongodb_uri not in serialized
    assert "do-not-echo" not in serialized


def test_component_declares_grouped_success_and_blocked_outputs(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    component = module.ApprovedDesignInvocationLoaderComponent
    assert component.name == "ApprovedDesignInvocationLoader"
    outputs = {output.name: output for output in component.outputs}
    assert set(outputs) == {"success_path", "blocked_path"}
    assert outputs["success_path"].group_outputs is True
    assert outputs["blocked_path"].group_outputs is True
    input_names = {item.name for item in component.inputs}
    assert {
        "approval_result",
        "request_envelope",
        "authentication_context",
        "mongodb_uri",
        "mongo_database",
        "work_collection",
        "pointer_collection",
        "skill_registry_collection",
        "timeout_ms",
    } <= input_names
    inputs = {item.name: item for item in component.inputs}
    assert inputs["mongo_database"].value == "business_work_design"

    factory = FakeFactory(FakeDatabase())
    monkeypatch.setattr(module, "MongoClient", factory)
    instance = component()
    instance.approval_result = approval_result()
    instance.request_envelope = request_envelope()
    instance.authentication_context = authentication_context(groups=["ops", "REPORTERS"])
    instance.mongodb_uri = "mongodb://internal"
    instance.mongo_database = "business_work_design"
    instance.work_collection = "work_definitions"
    instance.pointer_collection = "catalog_active_pointers"
    instance.skill_registry_collection = "skill_registry"
    instance.timeout_ms = 5000
    instance.max_skill_entries = 200
    instance.trace_id = "component-trace"
    stopped: list[str] = []
    instance.stop = stopped.append

    routed_data = instance.route_invocation()
    routed = routed_data.data

    assert routed["ok"] is True
    strict_json = routed_data.get_text()
    parsed = json.loads(strict_json)
    assert parsed["ok"] is True
    assert parsed["schema_version"] == "agent-design-invocation/v1"
    assert "text" not in parsed
    assert "'ok': True" not in strict_json
    assert stopped == ["blocked_path"]
    assert instance.status["route"] == "success_path"

    # A nested Langflow Run Flow can preserve Data fields while appending the
    # blank default field to the same payload.  This is the exact F10 -> F20
    # transport shell accepted by the F20 planner, not a permissive wrapper.
    planner = load_search_query_planner()
    nested_transport = copy.deepcopy(routed)
    nested_transport["default_value"] = ""
    accepted = planner.validate_design_invocation(nested_transport)
    assert accepted["ok"] is True
    assert accepted["work_definition"]["work_definition_id"] == routed["work_definition_id"]


def test_component_normalizes_mongodb_datetimes_before_emitting_strict_json(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MongoDB timestamps must not escape through the F10 -> F20 boundary."""

    timestamp = datetime(2026, 8, 30, 9, 15, 30, 123456, tzinfo=timezone.utc)
    work = approved_work()
    work.update(
        {
            "created_at": timestamp,
            "updated_at": timestamp,
            "last_event": {"event_id": "event-1", "occurred_at": timestamp},
        }
    )
    database = FakeDatabase(work)
    for skill_document in database["skill_registry"].rows:
        if skill_document.get("skill_id") == "skill-a":
            skill_document["approved_at"] = timestamp
    factory = FakeFactory(database)
    monkeypatch.setattr(module, "MongoClient", factory)

    instance = module.ApprovedDesignInvocationLoaderComponent()
    instance.approval_result = approval_result(work)
    instance.request_envelope = request_envelope()
    instance.authentication_context = authentication_context(groups=["ops"])
    instance.mongodb_uri = "mongodb://internal"
    instance.mongo_database = "business_work_design"
    instance.work_collection = "work_definitions"
    instance.pointer_collection = "catalog_active_pointers"
    instance.skill_registry_collection = "skill_registry"
    instance.timeout_ms = 5000
    instance.max_skill_entries = 200
    instance.trace_id = "component-datetime-trace"
    instance.stop = lambda _output: None

    routed_data = instance.route_invocation()
    routed = routed_data.data
    text_payload = json.loads(routed_data.get_text())

    # Both the structured Data payload and the strict JSON text must be safe.
    json.dumps(routed, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    expected_timestamp = "2026-08-30T09:15:30.123456Z"
    assert routed["work_definition"]["created_at"] == expected_timestamp
    assert routed["work_definition"]["updated_at"] == expected_timestamp
    assert routed["work_definition"]["last_event"]["occurred_at"] == expected_timestamp
    assert text_payload["work_definition"]["created_at"] == expected_timestamp
    assert text_payload["skill_registry"]["skills"][0]["approved_at"] == expected_timestamp

    # The exact JSON passed by Run Flow must remain valid for F20's authority
    # boundary; it must not contain Python datetime objects or a Python repr.
    planner = load_search_query_planner()
    assert planner.validate_design_invocation(routed_data.get_text())["ok"] is True
