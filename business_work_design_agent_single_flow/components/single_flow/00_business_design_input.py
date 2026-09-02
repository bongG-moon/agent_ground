from __future__ import annotations

"""Create the safe, deterministic request envelope for the single business-design flow.

This is intentionally a standalone Langflow component: it imports only Python's
standard library and Langflow/LFX public APIs.  In particular, it never persists
the submitted text and never emits a value that was recognised as a credential.
"""

import hashlib
import json
import re
import unicodedata
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, DropdownInput, IntInput, MultilineInput, Output
from lfx.schema import Data


_SCHEMA_VERSION = "business-design-request/v2"
_REDACTED = "[REDACTED]"
_MAX_DESCRIPTION_CHARS = 50_000
_MIN_DESCRIPTION_CHARS = 20
_DEFAULT_MODEL_CHARS = 16_000
_MAX_MODEL_CHARS = 16_000
_EXCERPT_MARKER = "\n\n[... 모델 전달용으로 일부 생략됨 ...]\n\n"

# These patterns deliberately operate on a bounded string and replace only the
# secret material, not an entire user paragraph.  Their matched values are never
# returned in the output, exception text, status, or trace fields.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "credential",
        re.compile(
            r"(?i)(\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|passwd|"
            r"authorization|cookie|private[_-]?key|secret)\b\s*[:=]\s*)(?:[\"']?)[^\s,;\]\)}]{4,}",
        ),
    ),
    ("bearer_token", re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}")),
    ("jwt", re.compile(r"\b[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b")),
    ("provider_key", re.compile(r"\b(?:sk|AIza)[-_A-Za-z0-9]{16,}\b")),
    (
        "credential_url",
        re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s:]{1,128}:[^/@\s]{1,128}@"),
    ),
    (
        "private_key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.S),
    ),
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _remove_unsafe_controls(value: str) -> tuple[str, int]:
    kept: list[str] = []
    removed = 0
    for char in value:
        if char in ("\n", "\r", "\t"):
            kept.append(char)
            continue
        if ord(char) == 0 or unicodedata.category(char) == "Cc":
            removed += 1
            continue
        kept.append(char)
    return "".join(kept), removed


def _redact(value: str) -> tuple[str, list[dict[str, str]], int]:
    kinds: list[str] = []
    count = 0
    redacted = value

    for kind, pattern in _SECRET_PATTERNS:
        def replace(match: re.Match[str], *, _kind: str = kind) -> str:
            nonlocal count
            count += 1
            kinds.append(_kind)
            # Patterns with a safe prefix retain it (for example, "api_key=").
            if _kind in {"credential", "bearer_token", "credential_url"}:
                return match.group(1) + _REDACTED
            return _REDACTED

        redacted = pattern.sub(replace, redacted)

    # The public contract does not leak match positions or original values.
    unique = [{"kind": kind, "replacement": _REDACTED} for kind in sorted(set(kinds))]
    return redacted, unique, count


def _excerpt_at_boundaries(value: str, limit: int) -> str:
    """Return a deterministic head/tail excerpt, preferring paragraph boundaries."""
    if len(value) <= limit:
        return value
    if limit < len(_EXCERPT_MARKER) + 20:
        raise ValueError("max_model_description_chars must be at least 80")

    usable = limit - len(_EXCERPT_MARKER)
    desired_head = int(usable * 0.60)
    desired_tail = usable - desired_head

    def left_boundary(position: int) -> int:
        start = max(0, position - 512)
        candidate = value.rfind("\n\n", start, position)
        return candidate + 2 if candidate >= start else position

    def right_boundary(position: int) -> int:
        end = min(len(value), position + 512)
        candidate = value.find("\n\n", position, end)
        return candidate if candidate >= 0 else position

    head_end = left_boundary(desired_head)
    tail_start = right_boundary(len(value) - desired_tail)
    # Do not let friendly boundary selection make the result exceed its limit.
    if tail_start <= head_end or head_end + len(_EXCERPT_MARKER) + (len(value) - tail_start) > limit:
        head_end = desired_head
        tail_start = len(value) - desired_tail
    return value[:head_end] + _EXCERPT_MARKER + value[tail_start:]


def _normalise_for_search(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


class BusinessDesignInputComponent(Component):
    display_name = "00 업무 설명 입력"
    description = "업무 설명을 안전하게 정리해 단일 설계 Flow의 요청으로 만듭니다."
    icon = "ClipboardPenLine"
    name = "BusinessDesignInput"

    inputs = [
        MultilineInput(
            name="description",
            display_name="업무 설명 원문",
            info="현재 업무의 절차, 분기, 입력·결과물, 사용하는 시스템을 가능한 한 구체적으로 작성합니다.",
            required=True,
        ),
        MultilineInput(
            name="additional_instructions",
            display_name="추가 설계 요청",
            info="선호하는 방향이나 반드시 유지할 조건이 있으면 작성합니다.",
            value="",
        ),
        MultilineInput(
            name="final_refinement_instructions",
            display_name="최종 설계 보완 지시",
            info=(
                "초안 설계 뒤 최종 보완 단계에서 특히 강조하거나 보충할 내용을 작성합니다. "
                "첫 번째 설계 모델 호출에는 전달하지 않습니다."
            ),
            value="",
        ),
        DropdownInput(
            name="language",
            display_name="언어",
            options=["ko"],
            value="ko",
            required=True,
        ),
        IntInput(
            name="max_model_description_chars",
            display_name="LLM 전달 원문 최대 문자 수",
            value=_DEFAULT_MODEL_CHARS,
            advanced=True,
            info="보고서 원문은 자르지 않고, LLM에 전달하는 안전한 원문만 이 길이로 제한합니다.",
        ),
    ]
    outputs = [Output(name="request", display_name="업무 요청", method="build_request")]

    def build_request(self) -> Data:
        raw_description = str(getattr(self, "description", "") or "")
        raw_instructions = str(getattr(self, "additional_instructions", "") or "")
        raw_final_refinement_instructions = str(getattr(self, "final_refinement_instructions", "") or "")
        language = str(getattr(self, "language", "ko") or "ko")
        if language != "ko":
            raise ValueError("LANGUAGE_UNSUPPORTED: 현재 Flow는 ko만 지원합니다.")
        if len(raw_description) > _MAX_DESCRIPTION_CHARS:
            raise ValueError(f"DESCRIPTION_TOO_LARGE: 업무 설명은 최대 {_MAX_DESCRIPTION_CHARS:,}자입니다.")

        safe_description, removed_description_controls = _remove_unsafe_controls(raw_description)
        safe_instructions, removed_instruction_controls = _remove_unsafe_controls(raw_instructions)
        safe_final_refinement, removed_final_refinement_controls = _remove_unsafe_controls(raw_final_refinement_instructions)
        display_description, description_redactions, description_count = _redact(safe_description)
        redacted_instructions, instruction_redactions, instruction_count = _redact(safe_instructions)
        redacted_final_refinement, final_refinement_redactions, final_refinement_count = _redact(safe_final_refinement)
        if len(display_description.strip()) < _MIN_DESCRIPTION_CHARS:
            raise ValueError(f"DESCRIPTION_TOO_SHORT: 업무 설명은 최소 {_MIN_DESCRIPTION_CHARS}자여야 합니다.")
        if len(display_description) > _MAX_DESCRIPTION_CHARS:
            raise ValueError(f"DESCRIPTION_TOO_LARGE: 업무 설명은 최대 {_MAX_DESCRIPTION_CHARS:,}자입니다.")

        requested_limit = int(getattr(self, "max_model_description_chars", _DEFAULT_MODEL_CHARS) or _DEFAULT_MODEL_CHARS)
        if not 80 <= requested_limit <= _MAX_MODEL_CHARS:
            raise ValueError(f"MODEL_DESCRIPTION_LIMIT_INVALID: 80~{_MAX_MODEL_CHARS:,} 사이여야 합니다.")
        description_for_model = _excerpt_at_boundaries(display_description, requested_limit)
        warnings: list[str] = []
        if len(description_for_model) < len(display_description):
            warnings.append("DESCRIPTION_TRUNCATED_FOR_MODEL")
        if removed_description_controls or removed_instruction_controls or removed_final_refinement_controls:
            warnings.append("UNSAFE_CONTROL_CHARACTERS_REMOVED")
        if description_count or instruction_count or final_refinement_count:
            warnings.append("SECRET_MATERIAL_REDACTED")

        all_redactions = description_redactions + [
            entry for entry in instruction_redactions if entry not in description_redactions
        ]
        all_redactions += [
            entry for entry in final_refinement_redactions if entry not in all_redactions
        ]
        request_material = {
            "description_display_redacted": display_description,
            "additional_instructions": redacted_instructions,
            "final_refinement_instructions": redacted_final_refinement,
            "language": language,
        }
        result = {
            "schema_version": _SCHEMA_VERSION,
            # This hash describes the source description only.  A redaction in an
            # optional design instruction must not erase deterministic identity of
            # an otherwise unredacted description.
            "description_original_sha256": _sha256(raw_description) if not description_count else None,
            "description_display_redacted": display_description,
            "description_for_model": description_for_model,
            "description_normalized": _normalise_for_search(display_description),
            "additional_instructions": redacted_instructions,
            "language": language,
            "redactions": all_redactions,
            "redaction_count": description_count + instruction_count + final_refinement_count,
            "description_char_count": len(display_description),
            "description_for_model_char_count": len(description_for_model),
            # This is deliberately held back from the first-pass prompt builder.
            # A later refinement component consumes only this redacted value after
            # the initial design has been normalized and checked.
            "final_refinement_instructions": redacted_final_refinement,
            "warnings": warnings,
            "source_description_sha256": _sha256(display_description),
            "request_sha256": _sha256(_canonical_json(request_material)),
        }
        self.status = "업무 설명을 안전한 설계 요청으로 정리했습니다."
        return Data(data=result)
