from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "components" / "work_definition" / "48_f10_chat_answer_next_router.py"


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
    spec = importlib.util.spec_from_file_location("f10_chat_answer_next_router_test", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ORIGINALS = _install_lfx_stubs()
try:
    MODULE = _load_component()
finally:
    _restore_modules(_ORIGINALS)


def _commit(*, route="next_round_path", next_round_number=2, ok=True):
    return {
        "ok": ok,
        "status": "NEEDS_CLARIFICATION" if ok else "BLOCKED",
        "route": route,
        "next_round_number": next_round_number,
        "trace_id": "trace-chat-answer-router",
    }


class _Graph:
    def __init__(self):
        self.exclusions = []

    def exclude_branches_conditionally(self, vertex_id, output_names):
        self.exclusions.append((vertex_id, list(output_names)))


class F10ChatAnswerNextRouterTests(unittest.TestCase):
    def test_next_round_two_only_selects_the_second_question_planner(self):
        result = MODULE.route_chat_answer_commit(_commit(next_round_number=2))

        self.assertTrue(result["ok"])
        self.assertEqual(result["route"], "round2_path")

    def test_next_round_three_only_selects_the_third_question_planner(self):
        result = MODULE.route_chat_answer_commit(_commit(next_round_number=3))

        self.assertTrue(result["ok"])
        self.assertEqual(result["route"], "round3_path")

    def test_review_result_is_rejected_because_it_goes_directly_to_the_joiner(self):
        result = MODULE.route_chat_answer_commit(_commit(route="review_path", next_round_number=None))

        self.assertFalse(result["ok"])
        self.assertEqual(result["route"], "blocked_path")
        self.assertEqual(result["error"]["code"], "CHAT_ANSWER_NEXT_ROUTE_INVALID")

    def test_failed_or_invalid_commit_fails_closed(self):
        upstream_failure = MODULE.route_chat_answer_commit(
            {"ok": False, "status": "BLOCKED", "error": {"code": "ANSWER_COMMIT_FAILED"}, "trace_id": "trace-upstream"}
        )
        invalid_round = MODULE.route_chat_answer_commit(_commit(next_round_number=4))

        self.assertFalse(upstream_failure["ok"])
        self.assertEqual(upstream_failure["route"], "blocked_path")
        self.assertEqual(upstream_failure["error"]["code"], "CHAT_ANSWER_COMMIT_FAILED")
        self.assertEqual(upstream_failure["error"]["details"]["upstream_code"], "ANSWER_COMMIT_FAILED")
        self.assertFalse(invalid_round["ok"])
        self.assertEqual(invalid_round["route"], "blocked_path")
        self.assertEqual(invalid_round["error"]["code"], "CHAT_ANSWER_NEXT_ROUND_INVALID")

    def test_json_commit_payload_is_accepted(self):
        result = MODULE.route_chat_answer_commit(json.dumps(_commit(next_round_number=2), ensure_ascii=False))

        self.assertTrue(result["ok"])
        self.assertEqual(result["route"], "round2_path")

    def test_component_excludes_every_nonselected_branch(self):
        component = MODULE.F10ChatAnswerNextRouterComponent()
        component._id = "next-router-48"
        component.graph = _Graph()
        component._current_output = "round3_path"
        component._router_result = _commit(next_round_number=3)
        component._router_result["route"] = "round3_path"

        result = component.route_next()

        self.assertTrue(result.data["ok"])
        self.assertEqual(
            component.stopped_outputs,
            ["round2_path", "blocked_path"],
        )
        self.assertEqual(
            component.graph.exclusions,
            [("next-router-48", ["round2_path", "blocked_path"])],
        )
        component._current_output = "round2_path"
        self.assertEqual(component.route_next().data, {})


if __name__ == "__main__":
    unittest.main()
