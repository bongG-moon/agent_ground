from __future__ import annotations

"""발표 계획과 생성 HTML의 구조·접근성·보안·밀도 위험을 검사한다."""

import json
import re
from copy import deepcopy
from html.parser import HTMLParser
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import BoolInput, DataInput, IntInput, Output
from lfx.schema.data import Data


_EXTERNAL_URL_RE = re.compile(r"(?:https?:)?//|\b(?:javascript|vbscript|file):", re.I)
_CSS_EXTERNAL_RE = re.compile(r"(?:@import|url\s*\(\s*['\"]?(?:https?:)?//)", re.I)
_DYNAMIC_NETWORK_RE = re.compile(r"\b(?:fetch|XMLHttpRequest|WebSocket|EventSource)\s*\(", re.I)
_PROHIBITED_TAGS = {"iframe", "object", "embed", "form", "base"}


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


def _plan(value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = _payload(value)
    plan = payload.get("presentation_plan")
    if not isinstance(plan, dict):
        plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else payload
    return deepcopy(plan), payload


def _html(value: Any) -> tuple[str, bool]:
    payload = _payload(value)
    for key in ("presentation_artifact", "html_presentation", "html_report", "presentation", "artifact"):
        container = payload.get(key)
        if isinstance(container, dict) and isinstance(container.get("html"), str):
            return container["html"], True
    if isinstance(payload.get("html"), str):
        return payload["html"], True
    return "", bool(payload)


class _PresentationHTMLInspector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: dict[str, int] = {}
        self.prohibited_tags: list[str] = []
        self.external_urls: list[str] = []
        self.event_attributes: list[str] = []
        self.images = 0
        self.images_without_alt = 0
        self.tables = 0
        self.tables_without_header = 0
        self.slide_sections = 0
        self.html_lang = ""
        self.has_title = False
        self.has_main = False
        self.external_script_or_link = False
        self._table_has_th: list[bool] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        self.tags[tag] = self.tags.get(tag, 0) + 1
        attributes = {str(key).lower(): str(value or "") for key, value in attrs}
        if tag in _PROHIBITED_TAGS:
            self.prohibited_tags.append(tag)
        if tag == "html":
            self.html_lang = attributes.get("lang", "").strip()
        if tag == "title":
            self.has_title = True
        if tag == "main":
            self.has_main = True
        if tag == "section":
            classes = set(attributes.get("class", "").split())
            if "slide" in classes or "presentation-slide" in classes or "data-slide" in attributes:
                self.slide_sections += 1
        if tag == "img":
            self.images += 1
            if not attributes.get("alt", "").strip():
                self.images_without_alt += 1
        if tag == "table":
            self.tables += 1
            self._table_has_th.append(False)
        if tag == "th" and self._table_has_th:
            self._table_has_th[-1] = True
        for key, value in attributes.items():
            if key.startswith("on"):
                self.event_attributes.append(key)
            if key in {"href", "src", "action", "poster", "data"} and value:
                if _EXTERNAL_URL_RE.search(value):
                    self.external_urls.append(value[:200])
            if tag == "script" and key == "src" and value:
                self.external_script_or_link = True
            if tag == "link" and key == "href" and value:
                self.external_script_or_link = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "table" and self._table_has_th:
            if not self._table_has_th.pop():
                self.tables_without_header += 1


def _check(checks: list[dict[str, Any]], check_id: str, status: str, message: str, slide_no: int = 0) -> None:
    checks.append({"check_id": check_id, "status": status, "message": message, "slide_no": slide_no})


def _content_length(value: Any) -> int:
    if isinstance(value, list):
        return sum(len(str(item)) for item in value)
    return len(str(value or ""))


def evaluate_presentation_quality(
    presentation_plan_value: Any,
    presentation_artifact_value: Any = None,
    *,
    require_html: Any = False,
    max_html_size_kb: Any = 8_000,
) -> dict[str, Any]:
    """계획과 선택적 HTML을 검사하고 HTML 원문을 포함하지 않는 보고서를 반환한다."""

    plan, plan_payload = _plan(presentation_plan_value)
    html_source, artifact_supplied = _html(presentation_artifact_value)
    checks: list[dict[str, Any]] = []
    blocking: list[str] = []
    warnings: list[str] = []
    slides = plan.get("slides") if isinstance(plan.get("slides"), list) else []
    target = plan.get("target_slide_count")
    try:
        target_count = int(target)
    except Exception:
        target_count = len(slides)
    if not slides:
        message = "발표 계획에 표시할 슬라이드가 없습니다."
        _check(checks, "plan-slide-count", "fail", message)
        blocking.append(message)
    elif len(slides) != target_count:
        message = f"계획 슬라이드 {len(slides)}장과 목표 {target_count}장이 다릅니다."
        _check(checks, "plan-slide-count", "warning", message)
        warnings.append(message)
    else:
        _check(checks, "plan-slide-count", "pass", f"계획 슬라이드 수가 목표 {target_count}장과 일치합니다.")

    overflow_count = 0
    for index, slide in enumerate(slides, 1):
        if not isinstance(slide, dict):
            continue
        risks: list[str] = []
        if len(str(slide.get("title") or "")) > 80:
            risks.append("제목 80자 초과")
        if len(str(slide.get("subtitle") or "")) > 180:
            risks.append("부제 180자 초과")
        elements = slide.get("elements") if isinstance(slide.get("elements"), list) else []
        if len(elements) > 8:
            risks.append("요소 8개 초과")
        for element in elements:
            if not isinstance(element, dict):
                continue
            element_type = str(element.get("element_type") or "")
            content = element.get("content")
            if element_type in {"text", "speaker_note"} and _content_length(content) > 500:
                risks.append("본문 500자 초과")
            if element_type == "bullet_list" and isinstance(content, list) and len(content) > 8:
                risks.append("글머리 8개 초과")
            rows = element.get("rows")
            if element_type == "table" and isinstance(rows, list) and len(rows) > 12:
                risks.append("표 12행 초과")
        if risks:
            overflow_count += 1
            message = f"슬라이드 {index} 오버플로 위험: {', '.join(dict.fromkeys(risks))}"
            _check(checks, "overflow-risk", "warning", message, index)
            warnings.append(message)
    if not overflow_count:
        _check(checks, "overflow-risk", "pass", "계획에서 뚜렷한 텍스트·요소 과밀 위험을 찾지 못했습니다.")

    try:
        max_bytes = max(100, min(100_000, int(max_html_size_kb))) * 1024
    except Exception:
        max_bytes = 8_000 * 1024
    require = bool(require_html)
    inspector = _PresentationHTMLInspector()
    if not html_source:
        if require or artifact_supplied:
            message = "검사할 HTML 원문이 없습니다."
            _check(checks, "html-present", "fail", message)
            blocking.append(message)
        else:
            message = "HTML 생성 전 계획만 검사했습니다. Renderer 이후 다시 검사하세요."
            _check(checks, "html-present", "warning", message)
            warnings.append(message)
    else:
        encoded_size = len(html_source.encode("utf-8"))
        if encoded_size > max_bytes:
            message = f"HTML 크기가 제한({max_bytes // 1024}KB)을 초과했습니다."
            _check(checks, "html-size", "fail", message)
            blocking.append(message)
        else:
            _check(checks, "html-size", "pass", "HTML 크기가 허용 범위입니다.")
        try:
            inspector.feed(html_source)
        except Exception as exc:
            message = f"HTML 구조를 분석할 수 없습니다: {str(exc)[:300]}"
            _check(checks, "html-parse", "fail", message)
            blocking.append(message)
        if inspector.prohibited_tags:
            message = f"금지된 HTML 태그가 있습니다: {', '.join(sorted(set(inspector.prohibited_tags)))}"
            _check(checks, "prohibited-tags", "fail", message)
            blocking.append(message)
        else:
            _check(checks, "prohibited-tags", "pass", "iframe, object, embed, form 등 금지 태그가 없습니다.")
        external_found = bool(inspector.external_urls or inspector.external_script_or_link or _CSS_EXTERNAL_RE.search(html_source))
        if external_found:
            message = "HTML에서 외부 URL 또는 외부 리소스 참조를 찾았습니다."
            _check(checks, "external-resources", "fail", message)
            blocking.append(message)
        else:
            _check(checks, "external-resources", "pass", "HTML이 외부 URL이나 외부 리소스를 참조하지 않습니다.")
        if inspector.event_attributes or _DYNAMIC_NETWORK_RE.search(html_source):
            message = "인라인 이벤트 속성 또는 동적 네트워크 호출 코드를 찾았습니다."
            _check(checks, "active-content", "fail", message)
            blocking.append(message)
        else:
            _check(checks, "active-content", "pass", "인라인 이벤트 속성과 동적 네트워크 호출이 없습니다.")
        accessibility_missing: list[str] = []
        if not inspector.html_lang:
            accessibility_missing.append("html lang")
        if not inspector.has_title:
            accessibility_missing.append("title")
        if not inspector.has_main:
            accessibility_missing.append("main")
        if inspector.images_without_alt:
            accessibility_missing.append(f"alt 없는 이미지 {inspector.images_without_alt}개")
        if inspector.tables_without_header:
            accessibility_missing.append(f"th 없는 표 {inspector.tables_without_header}개")
        if accessibility_missing:
            message = f"접근성 보완 필요: {', '.join(accessibility_missing)}"
            _check(checks, "accessibility", "warning", message)
            warnings.append(message)
        else:
            _check(checks, "accessibility", "pass", "기본 문서 구조, 이미지 대체 텍스트와 표 머리글을 확인했습니다.")
        if inspector.slide_sections and inspector.slide_sections != len(slides):
            message = f"HTML 슬라이드 {inspector.slide_sections}개와 계획 슬라이드 {len(slides)}개가 다릅니다."
            _check(checks, "rendered-slide-count", "fail", message)
            blocking.append(message)
        elif not inspector.slide_sections:
            message = "HTML에서 class='slide'인 section을 찾지 못했습니다."
            _check(checks, "rendered-slide-count", "warning", message)
            warnings.append(message)
        else:
            _check(checks, "rendered-slide-count", "pass", "HTML과 계획의 슬라이드 수가 일치합니다.")
        if re.search(r"overflow\s*:\s*(?:auto|scroll)", html_source, re.I):
            message = "슬라이드 CSS에 auto/scroll overflow가 있어 내용 잘림 또는 내부 스크롤을 확인해야 합니다."
            _check(checks, "css-overflow", "warning", message)
            warnings.append(message)

    status = "fail" if blocking else ("warning" if warnings else "pass")
    report = {
        "status": status,
        "checks": checks,
        "blocking_errors": blocking,
        "warnings": warnings,
        "metrics": {
            "planned_slide_count": len(slides),
            "target_slide_count": target_count,
            "rendered_slide_count": inspector.slide_sections if html_source else 0,
            "html_bytes": len(html_source.encode("utf-8")) if html_source else 0,
            "overflow_risk_slide_count": overflow_count,
        },
    }
    result = {
        "payload_version": "ppt-reference-html-v1",
        "quality_report": report,
        "presentation_plan": plan,
        "warnings": warnings,
        "errors": [{"code": "quality_gate_failed", "message": item} for item in blocking],
        "meta": {"status": status, **report["metrics"]},
    }
    # 공유 링크 발행 경로는 이 Gate 출력만 받습니다. 실패 시 HTML alias를 넣지 않아
    # Publisher가 검사 전 원문을 우회 발행할 수 없게 합니다.
    if status != "fail":
        artifact_payload = _payload(presentation_artifact_value)
        for key in ("presentation_artifact", "html_report"):
            if isinstance(artifact_payload.get(key), dict):
                result[key] = deepcopy(artifact_payload[key])
    return result


class PresentationQualityGate(Component):
    """발표 계획과 선택적 HTML 산출물을 검사하는 Flow 전용 Node."""

    display_name = "07 프레젠테이션 품질 검사"
    description = "슬라이드 수, 과밀 위험, 접근성, 외부 URL과 위험한 HTML 구조를 검사해 품질 보고서를 반환합니다."
    icon = "ShieldCheck"
    name = "PresentationQualityGate"

    inputs = [
        DataInput(name="presentation_plan", display_name="검증된 발표 계획", required=True),
        DataInput(name="presentation_artifact", display_name="HTML 프레젠테이션", required=False),
        BoolInput(name="require_html", display_name="HTML 필수 검사", value=False, advanced=True),
        IntInput(name="max_html_size_kb", display_name="최대 HTML 크기(KB)", value=8000, advanced=True),
    ]
    outputs = [Output(name="quality_report", display_name="프레젠테이션 품질 보고서", method="build_quality_report", types=["Data"])]

    def build_quality_report(self) -> Data:
        result = evaluate_presentation_quality(
            getattr(self, "presentation_plan", None),
            getattr(self, "presentation_artifact", None),
            require_html=getattr(self, "require_html", False),
            max_html_size_kb=getattr(self, "max_html_size_kb", 8000),
        )
        self.status = result["meta"]
        return _make_data(result)
