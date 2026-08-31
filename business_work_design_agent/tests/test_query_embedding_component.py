from __future__ import annotations

import hashlib
import importlib.util
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


class FakeEmbeddingRuntime:
    def __init__(self, vectors: list[list[Any]], *, model_name: str | None = "attribute-model") -> None:
        self.vectors = vectors
        self.model_name = model_name
        self.calls: list[list[str]] = []

    def embed_documents(self, texts: list[str]) -> list[list[Any]]:
        self.calls.append(list(texts))
        offset = sum(len(call) for call in self.calls[:-1])
        return self.vectors[offset : offset + len(texts)]


class QueryAwareEmbeddingRuntime(FakeEmbeddingRuntime):
    def __init__(self, vectors: list[list[Any]], *, model_name: str | None = "attribute-model") -> None:
        super().__init__(vectors, model_name=model_name)
        self.query_calls: list[str] = []

    def embed_query(self, text: str) -> list[Any]:
        self.query_calls.append(text)
        return self.vectors[len(self.query_calls) - 1]


class FlakyQueryEmbeddingRuntime(QueryAwareEmbeddingRuntime):
    def __init__(self, vectors: list[list[Any]], *, failures_before_success: int = 1) -> None:
        super().__init__(vectors)
        self.failures_before_success = failures_before_success
        self.provider_attempts = 0

    def embed_query(self, text: str) -> list[Any]:
        self.provider_attempts += 1
        if self.provider_attempts <= self.failures_before_success:
            raise OSError("temporary provider connection failure")
        return super().embed_query(text)


class FakeEmbeddingsWithModels:
    def __init__(self, runtime: FakeEmbeddingRuntime, *, available_model_id: str | None = "approved-embedding-model") -> None:
        self.embeddings = runtime
        self.available_models = {available_model_id: runtime} if available_model_id else {}

    def embed_documents(self, texts: list[str]) -> list[list[Any]]:
        return self.embeddings.embed_documents(texts)


class FailingEmbeddingRuntime:
    model_name = "broken-model"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise OSError("provider down")


class FailingEmbeddingsWithModels:
    def __init__(self) -> None:
        self.embeddings = FailingEmbeddingRuntime()
        self.available_models = {"broken-model": self.embeddings}

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embeddings.embed_documents(texts)


def runtime_embedding(
    vectors: list[list[Any]] | None = None,
    *,
    available_model_id: str | None = "approved-embedding-model",
    model_name: str | None = "attribute-model",
) -> FakeEmbeddingsWithModels:
    return FakeEmbeddingsWithModels(
        FakeEmbeddingRuntime(vectors or [[0, 0.25, -0.5], [1, 0.125, 0.75]], model_name=model_name),
        available_model_id=available_model_id,
    )


def build(module: ModuleType, **overrides: Any) -> tuple[dict[str, Any], FakeEmbeddingsWithModels]:
    embedding = overrides.pop("embedding", runtime_embedding())
    kwargs: dict[str, Any] = {
        "query_plan": query_plan(),
        "embedding": embedding,
        "batch_size": 16,
        "max_queries": 30,
        "max_embedding_retries": 2,
        "embedding_call_interval_seconds": 1.0,
    }
    kwargs.update(overrides)
    component = module.SearchQueryEmbeddingBatcherComponent(**kwargs)
    return dict(component.build_query_vectors().data), embedding


def contract_fingerprint(contract: dict[str, Any]) -> str:
    core = {key: contract[key] for key in ("schema_version", "runtime_class", "model_id", "dimension")}
    return "sha256:" + hashlib.sha256(
        json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def test_vectors_preserve_query_ids_order_and_derive_runtime_contract(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    result, embedding = build(module, batch_size=1)
    assert result["ok"] is True
    assert result["status"] == "VECTORIZED"
    assert result["schema_version"] == "query-vectors/v1"
    assert result["query_order"] == ["exact-outlook", "semantic-report"]
    assert list(result["vectors"]) == result["query_order"]
    assert result["vectors"]["exact-outlook"] == [0.0, 0.25, -0.5]
    assert all(len(vector) == 3 for vector in result["vectors"].values())
    assert all(math.isfinite(value) for vector in result["vectors"].values() for value in vector)
    assert embedding.embeddings.calls == [["Outlook"], ["메일을 근거로 주간 업무보고 초안을 생성"]]
    assert sleeps == [1.0]
    contract = result["embedding_contract"]
    assert contract["schema_version"] == "embedding-runtime-contract/v2"
    assert contract["model_id"] == "approved-embedding-model"
    assert contract["runtime_class"].endswith(".FakeEmbeddingRuntime")
    assert contract["dimension"] == 3
    assert contract["fingerprint"] == contract_fingerprint(contract)
    assert result["design_scope_sha256"] == query_plan()["design_scope_sha256"]
    assert result["query_plan_sha256"] == query_plan()["query_plan_sha256"]
    assert result["embedding_execution"] == {
        "calls": 2,
        "retry_attempts": 0,
        "minimum_interval_seconds": 1.0,
        "batch_size": 1,
    }


@pytest.mark.parametrize(
    ("vectors", "message"),
    [
        ([[0.0, 0.1, 0.2]], "count does not match"),
        ([[0.0, 0.1], [0.2, 0.3, 0.4]], "dimension is invalid"),
        ([[0.0, float("nan"), 0.2], [0.2, 0.3, 0.4]], "non-finite"),
        ([[0.0, float("inf"), 0.2], [0.2, 0.3, 0.4]], "non-finite"),
        ([[0.0, True, 0.2], [0.2, 0.3, 0.4]], "boolean"),
        ([[0.0, "not-number", 0.2], [0.2, 0.3, 0.4]], "non-numeric"),
    ],
)
def test_component_fails_closed_on_provider_vector_contracts(
    module: ModuleType,
    vectors: list[list[Any]],
    message: str,
) -> None:
    result, _ = build(module, embedding=runtime_embedding(vectors))
    assert result["ok"] is False
    assert result["status"] == "BLOCKED"
    assert result["error"]["code"] == "EMBEDDING_RESPONSE_INVALID"


def test_model_identity_uses_wrapper_map_then_allowlisted_runtime_fields(module: ModuleType) -> None:
    mapped, _ = build(module, embedding=runtime_embedding(available_model_id="selected-by-langflow", model_name="other"))
    assert mapped["embedding_contract"]["model_id"] == "selected-by-langflow"
    attribute, _ = build(module, embedding=runtime_embedding(available_model_id=None, model_name="runtime-attribute-model"))
    assert attribute["embedding_contract"]["model_id"] == "runtime-attribute-model"
    missing_id, _ = build(module, embedding=runtime_embedding(available_model_id=None, model_name=None))
    assert missing_id["error"]["code"] == "EMBEDDING_MODEL_CONFIGURATION_REQUIRED"
    ambiguous = runtime_embedding()
    ambiguous.available_models = {
        "model-a": ambiguous.embeddings,
        "model-b": ambiguous.embeddings,
    }
    ambiguous_result, _ = build(module, embedding=ambiguous)
    assert ambiguous_result["error"]["code"] == "EMBEDDING_MODEL_CONFIGURATION_REQUIRED"


def test_query_identity_missing_embedding_and_removed_provider_configuration_fail_closed(module: ModuleType) -> None:
    duplicate_plan = query_plan()
    duplicate_plan["queries"][1]["query_id"] = "exact-outlook"
    seal_plan(duplicate_plan)
    duplicate_result, _ = build(module, query_plan=duplicate_plan)
    assert duplicate_result["error"]["code"] == "QUERY_EMBEDDING_INPUT_INVALID"

    empty_text = query_plan()
    empty_text["queries"][0]["text"] = "   "
    seal_plan(empty_text)
    empty_result, _ = build(module, query_plan=empty_text)
    assert empty_result["error"]["code"] == "QUERY_EMBEDDING_INPUT_INVALID"

    no_model, _ = build(module, embedding=None)
    assert no_model["error"]["code"] == "EMBEDDING_MODEL_CONFIGURATION_REQUIRED"
    names = {item.name for item in module.SearchQueryEmbeddingBatcherComponent.inputs}
    assert names == {
        "query_plan",
        "embedding",
        "batch_size",
        "max_queries",
        "max_embedding_retries",
        "embedding_call_interval_seconds",
    }


def test_query_count_limit_is_enforced_before_vectorization(module: ModuleType) -> None:
    plan = seal_plan({
        "design_scope_sha256": "sha256:" + "b" * 64,
        "queries": [
            {"query_id": f"q-{index}", "text": f"query {index}"}
            for index in range(3)
        ],
    })
    embedding = runtime_embedding([[0.1, 0.2]] * 3)
    result, _ = build(module, query_plan=plan, max_queries=2, embedding=embedding)
    assert result["error"]["code"] == "QUERY_EMBEDDING_INPUT_INVALID"
    assert embedding.embeddings.calls == []


def test_provider_errors_are_not_silently_replaced(module: ModuleType) -> None:
    result, _ = build(module, embedding=FailingEmbeddingsWithModels(), max_embedding_retries=0)
    assert result["error"]["code"] == "EMBEDDING_PROVIDER_ERROR"
    assert result["error"]["retryable"] is True
    assert result["error"]["details"]["provider_failure_category"] == "provider_runtime_failure"
    assert result["error"]["details"]["provider_exception_type"].endswith("OSError")


def test_embedding_call_interval_has_a_one_second_floor(module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    result, _ = build(module, batch_size=1, embedding_call_interval_seconds=0.01)
    assert result["ok"] is True
    assert result["embedding_execution"]["minimum_interval_seconds"] == 1.0
    assert sleeps == [1.0]


def test_query_aware_runtime_uses_embed_query_with_retrieval_query_semantics(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    runtime = QueryAwareEmbeddingRuntime([[0.1, 0.2], [0.3, 0.4]])
    embedding = FakeEmbeddingsWithModels(runtime)
    result, _ = build(module, embedding=embedding)

    assert result["ok"] is True
    assert runtime.query_calls == ["Outlook", "메일을 근거로 주간 업무보고 초안을 생성"]
    assert runtime.calls == []
    assert sleeps == [1.0]
    assert result["embedding_execution"] == {
        "calls": 2,
        "retry_attempts": 0,
        "minimum_interval_seconds": 1.0,
        "batch_size": 16,
    }


def test_transient_query_provider_failure_retries_with_the_rate_limit_floor(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    runtime = FlakyQueryEmbeddingRuntime([[0.1, 0.2], [0.3, 0.4]])
    embedding = FakeEmbeddingsWithModels(runtime)
    result, _ = build(module, embedding=embedding, max_embedding_retries=1)

    assert result["ok"] is True
    assert runtime.provider_attempts == 3
    assert runtime.query_calls == ["Outlook", "메일을 근거로 주간 업무보고 초안을 생성"]
    # One retry for the first query, then the floor before the second query.
    assert sleeps == [1.0, 1.0]
    assert result["embedding_execution"]["calls"] == 3
    assert result["embedding_execution"]["retry_attempts"] == 1
