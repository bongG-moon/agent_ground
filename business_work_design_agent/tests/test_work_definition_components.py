from __future__ import annotations

import ast
import copy
import importlib.util
import json
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ROOT / "components" / "work_definition"
COMPONENT_FILES = [COMPONENTS / f"{number:02d}_{name}.py" for number, name in (
    (10, "work_request_envelope"),
    (11, "work_definition_normalizer"),
    (12, "work_completeness_evaluator"),
    (13, "clarification_batch_builder"),
    (14, "work_answer_loader"),
    (15, "work_answer_merger"),
    (16, "work_graph_normalizer"),
    (17, "work_preview_hasher"),
    (18, "work_definition_store"),
    (27, "work_clarification_router"),
    (28, "work_definition_branch_joiner"),
    (34, "work_runtime_state_store"),
    (35, "result_gate"),
    (36, "playground_command_router"),
)]


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
    for port_name in ("BoolInput", "DataInput", "DropdownInput", "IntInput", "MessageTextInput", "Output", "SecretStrInput"):
        setattr(modules["lfx.io"], port_name, Port)
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
    MODULES = {path.stem[:2]: _load(f"work_definition_{path.stem}", path) for path in COMPONENT_FILES}
finally:
    _restore_modules(_ORIGINALS)


def _fact(value, status="confirmed"):
    return {"value": value, "status": status, "evidence_turn_ids": ["turn-1"], "confidence": 1.0 if status == "confirmed" else 0.7, "last_updated_revision": 0}


def _base_work(*, channel="native_hitl", status="EXTRACTING", revision=0):
    return {
        "schema_version": "work-definition/v1",
        "work_definition_id": "wd-1",
        "tenant_id": "tenant-a",
        "owner_id": "owner-a",
        "session_id": "session-a",
        "channel_mode": channel,
        "revision": revision,
        "status": status,
        "source_requests": [],
        "goal": _fact("매일 보고서를 만든다"),
        "trigger": _fact("매일 09:00"),
        "frequency_volume": _fact("하루 1회"),
        "sla": _fact("10분 이내"),
        "automation_intent": _fact("반자동"),
        "scope_in": [],
        "scope_out": [],
        "actors": [{"id": "actor-1", "name": "담당자", "provenance": {"status": "confirmed"}}],
        "systems": [],
        "inputs": [{"id": "input-1", "name": "메일", "provenance": {"status": "confirmed"}}],
        "outputs": [{"id": "output-1", "name": "보고서", "provenance": {"status": "confirmed"}}],
        "steps": [{"id": "step-1", "title": "메일 조회", "provenance": {"status": "confirmed"}}],
        "decisions": [],
        "exceptions": [{"id": "ex-1", "value": "실패 시 담당자 알림", "provenance": {"status": "confirmed"}}],
        "pains": [],
        "risks_controls": [],
        "constraints": [],
        "success_criteria": [{"id": "sc-1", "value": "누락 0건", "provenance": {"status": "confirmed"}}],
        "assumptions": [],
        "unresolved": [],
        "as_is_graph": {"nodes": [], "edges": []},
        "preview_hash": None,
        "approved_hash": None,
        "processed_answer_batches": [],
    }


class StandaloneContractTests(unittest.TestCase):
    def test_all_work_definition_files_are_standalone_single_components(self):
        self.assertEqual(len(COMPONENT_FILES), 14)
        for path in COMPONENT_FILES:
            source = path.read_text(encoding="utf-8")
            self.assertFalse(path.read_bytes().startswith(b"\xef\xbb\xbf"), path.name)
            tree = ast.parse(source, filename=str(path))
            component_classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and any(isinstance(base, ast.Name) and base.id == "Component" for base in node.bases)]
            self.assertEqual(len(component_classes), 1, path.name)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertEqual(node.level, 0, path.name)
                    self.assertFalse((node.module or "").startswith("langflow"), path.name)
                    self.assertFalse((node.module or "").startswith("lfx._"), path.name)
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "sys":
                    self.assertNotEqual(node.attr, "path", path.name)
            self.assertNotIn("self.ctx", source, path.name)
            self.assertNotRegex(source, r"\b(eval|exec)\s*\(", path.name)
            output_methods = [node for node in component_classes[0].body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name not in {"__init__"}]
            self.assertTrue(any(isinstance(method.returns, ast.Name) and method.returns.id == "Data" for method in output_methods), path.name)


class WorkDefinitionPipelineTests(unittest.TestCase):
    def test_playground_command_router_accepts_only_strict_top_level_commands(self):
        parser = MODULES["36"]
        valid_start = parser.parse_playground_command(
            json.dumps({"command": "start", "request_text": "메일로 주간 보고서를 만듭니다.", "additional_prompt": "승인 단계 포함"})
        )
        self.assertTrue(valid_start["ok"])
        self.assertEqual(valid_start["route"], "start_path")
        self.assertEqual(valid_start["request_text"], "메일로 주간 보고서를 만듭니다.")

        nested = parser.parse_playground_command(
            '{"command":"reject","nested":{"command":"approve"}}'
        )
        self.assertFalse(nested["ok"])
        self.assertEqual(nested["error"]["code"], "PLAYGROUND_COMMAND_FIELDS_INVALID")

        duplicate = parser.parse_playground_command(
            '{"command":"reject","command":"approve"}'
        )
        self.assertFalse(duplicate["ok"])
        self.assertEqual(duplicate["error"]["code"], "PLAYGROUND_COMMAND_JSON_INVALID")

        malformed = parser.parse_playground_command('{"command":"approve"')
        self.assertFalse(malformed["ok"])
        self.assertEqual(malformed["route"], "blocked_path")

        valid_action = parser.parse_playground_command('{"command":"reject"}')
        self.assertTrue(valid_action["ok"])
        self.assertEqual(valid_action["route"], "reject_path")

        unsupported_rework = parser.parse_playground_command('{"command":"request_changes"}')
        self.assertFalse(unsupported_rework["ok"])
        self.assertEqual(unsupported_rework["error"]["code"], "PLAYGROUND_COMMAND_INVALID")

    def test_request_envelope_extracts_exact_channels_from_validated_playground_data(self):
        parsed = MODULES["36"].parse_playground_command(
            json.dumps({"command": "start", "request_text": "  원문 유지\n", "additional_prompt": "추가 지침\n"})
        )
        routed = MODULES["36"].Data(data=parsed)
        result = MODULES["10"].build_work_request_envelope(
            routed,
            additional_prompt=routed,
            tenant_id="tenant-a",
            owner_id="owner-a",
            session_id="session-a",
            channel_mode="playground",
            submitted_at="2026-08-27T00:00:00Z",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["envelope"]["source_request"]["raw_text"], "  원문 유지\n")
        self.assertEqual(result["envelope"]["additional_prompt"]["raw_text"], "추가 지침\n")

    def test_result_gate_requires_explicit_success_and_required_payload(self):
        success = {"ok": True, "status": "READY", "work_definition": {"work_definition_id": "wd-1"}}
        self.assertEqual(
            MODULES["35"].gate_result(success, required_field="work_definition.work_definition_id"),
            success,
        )
        missing = MODULES["35"].gate_result({"ok": True, "status": "READY"}, required_field="work_definition")
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["error"]["code"], "RESULT_REQUIRED_FIELD_MISSING")
        implicit = MODULES["35"].gate_result({"status": "READY"})
        self.assertFalse(implicit["ok"])
        self.assertEqual(implicit["error"]["code"], "RESULT_ENVELOPE_INVALID")
        original_failure = {"ok": False, "status": "BLOCKED", "error": {"code": "UPSTREAM", "message": "blocked"}}
        self.assertEqual(MODULES["35"].gate_result(original_failure), original_failure)

    def test_envelope_preserves_raw_channels_and_rejects_cross_channel_value(self):
        raw = "  메일을 읽고 보고서를 만듭니다.\n"
        result = MODULES["10"].build_work_request_envelope(raw, additional_prompt="추가 조건", tenant_id="tenant-a", owner_id="owner-a", session_id="session-a", channel_mode="native_hitl", submitted_at="2026-08-27T00:00:00Z")
        self.assertTrue(result["ok"])
        self.assertEqual(result["envelope"]["source_request"]["raw_text"], raw)
        self.assertEqual(result["envelope"]["additional_prompt"]["raw_text"], "추가 조건")
        bad = MODULES["10"].build_work_request_envelope("업무", tenant_id="t", owner_id="o", session_id="s", channel_mode="mixed")
        self.assertEqual(bad["error"]["code"], "WORK_CHANNEL_INVALID")

    def test_request_identity_contract_matches_hitl_api_boundary(self):
        boundary = "a" * 128
        accepted = MODULES["10"].build_work_request_envelope(
            "업무",
            tenant_id=boundary,
            owner_id="owner-a",
            session_id=boundary,
            work_definition_id=boundary,
            submitted_at="2026-08-27T00:00:00Z",
        )
        self.assertTrue(accepted["ok"])
        for invalid_field, invalid_value in (("session_id", "a" * 129), ("work_definition_id", "wd/invalid")):
            kwargs = {
                "tenant_id": "tenant-a",
                "owner_id": "owner-a",
                "session_id": "session-a",
                "work_definition_id": "wd-1",
                "submitted_at": "2026-08-27T00:00:00Z",
            }
            kwargs[invalid_field] = invalid_value
            blocked = MODULES["10"].build_work_request_envelope("업무", **kwargs)
            self.assertEqual(blocked["error"]["code"], "WORK_IDENTITY_INVALID")
            self.assertIn(invalid_field, blocked["error"]["details"]["fields"])

        naive_time = MODULES["10"].build_work_request_envelope(
            "업무",
            tenant_id="tenant-a",
            owner_id="owner-a",
            session_id="session-a",
            submitted_at="2026-08-27T00:00:00",
        )
        self.assertEqual(naive_time["error"]["code"], "WORK_REQUEST_TIMESTAMP_INVALID")

    def test_envelope_rejects_secret_literals_before_storage(self):
        for field, request_text, additional_prompt in (
            ("request_text", "메일을 조회한다 password=NeverStoreThis123456", ""),
            ("additional_prompt", "메일을 조회한다", "Bearer top-secret-token-1234567890"),
        ):
            result = MODULES["10"].build_work_request_envelope(
                request_text,
                additional_prompt=additional_prompt,
                tenant_id="tenant-a",
                owner_id="owner-a",
                session_id="session-a",
                submitted_at="2026-08-27T00:00:00Z",
            )
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "WORK_REQUEST_SECRET_MATERIAL_DETECTED")
            self.assertEqual(result["error"]["details"]["fields"], [field])
            serialized = json.dumps(result, ensure_ascii=False)
            self.assertNotIn("NeverStoreThis123456", serialized)
            self.assertNotIn("top-secret-token", serialized)

    def test_normalizer_downgrades_model_confirmation_and_preserves_existing_confirmation(self):
        envelope = MODULES["10"].build_work_request_envelope("메일 요약", tenant_id="tenant-a", owner_id="owner-a", session_id="session-a", submitted_at="2026-08-27T00:00:00Z")
        candidate = {"goal": {"value": "메일 요약 보고서", "status": "confirmed"}, "actors": [{"name": "담당자", "status": "confirmed"}], "as_is_graph": {"nodes": [{"id": "s", "kind": "start", "label": "시작"}], "edges": []}}
        first = MODULES["11"].normalize_work_definition(candidate, envelope)
        second = MODULES["11"].normalize_work_definition(candidate, envelope)
        self.assertTrue(first["ok"])
        self.assertEqual(first["work_definition"]["goal"]["status"], "inferred")
        self.assertEqual(first["work_definition"]["actors"][0]["provenance"]["status"], "inferred")
        self.assertEqual(first["work_definition"]["actors"][0]["id"], second["work_definition"]["actors"][0]["id"])
        existing = first["work_definition"]
        existing["goal"] = _fact("사용자 확정 목표")
        preserved = MODULES["11"].normalize_work_definition({"goal": "모델의 다른 목표"}, envelope, existing)
        self.assertEqual(preserved["work_definition"]["goal"]["value"], "사용자 확정 목표")
        self.assertEqual(preserved["work_definition"]["goal"]["status"], "confirmed")

    def test_completeness_and_batch_enforce_priority_limit_and_skip_confirmed(self):
        work = _base_work()
        work["goal"] = _fact(None, "unknown")
        work["trigger"] = _fact(None, "unknown")
        work["inputs"] = []
        work["outputs"] = []
        work["actors"] = []
        evaluated = MODULES["12"].evaluate_work_completeness(work)
        self.assertTrue(evaluated["completeness"]["needs_clarification"])
        built = MODULES["13"].build_clarification_batch(work, evaluated, round_number=1, max_questions=99, now_utc="2026-08-27T00:00:00Z")
        questions = built["clarification_batch"]["questions"]
        self.assertLessEqual(len(questions), 3)
        self.assertEqual(built["status"], "WAITING_ANSWER")
        self.assertTrue(all(question["target_paths"] for question in questions))

        work["goal"] = _fact("확정 목표", "confirmed")
        fake_eval = copy.deepcopy(evaluated)
        built_again = MODULES["13"].build_clarification_batch(work, fake_eval, round_number=1, max_questions=3, now_utc="2026-08-27T00:00:00Z")
        asked_paths = {path for question in built_again["clarification_batch"]["questions"] for path in question["target_paths"]}
        self.assertNotIn("goal", asked_paths)

        work["processed_answer_batches"] = [
            {"batch_id": "qb-1"},
            {"batch_id": "qb-2"},
            {"batch_id": "qb-3"},
        ]
        final_gate_blocked = MODULES["13"].build_clarification_batch(
            work,
            fake_eval,
            round_number=4,
            now_utc="2026-08-27T00:00:00Z",
        )
        self.assertEqual(final_gate_blocked["error"]["code"], "CLARIFICATION_ROUND_LIMIT")
        ready_eval = {
            "work_definition_id": work["work_definition_id"],
            "revision": work["revision"],
            "blocking_gaps": [],
        }
        final_gate_ready = MODULES["13"].build_clarification_batch(
            work,
            ready_eval,
            round_number=4,
            now_utc="2026-08-27T00:00:00Z",
        )
        self.assertEqual(final_gate_ready["status"], "READY_FOR_REVIEW")

    def test_clarification_round_is_derived_and_replay_or_skip_is_rejected(self):
        work = _base_work()
        work["goal"] = _fact(None, "unknown")
        completeness = MODULES["12"].evaluate_work_completeness(work)
        first = MODULES["13"].build_clarification_batch(work, completeness, round_number=0)
        self.assertEqual(first["clarification_batch"]["round_number"], 1)
        skipped = MODULES["13"].build_clarification_batch(work, completeness, round_number=2)
        self.assertEqual(skipped["error"]["code"], "CLARIFICATION_ROUND_SEQUENCE_MISMATCH")

        work["processed_answer_batches"] = [{"batch_id": "qb-1"}]
        second = MODULES["13"].build_clarification_batch(work, completeness, round_number=0)
        self.assertEqual(second["clarification_batch"]["round_number"], 2)
        replayed = MODULES["13"].build_clarification_batch(work, completeness, round_number=1)
        self.assertEqual(replayed["error"]["code"], "CLARIFICATION_ROUND_SEQUENCE_MISMATCH")

        work["processed_answer_batches"].append({"batch_id": "qb-2"})
        third = MODULES["13"].build_clarification_batch(work, completeness, round_number=0)
        self.assertEqual(third["clarification_batch"]["round_number"], 3)
        work["processed_answer_batches"].append({"batch_id": "qb-3"})
        fourth = MODULES["13"].build_clarification_batch(work, completeness, round_number=0)
        self.assertEqual(fourth["error"]["code"], "CLARIFICATION_ROUND_LIMIT")

    def test_answer_loader_blocks_channel_mixing_and_accepts_bound_native_form(self):
        work = _base_work()
        batch = {
            "batch_id": "qb-1",
            "work_definition_id": "wd-1",
            "tenant_id": "tenant-a",
            "owner_id": "owner-a",
            "session_id": "session-a",
            "channel_mode": "native_hitl",
            "revision": 0,
            "status": "WAITING_ANSWER",
            "expires_at": "2026-08-28T00:00:00Z",
            "questions": [{"question_id": "q-1", "target_paths": ["goal"], "required": True, "reason_code": "GOAL_UNKNOWN"}],
        }
        payload = {"channel_mode": "native_hitl", "command": "submit_answers", "work_definition_id": "wd-1", "batch_id": "qb-1", "session_id": "session-a", "expected_revision": 0, "idempotency_key": "idem-1", "answers": {"q-1": "새 목표"}}
        mixed = MODULES["14"].load_work_answers(work, batch, channel_mode="native_hitl", native_form_payload=payload, playground_payload={"anything": True}, human_action="submit_answers", now_utc="2026-08-27T00:00:00Z")
        self.assertEqual(mixed["error"]["code"], "ANSWER_CHANNEL_MIXED")
        valid = MODULES["14"].load_work_answers(work, batch, channel_mode="native_hitl", native_form_payload=payload, human_action="submit_answers", now_utc="2026-08-27T00:00:00Z")
        self.assertTrue(valid["ok"])
        self.assertEqual(valid["answer_submission"]["answers"][0]["target_paths"], ["goal"])

        playground_work = _base_work(channel="playground")
        playground_batch = {**batch, "channel_mode": "playground"}
        playground_payload = {**payload, "channel_mode": "playground"}
        playground = MODULES["14"].load_work_answers(playground_work, playground_batch, channel_mode="playground", playground_payload=playground_payload, now_utc="2026-08-27T00:00:00Z")
        self.assertTrue(playground["ok"])
        self.assertEqual(playground["answer_submission"]["channel_mode"], "playground")

    def test_answer_loader_strictly_validates_answer_type_and_choices(self):
        work = _base_work()
        batch = {
            "batch_id": "qb-types",
            "work_definition_id": "wd-1",
            "tenant_id": "tenant-a",
            "owner_id": "owner-a",
            "session_id": "session-a",
            "channel_mode": "native_hitl",
            "revision": 0,
            "status": "WAITING_ANSWER",
            "expires_at": "2026-08-28T00:00:00Z",
            "questions": [
                {
                    "question_id": "q-choice",
                    "target_paths": ["goal"],
                    "required": True,
                    "reason_code": "GOAL_UNKNOWN",
                    "answer_type": "single_choice",
                    "choices": ["A", "B"],
                }
            ],
        }
        base_payload = {
            "channel_mode": "native_hitl",
            "command": "submit_answers",
            "work_definition_id": "wd-1",
            "batch_id": "qb-types",
            "session_id": "session-a",
            "expected_revision": 0,
            "idempotency_key": "types-1",
        }
        invalid = MODULES["14"].load_work_answers(
            work,
            batch,
            channel_mode="native_hitl",
            native_form_payload={**base_payload, "answers": {"q-choice": "NOT_A_CHOICE"}},
            human_action="submit_answers",
            now_utc="2026-08-27T00:00:00Z",
        )
        self.assertEqual(invalid["error"]["code"], "ANSWER_CHOICE_INVALID")

        batch["questions"][0].update(answer_type="multi_choice", choices=["A", "B"])
        valid_multi = MODULES["14"].load_work_answers(
            work,
            batch,
            channel_mode="native_hitl",
            native_form_payload={**base_payload, "answers": {"q-choice": ["A", "A", "B"]}},
            human_action="submit_answers",
            now_utc="2026-08-27T00:00:00Z",
        )
        self.assertEqual(valid_multi["answer_submission"]["answers"][0]["value"], ["A", "B"])

        batch["questions"][0].update(answer_type="boolean", choices=[])
        invalid_bool = MODULES["14"].load_work_answers(
            work,
            batch,
            channel_mode="native_hitl",
            native_form_payload={**base_payload, "answers": {"q-choice": "true"}},
            human_action="submit_answers",
            now_utc="2026-08-27T00:00:00Z",
        )
        self.assertEqual(invalid_bool["error"]["code"], "ANSWER_VALUE_TYPE_INVALID")

    def test_answer_loader_accepts_on_time_durable_submission_after_deadline(self):
        work = _base_work()
        batch = {
            "batch_id": "qb-deadline",
            "work_definition_id": "wd-1",
            "tenant_id": "tenant-a",
            "owner_id": "owner-a",
            "session_id": "session-a",
            "channel_mode": "native_hitl",
            "revision": 0,
            "status": "ANSWERED",
            "answer_deadline_at": "2026-08-27T00:05:00Z",
            "expires_at": "2026-09-03T00:04:59Z",
            "questions": [{"question_id": "q-deadline", "target_paths": ["goal"], "required": True, "reason_code": "GOAL_UNKNOWN"}],
        }
        payload = {
            "channel_mode": "native_hitl",
            "command": "submit_answers",
            "work_definition_id": "wd-1",
            "batch_id": "qb-deadline",
            "session_id": "session-a",
            "expected_revision": 0,
            "idempotency_key": "deadline-1",
            "answers": {"q-deadline": "마감 전 답변"},
            "submitted_at": "2026-08-27T00:04:59Z",
        }
        loaded = MODULES["14"].load_work_answers(
            work,
            batch,
            channel_mode="native_hitl",
            native_form_payload=payload,
            human_action="submit_answers",
            now_utc="2026-08-27T00:06:00Z",
        )
        self.assertTrue(loaded["ok"])
        late = MODULES["14"].load_work_answers(
            work,
            batch,
            channel_mode="native_hitl",
            native_form_payload={**payload, "submitted_at": "2026-08-27T00:05:01Z"},
            human_action="submit_answers",
            now_utc="2026-08-27T00:06:00Z",
        )
        self.assertEqual(late["error"]["code"], "ANSWER_BATCH_EXPIRED")

    def test_answer_loader_can_fail_closed_fetch_from_companion_api(self):
        work = _base_work()
        batch = {
            "batch_id": "qb-api",
            "work_definition_id": "wd-1",
            "tenant_id": "tenant-a",
            "owner_id": "owner-a",
            "session_id": "session-a",
            "channel_mode": "native_hitl",
            "revision": 0,
            "status": "WAITING_ANSWER",
            "created_at": "2026-08-27T00:00:00Z",
            "expires_at": "2026-08-28T00:00:00Z",
            "questions": [{"question_id": "q-api", "target_paths": ["goal"], "required": True, "reason_code": "GOAL_UNKNOWN"}],
        }
        api_payload = {
            "clarification_batch": batch,
            "answer_submission": {"answers": {"q-api": "API에서 읽은 목표"}, "idempotency_key": "api-idem", "submitted_at": "2026-08-27T00:05:00Z", "turn_id": "turn-api"},
        }

        class Response:
            headers = {"Content-Type": "application/json"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def geturl(self):
                return "https://answer.internal/api/work-definitions/wd-1/question-batches/qb-api"

            def read(self, limit):
                return json.dumps(api_payload, ensure_ascii=False).encode("utf-8")

        def opener(request, timeout):
            self.assertEqual(request.get_header("Authorization"), "Bearer api-token")
            self.assertEqual(request.get_header("X-tenant-id"), "tenant-a")
            self.assertEqual(request.get_header("X-actor-id"), "owner-a")
            self.assertLessEqual(timeout, 60)
            return Response()

        loaded = MODULES["14"].load_work_answers_from_companion_api(
            work,
            batch,
            channel_mode="native_hitl",
            human_action="submit_answers",
            now_utc="2026-08-27T00:06:00Z",
            answer_api_base_url="https://answer.internal",
            answer_api_token="api-token",
            opener=opener,
        )
        self.assertTrue(loaded["ok"])
        self.assertEqual(loaded["store_result"]["source"], "companion_api")
        self.assertEqual(loaded["answer_submission"]["answers"][0]["value"], "API에서 읽은 목표")
        insecure = MODULES["14"].load_work_answers_from_companion_api(work, batch, channel_mode="native_hitl", human_action="submit_answers", answer_api_base_url="http://answer.internal", answer_api_token="api-token", opener=opener)
        self.assertEqual(insecure["error"]["code"], "ANSWER_API_CONFIG_INVALID")

    def test_answer_merge_preserves_provenance_conflict_and_is_idempotent(self):
        work = _base_work()
        submission = {
            "work_definition_id": "wd-1",
            "tenant_id": "tenant-a",
            "session_id": "session-a",
            "channel_mode": "native_hitl",
            "batch_id": "qb-1",
            "submission_id": "answer-1",
            "idempotency_key": "idem-1",
            "payload_sha256": "payload-hash",
            "expected_revision": 0,
            "answers": [{"question_id": "q-1", "value": "다른 목표", "target_paths": ["goal"], "evidence_turn_id": "turn-answer", "resolve_conflict": False}],
        }
        merged = MODULES["15"].merge_work_answers(work, submission)
        self.assertTrue(merged["ok"])
        self.assertEqual(merged["work_definition"]["revision"], 1)
        self.assertEqual(merged["work_definition"]["goal"]["status"], "conflicting")
        self.assertIn("turn-1", merged["work_definition"]["goal"]["evidence_turn_ids"])
        self.assertIn("turn-answer", merged["work_definition"]["goal"]["evidence_turn_ids"])
        replay = MODULES["15"].merge_work_answers(merged["work_definition"], submission)
        self.assertTrue(replay["merge_result"]["idempotent_replay"])
        self.assertEqual(replay["work_definition"]["revision"], 1)

        unresolved_submission = copy.deepcopy(submission)
        unresolved_submission.update({"batch_id": "qb-2", "submission_id": "answer-2", "idempotency_key": "idem-2", "payload_sha256": "payload-hash-2", "expected_revision": 1})
        unresolved_submission["answers"][0]["value"] = "세 번째 목표"
        still_conflicting = MODULES["15"].merge_work_answers(merged["work_definition"], unresolved_submission)
        self.assertEqual(still_conflicting["work_definition"]["goal"]["status"], "conflicting")
        self.assertIn("세 번째 목표", still_conflicting["work_definition"]["goal"]["conflicting_values"])

    def test_partial_decision_answer_preserves_schema_and_closes_branch_gap(self):
        work = _base_work()
        work["decisions"] = [{"id": "decision-1", "title": "오류 확인", "condition": "", "branches": []}]
        evaluated = MODULES["12"].evaluate_work_completeness(work)
        branch_gap = next(
            item for item in evaluated["completeness"]["blocking_gaps"] if item["reason_code"] == "BRANCH_CONDITION_UNKNOWN"
        )
        self.assertEqual(branch_gap["target_paths"], ["decisions[0]"])
        batch = MODULES["13"].build_clarification_batch(
            work,
            evaluated,
            round_number=1,
            now_utc="2026-08-27T00:00:00Z",
        )["clarification_batch"]
        question = next(item for item in batch["questions"] if item["reason_code"] == "BRANCH_CONDITION_UNKNOWN")
        self.assertIn("condition", question["text"])
        submission = {
            "work_definition_id": work["work_definition_id"],
            "tenant_id": work["tenant_id"],
            "session_id": work["session_id"],
            "channel_mode": work["channel_mode"],
            "batch_id": batch["batch_id"],
            "submission_id": "answer-decision",
            "idempotency_key": "idem-decision",
            "payload_sha256": "payload-decision",
            "expected_revision": 0,
            "answers": [
                {
                    "question_id": question["question_id"],
                    "value": json.dumps({"condition": "오류가 있는가", "branches": ["예", "아니오"]}, ensure_ascii=False),
                    "target_paths": question["target_paths"],
                    "evidence_turn_id": "turn-decision",
                    "resolve_conflict": False,
                }
            ],
        }
        merged = MODULES["15"].merge_work_answers(work, submission)
        self.assertTrue(merged["ok"])
        decision = merged["work_definition"]["decisions"][0]
        self.assertEqual(decision["id"], "decision-1")
        self.assertEqual(decision["condition"], "오류가 있는가")
        self.assertEqual([item["label"] for item in decision["branches"]], ["예", "아니오"])
        self.assertNotIn("value", decision)
        reevaluated = MODULES["12"].evaluate_work_completeness(merged["work_definition"])
        self.assertNotIn(
            "BRANCH_CONDITION_UNKNOWN",
            {item["reason_code"] for item in reevaluated["completeness"]["blocking_gaps"]},
        )

    def test_graph_validation_and_canonical_hash(self):
        work = _base_work()
        linear = MODULES["16"].normalize_work_graph(work)
        self.assertTrue(linear["ok"])
        self.assertTrue(linear["graph_validation"]["valid"])
        detailed = _base_work()
        detailed["as_is_graph"] = {
            "nodes": [
                {"id": "start", "kind": "start", "label": "시작"},
                {
                    "id": "manual",
                    "kind": "task",
                    "label": "수동 처리",
                    "current_work": "사람이 직접 처리한다.",
                    "problems": ["시간이 오래 걸린다."],
                    "improvement": "구조화 입력으로 자동화한다.",
                },
                {"id": "end", "kind": "end", "label": "끝"},
            ],
            "edges": [
                {"id": "d1", "source": "start", "target": "manual"},
                {"id": "d2", "source": "manual", "target": "end"},
            ],
        }
        detailed_result = MODULES["16"].normalize_work_graph(detailed)
        detail_node = next(
            node for node in detailed_result["work_definition"]["as_is_graph"]["nodes"] if node["id"] == "manual"
        )
        self.assertEqual(detail_node["current_work"], "사람이 직접 처리한다.")
        self.assertEqual(detail_node["problems"], ["시간이 오래 걸린다."])
        self.assertEqual(detail_node["improvement"], "구조화 입력으로 자동화한다.")
        derived = _base_work()
        derived["steps"] = [
            {"id": "s1", "title": "조회", "provenance": {"status": "confirmed"}},
            {"id": "s2", "title": "보고", "provenance": {"status": "confirmed"}},
        ]
        derived["decisions"] = [{
            "id": "d1",
            "title": "이상이 있는가",
            "after_step_ref": "s1",
            "branches": [
                {"label": "예", "condition": "has_issue == true", "target_step_ref": "s2"},
                {"label": "아니오", "default": True, "target": "end"},
            ],
            "provenance": {"status": "confirmed"},
        }]
        derived_graph = MODULES["16"].normalize_work_graph(derived)
        self.assertTrue(derived_graph["ok"])
        self.assertIn("decision", {node["kind"] for node in derived_graph["work_definition"]["as_is_graph"]["nodes"]})
        decision_work = _base_work()
        decision_work["as_is_graph"] = {
            "nodes": [
                {"id": "start", "kind": "start", "label": "시작"},
                {"id": "d", "kind": "decision", "label": "분기"},
                {"id": "end", "kind": "end", "label": "끝"},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "d"},
                {"id": "e2", "source": "d", "target": "end", "branch_label": "예"},
            ],
        }
        invalid = MODULES["16"].normalize_work_graph(decision_work)
        codes = {item["code"] for item in invalid["graph_validation"]["errors"]}
        self.assertIn("GRAPH_DECISION_BRANCH_COUNT", codes)
        self.assertIn("GRAPH_DECISION_BRANCH_CONTRACT", codes)

        cycle_work = _base_work()
        cycle_work["as_is_graph"] = {
            "nodes": [
                {"id": "start", "kind": "start", "label": "시작"},
                {"id": "a", "kind": "task", "label": "A"},
                {"id": "b", "kind": "task", "label": "B"},
                {"id": "end", "kind": "end", "label": "끝"},
            ],
            "edges": [
                {"id": "c1", "source": "start", "target": "a"},
                {"id": "c2", "source": "a", "target": "b"},
                {"id": "c3", "source": "b", "target": "a"},
                {"id": "c4", "source": "b", "target": "end"},
            ],
        }
        unbounded = MODULES["16"].normalize_work_graph(cycle_work)
        self.assertIn("GRAPH_UNBOUNDED_CYCLE", {item["code"] for item in unbounded["graph_validation"]["errors"]})
        cycle_work["as_is_graph"]["loop_policy"] = {"max_iterations": 3, "exit_condition": "성공 또는 3회"}
        bounded = MODULES["16"].normalize_work_graph(cycle_work)
        self.assertTrue(bounded["ok"])

        hashed_work = linear["work_definition"]
        first = MODULES["17"].build_work_preview_hash(linear)
        rearranged = copy.deepcopy(hashed_work)
        rearranged["updated_at"] = "tomorrow"
        rearranged["as_is_graph"]["nodes"] = list(reversed(rearranged["as_is_graph"]["nodes"]))
        for node in rearranged["as_is_graph"]["nodes"]:
            node["x"] = 999
            node["style"] = {"color": "red"}
        rearranged_envelope = copy.deepcopy(linear)
        rearranged_envelope["work_definition"] = rearranged
        second = MODULES["17"].build_work_preview_hash(rearranged_envelope)
        self.assertEqual(first["preview"]["preview_hash"], second["preview"]["preview_hash"])
        changed = copy.deepcopy(first["work_definition"])
        changed["approved_hash"] = first["preview"]["preview_hash"]
        changed["goal"] = _fact("의미가 달라진 목표")
        changed_envelope = copy.deepcopy(linear)
        changed_envelope["work_definition"] = changed
        invalidated = MODULES["17"].build_work_preview_hash(changed_envelope)
        self.assertTrue(invalidated["preview"]["approval_invalidated"])
        self.assertIsNone(invalidated["work_definition"]["approved_hash"])

        fail_closed = MODULES["17"].build_work_preview_hash(invalid)
        self.assertFalse(fail_closed["ok"])
        self.assertEqual(fail_closed["error"]["code"], "WORK_PREVIEW_UPSTREAM_REJECTED")
        missing_attestation = MODULES["17"].build_work_preview_hash({"ok": True, "work_definition": hashed_work})
        self.assertFalse(missing_attestation["ok"])
        self.assertEqual(missing_attestation["error"]["code"], "WORK_PREVIEW_GRAPH_ATTESTATION_REQUIRED")

    def test_multiple_decisions_after_same_step_are_all_reachable(self):
        work = _base_work()
        work["steps"] = [
            {"id": "s1", "title": "조회", "provenance": {"status": "confirmed"}},
            {"id": "s2", "title": "보고", "provenance": {"status": "confirmed"}},
        ]
        work["decisions"] = [
            {
                "id": decision_id,
                "title": title,
                "after_step_ref": "s1",
                "branches": [
                    {"label": "예", "condition": f"{decision_id} == true", "target_step_ref": "s2"},
                    {"label": "아니오", "default": True, "target": "end"},
                ],
                "provenance": {"status": "confirmed"},
            }
            for decision_id, title in (("d1", "이상 여부"), ("d2", "외부 전송 여부"))
        ]

        result = MODULES["16"].normalize_work_graph(work)
        self.assertTrue(result["ok"], result.get("graph_validation"))
        graph = result["work_definition"]["as_is_graph"]
        decision_ids = {node["id"] for node in graph["nodes"] if node["kind"] == "decision"}
        anchored_targets = {
            edge["target"] for edge in graph["edges"]
            if edge["source"] == "s1" and edge["target"] in decision_ids
        }
        self.assertEqual(anchored_targets, {"d1", "d2"})
        self.assertNotIn(
            "GRAPH_UNREACHABLE_NODE",
            {item["code"] for item in result["graph_validation"]["errors"]},
        )

    def test_clarification_router_and_branch_joiner_select_exactly_one_path(self):
        work = _base_work()
        batch = {
            "batch_id": "qb-1",
            "work_definition_id": work["work_definition_id"],
            "tenant_id": work["tenant_id"],
            "owner_id": work["owner_id"],
            "session_id": work["session_id"],
            "channel_mode": work["channel_mode"],
            "revision": work["revision"],
        }
        clarification = MODULES["27"].route_work_clarification(
            work,
            {"ok": True, "status": "WAITING_ANSWER", "clarification_batch": batch},
        )
        self.assertEqual(clarification["route"], "clarification_path")

        review = MODULES["27"].route_work_clarification(
            work,
            {"ok": True, "status": "READY_FOR_REVIEW", "clarification_batch": None},
        )
        self.assertEqual(review["route"], "review_path")
        joined = MODULES["28"].join_work_definition_branches(None, review)
        self.assertTrue(joined["ok"])
        self.assertEqual(joined["selected_branch"], "review")
        ambiguous = MODULES["28"].join_work_definition_branches(
            {"work_definition": work},
            review,
        )
        self.assertEqual(ambiguous["error"]["code"], "WORK_BRANCH_AMBIGUOUS")


class _Result:
    def __init__(self, matched_count=0):
        self.matched_count = matched_count


class _FakeCollection:
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
                return _Result(1)
        return _Result(0)


class _FakeDatabase:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, _FakeCollection())


class _Context:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def start_transaction(self):
        return _Context()


class _FakeClient:
    def __init__(self):
        self.databases = {}
        self.admin = self
        self.closed = False

    def command(self, name):
        if name != "ping":
            raise AssertionError(name)
        return {"ok": 1}

    def __getitem__(self, name):
        return self.databases.setdefault(name, _FakeDatabase())

    def start_session(self):
        return _Context()

    def close(self):
        self.closed = True


class MongoStoreTests(unittest.TestCase):
    def test_clarification_batch_is_durable_and_native_loader_reads_form_answer(self):
        fake = _FakeClient()

        def factory(*args, **kwargs):
            return fake

        work = _base_work()
        work["goal"] = _fact(None, "unknown")
        completeness = {
            "work_definition_id": "wd-1",
            "tenant_id": "tenant-a",
            "session_id": "session-a",
            "revision": 0,
            "blocking_gaps": [{"reason_code": "GOAL_UNKNOWN", "target_paths": ["goal"], "priority": "contract", "current_status": "unknown"}],
        }
        built = MODULES["13"].build_clarification_batch(work, completeness, now_utc="2026-08-27T00:00:00Z")
        persisted = MODULES["13"].persist_clarification_batch(built, mongodb_uri="mongodb://example", mongo_database="db", client_factory=factory)
        self.assertTrue(persisted["store_result"]["persisted"])
        batch_collection = fake["db"]["clarification_batches"]
        self.assertEqual(len(batch_collection.documents), 1)
        question_id = built["clarification_batch"]["questions"][0]["question_id"]
        batch_collection.documents[0]["status"] = "ANSWERED_PENDING_RESUME"
        batch_collection.documents[0]["answer_submission"] = {
            "answers": {question_id: "사용자가 확정한 목표"},
            "idempotency_key": "form-answer-1",
            "submitted_at": datetime(2026, 8, 27, 0, 5, tzinfo=timezone.utc),
            "turn_id": "turn-form",
        }
        loaded = MODULES["14"].load_work_answers_from_store(
            work,
            built["clarification_batch"],
            channel_mode="native_hitl",
            human_action="submit_answers",
            now_utc="2026-08-27T00:06:00Z",
            mongodb_uri="mongodb://example",
            mongo_database="db",
            client_factory=factory,
        )
        self.assertTrue(loaded["ok"])
        self.assertEqual(loaded["answer_submission"]["answers"][0]["value"], "사용자가 확정한 목표")

    def test_store_uses_transactional_cas_event_and_idempotent_replay(self):
        fake = _FakeClient()

        def factory(*args, **kwargs):
            return fake

        work = _base_work(status="EXTRACTING")
        create = MODULES["18"].store_work_definition(work, expected_revision=0, command="save", actor_id="actor-a", idempotency_key="create-1", mongodb_uri="mongodb://example", mongo_database="db", now_utc="2026-08-27T00:00:00Z", client_factory=factory)
        self.assertTrue(create["ok"])
        self.assertEqual(create["store_result"]["revision"], 0)
        self.assertEqual(len(fake["db"]["work_definition_events"].documents), 1)

        update = copy.deepcopy(create["work_definition"])
        update["status"] = "READY_FOR_REVIEW"
        update["preview_hash"] = "sha256:preview"
        saved = MODULES["18"].store_work_definition(update, expected_revision=0, command="save", actor_id="actor-a", idempotency_key="save-2", mongodb_uri="mongodb://example", mongo_database="db", now_utc="2026-08-27T00:01:00Z", client_factory=factory)
        self.assertTrue(saved["ok"])
        self.assertEqual(saved["store_result"]["revision"], 1)
        self.assertEqual(len(fake["db"]["work_definition_events"].documents), 2)

        replay = MODULES["18"].store_work_definition(update, expected_revision=0, command="save", actor_id="actor-a", idempotency_key="save-2", mongodb_uri="mongodb://example", mongo_database="db", now_utc="2026-08-27T00:02:00Z", client_factory=factory)
        self.assertTrue(replay["store_result"]["idempotent_replay"])
        self.assertEqual(len(fake["db"]["work_definition_events"].documents), 2)
        stale = MODULES["18"].store_work_definition(update, expected_revision=0, command="save", actor_id="actor-a", idempotency_key="stale-3", mongodb_uri="mongodb://example", mongo_database="db", now_utc="2026-08-27T00:03:00Z", client_factory=factory)
        self.assertEqual(stale["error"]["code"], "REVISION_CONFLICT")

        waiting = MODULES["18"].store_work_definition(saved["work_definition"], expected_revision=1, command="request_approval", actor_id="actor-a", idempotency_key="wait-approval", mongodb_uri="mongodb://example", mongo_database="db", now_utc="2026-08-27T00:04:00Z", client_factory=factory)
        self.assertTrue(waiting["ok"])
        self.assertEqual(waiting["status"], "WAITING_APPROVAL")
        approved = MODULES["18"].store_work_definition(waiting["work_definition"], expected_revision=2, command="approve", actor_id="owner-a", idempotency_key="approve-native", mongodb_uri="mongodb://example", mongo_database="db", now_utc="2026-08-27T00:05:00Z", client_factory=factory)
        self.assertTrue(approved["ok"])
        self.assertEqual(approved["status"], "APPROVED")

    def test_runtime_state_is_separate_from_semantic_revision_and_fail_closed(self):
        work = _base_work(status="EXTRACTING", revision=3)
        now = datetime(2026, 8, 27, 0, 0, tzinfo=timezone.utc)
        waiting, event, replayed = MODULES["34"]._prepare_runtime_documents(
            None,
            work,
            runtime_status="WAITING_ANSWER",
            phase="clarification_round_1",
            actor_id="owner-a",
            idempotency_key="runtime-1",
            request_sha256="a" * 64,
            now=now,
        )
        self.assertFalse(replayed)
        self.assertEqual(waiting["semantic_revision"], 3)
        self.assertEqual(waiting["runtime_revision"], 1)
        self.assertEqual(event["runtime_status"], "WAITING_ANSWER")
        merging, _, _ = MODULES["34"]._prepare_runtime_documents(
            waiting,
            work,
            runtime_status="MERGING",
            phase="clarification_round_1_resume",
            actor_id="owner-a",
            idempotency_key="runtime-2",
            request_sha256="b" * 64,
            now=now + timedelta(seconds=1),
        )
        self.assertEqual(merging["semantic_revision"], 3)
        self.assertEqual(merging["runtime_revision"], 2)
        stored_work = copy.deepcopy(work)
        stored_work["revision"] = 4
        reconciled, _, _ = MODULES["34"]._prepare_runtime_documents(
            merging,
            stored_work,
            runtime_status="MERGING",
            phase="clarification_round_1_stored",
            actor_id="owner-a",
            idempotency_key="runtime-reconciled",
            request_sha256="9" * 64,
            now=now + timedelta(milliseconds=500),
        )
        review_work = copy.deepcopy(stored_work)
        review_work["revision"] = 5
        ready, _, _ = MODULES["34"]._prepare_runtime_documents(
            reconciled,
            review_work,
            runtime_status="READY_FOR_REVIEW",
            phase="review_ready",
            actor_id="owner-a",
            idempotency_key="runtime-ready",
            request_sha256="8" * 64,
            now=now + timedelta(milliseconds=750),
        )
        self.assertEqual(ready["semantic_revision"], 5)
        with self.assertRaisesRegex(ValueError, "RUNTIME_ACTOR_MISMATCH"):
            MODULES["34"]._prepare_runtime_documents(
                merging,
                work,
                runtime_status="BLOCKED",
                phase="blocked",
                actor_id="other-user",
                idempotency_key="runtime-3",
                request_sha256="c" * 64,
                now=now + timedelta(seconds=2),
            )
        cancelled, _, _ = MODULES["34"]._prepare_runtime_documents(
            merging,
            work,
            runtime_status="CANCELLED",
            phase="cancelled",
            actor_id="owner-a",
            idempotency_key="runtime-4",
            request_sha256="d" * 64,
            now=now + timedelta(seconds=3),
        )
        next_revision = copy.deepcopy(work)
        next_revision["revision"] = 4
        with self.assertRaisesRegex(ValueError, "RUNTIME_STATE_TRANSITION_INVALID"):
            MODULES["34"]._prepare_runtime_documents(
                cancelled,
                next_revision,
                runtime_status="EXTRACTING",
                phase="illegal_reopen",
                actor_id="owner-a",
                idempotency_key="runtime-5",
                request_sha256="e" * 64,
                now=now + timedelta(seconds=4),
            )
        jumped = copy.deepcopy(work)
        jumped["revision"] = 6
        with self.assertRaisesRegex(ValueError, "RUNTIME_SEMANTIC_REVISION_GAP"):
            MODULES["34"]._prepare_runtime_documents(
                merging,
                jumped,
                runtime_status="READY_FOR_REVIEW",
                phase="revision_jump",
                actor_id="owner-a",
                idempotency_key="runtime-6",
                request_sha256="f" * 64,
                now=now + timedelta(seconds=5),
            )

    def test_playground_approval_requires_scoped_one_time_token(self):
        fake = _FakeClient()

        def factory(*args, **kwargs):
            return fake

        token = "0123456789abcdef0123456789abcdef"
        work = _base_work(channel="playground", status="READY_FOR_REVIEW")
        work["preview_hash"] = "sha256:preview"
        created = MODULES["18"].store_work_definition(work, expected_revision=0, command="save", actor_id="actor-a", idempotency_key="create-pg", mongodb_uri="mongodb://example", mongo_database="db", now_utc="2026-08-27T00:00:00Z", client_factory=factory)
        self.assertTrue(created["ok"])
        missing_issuer_token = MODULES["18"].store_work_definition(created["work_definition"], expected_revision=0, command="request_approval", actor_id="actor-a", idempotency_key="wait-missing-token", mongodb_uri="mongodb://example", mongo_database="db", now_utc="2026-08-27T00:00:30Z", client_factory=factory)
        self.assertEqual(missing_issuer_token["error"]["code"], "ACTION_TOKEN_ISSUANCE_REQUIRED")
        weak_token = MODULES["18"].store_work_definition(created["work_definition"], expected_revision=0, command="request_approval", actor_id="actor-a", idempotency_key="wait-weak-token", mongodb_uri="mongodb://example", mongo_database="db", one_time_action_token="too-short", now_utc="2026-08-27T00:00:45Z", client_factory=factory)
        self.assertEqual(weak_token["error"]["code"], "ACTION_TOKEN_WEAK")
        waiting = MODULES["18"].store_work_definition(created["work_definition"], expected_revision=0, command="request_approval", actor_id="actor-a", idempotency_key="wait-pg", mongodb_uri="mongodb://example", mongo_database="db", one_time_action_token=token, action_token_ttl_seconds=900, now_utc="2026-08-27T00:01:00Z", client_factory=factory)
        self.assertTrue(waiting["ok"])
        self.assertTrue(waiting["store_result"]["action_token_registered"])
        self.assertNotIn("pending_action", waiting["work_definition"])
        actor_mismatch = MODULES["18"].store_work_definition(waiting["work_definition"], expected_revision=1, command="approve", actor_id="actor-a", idempotency_key="approve-other-actor", mongodb_uri="mongodb://example", mongo_database="db", one_time_action_token=token, now_utc="2026-08-27T00:02:00Z", client_factory=factory)
        self.assertEqual(actor_mismatch["error"]["code"], "ACTION_ACTOR_MISMATCH")
        wrong = MODULES["18"].store_work_definition(waiting["work_definition"], expected_revision=1, command="approve", actor_id="owner-a", idempotency_key="approve-wrong", mongodb_uri="mongodb://example", mongo_database="db", one_time_action_token="wrong", now_utc="2026-08-27T00:02:00Z", client_factory=factory)
        self.assertEqual(wrong["error"]["code"], "ACTION_TOKEN_INVALID")
        tampered_action = copy.deepcopy(waiting["work_definition"])
        tampered_action["goal"] = "공격자가 바꾼 목표"
        tampered_action["preview_hash"] = "sha256:tampered-preview"
        approved = MODULES["18"].store_work_definition(tampered_action, expected_revision=1, command="approve", actor_id="owner-a", idempotency_key="approve-ok", mongodb_uri="mongodb://example", mongo_database="db", one_time_action_token=token, now_utc="2026-08-27T00:02:00Z", client_factory=factory)
        self.assertTrue(approved["ok"])
        self.assertEqual(approved["status"], "APPROVED")
        self.assertEqual(approved["work_definition"]["approved_hash"], "sha256:preview")
        self.assertNotEqual(approved["work_definition"].get("goal"), "공격자가 바꾼 목표")
        self.assertNotIn("pending_action", approved["work_definition"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
