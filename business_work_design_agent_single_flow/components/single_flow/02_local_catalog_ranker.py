from __future__ import annotations

"""Deterministic local lexical retrieval for the single business-design flow.

No embeddings, database queries, network calls, or LLM calls happen here.  The
ranker combines exact phrase, weighted BM25-like token, and character n-gram lanes
with reciprocal-rank fusion so that the same request and catalog always yield the
same candidate order.
"""

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, IntInput, Output
from lfx.schema import Data


_SCHEMA_VERSION = "local-catalog-retrieval/v1"
_ALGORITHM = "local-multisignal-rrf/v1"
_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]{2,}")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])")
_K1 = 1.5
_B = 0.75
_RRF_K = 60
_LANE_WEIGHTS = {"exact_phrase": 0.25, "token_bm25": 0.55, "character_ngram": 0.20}
_DEFAULT_TOP_N = 100
_DEFAULT_MAX_CANDIDATE_CHARS = 700
_DEFAULT_MAX_CONTEXT_CHARS = 56_000
# This is an internal context budget, not a Canvas selection control. The
# separate 03 LLM shortlister decides which keyword candidates are in scope.
_DEFAULT_EXPANDED_DETAIL_COUNT = 12
_MAX_EXPANDED_DETAIL_CHARS = 900
_FIELD_WEIGHTS = {
    "title": 6.0,
    "aliases": 5.0,
    "capabilities": 5.0,
    "systems": 5.0,
    "tags": 4.0,
    "category": 3.0,
    "description": 2.0,
    "use_cases": 2.0,
    "readme": 1.0,
    "limitations": 1.0,
}
_TECHNICAL_STATUS_PRIORITY = {
    "verified_runtime": 0,
    "flow_graph_extracted": 1,
    "ports_extracted": 2,
    "metadata_only": 3,
    "unknown": 4,
}
# Fixed source-level list: this is intentionally small because Korean terms often
# carry useful meaning even when they look grammatical.
_STOPWORDS = frozenset({"그리고", "또는", "대한", "위한", "에서", "으로", "하는", "합니다", "있는", "the", "and", "for", "with", "from", "into", "this", "that", "using"})


def _raw(value: Any) -> Any:
    data = getattr(value, "data", None)
    return data if isinstance(data, (dict, list)) else value


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _rounded(value: float) -> float:
    return float(f"{value:.6f}")


def _normalise(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = _CAMEL_BOUNDARY.sub(" ", text).casefold()
    return re.sub(r"\s+", " ", text).strip()


def _tokens(value: Any) -> list[str]:
    return [token for token in _TOKEN_RE.findall(_normalise(value)) if token not in _STOPWORDS]


def _field_text(item: dict[str, Any], field: str) -> str:
    value = item.get(field, []) if field in {"aliases", "capabilities", "systems", "tags", "use_cases", "limitations"} else item.get(field, "")
    if isinstance(value, list):
        return "\n".join(str(part) for part in value if isinstance(part, str))
    return str(value or "")


def _ngram_set(value: str) -> set[str]:
    compact = re.sub(r"\s+", "", _normalise(value))
    grams: set[str] = set()
    for size in (2, 3):
        grams.update(compact[index : index + size] for index in range(max(0, len(compact) - size + 1)))
    return {gram for gram in grams if gram}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _require_dict(value: Any, name: str) -> dict[str, Any]:
    value = _raw(value)
    if not isinstance(value, dict):
        raise ValueError(f"{name.upper()}_INVALID: expected a Data object")
    return value


def _truncate_text(value: str, maximum: int) -> str:
    if len(value) <= maximum:
        return value
    if maximum <= 1:
        return value[:maximum]
    return value[: maximum - 1] + "…"


def _string_list(value: Any, maximum_items: int, maximum_item_chars: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        _truncate_text(item, maximum_item_chars)
        for item in value
        if isinstance(item, str) and item.strip()
    ][:maximum_items]


def _ports_summary(value: Any, maximum: int) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(value, dict):
        return {"inputs": [], "outputs": []}
    result: dict[str, list[dict[str, Any]]] = {"inputs": [], "outputs": []}
    for direction in ("inputs", "outputs"):
        ports = value.get(direction, [])
        if not isinstance(ports, list):
            continue
        for port in ports[:20]:
            if not isinstance(port, dict):
                continue
            safe = {
                key: _truncate_text(str(port[key]), 160)
                for key in ("port_id", "name", "label", "data_type", "semantic_role", "schema_ref")
                if isinstance(port.get(key), str)
            }
            for key in ("required", "has_default", "secret", "streaming"):
                if type(port.get(key)) is bool:
                    safe[key] = port[key]
            result[direction].append(safe)
    encoded = _canonical_json(result)
    if len(encoded) <= maximum:
        return result
    # Preserve a truthful, compact contract summary instead of arbitrary JSON cuts.
    return {
        "inputs": [{"name": str(port.get("name") or port.get("label") or "input")[:80]} for port in result["inputs"][:8]],
        "outputs": [{"name": str(port.get("name") or port.get("label") or "output")[:80]} for port in result["outputs"][:8]],
    }


def _candidate_size(candidate: dict[str, Any]) -> int:
    return len(_canonical_json(candidate))


def _compact_optional_candidate_field(candidate: dict[str, Any]) -> bool:
    """Remove one optional explanation while preserving identity and retrieval evidence."""
    value = str(candidate.get("description", ""))
    if len(value) > 48:
        candidate["description"] = _truncate_text(value, max(48, len(value) // 2))
        return True
    return False


def _compact_expanded_detail(detail: dict[str, Any]) -> bool:
    """Shrink only the top-rank detail tier; never touch the 100-item registry."""
    for field in ("readme_excerpt", "limitations", "ports", "use_cases", "tags", "systems", "capabilities", "aliases", "description"):
        if field == "readme_excerpt":
            value = str(detail.get(field, ""))
            if value:
                detail[field] = _truncate_text(value, 120) if len(value) > 120 else ""
                return True
        elif field == "ports":
            if detail.get(field) != {"inputs": [], "outputs": []}:
                detail[field] = {"inputs": [], "outputs": []}
                return True
        elif field in {"limitations", "use_cases", "tags", "systems", "capabilities", "aliases"}:
            value = detail.get(field)
            if isinstance(value, list) and value:
                detail[field] = value[:-1] if len(value) > 1 else []
                return True
        elif field == "description":
            value = str(detail.get(field, ""))
            if len(value) > 120:
                detail[field] = _truncate_text(value, max(120, len(value) // 2))
                return True
    return False


def _reduce_candidate(candidate: dict[str, Any], maximum: int) -> dict[str, Any]:
    """Shrink only optional explanatory fields, retaining all identity/evidence."""
    candidate = json.loads(_canonical_json(candidate))
    while _candidate_size(candidate) > maximum and _compact_optional_candidate_field(candidate):
        pass
    if _candidate_size(candidate) > maximum:
        raise ValueError("CANDIDATE_CONTEXT_TOO_LARGE: required candidate identity and evidence exceed the configured limit")
    return candidate


def _reduce_expanded_detail(detail: dict[str, Any], maximum: int) -> dict[str, Any]:
    detail = json.loads(_canonical_json(detail))
    while _candidate_size(detail) > maximum and _compact_expanded_detail(detail):
        pass
    if _candidate_size(detail) > maximum:
        raise ValueError("CANDIDATE_CONTEXT_TOO_LARGE: expanded candidate detail exceeds the configured limit")
    return detail


def _candidate_reason(matched_fields: list[str], match_level: str) -> str:
    korean_field = {
        "title": "제목",
        "aliases": "별칭",
        "capabilities": "기능 설명",
        "systems": "연동 시스템",
        "tags": "태그",
        "category": "분류",
        "description": "설명",
        "use_cases": "활용 사례",
        "readme": "README",
        "limitations": "제약 사항",
    }
    fields = ", ".join(korean_field.get(field, field) for field in matched_fields[:3])
    if match_level == "strong":
        return f"{fields or '카탈로그'}에서 업무 핵심어가 강하게 일치합니다."
    if match_level == "moderate":
        return f"{fields or '카탈로그'}에서 관련 키워드가 일치합니다."
    if match_level == "weak":
        return f"{fields or '카탈로그'}에 약한 키워드 또는 문자 유사도가 있습니다."
    return "직접 일치는 없으며, 안정적인 참고 후보 순서로 제시합니다."


def _updated_sort_value(item: dict[str, Any]) -> float:
    value = str(item.get("updated_at", "") or "")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


class LocalCatalogRankerComponent(Component):
    display_name = "02 관련 기능 카탈로그 검색"
    description = "업무 설명과 로컬 카탈로그를 비교해 키워드 기반 후보 100개를 결정론적으로 정렬합니다. 실제 후보 선별은 03 LLM 노드가 수행합니다."
    icon = "ListFilter"
    name = "LocalCatalogRanker"

    inputs = [
        DataInput(name="request", display_name="업무 요청", required=True),
        DataInput(name="catalog_bundle", display_name="정규화 카탈로그", required=True),
        IntInput(
            name="top_n",
            display_name="상위 후보 수",
            value=_DEFAULT_TOP_N,
            required=True,
            info="키워드 기반으로 넓게 후보를 확보한 뒤 LLM이 실제 적용할 항목만 추립니다.",
        ),
        IntInput(
            name="max_candidate_chars",
            display_name="후보당 최대 문자 수",
            value=_DEFAULT_MAX_CANDIDATE_CHARS,
            advanced=True,
        ),
        IntInput(
            name="max_context_chars",
            display_name="전체 후보 context 최대 문자 수",
            value=_DEFAULT_MAX_CONTEXT_CHARS,
            advanced=True,
            info="내부 상세 문맥은 이 안전 상한 안에서 자동 축약됩니다. 실제 후보 선별은 03 LLM 노드가 수행합니다.",
        ),
    ]
    outputs = [Output(name="retrieval_result", display_name="카탈로그 검색 결과", method="rank_catalog")]

    def rank_catalog(self) -> Data:
        request = _require_dict(getattr(self, "request", None), "request")
        catalog = _require_dict(getattr(self, "catalog_bundle", None), "catalog_bundle")
        if request.get("schema_version") != "business-design-request/v2":
            raise ValueError("REQUEST_SCHEMA_INVALID: business-design-request/v2 is required")
        if catalog.get("schema_version") != "local-catalog-bundle/v2":
            raise ValueError("CATALOG_SCHEMA_INVALID: local-catalog-bundle/v2 is required")
        query_text = request.get("description_normalized")
        if not isinstance(query_text, str) or not query_text.strip():
            raise ValueError("REQUEST_DESCRIPTION_INVALID: description_normalized is required")
        request_sha = request.get("request_sha256")
        catalog_sha = ((catalog.get("source") or {}).get("file_sha256"))
        if not isinstance(request_sha, str) or not isinstance(catalog_sha, str):
            raise ValueError("REQUEST_OR_CATALOG_HASH_INVALID: deterministic input hashes are required")
        items = catalog.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("CATALOG_EMPTY: normalized catalog must contain at least one item")

        top_n = int(getattr(self, "top_n", _DEFAULT_TOP_N) or _DEFAULT_TOP_N)
        # Bounded rich README/port context for 03; deliberately not a Canvas
        # setting so it cannot be mistaken for a candidate-selection limit.
        expanded_detail_count = _DEFAULT_EXPANDED_DETAIL_COUNT
        max_candidate_chars = int(getattr(self, "max_candidate_chars", _DEFAULT_MAX_CANDIDATE_CHARS) or _DEFAULT_MAX_CANDIDATE_CHARS)
        max_context_chars = int(getattr(self, "max_context_chars", _DEFAULT_MAX_CONTEXT_CHARS) or _DEFAULT_MAX_CONTEXT_CHARS)
        if not (
            1 <= top_n <= 100
            and 500 <= max_candidate_chars <= 1_600
            and 4_000 <= max_context_chars <= 64_000
        ):
            raise ValueError("RETRIEVAL_LIMIT_INVALID: one or more retrieval limits are out of range")

        query_tokens = _tokens(query_text)
        query_token_set = set(query_tokens)
        query_norm = _normalise(query_text)
        query_phrases = {" ".join(query_tokens[index : index + 2]) for index in range(max(0, len(query_tokens) - 1))}
        query_grams = _ngram_set(query_text)

        prepared: list[dict[str, Any]] = []
        document_frequency: Counter[str] = Counter()
        field_average_lengths: dict[str, float] = {}
        field_lengths: dict[str, list[int]] = defaultdict(list)
        for index, raw_item in enumerate(items):
            if not isinstance(raw_item, dict):
                raise ValueError(f"CATALOG_ITEM_INVALID: items[{index}] must be an object")
            required = ("asset_id", "version", "asset_type", "title", "content_sha256", "catalog_url")
            if any(not isinstance(raw_item.get(field), str) or not raw_item.get(field) for field in required):
                raise ValueError(f"CATALOG_ITEM_INVALID: items[{index}] does not have a valid closed identity")
            fields = {field: _field_text(raw_item, field) for field in _FIELD_WEIGHTS}
            tokens_by_field = {field: _tokens(text) for field, text in fields.items()}
            all_tokens = set(token for tokens in tokens_by_field.values() for token in tokens)
            document_frequency.update(all_tokens)
            for field, tokens in tokens_by_field.items():
                field_lengths[field].append(len(tokens))
            combined_for_ngrams = raw_item.get("search_text") if isinstance(raw_item.get("search_text"), str) else "\n".join(fields.values())
            prepared.append({"item": raw_item, "fields": fields, "tokens": tokens_by_field, "ngrams": _ngram_set(combined_for_ngrams)})
        for field, lengths in field_lengths.items():
            field_average_lengths[field] = max(1.0, sum(lengths) / max(1, len(lengths)))

        exact_raw: dict[int, float] = {}
        bm25_raw: dict[int, float] = {}
        ngram_raw: dict[int, float] = {}
        matched_terms: dict[int, list[str]] = {}
        matched_fields: dict[int, list[str]] = {}
        total_docs = len(prepared)
        for index, entry in enumerate(prepared):
            fields = entry["fields"]
            tokens_by_field = entry["tokens"]
            title_alias_norms = [_normalise(fields["title"])] + [_normalise(alias) for alias in (entry["item"].get("aliases") or [])]
            exact = 3.0 if query_norm and query_norm in title_alias_norms else 0.0
            for field, text in fields.items():
                token_text = " ".join(tokens_by_field[field])
                if any(phrase and phrase in token_text for phrase in query_phrases):
                    exact = max(exact, 2.0 if field in {"title", "aliases"} else 1.0)
            if exact > 0:
                exact_raw[index] = exact

            token_score = 0.0
            fields_hit: list[str] = []
            found_terms: set[str] = set()
            for field, tokens in tokens_by_field.items():
                if not tokens:
                    continue
                counts = Counter(tokens)
                length = len(tokens)
                average_length = field_average_lengths[field]
                field_hit = False
                for term in query_token_set:
                    tf = counts.get(term, 0)
                    if not tf:
                        continue
                    df = document_frequency.get(term, 0)
                    idf = math.log(1.0 + (total_docs - df + 0.5) / (df + 0.5))
                    denominator = tf + _K1 * (1.0 - _B + _B * length / average_length)
                    token_score += _FIELD_WEIGHTS[field] * idf * (tf * (_K1 + 1.0) / denominator)
                    found_terms.add(term)
                    field_hit = True
                if field_hit:
                    fields_hit.append(field)
            if token_score > 0:
                bm25_raw[index] = token_score
            matched_terms[index] = sorted(found_terms)
            matched_fields[index] = fields_hit
            ngram_score = _jaccard(query_grams, entry["ngrams"])
            if ngram_score >= 0.05:
                ngram_raw[index] = ngram_score

        def lane_sort(scores: dict[int, float]) -> list[int]:
            return sorted(
                scores,
                key=lambda index: (
                    -scores[index],
                    str(prepared[index]["item"].get("title", "")),
                    str(prepared[index]["item"].get("asset_id", "")),
                    str(prepared[index]["item"].get("version", "")),
                ),
            )

        lane_orders = {"exact_phrase": lane_sort(exact_raw), "token_bm25": lane_sort(bm25_raw), "character_ngram": lane_sort(ngram_raw)}
        lane_ranks = {lane: {index: rank for rank, index in enumerate(order, start=1)} for lane, order in lane_orders.items()}
        scored: list[dict[str, Any]] = []
        for index, entry in enumerate(prepared):
            rrf_raw = sum(
                _LANE_WEIGHTS[lane] / (_RRF_K + lane_ranks[lane][index])
                for lane in _LANE_WEIGHTS
                if index in lane_ranks[lane]
            )
            score = _rounded(min(1.0, rrf_raw / (sum(_LANE_WEIGHTS.values()) / (_RRF_K + 1)))) if rrf_raw else 0.0
            if (exact_raw.get(index) == 3.0) or score >= 0.75:
                level = "strong"
            elif score >= 0.40:
                level = "moderate"
            elif score > 0:
                level = "weak"
            else:
                level = "none"
            scored.append(
                {
                    "index": index,
                    "item": entry["item"],
                    "score": score,
                    "match_level": level,
                    "matched_terms": matched_terms.get(index, []),
                    "matched_fields": matched_fields.get(index, []),
                    "lane_scores": {
                        "exact_phrase": _rounded(exact_raw.get(index, 0.0)),
                        "token_bm25": _rounded(bm25_raw.get(index, 0.0)),
                        "character_ngram": _rounded(ngram_raw.get(index, 0.0)),
                    },
                    "lane_ranks": {lane: lane_ranks[lane].get(index) for lane in _LANE_WEIGHTS},
                }
            )

        all_zero = all(record["score"] == 0 for record in scored)
        def fallback_popularity(item: dict[str, Any], key: str) -> int:
            value = item.get(key, 0)
            return value if type(value) is int and value >= 0 else 0

        if all_zero:
            scored.sort(
                key=lambda record: (
                    -fallback_popularity(record["item"], "stars_count"),
                    -fallback_popularity(record["item"], "downloads_count"),
                    -_updated_sort_value(record["item"]),
                    str(record["item"].get("title", "")),
                    str(record["item"].get("asset_id", "")),
                    str(record["item"].get("version", "")),
                ),
                reverse=False,
            )
        else:
            scored.sort(
                key=lambda record: (
                    -record["score"],
                    -len(record["matched_terms"]),
                    _TECHNICAL_STATUS_PRIORITY.get(str(record["item"].get("technical_contract_status", "unknown")), 4),
                    str(record["item"].get("title", "")),
                    str(record["item"].get("asset_id", "")),
                    str(record["item"].get("version", "")),
                )
            )

        candidates: list[dict[str, Any]] = []
        expanded_candidate_details: list[dict[str, Any]] = []
        hash_projection: list[dict[str, Any]] = []
        for rank, record in enumerate(scored[: min(top_n, len(scored))], start=1):
            item = record["item"]
            candidate = {
                "rank": rank,
                "asset_id": item["asset_id"],
                "version": item["version"],
                "asset_type": item["asset_type"],
                "title": _truncate_text(str(item["title"]), 120),
                "description": _truncate_text(str(item.get("description", "")), 120),
                "category": _truncate_text(str(item.get("category", "")), 48),
                "technical_contract_status": str(item.get("technical_contract_status", "unknown")),
                "score": record["score"],
                "match_level": record["match_level"],
                "matched_terms": _string_list(record["matched_terms"], 8, 48),
                "matched_fields": _string_list(record["matched_fields"], 5, 32),
            }
            candidates.append(_reduce_candidate(candidate, max_candidate_chars))
            hash_projection.append(
                {
                    "asset_id": item["asset_id"],
                    "version": item["version"],
                    "asset_type": item["asset_type"],
                    "content_sha256": item["content_sha256"],
                    "score": _rounded(float(record["score"])),
                }
            )
            if rank <= expanded_detail_count:
                detail = {
                    "rank": rank,
                    "asset_id": item["asset_id"],
                    "version": item["version"],
                    "description": _truncate_text(str(item.get("description", "")), 520),
                    "aliases": _string_list(item.get("aliases"), 8, 100),
                    "capabilities": _string_list(item.get("capabilities"), 8, 120),
                    "systems": _string_list(item.get("systems"), 6, 100),
                    "tags": _string_list(item.get("tags"), 8, 64),
                    "use_cases": _string_list(item.get("use_cases"), 6, 120),
                    "limitations": _string_list(item.get("limitations"), 4, 120),
                    "readme_excerpt": _truncate_text(str(item.get("readme", "")), 360),
                    "ports": _ports_summary(item.get("ports"), 420),
                    "retrieval_reason": _candidate_reason(record["matched_fields"], record["match_level"]),
                    "lane_scores": record["lane_scores"],
                    "lane_ranks": record["lane_ranks"],
                }
                expanded_candidate_details.append(_reduce_expanded_detail(detail, _MAX_EXPANDED_DETAIL_CHARS))

        # The raw retrieval result is intentionally bounded even when it contains
        # 100 identities.  The first-pass prompt independently builds a smaller
        # index-plus-detail representation from these two tiers.
        def context_size() -> int:
            return len(
                _canonical_json(
                    {
                        "candidates": candidates,
                        "expanded_candidate_details": expanded_candidate_details,
                    }
                )
            )

        while context_size() > max_context_chars:
            changed = False
            for detail in reversed(expanded_candidate_details):
                if _compact_expanded_detail(detail):
                    changed = True
            if not changed:
                for candidate in reversed(candidates):
                    if _compact_optional_candidate_field(candidate):
                        changed = True
            if not changed:
                raise ValueError("CANDIDATE_CONTEXT_TOO_LARGE: cannot retain all required candidate identities within the context limit")

        quality = "matched" if any(candidate["match_level"] in {"strong", "moderate"} for candidate in candidates) else (
            "weak_matches" if any(candidate["match_level"] == "weak" for candidate in candidates) else "no_direct_match"
        )
        result = {
            "schema_version": _SCHEMA_VERSION,
            "algorithm": _ALGORITHM,
            "request_sha256": request_sha,
            "catalog_file_sha256": catalog_sha,
            "top_n_requested": top_n,
            "top_n_returned": len(candidates),
            "expanded_detail_count_requested": expanded_detail_count,
            "expanded_detail_count_returned": len(expanded_candidate_details),
            "candidate_set_sha256": _sha256(hash_projection),
            "retrieval_quality": quality,
            "candidates": candidates,
            "expanded_candidate_details": expanded_candidate_details,
        }
        self.status = (
            f"카탈로그 {len(items):,}개 중 관련 후보 {len(candidates):,}개와 "
            f"내부 상세 문맥 {len(expanded_candidate_details):,}개를 준비했습니다."
        )
        return Data(data=result)
