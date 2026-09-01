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
from urllib.parse import parse_qsl, urlsplit, urlunsplit

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
MAX_PARENT_LEXICAL_SCAN = 2000
QUERY_KIND_WEIGHTS = {
    "purpose": 1.20,
    "capability": 1.25,
    "exact": 1.50,
    "risk": 1.15,
    "reporting": 1.00,
}
HYBRID_FUSION_VERSION = "normalized_weighted_hybrid/v1"
# The default application mode deliberately keeps keyword and semantic evidence
# separate until the final, explainable fusion step.  A parent-only lexical
# lane is lower weighted than the Atlas/BM25 lane: it is a portable recall
# safety net when Atlas Search is unavailable, not a replacement for it.
HYBRID_FAMILY_WEIGHTS = {
    "exact": 0.70,
    "lexical": 0.55,
    "parent_lexical": 0.35,
    "catalog_fallback": 0.25,
    "vector": 0.55,
    "native_fused": 0.75,
    "relation": 0.10,
}
HYBRID_COVERAGE_BONUS = 0.08
_FALLBACK_TOKEN_PATTERN = re.compile(r"[0-9A-Za-z가-힣]{2,}")
_CAMEL_CASE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_FALLBACK_STOP_TOKENS = {
    "agent", "ai", "api", "flow", "langflow", "workflow",
    "같이", "관련", "구현", "기능", "기반", "대해", "대한", "위해", "있는", "이런", "이것", "작업", "업무", "자동", "사용", "처리", "통해", "필요", "포함", "수정", "생성", "결과", "정보", "설계", "검증", "적용", "기존", "신규",
}
_RUNTIME_CONTRACT_SCHEMA = "embedding-runtime-contract/v2"
_RUNTIME_CONTRACT_FIELDS = ("schema_version", "runtime_class", "model_id", "dimension", "fingerprint")
_MAX_EMBEDDING_DIMENSION = 65536
_CATALOG_URL_FIELDS = ("catalog_url", "detail_url", "asset_url", "link", "url")
_SECRET_URL_QUERY_KEY_PATTERN = re.compile(
    r"(?:^|[_-])(api[_-]?key|authorization|cookie|credential|password|passwd|secret|session|token)(?:$|[_-])",
    re.IGNORECASE,
)


def _has_secret_url_query_key(value: Any) -> bool:
    key = str(value or "").casefold()
    compact = re.sub(r"[^a-z0-9]", "", key)
    return bool(_SECRET_URL_QUERY_KEY_PATTERN.search(key)) or any(
        marker in compact
        for marker in ("apikey", "authorization", "cookie", "credential", "password", "passwd", "secret", "session", "token")
    )


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


def _forward_blocked_envelope(value: Any, *, trace_id: str) -> dict[str, Any] | None:
    """Keep a prior query/vector failure visible through the linear search chain."""
    payload = _payload(value)
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if payload.get("ok") is not False or str(payload.get("status") or "") != "BLOCKED" or not isinstance(error, dict):
        return None
    details = error.get("details")
    forwarded_details = dict(details) if isinstance(details, dict) else {}
    upstream_trace_id = str(payload.get("trace_id") or "").strip()
    if upstream_trace_id:
        forwarded_details.setdefault("upstream_trace_id", upstream_trace_id)
    return _error(
        trace_id,
        str(error.get("code") or "UPSTREAM_SEARCH_STAGE_BLOCKED"),
        str(error.get("message") or "이전 검색 단계가 차단되었습니다."),
        retryable=error.get("retryable") is True,
        details=forwarded_details,
    )


def _retryable_embedding_provider_failure(value: Any) -> dict[str, Any] | None:
    """Return a safe keyword-only reason for a transient vector-provider fault.

    Authentication, malformed vector, and sealed-plan faults remain blocked.
    Only Component 29's explicit *retryable* provider error can fall back to
    keyword retrieval because it carries no untrusted vector evidence.
    """

    payload = _payload(value)
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if (
        payload.get("ok") is not False
        or str(payload.get("status") or "") != "BLOCKED"
        or not isinstance(error, dict)
        or str(error.get("code") or "") != "EMBEDDING_PROVIDER_ERROR"
        or error.get("retryable") is not True
    ):
        return None
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    result = {
        "mode": "keyword_only_embedding_provider_recovery",
        "reason_code": "EMBEDDING_PROVIDER_ERROR",
        "retryable": True,
    }
    upstream_trace_id = str(payload.get("trace_id") or "").strip()
    if upstream_trace_id:
        result["upstream_trace_id"] = upstream_trace_id
    category = str(details.get("provider_failure_category") or "").strip()
    if category:
        result["provider_failure_category"] = category[:100]
    return result


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


def _safe_catalog_url(value: Any) -> str:
    """Return a bounded, non-credentialed HTTP(S) catalog detail URL.

    Parent documents are data, not trusted UI markup.  Re-validate the
    optional display URL at retrieval time so a manually modified MongoDB
    document cannot turn into an unsafe report link downstream.
    """

    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or len(text) > 2048 or any(ord(character) < 32 or ord(character) == 127 for character in text):
        return ""
    if any(character.isspace() for character in text):
        return ""
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError:
        return ""
    scheme = parsed.scheme.casefold()
    hostname = parsed.hostname
    if scheme not in {"http", "https"} or not hostname or parsed.username is not None or parsed.password is not None:
        return ""
    if len(hostname) > 253:
        return ""
    try:
        query_pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=False)
    except ValueError:
        return ""
    if any(_has_secret_url_query_key(key) for key, _ in query_pairs):
        return ""
    normalized_host = hostname.casefold()
    if ":" in normalized_host and not normalized_host.startswith("["):
        normalized_host = f"[{normalized_host}]"
    netloc = normalized_host if port is None else f"{normalized_host}:{port}"
    return urlunsplit((scheme, netloc, parsed.path, parsed.query, ""))


def _catalog_detail_url(document: dict[str, Any]) -> str:
    """Use the normalized parent URL, with a safe legacy-record fallback."""

    sources: list[dict[str, Any]] = [document]
    raw_record = document.get("raw_record_redacted")
    if isinstance(raw_record, dict):
        sources.append(raw_record)
    for source in sources:
        for field in _CATALOG_URL_FIELDS:
            normalized = _safe_catalog_url(source.get(field))
            if normalized:
                return normalized
    return ""


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
        "catalog_url": _catalog_detail_url(document),
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


def _fallback_query_tokens(query_plan: dict[str, Any]) -> list[str]:
    """Build a bounded lexical signal for the authorized-parent fallback.

    Atlas search/vector indexes are the normal path.  This path is used only
    when every normal source is empty, so a failed or stale index does not turn
    into an empty allowlist despite an otherwise valid active snapshot.
    """

    tokens: list[str] = []
    queries = query_plan.get("queries") if isinstance(query_plan.get("queries"), list) else []
    for query in queries[:MAX_QUERY_COUNT]:
        if not isinstance(query, dict):
            continue
        for token in _lexical_tokens(query.get("text")):
            clean = token.strip()
            if clean in _FALLBACK_STOP_TOKENS or clean in tokens:
                continue
            tokens.append(clean[:80])
            if len(tokens) >= 80:
                return tokens
    return tokens


def _lexical_tokens(value: Any, *, maximum_characters: int = 12_000) -> list[str]:
    """Tokenize Korean/Latin metadata without relying on an Atlas analyzer.

    Component titles in this catalog often use ``PascalCase`` class names
    (for example ``DatalakeStarrocksQueryComponent``).  Splitting their case
    boundaries lets a sealed query such as ``StarRocks 조회`` find the same
    record through the portable keyword lane.
    """

    text = unicodedata.normalize("NFKC", str(value or ""))[:maximum_characters]
    # Preserve the unsplit identifier *and* its case-boundary form.  This
    # covers both ``StarRocks`` (query) and ``DatalakeStarrocks...`` (asset
    # title), whose boundary positions do not necessarily line up.
    tokens: list[str] = []
    for variant in (text, _CAMEL_CASE_BOUNDARY.sub(" ", text)):
        for token in _FALLBACK_TOKEN_PATTERN.findall(variant.casefold()):
            if token not in tokens:
                tokens.append(token)
    return tokens


def _query_phrase_terms(query_plan: dict[str, Any]) -> list[str]:
    """Return bounded normalized phrases for an additional exact-phrase boost.

    This is intentionally derived from the sealed query plan, not from a user
    supplied free-form string.  Phrases are only a tie-breaking lexical signal;
    the strict parent exact lane still owns exact asset identity matching.
    """

    result: list[str] = []
    queries = query_plan.get("queries") if isinstance(query_plan.get("queries"), list) else []
    for query in queries[:MAX_QUERY_COUNT]:
        if not isinstance(query, dict):
            continue
        phrase = _normalize_exact_key(query.get("text"))
        if len(phrase) >= 3 and phrase not in result:
            result.append(phrase[:500])
        if len(result) >= 30:
            break
    return result


def _parent_lexical_fields(document: dict[str, Any]) -> dict[str, list[str]]:
    """Tokenize only known, redacted parent metadata fields.

    F00 guarantees that ``lexical_text_redacted`` does not contain secrets.
    We still cap every field because this branch is the portable fallback for
    deployments without Atlas Search and must remain bounded.
    """

    sources = {
        "title": document.get("title"),
        "aliases": document.get("aliases_normalized"),
        "category": document.get("category"),
        "description": document.get("description"),
        "lexical": document.get("lexical_text_redacted") or document.get("readme"),
        "asset_id": document.get("asset_id") or document.get("id"),
    }
    result: dict[str, list[str]] = {}
    for name, value in sources.items():
        if isinstance(value, list):
            raw = " ".join(str(item or "") for item in value[:100])
        else:
            raw = str(value or "")
        result[name] = _lexical_tokens(raw)
    return result


def _deterministic_parent_lexical_search(
    collection: Any,
    base_filter: dict[str, Any],
    query_plan: dict[str, Any],
    source_limit: int,
    query_timeout_ms: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run a portable BM25-like keyword lane over ACL-scoped parent records.

    Atlas ``$search`` is the preferred lexical/BM25 implementation.  This
    lane is intentionally deterministic and independent of Atlas so a vector
    provider or Search index outage does not make a valid catalog disappear.
    It never queries outside the same tenant, active snapshot, asset-type, and
    ACL filter used by all other lanes.
    """

    scan_limit = max(source_limit, min(MAX_PARENT_LEXICAL_SCAN, max(200, source_limit * 20)))
    cursor = collection.find(base_filter, {"_id": 0}, max_time_ms=query_timeout_ms)
    # A portable lane cannot rely on an Atlas score ordering.  A stable parent
    # identity ordering makes the bounded sample reproducible when the
    # collection is larger than the safety cap; fake/test cursors may not
    # implement ``sort`` and are already supplied in deterministic order.
    try:
        cursor = cursor.sort([("asset_id", 1), ("version", 1)])
    except (AttributeError, TypeError):
        pass
    try:
        documents = [dict(item) for item in cursor.limit(scan_limit) if isinstance(item, dict)]
    except AttributeError:
        documents = [dict(item) for item in list(cursor)[:scan_limit] if isinstance(item, dict)]
    terms = _fallback_query_tokens(query_plan)
    phrases = _query_phrase_terms(query_plan)
    if not documents or not terms:
        return [], {
            "used": False,
            "mode": "deterministic_parent_bm25_like",
            "candidate_pool_sampled": len(documents),
            "matched_asset_count": 0,
            "query_token_count": len(terms),
            "semantic_match_verified": False,
        }

    prepared: list[tuple[dict[str, Any], dict[str, list[str]], int]] = []
    document_frequency = {term: 0 for term in terms}
    total_length = 0
    for document in documents:
        fields = _parent_lexical_fields(document)
        field_length = sum(len(tokens) for tokens in fields.values())
        total_length += field_length
        present = {token for tokens in fields.values() for token in set(tokens)}
        for term in terms:
            if term in present:
                document_frequency[term] += 1
        prepared.append((document, fields, field_length))

    average_length = max(1.0, total_length / len(prepared))
    field_weights = {
        "title": 6.0,
        "aliases": 5.0,
        "asset_id": 4.0,
        "category": 3.0,
        "description": 2.0,
        "lexical": 1.0,
    }
    ranked: list[dict[str, Any]] = []
    for document, fields, document_length in prepared:
        frequencies = {
            name: {term: tokens.count(term) for term in terms if term in tokens}
            for name, tokens in fields.items()
        }
        matched_terms: list[str] = []
        score = 0.0
        for term in terms:
            weighted_tf = sum(field_weights[name] * values.get(term, 0) for name, values in frequencies.items())
            if weighted_tf <= 0:
                continue
            matched_terms.append(term)
            # BM25-shaped scoring: a term appearing in fewer authorized
            # catalog parents receives more weight, while repeated text does
            # not grow without bound.
            idf = math.log(1.0 + (len(prepared) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            norm = 1.2 * (1.0 - 0.75 + 0.75 * (document_length / average_length))
            score += idf * ((weighted_tf * 2.2) / (weighted_tf + norm))
        searchable_text = _normalize_exact_key(
            " ".join(
                str(document.get(field) or "")
                for field in ("title", "aliases_normalized", "category", "description", "lexical_text_redacted", "readme")
            )
        )
        title_text = _normalize_exact_key(document.get("title"))
        phrase_hits = [phrase for phrase in phrases if phrase in searchable_text]
        score += sum(1.5 if phrase in title_text else 0.5 for phrase in phrase_hits)
        if not matched_terms:
            continue
        item = dict(document)
        item["_lexical_match_score"] = round(score, 12)
        item["_lexical_matched_terms"] = matched_terms[:40]
        item["_lexical_phrase_hits"] = phrase_hits[:20]
        item["_lexical_method"] = "deterministic_parent_bm25_like"
        ranked.append(item)

    ranked.sort(
        key=lambda item: (
            -float(item.get("_lexical_match_score") or 0.0),
            -len(item.get("_lexical_matched_terms") or []),
            -_popularity(item, "stars"),
            -_popularity(item, "downloads"),
            -_updated_timestamp(item),
            str(item.get("asset_id") or item.get("id") or ""),
            str(item.get("version") or ""),
        )
    )
    selected = ranked[:source_limit]
    return selected, {
        "used": bool(selected),
        "mode": "deterministic_parent_bm25_like",
        "candidate_pool_sampled": len(documents),
        "matched_asset_count": len(ranked),
        "query_token_count": len(terms),
        "semantic_match_verified": False,
    }


def _authorized_parent_fallback(
    collection: Any,
    base_filter: dict[str, Any],
    query_plan: dict[str, Any],
    source_limit: int,
    query_timeout_ms: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return an ACL-scoped, deterministic fallback when every index lane is empty.

    The fallback never reads around tenant/snapshot/ACL constraints.  It is not
    presented as vector evidence: its lower-confidence origin and lexical
    match score stay in the retrieval trace for the Blueprint and report.
    """

    selected, lexical_trace = _deterministic_parent_lexical_search(
        collection,
        base_filter,
        query_plan,
        source_limit,
        query_timeout_ms,
    )
    for item in selected:
        item["_fallback_match_score"] = float(item.get("_lexical_match_score") or 0.0)
        item["_fallback_matched_token_count"] = len(item.get("_lexical_matched_terms") or [])
    return selected, {
        "used": bool(selected),
        "mode": "authorized_parent_lexical_fallback",
        "reason": "HYBRID_INDEX_SOURCES_EMPTY" if selected else "NO_AUTHORIZED_PARENT_LEXICAL_MATCH",
        "candidate_pool_sampled": lexical_trace["candidate_pool_sampled"],
        "matched_asset_count": lexical_trace["matched_asset_count"],
        "query_token_count": lexical_trace["query_token_count"],
        "semantic_match_verified": False,
    }


def _scope_probe(
    chunks_collection: Any,
    parents_collection: Any,
    base_filter: dict[str, Any],
    tenant_id: str,
    snapshot_id: str,
    query_timeout_ms: int,
) -> dict[str, Any]:
    """Emit safe existence probes to distinguish an empty index from empty data."""

    probes = {
        "tenant_snapshot_chunk_exists": (chunks_collection, {"tenant_id": tenant_id, "snapshot_id": snapshot_id}),
        "authorized_chunk_exists": (chunks_collection, base_filter),
        "authorized_asset_exists": (parents_collection, base_filter),
    }
    result: dict[str, Any] = {}
    for label, (collection, query) in probes.items():
        try:
            result[label] = bool(collection.find_one(query, {"_id": 1}, max_time_ms=query_timeout_ms))
        except (AttributeError, TypeError, PyMongoError):
            result[label] = None
    return result


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


def _search_projection(score_meta: str) -> dict[str, Any]:
    """Return the bounded chunk projection shared by keyword/vector lanes."""

    return {
        "_id": 0,
        "score": {"$meta": score_meta},
        "asset_id": 1,
        "version": 1,
        "asset_type": 1,
        "title": 1,
        "description": 1,
        "category": 1,
        "readme": 1,
        "lexical_text_redacted": 1,
        "technical_contract_status": 1,
        "ports": 1,
        "relations": 1,
        "tenant_id": 1,
        "snapshot_id": 1,
        "acl": 1,
        "stars_count": 1,
        "downloads_count": 1,
        "updated_at": 1,
        "chunk_id": 1,
    }


def _atlas_lexical_search(
    collection: Any,
    *,
    lexical_index_name: str,
    search_text: str,
    search_filters: list[dict[str, Any]],
    source_limit: int,
    query_timeout_ms: int,
) -> list[dict[str, Any]]:
    """Run Atlas keyword/BM25 search with pre-candidate authorization filters."""

    if not search_text.strip():
        return []
    return list(
        collection.aggregate(
            [
                {
                    "$search": {
                        "index": lexical_index_name,
                        "compound": {
                            "must": [
                                {
                                    "text": {
                                        "query": search_text,
                                        "path": [
                                            "title",
                                            "aliases_normalized",
                                            "description",
                                            "lexical_text_redacted",
                                            "category",
                                        ],
                                    }
                                }
                            ],
                            "filter": search_filters,
                        },
                    }
                },
                {"$limit": source_limit},
                {"$project": _search_projection("searchScore")},
            ],
            maxTimeMS=query_timeout_ms,
        )
    )


def _atlas_vector_search(
    collection: Any,
    *,
    vector_index_name: str,
    vector: list[float],
    base_filter: dict[str, Any],
    source_limit: int,
    query_timeout_ms: int,
) -> list[dict[str, Any]]:
    """Run one vector lane; caller records an operator failure without leaking it."""

    return list(
        collection.aggregate(
            [
                {
                    "$vectorSearch": {
                        "index": vector_index_name,
                        "path": "embedding.vector",
                        "queryVector": vector,
                        "numCandidates": min(1000, source_limit * 10),
                        "limit": source_limit,
                        "filter": base_filter,
                    }
                },
                {"$project": _search_projection("vectorSearchScore")},
            ],
            maxTimeMS=query_timeout_ms,
        )
    )


def _safe_operator_diagnostic(exc: OperationFailure) -> dict[str, Any]:
    """Expose only stable operator diagnostics, never raw server detail."""

    code = getattr(exc, "code", None)
    return {
        "status": "unavailable",
        "mongo_code": int(code) if isinstance(code, int) and not isinstance(code, bool) else None,
        "exception_type": type(exc).__name__,
    }


def _application_hybrid_source_results(
    collection: Any,
    *,
    exact_docs: list[dict[str, Any]],
    search_text: str,
    search_filters: list[dict[str, Any]],
    vectors: dict[str, list[float]],
    lexical_index_name: str,
    vector_index_name: str,
    base_filter: dict[str, Any],
    source_limit: int,
    query_timeout_ms: int,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Collect independent keyword and vector lanes for client-side fusion.

    An Atlas operator/index failure affects only its lane.  The caller retains
    exact and portable parent-keyword evidence under the same scope, while
    connection/authentication failures still propagate normally.
    """

    source_results: dict[str, list[dict[str, Any]]] = {"exact": exact_docs, "lexical": []}
    lane_diagnostics: dict[str, Any] = {"atlas_lexical": {"status": "not_run"}, "atlas_vector": {}}
    try:
        source_results["lexical"] = _atlas_lexical_search(
            collection,
            lexical_index_name=lexical_index_name,
            search_text=search_text,
            search_filters=search_filters,
            source_limit=source_limit,
            query_timeout_ms=query_timeout_ms,
        )
        lane_diagnostics["atlas_lexical"] = {"status": "completed", "returned_count": len(source_results["lexical"])}
    except OperationFailure as exc:
        lane_diagnostics["atlas_lexical"] = _safe_operator_diagnostic(exc)

    for query_id, vector in vectors.items():
        lane_key = f"vector:{query_id}"
        try:
            source_results[lane_key] = _atlas_vector_search(
                collection,
                vector_index_name=vector_index_name,
                vector=vector,
                base_filter=base_filter,
                source_limit=source_limit,
                query_timeout_ms=query_timeout_ms,
            )
            lane_diagnostics["atlas_vector"][query_id] = {
                "status": "completed",
                "returned_count": len(source_results[lane_key]),
            }
        except OperationFailure as exc:
            source_results[lane_key] = []
            lane_diagnostics["atlas_vector"][query_id] = _safe_operator_diagnostic(exc)
    return source_results, lane_diagnostics


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
    query_embedding_contract: dict[str, Any] | None,
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
        pointer_snapshot_id = str((pointer or {}).get("snapshot_id") or "").strip()
        pointer_active_snapshot_id = str((pointer or {}).get("active_snapshot_id") or "").strip()
        # ``active_snapshot_id`` is the publication authority.  A dual-field
        # pointer left in a partially migrated state must never silently select
        # an arbitrary historical snapshot.
        if pointer_snapshot_id and pointer_active_snapshot_id and pointer_snapshot_id != pointer_active_snapshot_id:
            return {
                "active_snapshot_id": "",
                "pointer_error": "ACTIVE_POINTER_AMBIGUOUS",
                "source_results": {},
            }
        active_snapshot_id = pointer_active_snapshot_id or pointer_snapshot_id
        if not active_snapshot_id:
            return {"active_snapshot_id": "", "source_results": {}}
        if active_snapshot_id != snapshot_id:
            return {"active_snapshot_id": active_snapshot_id, "source_results": {}}
        vector_contract_status = "not_requested"
        if vectors:
            try:
                active_contract = _embedding_runtime_contract((pointer or {}).get("embedding_contract"))
            except ValueError:
                return {
                    "active_snapshot_id": active_snapshot_id,
                    "contract_error": "CATALOG_EMBEDDING_CONTRACT_MISSING",
                    "source_results": {},
                }
            if (
                not isinstance(query_embedding_contract, dict)
                or _embedding_runtime_signature(active_contract) != _embedding_runtime_signature(query_embedding_contract)
                or not hmac.compare_digest(
                    str(active_contract["fingerprint"]), str(query_embedding_contract["fingerprint"])
                )
            ):
                return {
                    "active_snapshot_id": active_snapshot_id,
                    "contract_error": "QUERY_EMBEDDING_CONTRACT_MISMATCH",
                    "source_results": {},
                }
            vector_contract_status = "verified"
        else:
            # A keyword-only recovery path does not consume or claim a vector
            # contract.  It still requires the active pointer/snapshot above.
            vector_contract_status = "not_required_keyword_only"

        collection = database[chunks_collection]
        parents_collection = database[assets_collection]
        queries = [item for item in query_plan.get("queries", []) if isinstance(item, dict)]
        # Exact system/API names remain eligible for the strict parent lookup,
        # but they must also participate in lexical retrieval.  Many catalog
        # entries mention an alias or a longer title rather than the exact
        # system name alone.
        search_text = " ".join(str(item.get("text") or "") for item in queries)[:4000]
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
        base_filter = _mongo_base_filter(tenant_id, snapshot_id, acl, asset_types)
        search_filters = _search_filter_clauses(tenant_id, snapshot_id, acl, asset_types)
        scope_diagnostics = _scope_probe(
            collection,
            parents_collection,
            base_filter,
            tenant_id,
            snapshot_id,
            query_timeout_ms,
        )
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

        native_pipeline_query_ids: dict[str, str] = {}
        lane_diagnostics: dict[str, Any] = {}
        effective_provider_mode = provider_mode
        if provider_mode == "application_rrf" or not vectors:
            source_results, lane_diagnostics = _application_hybrid_source_results(
                collection,
                exact_docs=exact_docs,
                search_text=search_text,
                search_filters=search_filters,
                vectors=vectors,
                lexical_index_name=lexical_index_name,
                vector_index_name=vector_index_name,
                base_filter=base_filter,
                source_limit=source_limit,
                query_timeout_ms=query_timeout_ms,
            )
            if provider_mode != "application_rrf":
                effective_provider_mode = "application_rrf_keyword_only"
        else:
            fusion_operator = "$rankFusion" if provider_mode == "native_rank_fusion" else "$scoreFusion"
            fusion_pipelines: dict[str, list[dict[str, Any]]] = {
                "lexical": [
                    {
                        "$search": {
                            "index": lexical_index_name,
                            "compound": {
                                "must": [{"text": {"query": search_text, "path": ["title", "aliases_normalized", "description", "lexical_text_redacted", "category"]}}],
                                "filter": search_filters,
                            },
                        }
                    },
                    {"$limit": source_limit},
                ]
            }
            native_weights: dict[str, float] = {"lexical": 1.0}
            query_kind_by_id = {
                str(item.get("query_id")): str(item.get("kind") or "")
                for item in queries
                if item.get("query_id")
            }
            for index, query_id in enumerate(vectors):
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
            try:
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
                lane_diagnostics = {"native_fusion": {"status": "completed", "returned_count": len(fused_docs)}}
            except OperationFailure as exc:
                # A native fusion operator can be missing on an otherwise
                # usable deployment.  Fall back to independently scoped lanes
                # instead of hiding all keyword/vector recall behind one
                # optional MongoDB feature.
                source_results, lane_diagnostics = _application_hybrid_source_results(
                    collection,
                    exact_docs=exact_docs,
                    search_text=search_text,
                    search_filters=search_filters,
                    vectors=vectors,
                    lexical_index_name=lexical_index_name,
                    vector_index_name=vector_index_name,
                    base_filter=base_filter,
                    source_limit=source_limit,
                    query_timeout_ms=query_timeout_ms,
                )
                lane_diagnostics["native_fusion"] = _safe_operator_diagnostic(exc)
                effective_provider_mode = "application_rrf_native_fallback"

        parent_lexical_docs, parent_lexical_trace = _deterministic_parent_lexical_search(
            parents_collection,
            base_filter,
            query_plan,
            source_limit,
            query_timeout_ms,
        )
        source_results["parent_lexical"] = parent_lexical_docs
        lane_diagnostics["parent_lexical"] = parent_lexical_trace

        fallback: dict[str, Any] = {
            "used": False,
            "mode": "none",
            "reason": "HYBRID_INDEX_SOURCES_AVAILABLE",
            "candidate_pool_sampled": 0,
            "matched_asset_count": 0,
            "query_token_count": 0,
            "semantic_match_verified": False,
        }
        # Do not conceal an empty index as an empty catalog.  A standard
        # MongoDB parent query under the same ACL/snapshot filter provides a
        # deterministic, explicitly low-confidence candidate set while the
        # trace still makes index/filter remediation visible.
        if not any(source_results.values()):
            scope_diagnostics["hybrid_lanes_empty"] = True
            if scope_diagnostics.get("authorized_chunk_exists") is True:
                scope_diagnostics["interpretation"] = "SEARCH_INCONCLUSIVE_OR_INDEX_FILTERED"
            elif scope_diagnostics.get("authorized_chunk_exists") is False:
                scope_diagnostics["interpretation"] = "NO_AUTHORIZED_CATALOG_CHUNKS"
            else:
                scope_diagnostics["interpretation"] = "SEARCH_SCOPE_UNVERIFIED"
            fallback_documents, fallback = _authorized_parent_fallback(
                parents_collection,
                base_filter,
                query_plan,
                source_limit,
                query_timeout_ms,
            )
            if fallback_documents:
                source_results["catalog_fallback"] = fallback_documents
        else:
            scope_diagnostics["hybrid_lanes_empty"] = False
            scope_diagnostics["interpretation"] = "HYBRID_CANDIDATES_FOUND"

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
            "native_pipeline_query_ids": native_pipeline_query_ids if effective_provider_mode == provider_mode and provider_mode != "application_rrf" else {},
            "missing_parent_count": missing_parent_count,
            "scope_diagnostics": scope_diagnostics,
            "fallback": fallback,
            "lane_diagnostics": lane_diagnostics,
            "effective_provider_mode": effective_provider_mode,
            "vector_contract_status": vector_contract_status,
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


def _finite_score(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    score = float(value)
    return score if math.isfinite(score) else None


def _source_raw_score(source: str, item: dict[str, Any]) -> float | None:
    """Use the source's real score when it exists, else deterministic rank."""

    if source in {"parent_lexical", "catalog_fallback"}:
        return _finite_score(item.get("_lexical_match_score", item.get("_fallback_match_score")))
    return _finite_score(item.get("score"))


def _source_lane_evidence(
    source: str,
    items: list[dict[str, Any]],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    """Collapse duplicate chunks and normalize comparable evidence per lane.

    Atlas search/vector scores are only meaningful within their own lane.  We
    normalize each lane before weights are applied, and use a small rank signal
    when a provider does not expose a stable raw score (exact/relation/native
    mock results).  This avoids raw BM25 values overpowering vector similarity.
    """

    documents: dict[tuple[str, str], dict[str, Any]] = {}
    evidence: dict[tuple[str, str], dict[str, Any]] = {}
    for rank, raw in enumerate(items[:MAX_SOURCE_CANDIDATES], start=1):
        if not isinstance(raw, dict):
            continue
        key = (str(raw.get("asset_id") or raw.get("id") or ""), str(raw.get("version") or ""))
        if not all(key) or key in evidence:
            continue
        raw_score = _source_raw_score(source, raw)
        documents[key] = dict(raw)
        evidence[key] = {"rank": rank, "raw_score": raw_score}
    numeric_scores = [entry["raw_score"] for entry in evidence.values() if entry["raw_score"] is not None and entry["raw_score"] > 0]
    maximum = max(numeric_scores) if numeric_scores else None
    lane_size = max(1, len(evidence))
    for entry in evidence.values():
        rank_score = 1.0 / float(entry["rank"])
        raw_score = entry["raw_score"]
        if maximum is not None and raw_score is not None and raw_score >= 0:
            # Keep a little rank distinction when the provider reports equal
            # scores, but do not compare score scales across different lanes.
            normalized = 0.85 * min(1.0, raw_score / maximum) + 0.15 * rank_score
        else:
            normalized = rank_score
        entry["normalized_score"] = round(max(0.0, min(1.0, normalized)), 12)
        entry["lane_size"] = lane_size
    return documents, evidence


def _normalized_weighted_hybrid_fusion(
    source_results: dict[str, list[dict[str, Any]]],
    *,
    query_kind_by_id: dict[str, str],
) -> list[dict[str, Any]]:
    """Fuse keyword/exact/vector evidence with normalized, bounded weights.

    The result is deterministic for a given source payload.  It intentionally
    gives a score trace per candidate so the subsequent Blueprint and report
    can distinguish a keyword-only recommendation from a true hybrid match.
    """

    documents: dict[tuple[str, str], dict[str, Any]] = {}
    by_source: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    for source, items in source_results.items():
        if not isinstance(items, list):
            continue
        source_documents, lane_evidence = _source_lane_evidence(source, items)
        by_source[source] = lane_evidence
        for key, document in source_documents.items():
            if key not in documents:
                documents[key] = document
            elif source == "native_fused" and isinstance(document.get("_fusion_score_details"), dict):
                # Exact parent metadata is preferred for display, but native
                # score details are the authoritative per-query evidence.
                documents[key]["_fusion_score_details"] = dict(document["_fusion_score_details"])

    scored: list[tuple[tuple[str, str], float, dict[str, Any], dict[str, Any]]] = []
    for key, document in documents.items():
        source_evidence: dict[str, dict[str, Any]] = {}
        ranks: dict[str, int] = {}
        for source, lane in by_source.items():
            entry = lane.get(key)
            if entry is None:
                continue
            ranks[source] = int(entry["rank"])
            source_evidence[source] = {
                "rank": int(entry["rank"]),
                "raw_score": entry["raw_score"],
                "normalized_score": entry["normalized_score"],
            }

        family_scores: dict[str, Any] = {}
        total = 0.0
        exact = source_evidence.get("exact")
        if exact is not None:
            contribution = HYBRID_FAMILY_WEIGHTS["exact"] * float(exact["normalized_score"])
            family_scores["exact"] = {"normalized_score": exact["normalized_score"], "weight": HYBRID_FAMILY_WEIGHTS["exact"], "contribution": round(contribution, 12)}
            total += contribution

        lexical_lanes = [
            (source, entry)
            for source, entry in source_evidence.items()
            if source in {"lexical", "parent_lexical", "catalog_fallback"}
        ]
        if lexical_lanes:
            lexical_source, lexical = max(
                lexical_lanes,
                key=lambda item: HYBRID_FAMILY_WEIGHTS[item[0]] * float(item[1]["normalized_score"]),
            )
            contribution = HYBRID_FAMILY_WEIGHTS[lexical_source] * float(lexical["normalized_score"])
            family_scores["lexical"] = {
                "source": lexical_source,
                "normalized_score": lexical["normalized_score"],
                "weight": HYBRID_FAMILY_WEIGHTS[lexical_source],
                "contribution": round(contribution, 12),
            }
            total += contribution

        vector_lanes = [
            (source, entry)
            for source, entry in source_evidence.items()
            if source.startswith("vector:") or source == "vector"
        ]
        if vector_lanes:
            weighted_total = 0.0
            weight_total = 0.0
            per_query: dict[str, Any] = {}
            for source, vector in vector_lanes:
                query_id = source.split(":", 1)[1] if source.startswith("vector:") else "unscoped_vector"
                query_weight = QUERY_KIND_WEIGHTS.get(query_kind_by_id.get(query_id, ""), 1.0)
                normalized = float(vector["normalized_score"])
                weighted_total += normalized * query_weight
                weight_total += query_weight
                per_query[query_id] = {
                    "raw_score": vector["raw_score"],
                    "normalized_score": vector["normalized_score"],
                    "query_kind_weight": query_weight,
                }
            vector_score = weighted_total / weight_total if weight_total else 0.0
            contribution = HYBRID_FAMILY_WEIGHTS["vector"] * vector_score
            family_scores["vector"] = {
                "normalized_score": round(vector_score, 12),
                "weight": HYBRID_FAMILY_WEIGHTS["vector"],
                "contribution": round(contribution, 12),
                "per_query": per_query,
            }
            total += contribution

        native = source_evidence.get("native_fused")
        if native is not None:
            contribution = HYBRID_FAMILY_WEIGHTS["native_fused"] * float(native["normalized_score"])
            family_scores["native_fused"] = {
                "normalized_score": native["normalized_score"],
                "weight": HYBRID_FAMILY_WEIGHTS["native_fused"],
                "contribution": round(contribution, 12),
            }
            total += contribution

        relation = source_evidence.get("relation")
        if relation is not None:
            contribution = HYBRID_FAMILY_WEIGHTS["relation"] * float(relation["normalized_score"])
            family_scores["relation"] = {
                "normalized_score": relation["normalized_score"],
                "weight": HYBRID_FAMILY_WEIGHTS["relation"],
                "contribution": round(contribution, 12),
            }
            total += contribution

        if "lexical" in family_scores and "vector" in family_scores:
            total += HYBRID_COVERAGE_BONUS
            family_scores["hybrid_coverage_bonus"] = HYBRID_COVERAGE_BONUS
        if total <= 0.0:
            continue
        scored.append((key, total, document, {"source_evidence": source_evidence, "source_ranks": ranks, "family_scores": family_scores}))

    scored.sort(
        key=lambda item: (
            -item[1],
            -_popularity(item[2], "stars"),
            -_popularity(item[2], "downloads"),
            -_updated_timestamp(item[2]),
            item[0][0],
            item[0][1],
        )
    )
    result: list[dict[str, Any]] = []
    for fused_rank, (_, score, document, detail) in enumerate(scored, start=1):
        item = dict(document)
        item["_fused_score"] = round(score, 12)
        item["_source_ranks"] = detail["source_ranks"]
        item["_source_evidence"] = detail["source_evidence"]
        item["_family_scores"] = detail["family_scores"]
        item["_fusion_method"] = HYBRID_FUSION_VERSION
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
    blocked_plan = _forward_blocked_envelope(query_plan, trace_id=trace_id)
    if blocked_plan is not None:
        return blocked_plan
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
        return _error(
            trace_id,
            "CATALOG_CONFIGURATION_MISSING",
            "MongoDB URI 환경변수 MONGO_URL을 설정하고 Catalog Hybrid Retriever에 연결해야 합니다.",
            details={
                "next_actions": [
                    "Langflow Global Variable MONGO_URL을 Secret으로 등록합니다.",
                    "F00/F20/F90의 MongoDB URI 입력에 MONGO_URL이 표시되는지 확인합니다.",
                ]
            },
        )
    query_ids = {str(item.get("query_id")) for item in plan["queries"] if isinstance(item, dict) and item.get("query_id")}
    if len(query_ids) != len(plan["queries"]):
        return _error(trace_id, "INVALID_QUERY_PLAN", "모든 query에는 중복되지 않는 query_id가 필요합니다.")
    if not query_ids:
        return _error(trace_id, "INVALID_QUERY_PLAN", "검색 query가 없습니다.")

    # Vector retrieval enhances keyword retrieval; it is not a prerequisite for
    # the exact/keyword lanes.  This is deliberately narrow: only an explicit
    # retryable provider outage from Component 29, or an absent/empty vector
    # payload, enters keyword-only mode.  Sealed-lock and malformed-vector
    # errors remain fail-closed.
    vectors: dict[str, list[float]] = {}
    query_embedding_contract: dict[str, Any] | None = None
    vector_execution: dict[str, Any] = {"mode": "keyword_only", "reason_code": "QUERY_VECTORS_ABSENT", "query_count": len(query_ids)}
    provider_recovery = _retryable_embedding_provider_failure(query_vectors)
    if provider_recovery is not None:
        vector_execution = {**provider_recovery, "query_count": len(query_ids), "vector_count": 0}
    else:
        blocked_vectors = _forward_blocked_envelope(query_vectors, trace_id=trace_id)
        if blocked_vectors is not None:
            return blocked_vectors
        vector_payload = _payload(query_vectors)
        supplied_lock_fields = ("design_scope_sha256", "query_plan_sha256")
        if any(field in vector_payload for field in supplied_lock_fields) and any(
            str(vector_payload.get(field) or "") != str(plan.get(field) or "")
            for field in supplied_lock_fields
        ):
            return _error(trace_id, "QUERY_VECTOR_PLAN_MISMATCH", "query vectors가 현재 query plan의 scope/hash lock과 일치하지 않습니다.")
        raw_vectors = vector_payload.get("vectors") if isinstance(vector_payload, dict) else None
        if raw_vectors in (None, {}, []):
            vector_execution = {"mode": "keyword_only", "reason_code": "QUERY_VECTORS_ABSENT", "query_count": len(query_ids), "vector_count": 0}
        else:
            if not all(field in vector_payload for field in supplied_lock_fields):
                return _error(trace_id, "QUERY_VECTOR_PLAN_MISMATCH", "vector 검색에는 현재 query plan의 scope/hash lock이 필요합니다.")
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
            if not vectors:
                vector_execution = {"mode": "keyword_only", "reason_code": "QUERY_VECTORS_ABSENT", "query_count": len(query_ids), "vector_count": 0}
            elif not set(vectors).issubset(query_ids):
                return _error(trace_id, "VECTOR_QUERY_MISMATCH", "query vector에 현재 plan에 없는 query_id가 포함되어 있습니다.")
            else:
                missing_query_ids = sorted(query_ids - set(vectors))
                vector_execution = {
                    "mode": "hybrid" if not missing_query_ids else "hybrid_partial_vectors",
                    "reason_code": "ALL_QUERY_VECTORS_AVAILABLE" if not missing_query_ids else "PARTIAL_QUERY_VECTORS_AVAILABLE",
                    "query_count": len(query_ids),
                    "vector_count": len(vectors),
                    "missing_query_ids": missing_query_ids,
                }
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
        return _error(
            trace_id,
            "SEARCH_OPERATOR_UNAVAILABLE",
            "선택한 검색 provider mode를 MongoDB가 실행하지 못했습니다. Atlas Search/Vector Search index 설정을 확인하세요.",
            retryable=False,
            details={
                "provider_mode": mode,
                "mongo_code": exc.code,
                "lexical_index_name": safe_lexical_index_name,
                "vector_index_name": safe_vector_index_name,
                "next_actions": [
                    "catalog_lexical Search index와 catalog_vector Vector Search index가 활성 collection에 있는지 확인합니다.",
                    "index 이름이 component 설정과 일치하는지 확인합니다.",
                ],
            },
        )
    except (ServerSelectionTimeoutError, ConnectionFailure) as exc:
        return _error(trace_id, "SEARCH_TIMEOUT", "MongoDB 검색 연결 시간이 초과되었습니다.", retryable=True, details={"exception_type": type(exc).__name__})
    except (ConfigurationError, PyMongoError) as exc:
        return _error(trace_id, "SEARCH_PROVIDER_ERROR", "MongoDB 검색 provider 오류가 발생했습니다.", retryable=False, details={"exception_type": type(exc).__name__})

    if not isinstance(backend_result, dict):
        return _error(
            trace_id,
            "SEARCH_RESPONSE_INVALID",
            "MongoDB 검색 응답 형식이 유효하지 않습니다.",
            details={"next_actions": ["MongoDB/Atlas Search provider와 index 응답을 확인합니다."]},
        )
    pointer_error = str(backend_result.get("pointer_error") or "")
    if pointer_error:
        return _error(
            trace_id,
            pointer_error if pointer_error == "ACTIVE_POINTER_AMBIGUOUS" else "ACTIVE_POINTER_INVALID",
            "활성 catalog pointer의 snapshot 필드가 일관되지 않습니다. F00 적재/게시 상태를 확인하세요.",
        )
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
    raw_source_results = backend_result.get("source_results")
    if not isinstance(raw_source_results, dict) or any(
        not isinstance(source_name, str) or not isinstance(source_items, list)
        for source_name, source_items in raw_source_results.items()
    ):
        return _error(
            trace_id,
            "SEARCH_RESPONSE_INVALID",
            "MongoDB 검색 응답의 source_results는 source별 list여야 합니다.",
            details={"next_actions": ["Atlas Search/Vector Search index와 hybrid retrieval provider 응답을 확인합니다."]},
        )
    source_results = raw_source_results
    query_kind_by_id = {
        str(item.get("query_id")): str(item.get("kind") or "")
        for item in plan["queries"]
        if isinstance(item, dict) and item.get("query_id")
    }
    def completed_result(
        selected_candidates: list[dict[str, Any]],
        rejected_counts: dict[str, int],
        *,
        empty_result_reason: str | None = None,
    ) -> dict[str, Any]:
        """Return a sealed retrieval result, including a valid empty result.

        An empty *authorized* result is not a backend failure.  Downstream
        blueprint generation can safely continue as long as it receives an
        explicit empty allowlist: it must then avoid all catalog references.
        Connection, snapshot, embedding-contract, and query validation errors
        are handled before this point and remain fail-closed.
        """
        retrieval_trace: dict[str, Any] = {
            "tenant_id": tenant,
            "snapshot_id": snapshot,
            "work_definition_id": str(plan["work_definition_id"]),
            "work_definition_revision": plan["work_definition_revision"],
            "approved_hash": str(plan["approved_hash"]),
            "design_scope_sha256": str(plan["design_scope_sha256"]),
            "query_plan_sha256": str(plan.get("query_plan_sha256") or ""),
            "source_counts": {key: len(value) for key, value in source_results.items() if isinstance(value, list)},
            "post_filter_rejected": rejected_counts,
            "returned_count": len(selected_candidates),
            "fusion": {
                "method": HYBRID_FUSION_VERSION,
                "family_weights": dict(HYBRID_FAMILY_WEIGHTS),
                "hybrid_coverage_bonus": HYBRID_COVERAGE_BONUS,
            },
            "query_kind_weights": {
                query_id: QUERY_KIND_WEIGHTS.get(kind, 1.0)
                for query_id, kind in sorted(query_kind_by_id.items())
            },
            "silent_fallback_used": False,
            "missing_parent_count": int(backend_result.get("missing_parent_count") or 0),
            "scope_diagnostics": (
                dict(backend_result.get("scope_diagnostics"))
                if isinstance(backend_result.get("scope_diagnostics"), dict)
                else {}
            ),
            "fallback": (
                dict(backend_result.get("fallback"))
                if isinstance(backend_result.get("fallback"), dict)
                else {"used": False, "mode": "none"}
            ),
            "vector_execution": {
                **vector_execution,
                "catalog_contract_status": str(backend_result.get("vector_contract_status") or "not_checked"),
            },
            "lane_diagnostics": (
                dict(backend_result.get("lane_diagnostics"))
                if isinstance(backend_result.get("lane_diagnostics"), dict)
                else {}
            ),
            "effective_provider_mode": str(backend_result.get("effective_provider_mode") or mode),
        }
        if empty_result_reason:
            retrieval_trace["empty_result_reason"] = empty_result_reason
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
            "candidates": selected_candidates,
            "retrieval_trace": retrieval_trace,
            "trace_id": trace_id,
        }

    fused = _normalized_weighted_hybrid_fusion(
        {key: value for key, value in source_results.items() if isinstance(value, list)},
        query_kind_by_id=query_kind_by_id,
    )
    if not fused:
        return completed_result(
            [],
            {"scope_or_acl": 0, "invalid_identity": 0},
            empty_result_reason="NO_CANDIDATES",
        )

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
        source_evidence = item.get("_source_evidence") if isinstance(item.get("_source_evidence"), dict) else {}
        family_scores = item.get("_family_scores") if isinstance(item.get("_family_scores"), dict) else {}
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
            for source in ("exact", "lexical", "parent_lexical", "native_fused", "relation", "catalog_fallback", "vector")
            if ranks.get(source) is not None
        ]
        combined_match_sources.extend(sorted(source for source in ranks if source.startswith("vector:")))
        cleaned = _clean_document(item, "fusion", int(item.get("_fused_rank") or 0), matched_query_ids)
        if not cleaned["asset_id"] or not cleaned["version"] or cleaned["asset_type"] not in {"component", "flow"}:
            rejected_counts["invalid_identity"] += 1
            continue
        cleaned["recommendation_status"] = "candidate"
        cleaned["retrieval_trace"] = {
            "exact_rank": ranks.get("exact"),
            "lexical_rank": ranks.get("lexical"),
            "parent_lexical_rank": ranks.get("parent_lexical"),
            "vector_rank": ranks.get("vector") or (min(vector_ranks.values()) if vector_ranks else None),
            "vector_ranks_by_query_id": vector_ranks,
            "native_vector_evidence_by_query_id": native_vector_evidence,
            "native_fused_rank": ranks.get("native_fused"),
            "relation_rank": ranks.get("relation"),
            "catalog_fallback_rank": ranks.get("catalog_fallback"),
            "catalog_fallback_match_score": int(item.get("_fallback_match_score") or 0),
            "catalog_fallback_matched_token_count": int(item.get("_fallback_matched_token_count") or 0),
            "deterministic_lexical_match_score": round(float(item.get("_lexical_match_score") or 0.0), 12),
            "deterministic_lexical_matched_terms": list(item.get("_lexical_matched_terms") or [])[:40],
            "deterministic_lexical_phrase_hits": list(item.get("_lexical_phrase_hits") or [])[:20],
            "combined_match_sources": combined_match_sources,
            "fused_rank": int(item.get("_fused_rank") or 0),
            "fused_score": round(float(item.get("_fused_score") or 0.0), 12),
            "fusion_method": str(item.get("_fusion_method") or HYBRID_FUSION_VERSION),
            "source_score_trace": source_evidence,
            "family_score_trace": family_scores,
            "snapshot_id": snapshot,
            "query_ids": matched_query_ids,
        }
        candidates.append(cleaned)
        if len(candidates) >= top_n:
            break
    if not candidates:
        return completed_result(
            [],
            rejected_counts,
            empty_result_reason="NO_AUTHORIZED_CANDIDATES",
        )
    return completed_result(candidates, rejected_counts)


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
    description = "활성 snapshot에서 tenant/ACL을 먼저 적용한 exact·Atlas keyword·portable keyword·vector evidence를 정규화 가중 결합합니다. vector/Atlas index가 일시 실패하면 같은 범위의 keyword-only 검색으로만 제한적으로 계속하고 모든 lane 근거를 trace에 남깁니다."
    icon = "SearchCheck"
    name = "CatalogHybridRetriever"

    inputs = [
        DataInput(name="query_plan", display_name="Search Query Plan", required=True),
        DataInput(
            name="query_vectors",
            display_name="Query Vectors",
            required=False,
            info="Embedding provider가 일시 실패하면 sealed query plan 기준의 keyword-only 검색으로 계속할 수 있습니다.",
        ),
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
