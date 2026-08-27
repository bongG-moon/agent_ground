from __future__ import annotations

import ast
import hashlib
import importlib.util
import inspect
import io
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_ROOT = PROJECT_ROOT / "components" / "catalog_ingestion"
COMPONENT_FILES = sorted(COMPONENT_ROOT.glob("[0-9][0-9]_*.py"))


@pytest.fixture(scope="session")
def modules() -> dict[str, ModuleType]:
    loaded: dict[str, ModuleType] = {}
    for path in COMPONENT_FILES:
        spec = importlib.util.spec_from_file_location(f"catalog_component_{path.stem}", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        loaded[path.name] = module
    return loaded


def test_exactly_eleven_standalone_component_files() -> None:
    assert [path.name for path in COMPONENT_FILES] == [
        "00_catalog_file_intake.py",
        "01_catalog_secret_scanner.py",
        "02_catalog_stream_parser.py",
        "03_catalog_record_normalizer.py",
        "04_catalog_embedding_text_builder.py",
        "05_catalog_embedding_batcher.py",
        "06_mongodb_snapshot_writer.py",
        "07_catalog_snapshot_validator.py",
        "08_catalog_snapshot_activator.py",
        "09_catalog_pipeline_worker_client.py",
        "33_catalog_activation_approval_client.py",
    ]


@pytest.mark.parametrize("path", COMPONENT_FILES, ids=lambda path: path.name)
def test_ast_standalone_contract(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    component_subclasses = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and any(isinstance(base, ast.Name) and base.id == "Component" for base in node.bases)
    ]
    assert len(component_subclasses) == 1

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative imports are forbidden"
            module_name = node.module or ""
            if module_name.startswith("lfx"):
                assert module_name in {"lfx.custom", "lfx.io", "lfx.schema"}
            assert not module_name.startswith(("business_work_design_agent", "components", "helpers", "common"))
        if isinstance(node, ast.Import):
            imported = {alias.name for alias in node.names}
            assert "importlib" not in imported
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"eval", "exec", "compile", "__import__"}
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "sys":
            assert node.attr != "path"

    component_class = component_subclasses[0]
    output_method_names: set[str] = set()
    for node in component_class.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            output_method_names.add(node.name)
            assert node.returns is not None, f"{path.name}:{node.name} must declare a return type"
    assert output_method_names
    assert "mongodb://" not in source.lower()
    assert "mongodb+srv://" not in source.lower()


def test_modules_load_and_expose_typed_outputs(modules: dict[str, ModuleType]) -> None:
    from lfx.custom import Component

    for module in modules.values():
        component_types = [
            value
            for value in module.__dict__.values()
            if inspect.isclass(value) and value.__module__ == module.__name__ and issubclass(value, Component)
        ]
        assert len(component_types) == 1
        component_type = component_types[0]
        assert component_type.inputs
        assert component_type.outputs
        for output in component_type.outputs:
            method = getattr(component_type, output.method)
            assert inspect.signature(method).return_annotation is not inspect.Signature.empty
            assert output.types == ["Data"]


def test_file_intake_identity_and_hash_are_deterministic(modules: dict[str, ModuleType]) -> None:
    module = modules["00_catalog_file_intake.py"]
    first = module._stable_ids("tenant-a", "a" * 64, "request-1")
    second = module._stable_ids("tenant-a", "a" * 64, "request-1")
    assert first == second
    assert first != module._stable_ids("tenant-b", "a" * 64, "request-1")
    assert module._normalize_tenant(" Tenant-A ") == "tenant-a"
    with pytest.raises(ValueError):
        module._normalize_tenant("../tenant")

    source = COMPONENT_ROOT / "00_catalog_file_intake.py"
    digest, size = module._hash_file(source, 1024 * 1024)
    assert digest == hashlib.sha256(source.read_bytes()).hexdigest()
    assert size == len(source.read_bytes())
    assert set(module._job_ref("tenant-a", "job-1", "snap-1", "INTAKE_STORED", 0, "trace-1")) == set(module._JOB_REF_KEYS)
    source_document, job_document = module._storage_documents(
        tenant_id="tenant-a",
        job_id="job-1",
        snapshot_id="snap-1",
        trace_id="trace-1",
        blob_id="blob-1",
        source_sha256="a" * 64,
        source_size=10,
        source_format="jsonl",
        idempotency_hash="b" * 64,
        uploader_id="user-1",
        now=module._utc_now(),
    )
    assert source_document["blob_id"] == job_document["source_blob_id"] == "blob-1"
    assert job_document["stage"] == "INTAKE_STORED"


def test_file_intake_rejects_paths_and_symlinks_outside_upload_root(
    modules: dict[str, ModuleType], tmp_path: Path
) -> None:
    module = modules["00_catalog_file_intake.py"]
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    inside = upload_root / "catalog.json"
    inside.write_text("[]", encoding="utf-8")
    outside = tmp_path / "outside.json"
    outside.write_text("[]", encoding="utf-8")
    assert module._resolve_allowed_upload(str(inside), str(upload_root)) == inside.resolve()
    with pytest.raises(ValueError, match="outside"):
        module._resolve_allowed_upload(str(outside), str(upload_root))
    link = upload_root / "linked.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("file symlinks are unavailable on this Windows host")
    with pytest.raises(ValueError, match="outside"):
        module._resolve_allowed_upload(str(link), str(upload_root))


def test_file_intake_duplicate_recovery_never_deletes_the_canonical_blob(modules: dict[str, ModuleType]) -> None:
    module = modules["00_catalog_file_intake.py"]

    class Bucket:
        def __init__(self) -> None:
            self.present = {"blob-a", "blob-b"}
            self.deleted: list[str] = []

        def exists(self, blob_id: str) -> bool:
            return blob_id in self.present

        def delete(self, blob_id: str) -> None:
            self.deleted.append(blob_id)
            self.present.remove(blob_id)

    class Sources:
        def find_one(self, query: dict[str, Any]) -> dict[str, Any]:
            return {"_id": "job-1", "tenant_id": "tenant-a", "source_sha256": "a" * 64, "blob_id": "blob-a"}

    class Jobs:
        def __init__(self, existing: dict[str, Any] | None) -> None:
            self.existing = existing
            self.updates: list[tuple[dict[str, Any], dict[str, Any]]] = []

        def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
            return self.existing

        def update_one(self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False) -> None:
            assert upsert is True
            self.updates.append((query, update))

    common = {
        "sources": Sources(),
        "tenant_id": "tenant-a",
        "job_id": "job-1",
        "snapshot_id": "snap-1",
        "trace_id": "trace-1",
        "source_sha256": "a" * 64,
        "source_size": 10,
        "source_format": "jsonl",
        "idempotency_hash": "b" * 64,
        "uploader_id": "owner-a",
        "now": module._utc_now(),
    }
    winning_bucket = Bucket()
    module._recover_duplicate_intake(
        bucket=winning_bucket,
        jobs=Jobs({"source_blob_id": "blob-a"}),
        uploaded_blob_id="blob-a",
        source_inserted_by_this_run=True,
        **common,
    )
    assert winning_bucket.deleted == []
    assert winning_bucket.exists("blob-a")

    losing_bucket = Bucket()
    recovered_jobs = Jobs(None)
    module._recover_duplicate_intake(
        bucket=losing_bucket,
        jobs=recovered_jobs,
        uploaded_blob_id="blob-b",
        source_inserted_by_this_run=False,
        **common,
    )
    assert losing_bucket.deleted == ["blob-b"]
    assert recovered_jobs.updates[0][1]["$setOnInsert"]["source_blob_id"] == "blob-a"


def test_secret_scanner_reports_only_codes_and_counts(modules: dict[str, ModuleType]) -> None:
    module = modules["01_catalog_secret_scanner.py"]
    secret = "password=verySecret123 token-free text AKIAABCDEFGHIJKLMNOP"
    counts = module._scan_text(secret)
    assert counts == {"ASSIGNED_CREDENTIAL": 1, "AWS_ACCESS_KEY": 1}
    assert "verySecret123" not in repr(counts)
    assert module._scan_text("README explains authentication without assigning a credential") == {}


def test_stream_parser_supports_array_wrapper_and_jsonl(modules: dict[str, ModuleType]) -> None:
    module = modules["02_catalog_stream_parser.py"]
    array_records = list(
        module._iter_json_sequence(
            io.StringIO('[{"id":"a"},{"id":"b"}]'),
            wrapper=False,
            read_chars=5,
            max_record_chars=1000,
        )
    )
    wrapper_records = list(
        module._iter_json_sequence(
            io.StringIO('{"items":[{"id":"a"}]}'),
            wrapper=True,
            read_chars=4,
            max_record_chars=1000,
        )
    )
    jsonl_records = list(module._iter_jsonl(io.StringIO('{"id":"a"}\nnot-json\n'), 1000))
    assert array_records == [(0, {"id": "a"}), (1, {"id": "b"})]
    assert wrapper_records == [(0, {"id": "a"})]
    assert jsonl_records[0] == (0, {"id": "a"})
    assert jsonl_records[1][1]["__parse_error__"] == "MALFORMED_JSONL_RECORD"
    assert module._detect_format("  [", "json") == "array"
    assert module._detect_format('{"items": [', "json") == "items_wrapper"


def test_record_normalizer_redacts_and_preserves_source(modules: dict[str, ModuleType]) -> None:
    module = modules["03_catalog_record_normalizer.py"]
    normalized, counts, warnings = module._normalize_record(
        {
            "id": "asset-1",
            "title": "Mail helper",
            "type": "py",
            "version": "v1",
            "description": "Owner user@example.com",
            "password": "do-not-store",
            "stars_count": "4",
            "downloads_count": -2,
            "created_at": "2026-08-27T00:00:00Z",
        },
        tenant_id="tenant-a",
        source={"file_id": "file-1", "record_index": 3, "file_sha256": "a" * 64},
    )
    assert normalized["asset_type"] == "component"
    assert normalized["title_normalized"] == "mail helper"
    assert normalized["aliases_normalized"] == ["mail", "helper"]
    assert normalized["raw_record_redacted"]["password"] == "[REDACTED:SENSITIVE_FIELD]"
    assert "[REDACTED:EMAIL]" in normalized["description"]
    assert normalized["source"]["record_index"] == 3
    assert normalized["downloads_count"] == 0
    assert counts == {"EMAIL": 1, "SENSITIVE_FIELD": 1}
    assert warnings == []
    grouped, _, _ = module._normalize_record(
        {"id": "asset-group", "title": "Group", "type": "py", "acl": {"visibility": "group", "groups": ["Engineering"]}},
        tenant_id="tenant-a",
        source={},
    )
    assert grouped["acl"] == {"visibility": "group", "groups": ["engineering"], "subjects": []}
    private, _, _ = module._normalize_record(
        {"id": "asset-private", "title": "Private", "type": "py", "acl": {"visibility": "private", "subjects": ["Employee-CaseSensitive"]}},
        tenant_id="tenant-a",
        source={},
    )
    assert private["acl"]["subjects"] == ["Employee-CaseSensitive"]
    with pytest.raises(ValueError, match="Private"):
        module._normalize_record(
            {"id": "asset-private-invalid", "title": "Private", "type": "py", "acl": {"visibility": "private"}},
            tenant_id="tenant-a",
            source={},
        )
    with pytest.raises(ValueError):
        module._normalize_record(
            {"id": "asset", "title": "title", "type": "py", "tenant_id": "tenant-b"},
            tenant_id="tenant-a",
            source={},
        )


def test_embedding_text_and_content_hash_are_deterministic(modules: dict[str, ModuleType]) -> None:
    normalizer = modules["03_catalog_record_normalizer.py"]
    builder = modules["04_catalog_embedding_text_builder.py"]
    validator = modules["07_catalog_snapshot_validator.py"]
    normalized, _, _ = normalizer._normalize_record(
        {"id": "asset-1", "title": "Helper", "type": "json", "description": "Does work", "readme": "Safe readme"},
        tenant_id="tenant-a",
        source={"file_id": "file-1", "record_index": 0, "file_sha256": "b" * 64},
    )
    first_asset, first_chunks = builder._build_asset_documents(
        normalized,
        tenant_id="tenant-a",
        snapshot_id="snap-1",
        max_text_chars=60000,
        chunk_chars=500,
        overlap_chars=20,
        max_chunks=16,
    )
    second_asset, second_chunks = builder._build_asset_documents(
        normalized,
        tenant_id="tenant-a",
        snapshot_id="snap-1",
        max_text_chars=60000,
        chunk_chars=500,
        overlap_chars=20,
        max_chunks=16,
    )
    assert first_asset["content_sha256"] == second_asset["content_sha256"]
    assert validator._asset_content_hash(first_asset) == first_asset["content_sha256"]
    assert first_chunks == second_chunks
    assert first_asset["raw_text"].splitlines()[0] == "title: Helper"
    assert first_chunks[0]["chunk_id"] == "whole"
    assert first_chunks[0]["title_normalized"] == "helper"
    assert first_chunks[0]["aliases_normalized"] == []
    assert builder._split_text("short text", 500, 20, 1) == ["short text"]
    with pytest.raises(ValueError, match="more chunks"):
        builder._split_text("word " * 500, 500, 20, 1)


def test_embedding_response_dimension_and_order_validation(modules: dict[str, ModuleType]) -> None:
    module = modules["05_catalog_embedding_batcher.py"]
    response = {"data": [{"index": 1, "embedding": [3, 4]}, {"index": 0, "embedding": [1, 2]}]}
    assert module._parse_embedding_response(response, 2, 2) == [[1.0, 2.0], [3.0, 4.0]]
    with pytest.raises(ValueError, match="dimension"):
        module._parse_embedding_response({"data": [{"index": 0, "embedding": [1]}]}, 1, 2)
    with pytest.raises(ValueError, match="non-numeric"):
        module._parse_embedding_response({"data": [{"index": 0, "embedding": [True, 0.2]}]}, 1, 2)
    with pytest.raises(ValueError, match="invalid vector index"):
        module._parse_embedding_response({"data": [{"index": True, "embedding": [0.1, 0.2]}]}, 1, 2)
    assert module._valid_reused_vector(
        {"embedding": {"model": "m", "version": "v", "dimension": 2, "vector": [True, 0.2]}},
        "m",
        "v",
        2,
    ) is None
    with pytest.raises(ValueError, match="HTTPS"):
        module._validate_endpoint("http://embedding.internal/v1/embeddings", False, {"embedding.internal"})
    assert module._validate_endpoint(
        "http://embedding.internal/v1/embeddings", True, {"embedding.internal"}
    ).startswith("http://")
    with pytest.raises(ValueError, match="allowlist"):
        module._validate_endpoint("https://unapproved.example/v1/embeddings", False, {"embedding.internal"})


def test_snapshot_writer_requires_complete_embedding_contract(modules: dict[str, ModuleType]) -> None:
    normalizer = modules["03_catalog_record_normalizer.py"]
    builder = modules["04_catalog_embedding_text_builder.py"]
    writer = modules["06_mongodb_snapshot_writer.py"]
    normalized, _, _ = normalizer._normalize_record(
        {"id": "asset-1", "title": "Helper", "type": "py"},
        tenant_id="tenant-a",
        source={"file_id": "file-1", "record_index": 0, "file_sha256": "c" * 64},
    )
    asset, chunks = builder._build_asset_documents(
        normalized,
        tenant_id="tenant-a",
        snapshot_id="snap-1",
        max_text_chars=60000,
        chunk_chars=500,
        overlap_chars=20,
        max_chunks=16,
    )
    contract = {"model": "model-a", "version": "2026-08", "dimension": 2}
    asset["embedding_manifest"] = {**contract, "chunk_count": len(chunks), "complete": True}
    for chunk in chunks:
        chunk["embedding"] = {
            "vector": [0.1, 0.2],
            **contract,
            "input_sha256": chunk["embedding_input_sha256"],
        }
    validated_asset, validated_chunks = writer._validate_staged_asset(
        {"embedding_status": "EMBEDDED", "asset_document": asset, "asset_chunks": chunks},
        contract,
    )
    assert validated_asset["asset_id"] == "asset-1"
    assert len(validated_chunks) == 1
    chunks[0]["embedding"]["vector"] = [float("nan"), 0.2]
    with pytest.raises(ValueError, match="chunk"):
        writer._validate_staged_asset(
            {"embedding_status": "EMBEDDED", "asset_document": asset, "asset_chunks": chunks},
            contract,
        )
    chunks[0]["embedding"]["vector"] = [0.1, 0.2]
    asset["embedding_manifest"]["dimension"] = 3
    with pytest.raises(ValueError, match="manifest"):
        writer._validate_staged_asset(
            {"embedding_status": "EMBEDDED", "asset_document": asset, "asset_chunks": chunks},
            contract,
        )


def test_snapshot_validation_helpers(modules: dict[str, ModuleType]) -> None:
    validator = modules["07_catalog_snapshot_validator.py"]
    checks = {"HASH": True, "VECTOR": True}
    valid, failures = validator._evaluate_validation(checks, {"assets": 1, "chunks": 1})
    assert valid is True and failures == []
    valid, failures = validator._evaluate_validation({"HASH": False}, {"assets": 0, "chunks": 1})
    assert valid is False
    assert failures == ["ASSET_COUNT_ZERO", "HASH"]
    assert validator._index_matches(
        {"uq": {"key": [("tenant_id", 1), ("snapshot_id", 1)], "unique": True}},
        ["tenant_id", "snapshot_id"],
        True,
    )


def test_snapshot_activator_requires_explicit_validated_approval(modules: dict[str, ModuleType]) -> None:
    module = modules["08_catalog_snapshot_activator.py"]
    report = {
        "ok": True,
        "status": "VALIDATED",
        "tenant_id": "tenant-a",
        "snapshot_id": "snap-1",
        "job_id": "job-1",
        "validation_hash": "a" * 64,
        "trace_id": "trace-1",
    }
    assert module._validate_activation_request(report, True, "admin-1")["snapshot_id"] == "snap-1"
    with pytest.raises(ValueError, match="approved=true"):
        module._validate_activation_request(report, False, "admin-1")
    bad_report = dict(report, status="VALIDATION_FAILED", ok=False)
    with pytest.raises(ValueError, match="VALIDATED"):
        module._validate_activation_request(bad_report, True, "admin-1")
    input_names = {item.name for item in module.CatalogSnapshotActivatorComponent.inputs}
    assert {"approval_trigger", "approval_id", "approval_nonce", "approved", "approver_id"} <= input_names


def test_restart_security_and_reconciliation_markers() -> None:
    normalizer_source = (COMPONENT_ROOT / "03_catalog_record_normalizer.py").read_text(encoding="utf-8")
    embedding_source = (COMPONENT_ROOT / "05_catalog_embedding_batcher.py").read_text(encoding="utf-8")
    writer_source = (COMPONENT_ROOT / "06_mongodb_snapshot_writer.py").read_text(encoding="utf-8")
    activator_source = (COMPONENT_ROOT / "08_catalog_snapshot_activator.py").read_text(encoding="utf-8")

    assert '"$unset": {"raw_record"' not in normalizer_source
    assert 'document.get("normalize_status") == "NORMALIZED"' in normalizer_source
    assert "approved_embedding_hosts" in embedding_source
    assert "_NoRedirectHandler" in embedding_source
    assert "EMBEDDING_CONTRACT_CHANGED" in embedding_source
    assert "uq_catalog_snapshot" in writer_source
    assert "SNAPSHOT_RECONCILIATION_FAILED" in writer_source
    assert "catalog_activation_approvals" in activator_source
    assert "_reconcile_active_projection" in activator_source
