from __future__ import annotations

"""모델이 만든 발표 계획을 Renderer가 허용하는 안전한 계약으로 제한한다.

레이아웃과 요소 유형은 허용 목록만 통과한다. 데이터 시각화는 실제 데이터셋과
실제 컬럼을 참조할 때만 유지하며 raw HTML/CSS/JavaScript와 임의 값 배열은 버린다.
"""

import html
import json
import re
from copy import deepcopy
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, IntInput, Output
from lfx.schema.data import Data


_ALLOWED_LAYOUTS = {
    "cover",
    "section",
    "title-content",
    "two-column",
    "kpi-grid",
    "chart-focus",
    "table-focus",
    "comparison",
    "timeline",
    "conclusion",
}
_LAYOUT_ALIASES = {
    "content": "title-content",
    "title_content": "title-content",
    "two_column": "two-column",
    "data_visual": "chart-focus",
    "data_visualization": "chart-focus",
    "chart": "chart-focus",
    "table": "table-focus",
    "image": "title-content",
    "closing": "conclusion",
}
_ALLOWED_ELEMENTS = {
    "title",
    "subtitle",
    "text",
    "bullet_list",
    "kpi",
    "image",
    "shape",
    "table",
    "bar_chart",
    "line_chart",
    "stacked_bar",
    "scatter_plot",
    "histogram",
    "process",
    "timeline",
    "source_note",
    "speaker_note",
}
_ELEMENT_ALIASES = {
    "bullets": "bullet_list",
    "bullet": "bullet_list",
    "bar": "bar_chart",
    "line": "line_chart",
    "scatter": "scatter_plot",
    "donut": "bar_chart",
    "source": "source_note",
}
_DATA_ELEMENTS = {"kpi", "table", "bar_chart", "line_chart", "stacked_bar", "scatter_plot", "histogram"}
_RAW_KEYS = {
    "html",
    "raw_html",
    "css",
    "raw_css",
    "javascript",
    "raw_js",
    "script",
    "style",
    "onclick",
    "onload",
    "srcdoc",
}
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_DEFAULT_DESIGN = {
    "aspect_ratio": "16:9",
    "colors": {
        "primary": "#2563EB",
        "secondary": "#0F766E",
        "accent": "#F59E0B",
        "background": "#F8FAFC",
        "surface": "#FFFFFF",
        "text": "#0F172A",
        "muted": "#475569",
    },
    "typography": {
        "heading_family": "Pretendard, Noto Sans KR, sans-serif",
        "body_family": "Pretendard, Noto Sans KR, sans-serif",
        "heading_scale": "strong",
    },
    "safe_margin_percent": {"top": 5, "right": 5, "bottom": 5, "left": 5},
    "motifs": ["좌측 정렬 제목", "간결한 정보 카드"],
}


def _make_data(payload: dict[str, Any]) -> Data:
    try:
        return Data(data=payload)
    except TypeError:
        return Data(payload)


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return deepcopy(value)
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return deepcopy(data)
    text = getattr(value, "text", None) or getattr(value, "content", None)
    if isinstance(text, str) and text.strip():
        try:
            parsed = json.loads(text)
            return deepcopy(parsed) if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _root(payload: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        if isinstance(payload.get(key), dict):
            return deepcopy(payload[key])
    return payload


def _safe_text(value: Any, maximum: int = 2_000) -> str:
    """사용자/모델 문자열에서 실행 가능한 마크업을 제거한다."""

    text = html.unescape(str(value or "")).replace("\x00", " ")
    text = re.sub(r"<script\b[^>]*>[\s\S]*?</script\s*>", "", text, flags=re.I)
    text = re.sub(r"<style\b[^>]*>[\s\S]*?</style\s*>", "", text, flags=re.I)
    text = re.sub(r"<!--[\s\S]*?-->", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"(?:javascript|vbscript)\s*:", "", text, flags=re.I)
    return text.strip()[:maximum]


def _safe_identifier(value: Any, fallback: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(value or "").strip())[:100]
    return text or fallback


def _layout(value: Any, fallback: str = "title-content") -> str:
    key = str(value or "").strip().lower().replace(" ", "_")
    key = _LAYOUT_ALIASES.get(key, key.replace("_", "-"))
    return key if key in _ALLOWED_LAYOUTS else fallback


def _element_type(value: Any) -> str:
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    key = _ELEMENT_ALIASES.get(key, key)
    return key if key in _ALLOWED_ELEMENTS else ""


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except Exception:
        number = default
    return max(minimum, min(maximum, number))


def _normalize_grid(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    x = _clamp_int(value.get("x"), 1, 1, 12)
    y = _clamp_int(value.get("y"), 1, 1, 12)
    width = _clamp_int(value.get("w") or value.get("width"), 12, 1, 12)
    height = _clamp_int(value.get("h") or value.get("height"), 4, 1, 12)
    if x + width - 1 > 12:
        width = 13 - x
    if y + height - 1 > 12:
        height = 13 - y
    return {"x": x, "y": y, "w": width, "h": height}


def _dataset_catalog(request: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, set[str]]]:
    datasets = request.get("datasets")
    by_id: dict[str, dict[str, Any]] = {}
    columns: dict[str, set[str]] = {}
    if not isinstance(datasets, list):
        return by_id, columns
    for index, item in enumerate(datasets, 1):
        if not isinstance(item, dict):
            continue
        dataset_id = str(item.get("dataset_id") or item.get("id") or f"dataset_{index}")
        by_id[dataset_id] = deepcopy(item)
        names: set[str] = set()
        declared = item.get("columns")
        if isinstance(declared, list):
            for column in declared:
                name = column if isinstance(column, str) else column.get("name") if isinstance(column, dict) else ""
                if name:
                    names.add(str(name))
        rows = item.get("rows")
        if isinstance(rows, list):
            for row in rows[:50]:
                if isinstance(row, dict):
                    names.update(str(key) for key in row)
        columns[dataset_id] = names
    return by_id, columns


def _referenced_fields(value: Any) -> set[str]:
    """encoding에서 데이터 컬럼을 의미하는 문자열을 재귀적으로 모은다."""

    result: set[str] = set()
    if isinstance(value, str) and value.strip():
        result.add(value.strip())
    elif isinstance(value, list):
        for item in value:
            result.update(_referenced_fields(item))
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in {"unit", "format", "aggregation", "sort", "direction", "label"}:
                continue
            result.update(_referenced_fields(item))
    return result


def _normalize_encoding(value: Any, available: set[str], warnings: list[str], context: str) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return {}
    clean: dict[str, Any] = {}
    for key in ("x", "y", "series", "value", "columns", "category", "size", "color", "unit", "format"):
        item = value.get(key)
        if key in {"unit", "format"}:
            if item is not None:
                clean[key] = _safe_text(item, 100)
            continue
        if isinstance(item, str):
            clean[key] = _safe_text(item, 200)
        elif isinstance(item, list):
            clean[key] = [_safe_text(entry, 200) for entry in item[:20] if isinstance(entry, str) and _safe_text(entry, 200)]
    fields = _referenced_fields(clean)
    missing = sorted(field for field in fields if field not in available)
    if missing:
        warnings.append(f"{context}: 데이터셋에 없는 컬럼 참조를 제거했습니다: {', '.join(missing[:8])}")
        return None
    return clean


def _normalize_data_views(
    raw_views: Any,
    datasets: dict[str, dict[str, Any]],
    dataset_columns: dict[str, set[str]],
    warnings: list[str],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    views: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    if not isinstance(raw_views, list):
        return views, by_id
    for index, raw in enumerate(raw_views[:100], 1):
        if not isinstance(raw, dict):
            continue
        dataset_id = str(raw.get("dataset_id") or "")
        if dataset_id not in datasets:
            warnings.append(f"data_view {index}: 존재하지 않는 데이터셋 '{_safe_text(dataset_id, 100)}' 참조를 제거했습니다.")
            continue
        view_id = _safe_identifier(raw.get("data_view_id") or raw.get("id"), f"view_{index}")
        if view_id in by_id:
            view_id = f"{view_id}_{index}"
        encoding = _normalize_encoding(raw.get("encoding"), dataset_columns[dataset_id], warnings, view_id)
        if encoding is None:
            continue
        visual = str(raw.get("visual") or raw.get("type") or "table").strip().lower().replace("-", "_")
        visual_aliases = {"bar_chart": "bar", "line_chart": "line", "scatter_plot": "scatter"}
        visual = visual_aliases.get(visual, visual)
        if visual not in {"table", "kpi", "bar", "line", "stacked_bar", "scatter", "histogram"}:
            visual = "table"
        view = {"data_view_id": view_id, "dataset_id": dataset_id, "visual": visual, "encoding": encoding}
        views.append(view)
        by_id[view_id] = view
    return views, by_id


def _normalize_content(value: Any, element_type: str) -> Any:
    if element_type == "bullet_list":
        if isinstance(value, list):
            return [_safe_text(item, 400) for item in value[:12] if _safe_text(item, 400)]
        text = _safe_text(value, 2_000)
        return [item.strip() for item in re.split(r"\r?\n|[•]", text) if item.strip()][:12]
    return _safe_text(value, 4_000)


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value or "").strip().replace(",", "")
    if text.endswith("%"):
        text = text[:-1]
    try:
        number = float(text)
    except Exception:
        return None
    return int(number) if number.is_integer() else number


def _column_label(dataset: dict[str, Any], name: str) -> str:
    columns = dataset.get("columns")
    if isinstance(columns, list):
        for column in columns:
            if isinstance(column, dict) and str(column.get("name") or "") == name:
                return _safe_text(column.get("label") or name, 160)
    return _safe_text(name, 160)


def _first_encoding_field(encoding: dict[str, Any], key: str) -> str:
    value = encoding.get(key)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return next((str(item) for item in value if isinstance(item, str) and item), "")
    return ""


def _materialize_data_element(
    element: dict[str, Any],
    dataset_id: str,
    dataset: dict[str, Any],
    warnings: list[str],
    context: str,
) -> bool:
    """검증한 원본 rows만 Renderer가 읽는 element 데이터 계약으로 변환한다."""

    rows = [row for row in dataset.get("rows", []) if isinstance(row, dict)] if isinstance(dataset.get("rows"), list) else []
    encoding = element.get("encoding") if isinstance(element.get("encoding"), dict) else {}
    element_type = str(element.get("element_type") or "")
    if element_type in {"stacked_bar", "histogram"}:
        warnings.append(f"{context}: Renderer 미지원 {element_type}을 원본 데이터 표로 변경했습니다.")
        element_type = "table"
        element["element_type"] = "table"
    if element_type == "table":
        fields = encoding.get("columns") if isinstance(encoding.get("columns"), list) else []
        if not fields:
            fields = [
                str(column.get("name")) if isinstance(column, dict) else str(column)
                for column in (dataset.get("columns") if isinstance(dataset.get("columns"), list) else [])
            ]
        fields = [field for field in fields if field][:16]
        if not fields:
            warnings.append(f"{context}: 표로 표시할 컬럼이 없어 요소를 제외했습니다.")
            return False
        element["columns"] = [{"key": field, "label": _column_label(dataset, field)} for field in fields]
        element["rows"] = [{field: row.get(field) for field in fields} for row in rows[:200]]
        element["caption"] = _safe_text(dataset.get("title") or "데이터 표", 200)
        element["limit"] = 12
        return bool(element["rows"] or fields)
    if element_type == "kpi":
        value_field = _first_encoding_field(encoding, "value") or _first_encoding_field(encoding, "y")
        label_field = _first_encoding_field(encoding, "x") or _first_encoding_field(encoding, "category")
        if not value_field:
            warnings.append(f"{context}: KPI 값 컬럼이 없어 요소를 제외했습니다.")
            return False
        items: list[dict[str, Any]] = []
        for index, row in enumerate(rows[:8], 1):
            value = row.get(value_field)
            if value is None:
                continue
            label = row.get(label_field) if label_field else _column_label(dataset, value_field)
            items.append({"label": _safe_text(label or f"지표 {index}", 120), "value": _safe_text(value, 120)})
        if not items:
            warnings.append(f"{context}: KPI로 표시할 실제 값이 없어 요소를 제외했습니다.")
            return False
        element["items"] = items
        return True
    if element_type in {"bar_chart", "line_chart"}:
        label_field = _first_encoding_field(encoding, "x") or _first_encoding_field(encoding, "category")
        value_field = _first_encoding_field(encoding, "y") or _first_encoding_field(encoding, "value")
        if not label_field or not value_field:
            warnings.append(f"{context}: 차트의 label/value 컬럼이 없어 요소를 표로 변경했습니다.")
            element["element_type"] = "table"
            element["encoding"] = {"columns": [field for field in (label_field, value_field) if field]}
            return _materialize_data_element(element, dataset_id, dataset, warnings, context)
        points: list[dict[str, Any]] = []
        for row in rows[:50]:
            value = _number(row.get(value_field))
            if value is not None:
                points.append({"label": _safe_text(row.get(label_field), 100), "value": value})
        if not points:
            warnings.append(f"{context}: 차트로 표시할 숫자 값이 없어 원본 표로 변경했습니다.")
            element["element_type"] = "table"
            element["encoding"] = {"columns": [label_field, value_field]}
            return _materialize_data_element(element, dataset_id, dataset, warnings, context)
        element["data"] = points
        element["title"] = _safe_text(dataset.get("title"), 180)
        return True
    if element_type == "scatter_plot":
        x_field = _first_encoding_field(encoding, "x")
        y_field = _first_encoding_field(encoding, "y")
        label_field = _first_encoding_field(encoding, "series") or _first_encoding_field(encoding, "category")
        if not x_field or not y_field:
            warnings.append(f"{context}: 산점도 x/y 컬럼이 없어 원본 표로 변경했습니다.")
            element["element_type"] = "table"
            element["encoding"] = {"columns": [field for field in (label_field, x_field, y_field) if field]}
            return _materialize_data_element(element, dataset_id, dataset, warnings, context)
        points: list[dict[str, Any]] = []
        for index, row in enumerate(rows[:50], 1):
            x_value, y_value = _number(row.get(x_field)), _number(row.get(y_field))
            if x_value is not None and y_value is not None:
                points.append(
                    {
                        "label": _safe_text(row.get(label_field) if label_field else f"항목 {index}", 100),
                        "x": x_value,
                        "y": y_value,
                    }
                )
        if not points:
            warnings.append(f"{context}: 산점도로 표시할 실제 숫자 쌍이 없어 원본 표로 변경했습니다.")
            element["element_type"] = "table"
            element["encoding"] = {"columns": [field for field in (label_field, x_field, y_field) if field]}
            return _materialize_data_element(element, dataset_id, dataset, warnings, context)
        element["data"] = points
        element["x_label"] = _column_label(dataset, x_field)
        element["y_label"] = _column_label(dataset, y_field)
        return True
    return True


def _normalize_elements(
    raw_elements: Any,
    slide_no: int,
    views: dict[str, dict[str, Any]],
    datasets: dict[str, dict[str, Any]],
    dataset_columns: dict[str, set[str]],
    reference_ids: set[str],
    references_by_id: dict[str, dict[str, Any]],
    used_ids: set[str],
    warnings: list[str],
) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    if not isinstance(raw_elements, list):
        return elements
    for index, raw in enumerate(raw_elements[:20], 1):
        if not isinstance(raw, dict):
            continue
        if any(str(key).lower() in _RAW_KEYS for key in raw):
            warnings.append(f"슬라이드 {slide_no} 요소 {index}: raw HTML/CSS/JavaScript 필드를 제거했습니다.")
        selected_type = _element_type(raw.get("element_type") or raw.get("type") or raw.get("block_type"))
        if not selected_type:
            warnings.append(f"슬라이드 {slide_no} 요소 {index}: 허용되지 않은 요소 유형을 제거했습니다.")
            continue
        element_id = _safe_identifier(raw.get("element_id") or raw.get("id"), f"s{slide_no:02d}-e{index:02d}")
        base_id = element_id
        suffix = 2
        while element_id in used_ids:
            element_id = f"{base_id}_{suffix}"
            suffix += 1
        used_ids.add(element_id)
        element: dict[str, Any] = {
            "element_id": element_id,
            "element_type": selected_type,
            "content": _normalize_content(raw.get("content") if "content" in raw else raw.get("text"), selected_type),
            "alt_text": _safe_text(raw.get("alt_text"), 500),
        }
        if selected_type in {"title", "subtitle", "speaker_note", "shape"}:
            warnings.append(f"슬라이드 {slide_no} 요소 {index}: Renderer에서 슬라이드 메타로 처리되는 {selected_type} 요소를 제외했습니다.")
            used_ids.discard(element_id)
            continue
        if selected_type in {"process", "timeline"}:
            warnings.append(f"슬라이드 {slide_no} 요소 {index}: Renderer 미지원 {selected_type} 요소를 text로 변경했습니다.")
            selected_type = "text"
            element["element_type"] = "text"
            element["content"] = _safe_text(raw.get("content") or raw.get("text") or raw.get("items"), 4_000)
        if selected_type == "source_note":
            selected_type = "text"
            element["element_type"] = "text"
        if selected_type == "bullet_list":
            element["items"] = deepcopy(element["content"])
        grid = _normalize_grid(raw.get("grid") or raw.get("position"))
        if grid:
            element["grid"] = grid
        if selected_type in _DATA_ELEMENTS:
            view_id = str(raw.get("data_view_id") or "")
            if view_id and view_id in views:
                element["data_view_id"] = view_id
                element["encoding"] = deepcopy(views[view_id]["encoding"])
                dataset_id = str(views[view_id].get("dataset_id") or "")
            else:
                dataset_id = str(raw.get("dataset_id") or "")
                if dataset_id not in datasets:
                    warnings.append(f"슬라이드 {slide_no} 요소 {index}: 유효한 data_view 또는 dataset 참조가 없어 제거했습니다.")
                    continue
                encoding = _normalize_encoding(raw.get("encoding"), dataset_columns[dataset_id], warnings, element_id)
                if encoding is None:
                    continue
                element["dataset_id"] = dataset_id
                element["encoding"] = encoding
            if not _materialize_data_element(element, dataset_id, datasets[dataset_id], warnings, f"슬라이드 {slide_no} 요소 {index}"):
                continue
        if selected_type == "image":
            reference_id = str(raw.get("reference_id") or "")
            if reference_id not in reference_ids:
                warnings.append(f"슬라이드 {slide_no} 이미지 요소: 실제 참고 이미지 ID가 없어 제거했습니다.")
                continue
            element["reference_id"] = reference_id
            reference_item = references_by_id.get(reference_id)
            if not isinstance(reference_item, dict):
                warnings.append(f"슬라이드 {slide_no} 이미지 요소: 참고 이미지 본문을 찾지 못해 제거했습니다.")
                continue
            element["src"] = str(reference_item.get("data_url") or reference_item.get("value") or "")
            element["alt"] = element["alt_text"] or "프레젠테이션 참고 이미지"
        source_note = _safe_text(raw.get("source_note"), 500)
        if source_note:
            element["source_note"] = source_note
        if not element["alt_text"] and selected_type in _DATA_ELEMENTS | {"image"}:
            element["alt_text"] = f"{_safe_text(raw.get('label') or selected_type, 100)} 시각 요소"
        if selected_type not in _DATA_ELEMENTS and selected_type != "image" and not element["content"]:
            warnings.append(f"슬라이드 {slide_no} 요소 {index}: 내용이 비어 있어 제거했습니다.")
            continue
        elements.append(element)
    return elements


def _normalize_design(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    design = deepcopy(_DEFAULT_DESIGN)
    colors_raw = raw.get("colors") if isinstance(raw.get("colors"), dict) else {}
    for key in design["colors"]:
        candidate = str(colors_raw.get(key) or "")
        if not candidate and key == "muted":
            candidate = str(colors_raw.get("muted_text") or "")
        if _HEX_COLOR_RE.fullmatch(candidate):
            design["colors"][key] = candidate.upper()
    margin_raw = raw.get("safe_margin_percent")
    if isinstance(raw.get("canvas"), dict) and isinstance(raw["canvas"].get("safe_margin_percent"), dict):
        margin_raw = raw["canvas"]["safe_margin_percent"]
    if isinstance(margin_raw, dict):
        design["safe_margin_percent"] = {
            side: _clamp_int(margin_raw.get(side), 5, 2, 12) for side in ("top", "right", "bottom", "left")
        }
    motifs = raw.get("motifs") or raw.get("shapes")
    if isinstance(motifs, list):
        clean = [_safe_text(item, 100) for item in motifs[:8] if _safe_text(item, 100)]
        if clean:
            design["motifs"] = clean
    scale = ""
    if isinstance(raw.get("typography"), dict):
        scale = str(raw["typography"].get("heading_scale") or raw["typography"].get("scale") or "")
    if scale in {"compact", "balanced", "strong", "dramatic"}:
        design["typography"]["heading_scale"] = scale
    return design


def _safe_dataset_copy(dataset: dict[str, Any]) -> dict[str, Any]:
    """Renderer에 전달할 데이터에서 실행 가능한 문자열만 제거한다."""

    result = deepcopy(dataset)
    for key in list(result):
        if str(key).lower() in _RAW_KEYS:
            result.pop(key, None)
    rows = result.get("rows")
    if isinstance(rows, list):
        clean_rows: list[dict[str, Any]] = []
        for row in rows[:2_000]:
            if not isinstance(row, dict):
                continue
            clean_rows.append(
                {
                    _safe_text(key, 200): (_safe_text(value, 2_000) if isinstance(value, str) else value)
                    for key, value in list(row.items())[:100]
                    if _safe_text(key, 200)
                }
            )
        result["rows"] = clean_rows
    return result


def normalize_presentation_plan(
    request_value: Any,
    analysis_value: Any,
    plan_value: Any,
    *,
    max_slides: Any = 30,
) -> dict[str, Any]:
    """발표 계획과 데이터 연결을 검증해 Renderer 입력 Data를 만든다."""

    request_payload = _payload(request_value)
    request = _root(request_payload, "request")
    analysis_payload = _payload(analysis_value)
    analysis = _root(analysis_payload, "analysis", "reference_analysis")
    plan_payload = _payload(plan_value)
    plan = _root(plan_payload, "plan_draft", "presentation_plan", "plan")
    warnings: list[str] = []
    errors: list[dict[str, str]] = []
    datasets, dataset_columns = _dataset_catalog(request)
    references_raw = request.get("reference_images") if isinstance(request.get("reference_images"), dict) else {}
    reference_ids = {
        str(item.get("reference_id"))
        for role in ("cover", "body")
        for item in (references_raw.get(role) if isinstance(references_raw.get(role), list) else [])
        if isinstance(item, dict) and item.get("reference_id")
    }
    references_by_id = {
        str(item.get("reference_id")): item
        for role in ("cover", "body")
        for item in (references_raw.get(role) if isinstance(references_raw.get(role), list) else [])
        if isinstance(item, dict) and item.get("reference_id")
    }
    raw_views = plan.get("data_views")
    views, views_by_id = _normalize_data_views(raw_views, datasets, dataset_columns, warnings)
    raw_slides = plan.get("slides")
    if not isinstance(raw_slides, list):
        raw_slides = []
        errors.append({"code": "missing_slides", "message": "계획에 slides 배열이 없습니다."})
    maximum = _clamp_int(max_slides, 30, 3, 50)
    if len(raw_slides) > maximum:
        warnings.append(f"슬라이드는 최대 {maximum}장까지만 유지했습니다.")
    slides: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, raw in enumerate(raw_slides[:maximum], 1):
        if not isinstance(raw, dict):
            continue
        if any(str(key).lower() in _RAW_KEYS for key in raw):
            warnings.append(f"슬라이드 {index}: raw HTML/CSS/JavaScript 필드를 제거했습니다.")
        elements_raw = raw.get("elements") if isinstance(raw.get("elements"), list) else raw.get("blocks")
        elements = _normalize_elements(
            elements_raw,
            index,
            views_by_id,
            datasets,
            dataset_columns,
            reference_ids,
            references_by_id,
            used_ids,
            warnings,
        )
        title = _safe_text(raw.get("title"), 300)
        if not elements and title:
            element_id = f"s{index:02d}-title"
            used_ids.add(element_id)
            elements = [
                {
                    "element_id": element_id,
                    "element_type": "text",
                    "content": _safe_text(raw.get("key_message") or raw.get("subtitle") or title, 2_000),
                    "alt_text": "슬라이드 핵심 내용",
                }
            ]
        if not elements:
            warnings.append(f"슬라이드 {index}: 표시할 요소가 없어 제외했습니다.")
            continue
        slides.append(
            {
                "slide_no": len(slides) + 1,
                "layout": _layout(raw.get("layout"), "cover" if not slides else "title-content"),
                "key_message": _safe_text(raw.get("key_message"), 800),
                "title": title or f"슬라이드 {len(slides) + 1}",
                "subtitle": _safe_text(raw.get("subtitle"), 500),
                "elements": elements,
                "speaker_notes": _safe_text(raw.get("speaker_notes"), 2_000),
            }
        )
    if slides and slides[0]["layout"] != "cover":
        warnings.append("첫 슬라이드 레이아웃을 cover로 보정했습니다.")
        slides[0]["layout"] = "cover"
    if slides and slides[-1]["layout"] != "conclusion":
        warnings.append("마지막 슬라이드 레이아웃을 conclusion으로 보정했습니다.")
        slides[-1]["layout"] = "conclusion"
    if not slides:
        errors.append({"code": "empty_plan", "message": "정규화 후 표시할 슬라이드가 없습니다."})

    brief = request.get("brief") if isinstance(request.get("brief"), dict) else {}
    design_input = plan.get("design_system") if isinstance(plan.get("design_system"), dict) else analysis.get("design_system")
    normalized_plan = {
        "plan_version": "ppt-reference-plan-v1",
        "title": _safe_text(plan.get("title") or brief.get("title") or "제목 미정 프레젠테이션", 300),
        "language": _safe_text(plan.get("language") or brief.get("language") or "ko", 20),
        "audience": _safe_text(plan.get("audience") or brief.get("audience"), 300),
        "purpose": _safe_text(plan.get("purpose") or brief.get("purpose"), 1_000),
        "target_slide_count": _clamp_int(plan.get("target_slide_count") or brief.get("slide_count"), len(slides) or 3, 3, maximum),
        "design_system": _normalize_design(design_input),
        "storyline": [_safe_text(item, 800) for item in plan.get("storyline", [])[:maximum] if _safe_text(item, 800)]
        if isinstance(plan.get("storyline"), list)
        else [slide["key_message"] for slide in slides],
        "data_views": views,
        "slides": slides,
        "normalization_warnings": warnings,
        "warnings": [],
        "errors": errors,
    }
    clean_datasets = [_safe_dataset_copy(item) for item in datasets.values()]
    return {
        "payload_version": "ppt-reference-html-v1",
        "flow_id": "ppt_reference_html_flow",
        "presentation_plan": normalized_plan,
        "datasets": clean_datasets,
        "reference_images": deepcopy(references_raw),
        "warnings": warnings,
        "errors": errors,
        "meta": {
            "status": "invalid" if errors else ("warning" if warnings else "ready"),
            "slide_count": len(slides),
            "data_view_count": len(views),
            "dataset_count": len(clean_datasets),
        },
    }


class PresentationPlanNormalizer(Component):
    """모델 계획을 허용 목록과 실제 데이터 계약에 맞추는 Flow 전용 Node."""

    display_name = "05 발표 계획 검증"
    description = "계획의 레이아웃·요소를 허용 목록으로 제한하고 실제 데이터셋과 컬럼 참조만 Renderer에 전달합니다."
    icon = "ListChecks"
    name = "PresentationPlanNormalizer"

    inputs = [
        DataInput(name="request", display_name="정규화 발표 요청", required=True),
        DataInput(name="analysis", display_name="참고 디자인 분석", required=True),
        DataInput(name="plan_draft", display_name="발표 계획 초안", required=True),
        IntInput(name="max_slides", display_name="최대 슬라이드 수", value=30, advanced=True),
    ]
    outputs = [Output(name="normalized_plan", display_name="검증된 발표 계획", method="build_normalized_plan", types=["Data"])]

    def build_normalized_plan(self) -> Data:
        result = normalize_presentation_plan(
            getattr(self, "request", None),
            getattr(self, "analysis", None),
            getattr(self, "plan_draft", None),
            max_slides=getattr(self, "max_slides", 30),
        )
        self.status = result["meta"]
        return _make_data(result)
