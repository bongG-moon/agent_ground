from __future__ import annotations

import base64
import copy
import hashlib
import importlib
import io
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from services.hitl_form_api.app import MemoryHitlRepository, Settings as HitlSettings, create_app as create_hitl_app
from services.report_api.app import MemoryReportRepository, Settings as ReportSettings, create_app as create_report_app


HITL_MODULE = importlib.import_module("services.hitl_form_api.app")
REPORT_MODULE = importlib.import_module("services.report_api.app")


AUTH_HEADERS = {
    "Authorization": "Bearer test-secret",
    "X-Tenant-ID": "tenant-a",
    "X-Actor-ID": "user-a",
}


def _csp_hash(value: str) -> str:
    return "sha256-" + base64.b64encode(hashlib.sha256(value.encode()).digest()).decode()


def test_report_api_is_immutable_idempotent_tenant_scoped_and_browser_clickable(monkeypatch) -> None:
    settings = ReportSettings(
        environment="test",
        storage_mode="memory",
        mongodb_uri="",
        mongodb_database="business_work_design",
        collection_prefix="test_",
        bearer_token="test-secret",
        allow_insecure_local=False,
        public_base_url="http://localhost:8091/api",
        retention_days=7,
        view_signing_secret="0123456789abcdef0123456789abcdef",
        view_token_ttl_seconds=300,
    )
    repository = MemoryReportRepository()
    app = create_report_app(settings, repository)
    html = "<!doctype html><html><head><style>body{color:#111}</style></head><body><script>void 0</script></body></html>"
    content_hash = "sha256:" + hashlib.sha256(html.encode()).hexdigest()
    payload = {
        "report_id": "report-test-1",
        "content_sha256": content_hash,
        "html": html,
        "metadata": {"script_csp_hash": _csp_hash("void 0"), "style_csp_hash": _csp_hash("body{color:#111}")},
    }
    headers = {**AUTH_HEADERS, "Idempotency-Key": "publish-1"}
    with TestClient(app) as client:
        created = client.post("/api/reports", headers=headers, json=payload)
        assert created.status_code == 201
        created_body = created.json()
        view_url = created_body["view_url"]
        download_url = created_body["download_url"]
        view_parts = urlsplit(view_url)
        download_parts = urlsplit(download_url)
        view_token = parse_qs(view_parts.query)["capability"][0]
        download_token = parse_qs(download_parts.query)["capability"][0]
        assert view_parts.path == "/api/reports/report-test-1"
        assert download_parts.path == "/api/reports/report-test-1/download"
        signed_view = client.get(view_url)
        assert signed_view.status_code == 200
        assert signed_view.text == html
        assert view_token not in signed_view.text
        assert signed_view.headers["referrer-policy"] == "no-referrer"
        assert signed_view.headers["cache-control"] == "private, no-store, max-age=0"
        signed_download = client.get(download_url)
        assert signed_download.status_code == 200
        assert signed_download.headers["content-disposition"].startswith("attachment;")
        other_actor_headers = {**AUTH_HEADERS, "X-Actor-ID": "user-b"}
        assert client.get(view_parts.path, headers=other_actor_headers).status_code == 404
        assert client.get(download_parts.path, headers=other_actor_headers).status_code == 404
        assert client.get(f"{view_parts.path}/metadata", headers=other_actor_headers).status_code == 404
        assert client.post(
            "/api/reports",
            headers={**other_actor_headers, "Idempotency-Key": "publish-other-actor"},
            json=payload,
        ).status_code == 409
        assert client.get(f"{download_parts.scheme}://{download_parts.netloc}{download_parts.path}?capability={view_token}").status_code == 401
        assert client.get(f"{view_parts.scheme}://{view_parts.netloc}{view_parts.path}?capability={download_token}").status_code == 401
        assert client.get(view_url + "&capability=" + view_token).status_code == 401
        assert client.get(view_url, headers=AUTH_HEADERS).status_code == 401
        wrong_path = view_url.replace("report-test-1?", "report-test-2?")
        assert client.get(wrong_path).status_code == 401
        encoded_claim, encoded_signature = view_token.split(".", 1)
        claim = json.loads(base64.urlsafe_b64decode(encoded_claim + "=" * (-len(encoded_claim) % 4)))
        assert "actor_id" not in claim
        assert "actor_sha256" not in claim
        assert claim["actor_binding"] == REPORT_MODULE._actor_binding(settings, "user-a")
        replacement = "A" if encoded_signature[0] != "A" else "B"
        tampered_token = encoded_claim + "." + replacement + encoded_signature[1:]
        tampered_url = view_url.replace(view_token, tampered_token)
        tampered = client.get(tampered_url)
        assert tampered.status_code == 401
        assert tampered.headers["cache-control"] == "private, no-store, max-age=0"
        assert tampered.headers["referrer-policy"] == "no-referrer"
        replay = client.post("/api/reports", headers=headers, json=payload)
        assert replay.status_code == 200
        assert replay.json()["content_sha256"] == content_hash
        viewed = client.get("/api/reports/report-test-1", headers=AUTH_HEADERS)
        assert viewed.status_code == 200
        assert viewed.text == html
        assert "default-src 'none'" in viewed.headers["content-security-policy"]
        wrong_tenant = client.get(
            "/api/reports/report-test-1",
            headers={**AUTH_HEADERS, "X-Tenant-ID": "tenant-b"},
        )
        assert wrong_tenant.status_code == 404
        missing_actor = client.get(
            "/api/reports/report-test-1",
            headers={key: value for key, value in AUTH_HEADERS.items() if key != "X-Actor-ID"},
        )
        assert missing_actor.status_code == 400
        changed = {**payload, "html": html + "<!--changed-->", "content_sha256": "sha256:" + hashlib.sha256((html + "<!--changed-->").encode()).hexdigest()}
        conflict = client.post("/api/reports", headers=headers, json=changed)
        assert conflict.status_code == 409

        generated_payload = {key: value for key, value in payload.items() if key != "report_id"}
        generated_headers = {**AUTH_HEADERS, "Idempotency-Key": "publish-generated-id"}
        generated = client.post("/api/reports", headers=generated_headers, json=generated_payload)
        generated_replay = client.post("/api/reports", headers=generated_headers, json=generated_payload)
        assert generated.status_code == 201
        assert generated_replay.status_code == 200
        assert generated.json()["report_id"] == generated_replay.json()["report_id"]

        future = datetime.now(timezone.utc) + timedelta(seconds=settings.view_token_ttl_seconds + 60)
        monkeypatch.setattr(REPORT_MODULE, "_now", lambda: future)
        assert client.get(view_url).status_code == 401


def test_report_mongo_stale_processing_reservation_is_atomically_reclaimed() -> None:
    from pymongo.errors import DuplicateKeyError

    html = "<!doctype html><html><body>recovered</body></html>"
    report = REPORT_MODULE.StoredReport(
        report_id="report-recovery",
        tenant_id="tenant-a",
        content_sha256=REPORT_MODULE._hash_html(html),
        html=html,
        metadata={},
        created_at=datetime.now(timezone.utc),
        actor_id="user-a",
    )
    request_hash = "a" * 64
    stale = datetime.now(timezone.utc) - timedelta(hours=1)

    class UpdateResult:
        def __init__(self, matched_count: int) -> None:
            self.matched_count = matched_count

    class FakeIdempotency:
        def __init__(self, allow_reclaim: bool) -> None:
            self.allow_reclaim = allow_reclaim
            self.reclaim_query: dict | None = None
            self.document = {
                "tenant_id": "tenant-a",
                "idempotency_key": "publish-recovery",
                "request_hash": request_hash,
                "report_id": report.report_id,
                "status": "PROCESSING",
                "created_at": stale,
                "lease_expires_at": stale,
                "lease_owner": "crashed-writer",
                "attempt": 1,
            }

        def insert_one(self, document: dict) -> None:
            raise DuplicateKeyError("existing reservation")

        def find_one(self, query: dict) -> dict:
            return copy.deepcopy(self.document)

        def update_one(self, query: dict, update: dict) -> UpdateResult:
            if "$or" in query:
                self.reclaim_query = copy.deepcopy(query)
                if not self.allow_reclaim:
                    return UpdateResult(0)
            self.document.update(copy.deepcopy(update.get("$set", {})))
            for key in update.get("$unset", {}):
                self.document.pop(key, None)
            for key, amount in update.get("$inc", {}).items():
                self.document[key] = int(self.document.get(key, 0)) + int(amount)
            return UpdateResult(1)

        def delete_one(self, query: dict) -> None:
            raise AssertionError("a successful recovery must not delete its reservation")

    class FakeReports:
        def __init__(self) -> None:
            self.document: dict | None = None

        def find_one(self, query: dict) -> dict | None:
            return copy.deepcopy(self.document)

        def insert_one(self, document: dict) -> None:
            self.document = copy.deepcopy(document)

    class FakeBucket:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        def upload_from_stream(self, filename: str, content: bytes, metadata: dict) -> str:
            assert content == html.encode("utf-8")
            return "blob-recovered"

        def delete(self, blob_id: str) -> None:
            self.deleted.append(blob_id)

    repository = object.__new__(REPORT_MODULE.MongoReportRepository)
    reservations = FakeIdempotency(allow_reclaim=True)
    repository._idempotency = reservations
    repository._reports = FakeReports()
    repository._bucket = FakeBucket()
    repository._retention_days = 90
    repository._processing_lease_seconds = 300

    stored, created = repository.put(report, "publish-recovery", request_hash)
    assert created is True
    assert stored.report_id == report.report_id
    assert reservations.reclaim_query is not None
    assert reservations.reclaim_query["status"] == "PROCESSING"
    assert reservations.document["status"] == "COMPLETED"
    assert reservations.document["attempt"] == 2
    assert "lease_owner" not in reservations.document

    blocked_repository = object.__new__(REPORT_MODULE.MongoReportRepository)
    blocked_repository._idempotency = FakeIdempotency(allow_reclaim=False)
    blocked_repository._reports = FakeReports()
    blocked_repository._bucket = FakeBucket()
    blocked_repository._retention_days = 90
    blocked_repository._processing_lease_seconds = 300
    try:
        blocked_repository.put(report, "publish-recovery", request_hash)
    except ValueError as exc:
        assert str(exc) == "IDEMPOTENCY_IN_PROGRESS"
    else:
        raise AssertionError("a live processing lease must not be stolen")


def _work_definition() -> dict:
    return {
        "work_definition_id": "wd-1",
        "tenant_id": "tenant-a",
        "session_id": "session-1",
        "owner_id": "user-a",
        "channel_mode": "native_hitl",
        "revision": 2,
        "status": "WAITING_ANSWER",
    }


def _batch() -> dict:
    return {
        "schema_version": "clarification-question-batch/v1",
        "batch_id": "qb-1",
        "work_definition_id": "wd-1",
        "tenant_id": "tenant-a",
        "session_id": "session-1",
        "channel_mode": "native_hitl",
        "revision": 2,
        "round_number": 1,
        "created_at": "2030-01-01T00:00:00Z",
        "expires_at": "2030-01-01T01:00:00Z",
        "status": "WAITING_ANSWER",
        "questions": [
            {
                "question_id": "q-1",
                "text": "저장 전에 담당자 확인이 필요한가요?",
                "target_paths": ["risks_controls"],
                "answer_type": "single_choice",
                "choices": ["필요", "불필요"],
                "required": True,
                "reason_code": "WRITE_APPROVAL_UNKNOWN",
            }
        ],
    }


def test_hitl_api_checks_revision_stores_answers_and_keeps_resume_server_side() -> None:
    settings = HitlSettings(
        environment="test",
        storage_mode="memory",
        mongodb_uri="",
        mongodb_database="business_work_design",
        collection_prefix="",
        bearer_token="test-secret",
        allow_insecure_local=False,
        langflow_base_url="http://localhost:7860",
        langflow_api_key="server-only-key",
        langflow_f10_flow_id="22222222-2222-2222-2222-222222222222",
        resume_enabled=False,
    )
    repository = MemoryHitlRepository()
    repository.seed_work_definition(_work_definition())
    app = create_hitl_app(settings, repository)
    registration = {
        "clarification_batch": _batch(),
        "workflow_job_id": "11111111-1111-1111-1111-111111111111",
        "workflow_request_id": "human-node:11111111-1111-1111-1111-111111111111",
    }
    with TestClient(app) as client:
        registered = client.post(
            "/api/work-definitions/wd-1/question-batches",
            headers={**AUTH_HEADERS, "Idempotency-Key": "register-1"},
            json=registration,
        )
        assert registered.status_code == 201
        assert "workflow_request_id" not in registered.json()
        assert "registration_idempotency_key" not in registered.json()
        other_actor = client.get(
            "/api/work-definitions/wd-1/question-batches/qb-1",
            headers={**AUTH_HEADERS, "X-Actor-ID": "user-b"},
        )
        assert other_actor.status_code == 404
        replay = client.post(
            "/api/work-definitions/wd-1/question-batches",
            headers={**AUTH_HEADERS, "Idempotency-Key": "register-1"},
            json=registration,
        )
        assert replay.status_code == 200
        stale = client.post(
            "/api/work-definitions/wd-1/question-batches/qb-1/answers",
            headers={**AUTH_HEADERS, "Idempotency-Key": "answer-stale"},
            json={"expected_revision": 1, "answers": [{"question_id": "q-1", "value": "필요"}]},
        )
        assert stale.status_code == 409
        invalid_choice = client.post(
            "/api/work-definitions/wd-1/question-batches/qb-1/answers",
            headers={**AUTH_HEADERS, "Idempotency-Key": "answer-invalid-choice"},
            json={"expected_revision": 2, "answers": [{"question_id": "q-1", "value": "NOT_A_CHOICE"}]},
        )
        assert invalid_choice.status_code == 422
        assert invalid_choice.json()["detail"] == "ANSWER_CHOICE_INVALID"
        answer_headers = {**AUTH_HEADERS, "Idempotency-Key": "answer-1"}
        answers = {"expected_revision": 2, "answers": [{"question_id": "q-1", "value": "필요"}]}
        submitted = client.post(
            "/api/work-definitions/wd-1/question-batches/qb-1/answers",
            headers=answer_headers,
            json=answers,
        )
        assert submitted.status_code == 200
        assert submitted.json()["status"] == "ANSWERED_PENDING_RESUME"
        assert submitted.json()["resume_result"]["status"] == "resume_disabled"
        second = client.post(
            "/api/work-definitions/wd-1/question-batches/qb-1/answers",
            headers=answer_headers,
            json=answers,
        )
        assert second.status_code == 200
        assert second.json()["idempotent_replay"] is True


def test_hitl_answer_deadline_is_immutable_while_ttl_moves_to_retention(monkeypatch) -> None:
    before_deadline = datetime(2030, 1, 1, 0, 59, 59, tzinfo=timezone.utc)
    after_deadline = datetime(2030, 1, 1, 1, 0, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(HITL_MODULE, "_now", lambda: before_deadline)
    document = HITL_MODULE._validate_batch(
        _batch(),
        "11111111-1111-1111-1111-111111111111",
        "human-node:11111111-1111-1111-1111-111111111111",
    )
    document["owner_id"] = "user-a"
    repository = MemoryHitlRepository()
    late_repository = MemoryHitlRepository()
    repository.register_batch(document, "register-deadline", "register-hash")
    late_repository.register_batch(document, "register-late", "register-hash-late")
    stored, created = repository.submit_answers(
        "tenant-a",
        "wd-1",
        "qb-1",
        2,
        [{"question_id": "q-1", "value": "필요"}],
        "user-a",
        "answer-deadline",
        "answer-hash",
    )
    assert created is True
    assert HITL_MODULE._parse_datetime(stored["answer_deadline_at"]) == datetime(2030, 1, 1, 1, 0, tzinfo=timezone.utc)
    assert HITL_MODULE._parse_datetime(stored["expires_at"]) > HITL_MODULE._parse_datetime(stored["answer_deadline_at"])
    assert stored["purge_at"] == stored["expires_at"]

    monkeypatch.setattr(HITL_MODULE, "_now", lambda: after_deadline)
    try:
        late_repository.submit_answers(
            "tenant-a",
            "wd-1",
            "qb-1",
            2,
            [{"question_id": "q-1", "value": "필요"}],
            "user-a",
            "answer-too-late",
            "late-hash",
        )
    except ValueError as exc:
        assert str(exc) == "BATCH_NOT_PENDING"
    else:
        raise AssertionError("late answer must be rejected")


def test_hitl_mongo_batch_attachment_uses_cas_and_rejects_a_racing_workflow() -> None:
    job_id = "11111111-1111-1111-1111-111111111111"
    request_id = "human-node:11111111-1111-1111-1111-111111111111"
    incoming = HITL_MODULE._validate_batch(_batch(), job_id, request_id)
    incoming["owner_id"] = "user-a"
    contract_fields = (
        "batch_id",
        "work_definition_id",
        "tenant_id",
        "owner_id",
        "session_id",
        "channel_mode",
        "revision",
        "round_number",
        "questions",
    )
    contract_hash = hashlib.sha256(
        HITL_MODULE._canonical({key: incoming.get(key) for key in contract_fields}).encode("utf-8")
    ).hexdigest()
    preexisting = copy.deepcopy(incoming)
    for key in (
        "workflow_job_id",
        "workflow_request_id",
        "registration_idempotency_key",
        "registration_request_hash",
    ):
        preexisting.pop(key, None)
    preexisting["contract_sha256"] = contract_hash
    immutable_deadline = preexisting["answer_deadline_at"]

    class UpdateResult:
        def __init__(self, matched_count: int) -> None:
            self.matched_count = matched_count

    class FakeBatches:
        def __init__(self, document: dict, race: bool = False) -> None:
            self.document = copy.deepcopy(document)
            self.race = race
            self.last_filter: dict | None = None

        def find_one(self, query: dict) -> dict:
            return copy.deepcopy(self.document)

        def update_one(self, query: dict, update: dict) -> UpdateResult:
            self.last_filter = copy.deepcopy(query)
            if self.race:
                self.document["workflow_job_id"] = "22222222-2222-2222-2222-222222222222"
                return UpdateResult(0)
            self.document.update(copy.deepcopy(update["$set"]))
            return UpdateResult(1)

    repository = object.__new__(HITL_MODULE.MongoHitlRepository)
    successful_batches = FakeBatches(preexisting)
    repository._batches = successful_batches
    attached, created = repository.register_batch(incoming, "register-cas", "request-hash-cas")
    assert created is False
    assert attached["workflow_job_id"] == job_id
    assert attached["workflow_request_id"] == request_id
    assert attached["answer_deadline_at"] == immutable_deadline
    assert successful_batches.last_filter is not None
    assert len(successful_batches.last_filter["$and"]) == 4

    racing_repository = object.__new__(HITL_MODULE.MongoHitlRepository)
    racing_repository._batches = FakeBatches(preexisting, race=True)
    try:
        racing_repository.register_batch(incoming, "register-cas", "request-hash-cas")
    except ValueError as exc:
        assert str(exc) == "BATCH_WORKFLOW_CONFLICT"
    else:
        raise AssertionError("a competing workflow attachment must fail closed")


def test_hitl_work_definition_endpoint_never_exposes_pending_action_secret_material() -> None:
    settings = HitlSettings(
        environment="test",
        storage_mode="memory",
        mongodb_uri="",
        mongodb_database="business_work_design",
        collection_prefix="",
        bearer_token="test-secret",
        allow_insecure_local=False,
        langflow_base_url="http://localhost:7860",
        langflow_api_key="server-only-key",
        langflow_f10_flow_id="22222222-2222-2222-2222-222222222222",
        resume_enabled=False,
    )
    repository = MemoryHitlRepository()
    work = _work_definition()
    work["pending_action"] = {
        "token_sha256": "must-not-leak",
        "allowed_commands": ["approve"],
        "revision": 2,
    }
    work["mutation_receipts"] = [{"request_sha256": "internal"}]
    repository.seed_work_definition(work)
    app = create_hitl_app(settings, repository)
    with TestClient(app) as client:
        response = client.get("/api/work-definitions/wd-1", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "pending_action" not in response.json()
    assert "mutation_receipts" not in response.json()
    assert "must-not-leak" not in response.text


def test_hitl_resume_409_reconciles_a_consumed_request_from_durable_job_status(monkeypatch) -> None:
    settings = HitlSettings(
        environment="test",
        storage_mode="memory",
        mongodb_uri="",
        mongodb_database="business_work_design",
        collection_prefix="",
        bearer_token="test-secret",
        allow_insecure_local=False,
        langflow_base_url="http://localhost:7860",
        langflow_api_key="server-only-key",
        langflow_f10_flow_id="22222222-2222-2222-2222-222222222222",
        resume_enabled=True,
    )

    class Response:
        def __init__(self, payload: object) -> None:
            self.status = 200
            self.body = HITL_MODULE.json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit: int) -> bytes:
            return self.body[:limit]

    class Opener:
        def open(self, request, timeout: int):
            url = request.full_url
            if url.endswith("/resume"):
                raise HITL_MODULE.urllib.error.HTTPError(
                    url,
                    409,
                    "Conflict",
                    {},
                    io.BytesIO(b'{"detail":{"code":"NOT_RESUMABLE"}}'),
                )
            if "/pending?" in url:
                return Response([])
            if "/workflows?job_id=" in url:
                return Response(
                    {
                        "job_id": "11111111-1111-1111-1111-111111111111",
                        "flow_id": settings.langflow_f10_flow_id,
                        "status": "in_progress",
                    }
                )
            raise AssertionError(url)

    monkeypatch.setattr(HITL_MODULE.urllib.request, "build_opener", lambda *args: Opener())
    result = HITL_MODULE._resume_langflow(
        settings,
        {
            "workflow_job_id": "11111111-1111-1111-1111-111111111111",
            "workflow_request_id": "human-node:request-1",
        },
    )
    assert result["status"] == "resumed_reconciled"


def test_production_settings_fail_closed() -> None:
    bad_hitl = HitlSettings(
        environment="production",
        storage_mode="memory",
        mongodb_uri="",
        mongodb_database="db",
        collection_prefix="",
        bearer_token="secret",
        allow_insecure_local=False,
        langflow_base_url="https://langflow.example.com",
        langflow_api_key="server-only-key",
        langflow_f10_flow_id="22222222-2222-2222-2222-222222222222",
        resume_enabled=True,
    )
    try:
        bad_hitl.validate()
    except RuntimeError as exc:
        assert "MongoDB" in str(exc)
    else:
        raise AssertionError("production HITL memory storage must be rejected")

    bad_report = ReportSettings(
        environment="production",
        storage_mode="memory",
        mongodb_uri="",
        mongodb_database="db",
        collection_prefix="x_",
        bearer_token="secret",
        allow_insecure_local=False,
        public_base_url="https://reports.example.com/api",
        retention_days=30,
        view_signing_secret="0123456789abcdef0123456789abcdef",
    )
    try:
        bad_report.validate()
    except RuntimeError as exc:
        assert "MongoDB" in str(exc)
    else:
        raise AssertionError("production memory storage must be rejected")

    weak_capability_secret = ReportSettings(
        environment="test",
        storage_mode="memory",
        mongodb_uri="",
        mongodb_database="db",
        collection_prefix="x_",
        bearer_token="secret",
        allow_insecure_local=False,
        public_base_url="http://localhost:8091/api",
        retention_days=30,
        view_signing_secret="too-short",
    )
    try:
        weak_capability_secret.validate()
    except RuntimeError as exc:
        assert "REPORT_VIEW_SIGNING_SECRET" in str(exc)
    else:
        raise AssertionError("weak report capability secret must be rejected")
