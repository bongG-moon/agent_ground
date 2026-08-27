from __future__ import annotations

import importlib.util
import hashlib
import json
import math
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_PATH = PROJECT_ROOT / "components" / "hybrid_retrieval" / "29_search_query_embedding_batcher.py"


def load_component() -> ModuleType:
    module_name = "test_query_embedding_batcher_runtime"
    spec = importlib.util.spec_from_file_location(module_name, COMPONENT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def module() -> ModuleType:
    return load_component()


def seal_plan(plan: dict[str, Any]) -> dict[str, Any]:
    core = {key: value for key, value in plan.items() if key != "query_plan_sha256"}
    plan["query_plan_sha256"] = "sha256:" + hashlib.sha256(
        json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return plan


def query_plan() -> dict[str, Any]:
    return seal_plan({
        "schema_version": "search-query-plan/v1",
        "design_scope_sha256": "sha256:" + "b" * 64,
        "queries": [
            {"query_id": "exact-outlook", "kind": "exact", "text": "Outlook"},
            {"query_id": "semantic-report", "kind": "semantic", "text": "메일을 근거로 주간 업무보고 초안을 생성"},
        ],
    })


def build(module: ModuleType, **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "query_plan": query_plan(),
        "provider_mode": "precomputed",
        "precomputed_vectors": {
            "vectors": {
                "exact-outlook": [0, 0.25, -0.5],
                "semantic-report": [1, 0.125, 0.75],
            }
        },
        "embedding_endpoint": "",
        "api_token": "",
        "model": "approved-embedding-model",
        "model_version": "2026-08-01",
        "dimension": 3,
        "batch_size": 16,
        "max_queries": 30,
        "timeout_seconds": 1,
        "max_response_bytes": 1024,
        "allowed_host": "",
        "allow_insecure_loopback": False,
    }
    kwargs.update(overrides)
    component = module.SearchQueryEmbeddingBatcherComponent(**kwargs)
    return dict(component.build_query_vectors().data)


def test_precomputed_mode_preserves_query_ids_order_dimension_and_contract(module: ModuleType) -> None:
    result = build(module)
    assert result["ok"] is True
    assert result["status"] == "VECTORIZED"
    assert result["schema_version"] == "query-vectors/v1"
    assert result["query_order"] == ["exact-outlook", "semantic-report"]
    assert list(result["vectors"]) == result["query_order"]
    assert result["vectors"]["exact-outlook"] == [0.0, 0.25, -0.5]
    assert all(len(vector) == 3 for vector in result["vectors"].values())
    assert all(math.isfinite(value) for vector in result["vectors"].values() for value in vector)
    assert result["embedding_contract"] == {
        "provider_mode": "precomputed",
        "model": "approved-embedding-model",
        "version": "2026-08-01",
        "dimension": 3,
    }
    assert result["provider_receipts"] == []
    assert result["design_scope_sha256"] == query_plan()["design_scope_sha256"]
    assert result["query_plan_sha256"] == query_plan()["query_plan_sha256"]


@pytest.mark.parametrize(
    ("vectors", "message"),
    [
        ({"exact-outlook": [0.0, 0.1, 0.2]}, "exactly match"),
        (
            {
                "exact-outlook": [0.0, 0.1, 0.2],
                "semantic-report": [0.2, 0.3, 0.4],
                "unexpected-query": [0.5, 0.6, 0.7],
            },
            "exactly match",
        ),
        (
            {"exact-outlook": [0.0, 0.1], "semantic-report": [0.2, 0.3, 0.4]},
            "dimension mismatch",
        ),
        (
            {"exact-outlook": [0.0, float("nan"), 0.2], "semantic-report": [0.2, 0.3, 0.4]},
            "non-finite",
        ),
        (
            {"exact-outlook": [0.0, float("inf"), 0.2], "semantic-report": [0.2, 0.3, 0.4]},
            "non-finite",
        ),
        (
            {"exact-outlook": [0.0, True, 0.2], "semantic-report": [0.2, 0.3, 0.4]},
            "boolean vector",
        ),
        (
            {"exact-outlook": [0.0, "not-number", 0.2], "semantic-report": [0.2, 0.3, 0.4]},
            "non-numeric",
        ),
    ],
)
def test_precomputed_mode_fails_closed_on_coverage_dimension_and_numeric_contracts(
    module: ModuleType,
    vectors: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        build(module, precomputed_vectors={"vectors": vectors})


def test_query_identity_and_provider_configuration_fail_closed(module: ModuleType) -> None:
    duplicate_plan = query_plan()
    duplicate_plan["queries"][1]["query_id"] = "exact-outlook"
    seal_plan(duplicate_plan)
    with pytest.raises(ValueError, match="unique"):
        build(module, query_plan=duplicate_plan)

    empty_text = query_plan()
    empty_text["queries"][0]["text"] = "   "
    seal_plan(empty_text)
    with pytest.raises(ValueError, match="query text"):
        build(module, query_plan=empty_text)

    with pytest.raises(ValueError, match="model and model_version"):
        build(module, model="")
    with pytest.raises(ValueError, match="model and model_version"):
        build(module, model_version="")
    with pytest.raises(ValueError, match="unsupported provider_mode"):
        build(module, provider_mode="silent-local-fallback")
    with pytest.raises(ValueError, match="precomputed_vectors"):
        build(module, precomputed_vectors=None)


def test_query_count_limit_is_enforced_before_vectorization(module: ModuleType) -> None:
    plan = seal_plan({
        "design_scope_sha256": "sha256:" + "b" * 64,
        "queries": [
            {"query_id": f"q-{index}", "text": f"query {index}"}
            for index in range(3)
        ]
    })
    with pytest.raises(ValueError, match="max_queries"):
        build(module, query_plan=plan, max_queries=2, precomputed_vectors={"vectors": {}})
