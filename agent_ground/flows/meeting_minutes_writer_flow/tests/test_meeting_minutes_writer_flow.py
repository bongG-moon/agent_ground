from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from lfx.custom.eval import eval_custom_component_code
from lfx.custom.utils import create_component_template


ROOT = Path(__file__).resolve().parents[3]
FLOW_ROOT = ROOT / "flows" / "meeting_minutes_writer_flow"
NODE_PATHS = {
    "request": FLOW_ROOT / "nodes" / "meeting_minutes_request_builder.py",
    "style": FLOW_ROOT / "nodes" / "meeting_minutes_style_analyzer.py",
    "draft": FLOW_ROOT / "nodes" / "meeting_minutes_draft_writer.py",
    "review": FLOW_ROOT / "nodes" / "meeting_minutes_reviewer.py",
}


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"모듈을 읽을 수 없습니다: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def extraction_message(items: list[tuple[str, str]]) -> str:
    total = len(items)
    blocks = ["# 문서 텍스트 추출 결과"]
    for index, (name, text) in enumerate(items, start=1):
        blocks.append(
            "\n".join(
                [
                    f"[FILE {index}/{total}] {name}",
                    "처리 경로: 로컬 추출",
                    f"문자 수: {len(text):,}",
                    "",
                    text,
                    f"[END FILE {index}/{total}]",
                ]
            )
        )
    return "\n\n".join(blocks)


class FakeModel:
    def __init__(self, response):
        self.response = response
        self.messages = []

    async def ainvoke(self, messages):
        self.messages.append(messages)
        return type("Response", (), {"content": self.response})()


def test_request_builder_pairs_files_by_upload_order() -> None:
    module = load_module("meeting_minutes_request_test", NODE_PATHS["request"])
    result = module.build_meeting_minutes_request(
        extraction_message([("t1.txt", "과거 녹취 1"), ("t2.txt", "과거 녹취 2")]),
        extraction_message([("m1.txt", "과거 회의록 1"), ("m2.txt", "과거 회의록 2")]),
        extraction_message([("current.txt", "현재 회의 의사결정과 후속 조치")]),
        additional_instructions="의사결정 위주로 작성",
        meeting_title="현재 회의",
    )
    request = result["request"]
    assert result["meta"]["example_pair_count"] == 2
    assert request["example_pairs"][0]["transcript_file_name"] == "t1.txt"
    assert request["example_pairs"][0]["minutes_file_name"] == "m1.txt"
    assert request["example_pairs"][1]["transcript"] == "과거 녹취 2"
    assert request["current_transcript"]["text"] == "현재 회의 의사결정과 후속 조치"
    assert request["additional_instructions"] == "의사결정 위주로 작성"
    assert request["trust_boundary"]["historical_facts_must_not_enter_current_minutes"] is True


def test_request_builder_rejects_mismatched_pairs_and_multiple_current_files() -> None:
    module = load_module("meeting_minutes_request_validation_test", NODE_PATHS["request"])
    with pytest.raises(ValueError, match="파일 수가 다릅니다"):
        module.build_meeting_minutes_request(
            extraction_message([("t1.txt", "녹취 1"), ("t2.txt", "녹취 2")]),
            extraction_message([("m1.txt", "회의록 1")]),
            extraction_message([("current.txt", "현재")]),
        )
    with pytest.raises(ValueError, match="정확히 한 개"):
        module.build_meeting_minutes_request(
            extraction_message([("t1.txt", "녹취 1")]),
            extraction_message([("m1.txt", "회의록 1")]),
            extraction_message([("c1.txt", "현재 1"), ("c2.txt", "현재 2")]),
        )


def test_style_analyzer_normalizes_profile_without_historical_body() -> None:
    request_module = load_module("meeting_minutes_request_style_test", NODE_PATHS["request"])
    style_module = load_module("meeting_minutes_style_test", NODE_PATHS["style"])
    request_result = request_module.build_meeting_minutes_request(
        extraction_message([("past.txt", "과거특정프로젝트 수치 7777을 논의함")]),
        extraction_message([("minutes.txt", "## 의사결정\n- 규칙을 적용함")]),
        extraction_message([("current.txt", "현재 회의 내용")]),
    )
    model = FakeModel(
        json.dumps(
            {
                "profile_name": "간결한 실행형",
                "section_order": ["주요 논의", "의사결정", "후속 조치"],
                "section_heading_style": "plain",
                "body_style": "bullets",
                "sentence_ending": "~함",
                "detail_level": "compact",
                "speaker_attribution": "selective",
                "decision_format": "결정 내용을 한 줄로 기록",
                "action_item_format": "[담당자]: [조치] / [기한]",
                "selection_rules": ["결론과 실행 항목을 기록"],
                "omission_rules": ["인사말과 반복을 제외"],
                "representative_phrasing": ["[안건]을 적용하기로 함"],
                "confidence": 0.91,
                "warnings": [],
            },
            ensure_ascii=False,
        )
    )
    result = asyncio.run(style_module.analyze_meeting_minutes_style(request_result, model))
    profile = result["style_profile"]
    assert profile["body_style"] == "bullets"
    assert profile["confidence"] == 0.91
    assert profile["historical_facts_retained"] is False
    assert "과거특정프로젝트" not in json.dumps(profile, ensure_ascii=False)
    assert model.messages


def test_draft_prompt_uses_current_facts_but_not_historical_examples() -> None:
    draft_module = load_module("meeting_minutes_draft_prompt_test", NODE_PATHS["draft"])
    request = {
        "request": {
            "example_pairs": [
                {
                    "transcript": "과거특정프로젝트 7777",
                    "minutes": "과거 결정을 승인함",
                }
            ],
            "current_transcript": {"file_name": "current.txt", "text": "현재 시범 범위를 운영팀으로 결정함"},
            "additional_instructions": "후속 조치 위주",
            "meeting_metadata": {"title": "현재 회의", "output_language": "ko"},
        }
    }
    style = {
        "style_profile": {
            "profile_version": "meeting-minutes-style-v1",
            "section_order": ["의사결정", "후속 조치"],
            "confidence": 0.8,
        }
    }
    message = draft_module.build_draft_prompt(request, style)
    assert "현재 시범 범위를 운영팀으로 결정함" in message.content
    assert "후속 조치 위주" in message.content
    assert "과거특정프로젝트" not in message.content
    assert "과거 결정을 승인함" not in message.content


def test_draft_and_reviewer_return_minutes_and_quality_report() -> None:
    draft_module = load_module("meeting_minutes_draft_runtime_test", NODE_PATHS["draft"])
    review_module = load_module("meeting_minutes_review_runtime_test", NODE_PATHS["review"])
    request = {
        "request": {
            "current_transcript": {
                "file_name": "current.txt",
                "text": "운영팀 주간 회의만 시범 적용하고 담당자A가 월요일까지 목록을 정리함",
            },
            "additional_instructions": "의사결정과 후속 조치 위주",
            "meeting_metadata": {"title": "자동 회의록 시범 회의", "output_language": "ko"},
        }
    }
    style = {
        "style_profile": {
            "profile_version": "meeting-minutes-style-v1",
            "section_order": ["의사결정", "후속 조치"],
            "body_style": "bullets",
            "confidence": 0.9,
        }
    }
    draft_model = FakeModel("# 자동 회의록 시범 회의\n\n## 의사결정\n- 운영팀 주간 회의만 적용함")
    draft = asyncio.run(draft_module.write_meeting_minutes_draft(request, style, draft_model))
    review_model = FakeModel(
        json.dumps(
            {
                "final_minutes": (
                    "# 자동 회의록 시범 회의\n\n"
                    "## 의사결정\n- 운영팀 주간 회의만 시범 적용함\n\n"
                    "## 후속 조치\n- 담당자A: 대상 목록 정리 / 월요일"
                ),
                "corrections": ["누락된 후속 조치 추가"],
                "remaining_checks": [],
            },
            ensure_ascii=False,
        )
    )
    result = asyncio.run(review_module.review_meeting_minutes(request, style, draft, review_model))
    assert "담당자A" in result["final_minutes"]
    assert result["quality_report"]["review_status"] == "completed"
    assert result["quality_report"]["human_review_required"] is True
    assert "현재 시범" not in json.dumps(result["quality_report"], ensure_ascii=False)


def test_reviewer_extracts_direct_minutes_from_structured_content_variants() -> None:
    review_module = load_module("meeting_minutes_review_variants_test", NODE_PATHS["review"])
    expected_minutes = "# 회의록\n\n## 의사결정\n- 운영 시범을 진행함"
    payload = {
        "final_minutes": expected_minutes,
        "corrections": ["표현 정리"],
        "remaining_checks": ["담당자 확인"],
    }
    variants = [
        payload,
        {"type": "json", "value": payload},
        {"type": "text", "text": json.dumps(payload, ensure_ascii=False)},
        json.dumps(json.dumps(payload, ensure_ascii=False), ensure_ascii=False),
        "검토 결과입니다.\n" + json.dumps(payload, ensure_ascii=False),
    ]
    for value in variants:
        parsed = review_module.parse_review_result(type("Response", (), {"content": value})())
        assert parsed["final_minutes"] == expected_minutes
        assert not parsed["final_minutes"].lstrip().startswith("{")


def test_draft_and_reviewer_restore_flattened_markdown_action_table() -> None:
    draft_module = load_module("meeting_minutes_draft_table_test", NODE_PATHS["draft"])
    review_module = load_module("meeting_minutes_review_table_test", NODE_PATHS["review"])
    flattened = (
        "# 자동 회의록 운영 시범\n\n"
        "후속 조치 | 담당 | 조치 | 기한 | | --- | --- | --- | "
        "| 문과장 | 평가표와 기준 회의록 준비 | 2026-06-22 | "
        "| 조책임 | 운영 서버 임시 파일 정리, 로그 설정, 실패 알림 확인 | 2026-06-23 | "
        "| 임대리 | 기본 추가 지시문과 사용자 검토 체크리스트 준비 | 2026-06-22 | "
        "| 송매니저 | 시범 참여자 확정 및 30분 교육 완료 | 2026-06-23 | "
        "| 서팀장 | 운영 승인 요청 진행 및 문서관리 담당 부서 협의 | 2026-06-22 |"
    )
    expected_table = (
        "## 후속 조치\n\n"
        "| 담당 | 조치 | 기한 |\n"
        "| --- | --- | --- |\n"
        "| 문과장 | 평가표와 기준 회의록 준비 | 2026-06-22 |\n"
        "| 조책임 | 운영 서버 임시 파일 정리, 로그 설정, 실패 알림 확인 | 2026-06-23 |\n"
        "| 임대리 | 기본 추가 지시문과 사용자 검토 체크리스트 준비 | 2026-06-22 |\n"
        "| 송매니저 | 시범 참여자 확정 및 30분 교육 완료 | 2026-06-23 |\n"
        "| 서팀장 | 운영 승인 요청 진행 및 문서관리 담당 부서 협의 | 2026-06-22 |"
    )

    draft_minutes = draft_module._clean_minutes(flattened)
    assert expected_table in draft_minutes
    assert "| | ---" not in draft_minutes

    parsed = review_module.parse_review_result(
        type(
            "Response",
            (),
            {
                "content": {
                    "final_minutes": flattened,
                    "corrections": [],
                    "remaining_checks": [],
                }
            },
        )()
    )
    assert expected_table in parsed["final_minutes"]
    assert "| | ---" not in parsed["final_minutes"]
    assert parsed["final_minutes"].count("\n| ") >= 7


def test_all_internal_sources_compile_into_langflow_192_templates() -> None:
    expected_classes = {
        "request": "MeetingMinutesRequestBuilder",
        "style": "MeetingMinutesStyleAnalyzer",
        "draft": "MeetingMinutesDraftWriter",
        "review": "MeetingMinutesReviewer",
    }
    for key, path in NODE_PATHS.items():
        code = path.read_text(encoding="utf-8")
        component_class = eval_custom_component_code(code)
        config, instance = create_component_template(
            {"code": code, "output_types": []},
            module_name=f"agent_ground.meeting_minutes.tests.{key}",
        )
        assert component_class.__name__ == expected_classes[key]
        assert instance.__class__.__name__ == expected_classes[key]
        assert config["field_order"]
        assert config["outputs"]


def test_internal_nodes_remain_standalone_single_file_components() -> None:
    project_local_prefixes = ("agent_ground", "components", "flows", "nodes", "scripts")
    for path in NODE_PATHS.values():
        source = path.read_text(encoding="utf-8")
        syntax_tree = ast.parse(source, filename=str(path))
        for node in ast.walk(syntax_tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            assert node.level == 0, f"{path.name}에 상대 import가 있습니다."
            module_name = str(node.module or "")
            assert not module_name.startswith(project_local_prefixes), (
                f"{path.name}이 프로젝트 내부 모듈 {module_name}에 의존합니다."
            )


def test_loader_executed_methods_keep_async_helper_factories_visible() -> None:
    request = {
        "request": {
            "example_pairs": [
                {
                    "pair_id": 1,
                    "transcript": "회의에서 적용 범위와 담당자를 논의함",
                    "minutes": "## 의사결정\n- 적용 범위를 확정함",
                }
            ],
            "current_transcript": {
                "file_name": "current.txt",
                "text": "운영팀 회의에 시범 적용하고 담당자A가 월요일까지 목록을 준비함",
            },
            "additional_instructions": "의사결정과 담당자·기한 위주",
            "meeting_metadata": {"title": "자동 회의록 시범 회의", "output_language": "ko"},
        }
    }

    style_class = eval_custom_component_code(NODE_PATHS["style"].read_text(encoding="utf-8"))
    style_holder = SimpleNamespace(
        request=request,
        model=FakeModel(
            json.dumps(
                {
                    "profile_name": "실행 중심",
                    "section_order": ["의사결정", "후속 조치"],
                    "body_style": "bullets",
                    "action_item_format": "[담당자]: [조치] / [기한]",
                    "selection_rules": ["결정과 실행 항목을 기록"],
                    "omission_rules": ["잡담을 제외"],
                    "confidence": 0.9,
                },
                ensure_ascii=False,
            )
        ),
        status=None,
    )
    style_data = asyncio.run(style_class.build_style_profile(style_holder))
    assert style_data.data["style_profile"]["profile_name"] == "실행 중심"

    draft_class = eval_custom_component_code(NODE_PATHS["draft"].read_text(encoding="utf-8"))
    draft_holder = SimpleNamespace(
        request=request,
        style_profile=style_data,
        model=FakeModel("# 자동 회의록 시범 회의\n\n## 의사결정\n- 운영팀 회의에 시범 적용함"),
        status=None,
    )
    draft_message = asyncio.run(draft_class.build_draft(draft_holder))
    assert "운영팀 회의" in draft_message.text

    review_class = eval_custom_component_code(NODE_PATHS["review"].read_text(encoding="utf-8"))
    review_holder = SimpleNamespace(
        request=request,
        style_profile=style_data,
        draft=draft_message.text,
        model=FakeModel(
            {
                "final_minutes": (
                    "# 자동 회의록 시범 회의\n\n"
                    "## 의사결정\n- 운영팀 회의에 시범 적용함\n\n"
                    "## 후속 조치\n- 담당자A: 목록 준비 / 월요일"
                ),
                "corrections": [],
                "remaining_checks": [],
            }
        ),
        status=None,
    )
    review_holder._run_once = lambda: review_class._run_once(review_holder)
    final_message = asyncio.run(review_class.build_final_minutes(review_holder))
    quality_data = asyncio.run(review_class.build_quality_report(review_holder))
    assert "담당자A" in final_message.text
    assert quality_data.data["review_status"] == "completed"


def test_flow_json_sources_edges_and_bundle_are_current() -> None:
    flow = json.loads((FLOW_ROOT / "meeting_minutes_writer_flow.json").read_text(encoding="utf-8"))
    nodes = flow["data"]["nodes"]
    edges = flow["data"]["edges"]
    assert flow["name"] == "meeting_minutes_writer_flow"
    assert flow["last_tested_version"] == "1.9.2"
    assert len(nodes) == 13
    assert len(edges) == 14
    node_by_id = {node["id"]: node for node in nodes}
    assert node_by_id["LanguageModelComponent-minutesWriter"]["data"]["node"]["template"]["model"]["value"] == ""
    assert node_by_id["MeetingMinutesReviewer-minutesWriter"]["data"]["selected_output"] == "final_minutes"

    source_map = {
        "MeetingMinutesRequestBuilder-minutesWriter": NODE_PATHS["request"],
        "MeetingMinutesStyleAnalyzer-minutesWriter": NODE_PATHS["style"],
        "MeetingMinutesDraftWriter-minutesWriter": NODE_PATHS["draft"],
        "MeetingMinutesReviewer-minutesWriter": NODE_PATHS["review"],
    }
    for node_id, source_path in source_map.items():
        embedded = node_by_id[node_id]["data"]["node"]["template"]["code"]["value"]
        assert embedded == source_path.read_text(encoding="utf-8")
    assert sum(1 for edge in edges if edge["target"] == "MeetingMinutesReviewer-minutesWriter") == 4

    bundle = json.loads((ROOT / "flows" / "00_AGENT_GROUND_ALL_FLOWS.json").read_text(encoding="utf-8"))
    assert [item["name"] for item in bundle["flows"]] == [
        "html_flow_0624",
        "enterprise_document_rag_flow",
        "skill_based_agent_flow",
        "ppt_reference_html_flow",
        "drm_document_text_extraction_flow",
        "meeting_minutes_writer_flow",
        "business_agent_design_complete",
    ]


def test_manifest_refs_and_samples_are_safe() -> None:
    manifest = json.loads((FLOW_ROOT / "manifest.json").read_text(encoding="utf-8"))
    refs = json.loads((FLOW_ROOT / "component_refs.json").read_text(encoding="utf-8"))
    internal = json.loads((FLOW_ROOT / "internal_nodes.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "user_testing"
    assert manifest["source_export_version"] == "1.9.2"
    assert refs["components"] == [{"id": "drm_document_text_extractor", "version": "0.6.0"}]
    assert len(internal["nodes"]) == 4
    all_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in FLOW_ROOT.rglob("*")
        if (
            path.is_file()
            and path.suffix.lower() in {".md", ".txt", ".json", ".py"}
            and "tests" not in path.relative_to(FLOW_ROOT).parts
        )
    )
    assert "Bearer ey" not in all_text
    assert "password=" not in all_text.lower()
    assert "http://internal" not in all_text.lower()
