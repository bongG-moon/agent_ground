from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import jsonschema
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_PATHS = {
    "preview": PROJECT_ROOT / "components" / "work_definition" / "17_work_preview_hasher.py",
    "skill": PROJECT_ROOT / "components" / "hybrid_retrieval" / "19_skill_context_resolver.py",
    "planner": PROJECT_ROOT / "components" / "hybrid_retrieval" / "20_search_query_planner.py",
}


def load_component(name: str, path: Path) -> ModuleType:
    module_name = f"test_f20_security_{name}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def modules() -> dict[str, ModuleType]:
    return {name: load_component(name, path) for name, path in COMPONENT_PATHS.items()}


def base_work() -> dict[str, Any]:
    return {
        "schema_version": "work-definition/v1",
        "work_definition_id": "wd-security-1",
        "tenant_id": "tenant-a",
        "owner_id": "employee-1",
        "session_id": "session-1",
        "channel_mode": "native_hitl",
        "revision": 7,
        "status": "READY_FOR_REVIEW",
        "title": {"value": "메일 업무보고", "status": "confirmed"},
        "goal": {"value": "메일을 수집해 주간 업무보고를 만든다", "status": "confirmed"},
        "trigger": {"value": "매주 금요일", "status": "confirmed"},
        "scope_in": [],
        "scope_out": [],
        "actors": [],
        "systems": [{"name": "Outlook", "provenance": {"status": "confirmed"}}],
        "inputs": [{"name": "업무 메일", "provenance": {"status": "confirmed"}}],
        "outputs": [{"name": "주간 업무보고", "provenance": {"status": "confirmed"}}],
        "steps": [
            {"step_id": "collect", "title": "메일 수집", "capability": "Outlook 메일을 조회한다"},
            {"step_id": "summarize", "title": "업무 요약", "capability": "업무별 진행 내용을 요약한다"},
        ],
        "decisions": [],
        "exceptions": [],
        "frequency_volume": {},
        "sla": {},
        "pains": [],
        "risks_controls": [
            {"name": "외부 전송 전 사용자 승인", "provenance": {"status": "confirmed"}}
        ],
        "constraints": [],
        "success_criteria": [],
        "automation_intent": {},
        "assumptions": [],
        "unresolved": [],
        "as_is_graph": {
            "nodes": [
                {"node_id": "collect", "node_type": "task", "title": "메일 수집"},
                {"node_id": "summarize", "node_type": "task", "title": "업무 요약"},
            ],
            "edges": [
                {"edge_id": "e-1", "source_node_id": "collect", "target_node_id": "summarize"}
            ],
        },
    }


def approved_work(modules: dict[str, ModuleType]) -> dict[str, Any]:
    work = base_work()
    preview = modules["preview"].build_work_preview_hash(
        {"ok": True, "work_definition": work, "graph_validation": {"valid": True}}
    )
    assert preview["ok"] is True
    approved = preview["work_definition"]
    approved["approved_hash"] = preview["preview"]["preview_hash"]
    approved["status"] = "APPROVED"
    return approved


def sealed_scope(modules: dict[str, ModuleType]) -> dict[str, Any]:
    scope = modules["planner"].build_design_scope(
        approved_work(modules),
        tenant_id="tenant-a",
        catalog_snapshot_id="snapshot-1",
        acl_context={"subject_id": "employee-1", "groups": ["engineering"]},
        design_prompt="승인 단계를 유지한다",
    )
    assert scope["ok"] is True
    return scope


def design_invocation(modules: dict[str, ModuleType]) -> dict[str, Any]:
    work = approved_work(modules)
    return {
        "ok": True,
        "status": "READY_FOR_DESIGN",
        "schema_version": "agent-design-invocation/v1",
        "artifact_refs": [],
        "tenant_id": "tenant-a",
        "work_definition_id": work["work_definition_id"],
        "work_definition_revision": work["revision"],
        "approved_hash": work["approved_hash"],
        "owner_id": work["owner_id"],
        "session_id": work["session_id"],
        "work_definition": work,
        "acl_context": {"subject_id": "employee-1", "groups": ["engineering"]},
        "catalog_snapshot_id": "snapshot-1",
        "skill_registry": {"skills": [], "count": 0, "truncated": False, "maximum": 200},
        "design_prompt": "승인 단계를 유지한다",
        "trust_boundary": {
            "work_definition_source": "mongodb-canonical-approved",
            "catalog_snapshot_source": "mongodb-active-pointer",
            "skill_registry_source": "mongodb-active-only",
            "authenticated_subject_verified": True,
        },
        "trace_id": "trace-invocation",
    }


def test_design_invocation_is_the_single_authority_bound_f20_input(modules: dict[str, ModuleType]) -> None:
    invocation = design_invocation(modules)
    result = modules["planner"].validate_design_invocation(invocation)
    assert result["ok"] is True
    assert result["tenant_id"] == "tenant-a"
    assert result["authority"]["work_definition_source"] == "mongodb-canonical-approved"

    strict_json_result = modules["planner"].validate_design_invocation(
        json.dumps(invocation, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    assert strict_json_result["ok"] is True
    assert strict_json_result["work_definition"]["work_definition_id"] == invocation["work_definition_id"]


def test_design_invocation_accepts_verified_legacy_or_empty_search_seed(
    modules: dict[str, ModuleType],
) -> None:
    """F20 remains runnable when an older imported F10 sends its raw seed."""

    legacy_text = "  Outlook 메일을 모아 주간 업무보고 초안을 만들고 승인 후 게시한다.  "
    legacy = design_invocation(modules)
    legacy["search_seed"] = {
        "turn_id": "turn-legacy-1",
        "raw_text": legacy_text,
        "language": "ko",
        "submitted_at": "2026-08-31T00:00:00Z",
        "sha256": "sha256:" + hashlib.sha256(legacy_text.encode("utf-8")).hexdigest(),
    }
    normalized = modules["planner"].validate_design_invocation(legacy)
    assert normalized["ok"] is True
    assert normalized["search_seed"]["text"] == legacy_text.strip()
    assert normalized["search_seed"]["sha256"] == "sha256:" + hashlib.sha256(
        legacy_text.strip().encode("utf-8")
    ).hexdigest()

    legacy_wrapper = design_invocation(modules)
    legacy_wrapper["search_seed"] = {"source_request": legacy["search_seed"]}
    wrapped_result = modules["planner"].validate_design_invocation(legacy_wrapper)
    assert wrapped_result["ok"] is True
    assert wrapped_result["search_seed"] == normalized["search_seed"]

    # Langflow's Message/Data bridge may leave one narrow transport wrapper
    # around the value when F10 and F20 were imported at different times.
    for bridge_seed in (
        {"data": legacy["search_seed"]},
        {"text": json.dumps(legacy["search_seed"], ensure_ascii=False)},
        {"data": {"text": json.dumps(legacy["search_seed"], ensure_ascii=False)}},
    ):
        bridged = design_invocation(modules)
        bridged["search_seed"] = bridge_seed
        bridged_result = modules["planner"].validate_design_invocation(bridged)
        assert bridged_result["ok"] is True
        assert bridged_result["search_seed"] == normalized["search_seed"]

    for empty_seed in ({}, "", "   ", {"text": ""}):
        empty = design_invocation(modules)
        empty["search_seed"] = empty_seed
        empty_result = modules["planner"].validate_design_invocation(empty)
        assert empty_result["ok"] is True
        assert empty_result["search_seed"] == {}


def test_design_invocation_rejects_unverified_legacy_search_seed(
    modules: dict[str, ModuleType],
) -> None:
    invalid = design_invocation(modules)
    invalid["search_seed"] = {
        "raw_text": "Outlook 메일을 모아 보고서를 만든다",
        "sha256": "sha256:" + "0" * 64,
    }
    result = modules["planner"].validate_design_invocation(invalid)
    assert result["ok"] is False
    assert result["error"]["code"] == "SEARCH_SEED_HASH_MISMATCH"

    missing_hash = design_invocation(modules)
    missing_hash["search_seed"] = {"raw_text": "Outlook 메일을 모아 보고서를 만든다"}
    missing_result = modules["planner"].validate_design_invocation(missing_hash)
    assert missing_result["ok"] is False
    assert missing_result["error"]["code"] == "SEARCH_SEED_HASH_MISMATCH"


def test_design_invocation_rejects_unknown_fields_and_untrusted_authority(modules: dict[str, ModuleType]) -> None:
    unknown = design_invocation(modules)
    unknown["browser_override"] = True
    assert modules["planner"].validate_design_invocation(unknown)["error"]["code"] == "DESIGN_INVOCATION_FIELDS_INVALID"

    untrusted = design_invocation(modules)
    untrusted["trust_boundary"]["work_definition_source"] = "browser"
    assert modules["planner"].validate_design_invocation(untrusted)["error"]["code"] == "DESIGN_INVOCATION_AUTHORITY_INVALID"


def test_empty_approved_skill_registry_preserves_design_scope_locks(modules: dict[str, ModuleType]) -> None:
    invocation = modules["planner"].validate_design_invocation(design_invocation(modules))
    assert invocation["ok"] is True
    scope = sealed_scope(modules)
    approved_skill_registry = {
        "ok": True,
        "status": "COMPLETED",
        "tenant_id": invocation["tenant_id"],
        "skills": invocation["skill_registry"],
        "authority": invocation["authority"],
        "trace_id": invocation["trace_id"],
    }

    result = modules["skill"].resolve_skill_context(scope, approved_skill_registry)

    assert result["ok"] is True
    assert result["status"] == "COMPLETED"
    assert result["approved_skill_context"] == ""
    assert result["applied_skills"] == []
    assert result["rejected_skills"] == []
    for field in (
        "tenant_id",
        "catalog_snapshot_id",
        "work_definition_id",
        "work_definition_revision",
        "approved_hash",
        "design_scope_sha256",
    ):
        assert result[field] == scope[field]


@pytest.mark.parametrize(
    ("registry", "error_code"),
    [
        (None, "SKILL_REGISTRY_EMPTY"),
        ({}, "SKILL_REGISTRY_EMPTY"),
        ({"skills": "not-a-list"}, "SKILL_REGISTRY_CONTRACT_INVALID"),
        ({"ok": False, "skills": []}, "SKILL_REGISTRY_UPSTREAM_FAILED"),
    ],
)
def test_missing_or_malformed_skill_registry_stays_fail_closed(
    modules: dict[str, ModuleType], registry: Any, error_code: str
) -> None:
    result = modules["skill"].resolve_skill_context(sealed_scope(modules), registry)

    assert result["ok"] is False
    assert result["error"]["code"] == error_code


def skill_entry(*, acl: Any) -> dict[str, Any]:
    prompt = "메일 업무보고 설계에서는 입력, 출력, 사용자 승인 단계를 명시한다."
    digest = "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return {
        "tenant_id": "tenant-a",
        "skill_id": "mail-report-design",
        "name": "Mail Report Design",
        "version": "1.0.0",
        "status": "active",
        "prompt_text": prompt,
        "prompt_sha256": digest,
        "trigger_rules": [{"kind": "contains", "value": "메일"}],
        "near_miss_rules": [],
        "acl": acl,
        "approved_by": "security-reviewer",
        "approved_at": "2026-08-28T09:00:00+09:00",
    }


def test_design_scope_rejects_semantic_mutation_after_approval(modules: dict[str, ModuleType]) -> None:
    work = approved_work(modules)
    work["goal"]["value"] = "승인되지 않은 다른 목표"

    result = modules["planner"].build_design_scope(
        work,
        tenant_id="tenant-a",
        catalog_snapshot_id="snapshot-1",
        acl_context={"subject_id": "employee-1", "groups": ["engineering"]},
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "WORK_DEFINITION_APPROVAL_HASH_MISMATCH"


@pytest.mark.parametrize(
    "secret_text",
    [
        "Bearer top-secret-token-1234567890",
        "api_key=abcdefghijklmnop123456",
        "password: CorrectHorseBatteryStaple",
        "access_token: abcdefghijklmnop",
    ],
)
def test_design_scope_rejects_secret_material_in_additional_prompt(
    modules: dict[str, ModuleType], secret_text: str
) -> None:
    result = modules["planner"].build_design_scope(
        approved_work(modules),
        tenant_id="tenant-a",
        catalog_snapshot_id="snapshot-1",
        acl_context={"subject_id": "employee-1", "groups": ["engineering"]},
        design_prompt="이 인증값을 사용해줘: " + secret_text,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "DESIGN_PROMPT_SECRET_MATERIAL_DETECTED"
    assert secret_text not in str(result)


def test_design_scope_projects_only_approved_semantics(modules: dict[str, ModuleType]) -> None:
    original = approved_work(modules)
    with_unapproved_extras = copy.deepcopy(original)
    with_unapproved_extras["source_requests"] = [{"raw": "untrusted raw prompt"}]
    with_unapproved_extras["extensions"] = {"tool_override": "unapproved-tool"}
    with_unapproved_extras["processed_answer_batches"] = ["batch-unapproved"]
    with_unapproved_extras["steps"][0]["trace_id"] = "Bearer nested-unapproved-secret-value"
    with_unapproved_extras["steps"][0]["ui_debug"] = {"credential": "should-not-project"}

    kwargs = {
        "tenant_id": "tenant-a",
        "catalog_snapshot_id": "snapshot-1",
        "acl_context": {"subject_id": "employee-1", "groups": ["engineering"]},
        "design_prompt": "승인 단계를 유지한다",
    }
    baseline = modules["planner"].build_design_scope(original, **kwargs)
    projected = modules["planner"].build_design_scope(with_unapproved_extras, **kwargs)

    assert baseline["ok"] is True and projected["ok"] is True
    assert baseline["design_scope_sha256"] == projected["design_scope_sha256"]
    assert projected["work_definition"] == baseline["work_definition"]
    for field in ("source_requests", "extensions", "processed_answer_batches"):
        assert field not in projected["work_definition"]
    assert "trace_id" not in projected["work_definition"]["steps"][0]
    assert "ui_debug" not in projected["work_definition"]["steps"][0]
    assert "nested-unapproved-secret-value" not in str(projected["work_definition"])


def test_search_seed_drives_retrieval_without_becoming_an_approved_semantic_fact(
    modules: dict[str, ModuleType],
) -> None:
    text = "Outlook 메일을 모아 주간 업무보고 초안을 만들고 승인 후 게시한다"
    seed = {
        "text": text,
        "sha256": "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "source": "validated_original_work_request",
        "truncated": False,
    }
    kwargs = {
        "tenant_id": "tenant-a",
        "catalog_snapshot_id": "snapshot-1",
        "acl_context": {"subject_id": "employee-1", "groups": ["engineering"]},
        "design_prompt": "카탈로그에서 검증된 자산을 우선 추천한다.",
        "search_seed": seed,
    }
    scope = modules["planner"].build_design_scope(approved_work(modules), **kwargs)
    plan = modules["planner"].build_search_query_plan(approved_work(modules), **kwargs)

    assert scope["ok"] is True and plan["ok"] is True
    assert scope["search_seed_sha256"] == seed["sha256"]
    assert text not in json.dumps(scope, ensure_ascii=False)
    assert any(item["text"] == text for item in plan["queries"])
    assert plan["search_seed_sha256"] == seed["sha256"]


def test_nested_confirmed_provenance_builds_exact_reporting_and_risk_queries(
    modules: dict[str, ModuleType],
) -> None:
    plan = modules["planner"].build_search_query_plan(
        approved_work(modules),
        tenant_id="tenant-a",
        catalog_snapshot_id="snapshot-1",
        acl_context={"subject_id": "employee-1", "groups": ["engineering"]},
    )

    assert plan["ok"] is True
    by_kind = {item["kind"]: item["text"] for item in plan["queries"]}
    assert by_kind["exact"] == "Outlook"
    assert "주간 업무보고" in by_kind["reporting"]
    assert by_kind["risk"] == "외부 전송 전 사용자 승인"
    assert plan["confirmed_inputs"] == ["업무 메일"]


def test_design_scope_and_query_plan_preserve_revision_zero_and_reject_boolean(
    modules: dict[str, ModuleType],
) -> None:
    work = base_work()
    work["revision"] = 0
    preview = modules["preview"].build_work_preview_hash(
        {"ok": True, "work_definition": work, "graph_validation": {"valid": True}}
    )
    approved = preview["work_definition"]
    approved["approved_hash"] = preview["preview"]["preview_hash"]
    approved["status"] = "APPROVED"
    kwargs = {
        "tenant_id": "tenant-a",
        "catalog_snapshot_id": "snapshot-1",
        "acl_context": {"subject_id": "employee-1", "groups": ["engineering"]},
    }
    scope = modules["planner"].build_design_scope(approved, **kwargs)
    plan = modules["planner"].build_search_query_plan(approved, **kwargs)
    assert scope["ok"] is True and scope["work_definition_revision"] == 0
    assert plan["ok"] is True and plan["work_definition_revision"] == 0

    approved["revision"] = True
    rejected = modules["planner"].build_design_scope(approved, **kwargs)
    assert rejected["ok"] is False
    assert rejected["error"]["code"] == "WORK_DEFINITION_REVISION_INVALID"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ({"revision": 4.9}, "WORK_DEFINITION_REVISION_INVALID"),
        ({"revision": "4"}, "WORK_DEFINITION_REVISION_INVALID"),
        ({"schema_version": "evil/v1"}, "WORK_DEFINITION_SCHEMA_INVALID"),
        ({"tenant_id": None}, "WORK_DEFINITION_IDENTITY_INVALID"),
        ({"tenant_id": 123}, "WORK_DEFINITION_IDENTITY_INVALID"),
        ({"work_definition_id": "bad id"}, "WORK_DEFINITION_IDENTITY_INVALID"),
    ],
)
def test_design_scope_rejects_noncanonical_work_headers_without_hash_bypass(
    modules: dict[str, ModuleType], mutation: dict[str, Any], expected_code: str
) -> None:
    work = base_work()
    preview = modules["preview"].build_work_preview_hash(
        {"ok": True, "work_definition": work, "graph_validation": {"valid": True}}
    )
    approved = preview["work_definition"]
    approved["approved_hash"] = preview["preview"]["preview_hash"]
    approved["status"] = "APPROVED"
    approved.update(mutation)

    rejected = modules["planner"].build_design_scope(
        approved,
        tenant_id="tenant-a",
        catalog_snapshot_id="snapshot-1",
        acl_context={"subject_id": "employee-1", "groups": ["engineering"]},
    )

    assert rejected["ok"] is False
    assert rejected["error"]["code"] == expected_code


def test_design_scope_and_query_plan_reject_reapproved_semantic_secret_material(
    modules: dict[str, ModuleType],
) -> None:
    work = base_work()
    work["goal"]["value"] = "password=NeverStoreThis123456"
    preview = modules["preview"].build_work_preview_hash(
        {"ok": True, "work_definition": work, "graph_validation": {"valid": True}}
    )
    approved = preview["work_definition"]
    approved["approved_hash"] = preview["preview"]["preview_hash"]
    approved["status"] = "APPROVED"
    kwargs = {
        "tenant_id": "tenant-a",
        "catalog_snapshot_id": "snapshot-1",
        "acl_context": {"subject_id": "employee-1", "groups": ["engineering"]},
    }

    scope = modules["planner"].build_design_scope(approved, **kwargs)
    plan = modules["planner"].build_search_query_plan(approved, **kwargs)

    assert scope["ok"] is False and plan["ok"] is False
    assert scope["error"]["code"] == "WORK_DEFINITION_SECRET_MATERIAL_DETECTED"
    assert plan["error"]["code"] == "WORK_DEFINITION_SECRET_MATERIAL_DETECTED"
    assert "NeverStoreThis123456" not in json.dumps(scope, ensure_ascii=False)
    assert "NeverStoreThis123456" not in json.dumps(plan, ensure_ascii=False)


def test_design_scope_rejects_secret_literal_encoded_in_semantic_mapping_key(
    modules: dict[str, ModuleType],
) -> None:
    secret_key = "api_key=abcdefghijklmnop"
    work = base_work()
    work["goal"]["value"] = {secret_key: ""}
    preview = modules["preview"].build_work_preview_hash(
        {"ok": True, "work_definition": work, "graph_validation": {"valid": True}}
    )
    approved = preview["work_definition"]
    approved["approved_hash"] = preview["preview"]["preview_hash"]
    approved["status"] = "APPROVED"
    kwargs = {
        "tenant_id": "tenant-a",
        "catalog_snapshot_id": "snapshot-1",
        "acl_context": {"subject_id": "employee-1", "groups": ["engineering"]},
    }

    result = modules["planner"].build_design_scope(approved, **kwargs)
    assert result["ok"] is False
    assert result["error"]["code"] == "WORK_DEFINITION_SECRET_MATERIAL_DETECTED"
    assert secret_key not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize(
    ("snapshot_id", "acl_context", "expected_code"),
    [
        (
            "snapshot-1",
            {"subject_id": "Authorization: Bearer ABCDEFGHIJKLMNOPQRSTUVWXYZ", "groups": ["engineering"]},
            "ACL_CONTEXT_IDENTITY_INVALID",
        ),
        (
            "snapshot-1",
            {"subject_id": "employee-1", "groups": ["engineering", "sk-abcdefghijklmnop"]},
            "ACL_CONTEXT_SECRET_MATERIAL_DETECTED",
        ),
        (
            "Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ==",
            {"subject_id": "employee-1", "groups": ["engineering"]},
            "CATALOG_SNAPSHOT_ID_INVALID",
        ),
    ],
)
def test_design_scope_rejects_noncanonical_or_secret_acl_and_snapshot_identity(
    modules: dict[str, ModuleType],
    snapshot_id: str,
    acl_context: dict[str, Any],
    expected_code: str,
) -> None:
    result = modules["planner"].build_design_scope(
        approved_work(modules),
        tenant_id="tenant-a",
        catalog_snapshot_id=snapshot_id,
        acl_context=acl_context,
    )

    assert result["ok"] is False
    assert result["error"]["code"] == expected_code
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in json.dumps(result, ensure_ascii=False)


def test_design_scope_rejects_acl_unknown_fields_and_group_overflow(
    modules: dict[str, ModuleType],
) -> None:
    work = approved_work(modules)
    unknown = modules["planner"].build_design_scope(
        work,
        tenant_id="tenant-a",
        catalog_snapshot_id="snapshot-1",
        acl_context={"subject_id": "employee-1", "groups": [], "credential": "[REDACTED]"},
    )
    overflow = modules["planner"].build_design_scope(
        work,
        tenant_id="tenant-a",
        catalog_snapshot_id="snapshot-1",
        acl_context={"subject_id": "employee-1", "groups": [f"group-{index}" for index in range(101)]},
    )

    assert unknown["error"]["code"] == "ACL_CONTEXT_FIELDS_INVALID"
    assert overflow["error"]["code"] == "ACL_CONTEXT_IDENTITY_INVALID"


@pytest.mark.parametrize(
    ("acl", "expected_reason"),
    [
        (None, "SKILL_ACL_INVALID"),
        ({"visibility": "group", "groups": [], "subjects": []}, "SKILL_ACL_INVALID"),
        ({"visibility": "private", "groups": [], "subjects": []}, "SKILL_ACL_INVALID"),
    ],
)
def test_skill_registry_acl_contract_fails_closed(
    modules: dict[str, ModuleType], acl: Any, expected_reason: str
) -> None:
    result = modules["skill"].resolve_skill_context(
        sealed_scope(modules),
        {"skills": [skill_entry(acl=acl)]},
    )

    assert result["ok"] is True
    assert result["applied_skills"] == []
    assert result["rejected_skills"][0]["reason"] == expected_reason


def test_skill_registry_applies_valid_group_acl(modules: dict[str, ModuleType]) -> None:
    entry = skill_entry(
        acl={"visibility": "group", "groups": ["engineering"], "subjects": []}
    )
    result = modules["skill"].resolve_skill_context(
        sealed_scope(modules),
        {"skills": [entry]},
    )

    assert result["ok"] is True
    assert [item["skill_id"] for item in result["applied_skills"]] == ["mail-report-design"]
    assert result["rejected_skills"] == []


def test_skill_registry_schema_enforces_approved_at_format_and_bound() -> None:
    schema = json.loads((PROJECT_ROOT / "schemas" / "skill_registry.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    valid = skill_entry(acl={"visibility": "tenant", "groups": [], "subjects": []})
    validator.validate(valid)
    invalid_format = {**valid, "approved_at": "not-a-date"}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(invalid_format)
    oversized = {**valid, "approved_at": "2" * 65}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(oversized)


def test_skill_registry_excludes_secret_bearing_prompt_without_echo(
    modules: dict[str, ModuleType],
) -> None:
    entry = skill_entry(acl={"visibility": "tenant", "groups": [], "subjects": []})
    secret = "sk-ABCDEFGHIJKLMNOPQRSTUVWX"
    entry["prompt_text"] = f"업무보고 설계 규칙 api_key={secret}"
    entry["prompt_sha256"] = "sha256:" + hashlib.sha256(entry["prompt_text"].encode("utf-8")).hexdigest()

    result = modules["skill"].resolve_skill_context(
        sealed_scope(modules),
        {"skills": [entry]},
    )

    assert result["ok"] is True
    assert result["applied_skills"] == []
    assert result["rejected_skills"][0]["reason"] == "SKILL_SECRET_MATERIAL_DETECTED"
    assert secret not in json.dumps(result, ensure_ascii=False)


def test_skill_registry_bounds_and_sanitizes_rejected_identity_trace(
    modules: dict[str, ModuleType],
) -> None:
    secret = "api_key=abcdefghijklmnop"
    oversized = skill_entry(acl={"visibility": "tenant", "groups": [], "subjects": []})
    oversized["skill_id"] = "x" * 100_000
    secret_version = skill_entry(acl={"visibility": "tenant", "groups": [], "subjects": []})
    secret_version["version"] = secret

    result = modules["skill"].resolve_skill_context(
        sealed_scope(modules),
        {"skills": [oversized, secret_version]},
    )

    serialized = json.dumps(result, ensure_ascii=False)
    assert result["ok"] is True
    assert result["applied_skills"] == []
    assert len(serialized) < 5_000
    assert secret not in serialized
    assert result["rejected_skills"][0]["skill_id"] == "[INVALID]"
    assert result["rejected_skills"][1]["version"] == "[INVALID]"


def test_requested_skill_reference_requires_registry_hash(modules: dict[str, ModuleType]) -> None:
    entry = skill_entry(
        acl={"visibility": "tenant", "groups": [], "subjects": []}
    )
    result = modules["skill"].resolve_skill_context(
        sealed_scope(modules),
        {"skills": [entry]},
        requested_skill_refs={
            "requested_skills": [{"skill_id": entry["skill_id"], "version": entry["version"]}]
        },
    )

    assert result["ok"] is True
    assert result["applied_skills"] == []
    assert result["rejected_skills"][0]["reason"] == "REQUESTED_SKILL_HASH_REQUIRED"


def test_duplicate_skill_identity_is_rejected(modules: dict[str, ModuleType]) -> None:
    entry = skill_entry(
        acl={"visibility": "tenant", "groups": [], "subjects": []}
    )
    result = modules["skill"].resolve_skill_context(
        sealed_scope(modules),
        {"skills": [entry, copy.deepcopy(entry)]},
    )

    assert result["ok"] is True
    assert result["applied_skills"] == []
    assert [item["reason"] for item in result["rejected_skills"]] == [
        "DUPLICATE_SKILL_IDENTITY",
        "DUPLICATE_SKILL_IDENTITY",
    ]


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ({"approved_by": ""}, "SKILL_APPROVAL_EVIDENCE_INVALID"),
        ({"approved_at": "2026-08-28T09:00:00"}, "SKILL_APPROVAL_EVIDENCE_INVALID"),
        ({"trigger_rules": []}, "SKILL_RULE_CONTRACT_INVALID"),
        (
            {"trigger_rules": [{"kind": "contains", "value": "메일", "values": ["보고"]}]},
            "SKILL_RULE_CONTRACT_INVALID",
        ),
        ({"trigger_rules": [{"kind": "regex", "value": ".*"}]}, "SKILL_RULE_CONTRACT_INVALID"),
        ({"unexpected_policy": "allow-all"}, "SKILL_REGISTRY_CONTRACT_INVALID"),
        ({"match_reason": "x" * 501}, "SKILL_REGISTRY_CONTRACT_INVALID"),
        ({"version": 1}, "INVALID_REGISTRY_IDENTITY"),
        ({"status": "ACTIVE"}, "INVALID_REGISTRY_STATUS"),
    ],
)
def test_skill_registry_rejects_missing_evidence_and_ambiguous_rules(
    modules: dict[str, ModuleType], mutation: dict[str, Any], expected_reason: str
) -> None:
    entry = skill_entry(acl={"visibility": "tenant", "groups": [], "subjects": []})
    entry.update(mutation)

    result = modules["skill"].resolve_skill_context(
        sealed_scope(modules),
        {"skills": [entry]},
    )

    assert result["ok"] is True
    assert result["applied_skills"] == []
    assert result["rejected_skills"][0]["reason"] == expected_reason
