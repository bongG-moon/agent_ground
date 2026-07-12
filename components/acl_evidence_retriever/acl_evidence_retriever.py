from __future__ import annotations

"""Permission-aware lexical retriever for the runnable enterprise RAG example.

ACL checks happen before injection inspection and before relevance scoring.  A
rejected record is discarded immediately; its identifier, content, title, and
even the number of rejected records are never placed in the output or status.
"""

import json
import math
import re
import unicodedata
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, Output
from lfx.schema.data import Data


CLASSIFICATION_LEVELS = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
INJECTION_PATTERNS = (
    r"ignore\s+(all\s+)?previous",
    r"ignore\s+(the\s+)?system",
    r"reveal\s+(the\s+)?(system|prompt|secret)",
    r"system\s*prompt",
    r"developer\s*message",
    r"지시.{0,12}무시",
    r"명령.{0,12}무시",
    r"시스템\s*프롬프트",
    r"비밀.{0,12}(공개|출력|노출)",
    r"<\s*(script|iframe|object)\b",
    r"\[\s*(system|developer)\s*\]",
)


def _payload(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return data
    text = _text(value)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    return [value]


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    for attr in ("text", "content"):
        candidate = getattr(value, attr, None)
        if isinstance(candidate, str):
            return candidate.strip()
    return str(value).strip()


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y", "active"}:
        return True
    if text in {"0", "false", "no", "n", "inactive", "deleted"}:
        return False
    return default


def _strings(value: Any, limit: int = 64) -> list[str]:
    if isinstance(value, str):
        values = re.split(r"[,;\n]", value)
    else:
        values = _list(value)
    result: list[str] = []
    for item in values:
        text = str(item or "").strip().lower()
        if text and text not in result:
            result.append(text[:128])
        if len(result) >= limit:
            break
    return result


def _clean_text(value: Any, limit: int) -> str:
    text = unicodedata.normalize("NFKC", _text(value))
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
    text = re.sub(r"[\u200b-\u200f\u2060\ufeff]", "", text)
    return text[:limit]


def _classification(value: Any) -> str:
    text = str(value or "internal").strip().lower()
    return text if text in CLASSIFICATION_LEVELS else "restricted"


def _records(index_value: Any) -> list[dict[str, Any]]:
    payload = _payload(index_value)
    nested = _dict(payload.get("document_index"))
    if nested:
        payload = nested
    raw_items: list[Any] = []
    for key in ("chunks", "documents", "records", "items", "ingest_data"):
        if isinstance(payload.get(key), list):
            raw_items = payload[key]
            break
    if not raw_items and any(key in payload for key in ("text", "content", "page_content")):
        raw_items = [payload]

    expanded: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        child_chunks = raw.get("chunks")
        if isinstance(child_chunks, list):
            parent = {key: value for key, value in raw.items() if key != "chunks"}
            for child in child_chunks:
                if isinstance(child, dict):
                    expanded.append({**parent, **child})
        else:
            expanded.append(raw)
    return expanded


def _merged_record(record: dict[str, Any]) -> dict[str, Any]:
    metadata = _dict(record.get("metadata"))
    # Explicit record fields win over parser metadata.
    return {**metadata, **record, "metadata": metadata}


def _is_authorized(record: dict[str, Any], actor: dict[str, Any]) -> bool:
    item = _merged_record(record)
    metadata = _dict(item.get("metadata"))
    acl = _dict(item.get("acl")) or _dict(metadata.get("acl"))

    # ACL metadata is mandatory.  Top-level compatibility fields are accepted
    # only when they make an explicit ACL, never as an implicit public default.
    acl_keys = {
        "tenant_id", "visibility", "classification", "security_classification",
        "allowed_users", "allowed_roles", "allowed_groups",
    }
    explicit_acl = bool(acl) or any(key in item for key in acl_keys) or any(key in metadata for key in acl_keys)
    if not explicit_acl:
        return False

    def field(name: str, fallback: Any = None) -> Any:
        if name in acl:
            return acl.get(name)
        if name in item:
            return item.get(name)
        return metadata.get(name, fallback)

    if not _bool(item.get("active", metadata.get("active", True)), default=True):
        return False
    if str(item.get("status") or metadata.get("status") or "").strip().lower() in {"deleted", "inactive", "revoked"}:
        return False

    actor_tenant = str(actor.get("tenant_id") or "").strip().lower()
    document_tenant = str(field("tenant_id") or "").strip().lower()
    if not actor_tenant or (document_tenant and document_tenant not in {actor_tenant, "*"}):
        return False

    classification = _classification(field("classification", field("security_classification", "restricted")))
    try:
        actor_level = int(actor.get("clearance_level", CLASSIFICATION_LEVELS.get(actor.get("clearance"), -1)))
    except Exception:
        actor_level = -1
    if CLASSIFICATION_LEVELS[classification] > actor_level:
        return False

    visibility = str(field("visibility") or "").strip().lower()
    allowed_users = set(_strings(field("allowed_users")))
    allowed_roles = set(_strings(field("allowed_roles")))
    allowed_groups = set(_strings(field("allowed_groups")))
    actor_user = str(actor.get("user_id") or "").strip().lower()
    actor_roles = set(_strings(actor.get("roles")))
    actor_groups = set(_strings(actor.get("groups")))

    principal_rules_present = bool(allowed_users or allowed_roles or allowed_groups)
    principal_match = (
        (actor_user in allowed_users if allowed_users else False)
        or bool(actor_roles & allowed_roles)
        or bool(actor_groups & allowed_groups)
    )
    if principal_rules_present:
        return principal_match
    # A legacy/single-tenant row without tenant_id is accepted only when an
    # explicit user/role/group ACL above matched.  Visibility alone is not
    # sufficient when tenant scope is absent.
    if not document_tenant:
        return False
    return visibility in {"public", "internal", "company"}


def _has_injection_signal(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text).lower()
    return any(re.search(pattern, normalized, flags=re.IGNORECASE | re.DOTALL) for pattern in INJECTION_PATTERNS)


def _stem_token(token: str) -> str:
    # Korean particles often attach directly to English/Korean terms
    # (``RAG를``, ``flow와``, ``문서는``).  Removing only well-known particles
    # improves exact lexical recall without turning this security component
    # into a broad fuzzy matcher.
    particles = (
        "으로", "에서", "에게", "까지", "부터", "처럼", "보다", "하고", "이며", "이고",
        "에는", "에서도", "은", "는", "이", "가", "을", "를", "와", "과", "로", "에", "의", "도", "만",
    )
    for suffix in particles:
        if token.endswith(suffix) and len(token) - len(suffix) >= 2:
            return token[: -len(suffix)]
    return token


def _tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).lower()
    fillers = {
        "그리고", "대한", "관련", "무엇", "어떻게", "어떤", "알려줘", "해주세요", "설명", "질문",
        "경우", "내용", "what", "how", "why", "about", "please",
    }
    result: list[str] = []
    for raw in re.findall(r"[a-z0-9가-힣_]{2,}", normalized):
        token = _stem_token(raw)
        if token and token not in fillers:
            result.append(token)
    return result


def _score(question: str, content: str, title: str) -> float:
    query_tokens = set(_tokens(question))
    if not query_tokens:
        return 0.0
    content_tokens = set(_tokens(content))
    title_tokens = set(_tokens(title))
    overlap = len(query_tokens & content_tokens) / max(len(query_tokens), 1)
    title_overlap = len(query_tokens & title_tokens) / max(len(query_tokens), 1)
    phrase = 1.0 if unicodedata.normalize("NFKC", question).lower() in unicodedata.normalize("NFKC", content).lower() else 0.0
    density = len(query_tokens & content_tokens) / max(math.sqrt(len(content_tokens) or 1), 1.0)
    return round(min(1.0, overlap * 0.68 + title_overlap * 0.20 + phrase * 0.08 + min(density, 1.0) * 0.04), 6)


def retrieve_authorized(request_value: Any, index_value: Any) -> dict[str, Any]:
    request = _payload(request_value)
    question = _clean_text(request.get("question"), 8000)
    actor = _dict(request.get("actor"))
    request_ready = bool(
        request.get("success")
        and _dict(request.get("security")).get("authorized_for_retrieval")
        and actor.get("identity_verified")
    )

    ranked: list[dict[str, Any]] = []
    injection_detected = False
    if request_ready and question:
        for raw_record in _records(index_value):
            # Security boundary: no text, ID, title, or score is read into an
            # output object until this record passes ACL.
            if not _is_authorized(raw_record, actor):
                continue
            item = _merged_record(raw_record)
            metadata = _dict(item.get("metadata"))
            document_id = _clean_text(
                item.get("document_id") or item.get("doc_id") or metadata.get("document_id"),
                160,
            )
            page = item.get("page", item.get("page_number", metadata.get("page", metadata.get("page_number"))))
            locator = _clean_text(
                item.get("source_locator")
                or metadata.get("source_locator")
                or (f"page:{page}" if page not in (None, "") else ""),
                240,
            )
            # Citation-ready identity is mandatory.  External/malformed rows
            # cannot become evidence merely because their text is relevant.
            if not document_id or (page in (None, "") and not locator):
                continue
            content = _clean_text(
                item.get("text") or item.get("content") or item.get("page_content"),
                12000,
            )
            if not content:
                continue
            title = _clean_text(
                item.get("title") or item.get("document_title") or item.get("source_file") or "사내 문서",
                240,
            )
            score = _score(question, content, title)
            if score <= 0.0:
                continue
            # Injection signals in irrelevant authorized documents must not
            # block an unrelated question.  Relevance is computed only after
            # ACL, then a relevant suspicious record is excluded here.
            if _has_injection_signal(content):
                injection_detected = True
                continue
            ranked.append(
                {
                    "record": item,
                    "content": content,
                    "title": title,
                    "score": score,
                    "document_id": document_id,
                    "page": page,
                    "locator": locator,
                }
            )

    ranked.sort(key=lambda row: (-row["score"], row["title"].lower()))
    evidence: list[dict[str, Any]] = []
    for position, row in enumerate(ranked[:6], start=1):
        item = row["record"]
        metadata = _dict(item.get("metadata"))
        page = row["page"]
        version = _clean_text(item.get("version") or metadata.get("version"), 80)
        locator = row["locator"]
        evidence.append(
            {
                "evidence_id": f"E{position}",
                "content": row["content"][:6000],
                "score": row["score"],
                "source": {
                    "document_id": row["document_id"],
                    "title": row["title"],
                    "version": version,
                    "page": page if isinstance(page, (int, float, str)) else "",
                    "locator": locator,
                    "classification": _classification(
                        item.get("classification") or metadata.get("classification") or "internal"
                    ),
                },
            }
        )

    return {
        "contract": "agent_ground.enterprise_rag.retrieval.v1",
        "success": bool(request_ready and question),
        "request_id": str(request.get("request_id") or "")[:96],
        "question": question,
        "retrieval_status": "evidence_found" if evidence else "no_evidence",
        "evidence": evidence,
        "security": {
            "acl_applied_before_scoring": True,
            "missing_acl_policy": "deny",
            "injection_signal_detected": injection_detected,
            "injection_records_excluded": injection_detected,
            "denied_information_exposed": False,
        },
        "warnings": ["Potential document instructions were excluded."] if injection_detected else [],
        "errors": [] if request_ready else ["Authorized evidence is unavailable."],
    }


class AclEvidenceRetriever(Component):
    display_name = "ACL Evidence Retriever"
    description = "문서 ACL을 먼저 적용한 뒤 허용된 문서만 점수화하며, 의심스러운 문서 지시는 검색 근거에서 제외합니다."
    icon = "ShieldSearch"
    name = "AclEvidenceRetriever"

    inputs = [
        DataInput(name="request", display_name="Request", input_types=["Data", "JSON"], required=True),
        DataInput(name="document_index", display_name="Document Index", input_types=["Data", "JSON"], required=True),
    ]
    outputs = [Output(name="retrieval", display_name="Retrieval", method="build_retrieval", types=["Data"])]

    def build_retrieval(self) -> Data:
        result = retrieve_authorized(getattr(self, "request", None), getattr(self, "document_index", None))
        self.status = "authorized evidence ready" if result["evidence"] else "no authorized evidence"
        return Data(data=result)
