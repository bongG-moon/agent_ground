from __future__ import annotations

import ast
import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ROOT / "components" / "work_definition"


def _install_lfx_stubs() -> dict[str, types.ModuleType | None]:
    names = ("lfx", "lfx.custom", "lfx.io", "lfx.schema")
    originals = {name: sys.modules.get(name) for name in names}

    class Component:
        pass

    class Port:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.__dict__.update(kwargs)

    class Data:
        def __init__(self, data=None):
            self.data = data or {}

    class Message:
        def __init__(self, text=""):
            self.text = text

    modules = {name: types.ModuleType(name) for name in names}
    modules["lfx.custom"].Component = Component
    modules["lfx.io"].DataInput = Port
    modules["lfx.io"].Output = Port
    modules["lfx.schema"].Data = Data
    modules["lfx.schema"].Message = Message
    sys.modules.update(modules)
    return originals


def _restore_modules(originals: dict[str, types.ModuleType | None]) -> None:
    for name, module in originals.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ORIGINALS = _install_lfx_stubs()
try:
    JOINER = _load("f10_review_entry_joiner", COMPONENTS / "40_f10_review_entry_joiner.py")
    TERMINAL = _load("f10_terminal_result_message", COMPONENTS / "41_f10_terminal_result_message.py")
finally:
    _restore_modules(_ORIGINALS)


def _work() -> dict[str, object]:
    return {
        "work_definition_id": "wd-1",
        "tenant_id": "tenant-1",
        "owner_id": "owner-1",
        "session_id": "session-1",
        "channel_mode": "native_hitl",
        "revision": 0,
        "status": "READY_FOR_REVIEW",
    }


class F10CompactHelperTests(unittest.TestCase):
    def test_joiner_selects_the_only_successful_review_result(self):
        result = JOINER.join_f10_review_entries(
            round2_review={"ok": True, "status": "READY_FOR_REVIEW", "work_definition": _work()}
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["route"], "review_work_definition")
        self.assertEqual(result["selected_input"], "round2_review")
        self.assertEqual(result["work_definition"]["work_definition_id"], "wd-1")

    def test_joiner_accepts_a_valid_direct_work_definition(self):
        result = JOINER.join_f10_review_entries(round1_answer_review=_work())
        self.assertTrue(result["ok"])
        self.assertEqual(result["selected_input"], "round1_answer_review")

    def test_joiner_accepts_the_numbered_chat_answer_review_result(self):
        result = JOINER.join_f10_review_entries(
            chat_answer_review={"ok": True, "status": "READY_FOR_REVIEW", "work_definition": _work()}
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["selected_input"], "chat_answer_review")

    def test_joiner_stops_when_every_optional_input_is_empty(self):
        result = JOINER.join_f10_review_entries()
        self.assertEqual(result["route"], "no_input")
        self.assertIsNone(result["ok"])

    def test_joiner_blocks_explicit_failure_and_ambiguous_successes(self):
        failed = JOINER.join_f10_review_entries(initial_review={"ok": False, "status": "BLOCKED"})
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["error"]["code"], "F10_REVIEW_ENTRY_UPSTREAM_FAILED")

        ambiguous = JOINER.join_f10_review_entries(
            initial_review={"ok": True, "work_definition": _work()},
            round1_review={"ok": True, "work_definition": _work()},
        )
        self.assertFalse(ambiguous["ok"])
        self.assertEqual(ambiguous["error"]["code"], "F10_REVIEW_ENTRY_AMBIGUOUS")

    def test_joiner_inputs_are_visible_and_outputs_are_grouped(self):
        component = JOINER.F10ReviewEntryJoinerComponent
        self.assertEqual(component.name, "F10ReviewEntryJoiner")
        self.assertEqual(len(component.inputs), 10)
        self.assertIn("chat_answer_review", {item.name for item in component.inputs})
        self.assertTrue(all(item.advanced is False for item in component.inputs))
        self.assertEqual({item.name for item in component.outputs}, {"review_work_definition", "blocked_path"})
        self.assertTrue(all(item.group_outputs for item in component.outputs))

    def test_terminal_message_is_safe_and_uses_first_supplied_input(self):
        text = TERMINAL.render_f10_terminal_result_message(
            [
                {
                    "status": "BLOCKED",
                    "error": {
                        "code": "UPSTREAM_TIMEOUT",
                        "message": "token=abc123 {\"password\": \"def456\"} MongoDB URI mongodb+srv://user:password@example.test/db",
                    },
                },
                {"status": "REJECTED", "message": "should not win"},
            ]
        )
        self.assertIn("업무 정의 처리", text)
        self.assertIn("UPSTREAM_TIMEOUT", text)
        self.assertNotIn("abc123", text)
        self.assertNotIn("def456", text)
        self.assertNotIn("mongodb+srv://", text)
        self.assertNotIn("should not win", text)

    def test_terminal_message_handles_no_input(self):
        self.assertEqual(TERMINAL.render_f10_terminal_result_message(), "표시할 최종 처리 결과가 아직 없습니다.")

    def test_terminal_message_shows_question_card_input_error(self):
        text = TERMINAL.render_f10_terminal_result_message(
            [
                {
                    "status": "BLOCKED",
                    "error": {"code": "ANSWER_REQUIRED_VALUE_MISSING", "message": "필수 질문의 답변이 비어 있습니다."},
                }
            ]
        )
        self.assertIn("질문 카드", text)
        self.assertIn("ANSWER_REQUIRED_VALUE_MISSING", text)

    def test_terminal_message_preserves_copyable_numbered_chat_guidance(self):
        text = TERMINAL.render_f10_terminal_result_message(
            [
                {
                    "ok": True,
                    "status": "WAITING_CHAT_ANSWER",
                    "chat_answer_guidance": (
                        "[질문과 입력 안내]\n"
                        "질문 1의 안내입니다.\n\n"
                        "[복사용 답변 양식 — 아래 블록만 복사]\n"
                        "질문 묶음: qb-1\n\n"
                        "1번: [1번 답변을 입력하세요]"
                    ),
                }
            ]
        )
        self.assertIn("[질문과 입력 안내]", text)
        self.assertIn("[복사용 답변 양식", text)
        self.assertIn("질문 묶음: qb-1", text)
        self.assertIn("\n1번: [1번 답변을 입력하세요]", text)

    def test_terminal_message_has_safe_authentication_and_mongodb_explanations(self):
        authentication = TERMINAL.render_f10_terminal_result_message(
            [{"status": "BLOCKED", "error": {"code": "TRUSTED_GATEWAY_SUBJECT_REQUIRED", "message": "subject=secret"}}]
        )
        mongodb = TERMINAL.render_f10_terminal_result_message(
            [{"status": "BLOCKED", "error": {"code": "DESIGN_INVOCATION_MONGODB_UNAVAILABLE", "message": "mongodb://user:secret@host"}}]
        )
        self.assertIn("인증된 사용자", authentication)
        self.assertIn("MongoDB 설정", mongodb)
        self.assertNotIn("secret", authentication)
        self.assertNotIn("mongodb://", mongodb)

    def test_terminal_message_explains_f20_handoff_and_blueprint_contract_failures(self):
        handoff = TERMINAL.render_f10_terminal_result_message(
            [{"status": "BLOCKED", "error": {"code": "F20_REPORT_HANDOFF_FIELDS_INVALID", "message": "handoff invalid"}}]
        )
        blueprint = TERMINAL.render_f10_terminal_result_message(
            [
                {
                    "status": "BLOCKED",
                    "error": {
                        "code": "TERMINAL_BLUEPRINT_BINDING_INVALID",
                        "message": "blueprint binding invalid",
                    },
                }
            ]
        )
        self.assertIn("F20 설계 결과", handoff)
        self.assertIn("F20_REPORT_HANDOFF_FIELDS_INVALID", handoff)
        self.assertIn("노드 연결 또는 입출력", blueprint)
        self.assertIn("TERMINAL_BLUEPRINT_BINDING_INVALID", blueprint)

    def test_terminal_uses_one_list_input_to_avoid_eager_optional_fan_in(self):
        inputs = TERMINAL.F10TerminalResultMessageComponent.inputs
        self.assertEqual([item.name for item in inputs], ["terminal_events"])
        self.assertTrue(inputs[0].is_list)

    def test_new_components_are_standalone(self):
        for path in (
            COMPONENTS / "40_f10_review_entry_joiner.py",
            COMPONENTS / "41_f10_terminal_result_message.py",
        ):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            self.assertFalse(path.read_bytes().startswith(b"\xef\xbb\xbf"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertEqual(node.level, 0)
                    self.assertFalse((node.module or "").startswith("langflow"))


if __name__ == "__main__":
    unittest.main()
