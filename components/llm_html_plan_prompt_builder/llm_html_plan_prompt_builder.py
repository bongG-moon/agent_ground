from __future__ import annotations

"""03a LLM 계획 프롬프트 노드.

이 파일은 Langflow 기본 Prompt Template 노드에 연결할 변수 값을 준비합니다.
Prompt Template 본문은 `docs/PROMPT_TEMPLATE.md` 내용을 직접 붙여넣어 사용합니다.
중요한 점은 LLM이 HTML 전체 코드를 직접 만들지 않고, 렌더러가 안전하게 처리할 수 있는
`report_plan` JSON만 보완하게 한다는 것입니다.
"""

import json
from copy import deepcopy
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MessageTextInput, Output
from lfx.schema.message import Message


def build_llm_html_plan_prompt_payload(
    payload_value: Any,
    data_profile_value: Any = None,
    component_catalog_value: Any = None,
    design_instruction: str = "",
) -> dict[str, Any]:
    """LLM이 report_plan JSON을 보완하도록 지시하는 프롬프트 payload를 만듭니다."""

    if isinstance(data_profile_value, str) and component_catalog_value is None and not design_instruction:
        # 과거 연결 방식과 호환하기 위한 처리입니다.
        # 두 번째 인자로 문자열만 들어오면 추가 디자인 지시로 간주합니다.
        design_instruction = data_profile_value
        data_profile_value = None
    payload = _payload(payload_value)
    request = _dict(payload.get("request"))
    llm_context = _dict(payload.get("llm_context"))
    profile = _payload(data_profile_value) or _dict(payload.get("data_profile")) or _dict(llm_context.get("data_profile"))
    catalog = _payload(component_catalog_value) or _dict(payload.get("html_component_catalog")) or _dict(llm_context.get("html_component_catalog"))
    base_plan = _dict(payload.get("report_plan"))
    data = _dict(_dict(payload.get("api_response")).get("data"))
    rows = _rows(data.get("rows"))

    prompt_variables = _prompt_variables(request, profile, catalog, base_plan, rows, design_instruction)

    return {
        "prompt_type": "llm_html_report_plan",
        "prompt_variables": prompt_variables,
        "prompt_variable_names": list(prompt_variables.keys()),
        "payload": payload,
        "base_plan": base_plan,
        "schema_hint": _schema_hint(),
    }


def _prompt_variables(
    request: dict[str, Any],
    profile: dict[str, Any],
    catalog: dict[str, Any],
    base_plan: dict[str, Any],
    rows: list[dict[str, Any]],
    design_instruction: str,
) -> dict[str, str]:
    """Prompt Template에 연결할 변수들을 짧은 JSON 문자열로 만듭니다."""

    user_request = {
        "question": request.get("question") or profile.get("question") or "",
        "view_request": request.get("view_request") or "",
        "view_request_priority": "high",
        "automated_visual_hint": _dict(request.get("visual_request")),
        "target_block_count_hint": _dict(base_plan.get("composition")).get("target_block_count", ""),
        "selected_dataset_id": request.get("selected_dataset_id") or "",
        "active_data_view_id": request.get("active_data_view_id") or _dict(_dict(base_plan.get("dataset_strategy"))).get("active_data_view_id") or "",
    }
    report_context = {
        "allowed_components": _component_contracts(catalog),
        "template_presets": _catalog_template_guidance(catalog),
        "data_profile": _profile_summary(profile),
        "data_dictionary": _data_dictionary(profile),
        "multi_dataset_context": _multi_dataset_context(profile, base_plan),
        "preview_rows": rows[:8],
        "deterministic_base_plan": base_plan,
        "base_plan_usage": "fallback_only_do_not_override_user_request",
    }
    return {
        "사용자_요청_JSON": _json_text(user_request),
        "리포트_컨텍스트_JSON": _json_text(report_context),
        "디자인_지시": str(design_instruction or "").strip() or "(none)",
        "렌더링_규칙": _rendering_rules_text(),
        "출력_스키마_JSON": _json_text(_schema_hint()),
    }


def _rendering_rules_text() -> str:
    """LLM이 레이아웃/차트/스타일을 결정할 때 지켜야 하는 규칙을 제공합니다."""

    return "\n".join(
        [
            "스타일 토큰 지침:",
            "- 전체 리포트는 Material admin dashboard 계열의 업무용 UI로 렌더링됩니다. 어두운 top app bar, 좌측 섹션 drawer, elevated white card, 명확한 table/chart hierarchy를 전제로 설계하세요.",
            "- visual_style.density: 운영자가 자주 보는 촘촘한 화면은 compact, 임원 요약 화면은 comfortable을 사용하세요.",
            "- visual_style.font_scale: 기본은 normal, KPI 중심 임원 보고서는 large, 표 중심 조회 결과는 small을 사용하세요.",
            "- audience: operator, analyst, executive, engineer, general 중 하나를 선택하세요.",
            "- report_goal: monitor, compare, diagnose, explain, audit, explore 중 하나를 선택하세요.",
            "- block.width: full, two_third, half, third 중 하나를 사용하세요.",
            "- block.emphasis: 가장 중요한 1-2개 블록은 high, 보조 블록은 medium, 참고성 블록은 low를 사용하세요.",
            "- block.description과 block.insight는 각각 짧은 한 문장으로 작성하세요.",
            "- block.annotations는 0-4개의 짧은 callout만 사용하고, tone은 info, positive, warning, danger, neutral 중 하나를 쓰세요.",
            "- block.style.accent_color는 #0f766e 또는 #2563eb 같은 hex color를 사용할 수 있습니다.",
            "",
            "여러 데이터셋 사용 지침:",
            "- report_context.multi_dataset_context.available_data_views를 확인하세요. joined_auto가 있으면 여러 dataset이 공통 key로 결합된 분석용 view입니다.",
            "- 사용자가 WIP와 생산량처럼 서로 다른 데이터셋의 metric을 함께 보려는 경우, 가능하면 joined_auto 또는 자동 결합 view의 컬럼을 사용하세요.",
            "- 특정 원본 데이터셋만 보는 블록이 필요하면 block.data_view_id에 해당 data_view_id를 적을 수 있습니다.",
            "- data_view_id를 지정하지 않으면 active_data_view_id가 사용됩니다. 단일 CSV/rows 입력에서는 기존처럼 하나의 active view만 사용됩니다.",
            "- join_keys와 relationship_candidates를 보고 결합 근거가 약하면 narrative.caveats 또는 method_note에 주의사항을 적으세요.",
            "",
            "컬럼/값 지시 해석 지침:",
            "- report_context.data_dictionary를 먼저 읽고 어떤 data_view의 어떤 컬럼이 metric/dimension/time/status/detail 역할인지 파악하세요.",
            "- 사용자가 특정 컬럼명이나 값(HIGH, WARN, 정상, 특정 공정명 등)을 언급하면 data_dictionary의 sample_values/top_values에 실제 값이 있는지 확인한 뒤 정확한 문자열로 사용하세요.",
            "- 사용자가 'A 또는 B인 행만', '95 이하', '특정 상태 제외'처럼 row 조건을 말하면 block.filter_rules와 filter_logic을 사용하세요. 단순 강조만 필요할 때만 highlight_rules를 사용하세요.",
            "- filter_rules의 operator는 eq, ne, gt, gte, lt, lte, contains, in, not_in 중 하나입니다. 여러 값을 포함하려면 operator=in과 value 배열을 사용하거나 filter_logic=or와 여러 eq rule을 사용하세요.",
            "- 요청 컬럼/값을 찾을 수 없으면 임의로 만들지 말고 가장 가까운 실제 컬럼을 사용하거나 request_interpretation.unmet_requests와 narrative.caveats에 이유를 적으세요.",
            "- request_interpretation에는 requested_columns, requested_value_conditions, data_binding_plan을 포함해 어떤 컬럼과 값을 어떤 블록에서 쓸지 짧게 남기세요.",
            "",
            "구현 지시사항 준수 지침:",
            "- 사용자_요청_JSON.view_request와 디자인_지시는 최종 화면 구현 요구사항입니다. 가능한 한 block 순서, width, chart_policy, table_policy, filter_rules, visual_style에 직접 반영하세요.",
            "- '상단/첫 줄/두 번째 줄/좌측/우측/마지막' 같은 배치 표현은 blocks 배열 순서와 width 조합으로 변환하세요.",
            "- 'KPI 5개', '차트 2개만', '표 컬럼은 ...만' 같은 수량/제한 표현은 반드시 metrics 개수, block 개수, table_policy.columns, limit에 반영하세요.",
            "- 반환 직전 request_interpretation의 requested_visuals/requested_columns/requested_value_conditions/layout_intent가 blocks에 반영됐는지 자체 점검하세요.",
            "",
            "차트 선택 지침:",
            "- trend_line_chart: 시간/날짜 변화와 시계열 모니터링에 사용하세요. 한국어 단서: 추이, 변화, 시계열, 일별, 월별, 선그래프, 라인 그래프. 보통 full width가 적합합니다.",
            "- comparison_bar_chart: 하나의 metric을 하나의 category 기준으로 비교하거나 순위를 볼 때 사용하세요. 한국어 단서: 비교, 막대그래프, 막대 그래프, 항목별, 유형별, 문항별.",
            "- grouped_bar_chart: 같은 category 기준으로 여러 metric을 나란히 비교할 때 사용하세요. 예: 공정별 WIP와 생산량. 한국어 단서: 묶음 막대, 복수 지표, 여러 지표, 나란히 비교.",
            "- stacked_comparison_bar: category 내부의 breakdown이나 구성비를 보여줄 때 사용하세요. 예: 공정별 상태 구성, 날짜별 제품 mix. 한국어 단서: 누적 막대, 상태별 구성, breakdown.",
            "- donut_chart: 비중/구성비/점유율 질문에 사용하세요. 한국어 단서: 도넛차트, 도넛 차트, 원형차트, 파이차트, 비중, 구성비. category는 6-8개로 제한하고 나머지는 상세 표로 보완하세요.",
            "- distribution_histogram: 숫자 컬럼 하나의 분포, 산포, 편차, 왜도, 이상치 후보를 볼 때 사용하세요. 한국어 단서: 분포, 분포도, 편차, 산포.",
            "- scatter_plot: 두 숫자 metric 간 관계나 상관을 볼 때 사용하세요. 한국어 단서: 상관관계, 산점도, 관계도.",
            "- heatmap_matrix: 두 dimension을 교차 비교하고 색 농도로 빠르게 스캔해야 할 때 사용하세요. 한국어 단서: 히트맵, 매트릭스, 교차표, 피벗.",
            "- detail_data_table과 ranking_table은 차트를 보완합니다. view_request가 표, 상세표, 테이블, 목록, raw rows를 요청하면 차트가 있더라도 표 블록을 포함하세요.",
            "",
            "레이아웃 일관성 지침:",
            "- full-width header/scope로 시작하고, 그다음 KPI/차트 row를 균형 있게 배치한 뒤, 상세 표를 뒤쪽에 놓으세요.",
            "- 12-column grid 기준으로 full=12, two_third=8, half=6, third=4로 생각하세요. 빈 공간이 어색하게 남는 조합은 피하세요.",
            "- 차트 카드 두 개를 나란히 놓는다면 두 카드의 width와 density를 동일하게 맞추세요.",
            "- 차트는 그림만 두지 말고 축 이름, tick, 수치 요약, 범주/구간 라벨을 읽을 수 있게 설계하세요.",
            "- histogram/scatter/heatmap은 단독 해석이 가능하도록 column, metric, row 수, 범위, 상관계수, 최대 cell 같은 보조 요약을 포함하는 구성을 선호하세요.",
            "- 라벨이 길거나 legend 항목이 많거나 표 컬럼이 많으면 full width로 배치하세요.",
            "- chart_policy.limit를 사용해 legend와 label이 카드 안에 들어오게 하세요. donut/stacked/grouped chart는 category 6-8개를 선호하세요.",
        ]
    )


def _json_text(value: Any) -> str:
    """프롬프트 변수로 넘길 값을 읽기 쉬운 JSON 문자열로 변환합니다."""

    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _component_contracts(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """카탈로그를 LLM이 읽기 쉬운 컴포넌트 계약 목록으로 줄입니다."""

    components = _list(catalog.get("components"))
    recommended = {
        str(item.get("component_id")): item
        for item in _list(catalog.get("recommended_components"))
        if isinstance(item, dict)
    }
    result = []
    for item in components:
        if not isinstance(item, dict):
            continue
        component_id = str(item.get("component_id") or "")
        rec = _dict(recommended.get(component_id))
        result.append(
            _compact_dict(
                {
                "component_id": component_id,
                "display_name": item.get("display_name") or component_id,
                "description": item.get("description") or "",
                "layout_role": item.get("layout_role") or "",
                "data_contract": _dict(item.get("data_contract")),
                "recommended": bool(rec),
                "suggested_bindings": _dict(rec.get("suggested_bindings")),
                "reason": rec.get("reason") or "",
                "template_guidance": item.get("template_guidance") or item.get("usage_guidance") or "",
                "style_defaults": _dict(item.get("style_defaults")),
                "default_spec": _dict(item.get("default_spec")),
                "example_spec": _dict(item.get("example_spec")),
                }
            )
        )
    return result


def _catalog_template_guidance(catalog: dict[str, Any]) -> dict[str, Any]:
    """카탈로그 JSON에 포함된 스타일/프리셋/메모 정보를 프롬프트에 넣습니다."""

    return {
        "template_defaults": _dict(catalog.get("template_defaults")),
        "style_presets": catalog.get("style_presets") if isinstance(catalog.get("style_presets"), (dict, list)) else {},
        "report_presets": catalog.get("report_presets") if isinstance(catalog.get("report_presets"), (dict, list)) else {},
        "catalog_notes": _list(catalog.get("catalog_notes")),
    }


def _compact_dict(value: dict[str, Any]) -> dict[str, Any]:
    """빈 값을 제거해 프롬프트에 들어가는 JSON을 짧게 만듭니다."""

    return {key: item for key, item in value.items() if item not in ("", {}, [], None)}


def _profile_summary(profile: dict[str, Any]) -> dict[str, Any]:
    """데이터 프로파일 중 LLM 판단에 필요한 컬럼/의도/경고 요약만 만듭니다."""

    groups = _dict(profile.get("column_groups"))
    shape = _dict(profile.get("shape"))
    return {
        "question": profile.get("question") or "",
        "shape": shape,
        "column_groups": groups,
        "columns": [
            _column_dictionary(item)
            for item in _list(profile.get("columns"))
            if isinstance(item, dict)
        ],
        "question_hints": _dict(profile.get("question_hints")),
        "report_signals": _dict(profile.get("report_signals")),
        "warnings": _list(profile.get("warnings")),
        "errors": _list(profile.get("errors")),
    }


def _data_dictionary(profile: dict[str, Any]) -> dict[str, Any]:
    """LLM이 컬럼/값 지시를 정확히 따를 수 있게 데이터 사전 형태로 요약합니다."""

    shape = _dict(profile.get("shape"))
    views = []
    for view in _list(profile.get("data_view_profiles"))[:8]:
        if not isinstance(view, dict):
            continue
        views.append(
            {
                "data_view_id": view.get("data_view_id") or "",
                "label": view.get("label") or view.get("data_view_id") or "",
                "strategy": view.get("strategy") or "",
                "source_dataset_ids": _list(view.get("source_dataset_ids")),
                "join_keys": _list(view.get("join_keys")),
                "shape": _dict(view.get("shape")),
                "column_groups": _dict(view.get("column_groups")),
                "columns": [
                    _column_dictionary(column)
                    for column in _list(view.get("columns"))
                    if isinstance(column, dict)
                ],
                "preview_rows": _list(view.get("preview_rows"))[:2],
            }
        )
    return {
        "active_data_view_id": shape.get("active_data_view_id") or "",
        "active_data_view_strategy": shape.get("active_data_view_strategy") or "",
        "how_to_use": [
            "Use exact column names from this dictionary for x/y/series/metrics/table/filter/highlight settings.",
            "Use top_values when the user mentions specific values such as HIGH, WARN, normal, process names, or status labels.",
            "Use numeric_stats for KPI metric choices, threshold interpretation, and sort direction.",
        ],
        "active_columns": [
            _column_dictionary(column)
            for column in _list(profile.get("columns"))
            if isinstance(column, dict)
        ],
        "data_views": views,
    }


def _column_dictionary(column: dict[str, Any]) -> dict[str, Any]:
    """컬럼 하나를 LLM용 데이터 사전 항목으로 변환합니다."""

    inferred = str(column.get("inferred_type") or "")
    top_values = _list(column.get("top_values"))[:12]
    value_examples = []
    for item in top_values:
        if isinstance(item, dict):
            value_examples.append({"value": item.get("value"), "count": item.get("count")})
        else:
            value_examples.append({"value": item, "count": None})
    return _compact_dict(
        {
            "name": column.get("name"),
            "role": _column_role(inferred),
            "inferred_type": inferred,
            "semantic_hint": column.get("semantic_hint", ""),
            "usage_hint": _column_usage_hint(inferred, str(column.get("semantic_hint") or "")),
            "sample_values": _list(column.get("sample_values"))[:5],
            "top_values": value_examples,
            "unique_count": column.get("unique_count"),
            "non_empty_count": column.get("non_empty_count"),
            "null_count": column.get("null_count"),
            "numeric_stats": _compact_numeric_stats(_dict(column.get("numeric_stats"))),
        }
    )


def _column_role(inferred_type: str) -> str:
    """추정 타입을 리포트 계획에서의 역할 설명으로 바꿉니다."""

    if inferred_type == "time":
        return "time_axis"
    if inferred_type == "numeric":
        return "metric"
    if inferred_type == "status":
        return "status_filter_or_highlight"
    if inferred_type == "dimension":
        return "category_or_group"
    if inferred_type == "id":
        return "identifier_or_join_key"
    if inferred_type == "text":
        return "detail_text"
    return inferred_type or "unknown"


def _column_usage_hint(inferred_type: str, semantic_hint: str) -> str:
    """LLM이 컬럼을 어디에 써야 하는지 짧게 안내합니다."""

    if semantic_hint == "status_or_exception":
        return "Use for filter_rules, highlight_rules, exception tables, and alert composition charts."
    if semantic_hint == "delta_or_rate":
        return "Use for KPI, trend, thresholds, and quality/performance diagnosis."
    if inferred_type == "time":
        return "Use as x axis for trend_line_chart or as date column in detail tables."
    if inferred_type == "numeric":
        return "Use as KPI metric, y value, sort column, threshold, or table metric."
    if inferred_type == "dimension":
        return "Use as x/series/category grouping or table segment column."
    if inferred_type == "status":
        return "Use exact top_values for filter_rules and highlight_rules."
    if inferred_type == "id":
        return "Use mainly in detail tables or as join/detail key, not as chart metric."
    return "Use only if it directly supports the user's request."


def _compact_numeric_stats(stats: dict[str, Any]) -> dict[str, Any]:
    """숫자 통계를 프롬프트에 넣기 좋은 짧은 형태로 정리합니다."""

    result = {}
    for key in ("min", "max", "sum", "avg"):
        if key in stats:
            result[key] = stats[key]
    return result


def _multi_dataset_context(profile: dict[str, Any], base_plan: dict[str, Any]) -> dict[str, Any]:
    """LLM이 여러 데이터셋/자동 결합 view를 판단할 수 있게 요약합니다."""

    strategy = _dict(base_plan.get("dataset_strategy"))
    return {
        "active_data_view_id": strategy.get("active_data_view_id") or _dict(profile.get("shape")).get("active_data_view_id") or "",
        "active_strategy": strategy.get("mode") or _dict(profile.get("shape")).get("active_data_view_strategy") or "",
        "available_datasets": _list(profile.get("available_datasets")),
        "available_data_views": _list(profile.get("available_data_views")),
        "relationship_candidates": _list(profile.get("relationship_candidates")),
        "data_view_profiles": _list(profile.get("data_view_profiles")),
        "usage_note": "Use active data view by default. Set block.data_view_id only when a block should use a specific original dataset or alternate view.",
    }


def _schema_hint() -> dict[str, Any]:
    """LLM이 반환해야 하는 report_plan JSON 구조 예시를 제공합니다.

    실제 검증은 03b에서 다시 수행하므로, 여기서는 LLM이 형식을 이해하도록 돕는
    스키마 힌트 역할을 합니다.
    """

    return {
        "request_interpretation": {
            "user_goal": "사용자가 이해하거나 결정하고 싶은 핵심 목표",
            "data_focus": "중점적으로 볼 데이터셋, metric, dimension, 기간, segment",
            "requested_visuals": ["kpi", "trend", "bar", "donut", "table", "insight"],
            "requested_blocks": ["사용자 요청을 만족하는 허용 component id"],
            "requested_columns": ["사용자가 직접 언급했거나 리포트에 반드시 반영해야 하는 실제 컬럼명"],
            "requested_value_conditions": [{"column": "실제 컬럼명", "operator": "eq | in | lte 등", "value": "실제 값 또는 값 배열", "purpose": "filter | highlight | sort | title"}],
            "data_binding_plan": ["각 주요 블록에서 사용할 data_view_id, x/y/series/metrics/filter/table 컬럼 계획"],
            "layout_intent": "사용자가 요청한 상단/중단/하단, 좌우 배치, 강조, 읽는 순서",
            "style_intent": "사용자가 요청한 색상, 톤, 밀도, 대상 독자, 시각적 분위기",
            "target_block_count": 6,
            "unmet_requests": ["지원할 수 없는 요청 항목과 그 이유"],
        },
        "title": "짧은 리포트 제목",
        "subtitle": "짧은 부제 또는 데이터 범위 설명",
        "filename_hint": "다운로드 파일명 힌트. 확장자 없이 사용자 요청과 데이터 초점을 반영해 20-60자 정도로 작성. 예: 공정별_WIP_위험_대시보드",
        "audience": "operator | analyst | executive | engineer | general 중 하나",
        "report_goal": "monitor | compare | diagnose | explain | audit | explore 중 하나",
        "layout": "dashboard | query_review | diagnosis | executive_summary | detail_review 중 하나",
        "visual_style": {
            "density": "compact | comfortable",
            "font_scale": "small | normal | large",
            "accent_color": "#0f766e",
            "secondary_color": "#2563eb",
            "max_width": "normal | wide",
        },
        "narrative": {
            "executive_summary": "핵심 요약 한 문장",
            "key_findings": ["데이터/profile에 근거한 짧은 발견사항"],
            "caveats": ["데이터 한계 또는 해석 시 주의사항"],
            "recommendations": ["다음 조치 또는 후속 확인사항"],
            "data_quality_notes": ["preview/data_ref/row-count 관련 참고사항"],
        },
        "reading_order": ["kpi", "trend", "comparison", "detail"],
        "blocks": [
            {
                "block_id": "허용된 component id",
                "title": "블록 제목",
                "data_view_id": "선택 사항. 특정 원본 dataset 또는 joined_auto/union_auto view를 사용할 때 지정",
                "section": "Overview | Trend | Comparison | Detail | Quality | Action",
                "description": "이 블록이 보여주는 내용을 설명하는 짧은 한 문장",
                "insight": "이 블록이 중요한 이유를 설명하는 짧은 한 문장",
                "badge": "선택 사항인 짧은 라벨",
                "width": "full | two_third | half | third",
                "emphasis": "high | medium | low",
                "density": "compact | comfortable",
                "font_scale": "small | normal | large",
                "x": "필요한 경우 실제 chart/table 기준 컬럼명",
                "y": "필요한 경우 실제 숫자 metric 컬럼명",
                "series": "필요한 경우 실제 dimension 컬럼명",
                "columns": ["표에 사용할 실제 컬럼명"],
                "metrics": [{"label": "짧은 라벨", "column": "실제 숫자 컬럼명", "aggregation": "sum | avg | min | max | count | nunique"}],
                "sort": {"by": "실제 컬럼명", "direction": "desc | asc"},
                "limit": 10,
                "filter_logic": "and | or",
                "filter_rules": [{"column": "실제 컬럼명", "operator": "eq | ne | gt | gte | lt | lte | contains | in | not_in", "value": "비교값 또는 값 배열"}],
                "chart_policy": {
                    "chart_type": "bar | horizontal_bar | grouped_bar | stacked_bar | line | donut | histogram | scatter | heatmap",
                    "orientation": "horizontal | vertical",
                    "x": "실제 컬럼명",
                    "y": "실제 숫자 컬럼명",
                    "series": "실제 컬럼명",
                    "metrics": [{"label": "짧은 라벨", "column": "실제 숫자 컬럼명", "aggregation": "sum | avg | min | max | count | nunique"}],
                    "limit": 10,
                    "bin_count": 8,
                    "show_values": True,
                    "show_legend": True,
                    "show_percent": True,
                    "normalize": False,
                },
                "table_policy": {"columns": ["실제 컬럼명"], "sort_by": "실제 컬럼명", "sort_direction": "desc", "limit": 50, "show_row_numbers": True},
                "annotations": [{"label": "짧은 라벨", "value": "짧은 텍스트/값", "tone": "info | positive | warning | danger | neutral"}],
                "highlight_rules": [{"column": "실제 컬럼명", "operator": "eq | ne | gt | gte | lt | lte | contains | in | not_in", "value": "비교값 또는 값 배열", "tone": "warning | danger | positive | info"}],
                "footnote": "선택 사항인 짧은 주석",
                "style": {"accent_color": "#0f766e"},
            }
        ],
        "reasoning_notes": ["블록과 레이아웃 선택 이유를 짧게 설명"],
    }


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


def _rows(value: Any) -> list[dict[str, Any]]:
    """dict row 목록만 안전하게 복사해서 반환합니다."""

    return [deepcopy(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


class LlmHtmlPlanPromptBuilder(Component):
    """Langflow 화면에 표시되는 03a 커스텀 컴포넌트 클래스."""

    display_name = "03a 프롬프트 변수 준비"
    description = "Langflow 기본 Prompt Template에 연결할 변수 값을 개별 출력으로 준비합니다."
    icon = "Sparkles"
    inputs = [
        DataInput(name="payload", display_name="기본 계획", required=True),
        MessageTextInput(name="design_instruction", display_name="추가 구현 지시사항", required=False),
    ]
    outputs = [
        Output(name="user_request_json", display_name="사용자_요청_JSON", method="build_user_request_json", group_outputs=True),
        Output(name="report_context_json", display_name="리포트_컨텍스트_JSON", method="build_report_context_json", group_outputs=True),
        Output(name="design_instruction_text", display_name="디자인_지시", method="build_design_instruction_text", group_outputs=True),
        Output(name="rendering_rules", display_name="렌더링_규칙", method="build_rendering_rules", group_outputs=True),
        Output(name="output_schema_json", display_name="출력_스키마_JSON", method="build_output_schema_json", group_outputs=True),
    ]

    def build_user_request_json(self) -> Message:
        """Prompt Template의 {사용자_요청_JSON} 변수에 연결할 사용자 요청 JSON을 만듭니다."""

        return self._prompt_variable_message("사용자_요청_JSON")

    def build_report_context_json(self) -> Message:
        """Prompt Template의 {리포트_컨텍스트_JSON} 변수에 연결할 리포트 컨텍스트 JSON을 만듭니다."""

        return self._prompt_variable_message("리포트_컨텍스트_JSON")

    def build_design_instruction_text(self) -> Message:
        """Prompt Template의 {디자인_지시} 변수에 연결할 추가 디자인 지시를 만듭니다."""

        return self._prompt_variable_message("디자인_지시")

    def build_rendering_rules(self) -> Message:
        """Prompt Template의 {렌더링_규칙} 변수에 연결할 렌더링 규칙을 만듭니다."""

        return self._prompt_variable_message("렌더링_규칙")

    def build_output_schema_json(self) -> Message:
        """Prompt Template의 {출력_스키마_JSON} 변수에 연결할 출력 스키마 JSON을 만듭니다."""

        return self._prompt_variable_message("출력_스키마_JSON")

    def _prompt_variable_message(self, key: str) -> Message:
        """중복 계산을 피하기 위해 공통 방식으로 Prompt Template 변수 값을 꺼냅니다."""

        result = build_llm_html_plan_prompt_payload(
            getattr(self, "payload", None),
            design_instruction=getattr(self, "design_instruction", ""),
        )
        self.status = {
            "prompt_type": result.get("prompt_type"),
            "outputs": result.get("prompt_variable_names", []),
        }
        variables = _dict(result.get("prompt_variables"))
        return Message(text=str(variables.get(key) or ""))
