from __future__ import annotations

import ast
import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from lfx.custom.custom_component.component import Component as SourceComponent
from lfx.custom.utils import build_custom_component_template


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_ROOT = PROJECT_ROOT / "components" / "catalog_ingestion"
COMPONENT_PATHS = {
    "loader": COMPONENT_ROOT / "00_catalog_json_loader.py",
    "chunker": COMPONENT_ROOT / "01_catalog_deterministic_chunker.py",
    "writer": COMPONENT_ROOT / "02_catalog_mongodb_vector_writer.py",
}


def _load_module(key: str) -> ModuleType:
    path = COMPONENT_PATHS[key]
    module_name = f"test_visible_catalog_pipeline_{key}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def modules() -> dict[str, ModuleType]:
    return {key: _load_module(key) for key in COMPONENT_PATHS}


def _write_catalog(tmp_path: Path, records: list[dict[str, Any]]) -> Path:
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"items": records}, ensure_ascii=False), encoding="utf-8")
    return path


def _load_bundle(loader: ModuleType, path: Path) -> dict[str, Any]:
    result = loader.CatalogJsonLoaderComponent(
        catalog_file=str(path),
        max_records=100,
        max_file_size_mb=10,
        max_record_chars=200000,
        max_text_chars=60000,
    ).load_catalog()
    assert result.data["ok"] is True
    return result.data


def _chunk_bundle(chunker: ModuleType, catalog_bundle: dict[str, Any]) -> dict[str, Any]:
    result = chunker.CatalogDeterministicChunkerComponent(
        catalog_bundle=catalog_bundle,
        chunk_chars=500,
        overlap_chars=20,
        max_chunks_per_record=16,
        max_total_chunks=100,
    ).build_chunks()
    assert result.data["ok"] is True
    return result.data


def _sample_records() -> list[dict[str, Any]]:
    return [
        {
            "id": "component-1",
            "title": "Mail Report Helper",
            "type": "py",
            "version": "v1.0.0",
            "description": "Owner user@example.com",
            "password": "never-store-this",
            "readme": "Read messages and create a weekly report. " * 30,
            "stars_count": 4,
            "ports": {"inputs": [{"name": "messages"}], "outputs": [{"name": "report"}]},
            "acl": {"visibility": "tenant"},
        },
        {
            "id": "flow-1",
            "title": "Weekly Report Flow",
            "type": "json",
            "version": "v2",
            "description": "Create and approve a weekly report",
        },
    ]


def test_sources_are_buildable_standalone_components_with_visible_primary_inputs(modules: dict[str, ModuleType]) -> None:
    expected = {
        "loader": ("CatalogJsonLoader", "catalog_bundle"),
        "chunker": ("CatalogDeterministicChunker", "chunk_bundle"),
        "writer": ("CatalogMongoDBVectorWriter", "ingestion_result"),
    }
    for key, path in COMPONENT_PATHS.items():
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        subclasses = [
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and any(isinstance(base, ast.Name) and base.id == "Component" for base in node.bases)
        ]
        assert len(subclasses) == 1
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.level == 0
                assert not (node.module or "").startswith(("components", "services", "business_work_design_agent"))
        template, instance = build_custom_component_template(SourceComponent(_code=source))
        assert instance.name == expected[key][0]
        assert [item["name"] for item in template["outputs"]] == [expected[key][1]]

    loader_inputs = {item.name: item for item in modules["loader"].CatalogJsonLoaderComponent.inputs}
    assert set(loader_inputs).isdisjoint({"tenant_id", "catalog_id"})
    assert not loader_inputs["catalog_file"].advanced
    chunker_inputs = {item.name: item for item in modules["chunker"].CatalogDeterministicChunkerComponent.inputs}
    assert all(not chunker_inputs[name].advanced for name in ("catalog_bundle", "chunk_chars", "overlap_chars"))
    writer_inputs = {item.name: item for item in modules["writer"].CatalogMongoDBVectorWriterComponent.inputs}
    assert {"embedding_model", "embedding_version", "embedding_dimension"}.isdisjoint(writer_inputs)
    assert all(
        not writer_inputs[name].advanced
        for name in (
            "chunk_bundle",
            "embedding",
            "mongodb_uri",
            "mongodb_database",
            "assets_collection",
            "chunks_collection",
            "pointer_collection",
            "dry_run",
        )
    )
    assert writer_inputs["dry_run"].display_name == "테스트 실행 (저장하지 않음)"
    assert "embedding_batch_size" not in writer_inputs
    assert writer_inputs["embedding_call_interval_seconds"].display_name == "임베딩 호출 간격(초)"
    assert "청크 1개씩 순차 호출" in writer_inputs["embedding_call_interval_seconds"].info
    assert writer_inputs["mongo_write_batch_size"].display_name == "MongoDB 일괄 저장 문서 수"
    assert "bulk write" in writer_inputs["mongo_write_batch_size"].info
    assert writer_inputs["mongodb_timeout_ms"].display_name == "MongoDB 연결·서버 선택 제한 시간 (ms)"
    assert "소켓 read/write는 안정성을 위해 최소 10초" in writer_inputs["mongodb_timeout_ms"].info
    assert "Embedding Model 호출 시간에는 적용되지 않습니다" in writer_inputs["mongodb_timeout_ms"].info


def test_loader_preserves_redacted_raw_parent_and_is_deterministic(
    modules: dict[str, ModuleType], tmp_path: Path
) -> None:
    loader = modules["loader"]
    path = _write_catalog(tmp_path, _sample_records())
    first = _load_bundle(loader, path)
    second = _load_bundle(loader, path)
    assert first == second
    assert first["status"] == "LOADED"
    assert first["tenant_id"] == "default"
    assert first["catalog_id"] == "internal-assets"
    assert first["counts"] == {"records": 2}
    assert len(first["source_sha256"]) == 64
    parent = first["records"][0]
    assert parent["tenant_id"] == "default"
    assert parent["catalog_id"] == "internal-assets"
    assert parent["raw_record_redacted"]["password"] == "[REDACTED:SENSITIVE_FIELD]"
    assert "never-store-this" not in parent["raw_text_redacted"]
    assert "user@example.com" not in parent["lexical_text_redacted"]
    assert parent["asset_type"] == "component"
    assert parent["technical_contract_status"] == "ports_extracted"
    assert parent["ports"]["inputs"] == [{"name": "messages"}]
    assert parent["source"]["file_sha256"] == first["source_sha256"]
    assert len(parent["raw_record_redacted_sha256"]) == len(parent["content_sha256"]) == 64


def test_chunker_exposes_policy_and_builds_deterministic_parent_chunk_contracts(
    modules: dict[str, ModuleType], tmp_path: Path
) -> None:
    catalog_bundle = _load_bundle(modules["loader"], _write_catalog(tmp_path, _sample_records()))
    first = _chunk_bundle(modules["chunker"], catalog_bundle)
    second = _chunk_bundle(modules["chunker"], catalog_bundle)
    assert first == second
    assert first["status"] == "CHUNKED"
    assert first["chunk_policy"]["chunk_chars"] == 500
    assert first["chunk_policy"]["overlap_chars"] == 20
    assert first["counts"]["records"] == 2
    assert first["counts"]["chunks"] > 2
    assert len(first["ingest_sha256"]) == 64
    parent = first["parents"][0]
    matching = [item for item in first["chunks"] if item["asset_id"] == parent["asset_id"]]
    assert parent["chunk_count"] == len(matching)
    assert parent["raw_record_redacted"]["password"] == "[REDACTED:SENSITIVE_FIELD]"
    assert "raw_record_redacted" not in matching[0]
    assert "embedding" not in matching[0]
    assert [item["chunk_ordinal"] for item in matching] == list(range(len(matching)))
    for chunk in first["chunks"]:
        assert chunk["embedding_input_sha256"] == modules["chunker"]._sha256_text(
            chunk["embedding_text_redacted"]
        )
        assert chunk["parent_content_sha256"] in {item["content_sha256"] for item in first["parents"]}


def test_writer_dry_run_is_deterministic_and_calls_neither_embedding_nor_mongodb(
    modules: dict[str, ModuleType], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _chunk_bundle(
        modules["chunker"],
        _load_bundle(modules["loader"], _write_catalog(tmp_path, _sample_records())),
    )
    writer = modules["writer"]

    class ForbiddenEmbedding:
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            raise AssertionError("dry-run must not call the Embedding Model")

    def forbidden_mongo(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("dry-run must not call MongoDB")

    sleep_calls: list[float] = []
    monkeypatch.setattr(writer, "MongoClient", forbidden_mongo)
    monkeypatch.setattr(writer.time, "sleep", lambda seconds: sleep_calls.append(seconds))
    kwargs = {
        "chunk_bundle": bundle,
        "embedding": ForbiddenEmbedding(),
        "mongodb_uri": "mongodb://must-not-be-used",
        "mongodb_database": "business_work_design",
        "assets_collection": "catalog_assets",
        "chunks_collection": "catalog_asset_chunks",
        "pointer_collection": "catalog_active_pointers",
        "dry_run": True,
    }
    first = writer.CatalogMongoDBVectorWriterComponent(**kwargs).write_catalog().data
    second = writer.CatalogMongoDBVectorWriterComponent(**kwargs).write_catalog().data
    assert first == second
    assert first["ok"] is True and first["status"] == "DRY_RUN_VALIDATED"
    assert first["execution_mode_display"] == "테스트 실행 (저장하지 않음)"
    assert first["message"] == "테스트 실행입니다. MongoDB에는 저장하지 않았습니다."
    assert first["counts"] == bundle["counts"]
    assert first["snapshot_id"] is None
    assert first["embedding_execution"] == {
        "mode": "deferred_test_run",
        "calls": 0,
        "minimum_interval_seconds": 1.0,
    }
    assert sleep_calls == []
    assert first["embedding_contract"] == {
        "schema_version": "embedding-runtime-contract/v2",
        "runtime_class": None,
        "model_id": None,
        "dimension": None,
        "fingerprint": None,
        "state": "DEFERRED",
    }


def test_runtime_contract_uses_langflow_wrapper_identity_before_allowed_underlying_fallback(
    modules: dict[str, ModuleType]
) -> None:
    writer = modules["writer"]

    class UnderlyingEmbedding:
        model_name = "fallback-model-must-not-win"

    class EmbeddingsWithModelsLike:
        def __init__(self) -> None:
            self.embeddings = UnderlyingEmbedding()
            self.available_models = {
                "configured-primary-model": self.embeddings,
                "another-model": UnderlyingEmbedding(),
            }

    embedding = EmbeddingsWithModelsLike()
    contract = writer._embedding_runtime_contract(embedding, 3)
    assert contract["schema_version"] == "embedding-runtime-contract/v2"
    assert contract["runtime_class"] == f"{UnderlyingEmbedding.__module__}.{UnderlyingEmbedding.__qualname__}"
    assert contract["model_id"] == "configured-primary-model"
    assert contract["dimension"] == 3
    assert contract["fingerprint"] == "sha256:" + writer._sha256_text(
        writer._canonical_json(
            {
                "schema_version": "embedding-runtime-contract/v2",
                "runtime_class": contract["runtime_class"],
                "model_id": "configured-primary-model",
                "dimension": 3,
            }
        )
    )
    assert writer._validate_embedding_contract(contract) == contract


def test_live_writer_fails_closed_before_mongodb_when_runtime_model_id_cannot_be_resolved(
    modules: dict[str, ModuleType], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _chunk_bundle(
        modules["chunker"],
        _load_bundle(modules["loader"], _write_catalog(tmp_path, _sample_records())),
    )
    writer = modules["writer"]
    embedding_calls: list[int] = []

    class UnidentifiedEmbedding:
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            embedding_calls.append(len(texts))
            return [[0.1, 0.9] for _ in texts]

    def forbidden_mongo(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("unidentified embedding runtime must fail before MongoDB")

    monkeypatch.setattr(writer, "MongoClient", forbidden_mongo)
    result = writer.CatalogMongoDBVectorWriterComponent(
        chunk_bundle=bundle,
        embedding=UnidentifiedEmbedding(),
        mongodb_uri="mongodb://must-not-be-used",
        mongodb_database="business_work_design",
        assets_collection="catalog_assets",
        chunks_collection="catalog_asset_chunks",
        pointer_collection="catalog_active_pointers",
        dry_run=False,
    ).write_catalog().data
    assert embedding_calls
    assert result["ok"] is False
    assert result["error"]["code"] == "CATALOG_PIPELINE_INVALID"
    assert "resolvable model ID" in result["error"]["message"]


def test_embedding_batches_must_match_first_runtime_dimension_and_contain_only_finite_values(
    modules: dict[str, ModuleType]
) -> None:
    writer = modules["writer"]

    class ChangingDimensionEmbedding:
        def __init__(self) -> None:
            self.calls = 0

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            self.calls += 1
            return [[0.25, 0.75] for _ in texts] if self.calls == 1 else [[0.1, 0.2, 0.7] for _ in texts]

    embedding = ChangingDimensionEmbedding()
    vectors, dimension = writer._embed_documents(embedding, ["first", "batch"])
    assert vectors == [[0.25, 0.75], [0.25, 0.75]]
    assert dimension == 2
    with pytest.raises(ValueError, match="dimension"):
        writer._embed_documents(embedding, ["second"], dimension)
    with pytest.raises(ValueError, match="non-finite"):
        writer._vectors([[float("nan"), 0.0]], 1, 2)


@pytest.mark.parametrize("response_shape", ["flat", "nested"])
def test_writer_retries_one_chunk_at_a_time_for_unambiguous_single_vector_batch_responses(
    modules: dict[str, ModuleType], response_shape: str
) -> None:
    writer = modules["writer"]
    calls: list[list[str]] = []

    class SingleVectorOnlyEmbedding:
        def embed_documents(self, texts: list[str]) -> list[float] | list[list[float]]:
            calls.append(list(texts))
            vector = [0.25, 0.75]
            return vector if response_shape == "flat" else [vector]

    vectors, dimension = writer._embed_documents(
        SingleVectorOnlyEmbedding(),
        ["first", "second", "third"],
    )
    assert vectors == [[0.25, 0.75], [0.25, 0.75], [0.25, 0.75]]
    assert dimension == 2
    assert calls == [["first", "second", "third"], ["first"], ["second"], ["third"]]


def test_writer_rejects_partial_multi_vector_batch_response_without_reassigning_vectors(
    modules: dict[str, ModuleType]
) -> None:
    writer = modules["writer"]

    class PartialBatchEmbedding:
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[0.25, 0.75], [0.25, 0.75]]

    with pytest.raises(ValueError, match="count"):
        writer._embed_documents(PartialBatchEmbedding(), ["first", "second", "third"])


@pytest.mark.parametrize("tamper", ["chunk_metadata", "ingest_hash", "raw_text", "source"])
def test_writer_dry_run_rejects_tampered_handoff_contracts(
    modules: dict[str, ModuleType], tmp_path: Path, tamper: str
) -> None:
    bundle = _chunk_bundle(
        modules["chunker"],
        _load_bundle(modules["loader"], _write_catalog(tmp_path, _sample_records())),
    )
    altered = copy.deepcopy(bundle)
    if tamper == "chunk_metadata":
        altered["chunks"][0]["acl"] = {"visibility": "private", "groups": [], "subjects": ["attacker"]}
    elif tamper == "ingest_hash":
        altered["ingest_sha256"] = "0" * 64
    elif tamper == "raw_text":
        altered["parents"][0]["raw_text_redacted"] += "tampered"
    else:
        altered["parents"][0]["source"]["file_sha256"] = "f" * 64
    result = modules["writer"].CatalogMongoDBVectorWriterComponent(
        chunk_bundle=altered,
        embedding=None,
        mongodb_uri="",
        mongodb_database="business_work_design",
        assets_collection="catalog_assets",
        chunks_collection="catalog_asset_chunks",
        pointer_collection="catalog_active_pointers",
        dry_run=True,
    ).write_catalog().data
    assert result["ok"] is False
    assert result["error"]["code"] == "CATALOG_PIPELINE_INVALID"


def test_pure_final_document_builder_preserves_f20_storage_contract(
    modules: dict[str, ModuleType], tmp_path: Path
) -> None:
    bundle = _chunk_bundle(
        modules["chunker"],
        _load_bundle(modules["loader"], _write_catalog(tmp_path, _sample_records())),
    )
    writer = modules["writer"]

    class FakeEmbedding:
        model_name = "embedding-model-a"

    contract = writer._embedding_runtime_contract(FakeEmbedding(), 2)
    snapshot_id = writer._snapshot_id(bundle, contract)
    parents, chunks = writer._build_stored_documents(
        bundle,
        contract,
        snapshot_id,
        [[0.25, 0.75] for _ in bundle["chunks"]],
    )
    assert all(item["snapshot_id"] == snapshot_id for item in parents + chunks)
    assert all(item["embedding_manifest"]["complete"] is True for item in parents)
    assert all(item["embedding"]["vector"] == [0.25, 0.75] for item in chunks)
    assert all(item["embedding_manifest"]["embedding_contract"] == contract for item in parents)
    assert all(item["embedding"]["contract"] == contract for item in chunks)
    assert all(item["embedding"]["input_sha256"] == item["embedding_input_sha256"] for item in chunks)
    assert parents[0]["raw_text_redacted"] == bundle["parents"][0]["raw_text_redacted"]


def test_writer_stores_f20_compatible_vectors_and_activates_pointer_last(
    modules: dict[str, ModuleType], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _chunk_bundle(
        modules["chunker"],
        _load_bundle(modules["loader"], _write_catalog(tmp_path, _sample_records())),
    )
    writer = modules["writer"]
    events: list[str] = []

    class FakeEmbedding:
        model_name = "embedding-model-a"

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            events.append(f"embed:{len(texts)}")
            return [[0.25, 0.75] for _ in texts]

    class FakeCollection:
        def __init__(self, name: str) -> None:
            self.name = name
            self.documents: list[dict[str, Any]] = []
            self.pointer: dict[str, Any] | None = None

        def create_index(self, *args: Any, **kwargs: Any) -> str:
            events.append(f"index:{self.name}")
            return "index"

        def count_documents(self, query: dict[str, Any]) -> int:
            return len(self.documents)

        def update_one(self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False) -> None:
            assert upsert is True
            self.pointer = dict(update["$set"])
            events.append(f"pointer:{self.name}")

    class FakeDatabase:
        def __init__(self) -> None:
            self.collections: dict[str, FakeCollection] = {}

        def __getitem__(self, name: str) -> FakeCollection:
            self.collections.setdefault(name, FakeCollection(name))
            return self.collections[name]

    class FakeAdmin:
        def command(self, name: str) -> dict[str, int]:
            assert name == "ping"
            return {"ok": 1}

    class FakeClient:
        def __init__(self) -> None:
            events.append("mongo-client")
            self.admin = FakeAdmin()
            self.database = FakeDatabase()

        def __getitem__(self, name: str) -> FakeDatabase:
            assert name == "business_work_design"
            return self.database

        def close(self) -> None:
            events.append("close")

    client_holder: dict[str, FakeClient] = {}
    chunk_write_sizes: list[int] = []

    def mongo_factory(*args: Any, **kwargs: Any) -> FakeClient:
        client = FakeClient()
        client_holder["client"] = client
        return client

    def fake_bulk_replace(collection: FakeCollection, documents: list[dict[str, Any]], batch_size: int) -> None:
        assert batch_size > 0
        collection.documents.extend(dict(item) for item in documents)
        if collection.name == "catalog_asset_chunks":
            chunk_write_sizes.append(len(documents))
        events.append(f"bulk:{collection.name}")

    sleep_calls: list[float] = []
    monkeypatch.setattr(writer, "MongoClient", mongo_factory)
    monkeypatch.setattr(writer, "_bulk_replace", fake_bulk_replace)
    monkeypatch.setattr(writer.time, "sleep", lambda seconds: sleep_calls.append(seconds))
    result = writer.CatalogMongoDBVectorWriterComponent(
        chunk_bundle=bundle,
        embedding=FakeEmbedding(),
        mongodb_uri="mongodb://secret-from-global",
        mongodb_database="business_work_design",
        assets_collection="catalog_assets",
        chunks_collection="catalog_asset_chunks",
        pointer_collection="catalog_active_pointers",
        dry_run=False,
        embedding_call_interval_seconds=0.25,
        mongo_write_batch_size=2,
        mongodb_timeout_ms=1000,
    ).write_catalog().data
    assert result["ok"] is True and result["status"] == "ACTIVE"
    assert result["counts"] == {**bundle["counts"], "vectors": bundle["counts"]["chunks"]}
    database = client_holder["client"].database.collections
    stored_chunks = database["catalog_asset_chunks"].documents
    stored_parents = database["catalog_assets"].documents
    assert len(stored_chunks) == bundle["counts"]["chunks"]
    assert len(stored_parents) == bundle["counts"]["records"]
    assert stored_chunks[0]["embedding"]["vector"] == [0.25, 0.75]
    assert stored_chunks[0]["embedding"]["input_sha256"] == stored_chunks[0]["embedding_input_sha256"]
    assert stored_chunks[0]["embedding"]["contract"]["model_id"] == "embedding-model-a"
    assert stored_chunks[0]["embedding"]["contract"] == result["embedding_contract"]
    assert stored_parents[0]["raw_record_redacted"]["password"] == "[REDACTED:SENSITIVE_FIELD]"
    assert stored_parents[0]["embedding_manifest"]["complete"] is True
    assert stored_parents[0]["embedding_manifest"]["embedding_contract"] == result["embedding_contract"]
    pointer_collection = database["catalog_active_pointers"]
    assert pointer_collection.pointer is not None
    assert pointer_collection.pointer["snapshot_id"] == pointer_collection.pointer["active_snapshot_id"] == result["snapshot_id"]
    assert pointer_collection.pointer["embedding_contract"] == result["embedding_contract"]
    assert result["embedding_contract"]["schema_version"] == "embedding-runtime-contract/v2"
    assert result["embedding_contract"]["dimension"] == 2
    embedding_events = [event for event in events if event.startswith("embed:")]
    assert embedding_events == ["embed:1"] * bundle["counts"]["chunks"]
    assert sleep_calls == [1.0] * (bundle["counts"]["chunks"] - 1)
    expected_chunk_write_sizes = [2] * (bundle["counts"]["chunks"] // 2)
    if bundle["counts"]["chunks"] % 2:
        expected_chunk_write_sizes.append(1)
    assert chunk_write_sizes == expected_chunk_write_sizes
    assert result["embedding_execution"] == {
        "mode": "sequential_one_chunk_per_call",
        "calls": bundle["counts"]["chunks"],
        "minimum_interval_seconds": 1.0,
    }
    assert events.index("embed:1") < events.index("mongo-client")
    assert events.index("bulk:catalog_assets") < events.index("pointer:catalog_active_pointers")
    assert all(
        events.index(event) < events.index("pointer:catalog_active_pointers")
        for event in events
        if event.startswith("bulk:catalog_asset_chunks")
    )
    assert events[-2:] == ["pointer:catalog_active_pointers", "close"]


def test_writer_never_activates_pointer_when_parent_storage_fails(
    modules: dict[str, ModuleType], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _chunk_bundle(
        modules["chunker"],
        _load_bundle(modules["loader"], _write_catalog(tmp_path, _sample_records())),
    )
    writer = modules["writer"]
    events: list[str] = []

    class FakeEmbedding:
        model_name = "embedding-model-a"

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[0.1, 0.9] for _ in texts]

    class FakeCollection:
        def __init__(self, name: str) -> None:
            self.name = name

        def create_index(self, *args: Any, **kwargs: Any) -> str:
            return "index"

        def count_documents(self, query: dict[str, Any]) -> int:
            return 0

        def update_one(self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False) -> None:
            events.append("POINTER_MUST_NOT_RUN")

    class FakeDatabase:
        def __init__(self) -> None:
            self.collections: dict[str, FakeCollection] = {}

        def __getitem__(self, name: str) -> FakeCollection:
            self.collections.setdefault(name, FakeCollection(name))
            return self.collections[name]

    class FakeClient:
        def __init__(self) -> None:
            self.admin = self
            self.database = FakeDatabase()

        def command(self, name: str) -> dict[str, int]:
            return {"ok": 1}

        def __getitem__(self, name: str) -> FakeDatabase:
            return self.database

        def close(self) -> None:
            events.append("close")

    def failing_bulk(collection: FakeCollection, documents: list[dict[str, Any]], batch_size: int) -> None:
        events.append(f"bulk:{collection.name}")
        if collection.name == "catalog_assets":
            raise writer.PyMongoError("parent write failed")

    monkeypatch.setattr(writer, "MongoClient", lambda *args, **kwargs: FakeClient())
    monkeypatch.setattr(writer, "_bulk_replace", failing_bulk)
    monkeypatch.setattr(writer.time, "sleep", lambda seconds: None)
    result = writer.CatalogMongoDBVectorWriterComponent(
        chunk_bundle=bundle,
        embedding=FakeEmbedding(),
        mongodb_uri="mongodb://secret-from-global",
        mongodb_database="business_work_design",
        assets_collection="catalog_assets",
        chunks_collection="catalog_asset_chunks",
        pointer_collection="catalog_active_pointers",
        dry_run=False,
    ).write_catalog().data
    assert result["ok"] is False and result["error"]["code"] == "MONGODB_INGEST_FAILED"
    assert "bulk:catalog_asset_chunks" in events
    assert "bulk:catalog_assets" in events
    assert "POINTER_MUST_NOT_RUN" not in events
    assert events[-1] == "close"
