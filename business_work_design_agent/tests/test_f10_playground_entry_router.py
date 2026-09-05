from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "components" / "work_definition" / "49_f10_playground_entry_router.py"


def _install_lfx_stubs() -> dict[str, types.ModuleType | None]:
    names = ("lfx", "lfx.custom", "lfx.io", "lfx.schema")
    originals = {name: sys.modules.get(name) for name in names}

    class Component:
        def __init__(self):
            self.stopped_outputs: list[str] = []

        def stop(self, output_name: str) -> None:
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
    spec = importlib.util.spec_from_file_location("f10_playground_entry_router_test", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ORIGINALS = _install_lfx_stubs()
try:
    MODULE = _load_component()
finally:
    _restore_modules(_ORIGINALS)


class _Graph:
    def __init__(self):
        self.exclusions: list[tuple[str, list[str]]] = []

    def exclude_branches_conditionally(self, vertex_id: str, output_names: list[str]) -> None:
        self.exclusions.append((vertex_id, list(output_names)))


class F10PlaygroundEntryRouterTests(unittest.TestCase):
    def test_blank_input_and_human_readable_start_command_begin_new_work(self):
        blank = MODULE.route_f10_playground_entry("   ")
        start_command = MODULE.route_f10_playground_entry("새 업무 시작")

        self.assertTrue(blank["ok"])
        self.assertEqual(blank["route"], "new_work_path")
        self.assertEqual(blank["status"], "START_NEW_WORK")
        self.assertTrue(start_command["ok"])
        self.assertEqual(start_command["route"], "new_work_path")

    def test_numbered_or_batch_header_reply_resumes_pending_questions(self):
        numbered = MODULE.route_f10_playground_entry("1번: 팀장 승인이 필요합니다.")
        batch_header = MODULE.route_f10_playground_entry("질문 묶음: qb-weekly-report-1")

        self.assertTrue(numbered["ok"])
        self.assertEqual(numbered["route"], "answer_path")
        self.assertEqual(numbered["status"], "CHAT_ANSWER_RECEIVED")
        self.assertEqual(numbered["answer_text"], "1번: 팀장 승인이 필요합니다.")
        self.assertTrue(batch_header["ok"])
        self.assertEqual(batch_header["route"], "answer_path")

    def test_unrelated_text_fails_closed_with_human_readable_guidance(self):
        result = MODULE.route_f10_playground_entry("안녕하세요, 업무 흐름을 만들어 주세요")

        self.assertFalse(result["ok"])
        self.assertEqual(result["route"], "blocked_path")
        self.assertEqual(result["error"]["code"], "F10_ENTRY_MODE_UNCLEAR")
        self.assertIn("새 업무 시작", result["error"]["message"])
        self.assertIn("1번:", result["error"]["message"])

    def test_component_excludes_every_nonselected_grouped_branch(self):
        component = MODULE.F10PlaygroundEntryRouterComponent()
        component._id = "playground-entry-router-49"
        component.graph = _Graph()
        component._current_output = "answer_path"
        component._entry_result = {
            "ok": True,
            "status": "CHAT_ANSWER_RECEIVED",
            "route": "answer_path",
            "answer_text": "1번: 답변",
        }

        result = component.route_entry()

        self.assertTrue(result.data["ok"])
        self.assertEqual(result.data["route"], "answer_path")
        self.assertEqual(component.stopped_outputs, ["new_work_path", "blocked_path"])
        self.assertEqual(
            component.graph.exclusions,
            [("playground-entry-router-49", ["new_work_path", "blocked_path"])],
        )
        component._current_output = "new_work_path"
        self.assertEqual(component.route_entry().data, {})

    def test_component_template_exposes_only_the_three_entry_modes(self):
        self.assertEqual(
            [item.name for item in MODULE.F10PlaygroundEntryRouterComponent.outputs],
            ["new_work_path", "answer_path", "blocked_path"],
        )
        self.assertTrue(all(item.group_outputs for item in MODULE.F10PlaygroundEntryRouterComponent.outputs))


if __name__ == "__main__":
    unittest.main()
