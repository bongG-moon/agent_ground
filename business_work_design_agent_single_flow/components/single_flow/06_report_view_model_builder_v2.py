from __future__ import annotations

"""Project a validated business-design result into the renderer's v2 view model.

This custom component is self-contained by design.  It only projects the
normalizer result; it never calls an LLM, a database, or another local module.
"""

import datetime as _dt
import hashlib
import json
import math
import re
import uuid
from decimal import Decimal
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, Output
from lfx.schema import Data


_SCHEMA = "report-view-model/v2"
_RENDERER = "business-report-renderer.v2"
_RESULT_SCHEMA = "business-design-result/v2"
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_SECRET_KEY = re.compile(r"(?i)(?:api[_-]?key|authorization|bearer|client[_-]?secret|cookie|credential|password|passwd|private[_-]?key|secret|token)")
_SECRET_VALUE = re.compile(r"(?i)(?:\b(?:api[_-]?key|password|passwd|secret|access[_-]?token|authorization)\s*[:=]\s*[^\s,;]{8,}|\bbearer\s+\S{8,}|\bsk-[A-Za-z0-9_-]{16,}\b|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")


def _safe_json(value: Any, path: str = "$") -> Any:
    data = getattr(value, "data", None)
    if isinstance(data, (dict, list)):
        return _safe_json(data, path)
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"[REPORT_VIEW_MODEL_INVALID] {path}에 유한하지 않은 숫자가 있습니다.")
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"[REPORT_VIEW_MODEL_INVALID] {path}에 유한하지 않은 숫자가 있습니다.")
        return value
    if isinstance(value, (tuple, set)):
        return [_safe_json(item, f"{path}[]") for item in value]
    if isinstance(value, list):
        return [_safe_json(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, dict):
        return {str(key): _safe_json(item, f"{path}.{key}") for key, item in value.items()}
    raise ValueError(f"[REPORT_VIEW_MODEL_INVALID] {path}의 값 형식을 처리할 수 없습니다.")


def _canonical(value: Any) -> str:
    return json.dumps(_safe_json(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text(value: Any, limit: int = 20_000) -> str:
    return str(value or "").replace("\x00", "").strip()[:limit]


def _strings(value: Any, limit: int = 100) -> list[str]:
    return [_text(item, 5_000) for item in value[:limit] if _text(item, 5_000)] if isinstance(value, list) else []


def _contains_secret(value: Any) -> bool:
    if isinstance(value, dict):
        return any((_SECRET_KEY.search(str(key)) and item not in (None, "", False, "[REDACTED]")) or _contains_secret(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    return isinstance(value, str) and value != "[REDACTED]" and bool(_SECRET_VALUE.search(value))


def _payload(value: Any) -> dict[str, Any]:
    raw = getattr(value, "data", None)
    if isinstance(raw, dict):
        value = raw
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("[REPORT_VIEW_MODEL_INVALID] 설계 결과가 JSON object가 아닙니다. 05 node 연결을 확인해 주세요.") from exc
    if not isinstance(value, dict):
        raise ValueError("[REPORT_VIEW_MODEL_INVALID] 설계 결과가 없습니다. 05 node 연결을 확인해 주세요.")
    return _safe_json(value, "design_result")


def _fact(label: str, value: Any, source: str = "analysis") -> dict[str, str]:
    return {"label": _text(label, 120), "value": _text(value, 5_000), "source": source if source in {"description", "analysis", "catalog", "assumption"} else "analysis"}


def _block(summary: Any = "", facts: list[dict[str, str]] | None = None, bullets: Any = None) -> dict[str, Any]:
    return {"summary": _text(summary), "facts": (facts or [])[:100], "bullets": _strings(bullets)}


def _catalog_url(asset_id: str, asset_type: str) -> str:
    return f"https://agent-hub.skhynix.com/#/{'flow' if asset_type == 'flow' else 'component'}/{asset_id}"


def _safe_catalog_item(raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    asset_id = _text(raw.get("asset_id"), 64).lower()
    asset_type = "flow" if _text(raw.get("asset_type"), 64).casefold() == "flow" else "component"
    if _UUID.fullmatch(asset_id) is None:
        raise ValueError("[REPORT_VIEW_MODEL_INVALID] 카탈로그 자산 ID가 표준 UUID가 아닙니다. 05 정규화 결과를 확인해 주세요.")
    return {
        "asset_id": asset_id,
        "version": _text(raw.get("version") or "unknown", 100) or "unknown",
        "title": _text(raw.get("title") or "카탈로그 자산", 500),
        "asset_type": asset_type,
        "technical_contract_status": _text(raw.get("technical_contract_status") or "unknown", 64),
        "catalog_url": _catalog_url(asset_id, asset_type),
        "target_node_ids": [_text(item, 128) for item in (raw.get("target_node_ids") or [])[:100] if _text(item, 128)],
        "reason": _text(raw.get("reason"), 5_000),
        "required_verification": _strings(raw.get("required_verification")),
        "decision_source": "llm" if raw.get("decision_source") == "llm" else "default_fill",
    }


def _catalog_recommendations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the small, reader-facing subset for a Flow-node detail drawer.

    The complete, traceable catalog record remains in ``catalog_application_plan``.
    Node details deliberately carry only the title, type/version, safe Agent Hub
    URL, selection reason, and pre-connection checks.  The asset ID remains only
    as an internal URL-validation binding; the renderer must never display it in
    the reader-facing drawer.  This prevents technical metadata from becoming a
    raw JSON block for users.
    """
    return [
        {
            "asset_id": item["asset_id"],
            "title": item["title"],
            "asset_type": item["asset_type"],
            "version": item["version"],
            "catalog_url": item["catalog_url"],
            "reason": item["reason"],
            "required_verification": item["required_verification"],
        }
        for item in items
    ]


def _graph_projection(raw: Any, *, selected: list[dict[str, Any]], graph_name: str) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    selected_keys = {(item["asset_id"], item["version"]): item for item in selected}
    details: dict[str, dict[str, Any]] = {}
    nodes: list[dict[str, Any]] = []
    for index, node in enumerate(raw.get("nodes") if isinstance(raw.get("nodes"), list) else []):
        if not isinstance(node, dict):
            continue
        node_id = _text(node.get("node_id"), 128)
        if not node_id:
            continue
        refs: list[dict[str, str]] = []
        for ref in node.get("catalog_asset_refs") if isinstance(node.get("catalog_asset_refs"), list) else []:
            if not isinstance(ref, dict):
                continue
            key = (_text(ref.get("asset_id"), 64).lower(), _text(ref.get("version") or "unknown", 100) or "unknown")
            if key in selected_keys and {"asset_id": key[0], "version": key[1]} not in refs:
                refs.append({"asset_id": key[0], "version": key[1]})
        detail_ref = f"{graph_name}:{node_id}"
        catalog_info = [selected_keys[(ref["asset_id"], ref["version"])] for ref in refs]
        # Keep the drawer deliberately task-oriented.  The graph node itself
        # supplies the title; technical fields and raw catalog objects remain in
        # the closed internal view model rather than becoming viewer-facing JSON.
        details[detail_ref] = {
            "current_work": _text(node.get("summary"), 5_000),
            "problems": _strings(node.get("problems")),
            "improvement": _text(node.get("improvement"), 5_000),
            "inputs": _strings(node.get("inputs")),
            "outputs": _strings(node.get("outputs")),
            "catalog_recommendations": _catalog_recommendations(catalog_info),
        }
        nodes.append({
            "node_id": node_id,
            "node_kind": _text(node.get("node_kind") or "work_step", 64),
            "title": _text(node.get("title") or f"업무 단계 {index + 1}", 500),
            "summary": _text(node.get("summary"), 5_000),
            "sequence": node.get("sequence") if isinstance(node.get("sequence"), int) else index,
            "implementation_source": _text(node.get("implementation_source") or "human_task", 64),
            "detail_ref": detail_ref,
            "catalog_refs": refs,
        })
    edges: list[dict[str, Any]] = []
    node_ids = {node["node_id"] for node in nodes}
    for index, edge in enumerate(raw.get("edges") if isinstance(raw.get("edges"), list) else []):
        if not isinstance(edge, dict):
            continue
        source = _text(edge.get("source_node_id"), 128)
        target = _text(edge.get("target_node_id"), 128)
        if source not in node_ids or target not in node_ids:
            continue
        edges.append({"edge_id": _text(edge.get("edge_id") or f"{graph_name}-edge-{index + 1}", 128), "source_node_id": source, "target_node_id": target, "edge_kind": _text(edge.get("edge_kind") or "control", 32), "label": _text(edge.get("label") or "다음", 500), "condition": _text(edge.get("condition"), 5_000), "is_default": bool(edge.get("is_default"))})
    fallback = [f"{node['sequence'] + 1}. {node['title']}: {node['summary']}".strip() for node in sorted(nodes, key=lambda item: (item["sequence"], item["node_id"]))]
    return {"nodes": nodes, "edges": edges, "details": details, "text_fallback": fallback}


def _completion_label(status: str, gap_count: int) -> str:
    if status == "COMPLETED_WITH_GAPS" or gap_count:
        return "설계 초안 생성 · 보완 필요"
    return "설계 완료"


def _short_refinement_instruction(value: Any) -> str:
    """Return a single reader-safe line of the optional second-pass instruction.

    The original request remains available in the closed input contract.  The
    report only needs a short reminder of what the requester asked the final
    pass to emphasize; it must not render a dict/list or arbitrary internal
    execution payload as text.
    """
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value.replace("\x00", "")).strip()[:1_200]


def _refinement_summary(design: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    """Project an optional future refinement result into a tiny UI contract.

    A later Flow revision may add a second LLM pass under ``design.refinement``.
    This projection intentionally recognizes only its outcome state.  Provider
    names, prompts, raw quality findings, timings, and other implementation
    traces are deliberately not copied into the report view model.
    """
    raw = design.get("refinement") if isinstance(design.get("refinement"), dict) else {}
    raw_status = _text(raw.get("status"), 64).upper()
    if raw_status in {"APPLIED", "COMPLETED", "REFINED", "SUCCESS"}:
        status = "APPLIED"
    elif raw_status in {"SKIPPED", "FALLBACK", "FAILED", "NOT_APPLIED", "UNAVAILABLE"}:
        status = "SKIPPED"
    elif raw_status in {"", "NONE", "NOT_REQUESTED"}:
        status = "NONE"
    else:
        # An unrecognized result must never be presented as successfully
        # applied.  The base draft is the safe, still-usable fallback.
        status = "SKIPPED"

    instructions = _short_refinement_instruction(request.get("final_refinement_instructions"))
    instructions_provided = bool(instructions)
    if status == "APPLIED":
        summary = "초안 점검과 보완 지시를 반영해 최종 설계를 한 번 더 다듬었습니다."
        status_label = "보완 반영 완료"
    elif status == "SKIPPED":
        summary = "2차 보완 결과를 적용하지 못해 검증된 기본 초안을 기준으로 보고서를 작성했습니다."
        status_label = "기본 초안 사용"
    elif instructions_provided:
        summary = "보완 지시는 제공됐지만 2차 보완 결과가 없어 기본 초안을 기준으로 보고서를 작성했습니다."
        status_label = "기본 초안 사용"
    else:
        summary = "2차 보완 단계는 요청되지 않아 기본 초안을 기준으로 보고서를 작성했습니다."
        status_label = "기본 초안 사용"

    return {
        "status": status,
        "status_provided": bool(raw_status),
        "status_label": status_label,
        "summary": summary,
        "final_refinement_instructions_provided": instructions_provided,
        "final_refinement_instructions": instructions,
    }


class ReportViewModelBuilderV2Component(Component):
    """06. Build the closed, renderer-safe report-view-model/v2 contract."""

    display_name = "06 Report View Model 생성"
    description = "정규화된 업무 설계를 화면용 보고서 계약으로 결정론적으로 투영합니다."
    icon = "LayoutTemplate"
    name = "ReportViewModelBuilderV2"

    inputs = [DataInput(name="design_result", display_name="정규화 설계 결과", required=True)]
    outputs = [Output(name="report_view_model", display_name="Report View Model", method="build_view_model", types=["Data"])]

    def build_view_model(self) -> Data:
        design = _payload(self.design_result)
        if design.get("schema_version") != _RESULT_SCHEMA:
            raise ValueError("[REPORT_VIEW_MODEL_INVALID] business-design-result/v2가 필요합니다. 05 node의 출력을 연결해 주세요.")
        if _contains_secret(design):
            raise ValueError("[REPORT_VIEW_MODEL_INVALID] 설계 결과에 민감정보로 의심되는 값이 있습니다. 마스킹된 입력으로 다시 실행해 주세요.")
        analysis = design.get("work_analysis") if isinstance(design.get("work_analysis"), dict) else {}
        request = design.get("request") if isinstance(design.get("request"), dict) else {}
        application = design.get("catalog_application") if isinstance(design.get("catalog_application"), dict) else {}
        selected = [_safe_catalog_item(item) for item in application.get("selected", []) if isinstance(item, dict)]
        considered = [_safe_catalog_item(item) for item in application.get("considered", []) if isinstance(item, dict)]
        not_used = [_safe_catalog_item(item) for item in application.get("not_used", []) if isinstance(item, dict)]
        all_catalog = selected + considered + not_used
        seen = {(item["asset_id"], item["version"]) for item in all_catalog}
        if len(seen) != len(all_catalog):
            raise ValueError("[REPORT_VIEW_MODEL_INVALID] 카탈로그 적용 계획의 자산이 중복됩니다. 05 정규화 결과를 확인해 주세요.")
        gaps = [item for item in design.get("information_gaps", []) if isinstance(item, dict)][:100]
        to_be = design.get("to_be_design") if isinstance(design.get("to_be_design"), dict) else {}
        problems = _strings(analysis.get("problems"))
        goal = _text(analysis.get("goal"))
        title = _text(analysis.get("title") or "업무 방식 및 개선 실행 보고서", 500)
        source_input = {
            "description_original_sha256": _text(request.get("description_original_sha256") or request.get("source_description_sha256"), 80) or None,
            "description_display_redacted": _text(request.get("description_display_redacted") or request.get("description") or "업무 설명이 제공되지 않았습니다."),
            "additional_instructions": _text(request.get("additional_instructions")),
            "redactions": _strings(request.get("redactions")),
            "redaction_count": request.get("redaction_count") if isinstance(request.get("redaction_count"), int) and request.get("redaction_count") >= 0 else 0,
        }
        refinement_summary = _refinement_summary(design, request)
        business_report = {
            "executive_summary": _block(to_be.get("summary") or goal or "입력된 업무를 기준으로 현재 방식과 개선 실행안을 정리했습니다.", [_fact("업무", title), _fact("설계 상태", "보완 필요" if gaps else "설계 완료")], [f"카탈로그 후보 {len(all_catalog)}개를 검토했습니다.", f"적용 권고 {len(selected)}개, 연결 검토 후보 {len(considered)}개입니다."]),
            "work_overview": _block(goal or "업무 목적은 입력 설명에서 추가 확인이 필요합니다.", [_fact("업무 범위", ", ".join(_strings(analysis.get("scope_in"))) or "확인 필요", "analysis")], _strings(analysis.get("success_criteria"))),
            "operating_context": _block(_text(analysis.get("trigger_and_frequency")) or "업무 실행 시점과 빈도는 설명에 따라 확인합니다.", [_fact("담당", ", ".join(_strings(analysis.get("actors"))) or "확인 필요"), _fact("사용 시스템", ", ".join(_strings(analysis.get("systems"))) or "확인 필요")], _strings(analysis.get("constraints"))),
            "as_is_analysis": _block("현재 업무 단계와 문제점을 바탕으로 현행 Flow를 정리했습니다.", [_fact("현재 단계", str(len(_graph_projection(design.get("as_is_graph"), selected=selected, graph_name="as-is")["nodes"])), "analysis")], problems),
            "improvement_direction": _block(_text(to_be.get("summary")) or "반복 작업은 자동화 후보로, 중요한 판단은 사람 검토 단계로 남깁니다.", [], _strings(to_be.get("principles"))),
            "to_be_operating_plan": _block("권장 TO-BE Flow는 카탈로그 재사용 후보와 신규 구현 필요 항목을 함께 표시합니다.", [_fact("적용 권고", str(len(selected)), "catalog"), _fact("연결 검토", str(len(considered)), "catalog")], []),
            "implementation_allocation": _block("카탈로그 자산은 기술 계약과 권한을 확인한 뒤 연결합니다.", [], [f"{item['title']} · {item['technical_contract_status']}" for item in selected]),
            "implementation_roadmap": _block("구현은 작은 검증 단위로 진행하고, 완료 기준을 충족한 뒤 다음 단계로 이동합니다.", [], [item.get("title", "구현 단계") for item in to_be.get("implementation_roadmap", []) if isinstance(item, dict)]),
            "risks_and_controls": _block("권한, 데이터 품질, 실패 시 게시 차단 기준을 구현 전에 확인합니다.", [], [item.get("risk", "위험 확인 필요") for item in to_be.get("risks_and_controls", []) if isinstance(item, dict)]),
            "validation_plan": _block("정상·예외·권한·중복 데이터 시나리오로 검증합니다.", [], [item.get("title", "검증 시나리오") for item in to_be.get("test_scenarios", []) if isinstance(item, dict)]),
            "open_items": _block("다음 실행 전에 보완할 사항을 확인하세요.", [_fact("보완 필요", str(len(gaps)), "analysis")], [item.get("question", "추가 정보 확인 필요") for item in gaps]),
        }
        as_is_graph = _graph_projection(design.get("as_is_graph"), selected=selected, graph_name="as-is")
        to_be_graph = _graph_projection(to_be, selected=selected, graph_name="to-be")
        trace = design.get("trace") if isinstance(design.get("trace"), dict) else {}
        technical_trace = {
            "source_description_sha256": _text(trace.get("source_description_sha256"), 80),
            "request_sha256": _text(trace.get("request_sha256"), 80),
            "catalog_file_sha256": _text(trace.get("catalog_file_sha256"), 80),
            "candidate_set_sha256": _text(trace.get("candidate_set_sha256"), 80),
            "top_n": trace.get("top_n") if isinstance(trace.get("top_n"), int) else len(all_catalog),
            "ranking_algorithm": _text(trace.get("ranking_algorithm") or "local-lexical-rrf/v1", 128),
            "model_identifier": _text(trace.get("model_identifier") or "unknown", 256),
            "renderer_version": _RENDERER,
        }
        result: dict[str, Any] = {
            "schema_version": _SCHEMA,
            "renderer_version": _RENDERER,
            "report_id": "",
            "source_contract_hash": _sha(design),
            "title": "업무 방식 및 개선 실행 보고서",
            "source_input": source_input,
            # This is the only refinement-related object allowed into the
            # reader-facing report.  It contains no LLM/provider trace data.
            "refinement_summary": refinement_summary,
            "completion_status": {"code": "COMPLETED_WITH_GAPS" if gaps else "COMPLETED", "label": _completion_label("COMPLETED_WITH_GAPS" if gaps else "COMPLETED", len(gaps)), "information_gap_count": len(gaps), "catalog_candidate_count": len(all_catalog), "catalog_selected_count": len(selected)},
            "business_report": business_report,
            "information_gaps": gaps,
            "as_is_graph": as_is_graph,
            "to_be_graph": to_be_graph,
            "catalog_application_plan": {"selected": selected, "considered": considered, "not_used": not_used},
            "implementation_plan": [item for item in to_be.get("implementation_roadmap", []) if isinstance(item, dict)],
            "risks_and_controls": [item for item in to_be.get("risks_and_controls", []) if isinstance(item, dict)],
            "validation_plan": [item for item in to_be.get("test_scenarios", []) if isinstance(item, dict)],
            "technical_trace": technical_trace,
        }
        material = {key: value for key, value in result.items() if key != "report_id"}
        result["report_id"] = "report-" + hashlib.sha256(_canonical(material).encode("utf-8")).hexdigest()[:24]
        self.status = f"보고서 View Model 생성 완료 · 적용 권고 {len(selected)}개 · 보완 필요 {len(gaps)}건"
        return Data(data=result)
