from __future__ import annotations

"""03 기본 리포트 계획 노드.

이 파일은 02번에서 추천된 요소를 실제 렌더러가 이해할 수 있는 `report_plan`
형식으로 바꿉니다. 이 계획은 그대로 HTML 렌더링에 사용할 수도 있고,
03a/03b를 거쳐 LLM이 더 풍부하게 보완한 계획으로 바꿀 수도 있습니다.
"""

import json
from copy import deepcopy
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MessageTextInput, Output
from lfx.schema.data import Data


def build_auto_html_plan(payload_value: Any, data_profile_value: Any, component_catalog_value: Any, max_blocks: Any = "auto") -> dict[str, Any]:
    """요청 데이터, 데이터 프로파일, 요소 카탈로그를 합쳐 기본 report_plan을 만듭니다."""

    payload = _payload(payload_value)
    profile = _payload(data_profile_value)
    catalog = _payload(component_catalog_value)
    request = _dict(payload.get("request"))
    groups = _dict(profile.get("column_groups"))
    shape = _dict(profile.get("shape"))
    recommendations = _prioritize_recommendations(
        _list(catalog.get("recommended_components")),
        _strings(_dict(request.get("visual_request")).get("requested_blocks")),
    )
    max_count, max_count_source = _resolve_max_blocks(max_blocks, request, profile)

    title = _title(request, profile)
    blocks: list[dict[str, Any]] = []
    for recommendation in recommendations:
        # 추천된 component_id를 렌더러가 바로 사용할 block dict로 변환합니다.
        if not isinstance(recommendation, dict):
            continue
        block_id = str(recommendation.get("component_id") or "")
        block = _block_from_recommendation(block_id, recommendation, groups, shape)
        if block and not any(existing.get("block_id") == block_id for existing in blocks):
            blocks.append(block)
        if len(blocks) >= max_count:
            break

    if not blocks:
        # 추천 결과가 비어도 최소한 제목과 상세표는 나오도록 fallback 계획을 둡니다.
        blocks = [
            {"block_id": "report_header", "title": title},
            {"block_id": "detail_data_table", "title": "상세 데이터", "columns": _display_columns(groups, limit=12), "limit": 50},
        ]

    plan = {
        "plan_version": "html-report-plan-v1",
        "title": title,
        "subtitle": str(request.get("view_request") or request.get("question") or ""),
        "layout": _layout(request, profile),
        "composition": {
            "planning_role": "deterministic_fallback_draft",
            "target_block_count": max_count,
            "block_count_source": max_count_source,
            "visual_request": _dict(request.get("visual_request")),
            "llm_usage_note": "LLM should treat this as a safe draft only and prioritize raw question/view_request.",
        },
        "dataset_strategy": _dataset_strategy(payload, profile),
        "blocks": blocks,
        "warnings": _list(profile.get("warnings")),
    }
    plan = _apply_template_defaults(plan, catalog)
    result = _compact_report_payload(payload, profile)
    result["report_plan"] = plan
    # 03a 프롬프트 노드가 너무 큰 payload 전체를 보지 않아도 되도록 LLM용 요약을 따로 넣습니다.
    result["llm_context"] = _llm_context(profile, catalog)
    return result


def _apply_template_defaults(plan: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    """카탈로그 JSON에 들어온 기본 스타일/블록 기본값을 plan에 반영합니다."""

    defaults = _dict(catalog.get("template_defaults"))
    if not defaults:
        return plan
    result = deepcopy(plan)
    for key in ("audience", "report_goal", "layout"):
        value = defaults.get(key)
        if value and not result.get(key):
            result[key] = value
    for key in ("visual_style", "narrative"):
        value = _dict(defaults.get(key))
        if value:
            merged = _dict(result.get(key))
            merged.update(value)
            result[key] = merged
    block_defaults = _dict(defaults.get("block_defaults") or defaults.get("blocks"))
    if block_defaults:
        result["blocks"] = [_apply_block_default(block, block_defaults) for block in _list(result.get("blocks"))]
    return result


def _apply_block_default(block: Any, block_defaults: dict[str, Any]) -> Any:
    """특정 block_id에 대한 기본 설정을 하나의 block에 병합합니다."""

    if not isinstance(block, dict):
        return block
    block_id = str(block.get("block_id") or "")
    defaults = _dict(block_defaults.get(block_id))
    if not defaults:
        return block
    result = deepcopy(defaults)
    result.update(deepcopy(block))
    style = _dict(defaults.get("style"))
    style.update(_dict(block.get("style")))
    if style:
        result["style"] = style
    return result


def _prioritize_recommendations(recommendations: list[Any], preferred_ids: list[str]) -> list[dict[str, Any]]:
    """00번 표시 요구에서 직접 요청한 요소를 추천 목록 앞쪽으로 옮깁니다."""

    pinned_ids = {"report_header", "scope_summary"}
    by_id: dict[str, dict[str, Any]] = {}
    original_order: list[str] = []

    for item in recommendations:
        if not isinstance(item, dict):
            continue
        component_id = str(item.get("component_id") or "").strip()
        if not component_id:
            continue
        if component_id not in by_id:
            by_id[component_id] = item
            original_order.append(component_id)

    result: list[dict[str, Any]] = []

    def append_component(component_id: str) -> None:
        if not component_id or any(existing.get("component_id") == component_id for existing in result):
            return
        result.append(deepcopy(by_id.get(component_id) or {"component_id": component_id}))

    for component_id in original_order:
        if component_id in pinned_ids:
            append_component(component_id)
    for component_id in preferred_ids:
        if component_id not in pinned_ids:
            append_component(component_id)
    for component_id in original_order:
        append_component(component_id)
    return result


def _block_from_recommendation(block_id: str, recommendation: dict[str, Any], groups: dict[str, Any], shape: dict[str, Any]) -> dict[str, Any]:
    """추천 컴포넌트를 렌더러용 block 설정으로 변환합니다.

    예를 들어 `comparison_bar_chart` 추천은 x/y 컬럼, limit, chart_policy를 가진
    block dict로 바뀝니다. 필요한 컬럼이 부족하면 빈 dict를 반환해 건너뜁니다.
    """

    bindings = _dict(recommendation.get("suggested_bindings"))
    numeric = _strings(groups.get("numeric_columns"))
    dims = _strings(groups.get("dimension_columns"))
    times = _strings(groups.get("time_columns"))
    texts = _strings(groups.get("text_columns"))
    columns = _display_columns(groups, limit=12)

    if block_id == "report_header":
        return {"block_id": block_id, "title": "리포트 개요"}
    if block_id == "scope_summary":
        return {"block_id": block_id, "title": "데이터 범위"}
    if block_id == "warning_box":
        return {"block_id": block_id, "title": "확인 필요 사항"}
    if block_id == "empty_state":
        return {"block_id": block_id, "title": "조회 결과 없음"}
    if block_id == "kpi_card_grid":
        metrics = _list(bindings.get("metrics"))
        if not metrics:
            metrics = [{"label": column, "column": column, "aggregation": "sum"} for column in numeric[:4]]
        return {"block_id": block_id, "title": "주요 지표", "metrics": metrics[:4]}
    if block_id == "metric_delta_card_grid":
        metrics = _list(bindings.get("metrics")) or [{"label": column, "column": column, "aggregation": "avg"} for column in numeric[:4]]
        return {"block_id": block_id, "title": "증감 지표", "metrics": metrics[:4]}
    if block_id == "trend_line_chart" and times and numeric:
        return {"block_id": block_id, "title": "시간에 따른 변화", "x": str(bindings.get("x") or times[0]), "y": str(bindings.get("y") or numeric[0])}
    if block_id == "period_comparison_table":
        return {"block_id": block_id, "title": "기간별 상세 비교", "columns": _unique([*times[:1], *dims[:3], *numeric[:4], *texts[:2]])}
    if block_id == "comparison_bar_chart" and dims and numeric:
        return {
            "block_id": block_id,
            "title": "그룹별 비교",
            "x": str(bindings.get("x") or dims[0]),
            "y": str(bindings.get("y") or numeric[0]),
            "limit": _positive_int(bindings.get("limit"), 10),
            "chart_policy": {"orientation": "horizontal", "show_values": True, "limit": _positive_int(bindings.get("limit"), 10)},
        }
    if block_id == "donut_chart" and dims and numeric:
        return {
            "block_id": block_id,
            "title": "구성비",
            "x": str(bindings.get("x") or dims[0]),
            "y": str(bindings.get("y") or numeric[0]),
            "limit": _positive_int(bindings.get("limit"), 8),
            "width": "half",
            "chart_policy": {"show_percent": True, "show_legend": True, "limit": _positive_int(bindings.get("limit"), 8)},
        }
    if block_id == "grouped_bar_chart" and dims and len(numeric) >= 2:
        metrics = _list(bindings.get("metrics")) or [{"label": column, "column": column, "aggregation": "sum"} for column in numeric[:3]]
        return {
            "block_id": block_id,
            "title": "복수 지표 비교",
            "x": str(bindings.get("x") or dims[0]),
            "metrics": metrics[:3],
            "limit": _positive_int(bindings.get("limit"), 8),
            "width": "full",
            "chart_policy": {"show_values": True, "limit": _positive_int(bindings.get("limit"), 8)},
        }
    if block_id == "stacked_comparison_bar" and len(dims) >= 2 and numeric:
        return {
            "block_id": block_id,
            "title": "구성 비교",
            "x": str(bindings.get("x") or dims[0]),
            "series": str(bindings.get("series") or dims[1]),
            "y": str(bindings.get("y") or numeric[0]),
            "limit": _positive_int(bindings.get("limit"), 8),
            "chart_policy": {"show_legend": True, "limit": _positive_int(bindings.get("limit"), 8)},
        }
    if block_id == "ranking_table" and numeric:
        sort = _dict(bindings.get("sort")) or {"by": numeric[0], "direction": "desc"}
        return {
            "block_id": block_id,
            "title": "상위 항목",
            "columns": _strings(bindings.get("columns")) or _unique([*dims[:4], *numeric[:4], *texts[:2]]),
            "sort": sort,
            "limit": _positive_int(bindings.get("limit"), 10),
        }
    if block_id == "detail_data_table":
        return {"block_id": block_id, "title": "상세 데이터", "columns": _strings(bindings.get("columns")) or columns, "limit": _positive_int(bindings.get("limit"), 50)}
    if block_id == "pivot_matrix_table" and len(dims) >= 2 and numeric:
        return {"block_id": block_id, "title": "교차표", "x": str(bindings.get("x") or dims[0]), "series": str(bindings.get("series") or dims[1]), "y": str(bindings.get("y") or numeric[0])}
    if block_id == "heatmap_matrix" and len(dims) >= 2 and numeric:
        return {
            "block_id": block_id,
            "title": "교차 히트맵",
            "x": str(bindings.get("x") or dims[0]),
            "series": str(bindings.get("series") or dims[1]),
            "y": str(bindings.get("y") or numeric[0]),
            "limit": _positive_int(bindings.get("limit"), 8),
            "width": "full",
            "chart_policy": {"show_values": True, "limit": _positive_int(bindings.get("limit"), 8)},
        }
    if block_id == "distribution_histogram" and numeric:
        return {
            "block_id": block_id,
            "title": "분포",
            "x": str(bindings.get("x") or bindings.get("column") or numeric[0]),
            "width": "half",
            "chart_policy": {"bin_count": _positive_int(bindings.get("bin_count"), 8), "show_values": True},
        }
    if block_id == "scatter_plot" and len(numeric) >= 2:
        return {
            "block_id": block_id,
            "title": "상관/산포",
            "x": str(bindings.get("x") or numeric[0]),
            "y": str(bindings.get("y") or numeric[1]),
            "limit": _positive_int(bindings.get("limit"), 120),
            "width": "half",
        }
    if block_id == "outlier_exception_table":
        status_columns = _strings(groups.get("status_columns"))
        return {"block_id": block_id, "title": "이상/예외 항목", "columns": _unique([*status_columns, *dims[:4], *numeric[:4], *texts[:2]]) or columns, "limit": 50}
    if block_id == "insight_bullets":
        return {"block_id": block_id, "title": "핵심 해석"}
    if block_id == "recommendation_list":
        return {"block_id": block_id, "title": "다음 확인 사항"}
    if block_id == "method_note":
        return {"block_id": block_id, "title": "생성 기준"}
    return {}


def _resolve_max_blocks(max_blocks: Any, request: dict[str, Any], profile: dict[str, Any]) -> tuple[int, str]:
    """수동 블록 수 대신 00번의 표시 요구를 우선 사용해 블록 수 제한을 정합니다."""

    raw = str(max_blocks or "auto").strip().lower()
    if raw not in {"", "auto", "\uc790\ub3d9"}:
        return max(3, min(_positive_int(max_blocks, 8), 10)), "manual"

    visual_request = _dict(request.get("visual_request"))
    try:
        target = int(visual_request.get("target_block_count") or 0)
    except Exception:
        target = 0
    if target:
        return max(3, min(target, 10)), "visual_request"

    text = " ".join(
        str(item or "")
        for item in (
            request.get("question"),
            request.get("view_request"),
            profile.get("intent_text"),
        )
    ).lower()
    if _contains(text, ["\uac04\ub2e8", "\uc9e7\uac8c", "simple", "brief"]):
        return 5, "auto_simple"
    if _contains(text, ["\uc0c1\uc138", "\ud48d\ubd80", "detailed", "full"]):
        return 10, "auto_detailed"
    return 8, "auto_default"


def _title(request: dict[str, Any], profile: dict[str, Any]) -> str:
    """질문 문장을 바탕으로 기본 리포트 제목을 만듭니다."""

    question = str(request.get("question") or profile.get("question") or "").strip()
    if "리포트" in question or "보고서" in question:
        return question[:80]
    if question:
        return f"{question[:60]} 리포트"
    return "HTML 데이터 리포트"


def _layout(request: dict[str, Any], profile: dict[str, Any]) -> str:
    """질문 힌트를 바탕으로 기본 레이아웃 유형을 고릅니다."""

    hints = _dict(profile.get("question_hints"))
    if hints.get("detail"):
        return "query_review"
    if hints.get("exception"):
        return "diagnosis"
    if hints.get("summary") or hints.get("comparison") or hints.get("trend"):
        return "dashboard"
    return "report"


def _display_columns(groups: dict[str, Any], limit: int) -> list[str]:
    """표나 LLM 컨텍스트에 보여줄 대표 컬럼을 고릅니다."""

    return _unique(
        [
            *_strings(groups.get("time_columns")),
            *_strings(groups.get("dimension_columns")),
            *_strings(groups.get("numeric_columns")),
            *_strings(groups.get("status_columns")),
            *_strings(groups.get("text_columns")),
        ]
    )[:limit]


def _compact_report_payload(payload: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """뒤 노드에 필요한 최소 payload만 남깁니다.

    앞 노드의 중복 정보를 모두 넘기면 Langflow 연결도 복잡하고 LLM 입력도 커지므로,
    요청/데이터/API 응답/요약 정도만 정리해 전달합니다.
    """

    shape = _dict(profile.get("shape"))
    groups = _dict(profile.get("column_groups"))
    api = _dict(payload.get("api_response"))
    data = _dict(api.get("data")) or _dict(payload.get("data"))
    result: dict[str, Any] = {
        "payload_version": payload.get("payload_version", "html-report-demo-v1"),
        "flow_type": payload.get("flow_type", "html_report_demo"),
        "status": payload.get("status", "ok"),
        "request": _dict(payload.get("request")),
        "available_datasets": _list(payload.get("available_datasets")),
        "available_data_views": _list(payload.get("available_data_views")),
        "data_views": _list(payload.get("data_views")),
        "relationship_candidates": _list(payload.get("relationship_candidates")),
        "api_response": {
            "status": api.get("status", payload.get("status", "ok")),
            "response_type": api.get("response_type", "demo_data"),
            "message": api.get("message", ""),
            "data": data,
        },
        "data_summary": {
            "row_count": shape.get("row_count", data.get("row_count", 0)),
            "preview_row_count": shape.get("preview_row_count", len(_list(data.get("rows")))),
            "column_count": shape.get("column_count", len(_list(data.get("columns")))),
            "data_is_preview": bool(shape.get("data_is_preview") or data.get("data_is_preview")),
            "numeric_columns": _strings(groups.get("numeric_columns")),
            "dimension_columns": _strings(groups.get("dimension_columns")),
            "time_columns": _strings(groups.get("time_columns")),
            "status_columns": _strings(groups.get("status_columns")),
            "active_data_view_id": shape.get("active_data_view_id") or data.get("data_view_id") or "",
            "active_data_view_strategy": shape.get("active_data_view_strategy") or data.get("strategy") or "",
        },
        "warnings": _list(payload.get("warnings")) or _list(profile.get("warnings")),
        "errors": _list(payload.get("errors")) or _list(profile.get("errors")),
    }
    return result


def _llm_context(profile: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    """LLM 프롬프트에 넣기 좋은 크기로 데이터 프로파일과 카탈로그를 요약합니다."""

    return {
        "data_profile": _compact_profile_for_llm(profile),
        "html_component_catalog": _compact_catalog_for_llm(catalog),
    }


def _compact_profile_for_llm(profile: dict[str, Any]) -> dict[str, Any]:
    """데이터 프로파일 중 LLM 계획 수립에 필요한 항목만 추립니다."""

    return {
        "question": profile.get("question") or "",
        "shape": _dict(profile.get("shape")),
        "column_groups": _dict(profile.get("column_groups")),
        "columns": [
            {
                "name": item.get("name"),
                "inferred_type": item.get("inferred_type"),
                "semantic_hint": item.get("semantic_hint", ""),
                "sample_values": _list(item.get("sample_values"))[:3],
            }
            for item in _list(profile.get("columns"))
            if isinstance(item, dict)
        ],
        "question_hints": _dict(profile.get("question_hints")),
        "report_signals": _dict(profile.get("report_signals")),
        "available_datasets": _list(profile.get("available_datasets")),
        "available_data_views": _list(profile.get("available_data_views")),
        "relationship_candidates": _list(profile.get("relationship_candidates")),
        "data_view_profiles": _list(profile.get("data_view_profiles")),
        "warnings": _list(profile.get("warnings")),
        "errors": _list(profile.get("errors")),
    }


def _dataset_strategy(payload: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """현재 active data view가 어떤 방식으로 만들어졌는지 plan에 기록합니다."""

    data = _dict(_dict(payload.get("api_response")).get("data"))
    shape = _dict(profile.get("shape"))
    active_view_id = str(data.get("data_view_id") or shape.get("active_data_view_id") or "")
    strategy = str(data.get("strategy") or shape.get("active_data_view_strategy") or "select")
    return {
        "mode": strategy,
        "active_data_view_id": active_view_id,
        "source_dataset_ids": _strings(data.get("source_dataset_ids")),
        "join_keys": _strings(data.get("join_keys")),
        "relationship_candidates": _list(payload.get("relationship_candidates"))[:6],
    }


def _compact_catalog_for_llm(catalog: dict[str, Any]) -> dict[str, Any]:
    """컴포넌트 카탈로그 중 LLM이 선택/배치에 참고할 정보만 추립니다."""

    return {
        "components": [
            _compact_dict(
                {
                    "component_id": item.get("component_id"),
                    "display_name": item.get("display_name"),
                    "description": item.get("description"),
                    "layout_role": item.get("layout_role"),
                    "data_contract": _dict(item.get("data_contract")),
                    "template_guidance": item.get("template_guidance") or item.get("usage_guidance"),
                    "style_defaults": _dict(item.get("style_defaults")),
                    "default_spec": _dict(item.get("default_spec")),
                    "example_spec": _dict(item.get("example_spec")),
                }
            )
            for item in _list(catalog.get("components"))
            if isinstance(item, dict)
        ],
        "recommended_components": _list(catalog.get("recommended_components")),
        "template_defaults": _dict(catalog.get("template_defaults")),
        "style_presets": deepcopy(catalog.get("style_presets")) if isinstance(catalog.get("style_presets"), (dict, list)) else {},
        "report_presets": deepcopy(catalog.get("report_presets")) if isinstance(catalog.get("report_presets"), (dict, list)) else {},
        "catalog_notes": _list(catalog.get("catalog_notes")),
    }


def _compact_dict(value: dict[str, Any]) -> dict[str, Any]:
    """빈 문자열/빈 dict/빈 list/None 값을 제거해 JSON을 작게 만듭니다."""

    return {key: item for key, item in value.items() if item not in ("", {}, [], None)}


def _payload(value: Any) -> dict[str, Any]:
    """Langflow Data/Message/dict/JSON 문자열을 일반 dict로 맞춥니다."""

    if isinstance(value, dict):
        return deepcopy(value)
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return deepcopy(data)
    text = getattr(value, "text", None) or getattr(value, "content", None)
    if isinstance(text, str) and text.strip():
        try:
            parsed = json.loads(text)
        except Exception:
            return {"text": text}
        return deepcopy(parsed) if isinstance(parsed, dict) else {"text": text}
    return {}


def _dict(value: Any) -> dict[str, Any]:
    """dict면 복사본을, 아니면 빈 dict를 반환합니다."""

    return deepcopy(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    """list면 복사본을, 아니면 빈 list를 반환합니다."""

    return deepcopy(value) if isinstance(value, list) else []


def _strings(value: Any) -> list[str]:
    """문자열 목록을 정리하고 중복을 제거합니다."""

    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _unique(values: list[Any]) -> list[str]:
    """값을 문자열로 바꾼 뒤 빈 값과 중복을 제거합니다."""

    result = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _positive_int(value: Any, default: int) -> int:
    """값을 양의 정수로 바꾸고 실패하면 default를 사용합니다."""

    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(1, parsed)


class AutoHtmlPlanBuilder(Component):
    """Langflow 화면에 표시되는 03번 커스텀 컴포넌트 클래스."""

    display_name = "03 기본 리포트 계획"
    description = "요소 추천 결과를 바탕으로 LLM이 보완할 수 있는 기본 리포트 계획을 만듭니다."
    icon = "ListChecks"
    inputs = [
        DataInput(name="payload", display_name="요청 데이터", required=True),
        DataInput(name="data_profile", display_name="데이터 분석 결과", required=True),
        DataInput(name="html_component_catalog", display_name="요소 추천 결과", required=True),
        MessageTextInput(name="max_blocks", display_name="블록 수 제한", value="auto", advanced=True),
    ]
    outputs = [Output(name="payload_out", display_name="기본 계획", method="build_payload")]

    def build_payload(self) -> Data:
        """요소 추천 결과를 받아 기본 계획 payload를 출력합니다."""

        result = build_auto_html_plan(
            getattr(self, "payload", None),
            getattr(self, "data_profile", None),
            getattr(self, "html_component_catalog", None),
            getattr(self, "max_blocks", "auto"),
        )
        plan = result.get("report_plan", {})
        self.status = {"blocks": [block.get("block_id") for block in plan.get("blocks", [])], "layout": plan.get("layout")}
        return Data(data=result)
