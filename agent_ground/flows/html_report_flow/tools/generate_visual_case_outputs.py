from __future__ import annotations

"""샘플 HTML 리포트 생성/검증용 보조 스크립트.

Langflow 화면을 열지 않고도 로컬에서 여러 CSV/카탈로그/LLM 계획 케이스를 실행해
`test_outputs/visual_case_outputs` 폴더에 HTML 결과와 요약 파일을 만들어 줍니다.
실제 Langflow 노드는 아니지만, 다양한 리포트 모양이 나오는지 확인할 때 사용합니다.
"""

import importlib.util
import json
import shutil
import sys
import types
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
COMPONENT_DIR = ROOT / "langflow_components" / "html_report_flow"
SAMPLE_DATA_DIR = ROOT / "samples" / "00_data_inputs"
SAMPLE_CATALOG_DIR = ROOT / "samples" / "02_component_catalogs"
OUTPUT_DIR = ROOT / "test_outputs" / "visual_case_outputs"

MARKERS = {
    "trend_line_chart": "line",
    "comparison_bar_chart": "bar",
    "donut_chart": "donut",
    "grouped_bar_chart": "grouped_bar",
    "stacked_comparison_bar": "stacked_bar",
    "distribution_histogram": "histogram",
    "scatter_plot": "scatter",
    "heatmap_matrix": "heatmap",
    "pivot_matrix_table": "heatmap",
    "kpi_card_grid": "kpi",
    "metric_delta_card_grid": "kpi",
    "ranking_table": "table",
    "detail_data_table": "table",
    "period_comparison_table": "table",
    "outlier_exception_table": "table",
    "insight_bullets": "insight",
    "recommendation_list": "insight",
}


def install_lfx_stubs() -> None:
    """Langflow가 설치되어 있지 않은 환경에서도 컴포넌트 파일을 import할 수 있게 가짜 모듈을 만듭니다.

    컴포넌트 파일들은 `lfx` 패키지를 import하지만, 이 테스트 스크립트는 순수 함수만 실행하면 됩니다.
    그래서 최소한의 Component/Data/Message/Input 클래스를 임시로 등록합니다.
    """

    if "lfx" in sys.modules:
        return

    class Component:
        pass

    class _Input:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.args = args
            self.kwargs = kwargs

    class Data:
        def __init__(self, data: Any = None, **kwargs: Any) -> None:
            self.data = data if data is not None else kwargs

    class Message:
        def __init__(self, text: str = "", **kwargs: Any) -> None:
            self.text = text
            self.content = text
            self.data = kwargs

    module_names = [
        "lfx",
        "lfx.custom",
        "lfx.custom.custom_component",
        "lfx.custom.custom_component.component",
        "lfx.io",
        "lfx.schema",
        "lfx.schema.data",
        "lfx.schema.message",
    ]
    for name in module_names:
        sys.modules.setdefault(name, types.ModuleType(name))

    sys.modules["lfx.custom.custom_component.component"].Component = Component
    io_module = sys.modules["lfx.io"]
    for name in ("DataInput", "DropdownInput", "MessageTextInput", "Output"):
        setattr(io_module, name, _Input)
    sys.modules["lfx.schema.data"].Data = Data
    sys.modules["lfx.schema.message"].Message = Message


def main() -> None:
    """모든 샘플 케이스를 실행하고 HTML/metadata/summary 파일을 생성합니다."""

    install_lfx_stubs()
    modules = {
        "m00": load_module("m00", COMPONENT_DIR / "00_demo_report_request_loader.py"),
        "m01": load_module("m01", COMPONENT_DIR / "01_data_profile_builder.py"),
        "m02": load_module("m02", COMPONENT_DIR / "02_html_component_catalog_builder.py"),
        "m03": load_module("m03", COMPONENT_DIR / "03_auto_html_plan_builder.py"),
        "m03b": load_module("m03b", COMPONENT_DIR / "03b_llm_html_plan_normalizer.py"),
        "m04": load_module("m04", COMPONENT_DIR / "04_html_template_renderer.py"),
    }

    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for case in deterministic_cases():
        results.append(run_deterministic_case(case, modules))
    for case in llm_like_cases():
        results.append(run_llm_like_case(case, modules))

    (OUTPUT_DIR / "case_matrix.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUTPUT_DIR / "CASE_SUMMARY.md").write_text(build_summary(results), encoding="utf-8")

    chart_coverage = sorted({chart for result in results for chart in result["present_markers"]})
    print("OUTPUT_DIR", OUTPUT_DIR)
    print("CASE_COUNT", len(results))
    print("CHART_COVERAGE", ", ".join(chart_coverage))
    print("SUMMARY", OUTPUT_DIR / "CASE_SUMMARY.md")


def deterministic_cases() -> list[dict[str, Any]]:
    """LLM 없이 규칙 기반 추천만으로 실행할 샘플 케이스 목록입니다."""

    return [
        {
            "case_id": "det_multi_dataset_join",
            "mode": "deterministic",
            "json": "sample_multi_wip_output_quality.json",
            "catalog": "catalog_operations_compact.json",
            "question": "Combine WIP, output, and quality data by date, line, and process.",
            "view_request": "KPI cards, DATE trend line for OUTPUT_QTY, ALERT_LEVEL donut, process comparison, risk detail table",
        },
        {
            "case_id": "det_wip_operations",
            "mode": "deterministic",
            "csv": "sample_wip.csv",
            "catalog": "catalog_operations_compact.json",
            "question": "Compare WIP and production by process, and show daily trend.",
            "view_request": "KPI cards, grouped bar, trend line, detail table",
        },
        {
            "case_id": "det_sales_composition",
            "mode": "deterministic",
            "csv": "sample_sales_channel_mix.csv",
            "catalog": "catalog_composition_dashboard.json",
            "question": "Show revenue share by channel as donut chart and compare revenue/orders by region.",
            "view_request": "Donut chart, grouped bar chart, ranking table",
        },
        {
            "case_id": "det_quality_diagnostic",
            "mode": "deterministic",
            "csv": "sample_quality_diagnostics.csv",
            "catalog": "catalog_quality_diagnostics.json",
            "question": "Diagnose defect count distribution, yield relationship, and warning rows.",
            "view_request": "Histogram, scatter plot, exception table",
        },
        {
            "case_id": "det_inventory_composition",
            "mode": "deterministic",
            "csv": "sample_inventory_flow.csv",
            "catalog": "catalog_composition_dashboard.json",
            "question": "Show stock status composition and inbound/outbound comparison by warehouse.",
            "view_request": "Donut, grouped bar, stacked bar, detail table",
        },
        {
            "case_id": "det_energy_correlation",
            "mode": "deterministic",
            "csv": "sample_energy_usage.csv",
            "catalog": "catalog_quality_diagnostics.json",
            "question": "Show kWh trend, downtime correlation, and equipment comparison.",
            "view_request": "Trend line, scatter plot, comparison bar",
        },
        {
            "case_id": "det_customer_funnel",
            "mode": "deterministic",
            "csv": "sample_customer_funnel.csv",
            "catalog": "catalog_executive_summary.json",
            "question": "Show conversion rate by stage and segment breakdown.",
            "view_request": "Executive summary, stacked bar, donut, recommendation",
        },
        {
            "case_id": "det_heatmap_matrix",
            "mode": "deterministic",
            "csv": "sample_sales_channel_mix.csv",
            "catalog": "catalog_composition_dashboard.json",
            "question": "Show revenue heatmap matrix by region and product.",
            "view_request": "Heatmap matrix and ranking table",
        },
        {
            "case_id": "det_detail_review",
            "mode": "deterministic",
            "csv": "sample_quality_diagnostics.csv",
            "catalog": "",
            "question": "Show raw warning rows and detailed inspection records.",
            "view_request": "Detail table first, compact query review",
        },
    ]


def llm_like_cases() -> list[dict[str, Any]]:
    """실제 LLM 응답처럼 만든 JSON 계획을 03b 검증 노드에 통과시키는 샘플 케이스 목록입니다."""

    return [
        {
            "case_id": "llm_multi_dataset_operations",
            "mode": "llm_like",
            "json": "sample_multi_wip_output_quality.json",
            "catalog": "catalog_operations_compact.json",
            "question": "Create a joined operations report from WIP, production, and quality datasets.",
            "view_request": "Top row KPI cards, output trend line, WIP alert donut, process risk table",
            "llm_plan": {
                "title": "Joined Operations Risk Report",
                "audience": "operator",
                "report_goal": "monitor",
                "layout": "dashboard",
                "dataset_strategy": {
                    "mode": "join",
                    "active_data_view_id": "joined_auto",
                    "join_keys": ["DATE", "LINE", "PROCESS"],
                },
                "visual_style": {"density": "compact", "font_scale": "normal", "accent_color": "#4f46e5", "secondary_color": "#0f766e", "max_width": "wide"},
                "narrative": {
                    "executive_summary": "WIP, output, and quality indicators are reviewed together by common operation keys.",
                    "key_findings": ["High WIP rows should be compared against output and backlog.", "Low yield periods need quality follow-up."],
                    "recommendations": ["Prioritize HIGH and WARN alert rows with high backlog first."],
                },
                "blocks": [
                    {"block_id": "report_header", "title": "Joined Operations Risk Report", "width": "full", "emphasis": "high"},
                    {"block_id": "kpi_card_grid", "title": "Core Joined Metrics", "width": "full", "emphasis": "high", "data_view_id": "joined_auto", "metrics": [{"label": "Total WIP", "column": "WIP_QTY", "aggregation": "sum"}, {"label": "Total Output", "column": "OUTPUT_QTY", "aggregation": "sum"}, {"label": "Avg Yield", "column": "YIELD_RATE", "aggregation": "avg"}, {"label": "Total Defects", "column": "DEFECT_QTY", "aggregation": "sum"}, {"label": "Backlog", "column": "BACKLOG_QTY", "aggregation": "sum"}]},
                    {"block_id": "trend_line_chart", "title": "Daily Output Trend", "width": "two_third", "emphasis": "high", "data_view_id": "joined_auto", "x": "DATE", "y": "OUTPUT_QTY", "chart_policy": {"chart_type": "line", "show_values": True}},
                    {"block_id": "donut_chart", "title": "WIP Alert Mix", "width": "third", "emphasis": "medium", "data_view_id": "wip_status", "x": "ALERT_LEVEL", "y": "WIP_QTY", "chart_policy": {"chart_type": "donut", "show_percent": True, "show_legend": True}},
                    {"block_id": "grouped_bar_chart", "title": "Process WIP, Output, Defect", "width": "full", "emphasis": "medium", "data_view_id": "joined_auto", "x": "PROCESS", "metrics": [{"label": "WIP", "column": "WIP_QTY", "aggregation": "sum"}, {"label": "Output", "column": "OUTPUT_QTY", "aggregation": "sum"}, {"label": "Defect", "column": "DEFECT_QTY", "aggregation": "sum"}]},
                    {"block_id": "detail_data_table", "title": "High Risk Joined Rows", "width": "full", "density": "compact", "data_view_id": "joined_auto", "filter_logic": "or", "filter_rules": [{"column": "ALERT_LEVEL", "operator": "in", "value": ["HIGH", "WARN"]}, {"column": "YIELD_RATE", "operator": "lte", "value": 95}], "highlight_rules": [{"column": "ALERT_LEVEL", "operator": "eq", "value": "HIGH", "tone": "danger"}, {"column": "ALERT_LEVEL", "operator": "eq", "value": "WARN", "tone": "warning"}], "table_policy": {"columns": ["DATE", "LINE", "PROCESS", "STATUS", "ALERT_LEVEL", "WIP_QTY", "OUTPUT_QTY", "BACKLOG_QTY", "DEFECT_QTY", "YIELD_RATE"], "limit": 30, "show_row_numbers": True}, "sort": {"by": "BACKLOG_QTY", "direction": "desc"}},
                ],
            },
        },
        {
            "case_id": "llm_exec_sales_mix",
            "mode": "llm_like",
            "csv": "sample_sales_channel_mix.csv",
            "catalog": "catalog_executive_summary.json",
            "question": "Create an executive revenue mix report.",
            "view_request": "Large KPI cards, donut, insight bullets, short detail table",
            "llm_plan": {
                "title": "Executive Revenue Mix Report",
                "audience": "executive",
                "report_goal": "explain",
                "layout": "executive_summary",
                "visual_style": {"density": "comfortable", "font_scale": "large", "accent_color": "#2563eb", "max_width": "normal"},
                "narrative": {
                    "executive_summary": "Revenue is concentrated in a few channel and region combinations.",
                    "key_findings": ["Online revenue is the primary visible contributor.", "Retail has more watch rows in the sample."],
                    "recommendations": ["Review channel margin before changing allocation."],
                },
                "blocks": [
                    {"block_id": "report_header", "title": "Executive Revenue Mix Report", "width": "full", "emphasis": "high"},
                    {"block_id": "kpi_card_grid", "title": "Business Impact", "width": "full", "emphasis": "high", "metrics": [{"label": "Revenue", "column": "REVENUE", "aggregation": "sum"}, {"label": "Orders", "column": "ORDERS", "aggregation": "sum"}, {"label": "Avg Margin", "column": "MARGIN_RATE", "aggregation": "avg"}]},
                    {"block_id": "donut_chart", "title": "Revenue Share by Channel", "width": "half", "emphasis": "medium", "x": "CHANNEL", "y": "REVENUE", "chart_policy": {"chart_type": "donut", "limit": 6, "show_percent": True, "show_legend": True}},
                    {"block_id": "insight_bullets", "title": "Executive Takeaways", "width": "half", "emphasis": "high"},
                    {"block_id": "detail_data_table", "title": "Verification Rows", "width": "full", "density": "compact", "table_policy": {"columns": ["DATE", "REGION", "CHANNEL", "PRODUCT", "REVENUE", "ORDERS", "STATUS"], "limit": 18, "show_row_numbers": True}},
                ],
            },
        },
        {
            "case_id": "llm_quality_lab",
            "mode": "llm_like",
            "csv": "sample_quality_diagnostics.csv",
            "catalog": "catalog_quality_diagnostics.json",
            "question": "Find defect distribution and process risk.",
            "view_request": "Diagnostics view with histogram, scatter, heatmap, and exception table",
            "llm_plan": {
                "title": "Quality Diagnostic Lab View",
                "audience": "engineer",
                "report_goal": "diagnose",
                "layout": "diagnosis",
                "visual_style": {"density": "compact", "font_scale": "normal", "accent_color": "#dc2626", "secondary_color": "#7c3aed", "max_width": "wide"},
                "blocks": [
                    {"block_id": "report_header", "title": "Quality Diagnostic Lab View", "width": "full", "emphasis": "critical"},
                    {"block_id": "distribution_histogram", "title": "Defect Count Distribution", "width": "half", "emphasis": "high", "x": "DEFECT_COUNT", "chart_policy": {"chart_type": "histogram", "bin_count": 8, "show_values": True}},
                    {"block_id": "scatter_plot", "title": "Yield vs Cycle Time", "width": "half", "emphasis": "high", "x": "CYCLE_TIME_SEC", "y": "YIELD_RATE", "chart_policy": {"chart_type": "scatter", "limit": 120}},
                    {"block_id": "heatmap_matrix", "title": "Process x Defect Heatmap", "width": "full", "emphasis": "medium", "x": "PROCESS", "series": "DEFECT_TYPE", "y": "DEFECT_COUNT", "chart_policy": {"chart_type": "heatmap", "limit": 8, "show_values": True}},
                    {"block_id": "outlier_exception_table", "title": "Warning And Danger Rows", "width": "full", "density": "compact", "table_policy": {"columns": ["DATE", "LINE", "PROCESS", "DEFECT_TYPE", "DEFECT_COUNT", "YIELD_RATE", "STATUS"], "limit": 50, "show_row_numbers": True}, "highlight_rules": [{"column": "STATUS", "operator": "eq", "value": "warning", "tone": "warning"}, {"column": "STATUS", "operator": "eq", "value": "danger", "tone": "danger"}]},
                ],
            },
        },
        {
            "case_id": "llm_inventory_board",
            "mode": "llm_like",
            "csv": "sample_inventory_flow.csv",
            "catalog": "catalog_composition_dashboard.json",
            "question": "Create inventory composition board.",
            "view_request": "Donut, stacked bars, grouped inbound/outbound, detail table",
            "llm_plan": {
                "title": "Inventory Flow Composition Board",
                "audience": "operator",
                "report_goal": "monitor",
                "layout": "dashboard",
                "visual_style": {"density": "compact", "font_scale": "normal", "accent_color": "#0891b2", "secondary_color": "#f59e0b", "max_width": "wide"},
                "blocks": [
                    {"block_id": "report_header", "title": "Inventory Flow Composition Board", "width": "full", "emphasis": "high"},
                    {"block_id": "donut_chart", "title": "On-Hand Share by Category", "width": "half", "x": "CATEGORY", "y": "ON_HAND", "chart_policy": {"chart_type": "donut", "limit": 6, "show_percent": True}},
                    {"block_id": "comparison_bar_chart", "title": "Days Of Supply by Product", "width": "half", "x": "PRODUCT", "y": "DAYS_OF_SUPPLY", "chart_policy": {"chart_type": "horizontal_bar", "limit": 8, "show_values": True}},
                    {"block_id": "stacked_comparison_bar", "title": "Warehouse Status Mix", "width": "full", "x": "WAREHOUSE", "series": "STOCK_STATUS", "y": "ON_HAND", "chart_policy": {"chart_type": "stacked_bar", "limit": 6, "show_legend": True}},
                    {"block_id": "grouped_bar_chart", "title": "Inbound vs Outbound", "width": "full", "x": "WAREHOUSE", "metrics": [{"label": "Inbound", "column": "INBOUND", "aggregation": "sum"}, {"label": "Outbound", "column": "OUTBOUND", "aggregation": "sum"}]},
                ],
            },
        },
        {
            "case_id": "llm_energy_risk",
            "mode": "llm_like",
            "csv": "sample_energy_usage.csv",
            "catalog": "catalog_operations_compact.json",
            "question": "Analyze energy efficiency and downtime risk.",
            "view_request": "Trend, scatter, comparison and recommendations",
            "llm_plan": {
                "title": "Energy Efficiency And Downtime Risk",
                "audience": "engineer",
                "report_goal": "diagnose",
                "layout": "dashboard",
                "visual_style": {"density": "compact", "font_scale": "normal", "accent_color": "#7c3aed", "secondary_color": "#0f766e", "max_width": "wide"},
                "blocks": [
                    {"block_id": "report_header", "title": "Energy Efficiency And Downtime Risk", "width": "full", "emphasis": "high"},
                    {"block_id": "trend_line_chart", "title": "Daily kWh Trend", "width": "full", "x": "DATE", "y": "KWH", "emphasis": "high"},
                    {"block_id": "scatter_plot", "title": "kWh vs Downtime", "width": "half", "x": "KWH", "y": "DOWNTIME_MIN", "emphasis": "medium"},
                    {"block_id": "comparison_bar_chart", "title": "Equipment kWh", "width": "half", "x": "EQUIPMENT", "y": "KWH", "chart_policy": {"limit": 8, "show_values": True}},
                    {"block_id": "recommendation_list", "title": "Follow-Up Checks", "width": "full", "items": ["Review Chiller-A during warning periods.", "Compare output-adjusted kWh before operational action."]},
                ],
            },
        },
        {
            "case_id": "llm_query_review",
            "mode": "llm_like",
            "csv": "sample_quality_diagnostics.csv",
            "catalog": "",
            "question": "Show detailed warning records.",
            "view_request": "Table-heavy query review with ranking",
            "llm_plan": {
                "title": "Detailed Warning Record Review",
                "audience": "analyst",
                "report_goal": "audit",
                "layout": "query_review",
                "visual_style": {"density": "compact", "font_scale": "small", "accent_color": "#475569", "max_width": "wide"},
                "blocks": [
                    {"block_id": "report_header", "title": "Detailed Warning Record Review", "width": "full", "emphasis": "medium"},
                    {"block_id": "scope_summary", "title": "Query Scope", "width": "full"},
                    {"block_id": "ranking_table", "title": "Top Defect Counts", "width": "half", "sort": {"by": "DEFECT_COUNT", "direction": "desc"}, "columns": ["PROCESS", "DEFECT_TYPE", "DEFECT_COUNT", "YIELD_RATE", "STATUS"], "limit": 10},
                    {"block_id": "insight_bullets", "title": "Review Notes", "width": "half", "lines": ["Rows are intended for inspection rather than executive summary.", "Check warning and danger statuses before sharing."]},
                    {"block_id": "detail_data_table", "title": "All Visible Records", "width": "full", "density": "compact", "table_policy": {"columns": ["DATE", "LINE", "PROCESS", "DEFECT_TYPE", "DEFECT_COUNT", "INSPECTION_COUNT", "YIELD_RATE", "STATUS"], "limit": 80, "show_row_numbers": True}},
                ],
            },
        },
    ]


def run_deterministic_case(case: dict[str, Any], modules: dict[str, Any]) -> dict[str, Any]:
    """하나의 deterministic 케이스를 00→01→02→03→04 순서로 실행합니다."""

    payload = build_base_payload(case, modules)
    profile = modules["m01"].build_data_profile(payload)
    catalog_json = read_catalog(case.get("catalog"))
    catalog = modules["m02"].build_html_component_catalog(profile, component_catalog_json=catalog_json)
    plan_payload = modules["m03"].build_auto_html_plan(payload, profile, catalog, max_blocks="10")
    rendered = modules["m04"].render_html_report(plan_payload)
    return write_result(case, profile, catalog, plan_payload, rendered)


def run_llm_like_case(case: dict[str, Any], modules: dict[str, Any]) -> dict[str, Any]:
    """하나의 LLM-like 케이스를 00→01→02→03→03b→04 순서로 실행합니다."""

    payload = build_base_payload(case, modules)
    profile = modules["m01"].build_data_profile(payload)
    catalog_json = read_catalog(case.get("catalog"))
    catalog = modules["m02"].build_html_component_catalog(profile, component_catalog_json=catalog_json)
    base_payload = modules["m03"].build_auto_html_plan(payload, profile, catalog, max_blocks="10")
    normalized = modules["m03b"].normalize_llm_html_plan(base_payload, json.dumps(case["llm_plan"], ensure_ascii=False), catalog)
    rendered = modules["m04"].render_html_report(normalized)
    return write_result(case, profile, catalog, normalized, rendered)


def build_base_payload(case: dict[str, Any], modules: dict[str, Any]) -> dict[str, Any]:
    """샘플 CSV 또는 JSON 파일을 읽어 00번 노드의 표준 payload를 만듭니다."""

    source_name = case.get("csv") or case.get("json")
    if not source_name:
        raise ValueError(f"Missing sample source for case: {case.get('case_id')}")
    data_text = (SAMPLE_DATA_DIR / source_name).read_text(encoding="utf-8")
    return modules["m00"].build_demo_report_request(
        case["question"],
        case.get("view_request", ""),
        data_text=data_text,
    )


def write_result(
    case: dict[str, Any],
    profile: dict[str, Any],
    catalog: dict[str, Any],
    plan_payload: dict[str, Any],
    rendered: dict[str, Any],
) -> dict[str, Any]:
    """케이스 실행 결과 HTML과 metadata.json을 파일로 저장합니다."""

    case_dir = OUTPUT_DIR / case["case_id"]
    case_dir.mkdir(parents=True, exist_ok=True)
    html_text = rendered["html_report"]["html"]
    plan = plan_payload["report_plan"]
    blocks = [block.get("block_id") for block in plan.get("blocks", []) if isinstance(block, dict)]
    widths = [block.get("width", "full") for block in plan.get("blocks", []) if isinstance(block, dict)]
    present_markers = sorted({MARKERS[block] for block in blocks if block in MARKERS})
    html_path = case_dir / "report.html"
    html_path.write_text(html_text, encoding="utf-8")
    metadata = {
        "case_id": case["case_id"],
        "mode": case["mode"],
        "data_source": case.get("csv") or case.get("json"),
        "catalog": case.get("catalog", ""),
        "question": case["question"],
        "dataset_strategy": plan.get("dataset_strategy", {}),
        "row_count": profile.get("shape", {}).get("row_count"),
        "plan_source": plan.get("plan_source", "deterministic"),
        "layout": plan.get("layout"),
        "visual_style": plan.get("visual_style", {}),
        "blocks": blocks,
        "widths": widths,
        "present_markers": present_markers,
        "html_bytes": len(html_text.encode("utf-8")),
        "html_file": str(html_path),
        "recommended_components": [item.get("component_id") for item in catalog.get("recommended_components", [])],
    }
    (case_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def build_summary(results: list[dict[str, Any]]) -> str:
    """전체 케이스 실행 결과를 사람이 읽기 쉬운 Markdown 요약으로 만듭니다."""

    marker_counts = Counter(chart for result in results for chart in result["present_markers"])
    block_counts = Counter(block for result in results for block in result["blocks"])
    lines = [
        "# Visual Case Output Summary",
        "",
        f"Generated cases: {len(results)}",
        "",
        "## Case Matrix",
        "",
        "| Case | Mode | Data | Layout | Visual markers | Block sequence | HTML |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for result in results:
        rel_html = Path(result["html_file"]).relative_to(OUTPUT_DIR)
        lines.append(
            "| {case_id} | {mode} | {data_source} | {layout} | {markers} | {blocks} | {html} |".format(
                case_id=result["case_id"],
                mode=result["mode"],
                data_source=result.get("data_source") or "",
                layout=result.get("layout") or "",
                markers=", ".join(result["present_markers"]),
                blocks=" -> ".join(result["blocks"][:8]),
                html=str(rel_html).replace("\\", "/"),
            )
        )
    lines.extend(
        [
            "",
            "## Marker Coverage",
            "",
            "| Marker | Count |",
            "| --- | ---: |",
        ]
    )
    for name, count in sorted(marker_counts.items()):
        lines.append(f"| {name} | {count} |")
    lines.extend(
        [
            "",
            "## Block Coverage",
            "",
            "| Block | Count |",
            "| --- | ---: |",
        ]
    )
    for name, count in sorted(block_counts.items()):
        lines.append(f"| `{name}` | {count} |")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Deterministic cases show what the flow produces from data, question, and catalog defaults without calling an LLM.",
            "- LLM-like cases pass curated JSON through `03b LLM 계획 검증` and `04 HTML 렌더링`, proving that the current renderer can produce visibly different layouts when the LLM plan differs.",
            "- If a real LLM keeps producing similar layouts, the issue is likely the LLM output variety rather than renderer capability; inspect `03b.최종 계획.report_plan.blocks`.",
        ]
    )
    return "\n".join(lines) + "\n"


def read_catalog(name: str | None) -> str:
    """samples/02_component_catalogs 폴더에서 카탈로그 JSON 문자열을 읽습니다."""

    if not name:
        return ""
    return (SAMPLE_CATALOG_DIR / name).read_text(encoding="utf-8")


def load_module(name: str, path: Path):
    """파일 경로로부터 Python 모듈을 동적으로 불러옵니다."""

    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    main()
