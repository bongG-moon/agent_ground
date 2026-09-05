from __future__ import annotations

import copy
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "components" / "work_definition" / "46_f10_numbered_chat_answer_parser.py"


def _install_lfx_stubs() -> dict[str, types.ModuleType | None]:
    names = ("lfx", "lfx.custom", "lfx.io", "lfx.schema")
    originals = {name: sys.modules.get(name) for name in names}

    class Component:
        def __init__(self):
            self.stopped_outputs = []

        def stop(self, output_name):
            self.stopped_outputs.append(output_name)

    class Port:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.__dict__.update(kwargs)

    class Data:
        def __init__(self, data=None):
            self.data = data or {}

    modules = {name: types.ModuleType(name) for name in names}
    modules["lfx.custom"].Component = Component
    modules["lfx.io"].DataInput = Port
    modules["lfx.io"].MessageTextInput = Port
    modules["lfx.io"].Output = Port
    modules["lfx.schema"].Data = Data
    sys.modules.update(modules)
    return originals


def _restore_modules(originals: dict[str, types.ModuleType | None]) -> None:
    for name, module in originals.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def _load_component():
    spec = importlib.util.spec_from_file_location("f10_numbered_chat_answer_parser_test", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ORIGINALS = _install_lfx_stubs()
try:
    MODULE = _load_component()
finally:
    _restore_modules(_ORIGINALS)


def _batch(*, questions=None):
    return {
        "schema_version": "clarification-question-batch/v1",
        "batch_id": "qb-numbered-1",
        "work_definition_id": "wd-numbered-1",
        "tenant_id": "team-a",
        "owner_id": "employee-1004",
        "session_id": "session-numbered-1",
        "channel_mode": "native_hitl",
        "revision": 4,
        "round_number": 2,
        "status": "WAITING_ANSWER",
        "questions": questions
        or [
            {
                "question_id": "q unsafe/id?1",
                "text": "업무 완료 결과는 무엇인가요?",
                "target_paths": ["goal"],
                "answer_type": "text",
                "choices": [],
                "required": True,
            },
            {
                "question_id": "q-choice",
                "text": "승인 방식은 무엇인가요?",
                "target_paths": ["automation_intent"],
                "answer_type": "single_choice",
                "choices": ["자동", "수동"],
                "required": True,
            },
            {
                "question_id": "q-many",
                "text": "사용할 채널은 무엇인가요?",
                "target_paths": ["inputs"],
                "answer_type": "multi_choice",
                "choices": ["메일", "JIRA", "Outlook"],
                "required": True,
            },
            {
                "question_id": "q-approval",
                "text": "승인이 필요한가요?",
                "target_paths": ["risks_controls"],
                "answer_type": "boolean",
                "choices": [],
                "required": True,
            },
        ],
    }


class _Graph:
    def __init__(self):
        self.exclusions = []

    def exclude_branches_conditionally(self, vertex_id, output_names):
        self.exclusions.append({"vertex_id": vertex_id, "output_names": list(output_names)})


class NumberedChatAnswerParserTests(unittest.TestCase):
    def test_numbered_multiline_reply_builds_component_39_native_submission(self):
        result = MODULE.build_chat_answer_submission(
            _batch(),
            """질문 묶음: qb-numbered-1
1번: 검토 완료된 주간 업무보고 포털 링크입니다.
2. 수동
3) 메일, JIRA
4번: 예""",
            actor_id="employee-1004",
            request_id="request-numbered-1",
            now_utc="2026-09-02T01:02:03Z",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "ANSWER_SUBMITTED")
        self.assertEqual(result["route"], "branch_submit_answers")
        native = result["answer_submission"]
        self.assertEqual(native["schema_version"], "native-clarification-answer-submission/v1")
        self.assertEqual(native["action_id"], "submit_answers")
        self.assertEqual(native["request_id"], "request-numbered-1")
        self.assertEqual(native["owner_id"], "employee-1004")
        self.assertEqual(native["submitted_at"], "2026-09-02T01:02:03Z")
        self.assertEqual(
            [item["value"] for item in native["answers"]],
            ["검토 완료된 주간 업무보고 포털 링크입니다.", "수동", ["메일", "JIRA"], True],
        )
        # No datetime or other non-JSON data may reach a Langflow Data output.
        json.dumps(result, ensure_ascii=False, allow_nan=False)

    def test_exact_question_id_and_multiline_answer_are_supported_without_fuzzy_matching(self):
        questions = _batch()["questions"][:2]
        result = MODULE.build_chat_answer_submission(
            _batch(questions=questions),
            """질문 ID [q unsafe/id?1]: 보고 포털 링크를 만듭니다.
원본 메일 제목도 함께 남깁니다.
질문 ID [q-choice]: 자동""",
            now_utc="2026-09-02T00:00:00Z",
        )

        self.assertTrue(result["ok"])
        answers = result["answer_submission"]["answers"]
        self.assertEqual(answers[0]["question_id"], "q unsafe/id?1")
        self.assertEqual(answers[0]["value"], "보고 포털 링크를 만듭니다.\n원본 메일 제목도 함께 남깁니다.")
        self.assertEqual(answers[1]["value"], "자동")

    def test_missing_required_answer_fails_closed_before_component_39(self):
        result = MODULE.build_chat_answer_submission(
            _batch(),
            "1번: 링크\n2번: 수동\n3번: 메일, JIRA",
            now_utc="2026-09-02T00:00:00Z",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["route"], "blocked_path")
        self.assertEqual(result["error"]["code"], "ANSWER_REQUIRED_VALUE_MISSING")
        self.assertNotIn("answer_submission", result)

    def test_unknown_or_duplicate_labels_are_not_silently_mapped(self):
        unknown = MODULE.build_chat_answer_submission(
            _batch(),
            "5번: 잘못된 질문 번호",
            now_utc="2026-09-02T00:00:00Z",
        )
        self.assertFalse(unknown["ok"])
        self.assertEqual(unknown["error"]["code"], "ANSWER_LABEL_UNKNOWN")
        self.assertEqual(unknown["error"]["details"]["line_number"], 1)

        duplicate = MODULE.build_chat_answer_submission(
            _batch(),
            "1번: 첫 답변\n1번: 두 번째 답변",
            now_utc="2026-09-02T00:00:00Z",
        )
        self.assertFalse(duplicate["ok"])
        self.assertEqual(duplicate["error"]["code"], "ANSWER_LABEL_DUPLICATED")

    def test_actor_identity_mismatch_is_rejected(self):
        result = MODULE.build_chat_answer_submission(
            _batch(),
            "1번: 링크\n2번: 수동\n3번: 메일\n4번: 예",
            actor_id="employee-other",
            now_utc="2026-09-02T00:00:00Z",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "ACTION_ACTOR_MISMATCH")

    def test_oversized_chat_reply_is_rejected_without_silent_truncation(self):
        result = MODULE.build_chat_answer_submission(
            _batch(),
            "1번: " + ("가" * MODULE.MAX_INPUT_CHARS),
            now_utc="2026-09-02T00:00:00Z",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "ANSWER_TEXT_TOO_LARGE")

    def test_component_exposes_one_submit_branch_or_one_blocked_branch(self):
        component = MODULE.F10NumberedChatAnswerParserComponent()
        component._id = "node-46"
        component.graph = _Graph()
        component.clarification_batch = copy.deepcopy(_batch())
        component.answer_text = "1번: 링크\n2번: 수동\n3번: 메일\n4번: 예"
        component.actor_id = "employee-1004"
        component.request_id = "request-component-46"
        component.now_utc = "2026-09-02T00:00:00Z"
        component._current_output = "submit_trigger"

        trigger = component.route_submission().data

        self.assertTrue(trigger["ok"])
        self.assertEqual(trigger["route"], "branch_submit_answers")
        self.assertIn("blocked_path", component.stopped_outputs)
        self.assertEqual(
            component.graph.exclusions,
            [{"vertex_id": "node-46", "output_names": ["blocked_path"]}],
        )
        component._current_output = "blocked_path"
        self.assertEqual(component.route_submission().data, {})

    def test_component_template_matches_loader_and_component_39_contract(self):
        inputs = {item.name: item for item in MODULE.F10NumberedChatAnswerParserComponent.inputs}
        self.assertEqual(set(inputs), {"clarification_batch", "answer_text", "actor_id", "request_id", "now_utc"})
        self.assertEqual(
            [item.name for item in MODULE.F10NumberedChatAnswerParserComponent.outputs],
            ["answer_submission", "submit_trigger", "blocked_path"],
        )
        self.assertTrue(inputs["clarification_batch"].required)
        self.assertTrue(inputs["answer_text"].required)


if __name__ == "__main__":
    unittest.main()
