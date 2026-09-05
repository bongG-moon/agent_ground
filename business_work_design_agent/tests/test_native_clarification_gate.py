from __future__ import annotations

import copy
import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "components" / "work_definition" / "42_f10_clarification_answer_gate.py"


def _install_lfx_stubs() -> dict[str, types.ModuleType | None]:
    names = ("lfx", "lfx.custom", "lfx.io", "lfx.schema")
    originals = {name: sys.modules.get(name) for name in names}

    class Component:
        def __init__(self):
            self.stopped_outputs = []

        def stop(self, output_name):
            self.stopped_outputs = getattr(self, "stopped_outputs", []) + [output_name]

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
    spec = importlib.util.spec_from_file_location("native_clarification_gate_test", SOURCE)
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
        "batch_id": "qb-native-1",
        "work_definition_id": "wd-native-1",
        "tenant_id": "team-a",
        "owner_id": "employee-1004",
        "session_id": "session-native-1",
        "channel_mode": "native_hitl",
        "revision": 4,
        "round_number": 2,
        "status": "WAITING_ANSWER",
        "questions": questions
        or [
            {
                "question_id": "q unsafe/id?1",
                "text": "업무가 완료되었다고 판단할 최종 결과는 무엇인가요?",
                "target_paths": ["goal"],
                "answer_type": "text",
                "choices": [],
                "required": True,
                "reason_code": "GOAL_UNKNOWN",
            },
            {
                "question_id": "q unsafe/id?2",
                "text": "업무 시작 조건은 무엇인가요?",
                "target_paths": ["trigger"],
                "answer_type": "text",
                "choices": [],
                "required": True,
                "reason_code": "TRIGGER_UNKNOWN",
            },
        ],
    }


class _Graph:
    def __init__(self):
        self.run_id = "run-native-1"
        self.human_input_decisions = {}
        self.pauses = []
        self.exclusions = []

    def request_pause(self, *, reason, data):
        self.pauses.append({"reason": reason, "data": copy.deepcopy(data)})

    def exclude_branches_conditionally(self, vertex_id, output_names):
        self.exclusions.append({"vertex_id": vertex_id, "output_names": list(output_names)})


class NativeClarificationGateTests(unittest.TestCase):
    def test_pause_is_choice_only_and_preserves_question_mapping(self):
        request = MODULE.build_pause_request(_batch(), component_id="node-42", run_id="run-42")

        self.assertEqual(request["kind"], "node_input")
        # Match the native Human Input resume key exactly.  The batch is
        # carried in the pause payload; adding it to this key breaks 1.11.0
        # resume because the server injects by <component_id>:<run_id>.
        self.assertEqual(request["request_id"], "node-42:run-42")
        # The operational 1.11.0 Human Input implementation accepts choices
        # only.  Dynamic fields would render no input controls in Playground.
        self.assertNotIn("schema", request)
        self.assertEqual(
            request["field_mappings"],
            [
                {"field_name": "answer_01", "question_id": "q unsafe/id?1"},
                {"field_name": "answer_02", "question_id": "q unsafe/id?2"},
            ],
        )
        self.assertEqual(
            [option["action_id"] for option in request["options"]],
            ["continue_to_chat", "skip_additional_input", "cancel"],
        )
        self.assertIn("답변 입력하기", request["prompt"])
        self.assertIn("추가 입력 건너뛰기", request["prompt"])
        # Internal answer_01 mappings remain in the payload, but the person
        # sees only the Korean numbered reply syntax.
        self.assertNotIn("answer_01", request["prompt"])
        self.assertIn("1번: ...", request["prompt"])

    def test_final_round_pause_accepts_and_maps_four_questions(self):
        """Round 3 keeps four stable number mappings without adding form fields."""

        questions = _batch()["questions"] + [
            {
                "question_id": "q unsafe/id?3",
                "text": "필수 승인 기준은 무엇인가요?",
                "target_paths": ["risks_controls"],
                "answer_type": "text",
                "choices": [],
                "required": True,
                "reason_code": "WRITE_APPROVAL_UNKNOWN",
            },
            {
                "question_id": "q unsafe/id?4",
                "text": "실패했을 때의 처리 기준은 무엇인가요?",
                "target_paths": ["exceptions"],
                "answer_type": "text",
                "choices": [],
                "required": True,
                "reason_code": "FAILURE_POLICY_UNKNOWN",
            },
        ]
        batch = _batch(questions=questions)
        batch["round_number"] = 3
        component = MODULE.F10ClarificationAnswerGateComponent()
        component._id = "node-42"
        component.graph = _Graph()
        component.clarification_batch = {"clarification_batch": batch}

        waiting = component.build_submission().data

        self.assertEqual(waiting["status"], "WAITING_ANSWER")
        self.assertEqual(len(component.graph.pauses), 1)
        pause = component.graph.pauses[0]["data"]
        self.assertNotIn("schema", pause)
        self.assertEqual(
            pause["field_mappings"][-1],
            {"field_name": "answer_04", "question_id": "q unsafe/id?4"},
        )
        self.assertNotIn("answer_04", pause["prompt"])
        self.assertIn("4번: ...", pause["prompt"])

    def test_component_pauses_then_returns_numbered_chat_guidance(self):
        component = MODULE.F10ClarificationAnswerGateComponent()
        component._id = "node-42"
        component.graph = _Graph()
        component.clarification_batch = {"clarification_batch": _batch()}

        waiting = component.build_submission().data
        self.assertEqual(waiting["status"], "WAITING_ANSWER")
        self.assertEqual(len(component.graph.pauses), 1)
        pause = component.graph.pauses[0]["data"]
        self.assertEqual(pause["kind"], "node_input")
        self.assertNotIn("schema", pause)

        component.graph.human_input_decisions[pause["request_id"]] = {
            "action_id": "continue_to_chat",
        }
        resumed = component.build_submission().data

        self.assertTrue(resumed["ok"])
        self.assertEqual(resumed["status"], "WAITING_CHAT_ANSWER")
        self.assertEqual(resumed["route"], "branch_continue_chat")
        self.assertIsNone(resumed["answer_submission"])
        self.assertEqual(resumed["chat_request_id"], pause["request_id"])
        self.assertIn("[질문과 입력 안내]", resumed["chat_answer_guidance"])
        self.assertIn("[복사용 답변 양식", resumed["chat_answer_guidance"])
        self.assertIn("질문 묶음: qb-native-1", resumed["chat_answer_guidance"])
        self.assertIn("1번: [1번 답변을 입력하세요]", resumed["chat_answer_guidance"])
        self.assertIn("2번: [2번 답변을 입력하세요]", resumed["chat_answer_guidance"])

    def test_cached_waiting_pause_rechecks_native_request_id_after_resume(self):
        """A restored component must not retain its pre-resume WAITING result."""

        component = MODULE.F10ClarificationAnswerGateComponent()
        component._id = "node-42"
        component.graph = _Graph()
        component.clarification_batch = {"clarification_batch": _batch()}

        waiting = component.build_submission().data
        request_id = waiting["resume"]["request_id"]
        self.assertEqual(request_id, "node-42:run-native-1")
        self.assertEqual(component._answer_gate_result["status"], "WAITING_ANSWER")

        # This mirrors the decision map that Langflow 1.11.0 injects into a
        # restored graph.  The same instance still holds the cached envelope.
        component.graph.human_input_decisions[request_id] = {"action_id": "continue_to_chat"}
        resumed = component.build_submission().data

        self.assertEqual(resumed["status"], "WAITING_CHAT_ANSWER")
        self.assertEqual(resumed["route"], "branch_continue_chat")
        self.assertEqual(resumed["chat_request_id"], request_id)
        self.assertEqual(len(component.graph.pauses), 1)

    def test_skip_additional_input_uses_a_dedicated_branch_without_fake_answers(self):
        """Skip is explicit consent, not a partial Submit or a cancellation."""

        result = MODULE.build_resumed_submission(
            _batch(),
            {"action_id": "skip_additional_input", "values": {"answer_01": "ignored"}},
            request_id="node:run:batch",
            now_utc="2026-08-30T00:00:00Z",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "CLARIFICATION_SKIPPED")
        self.assertEqual(result["route"], "branch_skip_additional_input")
        self.assertIsNone(result["answer_submission"])
        self.assertEqual(result["human_decision"], {"action_id": "skip_additional_input", "values": {}})
        skip = result["skip_submission"]
        self.assertEqual(skip["schema_version"], "native-clarification-skip-submission/v1")
        self.assertEqual(skip["action_id"], "skip_additional_input")
        self.assertEqual(skip["skipped_question_ids"], ["q unsafe/id?1", "q unsafe/id?2"])

    def test_skip_branch_stops_answer_output_and_excludes_other_branches(self):
        component = MODULE.F10ClarificationAnswerGateComponent()
        component._id = "node-42"
        component.graph = _Graph()
        component.clarification_batch = _batch()
        waiting = component.build_submission().data
        request_id = waiting["resume"]["request_id"]
        component.graph.human_input_decisions[request_id] = {"action_id": "skip_additional_input", "values": {}}

        skipped = component.build_submission().data
        component.route_submission()

        self.assertEqual(skipped["route"], "branch_skip_additional_input")
        self.assertIn("branch_continue_chat", component.stopped_outputs)
        self.assertEqual(
            component.graph.exclusions,
            [
                {
                    "vertex_id": "node-42",
                    "output_names": ["branch_continue_chat", "branch_cancel", "blocked_path"],
                }
            ],
        )

    def test_waiting_pause_does_not_emit_a_branch_trigger_payload(self):
        """A checkpoint must not queue both action branches before a user decides."""
        component = MODULE.F10ClarificationAnswerGateComponent()
        component._id = "node-42"
        component.graph = _Graph()
        component.clarification_batch = {"clarification_batch": _batch()}

        waiting_branch_payload = component.route_submission().data

        self.assertEqual(len(component.graph.pauses), 1)
        # This mirrors built-in HumanInput: while paused, each branch output
        # must be blank so stale Submit/Cancel Data cannot both reach 39 after
        # checkpoint resume.
        self.assertEqual(waiting_branch_payload, {})

    def test_cancel_only_uses_cancel_branch_and_stops_answer_output(self):
        component = MODULE.F10ClarificationAnswerGateComponent()
        component._id = "node-42"
        component.graph = _Graph()
        component.clarification_batch = _batch()
        waiting = component.build_submission().data
        request_id = waiting["resume"]["request_id"]
        component.graph.human_input_decisions[request_id] = {"action_id": "cancel", "values": {}}

        cancelled = component.build_submission().data
        self.assertEqual(cancelled["route"], "branch_cancel")
        component.route_submission()
        self.assertIn("branch_continue_chat", component.stopped_outputs)
        self.assertIn("blocked_path", component.stopped_outputs)
        self.assertEqual(
            component.graph.exclusions,
            [
                {
                    "vertex_id": "node-42",
                    "output_names": ["branch_continue_chat", "branch_skip_additional_input", "blocked_path"],
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
