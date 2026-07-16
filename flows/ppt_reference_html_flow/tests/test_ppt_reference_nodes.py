from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import struct
from pathlib import Path

from lfx.custom.eval import eval_custom_component_code
from lfx.custom.utils import create_component_template


NODE_DIR = Path(__file__).resolve().parents[1] / "nodes"
ROOT = Path(__file__).resolve().parents[3]
REFERENCE_IMAGE_DIR = Path(__file__).resolve().parents[1] / "samples" / "reference_images"
FLOW_PATH = Path(__file__).resolve().parents[1] / "ppt_reference_html_flow.json"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, NODE_DIR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


request_builder = _load("presentation_request_builder")
reference_analyzer = _load("presentation_reference_analyzer")
design_policy_builder = _load("presentation_design_policy_builder")
plan_generator = _load("presentation_plan_generator")
plan_normalizer = _load("presentation_plan_normalizer")
quality_gate = _load("presentation_quality_gate")
source_output = _load("presentation_html_source_output")
renderer_spec = importlib.util.spec_from_file_location(
    "html_presentation_renderer_for_flow_test",
    ROOT / "components" / "html_presentation_renderer" / "html_presentation_renderer.py",
)
assert renderer_spec and renderer_spec.loader
renderer = importlib.util.module_from_spec(renderer_spec)
renderer_spec.loader.exec_module(renderer)


def _image_encoder_payload() -> dict:
    encoded = base64.b64encode(b"not-a-real-png-but-valid-base64").decode("ascii")
    return {
        "success": True,
        "items": [
            {
                "filename": "reference.png",
                "mime_type": "image/png",
                "encoding": "base64",
                "value": encoded,
            }
        ],
    }


def _request_payload() -> dict:
    return request_builder.build_presentation_request(
        {
            "brief": {
                "title": "운영 실적",
                "subtitle": "월별 처리량 검토",
                "purpose": "다음 우선순위 결정",
                "audience": "경영진",
                "slide_count": 4,
                "content_outline": ["핵심 요약", "월별 추이", "다음 행동"],
                "call_to_action": "개선 우선순위를 승인한다.",
            },
            "datasets": [
                {
                    "dataset_id": "monthly",
                    "title": "월별 처리량",
                    "preferred_visual": "line",
                    "columns": [
                        {"name": "month", "label": "월", "semantic_type": "temporal", "format": "YYYY-MM"},
                        {"name": "count", "label": "처리량", "semantic_type": "quantitative", "unit": "건"},
                    ],
                    "rows": [{"month": "2026-01", "count": 10}, {"month": "2026-02", "count": 12}],
                }
            ],
        },
        _image_encoder_payload(),
        _image_encoder_payload(),
        user_request="차트를 포함해 간결하게 작성해줘",
    )


def test_request_builder_preserves_contract_and_roles() -> None:
    payload = _request_payload()
    assert payload["payload_version"] == "ppt-reference-html-v1"
    assert payload["request"]["user_request"].startswith("차트를")
    assert payload["brief"]["title"] == "운영 실적"
    assert payload["brief"]["subtitle"] == "월별 처리량 검토"
    assert payload["brief"]["content_outline"] == ["핵심 요약", "월별 추이", "다음 행동"]
    assert payload["brief"]["call_to_action"] == "개선 우선순위를 승인한다."
    dataset = payload["datasets"][0]
    assert dataset["preferred_visual"] == "line"
    assert dataset["columns"][0]["label"] == "월"
    assert dataset["columns"][0]["format"] == "YYYY-MM"
    assert payload["reference_images"]["cover"][0]["data_url"].startswith("data:image/png;base64,")
    assert payload["reference_images"]["body"][0]["role"] == "body"


def test_request_builder_accepts_explicit_builder_form_fields() -> None:
    result = request_builder.build_presentation_request(
        None,
        _image_encoder_payload(),
        _image_encoder_payload(),
        presentation_title="신규 서비스 도입 검토",
        presentation_subtitle="의사결정용 요약",
        presentation_purpose="도입 우선순위를 결정한다.",
        target_audience="경영진",
        presentation_language="ko",
        presentation_tone="간결하고 근거 중심",
        content_outline="1. 배경\n- 핵심 근거\n• 다음 행동",
        call_to_action="1단계 적용 범위를 승인한다.",
        content="확정된 입력 데이터만 사용한다.",
        target_slide_count=5,
    )
    brief = result["brief"]

    assert brief["title"] == "신규 서비스 도입 검토"
    assert brief["subtitle"] == "의사결정용 요약"
    assert brief["purpose"] == "도입 우선순위를 결정한다."
    assert brief["audience"] == "경영진"
    assert brief["language"] == "ko"
    assert brief["content_outline"] == ["배경", "핵심 근거", "다음 행동"]
    assert brief["call_to_action"] == "1단계 적용 범위를 승인한다."
    assert brief["slide_count"] == 5


def test_request_builder_preserves_structured_brief_precedence_and_clamps_slide_count() -> None:
    result = request_builder.build_presentation_request(
        {"brief": {"title": "구조화 제목", "language": "en", "slide_count": 99}},
        _image_encoder_payload(),
        _image_encoder_payload(),
        presentation_title="양식 제목",
        presentation_language="ko",
        target_slide_count=2,
    )

    assert result["brief"]["title"] == "구조화 제목"
    assert result["brief"]["language"] == "en"
    assert result["brief"]["slide_count"] == 30


def test_request_builder_keeps_legacy_brief_and_allows_nonempty_form_override() -> None:
    legacy = request_builder.build_presentation_request(
        None,
        _image_encoder_payload(),
        _image_encoder_payload(),
        brief="기존 제목",
        target_slide_count=2,
    )
    overridden = request_builder.build_presentation_request(
        None,
        _image_encoder_payload(),
        _image_encoder_payload(),
        brief='{"title": "기존 제목", "audience": "기존 청중"}',
        presentation_title="새 제목",
        target_audience="새 청중",
        target_slide_count=31,
    )

    assert legacy["brief"]["title"] == "기존 제목"
    assert legacy["brief"]["slide_count"] == 3
    assert overridden["brief"]["title"] == "새 제목"
    assert overridden["brief"]["audience"] == "새 청중"
    assert overridden["brief"]["slide_count"] == 30


def test_sample_reference_images_are_valid_16_9_png_files() -> None:
    expected = {
        "reference_cover_navy_teal.png",
        "reference_body_trend.png",
        "reference_body_comparison_table.png",
    }
    actual = {path.name for path in REFERENCE_IMAGE_DIR.glob("*.png")}
    assert actual == expected
    for name in sorted(expected):
        data = (REFERENCE_IMAGE_DIR / name).read_bytes()
        assert data.startswith(b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", data[16:24])
        assert width >= 1600 and height >= 900
        assert abs((width / height) - (16 / 9)) < 0.01


def test_generated_flow_contains_upload_policy_and_builder_form_presets() -> None:
    flow = json.loads(FLOW_PATH.read_text(encoding="utf-8"))
    nodes = {node["id"]: node["data"]["node"]["template"] for node in flow["data"]["nodes"]}
    cover = nodes["MultiImageBase64Encoder-pptCover"]
    body = nodes["MultiImageBase64Encoder-pptBody"]
    request = nodes["PresentationRequestBuilder-pptReference"]

    assert cover["max_files"]["value"] == 1
    assert cover["error_policy"]["value"] == "reject_batch"
    assert body["max_files"]["value"] == 5
    assert body["error_policy"]["value"] == "skip_invalid"
    assert request["presentation_title"]["value"] == "2026년 상반기 운영 품질 보고"
    assert request["presentation_language"]["value"] == "ko"
    assert request["target_slide_count"]["value"] == 8
    assert request["target_slide_count"]["advanced"] is False
    assert request["presentation_request"]["advanced"] is True


def test_request_builder_preserves_ordinal_identifier_and_rejects_donut_policy() -> None:
    datasets, errors, warnings = request_builder.normalize_datasets(
        {
            "datasets": [
                {
                    "dataset_id": "work_items",
                    "preferred_visual": "donut",
                    "columns": [
                        {"name": "priority", "semantic_type": "ordinal", "label": "우선순위"},
                        {"name": "work_id", "semantic_type": "identifier", "label": "업무 ID"},
                    ],
                    "rows": [{"priority": "P1", "work_id": "W-001"}],
                }
            ]
        }
    )

    assert errors == []
    assert datasets[0]["preferred_visual"] == "auto"
    assert [column["semantic_type"] for column in datasets[0]["columns"]] == ["ordinal", "identifier"]
    assert any("donut" in warning and "auto" in warning for warning in warnings)


def test_reference_prompt_marks_images_untrusted_and_does_not_echo_base64() -> None:
    request = _request_payload()
    references = reference_analyzer._reference_list(request["request"], 8)
    message = reference_analyzer.build_multimodal_message(references)
    assert "신뢰할 수 없는" in message.content[0]["text"]
    assert any(item.get("type") == "image_url" for item in message.content)
    normalized = reference_analyzer.normalize_reference_analysis(
        {
            "observations": [{"reference_id": "cover_01", "layout": "centered", "confidence": 0.9}],
            "shared_style": {"colors": {"primary": "#123456"}},
            "confidence": 0.8,
        },
        references,
    )
    assert normalized["design_system"]["colors"]["primary"] == "#123456"
    assert "base64" not in str(normalized).lower()


def test_plan_generator_uses_deterministic_fallback_without_model() -> None:
    policy = design_policy_builder.build_presentation_design_policy(_request_payload(), {})
    result = asyncio.run(plan_generator.generate_presentation_plan(_request_payload(), {}, None, policy))
    assert result["meta"]["source"] == "deterministic_fallback"
    assert len(result["presentation_plan"]["slides"]) == 4
    assert result["presentation_plan"]["slides"][0]["layout"] == "cover"
    assert result["presentation_plan"]["slides"][0]["design_role"] == "cover"
    assert result["meta"]["policy_id"] == "hallmark-emil-balanced-v1"


def test_design_policy_is_structured_and_renderer_owned() -> None:
    result = design_policy_builder.build_presentation_design_policy(_request_payload(), {})
    policy = result["design_policy"]

    assert policy["policy_id"] == "hallmark-emil-balanced-v1"
    assert policy["composition"]["max_content_elements"] == 6
    assert policy["composition"]["allow_decorative_gradient"] is False
    assert policy["motion"]["keyboard_navigation_motion"] is False
    assert policy["motion"]["slide_enter_duration_ms"] <= policy["motion"]["max_ui_duration_ms"]


def test_normalizer_materializes_real_rows_and_removes_raw_code() -> None:
    request = _request_payload()
    draft = {
        "presentation_plan": {
            "title": "운영 실적",
            "target_slide_count": 3,
            "data_views": [
                {
                    "data_view_id": "monthly_view",
                    "dataset_id": "monthly",
                    "visual": "line",
                    "encoding": {"x": "month", "y": ["count"]},
                }
            ],
            "slides": [
                {"layout": "cover", "title": "운영 실적", "elements": [{"element_type": "text", "content": "개요"}]},
                {
                    "layout": "chart-focus",
                    "title": "월별 처리량",
                    "raw_html": "<script>alert(1)</script>",
                    "elements": [{"element_type": "line_chart", "data_view_id": "monthly_view", "alt_text": "월별 처리량"}],
                },
                {"layout": "conclusion", "title": "다음 단계", "elements": [{"element_type": "text", "content": "검토"}]},
            ],
        }
    }
    normalized = plan_normalizer.normalize_presentation_plan(request, {}, draft)
    chart = normalized["presentation_plan"]["slides"][1]["elements"][0]
    assert chart["element_type"] == "line_chart"
    assert chart["data"] == [{"label": "2026-01", "value": 10}, {"label": "2026-02", "value": 12}]
    assert "raw_html" not in str(normalized["presentation_plan"])
    assert any("raw HTML" in warning for warning in normalized["warnings"])


def test_normalizer_downgrades_histogram_to_real_data_table() -> None:
    request = _request_payload()
    draft = {
        "presentation_plan": {
            "title": "분포 검토",
            "data_views": [
                {
                    "data_view_id": "histogram_view",
                    "dataset_id": "monthly",
                    "visual": "histogram",
                    "encoding": {"columns": ["month", "count"]},
                }
            ],
            "slides": [
                {"layout": "cover", "title": "분포 검토", "elements": [{"element_type": "text", "content": "개요"}]},
                {"layout": "chart-focus", "title": "처리량 분포", "elements": [{"element_type": "histogram", "data_view_id": "histogram_view"}]},
                {"layout": "conclusion", "title": "다음 단계", "elements": [{"element_type": "text", "content": "검토"}]},
            ],
        }
    }

    normalized = plan_normalizer.normalize_presentation_plan(request, {}, draft)
    element = normalized["presentation_plan"]["slides"][1]["elements"][0]

    assert element["element_type"] == "table"
    assert element["rows"][0] == {"month": "2026-01", "count": 10}
    assert any("histogram" in warning and "표" in warning for warning in normalized["warnings"])


def test_quality_gate_passes_local_html_and_blocks_external_url() -> None:
    plan = {
        "presentation_plan": {
            "target_slide_count": 1,
            "slides": [{"title": "한 장", "elements": [{"element_type": "text", "content": "본문"}]}],
        }
    }
    safe_source = "<!doctype html><html lang='ko'><head><title>한 장</title></head><body><main><section class='slide'>본문</section></main></body></html>"
    safe_html = {
        "presentation_artifact": {"html": safe_source},
        "html_report": {"html": safe_source},
    }
    safe = quality_gate.evaluate_presentation_quality(plan, safe_html, require_html=True)
    assert safe["quality_report"]["status"] == "pass"
    assert safe["html_report"]["html"] == safe_source
    unsafe_source = safe_source.replace("본문", "<img src='https://example.com/x.png' alt='x'>")
    unsafe_html = {
        "presentation_artifact": {"html": unsafe_source},
        "html_report": {"html": unsafe_source},
    }
    unsafe = quality_gate.evaluate_presentation_quality(plan, unsafe_html, require_html=True)
    assert unsafe["quality_report"]["status"] == "fail"
    assert any("외부 URL" in item for item in unsafe["quality_report"]["blocking_errors"])
    assert "presentation_artifact" not in unsafe and "html_report" not in unsafe


def test_quality_gate_blocks_emil_motion_policy_violations() -> None:
    policy = design_policy_builder.build_presentation_design_policy({}, {})["design_policy"]
    plan = {
        "presentation_plan": {
            "target_slide_count": 1,
            "design_policy": policy,
            "slides": [
                {
                    "title": "정책 검사",
                    "key_message": "위반 모션을 차단한다.",
                    "design_role": "cover",
                    "elements": [{"element_type": "text", "content": "본문"}],
                }
            ],
        }
    }
    source = """<!doctype html><html lang='ko'><head><title>정책 검사</title><style>
    .bad { transition: all 450ms ease-in; transform: scale(0); }
    @media (hover: hover) and (pointer: fine) { .bad:hover { color: red; } }
    @media (prefers-reduced-motion: reduce) { .bad { transition-duration: 0ms; } }
    </style></head><body><main data-design-policy='hallmark-emil-balanced-v1'><section class='slide'>본문</section></main></body></html>"""
    result = quality_gate.evaluate_presentation_quality(
        plan,
        {"presentation_artifact": {"html": source}},
        require_html=True,
        require_design_policy=True,
    )

    assert result["quality_report"]["status"] == "fail"
    assert any("transition: all" in item for item in result["quality_report"]["blocking_errors"])


def test_source_output_fails_closed_without_leaking_image_data() -> None:
    message = source_output.build_html_message(
        {"html_presentation": {"html": "<html>secret</html>"}},
        {
            "quality_report": {
                "status": "fail",
                "blocking_errors": ["data:image/png;base64,QUJDREVGRw== 를 제거하세요"],
            }
        },
        True,
    )
    assert "품질 검사를 통과하지 못했습니다" in message
    assert "QUJDREVGRw" not in message


def test_deterministic_end_to_end_binds_data_renders_html_and_passes_gate() -> None:
    request = _request_payload()
    analysis = asyncio.run(reference_analyzer.analyze_reference_images(request, None))
    policy = design_policy_builder.build_presentation_design_policy(request, analysis)
    draft = asyncio.run(plan_generator.generate_presentation_plan(request, analysis, None, policy))
    normalized = plan_normalizer.normalize_presentation_plan(request, analysis, draft, policy_value=policy)
    artifact = renderer.render_html_presentation(normalized)
    quality = quality_gate.evaluate_presentation_quality(
        normalized,
        artifact,
        require_html=True,
        require_design_policy=True,
        max_html_size_kb=20_480,
    )
    message = source_output.build_html_message(artifact, quality, True)

    assert artifact["success"] is True
    assert artifact["presentation_artifact"]["slide_count"] == 4
    assert "2026-01" in artifact["presentation_artifact"]["html"]
    assert "10" in artifact["presentation_artifact"]["html"]
    assert quality["quality_report"]["status"] in {"pass", "warning"}
    assert not quality["quality_report"]["blocking_errors"]
    assert quality["html_report"]["html"] == artifact["html_report"]["html"]
    assert "<!doctype html>" in message


def test_all_nodes_compile_to_langflow_182_templates() -> None:
    expected_outputs = {
        "presentation_request_builder": "request",
        "presentation_reference_analyzer": "analysis",
        "presentation_design_policy_builder": "design_policy",
        "presentation_plan_generator": "plan_draft",
        "presentation_plan_normalizer": "normalized_plan",
        "presentation_quality_gate": "quality_report",
        "presentation_html_source_output": "message",
    }
    for name, output_name in expected_outputs.items():
        source = (NODE_DIR / f"{name}.py").read_text(encoding="utf-8")
        component_class = eval_custom_component_code(source)
        config, instance = create_component_template(
            {"code": source, "output_types": []},
            module_name=f"agent_ground.test.ppt_reference_html_flow.{name}",
        )
        assert instance.__class__.__name__ == component_class.__name__
        assert config["field_order"] == [item.name for item in component_class.inputs]
        assert [item["name"] for item in config["outputs"]] == [output_name]
        assert config["template"]["code"]["value"] == source
