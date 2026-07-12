from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from lfx.custom import Component
from lfx.io import BoolInput, DataInput, DropdownInput, IntInput, MessageTextInput, Output
from lfx.schema import Data


VALID_CLASSIFICATIONS = {"public", "internal", "confidential", "restricted"}
DEFAULT_PAGE_BREAK_MARKER = "[[PAGE_BREAK]]"


# The demo corpus is deliberately embedded so an imported flow can be executed
# before a file service, vector database, or model is configured.  It contains
# no credentials or personal data.
DEMO_DOCUMENTS: list[dict[str, Any]] = [
    {
        "document_id": "kb-001",
        "title": "RAG lifecycle separation",
        "version": "1.0",
        "page": 1,
        "source_locator": "page:1",
        "source_url": "",
        "classification": "internal",
        "allowed_roles": ["employee"],
        "allowed_groups": ["all-employees"],
        "tenant_id": "agent-ground",
        "visibility": "internal",
        "content": (
            "RAG는 문서 적재 lifecycle과 사용자 질문 lifecycle을 분리한다. "
            "문서 적재 lifecycle은 upload, parse, chunk, embedding, vector upsert를 담당한다. "
            "사용자 질문 lifecycle은 질문 접수, 권한 필터, 검색, 근거 검증, 답변과 citation 생성을 담당한다. "
            "두 lifecycle을 분리하면 재색인과 사용자 응답을 각각 배포하고 테스트하며 모니터링할 수 있다."
        ),
    },
    {
        "document_id": "kb-003",
        "title": "Standalone component output contract",
        "version": "1.0",
        "page": 2,
        "source_locator": "page:2",
        "source_url": "",
        "classification": "internal",
        "allowed_roles": ["employee"],
        "allowed_groups": ["all-employees"],
        "tenant_id": "agent-ground",
        "visibility": "internal",
        "content": (
            "사내 공통 Standalone component의 Data output은 success, errors, warnings를 기본으로 포함한다. "
            "대용량 결과는 전체 원문보다 row_count, columns, preview_rows, data_ref를 우선 전달한다. "
            "실패 시 예외 원문이나 비밀값을 그대로 노출하지 않고 downstream이 처리할 수 있는 안전한 오류 계약을 반환한다."
        ),
    },
    {
        "document_id": "kb-006",
        "title": "Vector ingest metadata contract",
        "version": "1.0",
        "page": 3,
        "source_locator": "page:3",
        "source_url": "",
        "classification": "internal",
        "allowed_roles": ["employee"],
        "allowed_groups": ["all-employees"],
        "tenant_id": "agent-ground",
        "visibility": "internal",
        "content": (
            "Vector store 적재 record에는 id, text 또는 page_content, metadata가 필요하다. "
            "metadata에는 document_id, source_file, version, page, chunk_index, source_locator, "
            "classification과 접근 제어 정보가 포함되어야 한다."
        ),
    },
    {
        "document_id": "security-runbook-001",
        "title": "Restricted security runbook",
        "version": "1.0",
        "page": 1,
        "source_locator": "page:1",
        "source_url": "",
        "classification": "restricted",
        "allowed_roles": ["security_admin"],
        "allowed_groups": ["security_operations"],
        "tenant_id": "agent-ground",
        "visibility": "restricted",
        "content": (
            "보안 사고 대응 runbook은 승인된 보안 운영 담당자만 조회한다. "
            "일반 사용자 질문에는 문서의 존재, 제목, 세부 절차를 공개하지 않는다."
        ),
    },
]


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except Exception:
        number = default
    return max(minimum, min(maximum, number))


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = re.split(r"[,;\n]", value)
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = [value]

    result: list[str] = []
    for item in items:
        text = str(item or "").strip().lower()
        if text and text not in result:
            result.append(text)
    return result


def _text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    for attr in ("text", "content", "message"):
        item = getattr(value, attr, None)
        if isinstance(item, str):
            return item
    return ""


def _payload_from_value(value: Any) -> Any:
    """Convert Langflow Data/Message, JSON text, dict, or list to a plain value."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value

    data = getattr(value, "data", None)
    if isinstance(data, (dict, list)):
        return data

    text = _text_value(value).strip()
    if not text:
        return None

    candidate = text
    if candidate.startswith("```") and candidate.endswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"\s*```$", "", candidate)
    try:
        return json.loads(candidate)
    except Exception:
        return {"content": text}


def _document_items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []

    for key in ("documents", "rows", "items"):
        items = payload.get(key)
        if isinstance(items, list):
            return items
        if isinstance(items, dict):
            return [items]

    nested = payload.get("data")
    if isinstance(nested, list):
        return nested
    if isinstance(nested, dict) and nested is not payload:
        nested_items = _document_items(nested)
        if nested_items:
            return nested_items

    if any(key in payload for key in ("content", "text", "page_content", "deleted", "active")):
        return [payload]
    return []


def _safe_text(value: Any, maximum: int) -> str:
    return str(value or "").strip()[:maximum]


def _page_number(value: Any) -> int | None:
    try:
        page = int(value)
    except Exception:
        return None
    return page if page > 0 else None


def _stable_document_id(item: dict[str, Any], content: str, position: int) -> str:
    supplied = _safe_text(
        item.get("document_id") or item.get("doc_id") or item.get("id"),
        200,
    )
    if supplied:
        return supplied

    identity = "|".join(
        [
            _safe_text(item.get("source_name") or item.get("file_name"), 300),
            _safe_text(item.get("title") or item.get("name"), 300),
            _safe_text(item.get("source_locator"), 500),
        ]
    ).strip("|")
    if not identity:
        identity = f"document-position:{position}|{content[:1000]}"
    return "doc-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def _content_from_item(item: dict[str, Any]) -> str:
    for key in ("content", "text", "page_content", "raw_content", "markdown", "extracted_text"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _expand_pages(item: dict[str, Any], page_break_marker: str) -> list[dict[str, Any]]:
    pages = item.get("pages")
    if isinstance(pages, list) and pages:
        expanded: list[dict[str, Any]] = []
        for index, page_item in enumerate(pages, start=1):
            copied = dict(item)
            copied.pop("pages", None)
            if isinstance(page_item, dict):
                copied.update(page_item)
            else:
                copied["content"] = str(page_item or "")
            copied.setdefault("page", index)
            expanded.append(copied)
        return expanded

    content = _content_from_item(item)
    marker = str(page_break_marker or "").strip()
    if marker and marker in content and _page_number(item.get("page")) is None:
        expanded = []
        for index, part in enumerate(content.split(marker), start=1):
            if not part.strip():
                continue
            copied = dict(item)
            copied["content"] = part.strip()
            copied["page"] = index
            copied["source_locator"] = f"page:{index}"
            expanded.append(copied)
        return expanded
    return [item]


def normalize_document_input(
    value: Any,
    *,
    use_demo_corpus: bool = True,
    default_source_name: str = "enterprise_demo_corpus",
    default_tenant_id: str = "agent-ground",
    default_classification: str = "internal",
    default_allowed_roles: Any = "employee",
    default_allowed_groups: Any = "all-employees",
    page_break_marker: str = DEFAULT_PAGE_BREAK_MARKER,
    max_documents: int = 100,
    max_chars_per_document: int = 200_000,
) -> dict[str, Any]:
    """Normalize document-like input into the public ingestion contract.

    The function is intentionally independent of Langflow classes so contract
    tests can call it directly.
    """
    max_documents = _clamp_int(max_documents, 100, 1, 2_000)
    max_chars_per_document = _clamp_int(max_chars_per_document, 200_000, 1_000, 2_000_000)
    classification_default = str(default_classification or "internal").strip().lower()
    if classification_default not in VALID_CLASSIFICATIONS:
        classification_default = "internal"

    payload = _payload_from_value(value)
    raw_items = _document_items(payload)
    demo_mode = False
    if not raw_items and _as_bool(use_demo_corpus, default=True):
        raw_items = [dict(item) for item in DEMO_DOCUMENTS]
        demo_mode = True

    errors: list[str] = []
    warnings: list[str] = []
    documents: list[dict[str, Any]] = []
    rejected_count = 0
    truncated_count = 0

    if not raw_items:
        errors.append("No document input was provided and the demo corpus is disabled.")

    if len(raw_items) > max_documents:
        raw_items = raw_items[:max_documents]
        warnings.append("Document count exceeded the configured limit; remaining items were not processed.")

    expanded_items: list[dict[str, Any]] = []
    for raw_item in raw_items:
        if isinstance(raw_item, str):
            raw_item = {"content": raw_item}
        if not isinstance(raw_item, dict):
            rejected_count += 1
            continue
        expanded_items.extend(_expand_pages(dict(raw_item), page_break_marker))

    if len(expanded_items) > max_documents:
        expanded_items = expanded_items[:max_documents]
        warnings.append("Expanded page count exceeded the configured document limit.")

    default_roles = _string_list(default_allowed_roles)
    default_groups = _string_list(default_allowed_groups)
    for position, item in enumerate(expanded_items, start=1):
        content = _content_from_item(item)
        deleted = _as_bool(item.get("deleted"), default=False)
        active = _as_bool(item.get("active"), default=not deleted)

        # Tombstones are allowed to omit content; active documents are not.
        if not content and not deleted and active:
            rejected_count += 1
            continue
        if len(content) > max_chars_per_document:
            content = content[:max_chars_per_document]
            truncated_count += 1

        document_id = _stable_document_id(item, content, position)
        page = _page_number(item.get("page") or item.get("page_number"))
        source_locator = _safe_text(item.get("source_locator"), 1_000)
        if not source_locator:
            source_locator = f"page:{page}" if page is not None else f"document:{document_id}"

        classification = str(item.get("classification") or classification_default).strip().lower()
        if classification not in VALID_CLASSIFICATIONS:
            classification = classification_default
            warnings.append("A document classification was invalid and was replaced by the configured default.")

        roles = _string_list(item.get("allowed_roles"))
        groups = _string_list(item.get("allowed_groups"))
        if classification != "public":
            if not roles:
                roles = list(default_roles)
            if not groups:
                groups = list(default_groups)

        source_name = _safe_text(
            item.get("source_name") or item.get("file_name") or default_source_name,
            300,
        )
        title = _safe_text(item.get("title") or item.get("name") or source_name or document_id, 500)
        version = _safe_text(item.get("version") or item.get("document_version") or "1", 100)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        documents.append(
            {
                "document_id": document_id,
                "title": title,
                "version": version,
                "content": content,
                "content_hash": content_hash,
                "page": page,
                "source_locator": source_locator,
                "source_url": _safe_text(item.get("source_url") or item.get("url"), 2_000),
                "source_name": source_name,
                "tenant_id": _safe_text(item.get("tenant_id") or default_tenant_id, 200).lower(),
                "visibility": _safe_text(item.get("visibility") or classification, 100).lower(),
                "classification": classification,
                "allowed_roles": roles,
                "allowed_groups": groups,
                "active": bool(active and not deleted),
                "deleted": deleted,
            }
        )

    if rejected_count:
        warnings.append("One or more document items were rejected because their shape or content was invalid.")
    if truncated_count:
        warnings.append("One or more documents exceeded the character limit and were truncated.")
    if not documents and not errors:
        errors.append("No valid documents remained after normalization.")

    success = bool(documents) and not errors
    return {
        "success": success,
        "stage": "document_input_normalizer",
        "documents": documents,
        "document_count": len(documents),
        "demo_mode": demo_mode,
        "errors": errors,
        "warnings": warnings,
        "trace": [
            {
                "stage": "document_input_normalizer",
                "status": "success" if success else "failed",
                "document_count": len(documents),
                "rejected_count": rejected_count,
                "truncated_count": truncated_count,
                "demo_mode": demo_mode,
            }
        ],
    }


class DocumentInputNormalizer(Component):
    display_name = "00 Document Input Normalizer"
    description = "Normalize Data, Message, text, or JSON into documents with citation and ACL metadata."
    icon = "Files"
    name = "DocumentInputNormalizer"

    inputs = [
        DataInput(
            name="document_input",
            display_name="Document Input",
            info="Optional Data, Message, JSON, or text. Empty input uses the embedded demo corpus when enabled.",
            input_types=["Data", "Message", "Text", "JSON"],
            required=False,
        ),
        BoolInput(
            name="use_demo_corpus",
            display_name="Use Demo Corpus When Empty",
            value=True,
        ),
        MessageTextInput(
            name="default_source_name",
            display_name="Default Source Name",
            value="enterprise_demo_corpus",
            advanced=True,
        ),
        MessageTextInput(
            name="default_tenant_id",
            display_name="Default Tenant ID",
            value="agent-ground",
            advanced=True,
        ),
        DropdownInput(
            name="default_classification",
            display_name="Default Classification",
            options=["public", "internal", "confidential", "restricted"],
            value="internal",
            advanced=True,
        ),
        MessageTextInput(
            name="default_allowed_roles",
            display_name="Default Allowed Roles",
            info="Comma-separated trusted role names. This is document metadata, not end-user identity.",
            value="employee",
            advanced=True,
        ),
        MessageTextInput(
            name="default_allowed_groups",
            display_name="Default Allowed Groups",
            value="all-employees",
            advanced=True,
        ),
        MessageTextInput(
            name="page_break_marker",
            display_name="Page Break Marker",
            value=DEFAULT_PAGE_BREAK_MARKER,
            advanced=True,
        ),
        IntInput(
            name="max_documents",
            display_name="Max Documents or Pages",
            value=100,
            advanced=True,
        ),
        IntInput(
            name="max_chars_per_document",
            display_name="Max Characters per Document",
            value=200000,
            advanced=True,
        ),
    ]

    outputs = [Output(name="documents", display_name="Documents", method="build_documents", types=["Data"])]

    def build_documents(self) -> Data:
        result = normalize_document_input(
            getattr(self, "document_input", None),
            use_demo_corpus=getattr(self, "use_demo_corpus", True),
            default_source_name=getattr(self, "default_source_name", "enterprise_demo_corpus"),
            default_tenant_id=getattr(self, "default_tenant_id", "agent-ground"),
            default_classification=getattr(self, "default_classification", "internal"),
            default_allowed_roles=getattr(self, "default_allowed_roles", "employee"),
            default_allowed_groups=getattr(self, "default_allowed_groups", "all-employees"),
            page_break_marker=getattr(self, "page_break_marker", DEFAULT_PAGE_BREAK_MARKER),
            max_documents=getattr(self, "max_documents", 100),
            max_chars_per_document=getattr(self, "max_chars_per_document", 200000),
        )
        self.status = {
            "success": result["success"],
            "document_count": result["document_count"],
            "demo_mode": result["demo_mode"],
            "warning_count": len(result["warnings"]),
            "error_count": len(result["errors"]),
        }
        return Data(data=result)
