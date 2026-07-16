from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType

from lfx.custom.eval import eval_custom_component_code
from lfx.custom.utils import create_component_template


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "components" / "html_presentation_renderer" / "html_presentation_renderer.py"


def load_renderer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("test_html_presentation_renderer_source", SOURCE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"렌더러 원본을 불러올 수 없습니다: {SOURCE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample_plan() -> dict:
    return {
        "request": {
            "question": "상반기 실적을 발표 자료로 만들어줘",
            "audience": "경영진",
            "secret": "출력에 포함하면 안 되는 내부값",
        },
        "presentation_plan": {
            "title": "2026년 상반기 실적",
            "subtitle": "핵심 성과와 다음 행동",
            "filename_hint": "2026_상반기_실적.html",
            "theme": {
                "primary_color": "#2563EB",
                "accent_color": "#0F766E",
                "font_family": "sans",
            },
            "design_policy": {
                "policy_id": "hallmark-emil-balanced-v1",
                "composition": {"allow_decorative_gradient": False},
                "motion": {
                    "profile": "purposeful-subtle",
                    "max_ui_duration_ms": 300,
                    "button_press_duration_ms": 120,
                    "slide_enter_duration_ms": 180,
                    "easing": "cubic-bezier(0.23, 1, 0.32, 1)",
                },
            },
            "slides": [
                {
                    "slide_id": "cover",
                    "layout": "title",
                    "design_role": "cover",
                    "visual_weight": "strong",
                    "eyebrow": "Executive briefing",
                    "title": "2026년 상반기 실적",
                    "subtitle": "핵심 성과와 다음 행동",
                    "footer": "Agent Ground",
                    "elements": [
                        {
                            "type": "kpi_grid",
                            "items": [
                                {"label": "매출", "value": "128억", "delta": "+12%"},
                                {"label": "위험 과제", "value": "3건", "delta": "검토 필요", "tone": "danger"},
                            ],
                        }
                    ],
                },
                {
                    "layout": "two_column",
                    "design_role": "comparison",
                    "visual_weight": "balanced",
                    "title": "성과와 추이",
                    "elements": [
                        {
                            "type": "bar_chart",
                            "title": "지역별 매출",
                            "width": "half",
                            "data": [
                                {"label": "서울", "value": 42},
                                {"label": "부산", "value": 31},
                                {"label": "대전", "value": 19},
                            ],
                        },
                        {
                            "type": "line_chart",
                            "title": "월별 추이",
                            "width": "half",
                            "labels": ["1월", "2월", "3월"],
                            "values": [12, 18, 25],
                        },
                    ],
                },
            ],
        },
    }


def test_renderer_builds_self_contained_16_9_navigation_and_report_alias() -> None:
    module = load_renderer()
    result = module.render_html_presentation(sample_plan())

    assert result["success"] is True
    assert result["status"] == "ok"
    assert result["payload_version"] == "html-presentation-artifact-v1"
    artifact = result["presentation_artifact"]
    document = artifact["html"]

    assert artifact["aspect_ratio"] == "16:9"
    assert artifact["self_contained"] is True
    assert artifact["slide_count"] == 2
    assert artifact["filename_hint"] == "2026_상반기_실적"
    assert artifact["design_policy_id"] == "hallmark-emil-balanced-v1"
    assert artifact["motion_profile"] == "purposeful-subtle"
    assert artifact["byte_size"] == len(document.encode("utf-8"))
    assert artifact["sha256"] == hashlib.sha256(document.encode("utf-8")).hexdigest()
    assert result["html_report"] == artifact
    assert result["request"] == {
        "question": "상반기 실적을 발표 자료로 만들어줘",
        "audience": "경영진",
    }

    assert "aspect-ratio: 16 / 9" in document
    assert 'id="deck-prev"' in document
    assert 'id="deck-next"' in document
    assert 'id="deck-progress"' in document
    assert "ArrowRight" in document and "ArrowLeft" in document
    assert "history.replaceState" in document
    assert "requestFullscreen" in document
    assert "fullscreenchange" in document
    assert "@media print" in document
    assert "@page" in document
    assert 'data-design-policy="hallmark-emil-balanced-v1"' in document
    assert 'data-design-role="cover"' in document
    assert "linear-gradient" not in document and "radial-gradient" not in document
    assert "transition: all" not in document
    assert "scale(0)" not in document
    assert "@media (hover: hover) and (pointer: fine)" in document
    assert "prefers-reduced-motion: reduce" in document
    assert 'interaction === "pointer"' in document
    assert "https://" not in document and "http://" not in document


def test_renderer_outputs_semantic_table_and_accessible_inline_svg_charts() -> None:
    module = load_renderer()
    plan = {
        "title": "차트와 표",
        "slides": [
            {
                "title": "데이터 표현",
                "elements": [
                    {
                        "type": "table",
                        "title": "정확한 값",
                        "caption": "지역별 실적표",
                        "columns": [
                            {"key": "region", "label": "지역"},
                            {"key": "revenue", "label": "매출"},
                        ],
                        "rows": [
                            {"region": "서울", "revenue": 42},
                            {"region": "부산", "revenue": 31},
                        ],
                    },
                    {
                        "type": "bar_chart",
                        "title": "막대",
                        "data": [{"label": "A", "value": 10}, {"label": "B", "value": -2}],
                    },
                    {
                        "type": "line_chart",
                        "title": "선",
                        "data": [{"label": "1월", "value": 3}, {"label": "2월", "value": 7}],
                    },
                    {
                        "type": "scatter_chart",
                        "title": "산점도",
                        "x_label": "비용",
                        "y_label": "효과",
                        "data": [
                            {"label": "과제 A", "x": 10, "y": 20},
                            {"label": "과제 B", "x": 15, "y": 35},
                        ],
                    },
                ],
            }
        ],
    }
    document = module.render_html_presentation(plan)["presentation_artifact"]["html"]

    assert "<table>" in document
    assert "<caption>지역별 실적표</caption>" in document
    assert '<th scope="col">지역</th>' in document
    assert '<th scope="row">서울</th>' in document
    assert document.count('role="img"') == 3
    assert "<title id=\"bar-title-0-1\">막대</title>" in document
    assert "<title id=\"line-title-0-2\">선</title>" in document
    assert "<title id=\"scatter-title-0-3\">산점도</title>" in document
    assert "<desc id=" in document
    assert "<polyline" in document
    assert "과제 A: X 10, Y 20" in document


def test_renderer_accepts_flow_layout_aliases_and_nested_design_system() -> None:
    module = load_renderer()
    plan = {
        "title": "Flow 계약 호환",
        "design_system": {
            "colors": {
                "background": "#111827",
                "text": "#17212B",
                "muted_text": "#607080",
                "primary": "#173B57",
                "accent": "#E78A2F",
            },
            "typography": {"body_family": "Pretendard, Noto Sans KR, sans-serif"},
        },
        "slides": [
            {"layout": "cover", "title": "표지", "elements": [{"element_type": "text", "content": "시작"}]},
            {"layout": "chart-focus", "title": "차트", "elements": [{"element_type": "bar_chart", "data": [{"label": "A", "value": 1}]}]},
            {"layout": "conclusion", "title": "마무리", "elements": [{"element_type": "text", "content": "끝"}]},
        ],
    }

    result = module.render_html_presentation(plan)
    document = result["presentation_artifact"]["html"]

    assert result["presentation_artifact"]["slide_count"] == 3
    assert "slide-layout-title" in document
    assert "slide-layout-chart" in document
    assert "slide-layout-closing" in document
    assert "--primary: #173b57" in document
    assert "--muted: #607080" in document
    assert not any(item["code"] == "unsupported_layout_replaced" for item in result["warnings"])


def test_renderer_escapes_user_content_and_rejects_external_images_and_css_values() -> None:
    module = load_renderer()
    malicious = {
        "title": "<script>alert('제목')</script>",
        "theme": {
            "primary_color": "red; background:url(https://evil.invalid/x)",
            "font_family": "url(https://evil.invalid/font)",
        },
        "slides": [
            {
                "title": "<img src=x onerror=alert(1)>",
                "elements": [
                    {"type": "text", "text": "</script><script>alert('본문')</script>"},
                    {"type": "image", "src": "https://evil.invalid/image.png", "alt": "외부 이미지"},
                ],
            }
        ],
    }
    result = module.render_html_presentation(malicious)
    document = result["presentation_artifact"]["html"]
    warning_codes = {item["code"] for item in result["warnings"]}

    assert result["success"] is True
    assert "&lt;script&gt;alert(&#x27;제목&#x27;)&lt;/script&gt;" in document
    assert "&lt;img src=x onerror=alert(1)&gt;" in document
    assert "&lt;/script&gt;&lt;script&gt;alert(&#x27;본문&#x27;)&lt;/script&gt;" in document
    assert "evil.invalid" not in document
    assert "red; background" not in document
    assert "invalid_theme_color_replaced" in warning_codes
    assert "unsupported_font_replaced" in warning_codes
    assert "unsafe_image_source_skipped" in warning_codes


def test_renderer_allows_only_embedded_raster_data_url() -> None:
    module = load_renderer()
    # 1x1 투명 PNG입니다.
    png_data_url = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    plan = {
        "title": "이미지",
        "slides": [
            {
                "title": "내장 이미지",
                "elements": [
                    {"type": "image", "src": png_data_url, "alt": "투명 픽셀", "caption": "내장 Data URL"},
                    {"type": "image", "src": "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=", "alt": "차단 SVG"},
                ],
            }
        ],
    }
    result = module.render_html_presentation(plan)
    document = result["presentation_artifact"]["html"]

    assert png_data_url in document
    assert "data:image/svg+xml" not in document
    assert any(item["code"] == "unsafe_image_source_skipped" for item in result["warnings"])


def test_renderer_returns_structured_error_for_missing_slides_and_size_limit(monkeypatch) -> None:
    module = load_renderer()
    missing = module.render_html_presentation({"title": "빈 계획", "slides": []})

    assert missing["success"] is False
    assert missing["presentation_artifact"]["html"] == ""
    assert missing["html_report"]["html"] == ""
    assert missing["errors"][0]["code"] == "slides_required"

    assert module.MAX_HTML_BYTES == 20 * 1024 * 1024
    monkeypatch.setattr(module, "MAX_HTML_BYTES", 1_000)
    oversized = module.render_html_presentation(
        {"title": "크기 제한", "slides": [{"title": "큰 문서", "content": "가" * 5_000}]}
    )
    assert oversized["success"] is False
    assert oversized["errors"][-1]["code"] == "html_size_exceeded"
    assert oversized["presentation_artifact"]["byte_size"] == 0
    assert oversized["presentation_artifact"]["sha256"] == ""


def test_component_compiles_to_langflow_182_template_with_fixed_ports() -> None:
    source = SOURCE_PATH.read_text(encoding="utf-8")
    component_class = eval_custom_component_code(source)
    config, instance = create_component_template(
        {"code": source, "output_types": []},
        module_name="agent_ground.test.html_presentation_renderer",
    )

    assert component_class.__name__ == "HtmlPresentationRenderer"
    assert instance.__class__.__name__ == "HtmlPresentationRenderer"
    assert config["field_order"] == ["presentation_plan"]
    assert [item["name"] for item in config["outputs"]] == ["presentation_artifact"]
    assert config["outputs"][0]["types"] == ["Data"]
    assert config["template"]["code"]["value"] == source
