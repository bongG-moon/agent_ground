from __future__ import annotations

import copy
import importlib.util
import json
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "components" / "work_definition" / "47_f10_chat_answer_resume_loader.py"


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
    for port_name in ("DataInput", "IntInput", "MessageTextInput", "Output", "SecretStrInput"):
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
    spec = importlib.util.spec_from_file_location("f10_chat_answer_resume_loader_test", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ORIGINALS = _install_lfx_stubs()
try:
    MODULE = _load_component()
finally:
    _restore_modules(_ORIGINALS)


class _Collection:
    def __init__(self, documents=None):
        self.documents = list(documents or [])

    @staticmethod
    def _matches(document, query):
        for key, expected in query.items():
            actual = document.get(key)
            if isinstance(expected, dict) and "$in" in expected:
                if actual not in expected["$in"]:
                    return False
            elif actual != expected:
                return False
        return True

    def find_one(self, query):
        return next((copy.deepcopy(item) for item in self.documents if self._matches(item, query)), None)

    def find(self, query):
        return [copy.deepcopy(item) for item in self.documents if self._matches(item, query)]


class _Database:
    def __init__(self, *, work=None, batches=None):
        self.collections = {
            "work_definitions": _Collection(work),
            "clarification_batches": _Collection(batches),
        }

    def __getitem__(self, name):
        return self.collections.setdefault(name, _Collection())


class _Client:
    def __init__(self, database):
        self.database = database
        self.admin = self
        self.closed = False

    def command(self, name):
        if name != "ping":
            raise AssertionError(name)
        return {"ok": 1}

    def __getitem__(self, name):
        if name != "business_work_design":
            raise AssertionError(name)
        return self.database

    def close(self):
        self.closed = True


def _factory(client):
    def create(*args, **kwargs):
        return client

    return create


def _work(*, work_definition_id="wd-chat-1", revision=4, owner_id="employee-1004"):
    return {
        "_id": object(),
        "work_definition_id": work_definition_id,
        "tenant_id": "default",
        "owner_id": owner_id,
        "session_id": "session-chat-1",
        "channel_mode": "native_hitl",
        "revision": revision,
        "status": "WAITING_ANSWER",
        "persisted_at": datetime(2026, 9, 2, 0, 0, tzinfo=timezone.utc),
    }


def _batch(
    *,
    batch_id="qb-chat-1",
    work_definition_id="wd-chat-1",
    revision=4,
    owner_id="employee-1004",
    status="WAITING_ANSWER",
):
    return {
        "_id": object(),
        "batch_id": batch_id,
        "work_definition_id": work_definition_id,
        "tenant_id": "default",
        "owner_id": owner_id,
        "session_id": "session-chat-1",
        "channel_mode": "native_hitl",
        "revision": revision,
        "round_number": 2,
        "status": status,
        "created_at": datetime(2026, 9, 2, 0, 1, tzinfo=timezone.utc),
        "answer_deadline_at": datetime(2026, 9, 2, 1, 0, tzinfo=timezone.utc),
        "questions": [{"question_id": "q-chat-1", "text": "승인 기준은 무엇인가요?"}],
    }


def _resume(client, reply, *, now="2026-09-02T00:10:00Z"):
    return MODULE.build_chat_answer_resume(
        reply,
        employee_id="employee-1004",
        mongodb_uri="mongodb://example",
        mongo_database="business_work_design",
        now_utc=now,
        client_factory=_factory(client),
    )


class F10ChatAnswerResumeLoaderTests(unittest.TestCase):
    def test_loads_the_single_pending_batch_and_emits_json_safe_context(self):
        client = _Client(_Database(work=[_work()], batches=[_batch()]))

        result = _resume(client, "1번: 팀장 승인 후 포털에 게시합니다.")

        self.assertTrue(result["ok"])
        self.assertEqual(result["route"], "success_path")
        self.assertEqual(result["clarification_batch"]["batch_id"], "qb-chat-1")
        self.assertEqual(result["clarification_context"]["round_number"], 2)
        self.assertEqual(result["clarification_context"]["completeness"]["revision"], 4)
        self.assertEqual(result["clarification_batch"]["created_at"], "2026-09-02T00:01:00Z")
        self.assertEqual(result["clarification_context"]["work_definition"]["persisted_at"], "2026-09-02T00:00:00Z")
        self.assertNotIn("_id", result["clarification_batch"])
        self.assertNotIn("_id", result["clarification_context"]["work_definition"])
        json.dumps(result, ensure_ascii=False, allow_nan=False)
        self.assertTrue(client.closed)

    def test_question_batch_header_selects_one_pending_batch_when_several_exist(self):
        work_a = _work(work_definition_id="wd-chat-a")
        work_b = _work(work_definition_id="wd-chat-b")
        batch_a = _batch(batch_id="qb-chat-a", work_definition_id="wd-chat-a")
        batch_b = _batch(batch_id="qb-chat-b", work_definition_id="wd-chat-b")
        client = _Client(_Database(work=[work_a, work_b], batches=[batch_a, batch_b]))

        result = _resume(client, "질문 묶음: qb-chat-b\n1번: 승인 담당자는 팀장입니다.")

        self.assertTrue(result["ok"])
        self.assertEqual(result["clarification_batch"]["batch_id"], "qb-chat-b")
        self.assertEqual(result["clarification_context"]["work_definition"]["work_definition_id"], "wd-chat-b")

    def test_no_pending_batch_fails_closed_with_a_human_readable_route(self):
        client = _Client(_Database(work=[_work()], batches=[]))

        result = _resume(client, "1번: 답변")

        self.assertFalse(result["ok"])
        self.assertEqual(result["route"], "blocked_path")
        self.assertEqual(result["error"]["code"], "CHAT_ANSWER_BATCH_NOT_FOUND")
        self.assertIn("답변을 기다리는 질문이 없습니다", result["error"]["message"])

    def test_multiple_pending_batches_require_the_question_batch_header(self):
        work_a = _work(work_definition_id="wd-chat-a")
        work_b = _work(work_definition_id="wd-chat-b")
        client = _Client(
            _Database(
                work=[work_a, work_b],
                batches=[
                    _batch(batch_id="qb-chat-a", work_definition_id="wd-chat-a"),
                    _batch(batch_id="qb-chat-b", work_definition_id="wd-chat-b"),
                ],
            )
        )

        result = _resume(client, "1번: 질문 묶음을 적지 않은 답변")

        self.assertFalse(result["ok"])
        self.assertEqual(result["route"], "blocked_path")
        self.assertEqual(result["error"]["code"], "CHAT_ANSWER_BATCH_AMBIGUOUS")
        self.assertIn("질문 묶음", result["error"]["message"])

    def test_two_question_batch_ids_are_rejected_before_mongodb_lookup(self):
        client = _Client(_Database(work=[_work()], batches=[_batch()]))

        result = _resume(client, "질문 묶음: qb-chat-1\n질문 묶음: qb-chat-2\n1번: 답변")

        self.assertFalse(result["ok"])
        self.assertEqual(result["route"], "blocked_path")
        self.assertEqual(result["error"]["code"], "CHAT_ANSWER_BATCH_ID_AMBIGUOUS")
        self.assertFalse(client.closed, "MongoDB must not be opened for a malformed batch header")

    def test_invalid_configuration_uses_the_blocked_route_without_connecting(self):
        called = False

        def forbidden_factory(*args, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("MongoDB must not be opened for missing configuration")

        result = MODULE.build_chat_answer_resume(
            "1번: 답변",
            employee_id="employee-1004",
            mongodb_uri="",
            mongo_database="business_work_design",
            now_utc="2026-09-02T00:10:00Z",
            client_factory=forbidden_factory,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["route"], "blocked_path")
        self.assertEqual(result["error"]["code"], "CHAT_ANSWER_RESUME_CONFIG_MISSING")
        self.assertFalse(called)

    def test_component_only_emits_its_selected_success_branch(self):
        class Graph:
            def __init__(self):
                self.exclusions = []

            def exclude_branches_conditionally(self, vertex_id, output_names):
                self.exclusions.append((vertex_id, list(output_names)))

        component = MODULE.F10ChatAnswerResumeLoaderComponent()
        component._id = "resume-loader-47"
        component.graph = Graph()
        component._current_output = "success_path"
        component._resume_result = {"ok": True, "status": "CHAT_ANSWER_READY", "route": "success_path"}

        result = component.route_resume()

        self.assertTrue(result.data["ok"])
        self.assertEqual(component.stopped_outputs, ["blocked_path"])
        self.assertEqual(component.graph.exclusions, [("resume-loader-47", ["blocked_path"])])
        component._current_output = "blocked_path"
        self.assertEqual(component.route_resume().data, {})


if __name__ == "__main__":
    unittest.main()
