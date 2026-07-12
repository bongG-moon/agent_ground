from __future__ import annotations

"""04 HTML 렌더링 노드.

이 파일은 최종 report_plan과 rows 데이터를 받아 독립 실행 가능한 HTML 문자열을 만듭니다.
03/03b까지는 "어떤 블록을 어떤 순서와 스타일로 보여줄지"를 계획하고,
이 파일은 그 계획을 실제 HTML/CSS 조각으로 바꿉니다.
"""

import html
import json
from copy import deepcopy
from datetime import datetime
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, Output
from lfx.schema.data import Data


# 차트에서 반복 사용되는 기본 색상 팔레트입니다.
CHART_COLORS = ["#2563eb", "#0f766e", "#f59e0b", "#dc2626", "#7c3aed", "#0891b2", "#65a30d", "#be185d", "#475569", "#ea580c"]


def render_html_report(payload_value: Any) -> dict[str, Any]:
    """최종 payload를 받아 HTML 원문을 생성하고 `html_report`에 담아 반환합니다."""

    payload = _payload(payload_value)
    plan = _dict(payload.get("report_plan"))
    data = _dict(_dict(payload.get("api_response")).get("data"))
    rows = _rows(data.get("rows"))
    columns = _strings(data.get("columns")) or _columns_from_rows(rows)
    title = str(plan.get("title") or "HTML 데이터 리포트")
    filename_hint = _filename_hint(plan.get("filename_hint") or title)
    blocks = _list(plan.get("blocks"))
    rendered_blocks = []
    nav_items = []
    for block in blocks:
        # block_id에 따라 KPI/차트/표/문장 블록 중 하나를 렌더링합니다.
        if not isinstance(block, dict):
            continue
        block_rows, block_columns = _block_data(payload, block, rows, columns)
        rendered = _render_block(block, payload, block_rows, block_columns)
        if rendered:
            anchor_id = f"report-section-{len(rendered_blocks) + 1}"
            rendered_blocks.append(_wrap_block(block, rendered, anchor_id))
            nav_items.append(
                {
                    "anchor_id": anchor_id,
                    "title": str(block.get("title") or block.get("block_id") or "Section"),
                    "section": str(block.get("section") or _block_nav_label(str(block.get("block_id") or ""))),
                    "block_id": str(block.get("block_id") or ""),
                }
            )
    document = _document(title, str(plan.get("subtitle") or ""), rendered_blocks, plan, nav_items)
    result = _compact_renderer_payload(payload)
    result["html_report"] = {
        **_dict(payload.get("html_report")),
        "title": title,
        "html": document,
        "row_count": int(data.get("row_count") or len(rows)),
        "blocks": [block.get("block_id") for block in blocks if isinstance(block, dict)],
        "filename_hint": filename_hint,
        "rendered_at": datetime.now().isoformat(timespec="seconds"),
    }
    return result


def _compact_renderer_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """렌더링 이후에도 필요한 payload 정보만 추려 다음 노드로 넘깁니다."""

    result: dict[str, Any] = {
        "payload_version": payload.get("payload_version", "html-report-demo-v1"),
        "flow_type": payload.get("flow_type", "html_report_demo"),
        "status": payload.get("status", "ok"),
        "request": _dict(payload.get("request")),
        "available_datasets": _list(payload.get("available_datasets")),
        "available_data_views": _list(payload.get("available_data_views")),
        "api_response": _dict(payload.get("api_response")),
        "data_summary": _dict(payload.get("data_summary")),
        "report_plan": _dict(payload.get("report_plan")),
        "warnings": _list(payload.get("warnings")),
        "errors": _list(payload.get("errors")),
    }
    for key in ("llm_report_plan",):
        if payload.get(key):
            result[key] = deepcopy(payload[key])
    return result


def _block_data(payload: dict[str, Any], block: dict[str, Any], default_rows: list[dict[str, Any]], default_columns: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """block.data_view_id가 있으면 해당 data view의 rows/columns를 사용합니다."""

    data_view_id = str(block.get("data_view_id") or "").strip()
    if not data_view_id:
        return _filter_rows(default_rows, block), default_columns
    for view in _list(payload.get("data_views")):
        if isinstance(view, dict) and str(view.get("data_view_id") or "") == data_view_id:
            rows = _rows(view.get("rows"))
            columns = _strings(view.get("columns")) or _columns_from_rows(rows)
            return _filter_rows(rows, block), columns
    return _filter_rows(default_rows, block), default_columns


def _render_block(block: dict[str, Any], payload: dict[str, Any], rows: list[dict[str, Any]], columns: list[str]) -> str:
    """block_id를 보고 알맞은 렌더링 함수로 분기합니다."""

    block_id = str(block.get("block_id") or "")
    markup = ""
    if block_id == "report_header":
        markup = _report_header(block, payload, rows)
    elif block_id == "scope_summary":
        markup = _scope_summary(block, payload, rows, columns)
    elif block_id == "warning_box":
        markup = _warning_box(block, payload)
    elif block_id == "empty_state":
        markup = _empty_state(block)
    elif block_id in {"kpi_card_grid", "metric_delta_card_grid"}:
        markup = _kpi_cards(block, rows)
    elif block_id == "comparison_bar_chart":
        markup = _bar_chart(block, rows)
    elif block_id == "donut_chart":
        markup = _donut_chart(block, rows)
    elif block_id == "grouped_bar_chart":
        markup = _grouped_bar_chart(block, rows)
    elif block_id == "stacked_comparison_bar":
        markup = _stacked_bar_chart(block, rows)
    elif block_id == "trend_line_chart":
        markup = _line_chart(block, rows)
    elif block_id == "distribution_histogram":
        markup = _histogram_chart(block, rows)
    elif block_id == "scatter_plot":
        markup = _scatter_plot(block, rows)
    elif block_id in {"heatmap_matrix", "pivot_matrix_table"}:
        markup = _heatmap_matrix(block, rows)
    elif block_id in {"ranking_table", "rank_change_table", "detail_data_table", "period_comparison_table", "outlier_exception_table"}:
        markup = _table(block, rows, columns)
    elif block_id == "insight_bullets":
        markup = _insight_bullets(block, payload, rows)
    elif block_id == "recommendation_list":
        markup = _recommendations(block, payload)
    elif block_id == "method_note":
        markup = _method_note(block, payload)
    return _decorate_block_markup(block, markup)


def _report_header(block: dict[str, Any], payload: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """리포트 최상단 제목/부제목/row 수 메타 정보를 그립니다."""

    plan = _dict(payload.get("report_plan"))
    request = _dict(payload.get("request"))
    data = _dict(_dict(payload.get("api_response")).get("data"))
    title = html.escape(str(plan.get("title") or block.get("title") or "HTML 데이터 리포트"))
    subtitle = html.escape(str(plan.get("subtitle") or request.get("view_request") or request.get("question") or ""))
    row_count = int(data.get("row_count") or len(rows))
    audience = str(plan.get("audience") or "").strip()
    goal = str(plan.get("report_goal") or "").strip()
    badge = str(block.get("badge") or audience or "HTML REPORT").strip()
    meta = [f"{row_count:,} rows"]
    if audience:
        meta.append(f"Audience: {audience}")
    if goal:
        meta.append(f"Goal: {goal}")
    return f"""
<section class="hero">
  <div>
    <p class="eyebrow">{html.escape(badge)}</p>
    <h1>{title}</h1>
    <p class="subtitle">{subtitle}</p>
  </div>
  <div class="hero-meta">
    {''.join(f'<span>{html.escape(item)}</span>' for item in meta)}
  </div>
</section>
"""


def _scope_summary(block: dict[str, Any], payload: dict[str, Any], rows: list[dict[str, Any]], columns: list[str]) -> str:
    """데이터셋명, row 수, preview 여부, 컬럼 수를 카드 형태로 요약합니다."""

    request = _dict(payload.get("request"))
    data = _dict(_dict(payload.get("api_response")).get("data"))
    datasets = _list(payload.get("available_datasets"))
    selected = str(request.get("selected_dataset_id") or "")
    dataset_label = selected
    for item in datasets:
        if isinstance(item, dict) and str(item.get("dataset_id")) == selected:
            dataset_label = str(item.get("label") or selected)
            break
    items = [
        ("Dataset", dataset_label or "demo_dataset"),
        ("Rows", f"{int(data.get('row_count') or len(rows)):,}"),
        ("Preview", "Yes" if data.get("data_is_preview") else "No"),
        ("Columns", f"{len(columns):,}"),
    ]
    cards = "".join(f"<div><b>{html.escape(label)}</b><span>{html.escape(value)}</span></div>" for label, value in items)
    return f"""
<section class="panel">
  <h2>{html.escape(str(block.get("title") or "데이터 범위"))}</h2>
  <div class="scope-grid">{cards}</div>
</section>
"""


def _warning_box(block: dict[str, Any], payload: dict[str, Any]) -> str:
    """경고/오류/preview 제한 같은 사용자가 알아야 할 주의사항을 표시합니다."""

    warnings = [str(item) for item in _list(payload.get("warnings"))]
    errors = [str(item) for item in _list(payload.get("errors"))]
    data = _dict(_dict(payload.get("api_response")).get("data"))
    if data.get("data_is_preview"):
        warnings.append("전체 row 중 일부 preview만 HTML에 표시될 수 있습니다.")
    if not warnings and not errors:
        return ""
    lines = "".join(f"<li>{html.escape(item)}</li>" for item in [*errors, *warnings])
    return f"""
<section class="notice">
  <h2>{html.escape(str(block.get("title") or "확인 필요 사항"))}</h2>
  <ul>{lines}</ul>
</section>
"""


def _empty_state(block: dict[str, Any]) -> str:
    """row가 없을 때 보여줄 빈 상태 안내 블록을 만듭니다."""

    return f"""
<section class="panel empty">
  <h2>{html.escape(str(block.get("title") or "조회 결과 없음"))}</h2>
  <p>조건에 맞는 데이터가 없습니다. 입력 데이터 또는 조회 조건을 확인하세요.</p>
</section>
"""


def _kpi_cards(block: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """metric 설정을 집계해서 KPI 카드 묶음으로 표시합니다."""

    metrics = [item for item in _list(block.get("metrics")) if isinstance(item, dict)]
    if not metrics:
        return ""
    cards = []
    for metric in metrics[:6]:
        column = str(metric.get("column") or "")
        aggregation = str(metric.get("aggregation") or "sum")
        label = str(metric.get("label") or column)
        value = _aggregate(rows, column, aggregation)
        cards.append(
            f"""
<div class="kpi-card">
  <span>{html.escape(label)}</span>
  <strong>{html.escape(_format_number(value))}</strong>
  <small>{html.escape(aggregation)}</small>
</div>
"""
        )
    return f"""
<section class="panel">
  <h2>{html.escape(str(block.get("title") or "주요 지표"))}</h2>
  <div class="kpi-grid">{''.join(cards)}</div>
</section>
"""


def _bar_chart(block: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """범주별 숫자 합계를 수평 막대 그래프로 그립니다."""

    policy = _dict(block.get("chart_policy"))
    x = str(block.get("x") or policy.get("x") or "")
    y = str(block.get("y") or policy.get("y") or "")
    limit = _positive_int(block.get("limit") or policy.get("limit"), 10)
    grouped = _group_sum(rows, x, y)[:limit]
    if not grouped:
        return ""
    max_value = max(value for _, value in grouped) or 1
    bars = []
    for label, value in grouped:
        width = max(2, round(value / max_value * 100))
        bars.append(
            f"""
<div class="bar-row">
  <span class="bar-label">{html.escape(str(label))}</span>
  <div class="bar-track"><div class="bar-fill" style="width:{width}%"></div></div>
  <span class="bar-value">{html.escape(_format_number(value))}</span>
</div>
"""
        )
    return f"""
<section class="panel">
  <h2>{html.escape(str(block.get("title") or "그룹별 비교"))}</h2>
  <div class="bar-chart chart-zone">{''.join(bars)}</div>
</section>
"""


def _donut_chart(block: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """범주별 구성비를 CSS conic-gradient 기반 도넛 차트로 그립니다."""

    policy = _dict(block.get("chart_policy"))
    x = str(block.get("x") or policy.get("x") or "")
    y = str(block.get("y") or policy.get("y") or "")
    limit = _positive_int(block.get("limit") or policy.get("limit"), 8)
    if not x:
        return ""
    grouped = (_group_sum(rows, x, y) if y else _group_count(rows, x))[:limit]
    total = sum(value for _, value in grouped)
    if not grouped or total <= 0:
        return ""

    start = 0.0
    segments = []
    legend = []
    for index, (label, value) in enumerate(grouped):
        color = _chart_color(index)
        degrees = 360 * value / total
        end = start + degrees
        segments.append(f"{color} {start:.2f}deg {end:.2f}deg")
        percent = value / total * 100
        legend.append(
            f"""
<div class="legend-item">
  <span class="legend-swatch" style="background:{color}"></span>
  <span class="legend-label">{html.escape(str(label))}</span>
  <b>{html.escape(_format_number(value))}</b>
  <small>{percent:.1f}%</small>
</div>
"""
        )
        start = end
    gradient = ", ".join(segments)
    return f"""
<section class="panel">
  <h2>{html.escape(str(block.get("title") or "구성비"))}</h2>
  <div class="donut-layout chart-zone">
    <div class="donut-visual" style="background:conic-gradient({gradient});">
      <div class="donut-hole"><strong>100%</strong><span>{html.escape(_format_number(total))}</span></div>
    </div>
    <div class="chart-legend">{''.join(legend)}</div>
  </div>
</section>
"""


def _grouped_bar_chart(block: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """하나의 범주 안에서 여러 metric을 나란히 비교하는 묶음 막대 그래프를 만듭니다."""

    policy = _dict(block.get("chart_policy"))
    x = str(block.get("x") or policy.get("x") or "")
    metrics = [item for item in _list(block.get("metrics") or policy.get("metrics")) if isinstance(item, dict)]
    y = str(block.get("y") or policy.get("y") or "")
    if not metrics and y:
        metrics = [{"label": y, "column": y, "aggregation": "sum"}]
    metrics = metrics[:4]
    limit = _positive_int(block.get("limit") or policy.get("limit"), 8)
    if not x or not metrics:
        return ""

    labels = _top_group_labels(rows, x, [str(metric.get("column") or "") for metric in metrics], limit)
    values_by_label: dict[str, list[tuple[str, float]]] = {}
    max_value = 0.0
    for label in labels:
        label_rows = [row for row in rows if str(row.get(x) or "(blank)") == label]
        metric_values = []
        for metric in metrics:
            column = str(metric.get("column") or "")
            aggregation = str(metric.get("aggregation") or "sum")
            value = _aggregate(label_rows, column, aggregation)
            number = _number(value) or 0
            max_value = max(max_value, number)
            metric_values.append((str(metric.get("label") or column), number))
        values_by_label[label] = metric_values
    if not values_by_label:
        return ""

    rows_markup = []
    for label in labels:
        metric_bars = []
        for index, (metric_label, value) in enumerate(values_by_label.get(label, [])):
            width = max(2, round(value / max(max_value, 1) * 100))
            color = _chart_color(index)
            metric_bars.append(
                f"""
<div class="grouped-metric-row">
  <span>{html.escape(metric_label)}</span>
  <div class="bar-track"><div class="bar-fill solid" style="width:{width}%;background:{color};"></div></div>
  <b>{html.escape(_format_number(value))}</b>
</div>
"""
            )
        rows_markup.append(
            f"""
<div class="grouped-category">
  <div class="grouped-label">{html.escape(label)}</div>
  <div class="grouped-bars">{''.join(metric_bars)}</div>
</div>
"""
        )
    return f"""
<section class="panel">
  <h2>{html.escape(str(block.get("title") or "복수 지표 비교"))}</h2>
  <div class="grouped-chart chart-zone">{''.join(rows_markup)}</div>
</section>
"""


def _stacked_bar_chart(block: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """큰 범주별 내부 구성을 누적 막대로 표시합니다."""

    policy = _dict(block.get("chart_policy"))
    x = str(block.get("x") or policy.get("x") or "")
    series = str(block.get("series") or policy.get("series") or "")
    y = str(block.get("y") or policy.get("y") or "")
    limit = _positive_int(block.get("limit") or policy.get("limit"), 8)
    if not x or not series:
        return ""

    matrix: dict[str, dict[str, float]] = {}
    series_totals: dict[str, float] = {}
    for row in rows:
        x_label = str(row.get(x) or "(blank)")
        series_label = str(row.get(series) or "(blank)")
        value = _row_metric(row, y)
        matrix.setdefault(x_label, {})
        matrix[x_label][series_label] = matrix[x_label].get(series_label, 0) + value
        series_totals[series_label] = series_totals.get(series_label, 0) + value
    if not matrix:
        return ""

    x_labels = sorted(matrix, key=lambda label: sum(matrix[label].values()), reverse=True)[:limit]
    series_labels = sorted(series_totals, key=series_totals.get, reverse=True)[:8]
    legend = "".join(
        f'<span class="stacked-legend-item"><i style="background:{_chart_color(index)}"></i>{html.escape(label)}</span>'
        for index, label in enumerate(series_labels)
    )
    stacked_rows = []
    for label in x_labels:
        total = sum(matrix[label].values())
        if total <= 0:
            continue
        segments = []
        for index, series_label in enumerate(series_labels):
            value = matrix[label].get(series_label, 0)
            if value <= 0:
                continue
            width = max(1, value / total * 100)
            segments.append(
                f'<div class="stacked-segment" style="width:{width:.2f}%;background:{_chart_color(index)}" title="{html.escape(series_label)} {html.escape(_format_number(value))}"></div>'
            )
        stacked_rows.append(
            f"""
<div class="stacked-row">
  <span class="stacked-label">{html.escape(label)}</span>
  <div class="stacked-track">{''.join(segments)}</div>
  <span class="stacked-total">{html.escape(_format_number(total))}</span>
</div>
"""
        )
    return f"""
<section class="panel">
  <h2>{html.escape(str(block.get("title") or "구성 비교"))}</h2>
  <div class="stacked-chart chart-zone">
    <div class="stacked-legend">{legend}</div>
    {''.join(stacked_rows)}
  </div>
</section>
"""


def _line_chart(block: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """시간/순서 축에 따른 숫자 변화를 SVG 선 그래프로 그립니다."""

    policy = _dict(block.get("chart_policy"))
    x = str(block.get("x") or policy.get("x") or "")
    y = str(block.get("y") or policy.get("y") or "")
    points = sorted(_group_sum(rows, x, y), key=lambda item: str(item[0]))
    if len(points) < 2:
        return ""
    width = 720
    height = 260
    pad_x = 36
    pad_top = 38
    pad_bottom = 38
    values = [value for _, value in points]
    min_value = min(values)
    max_value = max(values)
    span = max(max_value - min_value, 1)
    coords = []
    for index, (_, value) in enumerate(points):
        px = pad_x + (width - pad_x * 2) * index / max(len(points) - 1, 1)
        py = height - pad_bottom - (height - pad_top - pad_bottom) * (value - min_value) / span
        coords.append((px, py))
    polyline = " ".join(f"{xv:.1f},{yv:.1f}" for xv, yv in coords)
    point_marks = []
    for (label, value), (xv, yv) in zip(points, coords):
        label_y = yv - 12 if yv > pad_top + 18 else yv + 22
        anchor = "middle"
        if xv < pad_x + 18:
            anchor = "start"
        elif xv > width - pad_x - 18:
            anchor = "end"
        point_marks.append(
            f"""
<g class="line-point">
  <circle cx="{xv:.1f}" cy="{yv:.1f}" r="3.5"></circle>
  <text x="{xv:.1f}" y="{label_y:.1f}" text-anchor="{anchor}" class="point-value">{html.escape(_format_number(value))}</text>
  <title>{html.escape(str(label))}: {html.escape(_format_number(value))}</title>
</g>
"""
        )
    first_label = html.escape(str(points[0][0]))
    last_label = html.escape(str(points[-1][0]))
    return f"""
<section class="panel">
  <h2>{html.escape(str(block.get("title") or "시간에 따른 변화"))}</h2>
  <div class="chart-zone">
    <svg class="line-chart" viewBox="0 0 {width} {height}" role="img">
      <line x1="{pad_x}" y1="{height-pad_bottom}" x2="{width-pad_x}" y2="{height-pad_bottom}" class="axis"></line>
      <polyline points="{polyline}" class="trend-line"></polyline>
      {''.join(point_marks)}
    </svg>
  </div>
  <div class="chart-caption"><span>{first_label}</span><span>{last_label}</span></div>
</section>
"""


def _histogram_chart(block: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """숫자 컬럼의 분포를 구간별 막대 수로 보여주는 히스토그램을 만듭니다."""

    policy = _dict(block.get("chart_policy"))
    column = str(block.get("x") or policy.get("x") or policy.get("column") or "")
    values = sorted(value for value in (_number(row.get(column)) for row in rows) if value is not None)
    if len(values) < 2:
        return ""
    bin_count = min(max(_positive_int(policy.get("bin_count"), 8), 4), 12)
    min_value = min(values)
    max_value = max(values)
    avg_value = sum(values) / len(values)
    median_value = _median(values)
    if min_value == max_value:
        bin_specs = [(min_value, max_value, len(values))]
    else:
        bin_width = (max_value - min_value) / bin_count
        bins = [0 for _ in range(bin_count)]
        for value in values:
            index = min(int((value - min_value) / bin_width), bin_count - 1)
            bins[index] += 1
        bin_specs = []
        for index, count in enumerate(bins):
            start = min_value + bin_width * index
            end = max_value if index == bin_count - 1 else min_value + bin_width * (index + 1)
            bin_specs.append((start, end, count))
    max_count = max((count for _, _, count in bin_specs), default=1) or 1
    bars = []
    for start, end, count in bin_specs:
        height_pct = 0 if count <= 0 else max(6, count / max_count * 100)
        label = _range_label(start, end)
        zero_class = " is-zero" if count <= 0 else ""
        bars.append(
            f"""
<div class="histogram-bin" title="{html.escape(column)} {html.escape(label)}: {count:,} rows">
  <span class="histogram-value">{count:,}</span>
  <div class="histogram-bar-shell"><div class="histogram-bar{zero_class}" style="height:{height_pct:.2f}%"></div></div>
  <span class="histogram-bin-label">{html.escape(label)}</span>
</div>
"""
        )
    marker = ""
    if max_value > min_value:
        marker_left = _percent_position(avg_value, min_value, max_value)
        marker = f'<div class="histogram-marker" style="left:{marker_left:.2f}%"><span>Avg {_axis_number(avg_value)}</span></div>'
    summary = _chart_summary(
        [
            ("Column", column),
            ("Rows", f"{len(values):,}"),
            ("Avg", _axis_number(avg_value)),
            ("Median", _axis_number(median_value)),
            ("Range", f"{_axis_number(min_value)} - {_axis_number(max_value)}"),
        ]
    )
    return f"""
<section class="panel">
  <h2>{html.escape(str(block.get("title") or "분포"))}</h2>
  {summary}
  <div class="histogram-chart chart-zone">
    <div class="histogram-plot">
      <div class="histogram-y-axis"><span>{max_count:,}</span><span>{max_count // 2:,}</span><span>0</span></div>
      <div class="histogram-grid" style="--bins:{len(bin_specs)}">
        {marker}
        {''.join(bars)}
      </div>
    </div>
    <div class="axis-label histogram-x-label">{html.escape(column)} bins</div>
  </div>
</section>
"""


def _scatter_plot(block: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """두 숫자 컬럼의 관계를 SVG 산점도로 표시합니다."""

    policy = _dict(block.get("chart_policy"))
    x = str(block.get("x") or policy.get("x") or "")
    y = str(block.get("y") or policy.get("y") or "")
    limit = _positive_int(block.get("limit") or policy.get("limit"), 120)
    points = [(_number(row.get(x)), _number(row.get(y))) for row in rows]
    points = [(xv, yv) for xv, yv in points if xv is not None and yv is not None][:limit]
    if len(points) < 2:
        return ""
    width = 760
    height = 320
    plot_left = 58
    plot_right = width - 24
    plot_top = 24
    plot_bottom = height - 58
    x_values = [item[0] for item in points]
    y_values = [item[1] for item in points]
    x_min, x_max = min(x_values), max(x_values)
    y_min, y_max = min(y_values), max(y_values)
    x_span = max(x_max - x_min, 1)
    y_span = max(y_max - y_min, 1)
    x_domain_min = x_min - x_span * 0.06
    x_domain_max = x_max + x_span * 0.06
    y_domain_min = y_min - y_span * 0.08
    y_domain_max = y_max + y_span * 0.08

    def sx(value: float) -> float:
        return plot_left + (plot_right - plot_left) * (value - x_domain_min) / max(x_domain_max - x_domain_min, 1)

    def sy(value: float) -> float:
        return plot_bottom - (plot_bottom - plot_top) * (value - y_domain_min) / max(y_domain_max - y_domain_min, 1)

    grid_lines = []
    for tick in _tick_values(y_min, y_max, 4):
        py = sy(tick)
        grid_lines.append(
            f'<line x1="{plot_left}" y1="{py:.1f}" x2="{plot_right}" y2="{py:.1f}" class="grid-line"></line>'
            f'<text x="{plot_left - 10}" y="{py + 4:.1f}" class="scatter-tick" text-anchor="end">{html.escape(_axis_number(tick))}</text>'
        )
    for tick in _tick_values(x_min, x_max, 4):
        px = sx(tick)
        grid_lines.append(
            f'<line x1="{px:.1f}" y1="{plot_top}" x2="{px:.1f}" y2="{plot_bottom}" class="grid-line"></line>'
            f'<text x="{px:.1f}" y="{plot_bottom + 20}" class="scatter-tick" text-anchor="middle">{html.escape(_axis_number(tick))}</text>'
        )

    circles = []
    for xv, yv in points:
        px = sx(xv)
        py = sy(yv)
        circles.append(
            f'<circle class="scatter-point" cx="{px:.1f}" cy="{py:.1f}" r="4.4">'
            f'<title>{html.escape(x)}: {html.escape(_axis_number(xv))}, {html.escape(y)}: {html.escape(_axis_number(yv))}</title>'
            "</circle>"
        )

    avg_x = sum(x_values) / len(x_values)
    avg_y = sum(y_values) / len(y_values)
    mean_lines = (
        f'<line x1="{sx(avg_x):.1f}" y1="{plot_top}" x2="{sx(avg_x):.1f}" y2="{plot_bottom}" class="scatter-mean"></line>'
        f'<line x1="{plot_left}" y1="{sy(avg_y):.1f}" x2="{plot_right}" y2="{sy(avg_y):.1f}" class="scatter-mean"></line>'
    )
    trend_markup = ""
    trend = _linear_trend(points, x_domain_min, x_domain_max)
    if trend:
        x1, y1, x2, y2 = trend
        trend_markup = (
            f'<line x1="{sx(x1):.1f}" y1="{sy(_clamp(y1, y_domain_min, y_domain_max)):.1f}" '
            f'x2="{sx(x2):.1f}" y2="{sy(_clamp(y2, y_domain_min, y_domain_max)):.1f}" class="scatter-trend"></line>'
        )
    corr = _correlation(points)
    summary = _chart_summary(
        [
            ("X", x),
            ("Y", y),
            ("Points", f"{len(points):,}"),
            ("Corr", f"{corr:.2f}" if corr is not None else "n/a"),
        ]
    )
    return f"""
<section class="panel">
  <h2>{html.escape(str(block.get("title") or "상관/산포"))}</h2>
  {summary}
  <div class="scatter-plot-wrap chart-zone">
    <svg class="scatter-chart" viewBox="0 0 {width} {height}" role="img">
      <rect x="{plot_left}" y="{plot_top}" width="{plot_right - plot_left}" height="{plot_bottom - plot_top}" class="plot-bg"></rect>
      {''.join(grid_lines)}
      <line x1="{plot_left}" y1="{plot_bottom}" x2="{plot_right}" y2="{plot_bottom}" class="axis"></line>
      <line x1="{plot_left}" y1="{plot_top}" x2="{plot_left}" y2="{plot_bottom}" class="axis"></line>
      {mean_lines}
      {trend_markup}
      {''.join(circles)}
      <text x="{(plot_left + plot_right) / 2:.1f}" y="{height - 12}" class="scatter-axis-title" text-anchor="middle">{html.escape(x)}</text>
      <text x="16" y="{(plot_top + plot_bottom) / 2:.1f}" class="scatter-axis-title" text-anchor="middle" transform="rotate(-90 16 {(plot_top + plot_bottom) / 2:.1f})">{html.escape(y)}</text>
    </svg>
  </div>
  <div class="chart-caption"><span>{html.escape(x)}: {_axis_number(x_min)} - {_axis_number(x_max)}</span><span>{html.escape(y)}: {_axis_number(y_min)} - {_axis_number(y_max)}</span></div>
</section>
"""


def _heatmap_matrix(block: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """두 범주 축의 교차값을 색상 강도로 보여주는 히트맵 표를 만듭니다."""

    policy = _dict(block.get("chart_policy"))
    x = str(block.get("x") or policy.get("x") or "")
    series = str(block.get("series") or policy.get("series") or "")
    y = str(block.get("y") or policy.get("y") or "")
    limit = _positive_int(block.get("limit") or policy.get("limit"), 8)
    if not x or not series:
        return ""
    matrix: dict[str, dict[str, float]] = {}
    row_totals: dict[str, float] = {}
    col_totals: dict[str, float] = {}
    for row in rows:
        row_label = str(row.get(x) or "(blank)")
        col_label = str(row.get(series) or "(blank)")
        value = _row_metric(row, y)
        matrix.setdefault(row_label, {})
        matrix[row_label][col_label] = matrix[row_label].get(col_label, 0) + value
        row_totals[row_label] = row_totals.get(row_label, 0) + value
        col_totals[col_label] = col_totals.get(col_label, 0) + value
    if not matrix:
        return ""
    row_labels = sorted(row_totals, key=row_totals.get, reverse=True)[:limit]
    col_labels = sorted(col_totals, key=col_totals.get, reverse=True)[:limit]
    max_value = max((matrix.get(row_label, {}).get(col_label, 0) for row_label in row_labels for col_label in col_labels), default=0) or 1
    header = "".join(f"<th>{html.escape(label)}</th>" for label in col_labels)
    body = []
    for row_label in row_labels:
        cells = []
        for col_label in col_labels:
            value = matrix.get(row_label, {}).get(col_label, 0)
            intensity = min(max(value / max_value, 0), 1)
            alpha = 0.08 + intensity * 0.78 if value else 0.03
            text_color = "#ffffff" if intensity >= 0.58 else "#17202a"
            cells.append(
                f'<td class="heatmap-cell" style="background:rgba(37,99,235,{alpha:.2f});color:{text_color}" '
                f'title="{html.escape(x)}={html.escape(row_label)}, {html.escape(series)}={html.escape(col_label)}, {html.escape(y or "count")}={html.escape(_format_number(value))}">'
                f"{html.escape(_format_number(value))}</td>"
            )
        total_value = row_totals.get(row_label, 0)
        body.append(f'<tr><th>{html.escape(row_label)}</th>{"".join(cells)}<td class="heatmap-total">{html.escape(_format_number(total_value))}</td></tr>')
    footer_cells = "".join(f'<td class="heatmap-total">{html.escape(_format_number(col_totals.get(col_label, 0)))}</td>' for col_label in col_labels)
    grand_total = sum(row_totals.get(row_label, 0) for row_label in row_labels)
    summary = _chart_summary(
        [
            ("Rows", x),
            ("Columns", series),
            ("Metric", y or "count"),
            ("Max cell", _format_number(max_value)),
        ]
    )
    return f"""
<section class="panel">
  <h2>{html.escape(str(block.get("title") or "교차 히트맵"))}</h2>
  {summary}
  <div class="heatmap-wrap chart-zone">
    <table class="heatmap-table">
      <thead><tr><th>{html.escape(x)} \\ {html.escape(series)}</th>{header}<th class="heatmap-total">Total</th></tr></thead>
      <tbody>{''.join(body)}</tbody>
      <tfoot><tr><th>Total</th>{footer_cells}<td class="heatmap-total">{html.escape(_format_number(grand_total))}</td></tr></tfoot>
    </table>
  </div>
</section>
"""


def _table(block: dict[str, Any], rows: list[dict[str, Any]], fallback_columns: list[str]) -> str:
    """상세표/랭킹표/예외표 등 표 형태 블록을 렌더링합니다."""

    policy = _dict(block.get("table_policy"))
    columns = _strings(block.get("columns")) or _strings(policy.get("columns")) or fallback_columns[:12]
    sort = _dict(block.get("sort"))
    if not sort and policy.get("sort_by"):
        sort = {"by": policy.get("sort_by"), "direction": policy.get("sort_direction") or "desc"}
    limit = _positive_int(block.get("limit") or policy.get("limit"), 50)
    show_row_numbers = bool(block.get("show_row_numbers") or policy.get("show_row_numbers"))
    highlight_rules = _list(block.get("highlight_rules"))
    table_rows = deepcopy(rows)
    sort_by = str(sort.get("by") or "")
    if sort_by:
        # 숫자 컬럼 정렬을 우선 지원합니다. 숫자로 해석되지 않으면 0으로 처리됩니다.
        reverse = str(sort.get("direction") or "desc").lower() != "asc"
        table_rows = sorted(table_rows, key=lambda row: _number(row.get(sort_by)) or 0, reverse=reverse)
    table_rows = table_rows[:limit]
    if not columns or not table_rows:
        return ""
    header = ("<th>#</th>" if show_row_numbers else "") + "".join(f"<th>{html.escape(column)}</th>" for column in columns)
    body = []
    for index, row in enumerate(table_rows, start=1):
        row_class = _row_class(row, highlight_rules)
        row_number = f"<td>{index}</td>" if show_row_numbers else ""
        cells = "".join(
            f'<td class="{"num-cell" if _number(row.get(column)) is not None else "text-cell"}">{html.escape(_cell(row.get(column)))}</td>'
            for column in columns
        )
        body.append(f'<tr class="{row_class}">{row_number}{cells}</tr>')
    return f"""
<section class="panel">
  <h2>{html.escape(str(block.get("title") or "상세 데이터"))}</h2>
  <div class="table-wrap">
    <table>
      <thead><tr>{header}</tr></thead>
      <tbody>{''.join(body)}</tbody>
    </table>
  </div>
</section>
"""


def _insight_bullets(block: dict[str, Any], payload: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """LLM 또는 기본 로직이 만든 핵심 해석 문장을 bullet 목록으로 표시합니다."""

    data = _dict(_dict(payload.get("api_response")).get("data"))
    plan = _dict(payload.get("report_plan"))
    narrative = _dict(plan.get("narrative"))
    profile = _dict(payload.get("data_profile"))
    groups = _dict(profile.get("column_groups"))
    lines = _strings(block.get("lines")) or _strings(narrative.get("key_findings"))
    if not lines:
        lines = [
            f"총 {int(data.get('row_count') or len(rows)):,}건의 데이터를 기준으로 구성했습니다.",
            f"숫자 지표 {len(_strings(groups.get('numeric_columns')))}개와 비교 기준 {len(_strings(groups.get('dimension_columns')))}개를 감지했습니다.",
        ]
        if _strings(groups.get("time_columns")):
            lines.append("시간 컬럼이 있어 추이형 블록을 함께 구성할 수 있습니다.")
    summary = str(narrative.get("executive_summary") or "").strip()
    summary_markup = f'<p class="block-lead">{html.escape(summary)}</p>' if summary else ""
    return f"""
<section class="panel">
  <h2>{html.escape(str(block.get("title") or "핵심 해석"))}</h2>
  {summary_markup}
  <ul class="insight-list">{''.join(f'<li>{html.escape(line)}</li>' for line in lines)}</ul>
</section>
"""


def _recommendations(block: dict[str, Any], payload: dict[str, Any]) -> str:
    """다음 확인 사항이나 권장 조치를 bullet 목록으로 표시합니다."""

    plan = _dict(payload.get("report_plan"))
    narrative = _dict(plan.get("narrative"))
    lines = _strings(block.get("items")) or _strings(narrative.get("recommendations"))
    if not lines:
        lines = ["상세 row가 필요한 경우 원본 데이터 또는 data_ref 기반 전체 조회를 확인하세요.", "공유 전 민감정보 컬럼이 포함되어 있지 않은지 점검하세요."]
    return f"""
<section class="panel">
  <h2>{html.escape(str(block.get("title") or "다음 확인 사항"))}</h2>
  <ul class="insight-list">{''.join(f'<li>{html.escape(line)}</li>' for line in lines)}</ul>
</section>
"""


def _method_note(block: dict[str, Any], payload: dict[str, Any]) -> str:
    """리포트 생성 기준과 caveat를 하단 안내문으로 표시합니다."""

    plan = _dict(payload.get("report_plan"))
    narrative = _dict(plan.get("narrative"))
    source = str(plan.get("plan_source") or "deterministic")
    caveats = _strings(narrative.get("caveats")) + _strings(narrative.get("data_quality_notes"))
    caveat_markup = "".join(f"<li>{html.escape(item)}</li>" for item in caveats)
    return f"""
<section class="method-note">
  <h2>{html.escape(str(block.get("title") or "생성 기준"))}</h2>
  <p>이 HTML은 허용된 block registry와 renderer로 생성되었습니다. Plan source: {html.escape(source)}. LLM raw HTML은 삽입하지 않습니다.</p>
  {f'<ul class="insight-list">{caveat_markup}</ul>' if caveat_markup else ''}
</section>
"""


def _decorate_block_markup(block: dict[str, Any], markup: str) -> str:
    """렌더링된 블록 HTML에 설명문, insight, annotation, footnote를 추가합니다."""

    if not markup:
        return ""
    description = str(block.get("description") or "").strip()
    insight = str(block.get("insight") or "").strip()
    lead_parts = [text for text in (description, insight) if text]
    if lead_parts and "</h2>" in markup:
        lead = "".join(f'<p class="block-lead">{html.escape(text)}</p>' for text in lead_parts)
        markup = markup.replace("</h2>", f"</h2>{lead}", 1)

    annotations = _annotation_markup(_list(block.get("annotations")))
    footnote = str(block.get("footnote") or "").strip()
    footer = annotations
    if footnote:
        footer += f'<p class="block-footnote">{html.escape(footnote)}</p>'
    if footer and "</section>" in markup:
        markup = markup.replace("</section>", f"{footer}</section>", 1)
    return markup


def _annotation_markup(annotations: list[Any]) -> str:
    """annotation 설정을 작은 chip UI HTML로 바꿉니다."""

    chips = []
    for item in annotations:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        value = str(item.get("value") or "").strip()
        tone = _safe_token(item.get("tone"), {"info", "positive", "warning", "danger", "neutral"}, "info")
        if not label and not value:
            continue
        text = f"{label}: {value}" if label and value else label or value
        chips.append(f'<span class="annotation-chip tone-{tone}">{html.escape(text)}</span>')
    return f'<div class="annotation-list">{"".join(chips)}</div>' if chips else ""


def _row_class(row: dict[str, Any], rules: list[Any]) -> str:
    """highlight_rules에 맞는 row라면 강조 CSS class를 반환합니다."""

    for rule in rules:
        if isinstance(rule, dict) and _rule_matches(row, rule):
            tone = _safe_token(rule.get("tone"), {"info", "positive", "warning", "danger", "neutral"}, "warning")
            return f"row-{tone}"
    return ""


def _filter_rows(rows: list[dict[str, Any]], block: dict[str, Any]) -> list[dict[str, Any]]:
    """block.filter_rules 조건에 맞는 row만 남깁니다."""

    rules = [rule for rule in _list(block.get("filter_rules")) if isinstance(rule, dict)]
    if not rules:
        return rows
    logic = _safe_token(block.get("filter_logic"), {"and", "or"}, "and")
    result = []
    for row in rows:
        matches = [_rule_matches(row, rule) for rule in rules]
        if (logic == "or" and any(matches)) or (logic == "and" and all(matches)):
            result.append(row)
    return result


def _rule_matches(row: dict[str, Any], rule: dict[str, Any]) -> bool:
    """하나의 row가 강조 규칙과 일치하는지 비교합니다."""

    column = str(rule.get("column") or "")
    operator = str(rule.get("operator") or "eq").lower()
    actual = row.get(column)
    expected = rule.get("value")
    actual_number = _number(actual)
    expected_number = _number(expected)
    if operator in {"gt", "gte", "lt", "lte"} and actual_number is not None and expected_number is not None:
        if operator == "gt":
            return actual_number > expected_number
        if operator == "gte":
            return actual_number >= expected_number
        if operator == "lt":
            return actual_number < expected_number
        if operator == "lte":
            return actual_number <= expected_number
    actual_text = str(actual or "")
    if operator in {"in", "not_in"}:
        expected_values = expected if isinstance(expected, list) else [expected]
        expected_texts = {str(item or "") for item in expected_values}
        matched = actual_text in expected_texts
        return not matched if operator == "not_in" else matched
    expected_text = str(expected or "")
    if operator == "contains":
        return expected_text.lower() in actual_text.lower()
    if operator == "ne":
        return actual_text != expected_text
    return actual_text == expected_text


def _wrap_block(block: dict[str, Any], markup: str, anchor_id: str = "") -> str:
    """각 블록을 12컬럼 grid에서 배치할 수 있는 wrapper div로 감쌉니다."""

    width = _safe_token(block.get("width"), {"full", "two_third", "half", "third"}, "full")
    density = _safe_token(block.get("density"), {"compact", "comfortable"}, "comfortable")
    font_scale = _safe_token(block.get("font_scale"), {"small", "normal", "large"}, "normal")
    emphasis = _safe_token(block.get("emphasis"), {"low", "medium", "high", "critical"}, "medium")
    style = _dict(block.get("style"))
    accent = _safe_color(style.get("accent_color") or block.get("accent_color"))
    style_attr = f' style="--block-accent:{accent};"' if accent else ""
    classes = " ".join(
        [
            "report-block",
            f"block-{width}",
            f"density-{density}",
            f"font-{font_scale}",
            f"emphasis-{emphasis}",
        ]
    )
    id_attr = f' id="{html.escape(anchor_id)}"' if anchor_id else ""
    return f'<div{id_attr} class="{classes}"{style_attr}>{markup}</div>'


def _side_nav_markup(title: str, nav_items: list[dict[str, str]], generated_at: str = "") -> str:
    """Material admin 형태의 좌측 report drawer를 만듭니다."""

    brand_title = str(title or "Report").strip()
    nav_links = []
    for index, item in enumerate(nav_items[:10], start=1):
        anchor_id = str(item.get("anchor_id") or "").strip()
        item_title = str(item.get("title") or "Section").strip()
        section = str(item.get("section") or "").strip()
        if not anchor_id:
            continue
        nav_links.append(
            f"""
<a class="side-link" href="#{html.escape(anchor_id)}" title="{html.escape(item_title)}">
  <i>{index}</i>
  <span class="side-link-copy">
    <strong>{html.escape(item_title)}</strong>
    {f'<small>{html.escape(section)}</small>' if section else ''}
  </span>
</a>
"""
        )
    if not nav_links:
        nav_links.append('<a class="side-link" href="#"><i>1</i><span class="side-link-copy"><strong>Overview</strong><small>Report</small></span></a>')
    return f"""
<aside class="side-nav">
  <div class="side-nav-content">
    <div class="side-brand">
      <span class="side-brand-mark">MR</span>
      <div class="side-brand-copy">
        <span>Material Report</span>
        <small>{html.escape(brand_title)}</small>
      </div>
    </div>
    <div class="side-section-title">Report Sections</div>
    <nav>
      {''.join(nav_links)}
    </nav>
  </div>
  <div class="side-footer">
    <span>구현 시간</span>
    <strong>{html.escape(generated_at)}</strong>
  </div>
</aside>
"""


def _block_nav_label(block_id: str) -> str:
    """block_id를 좌측 navigation의 짧은 섹션 라벨로 변환합니다."""

    mapping = {
        "report_header": "Overview",
        "scope_summary": "Context",
        "kpi_card_grid": "KPI",
        "metric_delta_card_grid": "KPI",
        "trend_line_chart": "Trend",
        "comparison_bar_chart": "Comparison",
        "grouped_bar_chart": "Comparison",
        "stacked_comparison_bar": "Composition",
        "donut_chart": "Composition",
        "distribution_histogram": "Distribution",
        "scatter_plot": "Relationship",
        "heatmap_matrix": "Matrix",
        "pivot_matrix_table": "Matrix",
        "ranking_table": "Ranking",
        "rank_change_table": "Ranking",
        "detail_data_table": "Detail",
        "outlier_exception_table": "Exceptions",
        "insight_bullets": "Insights",
        "recommendation_list": "Actions",
        "method_note": "Method",
    }
    return mapping.get(block_id, "Section")


def _document(title: str, subtitle: str, body: list[str], plan: dict[str, Any], nav_items: list[dict[str, str]] | None = None) -> str:
    """완성된 블록 HTML 목록을 하나의 독립 실행 HTML 문서로 감쌉니다.

    외부 CSS/JS 파일 없이 Playground나 다운로드 파일만으로 바로 열리게 하려고
    필요한 CSS를 `<style>` 태그 안에 함께 넣습니다.
    """

    visual_style = _dict(plan.get("visual_style"))
    main_style = _visual_style_attr(visual_style)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    nav_markup = _side_nav_markup(title, nav_items or [], generated_at)
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: light; --ink:#202124; --muted:#6f7785; --line:#dde3ec; --surface:#f5f7fb; --canvas:#f4f6f9; --drawer:#ffffff; --nav-ink:#2f3744; --nav-muted:#7b8493; --nav-soft:#98a2b3; --accent:#0f766e; --accent-2:#2563eb; --appbar:#4e73df; --appbar-mid:#3f57e8; --appbar-2:#5b2fd3; --warn:#9f580a; --nav-width:312px; --report-width:1320px; --panel-pad:22px; --grid-gap:20px; --h1-size:32px; --h2-size:19px; --body-size:14px; --elevation-1:0 1px 2px rgba(60,64,67,.12), 0 1px 3px rgba(60,64,67,.16); --elevation-2:0 8px 24px rgba(60,64,67,.14); }}
    * {{ box-sizing:border-box; }}
    html {{ scroll-behavior:smooth; }}
    body {{ margin:0; font-family:-apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans KR", "Malgun Gothic", Roboto, Arial, sans-serif; font-size:var(--body-size); color:var(--ink); background:var(--canvas); line-height:1.5; }}
    .nav-toggle-input {{ position:fixed; width:1px; height:1px; opacity:0; pointer-events:none; }}
    .nav-toggle-button {{ position:fixed; top:29px; left:calc(var(--nav-width) - 64px); z-index:40; width:38px; height:38px; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:4px; border:1px solid #dfe7ff; border-radius:12px; background:#f4f6ff; color:var(--appbar); box-shadow:none; cursor:pointer; transition:left .22s ease, top .22s ease, transform .22s ease, background .22s ease, box-shadow .22s ease; }}
    .nav-toggle-button:hover {{ transform:translateY(-1px); background:#edf2ff; box-shadow:0 8px 18px rgba(78,115,223,.16); }}
    .nav-toggle-button span {{ display:block; width:17px; height:2px; border-radius:999px; background:currentColor; }}
    .app-shell {{ min-height:100vh; display:grid; grid-template-columns:var(--nav-width) minmax(0, 1fr); transition:grid-template-columns .22s ease; }}
    .nav-toggle-input:checked ~ .nav-toggle-button {{ top:22px; left:18px; background:#fff; box-shadow:0 10px 26px rgba(60,64,67,.18); }}
    .nav-toggle-input:checked ~ .app-shell {{ grid-template-columns:0 minmax(0, 1fr); }}
    .nav-toggle-input:checked ~ .app-shell .side-nav {{ width:0; border-right:0; box-shadow:none; opacity:0; pointer-events:none; }}
    .nav-toggle-input:checked ~ .app-shell main {{ padding-left:76px; }}
    .side-nav {{ position:sticky; top:0; height:100vh; width:var(--nav-width); min-width:0; overflow:hidden; background:linear-gradient(180deg, #ffffff 0%, #fbfcff 68%, #ffffff 100%); border-right:1px solid #dfe5ef; box-shadow:8px 0 24px rgba(60,64,67,.06); z-index:20; display:flex; flex-direction:column; opacity:1; transition:width .22s ease, opacity .18s ease, border-color .22s ease, box-shadow .22s ease; }}
    .side-nav-content {{ flex:1; min-height:0; overflow:auto; padding-bottom:16px; scrollbar-width:thin; scrollbar-color:#cbd5e1 transparent; }}
    .side-brand {{ min-height:92px; display:flex; align-items:center; gap:14px; padding:18px 78px 18px 28px; border-bottom:1px solid #edf1f6; color:var(--nav-ink); letter-spacing:0; }}
    .side-brand-mark {{ flex:0 0 auto; display:inline-grid; place-items:center; width:44px; height:44px; border-radius:13px; background:linear-gradient(135deg, var(--appbar), var(--appbar-2)); color:#fff; font-size:16px; font-weight:760; letter-spacing:0; box-shadow:0 8px 18px rgba(78, 115, 223, .30); }}
    .side-brand-copy {{ min-width:0; }}
    .side-brand-copy > span {{ display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--nav-ink); font-size:15px; line-height:1.25; font-weight:720; letter-spacing:0; }}
    .side-brand small {{ display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; margin-top:4px; color:var(--nav-muted); font-size:12px; line-height:1.42; font-weight:560; letter-spacing:0; word-break:keep-all; }}
    .side-section-title {{ margin:24px 28px 10px; color:var(--nav-soft); font-size:11px; line-height:1.2; font-weight:760; letter-spacing:0; text-transform:uppercase; }}
    .side-link {{ display:grid; grid-template-columns:32px minmax(0,1fr); gap:12px; align-items:start; min-height:54px; margin:3px 18px; padding:10px 12px; border:1px solid transparent; border-radius:12px; color:var(--nav-muted); text-decoration:none; font-weight:620; transition:background .18s ease, border-color .18s ease, box-shadow .18s ease, color .18s ease; }}
    .side-link:hover {{ background:#f5f7ff; border-color:#e0e7ff; color:var(--nav-ink); box-shadow:0 8px 18px rgba(78,115,223,.08); }}
    .side-link i {{ display:inline-grid; place-items:center; width:30px; height:30px; border-radius:9px; background:#f0f4f8; color:#647084; font-style:normal; font-size:12px; font-weight:720; letter-spacing:0; }}
    .side-link:hover i {{ background:linear-gradient(135deg, var(--appbar), var(--appbar-2)); color:#fff; }}
    .side-link-copy {{ min-width:0; display:block; }}
    .side-link strong {{ display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; color:inherit; font-size:13px; line-height:1.38; font-weight:650; letter-spacing:0; word-break:keep-all; overflow-wrap:anywhere; }}
    .side-link small {{ display:block; margin-top:3px; color:var(--nav-soft); font-size:10.5px; line-height:1.25; font-weight:650; letter-spacing:0; }}
    .side-footer {{ margin-top:auto; padding:16px 28px 22px; border-top:1px solid #edf1f6; background:linear-gradient(180deg, #ffffff, #fbfcff); color:var(--muted); }}
    .side-footer span {{ display:block; margin-bottom:4px; font-size:10.5px; font-weight:720; letter-spacing:0; text-transform:uppercase; }}
    .side-footer strong {{ display:block; color:#475569; font-size:12px; font-weight:620; font-variant-numeric:tabular-nums; }}
    .workspace {{ min-width:0; display:flex; flex-direction:column; }}
    main {{ width:100%; max-width:var(--report-width); margin:0 auto; padding:28px 32px 46px 56px; transition:padding-left .22s ease; }}
    .report-grid {{ display:grid; grid-template-columns:repeat(12, minmax(0, 1fr)); gap:var(--grid-gap); align-items:stretch; grid-auto-flow:row dense; }}
    .report-block {{ min-width:0; display:flex; flex-direction:column; font-size:var(--body-size); --block-accent:var(--accent); }}
    .block-full {{ grid-column:span 12; }}
    .block-two_third {{ grid-column:span 8; }}
    .block-half {{ grid-column:span 6; }}
    .block-third {{ grid-column:span 4; }}
    .report-block > .hero, .report-block > .panel, .report-block > .notice, .report-block > .method-note {{ margin-top:0; height:100%; flex:1; }}
    .hero {{ display:flex; justify-content:space-between; gap:22px; align-items:flex-end; padding:30px; border:0; border-radius:8px; background:linear-gradient(135deg, var(--appbar) 0%, var(--appbar-mid) 48%, var(--appbar-2) 100%); color:#fff; box-shadow:0 14px 32px rgba(78, 115, 223, .24); }}
    .eyebrow {{ margin:0 0 8px; font-size:12px; font-weight:800; color:rgba(255,255,255,.76); letter-spacing:.04em; }}
    h1 {{ margin:0; font-size:var(--h1-size); line-height:1.2; }}
    h2 {{ margin:0 0 15px; font-size:var(--h2-size); letter-spacing:0; }}
    .hero .subtitle {{ color:rgba(255,255,255,.72); }}
    .subtitle {{ margin:10px 0 0; color:var(--muted); max-width:760px; }}
    .hero-meta {{ display:flex; flex-direction:column; gap:6px; font-size:13px; color:rgba(255,255,255,.72); text-align:right; }}
    .hero-meta span {{ min-height:26px; display:inline-flex; justify-content:flex-end; align-items:center; padding:4px 9px; border-radius:999px; background:rgba(255,255,255,.16); border:1px solid rgba(255,255,255,.18); }}
    .panel, .notice, .method-note {{ margin-top:22px; padding:var(--panel-pad); border:0; border-radius:8px; background:#fff; display:flex; flex-direction:column; box-shadow:var(--elevation-1); }}
    .panel h2 {{ position:relative; padding-left:13px; }}
    .panel h2::before {{ content:""; position:absolute; left:0; top:.18em; bottom:.18em; width:4px; border-radius:999px; background:var(--block-accent); }}
    .notice {{ background:#fff8ed; border:1px solid #f0c98b; color:#4c3104; }}
    .method-note {{ background:#fff; color:var(--muted); border-top:3px solid #e2e8f0; }}
    .block-lead {{ margin:0 0 12px; color:var(--muted); max-width:72ch; }}
    .block-footnote {{ margin:12px 0 0; font-size:12px; color:var(--muted); }}
    .annotation-list {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:12px; }}
    .annotation-chip {{ display:inline-flex; align-items:center; min-height:26px; padding:4px 9px; border-radius:999px; font-size:12px; font-weight:650; background:#edf2f7; color:#334155; }}
    .tone-info {{ background:#e0f2fe; color:#075985; }}
    .tone-positive {{ background:#dcfce7; color:#166534; }}
    .tone-warning {{ background:#fef3c7; color:#92400e; }}
    .tone-danger {{ background:#fee2e2; color:#991b1b; }}
    .tone-neutral {{ background:#edf2f7; color:#334155; }}
    .density-compact {{ --panel-pad:14px; }}
    .density-compact table {{ font-size:12px; }}
    .density-compact th, .density-compact td {{ padding:7px 8px; }}
    .font-small {{ --h1-size:28px; --h2-size:17px; --body-size:13px; }}
    .font-large {{ --h1-size:38px; --h2-size:22px; --body-size:15px; }}
    .emphasis-high > .panel, .emphasis-high > .notice {{ box-shadow:0 8px 24px rgba(15, 23, 42, .08); }}
    .emphasis-critical > .panel, .emphasis-critical > .notice {{ border-color:#e5a3a3; background:#fff7f7; box-shadow:0 8px 24px rgba(15, 23, 42, .08); }}
    .emphasis-high h2, .emphasis-critical h2 {{ color:var(--block-accent); }}
    .chart-zone {{ flex:1; min-height:220px; }}
    .block-third .chart-zone, .block-half .chart-zone {{ min-height:240px; }}
    .scope-grid, .kpi-grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(170px, 1fr)); gap:12px; }}
    .scope-grid div, .kpi-card {{ border:1px solid #e3e8f0; border-radius:8px; padding:16px; background:#fff; box-shadow:0 1px 2px rgba(60,64,67,.08); }}
    .scope-grid b, .kpi-card span {{ display:block; font-size:12px; color:var(--muted); margin-bottom:8px; }}
    .scope-grid span, .kpi-card strong {{ display:block; font-size:22px; font-weight:750; overflow-wrap:anywhere; }}
    .kpi-card small {{ display:block; margin-top:6px; color:var(--muted); }}
    .bar-chart {{ display:grid; gap:10px; align-content:center; }}
    .bar-row {{ display:grid; grid-template-columns:minmax(110px, 180px) 1fr minmax(70px, auto); gap:10px; align-items:center; }}
    .bar-label, .bar-value {{ font-size:13px; color:var(--muted); overflow-wrap:anywhere; }}
    .bar-track {{ height:14px; background:#edf2f7; border-radius:999px; overflow:hidden; }}
    .bar-fill {{ height:100%; background:linear-gradient(90deg, var(--accent), var(--accent-2)); }}
    .bar-fill.solid {{ background:var(--accent); }}
    .donut-layout {{ display:grid; grid-template-columns:minmax(160px, 220px) 1fr; gap:18px; align-items:center; }}
    .donut-visual {{ width:min(220px, 100%); aspect-ratio:1; border-radius:50%; position:relative; margin:auto; box-shadow:inset 0 0 0 1px var(--line); }}
    .donut-hole {{ position:absolute; inset:24%; border-radius:50%; background:#fff; display:flex; flex-direction:column; align-items:center; justify-content:center; text-align:center; border:1px solid var(--line); }}
    .donut-hole strong {{ font-size:24px; }}
    .donut-hole span {{ color:var(--muted); font-size:12px; }}
    .chart-legend {{ display:grid; gap:8px; align-content:center; }}
    .legend-item {{ display:grid; grid-template-columns:12px minmax(0, 1fr) auto auto; gap:8px; align-items:center; font-size:12px; color:var(--muted); }}
    .legend-swatch {{ width:10px; height:10px; border-radius:999px; }}
    .legend-label {{ color:var(--ink); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
    .grouped-chart {{ display:grid; gap:14px; }}
    .grouped-category {{ display:grid; grid-template-columns:minmax(100px, 160px) 1fr; gap:12px; align-items:start; }}
    .grouped-label {{ font-size:13px; font-weight:650; color:var(--ink); overflow-wrap:anywhere; }}
    .grouped-bars {{ display:grid; gap:6px; }}
    .grouped-metric-row {{ display:grid; grid-template-columns:minmax(80px, 130px) 1fr minmax(64px, auto); gap:8px; align-items:center; font-size:12px; color:var(--muted); }}
    .grouped-metric-row b {{ color:var(--ink); text-align:right; }}
    .stacked-chart {{ display:grid; gap:10px; align-content:center; }}
    .stacked-legend {{ display:flex; flex-wrap:wrap; gap:8px 12px; margin-bottom:4px; }}
    .stacked-legend-item {{ display:inline-flex; gap:6px; align-items:center; font-size:12px; color:var(--muted); }}
    .stacked-legend-item i {{ width:10px; height:10px; border-radius:2px; display:inline-block; }}
    .stacked-row {{ display:grid; grid-template-columns:minmax(100px, 170px) 1fr minmax(64px, auto); gap:10px; align-items:center; }}
    .stacked-label, .stacked-total {{ font-size:13px; color:var(--muted); overflow-wrap:anywhere; }}
    .stacked-track {{ display:flex; height:18px; overflow:hidden; border-radius:999px; background:#e7edf5; }}
    .stacked-segment {{ height:100%; min-width:1px; }}
    .chart-summary {{ display:flex; flex-wrap:wrap; gap:8px; margin:0 0 12px; }}
    .chart-summary span {{ display:inline-flex; align-items:center; gap:6px; min-height:28px; padding:5px 9px; border:1px solid #dbe3ee; border-radius:999px; background:#f8fafc; color:#17202a; font-size:12px; font-variant-numeric:tabular-nums; }}
    .chart-summary b {{ color:var(--muted); font-weight:700; }}
    .axis-label {{ color:var(--muted); font-size:12px; font-weight:700; }}
    .histogram-chart {{ display:grid; gap:8px; }}
    .histogram-plot {{ display:grid; grid-template-columns:42px minmax(0, 1fr); gap:10px; align-items:stretch; }}
    .histogram-y-axis {{ display:flex; flex-direction:column; justify-content:space-between; text-align:right; color:var(--muted); font-size:11px; font-variant-numeric:tabular-nums; padding:24px 0 44px; }}
    .histogram-grid {{ position:relative; display:grid; grid-template-columns:repeat(var(--bins), minmax(0, 1fr)); gap:8px; min-height:260px; padding:22px 12px 42px; overflow:hidden; border:1px solid var(--line); border-radius:8px; background:repeating-linear-gradient(to bottom, #f8fafc 0, #f8fafc 57px, #e8edf5 58px), linear-gradient(180deg, #fbfcfe, #f5f7fb); }}
    .histogram-bin {{ position:relative; z-index:1; display:grid; grid-template-rows:22px minmax(118px, 1fr) 34px; gap:5px; align-items:end; min-width:0; }}
    .histogram-value {{ align-self:end; text-align:center; color:#475569; font-size:11px; font-weight:700; font-variant-numeric:tabular-nums; }}
    .histogram-bar-shell {{ height:100%; min-height:118px; display:flex; align-items:flex-end; }}
    .histogram-bar {{ width:100%; min-height:2px; border-radius:7px 7px 2px 2px; background:linear-gradient(180deg, var(--accent-2), var(--block-accent)); box-shadow:0 8px 18px rgba(15, 23, 42, .12); }}
    .histogram-bar.is-zero {{ opacity:.35; background:#cbd5e1; box-shadow:none; }}
    .histogram-bin-label {{ align-self:start; text-align:center; color:var(--muted); font-size:10px; line-height:1.15; overflow-wrap:anywhere; font-variant-numeric:tabular-nums; }}
    .histogram-marker {{ position:absolute; top:8px; bottom:39px; z-index:2; border-left:2px dashed rgba(220, 38, 38, .58); pointer-events:none; }}
    .histogram-marker span {{ position:absolute; top:0; left:0; transform:translateX(-50%); white-space:nowrap; padding:2px 6px; border-radius:999px; background:#fff1f2; color:#9f1239; border:1px solid #fecdd3; font-size:11px; font-weight:750; }}
    .histogram-x-label {{ text-align:center; margin-left:52px; }}
    .line-chart {{ width:100%; height:auto; max-height:300px; background:#fff; border:1px solid var(--line); border-radius:8px; }}
    .line-chart .axis {{ stroke:#94a3b8; stroke-width:1; }}
    .line-chart .trend-line {{ fill:none; stroke:var(--accent-2); stroke-width:3; }}
    .line-chart circle {{ fill:#ffffff; stroke:var(--accent-2); stroke-width:2; }}
    .line-chart .point-value {{ fill:#334155; stroke:#ffffff; stroke-width:4px; paint-order:stroke; font-size:11px; font-weight:720; font-variant-numeric:tabular-nums; }}
    .scatter-plot-wrap {{ min-height:280px; }}
    .scatter-chart {{ display:block; width:100%; height:auto; max-height:360px; background:#ffffff; border:1px solid var(--line); border-radius:8px; }}
    .scatter-chart .plot-bg {{ fill:#f8fafc; stroke:#dbe3ee; stroke-width:1; }}
    .scatter-chart .grid-line {{ stroke:#e4eaf2; stroke-width:1; }}
    .scatter-chart .axis {{ stroke:#64748b; stroke-width:1.2; }}
    .scatter-chart .scatter-mean {{ stroke:#f59e0b; stroke-width:1.5; stroke-dasharray:5 5; opacity:.9; }}
    .scatter-chart .scatter-trend {{ stroke:var(--accent-2); stroke-width:2.4; opacity:.92; }}
    .scatter-chart .scatter-point {{ fill:var(--block-accent); fill-opacity:.78; stroke:#ffffff; stroke-width:1.4; }}
    .scatter-chart .scatter-tick {{ fill:#64748b; font-size:11px; font-variant-numeric:tabular-nums; }}
    .scatter-chart .scatter-axis-title {{ fill:#334155; font-size:12px; font-weight:750; }}
    .chart-caption {{ display:flex; justify-content:space-between; color:var(--muted); font-size:13px; margin-top:6px; }}
    .heatmap-wrap {{ overflow:auto; border:1px solid var(--line); border-radius:8px; background:#fff; }}
    .heatmap-table {{ min-width:max-content; border-collapse:separate; border-spacing:0; }}
    .heatmap-table th, .heatmap-table td {{ text-align:center; white-space:nowrap; border-right:1px solid #e2e8f0; border-bottom:1px solid #e2e8f0; font-variant-numeric:tabular-nums; }}
    .heatmap-table thead th {{ background:#f1f5f9; color:#334155; }}
    .heatmap-table tbody th {{ background:#f8fafc; position:sticky; left:0; z-index:1; text-align:left; font-weight:750; }}
    .heatmap-table tfoot th, .heatmap-table tfoot td, .heatmap-total {{ background:#eef3f8; color:#17202a; font-weight:800; }}
    .heatmap-cell {{ font-weight:750; }}
    .table-wrap {{ overflow:auto; border:1px solid var(--line); border-radius:8px; }}
    table {{ border-collapse:collapse; min-width:100%; font-size:13px; }}
    th, td {{ padding:9px 10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }}
    th {{ position:sticky; top:0; background:#eef3f8; font-weight:700; }}
    .num-cell {{ text-align:right; font-variant-numeric:tabular-nums; }}
    .text-cell {{ overflow-wrap:anywhere; }}
    tr.row-info td {{ background:#f0f9ff; }}
    tr.row-positive td {{ background:#f0fdf4; }}
    tr.row-warning td {{ background:#fffbeb; }}
    tr.row-danger td {{ background:#fef2f2; }}
    tr.row-neutral td {{ background:#f8fafc; }}
    .insight-list {{ margin:0; padding-left:20px; }}
    @media (max-width: 1100px) {{ .app-shell {{ grid-template-columns:1fr; }} .side-nav, .nav-toggle-button {{ display:none; }} }}
    @media (max-width: 900px) {{ .block-half, .block-third, .block-two_third {{ grid-column:span 12; }} }}
    @media (max-width: 720px) {{ main {{ padding:18px 14px 34px; }} .hero {{ display:block; padding:24px 20px; }} .hero-meta {{ text-align:left; margin-top:16px; }} .bar-row, .grouped-category, .grouped-metric-row, .stacked-row, .donut-layout {{ grid-template-columns:1fr; }} .chart-zone {{ min-height:180px; }} .histogram-plot {{ grid-template-columns:32px minmax(0, 1fr); }} .histogram-grid {{ gap:4px; padding-left:8px; padding-right:8px; }} .histogram-bin-label {{ font-size:9px; }} }}
  </style>
</head>
<body>
  <input class="nav-toggle-input" type="checkbox" id="nav-toggle">
  <label class="nav-toggle-button" for="nav-toggle" aria-label="목차 접기/펼치기">
    <span></span><span></span><span></span>
  </label>
  <div class="app-shell">
    {nav_markup}
    <div class="workspace">
      <main{main_style}>
        <div class="report-grid">
          {''.join(body)}
        </div>
      </main>
    </div>
  </div>
</body>
</html>"""


def _visual_style_attr(visual_style: dict[str, Any]) -> str:
    """visual_style 설정을 CSS 변수 style 속성으로 변환합니다."""

    declarations = []
    accent = _safe_color(visual_style.get("accent_color"))
    secondary = _safe_color(visual_style.get("secondary_color"))
    if accent:
        declarations.append(f"--accent:{accent}")
    if secondary:
        declarations.append(f"--accent-2:{secondary}")
    if str(visual_style.get("max_width") or "").lower() == "wide":
        declarations.append("--report-width:1380px")
    density = str(visual_style.get("density") or "").lower()
    if density == "compact":
        declarations.extend(["--panel-pad:14px", "--grid-gap:14px"])
    elif density == "comfortable":
        declarations.extend(["--panel-pad:20px", "--grid-gap:20px"])
    font_scale = str(visual_style.get("font_scale") or "").lower()
    if font_scale == "small":
        declarations.extend(["--h1-size:28px", "--h2-size:17px", "--body-size:13px"])
    elif font_scale == "large":
        declarations.extend(["--h1-size:38px", "--h2-size:22px", "--body-size:15px"])
    return f' style="{";".join(declarations)}"' if declarations else ""


def _safe_token(value: Any, allowed: set[str], fallback: str) -> str:
    """허용된 문자열 토큰만 통과시키고 나머지는 fallback으로 바꿉니다."""

    text = str(value or "").strip().lower().replace("-", "_")
    return text if text in allowed else fallback


def _safe_color(value: Any) -> str:
    """`#RRGGBB` 형식의 색상만 CSS에 넣도록 검증합니다."""

    text = str(value or "").strip()
    if len(text) == 7 and text.startswith("#") and all(ch in "0123456789abcdefABCDEF" for ch in text[1:]):
        return text
    return ""


def _aggregate(rows: list[dict[str, Any]], column: str, aggregation: str) -> Any:
    """KPI 카드와 차트에서 사용할 기본 집계값을 계산합니다."""

    values = [_number(row.get(column)) for row in rows]
    numbers = [value for value in values if value is not None]
    if aggregation == "count":
        return len(rows)
    if aggregation == "nunique":
        return len({str(row.get(column)) for row in rows if row.get(column) not in (None, "")})
    if not numbers:
        return 0
    if aggregation in {"avg", "average", "mean"}:
        return sum(numbers) / len(numbers)
    if aggregation == "min":
        return min(numbers)
    if aggregation == "max":
        return max(numbers)
    return sum(numbers)


def _group_sum(rows: list[dict[str, Any]], dimension: str, metric: str) -> list[tuple[str, float]]:
    """범주별 metric 합계를 계산하고 큰 값 순서로 정렬합니다."""

    grouped: dict[str, float] = {}
    for row in rows:
        label = str(row.get(dimension) or "(blank)")
        value = _number(row.get(metric)) or 0
        grouped[label] = grouped.get(label, 0) + value
    return sorted(grouped.items(), key=lambda item: item[1], reverse=True)


def _group_count(rows: list[dict[str, Any]], dimension: str) -> list[tuple[str, float]]:
    """범주별 row 개수를 계산하고 큰 값 순서로 정렬합니다."""

    grouped: dict[str, float] = {}
    for row in rows:
        label = str(row.get(dimension) or "(blank)")
        grouped[label] = grouped.get(label, 0) + 1
    return sorted(grouped.items(), key=lambda item: item[1], reverse=True)


def _top_group_labels(rows: list[dict[str, Any]], dimension: str, metrics: list[str], limit: int) -> list[str]:
    """여러 metric 합계 기준으로 상위 범주 label을 고릅니다."""

    totals: dict[str, float] = {}
    for row in rows:
        label = str(row.get(dimension) or "(blank)")
        value = sum((_number(row.get(metric)) or 0) for metric in metrics if metric)
        totals[label] = totals.get(label, 0) + value
    return [label for label, _ in sorted(totals.items(), key=lambda item: item[1], reverse=True)[:limit]]


def _row_metric(row: dict[str, Any], metric: str) -> float:
    """row 하나에서 metric 값을 숫자로 꺼냅니다. metric이 없으면 count용 1을 반환합니다."""

    if metric:
        return _number(row.get(metric)) or 0
    return 1.0


def _chart_color(index: int) -> str:
    """팔레트에서 index에 맞는 차트 색상을 순환 선택합니다."""

    return CHART_COLORS[index % len(CHART_COLORS)]


def _number(value: Any) -> float | None:
    """문자열/숫자 값을 float로 변환하고, 실패하면 None을 반환합니다."""

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value == value:
        return float(value)
    text = str(value or "").strip().replace(",", "").replace("%", "")
    try:
        return float(text) if text else None
    except Exception:
        return None


def _format_number(value: Any) -> str:
    """숫자를 리포트에 보기 좋은 문자열로 포맷합니다."""

    number = _number(value)
    if number is None:
        return str(value)
    if abs(number) >= 1000:
        return f"{number:,.0f}" if number.is_integer() else f"{number:,.2f}"
    return f"{number:.0f}" if number.is_integer() else f"{number:.2f}"


def _axis_number(value: Any) -> str:
    """차트 축/구간 라벨에 맞게 너무 길지 않은 숫자 문자열을 만듭니다."""

    number = _number(value)
    if number is None:
        return str(value)
    if abs(number) >= 1000:
        text = f"{number:,.0f}" if number.is_integer() else f"{number:,.1f}"
    elif abs(number) >= 100:
        text = f"{number:.1f}"
    else:
        text = f"{number:.2f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _range_label(start: float, end: float) -> str:
    """히스토그램 x축에 표시할 구간 라벨을 만듭니다."""

    start_text = _axis_number(start)
    end_text = _axis_number(end)
    return start_text if start_text == end_text else f"{start_text}-{end_text}"


def _median(values: list[float]) -> float:
    """정렬된 숫자 목록의 중앙값을 계산합니다."""

    if not values:
        return 0
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2


def _percent_position(value: float, min_value: float, max_value: float) -> float:
    """값을 0-100% 위치로 변환하고 차트 밖으로 나가지 않게 제한합니다."""

    span = max(max_value - min_value, 1)
    return _clamp((value - min_value) / span * 100, 0, 100)


def _tick_values(min_value: float, max_value: float, count: int) -> list[float]:
    """간단한 min/mid/max 기반 tick 값을 만듭니다."""

    if count <= 1 or min_value == max_value:
        return [min_value]
    ticks = [min_value + (max_value - min_value) * index / (count - 1) for index in range(count)]
    result: list[float] = []
    for tick in ticks:
        if not any(abs(tick - existing) < 1e-9 for existing in result):
            result.append(tick)
    return result


def _correlation(points: list[tuple[float, float]]) -> float | None:
    """산점도에 표시할 Pearson 상관계수를 계산합니다."""

    if len(points) < 2:
        return None
    avg_x = sum(x for x, _ in points) / len(points)
    avg_y = sum(y for _, y in points) / len(points)
    numerator = sum((x - avg_x) * (y - avg_y) for x, y in points)
    denom_x = sum((x - avg_x) ** 2 for x, _ in points)
    denom_y = sum((y - avg_y) ** 2 for _, y in points)
    denominator = (denom_x * denom_y) ** 0.5
    return numerator / denominator if denominator else None


def _linear_trend(points: list[tuple[float, float]], x_min: float, x_max: float) -> tuple[float, float, float, float] | None:
    """산점도 추세선의 양 끝 좌표를 값 기준으로 계산합니다."""

    if len(points) < 2:
        return None
    avg_x = sum(x for x, _ in points) / len(points)
    avg_y = sum(y for _, y in points) / len(points)
    denominator = sum((x - avg_x) ** 2 for x, _ in points)
    if not denominator:
        return None
    slope = sum((x - avg_x) * (y - avg_y) for x, y in points) / denominator
    intercept = avg_y - slope * avg_x
    return (x_min, slope * x_min + intercept, x_max, slope * x_max + intercept)


def _clamp(value: float, min_value: float, max_value: float) -> float:
    """숫자를 min/max 범위 안으로 제한합니다."""

    return max(min_value, min(max_value, value))


def _chart_summary(items: list[tuple[str, Any]]) -> str:
    """차트 위쪽에 보여줄 작은 메타/수치 chip 묶음을 만듭니다."""

    chips = []
    for label, value in items:
        value_text = str(value or "").strip()
        if not value_text:
            continue
        chips.append(f'<span><b>{html.escape(str(label))}</b>{html.escape(value_text)}</span>')
    return f'<div class="chart-summary">{"".join(chips)}</div>' if chips else ""


def _short_label(value: Any, limit: int) -> str:
    """내비게이션처럼 좁은 공간에 넣을 짧은 label을 만듭니다."""

    text = str(value or "").strip()
    return text if len(text) <= limit else text[: max(1, limit - 1)].rstrip() + "…"


def _cell(value: Any) -> str:
    """테이블 셀에 넣을 값을 문자열로 변환합니다."""

    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return _format_number(value) if _number(value) is not None else str(value)


def _filename_hint(title: Any) -> str:
    """리포트 제목/LLM 파일명 힌트를 다운로드 파일명 힌트로 쓸 수 있게 정리합니다."""

    safe = "".join(ch if ch.isalnum() else "_" for ch in str(title or "")).strip("_")
    return (safe or "html_report")[:80]


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


def _columns_from_rows(rows: list[dict[str, Any]]) -> list[str]:
    """rows에서 컬럼명을 처음 등장한 순서대로 수집합니다."""

    columns: list[str] = []
    for row in rows:
        for key in row:
            text = str(key)
            if text not in columns:
                columns.append(text)
    return columns


def _positive_int(value: Any, default: int) -> int:
    """값을 양의 정수로 바꾸고 실패하면 default를 사용합니다."""

    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(1, parsed)


class HtmlTemplateRenderer(Component):
    """Langflow 화면에 표시되는 04번 커스텀 컴포넌트 클래스."""

    display_name = "04 HTML 렌더링"
    description = "최종 리포트 계획과 데이터를 이용해 독립 실행 가능한 HTML 원문을 생성합니다."
    icon = "FileCode2"
    inputs = [DataInput(name="payload", display_name="최종 계획", required=True)]
    outputs = [Output(name="payload_out", display_name="HTML 생성 결과", method="build_payload")]

    def build_payload(self) -> Data:
        """최종 계획 payload를 받아 HTML 생성 결과 payload를 출력합니다."""

        result = render_html_report(getattr(self, "payload", None))
        html_report = result.get("html_report", {})
        self.status = {
            "title": html_report.get("title"),
            "blocks": html_report.get("blocks", []),
            "html_bytes": len(str(html_report.get("html") or "").encode("utf-8")),
        }
        return Data(data=result)
