from __future__ import annotations

"""03b LLM 계획 검증 노드.

이 파일은 LLM이 만든 report_plan JSON을 그대로 사용하지 않고,
허용된 block_id인지, 실제 컬럼명만 참조하는지, width/style 값이 안전한지 검증합니다.
검증에 실패하면 03번 기본 계획으로 fallback해서 flow가 중단되지 않게 합니다.
"""

import json
import re
from copy import deepcopy
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MessageTextInput, Output
from lfx.schema.data import Data


# LLM이 마음대로 CSS/HTML을 만들지 못하도록, 사용할 수 있는 값들을 작은 set으로 제한합니다.
WIDTHS = {"full", "two_third", "half", "third"}
EMPHASIS = {"high", "medium", "low", "critical"}
DENSITIES = {"compact", "comfortable"}
FONT_SCALES = {"small", "normal", "large"}
LAYOUTS = {"dashboard", "query_review", "diagnosis", "executive_summary", "detail_review", "report"}
AGGREGATIONS = {"sum", "avg", "average", "mean", "min", "max", "count", "nunique"}
AUDIENCES = {"operator", "analyst", "executive", "engineer", "general"}
REPORT_GOALS = {"monitor", "compare", "diagnose", "explain", "audit", "explore"}
TONES = {"info", "positive", "warning", "danger", "neutral"}
OPERATORS = {"eq", "ne", "gt", "gte", "lt", "lte", "contains", "in", "not_in"}
CHART_TYPES = {"bar", "horizontal_bar", "grouped_bar", "stacked_bar", "line", "donut", "histogram", "scatter", "heatmap"}
ORIENTATIONS = {"horizontal", "vertical"}


def normalize_llm_html_plan(base_payload_value: Any, llm_response_value: Any, component_catalog_value: Any = None) -> dict[str, Any]:
    """LLM 응답에서 JSON 계획을 추출하고 최종 report_plan으로 정규화합니다."""

    payload = _payload(base_payload_value)
    llm_text = _text(llm_response_value)
    llm_json = _extract_json_object(llm_text)
    base_plan = _dict(payload.get("report_plan"))
    llm_context = _dict(payload.get("llm_context"))
    catalog = _payload(component_catalog_value) or _dict(payload.get("html_component_catalog")) or _dict(llm_context.get("html_component_catalog"))
    allowed_blocks = _allowed_blocks(catalog)
    columns = _available_columns(payload)
    data_view_ids = _available_data_view_ids(payload)
    warnings = _list(payload.get("warnings"))

    if not llm_json:
        # LLM이 JSON이 아닌 설명문만 반환해도 리포트 생성은 계속되도록 기본 계획을 사용합니다.
        result = deepcopy(payload)
        result["report_plan"] = _mark_plan(base_plan, "deterministic_fallback", ["LLM response did not contain a JSON object."])
        result["llm_report_plan"] = {"status": "fallback", "llm_text_preview": llm_text[:1200], "errors": ["missing_json"]}
        result["warnings"] = warnings + ["LLM plan fallback: JSON object not found."]
        return result

    normalized_plan, plan_warnings = _normalize_plan(llm_json, base_plan, allowed_blocks, columns, data_view_ids)
    result = deepcopy(payload)
    result["report_plan"] = normalized_plan
    result["llm_report_plan"] = {
        "status": "ok" if normalized_plan.get("plan_source") == "llm" else "fallback",
        "raw_plan": llm_json,
        "request_interpretation": _dict(normalized_plan.get("request_interpretation")),
        "warnings": plan_warnings,
        "llm_text_preview": llm_text[:1200],
    }
    result["warnings"] = warnings + [f"LLM plan warning: {item}" for item in plan_warnings]
    return result


def _normalize_plan(
    llm_json: dict[str, Any],
    base_plan: dict[str, Any],
    allowed_blocks: set[str],
    columns: set[str],
    data_view_ids: set[str],
) -> tuple[dict[str, Any], list[str]]:
    """Validate the LLM output while preserving the user's interpreted intent."""

    warnings: list[str] = []
    # The preferred output is one object with request_interpretation and plan fields,
    # but this also accepts {"request_interpretation": ..., "report_plan": {...}}.
    llm_plan = _dict(llm_json.get("report_plan")) or llm_json
    interpretation_raw = _dict(llm_json.get("request_interpretation")) or _dict(llm_plan.get("request_interpretation"))
    request_interpretation = _normalize_request_interpretation(interpretation_raw)

    base_blocks = [item for item in _list(base_plan.get("blocks")) if isinstance(item, dict)]
    base_by_id = {str(item.get("block_id")): item for item in base_blocks}
    raw_blocks = [item for item in _list(llm_plan.get("blocks")) if isinstance(item, dict)]
    blocks: list[dict[str, Any]] = []

    for raw in raw_blocks:
        block_id = str(raw.get("block_id") or "").strip()
        if block_id not in allowed_blocks:
            # Unknown block IDs are skipped, but valid LLM blocks still survive.
            warnings.append(f"Skipped unknown block_id: {block_id or '(blank)'}")
            continue
        base = _dict(base_by_id.get(block_id))
        block = _normalize_block(raw, base, columns, data_view_ids, warnings)
        if block and not any(existing.get("block_id") == block_id for existing in blocks):
            blocks.append(block)

    if not blocks:
        warnings.append("No valid LLM blocks remained after validation; deterministic base blocks were used.")
        fallback = _mark_plan(base_plan, "deterministic_fallback", warnings)
        if request_interpretation:
            fallback["request_interpretation"] = request_interpretation
        return fallback, warnings

    if not any(block.get("block_id") == "report_header" for block in blocks) and "report_header" in allowed_blocks:
        blocks.insert(0, _normalize_block({"block_id": "report_header", "width": "full", "emphasis": "high"}, _dict(base_by_id.get("report_header")), columns, data_view_ids, warnings))

    plan = deepcopy(base_plan)
    plan["plan_version"] = "html-report-plan-llm-v1"
    plan["plan_source"] = "llm"
    plan["title"] = _short_text(llm_plan.get("title"), base_plan.get("title") or "HTML 데이터 리포트", 90)
    plan["subtitle"] = _short_text(llm_plan.get("subtitle"), base_plan.get("subtitle") or "", 160)
    plan["filename_hint"] = _short_text(llm_plan.get("filename_hint"), base_plan.get("filename_hint") or plan["title"], 80)
    plan["audience"] = _choice(llm_plan.get("audience"), AUDIENCES, base_plan.get("audience") or "general")
    plan["report_goal"] = _choice(llm_plan.get("report_goal"), REPORT_GOALS, base_plan.get("report_goal") or "explore")
    plan["layout"] = _choice(llm_plan.get("layout"), LAYOUTS, base_plan.get("layout") or "dashboard")
    plan["visual_style"] = _normalize_visual_style(_dict(llm_plan.get("visual_style")), _dict(base_plan.get("visual_style")))
    plan["narrative"] = _normalize_narrative(_dict(llm_plan.get("narrative")), _dict(base_plan.get("narrative")))
    plan["dataset_strategy"] = _normalize_dataset_strategy(_dict(llm_plan.get("dataset_strategy")), _dict(base_plan.get("dataset_strategy")), data_view_ids, warnings)
    if request_interpretation:
        plan["request_interpretation"] = request_interpretation
    reading_order = _normalize_string_list(llm_plan.get("reading_order"), 8, 40)
    if reading_order:
        plan["reading_order"] = reading_order
    plan["blocks"] = blocks[:10]
    plan["warnings"] = _list(base_plan.get("warnings"))
    notes = _list(llm_plan.get("reasoning_notes")) or _list(llm_json.get("reasoning_notes"))
    plan["llm_reasoning_notes"] = [str(item)[:200] for item in notes[:6]]
    return plan, warnings


def _normalize_block(raw: dict[str, Any], base: dict[str, Any], columns: set[str], data_view_ids: set[str], warnings: list[str]) -> dict[str, Any]:
    """LLM이 만든 block 하나를 렌더러가 안전하게 쓸 수 있는 형태로 정리합니다.

    컬럼명은 실제 데이터 컬럼에 있는 경우만 허용하고,
    width/emphasis/density 같은 레이아웃 값도 미리 정한 후보 안에서만 통과시킵니다.
    """

    block_id = str(raw.get("block_id") or base.get("block_id") or "").strip()
    block = deepcopy(base)
    block["block_id"] = block_id
    block["title"] = _short_text(raw.get("title"), base.get("title") or block_id.replace("_", " ").title(), 80)
    block["width"] = _choice(raw.get("width"), WIDTHS, base.get("width") or _default_width(block_id))
    block["emphasis"] = _choice(raw.get("emphasis"), EMPHASIS, base.get("emphasis") or "medium")
    block["density"] = _choice(raw.get("density"), DENSITIES, base.get("density") or "comfortable")
    block["font_scale"] = _choice(raw.get("font_scale"), FONT_SCALES, base.get("font_scale") or "normal")
    data_view_id = str(raw.get("data_view_id") or base.get("data_view_id") or "").strip()
    if data_view_id:
        if data_view_id in data_view_ids:
            block["data_view_id"] = data_view_id
        else:
            warnings.append(f"Ignored invalid data_view_id for {block_id}: {data_view_id}")
    for key, limit in (
        ("section", 48),
        ("description", 180),
        ("insight", 220),
        ("badge", 32),
        ("footnote", 180),
    ):
        value = str(raw.get(key) or "").strip()
        if value:
            block[key] = value[:limit]

    lines = _normalize_string_list(raw.get("lines") or raw.get("bullets"), 8, 180)
    if lines:
        block["lines"] = lines
    items = _normalize_string_list(raw.get("items"), 8, 180)
    if items:
        block["items"] = items

    style = _dict(base.get("style"))
    raw_style = _dict(raw.get("style"))
    accent = _safe_color(raw_style.get("accent_color") or raw.get("accent_color"))
    if accent:
        style["accent_color"] = accent
    if style:
        block["style"] = style

    for key in ("x", "y", "series"):
        value = str(raw.get(key) or "").strip()
        if value and value in columns:
            block[key] = value
        elif value:
            # LLM이 존재하지 않는 컬럼명을 만들면 무시하고 warning만 남깁니다.
            warnings.append(f"Ignored invalid column for {block_id}.{key}: {value}")

    raw_columns = _valid_columns(_list(raw.get("columns")), columns)
    if raw_columns:
        block["columns"] = raw_columns[:14]
    elif "columns" in raw and raw.get("columns"):
        warnings.append(f"Ignored invalid columns for {block_id}.columns")

    raw_metrics = _normalize_metrics(_list(raw.get("metrics")), columns, warnings, block_id)
    if raw_metrics:
        block["metrics"] = raw_metrics[:4]

    raw_sort = _dict(raw.get("sort"))
    sort_by = str(raw_sort.get("by") or "").strip()
    if sort_by in columns:
        block["sort"] = {"by": sort_by, "direction": _choice(raw_sort.get("direction"), {"asc", "desc"}, "desc")}

    if raw.get("limit") is not None:
        block["limit"] = min(max(_positive_int(raw.get("limit"), _positive_int(base.get("limit"), 10)), 1), 200)

    chart_policy = _normalize_chart_policy(_dict(raw.get("chart_policy")), columns, warnings, block_id)
    if chart_policy:
        block["chart_policy"] = chart_policy
        for key in (
            "x",
            "y",
            "series",
            "limit",
            "show_values",
            "show_legend",
            "show_percent",
            "normalize",
            "bin_count",
            "orientation",
            "chart_type",
        ):
            if key in chart_policy:
                block[key] = chart_policy[key]
        if chart_policy.get("metrics"):
            block["metrics"] = chart_policy["metrics"]

    table_policy = _normalize_table_policy(_dict(raw.get("table_policy")), columns, warnings, block_id)
    if table_policy:
        block["table_policy"] = table_policy
        if table_policy.get("columns"):
            block["columns"] = table_policy["columns"]
        if table_policy.get("sort"):
            block["sort"] = table_policy["sort"]
        if table_policy.get("limit") is not None:
            block["limit"] = table_policy["limit"]
        if table_policy.get("show_row_numbers") is not None:
            block["show_row_numbers"] = table_policy["show_row_numbers"]

    raw_filter_rules = raw.get("filter_rules")
    if raw_filter_rules is None:
        raw_filter_rules = _dict(raw.get("table_policy")).get("filter_rules")
    filter_rules = _normalize_filter_rules(_list(raw_filter_rules), columns, warnings, block_id)
    if filter_rules:
        block["filter_rules"] = filter_rules[:10]
        block["filter_logic"] = _choice(raw.get("filter_logic") or _dict(raw.get("table_policy")).get("filter_logic"), {"and", "or"}, "and")

    annotations = _normalize_annotations(_list(raw.get("annotations")))
    if annotations:
        block["annotations"] = annotations[:6]

    highlight_rules = _normalize_highlight_rules(_list(raw.get("highlight_rules")), columns, warnings, block_id)
    if highlight_rules:
        block["highlight_rules"] = highlight_rules[:8]

    return block


def _normalize_narrative(raw: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """LLM이 작성한 요약/해석/주의사항 문장을 길이 제한 안에서 정리합니다."""

    result = deepcopy(base)
    summary = str(raw.get("executive_summary") or raw.get("summary") or "").strip()
    if summary:
        result["executive_summary"] = summary[:320]
    for key in ("key_findings", "caveats", "recommendations", "data_quality_notes"):
        values = _normalize_string_list(raw.get(key), 8, 220)
        if values:
            result[key] = values
    return result


def _normalize_request_interpretation(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep the LLM's request understanding in a compact, renderer-safe form."""

    if not raw:
        return {}
    result: dict[str, Any] = {}
    for key, limit in (
        ("user_goal", 240),
        ("data_focus", 200),
        ("layout_intent", 240),
        ("style_intent", 200),
    ):
        value = str(raw.get(key) or "").strip()
        if value:
            result[key] = value[:limit]
    for key in ("requested_visuals", "requested_blocks", "requested_order", "requested_columns", "data_binding_plan"):
        values = _normalize_string_list(raw.get(key), 12, 80)
        if values:
            result[key] = values
    value_conditions = []
    for item in _list(raw.get("requested_value_conditions")):
        if isinstance(item, dict):
            column = str(item.get("column") or "").strip()
            operator = str(item.get("operator") or "").strip()
            value = item.get("value")
            purpose = str(item.get("purpose") or "").strip()
            compact = {key: val for key, val in {"column": column, "operator": operator, "value": value, "purpose": purpose}.items() if val not in ("", None, [], {})}
            if compact:
                value_conditions.append(compact)
        else:
            text = str(item or "").strip()
            if text:
                value_conditions.append({"text": text[:160]})
        if len(value_conditions) >= 12:
            break
    if value_conditions:
        result["requested_value_conditions"] = value_conditions
    unmet = []
    for item in _list(raw.get("unmet_requests")):
        if isinstance(item, dict):
            request = str(item.get("request") or item.get("item") or item.get("label") or "").strip()
            reason = str(item.get("reason") or item.get("why") or item.get("value") or "").strip()
            text = ": ".join(part for part in (request, reason) if part)
        else:
            text = str(item or "").strip()
        if text and text not in unmet:
            unmet.append(text[:220])
        if len(unmet) >= 8:
            break
    if unmet:
        result["unmet_requests"] = unmet
    try:
        target = int(raw.get("target_block_count") or 0)
    except Exception:
        target = 0
    if target:
        result["target_block_count"] = max(1, min(target, 12))
    return result


def _normalize_visual_style(raw: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """density, font_scale, 색상 같은 전체 스타일 설정을 검증합니다."""

    result = deepcopy(base)
    result["density"] = _choice(raw.get("density"), DENSITIES, base.get("density") or "comfortable")
    result["font_scale"] = _choice(raw.get("font_scale"), FONT_SCALES, base.get("font_scale") or "normal")
    result["max_width"] = _choice(raw.get("max_width"), {"normal", "wide"}, base.get("max_width") or "normal")
    accent = _safe_color(raw.get("accent_color") or base.get("accent_color"))
    secondary = _safe_color(raw.get("secondary_color") or base.get("secondary_color"))
    if accent:
        result["accent_color"] = accent
    if secondary:
        result["secondary_color"] = secondary
    return result


def _normalize_dataset_strategy(raw: dict[str, Any], base: dict[str, Any], data_view_ids: set[str], warnings: list[str]) -> dict[str, Any]:
    """LLM이 적은 dataset/data view 사용 계획을 안전하게 보존합니다."""

    result = deepcopy(base)
    active = str(raw.get("active_data_view_id") or raw.get("data_view_id") or base.get("active_data_view_id") or "").strip()
    if active:
        if active in data_view_ids:
            result["active_data_view_id"] = active
        else:
            warnings.append(f"Ignored invalid dataset_strategy active_data_view_id: {active}")
    mode = str(raw.get("mode") or raw.get("strategy") or base.get("mode") or "").strip().lower()
    if mode in {"select", "join", "union", "separate_sections"}:
        result["mode"] = mode
    for key in ("source_dataset_ids", "join_keys"):
        values = _normalize_string_list(raw.get(key), 12, 80)
        if values:
            result[key] = values
    return result


def _normalize_chart_policy(policy: dict[str, Any], columns: set[str], warnings: list[str], block_id: str) -> dict[str, Any]:
    """차트 설정을 검증합니다.

    chart_type, orientation, x/y/series, limit 같은 값만 통과시키고,
    컬럼명이 실제 데이터에 없으면 해당 값은 버립니다.
    """

    result: dict[str, Any] = {}
    for key in ("x", "y", "series"):
        value = str(policy.get(key) or "").strip()
        if value and value in columns:
            result[key] = value
        elif value:
            warnings.append(f"Ignored invalid chart_policy column for {block_id}.{key}: {value}")
    if policy.get("limit") is not None:
        result["limit"] = min(max(_positive_int(policy.get("limit"), 10), 1), 100)
    if policy.get("bin_count") is not None:
        result["bin_count"] = min(max(_positive_int(policy.get("bin_count"), 8), 3), 20)
    chart_type = _choice(policy.get("chart_type"), CHART_TYPES, "")
    if chart_type:
        result["chart_type"] = chart_type
    orientation = _choice(policy.get("orientation"), ORIENTATIONS, "")
    if orientation:
        result["orientation"] = orientation
    if policy.get("show_values") is not None:
        result["show_values"] = _bool(policy.get("show_values"))
    if policy.get("show_legend") is not None:
        result["show_legend"] = _bool(policy.get("show_legend"))
    if policy.get("show_percent") is not None:
        result["show_percent"] = _bool(policy.get("show_percent"))
    if policy.get("normalize") is not None:
        result["normalize"] = _bool(policy.get("normalize"))
    metrics = _normalize_metrics(_list(policy.get("metrics")), columns, warnings, block_id)
    if metrics:
        result["metrics"] = metrics[:4]
    return result


def _normalize_table_policy(policy: dict[str, Any], columns: set[str], warnings: list[str], block_id: str) -> dict[str, Any]:
    """표 설정을 검증합니다. 컬럼 목록, 정렬 컬럼, row 수 제한을 안전한 값으로 보정합니다."""

    result: dict[str, Any] = {}
    valid_columns = _valid_columns(_list(policy.get("columns")), columns)
    if valid_columns:
        result["columns"] = valid_columns[:14]
    elif policy.get("columns"):
        warnings.append(f"Ignored invalid table_policy columns for {block_id}")
    sort_by = str(policy.get("sort_by") or _dict(policy.get("sort")).get("by") or "").strip()
    if sort_by:
        if sort_by in columns:
            result["sort"] = {
                "by": sort_by,
                "direction": _choice(policy.get("sort_direction") or _dict(policy.get("sort")).get("direction"), {"asc", "desc"}, "desc"),
            }
        else:
            warnings.append(f"Ignored invalid table_policy sort column for {block_id}: {sort_by}")
    if policy.get("limit") is not None:
        result["limit"] = min(max(_positive_int(policy.get("limit"), 50), 1), 200)
    if policy.get("show_row_numbers") is not None:
        result["show_row_numbers"] = _bool(policy.get("show_row_numbers"))
    return result


def _normalize_annotations(values: list[Any]) -> list[dict[str, str]]:
    """차트/카드에 표시할 짧은 주석 chip 목록을 정리합니다."""

    result = []
    for item in values:
        if isinstance(item, dict):
            label = _short_text(item.get("label"), "", 42)
            value = _short_text(item.get("value") or item.get("text"), "", 90)
            tone = _choice(item.get("tone"), TONES, "info")
        else:
            label = ""
            value = _short_text(item, "", 90)
            tone = "info"
        if label or value:
            result.append({"label": label, "value": value, "tone": tone})
    return result


def _normalize_highlight_rules(values: list[Any], columns: set[str], warnings: list[str], block_id: str) -> list[dict[str, Any]]:
    """테이블 row 강조 규칙을 검증합니다."""

    result = []
    for item in values:
        if not isinstance(item, dict):
            continue
        column = str(item.get("column") or "").strip()
        if column not in columns:
            warnings.append(f"Ignored invalid highlight column for {block_id}: {column}")
            continue
        result.append(
            {
                "column": column,
                "operator": _choice(item.get("operator"), OPERATORS, "eq"),
                "value": _normalize_rule_value(item.get("value")),
                "tone": _choice(item.get("tone"), TONES, "warning"),
            }
        )
    return result


def _normalize_filter_rules(values: list[Any], columns: set[str], warnings: list[str], block_id: str) -> list[dict[str, Any]]:
    """차트/표에 적용할 row 필터 규칙을 검증합니다."""

    result = []
    for item in values:
        if not isinstance(item, dict):
            continue
        column = str(item.get("column") or "").strip()
        if column not in columns:
            warnings.append(f"Ignored invalid filter column for {block_id}: {column}")
            continue
        result.append(
            {
                "column": column,
                "operator": _choice(item.get("operator"), OPERATORS, "eq"),
                "value": _normalize_rule_value(item.get("value")),
            }
        )
    return result


def _normalize_rule_value(value: Any) -> Any:
    """filter/highlight 비교값을 JSON으로 안전하게 보존합니다."""

    if isinstance(value, list):
        result = []
        for item in value[:20]:
            if isinstance(item, (str, int, float, bool)) or item is None:
                result.append(item)
            else:
                result.append(str(item))
        return result
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _mark_plan(plan: dict[str, Any], source: str, warnings: list[str]) -> dict[str, Any]:
    """plan에 출처와 경고 메모를 붙입니다."""

    result = deepcopy(plan)
    result["plan_source"] = source
    result["llm_reasoning_notes"] = warnings[:6]
    return result


def _allowed_blocks(catalog: dict[str, Any]) -> set[str]:
    """카탈로그에서 허용된 block_id 목록을 가져옵니다."""

    result = {str(item.get("component_id")) for item in _list(catalog.get("components")) if isinstance(item, dict) and item.get("component_id")}
    return result or {
        "report_header",
        "scope_summary",
        "warning_box",
        "empty_state",
        "kpi_card_grid",
        "trend_line_chart",
        "comparison_bar_chart",
        "ranking_table",
        "detail_data_table",
        "insight_bullets",
        "method_note",
    }


def _available_columns(payload: dict[str, Any]) -> set[str]:
    """데이터 프로파일과 실제 row에서 사용할 수 있는 컬럼명을 모읍니다."""

    llm_context = _dict(payload.get("llm_context"))
    profile = _dict(payload.get("data_profile")) or _dict(llm_context.get("data_profile"))
    data = _dict(_dict(payload.get("api_response")).get("data"))
    result = {str(item.get("name")) for item in _list(profile.get("columns")) if isinstance(item, dict) and item.get("name")}
    result.update(str(item) for item in _list(data.get("columns")) if item)
    for view in _list(payload.get("data_views")):
        if isinstance(view, dict):
            result.update(str(item) for item in _list(view.get("columns")) if item)
            for row in _rows(view.get("rows"))[:20]:
                result.update(str(key) for key in row.keys())
    for view_profile in _list(profile.get("data_view_profiles")):
        if isinstance(view_profile, dict):
            for column in _list(view_profile.get("columns")):
                if isinstance(column, dict) and column.get("name"):
                    result.add(str(column.get("name")))
    for row in _rows(data.get("rows")):
        result.update(str(key) for key in row.keys())
    return result


def _available_data_view_ids(payload: dict[str, Any]) -> set[str]:
    """payload 안에서 block.data_view_id로 지정 가능한 view id를 모읍니다."""

    result = set()
    request = _dict(payload.get("request"))
    if request.get("active_data_view_id"):
        result.add(str(request.get("active_data_view_id")))
    data = _dict(_dict(payload.get("api_response")).get("data"))
    if data.get("data_view_id"):
        result.add(str(data.get("data_view_id")))
    for view in _list(payload.get("data_views")) + _list(payload.get("available_data_views")):
        if isinstance(view, dict) and view.get("data_view_id"):
            result.add(str(view.get("data_view_id")))
    return result


def _normalize_metrics(metrics: list[Any], columns: set[str], warnings: list[str], block_id: str) -> list[dict[str, Any]]:
    """KPI/차트 metric 설정을 검증합니다."""

    result = []
    for item in metrics:
        if not isinstance(item, dict):
            continue
        column = str(item.get("column") or "").strip()
        if column not in columns:
            warnings.append(f"Ignored invalid metric column for {block_id}: {column}")
            continue
        result.append(
            {
                "label": _short_text(item.get("label"), column, 32),
                "column": column,
                "aggregation": _choice(item.get("aggregation"), AGGREGATIONS, "sum"),
            }
        )
    return result


def _valid_columns(values: list[Any], columns: set[str]) -> list[str]:
    """후보 컬럼 목록에서 실제 존재하는 컬럼만 남깁니다."""

    result = []
    for value in values:
        text = str(value or "").strip()
        if text and text in columns and text not in result:
            result.append(text)
    return result


def _normalize_string_list(value: Any, limit: int, item_limit: int) -> list[str]:
    """문자열 또는 문자열 목록을 개수/길이 제한에 맞게 정리합니다."""

    result = []
    values = _list(value)
    if isinstance(value, str):
        values = [value]
    for item in values:
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("label") or item.get("value") or "").strip()
        else:
            text = str(item or "").strip()
        if text and text not in result:
            result.append(text[:item_limit])
        if len(result) >= limit:
            break
    return result


def _extract_json_object(text: str) -> dict[str, Any]:
    """LLM 응답 텍스트에서 JSON 객체를 찾아 파싱합니다.

    LLM이 ```json 코드블록으로 감싸거나 앞뒤 설명을 붙이는 경우도 있어,
    여러 후보 문자열을 순서대로 시도합니다.
    """

    raw = str(text or "").strip()
    if not raw:
        return {}
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.S | re.I)
    candidates = [fenced.group(1)] if fenced else []
    candidates.append(raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _text(value: Any) -> str:
    """Langflow Message/Data/dict 등에서 LLM 응답 텍스트를 꺼냅니다."""

    if value is None:
        return ""
    if isinstance(value, str):
        return value
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        for key in ("llm_text", "text", "content", "response", "message"):
            if isinstance(data.get(key), str):
                return data[key]
    for attr in ("text", "content"):
        text = getattr(value, attr, None)
        if isinstance(text, str):
            return text
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


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


def _short_text(value: Any, fallback: Any, limit: int) -> str:
    """문자열을 최대 길이로 잘라 UI가 깨지지 않게 합니다."""

    text = str(value or fallback or "").strip()
    return text[:limit]


def _choice(value: Any, allowed: set[str], fallback: Any) -> str:
    """허용된 후보 값이면 사용하고, 아니면 fallback을 사용합니다."""

    text = str(value or "").strip().lower().replace("-", "_")
    return text if text in allowed else str(fallback or "").strip().lower().replace("-", "_")


def _safe_color(value: Any) -> str:
    """`#RRGGBB` 형식의 안전한 색상 값만 통과시킵니다."""

    text = str(value or "").strip()
    return text if re.fullmatch(r"#[0-9a-fA-F]{6}", text) else ""


def _default_width(block_id: str) -> str:
    """block 종류별 기본 너비를 정합니다."""

    if block_id in {"report_header", "trend_line_chart", "grouped_bar_chart", "stacked_comparison_bar", "heatmap_matrix", "detail_data_table", "ranking_table", "outlier_exception_table", "pivot_matrix_table"}:
        return "full"
    if block_id in {"kpi_card_grid", "comparison_bar_chart", "donut_chart", "distribution_histogram", "scatter_plot", "insight_bullets"}:
        return "half"
    return "full"


def _positive_int(value: Any, default: int) -> int:
    """값을 양의 정수로 바꾸고 실패하면 default를 사용합니다."""

    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(1, parsed)


def _bool(value: Any) -> bool:
    """문자열/숫자 형태의 boolean 값을 True/False로 해석합니다."""

    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


class LlmHtmlPlanNormalizer(Component):
    """Langflow 화면에 표시되는 03b 커스텀 컴포넌트 클래스."""

    display_name = "03b LLM 계획 검증"
    description = "LLM이 만든 리포트 계획 JSON을 검증하고 렌더러가 쓸 최종 계획으로 정리합니다."
    icon = "ShieldCheck"
    inputs = [
        DataInput(name="base_payload", display_name="기본 계획", required=True),
        MessageTextInput(name="llm_response", display_name="LLM 응답", required=True),
    ]
    outputs = [Output(name="payload_out", display_name="최종 계획", method="build_payload")]

    def build_payload(self) -> Data:
        """기본 계획과 LLM 응답을 받아 검증된 최종 계획을 출력합니다."""

        result = normalize_llm_html_plan(
            getattr(self, "base_payload", None),
            getattr(self, "llm_response", ""),
        )
        plan = result.get("report_plan", {})
        self.status = {
            "source": plan.get("plan_source"),
            "layout": plan.get("layout"),
            "blocks": [block.get("block_id") for block in plan.get("blocks", [])],
        }
        return Data(data=result)
