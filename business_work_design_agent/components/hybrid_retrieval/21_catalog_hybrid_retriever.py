from __future__ import annotations

import json
import hashlib
import hmac
import math
import re
import unicodedata
import uuid
from datetime import datetime
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, DropdownInput, IntInput, MessageTextInput, Output, SecretStrInput
from lfx.schema import Data
from pymongo import MongoClient
from pymongo.errors import ConfigurationError, ConnectionFailure, OperationFailure, PyMongoError, ServerSelectionTimeoutError


PROVIDER_MODES = {"native_rank_fusion", "native_score_fusion", "application_rrf"}
TECHNICAL_STATUSES = {"metadata_only", "ports_extracted", "flow_graph_extracted", "verified_runtime"}
MAX_QUERY_COUNT = 30
MAX_NATIVE_QUERY_COUNT = 12
MAX_SOURCE_CANDIDATES = 100
QUERY_KIND_WEIGHTS = {
    "purpose": 1.20,
    "capability": 1.25,
    "exact": 1.50,
    "risk": 1.15,
    "reporting": 1.00,
}
_RUNTIME_CONTRACT_SCHEMA = "embedding-runtime-contract/v2"
_RUNTIME_CONTRACT_FIELDS = ("schema_version", "runtime_class", "model_id", "dimension", "fingerprint")
_MAX_EMBEDDING_DIMENSION = 65536


def _safe_identifier(value: Any, default: str) -> str:
    text = str(value or default).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", text):
        raise ValueError("INVALID_PROVIDER_IDENTIFIER")
    return text


def _payload(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    data = getattr(value, "data", None)
    if isinstance(data, (dict, list)):
        return data
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return {}


def _secret(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        return str(getter())
    return str(value or "")


def _canonical_hash(value: Any) -> str:
    material = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _string_list(value: Any, maximum: int = 100) -> list[str]:
    if isinstance(value, str):
        source = re.split(r"[,;\n]", value)
    elif isinstance(value, (list, tuple, set)):
        source = list(value)
    else:
        source = []
    result: list[str] = []
    for item in source[:maximum]:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _normalize_exact_key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return re.sub(r"\s+", " ", text)[:500]


def _acl_filter(tenant_id: str, acl: dict[str, Any]) -> dict[str, Any]:
    groups = [item.lower() for item in _string_list(acl.get("groups"))]
    subject_id = str(acl.get("subject_id") or "")
    choices: list[dict[str, Any]] = [{"acl.visibility": "tenant"}]
    if groups:
        choices.append({"acl.visibility": "group", "acl.groups": {"$in": groups}})
    if subject_id:
        choices.append({"acl.visibility": "private", "acl.subjects": subject_id})
    return {"tenant_id": tenant_id, "$or": choices}


def _acl_allows(document: dict[str, Any], tenant_id: str, snapshot_id: str, acl: dict[str, Any]) -> bool:
    if str(document.get("tenant_id") or "") != tenant_id:
        return False
    if str(document.get("snapshot_id") or "") != snapshot_id:
        return False
    document_acl = document.get("acl") if isinstance(document.get("acl"), dict) else None
    if document_acl is None:
        return False
    visibility = str(document_acl.get("visibility") or "").lower()
    groups = {item.lower() for item in _string_list(acl.get("groups"))}
    allowed_groups = {item.lower() for item in _string_list(document_acl.get("groups"))}
    subjects = set(_string_list(document_acl.get("subjects")))
    subject_id = str(acl.get("subject_id") or "")
    if visibility == "tenant":
        return True
    if visibility == "group":
        return bool(groups & allowed_groups)
    return visibility == "private" and bool(subject_id) and subject_id in subjects


def _runtime_contract_text(value: Any, maximum: int = 500) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return text if 0 < len(text) <= maximum else ""


def _embedding_runtime_contract(value: Any) -> dict[str, Any]:
    """Validate the portable runtime identity emitted by the vectorization node."""
    if not isinstance(value, dict) or set(value) != set(_RUNTIME_CONTRACT_FIELDS):
        raise ValueError("EMBEDDING_RUNTIME_CONTRACT_INVALID")
    if value.get("schema_version") != _RUNTIME_CONTRACT_SCHEMA:
        raise ValueError("EMBEDDING_RUNTIME_CONTRACT_INVALID")
    runtime_class = _runtime_contract_text(value.get("runtime_class"), maximum=1000)
    model_id = _runtime_contract_text(value.get("model_id"))
    dimension = value.get("dimension")
    fingerprint = value.get("fingerprint")
    if (
        not runtime_class
        or not model_id
        or isinstance(dimension, bool)
        or not isinstance(dimension, int)
        or not 1 <= dimension <= _MAX_EMBEDDING_DIMENSION
        or not isinstance(fingerprint, str)
        or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", fingerprint)
    ):
        raise ValueError("EMBEDDING_RUNTIME_CONTRACT_INVALID")
    signature = {
        "schema_version": _RUNTIME_CONTRACT_SCHEMA,
        "runtime_class": runtime_class,
        "model_id": model_id,
        "dimension": dimension,
    }
    expected_fingerprint = _canonical_hash(signature)
    if not hmac.compare_digest(fingerprint, expected_fingerprint):
        raise ValueError("EMBEDDING_RUNTIME_CONTRACT_FINGERPRINT_INVALID")
    return {**signature, "fingerprint": fingerprint}


def _embedding_runtime_signature(contract: dict[str, Any]) -> tuple[str, str, str, int]:
    return (
        str(contract["schema_version"]),
        str(contract["runtime_class"]),
        str(contract["model_id"]),
        int(contract["dimension"]),
    )


def _query_vectors(value: Any) -> tuple[dict[str, list[float]], dict[str, Any]]:
    payload = _payload(value)
    contract = _embedding_runtime_contract(payload.get("embedding_contract"))
    declared_dimension = contract["dimension"]
    raw = payload.get("vectors") if isinstance(payload, dict) else None
    result: dict[str, list[float]] = {}
    if isinstance(raw, dict):
        pairs = raw.items()
    elif isinstance(raw, list):
        pairs = ((item.get("query_id"), item.get("vector")) for item in raw if isinstance(item, dict))
    else:
        pairs = []
    dimension: int | None = None
    for query_id, vector in pairs:
        if not query_id or not isinstance(vector, list) or not vector or len(vector) > _MAX_EMBEDDING_DIMENSION:
            raise ValueError("VECTOR_PAYLOAD_INVALID")
        if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in vector):
            raise ValueError("VECTOR_NUMERIC_INVALID")
        values = [float(item) for item in vector]
        if not all(math.isfinite(item) for item in values):
            raise ValueError("VECTOR_NUMERIC_INVALID")
        if dimension is None:
            dimension = len(values)
        if len(values) != dimension or len(values) != declared_dimension:
            raise ValueError("VECTOR_DIMENSION_MISMATCH")
        result[str(query_id)] = values
    return result, contract


def _mongo_base_filter(tenant_id: str, snapshot_id: str, acl: dict[str, Any], asset_types: list[str]) -> dict[str, Any]:
    base = _acl_filter(tenant_id, acl)
    base["snapshot_id"] = snapshot_id
    if asset_types:
        base["asset_type"] = {"$in": asset_types}
    return base


def _search_filter_clauses(tenant_id: str, snapshot_id: str, acl: dict[str, Any], asset_types: list[str]) -> list[dict[str, Any]]:
    clauses: list[dict[str, Any]] = [
        {"equals": {"path": "tenant_id", "value": tenant_id}},
        {"equals": {"path": "snapshot_id", "value": snapshot_id}},
    ]
    if asset_types:
        clauses.append({"in": {"path": "asset_type", "value": asset_types}})
    # Atlas Search must apply authorization before producing candidates.  This
    # compound clause mirrors the post-query fail-closed ACL verification.
    should: list[dict[str, Any]] = [
        {
            "compound": {
                "must": [{"equals": {"path": "acl.visibility", "value": "tenant"}}]
            }
        }
    ]
    groups = [item.lower() for item in _string_list(acl.get("groups"))]
    if groups:
        should.append(
            {
                "compound": {
                    "must": [
                        {"equals": {"path": "acl.visibility", "value": "group"}},
                        {"in": {"path": "acl.groups", "value": groups}},
                    ]
                }
            }
        )
    subject_id = str(acl.get("subject_id") or "")
    if subject_id:
        should.append(
            {
                "compound": {
                    "must": [
                        {"equals": {"path": "acl.visibility", "value": "private"}},
                        {"equals": {"path": "acl.subjects", "value": subject_id}},
                    ]
                }
            }
        )
    clauses.append({"compound": {"should": should, "minimumShouldMatch": 1}})
    return clauses


def _clean_document(document: dict[str, Any], source: str, rank: int, query_ids: list[str]) -> dict[str, Any]:
    technical = document.get("technical_contract") if isinstance(document.get("technical_contract"), dict) else {}
    technical_status = str(document.get("technical_contract_status") or technical.get("status") or "metadata_only")
    if technical_status not in TECHNICAL_STATUSES:
        technical_status = "metadata_only"
    ports = document.get("ports") if isinstance(document.get("ports"), dict) else {
        "inputs": technical.get("inputs") if isinstance(technical.get("inputs"), list) else [],
        "outputs": technical.get("outputs") if isinstance(technical.get("outputs"), list) else [],
    }
    popularity = document.get("popularity") if isinstance(document.get("popularity"), dict) else {}
    return {
        "asset_id": str(document.get("asset_id") or document.get("id") or "")[:200],
        "version": str(document.get("version") or "")[:100],
        "asset_type": str(document.get("asset_type") or document.get("type") or "")[:50],
        "title": str(document.get("title") or "")[:500],
        "description": str(document.get("description") or "")[:2000],
        "category": str(document.get("category") or "")[:200],
        "readme": str(document.get("readme") or document.get("lexical_text_redacted") or "")[:8000],
        "technical_contract_status": technical_status,
        "ports": ports,
        "relations": document.get("relations") if isinstance(document.get("relations"), list) else [],
        "tenant_id": str(document.get("tenant_id") or ""),
        "snapshot_id": str(document.get("snapshot_id") or ""),
        "acl": document.get("acl") if isinstance(document.get("acl"), dict) else {},
        "stars_count": int(document.get("stars_count") or popularity.get("stars") or 0),
        "downloads_count": int(document.get("downloads_count") or popularity.get("downloads") or 0),
        "updated_at": str(document.get("updated_at") or ""),
        "matched_chunk_id": str(document.get("chunk_id") or "")[:200],
        "source": source,
        "source_rank": rank,
        "query_ids": query_ids,
    }


def _enrich_source_results(
    source_results: dict[str, list[dict[str, Any]]],
    parent_documents: list[dict[str, Any]],
    source_limit: int,
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    parents = {
        (str(item.get("asset_id") or item.get("id") or ""), str(item.get("version") or "")): item
        for item in parent_documents
        if isinstance(item, dict)
    }
    enriched: dict[str, list[dict[str, Any]]] = {}
    missing = 0
    for source, items in source_results.items():
        output: list[dict[str, Any]] = []
        for item in items[:source_limit]:
            if not isinstance(item, dict):
                continue
            identity = (str(item.get("asset_id") or item.get("id") or ""), str(item.get("version") or ""))
            parent = parents.get(identity)
            if parent is None:
                missing += 1
                continue
            merged = dict(parent)
            for key, value in item.items():
                if key.startswith("_") or key in {"score", "chunk_id", "chunk_ordinal", "lexical_text_redacted"}:
                    merged[key] = value
            output.append(merged)
        enriched[source] = output
    return enriched, missing


def _updated_timestamp(document: dict[str, Any]) -> float:
    text = str(document.get("updated_at") or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (ValueError, OSError):
        return 0.0


def _popularity(document: dict[str, Any], key: str) -> int:
    nested = document.get("popularity") if isinstance(document.get("popularity"), dict) else {}
    try:
        return max(0, int(document.get(f"{key}_count") or nested.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def _find_exact_parents(
    collection: Any,
    base_filter: dict[str, Any],
    exact_keys: list[str],
    exact_terms: list[str],
    source_limit: int,
    query_timeout_ms: int,
) -> list[dict[str, Any]]:
    lanes = (
        {"title_normalized": {"$in": exact_keys}},
        {"aliases_normalized": {"$in": exact_keys}},
        {"asset_id": {"$in": exact_terms}},
    )
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for lane in lanes:
        if not next(iter(lane.values()))["$in"]:
            continue
        cursor = collection.find({"$and": [base_filter, lane]}, {"_id": 0}, max_time_ms=query_timeout_ms)
        cursor = cursor.sort([("asset_id", 1), ("version", 1)]).limit(source_limit)
        for document in cursor:
            identity = (str(document.get("asset_id") or document.get("id") or ""), str(document.get("version") or ""))
            if all(identity) and identity not in seen:
                seen.add(identity)
                result.append(document)
            if len(result) >= source_limit:
                return result
    return result


def _retrieve_from_mongodb(
    *,
    mongodb_uri: str,
    database_name: str,
    chunks_collection: str,
    pointer_collection: str,
    assets_collection: str = "catalog_assets",
    tenant_id: str,
    snapshot_id: str,
    acl: dict[str, Any],
    query_plan: dict[str, Any],
    vectors: dict[str, list[float]],
    query_embedding_contract: dict[str, Any],
    provider_mode: str,
    lexical_index_name: str,
    vector_index_name: str,
    source_limit: int,
    server_selection_timeout_ms: int,
    query_timeout_ms: int,
) -> dict[str, Any]:
    client = MongoClient(
        mongodb_uri,
        serverSelectionTimeoutMS=server_selection_timeout_ms,
        connectTimeoutMS=server_selection_timeout_ms,
        socketTimeoutMS=query_timeout_ms,
        appname="business-work-design-agent-retriever",
    )
    try:
        database = client[database_name]
        pointer = database[pointer_collection].find_one(
            {"tenant_id": tenant_id},
            {"_id": 0, "snapshot_id": 1, "active_snapshot_id": 1, "embedding_contract": 1},
            max_time_ms=query_timeout_ms,
        )
        active_snapshot_id = str((pointer or {}).get("snapshot_id") or (pointer or {}).get("active_snapshot_id") or "")
        if not active_snapshot_id:
            return {"active_snapshot_id": "", "source_results": {}}
        if active_snapshot_id != snapshot_id:
            return {"active_snapshot_id": active_snapshot_id, "source_results": {}}
        try:
            active_contract = _embedding_runtime_contract((pointer or {}).get("embedding_contract"))
        except ValueError:
            return {"active_snapshot_id": active_snapshot_id, "contract_error": "CATALOG_EMBEDDING_CONTRACT_MISSING", "source_results": {}}
        if (
            _embedding_runtime_signature(active_contract) != _embedding_runtime_signature(query_embedding_contract)
            or not hmac.compare_digest(
                str(active_contract["fingerprint"]), str(query_embedding_contract["fingerprint"])
            )
        ):
            return {"active_snapshot_id": active_snapshot_id, "contract_error": "QUERY_EMBEDDING_CONTRACT_MISMATCH", "source_results": {}}

        collection = database[chunks_collection]
        parents_collection = database[assets_collection]
        queries = [item for item in query_plan.get("queries", []) if isinstance(item, dict)]
        search_text = " ".join(str(item.get("text") or "") for item in queries if item.get("kind") != "exact")[:4000]
        exact_terms = [str(item.get("text") or "")[:500] for item in queries if item.get("kind") == "exact"]
        exact_keys = [key for key in (_normalize_exact_key(term) for term in exact_terms) if key]
        asset_types = sorted(
            {
                str(asset_type)
                for item in queries
                for asset_type in item.get("expected_asset_types", [])
                if asset_type in {"component", "flow"}
            }
        )
        query_ids = [str(item.get("query_id")) for item in queries if item.get("query_id")]
        base_filter = _mongo_base_filter(tenant_id, snapshot_id, acl, asset_types)
        search_filters = _search_filter_clauses(tenant_id, snapshot_id, acl, asset_types)
        exact_docs: list[dict[str, Any]] = []
        if exact_keys or exact_terms:
            exact_docs = _find_exact_parents(
                parents_collection,
                base_filter,
                exact_keys,
                exact_terms,
                source_limit,
                query_timeout_ms,
            )

        if provider_mode == "application_rrf":
            lexical_docs = list(
                collection.aggregate(
                    [
                        {
                            "$search": {
                                "index": lexical_index_name,
                                "compound": {
                                    "must": [{"text": {"query": search_text, "path": ["title", "description", "lexical_text_redacted", "category"]}}],
                                    "filter": search_filters,
                                },
                            }
                        },
                        {"$limit": source_limit},
                        {"$project": {"_id": 0, "score": {"$meta": "searchScore"}, "asset_id": 1, "version": 1, "asset_type": 1, "title": 1, "description": 1, "category": 1, "readme": 1, "lexical_text_redacted": 1, "technical_contract_status": 1, "ports": 1, "relations": 1, "tenant_id": 1, "snapshot_id": 1, "acl": 1, "stars_count": 1, "downloads_count": 1, "updated_at": 1, "chunk_id": 1}},
                    ],
                    maxTimeMS=query_timeout_ms,
                )
            )
            source_results = {"exact": exact_docs, "lexical": lexical_docs}
            for query_id in query_ids:
                vector_docs = list(
                    collection.aggregate(
                        [
                            {
                                "$vectorSearch": {
                                    "index": vector_index_name,
                                    "path": "embedding.vector",
                                    "queryVector": vectors[query_id],
                                    "numCandidates": min(1000, source_limit * 10),
                                    "limit": source_limit,
                                    "filter": base_filter,
                                }
                            },
                            {"$project": {"_id": 0, "score": {"$meta": "vectorSearchScore"}, "asset_id": 1, "version": 1, "asset_type": 1, "title": 1, "description": 1, "category": 1, "readme": 1, "lexical_text_redacted": 1, "technical_contract_status": 1, "ports": 1, "relations": 1, "tenant_id": 1, "snapshot_id": 1, "acl": 1, "stars_count": 1, "downloads_count": 1, "updated_at": 1, "chunk_id": 1}},
                        ],
                        maxTimeMS=query_timeout_ms,
                    )
                )
                source_results[f"vector:{query_id}"] = vector_docs
        else:
            fusion_operator = "$rankFusion" if provider_mode == "native_rank_fusion" else "$scoreFusion"
            fusion_pipelines: dict[str, list[dict[str, Any]]] = {
                "lexical": [
                    {
                        "$search": {
                            "index": lexical_index_name,
                            "compound": {
                                "must": [{"text": {"query": search_text, "path": ["title", "description", "lexical_text_redacted", "category"]}}],
                                "filter": search_filters,
                            },
                        }
                    },
                    {"$limit": source_limit},
                ]
            }
            native_pipeline_query_ids: dict[str, str] = {}
            native_weights: dict[str, float] = {"lexical": 1.0}
            query_kind_by_id = {
                str(item.get("query_id")): str(item.get("kind") or "")
                for item in queries
                if item.get("query_id")
            }
            for index, query_id in enumerate(query_ids):
                pipeline_name = f"vector_{index:02d}"
                native_pipeline_query_ids[pipeline_name] = query_id
                native_weights[pipeline_name] = QUERY_KIND_WEIGHTS.get(query_kind_by_id.get(query_id, ""), 1.0)
                fusion_pipelines[pipeline_name] = [
                    {
                        "$vectorSearch": {
                            "index": vector_index_name,
                            "path": "embedding.vector",
                            "queryVector": vectors[query_id],
                            "numCandidates": min(1000, source_limit * 10),
                            "limit": source_limit,
                            "filter": base_filter,
                        }
                    }
                ]
            fusion_spec: dict[str, Any] = {
                "input": {"pipelines": fusion_pipelines},
                "combination": {"weights": native_weights},
                "scoreDetails": True,
            }
            if provider_mode == "native_score_fusion":
                fusion_spec["input"]["normalization"] = "none"
                fusion_spec["combination"]["method"] = "avg"
            score_details_meta = "searchScoreDetails" if provider_mode == "native_rank_fusion" else "scoreDetails"
            fused_docs = list(
                collection.aggregate(
                    [
                        {fusion_operator: fusion_spec},
                        {"$addFields": {"_fusion_score_details": {"$meta": score_details_meta}}},
                        {"$limit": source_limit},
                    ],
                    maxTimeMS=query_timeout_ms,
                )
            )
            source_results = {"exact": exact_docs, "native_fused": fused_docs}

        # Search chunks only establish candidate identity and ranking evidence.
        # Re-fetch authoritative parent metadata under the same tenant/snapshot/
        # ACL filter so ports, relations, README, and popularity cannot be lost
        # or forged by a stale/orphan chunk.
        identities = sorted(
            {
                (str(item.get("asset_id") or item.get("id") or ""), str(item.get("version") or ""))
                for items in source_results.values()
                for item in items[:source_limit]
                if isinstance(item, dict)
                and str(item.get("asset_id") or item.get("id") or "")
                and str(item.get("version") or "")
            }
        )
        parent_documents: list[dict[str, Any]] = []
        if identities:
            for offset in range(0, len(identities), 250):
                batch = identities[offset : offset + 250]
                identity_filter = {"$or": [{"asset_id": asset_id, "version": version} for asset_id, version in batch]}
                parent_documents.extend(
                    list(
                        parents_collection.find(
                            {"$and": [base_filter, identity_filter]},
                            {"_id": 0},
                            max_time_ms=query_timeout_ms,
                        )
                    )
                )
        source_results, missing_parent_count = _enrich_source_results(source_results, parent_documents, source_limit)

        # Relation payloads are never trusted as documents. Only parent relation
        # IDs are re-queried from the authoritative asset collection.
        relation_refs: list[tuple[str, str]] = []
        for source_items in source_results.values():
            for document in source_items[:source_limit]:
                for relation in document.get("relations", []) if isinstance(document.get("relations"), list) else []:
                    if not isinstance(relation, dict):
                        continue
                    ref = (str(relation.get("asset_id") or ""), str(relation.get("version") or ""))
                    if all(ref) and ref not in relation_refs:
                        relation_refs.append(ref)
                    if len(relation_refs) >= source_limit:
                        break
        if relation_refs:
            relation_identity = {
                "$or": [{"asset_id": asset_id, "version": version} for asset_id, version in relation_refs]
            }
            source_results["relation"] = list(
                parents_collection.find(
                    {"$and": [base_filter, relation_identity]},
                    {"_id": 0},
                    limit=source_limit,
                    max_time_ms=query_timeout_ms,
                )
            )
        return {
            "active_snapshot_id": active_snapshot_id,
            "source_results": source_results,
            "native_pipeline_query_ids": native_pipeline_query_ids if provider_mode != "application_rrf" else {},
            "missing_parent_count": missing_parent_count,
        }
    finally:
        client.close()


def _rrf(
    source_results: dict[str, list[dict[str, Any]]],
    k: int = 60,
    source_weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    scores: dict[tuple[str, str], float] = {}
    documents: dict[tuple[str, str], dict[str, Any]] = {}
    ranks: dict[tuple[str, str], dict[str, int]] = {}
    for source, items in source_results.items():
        seen_in_source: set[tuple[str, str]] = set()
        for rank, item in enumerate(items[:MAX_SOURCE_CANDIDATES], start=1):
            if not isinstance(item, dict):
                continue
            key = (str(item.get("asset_id") or item.get("id") or ""), str(item.get("version") or ""))
            if not all(key) or key in seen_in_source:
                continue
            seen_in_source.add(key)
            default_weight = 2.0 if source == "exact" else 0.25 if source == "relation" else 1.0
            weight = float((source_weights or {}).get(source, default_weight))
            scores[key] = scores.get(key, 0.0) + weight / (k + rank)
            ranks.setdefault(key, {})[source] = rank
            if key not in documents:
                documents[key] = dict(item)
            elif source == "native_fused" and isinstance(item.get("_fusion_score_details"), dict):
                # exact results are inserted first, but native score details are
                # the authoritative evidence for per-query contribution trace.
                documents[key]["_fusion_score_details"] = dict(item["_fusion_score_details"])
    ordered = sorted(
        scores,
        key=lambda key: (
            -scores[key],
            -_popularity(documents[key], "stars"),
            -_popularity(documents[key], "downloads"),
            -_updated_timestamp(documents[key]),
            key[0],
            key[1],
        ),
    )
    result: list[dict[str, Any]] = []
    for fused_rank, key in enumerate(ordered, start=1):
        item = dict(documents[key])
        item["_fused_score"] = scores[key]
        item["_source_ranks"] = ranks[key]
        item["_fused_rank"] = fused_rank
        result.append(item)
    return result


def _native_vector_contributions(
    document: dict[str, Any], pipeline_query_ids: dict[str, str]
) -> tuple[list[str], dict[str, int | float | str]]:
    score_details = document.get("_fusion_score_details")
    details = score_details.get("details") if isinstance(score_details, dict) else None
    if not isinstance(details, list):
        return [], {}
    matched: list[str] = []
    ranks: dict[str, int | float | str] = {}
    for detail in details:
        if not isinstance(detail, dict):
            continue
        query_id = pipeline_query_ids.get(str(detail.get("inputPipelineName") or ""))
        if not query_id:
            continue
        if query_id not in matched:
            matched.append(query_id)
        rank_or_score = detail.get("rank")
        if rank_or_score in (None, "N/A"):
            rank_or_score = detail.get("inputPipelineRawScore", detail.get("value"))
        if rank_or_score is not None:
            ranks[query_id] = rank_or_score
    return sorted(matched), ranks


def retrieve_catalog_candidates(
    query_plan: Any,
    query_vectors: Any,
    *,
    tenant_id: str = "",
    catalog_snapshot_id: str = "",
    acl_context: Any = None,
    provider_mode: str,
    mongodb_uri: Any,
    database_name: str = "business_work_design",
    chunks_collection: str = "catalog_asset_chunks",
    assets_collection: str = "catalog_assets",
    pointer_collection: str = "catalog_active_pointers",
    lexical_index_name: str = "catalog_lexical",
    vector_index_name: str = "catalog_vector",
    top_n: int = 20,
    source_limit: int = 50,
    server_selection_timeout_ms: int = 5000,
    query_timeout_ms: int = 10000,
) -> dict[str, Any]:
    trace_id = str(uuid.uuid4())
    plan = _payload(query_plan)
    mode = str(provider_mode or "").strip()
    if mode not in PROVIDER_MODES:
        return _error(trace_id, "UNSUPPORTED_PROVIDER_MODE", "지원되지 않는 provider_mode입니다.")
    if not plan.get("ok"):
        return _error(trace_id, "INVALID_QUERY_PLAN", "성공한 query plan이 필요합니다.")
    tenant = str(plan.get("tenant_id") or "").strip()
    snapshot = str(plan.get("catalog_snapshot_id") or "").strip()
    supplied_tenant = str(tenant_id or "").strip()
    supplied_snapshot = str(catalog_snapshot_id or "").strip()
    if not tenant or not snapshot:
        return _error(trace_id, "CATALOG_SCOPE_MISSING", "query plan에 tenant_id와 catalog_snapshot_id가 필요합니다.")
    if (supplied_tenant and supplied_tenant != tenant) or (supplied_snapshot and supplied_snapshot != snapshot):
        return _error(trace_id, "QUERY_PLAN_SCOPE_MISMATCH", "query plan의 tenant 또는 snapshot 계약이 일치하지 않습니다.")
    plan_acl = plan.get("acl") if isinstance(plan.get("acl"), dict) else {}
    acl = _payload(acl_context) if acl_context is not None else plan_acl
    if not plan_acl.get("subject_id"):
        return _error(trace_id, "ACL_CONTEXT_MISSING", "query plan에 검증 가능한 ACL context가 필요합니다.")
    if str(plan_acl.get("subject_id") or "") != str(acl.get("subject_id") or "") or {
        item.lower() for item in _string_list(plan_acl.get("groups"))
    } != {item.lower() for item in _string_list(acl.get("groups"))}:
        return _error(trace_id, "ACL_CONTEXT_MISMATCH", "query plan을 만든 ACL context와 현재 ACL context가 다릅니다.")
    lock_fields = ("work_definition_id", "work_definition_revision", "approved_hash", "design_scope_sha256")
    if any(plan.get(field) in (None, "") for field in lock_fields) or not str(plan.get("approved_hash")).startswith("sha256:"):
        return _error(trace_id, "QUERY_PLAN_LOCK_MISSING", "query plan에 승인 업무와 design scope lock이 필요합니다.")
    query_plan_hash = str(plan.get("query_plan_sha256") or "")
    plan_core = {
        key: value
        for key, value in plan.items()
        if key not in {"ok", "status", "query_plan_sha256", "trace_id"}
    }
    if (
        not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", query_plan_hash)
        or not hmac.compare_digest(query_plan_hash.lower(), _canonical_hash(plan_core).lower())
    ):
        return _error(trace_id, "QUERY_PLAN_HASH_MISMATCH", "query plan hash가 canonical payload와 일치하지 않습니다.")
    if not isinstance(plan.get("queries"), list) or not plan["queries"]:
        return _error(trace_id, "INVALID_QUERY_PLAN", "검색 query가 없습니다.")
    provider_query_limit = MAX_NATIVE_QUERY_COUNT if mode.startswith("native_") else MAX_QUERY_COUNT
    if len(plan["queries"]) > provider_query_limit:
        return _error(
            trace_id,
            "SEARCH_QUERY_LIMIT_EXCEEDED",
            "선택한 검색 provider가 한 번에 처리할 수 있는 query 수를 초과했습니다.",
            details={"provider_mode": mode, "query_count": len(plan["queries"]), "maximum": provider_query_limit},
        )
    uri = _secret(mongodb_uri)
    if not uri:
        return _error(trace_id, "CATALOG_CONFIGURATION_MISSING", "MongoDB 연결 설정이 필요합니다.")
    vector_payload = _payload(query_vectors)
    if any(str(vector_payload.get(field) or "") != str(plan.get(field) or "") for field in ("design_scope_sha256", "query_plan_sha256")):
        return _error(trace_id, "QUERY_VECTOR_PLAN_MISMATCH", "query vectors가 현재 query plan의 scope/hash lock과 일치하지 않습니다.")
    try:
        vectors, query_embedding_contract = _query_vectors(query_vectors)
    except ValueError as exc:
        code = str(exc)
        messages = {
            "EMBEDDING_RUNTIME_CONTRACT_INVALID": "query vector의 embedding runtime 계약이 유효하지 않습니다.",
            "EMBEDDING_RUNTIME_CONTRACT_FINGERPRINT_INVALID": "query vector의 embedding runtime fingerprint가 계약과 일치하지 않습니다.",
            "VECTOR_PAYLOAD_INVALID": "query vector payload가 유효하지 않습니다.",
            "VECTOR_NUMERIC_INVALID": "query vector는 bool이 아닌 유한 숫자로만 구성되어야 합니다.",
            "VECTOR_DIMENSION_MISMATCH": "query vector dimension이 계약 또는 다른 vector와 다릅니다.",
        }
        return _error(trace_id, code if code in messages else "VECTOR_PAYLOAD_INVALID", messages.get(code, "query vector가 유효하지 않습니다."))
    query_ids = {str(item.get("query_id")) for item in plan["queries"] if isinstance(item, dict) and item.get("query_id")}
    if len(query_ids) != len(plan["queries"]):
        return _error(trace_id, "INVALID_QUERY_PLAN", "모든 query에는 중복되지 않는 query_id가 필요합니다.")
    if not query_ids or set(vectors) != query_ids:
        return _error(trace_id, "VECTOR_QUERY_MISSING", "모든 query plan ID에 정확히 대응하는 vector가 필요합니다.")
    top_n = max(1, min(50, int(top_n or 20)))
    source_limit = max(top_n, min(MAX_SOURCE_CANDIDATES, int(source_limit or 50)))
    try:
        safe_database_name = _safe_identifier(database_name, "business_work_design")
        safe_chunks_collection = _safe_identifier(chunks_collection, "catalog_asset_chunks")
        safe_assets_collection = _safe_identifier(assets_collection, "catalog_assets")
        safe_pointer_collection = _safe_identifier(pointer_collection, "catalog_active_pointers")
        safe_lexical_index_name = _safe_identifier(lexical_index_name, "catalog_lexical")
        safe_vector_index_name = _safe_identifier(vector_index_name, "catalog_vector")
    except ValueError:
        return _error(trace_id, "INVALID_SEARCH_CONFIGURATION", "database, collection 또는 index 이름 형식이 잘못되었습니다.")
    try:
        backend_result = _retrieve_from_mongodb(
            mongodb_uri=uri,
            database_name=safe_database_name,
            chunks_collection=safe_chunks_collection,
            assets_collection=safe_assets_collection,
            pointer_collection=safe_pointer_collection,
            tenant_id=tenant,
            snapshot_id=snapshot,
            acl=acl,
            query_plan=plan,
            vectors=vectors,
            query_embedding_contract=query_embedding_contract,
            provider_mode=mode,
            lexical_index_name=safe_lexical_index_name,
            vector_index_name=safe_vector_index_name,
            source_limit=source_limit,
            server_selection_timeout_ms=max(1000, min(30000, int(server_selection_timeout_ms or 5000))),
            query_timeout_ms=max(1000, min(60000, int(query_timeout_ms or 10000))),
        )
    except OperationFailure as exc:
        if exc.code == 50:
            return _error(trace_id, "SEARCH_TIMEOUT", "MongoDB 검색 실행 시간이 초과되었습니다.", retryable=True, details={"mongo_code": exc.code})
        return _error(trace_id, "SEARCH_OPERATOR_UNAVAILABLE", "선택한 검색 provider mode를 MongoDB가 실행하지 못했습니다.", retryable=False, details={"provider_mode": mode, "mongo_code": exc.code})
    except (ServerSelectionTimeoutError, ConnectionFailure) as exc:
        return _error(trace_id, "SEARCH_TIMEOUT", "MongoDB 검색 연결 시간이 초과되었습니다.", retryable=True, details={"exception_type": type(exc).__name__})
    except (ConfigurationError, PyMongoError) as exc:
        return _error(trace_id, "SEARCH_PROVIDER_ERROR", "MongoDB 검색 provider 오류가 발생했습니다.", retryable=False, details={"exception_type": type(exc).__name__})

    active_snapshot = str(backend_result.get("active_snapshot_id") or "")
    if not active_snapshot:
        return _error(trace_id, "CATALOG_NOT_READY", "활성 catalog snapshot이 없습니다.")
    if active_snapshot != snapshot:
        return _error(trace_id, "ACTIVE_SNAPSHOT_MISMATCH", "요청 snapshot이 현재 활성 snapshot과 일치하지 않습니다.", details={"requested_snapshot_id": snapshot})
    contract_error = str(backend_result.get("contract_error") or "")
    if contract_error:
        messages = {
            "CATALOG_EMBEDDING_CONTRACT_MISSING": "활성 catalog pointer에 embedding 계약이 없습니다. snapshot을 재검증·재활성화해야 합니다.",
            "QUERY_EMBEDDING_CONTRACT_MISMATCH": "query embedding runtime 계약이 활성 catalog와 일치하지 않습니다.",
        }
        return _error(trace_id, contract_error, messages.get(contract_error, "embedding 계약이 일치하지 않습니다."))
    source_results = backend_result.get("source_results") if isinstance(backend_result.get("source_results"), dict) else {}
    query_kind_by_id = {
        str(item.get("query_id")): str(item.get("kind") or "")
        for item in plan["queries"]
        if isinstance(item, dict) and item.get("query_id")
    }
    source_weights = {
        f"vector:{query_id}": QUERY_KIND_WEIGHTS.get(kind, 1.0)
        for query_id, kind in query_kind_by_id.items()
    }
    fused = _rrf(
        {key: value for key, value in source_results.items() if isinstance(value, list)},
        source_weights=source_weights,
    )
    if not fused:
        return _error(trace_id, "NO_CANDIDATES", "권한과 검색 조건을 만족하는 catalog 후보가 없습니다.")

    candidates: list[dict[str, Any]] = []
    rejected_counts = {"scope_or_acl": 0, "invalid_identity": 0}
    native_pipeline_query_ids = (
        backend_result.get("native_pipeline_query_ids")
        if isinstance(backend_result.get("native_pipeline_query_ids"), dict)
        else {}
    )
    for item in fused:
        if not _acl_allows(item, tenant, snapshot, acl):
            rejected_counts["scope_or_acl"] += 1
            continue
        ranks = item.get("_source_ranks") if isinstance(item.get("_source_ranks"), dict) else {}
        vector_ranks = {
            source.split(":", 1)[1]: rank
            for source, rank in ranks.items()
            if source.startswith("vector:")
        }
        matched_query_ids = sorted(vector_ranks)
        native_query_ids, native_vector_evidence = _native_vector_contributions(item, native_pipeline_query_ids)
        if native_query_ids:
            matched_query_ids = native_query_ids
        combined_match_sources = [
            source
            for source in ("exact", "lexical", "native_fused", "relation")
            if ranks.get(source) is not None
        ]
        cleaned = _clean_document(item, "fusion", int(item.get("_fused_rank") or 0), matched_query_ids)
        if not cleaned["asset_id"] or not cleaned["version"] or cleaned["asset_type"] not in {"component", "flow"}:
            rejected_counts["invalid_identity"] += 1
            continue
        cleaned["recommendation_status"] = "candidate"
        cleaned["retrieval_trace"] = {
            "exact_rank": ranks.get("exact"),
            "lexical_rank": ranks.get("lexical"),
            "vector_rank": ranks.get("vector") or (min(vector_ranks.values()) if vector_ranks else None),
            "vector_ranks_by_query_id": vector_ranks,
            "native_vector_evidence_by_query_id": native_vector_evidence,
            "native_fused_rank": ranks.get("native_fused"),
            "relation_rank": ranks.get("relation"),
            "combined_match_sources": combined_match_sources,
            "fused_rank": int(item.get("_fused_rank") or 0),
            "fused_score": round(float(item.get("_fused_score") or 0.0), 12),
            "snapshot_id": snapshot,
            "query_ids": matched_query_ids,
        }
        candidates.append(cleaned)
        if len(candidates) >= top_n:
            break
    if not candidates:
        return _error(trace_id, "NO_AUTHORIZED_CANDIDATES", "후보 재검증 후 권한이 확인된 자산이 없습니다.")
    return {
        "ok": True,
        "status": "COMPLETED",
        "tenant_id": tenant,
        "snapshot_id": snapshot,
        "work_definition_id": str(plan["work_definition_id"]),
        "work_definition_revision": plan["work_definition_revision"],
        "approved_hash": str(plan["approved_hash"]),
        "design_scope_sha256": str(plan["design_scope_sha256"]),
        "query_plan_sha256": str(plan.get("query_plan_sha256") or ""),
        "provider_mode": mode,
        "candidates": candidates,
        "retrieval_trace": {
            "tenant_id": tenant,
            "snapshot_id": snapshot,
            "work_definition_id": str(plan["work_definition_id"]),
            "work_definition_revision": plan["work_definition_revision"],
            "approved_hash": str(plan["approved_hash"]),
            "design_scope_sha256": str(plan["design_scope_sha256"]),
            "query_plan_sha256": str(plan.get("query_plan_sha256") or ""),
            "source_counts": {key: len(value) for key, value in source_results.items() if isinstance(value, list)},
            "post_filter_rejected": rejected_counts,
            "returned_count": len(candidates),
            "rrf_k": 60,
            "query_kind_weights": {
                query_id: QUERY_KIND_WEIGHTS.get(kind, 1.0)
                for query_id, kind in sorted(query_kind_by_id.items())
            },
            "silent_fallback_used": False,
            "missing_parent_count": int(backend_result.get("missing_parent_count") or 0),
        },
        "trace_id": trace_id,
    }


def _error(
    trace_id: str,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "BLOCKED",
        "artifact_refs": [],
        "error": {"code": code, "message": message, "retryable": retryable, "details": details or {}},
        "resume": None,
        "trace_id": trace_id,
    }


class CatalogHybridRetrieverComponent(Component):
    display_name = "21 Catalog Hybrid Retriever"
    description = "활성 snapshot에서 tenant/ACL을 적용한 exact, lexical, vector 후보를 명시적 provider mode로 결합합니다."
    icon = "SearchCheck"
    name = "CatalogHybridRetriever"

    inputs = [
        DataInput(name="query_plan", display_name="Search Query Plan", required=True),
        DataInput(name="query_vectors", display_name="Query Vectors", required=True),
        DropdownInput(name="provider_mode", display_name="Provider Mode", options=sorted(PROVIDER_MODES), value="application_rrf"),
        SecretStrInput(name="mongodb_uri", display_name="MongoDB URI", required=True),
        MessageTextInput(name="database_name", display_name="MongoDB Database", value="business_work_design", advanced=True),
        MessageTextInput(name="chunks_collection", display_name="Chunks Collection", value="catalog_asset_chunks", advanced=True),
        MessageTextInput(name="assets_collection", display_name="Assets Collection", value="catalog_assets", advanced=True),
        MessageTextInput(name="pointer_collection", display_name="Active Pointer Collection", value="catalog_active_pointers", advanced=True),
        MessageTextInput(name="lexical_index_name", display_name="Lexical Index", value="catalog_lexical", advanced=True),
        MessageTextInput(name="vector_index_name", display_name="Vector Index", value="catalog_vector", advanced=True),
        IntInput(name="top_n", display_name="Final Top N", value=20, advanced=True),
        IntInput(name="source_limit", display_name="Per-source Candidate Limit", value=50, advanced=True),
        IntInput(name="server_selection_timeout_ms", display_name="Server Selection Timeout (ms)", value=5000, advanced=True),
        IntInput(name="query_timeout_ms", display_name="Query Timeout (ms)", value=10000, advanced=True),
    ]
    outputs = [Output(name="retrieval_result", display_name="Retrieval Result", method="build_retrieval_result", types=["Data"])]

    def build_retrieval_result(self) -> Data:
        result = retrieve_catalog_candidates(
            self.query_plan,
            self.query_vectors,
            provider_mode=self.provider_mode,
            mongodb_uri=self.mongodb_uri,
            database_name=getattr(self, "database_name", "business_work_design"),
            chunks_collection=getattr(self, "chunks_collection", "catalog_asset_chunks"),
            assets_collection=getattr(self, "assets_collection", "catalog_assets"),
            pointer_collection=getattr(self, "pointer_collection", "catalog_active_pointers"),
            lexical_index_name=getattr(self, "lexical_index_name", "catalog_lexical"),
            vector_index_name=getattr(self, "vector_index_name", "catalog_vector"),
            top_n=getattr(self, "top_n", 20),
            source_limit=getattr(self, "source_limit", 50),
            server_selection_timeout_ms=getattr(self, "server_selection_timeout_ms", 5000),
            query_timeout_ms=getattr(self, "query_timeout_ms", 10000),
        )
        self.status = f"Hybrid retrieval: {result.get('status')} / mode={result.get('provider_mode', self.provider_mode)} / candidates={len(result.get('candidates', []))}"
        return Data(data=result)
