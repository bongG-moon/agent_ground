from __future__ import annotations

import copy
import importlib.util
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "components" / "work_definition" / "39_f10_answer_commit.py"


def _install_lfx_stubs() -> dict[str, types.ModuleType | None]:
    names = ("lfx", "lfx.custom", "lfx.io", "lfx.schema")
    originals = {name: sys.modules.get(name) for name in names}

    class Component:
        def stop(self, output_name):
            return output_name

    class Port:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.__dict__.update(kwargs)

    class Data:
        def __init__(self, data=None):
            self.data = data or {}

    modules = {name: types.ModuleType(name) for name in names}
    modules["lfx.custom"].Component = Component
    for port_name in ("DataInput", "IntInput", "MessageInput", "MessageTextInput", "Output", "SecretStrInput"):
        setattr(modules["lfx.io"], port_name, Port)
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
    spec = importlib.util.spec_from_file_location("f10_answer_commit_test", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ORIGINALS = _install_lfx_stubs()
try:
    MODULE = _load_component()
finally:
    _restore_modules(_ORIGINALS)


class _WriteResult:
    def __init__(self, matched_count=0):
        self.matched_count = matched_count


class _Collection:
    def __init__(self):
        self.documents = []

    @staticmethod
    def _matches(document, query):
        return all(document.get(key) == value for key, value in query.items())

    def find_one(self, query, session=None):
        return next((copy.deepcopy(document) for document in self.documents if self._matches(document, query)), None)

    def insert_one(self, document, session=None):
        self.documents.append(copy.deepcopy(document))
        return types.SimpleNamespace(inserted_id=len(self.documents))

    def replace_one(self, query, document, session=None):
        for index, current in enumerate(self.documents):
            if self._matches(current, query):
                self.documents[index] = copy.deepcopy(document)
                return _WriteResult(1)
        return _WriteResult(0)


class _Database:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, _Collection())


class _Client:
    def __init__(self):
        self.databases = {}
        self.admin = self
        self.closed = False

    def command(self, name):
        if name != "ping":
            raise AssertionError(name)
        return {"ok": 1}

    def __getitem__(self, name):
        return self.databases.setdefault(name, _Database())

    def close(self):
        self.closed = True


def _fact(value, status="confirmed"):
    return {
        "value": value,
        "status": status,
        "evidence_turn_ids": ["turn-initial"],
        "confidence": 1.0 if status == "confirmed" else 0.0,
        "last_updated_revision": 0,
    }


def _confirmed_list(name):
    return [{"id": name, "value": name, "provenance": {"status": "confirmed"}}]


def _work(*, goal_status="confirmed", trigger_status="confirmed"):
    return {
        "schema_version": "work-definition/v1",
        "work_definition_id": "wd-39",
        "tenant_id": "tenant-39",
        "owner_id": "owner-39",
        "session_id": "session-39",
        "channel_mode": "native_hitl",
        "revision": 0,
        "status": "WAITING_ANSWER",
        "goal": _fact("일일 보고서를 생성", goal_status),
        "trigger": _fact("매일 09:00", trigger_status),
        "frequency_volume": _fact("하루 1회"),
        "sla": _fact("10분"),
        "automation_intent": _fact("반자동"),
        "scope_in": [],
        "scope_out": [],
        "actors": _confirmed_list("담당자"),
        "systems": [],
        "inputs": _confirmed_list("메일"),
        "outputs": _confirmed_list("보고서"),
        "steps": _confirmed_list("메일 조회"),
        "decisions": [],
        "exceptions": _confirmed_list("실패 시 담당자 알림"),
        "pains": [],
        "risks_controls": [],
        "constraints": [],
        "success_criteria": _confirmed_list("누락 0건"),
        "assumptions": [],
        "unresolved": [],
        "as_is_graph": {"nodes": [], "edges": []},
        "approved_hash": None,
        "processed_answer_batches": [],
    }


def _context(work, *, round_number):
    return {
        "work_definition": copy.deepcopy(work),
        "completeness": {
            "work_definition_id": work["work_definition_id"],
            "tenant_id": work["tenant_id"],
            "session_id": work["session_id"],
            "revision": work["revision"],
            "blocking_gaps": [],
        },
        "round_number": round_number,
    }


def _batch(work, *, round_number, question_path="goal", answer="확정된 업무 목표"):
    return {
        "batch_id": f"qb-{round_number}",
        "work_definition_id": work["work_definition_id"],
        "tenant_id": work["tenant_id"],
        "owner_id": work["owner_id"],
        "session_id": work["session_id"],
        "channel_mode": work["channel_mode"],
        "revision": work["revision"],
        "round_number": round_number,
        "status": "ANSWERED",
        "questions": [
            {
                "question_id": "q-1",
                "answer_type": "text",
                "target_paths": [question_path],
                "required": True,
                "reason_code": "GOAL_UNKNOWN",
            }
        ],
        "answers": [{"question_id": "q-1", "value": answer, "evidence_turn_id": "turn-answer"}],
        "answer_idempotency_key": f"answer-{round_number}",
        "answer_turn_id": "turn-answer",
        "answered_at": datetime(2026, 8, 30, 0, 1, tzinfo=timezone.utc),
        "expires_at": datetime(2026, 8, 30, 1, 0, tzinfo=timezone.utc),
    }


def _native_skip(batch):
    return {
        "schema_version": "native-clarification-skip-submission/v1",
        "batch_id": batch["batch_id"],
        "work_definition_id": batch["work_definition_id"],
        "tenant_id": batch["tenant_id"],
        "owner_id": batch["owner_id"],
        "session_id": batch["session_id"],
        "channel_mode": batch["channel_mode"],
        "revision": batch["revision"],
        "round_number": batch["round_number"],
        "request_id": f"answer-gate:run-1:{batch['batch_id']}",
        "action_id": "skip_additional_input",
        "skipped_question_ids": [question["question_id"] for question in batch["questions"]],
    }


def _seed(client, work, batch):
    client["db"]["work_definitions"].documents.append(copy.deepcopy(work))
    client["db"]["clarification_batches"].documents.append(copy.deepcopy(batch))


def _factory(client):
    def make_client(*args, **kwargs):
        return client

    return make_client


def _commit(client, context, batch, *, submit=None, skip=None, cancel=None):
    return MODULE.commit_answer_or_cancel(
        context,
        {"clarification_batch": batch},
        submit_trigger=submit,
        skip_trigger=skip,
        cancel_trigger=cancel,
        mongodb_uri="mongodb://example",
        mongo_database="db",
        now_utc="2026-08-30T00:10:00Z",
        client_factory=_factory(client),
    )


class F10AnswerCommitTests(unittest.TestCase):
    def test_public_ports_keep_human_trigger_edges_non_advanced(self):
        component = MODULE.F10AnswerCommitComponent
        self.assertEqual(component.display_name, "39 답변 반영·다음 단계")
        self.assertEqual([item.name for item in component.outputs], ["next_round_path", "review_path", "cancelled_path", "blocked_path"])
        inputs = {item.name: item for item in component.inputs}
        for name in ("clarification_context", "clarification_batch", "submit_trigger", "skip_trigger", "cancel_trigger"):
            self.assertFalse(inputs[name].advanced, name)
        self.assertEqual(inputs["mongo_database"].value, "business_work_design")
        for name in ("mongodb_uri", "mongo_database"):
            self.assertFalse(getattr(inputs[name], "advanced", False), name)

    def test_selected_review_route_excludes_future_branches_for_joiner(self):
        """A direct review must not make the joiner pull future unbuilt 39 nodes."""

        class Graph:
            def __init__(self):
                self.calls = []

            def exclude_branches_conditionally(self, vertex_id, output_names):
                self.calls.append((vertex_id, list(output_names)))

        component = MODULE.F10AnswerCommitComponent()
        stopped = []
        component.stop = stopped.append
        component.graph = Graph()
        component._id = "commit-round-1"
        component._commit_result = {"ok": True, "status": "READY_FOR_REVIEW", "route": "review_path"}

        result = component.route_commit()

        self.assertEqual(result.data["route"], "review_path")
        self.assertEqual(stopped, ["next_round_path", "cancelled_path", "blocked_path"])
        self.assertEqual(
            component.graph.calls,
            [("commit-round-1", ["next_round_path", "cancelled_path", "blocked_path"])],
        )

    def test_no_trigger_is_waiting_and_never_connects_to_mongo(self):
        work = _work(goal_status="unknown")
        context = _context(work, round_number=1)

        def forbidden_factory(*args, **kwargs):
            raise AssertionError("MongoDB must not be called before a Human Input trigger")

        result = MODULE.commit_answer_or_cancel(
            context,
            {"batch_id": "qb-1"},
            mongodb_uri="",
            mongo_database="",
            client_factory=forbidden_factory,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "WAITING_ANSWER")
        self.assertIsNone(result["route"])

    def test_submit_route_payload_is_not_treated_as_cancel_when_both_inputs_receive_it(self):
        """A selected Submit route may be restored on both predecessor inputs."""
        client = _Client()
        work = _work(goal_status="unknown", trigger_status="unknown")
        batch = _batch(work, round_number=1, question_path="goal")
        _seed(client, work, batch)
        selected_submit_payload = {"route": "branch_submit_answers", "status": "ANSWER_SUBMITTED"}

        result = _commit(
            client,
            _context(work, round_number=1),
            batch,
            submit=selected_submit_payload,
            cancel=selected_submit_payload,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["route"], "next_round_path")

    def test_cancel_route_payload_is_not_treated_as_submit_when_both_inputs_receive_it(self):
        """A selected Cancel route may be restored on both predecessor inputs."""
        client = _Client()
        work = _work(goal_status="unknown")
        batch = _batch(work, round_number=1)
        _seed(client, work, batch)
        selected_cancel_payload = {"route": "branch_cancel", "status": "CANCELLED"}

        result = _commit(
            client,
            _context(work, round_number=1),
            batch,
            submit=selected_cancel_payload,
            cancel=selected_cancel_payload,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["route"], "cancelled_path")

    def test_cancel_transitions_stored_work_to_terminal_cancelled(self):
        client = _Client()
        work = _work(goal_status="unknown")
        batch = _batch(work, round_number=1)
        _seed(client, work, batch)

        result = _commit(client, _context(work, round_number=1), batch, cancel="cancel")

        self.assertTrue(result["ok"])
        self.assertEqual(result["route"], "cancelled_path")
        self.assertEqual(result["status"], "CANCELLED")
        stored = client["db"]["work_definitions"].documents[0]
        self.assertEqual(stored["status"], "CANCELLED")
        self.assertEqual(stored["revision"], 1)

    def test_first_cancel_creates_a_terminal_canonical_work(self):
        client = _Client()
        work = _work(goal_status="unknown")
        batch = _batch(work, round_number=1)

        result = _commit(client, _context(work, round_number=1), batch, cancel="cancel")

        self.assertTrue(result["ok"])
        self.assertEqual(result["route"], "cancelled_path")
        stored = client["db"]["work_definitions"].documents
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["status"], "CANCELLED")
        self.assertEqual(stored[0]["revision"], 1)

    def test_submitted_answer_routes_to_next_round_when_a_gap_remains(self):
        client = _Client()
        work = _work(goal_status="unknown", trigger_status="unknown")
        batch = _batch(work, round_number=1, question_path="goal")
        _seed(client, work, batch)

        result = _commit(client, _context(work, round_number=1), batch, submit="submit")

        self.assertTrue(result["ok"])
        self.assertEqual(result["route"], "next_round_path")
        self.assertEqual(result["status"], "WAITING_ANSWER")
        self.assertEqual(result["next_round_number"], 2)
        stored = client["db"]["work_definitions"].documents[0]
        self.assertEqual(stored["revision"], 1)
        self.assertEqual(stored["goal"]["status"], "confirmed")

    def test_submitted_answer_routes_to_review_when_complete(self):
        client = _Client()
        work = _work(goal_status="unknown")
        batch = _batch(work, round_number=1, question_path="goal")
        _seed(client, work, batch)

        result = _commit(client, _context(work, round_number=1), batch, submit="submit")

        self.assertTrue(result["ok"])
        self.assertEqual(result["route"], "review_path")
        self.assertEqual(result["status"], "READY_FOR_REVIEW")
        self.assertEqual(result["work_definition"]["revision"], 1)

    def test_first_submit_creates_the_initial_canonical_work_before_merging(self):
        client = _Client()
        work = _work(goal_status="unknown")
        batch = _batch(work, round_number=1, question_path="goal")
        client["db"]["clarification_batches"].documents.append(copy.deepcopy(batch))

        result = _commit(client, _context(work, round_number=1), batch, submit="submit")

        self.assertTrue(result["ok"])
        self.assertEqual(result["route"], "review_path")
        stored = client["db"]["work_definitions"].documents
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["revision"], 1)
        self.assertEqual(stored[0]["status"], "READY_FOR_REVIEW")

    def test_answer_form_resume_records_are_normalized_before_commit(self):
        for resume_status in ("ANSWERED_PENDING_RESUME", "RESUMED"):
            with self.subTest(resume_status=resume_status):
                client = _Client()
                work = _work(goal_status="unknown")
                batch = _batch(work, round_number=1, question_path="goal")
                raw_answers = copy.deepcopy(batch.pop("answers"))
                idempotency_key = batch.pop("answer_idempotency_key")
                submitted_at = batch.pop("answered_at")
                batch.pop("answer_turn_id")
                batch["status"] = resume_status
                batch["answer_submission"] = {
                    "schema_version": "work-answer-submission/v1",
                    "submission_id": "answer-resume-1",
                    "idempotency_key": idempotency_key,
                    "channel_mode": work["channel_mode"],
                    "work_definition_id": work["work_definition_id"],
                    "tenant_id": work["tenant_id"],
                    "session_id": work["session_id"],
                    "batch_id": batch["batch_id"],
                    "expected_revision": work["revision"],
                    "answers": raw_answers,
                    "submitted_at": submitted_at,
                    "actor_id": work["owner_id"],
                }
                _seed(client, work, batch)

                result = _commit(client, _context(work, round_number=1), batch, submit="submit")

                self.assertTrue(result["ok"])
                self.assertEqual(result["route"], "review_path")
                self.assertEqual(result["work_definition"]["goal"]["status"], "confirmed")

    def test_resume_submission_identity_mismatch_is_fail_closed(self):
        client = _Client()
        work = _work(goal_status="unknown")
        batch = _batch(work, round_number=1, question_path="goal")
        answers = copy.deepcopy(batch.pop("answers"))
        batch["status"] = "ANSWERED_PENDING_RESUME"
        batch["answer_submission"] = {
            "idempotency_key": batch.pop("answer_idempotency_key"),
            "channel_mode": work["channel_mode"],
            "work_definition_id": "other-work",
            "tenant_id": work["tenant_id"],
            "batch_id": batch["batch_id"],
            "expected_revision": 0,
            "answers": answers,
            "submitted_at": batch.pop("answered_at"),
            "actor_id": work["owner_id"],
        }
        _seed(client, work, batch)

        result = _commit(client, _context(work, round_number=1), batch, submit="submit")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "ANSWER_SUBMISSION_IDENTITY_MISMATCH")
        self.assertEqual(client["db"]["work_definitions"].documents[0]["revision"], 0)

    def test_invalid_first_submission_does_not_create_a_work_definition(self):
        client = _Client()
        work = _work(goal_status="unknown")
        batch = _batch(work, round_number=1)
        batch["status"] = "WAITING_ANSWER"
        client["db"]["clarification_batches"].documents.append(copy.deepcopy(batch))

        result = _commit(client, _context(work, round_number=1), batch, submit="submit")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "ANSWER_FORM_NOT_SUBMITTED")
        self.assertEqual(client["db"]["work_definitions"].documents, [])

    def test_native_playground_submission_is_persisted_then_committed(self):
        client = _Client()
        work = _work(goal_status="unknown")
        batch = _batch(work, round_number=1, question_path="goal")
        batch["status"] = "WAITING_ANSWER"
        batch.pop("answers")
        batch.pop("answer_idempotency_key")
        batch.pop("answer_turn_id")
        batch.pop("answered_at")
        _seed(client, work, batch)
        native = {
            "schema_version": "native-clarification-answer-submission/v1",
            "batch_id": batch["batch_id"],
            "work_definition_id": work["work_definition_id"],
            "tenant_id": work["tenant_id"],
            "owner_id": work["owner_id"],
            "session_id": work["session_id"],
            "channel_mode": work["channel_mode"],
            "revision": work["revision"],
            "round_number": 1,
            "request_id": "answer-gate:run-1:qb-1",
            "action_id": "submit_answers",
            "answers": [{"question_id": "q-1", "value": "확정된 업무 목표", "evidence_turn_id": "native-hitl-q-1"}],
        }

        result = MODULE.commit_answer_or_cancel(
            _context(work, round_number=1),
            {"clarification_batch": batch},
            native_answer_submission={"answer_submission": native},
            submit_trigger={"route": "branch_submit_answers"},
            mongodb_uri="mongodb://example",
            mongo_database="db",
            now_utc="2026-08-30T00:10:00Z",
            client_factory=_factory(client),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["route"], "review_path")
        stored_batch = client["db"]["clarification_batches"].documents[0]
        self.assertEqual(stored_batch["status"], "RESUMED")
        self.assertEqual(stored_batch["answer_submission"]["schema_version"], "work-answer-submission/v1")
        self.assertEqual(stored_batch["answer_submission"]["answers"][0]["value"], "확정된 업무 목표")
        self.assertEqual(client["db"]["work_definitions"].documents[0]["goal"]["value"], "확정된 업무 목표")

    def test_invalid_native_answer_does_not_lock_the_question_batch(self):
        client = _Client()
        work = _work(goal_status="unknown")
        batch = _batch(work, round_number=1, question_path="goal")
        batch["status"] = "WAITING_ANSWER"
        batch.pop("answers")
        batch.pop("answer_idempotency_key")
        batch.pop("answer_turn_id")
        batch.pop("answered_at")
        _seed(client, work, batch)
        native = {
            "schema_version": "native-clarification-answer-submission/v1",
            "batch_id": batch["batch_id"],
            "work_definition_id": work["work_definition_id"],
            "tenant_id": work["tenant_id"],
            "owner_id": work["owner_id"],
            "session_id": work["session_id"],
            "channel_mode": work["channel_mode"],
            "revision": work["revision"],
            "round_number": 1,
            "request_id": "answer-gate:run-1:qb-1",
            "action_id": "submit_answers",
            "answers": [{"question_id": "q-1", "value": "", "evidence_turn_id": "native-hitl-q-1"}],
        }

        result = MODULE.commit_answer_or_cancel(
            _context(work, round_number=1),
            {"clarification_batch": batch},
            native_answer_submission=native,
            submit_trigger={"route": "branch_submit_answers"},
            mongodb_uri="mongodb://example",
            mongo_database="db",
            now_utc="2026-08-30T00:10:00Z",
            client_factory=_factory(client),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "ANSWER_REQUIRED_VALUE_MISSING")
        self.assertEqual(client["db"]["clarification_batches"].documents[0]["status"], "WAITING_ANSWER")
        self.assertEqual(client["db"]["work_definitions"].documents[0]["revision"], 0)

    def test_explicit_skip_routes_to_review_and_marks_skipped_questions_unresolved(self):
        """Skip keeps gaps visible while letting the human review the draft."""

        client = _Client()
        work = _work(goal_status="unknown", trigger_status="unknown")
        batch = _batch(work, round_number=1, question_path="goal")
        batch["status"] = "WAITING_ANSWER"
        batch.pop("answers")
        batch.pop("answer_idempotency_key")
        batch.pop("answer_turn_id")
        batch.pop("answered_at")
        _seed(client, work, batch)
        native_skip = _native_skip(batch)

        result = _commit(
            client,
            _context(work, round_number=1),
            batch,
            skip={"route": "branch_skip_additional_input", "skip_submission": native_skip},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["route"], "review_path")
        self.assertEqual(result["status"], "READY_FOR_REVIEW")
        self.assertTrue(result["clarification_skipped"])
        self.assertEqual(result["skip_summary"]["skipped_question_ids"], ["q-1"])
        self.assertGreaterEqual(result["skip_summary"]["remaining_gap_count"], 1)
        self.assertTrue(result["completeness"]["needs_clarification"])
        stored_work = client["db"]["work_definitions"].documents[0]
        self.assertEqual(stored_work["revision"], 1)
        self.assertEqual(stored_work["status"], "READY_FOR_REVIEW")
        self.assertEqual(stored_work["goal"]["status"], "unknown")
        self.assertEqual(len(stored_work["unresolved"]), 1)
        unresolved = stored_work["unresolved"][0]
        self.assertIn("추가 입력 건너뜀", unresolved["value"])
        self.assertEqual(unresolved["question_id"], "q-1")
        self.assertEqual(unresolved["target_paths"], ["goal"])
        stored_batch = client["db"]["clarification_batches"].documents[0]
        self.assertEqual(stored_batch["status"], "RESUMED")
        self.assertEqual(stored_batch["skip_submission"]["schema_version"], "work-clarification-skip/v1")

    def test_explicit_skip_replay_is_idempotent(self):
        client = _Client()
        work = _work(goal_status="unknown")
        batch = _batch(work, round_number=1, question_path="goal")
        batch["status"] = "WAITING_ANSWER"
        for name in ("answers", "answer_idempotency_key", "answer_turn_id", "answered_at"):
            batch.pop(name)
        _seed(client, work, batch)
        skip = {"route": "branch_skip_additional_input", "skip_submission": _native_skip(batch)}

        first = _commit(client, _context(work, round_number=1), batch, skip=skip)
        replay = _commit(client, _context(work, round_number=1), batch, skip=skip)

        self.assertTrue(first["ok"])
        self.assertTrue(replay["ok"])
        self.assertEqual(replay["route"], "review_path")
        self.assertTrue(replay["store_result"]["idempotent_replay"])
        self.assertEqual(client["db"]["work_definitions"].documents[0]["revision"], 1)

    def test_skip_requires_the_full_displayed_question_list(self):
        client = _Client()
        work = _work(goal_status="unknown")
        batch = _batch(work, round_number=1, question_path="goal")
        batch["status"] = "WAITING_ANSWER"
        for name in ("answers", "answer_idempotency_key", "answer_turn_id", "answered_at"):
            batch.pop(name)
        _seed(client, work, batch)
        invalid_skip = _native_skip(batch)
        invalid_skip["skipped_question_ids"] = []

        result = _commit(
            client,
            _context(work, round_number=1),
            batch,
            skip={"route": "branch_skip_additional_input", "skip_submission": invalid_skip},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "NATIVE_SKIP_SUBMISSION_INVALID")
        self.assertEqual(client["db"]["clarification_batches"].documents[0]["status"], "WAITING_ANSWER")
        self.assertEqual(client["db"]["work_definitions"].documents[0]["revision"], 0)

    def test_submit_and_skip_are_ambiguous_and_do_not_touch_mongodb(self):
        work = _work(goal_status="unknown")

        def forbidden_factory(*args, **kwargs):
            raise AssertionError("MongoDB must not be called for ambiguous Human Input")

        result = MODULE.commit_answer_or_cancel(
            _context(work, round_number=1),
            {"clarification_batch": _batch(work, round_number=1)},
            submit_trigger={"route": "branch_submit_answers"},
            skip_trigger={"route": "branch_skip_additional_input"},
            mongodb_uri="",
            mongo_database="",
            client_factory=forbidden_factory,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "HUMAN_ACTION_AMBIGUOUS")

    def test_third_round_blocks_when_a_required_gap_remains(self):
        client = _Client()
        work = _work(goal_status="unknown", trigger_status="unknown")
        batch = _batch(work, round_number=3, question_path="goal")
        _seed(client, work, batch)

        result = _commit(client, _context(work, round_number=3), batch, submit="submit")

        self.assertTrue(result["ok"])
        self.assertEqual(result["route"], "blocked_path")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["error"]["code"], "CLARIFICATION_ROUND_LIMIT")
        self.assertTrue(result["error"]["details"]["remaining_gaps"])

    def test_final_round_four_answers_can_complete_the_remaining_definition(self):
        """The fourth final-card field prevents the tenth gap from being stranded."""

        client = _Client()
        work = _work()
        work["revision"] = 2
        work["trigger"] = _fact(None, "unknown")
        work["exceptions"] = []
        work["sla"] = _fact(None, "unknown")
        work["success_criteria"] = []
        batch = _batch(work, round_number=3, question_path="trigger")
        batch["questions"] = [
            {
                "question_id": "q-1",
                "answer_type": "text",
                "target_paths": ["trigger"],
                "required": True,
                "reason_code": "TRIGGER_UNKNOWN",
            },
            {
                "question_id": "q-2",
                "answer_type": "text",
                "target_paths": ["exceptions"],
                "required": True,
                "reason_code": "FAILURE_POLICY_UNKNOWN",
            },
            {
                "question_id": "q-3",
                "answer_type": "text",
                "target_paths": ["sla"],
                "required": True,
                "reason_code": "SLA_UNKNOWN",
            },
            {
                "question_id": "q-4",
                "answer_type": "text",
                "target_paths": ["success_criteria"],
                "required": True,
                "reason_code": "SUCCESS_CRITERIA_UNKNOWN",
            },
        ]
        batch["answers"] = [
            {"question_id": "q-1", "value": "매주 목요일 09:00", "evidence_turn_id": "turn-r3"},
            {"question_id": "q-2", "value": "조회 실패 시 담당자에게 원인을 알리고 게시하지 않는다", "evidence_turn_id": "turn-r3"},
            {"question_id": "q-3", "value": "금요일 16시까지", "evidence_turn_id": "turn-r3"},
            {"question_id": "q-4", "value": "누락 0건과 팀장 승인 완료", "evidence_turn_id": "turn-r3"},
        ]
        _seed(client, work, batch)

        result = _commit(client, _context(work, round_number=3), batch, submit="submit")

        self.assertTrue(result["ok"])
        self.assertEqual(result["route"], "review_path")
        self.assertEqual(result["status"], "READY_FOR_REVIEW")
        self.assertEqual(result["work_definition"]["revision"], 3)
        self.assertEqual(result["work_definition"]["success_criteria"][0]["value"], "누락 0건과 팀장 승인 완료")


if __name__ == "__main__":
    unittest.main()
