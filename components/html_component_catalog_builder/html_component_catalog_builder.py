from __future__ import annotations

"""02 리포트 요소 카탈로그 노드.

이 파일은 HTML 리포트에 사용할 수 있는 블록 목록을 관리합니다.
예를 들어 KPI 카드, 막대그래프, 도넛차트, 상세표 같은 요소를 정의하고,
01번 데이터 분석 결과와 사용자 질문을 보고 어떤 요소를 쓰면 좋을지 추천합니다.
"""

import json
from copy import deepcopy
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MessageTextInput, Output
from lfx.schema.data import Data


CATALOG_VERSION = "html-report-blocks-v2"


def _c(
    component_id: str,
    family: str,
    name: str,
    description: str,
    role: str,
    priority: int,
    *,
    numeric: bool = False,
    dimension: bool = False,
    time: bool = False,
    min_rows: int = 0,
    hints: list[str] | None = None,
    template_guidance: str = "",
    default_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """컴포넌트 카탈로그 항목 하나를 만드는 작은 helper입니다.

    같은 구조의 딕셔너리를 반복해서 작성하면 실수하기 쉬우므로,
    `_c(...)` 형태로 필요한 값만 넘기면 표준 형식으로 만들어 줍니다.
    """

    return {
        "component_id": component_id,
        "family": family,
        "display_name": name,
        "description": description,
        "layout_role": role,
        "best_for": hints or [],
        "avoid_when": [],
        "intent_hints": hints or [],
        "template_guidance": template_guidance,
        "default_spec": default_spec or {},
        "data_contract": {
            "requires_numeric": numeric,
            "requires_dimension": dimension,
            "requires_time": time,
            "supports_preview_rows": True,
            "min_rows": min_rows,
        },
        "compatible_with": [],
        "default_priority": priority,
    }


# 기본으로 제공되는 리포트 요소 목록입니다.
# 사용자가 `요소 양식 JSON`에 같은 component_id를 넣으면 아래 기본값을 일부 덮어쓸 수 있습니다.
DEFAULT_COMPONENTS = [
    _c("report_header", "context", "리포트 제목", "제목, 데이터 범위, row 수를 표시합니다.", "top", 100, hints=["report", "dashboard"]),
    _c("scope_summary", "context", "데이터 범위 요약", "필터, 데이터셋, preview/data_ref 여부를 요약합니다.", "top", 95, hints=["scope", "filter", "조회"]),
    _c("warning_box", "quality", "주의사항 박스", "preview, data_ref, 오류, 경고 등 주의사항을 표시합니다.", "notice", 90, hints=["warning", "preview", "주의"]),
    _c("empty_state", "quality", "데이터 없음 안내", "조건에 맞는 row가 없을 때 표시합니다.", "main", 88, hints=["empty", "no result"]),
    _c("kpi_card_grid", "kpi", "KPI 카드 묶음", "핵심 숫자 지표를 카드로 보여줍니다.", "summary", 85, numeric=True, min_rows=1, hints=["요약", "현황", "KPI"]),
    _c("trend_line_chart", "trend", "추이 선 그래프", "시간 축에 따른 metric 변화를 보여줍니다.", "main", 82, numeric=True, time=True, min_rows=2, hints=["추이", "변화", "기간"]),
    _c("comparison_bar_chart", "comparison", "비교 막대 그래프", "범주별 metric 차이를 막대 그래프로 비교합니다.", "main", 80, numeric=True, dimension=True, min_rows=1, hints=["비교", "공정별", "제품별", "막대", "bar"], template_guidance="범주별 단일 metric 비교, Top N 비교, 공정/제품/상태별 차이를 볼 때 사용합니다. 기본 렌더링은 가독성을 위해 수평 막대입니다.", default_spec={"width": "half", "chart_policy": {"limit": 10, "show_values": True, "orientation": "horizontal"}}),
    _c("donut_chart", "composition", "도넛 구성비 차트", "범주별 구성비와 비중을 도넛 차트로 보여줍니다.", "main", 79, numeric=True, dimension=True, min_rows=1, hints=["도넛", "donut", "비중", "구성비", "share", "ratio"], template_guidance="구성비, 점유율, 상태/제품/공정별 비중을 빠르게 볼 때 사용합니다. 범주가 많으면 limit 6-8로 제한하고 상세 표를 뒤에 둡니다.", default_spec={"width": "half", "chart_policy": {"limit": 8, "show_percent": True, "show_legend": True}}),
    _c("grouped_bar_chart", "comparison", "묶음 막대 그래프", "범주별 여러 metric을 묶음 막대로 비교합니다.", "main", 77, numeric=True, dimension=True, min_rows=1, hints=["복수 지표", "동시 비교", "grouped", "묶음", "WIP 생산량"], template_guidance="같은 범주에서 WIP/생산량처럼 여러 숫자 지표를 나란히 비교할 때 사용합니다. metric은 2-3개로 제한합니다.", default_spec={"width": "full", "chart_policy": {"limit": 8, "show_values": True}}),
    _c("ranking_table", "ranking", "순위 표", "상위/하위 N개 항목을 순위 표로 보여줍니다.", "main", 78, numeric=True, dimension=True, min_rows=1, hints=["상위", "하위", "top", "순위"]),
    _c("metric_delta_card_grid", "kpi", "증감 카드", "현재값과 기준값, 목표값, 증감률을 함께 보여줍니다.", "summary", 76, numeric=True, min_rows=1, hints=["증감", "변화", "달성률"]),
    _c("outlier_exception_table", "quality", "이상/예외 표", "이상, 오류, 경고, 임계치 초과 항목을 강조합니다.", "main", 75, min_rows=1, hints=["이상", "문제", "경고", "오류"]),
    _c("detail_data_table", "detail", "상세 데이터 표", "조회/분석 결과 row를 표로 확인합니다.", "detail", 72, min_rows=1, hints=["상세", "목록", "row", "조회"]),
    _c("insight_bullets", "narrative", "핵심 해석 문장", "핵심 해석을 짧은 bullet로 전달합니다.", "support", 68, hints=["해석", "요약", "리포트"]),
    _c("period_comparison_table", "trend", "기간 비교 표", "기간별 값을 정확하게 비교하는 표입니다.", "support", 65, numeric=True, time=True, min_rows=2, hints=["기간별", "일별", "월별"]),
    _c("stacked_comparison_bar", "composition", "누적 구성 막대", "범주 안의 상태/제품/구분 breakdown을 누적 막대로 보여줍니다.", "main", 70, numeric=True, dimension=True, min_rows=1, hints=["누적", "stacked", "breakdown", "구성", "상태별"], template_guidance="큰 범주별 내부 구성을 함께 비교할 때 사용합니다. x는 주 범주, series는 내부 구분, y는 숫자 metric입니다.", default_spec={"width": "full", "chart_policy": {"limit": 8, "show_values": False, "show_legend": True}}),
    _c("recommendation_list", "narrative", "다음 확인 사항", "다음 확인/조치 항목을 제안합니다.", "bottom", 58, hints=["추천", "조치", "개선", "원인"]),
    _c("rank_change_table", "ranking", "순위 변동 표", "현재 순위와 이전 순위의 변동을 보여줍니다.", "support", 56, numeric=True, dimension=True, min_rows=1, hints=["순위 변화", "랭킹 변화"]),
    _c("pivot_matrix_table", "detail", "교차표", "두 dimension의 교차표로 metric을 비교합니다.", "main", 55, numeric=True, dimension=True, min_rows=1, hints=["교차", "matrix", "pivot"]),
    _c("heatmap_matrix", "comparison", "교차 히트맵", "두 dimension 교차값의 크기를 색상 강도와 수치로 보여줍니다.", "main", 54, numeric=True, dimension=True, min_rows=1, hints=["히트맵", "heatmap", "교차", "matrix", "pivot"], template_guidance="공정 x 제품, 날짜 x 상태처럼 두 축의 조합을 빠르게 훑을 때 사용합니다. row/column 수가 많으면 limit을 낮추고, 축 라벨/수치/총계를 함께 보여주는 검토용 블록으로 사용합니다.", default_spec={"width": "full", "chart_policy": {"limit": 8, "show_values": True}}),
    _c("distribution_histogram", "distribution", "분포 히스토그램", "숫자 값의 분포, 구간별 빈도, 평균/중앙값/범위를 함께 보여줍니다.", "support", 73, numeric=True, min_rows=10, hints=["분포", "편차", "산포", "histogram", "히스토그램"], template_guidance="숫자 컬럼의 값 분포, 편차, 치우침을 확인할 때 사용합니다. bin_count는 6-12 사이를 권장하며, 구간 라벨과 count가 읽히도록 너무 작은 width를 피합니다.", default_spec={"width": "half", "chart_policy": {"bin_count": 8, "show_values": True}}),
    _c("scatter_plot", "relationship", "산점도", "두 숫자 metric 사이의 관계, 산포, 평균선, 추세와 상관계수를 보여줍니다.", "support", 72, numeric=True, min_rows=5, hints=["상관", "관계", "scatter", "산점도", "correlation"], template_guidance="두 숫자 컬럼 간 관계, 이상점, 양/음의 상관을 보고 싶을 때 사용합니다. x와 y 모두 numeric column이어야 하며, 축 라벨과 수치 범위가 읽혀야 하므로 half 이상 width를 권장합니다.", default_spec={"width": "half", "chart_policy": {"limit": 120, "show_values": False}}),
    _c("method_note", "context", "생성 기준", "집계 방식, 제한 사항, 데이터 기준을 설명합니다.", "bottom", 45, hints=["기준", "method", "주의"]),
]


def build_html_component_catalog(data_profile_value: Any, question: str = "", component_catalog_json: str = "") -> dict[str, Any]:
    """기본 카탈로그와 사용자 지정 카탈로그를 합친 뒤 추천 요소를 계산합니다."""

    profile = _profile(data_profile_value)
    resolved_question = str(question or "").strip() or str(profile.get("intent_text") or profile.get("question") or "").strip()
    catalog_config = _parse_catalog_config(component_catalog_json)
    components = _merge_components(DEFAULT_COMPONENTS, _list(catalog_config.get("components")))
    recommendations = _recommend_components(profile, resolved_question, components)
    return {
        "catalog_version": CATALOG_VERSION,
        "components": components,
        "recommended_components": recommendations,
        "selection_context": _selection_context(profile, resolved_question),
        "template_defaults": _dict(catalog_config.get("template_defaults")),
        "style_presets": catalog_config.get("style_presets") if isinstance(catalog_config.get("style_presets"), (dict, list)) else {},
        "report_presets": catalog_config.get("report_presets") if isinstance(catalog_config.get("report_presets"), (dict, list)) else {},
        "catalog_notes": _strings(catalog_config.get("catalog_notes")),
    }


def _recommend_components(profile: dict[str, Any], question: str, components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """데이터 형태와 질문 의도를 보고 사용할 만한 리포트 블록을 추천합니다.

    예:
    - 숫자 컬럼이 있으면 KPI 카드 후보가 됩니다.
    - 날짜 컬럼과 숫자 컬럼이 함께 있으면 추이 선 그래프 후보가 됩니다.
    - 범주 컬럼과 숫자 컬럼이 있으면 막대/도넛/랭킹 표 후보가 됩니다.
    """

    groups = _dict(profile.get("column_groups"))
    shape = _dict(profile.get("shape"))
    signals = _dict(profile.get("report_signals"))
    hints = _dict(profile.get("question_hints"))
    row_count = _int(shape.get("row_count"), 0)
    numeric = _strings(groups.get("numeric_columns"))
    dims = _strings(groups.get("dimension_columns"))
    times = _strings(groups.get("time_columns"))
    texts = _strings(groups.get("text_columns"))
    statuses = _strings(groups.get("status_columns"))
    deltas = _strings(groups.get("delta_columns"))
    warnings = _list(profile.get("warnings"))
    errors = _list(profile.get("errors"))
    recs: list[dict[str, Any]] = []
    # `question`에는 01번에서 만든 intent_text가 들어옵니다.
    # 즉 사용자의 원 질문뿐 아니라 00번의 "보고 싶은 방식" 입력도 함께 포함됩니다.
    # 여기 키워드가 좁으면 "도넛차트, 막대그래프, 추이 그래프"처럼 직접 쓴 표시 요구가
    # 추천 요소로 이어지지 않으므로 한국어/영문 표현을 넓게 받습니다.
    asks_summary = bool(hints.get("summary") or hints.get("report")) or _contains(
        question,
        ["summary", "overview", "dashboard", "monitor", "kpi", "card", "요약", "현황", "대시보드", "리포트", "보고서", "지표", "카드", "주요값", "주요 값"],
    )
    asks_trend = bool(hints.get("trend")) or _contains(
        question,
        ["trend", "daily", "weekly", "monthly", "date", "time", "timeline", "line", "line chart", "시계열", "추이", "변화", "기간", "일별", "주별", "월별", "날짜별", "시간별", "선그래프", "선 그래프", "라인그래프", "라인 그래프"],
    )
    asks_comparison = bool(hints.get("comparison")) or _contains(
        question,
        ["compare", "comparison", "by ", "bar", "bar chart", "bar graph", "chart", "비교", "막대", "막대그래프", "막대 그래프", "비교그래프", "비교 그래프", "차트", "그래프", "공정별", "제품별", "지역별", "채널별", "문항별", "유형별", "항목별"],
    )
    asks_composition = _contains(
        question,
        ["도넛", "도넛차트", "도넛 차트", "원형", "원형차트", "원형 차트", "파이", "파이차트", "파이 차트", "donut", "donut chart", "pie", "pie chart", "비중", "구성비", "점유율", "share", "ratio", "composition", "mix"],
    )
    asks_grouped = _contains(
        question,
        ["동시", "여러", "복수", "묶음", "묶음막대", "묶음 막대", "그룹막대", "그룹 막대", "나란히", "grouped", "grouped bar", "clustered", "multiple", "wip production", "wip와 생산", "비교 그래프", "inbound", "outbound", "orders"],
    )
    asks_breakdown = _contains(
        question,
        ["breakdown", "누적", "누적막대", "누적 막대", "stacked", "stacked bar", "상태별", "세그먼트", "구분별", "stage", "segment", "funnel"],
    )
    asks_heatmap = _contains(
        question,
        ["heatmap", "히트맵", "matrix", "pivot", "cross", "교차", "교차표", "매트릭스", "행렬", "피벗"],
    )
    asks_distribution = _contains(
        question,
        ["분포", "분포도", "값 분포", "편차", "산포", "histogram", "hist", "distribution", "deviation", "spread", "variance"],
    )
    asks_relationship = _contains(
        question,
        ["상관", "상관관계", "관계", "관계도", "scatter", "scatter plot", "산점도", "산포도", "correlation", "relationship"],
    )
    asks_ranking = bool(hints.get("ranking")) or _contains(
        question,
        ["rank", "ranking", "top", "top n", "bottom", "상위", "하위", "순위", "순위표", "랭킹", "상위권", "하위권"],
    )
    asks_exception = bool(hints.get("exception")) or _contains(
        question,
        ["warning", "danger", "defect", "diagnose", "risk", "exception", "이상", "예외", "문제", "경고", "오류", "진단", "불량", "결함", "위험", "리스크"],
    )
    specific_visual = any(
        [
            asks_trend,
            asks_comparison,
            asks_composition,
            asks_grouped,
            asks_breakdown,
            asks_heatmap,
            asks_distribution,
            asks_relationship,
            asks_ranking,
            asks_exception,
        ]
    )

    def add(component_id: str, reason: str, confidence: str = "medium", bindings: dict[str, Any] | None = None) -> None:
        """추천 목록에 중복 없이 하나의 컴포넌트를 추가합니다."""

        if not _exists(components, component_id) or any(item.get("component_id") == component_id for item in recs):
            return
        recs.append({"component_id": component_id, "reason": reason, "confidence": confidence, "suggested_bindings": bindings or {}})

    add("report_header", "HTML 결과의 제목과 범위를 고정합니다.", "high")
    add("scope_summary", "row_count, preview, data_ref 상태를 사용자에게 알려야 합니다.", "high")

    if row_count <= 0:
        # row가 없으면 차트보다 empty state와 기준 설명이 더 중요합니다.
        add("empty_state", "조회/분석 결과 row가 없습니다.", "high")
        add("method_note", "조회 조건과 다음 확인 방법을 남깁니다.", "medium")
        return recs

    if warnings or errors or signals.get("needs_preview_warning"):
        add("warning_box", "preview row, data_ref, warning/error가 있습니다.", "high", {"warnings": len(warnings), "errors": len(errors), "data_is_preview": bool(shape.get("data_is_preview"))})

    asks_detail = bool(hints.get("detail")) or _contains(question, ["상세", "상세표", "표", "테이블", "목록", "리스트", "detail", "table", "raw", "row"])
    detail_only = asks_detail and not any(
        [
            asks_summary,
            asks_trend,
            asks_comparison,
            asks_composition,
            asks_grouped,
            asks_breakdown,
            asks_heatmap,
            asks_distribution,
            asks_relationship,
            asks_ranking,
            asks_exception,
            bool(hints.get("report")),
        ]
    )
    if numeric and not detail_only and (asks_summary or not specific_visual):
        add("kpi_card_grid", "숫자 metric 후보가 있어 핵심 값을 카드로 요약할 수 있습니다.", "high" if hints.get("summary") else "medium", {"metrics": [{"column": column, "aggregation": "sum"} for column in numeric[:4]]})
    if numeric and deltas:
        add("metric_delta_card_grid", "delta/rate 계열 컬럼이 있어 증감 카드를 만들 수 있습니다.", "medium", {"metrics": [{"column": column, "aggregation": "avg"} for column in deltas[:3]]})
    if times and numeric and (asks_trend or not specific_visual):
        add("trend_line_chart", "시간 컬럼과 metric이 있어 변화 그래프가 적합합니다.", "high" if hints.get("trend") else "medium", {"x": times[0], "y": numeric[0]})
        add("period_comparison_table", "기간별 값을 표로 검증할 수 있습니다.", "medium", {"columns": _display_columns(times, dims, numeric, texts, 8)})
    if dims and numeric:
        if asks_comparison or (not specific_visual and not detail_only):
            add("comparison_bar_chart", "dimension별 metric 비교가 가능합니다.", "high" if hints.get("comparison") else "medium", {"x": dims[0], "y": numeric[0], "limit": 10})
        if asks_composition:
            add("donut_chart", "구성비/비중 의도가 있어 도넛 차트가 적합합니다.", "high", {"x": dims[0], "y": numeric[0], "limit": 8})
        if len(numeric) >= 2 and asks_grouped:
            add("grouped_bar_chart", "같은 범주에서 복수 metric을 함께 비교할 수 있습니다.", "high" if hints.get("comparison") else "medium", {"x": dims[0], "metrics": [{"column": column, "aggregation": "sum"} for column in numeric[:3]], "limit": 8})
        if asks_ranking or (not specific_visual and not detail_only):
            add("ranking_table", "dimension별 metric을 정렬해 상위/하위 항목을 확인할 수 있습니다.", "high" if hints.get("ranking") else "medium", {"columns": _display_columns([], dims, numeric, texts, 8), "sort": {"by": numeric[0], "direction": "desc"}, "limit": 10})
    if len(dims) >= 2 and numeric and asks_heatmap:
        add("pivot_matrix_table", "복수 dimension이 있어 교차표 구성이 가능합니다.", "medium", {"rows": dims[0], "columns": dims[1], "value": numeric[0]})
        add("heatmap_matrix", "복수 dimension의 교차값을 색상 강도로 비교할 수 있습니다.", "high", {"x": dims[0], "series": dims[1], "y": numeric[0], "limit": 8})
    if len(dims) >= 2 and numeric and (asks_breakdown or asks_composition):
        add("stacked_comparison_bar", "구성비/breakdown 의도에 맞는 누적 비교가 가능합니다.", "high", {"x": dims[0], "series": dims[1], "y": numeric[0]})
    if numeric and row_count >= 10 and asks_distribution:
        add("distribution_histogram", "숫자 row가 충분해 분포 확인 블록을 사용할 수 있습니다.", "high", {"column": numeric[0]})
    if len(numeric) >= 2 and row_count >= 5 and asks_relationship:
        add("scatter_plot", "두 numeric metric 사이의 관계를 산점도로 확인할 수 있습니다.", "high", {"x": numeric[0], "y": numeric[1], "limit": 120})
    if asks_exception or (statuses and not specific_visual) or signals.get("has_status_or_exception"):
        add("outlier_exception_table", "상태/경고/오류/이상 신호를 별도 표로 강조합니다.", "high" if hints.get("exception") else "medium", {"columns": _display_columns(times, dims + statuses, numeric, texts, 10)})
    if detail_only or signals.get("looks_like_query_result") or row_count > 0:
        add("detail_data_table", "조회 결과나 분석 row를 직접 확인할 수 있습니다.", "high" if detail_only else "medium", {"columns": _display_columns(times, dims, numeric, texts, 12), "limit": 50})
    if not detail_only and (asks_summary or asks_comparison or asks_trend or asks_ranking or asks_exception):
        add("insight_bullets", "차트/표를 해석하는 짧은 핵심 문장이 필요합니다.", "medium")
    if asks_exception or _contains(question, ["조치", "개선", "원인", "recommend"]):
        add("recommendation_list", "문제/원인/개선 의도에는 다음 확인 항목이 필요합니다.", "medium")
    add("method_note", "집계 기준과 preview 제한을 하단에 남깁니다.", "low")
    return _sort_recs(recs, components)[:10]


def _selection_context(profile: dict[str, Any], question: str) -> dict[str, Any]:
    """추천 판단에 사용된 핵심 컨텍스트를 사람이 확인하기 쉬운 형태로 남깁니다."""

    groups = _dict(profile.get("column_groups"))
    shape = _dict(profile.get("shape"))
    return {
        "question": str(question or profile.get("question") or ""),
        "view_request": str(profile.get("view_request") or ""),
        "intent_text": str(profile.get("intent_text") or question or profile.get("question") or ""),
        "row_count": _int(shape.get("row_count"), 0),
        "preview_row_count": _int(shape.get("preview_row_count"), 0),
        "data_is_preview": bool(shape.get("data_is_preview")),
        "numeric_columns": _strings(groups.get("numeric_columns")),
        "dimension_columns": _strings(groups.get("dimension_columns")),
        "time_columns": _strings(groups.get("time_columns")),
        "status_columns": _strings(groups.get("status_columns")),
        "question_hints": _dict(profile.get("question_hints")),
    }


def _sort_recs(recs: list[dict[str, Any]], components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """기본 우선순위와 추천 신뢰도를 합쳐 추천 순서를 정합니다."""

    priority = {str(item.get("component_id")): _int(item.get("default_priority"), 0) for item in components}
    bonus = {"high": 30, "medium": 10, "low": 0}
    return sorted(recs, key=lambda item: priority.get(str(item.get("component_id")), 0) + bonus.get(str(item.get("confidence")), 0), reverse=True)


def _display_columns(time_columns: list[str], dimension_columns: list[str], numeric_columns: list[str], text_columns: list[str], limit: int) -> list[str]:
    """표에 표시하기 좋은 컬럼을 시간→범주→숫자→텍스트 순서로 고릅니다."""

    result: list[str] = []
    for group in (time_columns, dimension_columns, numeric_columns, text_columns):
        for column in group:
            text = str(column or "").strip()
            if text and text not in result:
                result.append(text)
            if len(result) >= limit:
                return result
    return result


def _exists(components: list[dict[str, Any]], component_id: str) -> bool:
    """카탈로그에 특정 component_id가 있는지 확인합니다."""

    return any(str(item.get("component_id")) == component_id for item in components)


def _merge_components(defaults: list[dict[str, Any]], overrides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """기본 컴포넌트와 사용자 지정 컴포넌트 설정을 합칩니다.

    같은 component_id가 있으면 사용자 지정 값이 기본값을 덮어씁니다.
    """

    merged = {str(item.get("component_id")): deepcopy(item) for item in defaults if item.get("component_id")}
    for item in overrides:
        component_id = str(item.get("component_id") or "").strip()
        if component_id:
            base = merged.get(component_id, {})
            base.update(deepcopy(item))
            merged[component_id] = base
    return sorted(merged.values(), key=lambda item: _int(item.get("default_priority"), 0), reverse=True)


def _parse_catalog_config(raw: str) -> dict[str, Any]:
    """`요소 양식 JSON` 입력값을 dict 형식으로 파싱합니다."""

    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    if isinstance(parsed, list):
        return {"components": [item for item in parsed if isinstance(item, dict)]}
    if isinstance(parsed, dict):
        result = deepcopy(parsed)
        result["components"] = [item for item in _list(parsed.get("components")) if isinstance(item, dict)]
        return result
    return {}


def _profile(value: Any) -> dict[str, Any]:
    """Langflow Data 객체나 dict에서 실제 data_profile 딕셔너리를 꺼냅니다."""

    payload = _payload(value)
    return deepcopy(payload["data_profile"]) if isinstance(payload.get("data_profile"), dict) else payload


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
    """list/tuple/set이면 list로 바꾸고, 아니면 빈 list를 반환합니다."""

    return list(value) if isinstance(value, (list, tuple, set)) else []


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


def _int(value: Any, fallback: int = 0) -> int:
    """값을 정수로 바꾸고 실패하면 fallback을 반환합니다."""

    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value == value:
        return int(value)
    try:
        return int(str(value).replace(",", ""))
    except Exception:
        return fallback


def _contains(text: Any, needles: list[str]) -> bool:
    """문장 안에 후보 단어 중 하나라도 포함되어 있는지 확인합니다."""

    haystack = str(text or "").lower()
    return any(needle.lower() in haystack for needle in needles)


class HtmlComponentCatalogBuilder(Component):
    """Langflow 화면에 표시되는 02번 커스텀 컴포넌트 클래스."""

    display_name = "02 기본 요소 양식/추천"
    description = "내부 기본 요소 양식을 바탕으로 데이터와 요청 의도에 맞는 HTML 리포트 블록 후보를 추천합니다."
    icon = "LayoutTemplate"
    inputs = [
        DataInput(name="data_profile", display_name="데이터 분석 결과", required=True),
        MessageTextInput(name="component_catalog_json", display_name="요소 양식 JSON (선택)", required=False, advanced=True),
    ]
    outputs = [Output(name="html_component_catalog", display_name="요소 추천 결과", method="build_catalog")]

    def build_catalog(self) -> Data:
        """01번 데이터 분석 결과를 받아 요소 추천 결과를 출력합니다."""

        result = build_html_component_catalog(
            getattr(self, "data_profile", None),
            component_catalog_json=getattr(self, "component_catalog_json", ""),
        )
        self.status = {
            "components": len(result.get("components", [])),
            "recommended": [item.get("component_id") for item in result.get("recommended_components", [])[:5]],
        }
        return Data(data=result)
