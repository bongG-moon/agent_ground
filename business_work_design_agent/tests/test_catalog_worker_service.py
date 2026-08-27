from __future__ import annotations

import hashlib
import importlib.util
import time
from pathlib import Path
from types import ModuleType
from typing import Any

from fastapi.testclient import TestClient

from services.catalog_worker.app import (
    MemoryApprovalRepository,
    Settings,
    _load_component,
    _sign_activation_attestation,
    run_catalog_pipeline,
    create_app,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_ROOT = PROJECT_ROOT / "components" / "catalog_ingestion"
AUTH_HEADERS = {
    "Authorization": "Bearer test-secret",
    "X-Tenant-ID": "tenant-a",
    "X-Actor-ID": "admin-a",
}


def _settings() -> Settings:
    return Settings(
        environment="test",
        storage_mode="memory",
        mongodb_uri="",
        mongodb_database="business_work_design",
        collection_prefix="",
        bearer_token="test-secret",
        allow_insecure_local=False,
        component_root=str(COMPONENT_ROOT),
        max_stage_invocations=400,
        max_total_seconds=1800,
        stage_timeout_seconds=30,
        approval_ttl_seconds=900,
        embedding_endpoint="https://embedding.example/v1/embeddings",
        embedding_approved_hosts="embedding.example",
        embedding_api_key="server-only-key",
        embedding_model="embed-model",
        embedding_version="v1",
        embedding_dimension=2,
        embedding_allow_insecure_http=False,
        approval_attestation_secret="test-attestation-secret-32-bytes-minimum-value",
    )


def _activation_attestation(
    report: dict[str, Any],
    *,
    actor_id: str = "admin-a",
    jti: str = "approval-attestation-0001",
    expected_previous_snapshot_id: str = "",
) -> str:
    now = int(time.time())
    return _sign_activation_attestation(
        _settings().approval_attestation_secret,
        {
            "schema_version": "catalog-activation-attestation/v1",
            "decision": "activate_snapshot",
            "tenant_id": "tenant-a",
            "actor_id": actor_id,
            "snapshot_id": str(report["snapshot_id"]),
            "job_id": str(report["job_id"]),
            "validation_hash": str(report["validation_hash"]),
            "expected_previous_snapshot_id": expected_previous_snapshot_id,
            "iat": now,
            "exp": now + 300,
            "jti": jti,
        },
    )


def _job_ref(stage: str = "SECRET_SCAN_PASSED", cursor: int = 0) -> dict[str, Any]:
    return {
        "tenant_id": "tenant-a",
        "job_id": "job-a",
        "snapshot_id": "snapshot-a",
        "stage": stage,
        "expected_cursor": cursor,
        "trace_id": "trace-a",
    }


def _next(current: dict[str, Any], stage: str, cursor: int) -> dict[str, Any]:
    return {**current, "stage": stage, "expected_cursor": cursor}


def test_pure_pipeline_repeats_partial_stages_until_validated() -> None:
    calls: list[str] = []

    def invoke(route: str, current: dict[str, Any]) -> dict[str, Any]:
        calls.append(route)
        stage = current["stage"]
        transitions = {
            "SECRET_SCAN_PASSED": "PARSE_PARTIAL",
            "PARSE_PARTIAL": "PARSE_COMPLETED",
            "PARSE_COMPLETED": "NORMALIZE_COMPLETED",
            "NORMALIZE_COMPLETED": "TEXT_BUILD_COMPLETED",
            "TEXT_BUILD_COMPLETED": "EMBEDDING_PARTIAL",
            "EMBEDDING_PARTIAL": "EMBEDDING_COMPLETED",
            "EMBEDDING_COMPLETED": "SNAPSHOT_WRITE_PARTIAL",
            "SNAPSHOT_WRITE_PARTIAL": "SNAPSHOT_WRITE_COMPLETED",
        }
        if stage == "SNAPSHOT_WRITE_COMPLETED":
            return {
                "ok": True,
                "status": "VALIDATED",
                "tenant_id": current["tenant_id"],
                "job_id": current["job_id"],
                "snapshot_id": current["snapshot_id"],
                "validation_hash": "a" * 64,
                "trace_id": current["trace_id"],
            }
        next_ref = _next(current, transitions[stage], current["expected_cursor"] + 1000)
        if route == "write":
            return {"ok": True, "status": "BUILDING", "job_ref": next_ref, "trace_id": current["trace_id"]}
        return next_ref

    result = run_catalog_pipeline(
        _job_ref(),
        invoke,
        max_stage_invocations=20,
        max_total_seconds=60,
    )
    assert result["ok"] is True
    assert result["status"] == "VALIDATED"
    assert result["invocation_count"] == 9
    assert calls == ["parse", "parse", "normalize", "text", "embed", "embed", "write", "write", "validate"]


def test_pure_pipeline_stops_at_invocation_limit_with_latest_durable_ref() -> None:
    def always_partial(route: str, current: dict[str, Any]) -> dict[str, Any]:
        assert route == "parse"
        return _next(current, "PARSE_PARTIAL", current["expected_cursor"] + 2000)

    result = run_catalog_pipeline(
        _job_ref(),
        always_partial,
        max_stage_invocations=3,
        max_total_seconds=60,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "CATALOG_PIPELINE_INVOCATION_LIMIT"
    assert result["job_ref"]["stage"] == "PARSE_PARTIAL"
    assert result["job_ref"]["expected_cursor"] == 6000
    assert result["invocation_count"] == 3


def test_pure_pipeline_fails_closed_on_scope_change_and_timeout() -> None:
    changed = run_catalog_pipeline(
        _job_ref(),
        lambda _route, current: {**current, "tenant_id": "tenant-b", "stage": "PARSE_PARTIAL"},
        max_stage_invocations=2,
        max_total_seconds=60,
    )
    assert changed["error"]["code"] == "CATALOG_STAGE_SCOPE_MISMATCH"

    def timeout(_route: str, _current: dict[str, Any]) -> dict[str, Any]:
        raise TimeoutError

    timed_out = run_catalog_pipeline(
        _job_ref(),
        timeout,
        max_stage_invocations=2,
        max_total_seconds=60,
    )
    assert timed_out["error"]["code"] == "CATALOG_STAGE_TIMEOUT"
    assert timed_out["error"]["retryable"] is True


def test_pure_pipeline_caps_stage_timeout_by_remaining_total_deadline() -> None:
    class TimedInvoker:
        def __init__(self) -> None:
            self.remaining: list[float] = []

        def __call__(self, _route: str, _current: dict[str, Any]) -> dict[str, Any]:
            raise AssertionError("timed invoker path should be selected")

        def invoke_with_timeout(self, _route: str, current: dict[str, Any], remaining: float) -> dict[str, Any]:
            self.remaining.append(remaining)
            return _next(current, "PARSE_PARTIAL", 1)

    invoker = TimedInvoker()
    result = run_catalog_pipeline(
        _job_ref(),
        invoker,
        max_stage_invocations=1,
        max_total_seconds=17,
        monotonic=lambda: 5.0,
    )
    assert result["error"]["code"] == "CATALOG_PIPELINE_INVOCATION_LIMIT"
    assert invoker.remaining == [17.0]


def test_worker_loads_existing_standalone_component_by_exact_file_path() -> None:
    component_class, method_name, input_name = _load_component(str(COMPONENT_ROOT), "parse")
    assert component_class.__name__ == "CatalogStreamParserComponent"
    assert method_name == "parse_catalog"
    assert input_name == "job_ref"


def test_memory_pipeline_lease_is_exclusive_and_owner_scoped() -> None:
    repository = MemoryApprovalRepository()
    assert repository.acquire_pipeline_lease("tenant-a", "job-a", "owner-1", 60) is True
    assert repository.acquire_pipeline_lease("tenant-a", "job-a", "owner-2", 60) is False
    assert repository.heartbeat_pipeline_lease("tenant-a", "job-a", "owner-2", 60) is False
    assert repository.heartbeat_pipeline_lease("tenant-a", "job-a", "owner-1", 60) is True
    repository.release_pipeline_lease("tenant-a", "job-a", "owner-2")
    assert repository.acquire_pipeline_lease("tenant-a", "job-a", "owner-2", 60) is False
    repository.release_pipeline_lease("tenant-a", "job-a", "owner-1")
    assert repository.acquire_pipeline_lease("tenant-a", "job-a", "owner-2", 60) is True


def _validation_report() -> dict[str, Any]:
    return {
        "ok": True,
        "status": "VALIDATED",
        "tenant_id": "tenant-a",
        "snapshot_id": "snapshot-a",
        "job_id": "job-a",
        "validation_hash": "a" * 64,
        "trace_id": "trace-a",
    }


def test_worker_api_is_authenticated_tenant_scoped_and_runs_pipeline() -> None:
    repository = MemoryApprovalRepository()

    def runner(route: str, current: dict[str, Any]) -> dict[str, Any]:
        assert route == "validate"
        return _validation_report()

    app = create_app(_settings(), repository, runner)
    with TestClient(app) as client:
        completed = client.post(
            "/api/catalog/pipeline/run",
            headers=AUTH_HEADERS,
            json={"job_ref": _job_ref("SNAPSHOT_WRITE_COMPLETED", 30000)},
        )
        assert completed.status_code == 200
        assert completed.json()["status"] == "VALIDATED"
        wrong_tenant = client.post(
            "/api/catalog/pipeline/run",
            headers={**AUTH_HEADERS, "X-Tenant-ID": "tenant-b"},
            json={"job_ref": _job_ref("SNAPSHOT_WRITE_COMPLETED", 30000)},
        )
        assert wrong_tenant.status_code == 403
        unauthenticated = client.post(
            "/api/catalog/pipeline/run",
            headers={key: value for key, value in AUTH_HEADERS.items() if key != "Authorization"},
            json={"job_ref": _job_ref("SNAPSHOT_WRITE_COMPLETED", 30000)},
        )
        assert unauthenticated.status_code == 401


def test_raw_nonce_issuer_is_not_a_public_http_surface() -> None:
    repository = MemoryApprovalRepository()
    report = _validation_report()
    repository.seed_snapshot(
        {
            "tenant_id": "tenant-a",
            "snapshot_id": "snapshot-a",
            "job_id": "job-a",
            "status": "VALIDATED",
            "validation_hash": "a" * 64,
            "validation": report,
        }
    )
    app = create_app(_settings(), repository, lambda _route, _ref: {})
    with TestClient(app) as client:
        assert "/api/catalog/snapshots/{snapshot_id}/activation-approvals" not in client.get("/openapi.json").json()["paths"]
        issued = client.post(
            "/api/catalog/snapshots/snapshot-a/activation-approvals",
            headers={**AUTH_HEADERS, "Idempotency-Key": "approval-request-0001"},
            json={"validation_report": report, "approval_trigger": "approve"},
        )
        assert issued.status_code == 404
        assert not repository._approvals


def test_combined_activation_rejects_unpersisted_snapshot_even_with_attestation() -> None:
    repository = MemoryApprovalRepository()
    app = create_app(_settings(), repository, lambda _route, _ref: {})
    with TestClient(app) as client:
        missing = client.post(
            "/api/catalog/snapshots/snapshot-a/activate",
            headers={**AUTH_HEADERS, "Idempotency-Key": "approval-request-0002"},
            json={
                "validation_report": _validation_report(),
                "approval_trigger": "approve",
                "approval_attestation": _activation_attestation(_validation_report()),
            },
        )
        assert missing.status_code == 409


def test_combined_activation_keeps_nonce_off_langflow_facing_response() -> None:
    repository = MemoryApprovalRepository()
    report = _validation_report()
    repository.seed_snapshot(
        {
            "tenant_id": "tenant-a",
            "snapshot_id": "snapshot-a",
            "job_id": "job-a",
            "status": "VALIDATED",
            "validation_hash": "a" * 64,
            "validation": report,
        }
    )
    captured: dict[str, str] = {}

    def activate(
        incoming_report: dict[str, Any],
        trigger: str,
        actor: str,
        approval_id: str,
        nonce: str,
        expected_previous: str,
    ) -> dict[str, Any]:
        assert incoming_report["validation_hash"] == "a" * 64
        assert trigger == "approve"
        assert actor == "admin-a"
        assert approval_id.startswith("cap-")
        assert len(nonce) >= 32
        assert expected_previous == ""
        captured["nonce"] = nonce
        pointer = {
            "ok": True,
            "tenant_id": "tenant-a",
            "active_snapshot_id": "snapshot-a",
            "rollback_snapshot_id": None,
            "embedding_contract": {"model": "embed-model", "version": "v1", "dimension": 2},
            "validation_hash": "a" * 64,
            "trace_id": "trace-a",
        }
        repository.seed_active_pointer(pointer)
        return pointer

    app = create_app(_settings(), repository, lambda _route, _ref: {}, activate)
    attestation = _activation_attestation(report)
    with TestClient(app) as client:
        response = client.post(
            "/api/catalog/snapshots/snapshot-a/activate",
            headers={**AUTH_HEADERS, "Idempotency-Key": "activation-request-0001"},
            json={
                "validation_report": report,
                "approval_trigger": "approve",
                "expected_previous_snapshot_id": None,
                "approval_attestation": attestation,
            },
        )
        assert response.status_code == 200
        public = response.json()
        assert public["status"] == "ACTIVE"
        assert public["active_snapshot_id"] == "snapshot-a"
        assert "approval_nonce" not in public
        assert "approval_id" not in public
        assert "validation_hash" not in public
        assert captured["nonce"] not in repr(public)
        replay = client.post(
            "/api/catalog/snapshots/snapshot-a/activate",
            headers={**AUTH_HEADERS, "Idempotency-Key": "activation-request-0001"},
            json={
                "validation_report": report,
                "approval_trigger": "approve",
                "expected_previous_snapshot_id": None,
                "approval_attestation": attestation,
            },
        )
        assert replay.status_code == 200
        assert replay.json()["idempotent_replay"] is True
        assert "approval_nonce" not in replay.json()
        assert repository.snapshot_for_approval("tenant-a", "snapshot-a", "job-a")["status"] == "ACTIVE"
        reused_claim = client.post(
            "/api/catalog/snapshots/snapshot-a/activate",
            headers={**AUTH_HEADERS, "Idempotency-Key": "activation-request-0002"},
            json={
                "validation_report": report,
                "approval_trigger": "approve",
                "expected_previous_snapshot_id": None,
                "approval_attestation": attestation,
            },
        )
        assert reused_claim.status_code == 409
        assert reused_claim.json()["detail"] == "ATTESTATION_REPLAY"

        rebound_previous = client.post(
            "/api/catalog/snapshots/snapshot-a/activate",
            headers={**AUTH_HEADERS, "Idempotency-Key": "activation-request-0003"},
            json={
                "validation_report": report,
                "approval_trigger": "approve",
                "expected_previous_snapshot_id": "snapshot-old",
                "approval_attestation": attestation,
            },
        )
        assert rebound_previous.status_code == 422


def test_lost_activation_nonce_requires_new_gateway_attestation_and_idempotency_key() -> None:
    repository = MemoryApprovalRepository()
    report = _validation_report()
    repository.seed_snapshot(
        {
            "tenant_id": "tenant-a",
            "snapshot_id": "snapshot-a",
            "job_id": "job-a",
            "status": "VALIDATED",
            "validation_hash": "a" * 64,
            "validation": report,
        }
    )

    def fail_activation(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("simulated response loss before pointer switch")

    app = create_app(_settings(), repository, lambda _route, _ref: {}, fail_activation)
    body = {
        "validation_report": report,
        "approval_trigger": "approve",
        "expected_previous_snapshot_id": None,
        "approval_attestation": _activation_attestation(report, jti="approval-attestation-lost-0001"),
    }
    with TestClient(app) as client:
        first = client.post(
            "/api/catalog/snapshots/snapshot-a/activate",
            headers={**AUTH_HEADERS, "Idempotency-Key": "activation-lost-0001"},
            json=body,
        )
        assert first.status_code == 409
        replay = client.post(
            "/api/catalog/snapshots/snapshot-a/activate",
            headers={**AUTH_HEADERS, "Idempotency-Key": "activation-lost-0001"},
            json=body,
        )
        assert replay.status_code == 409
        assert "new attestation JTI" in replay.json()["detail"]
        assert "new idempotency key" in replay.json()["detail"]


def _load_client(filename: str) -> ModuleType:
    path = COMPONENT_ROOT / filename
    spec = importlib.util.spec_from_file_location("test_" + path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_standalone_clients_enforce_https_allowlist_and_no_redirect_handler() -> None:
    pipeline_client = _load_client("09_catalog_pipeline_worker_client.py")
    approval_client = _load_client("33_catalog_activation_approval_client.py")
    approved = {"worker.example:443"}
    assert pipeline_client._validate_server_url("https://worker.example:443/api", approved).startswith("https://")
    assert approval_client._validate_server_url("https://worker.example:443/api", approved).startswith("https://")
    for module in (pipeline_client, approval_client):
        try:
            module._validate_server_url("http://worker.example/api", {"worker.example"})
        except ValueError as exc:
            assert "HTTPS" in str(exc)
        else:
            raise AssertionError("non-loopback HTTP must be rejected")
        try:
            module._validate_server_url("https://other.example/api", {"worker.example"})
        except ValueError as exc:
            assert "allowlist" in str(exc)
        else:
            raise AssertionError("non-allowlisted host must be rejected")
