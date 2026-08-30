from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATHS = {
    "work_definition": PROJECT_ROOT / "samples" / "approved_work_definition.json",
    "blueprint": PROJECT_ROOT / "samples" / "approved_agent_blueprint.json",
    "terminal": PROJECT_ROOT / "samples" / "agent_blueprint_terminal.json",
    "candidate_context": PROJECT_ROOT / "samples" / "candidate_context.json",
    "report_handoff": PROJECT_ROOT / "samples" / "f20_report_handoff.json",
}
COMPONENT_PATHS = {
    "graph": PROJECT_ROOT / "components" / "work_definition" / "16_work_graph_normalizer.py",
    "preview": PROJECT_ROOT / "components" / "work_definition" / "17_work_preview_hasher.py",
    "skill": PROJECT_ROOT / "components" / "hybrid_retrieval" / "19_skill_context_resolver.py",
    "scope": PROJECT_ROOT / "components" / "hybrid_retrieval" / "20_search_query_planner.py",
    "candidate": PROJECT_ROOT / "components" / "hybrid_retrieval" / "22_candidate_context_builder.py",
    "normalizer": PROJECT_ROOT / "components" / "agent_blueprint" / "23_agent_blueprint_normalizer.py",
    "ports": PROJECT_ROOT / "components" / "agent_blueprint" / "24_port_contract_validator.py",
    "readiness": PROJECT_ROOT / "components" / "agent_blueprint" / "25_blueprint_readiness_classifier.py",
    "generation": PROJECT_ROOT / "components" / "agent_blueprint" / "26_component_generation_prompt_builder.py",
    "report_handoff": PROJECT_ROOT / "components" / "agent_blueprint" / "38_f20_report_handoff_builder.py",
}


def _load_component(name: str, path: Path) -> ModuleType:
    module_name = f"business_work_design_sample_{name}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Component module을 load할 수 없습니다: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _modules() -> dict[str, ModuleType]:
    return {name: _load_component(name, path) for name, path in COMPONENT_PATHS.items()}


def _fact(value: Any, *turn_ids: str, revision: int = 4) -> dict[str, Any]:
    return {
        "value": value,
        "status": "confirmed",
        "evidence_turn_ids": list(turn_ids),
        "confidence": 1.0,
        "last_updated_revision": revision,
    }


def _item(item_id: str, *, turn_ids: tuple[str, ...], **fields: Any) -> dict[str, Any]:
    return {
        "id": item_id,
        **fields,
        "provenance": {
            "status": "confirmed",
            "evidence_turn_ids": list(turn_ids),
            "confidence": 1.0,
            "last_updated_revision": 4,
        },
    }


def source_work_definition() -> dict[str, Any]:
    """Return the pre-approval canonical WorkDefinition used by the sample pipeline."""

    return {
        "schema_version": "work-definition/v1",
        "tenant_id": "default",
        "owner_id": "employee-demo",
        "session_id": "session-weekly-email-report",
        "channel_mode": "native_hitl",
        "work_definition_id": "wd-weekly-email-report",
        "revision": 4,
        "status": "READY_FOR_REVIEW",
        "title": _fact("메일 기반 주간 업무보고 작성", "turn-001"),
        "goal": _fact(
            "지난 한 주의 업무 메일을 근거로 주간 업무보고 초안을 만들고 담당자 검토 후 공유한다.",
            "turn-001",
            "turn-003",
        ),
        "trigger": _fact("매주 금요일 15시 또는 담당자의 수동 실행", "turn-002"),
        "frequency_volume": _fact(
            {"frequency": "weekly", "estimated_messages_per_run": 120},
            "turn-002",
        ),
        "sla": _fact(
            {"draft_due": "금요일 16:00", "approval_due": "금요일 17:00"},
            "turn-003",
        ),
        "automation_intent": _fact("semi_automatic", "turn-003"),
        "scope_in": [
            _item(
                "scope-in-weekly-mail",
                turn_ids=("turn-001",),
                name="승인된 기간의 업무 메일 수집과 주간 보고서 초안 작성",
            )
        ],
        "scope_out": [
            _item(
                "scope-out-unapproved-publish",
                turn_ids=("turn-003",),
                name="사용자 승인 없는 보고서 게시 또는 외부 전송",
            )
        ],
        "actors": [
            _item(
                "employee",
                turn_ids=("turn-001",),
                name="업무 담당자",
                role="보고서 검토 및 승인",
            ),
            _item(
                "team-lead",
                turn_ids=("turn-003",),
                name="팀 리더",
                role="승인된 보고서 수신",
            ),
        ],
        "systems": [
            _item(
                "outlook",
                turn_ids=("turn-001",),
                name="사내 Outlook",
                purpose="기간 내 메일 조회",
            ),
            _item(
                "report-portal",
                turn_ids=("turn-003",),
                name="업무보고 포털",
                purpose="승인본 게시",
            ),
        ],
        "inputs": [
            _item(
                "mail-window",
                turn_ids=("turn-002",),
                name="조회 기간",
                data_type="DateRange",
                required=True,
            ),
            _item(
                "mail-account",
                turn_ids=("turn-001",),
                name="사내 메일 계정 참조",
                data_type="CredentialRef",
                required=True,
            ),
        ],
        "steps": [
            _item(
                "request",
                turn_ids=("turn-002",),
                step_id="request",
                sequence=1,
                title="보고 기간 확인",
                capability="승인된 조회 기간과 실행 요청을 확인한다.",
                owner="업무 담당자",
            ),
            _item(
                "collect",
                turn_ids=("turn-001",),
                step_id="collect",
                sequence=2,
                title="관련 메일 검색",
                capability="기간 내 업무 메일을 검색하고 원문 참조를 보존한다.",
                owner="업무 담당자",
            ),
            _item(
                "draft",
                turn_ids=("turn-001",),
                step_id="draft",
                sequence=3,
                title="업무 항목 분류 및 초안 작성",
                capability="메일을 업무, 성과, 이슈로 정리하고 근거를 연결한다.",
                owner="업무 담당자",
            ),
            _item(
                "review",
                turn_ids=("turn-003",),
                step_id="review",
                sequence=4,
                title="근거와 민감정보 검토",
                capability="담당자가 근거, 누락, 민감정보를 확인하고 승인한다.",
                owner="업무 담당자",
            ),
            _item(
                "share",
                turn_ids=("turn-003",),
                step_id="share",
                sequence=5,
                title="팀 리더에게 공유",
                capability="승인된 보고서를 사내 포털에 게시하고 링크를 전달한다.",
                owner="업무 담당자",
            ),
        ],
        "decisions": [
            _item(
                "sensitive-check",
                turn_ids=("turn-003",),
                decision_id="sensitive-check",
                question="민감정보 또는 근거가 부족한 문장이 있는가?",
                owner="업무 담당자",
            )
        ],
        "outputs": [
            _item(
                "weekly-report",
                turn_ids=("turn-003",),
                name="승인된 주간 업무보고",
                data_type="Document",
            )
        ],
        "exceptions": [
            _item(
                "mail-auth-expired",
                turn_ids=("turn-003",),
                condition="메일 인증 만료",
                handling="재인증 후 다시 실행",
            )
        ],
        "pains": [
            _item(
                "manual-search",
                turn_ids=("turn-001",),
                description="메일을 하나씩 열어 업무 항목을 옮기는 데 시간이 많이 든다.",
            ),
            _item(
                "missing-evidence",
                turn_ids=("turn-001",),
                description="초안 문장과 원본 메일의 연결 근거를 다시 찾기 어렵다.",
            ),
        ],
        "risks_controls": [
            _item(
                "sensitive-information",
                turn_ids=("turn-003",),
                name="외부 전송 전 사용자 승인",
                risk="민감정보가 보고서에 포함될 수 있음",
                control="게시 전 담당자 승인과 민감정보 확인을 필수화",
            ),
            _item(
                "mail-read-failure",
                turn_ids=("turn-003",),
                name="메일 조회 실패 표시",
                risk="메일 조회 실패를 성공으로 오인",
                control="조회 건수와 실패 사유를 보고서에 표시",
            ),
        ],
        "constraints": [
            _item(
                "internal-network-only",
                turn_ids=("turn-003",),
                name="사내망에서만 메일과 보고서 API에 접근",
            ),
            _item(
                "approval-before-publish",
                turn_ids=("turn-003",),
                name="사용자 승인 없이 외부 전송 금지",
            ),
        ],
        "success_criteria": [
            _item(
                "evidence-coverage",
                turn_ids=("turn-003",),
                name="메일 원문 링크를 가진 업무 항목 비율 100%",
            ),
            _item(
                "no-unapproved-publish",
                turn_ids=("turn-003",),
                name="승인 전 게시 시도 0건",
            ),
        ],
        "assumptions": [
            _item(
                "mail-filter-support",
                turn_ids=("turn-003",),
                name="메일 API가 기간과 발신자 필터를 지원한다.",
            )
        ],
        "unresolved": [],
        "as_is_graph": {
            "schema_version": "work-graph/v1",
            "nodes": [
                {
                    "id": "as-start", "kind": "start", "label": "금요일 보고 준비 시작", "detail_ref": "detail-as-start",
                    "current_work": "담당자가 금요일에 직접 조회 기간과 보고 대상 범위를 정한다.",
                    "problems": ["실행 기준이 사람마다 달라 누락 범위가 생길 수 있다."],
                    "improvement": "정기 실행 또는 수동 요청을 하나의 승인된 실행 계약으로 받는다.",
                },
                {
                    "id": "as-search", "kind": "task", "label": "Outlook 메일 수동 검색", "detail_ref": "detail-as-search",
                    "current_work": "담당자가 Outlook에서 기간과 발신자를 바꿔가며 업무 메일을 직접 찾는다.",
                    "problems": ["메일을 하나씩 열어야 하며 원문 근거를 다시 찾는 시간이 길다."],
                    "improvement": "승인된 조회 범위만 사용하는 standalone 수집 Component로 원문 참조를 함께 보존한다.",
                },
                {
                    "id": "as-draft", "kind": "task", "label": "업무보고 수동 작성", "detail_ref": "detail-as-draft",
                    "current_work": "검색한 메일 내용을 사람이 업무, 성과, 이슈 항목으로 옮겨 적는다.",
                    "problems": ["초안 문장과 원본 메일의 연결 근거가 끊기고 반복 편집이 많다."],
                    "improvement": "근거 우선 요약 Skill을 적용한 신규 adapter가 문장별 source_ref를 유지한다.",
                },
                {
                    "id": "as-review", "kind": "human_review", "label": "민감정보와 내용 검토", "detail_ref": "detail-as-review",
                    "current_work": "담당자가 완성된 문서를 다시 읽으며 누락과 민감정보를 확인한다.",
                    "problems": ["검토 여부와 수정 사유가 구조화되어 남지 않는다."],
                    "improvement": "게시 직전 Human 승인 gate에서 승인, 수정 요청, 거절을 명시적으로 기록한다.",
                },
                {
                    "id": "as-end", "kind": "end", "label": "업무보고 공유 완료", "detail_ref": "detail-as-end",
                    "current_work": "승인된 문서를 포털에 올리고 링크를 별도로 전달한다.",
                    "problems": ["승인 전 게시 여부와 게시 결과를 일관되게 추적하기 어렵다."],
                    "improvement": "승인 상태가 확인된 경우에만 Report API가 게시하고 감사 추적 ID와 링크를 반환한다.",
                },
            ],
            "edges": [
                {"id": "as-e1", "source": "as-start", "target": "as-search", "branch_label": "작성 시작"},
                {"id": "as-e2", "source": "as-search", "target": "as-draft", "branch_label": "검색 결과"},
                {"id": "as-e3", "source": "as-draft", "target": "as-review", "branch_label": "초안 검토"},
                {"id": "as-e4", "source": "as-review", "target": "as-end", "branch_label": "승인 후 공유"},
            ],
            "loop_policy": None,
        },
        "source_requests": [
            {
                "turn_id": "turn-001",
                "text": "지난 한 주의 업무 메일로 주간 업무보고 초안을 만들고 싶다.",
            }
        ],
        "processed_answer_batches": ["answer-batch-001"],
        "extensions": {},
        "preview_hash": None,
        "approved_hash": None,
    }


def _retrieval_result(scope: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "status": "COMPLETED",
        "tenant_id": scope["tenant_id"],
        "snapshot_id": scope["catalog_snapshot_id"],
        "work_definition_id": scope["work_definition_id"],
        "work_definition_revision": scope["work_definition_revision"],
        "approved_hash": scope["approved_hash"],
        "design_scope_sha256": scope["design_scope_sha256"],
        "query_plan_sha256": "sha256:" + "1" * 64,
        "provider_mode": "sample_exact_lexical_vector_fusion",
        "candidates": [
            {
                "asset_id": "47d41a8d-9208-48c2-b79b-9d84d7ce199d",
                "version": "v1.0.0",
                "asset_type": "flow",
                "title": "email기반 자동 업무보고 만들기",
                "description": "메일을 수집해 업무보고를 만드는 기존 Flow 후보",
                "technical_contract_status": "metadata_only",
                "ports": {"inputs": [], "outputs": []},
            },
            {
                "asset_id": "e21931b2-1093-4f32-b55a-36ac66ef5b59",
                "version": "v1.0.1",
                "asset_type": "component",
                "title": "Outlook일정가지고오기 component(GetScheduleComponent)",
                "description": "Outlook 연계 방식 참고용 Component 후보",
                "technical_contract_status": "metadata_only",
                "ports": {"inputs": [], "outputs": []},
            },
        ],
        "retrieval_trace": {
            "exact_used": True,
            "lexical_used": True,
            "vector_used": True,
            "fusion": "weighted_rrf",
            "silent_fallback_used": False,
        },
    }


def _skill_registry() -> dict[str, Any]:
    prompt_text = "업무보고 문장은 원본 근거를 보존하고 게시 전 담당자 승인을 거친다."
    prompt_sha256 = "sha256:" + hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
    skill = {
        "tenant_id": "default",
        "skill_id": "skill-evidence-first-summarization",
        "name": "근거 우선 요약",
        "version": "1.2.0",
        "prompt_text": prompt_text,
        "prompt_sha256": prompt_sha256,
        "trigger_rules": [{"kind": "contains", "value": "업무보고"}],
        "near_miss_rules": [],
        "status": "active",
        "acl": {"visibility": "group", "groups": ["business-automation"], "subjects": []},
        "approved_by": "sample-security-reviewer",
        "approved_at": "2026-08-28T09:00:00+09:00",
        "match_reason": "업무보고 문장마다 원본 메일 근거를 유지해야 한다.",
        "target_stage": "report-draft-adapter",
    }
    return {"skills": [skill]}


def _port(port_id: str, display_name: str, semantic_role: str, *, required: bool = True) -> dict[str, Any]:
    return {
        "port_id": port_id,
        "display_name": display_name,
        "data_type": "Data",
        "semantic_role": semantic_role,
        "cardinality": "one",
        "required": required,
    }


def _generation_contract(
    *,
    component_filename: str,
    class_name: str,
    display_name: str,
    responsibility: str,
    input_name: str,
    input_role: str,
    output_name: str,
    output_role: str,
    secret_inputs: list[dict[str, Any]],
    dependencies: list[str],
    error_codes: list[str],
) -> dict[str, Any]:
    return {
        "component_filename": component_filename,
        "class_name": class_name,
        "display_name": display_name,
        "responsibility": responsibility,
        "input_contract": {input_name: {"type": "Data", "semantic_role": input_role, "required": True}},
        "output_contract": {output_name: {"type": "Data", "semantic_role": output_role}},
        "secret_inputs": secret_inputs,
        "dependencies": dependencies,
        "timeout_limits": {"execution_seconds": 10, "max_items": 200, "retry_count": 0},
        "error_codes": error_codes,
        "deployment_mode": "inline_bounded",
        "prompt_pack": "CCP-WORK",
    }


def _blueprint_draft(scope: dict[str, Any], skill_context: dict[str, Any]) -> dict[str, Any]:
    skill = skill_context["applied_skills"][0]
    return {
        "blueprint_id": "bp-weekly-email-report-v1",
        "work_definition_id": scope["work_definition_id"],
        "work_definition_revision": scope["work_definition_revision"],
        "approved_hash": scope["approved_hash"],
        "catalog_snapshot_id": scope["catalog_snapshot_id"],
        "pattern": "deterministic_sequential",
        "pattern_reason": "메일 조회와 초안 작성은 순차 실행하고 게시 직전에 사람의 승인을 강제해야 한다.",
        "roles": [
            {"role_id": "requester", "name": "업무 담당자", "responsibility": "실행, 검토, 승인"},
            {"role_id": "recipient", "name": "팀 리더", "responsibility": "승인본 확인"},
        ],
        "nodes": [
            {
                "node_id": "agent-start",
                "node_type": "start",
                "title": "정기 또는 수동 실행",
                "responsibility": "승인된 실행 요청과 조회 기간을 Flow에 전달한다.",
                "current_work": "담당자가 매주 조회 기간과 실행 시점을 수동으로 정한다.",
                "problems": ["실행 조건이 비정형이라 재현성이 낮다."],
                "improvement": "정기 또는 수동 실행 요청을 동일한 구조화 입력으로 만든다.",
                "implementation_source": "builtin",
                "builtin_satisfies": True,
                "reuse_decision_reason": "Langflow 기본 입력 요소로 충족한다.",
                "runtime_validation_status": "verified_runtime",
                "inputs": [],
                "outputs": [_port("run-request", "실행 요청", "run_request")],
                "applied_skills": [],
            },
            {
                "node_id": "mail-collector",
                "node_type": "system_call",
                "title": "Outlook 업무 메일 수집",
                "responsibility": "승인된 기간으로 업무 메일을 조회하고 원문 참조를 보존한다.",
                "current_work": "Outlook에서 업무 메일을 사람이 직접 검색하고 원문을 열어 확인한다.",
                "problems": ["수동 검색 시간이 길고 보고 문장과 원본 메일의 연결이 끊긴다."],
                "improvement": "조회 범위, 권한, timeout을 제한한 standalone 수집기로 원문 참조를 보존한다.",
                "implementation_source": "new_standalone_component",
                "reuse_decision_reason": "검색된 메일 보고 Flow는 metadata_only라 실행 계약으로 채택하지 않고 신규 standalone 계약을 생성한다.",
                "runtime_validation_status": "unverified",
                "inputs": [_port("run-request", "실행 요청", "run_request")],
                "outputs": [_port("mail-messages", "메일 문서", "mail_documents")],
                "required_secrets": [
                    {"name": "mail_api_credential_ref", "required": True, "configured": False}
                ],
                "required_permissions": [
                    {"name": "mail.read", "required": True, "granted": False}
                ],
                "network_zone": "internal",
                "timeout_policy": {"execution_seconds": 10, "retry_count": 0},
                "failure_policy": {"fail_closed": True, "error_code": "MAIL_COLLECTION_FAILED"},
                "applied_skills": [],
                "generation_contract": _generation_contract(
                    component_filename="40_outlook_mail_collector.py",
                    class_name="OutlookMailCollectorComponent",
                    display_name="Outlook Mail Collector",
                    responsibility="승인된 조회 범위로 업무 메일을 수집하고 원문 참조가 있는 Data를 반환한다.",
                    input_name="run_request",
                    input_role="run_request",
                    output_name="mail_messages",
                    output_role="mail_documents",
                    secret_inputs=[{"name": "mail_api_credential_ref", "required": True}],
                    dependencies=["httpx>=0.27,<1"],
                    error_codes=["INVALID_RUN_REQUEST", "MAIL_AUTH_REQUIRED", "MAIL_COLLECTION_FAILED"],
                ),
                "tests": [
                    {"test_id": "mail-empty", "description": "빈 조회 결과를 정상 empty data로 구분한다."}
                ],
            },
            {
                "node_id": "report-draft-adapter",
                "node_type": "task",
                "title": "근거 연결형 보고서 초안 생성",
                "responsibility": "메일 문서를 업무, 성과, 이슈로 정규화하고 각 문장에 원문 참조를 연결한다.",
                "current_work": "담당자가 메일 내용을 업무보고 양식에 복사하고 다시 분류한다.",
                "problems": ["반복 편집이 많고 초안의 근거 누락을 뒤늦게 찾는다."],
                "improvement": "승인 Skill과 신규 adapter가 source_ref가 있는 초안을 결정론적 계약으로 만든다.",
                "implementation_source": "new_standalone_component",
                "reuse_decision_reason": "승인 근거 계약을 충족하는 검증된 Component가 없어 신규 standalone Component가 필요하다.",
                "runtime_validation_status": "unverified",
                "inputs": [_port("mail-messages", "메일 문서", "mail_documents")],
                "outputs": [_port("draft-report", "보고서 초안", "weekly_report_draft")],
                "applied_skills": [skill],
                "generation_contract": _generation_contract(
                    component_filename="41_evidence_linked_report_adapter.py",
                    class_name="EvidenceLinkedReportAdapterComponent",
                    display_name="Evidence Linked Report Adapter",
                    responsibility="메일 문서를 source_ref가 보존된 주간 보고서 초안으로 변환한다.",
                    input_name="mail_messages",
                    input_role="mail_documents",
                    output_name="weekly_report_draft",
                    output_role="weekly_report_draft",
                    secret_inputs=[],
                    dependencies=[],
                    error_codes=["INVALID_MAIL_MESSAGES", "OUTPUT_LIMIT_EXCEEDED"],
                ),
                "tests": [
                    {"test_id": "evidence-link", "description": "각 보고 문장에 source_ref가 남는지 검증한다."}
                ],
            },
            {
                "node_id": "human-approval",
                "node_type": "human_review",
                "title": "민감정보 및 내용 승인",
                "responsibility": "담당자가 근거, 누락, 민감정보를 확인하고 승인 또는 수정 요청을 선택한다.",
                "current_work": "완성 문서를 읽고 메신저나 구두로 수정 여부를 전달한다.",
                "problems": ["승인 상태와 수정 사유가 Flow 상태에 연결되지 않는다."],
                "improvement": "Human gate가 승인, 수정 요청, 거절을 구조화하고 승인 전 게시를 차단한다.",
                "implementation_source": "human_task",
                "reuse_decision_reason": "게시 전 최종 책임은 사람이 가져야 한다.",
                "inputs": [_port("draft-report", "보고서 초안", "weekly_report_draft")],
                "outputs": [_port("approved-report", "승인된 보고서", "approved_weekly_report")],
                "applied_skills": [],
            },
            {
                "node_id": "report-publisher",
                "node_type": "system_call",
                "title": "승인 보고서 게시",
                "responsibility": "승인된 보고서를 인증된 사내 Report API에 게시하고 조회 링크를 반환한다.",
                "current_work": "담당자가 포털에 문서를 직접 올리고 공유 링크를 복사한다.",
                "problems": ["게시 성공 여부와 승인본의 일치 여부를 감사하기 어렵다."],
                "improvement": "actor/tenant와 content hash가 묶인 Report API가 승인본만 저장하고 링크를 반환한다.",
                "implementation_source": "companion_service",
                "reuse_decision_reason": "HTML 영속 저장과 인증된 배포는 Langflow 실행 수명 밖의 서비스 책임이다.",
                "inputs": [_port("approved-report", "승인된 보고서", "approved_weekly_report")],
                "outputs": [_port("report-url", "보고서 URL", "report_link")],
                "applied_skills": [],
            },
            {
                "node_id": "agent-end",
                "node_type": "end",
                "title": "보고서 링크 전달",
                "responsibility": "게시 결과와 감사 추적 ID를 사용자에게 반환한다.",
                "current_work": "게시 후 링크와 완료 여부를 사람이 별도로 알린다.",
                "problems": ["게시 실패가 완료로 오인될 수 있다."],
                "improvement": "성공 envelope의 보고서 링크와 감사 추적 ID만 최종 출력으로 전달한다.",
                "implementation_source": "builtin",
                "builtin_satisfies": True,
                "reuse_decision_reason": "Langflow 기본 출력 요소로 충족한다.",
                "runtime_validation_status": "verified_runtime",
                "inputs": [_port("report-url", "보고서 URL", "report_link")],
                "outputs": [],
                "applied_skills": [],
            },
        ],
        "edges": [
            {"edge_id": "agent-e1", "source_node_id": "agent-start", "source_port_id": "run-request", "target_node_id": "mail-collector", "target_port_id": "run-request", "label": "실행 요청", "is_default": True},
            {"edge_id": "agent-e2", "source_node_id": "mail-collector", "source_port_id": "mail-messages", "target_node_id": "report-draft-adapter", "target_port_id": "mail-messages", "label": "메일 원문과 메타데이터", "is_default": True},
            {"edge_id": "agent-e3", "source_node_id": "report-draft-adapter", "source_port_id": "draft-report", "target_node_id": "human-approval", "target_port_id": "draft-report", "label": "초안 검토 요청", "is_default": True},
            {"edge_id": "agent-e4", "source_node_id": "human-approval", "source_port_id": "approved-report", "target_node_id": "report-publisher", "target_port_id": "approved-report", "label": "승인된 경우만 게시", "condition": "approval_status == 'APPROVED'", "is_default": False},
            {"edge_id": "agent-e5", "source_node_id": "report-publisher", "source_port_id": "report-url", "target_node_id": "agent-end", "target_port_id": "report-url", "label": "게시 링크", "is_default": True},
        ],
        "human_gates": [
            {"gate_id": "publish-approval", "node_id": "human-approval", "required": True}
        ],
        "secrets_permissions": [
            {"node_id": "mail-collector", "declaration": "mail_api_credential_ref", "permission": "mail.read"}
        ],
        "failure_policy": {"fail_closed": True, "publish_without_approval": False},
        "observability": {"audit_fields": ["trace_id", "status", "error_code"]},
        "tests": [
            {"test_id": "e2e-approved-path", "description": "승인된 초안만 Report API에 게시되는지 검증"},
            {"test_id": "e2e-rejected-path", "description": "거절 또는 수정 요청 시 게시 호출이 발생하지 않는지 검증"},
        ],
        "assumptions": [
            {"id": "report-api-contract", "text": "Report API의 인증 계약은 배포 전 runtime 검증한다."}
        ],
        "unresolved": [],
    }


def build_sample_documents() -> dict[str, dict[str, Any]]:
    modules = _modules()

    graph_result = modules["graph"].normalize_work_graph(source_work_definition())
    if graph_result.get("ok") is not True:
        raise RuntimeError(f"Work graph normalization failed: {graph_result.get('error')}")
    preview_result = modules["preview"].build_work_preview_hash(graph_result)
    if preview_result.get("ok") is not True:
        raise RuntimeError(f"Work preview hash failed: {preview_result.get('error')}")
    approved_work = preview_result["work_definition"]
    approved_work["approved_hash"] = preview_result["preview"]["preview_hash"]
    approved_work["status"] = "APPROVED"
    confirmed_preview = modules["preview"].build_work_preview_hash(
        {
            "ok": True,
            "work_definition": approved_work,
            "graph_validation": graph_result["graph_validation"],
        }
    )
    if confirmed_preview.get("ok") is not True or confirmed_preview.get("status") != "APPROVED":
        raise RuntimeError("Approved WorkDefinition did not retain its Component 17 semantic hash.")
    approved_work = confirmed_preview["work_definition"]

    scope = modules["scope"].build_design_scope(
        approved_work,
        tenant_id="default",
        catalog_snapshot_id="catalog-snapshot-20260827",
        acl_context={"subject_id": "employee-demo", "groups": ["business-automation"]},
        design_prompt="메일 근거를 보존하고 게시 전 사람의 승인을 강제한다.",
    )
    if scope.get("ok") is not True:
        raise RuntimeError(f"Design scope sealing failed: {scope.get('error')}")
    candidates = modules["candidate"].build_candidate_context(_retrieval_result(scope))
    if candidates.get("ok") is not True:
        raise RuntimeError(f"Candidate context build failed: {candidates.get('error')}")
    candidates["trace_id"] = "trace-sample-candidate-context"
    skills = modules["skill"].resolve_skill_context(scope, _skill_registry())
    if skills.get("ok") is not True or len(skills.get("applied_skills", [])) != 1:
        raise RuntimeError(f"Approved Skill resolution failed: {skills.get('error') or skills.get('rejected_skills')}")
    draft = _blueprint_draft(scope, skills)

    normalized = modules["normalizer"].normalize_agent_blueprint_from_scope(
        draft,
        scope,
        candidates,
        skills,
    )
    if normalized.get("ok") is not True:
        raise RuntimeError(f"Blueprint normalization failed: {normalized.get('error')}")
    port_validated = modules["ports"].validate_port_contracts(normalized)
    if port_validated.get("ok") is not True or port_validated.get("validation_issues"):
        raise RuntimeError(f"Port contract validation failed: {port_validated.get('validation_issues')}")
    classified = modules["readiness"].classify_blueprint_readiness(port_validated)
    if classified.get("ok") is not True:
        raise RuntimeError(f"Blueprint readiness classification failed: {classified.get('error')}")
    terminal = modules["generation"].build_component_generation_prompt(
        classified,
        target_node_id="",
    )
    if terminal.get("ok") is not True:
        raise RuntimeError(f"Generation request build failed: {terminal.get('error')}")
    if terminal.get("generation_request_count") != 2:
        raise RuntimeError("The sample must create exactly two standalone generation requests.")
    terminal["trace_id"] = "trace-sample-f20-terminal"

    report_handoff = modules["report_handoff"].build_f20_report_handoff(scope, candidates, terminal)
    if report_handoff.get("ok") is not True or report_handoff.get("status") != "COMPLETED":
        raise RuntimeError(f"F20 report handoff build failed: {report_handoff.get('error')}")
    report_handoff["trace_id"] = "trace-sample-f20-report-handoff"

    return {
        "work_definition": approved_work,
        "blueprint": terminal["blueprint"],
        "terminal": terminal,
        "candidate_context": candidates,
        "report_handoff": report_handoff,
    }


def _serialized(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False, allow_nan=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build deterministic canonical sample contracts through Components 16/17/19/20/22/23/24/25/26/38.")
    parser.add_argument("--check", action="store_true", help="Do not write; fail when committed samples differ from the pipeline output.")
    args = parser.parse_args()
    documents = build_sample_documents()
    mismatches: list[str] = []
    for name, path in SAMPLE_PATHS.items():
        expected = _serialized(documents[name])
        if args.check:
            actual = path.read_text(encoding="utf-8") if path.exists() else ""
            if actual != expected:
                mismatches.append(str(path.relative_to(PROJECT_ROOT)))
        else:
            path.write_text(expected, encoding="utf-8", newline="\n")
    if mismatches:
        print("Sample contracts are stale: " + ", ".join(mismatches))
        return 1
    print("Sample contracts are current: " + ", ".join(str(path.relative_to(PROJECT_ROOT)) for path in SAMPLE_PATHS.values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
