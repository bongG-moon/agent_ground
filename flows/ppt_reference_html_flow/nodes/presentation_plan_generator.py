from __future__ import annotations

"""발표 요청과 디자인 분석을 슬라이드 계획 JSON 초안으로 변환한다.

Language Model이 실패하거나 잘못된 JSON을 반환해도 원본 데이터에만 근거한
결정론적 초안을 제공한다. 이 단계에서는 HTML/CSS/JavaScript를 생성하지 않는다.
"""

import json
import re
from copy import deepcopy
from typing import Any

from langchain_core.messages import HumanMessage
from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, HandleInput, Output
from lfx.schema.data import Data


_VISUAL_TO_LAYOUT_ELEMENT = {
    "kpi": ("kpi-grid", "kpi"),
    "bar": ("chart-focus", "bar_chart"),
    "line": ("chart-focus", "line_chart"),
    "donut": ("chart-focus", "bar_chart"),
    "scatter": ("chart-focus", "scatter_plot"),
    "histogram": ("chart-focus", "histogram"),
    "stacked_bar": ("chart-focus", "stacked_bar"),
    "table": ("table-focus", "table"),
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


def _request_root(value: Any) -> dict[str, Any]:
    payload = _payload(value)
    request = payload.get("request")
    return deepcopy(request) if isinstance(request, dict) else payload


def _analysis_root(value: Any) -> dict[str, Any]:
    payload = _payload(value)
    for key in ("analysis", "reference_analysis"):
        if isinstance(payload.get(key), dict):
            return deepcopy(payload[key])
    return payload


def _extract_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return str(content or "")


def parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return deepcopy(value)
    text = _extract_text(value).strip()
    if not text:
        raise ValueError("계획 모델 응답이 비어 있습니다.")
    decoder = json.JSONDecoder()
    for candidate in (re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I), text):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        for index, char in enumerate(candidate):
            if char != "{":
                continue
            try:
                parsed, _ = decoder.raw_decode(candidate[index:])
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
    raise ValueError("계획 모델 응답에서 JSON object를 찾지 못했습니다.")


def _safe_text(value: Any, maximum: int = 1_000) -> str:
    text = str(value or "").replace("\x00", " ")
    text = re.sub(r"<script\b[^>]*>[\s\S]*?</script\s*>", "", text, flags=re.I)
    text = re.sub(r"<style\b[^>]*>[\s\S]*?</style\s*>", "", text, flags=re.I)
    text = re.sub(r"[\r\t]+", " ", text)
    return text.strip()[:maximum]


def _brief(request: dict[str, Any]) -> dict[str, Any]:
    brief = request.get("brief")
    return deepcopy(brief) if isinstance(brief, dict) else {}


def _datasets(request: dict[str, Any]) -> list[dict[str, Any]]:
    datasets = request.get("datasets")
    return [deepcopy(item) for item in datasets if isinstance(item, dict)] if isinstance(datasets, list) else []


def _column_names(dataset: dict[str, Any], semantic: str = "") -> list[str]:
    result: list[str] = []
    columns = dataset.get("columns")
    if isinstance(columns, list):
        for item in columns:
            if isinstance(item, str):
                name = item
                item_type = ""
            elif isinstance(item, dict):
                name = str(item.get("name") or "")
                item_type = str(item.get("semantic_type") or item.get("type") or "")
            else:
                continue
            if name and (not semantic or item_type == semantic):
                result.append(name)
    return result


def _data_view(dataset: dict[str, Any], index: int) -> tuple[dict[str, Any], str, str]:
    dataset_id = str(dataset.get("dataset_id") or dataset.get("id") or f"dataset_{index}")
    recommendations = dataset.get("recommended_visuals")
    preferred = str(dataset.get("preferred_visual") or "").lower()
    if isinstance(recommendations, list) and recommendations:
        visual = str(recommendations[0]).lower()
    else:
        visual = preferred if preferred and preferred != "auto" else "table"
    layout, element_type = _VISUAL_TO_LAYOUT_ELEMENT.get(visual, ("table-focus", "table"))
    numeric = _column_names(dataset, "quantitative")
    temporal = _column_names(dataset, "temporal")
    nominal = _column_names(dataset, "nominal")
    all_columns = _column_names(dataset)
    x = (temporal or nominal or all_columns or [""])[0]
    y = numeric[:2]
    if element_type == "table":
        encoding = {"columns": all_columns[:8]}
    elif element_type == "kpi":
        encoding = {"value": (numeric or all_columns or [""])[0]}
    else:
        if element_type == "scatter_plot" and len(numeric) >= 2:
            x, y = numeric[0], [numeric[1]]
        encoding = {"x": x, "y": y or all_columns[1:2]}
    return (
        {
            "data_view_id": f"view_{dataset_id}",
            "dataset_id": dataset_id,
            "visual": visual,
            "encoding": encoding,
        },
        layout,
        element_type,
    )


def _content_points(content: str) -> list[str]:
    points: list[str] = []
    for part in re.split(r"(?:\r?\n)+|(?<=[.!?。])\s+", str(content or "")):
        text = re.sub(r"^[\-•*\d.)\s]+", "", part).strip()
        if text and text not in points:
            points.append(text[:240])
    return points[:40]


def build_deterministic_plan(request_value: Any, analysis_value: Any) -> dict[str, Any]:
    """모델 없이도 데이터 계약을 지키는 보수적인 발표 계획을 만든다."""

    request = _request_root(request_value)
    analysis = _analysis_root(analysis_value)
    brief = _brief(request)
    datasets = _datasets(request)
    try:
        target = max(3, min(30, int(brief.get("slide_count") or 10)))
    except Exception:
        target = 10
    title = _safe_text(brief.get("title") or "제목 미정 프레젠테이션", 200)
    purpose = _safe_text(brief.get("purpose"), 500)
    audience = _safe_text(brief.get("audience") or "사내 이해관계자", 200)
    outline = brief.get("content_outline") if isinstance(brief.get("content_outline"), list) else []
    content_points = _content_points(
        "\n".join(
            [
                str(brief.get("content") or request.get("content") or ""),
                *[str(item) for item in outline],
            ]
        )
    )
    design = analysis.get("design_system") if isinstance(analysis.get("design_system"), dict) else {}
    data_views: list[dict[str, Any]] = []
    slide_candidates: list[dict[str, Any]] = []

    slide_candidates.append(
        {
            "layout": "cover",
            "key_message": purpose,
            "title": title,
            "subtitle": purpose,
            "elements": [
                {"element_type": "title", "content": title, "alt_text": "발표 제목"},
                {"element_type": "subtitle", "content": purpose, "alt_text": "발표 목적"},
            ],
            "speaker_notes": "사용자가 제공한 발표 목적과 범위를 소개합니다.",
        }
    )

    for index, dataset in enumerate(datasets, 1):
        view, layout, element_type = _data_view(dataset, index)
        data_views.append(view)
        dataset_title = _safe_text(dataset.get("title") or view["dataset_id"], 200)
        source = _safe_text(dataset.get("source"), 300)
        slide_candidates.append(
            {
                "layout": layout,
                "key_message": f"{dataset_title}의 원본 데이터를 확인합니다.",
                "title": dataset_title,
                "subtitle": _safe_text(dataset.get("period"), 120),
                "elements": [
                    {
                        "element_type": element_type,
                        "content": "",
                        "data_view_id": view["data_view_id"],
                        "encoding": deepcopy(view["encoding"]),
                        "alt_text": f"{dataset_title} 데이터 시각화",
                        "source_note": f"출처: {source}" if source else "",
                    }
                ],
                "speaker_notes": "표시된 값은 입력 데이터셋을 그대로 사용하며 제공되지 않은 수치는 추정하지 않습니다.",
            }
        )

    for offset in range(0, len(content_points), 5):
        group = content_points[offset : offset + 5]
        slide_candidates.append(
            {
                "layout": "title-content",
                "key_message": group[0] if group else "",
                "title": f"핵심 내용 {offset // 5 + 1}",
                "subtitle": "",
                "elements": [
                    {"element_type": "bullet_list", "content": group, "alt_text": "핵심 내용 목록"}
                ],
                "speaker_notes": "사용자가 제공한 본문 내용을 요약하지 않고 항목별로 배치했습니다.",
            }
        )

    conclusion_text = _safe_text(brief.get("call_to_action"), 500) or purpose or "발표 내용을 검토하고 다음 조치를 결정합니다."
    conclusion = {
        "layout": "conclusion",
        "key_message": conclusion_text,
        "title": "다음 단계",
        "subtitle": "",
        "elements": [{"element_type": "text", "content": conclusion_text, "alt_text": "발표 마무리"}],
        "speaker_notes": "사용자가 제공한 목적을 기준으로 논의를 마무리합니다.",
    }
    body_limit = target - 2
    body = slide_candidates[1 : 1 + body_limit]
    while len(body) < body_limit:
        number = len(body) + 1
        body.append(
            {
                "layout": "section" if number == 1 else "title-content",
                "key_message": purpose,
                "title": "발표 개요" if number == 1 else f"논의 항목 {number}",
                "subtitle": "",
                "elements": [
                    {
                        "element_type": "text",
                        "content": purpose or "추가 내용은 발표자가 입력한 근거를 바탕으로 보완합니다.",
                        "alt_text": "발표 개요",
                    }
                ],
                "speaker_notes": "근거가 없는 수치나 사실을 추가하지 않습니다.",
            }
        )
    slides = [slide_candidates[0], *body, conclusion]
    for slide_no, slide in enumerate(slides, 1):
        slide["slide_no"] = slide_no
        for element_index, element in enumerate(slide.get("elements", []), 1):
            element["element_id"] = f"s{slide_no:02d}-e{element_index:02d}"
    return {
        "plan_version": "ppt-reference-plan-v1",
        "title": title,
        "language": _safe_text(brief.get("language") or "ko", 20),
        "audience": audience,
        "purpose": purpose,
        "target_slide_count": target,
        "design_system": deepcopy(design),
        "storyline": [slide.get("key_message", "") for slide in slides],
        "data_views": data_views,
        "slides": slides,
        "normalization_warnings": [],
        "warnings": [],
        "errors": [],
    }


def _compact_request(request_value: Any) -> dict[str, Any]:
    """Base64와 과도한 행을 제거한 모델 입력용 요청 사본을 만든다."""

    request = _request_root(request_value)
    brief = _brief(request)
    compact_datasets: list[dict[str, Any]] = []
    for dataset in _datasets(request)[:20]:
        compact_datasets.append(
            {
                "dataset_id": dataset.get("dataset_id") or dataset.get("id"),
                "title": dataset.get("title"),
                "source": dataset.get("source"),
                "period": dataset.get("period"),
                "columns": dataset.get("columns", [])[:100] if isinstance(dataset.get("columns"), list) else [],
                "row_count": dataset.get("row_count") if dataset.get("row_count") is not None else len(dataset.get("rows", [])),
                "recommended_visuals": dataset.get("recommended_visuals", []),
                "preview_rows": dataset.get("rows", [])[:5] if isinstance(dataset.get("rows"), list) else [],
            }
        )
    return {"brief": brief, "template_mode": request.get("template_mode"), "datasets": compact_datasets}


def build_plan_message(request_value: Any, analysis_value: Any) -> HumanMessage:
    compact_request = _compact_request(request_value)
    analysis = _analysis_root(analysis_value)
    compact_analysis = {
        "design_system": analysis.get("design_system", {}),
        "observations": analysis.get("observations", []),
        "confidence": analysis.get("confidence", 0),
        "warnings": analysis.get("warnings", []),
    }
    instruction = (
        "당신은 기업용 프레젠테이션 정보 설계자입니다. 아래 요청의 brief와 실제 datasets만 사실 근거로 사용하십시오. "
        "참고 디자인 분석은 시각 스타일 참고일 뿐 사실 근거가 아닙니다. 제공되지 않은 수치, 증감률, 순위, 원인, 출처를 "
        "만들지 마십시오. HTML, CSS, JavaScript를 작성하거나 raw_html/raw_css/raw_js 필드를 만들지 마십시오. "
        "반드시 JSON object 하나만 반환하십시오. 최상위 key는 presentation_plan이며, plan에는 title, language, audience, "
        "purpose, target_slide_count, design_system, storyline, data_views, slides가 있어야 합니다. "
        "slide layout은 cover, section, title-content, two-column, kpi-grid, chart-focus, table-focus, comparison, timeline, conclusion 중 하나입니다. "
        "element_type은 title, subtitle, text, bullet_list, kpi, image, shape, table, bar_chart, line_chart, stacked_bar, "
        "scatter_plot, histogram, process, timeline, source_note, speaker_note 중 하나입니다. "
        "현재 실제 차트 Renderer는 kpi, table, bar_chart, line_chart, scatter_plot을 지원합니다. stacked_bar와 histogram은 "
        "임의 계산이 필요하므로 사용자가 확정 집계값과 계산 근거를 제공하지 않았다면 table을 선택하십시오. "
        "차트와 표는 dataset_id와 실제 column 이름으로 data_view를 만들고 임의의 값 배열을 쓰지 마십시오. "
        "슬라이드마다 title, key_message, elements, speaker_notes를 포함하고 목표 슬라이드 수를 지키십시오.\n\n"
        f"REQUEST_JSON:\n{json.dumps(compact_request, ensure_ascii=False, default=str)}\n\n"
        f"DESIGN_ANALYSIS_JSON:\n{json.dumps(compact_analysis, ensure_ascii=False, default=str)}"
    )
    return HumanMessage(content=instruction)


async def generate_presentation_plan(request_value: Any, analysis_value: Any, model: Any) -> dict[str, Any]:
    fallback = build_deterministic_plan(request_value, analysis_value)
    warnings: list[str] = []
    errors: list[dict[str, str]] = []
    source = "deterministic_fallback"
    draft = fallback
    if model is None:
        warnings.append("Language Model이 연결되지 않아 결정론적 기본 계획을 사용했습니다.")
    else:
        try:
            message = build_plan_message(request_value, analysis_value)
            if callable(getattr(model, "ainvoke", None)):
                response = await model.ainvoke([message])
            elif callable(getattr(model, "invoke", None)):
                response = model.invoke([message])
            else:
                raise TypeError("연결된 객체가 invoke 또는 ainvoke를 지원하지 않습니다.")
            parsed = parse_json_object(response)
            candidate = parsed.get("presentation_plan") or parsed.get("plan") or parsed
            if not isinstance(candidate, dict) or not isinstance(candidate.get("slides"), list) or not candidate["slides"]:
                raise ValueError("계획 JSON에 비어 있지 않은 slides 배열이 없습니다.")
            draft = candidate
            source = "language_model"
        except Exception as exc:
            errors.append({"code": "plan_generation_failed", "message": _safe_text(exc, 500)})
            warnings.append("모델 계획을 사용할 수 없어 결정론적 기본 계획으로 대체했습니다.")
    return {
        "payload_version": "ppt-reference-html-v1",
        "plan_draft": draft,
        "presentation_plan": draft,
        "warnings": warnings,
        "errors": errors,
        "meta": {"status": "ready" if source == "language_model" else "fallback", "source": source, "slide_count": len(draft.get("slides", []))},
    }


class PresentationPlanGenerator(Component):
    """디자인 분석과 실제 데이터를 슬라이드 계획 JSON으로 바꾸는 Flow 전용 Node."""

    display_name = "04 발표 계획 생성"
    description = "참고 디자인과 실제 데이터에 근거한 슬라이드 계획 JSON을 생성하고 실패 시 안전한 기본 계획으로 대체합니다."
    icon = "PanelsTopLeft"
    name = "PresentationPlanGenerator"

    inputs = [
        DataInput(name="request", display_name="정규화 발표 요청", required=True),
        DataInput(name="analysis", display_name="참고 디자인 분석", required=True),
        HandleInput(
            name="model",
            display_name="계획 Language Model",
            input_types=["LanguageModel"],
            required=False,
            info="구조화 JSON을 생성할 수 있는 모델입니다. 연결하지 않으면 결정론적 기본 계획을 사용합니다.",
        ),
    ]
    outputs = [Output(name="plan_draft", display_name="발표 계획 초안", method="build_plan", types=["Data"])]

    async def build_plan(self) -> Data:
        result = await generate_presentation_plan(
            getattr(self, "request", None),
            getattr(self, "analysis", None),
            getattr(self, "model", None),
        )
        self.status = result["meta"]
        return _make_data(result)
