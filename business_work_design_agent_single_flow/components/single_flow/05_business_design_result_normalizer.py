from __future__ import annotations

"""Normalize one LLM design draft into the authoritative single-flow result.

This file is intentionally standalone: it does not import project helpers or
other custom components.  The model may *suggest* a design, but the input
request and locally ranked catalog candidates remain authoritative here.
"""

import datetime as _dt
import hashlib
import json
import math
import re
import uuid
from decimal import Decimal
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, Output
from lfx.schema import Data


_SCHEMA = "business-design-result/v2"
_DRAFT_SCHEMA = "business-design-draft/v1"
_REFINEMENT_FALLBACK_SCHEMA = "business-design-refinement-fallback/v1"
_CATALOG_SHORTLIST_SCHEMA = "catalog-shortlist/v1"
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_NODE_KINDS = {"start", "end", "work_step", "decision", "human_review", "system_call", "exception"}
_EDGE_KINDS = {"control", "branch", "error", "retry"}
_SOURCES = {"human_task", "builtin", "catalog_component", "catalog_flow", "new_component", "external_service"}
_SEVERITIES = {"required", "important", "optional"}
_DECISIONS = {"selected", "considered", "not_used"}
_TECHNICAL = {"metadata_only", "ports_extracted", "flow_graph_extracted", "verified_runtime", "unknown"}
_MAX_SHORTLISTED_CATALOG_ITEMS = 30
# The model call is configured for an 8,192-token response.  The fixed
# Structured Output model intentionally stays fairly open so providers with
# different JSON-schema support can still call it.  This post-model boundary is
# therefore responsible for keeping one unusually verbose completion from
# becoming an oversized graph/report or from consuming excessive CPU while it
# is normalized.  These are output safety caps, not business-design targets:
# 04 asks the model for a concise 32-node/48-edge TO-BE diagram, while this
# boundary keeps deliberate headroom (60 nodes/120 edges) for a valid richer
# transport without permitting an unbounded payload.
_MAX_DRAFT_RESPONSE_BYTES = 256_000
_MAX_COLLECTION_ITEMS = 30
_MAX_COLLECTION_ITEM_CHARS = 2_000
_MAX_CURRENT_STEPS = 40
_MAX_CURRENT_BRANCHES = 40
_MAX_CURRENT_EXCEPTIONS = 40
_MAX_INFORMATION_GAPS = 30
_MAX_GRAPH_NODES = 60
_MAX_GRAPH_EDGES = 120
_MAX_IMPLEMENTATION_ROADMAP_ITEMS = 20
_MAX_RISKS_AND_CONTROLS = 40
_MAX_TEST_SCENARIOS = 40
_MAX_CATALOG_DECISIONS = 30
_MAX_DECISION_TARGET_NODES = 30
_MAX_NARRATIVE_CHARS = 8_000
_MAX_DETAIL_CHARS = 2_000
_SECRET_KEY = re.compile(r"(?i)(?:api[_-]?key|authorization|bearer|client[_-]?secret|cookie|credential|password|passwd|private[_-]?key|secret|token)")
_SECRET_VALUE = re.compile(
    r"(?i)(?:\b(?:api[_-]?key|password|passwd|secret|access[_-]?token|client[_-]?secret|authorization)\s*[:=]\s*[^\s,;]{8,}|\bbearer\s+\S{8,}|\bsk-[A-Za-z0-9_-]{16,}\b|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
)
_FENCED_JSON_DOCUMENT = re.compile(
    r"\A\s*```(?:json|application/json)?[ \t]*\r?\n(?P<body>.*?)\r?\n?```\s*\Z",
    re.I | re.S,
)


def _safe_json(value: Any, path: str = "$") -> Any:
    """Convert supported transport values before hashing; reject unsafe objects."""

    data = getattr(value, "data", None)
    if isinstance(data, (dict, list)):
        return _safe_json(data, path)
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"[DESIGN_RESULT_INVALID] {path}에 유한하지 않은 숫자가 있습니다. 모델 응답을 다시 실행해 주세요.")
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"[DESIGN_RESULT_INVALID] {path}에 유한하지 않은 숫자가 있습니다. 모델 응답을 다시 실행해 주세요.")
        return value
    if isinstance(value, (tuple, set)):
        return [_safe_json(item, f"{path}[]") for item in value]
    if isinstance(value, list):
        return [_safe_json(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, dict):
        converted: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"[DESIGN_RESULT_INVALID] {path}에 문자열이 아닌 key가 있습니다. 모델 응답을 다시 실행해 주세요.")
            converted[key] = _safe_json(item, f"{path}.{key}")
        return converted
    raise ValueError(f"[DESIGN_RESULT_INVALID] {path}에 지원하지 않는 값 형식이 있습니다. 모델 응답을 다시 실행해 주세요.")


def _canonical(value: Any) -> str:
    return json.dumps(_safe_json(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _bounded_text(value: Any, limit: int = 20_000) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text[:limit]


def _bounded_list(
    value: Any,
    limit: int = _MAX_COLLECTION_ITEMS,
    item_limit: int = _MAX_COLLECTION_ITEM_CHARS,
) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:limit]:
        text = _bounded_text(item, item_limit)
        if text:
            result.append(text)
    return result


def _bounded_items(
    value: Any,
    *,
    limit: int,
    warning_code: str,
    warnings: list[str],
) -> list[Any]:
    """Return a bounded list and record an audit warning when it was clipped.

    We deliberately clip instead of rejecting an otherwise usable design.  The
    result remains deterministic, and the warning makes the loss visible to
    the report and run trace without feeding model text back into an error.
    """

    if not isinstance(value, list):
        return []
    if len(value) > limit:
        warnings.append(warning_code)
    return value[:limit]


def _assert_draft_response_size(draft: dict[str, Any]) -> None:
    """Reject an implausibly large post-parse model response before projection.

    The cap leaves substantial headroom above a normal 8,192-token JSON
    completion while preventing nested generic Pydantic fields from expanding
    into multi-megabyte report payloads.  Do not include the raw model content
    in the error because it can contain business-sensitive text.
    """

    material = json.dumps(draft, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    size = len(material.encode("utf-8"))
    if size > _MAX_DRAFT_RESPONSE_BYTES:
        raise ValueError(
            "[DESIGN_RESULT_TOO_LARGE] 모델 구조화 설계 응답이 안전 상한을 초과했습니다. "
            f"현재 {size} bytes, 최대 {_MAX_DRAFT_RESPONSE_BYTES} bytes입니다. "
            "05 Language Model의 출력 길이와 단계·분기·위험·테스트 항목 수를 줄인 뒤 다시 실행하세요."
        )


def _contains_secret(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if _SECRET_KEY.search(str(key)) and item not in (None, "", False, "[REDACTED]"):
                return True
            if _contains_secret(item):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    return isinstance(value, str) and value != "[REDACTED]" and bool(_SECRET_VALUE.search(value))


def _model_response_shape(text: str) -> str:
    """Classify the response without including its contents in an error message."""

    if text.lstrip().startswith("```"):
        return "코드 블록 또는 Markdown"
    if re.search(r"(?m)^\s*(?:#{1,6}\s|[-*+]\s|\d+[.)]\s)|\*\*|---", text):
        return "설명문 또는 Markdown"
    return "JSON 형식이 아닌 텍스트"


def _model_output_not_json_error(text: str, *, issue: str = "JSON object를 찾을 수 없습니다") -> ValueError:
    """Return an actionable, non-disclosing model-output contract error.

    The raw model reply may contain a work description or accidental secret.  Keep
    it out of Langflow's visible exception while leaving a stable, bounded
    diagnostic fingerprint that lets an operator correlate the failed run.
    """

    response_hash = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    shape = _model_response_shape(text)
    return ValueError(
        "[MODEL_OUTPUT_NOT_JSON] model_response가 유효한 JSON object가 아닙니다. "
        "업무 설계 결과 정규화는 설명문을 설계 결과로 추정·변환하지 않고 안전하게 중단했습니다. "
        f"진단: {shape}; 응답 길이 {len(text)}자; 응답 지문 sha256:{response_hash[:16]}. "
        f"문제: {issue}. "
        "조치: 05 Language Model에서 제공된다면 JSON/Structured Output을 활성화하고, "
        "System Message의 JSON 전용 계약을 유지한 뒤 다시 실행하세요. "
        "재시도 지시문: 반드시 schema_version이 business-design-draft/v1인 JSON object 하나만 반환하세요. "
        "Markdown, 설명, 코드 펜스, 주석, 앞뒤 문장을 포함하지 마세요. "
        "최상위 키는 schema_version, work_analysis, information_gaps, as_is_graph, to_be_design, catalog_decisions 입니다."
    )


def _parse_model_json_document(text: str) -> dict[str, Any]:
    """Accept only one JSON document, optionally wrapped by one JSON code fence.

    We intentionally do not scan a prose reply for a later ``{...}`` fragment.
    That behavior can silently select an unrelated example object from an LLM
    explanation and turn it into a seemingly valid design.
    """

    normalized = text.lstrip("\ufeff").strip()
    fenced = _FENCED_JSON_DOCUMENT.fullmatch(normalized)
    if fenced is not None:
        normalized = fenced.group("body").strip()
    try:
        parsed = json.loads(normalized)
    except json.JSONDecodeError as exc:
        raise _model_output_not_json_error(text) from exc
    if not isinstance(parsed, dict):
        raise _model_output_not_json_error(text, issue="JSON은 반환됐지만 최상위 값이 object가 아닙니다")
    return _safe_json(parsed, "model_response")


def _unwrap_structured_output_envelope(value: dict[str, Any]) -> dict[str, Any]:
    """Accept only an unambiguous transport wrapper from Structured Output.

    Langflow 1.11's built-in Structured Output component normally returns a
    single extracted object directly.  Its ``results`` wrapper is instead the
    documented *multiple extraction* shape.  Selecting an arbitrary entry from
    that list would turn a partial or unrelated extraction into a report, so it
    must be rejected.  A one-item wrapper can occur in an intermediary
    transport, and is safe to unwrap only when that sole item is already a
    complete draft-contract object.
    """

    if set(value) != {"results"}:
        return value
    results = value.get("results")
    if not isinstance(results, list):
        raise ValueError(
            "[STRUCTURED_OUTPUT_SCHEMA_MISMATCH] 06 Structured Output의 results 값이 목록이 아닙니다. "
            "최신 F01 Flow를 다시 import하고 06의 Output Schema가 business-design-draft/v1 계약인지 확인해 주세요."
        )
    if len(results) == 1 and isinstance(results[0], dict):
        item = _safe_json(results[0], "model_response.results[0]")
        if item.get("schema_version") == _DRAFT_SCHEMA and (
            isinstance(item.get("work_analysis"), dict) or isinstance(item.get("to_be_design"), dict)
        ):
            return item

    # This exact shape is the default TableInput schema shipped by Langflow's
    # Structured Output node.  It means the configured output_schema did not
    # survive import/refresh; it is not a business-design draft.
    default_field_shape = bool(results) and all(
        isinstance(item, dict) and set(item) == {"field"} for item in results
    )
    if default_field_shape:
        raise ValueError(
            "[STRUCTURED_OUTPUT_SCHEMA_MISMATCH] 구버전 06 Structured Output이 기본 Output Schema(field)로 실행되었습니다. "
            "현재 results.field 목록은 업무 설계 JSON이 아니므로 안전하게 사용하지 않았습니다. "
            "최신 F01 JSON을 새 Flow로 import한 뒤 06이 `업무 설계 JSON 생성` standalone component인지 확인해 주세요. "
            "최신 06은 editable Output Schema를 사용하지 않으며 schema_version, work_analysis, information_gaps, "
            "as_is_graph, to_be_design, catalog_decisions의 6개 필드를 고정합니다."
        )
    raise ValueError(
        "[STRUCTURED_OUTPUT_SCHEMA_MISMATCH] 06 Structured Output이 여러 개의 결과를 반환했습니다. "
        "이 Flow는 업무 설명 하나당 business-design-draft/v1 객체 하나만 허용하며, 여러 결과 중 하나를 임의로 선택하지 않습니다. "
        "06의 Output Schema와 연결을 확인한 뒤 다시 실행해 주세요."
    )


def _transport_object(value: Any, field: str) -> dict[str, Any]:
    # Langflow Message also exposes a ``data`` dictionary for message metadata.
    # For the model-response port its JSON proposal is always Message.text, not
    # that transport metadata dictionary.
    if field == "model_response" and hasattr(value, "text") and not isinstance(value, (str, dict)):
        message_text = getattr(value, "text", None)
        if not isinstance(message_text, str) or not message_text.strip():
            raise _model_output_not_json_error("", issue="Language Model Message.text가 비어 있습니다")
        return _parse_model_json_document(message_text)
    if hasattr(value, "text") and not isinstance(value, (str, dict)):
        message_text = getattr(value, "text", None)
        if isinstance(message_text, str) and message_text.strip():
            value = message_text
    raw = getattr(value, "data", None)
    if isinstance(raw, dict):
        value = raw
    elif raw is not None:
        value = raw
    elif hasattr(value, "text"):
        value = getattr(value, "text")
    if isinstance(value, dict):
        parsed = _safe_json(value, field)
        return _unwrap_structured_output_envelope(parsed) if field == "model_response" else parsed
    if isinstance(value, str):
        if field == "model_response":
            return _parse_model_json_document(value)
        try:
            parsed = json.loads(value.lstrip("\ufeff").strip())
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return _safe_json(parsed, field)
    raise ValueError(f"[DESIGN_RESULT_INVALID] {field}은 JSON object여야 합니다. 이전 node의 연결을 확인해 주세요.")


def _optional_transport_object(value: Any, field: str) -> dict[str, Any] | None:
    """Read an optional Data/JSON input without turning an absent port into an error."""

    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    raw = getattr(value, "data", None)
    if raw is None and not isinstance(value, (str, dict)):
        text = getattr(value, "text", None)
        if text is None or (isinstance(text, str) and not text.strip()):
            return None
    return _transport_object(value, field)


def _safe_refinement_reason(value: Any) -> str:
    """Keep only a stable, non-provider diagnostic category in the report path."""

    reason = _bounded_text(value, 80).upper()
    mappings = {
        "REFINEMENT_MODEL_MISSING": "MODEL_UNAVAILABLE",
        "REFINEMENT_NATIVE_CALL_FAILED": "MODEL_UNAVAILABLE",
        "REFINEMENT_UNEXPECTED_FAILURE": "MODEL_UNAVAILABLE",
        "REFINEMENT_PROMPT_INVALID": "REFINEMENT_NOT_RUN",
        "REFINEMENT_COMPATIBILITY_JSON_INVALID": "MODEL_OUTPUT_INVALID",
        "REFINEMENT_OUTPUT_INVALID": "MODEL_OUTPUT_INVALID",
        "MODEL_UNAVAILABLE": "MODEL_UNAVAILABLE",
        "STRUCTURED_OUTPUT_UNAVAILABLE": "STRUCTURED_OUTPUT_UNAVAILABLE",
        "MODEL_OUTPUT_INVALID": "MODEL_OUTPUT_INVALID",
        "REFINEMENT_TIMEOUT": "REFINEMENT_TIMEOUT",
        "REFINEMENT_NOT_RUN": "REFINEMENT_NOT_RUN",
    }
    return mappings.get(reason, "REFINEMENT_NOT_RUN")


def _use_verified_initial_result(
    fallback_design_result: dict[str, Any] | None,
    *,
    request: dict[str, Any],
    retrieval: dict[str, Any],
    fallback: dict[str, Any],
) -> dict[str, Any]:
    """Return an already normalized initial result if the optional refiner fails.

    The fallback is deliberately narrow: it may only pass through the result
    produced by the first instance of this same normalizer for the exact
    request and candidate set.  Provider error text never travels to the
    report; the report only receives a Korean-readable status category.
    """

    if not isinstance(fallback_design_result, dict):
        raise ValueError(
            "[REFINEMENT_FALLBACK_INVALID] 최종 보완 결과를 사용할 수 없어도 1차 검증 설계 결과가 필요합니다. "
            "07의 정규화 설계 결과를 09의 fallback input에 연결하세요."
        )
    result = _safe_json(fallback_design_result, "fallback_design_result")
    if result.get("schema_version") != _SCHEMA or not isinstance(result.get("request"), dict):
        raise ValueError(
            "[REFINEMENT_FALLBACK_INVALID] fallback input은 검증된 business-design-result/v2여야 합니다."
        )
    if _contains_secret(result):
        raise ValueError("[REFINEMENT_FALLBACK_INVALID] 1차 검증 설계 결과에 민감정보로 의심되는 값이 있습니다.")
    fallback_request_sha = _bounded_text(result["request"].get("request_sha256"), 80)
    request_sha = _bounded_text(request.get("request_sha256"), 80)
    trace = result.get("trace") if isinstance(result.get("trace"), dict) else {}
    fallback_candidate_hash = _bounded_text(trace.get("candidate_set_sha256"), 80)
    candidate_hash = _bounded_text(retrieval.get("candidate_set_sha256"), 80)
    if not request_sha or fallback_request_sha != request_sha or not candidate_hash or fallback_candidate_hash != candidate_hash:
        raise ValueError(
            "[REFINEMENT_FALLBACK_INVALID] 1차 결과의 업무 요청 또는 카탈로그 후보 집합이 현재 실행과 일치하지 않습니다."
        )
    reason = _safe_refinement_reason(fallback.get("reason_code"))
    warnings = _bounded_list(result.get("warnings"), limit=200, item_limit=128)
    warning = f"REFINEMENT_SKIPPED_{reason}"
    if warning not in warnings:
        warnings.append(warning)
    # Catalog decisions in the first pass already mean an actual mapped
    # application, not a preliminary candidate shortlist.  When refinement
    # cannot run, preserve that validated first-pass decision exactly; moving
    # it to a different partition here would make the fallback report lie.
    result["warnings"] = sorted(set(warnings))
    result["refinement"] = {
        "status": "SKIPPED",
        "reason_code": reason,
        "operator_instruction_provided": bool(_bounded_text(request.get("final_refinement_instructions"), 4_000)),
        "message": "최종 보완 모델을 사용할 수 없어 1차 검증 설계 결과를 표시했습니다.",
    }
    return result


def _slug(value: Any, fallback: str, used: set[str]) -> str:
    text = re.sub(r"[^A-Za-z0-9._:-]+", "-", _bounded_text(value, 128).casefold()).strip("-._:")
    if not text or _IDENTITY.fullmatch(text) is None:
        text = fallback
    candidate = text[:128]
    suffix = 2
    while candidate in used:
        base = text[: max(1, 124 - len(str(suffix)))]
        candidate = f"{base}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _asset_type(value: Any) -> str:
    text = _bounded_text(value, 64).casefold()
    return "flow" if text in {"flow", "json"} else "component"


def _catalog_url(asset_id: str, asset_type: str) -> str:
    return f"https://agent-hub.skhynix.com/#/{'flow' if asset_type == 'flow' else 'component'}/{asset_id}"


def _candidate_registry(retrieval: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = retrieval.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("[RETRIEVAL_INPUT_INVALID] 검색 후보 목록이 없습니다. 02 관련 기능 카탈로그 검색 결과를 확인해 주세요.")
    registry: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    # 02 exposes up to 100 locally ranked candidates.  The model may select a
    # lower-ranked but semantically exact asset, so the authoritative registry
    # must cover the entire bounded retrieval set rather than silently cutting
    # it back to the former 50-item UI default.
    for index, raw in enumerate(candidates[:100]):
        if not isinstance(raw, dict):
            continue
        asset_id = _bounded_text(raw.get("asset_id") or raw.get("id"), 64).lower()
        version = _bounded_text(raw.get("version") or "unknown", 100) or "unknown"
        if _UUID.fullmatch(asset_id) is None:
            raise ValueError(f"[RETRIEVAL_INPUT_INVALID] {index + 1}번째 후보의 asset_id가 표준 UUID가 아닙니다. 카탈로그 파일을 확인해 주세요.")
        identity = (asset_id, version)
        if identity in seen:
            raise ValueError("[RETRIEVAL_INPUT_INVALID] 검색 후보의 asset_id와 version 조합이 중복됩니다. 카탈로그 결과를 확인해 주세요.")
        seen.add(identity)
        asset_type = _asset_type(raw.get("asset_type") or raw.get("type"))
        status = _bounded_text(raw.get("technical_contract_status") or "metadata_only", 64)
        if status not in _TECHNICAL:
            status = "unknown"
        registry.append(
            {
                "asset_id": asset_id,
                "version": version,
                "title": _bounded_text(raw.get("title") or f"카탈로그 자산 {index + 1}", 500),
                "asset_type": asset_type,
                "technical_contract_status": status,
                "catalog_url": _catalog_url(asset_id, asset_type),
            }
        )
    if not registry:
        raise ValueError("[RETRIEVAL_INPUT_INVALID] 사용할 수 있는 검색 후보가 없습니다. 카탈로그와 검색 결과를 확인해 주세요.")
    return registry


def _normalize_current_routes(value: Any, *, exception: bool, warnings: list[str]) -> list[dict[str, Any]]:
    """Project branch/exception records into the small report contract.

    These two fields used to pass raw nested model objects through unchanged.
    Unlike graph nodes, that left their nested text and arbitrary keys without a
    size bound.  They are display/audit context only, so preserve the documented
    fields and discard unneeded model-provided extras.
    """

    raw_items = _bounded_items(
        value,
        limit=_MAX_CURRENT_EXCEPTIONS if exception else _MAX_CURRENT_BRANCHES,
        warning_code="CURRENT_EXCEPTIONS_TRUNCATED" if exception else "CURRENT_BRANCHES_TRUNCATED",
        warnings=warnings,
    )
    result: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        normalized = {
            "source_step_ref": _bounded_text(item.get("source_step_ref"), 128),
            "condition": _bounded_text(item.get("condition"), _MAX_DETAIL_CHARS),
            "target_step_ref": _bounded_text(item.get("target_step_ref"), 128),
        }
        if exception:
            normalized["handling"] = _bounded_text(item.get("handling"), _MAX_DETAIL_CHARS)
        else:
            normalized["is_default"] = bool(item.get("is_default"))
        result.append(normalized)
    return result


def _normalize_work_analysis(raw: Any, warnings: list[str]) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    result: dict[str, Any] = {
        "title": _bounded_text(raw.get("title") or raw.get("work_name") or "업무 설계 초안", 500),
        "goal": _bounded_text(raw.get("goal"), _MAX_NARRATIVE_CHARS),
        "scope_in": _bounded_list(raw.get("scope_in")),
        "scope_out": _bounded_list(raw.get("scope_out")),
        "actors": _bounded_list(raw.get("actors")),
        "systems": _bounded_list(raw.get("systems")),
        "inputs": _bounded_list(raw.get("inputs")),
        "outputs": _bounded_list(raw.get("outputs")),
        "trigger_and_frequency": _bounded_text(
            raw.get("trigger_and_frequency") or raw.get("trigger"), _MAX_NARRATIVE_CHARS
        ),
        "constraints": _bounded_list(raw.get("constraints")),
        "success_criteria": _bounded_list(raw.get("success_criteria")),
        "problems": _bounded_list(raw.get("problems")),
    }
    steps: list[dict[str, Any]] = []
    for index, item in enumerate(
        _bounded_items(
            raw.get("current_steps"),
            limit=_MAX_CURRENT_STEPS,
            warning_code="CURRENT_STEPS_TRUNCATED",
            warnings=warnings,
        )
    ):
        if not isinstance(item, dict):
            continue
        steps.append(
            {
                "step_ref": _bounded_text(item.get("step_ref") or item.get("id") or f"current-step-{index + 1}", 128),
                "sequence": int(item.get("sequence")) if isinstance(item.get("sequence"), int) and item.get("sequence") >= 0 else index + 1,
                "title": _bounded_text(item.get("title") or item.get("label") or f"현재 업무 단계 {index + 1}", 500),
                "description": _bounded_text(item.get("description") or item.get("summary"), _MAX_DETAIL_CHARS),
                "actor": _bounded_text(item.get("actor"), 500),
                "system": _bounded_text(item.get("system"), 500),
                "inputs": _bounded_list(item.get("inputs")),
                "outputs": _bounded_list(item.get("outputs")),
                "evidence_status": _bounded_text(item.get("evidence_status"), 32) if _bounded_text(item.get("evidence_status"), 32) in {"explicit", "inferred", "unknown"} else "inferred",
            }
        )
    result["current_steps"] = steps
    result["current_branches"] = _normalize_current_routes(
        raw.get("current_branches"), exception=False, warnings=warnings
    )
    result["current_exceptions"] = _normalize_current_routes(
        raw.get("current_exceptions"), exception=True, warnings=warnings
    )
    return result


def _normalize_gaps(raw: Any, warnings: list[str]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(
        _bounded_items(
            raw,
            limit=_MAX_INFORMATION_GAPS,
            warning_code="INFORMATION_GAPS_TRUNCATED",
            warnings=warnings,
        )
    ):
        if not isinstance(item, dict):
            continue
        field = _bounded_text(item.get("field") or f"additional_information_{index + 1}", 128)
        question = _bounded_text(item.get("question"), _MAX_DETAIL_CHARS)
        if not question or (field, question) in seen:
            continue
        seen.add((field, question))
        severity = _bounded_text(item.get("severity") or "important", 32)
        if severity not in _SEVERITIES:
            severity = "important"
            warnings.append("INFORMATION_GAP_SEVERITY_NORMALIZED")
        result.append(
            {
                "gap_id": _slug(item.get("gap_id") or f"gap-{field}", f"gap-{index + 1}", {value["gap_id"] for value in result}),
                "field": field,
                "severity": severity,
                "question": question,
                "why_needed": _bounded_text(item.get("why_needed"), _MAX_DETAIL_CHARS),
                "design_impact": _bounded_text(item.get("design_impact"), _MAX_DETAIL_CHARS),
                "suggested_description_text": _bounded_text(item.get("suggested_description_text"), _MAX_DETAIL_CHARS),
            }
        )
        if len(result) >= _MAX_INFORMATION_GAPS:
            break
    return result


def _node_from_raw(raw: dict[str, Any], *, prefix: str, sequence: int, used: set[str]) -> tuple[dict[str, Any], str]:
    original = _bounded_text(raw.get("node_id") or raw.get("id") or raw.get("key") or f"{prefix}-{sequence}", 128)
    kind = _bounded_text(raw.get("node_kind") or raw.get("kind") or "work_step", 64)
    if kind not in _NODE_KINDS:
        kind = "work_step"
    if kind == "start":
        node_id = "start"
        used.add(node_id)
    elif kind == "end":
        node_id = "end"
        used.add(node_id)
    else:
        node_id = _slug(f"{prefix}-{original}", f"{prefix}-{sequence}", used)
    source = _bounded_text(raw.get("implementation_source"), 64)
    if source not in _SOURCES:
        source = "human_task" if prefix == "as-is" else "builtin"
    node = {
        "node_id": node_id,
        "node_kind": kind,
        "title": _bounded_text(raw.get("title") or raw.get("label") or ("업무 시작" if kind == "start" else "업무 종료" if kind == "end" else f"업무 단계 {sequence}"), 500),
        "summary": _bounded_text(raw.get("summary") or raw.get("description") or raw.get("detail"), _MAX_DETAIL_CHARS),
        "sequence": sequence,
        "actor": _bounded_text(raw.get("actor"), 500),
        "system": _bounded_text(raw.get("system"), 500),
        "inputs": _bounded_list(raw.get("inputs")),
        "outputs": _bounded_list(raw.get("outputs")),
        "implementation_source": source,
        "catalog_asset_refs": [],
    }
    return node, original


def _graph_from_raw(
    raw: Any,
    *,
    prefix: str,
    fallback_steps: list[dict[str, Any]],
    warnings: list[str],
    add_gap: list[dict[str, str]],
) -> tuple[dict[str, Any], dict[str, str]]:
    raw = raw if isinstance(raw, dict) else {}
    raw_nodes = _bounded_items(
        raw.get("nodes"),
        limit=_MAX_GRAPH_NODES,
        warning_code=f"{prefix.upper().replace('-', '_')}_GRAPH_NODES_TRUNCATED",
        warnings=warnings,
    )
    used: set[str] = set()
    nodes: list[dict[str, Any]] = []
    original_to_id: dict[str, str] = {}
    for index, item in enumerate(raw_nodes):
        if not isinstance(item, dict):
            continue
        node, original = _node_from_raw(item, prefix=prefix, sequence=index + 1, used=used)
        if node["node_id"] in {existing["node_id"] for existing in nodes}:
            continue
        nodes.append(node)
        original_to_id[original] = node["node_id"]
    if not any(node["node_kind"] == "start" for node in nodes):
        nodes.insert(0, {"node_id": "start", "node_kind": "start", "title": "업무 시작", "summary": "", "sequence": 0, "actor": "", "system": "", "inputs": [], "outputs": [], "implementation_source": "human_task", "catalog_asset_refs": []})
        used.add("start")
    if not any(node["node_kind"] == "end" for node in nodes):
        nodes.append({"node_id": "end", "node_kind": "end", "title": "업무 종료", "summary": "", "sequence": len(nodes) + 1, "actor": "", "system": "", "inputs": [], "outputs": [], "implementation_source": "human_task", "catalog_asset_refs": []})
        used.add("end")
    work_nodes = [node for node in nodes if node["node_kind"] not in {"start", "end"}]
    if not work_nodes and fallback_steps:
        for index, step in enumerate(fallback_steps[:_MAX_CURRENT_STEPS], start=1):
            node, original = _node_from_raw(
                {"node_id": step.get("step_ref"), "title": step.get("title"), "summary": step.get("description"), "actor": step.get("actor"), "system": step.get("system"), "inputs": step.get("inputs"), "outputs": step.get("outputs"), "node_kind": "work_step"},
                prefix=prefix,
                sequence=index,
                used=used,
            )
            nodes.insert(-1, node)
            original_to_id[original] = node["node_id"]
        work_nodes = [node for node in nodes if node["node_kind"] not in {"start", "end"}]
        warnings.append("GRAPH_REPAIRED_FROM_WORK_ANALYSIS")
    if not work_nodes:
        placeholder = {"node_id": _slug(f"{prefix}-detail-needed", f"{prefix}-detail-needed", used), "node_kind": "work_step", "title": "업무 세부 단계 확인 필요", "summary": "업무 설명에 단계 순서가 충분히 드러나지 않아 다음 실행에서 보완이 필요합니다.", "sequence": 1, "actor": "", "system": "", "inputs": [], "outputs": [], "implementation_source": "human_task" if prefix == "as-is" else "builtin", "catalog_asset_refs": []}
        nodes.insert(-1, placeholder)
        work_nodes = [placeholder]
        add_gap.append({"gap_id": f"gap-{prefix}-steps", "field": "work_steps", "severity": "important", "question": "업무가 시작된 뒤 완료될 때까지의 단계와 분기 조건을 순서대로 설명해 주세요.", "why_needed": "현재 업무와 개선 Flow의 연결 및 예외 처리를 정확히 표현하기 위해 필요합니다.", "design_impact": "현재 설계에서는 세부 단계를 확인 필요 항목으로 남겼습니다.", "suggested_description_text": "업무 단계는 1) … 2) … 3) … 순서이며, …인 경우에는 …로 분기합니다."})
    # Keep one canonical start/end and turn accidental extra terminals into normal work steps.
    starts = [node for node in nodes if node["node_kind"] == "start"]
    ends = [node for node in nodes if node["node_kind"] == "end"]
    for node in starts[1:] + ends[1:]:
        node["node_kind"] = "work_step"
    start = next(node for node in nodes if node["node_kind"] == "start")
    end = next(node for node in nodes if node["node_kind"] == "end")
    id_set = {node["node_id"] for node in nodes}
    raw_edges = _bounded_items(
        raw.get("edges"),
        limit=_MAX_GRAPH_EDGES,
        warning_code=f"{prefix.upper().replace('-', '_')}_GRAPH_EDGES_TRUNCATED",
        warnings=warnings,
    )
    edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str, str]] = set()
    for index, item in enumerate(raw_edges):
        if not isinstance(item, dict):
            continue
        source_raw = _bounded_text(item.get("source_node_id") or item.get("source") or item.get("from"), 128)
        target_raw = _bounded_text(item.get("target_node_id") or item.get("target") or item.get("to"), 128)
        source = original_to_id.get(source_raw, source_raw)
        target = original_to_id.get(target_raw, target_raw)
        if source not in id_set or target not in id_set or source == target:
            warnings.append("GRAPH_DANGLING_EDGE_REMOVED")
            continue
        edge_kind = _bounded_text(item.get("edge_kind") or item.get("kind") or "control", 32)
        if edge_kind not in _EDGE_KINDS:
            edge_kind = "control"
        label = _bounded_text(item.get("label") or ("다음" if edge_kind == "control" else "분기"), 500)
        condition = _bounded_text(item.get("condition"), _MAX_DETAIL_CHARS)
        if edge_kind == "branch" and not label:
            label = "분기"
        key = (source, target, edge_kind, label)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        retry = item.get("retry_policy") if isinstance(item.get("retry_policy"), dict) else {}
        edge = {"edge_id": _slug(item.get("edge_id") or f"{prefix}-edge-{index + 1}", f"{prefix}-edge-{index + 1}", {edge_value["edge_id"] for edge_value in edges}), "source_node_id": source, "target_node_id": target, "edge_kind": edge_kind, "label": label or "다음", "condition": condition, "is_default": bool(item.get("is_default")), "retry_policy": {}}
        if edge_kind == "retry":
            attempts = retry.get("max_attempts") if isinstance(retry.get("max_attempts"), int) else item.get("max_attempts")
            backoff = retry.get("backoff_seconds") if isinstance(retry.get("backoff_seconds"), (int, float)) else item.get("backoff_seconds")
            exhausted_raw = _bounded_text(retry.get("on_exhausted_target_node_id") or item.get("on_exhausted_target_node_id"), 128)
            exhausted = original_to_id.get(exhausted_raw, exhausted_raw)
            if not isinstance(attempts, int) or attempts < 1 or not isinstance(backoff, (int, float)) or backoff < 0 or exhausted not in id_set:
                warnings.append("GRAPH_RETRY_POLICY_NORMALIZED")
                edge["retry_policy"] = {"max_attempts": 1, "backoff_seconds": 0, "on_exhausted_target_node_id": end["node_id"]}
            else:
                edge["retry_policy"] = {"max_attempts": min(attempts, 20), "backoff_seconds": min(float(backoff), 3600), "on_exhausted_target_node_id": exhausted}
        edges.append(edge)
    # A deterministic linear repair is safer than emitting an orphan graph.
    normal_nodes = sorted([node for node in nodes if node["node_kind"] not in {"start", "end"}], key=lambda node: (node["sequence"], node["node_id"]))
    needs_repair = not edges or not _reachable(start["node_id"], end["node_id"], edges) or any(not _reachable(start["node_id"], node["node_id"], edges) for node in normal_nodes)
    if needs_repair:
        ordered = [start] + normal_nodes + [end]
        edges = []
        for index, (source, target) in enumerate(zip(ordered, ordered[1:]), start=1):
            edges.append({"edge_id": f"{prefix}-edge-{index}", "source_node_id": source["node_id"], "target_node_id": target["node_id"], "edge_kind": "control", "label": "다음", "condition": "", "is_default": False, "retry_policy": {}})
        warnings.append("GRAPH_REPAIRED")
    # Decision with insufficient outgoing branches is a work step, not a false decision UI.
    outgoing: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        outgoing.setdefault(edge["source_node_id"], []).append(edge)
    for node in nodes:
        if node["node_kind"] == "decision":
            branches = [edge for edge in outgoing.get(node["node_id"], []) if edge["edge_kind"] == "branch"]
            if len({edge["label"] for edge in branches}) < 2:
                node["node_kind"] = "work_step"
                warnings.append("GRAPH_DECISION_NORMALIZED")
    for sequence, node in enumerate(sorted(nodes, key=lambda node: (0 if node["node_kind"] == "start" else 2 if node["node_kind"] == "end" else 1, node["sequence"], node["node_id"])), start=0):
        node["sequence"] = sequence
    return {"nodes": sorted(nodes, key=lambda node: node["sequence"]), "edges": edges}, original_to_id


def _reachable(source: str, target: str, edges: list[dict[str, Any]]) -> bool:
    seen: set[str] = set()
    stack = [source]
    while stack:
        node = stack.pop()
        if node == target:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(edge["target_node_id"] for edge in edges if edge["source_node_id"] == node and edge["edge_kind"] != "retry")
    return False


def _normalize_tobe(raw: Any, warnings: list[str], gaps: list[dict[str, str]]) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    raw = raw if isinstance(raw, dict) else {}
    graph, mapping = _graph_from_raw(raw, prefix="to-be", fallback_steps=[], warnings=warnings, add_gap=gaps)
    result = {
        "summary": _bounded_text(raw.get("summary"), _MAX_NARRATIVE_CHARS),
        "principles": _bounded_list(raw.get("principles")),
        "nodes": graph["nodes"],
        "edges": graph["edges"],
        "implementation_roadmap": [],
        "risks_and_controls": [],
        "test_scenarios": [],
    }
    for item in _bounded_items(
        raw.get("implementation_roadmap"),
        limit=_MAX_IMPLEMENTATION_ROADMAP_ITEMS,
        warning_code="IMPLEMENTATION_ROADMAP_TRUNCATED",
        warnings=warnings,
    ):
        if not isinstance(item, dict):
            continue
        result["implementation_roadmap"].append(
            {
                "phase": _bounded_text(item.get("phase") or str(len(result["implementation_roadmap"]) + 1), 128),
                "title": _bounded_text(item.get("title") or "구현 단계", 500),
                "actions": _bounded_list(item.get("actions")),
                "dependencies": _bounded_list(item.get("dependencies")),
                "completion_criteria": _bounded_list(item.get("completion_criteria")),
            }
        )
    for item in _bounded_items(
        raw.get("risks_and_controls"),
        limit=_MAX_RISKS_AND_CONTROLS,
        warning_code="RISKS_AND_CONTROLS_TRUNCATED",
        warnings=warnings,
    ):
        if not isinstance(item, dict):
            continue
        result["risks_and_controls"].append(
            {
                "risk_id": _slug(
                    item.get("risk_id") or f"risk-{len(result['risks_and_controls']) + 1}",
                    f"risk-{len(result['risks_and_controls']) + 1}",
                    {risk["risk_id"] for risk in result["risks_and_controls"]},
                ),
                "risk": _bounded_text(item.get("risk"), _MAX_DETAIL_CHARS),
                "impact": _bounded_text(item.get("impact"), _MAX_DETAIL_CHARS),
                "control": _bounded_text(item.get("control"), _MAX_DETAIL_CHARS),
                "owner_role": _bounded_text(item.get("owner_role"), 500),
            }
        )
    for item in _bounded_items(
        raw.get("test_scenarios"),
        limit=_MAX_TEST_SCENARIOS,
        warning_code="TEST_SCENARIOS_TRUNCATED",
        warnings=warnings,
    ):
        if not isinstance(item, dict):
            continue
        result["test_scenarios"].append(
            {
                "test_id": _slug(
                    item.get("test_id") or f"test-{len(result['test_scenarios']) + 1}",
                    f"test-{len(result['test_scenarios']) + 1}",
                    {test["test_id"] for test in result["test_scenarios"]},
                ),
                "title": _bounded_text(item.get("title") or "검증 시나리오", 500),
                "given": _bounded_text(item.get("given"), _MAX_DETAIL_CHARS),
                "when": _bounded_text(item.get("when"), _MAX_DETAIL_CHARS),
                "then": _bounded_text(item.get("then"), _MAX_DETAIL_CHARS),
            }
        )
    return result, mapping, raw


def _catalog_shortlist_policy(draft: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    """Read the Canvas-owned cap from the dedicated 03 shortlist result."""

    raw = draft.get("catalog_shortlist_policy")
    if raw is None:
        raise ValueError(
            "[CATALOG_SHORTLIST_POLICY_INVALID] 03 LLM 카탈로그 후보 선별 결과의 정책이 없습니다."
        )
    if not isinstance(raw, dict):
        raise ValueError(
            "[CATALOG_SHORTLIST_POLICY_INVALID] 선별 후보 수 정책이 object가 아닙니다. "
            "03의 LLM 선별 후보 최대 수 설정을 확인하세요."
        )
    maximum = raw.get("max_shortlisted_catalog_items")
    if type(maximum) is not int or not 1 <= maximum <= _MAX_SHORTLISTED_CATALOG_ITEMS:
        raise ValueError(
            "[CATALOG_SHORTLIST_POLICY_INVALID] LLM 선별 후보 최대 수는 "
            f"1~{_MAX_SHORTLISTED_CATALOG_ITEMS} 사이의 정수여야 합니다."
        )
    return {
        "max_shortlisted_catalog_items": maximum,
        "selection_scope": "candidate_shortlist_only",
        "selection_source": "llm_catalog_shortlister",
    }


def _normalize_decisions(
    raw: Any,
    registry: list[dict[str, Any]],
    node_mapping: dict[str, str],
    tobe_nodes: list[dict[str, Any]],
    warnings: list[str],
    *,
    shortlist_policy: dict[str, Any],
    allowed_candidate_keys: set[tuple[str, str]] | None = None,
    decision_source: str = "llm",
) -> dict[str, Any]:
    raw_items = _bounded_items(
        raw,
        limit=_MAX_CATALOG_DECISIONS,
        warning_code="CATALOG_DECISIONS_TRUNCATED",
        warnings=warnings,
    )
    proposals: dict[tuple[str, str], dict[str, Any]] = {}
    known_registry_keys = {(candidate["asset_id"], candidate["version"]) for candidate in registry}
    allowed_keys = set(allowed_candidate_keys) if allowed_candidate_keys is not None else None
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        asset_id = _bounded_text(item.get("asset_id") or item.get("id"), 64).lower()
        version = _bounded_text(item.get("version") or "unknown", 100) or "unknown"
        key = (asset_id, version)
        if asset_id:
            proposals.setdefault(key, item)
    if allowed_keys is not None:
        for key, proposal in proposals.items():
            if key in known_registry_keys and key not in allowed_keys and _bounded_text(proposal.get("decision"), 32) != "not_used":
                warnings.append("CATALOG_DECISION_OUTSIDE_SHORTLIST")
    known_node_ids = {node["node_id"] for node in tobe_nodes}
    partitions: dict[str, list[dict[str, Any]]] = {"selected": [], "considered": [], "not_used": []}
    for candidate in registry:
        key = (candidate["asset_id"], candidate["version"])
        proposal = proposals.get(key)
        if allowed_keys is not None and key not in allowed_keys:
            # The 100-item lexical pool is evidence for 03 only.  Do not
            # expose the remaining 100-N assets as report candidates or let a
            # later design decision quietly turn them into not-used choices.
            continue
        if proposal is None:
            decision = "not_used"
            source = "default_fill"
            reason = "모델이 적용 또는 연결 검토 대상으로 지정하지 않았습니다."
            required = []
            targets: list[str] = []
            warnings.append("CATALOG_DECISION_DEFAULT_FILLED")
        else:
            decision = _bounded_text(proposal.get("decision"), 32)
            if decision not in _DECISIONS:
                decision = "not_used"
                warnings.append("CATALOG_DECISION_NORMALIZED")
            source = decision_source
            reason = _bounded_text(proposal.get("reason"), _MAX_DETAIL_CHARS) or ("카탈로그 후보를 이 단계에 적용하는 방안을 검토합니다." if decision != "not_used" else "현재 설계에서 직접 적용 대상으로 지정하지 않았습니다.")
            required = _bounded_list(proposal.get("required_verification"))
            targets = []
            raw_targets = _bounded_items(
                proposal.get("target_node_ids"),
                limit=_MAX_DECISION_TARGET_NODES,
                warning_code="CATALOG_TARGET_NODES_TRUNCATED",
                warnings=warnings,
            )
            for target in raw_targets:
                raw_target = _bounded_text(target, 128)
                normalized = node_mapping.get(raw_target, raw_target)
                if normalized in known_node_ids and normalized not in targets:
                    targets.append(normalized)
                elif raw_target:
                    warnings.append("CATALOG_TARGET_NODE_INVALID")
            if decision == "selected" and not targets:
                # The separate 03 LLM is responsible for candidate
                # shortlisting.  Here selected has one meaning only: this
                # asset is actually applied to a concrete TO-BE node.
                decision = "considered"
                warnings.append("CATALOG_SELECTED_WITHOUT_TARGET_NORMALIZED")
            if decision == "not_used":
                targets = []
        value = {**candidate, "target_node_ids": targets, "reason": reason, "required_verification": required, "decision_source": source}
        partitions[decision].append(value)
    selected_keys = {(item["asset_id"], item["version"]) for item in partitions["selected"]}
    for node in tobe_nodes:
        refs = []
        for item in partitions["selected"]:
            if node["node_id"] in item["target_node_ids"]:
                refs.append({"asset_id": item["asset_id"], "version": item["version"]})
        node["catalog_asset_refs"] = refs
        if refs and node["implementation_source"] == "builtin":
            node["implementation_source"] = "catalog_component" if any((asset_id, version) in selected_keys and next(candidate for candidate in partitions["selected"] if candidate["asset_id"] == asset_id and candidate["version"] == version)["asset_type"] == "component" for asset_id, version in [(ref["asset_id"], ref["version"]) for ref in refs]) else "catalog_flow"
    return {
        "candidate_count": len(registry) if allowed_keys is None else len(allowed_keys),
        "retrieval_candidate_count": len(registry),
        "selection_policy": _safe_json(shortlist_policy, "catalog_shortlist_policy"),
        **partitions,
    }


def _validated_catalog_shortlist(
    shortlist_result: dict[str, Any],
    *,
    request: dict[str, Any],
    retrieval: dict[str, Any],
    registry: list[dict[str, Any]],
    warnings: list[str],
) -> tuple[dict[str, Any], set[tuple[str, str]], list[dict[str, Any]]]:
    """Validate the direct 03 LLM shortlist against the current 02 registry.

    The shortlist is the fixed *review scope* for both design-model calls. It
    is deliberately separate from ``catalog_decisions``: later models may use,
    merely consider, or reject every shortlisted asset, but cannot add a
    retrieved asset that 03 did not select.
    """

    fixed = _safe_json(shortlist_result, "catalog_shortlist")
    if fixed.get("schema_version") != _CATALOG_SHORTLIST_SCHEMA:
        raise ValueError(
            "[CATALOG_SHORTLIST_LOCK_INVALID] 고정 카탈로그 후보 입력은 03의 catalog-shortlist/v1 결과여야 합니다."
        )
    if fixed.get("ok") is not True or fixed.get("status") != "COMPLETED":
        raise ValueError("[CATALOG_SHORTLIST_LOCK_INVALID] 03 LLM 카탈로그 후보 선별이 완료되지 않았습니다.")
    fixed_request_sha = _bounded_text(fixed.get("request_sha256"), 80)
    request_sha = _bounded_text(request.get("request_sha256"), 80)
    fixed_candidate_sha = _bounded_text(fixed.get("candidate_set_sha256"), 80)
    candidate_sha = _bounded_text(retrieval.get("candidate_set_sha256"), 80)
    if not request_sha or fixed_request_sha != request_sha or not candidate_sha or fixed_candidate_sha != candidate_sha:
        raise ValueError(
            "[CATALOG_SHORTLIST_LOCK_INVALID] 03 선별 후보의 업무 요청 또는 후보 집합이 현재 실행과 일치하지 않습니다."
        )
    fixed_catalog_sha = _bounded_text(fixed.get("catalog_file_sha256"), 80)
    catalog_sha = _bounded_text(retrieval.get("catalog_file_sha256") or retrieval.get("file_sha256"), 80)
    if not catalog_sha or fixed_catalog_sha != catalog_sha:
        raise ValueError("[CATALOG_SHORTLIST_LOCK_INVALID] 03 선별 후보의 카탈로그 파일이 현재 검색 결과와 일치하지 않습니다.")
    policy_raw = fixed.get("selection_policy")
    if not isinstance(policy_raw, dict) or policy_raw.get("selection_scope") != "candidate_shortlist_only":
        raise ValueError("[CATALOG_SHORTLIST_LOCK_INVALID] 03 선별 후보의 selection_policy가 유효하지 않습니다.")
    policy = _catalog_shortlist_policy({"catalog_shortlist_policy": policy_raw}, warnings)
    registry_keys = {(item["asset_id"], item["version"]) for item in registry}
    shortlist_keys: set[tuple[str, str]] = set()
    normalized_candidates: list[dict[str, Any]] = []
    shortlisted = fixed.get("shortlisted_candidates") if isinstance(fixed.get("shortlisted_candidates"), list) else None
    if shortlisted is None or fixed.get("shortlisted_count") != len(shortlisted):
        raise ValueError("[CATALOG_SHORTLIST_LOCK_INVALID] 03 선별 후보 목록 또는 후보 수 계약이 유효하지 않습니다.")
    if len(shortlisted) > policy["max_shortlisted_catalog_items"]:
        raise ValueError("[CATALOG_SHORTLIST_LOCK_INVALID] 03 선별 후보 수가 Canvas 상한을 초과했습니다.")
    registry_by_key = {(item["asset_id"], item["version"]): item for item in registry}
    for index, item in enumerate(shortlisted, start=1):
        if not isinstance(item, dict):
            raise ValueError("[CATALOG_SHORTLIST_LOCK_INVALID] 03 선별 후보에 object가 아닌 항목이 있습니다.")
        asset_id = _bounded_text(item.get("asset_id"), 64).lower()
        version = _bounded_text(item.get("version") or "unknown", 100) or "unknown"
        key = (asset_id, version)
        if item.get("shortlist_rank") != index or key not in registry_keys or key in shortlist_keys:
            raise ValueError("[CATALOG_SHORTLIST_LOCK_INVALID] 03 선별 후보의 순서·식별자 또는 중복이 유효하지 않습니다.")
        shortlist_keys.add(key)
        source = registry_by_key[key]
        normalized_candidates.append(
            {
                **source,
                "shortlist_rank": index,
                "reason": _bounded_text(item.get("reason"), 2_000)
                or "업무 설명과 카탈로그 후보 정보를 바탕으로 후속 설계 검토 대상으로 선별했습니다.",
            }
        )
    warnings.append("CATALOG_CANDIDATE_SHORTLIST_PRESERVED")
    return policy, shortlist_keys, normalized_candidates


class BusinessDesignResultNormalizerComponent(Component):
    """Make an LLM draft safe, complete, and bound to the fixed shortlist."""

    display_name = "업무 설계 결과 정규화·검증"
    description = "모델 설계 초안을 검증하고, 입력·카탈로그 registry·03의 고정 shortlist를 권위 데이터로 다시 결합합니다."
    icon = "ShieldCheck"
    name = "BusinessDesignResultNormalizer"

    inputs = [
        DataInput(
            name="model_response",
            display_name="모델 구조화 설계 응답",
            info="06 또는 09의 Structured Output(JSON/Data)을 연결합니다. Message 텍스트도 직접 테스트·호환 경로에서 안전하게 처리합니다.",
            required=True,
            input_types=["Data", "JSON"],
        ),
        DataInput(name="request", display_name="업무 요청", required=True),
        DataInput(name="retrieval_result", display_name="카탈로그 검색 결과", required=True),
        DataInput(
            name="catalog_shortlist",
            display_name="고정 LLM 선별 카탈로그 후보",
            info=(
                "03 LLM 카탈로그 후보 선별 결과를 연결합니다. 이후 설계 모델은 이 목록 안에서만 "
                "실제 적용·검토·미사용을 판단할 수 있으며, 후보 사용은 강제되지 않습니다."
            ),
            required=True,
            input_types=["Data", "JSON"],
        ),
        DataInput(
            name="fallback_design_result",
            display_name="1차 검증 설계 결과(보완 실패 시 사용)",
            info="최종 보완 모델을 사용할 수 없을 때 이미 검증된 1차 설계 결과를 안전하게 유지합니다. 최종 정규화 인스턴스에만 07의 결과를 연결합니다.",
            required=False,
            input_types=["Data", "JSON"],
        ),
    ]
    outputs = [Output(name="design_result", display_name="정규화 설계 결과", method="normalize_design", types=["Data"])]

    def normalize_design(self) -> Data:
        request = _transport_object(self.request, "request")
        retrieval = _transport_object(self.retrieval_result, "retrieval_result")
        draft = _transport_object(self.model_response, "model_response")
        fallback_design_result = _optional_transport_object(
            getattr(self, "fallback_design_result", None),
            "fallback_design_result",
        )
        catalog_shortlist = _transport_object(getattr(self, "catalog_shortlist", None), "catalog_shortlist")
        if draft.get("schema_version") == _REFINEMENT_FALLBACK_SCHEMA:
            result = _use_verified_initial_result(
                fallback_design_result,
                request=request,
                retrieval=retrieval,
                fallback=draft,
            )
            self.status = "최종 보완 모델을 건너뛰고 1차 검증 설계 결과를 사용했습니다."
            return Data(data=result)
        if draft.get("schema_version") not in {None, _DRAFT_SCHEMA}:
            raise ValueError("[DESIGN_RESULT_INVALID] 모델 응답의 schema_version이 business-design-draft/v1이 아닙니다. 고정 Prompt를 확인해 주세요.")
        _assert_draft_response_size(draft)
        if _contains_secret(draft):
            raise ValueError("[DESIGN_RESULT_INVALID] 모델 응답에 민감정보로 의심되는 값이 있습니다. 업무 설명을 마스킹한 뒤 다시 실행해 주세요.")
        if not isinstance(draft.get("work_analysis"), dict) and not isinstance(draft.get("to_be_design"), dict):
            raise ValueError("[DESIGN_RESULT_INVALID] 모델 응답에 업무 분석과 개선 설계가 없습니다. 모델을 다시 실행해 주세요.")
        registry = _candidate_registry(retrieval)
        warnings: list[str] = []
        work_analysis = _normalize_work_analysis(draft.get("work_analysis"), warnings)
        gaps = _normalize_gaps(draft.get("information_gaps"), warnings)
        as_is_graph, _ = _graph_from_raw(draft.get("as_is_graph"), prefix="as-is", fallback_steps=work_analysis["current_steps"], warnings=warnings, add_gap=gaps)
        to_be_design, node_mapping, _ = _normalize_tobe(draft.get("to_be_design"), warnings, gaps)
        shortlist_policy, allowed_candidate_keys, shortlisted_candidates = _validated_catalog_shortlist(
            catalog_shortlist,
            request=request,
            retrieval=retrieval,
            registry=registry,
            warnings=warnings,
        )
        catalog_application = _normalize_decisions(
            draft.get("catalog_decisions"),
            registry,
            node_mapping,
            to_be_design["nodes"],
            warnings,
            shortlist_policy=shortlist_policy,
            allowed_candidate_keys=allowed_candidate_keys,
        )
        if not work_analysis["current_steps"]:
            work_analysis["current_steps"] = [
                {"step_ref": node["node_id"], "sequence": node["sequence"], "title": node["title"], "description": node["summary"], "actor": node["actor"], "system": node["system"], "inputs": node["inputs"], "outputs": node["outputs"], "evidence_status": "inferred"}
                for node in as_is_graph["nodes"]
                if node["node_kind"] not in {"start", "end"}
            ]
        # Stable trace: only small, non-sensitive audit identities are retained.
        trace = {
            "source_description_sha256": _bounded_text(request.get("description_original_sha256") or request.get("source_description_sha256"), 80),
            "request_sha256": _bounded_text(request.get("request_sha256"), 80),
            "catalog_file_sha256": _bounded_text(retrieval.get("catalog_file_sha256") or retrieval.get("file_sha256"), 80),
            "candidate_set_sha256": _bounded_text(retrieval.get("candidate_set_sha256"), 80),
            "top_n": retrieval.get("top_n_returned") if isinstance(retrieval.get("top_n_returned"), int) else len(registry),
            "ranking_algorithm": _bounded_text(retrieval.get("ranking_algorithm") or "local-lexical-rrf/v1", 128),
            "catalog_shortlist_policy": catalog_application.get("selection_policy") if isinstance(catalog_application.get("selection_policy"), dict) else {},
            "model_identifier": _bounded_text(getattr(self.model_response, "metadata", {}).get("model") if isinstance(getattr(self.model_response, "metadata", None), dict) else "unknown", 256) or "unknown",
        }
        result = {
            "schema_version": _SCHEMA,
            "status": "COMPLETED_WITH_GAPS" if gaps else "COMPLETED",
            "request": request,
            "work_analysis": work_analysis,
            "information_gaps": gaps,
            "as_is_graph": as_is_graph,
            "to_be_design": to_be_design,
            "catalog_candidate_shortlist": {
                "schema_version": _CATALOG_SHORTLIST_SCHEMA,
                "policy": shortlist_policy,
                "candidates": shortlisted_candidates,
            },
            "catalog_application": catalog_application,
            "warnings": sorted(set(warnings)),
            "trace": trace,
        }
        # This component is used twice in F01.  The second instance receives
        # the already verified first-pass result as its optional fallback;
        # only then expose refinement status to the report.  The first
        # normalizer remains a pure initial validation step.
        if fallback_design_result is not None:
            result["refinement"] = {
                "status": "APPLIED",
                "operator_instruction_provided": bool(
                    _bounded_text(request.get("final_refinement_instructions"), 4_000)
                ),
            }
        self.status = (
            f"설계 결과 정규화 완료 · 보완 필요 {len(gaps)}건 · 카탈로그 후보 {len(registry)}개 · "
            f"LLM 선별 후보 {len(shortlisted_candidates)}개 · 실제 적용 권고 {len(catalog_application['selected'])}개"
        )
        return Data(data=result)
