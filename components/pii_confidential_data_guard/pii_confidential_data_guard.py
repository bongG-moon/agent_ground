from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable

from lfx.custom import Component
from lfx.io import BoolInput, DataInput, DropdownInput, Output
from lfx.schema import Data


CLASSIFICATION_ORDER = {
    "public": 0,
    "internal": 1,
    "confidential": 2,
    "restricted": 3,
}


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _payload_from_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {"documents": value}

    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        return {"documents": data}

    text = getattr(value, "text", None) or getattr(value, "content", None)
    if isinstance(value, str):
        text = value
    if isinstance(text, str) and text.strip():
        try:
            parsed = json.loads(text)
        except Exception:
            return {}
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"documents": parsed}
    return {}


def _pattern_specs(
    *,
    detect_emails: bool,
    detect_phone_numbers: bool,
    detect_national_ids: bool,
    detect_employee_ids: bool,
    detect_secrets: bool,
) -> list[tuple[str, re.Pattern[str], str | Callable[[re.Match[str]], str]]]:
    specs: list[tuple[str, re.Pattern[str], str | Callable[[re.Match[str]], str]]] = []
    if detect_secrets:
        specs.append(
            (
                "secret",
                re.compile(
                    r"(?i)\b(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|passwd|secret)\b"
                    r"\s*[:=]\s*([^\s,;]{4,})"
                ),
                lambda match: f"{match.group(1)}=[REDACTED:SECRET]",
            )
        )
    if detect_national_ids:
        specs.append(
            (
                "national_id",
                re.compile(r"(?<!\d)\d{6}-?[1-4]\d{6}(?!\d)"),
                "[REDACTED:NATIONAL_ID]",
            )
        )
    if detect_emails:
        specs.append(
            (
                "email",
                re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+(?![\w.-])"),
                "[REDACTED:EMAIL]",
            )
        )
    if detect_phone_numbers:
        specs.append(
            (
                "phone",
                re.compile(r"(?<!\d)(?:(?:\+?82[- ]?)?0?1[016789]|0\d{1,2})[- ]?\d{3,4}[- ]?\d{4}(?!\d)"),
                "[REDACTED:PHONE]",
            )
        )
    if detect_employee_ids:
        specs.append(
            (
                "employee_id",
                re.compile(r"(?i)(?<![\w])(?:(?:EMP|EID)[- :#]+[A-Z0-9]{4,16}|사번[- :#]*[A-Z0-9]{4,16})(?![\w])"),
                "[REDACTED:EMPLOYEE_ID]",
            )
        )
    return specs


def _scan_and_redact(
    text: Any,
    specs: list[tuple[str, re.Pattern[str], str | Callable[[re.Match[str]], str]]],
) -> tuple[str, dict[str, int]]:
    safe_text = str(text or "")
    counts: dict[str, int] = {}
    for name, pattern, replacement in specs:
        matches = list(pattern.finditer(safe_text))
        if not matches:
            continue
        counts[name] = counts.get(name, 0) + len(matches)
        safe_text = pattern.sub(replacement, safe_text)
    return safe_text, counts


def _merge_counts(target: dict[str, int], incoming: dict[str, int]) -> None:
    for name, count in incoming.items():
        target[name] = target.get(name, 0) + int(count)


def _safe_document_id(
    value: Any,
    specs: list[tuple[str, re.Pattern[str], str | Callable[[re.Match[str]], str]]],
) -> tuple[str, dict[str, int]]:
    original = str(value or "").strip()
    redacted, counts = _scan_and_redact(original, specs)
    if counts:
        # A redaction token is not a useful unique ID.  Hash the original value
        # and expose only the non-reversible short identifier.
        return "protected-doc-" + hashlib.sha256(original.encode("utf-8")).hexdigest()[:20], counts
    return redacted, counts


def _upgrade_classification(value: Any) -> str:
    classification = str(value or "internal").strip().lower()
    if classification not in CLASSIFICATION_ORDER:
        classification = "internal"
    if CLASSIFICATION_ORDER[classification] < CLASSIFICATION_ORDER["confidential"]:
        return "confidential"
    return classification


def guard_documents(
    value: Any,
    *,
    guard_mode: str = "redact",
    detect_emails: bool = True,
    detect_phone_numbers: bool = True,
    detect_national_ids: bool = True,
    detect_employee_ids: bool = True,
    detect_secrets: bool = True,
    upgrade_classification: bool = True,
) -> dict[str, Any]:
    """Redact or block sensitive patterns without returning their raw values."""
    payload = _payload_from_value(value)
    documents_value = payload.get("documents")
    documents = documents_value if isinstance(documents_value, list) else []

    selected_mode = str(guard_mode or "redact").strip().lower()
    warnings: list[str] = []
    errors: list[str] = []
    if selected_mode not in {"redact", "block"}:
        selected_mode = "redact"
        warnings.append("Guard mode was invalid and was changed to redact.")

    specs = _pattern_specs(
        detect_emails=_as_bool(detect_emails, True),
        detect_phone_numbers=_as_bool(detect_phone_numbers, True),
        detect_national_ids=_as_bool(detect_national_ids, True),
        detect_employee_ids=_as_bool(detect_employee_ids, True),
        detect_secrets=_as_bool(detect_secrets, True),
    )

    safe_documents: list[dict[str, Any]] = []
    blocked_document_ids: list[str] = []
    match_counts: dict[str, int] = {}
    affected_document_count = 0
    invalid_document_count = 0

    for raw_document in documents:
        if not isinstance(raw_document, dict):
            invalid_document_count += 1
            continue

        document = dict(raw_document)
        safe_id, id_counts = _safe_document_id(document.get("document_id"), specs)
        document_counts: dict[str, int] = {}
        _merge_counts(document_counts, id_counts)

        safe_string_fields: dict[str, str] = {}
        for field in (
            "title",
            "content",
            "source_locator",
            "source_url",
            "source_name",
            "version",
            "tenant_id",
            "visibility",
        ):
            redacted, counts = _scan_and_redact(document.get(field), specs)
            safe_string_fields[field] = redacted
            _merge_counts(document_counts, counts)

        safe_roles: list[str] = []
        roles = document.get("allowed_roles") if isinstance(document.get("allowed_roles"), list) else []
        for role in roles:
            redacted, counts = _scan_and_redact(role, specs)
            _merge_counts(document_counts, counts)
            if redacted.strip() and redacted.strip().lower() not in safe_roles:
                safe_roles.append(redacted.strip().lower())

        safe_groups: list[str] = []
        groups = document.get("allowed_groups") if isinstance(document.get("allowed_groups"), list) else []
        for group in groups:
            redacted, counts = _scan_and_redact(group, specs)
            _merge_counts(document_counts, counts)
            if redacted.strip() and redacted.strip().lower() not in safe_groups:
                safe_groups.append(redacted.strip().lower())

        if document_counts:
            affected_document_count += 1
            _merge_counts(match_counts, document_counts)
            if selected_mode == "block":
                blocked_document_ids.append(safe_id)
                continue

        # Rebuild from an allowlist instead of passing arbitrary upstream keys.
        # Otherwise an unknown metadata field could carry the very raw value
        # that this guard removed from content/title.
        document = {
            "document_id": safe_id,
            "title": safe_string_fields["title"],
            "version": safe_string_fields["version"] or "1",
            "content": safe_string_fields["content"],
            "content_hash": hashlib.sha256(safe_string_fields["content"].encode("utf-8")).hexdigest(),
            "page": raw_document.get("page"),
            "source_locator": safe_string_fields["source_locator"],
            "source_url": safe_string_fields["source_url"],
            "source_name": safe_string_fields["source_name"],
            "tenant_id": safe_string_fields["tenant_id"].strip().lower(),
            "visibility": safe_string_fields["visibility"].strip().lower(),
            "classification": str(raw_document.get("classification") or "internal").strip().lower(),
            "allowed_roles": safe_roles,
            "allowed_groups": safe_groups,
            "active": _as_bool(raw_document.get("active"), True),
            "deleted": _as_bool(raw_document.get("deleted"), False),
        }
        if document_counts and _as_bool(upgrade_classification, True):
            document["classification"] = _upgrade_classification(document.get("classification"))
        document["security_scan"] = {
            "guard_mode": selected_mode,
            "sensitive_pattern_count": sum(document_counts.values()),
            "pattern_types": sorted(document_counts),
        }
        safe_documents.append(document)

    if not documents:
        errors.append("No documents were available for the confidentiality guard.")
    if invalid_document_count:
        warnings.append("One or more invalid document items were skipped by the confidentiality guard.")
    if affected_document_count:
        action = "blocked" if selected_mode == "block" else "redacted"
        warnings.append(f"Sensitive patterns were detected and {action} according to the configured policy.")
    if documents and not safe_documents:
        errors.append("No documents remained after the confidentiality policy was applied.")

    success = bool(safe_documents) and not errors
    return {
        "success": success,
        "stage": "pii_confidential_data_guard",
        "documents": safe_documents,
        "document_count": len(safe_documents),
        "guard_mode": selected_mode,
        "affected_document_count": affected_document_count,
        "blocked_document_count": len(blocked_document_ids),
        "blocked_document_ids": blocked_document_ids,
        "match_counts": match_counts,
        "errors": errors,
        "warnings": warnings,
        "trace": [
            {
                "stage": "pii_confidential_data_guard",
                "status": "success" if success else "failed",
                "guard_mode": selected_mode,
                "input_document_count": len(documents),
                "safe_document_count": len(safe_documents),
                "affected_document_count": affected_document_count,
                "blocked_document_count": len(blocked_document_ids),
                "sensitive_pattern_count": sum(match_counts.values()),
            }
        ],
    }


class PiiConfidentialDataGuard(Component):
    display_name = "01 PII & Confidential Data Guard"
    description = "Redact or block common sensitive patterns before documents enter the retrieval index."
    icon = "ShieldCheck"
    name = "PiiConfidentialDataGuard"

    inputs = [
        DataInput(
            name="documents",
            display_name="Documents",
            input_types=["Data", "JSON"],
            required=True,
        ),
        DropdownInput(
            name="guard_mode",
            display_name="Guard Mode",
            options=["redact", "block"],
            value="redact",
        ),
        BoolInput(name="detect_emails", display_name="Detect Email Addresses", value=True, advanced=True),
        BoolInput(name="detect_phone_numbers", display_name="Detect Phone Numbers", value=True, advanced=True),
        BoolInput(name="detect_national_ids", display_name="Detect National IDs", value=True, advanced=True),
        BoolInput(name="detect_employee_ids", display_name="Detect Employee IDs", value=True, advanced=True),
        BoolInput(name="detect_secrets", display_name="Detect Secret-like Values", value=True, advanced=True),
        BoolInput(
            name="upgrade_classification",
            display_name="Upgrade Classification When Detected",
            value=True,
            advanced=True,
        ),
    ]

    outputs = [
        Output(
            name="safe_documents",
            display_name="Safe Documents",
            method="build_safe_documents",
            types=["Data"],
        )
    ]

    def build_safe_documents(self) -> Data:
        result = guard_documents(
            getattr(self, "documents", None),
            guard_mode=getattr(self, "guard_mode", "redact"),
            detect_emails=getattr(self, "detect_emails", True),
            detect_phone_numbers=getattr(self, "detect_phone_numbers", True),
            detect_national_ids=getattr(self, "detect_national_ids", True),
            detect_employee_ids=getattr(self, "detect_employee_ids", True),
            detect_secrets=getattr(self, "detect_secrets", True),
            upgrade_classification=getattr(self, "upgrade_classification", True),
        )
        # Only aggregate counts are displayed. Raw matched values never enter
        # self.status, errors, warnings, or trace.
        self.status = {
            "success": result["success"],
            "safe_document_count": result["document_count"],
            "affected_document_count": result["affected_document_count"],
            "blocked_document_count": result["blocked_document_count"],
            "warning_count": len(result["warnings"]),
            "error_count": len(result["errors"]),
        }
        return Data(data=result)
