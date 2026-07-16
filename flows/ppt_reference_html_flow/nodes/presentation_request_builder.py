from __future__ import annotations

"""발표 요청과 참고 이미지, 데이터셋을 하나의 안전한 요청 계약으로 정리한다.

이 파일은 Langflow 1.8.2 / LFX 0.3.4에서 한 파일만 붙여 넣어도 동작하도록
작성한 Standalone 내부 Node다. 다른 Flow에서 재사용할 독립 기능이 아니라
`ppt_reference_html_flow`의 입력 계약을 만드는 단계이므로 Component Library에는
등록하지 않는다.
"""

import base64
import csv
import io
import json
import math
import re
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, FileInput, IntInput, MessageTextInput, MultilineInput, Output
from lfx.schema.data import Data


_ALLOWED_IMAGE_MIME = {"image/png", "image/jpeg", "image/gif", "image/webp"}
_MIME_FROM_EXTENSION = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
_MAX_DATASET_FILE_BYTES = 5 * 1024 * 1024
_MAX_DATASETS = 20
_MAX_ROWS = 2_000
_MAX_COLUMNS = 100
_DATA_URL_RE = re.compile(r"^data:(image/[a-z0-9.+-]+);base64,([A-Za-z0-9+/=\s]+)$", re.I)
_TEMPORAL_RE = re.compile(
    r"^(?:\d{4}[-/.]\d{1,2}(?:[-/.]\d{1,2})?|\d{1,2}:\d{2}(?::\d{2})?|\d{8})$"
)


def _make_data(payload: dict[str, Any]) -> Data:
    """LFX 세부 버전에 따른 Data 생성자 차이를 흡수한다."""

    try:
        return Data(data=payload)
    except TypeError:
        return Data(payload)


def _payload(value: Any) -> Any:
    """Data, Message, JSON 문자열을 일반 Python 값으로 바꾼다."""

    if value is None:
        return None
    if isinstance(value, (dict, list, str, int, float, bool)):
        if isinstance(value, str) and value.strip():
            try:
                return json.loads(value)
            except Exception:
                return value
        return deepcopy(value)
    data = getattr(value, "data", None)
    if isinstance(data, (dict, list)):
        return deepcopy(data)
    text = getattr(value, "text", None) or getattr(value, "content", None)
    if isinstance(text, str):
        try:
            return json.loads(text)
        except Exception:
            return text
    return None


def _safe_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(maximum, parsed))


def _read_file_bytes(item: Any) -> tuple[str, bytes]:
    """Langflow FileInput이 내보내는 경로/객체를 파일명과 bytes로 읽는다."""

    if isinstance(item, (str, Path)):
        path = Path(item)
        return path.name, path.read_bytes()
    if isinstance(item, dict):
        name = str(item.get("filename") or item.get("name") or "dataset")
        path_value = item.get("path") or item.get("file_path")
        if path_value:
            return Path(str(path_value)).name, Path(str(path_value)).read_bytes()
        content = item.get("content")
        if isinstance(content, bytes):
            return Path(name).name, content
        if isinstance(content, str):
            return Path(name).name, content.encode("utf-8")
    path_value = getattr(item, "path", None) or getattr(item, "file_path", None)
    if path_value:
        path = Path(str(path_value))
        return path.name, path.read_bytes()
    name = str(getattr(item, "name", None) or "dataset")
    read = getattr(item, "read", None)
    if callable(read):
        content = read()
        if isinstance(content, str):
            content = content.encode("utf-8")
        if isinstance(content, bytes):
            return Path(name).name, content
    raise ValueError("지원하지 않는 데이터 파일 입력 형식입니다.")


def _normalize_brief(
    value: Any,
    content: str,
    slide_count: int,
    user_request: str = "",
) -> tuple[dict[str, Any], list[str]]:
    """간단한 문자열 또는 구조화 입력을 발표 brief로 정규화한다."""

    warnings: list[str] = []
    raw = _payload(value)
    if isinstance(raw, dict) and isinstance(raw.get("brief"), dict):
        raw = raw["brief"]
    if isinstance(raw, str):
        raw = {"title": raw.strip()}
    if not isinstance(raw, dict):
        raw = {}

    supplemental_request = str(raw.get("user_request") or user_request or "").strip()[:10_000]
    request_first_line = next((line.strip() for line in supplemental_request.splitlines() if line.strip()), "")
    title = str(raw.get("title") or request_first_line[:120] or "제목 미정 프레젠테이션").strip()[:200]
    subtitle = str(raw.get("subtitle") or "").strip()[:500]
    purpose = str(raw.get("purpose") or raw.get("objective") or supplemental_request).strip()[:2_000]
    audience = str(raw.get("audience") or "사내 이해관계자").strip()[:300]
    tone = str(raw.get("tone") or "간결하고 근거 중심").strip()[:300]
    language = str(raw.get("language") or "ko").strip()[:20]
    count = _safe_int(raw.get("slide_count"), slide_count, 3, 30)
    call_to_action = str(raw.get("call_to_action") or "").strip()[:1_000]
    outline_value = raw.get("content_outline")
    content_outline = (
        [str(item).strip()[:300] for item in outline_value[:30] if str(item).strip()]
        if isinstance(outline_value, list)
        else []
    )
    body_parts = [str(raw.get("content") or "").strip(), str(content or "").strip()]
    if supplemental_request and supplemental_request not in body_parts:
        body_parts.append(supplemental_request)
    body = "\n\n".join(part for index, part in enumerate(body_parts) if part and part not in body_parts[:index])[:30_000]

    if title == "제목 미정 프레젠테이션":
        warnings.append("발표 제목이 없어 기본 제목을 사용했습니다.")
    if not purpose:
        warnings.append("발표 목적이 비어 있습니다. 계획 생성 품질이 낮아질 수 있습니다.")

    return {
        "title": title,
        "subtitle": subtitle,
        "purpose": purpose,
        "audience": audience,
        "language": language,
        "slide_count": count,
        "tone": tone,
        "content": body,
        "content_outline": content_outline,
        "call_to_action": call_to_action,
        "user_request": supplemental_request,
    }, warnings


def _merge_form_brief(
    value: Any,
    *,
    presentation_title: str = "",
    presentation_subtitle: str = "",
    presentation_purpose: str = "",
    target_audience: str = "",
    presentation_language: str = "",
    presentation_tone: str = "",
    content_outline: str = "",
    call_to_action: str = "",
) -> Any:
    """Builder의 개별 입력 필드를 기존 brief 계약과 합친다."""

    raw = _payload(value)
    if isinstance(raw, dict) and isinstance(raw.get("brief"), dict):
        raw = raw["brief"]
    if isinstance(raw, str):
        raw = {"title": raw.strip()}
    if not isinstance(raw, dict):
        raw = {}
    merged = deepcopy(raw)
    form_values = {
        "title": presentation_title,
        "subtitle": presentation_subtitle,
        "purpose": presentation_purpose,
        "audience": target_audience,
        "language": presentation_language,
        "tone": presentation_tone,
        "call_to_action": call_to_action,
    }
    for key, value_item in form_values.items():
        text = str(value_item or "").strip()
        if text:
            merged[key] = text
    outline_items: list[str] = []
    for line in str(content_outline or "").splitlines():
        item = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip()
        if item and item not in outline_items:
            outline_items.append(item[:300])
    if outline_items:
        merged["content_outline"] = outline_items[:30]
    return merged


def _decode_image_value(value: str, mime_hint: str) -> tuple[str, str, int]:
    """Base64 또는 Data URL을 검증하고 Data URL로 통일한다."""

    text = str(value or "").strip()
    match = _DATA_URL_RE.fullmatch(text)
    if match:
        mime = match.group(1).lower()
        encoded = re.sub(r"\s+", "", match.group(2))
    else:
        mime = mime_hint.lower()
        encoded = re.sub(r"\s+", "", text)
    if mime not in _ALLOWED_IMAGE_MIME:
        raise ValueError(f"허용되지 않은 이미지 형식입니다: {mime or '알 수 없음'}")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("이미지 Base64 값이 올바르지 않습니다.") from exc
    if not raw:
        raise ValueError("빈 이미지입니다.")
    return f"data:{mime};base64,{encoded}", mime, len(raw)


def _image_items(value: Any) -> list[dict[str, Any]]:
    payload = _payload(value)
    if isinstance(payload, dict):
        for key in ("items", "encoded_images", "images"):
            if isinstance(payload.get(key), list):
                return [item for item in payload[key] if isinstance(item, dict)]
        if any(key in payload for key in ("value", "base64", "data_url")):
            return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def normalize_reference_images(
    cover_value: Any,
    body_value: Any,
    *,
    max_images: int = 12,
    max_total_bytes: int = 16 * 1024 * 1024,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, str]], list[str]]:
    """Encoder 결과를 역할별 이미지 참조로 정규화한다.

    오류에는 파일명과 이유만 기록하며 Base64 본문은 절대 포함하지 않는다.
    """

    result = {"cover": [], "body": []}
    errors: list[dict[str, str]] = []
    warnings: list[str] = []
    total_bytes = 0
    position = 0
    for role, value in (("cover", cover_value), ("body", body_value)):
        for item in _image_items(value):
            position += 1
            if position > max_images:
                errors.append({"code": "too_many_images", "message": f"이미지는 최대 {max_images}개까지 허용됩니다."})
                break
            filename = Path(str(item.get("filename") or item.get("name") or f"{role}_{position}")).name[:200]
            raw_value = str(item.get("value") or item.get("data_url") or item.get("base64") or "")
            mime_hint = str(item.get("mime_type") or _MIME_FROM_EXTENSION.get(Path(filename).suffix.lower(), ""))
            try:
                data_url, mime, byte_size = _decode_image_value(raw_value, mime_hint)
            except ValueError as exc:
                errors.append({"code": "invalid_reference_image", "message": f"{filename}: {exc}"})
                continue
            total_bytes += byte_size
            if total_bytes > max_total_bytes:
                errors.append({"code": "reference_images_too_large", "message": "참고 이미지 전체 용량 제한을 초과했습니다."})
                break
            result[role].append(
                {
                    "reference_id": f"{role}_{len(result[role]) + 1:02d}",
                    "role": role,
                    "position": len(result[role]) + 1,
                    "filename": filename,
                    "mime_type": mime,
                    "byte_size": byte_size,
                    "encoding": "data_url",
                    "value": data_url,
                    "data_url": data_url,
                    "sha256": str(item.get("sha256") or "")[:128],
                }
            )
    if not result["cover"]:
        warnings.append("유효한 표지 참고 이미지가 없습니다. 기본 표지 스타일을 사용합니다.")
    if not result["body"]:
        warnings.append("유효한 본문 참고 이미지가 없습니다. 기본 본문 스타일을 사용합니다.")
    return result, errors, warnings


def _non_empty(values: list[Any]) -> list[Any]:
    return [value for value in values if value is not None and str(value).strip() != ""]


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if text.endswith("%"):
            text = text[:-1]
        try:
            return math.isfinite(float(text))
        except Exception:
            return False
    return False


def _semantic_type(name: str, values: list[Any], declared: str = "") -> str:
    declared_key = str(declared or "").strip().lower()
    aliases = {
        "number": "quantitative",
        "numeric": "quantitative",
        "quantitative": "quantitative",
        "date": "temporal",
        "datetime": "temporal",
        "time": "temporal",
        "temporal": "temporal",
        "category": "nominal",
        "categorical": "nominal",
        "dimension": "nominal",
        "nominal": "nominal",
        "text": "text",
        "ordinal": "ordinal",
        "identifier": "identifier",
    }
    if declared_key in aliases:
        return aliases[declared_key]
    sample = _non_empty(values)[:100]
    if not sample:
        return "text"
    if sum(_is_number(value) for value in sample) / len(sample) >= 0.9:
        return "quantitative"
    temporal_count = sum(
        isinstance(value, (date, datetime)) or bool(_TEMPORAL_RE.fullmatch(str(value).strip()))
        for value in sample
    )
    if temporal_count / len(sample) >= 0.8:
        return "temporal"
    unique_ratio = len({str(value) for value in sample}) / len(sample)
    average_length = sum(len(str(value)) for value in sample) / len(sample)
    normalized_name = re.sub(r"[^a-z0-9]", "", str(name).lower())
    if unique_ratio >= 0.9 and any(token in normalized_name for token in ("id", "identifier", "code", "번호", "코드")):
        return "identifier"
    if unique_ratio <= 0.6 and average_length <= 80:
        return "nominal"
    return "text"


def _recommend_visual(columns: list[dict[str, Any]], rows: list[dict[str, Any]], preferred: str) -> list[str]:
    allowed = {"auto", "table", "kpi", "bar", "line", "scatter", "histogram", "stacked_bar"}
    requested = preferred if preferred in allowed else "auto"
    if requested != "auto":
        return [requested, "table"] if requested != "table" else ["table"]
    numeric = [column["name"] for column in columns if column["semantic_type"] == "quantitative"]
    temporal = [column["name"] for column in columns if column["semantic_type"] == "temporal"]
    dimensions = [column["name"] for column in columns if column["semantic_type"] in {"nominal", "ordinal"}]
    if temporal and numeric:
        return ["line", "table"]
    if dimensions and numeric:
        categories = len({str(row.get(dimensions[0])) for row in rows})
        if 1 < categories <= 12:
            return ["bar", "table"]
    if len(numeric) >= 2 and len(rows) >= 8:
        return ["scatter", "table"]
    if len(numeric) == 1 and len(rows) == 1:
        return ["kpi", "table"]
    return ["table"]


def _normalize_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value[:_MAX_ROWS]:
        if isinstance(item, dict):
            rows.append({str(key)[:200]: item[key] for key in list(item)[:_MAX_COLUMNS]})
    return rows


def _dataset_from_rows(raw: dict[str, Any], index: int, warnings: list[str]) -> dict[str, Any] | None:
    rows = _normalize_rows(raw.get("rows") or raw.get("data"))
    declared_columns = raw.get("columns") if isinstance(raw.get("columns"), list) else []
    column_names: list[str] = []
    declared_by_name: dict[str, dict[str, Any]] = {}
    for item in declared_columns[:_MAX_COLUMNS]:
        if isinstance(item, str):
            name = item.strip()
            declared = {"name": name}
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            declared = item
        else:
            continue
        if name and name not in column_names:
            column_names.append(name)
            declared_by_name[name] = declared
    for row in rows:
        for key in row:
            if key not in column_names and len(column_names) < _MAX_COLUMNS:
                column_names.append(key)
    if not column_names:
        warnings.append(f"{index}번째 데이터셋에 컬럼이 없어 제외했습니다.")
        return None
    columns: list[dict[str, Any]] = []
    for name in column_names:
        declared = declared_by_name.get(name, {})
        values = [row.get(name) for row in rows]
        columns.append(
            {
                "name": name,
                "label": str(declared.get("label") or name)[:200],
                "semantic_type": _semantic_type(name, values, declared.get("semantic_type") or declared.get("type")),
                "unit": str(declared.get("unit") or "")[:100],
                "format": str(declared.get("format") or "")[:100],
                "description": str(declared.get("description") or "")[:500],
                "non_empty_count": len(_non_empty(values)),
                "distinct_count": len({str(value) for value in _non_empty(values)}),
            }
        )
    dataset_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(raw.get("dataset_id") or raw.get("id") or f"dataset_{index}"))[:80]
    if not dataset_id:
        dataset_id = f"dataset_{index}"
    preferred = str(raw.get("preferred_visual") or "auto").strip().lower()
    allowed_visuals = {"auto", "table", "kpi", "bar", "line", "scatter", "histogram", "stacked_bar"}
    if preferred not in allowed_visuals:
        warnings.append(
            f"{dataset_id}: 지원하지 않는 preferred_visual '{preferred}'을 auto로 변경했습니다."
        )
        preferred = "auto"
    return {
        "dataset_id": dataset_id,
        "title": str(raw.get("title") or dataset_id).strip()[:300],
        "source": str(raw.get("source") or "").strip()[:500],
        "period": str(raw.get("period") or "").strip()[:200],
        "description": str(raw.get("description") or "").strip()[:1_000],
        "preferred_visual": preferred,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": isinstance(raw.get("rows") or raw.get("data"), list) and len(raw.get("rows") or raw.get("data")) > _MAX_ROWS,
        "recommended_visuals": _recommend_visual(columns, rows, preferred),
    }


def _dataset_candidates(value: Any) -> list[dict[str, Any]]:
    parsed = _payload(value)
    if isinstance(parsed, dict):
        if isinstance(parsed.get("datasets"), list):
            return [item for item in parsed["datasets"] if isinstance(item, dict)]
        if isinstance(parsed.get("rows"), list) or isinstance(parsed.get("data"), list):
            return [parsed]
        if parsed and all(not isinstance(item, (dict, list)) for item in parsed.values()):
            return [{"rows": [parsed]}]
    if isinstance(parsed, list):
        if parsed and all(isinstance(item, dict) and "rows" not in item and "data" not in item for item in parsed):
            return [{"rows": parsed}]
        return [item for item in parsed if isinstance(item, dict)]
    return []


def _datasets_from_files(files: Any, errors: list[dict[str, str]]) -> list[dict[str, Any]]:
    items = files if isinstance(files, (list, tuple)) else ([] if not files else [files])
    candidates: list[dict[str, Any]] = []
    for item in items:
        try:
            filename, content = _read_file_bytes(item)
            if len(content) > _MAX_DATASET_FILE_BYTES:
                raise ValueError("파일 크기가 5MB를 초과합니다.")
            extension = Path(filename).suffix.lower()
            text = content.decode("utf-8-sig")
            if extension == ".csv":
                rows = list(csv.DictReader(io.StringIO(text)))
                candidates.append({"dataset_id": Path(filename).stem, "title": Path(filename).stem, "rows": rows})
            elif extension == ".json":
                parsed = json.loads(text)
                candidates.extend(_dataset_candidates(parsed))
            else:
                raise ValueError("CSV 또는 JSON 파일만 사용할 수 있습니다.")
        except Exception as exc:
            safe_name = Path(str(getattr(item, "name", None) or item or "dataset")).name[:200]
            errors.append({"code": "invalid_dataset_file", "message": f"{safe_name}: {exc}"})
    return candidates


def normalize_datasets(value: Any, files: Any = None) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[str]]:
    """JSON/CSV 데이터셋을 행·컬럼 계약과 시각화 권고로 정리한다."""

    errors: list[dict[str, str]] = []
    warnings: list[str] = []
    candidates = _dataset_candidates(value) + _datasets_from_files(files, errors)
    if len(candidates) > _MAX_DATASETS:
        warnings.append(f"데이터셋은 최대 {_MAX_DATASETS}개까지만 처리했습니다.")
    datasets: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(candidates[:_MAX_DATASETS], 1):
        dataset = _dataset_from_rows(raw, index, warnings)
        if dataset is None:
            continue
        base_id = dataset["dataset_id"]
        suffix = 2
        while dataset["dataset_id"] in seen_ids:
            dataset["dataset_id"] = f"{base_id}_{suffix}"
            suffix += 1
        seen_ids.add(dataset["dataset_id"])
        datasets.append(dataset)
    return datasets, errors, warnings


def build_presentation_request(
    presentation_request: Any,
    cover_images: Any,
    body_images: Any,
    *,
    brief: str = "",
    content: str = "",
    datasets_json: str = "",
    dataset_files: Any = None,
    target_slide_count: Any = 10,
    user_request: str = "",
    presentation_title: str = "",
    presentation_subtitle: str = "",
    presentation_purpose: str = "",
    target_audience: str = "",
    presentation_language: str = "",
    presentation_tone: str = "",
    content_outline: str = "",
    call_to_action: str = "",
) -> dict[str, Any]:
    """모든 입력을 후속 Node가 공유하는 `presentation_request`로 합친다."""

    source = _payload(presentation_request)
    source_dict = source if isinstance(source, dict) else {}
    structured_brief = source_dict.get("brief") if isinstance(source_dict.get("brief"), dict) else None
    brief_source: Any = structured_brief or _merge_form_brief(
        brief or source,
        presentation_title=presentation_title,
        presentation_subtitle=presentation_subtitle,
        presentation_purpose=presentation_purpose,
        target_audience=target_audience,
        presentation_language=presentation_language,
        presentation_tone=presentation_tone,
        content_outline=content_outline,
        call_to_action=call_to_action,
    )
    count = _safe_int(target_slide_count, 10, 3, 30)
    normalized_brief, brief_warnings = _normalize_brief(
        brief_source,
        content or str(source_dict.get("content") or ""),
        count,
        user_request or str(source_dict.get("user_request") or ""),
    )
    dataset_source = source_dict.get("datasets") if "datasets" in source_dict else datasets_json
    datasets, dataset_errors, dataset_warnings = normalize_datasets(dataset_source, dataset_files)
    references, image_errors, image_warnings = normalize_reference_images(cover_images, body_images)
    errors = image_errors + dataset_errors
    warnings = brief_warnings + dataset_warnings + image_warnings
    request_contract = {
        "brief": normalized_brief,
        "user_request": normalized_brief.get("user_request", ""),
        "template_mode": str(source_dict.get("template_mode") or "analyze_and_rebuild"),
        "datasets": datasets,
        "reference_images": references,
    }
    result = {
        "payload_version": "ppt-reference-html-v1",
        "flow_id": "ppt_reference_html_flow",
        "schema_version": "1.0",
        "status": "error" if errors else ("warning" if warnings else "ok"),
        "request": request_contract,
        "brief": deepcopy(normalized_brief),
        "user_request": normalized_brief.get("user_request", ""),
        "template_mode": request_contract["template_mode"],
        "datasets": deepcopy(datasets),
        "reference_images": deepcopy(references),
        "recommendations": [
            {"dataset_id": item["dataset_id"], "visuals": item["recommended_visuals"]}
            for item in datasets
        ],
        "errors": errors,
        "warnings": warnings,
        "meta": {
            "dataset_count": len(datasets),
            "cover_image_count": len(references["cover"]),
            "body_image_count": len(references["body"]),
            "status": "invalid" if errors else ("warning" if warnings else "ready"),
        },
    }
    return result


class PresentationRequestBuilder(Component):
    """발표 입력을 이미지·데이터·brief가 분리된 단일 Data 계약으로 만든다."""

    display_name = "02 발표 요청 정리"
    description = "제목·목적·대상·목차·본문 양식, JSON/CSV 데이터와 표지·본문 이미지 결과를 하나의 요청 Data로 정리합니다."
    icon = "ClipboardList"
    name = "PresentationRequestBuilder"

    inputs = [
        DataInput(
            name="presentation_request",
            display_name="구조화 발표 요청",
            required=False,
            advanced=True,
            info="brief와 datasets가 들어 있는 Data입니다. 비워 두고 아래 입력만 사용해도 됩니다.",
        ),
        MessageTextInput(
            name="user_request",
            display_name="사용자 발표 요청",
            required=False,
            value="",
            info="Chat Input 메시지를 연결합니다. 구조화 brief가 있으면 누락된 목적과 본문을 보완하는 용도로만 사용합니다.",
        ),
        MessageTextInput(name="presentation_title", display_name="발표 제목", required=False, value=""),
        MessageTextInput(name="presentation_subtitle", display_name="발표 부제", required=False, value=""),
        MultilineInput(name="presentation_purpose", display_name="발표 목적", required=False, value=""),
        MessageTextInput(name="target_audience", display_name="대상 청중", required=False, value=""),
        MessageTextInput(
            name="presentation_language",
            display_name="발표 언어",
            required=False,
            value="ko",
            advanced=True,
            info="언어 코드 또는 언어 이름을 입력합니다. 기본값은 ko입니다.",
        ),
        MessageTextInput(name="presentation_tone", display_name="발표 톤", required=False, value=""),
        MultilineInput(
            name="content_outline",
            display_name="슬라이드 목차",
            required=False,
            value="",
            info="한 줄에 한 항목씩 입력합니다. 번호나 글머리표는 자동으로 제거합니다.",
        ),
        MultilineInput(name="call_to_action", display_name="마지막 요청·의사결정", required=False, value=""),
        MultilineInput(name="content", display_name="발표 본문", required=False, value=""),
        MultilineInput(
            name="brief",
            display_name="기존 Brief 문자열·JSON",
            required=False,
            value="",
            advanced=True,
            info="기존 Flow 호환 입력입니다. 위 개별 양식을 사용하면 비워 둘 수 있습니다.",
        ),
        MultilineInput(
            name="datasets_json",
            display_name="데이터셋 JSON",
            required=False,
            value="",
            info="datasets 배열 또는 행 목록을 JSON으로 입력합니다.",
        ),
        FileInput(
            name="dataset_files",
            display_name="CSV/JSON 데이터 파일",
            file_types=["csv", "json"],
            required=False,
            is_list=True,
            value=[],
            temp_file=True,
        ),
        DataInput(name="cover_images", display_name="표지 이미지 Encoder 결과", required=False),
        DataInput(name="body_images", display_name="본문 이미지 Encoder 결과", required=False),
        IntInput(name="target_slide_count", display_name="목표 슬라이드 수", value=10),
    ]
    outputs = [Output(name="request", display_name="정규화 발표 요청", method="build_request", types=["Data"])]

    def build_request(self) -> Data:
        result = build_presentation_request(
            getattr(self, "presentation_request", None),
            getattr(self, "cover_images", None),
            getattr(self, "body_images", None),
            brief=getattr(self, "brief", ""),
            content=getattr(self, "content", ""),
            datasets_json=getattr(self, "datasets_json", ""),
            dataset_files=getattr(self, "dataset_files", None),
            target_slide_count=getattr(self, "target_slide_count", 10),
            user_request=getattr(self, "user_request", ""),
            presentation_title=getattr(self, "presentation_title", ""),
            presentation_subtitle=getattr(self, "presentation_subtitle", ""),
            presentation_purpose=getattr(self, "presentation_purpose", ""),
            target_audience=getattr(self, "target_audience", ""),
            presentation_language=getattr(self, "presentation_language", "ko"),
            presentation_tone=getattr(self, "presentation_tone", ""),
            content_outline=getattr(self, "content_outline", ""),
            call_to_action=getattr(self, "call_to_action", ""),
        )
        self.status = result["meta"]
        return _make_data(result)
