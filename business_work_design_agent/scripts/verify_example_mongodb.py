from __future__ import annotations

"""Read-only verification for the F00 catalog and demo Skill MongoDB state."""

import argparse
import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any

from pymongo import MongoClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG_SAMPLE = PROJECT_ROOT / "samples" / "f00_catalog_assets_example.json"
DEFAULT_SKILL_SAMPLE = PROJECT_ROOT / "samples" / "skill_registry_example.json"
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")
EMBEDDING_CONTRACT_VERSION = "embedding-runtime-contract/v2"


def _items(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("items")
    if not isinstance(payload, list) or not payload or any(not isinstance(item, dict) for item in payload):
        raise ValueError(f"{path}: expected a non-empty array or {{items:[...]}}.")
    return payload


def _identifier(value: str, label: str) -> str:
    if not IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"{label} is not a safe MongoDB identifier.")
    return value


def _identity(record: dict[str, Any]) -> tuple[str, str]:
    asset_id = str(record.get("id") or record.get("asset_id") or "").strip()
    version = str(record.get("version") or "unversioned").strip() or "unversioned"
    if not asset_id:
        raise ValueError("Catalog sample item is missing id/asset_id.")
    return asset_id, version


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_parent_content(
    parent: dict[str, Any],
    expected_record: dict[str, Any],
    expected_source_sha256: str,
    expected_source_size: int,
) -> None:
    """Verify this safe example record, its source provenance, and stored hashes exactly."""
    raw_record = parent.get("raw_record_redacted")
    if raw_record != expected_record:
        raise RuntimeError("Stored parent redacted record does not match the uploaded example record.")
    raw_text = _canonical_json(raw_record)
    record_sha256 = _sha256_text(raw_text)
    if parent.get("raw_text_redacted") != raw_text or parent.get("raw_record_redacted_sha256") != record_sha256:
        raise RuntimeError("Stored parent redacted source text/hash is inconsistent.")
    source = parent.get("source") if isinstance(parent.get("source"), dict) else {}
    if source.get("file_sha256") != expected_source_sha256 or source.get("file_size_bytes") != expected_source_size:
        raise RuntimeError("Stored parent source provenance does not match the example file.")
    asset_id, version = _identity(expected_record)
    content_basis = {
        "identity": [asset_id, version],
        "record_sha256": record_sha256,
        "technical_contract": parent.get("technical_contract"),
        "acl": parent.get("acl"),
    }
    if parent.get("content_sha256") != _sha256_text(_canonical_json(content_basis)):
        raise RuntimeError("Stored parent content hash is inconsistent with its record and contracts.")
    lexical_text = parent.get("lexical_text_redacted")
    if not isinstance(lexical_text, str) or f"original_record: {raw_text}" not in lexical_text:
        raise RuntimeError("Stored parent searchable text does not preserve the example source record.")


def _validate_skill_content(stored: dict[str, Any], expected: dict[str, Any]) -> None:
    for key, expected_value in expected.items():
        if stored.get(key) != expected_value:
            raise RuntimeError(f"Stored Skill field {key!r} does not match the approved example.")
    actual_hash = "sha256:" + hashlib.sha256(str(stored.get("prompt_text") or "").encode("utf-8")).hexdigest()
    if actual_hash != expected.get("prompt_sha256"):
        raise RuntimeError("Stored Skill prompt hash does not match the approved example.")


def _valid_vector(value: Any, dimension: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) == dimension
        and all(not isinstance(number, bool) and isinstance(number, (int, float)) and math.isfinite(float(number)) for number in value)
    )


def _validate_embedding_runtime_contract(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError("Active catalog pointer has no embedding runtime contract.")
    fields = {"schema_version", "runtime_class", "model_id", "dimension", "fingerprint"}
    if set(value) != fields or value.get("schema_version") != EMBEDDING_CONTRACT_VERSION:
        raise RuntimeError("Active catalog pointer embedding runtime contract is invalid.")
    runtime_class = value.get("runtime_class")
    model_id = value.get("model_id")
    dimension = value.get("dimension")
    if (
        not isinstance(runtime_class, str)
        or not runtime_class.strip()
        or not isinstance(model_id, str)
        or not model_id.strip()
        or isinstance(dimension, bool)
        or not isinstance(dimension, int)
        or not 1 <= dimension <= 65536
    ):
        raise RuntimeError("Active catalog pointer embedding runtime contract fields are invalid.")
    core = {
        "schema_version": EMBEDDING_CONTRACT_VERSION,
        "runtime_class": runtime_class,
        "model_id": model_id,
        "dimension": dimension,
    }
    expected_fingerprint = "sha256:" + _sha256_text(_canonical_json(core))
    if value.get("fingerprint") != expected_fingerprint:
        raise RuntimeError("Active catalog pointer embedding runtime contract fingerprint is invalid.")
    return {**core, "fingerprint": expected_fingerprint}


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify F00 example catalog and Skill registry in MongoDB without writing.")
    parser.add_argument("--mongodb-uri", default=os.environ.get("MONGODB_URI", ""))
    parser.add_argument("--database", default=os.environ.get("MONGODB_DATABASE", "business_work_design"))
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--catalog-id", default="internal-assets")
    parser.add_argument("--catalog-sample", type=Path, default=DEFAULT_CATALOG_SAMPLE)
    parser.add_argument("--skill-sample", type=Path, default=DEFAULT_SKILL_SAMPLE)
    args = parser.parse_args()

    if not str(args.mongodb_uri).strip():
        raise ValueError("MONGODB_URI or --mongodb-uri is required.")
    database_name = _identifier(str(args.database), "database")
    tenant_id = str(args.tenant_id).strip()
    catalog_id = str(args.catalog_id).strip()
    if not tenant_id or not catalog_id:
        raise ValueError("tenant-id and catalog-id are required.")
    catalog_sample_path = args.catalog_sample.resolve()
    catalog_items = _items(catalog_sample_path)
    skill_items = _items(args.skill_sample.resolve())
    expected_source_bytes = catalog_sample_path.read_bytes()
    expected_source_sha256 = hashlib.sha256(expected_source_bytes).hexdigest()
    expected_source_size = len(expected_source_bytes)
    expected_records = {_identity(item): item for item in catalog_items}
    expected_assets = set(expected_records)
    if len(expected_assets) != len(catalog_items):
        raise ValueError("Catalog sample contains duplicate asset identity.")
    if any(str(item.get("tenant_id") or tenant_id).strip().lower() != tenant_id for item in catalog_items):
        raise ValueError("Catalog sample tenant scope does not match --tenant-id.")
    if any(str(item.get("tenant_id") or "") != tenant_id for item in skill_items):
        raise ValueError("Skill sample tenant scope does not match --tenant-id.")

    client = MongoClient(
        str(args.mongodb_uri),
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        retryReads=True,
    )
    try:
        client.admin.command("ping")
        database = client[database_name]
        pointer = database["catalog_active_pointers"].find_one({"_id": tenant_id})
        if not isinstance(pointer, dict):
            raise RuntimeError("Active catalog pointer was not found for the example tenant.")
        if pointer.get("tenant_id") != tenant_id or pointer.get("catalog_id") != catalog_id:
            raise RuntimeError("Active catalog pointer tenant/catalog does not match the example.")
        if pointer.get("source_sha256") != expected_source_sha256:
            raise RuntimeError("Active catalog pointer source hash does not match the example file.")
        snapshot_id = pointer.get("active_snapshot_id") or pointer.get("snapshot_id")
        if not isinstance(snapshot_id, str) or not snapshot_id:
            raise RuntimeError("Active catalog pointer has no valid snapshot identity.")
        contract = _validate_embedding_runtime_contract(pointer.get("embedding_contract"))
        dimension = contract["dimension"]

        base_filter = {"tenant_id": tenant_id, "snapshot_id": snapshot_id}
        stored_asset_count = database["catalog_assets"].count_documents(base_filter)
        pointer_counts = pointer.get("counts") if isinstance(pointer.get("counts"), dict) else {}
        if stored_asset_count != len(expected_assets) or pointer_counts.get("records") != stored_asset_count:
            raise RuntimeError("Stored parent count does not match the example file and active pointer.")
        asset_filter = {
            "$and": [
                base_filter,
                {"$or": [{"asset_id": asset_id, "version": version} for asset_id, version in sorted(expected_assets)]},
            ]
        }
        parents = list(database["catalog_assets"].find(asset_filter))
        stored_assets = {(str(item.get("asset_id") or ""), str(item.get("version") or "")) for item in parents}
        if stored_assets != expected_assets:
            raise RuntimeError("Stored parent identities do not match the example file.")
        for parent in parents:
            if not all(parent.get(field) for field in ("raw_record_redacted", "raw_text_redacted", "lexical_text_redacted", "content_sha256")):
                raise RuntimeError("Stored parent is missing redacted source/search fields.")
            identity = (str(parent.get("asset_id") or ""), str(parent.get("version") or ""))
            _validate_parent_content(
                parent,
                expected_records[identity],
                expected_source_sha256,
                expected_source_size,
            )
            manifest = parent.get("embedding_manifest") if isinstance(parent.get("embedding_manifest"), dict) else {}
            if manifest.get("complete") is not True or manifest.get("embedding_contract") != contract:
                raise RuntimeError("Stored parent embedding manifest does not match the active pointer.")

        chunks = list(database["catalog_asset_chunks"].find(base_filter))
        if len(chunks) != pointer_counts.get("chunks") or len(chunks) < len(expected_assets):
            raise RuntimeError("Stored chunk count does not match the active pointer.")
        chunk_assets = {(str(item.get("asset_id") or ""), str(item.get("version") or "")) for item in chunks}
        if not expected_assets.issubset(chunk_assets):
            raise RuntimeError("At least one example parent has no searchable chunk.")
        for chunk in chunks:
            embedding = chunk.get("embedding") if isinstance(chunk.get("embedding"), dict) else {}
            if embedding.get("contract") != contract:
                raise RuntimeError("Chunk embedding runtime contract does not match the active pointer.")
            if not _valid_vector(embedding.get("vector"), dimension):
                raise RuntimeError("Chunk embedding vector is missing, non-finite, or has the wrong dimension.")

        for skill in skill_items:
            identity_filter = {
                "tenant_id": skill.get("tenant_id"),
                "skill_id": skill.get("skill_id"),
                "version": skill.get("version"),
                "status": "active",
            }
            stored = database["skill_registry"].find_one(identity_filter)
            if not isinstance(stored, dict):
                raise RuntimeError("Expected active Skill registry item was not found.")
            _validate_skill_content(stored, skill)

        print(json.dumps({
            "ok": True,
            "status": "EXAMPLE_MONGODB_VERIFIED",
            "tenant_id": tenant_id,
            "catalog_id": catalog_id,
            "snapshot_id": snapshot_id,
            "assets": stored_asset_count,
            "chunks": len(chunks),
            "vectors": len(chunks),
            "embedding_contract": contract,
            "active_skills": len(skill_items),
        }, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
