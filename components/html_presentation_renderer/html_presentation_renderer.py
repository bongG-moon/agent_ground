from __future__ import annotations

"""검증된 프레젠테이션 계획을 독립 실행 가능한 16:9 HTML로 렌더링합니다.

이 Component는 LLM이 작성한 HTML/CSS/JavaScript를 실행하지 않습니다. 입력 계획에서
허용한 필드만 읽고, 모든 사용자 문자열을 이스케이프한 뒤 코드에 포함된 정적 템플릿으로
슬라이드를 생성합니다.
"""

import base64
import binascii
import hashlib
import html
import json
import math
import re
from copy import deepcopy
from datetime import datetime
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, Output
from lfx.schema.data import Data


MAX_HTML_BYTES = 20 * 1024 * 1024
MAX_SLIDES = 200
MAX_ELEMENTS_PER_SLIDE = 40
MAX_TABLE_ROWS = 30
MAX_CHART_POINTS = 24

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_-]+")
_DATA_IMAGE_RE = re.compile(
    r"^data:image/(?P<kind>png|jpeg|jpg|gif|webp);base64,(?P<data>[A-Za-z0-9+/=\s]+)$",
    re.IGNORECASE,
)

_LAYOUTS = {
    "title",
    "section",
    "content",
    "two_column",
    "three_column",
    "chart",
    "table",
    "closing",
}
_LAYOUT_ALIASES = {
    "cover": "title",
    "title_content": "content",
    "kpi_grid": "content",
    "chart_focus": "chart",
    "table_focus": "table",
    "comparison": "two_column",
    "timeline": "content",
    "conclusion": "closing",
}
_WIDTHS = {
    "full": "element-width-full",
    "half": "element-width-half",
    "third": "element-width-third",
    "two_thirds": "element-width-two-thirds",
}
_FONT_STACKS = {
    "sans": "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans KR', Arial, sans-serif",
    "serif": "Georgia, 'Noto Serif KR', 'Times New Roman', serif",
    "mono": "'Cascadia Code', Consolas, 'Noto Sans Mono', monospace",
}


def render_html_presentation(presentation_plan_value: Any) -> dict[str, Any]:
    """프레젠테이션 계획을 HTML artifact payload로 변환합니다."""

    payload = _payload(presentation_plan_value)
    plan = _dict(payload.get("presentation_plan")) or _dict(payload.get("report_plan")) or payload
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    title = _text(plan.get("title"), "HTML 프레젠테이션", 240)
    raw_slides = plan.get("slides")
    if not isinstance(raw_slides, list) or not raw_slides:
        errors.append(
            {
                "code": "slides_required",
                "message": "presentation_plan.slides에는 한 개 이상의 슬라이드가 필요합니다.",
            }
        )
        return _error_result(title, plan, payload, errors, warnings)
    if len(raw_slides) > MAX_SLIDES:
        errors.append(
            {
                "code": "slide_count_exceeded",
                "message": f"슬라이드는 최대 {MAX_SLIDES}개까지 렌더링할 수 있습니다.",
                "actual": len(raw_slides),
                "limit": MAX_SLIDES,
            }
        )
        return _error_result(title, plan, payload, errors, warnings)

    theme = _normalize_theme(
        plan.get("theme") or plan.get("visual_style") or plan.get("design_system"),
        warnings,
    )
    design_policy = _normalize_design_policy(plan.get("design_policy"), warnings)
    normalized_slides: list[dict[str, Any]] = []
    used_slide_ids: set[str] = set()
    for index, raw_slide in enumerate(raw_slides):
        if not isinstance(raw_slide, dict):
            warnings.append(
                {
                    "code": "invalid_slide_skipped",
                    "message": "객체가 아닌 슬라이드 항목을 제외했습니다.",
                    "position": index + 1,
                }
            )
            continue
        normalized_slides.append(
            _normalize_slide(raw_slide, index, used_slide_ids, warnings)
        )

    if not normalized_slides:
        errors.append(
            {
                "code": "no_renderable_slides",
                "message": "렌더링할 수 있는 슬라이드가 없습니다.",
            }
        )
        return _error_result(title, plan, payload, errors, warnings)

    document = _build_document(title, plan, normalized_slides, theme, design_policy, warnings)
    html_bytes = document.encode("utf-8")
    if len(html_bytes) > MAX_HTML_BYTES:
        errors.append(
            {
                "code": "html_size_exceeded",
                "message": "생성된 HTML이 20MB 제한을 초과했습니다.",
                "actual_bytes": len(html_bytes),
                "limit_bytes": MAX_HTML_BYTES,
            }
        )
        return _error_result(title, plan, payload, errors, warnings)

    filename_hint = _filename_hint(plan.get("filename_hint") or title)
    digest = hashlib.sha256(html_bytes).hexdigest()
    artifact = {
        "title": title,
        "html": document,
        "mime_type": "text/html; charset=utf-8",
        "filename_hint": filename_hint,
        "slide_count": len(normalized_slides),
        "aspect_ratio": "16:9",
        "self_contained": True,
        "byte_size": len(html_bytes),
        "sha256": digest,
        "rendered_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "design_policy_id": design_policy["policy_id"],
        "motion_profile": design_policy["motion_profile"],
    }
    report_plan = {
        "title": title,
        "subtitle": _text(plan.get("subtitle"), "", 360),
        "filename_hint": filename_hint,
        "layout": "html_presentation_16_9",
        "slide_count": len(normalized_slides),
    }
    return {
        "success": True,
        "status": "ok",
        "payload_version": "html-presentation-artifact-v1",
        "flow_type": "ppt_image_to_html",
        "request": _public_request(payload.get("request")),
        "report_plan": report_plan,
        "presentation_artifact": artifact,
        # 기존 ReportApiPublisher가 별도 수정 없이 사용할 수 있는 호환 alias입니다.
        "html_report": deepcopy(artifact),
        "warnings": warnings,
        "errors": [],
        "trace": [
            {
                "stage": "html_presentation_renderer",
                "status": "success",
                "slide_count": len(normalized_slides),
                "byte_size": len(html_bytes),
                "sha256": digest,
                "design_policy_id": design_policy["policy_id"],
            }
        ],
    }


def _normalize_theme(value: Any, warnings: list[dict[str, Any]]) -> dict[str, str]:
    raw = _dict(value)
    colors = _dict(raw.get("colors"))
    if colors:
        # Flow의 design_system.colors와 독립 Component의 평면 theme 계약을 모두 받습니다.
        raw = {**raw, **colors}
    defaults = {
        "background": "#0f172a",
        "surface": "#ffffff",
        "text": "#0f172a",
        "muted": "#475569",
        "primary": "#2563eb",
        "accent": "#0f766e",
        "danger": "#dc2626",
        "chart_1": "#2563eb",
        "chart_2": "#0f766e",
        "chart_3": "#f59e0b",
        "chart_4": "#7c3aed",
    }
    aliases = {
        "background": ("background", "background_color"),
        "surface": ("surface", "surface_color"),
        "text": ("text", "text_color"),
        "muted": ("muted", "muted_text", "muted_text_color"),
        "primary": ("primary", "primary_color"),
        "accent": ("accent", "accent_color"),
        "danger": ("danger", "danger_color"),
        "chart_1": ("chart_1",),
        "chart_2": ("chart_2",),
        "chart_3": ("chart_3",),
        "chart_4": ("chart_4",),
    }
    result: dict[str, str] = {}
    for key, keys in aliases.items():
        candidate = next((raw.get(alias) for alias in keys if raw.get(alias) not in (None, "")), None)
        if candidate is None:
            result[key] = defaults[key]
            continue
        text = str(candidate).strip()
        if _COLOR_RE.fullmatch(text):
            result[key] = text.lower()
        else:
            result[key] = defaults[key]
            warnings.append(
                {
                    "code": "invalid_theme_color_replaced",
                    "message": "안전하지 않거나 지원하지 않는 색상을 기본값으로 교체했습니다.",
                    "field": key,
                }
            )

    typography = _dict(raw.get("typography"))
    font_value = str(
        raw.get("font_family")
        or raw.get("font")
        or typography.get("body_family")
        or "sans"
    ).strip().lower()
    if "mono" in font_value or "consol" in font_value:
        font_key = "mono"
    elif "serif" in font_value and "sans" not in font_value:
        font_key = "serif"
    else:
        font_key = "sans"
    result["font_stack"] = _FONT_STACKS.get(font_key, _FONT_STACKS["sans"])
    known_font_tokens = (
        "sans",
        "serif",
        "mono",
        "arial",
        "pretendard",
        "noto",
        "segoe",
        "georgia",
        "times",
        "consolas",
        "cascadia",
    )
    if not any(token in font_value for token in known_font_tokens):
        warnings.append(
            {
                "code": "unsupported_font_replaced",
                "message": "외부 글꼴을 불러오지 않고 시스템 기본 글꼴을 사용합니다.",
            }
        )
    return result


def _normalize_design_policy(value: Any, warnings: list[dict[str, Any]]) -> dict[str, Any]:
    raw = _dict(value)
    motion = _dict(raw.get("motion"))
    policy_id = _safe_id(raw.get("policy_id")) or "hallmark-emil-balanced-v1"
    easing = str(motion.get("easing") or "cubic-bezier(0.23, 1, 0.32, 1)").strip()
    if not re.fullmatch(r"cubic-bezier\([0-9.,\s-]+\)|ease|ease-out|linear", easing):
        easing = "cubic-bezier(0.23, 1, 0.32, 1)"
        warnings.append(
            {
                "code": "invalid_motion_easing_replaced",
                "message": "지원하지 않는 모션 easing을 정책 기본값으로 교체했습니다.",
            }
        )
    maximum = _positive_int(motion.get("max_ui_duration_ms"), 300, 100, 300)
    return {
        "policy_id": policy_id,
        "motion_profile": _safe_id(motion.get("profile")) or "purposeful-subtle",
        "button_press_duration_ms": min(maximum, _positive_int(motion.get("button_press_duration_ms"), 120, 0, 300)),
        "slide_enter_duration_ms": min(maximum, _positive_int(motion.get("slide_enter_duration_ms"), 180, 0, 300)),
        "easing": easing,
    }


def _normalize_slide(
    slide: dict[str, Any],
    index: int,
    used_ids: set[str],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    raw_id = _safe_id(slide.get("slide_id") or slide.get("id") or f"slide-{index + 1}")
    slide_id = raw_id or f"slide-{index + 1}"
    if slide_id in used_ids:
        slide_id = f"{slide_id}-{index + 1}"
    used_ids.add(slide_id)

    layout = str(slide.get("layout") or "content").strip().lower().replace("-", "_")
    layout = _LAYOUT_ALIASES.get(layout, layout)
    if layout not in _LAYOUTS:
        warnings.append(
            {
                "code": "unsupported_layout_replaced",
                "message": "지원하지 않는 슬라이드 레이아웃을 content로 교체했습니다.",
                "position": index + 1,
                "layout": layout,
            }
        )
        layout = "content"

    elements = _slide_elements(slide)
    if len(elements) > MAX_ELEMENTS_PER_SLIDE:
        warnings.append(
            {
                "code": "element_count_truncated",
                "message": f"한 슬라이드의 요소를 {MAX_ELEMENTS_PER_SLIDE}개로 제한했습니다.",
                "position": index + 1,
            }
        )
        elements = elements[:MAX_ELEMENTS_PER_SLIDE]

    return {
        "slide_id": slide_id,
        "layout": layout,
        "design_role": _safe_id(slide.get("design_role")) or "framing",
        "visual_weight": _safe_id(slide.get("visual_weight")) or "balanced",
        "key_message": _text(slide.get("key_message"), "", 800),
        "eyebrow": _text(slide.get("eyebrow") or slide.get("section"), "", 120),
        "title": _text(slide.get("title"), f"슬라이드 {index + 1}", 240),
        "subtitle": _text(slide.get("subtitle"), "", 480),
        "footer": _text(slide.get("footer"), "", 180),
        "elements": elements,
    }


def _slide_elements(slide: dict[str, Any]) -> list[dict[str, Any]]:
    elements = slide.get("elements")
    if isinstance(elements, list):
        return [deepcopy(item) for item in elements if isinstance(item, dict)]

    inferred: list[dict[str, Any]] = []
    content = slide.get("content") or slide.get("body")
    if isinstance(content, str) and content.strip():
        inferred.append({"type": "text", "text": content})
    elif isinstance(content, list):
        inferred.append({"type": "bullets", "items": content})
    if isinstance(slide.get("bullets"), list):
        inferred.append({"type": "bullets", "items": slide["bullets"]})
    if isinstance(slide.get("kpis"), list):
        inferred.append({"type": "kpi_grid", "items": slide["kpis"]})
    if isinstance(slide.get("table"), dict):
        inferred.append({"type": "table", **deepcopy(slide["table"])})
    if isinstance(slide.get("chart"), dict):
        inferred.append(deepcopy(slide["chart"]))
    return inferred


def _build_document(
    title: str,
    plan: dict[str, Any],
    slides: list[dict[str, Any]],
    theme: dict[str, str],
    design_policy: dict[str, Any],
    warnings: list[dict[str, Any]],
) -> str:
    total = len(slides)
    rendered_slides = "".join(
        _render_slide(slide, index, total, theme, warnings)
        for index, slide in enumerate(slides)
    )
    safe_title = html.escape(title)
    safe_subtitle = html.escape(_text(plan.get("subtitle"), "", 360))
    font_stack = theme["font_stack"]
    policy_id = html.escape(str(design_policy["policy_id"]), quote=True)
    motion_profile = html.escape(str(design_policy["motion_profile"]), quote=True)
    button_duration = int(design_policy["button_press_duration_ms"])
    slide_duration = int(design_policy["slide_enter_duration_ms"])
    motion_easing = str(design_policy["easing"])

    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="color-scheme" content="light" />
  <title>{safe_title}</title>
  <style>
    :root {{
      --deck-bg: {theme['background']};
      --surface: {theme['surface']};
      --text: {theme['text']};
      --muted: {theme['muted']};
      --primary: {theme['primary']};
      --accent: {theme['accent']};
      --danger: {theme['danger']};
      --chart-1: {theme['chart_1']};
      --chart-2: {theme['chart_2']};
      --chart-3: {theme['chart_3']};
      --chart-4: {theme['chart_4']};
      --font-stack: {font_stack};
      --motion-button: {button_duration}ms;
      --motion-slide: {slide_duration}ms;
      --motion-easing: {motion_easing};
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ min-height: 100%; margin: 0; }}
    body {{
      background: var(--deck-bg);
      color: var(--text);
      font-family: var(--font-stack);
      overflow: hidden;
    }}
    button {{ font: inherit; }}
    .deck-shell {{ min-height: 100vh; display: grid; grid-template-rows: 1fr auto; gap: 14px; padding: 18px; }}
    .deck-stage {{
      width: min(100%, calc((100vh - 104px) * 16 / 9));
      aspect-ratio: 16 / 9;
      margin: auto;
      position: relative;
      background: var(--surface);
      border-radius: 18px;
      overflow: hidden;
      box-shadow: 0 28px 80px rgb(0 0 0 / 38%);
    }}
    .slide {{
      position: absolute;
      inset: 0;
      display: grid;
      grid-template-rows: auto 1fr auto;
      gap: clamp(12px, 2vw, 28px);
      padding: clamp(28px, 4.2vw, 68px);
      background: var(--surface);
      overflow: hidden;
    }}
    .slide[hidden] {{ display: none; }}
    .slide::before {{
      content: "";
      position: absolute;
      inset: 0 auto 0 0;
      width: 10px;
      background: var(--primary);
    }}
    .slide-header {{ position: relative; z-index: 1; }}
    .slide-eyebrow {{ margin: 0 0 8px; color: var(--primary); font-size: clamp(12px, 1.05vw, 18px); font-weight: 800; letter-spacing: .12em; text-transform: uppercase; }}
    .slide h1 {{ margin: 0; max-width: 94%; font-size: clamp(28px, 3.8vw, 62px); line-height: 1.08; letter-spacing: -.035em; }}
    .slide-subtitle {{ margin: 12px 0 0; max-width: 86%; color: var(--muted); font-size: clamp(15px, 1.55vw, 25px); line-height: 1.5; }}
    .slide-body {{
      min-height: 0;
      display: grid;
      grid-template-columns: repeat(12, minmax(0, 1fr));
      align-content: center;
      gap: clamp(10px, 1.35vw, 22px);
      overflow: hidden;
    }}
    .slide-layout-title .slide-header, .slide-layout-closing .slide-header {{ align-self: end; }}
    .slide-layout-title .slide-body, .slide-layout-closing .slide-body {{ align-content: start; }}
    .slide-layout-section {{ background: var(--primary); color: #fff; }}
    .slide-layout-section::before {{ display: none; }}
    .slide-layout-section .slide-eyebrow, .slide-layout-section .slide-subtitle {{ color: rgb(255 255 255 / 82%); }}
    .slide-layout-two_column .slide-element {{ grid-column: span 6; }}
    .slide-layout-three_column .slide-element {{ grid-column: span 4; }}
    .slide-element {{ grid-column: 1 / -1; min-width: 0; }}
    .element-width-full {{ grid-column: 1 / -1 !important; }}
    .element-width-half {{ grid-column: span 6 !important; }}
    .element-width-third {{ grid-column: span 4 !important; }}
    .element-width-two-thirds {{ grid-column: span 8 !important; }}
    .element-card {{ background: #fff; border: 1px solid #dbe4f0; border-radius: 12px; padding: clamp(14px, 1.8vw, 26px); }}
    .element-title {{ margin: 0 0 12px; font-size: clamp(16px, 1.45vw, 23px); line-height: 1.25; }}
    .body-text {{ margin: 0; font-size: clamp(16px, 1.65vw, 28px); line-height: 1.6; white-space: pre-line; }}
    .bullet-list {{ margin: 0; padding-left: 1.25em; display: grid; gap: clamp(8px, 1vw, 16px); font-size: clamp(16px, 1.55vw, 25px); line-height: 1.45; }}
    .bullet-list li::marker {{ color: var(--primary); }}
    .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 14px; }}
    .kpi {{ border-top: 5px solid var(--primary); border-radius: 14px; background: #f8fafc; padding: 18px; }}
    .kpi-label {{ margin: 0; color: var(--muted); font-size: clamp(12px, 1vw, 17px); }}
    .kpi-value {{ margin: 7px 0 0; font-size: clamp(24px, 3vw, 46px); font-weight: 800; letter-spacing: -.03em; }}
    .kpi-delta {{ margin: 6px 0 0; color: var(--accent); font-weight: 700; }}
    .kpi.tone-danger {{ border-color: var(--danger); }}
    .kpi.tone-danger .kpi-delta {{ color: var(--danger); }}
    .callout {{ border-left: 6px solid var(--accent); background: #f0fdfa; border-radius: 10px; padding: 18px 22px; font-size: clamp(15px, 1.4vw, 23px); line-height: 1.5; }}
    .quote {{ margin: 0; padding: 8px 0 8px 26px; border-left: 7px solid var(--primary); font-size: clamp(20px, 2.4vw, 38px); line-height: 1.42; font-weight: 650; }}
    .table-wrap {{ max-height: 100%; overflow: hidden; border: 1px solid #dbe4f0; border-radius: 12px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: clamp(10px, .95vw, 16px); background: #fff; }}
    caption {{ padding: 10px 12px; text-align: left; color: var(--muted); font-weight: 700; }}
    th, td {{ padding: 9px 11px; border-bottom: 1px solid #e2e8f0; text-align: left; vertical-align: top; }}
    thead th {{ background: #eef2ff; color: #1e293b; font-weight: 800; }}
    tbody th {{ font-weight: 700; }}
    tbody tr:nth-child(even) {{ background: #f8fafc; }}
    .table-note, .chart-note {{ margin: 8px 0 0; color: var(--muted); font-size: clamp(10px, .85vw, 14px); }}
    .chart-wrap {{ min-height: 0; }}
    .chart-svg {{ width: 100%; height: auto; max-height: 41vh; display: block; }}
    .chart-axis {{ stroke: #94a3b8; stroke-width: 1.5; }}
    .chart-grid {{ stroke: #e2e8f0; stroke-width: 1; }}
    .chart-label {{ fill: var(--muted); font-family: var(--font-stack); font-size: 14px; }}
    .chart-value {{ fill: var(--text); font-family: var(--font-stack); font-size: 13px; font-weight: 700; }}
    .chart-line {{ fill: none; stroke: var(--primary); stroke-width: 4; stroke-linejoin: round; stroke-linecap: round; }}
    .chart-point {{ fill: var(--surface); stroke: var(--primary); stroke-width: 4; }}
    .image-frame {{ margin: 0; height: 100%; display: grid; align-content: center; }}
    .image-frame img {{ display: block; width: 100%; max-height: 43vh; object-fit: contain; border-radius: 12px; }}
    .image-frame figcaption {{ margin-top: 8px; color: var(--muted); font-size: clamp(10px, .9vw, 15px); }}
    .slide-footer {{ display: flex; justify-content: space-between; gap: 16px; color: var(--muted); font-size: clamp(10px, .82vw, 14px); }}
    .deck-controls {{
      width: min(100%, 1040px);
      margin: auto;
      display: grid;
      grid-template-columns: auto auto minmax(120px, 1fr) auto auto;
      align-items: center;
      gap: 10px;
      color: #fff;
    }}
    .deck-button {{ border: 1px solid rgb(255 255 255 / 30%); border-radius: 10px; background: rgb(255 255 255 / 12%); color: #fff; padding: 10px 14px; cursor: pointer; transition: transform var(--motion-button) var(--motion-easing), background-color var(--motion-button) var(--motion-easing); }}
    .deck-button:active {{ transform: scale(.97); }}
    .deck-button:focus-visible {{ background: rgb(255 255 255 / 22%); outline: 3px solid rgb(255 255 255 / 35%); outline-offset: 2px; }}
    .deck-button:disabled {{ cursor: not-allowed; opacity: .4; }}
    .progress-wrap {{ display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 10px; }}
    progress {{ width: 100%; height: 10px; accent-color: var(--primary); }}
    .deck-status {{ min-width: 72px; text-align: right; font-variant-numeric: tabular-nums; }}
    .noscript-note {{ position: fixed; left: 18px; bottom: 18px; z-index: 20; padding: 10px 14px; background: #fff7ed; color: #9a3412; border-radius: 8px; }}
    :fullscreen .deck-shell {{ padding: 0; grid-template-rows: 1fr auto; background: var(--deck-bg); }}
    :fullscreen .deck-stage {{ width: min(100vw, calc((100vh - 72px) * 16 / 9)); border-radius: 0; box-shadow: none; }}
    @media (max-width: 760px) {{
      .deck-shell {{ padding: 8px; gap: 8px; }}
      .deck-stage {{ width: 100%; }}
      .deck-controls {{ grid-template-columns: auto auto 1fr auto; }}
      #deck-fullscreen {{ display: none; }}
      .slide-layout-two_column .slide-element,
      .slide-layout-three_column .slide-element,
      .element-width-half,
      .element-width-third,
      .element-width-two-thirds {{ grid-column: 1 / -1 !important; }}
    }}
    @media (hover: hover) and (pointer: fine) {{
      .deck-button:hover {{ background: rgb(255 255 255 / 22%); }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      *, *::before, *::after {{ animation-duration: .01ms !important; animation-iteration-count: 1 !important; scroll-behavior: auto !important; transition-duration: 0ms !important; }}
      .deck-button:active {{ transform: none; }}
    }}
    @page {{ size: 13.333in 7.5in; margin: 0; }}
    @media print {{
      html, body {{ width: 13.333in; height: auto; background: #fff; overflow: visible; }}
      .deck-shell {{ display: block; min-height: 0; padding: 0; }}
      .deck-stage {{ width: 13.333in; height: auto; aspect-ratio: auto; margin: 0; overflow: visible; box-shadow: none; border-radius: 0; }}
      .slide, .slide[hidden] {{ position: relative; display: grid !important; width: 13.333in; height: 7.5in; break-after: page; page-break-after: always; }}
      .slide:last-child {{ break-after: auto; page-break-after: auto; }}
      .deck-controls, .noscript-note {{ display: none !important; }}
    }}
  </style>
</head>
<body>
  <main class="deck-shell" aria-label="{safe_title}" data-design-policy="{policy_id}" data-motion-profile="{motion_profile}">
    <section class="deck-stage" id="deck-stage" aria-label="프레젠테이션 슬라이드">
      {rendered_slides}
    </section>
    <nav class="deck-controls" aria-label="슬라이드 탐색">
      <button class="deck-button" id="deck-first" type="button" aria-label="첫 슬라이드">처음</button>
      <button class="deck-button" id="deck-prev" type="button" aria-label="이전 슬라이드">이전</button>
      <div class="progress-wrap">
        <progress id="deck-progress" max="{total}" value="1" aria-label="프레젠테이션 진행률"></progress>
        <span class="deck-status" id="deck-status" aria-live="polite">1 / {total}</span>
      </div>
      <button class="deck-button" id="deck-next" type="button" aria-label="다음 슬라이드">다음</button>
      <button class="deck-button" id="deck-fullscreen" type="button" aria-label="전체 화면 전환">전체 화면</button>
    </nav>
  </main>
  <noscript><p class="noscript-note">슬라이드 탐색에는 JavaScript가 필요합니다. 인쇄하면 전체 슬라이드가 출력됩니다.</p></noscript>
  <script>
    (() => {{
      "use strict";
      const slides = Array.from(document.querySelectorAll("[data-deck-slide]"));
      const previousButton = document.getElementById("deck-prev");
      const nextButton = document.getElementById("deck-next");
      const firstButton = document.getElementById("deck-first");
      const fullscreenButton = document.getElementById("deck-fullscreen");
      const progress = document.getElementById("deck-progress");
      const status = document.getElementById("deck-status");
      const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
      const slideMotion = {{ duration: {slide_duration}, easing: "{motion_easing}" }};
      let currentIndex = 0;

      const indexFromHash = () => {{
        const match = window.location.hash.match(/^#slide-(\\d+)$/);
        if (!match) return 0;
        return Math.min(slides.length - 1, Math.max(0, Number(match[1]) - 1));
      }};

      const showSlide = (nextIndex, updateHash = true, interaction = "programmatic") => {{
        currentIndex = Math.min(slides.length - 1, Math.max(0, nextIndex));
        slides.forEach((slide, index) => {{ slide.hidden = index !== currentIndex; }});
        previousButton.disabled = currentIndex === 0;
        firstButton.disabled = currentIndex === 0;
        nextButton.disabled = currentIndex === slides.length - 1;
        progress.value = currentIndex + 1;
        status.textContent = `${{currentIndex + 1}} / ${{slides.length}}`;
        if (updateHash) {{
          const nextHash = `#slide-${{currentIndex + 1}}`;
          if (window.location.hash !== nextHash) history.replaceState(null, "", nextHash);
        }}
        if (interaction === "pointer" && !reducedMotion.matches && slides[currentIndex].animate) {{
          slides[currentIndex].animate(
            [{{ opacity: 0, transform: "translateY(10px)" }}, {{ opacity: 1, transform: "translateY(0)" }}],
            slideMotion
          );
        }}
        slides[currentIndex].focus({{ preventScroll: true }});
      }};

      previousButton.addEventListener("click", () => showSlide(currentIndex - 1, true, "pointer"));
      nextButton.addEventListener("click", () => showSlide(currentIndex + 1, true, "pointer"));
      firstButton.addEventListener("click", () => showSlide(0, true, "pointer"));
      window.addEventListener("hashchange", () => showSlide(indexFromHash(), false));
      document.addEventListener("keydown", (event) => {{
        const tagName = String(event.target && event.target.tagName || "").toLowerCase();
        if (["input", "textarea", "select"].includes(tagName)) return;
        const keyActions = {{
          ArrowRight: () => showSlide(currentIndex + 1),
          ArrowDown: () => showSlide(currentIndex + 1),
          PageDown: () => showSlide(currentIndex + 1),
          " ": () => showSlide(currentIndex + 1),
          ArrowLeft: () => showSlide(currentIndex - 1),
          ArrowUp: () => showSlide(currentIndex - 1),
          PageUp: () => showSlide(currentIndex - 1),
          Home: () => showSlide(0),
          End: () => showSlide(slides.length - 1),
        }};
        const action = keyActions[event.key];
        if (action) {{ event.preventDefault(); action(); }}
      }});
      fullscreenButton.addEventListener("click", async () => {{
        try {{
          if (document.fullscreenElement) await document.exitFullscreen();
          else await document.documentElement.requestFullscreen();
        }} catch (_error) {{
          fullscreenButton.textContent = "전체 화면 사용 불가";
        }}
      }});
      document.addEventListener("fullscreenchange", () => {{
        fullscreenButton.textContent = document.fullscreenElement ? "전체 화면 종료" : "전체 화면";
        fullscreenButton.setAttribute("aria-label", fullscreenButton.textContent);
      }});

      showSlide(indexFromHash(), false);
    }})();
  </script>
</body>
</html>"""


def _render_slide(
    slide: dict[str, Any],
    index: int,
    total: int,
    theme: dict[str, str],
    warnings: list[dict[str, Any]],
) -> str:
    hidden = "" if index == 0 else " hidden"
    eyebrow = (
        f'<p class="slide-eyebrow">{html.escape(slide["eyebrow"])}</p>'
        if slide["eyebrow"]
        else ""
    )
    subtitle = (
        f'<p class="slide-subtitle">{html.escape(slide["subtitle"])}</p>'
        if slide["subtitle"]
        else ""
    )
    rendered_elements: list[str] = []
    for element_index, element in enumerate(slide["elements"]):
        rendered = _render_element(
            element,
            slide_index=index,
            element_index=element_index,
            theme=theme,
            warnings=warnings,
        )
        if rendered:
            rendered_elements.append(rendered)
    footer_text = html.escape(slide["footer"])
    return f"""
      <article class="slide slide-layout-{slide['layout']} slide-role-{slide['design_role']} slide-weight-{slide['visual_weight']}" id="slide-{index + 1}" data-deck-slide data-design-role="{slide['design_role']}" data-visual-weight="{slide['visual_weight']}" tabindex="-1" role="group" aria-roledescription="슬라이드" aria-label="{index + 1} / {total}: {html.escape(slide['title'])}"{hidden}>
        <header class="slide-header">
          {eyebrow}
          <h1>{html.escape(slide['title'])}</h1>
          {subtitle}
        </header>
        <div class="slide-body">{''.join(rendered_elements)}</div>
        <footer class="slide-footer"><span>{footer_text}</span><span>{index + 1} / {total}</span></footer>
      </article>"""


def _render_element(
    element: dict[str, Any],
    *,
    slide_index: int,
    element_index: int,
    theme: dict[str, str],
    warnings: list[dict[str, Any]],
) -> str:
    element_type = str(element.get("type") or element.get("element_type") or "text").strip().lower().replace("-", "_")
    width = _WIDTHS.get(str(element.get("width") or "full").strip().lower(), _WIDTHS["full"])
    title = _text(element.get("title"), "", 180)
    title_markup = f'<h2 class="element-title">{html.escape(title)}</h2>' if title else ""
    card = bool(element.get("card", element_type not in {"text", "bullets", "bullet_list", "quote", "kpi", "kpis", "kpi_grid", "metrics"}))
    classes = f"slide-element {width}" + (" element-card" if card else "")
    body = ""

    if element_type in {"text", "paragraph"}:
        body = f'<p class="body-text">{html.escape(_text(element.get("text") or element.get("content"), "", 8000))}</p>'
    elif element_type in {"bullets", "bullet_list", "list"}:
        items = _list(element.get("items") or element.get("bullets"))[:20]
        body = '<ul class="bullet-list">' + "".join(
            f"<li>{html.escape(_item_text(item, 900))}</li>" for item in items
        ) + "</ul>"
    elif element_type in {"kpi", "kpis", "kpi_grid", "metrics"}:
        body = _render_kpis(element)
    elif element_type == "table":
        body = _render_table(element)
    elif element_type in {"bar", "bar_chart", "column_chart"}:
        body = _render_bar_chart(element, slide_index, element_index, theme)
    elif element_type in {"line", "line_chart", "trend_line_chart"}:
        body = _render_line_chart(element, slide_index, element_index)
    elif element_type in {"scatter", "scatter_chart", "scatter_plot"}:
        body = _render_scatter_chart(element, slide_index, element_index)
    elif element_type in {"callout", "insight"}:
        body = f'<div class="callout">{html.escape(_text(element.get("text") or element.get("content"), "", 3000))}</div>'
    elif element_type == "quote":
        body = f'<blockquote class="quote">{html.escape(_text(element.get("text") or element.get("content"), "", 3000))}</blockquote>'
    elif element_type == "image":
        body = _render_image(element, warnings, slide_index + 1)
    else:
        warnings.append(
            {
                "code": "unsupported_element_skipped",
                "message": "지원하지 않는 슬라이드 요소를 제외했습니다.",
                "slide_position": slide_index + 1,
                "element_type": element_type,
            }
        )
        return ""
    if not body:
        return ""
    source_note = _text(element.get("source_note"), "", 500)
    source_markup = (
        f'<p class="chart-note">{html.escape(source_note)}</p>' if source_note else ""
    )
    return f'<section class="{classes}">{title_markup}{body}{source_markup}</section>'


def _render_kpis(element: dict[str, Any]) -> str:
    items = _list(element.get("items") or element.get("metrics"))[:8]
    cards: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        tone = "danger" if str(item.get("tone") or "").lower() in {"danger", "warning", "risk"} else "default"
        delta = _text(item.get("delta"), "", 100)
        delta_markup = f'<p class="kpi-delta">{html.escape(delta)}</p>' if delta else ""
        cards.append(
            f'<div class="kpi tone-{tone}"><p class="kpi-label">{html.escape(_text(item.get("label"), "지표", 120))}</p>'
            f'<p class="kpi-value">{html.escape(_text(item.get("value"), "-", 120))}</p>{delta_markup}</div>'
        )
    return f'<div class="kpi-grid">{"".join(cards)}</div>' if cards else ""


def _render_table(element: dict[str, Any]) -> str:
    raw_columns = _list(element.get("columns"))
    raw_rows = _list(element.get("rows") or element.get("data"))
    if not raw_columns and raw_rows and isinstance(raw_rows[0], dict):
        raw_columns = list(raw_rows[0].keys())

    columns: list[tuple[str, str]] = []
    for column in raw_columns[:16]:
        if isinstance(column, dict):
            key = _text(column.get("key") or column.get("name"), "", 120)
            label = _text(column.get("label") or column.get("display_name") or key, key, 160)
        else:
            key = _text(column, "", 120)
            label = key
        if key:
            columns.append((key, label))
    if not columns:
        return '<p class="body-text">표시할 표 컬럼이 없습니다.</p>'

    requested_limit = _positive_int(element.get("limit"), 12, 1, MAX_TABLE_ROWS)
    rows = raw_rows[:requested_limit]
    headers = "".join(f'<th scope="col">{html.escape(label)}</th>' for _key, label in columns)
    body_rows: list[str] = []
    for row in rows:
        cells: list[str] = []
        for column_index, (key, _label) in enumerate(columns):
            if isinstance(row, dict):
                value = row.get(key)
            elif isinstance(row, (list, tuple)):
                value = row[column_index] if column_index < len(row) else ""
            else:
                value = row if column_index == 0 else ""
            tag = "th" if column_index == 0 else "td"
            scope = ' scope="row"' if column_index == 0 else ""
            cells.append(f'<{tag}{scope}>{html.escape(_text(value, "", 300))}</{tag}>')
        body_rows.append(f"<tr>{''.join(cells)}</tr>")

    caption = _text(element.get("caption") or element.get("title"), "데이터 표", 200)
    truncated = len(raw_rows) > len(rows)
    note = (
        f'<p class="table-note">전체 {len(raw_rows):,}행 중 {len(rows):,}행을 표시합니다.</p>'
        if truncated
        else ""
    )
    return (
        f'<div class="table-wrap"><table><caption>{html.escape(caption)}</caption>'
        f'<thead><tr>{headers}</tr></thead><tbody>{"".join(body_rows)}</tbody></table></div>{note}'
    )


def _render_bar_chart(element: dict[str, Any], slide_index: int, element_index: int, theme: dict[str, str]) -> str:
    points = _category_points(element)[:MAX_CHART_POINTS]
    if not points:
        return '<p class="body-text">표시할 막대그래프 데이터가 없습니다.</p>'

    values = [value for _label, value in points]
    minimum = min(0.0, min(values))
    maximum = max(0.0, max(values))
    span = maximum - minimum or 1.0
    left, top, plot_width, plot_height = 72.0, 24.0, 824.0, 294.0
    baseline_y = top + (maximum / span) * plot_height
    slot = plot_width / len(points)
    bar_width = max(6.0, slot * 0.58)
    title_id = f"bar-title-{slide_index}-{element_index}"
    desc_id = f"bar-desc-{slide_index}-{element_index}"
    bars: list[str] = []
    for index, (label, value) in enumerate(points):
        value_y = top + ((maximum - value) / span) * plot_height
        y = min(value_y, baseline_y)
        height = max(1.0, abs(value_y - baseline_y))
        x = left + slot * index + (slot - bar_width) / 2
        color = theme[f"chart_{index % 4 + 1}"]
        bars.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_width:.2f}" height="{height:.2f}" rx="5" fill="{color}"><title>{html.escape(label)}: {html.escape(_format_number(value))}</title></rect>'
            f'<text class="chart-value" x="{x + bar_width / 2:.2f}" y="{max(14.0, y - 7):.2f}" text-anchor="middle">{html.escape(_format_number(value))}</text>'
            f'<text class="chart-label" x="{x + bar_width / 2:.2f}" y="{top + plot_height + 26:.2f}" text-anchor="middle">{html.escape(_truncate(label, 14))}</text>'
        )
    title = _text(element.get("title"), "막대그래프", 180)
    desc = ", ".join(f"{label} {_format_number(value)}" for label, value in points)
    return f"""
<div class="chart-wrap">
  <svg class="chart-svg" viewBox="0 0 960 370" role="img" aria-labelledby="{title_id} {desc_id}">
    <title id="{title_id}">{html.escape(title)}</title>
    <desc id="{desc_id}">{html.escape(desc)}</desc>
    <line class="chart-grid" x1="{left}" y1="{top}" x2="{left + plot_width}" y2="{top}" />
    <line class="chart-axis" x1="{left}" y1="{baseline_y:.2f}" x2="{left + plot_width}" y2="{baseline_y:.2f}" />
    {''.join(bars)}
  </svg>
</div>"""


def _render_line_chart(element: dict[str, Any], slide_index: int, element_index: int) -> str:
    points = _category_points(element)[:MAX_CHART_POINTS]
    if not points:
        return '<p class="body-text">표시할 선그래프 데이터가 없습니다.</p>'
    values = [value for _label, value in points]
    minimum, maximum = min(values), max(values)
    if math.isclose(minimum, maximum):
        minimum -= 1.0
        maximum += 1.0
    left, top, plot_width, plot_height = 72.0, 28.0, 824.0, 280.0
    x_step = plot_width / max(1, len(points) - 1)
    coordinates: list[tuple[float, float]] = []
    for index, (_label, value) in enumerate(points):
        x = left + x_step * index
        y = top + (maximum - value) / (maximum - minimum) * plot_height
        coordinates.append((x, y))
    polyline = " ".join(f"{x:.2f},{y:.2f}" for x, y in coordinates)
    title_id = f"line-title-{slide_index}-{element_index}"
    desc_id = f"line-desc-{slide_index}-{element_index}"
    marks: list[str] = []
    for index, ((label, value), (x, y)) in enumerate(zip(points, coordinates, strict=False)):
        show_label = len(points) <= 12 or index in {0, len(points) - 1} or index % 2 == 0
        label_markup = (
            f'<text class="chart-label" x="{x:.2f}" y="{top + plot_height + 26:.2f}" text-anchor="middle">{html.escape(_truncate(label, 12))}</text>'
            if show_label
            else ""
        )
        marks.append(
            f'<circle class="chart-point" cx="{x:.2f}" cy="{y:.2f}" r="5"><title>{html.escape(label)}: {html.escape(_format_number(value))}</title></circle>{label_markup}'
        )
    title = _text(element.get("title"), "선그래프", 180)
    desc = ", ".join(f"{label} {_format_number(value)}" for label, value in points)
    return f"""
<div class="chart-wrap">
  <svg class="chart-svg" viewBox="0 0 960 370" role="img" aria-labelledby="{title_id} {desc_id}">
    <title id="{title_id}">{html.escape(title)}</title>
    <desc id="{desc_id}">{html.escape(desc)}</desc>
    <line class="chart-grid" x1="{left}" y1="{top}" x2="{left + plot_width}" y2="{top}" />
    <line class="chart-grid" x1="{left}" y1="{top + plot_height / 2:.2f}" x2="{left + plot_width}" y2="{top + plot_height / 2:.2f}" />
    <line class="chart-axis" x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" y2="{top + plot_height}" />
    <polyline class="chart-line" points="{polyline}" />
    {''.join(marks)}
  </svg>
</div>"""


def _render_scatter_chart(element: dict[str, Any], slide_index: int, element_index: int) -> str:
    points = _scatter_points(element)[:MAX_CHART_POINTS]
    if not points:
        return '<p class="body-text">표시할 산점도 데이터가 없습니다.</p>'
    x_values = [point[1] for point in points]
    y_values = [point[2] for point in points]
    x_min, x_max = _expanded_range(min(x_values), max(x_values))
    y_min, y_max = _expanded_range(min(y_values), max(y_values))
    left, top, plot_width, plot_height = 72.0, 28.0, 824.0, 280.0
    title_id = f"scatter-title-{slide_index}-{element_index}"
    desc_id = f"scatter-desc-{slide_index}-{element_index}"
    circles: list[str] = []
    for label, x_value, y_value in points:
        x = left + (x_value - x_min) / (x_max - x_min) * plot_width
        y = top + (y_max - y_value) / (y_max - y_min) * plot_height
        circles.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="7" fill="var(--primary)" fill-opacity=".78"><title>{html.escape(label)}: X {_format_number(x_value)}, Y {_format_number(y_value)}</title></circle>'
        )
    title = _text(element.get("title"), "산점도", 180)
    desc = ", ".join(
        f"{label} X {_format_number(x_value)} Y {_format_number(y_value)}"
        for label, x_value, y_value in points
    )
    x_label = html.escape(_text(element.get("x_label"), "X", 80))
    y_label = html.escape(_text(element.get("y_label"), "Y", 80))
    return f"""
<div class="chart-wrap">
  <svg class="chart-svg" viewBox="0 0 960 370" role="img" aria-labelledby="{title_id} {desc_id}">
    <title id="{title_id}">{html.escape(title)}</title>
    <desc id="{desc_id}">{html.escape(desc)}</desc>
    <line class="chart-grid" x1="{left}" y1="{top}" x2="{left + plot_width}" y2="{top}" />
    <line class="chart-grid" x1="{left}" y1="{top + plot_height / 2:.2f}" x2="{left + plot_width}" y2="{top + plot_height / 2:.2f}" />
    <line class="chart-axis" x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" y2="{top + plot_height}" />
    <line class="chart-axis" x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}" />
    {''.join(circles)}
    <text class="chart-label" x="{left + plot_width / 2:.2f}" y="{top + plot_height + 32:.2f}" text-anchor="middle">{x_label}</text>
    <text class="chart-label" x="18" y="{top + plot_height / 2:.2f}" text-anchor="middle" transform="rotate(-90 18 {top + plot_height / 2:.2f})">{y_label}</text>
  </svg>
</div>"""


def _render_image(
    element: dict[str, Any],
    warnings: list[dict[str, Any]],
    slide_position: int,
) -> str:
    source = str(element.get("src") or element.get("data_url") or "").strip()
    if not _safe_image_data_url(source):
        warnings.append(
            {
                "code": "unsafe_image_source_skipped",
                "message": "외부 URL 또는 유효하지 않은 이미지 Data URL을 제외했습니다.",
                "slide_position": slide_position,
            }
        )
        return ""
    alt = _text(element.get("alt"), "프레젠테이션 이미지", 240)
    caption = _text(element.get("caption"), "", 300)
    caption_markup = f"<figcaption>{html.escape(caption)}</figcaption>" if caption else ""
    return (
        f'<figure class="image-frame"><img src="{html.escape(source, quote=True)}" alt="{html.escape(alt)}" />'
        f"{caption_markup}</figure>"
    )


def _category_points(element: dict[str, Any]) -> list[tuple[str, float]]:
    raw = element.get("data") or element.get("points")
    result: list[tuple[str, float]] = []
    if isinstance(raw, list):
        for index, item in enumerate(raw):
            if isinstance(item, dict):
                label = _text(item.get("label") or item.get("category") or item.get("x"), f"항목 {index + 1}", 100)
                value = _number(item.get("value") if item.get("value") is not None else item.get("y"))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                label = _text(item[0], f"항목 {index + 1}", 100)
                value = _number(item[1])
            else:
                label = f"항목 {index + 1}"
                value = _number(item)
            if value is not None:
                result.append((label, value))
        return result

    labels = _list(element.get("labels"))
    values = _list(element.get("values"))
    for index, value_raw in enumerate(values):
        value = _number(value_raw)
        if value is None:
            continue
        label = _text(labels[index] if index < len(labels) else f"항목 {index + 1}", f"항목 {index + 1}", 100)
        result.append((label, value))
    return result


def _scatter_points(element: dict[str, Any]) -> list[tuple[str, float, float]]:
    raw = _list(element.get("data") or element.get("points"))
    result: list[tuple[str, float, float]] = []
    for index, item in enumerate(raw):
        if isinstance(item, dict):
            label = _text(item.get("label") or item.get("name"), f"항목 {index + 1}", 100)
            x_value = _number(item.get("x"))
            y_value = _number(item.get("y"))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            label = _text(item[2] if len(item) > 2 else f"항목 {index + 1}", f"항목 {index + 1}", 100)
            x_value = _number(item[0])
            y_value = _number(item[1])
        else:
            continue
        if x_value is not None and y_value is not None:
            result.append((label, x_value, y_value))
    return result


def _safe_image_data_url(value: str) -> bool:
    match = _DATA_IMAGE_RE.fullmatch(value)
    if not match:
        return False
    encoded = re.sub(r"\s+", "", match.group("data"))
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return False
    return bool(decoded)


def _error_result(
    title: str,
    plan: dict[str, Any],
    payload: dict[str, Any],
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    filename_hint = _filename_hint(plan.get("filename_hint") or title)
    empty_artifact = {
        "title": title,
        "html": "",
        "mime_type": "text/html; charset=utf-8",
        "filename_hint": filename_hint,
        "slide_count": 0,
        "aspect_ratio": "16:9",
        "self_contained": True,
        "byte_size": 0,
        "sha256": "",
    }
    return {
        "success": False,
        "status": "error",
        "payload_version": "html-presentation-artifact-v1",
        "flow_type": "ppt_image_to_html",
        "request": _public_request(payload.get("request")),
        "report_plan": {
            "title": title,
            "filename_hint": filename_hint,
            "layout": "html_presentation_16_9",
            "slide_count": 0,
        },
        "presentation_artifact": empty_artifact,
        "html_report": deepcopy(empty_artifact),
        "warnings": warnings,
        "errors": errors,
        "trace": [
            {
                "stage": "html_presentation_renderer",
                "status": "failed",
                "error_codes": [item.get("code") for item in errors],
            }
        ],
    }


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return deepcopy(value)
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return deepcopy(data)
    text = getattr(value, "text", None) or getattr(value, "content", None)
    if isinstance(text, str):
        try:
            parsed = json.loads(text)
        except Exception:
            return {}
        return deepcopy(parsed) if isinstance(parsed, dict) else {}
    return {}


def _public_request(value: Any) -> dict[str, Any]:
    request = _dict(value)
    allowed = (
        "question",
        "view_request",
        "title",
        "audience",
        "objective",
        "language",
    )
    return {
        key: _text(request.get(key), "", 1000)
        for key in allowed
        if request.get(key) not in (None, "")
    }


def _dict(value: Any) -> dict[str, Any]:
    return deepcopy(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _text(value: Any, default: str = "", limit: int = 1000) -> str:
    if value is None:
        return default
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    text = text.strip()
    return (text or default)[:limit]


def _item_text(value: Any, limit: int) -> str:
    if isinstance(value, dict):
        return _text(value.get("text") or value.get("label") or value.get("value"), "", limit)
    return _text(value, "", limit)


def _safe_id(value: Any) -> str:
    text = _SAFE_ID_RE.sub("-", str(value or "").strip()).strip("-_").lower()
    return text[:80]


def _filename_hint(value: Any) -> str:
    text = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", str(value or "presentation").strip())
    text = text.strip("._-")[:100] or "presentation"
    return text[:-5] if text.lower().endswith(".html") else text


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _format_number(value: float) -> str:
    if math.isclose(value, round(value)):
        return f"{int(round(value)):,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _expanded_range(minimum: float, maximum: float) -> tuple[float, float]:
    if not math.isclose(minimum, maximum):
        return minimum, maximum
    padding = abs(minimum) * 0.1 or 1.0
    return minimum - padding, maximum + padding


def _positive_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: max(1, limit - 1)] + "…"


class HtmlPresentationRenderer(Component):
    """Langflow Builder에 표시되는 Standalone Component입니다."""

    display_name = "HTML 프레젠테이션 렌더러"
    description = "검증된 슬라이드 계획과 디자인·모션 정책을 독립 16:9 HTML 프레젠테이션으로 결정론적으로 변환합니다."
    icon = "Presentation"
    name = "HtmlPresentationRenderer"

    inputs = [
        DataInput(
            name="presentation_plan",
            display_name="검증된 프레젠테이션 계획",
            required=True,
            info="presentation_plan.slides와 선택적 theme을 포함한 검증 완료 Data를 연결합니다.",
        )
    ]

    outputs = [
        Output(
            name="presentation_artifact",
            display_name="HTML 프레젠테이션 결과",
            method="build_artifact",
            types=["Data"],
        )
    ]

    def build_artifact(self) -> Data:
        result = render_html_presentation(getattr(self, "presentation_plan", None))
        artifact = _dict(result.get("presentation_artifact"))
        self.status = {
            "status": result.get("status"),
            "slide_count": artifact.get("slide_count", 0),
            "byte_size": artifact.get("byte_size", 0),
            "sha256": artifact.get("sha256", ""),
            "warning_count": len(_list(result.get("warnings"))),
            "error_count": len(_list(result.get("errors"))),
        }
        return Data(data=result)
