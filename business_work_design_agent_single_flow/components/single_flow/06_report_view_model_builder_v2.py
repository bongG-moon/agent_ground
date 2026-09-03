from __future__ import annotations

"""Project a validated business-design result into the renderer's v2 view model.

This custom component is self-contained by design.  It only projects the
normalizer result; it never calls an LLM, a database, or another local module.
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


_SCHEMA = "report-view-model/v2"
_RENDERER = "business-report-renderer.v2"
_RESULT_SCHEMA = "business-design-result/v2"
_IO_PLAN_SCHEMA = "langflow-implementation-io-plan/v1"
_LANGFLOW_VERSION = "1.11.0"
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_SECRET_KEY = re.compile(r"(?i)(?:api[_-]?key|authorization|bearer|client[_-]?secret|cookie|credential|password|passwd|private[_-]?key|secret|token)")
_SECRET_VALUE = re.compile(r"(?i)(?:\b(?:api[_-]?key|password|passwd|secret|access[_-]?token|authorization)\s*[:=]\s*[^\s,;]{8,}|\bbearer\s+\S{8,}|\bsk-[A-Za-z0-9_-]{16,}\b|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")
_PORT_TYPES = {"Message", "Data", "DataFrame", "환경 설정"}
_EDGE_KINDS = {"control", "branch", "error", "retry"}


def _safe_json(value: Any, path: str = "$") -> Any:
    data = getattr(value, "data", None)
    if isinstance(data, (dict, list)):
        return _safe_json(data, path)
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"[REPORT_VIEW_MODEL_INVALID] {path}에 유한하지 않은 숫자가 있습니다.")
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"[REPORT_VIEW_MODEL_INVALID] {path}에 유한하지 않은 숫자가 있습니다.")
        return value
    if isinstance(value, (tuple, set)):
        return [_safe_json(item, f"{path}[]") for item in value]
    if isinstance(value, list):
        return [_safe_json(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, dict):
        return {str(key): _safe_json(item, f"{path}.{key}") for key, item in value.items()}
    raise ValueError(f"[REPORT_VIEW_MODEL_INVALID] {path}의 값 형식을 처리할 수 없습니다.")


def _canonical(value: Any) -> str:
    return json.dumps(_safe_json(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text(value: Any, limit: int = 20_000) -> str:
    return str(value or "").replace("\x00", "").strip()[:limit]


def _strings(value: Any, limit: int = 100) -> list[str]:
    return [_text(item, 5_000) for item in value[:limit] if _text(item, 5_000)] if isinstance(value, list) else []


def _contains_secret(value: Any) -> bool:
    if isinstance(value, dict):
        return any((_SECRET_KEY.search(str(key)) and item not in (None, "", False, "[REDACTED]")) or _contains_secret(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    return isinstance(value, str) and value != "[REDACTED]" and bool(_SECRET_VALUE.search(value))


def _payload(value: Any) -> dict[str, Any]:
    raw = getattr(value, "data", None)
    if isinstance(raw, dict):
        value = raw
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("[REPORT_VIEW_MODEL_INVALID] 설계 결과가 JSON object가 아닙니다. 05 node 연결을 확인해 주세요.") from exc
    if not isinstance(value, dict):
        raise ValueError("[REPORT_VIEW_MODEL_INVALID] 설계 결과가 없습니다. 05 node 연결을 확인해 주세요.")
    return _safe_json(value, "design_result")


def _fact(label: str, value: Any, source: str = "analysis") -> dict[str, str]:
    return {"label": _text(label, 120), "value": _text(value, 5_000), "source": source if source in {"description", "analysis", "catalog", "assumption"} else "analysis"}


def _block(summary: Any = "", facts: list[dict[str, str]] | None = None, bullets: Any = None) -> dict[str, Any]:
    return {"summary": _text(summary), "facts": (facts or [])[:100], "bullets": _strings(bullets)}


def _catalog_url(asset_id: str, asset_type: str) -> str:
    return f"https://agent-hub.skhynix.com/#/{'flow' if asset_type == 'flow' else 'component'}/{asset_id}"


def _safe_catalog_item(raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    asset_id = _text(raw.get("asset_id"), 64).lower()
    asset_type = "flow" if _text(raw.get("asset_type"), 64).casefold() == "flow" else "component"
    if _UUID.fullmatch(asset_id) is None:
        raise ValueError("[REPORT_VIEW_MODEL_INVALID] 카탈로그 자산 ID가 표준 UUID가 아닙니다. 05 정규화 결과를 확인해 주세요.")
    return {
        "asset_id": asset_id,
        "version": _text(raw.get("version") or "unknown", 100) or "unknown",
        "title": _text(raw.get("title") or "카탈로그 자산", 500),
        "asset_type": asset_type,
        "technical_contract_status": _text(raw.get("technical_contract_status") or "unknown", 64),
        "catalog_url": _catalog_url(asset_id, asset_type),
        "target_node_ids": [_text(item, 128) for item in (raw.get("target_node_ids") or [])[:100] if _text(item, 128)],
        "reason": _text(raw.get("reason"), 5_000),
        "required_verification": _strings(raw.get("required_verification")),
        "decision_source": "llm" if raw.get("decision_source") == "llm" else "default_fill",
    }


def _catalog_recommendations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the small, reader-facing subset for a Flow-node detail drawer.

    The complete, traceable catalog record remains in ``catalog_application_plan``.
    Node details deliberately carry only the title, type/version, safe Agent Hub
    URL, selection reason, and pre-connection checks.  The asset ID remains only
    as an internal URL-validation binding; the renderer must never display it in
    the reader-facing drawer.  This prevents technical metadata from becoming a
    raw JSON block for users.
    """
    return [
        {
            "asset_id": item["asset_id"],
            "title": item["title"],
            "asset_type": item["asset_type"],
            "version": item["version"],
            "catalog_url": item["catalog_url"],
            "reason": item["reason"],
            "required_verification": item["required_verification"],
        }
        for item in items
    ]


def _safe_plan_text(value: Any, fallback: str, limit: int = 500) -> tuple[str, bool]:
    """Return a display-safe port label and whether it denotes a hidden setting.

    The design-result contract carries free-form model text.  A port plan must
    never turn a pasted object or a credential-looking label into drawer text,
    so non-string and object-looking entries become a short implementation
    placeholder.  The original design result remains the authoritative source
    for the next authoring pass; this reader-facing plan stays closed and safe.
    """
    if not isinstance(value, str):
        return fallback, False
    text = re.sub(r"\s+", " ", value.replace("\x00", "")).strip()[:limit]
    if not text or text.startswith(("{", "[")):
        return fallback, False
    if _SECRET_KEY.search(text) or _SECRET_VALUE.search(text):
        return "비공개 환경 설정값", True
    return text, False


def _reader_labels(value: Any, *, limit: int = 3) -> list[str]:
    """Return a short, safe, de-duplicated list for reader-facing prose.

    Node summaries and description-derived port labels both originate from the
    model response.  The detailed drawer must remain useful without repeating
    raw objects, long configuration values, or secret-looking strings.  This
    helper intentionally uses the same sanitisation rule as the Langflow I/O
    blueprint so the narrative and its port cards cannot disagree about what
    is safe to show.
    """
    values = value if isinstance(value, list) else []
    labels: list[str] = []
    for item in values:
        label, configuration = _safe_plan_text(item, "", limit=240)
        if not label or configuration or label in labels:
            continue
        labels.append(label)
        if len(labels) >= limit:
            break
    return labels


def _reader_label_text(value: Any, *, limit: int = 3) -> str:
    return ", ".join(_reader_labels(value, limit=limit))


def _concrete_current_work(
    *,
    node: dict[str, Any],
    detail: dict[str, Any],
    input_labels: list[str],
    output_labels: list[str],
    source_titles: list[str],
    target_titles: list[str],
) -> str:
    """Turn a terse node summary into a compact execution narrative.

    The report reader should be able to answer four practical questions from
    the first drawer block: what the stage does, what it receives, what it
    produces, and where that result goes.  This is explanatory text only;
    exact Langflow port IDs/types stay in ``implementation_io_plan`` below.
    """
    safe_title, title_is_configuration = _safe_plan_text(node.get("title"), "이 업무 단계", limit=300)
    if title_is_configuration:
        safe_title = "이 업무 단계"
    summary, summary_is_configuration = _safe_plan_text(
        detail.get("current_work"),
        f"{safe_title}에서 필요한 업무를 처리합니다.",
        limit=3_500,
    )
    if summary_is_configuration:
        summary = f"{safe_title}에서 필요한 업무를 처리합니다."

    input_text = ", ".join(input_labels[:3])
    output_text = ", ".join(output_labels[:3])
    source_text = ", ".join(source_titles[:2])
    target_text = ", ".join(target_titles[:3])

    # Use explicit input/result sentences rather than Korean object-particle
    # placeholders such as ``을(를)``.  Port labels are free-form, so a fixed
    # particle is often grammatically wrong and makes an otherwise practical
    # implementation description harder to scan.
    input_source = f"앞 단계 {source_text}에서 전달된" if source_text else "실행 시 제공되는"
    if input_text and output_text:
        if input_text == output_text:
            context = (
                f"{input_source} 입력값은 ‘{input_text}’입니다. "
                "이를 다음 단계에서 사용할 수 있도록 정리합니다."
            )
        else:
            context = f"{input_source} 입력값은 ‘{input_text}’입니다. 처리 결과는 ‘{output_text}’입니다."
    elif input_text:
        context = f"{input_source} 입력값은 ‘{input_text}’입니다. 이를 확인·처리합니다."
    elif output_text:
        context = f"이 단계의 처리 결과는 ‘{output_text}’입니다."
    else:
        context = ""

    if target_text:
        handoff = f"완료 후 결과를 {target_text} 단계에 전달합니다."
    elif output_text:
        handoff = "완료 후 결과를 사용자에게 표시하거나 다음 Flow에 전달합니다."
    else:
        handoff = ""
    return " ".join(part for part in (summary, context, handoff) if part)[:5_000]


def _enrich_current_work_descriptions(
    *,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    details: dict[str, dict[str, Any]],
) -> None:
    """Enrich every detail after graph edges and TO-BE I/O plans are known."""
    node_by_id = {node["node_id"]: node for node in nodes}
    incoming: dict[str, list[dict[str, Any]]] = {node["node_id"]: [] for node in nodes}
    outgoing: dict[str, list[dict[str, Any]]] = {node["node_id"]: [] for node in nodes}
    for edge in edges:
        source = edge.get("source_node_id")
        target = edge.get("target_node_id")
        if source in node_by_id and target in node_by_id:
            outgoing[source].append(edge)
            incoming[target].append(edge)

    for node in nodes:
        detail = details.get(node["detail_ref"])
        if not isinstance(detail, dict):
            continue
        plan = detail.get("implementation_io_plan")
        if isinstance(plan, dict):
            input_records = plan.get("inputs") if isinstance(plan.get("inputs"), list) else []
            output_records = plan.get("outputs") if isinstance(plan.get("outputs"), list) else []
            external_records = plan.get("external_inputs") if isinstance(plan.get("external_inputs"), list) else []
            # ``inputs`` normally includes every external input already.  Keep
            # ``external_inputs`` in the projection as well so a future plan
            # variant cannot accidentally make the start-node narrative omit
            # the value a person supplies when the Flow starts.
            input_labels = _reader_labels(
                [
                    item.get("label")
                    for item in [*input_records, *external_records]
                    if isinstance(item, dict) and item.get("data_type") != "환경 설정"
                ]
            )
            output_labels = _reader_labels(
                [item.get("label") for item in output_records if isinstance(item, dict) and not item.get("configuration")]
            )
            source_titles = _reader_labels(
                [
                    item.get("source_node_title")
                    for item in input_records
                    if isinstance(item, dict) and item.get("binding_kind") == "upstream_output"
                ],
                limit=2,
            )
            target_titles = _reader_labels(
                [
                    binding.get("target_node_title")
                    for item in output_records
                    if isinstance(item, dict)
                    for binding in (item.get("downstream_bindings") if isinstance(item.get("downstream_bindings"), list) else [])
                    if isinstance(binding, dict) and binding.get("binding_kind") == "downstream_input"
                ]
            )
        else:
            input_labels = _reader_labels(detail.get("inputs"))
            output_labels = _reader_labels(detail.get("outputs"))
            source_titles = _reader_labels(
                [node_by_id[edge["source_node_id"]]["title"] for edge in incoming[node["node_id"]]],
                limit=2,
            )
            target_titles = _reader_labels(
                [node_by_id[edge["target_node_id"]]["title"] for edge in outgoing[node["node_id"]]]
            )
        detail["current_work"] = _concrete_current_work(
            node=node,
            detail=detail,
            input_labels=input_labels,
            output_labels=output_labels,
            source_titles=source_titles,
            target_titles=target_titles,
        )


def _type_label(data_type: str) -> str:
    return {
        "Message": "Message · 대화/설명 텍스트",
        "Data": "Data · 구조화된 업무 데이터",
        "DataFrame": "DataFrame · 표 형태의 데이터",
        "환경 설정": "환경 설정 · 캔버스 연결 없이 별도 설정",
    }[data_type]


def _infer_port_type(label: str, *, node_kind: str, direction: str, configuration: bool = False) -> str:
    """Use a deliberately small, Langflow 1.11-friendly type vocabulary.

    This is a blueprint recommendation, not a claim about a catalog runtime
    port.  Structured Data is the safe default; Message is reserved for human
    text and DataFrame for explicitly tabular payloads.
    """
    if configuration:
        return "환경 설정"
    lowered = label.casefold()
    if any(token in lowered for token in ("표", "테이블", "dataframe", "행 목록", "row list")):
        return "DataFrame"
    if node_kind == "start" and direction in {"input", "in"}:
        return "Message"
    if any(token in lowered for token in ("업무 설명", "자연어", "메시지", "질문", "답변", "프롬프트", "알림", "코멘트", "지시")):
        return "Message"
    if node_kind in {"end", "exception"} and direction in {"output", "out"}:
        return "Message"
    return "Data"


def _component_type(node: dict[str, Any]) -> str:
    """A concise implementation recommendation for a Langflow 1.11 canvas."""
    kind = _text(node.get("node_kind"), 64)
    source = _text(node.get("implementation_source"), 64)
    if kind == "start":
        return "Chat Input"
    if kind == "end":
        return "Chat Output"
    if kind == "human_review" or source == "human_task":
        return "Chat Input 또는 Form 입력 + 조건 분기"
    if kind == "decision":
        return "조건 분기 Component"
    if source == "catalog_component":
        return "카탈로그 Component"
    if source == "catalog_flow":
        return "카탈로그 Flow 연결"
    if source == "new_component":
        return "Standalone Custom Component"
    if source == "external_service":
        return "Standalone Custom Component + 외부 API"
    return "기본 Component 조합"


def _external_input_node_type(data_type: str) -> tuple[str, str]:
    if data_type == "Message":
        return "Chat Input", "input_value"
    if data_type == "DataFrame":
        return "DataFrame Input", "input_value"
    if data_type == "환경 설정":
        return "SecretStrInput", "value"
    return "Data Input", "input_value"


def _terminal_output_node_type(data_type: str) -> tuple[str, str, str]:
    if data_type == "Message":
        return "Chat Output", "inputs", "Message"
    if data_type == "DataFrame":
        return "Data Output", "input_value", "DataFrame"
    return "Data Output", "input_value", "Data"


def _port_id(node_id: str, direction: str, index: int) -> str:
    return f"{node_id}:{direction}:{index}"


def _new_port(
    *,
    node_id: str,
    direction: str,
    index: int,
    label: str,
    node_kind: str,
    definition_source: str,
    configuration: bool = False,
    required: bool = True,
) -> dict[str, Any]:
    data_type = _infer_port_type(label, node_kind=node_kind, direction=direction, configuration=configuration)
    return {
        "port_id": _port_id(node_id, direction, index),
        "label": label,
        "data_type": data_type,
        "type_label": _type_label(data_type),
        "required": bool(required),
        "definition_source": definition_source,
        "configuration": bool(configuration),
    }


def _declared_port_labels(raw: Any, *, prefix: str) -> list[tuple[str, bool]]:
    values = raw if isinstance(raw, list) else []
    result: list[tuple[str, bool]] = []
    for index, value in enumerate(values[:100], start=1):
        label, configuration = _safe_plan_text(value, f"{prefix} {index}")
        if (label, configuration) not in result:
            result.append((label, configuration))
    return result


def _generated_output_label(node: dict[str, Any], edge: dict[str, Any], index: int) -> tuple[str, bool]:
    kind = _text(node.get("node_kind"), 64)
    edge_kind = _text(edge.get("edge_kind"), 32)
    label, _ = _safe_plan_text(edge.get("label"), "다음")
    if kind == "start":
        return "업무 설명", False
    if kind == "human_review" or edge_kind == "branch":
        return f"{label} 판단 결과", False
    if edge_kind == "error":
        return "오류 처리 정보", False
    if edge_kind == "retry":
        return "재시도 요청 정보", False
    return f"{label} 전달 데이터" if label != "다음" else f"다음 단계 전달 데이터 {index}", False


def _generated_input_label(edge: dict[str, Any], index: int) -> tuple[str, bool]:
    label, _ = _safe_plan_text(edge.get("label"), "이전 단계")
    if label == "다음":
        return f"이전 단계 결과 {index}", False
    return f"{label} 결과", False


def _edge_matches_declared_input(
    edge: dict[str, Any],
    source_port: dict[str, Any],
    candidate_input: dict[str, Any],
) -> bool:
    """Only bind an edge to a named input when the labels support that claim.

    The normalized design contract has business-level labels but not verified
    Langflow handles.  Positional binding (first edge -> first declared input)
    can produce misleading plans such as ``업무 설명 -> 기간``.  When the
    edge/source label and the declared input have no meaningful overlap, keep
    that declared input as an external Canvas input and create a clearly
    labelled edge-derived handoff instead.
    """

    if candidate_input.get("configuration"):
        return False
    target_label = _text(candidate_input.get("label"), 500).casefold()
    source_label = _text(source_port.get("label"), 500).casefold()
    edge_label, _ = _safe_plan_text(edge.get("label"), "")
    edge_label = edge_label.casefold()
    if not target_label:
        return False
    if target_label == source_label or target_label == edge_label:
        return True
    target_tokens = set(re.findall(r"[0-9a-z가-힣]{2,}", target_label))
    context_tokens = set(re.findall(r"[0-9a-z가-힣]{2,}", f"{source_label} {edge_label}"))
    return bool(target_tokens & context_tokens)


def _node_plan_status(catalog_items: list[dict[str, Any]]) -> tuple[str, str, str]:
    statuses = {_text(item.get("technical_contract_status"), 64) for item in catalog_items}
    if "metadata_only" in statuses:
        return (
            "METADATA_ONLY",
            "설계 초안 · 카탈로그 실제 포트 확인 필요",
            "선택된 카탈로그가 설명 기반 후보입니다. 아래 포트 ID와 유형은 Langflow 1.11 구현 청사진이며, Agent Hub에서 실제 입력·출력·권한 계약을 확인한 뒤 확정하세요.",
        )
    if statuses:
        return (
            "CATALOG_REFERENCE",
            "설계 초안 · 카탈로그 계약 이력 참고",
            "카탈로그의 기술 계약 이력은 참고할 수 있지만, 이 업무 Flow에 연결하는 포트 매핑은 아직 구현 청사진입니다. 실제 Canvas 연결 전에 자산의 최신 계약을 확인하세요.",
        )
    return (
        "BLUEPRINT",
        "설계 초안 · 구현 전 포트 정의 필요",
        "업무 설명과 Flow 연결을 바탕으로 만든 Langflow 1.11 구현 청사진입니다. 실제 Component 입력·출력 포트는 구현 시 확정하세요.",
    )


def _implementation_note(node: dict[str, Any]) -> str:
    kind = _text(node.get("node_kind"), 64)
    source = _text(node.get("implementation_source"), 64)
    if kind == "human_review" or source == "human_task":
        return "실행 중 Human Input pause를 전제하지 않습니다. 검토 결정은 Chat Input·Form 또는 외부 승인 결과를 입력으로 받아 새 실행에서 처리하도록 설계합니다."
    if source == "new_component":
        return "신규 기능은 Langflow 1.11 Standalone Custom Component로 만들고, Data 입력·출력 계약을 명시한 뒤 단위 테스트로 확인합니다."
    if source == "external_service":
        return "외부 API 호출은 Standalone Custom Component에 분리하고, 비공개 환경 설정값은 Canvas 연결이 아닌 환경 설정으로 관리합니다."
    if source in {"catalog_component", "catalog_flow"}:
        return "카탈로그 자산의 최신 포트·권한 계약을 확인한 뒤 아래 청사진의 입력·출력 이름에 맞게 매핑합니다."
    return "구현 시 아래 Data/Message 계약을 기준으로 기본 Component를 조합하고, 연결 전후 타입이 달라지면 Type Convert를 명시적으로 둡니다."


def _validate_io_text(value: Any, field: str, *, allow_empty: bool = False) -> None:
    if not isinstance(value, str) or (not allow_empty and not value.strip()) or len(value) > 5_000:
        raise ValueError(f"[REPORT_VIEW_MODEL_INVALID] 구현 I/O 계획의 {field} 형식이 유효하지 않습니다.")
    if _SECRET_VALUE.search(value):
        raise ValueError(f"[REPORT_VIEW_MODEL_INVALID] 구현 I/O 계획의 {field}에 민감정보로 의심되는 값이 있습니다.")


def _validate_implementation_io_plan(plan: Any) -> None:
    """Validate the closed, deterministic plan before it reaches the renderer."""
    if not isinstance(plan, dict):
        raise ValueError("[REPORT_VIEW_MODEL_INVALID] 구현 I/O 계획이 object가 아닙니다.")
    expected = {
        "schema_version", "langflow_version", "plan_status", "plan_status_label", "plan_note",
        "component_type", "implementation_note", "inputs", "external_inputs", "outputs",
    }
    if set(plan) != expected:
        raise ValueError("[REPORT_VIEW_MODEL_INVALID] 구현 I/O 계획 필드가 닫힌 계약과 일치하지 않습니다.")
    if plan["schema_version"] != _IO_PLAN_SCHEMA or plan["langflow_version"] != _LANGFLOW_VERSION:
        raise ValueError("[REPORT_VIEW_MODEL_INVALID] 구현 I/O 계획 버전이 유효하지 않습니다.")
    if plan["plan_status"] not in {"BLUEPRINT", "METADATA_ONLY", "CATALOG_REFERENCE"}:
        raise ValueError("[REPORT_VIEW_MODEL_INVALID] 구현 I/O 계획 상태가 유효하지 않습니다.")
    for field in ("plan_status_label", "plan_note", "component_type", "implementation_note"):
        _validate_io_text(plan[field], field)
    if not isinstance(plan["inputs"], list) or not isinstance(plan["external_inputs"], list) or not isinstance(plan["outputs"], list):
        raise ValueError("[REPORT_VIEW_MODEL_INVALID] 구현 I/O 계획의 포트 목록이 배열이 아닙니다.")
    if len(plan["inputs"]) > 100 or len(plan["outputs"]) > 100 or len(plan["external_inputs"]) > 100:
        raise ValueError("[REPORT_VIEW_MODEL_INVALID] 구현 I/O 계획의 포트 수가 한도를 초과했습니다.")

    input_keys = {
        "port_id", "label", "data_type", "type_label", "required", "definition_source", "configuration",
        "binding_kind", "source_node_id", "source_node_title", "source_output_port_id", "source_output_label",
        "source_output_data_type", "connection_label", "note",
    }
    output_keys = {"port_id", "label", "data_type", "type_label", "required", "definition_source", "configuration", "note", "downstream_bindings"}
    external_keys = {"input_port_id", "label", "data_type", "type_label", "required", "recommended_node_type", "recommended_input_name", "note"}
    binding_keys = {
        "binding_kind", "target_node_id", "target_node_title", "target_input_port_id", "target_input_label",
        "target_input_data_type", "target_node_type", "edge_label", "edge_kind", "connection_label", "note",
    }
    seen_input_ids: set[str] = set()
    for port in plan["inputs"]:
        if not isinstance(port, dict) or set(port) != input_keys:
            raise ValueError("[REPORT_VIEW_MODEL_INVALID] 구현 I/O 입력 포트 계약이 유효하지 않습니다.")
        if port["data_type"] not in _PORT_TYPES or port["type_label"] != _type_label(port["data_type"]):
            raise ValueError("[REPORT_VIEW_MODEL_INVALID] 구현 I/O 입력 포트 type이 유효하지 않습니다.")
        if port["binding_kind"] not in {"upstream_output", "external_input", "configuration"}:
            raise ValueError("[REPORT_VIEW_MODEL_INVALID] 구현 I/O 입력 연결 종류가 유효하지 않습니다.")
        if not isinstance(port["required"], bool) or not isinstance(port["configuration"], bool):
            raise ValueError("[REPORT_VIEW_MODEL_INVALID] 구현 I/O 입력 필수 여부가 유효하지 않습니다.")
        for field in ("port_id", "label", "definition_source", "source_node_id", "source_node_title", "source_output_port_id", "source_output_label", "source_output_data_type", "connection_label", "note"):
            _validate_io_text(port[field], f"input.{field}", allow_empty=field.startswith("source_"))
        if port["port_id"] in seen_input_ids:
            raise ValueError("[REPORT_VIEW_MODEL_INVALID] 구현 I/O 입력 포트 ID가 중복됩니다.")
        seen_input_ids.add(port["port_id"])

    seen_output_ids: set[str] = set()
    for port in plan["outputs"]:
        if not isinstance(port, dict) or set(port) != output_keys:
            raise ValueError("[REPORT_VIEW_MODEL_INVALID] 구현 I/O 출력 포트 계약이 유효하지 않습니다.")
        if port["data_type"] not in _PORT_TYPES or port["type_label"] != _type_label(port["data_type"]):
            raise ValueError("[REPORT_VIEW_MODEL_INVALID] 구현 I/O 출력 포트 type이 유효하지 않습니다.")
        if not isinstance(port["required"], bool) or not isinstance(port["configuration"], bool) or not isinstance(port["downstream_bindings"], list):
            raise ValueError("[REPORT_VIEW_MODEL_INVALID] 구현 I/O 출력 포트 형식이 유효하지 않습니다.")
        for field in ("port_id", "label", "definition_source", "note"):
            _validate_io_text(port[field], f"output.{field}")
        if port["port_id"] in seen_output_ids:
            raise ValueError("[REPORT_VIEW_MODEL_INVALID] 구현 I/O 출력 포트 ID가 중복됩니다.")
        seen_output_ids.add(port["port_id"])
        for binding in port["downstream_bindings"]:
            if not isinstance(binding, dict) or set(binding) != binding_keys:
                raise ValueError("[REPORT_VIEW_MODEL_INVALID] 구현 I/O 다음 단계 연결 계약이 유효하지 않습니다.")
            if binding["binding_kind"] not in {"downstream_input", "external_output"} or binding["edge_kind"] not in _EDGE_KINDS:
                raise ValueError("[REPORT_VIEW_MODEL_INVALID] 구현 I/O 다음 단계 연결 종류가 유효하지 않습니다.")
            if binding["target_input_data_type"] not in _PORT_TYPES:
                raise ValueError("[REPORT_VIEW_MODEL_INVALID] 구현 I/O 다음 단계 입력 type이 유효하지 않습니다.")
            for field in ("target_node_id", "target_node_title", "target_input_port_id", "target_input_label", "target_input_data_type", "target_node_type", "edge_label", "edge_kind", "connection_label", "note"):
                _validate_io_text(binding[field], f"binding.{field}")

    external_port_ids = set()
    for item in plan["external_inputs"]:
        if not isinstance(item, dict) or set(item) != external_keys:
            raise ValueError("[REPORT_VIEW_MODEL_INVALID] 구현 I/O 외부 입력 계약이 유효하지 않습니다.")
        if item["data_type"] not in _PORT_TYPES or item["type_label"] != _type_label(item["data_type"]):
            raise ValueError("[REPORT_VIEW_MODEL_INVALID] 구현 I/O 외부 입력 type이 유효하지 않습니다.")
        if not isinstance(item["required"], bool) or item["input_port_id"] not in seen_input_ids:
            raise ValueError("[REPORT_VIEW_MODEL_INVALID] 구현 I/O 외부 입력 포트 연결이 유효하지 않습니다.")
        if item["input_port_id"] in external_port_ids:
            raise ValueError("[REPORT_VIEW_MODEL_INVALID] 구현 I/O 외부 입력 포트가 중복됩니다.")
        external_port_ids.add(item["input_port_id"])
        for field in ("input_port_id", "label", "recommended_node_type", "recommended_input_name", "note"):
            _validate_io_text(item[field], f"external.{field}")


def _attach_implementation_io_plans(
    *,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    details: dict[str, dict[str, Any]],
    raw_nodes: dict[str, dict[str, Any]],
    catalog_by_node: dict[str, list[dict[str, Any]]],
) -> None:
    """Attach one closed Langflow 1.11 I/O blueprint to every TO-BE node.

    The upstream result schema exposes business-level ``inputs``/``outputs``
    but no verified component-port map.  We therefore generate stable port IDs
    and bind every graph edge to them deterministically.  The status/note makes
    that distinction explicit, especially for metadata-only catalog assets.
    """
    node_by_id = {node["node_id"]: node for node in nodes}
    ordered_nodes = sorted(nodes, key=lambda item: (item["sequence"], item["node_id"]))
    node_index = {node["node_id"]: index for index, node in enumerate(ordered_nodes)}
    ordered_edges = sorted(
        edges,
        key=lambda item: (
            node_index.get(item["source_node_id"], 10_000),
            node_index.get(item["target_node_id"], 10_000),
            item["edge_id"],
        ),
    )
    incoming: dict[str, list[dict[str, Any]]] = {node["node_id"]: [] for node in nodes}
    outgoing: dict[str, list[dict[str, Any]]] = {node["node_id"]: [] for node in nodes}
    for edge in ordered_edges:
        outgoing[edge["source_node_id"]].append(edge)
        incoming[edge["target_node_id"]].append(edge)

    state: dict[str, dict[str, Any]] = {}
    for node in ordered_nodes:
        node_id = node["node_id"]
        raw = raw_nodes.get(node_id, {})
        kind = node["node_kind"]
        input_ports = [
            _new_port(
                node_id=node_id,
                direction="in",
                index=index,
                label=label,
                node_kind=kind,
                definition_source="description",
                configuration=configuration,
            )
            for index, (label, configuration) in enumerate(_declared_port_labels(raw.get("inputs"), prefix="입력 데이터"), start=1)
        ]
        output_ports = [
            _new_port(
                node_id=node_id,
                direction="out",
                index=index,
                label=label,
                node_kind=kind,
                definition_source="description",
                configuration=configuration,
            )
            for index, (label, configuration) in enumerate(_declared_port_labels(raw.get("outputs"), prefix="출력 데이터"), start=1)
        ]
        for edge_index, edge in enumerate(outgoing[node_id], start=1):
            if edge_index <= len(output_ports):
                continue
            label, configuration = _generated_output_label(node, edge, edge_index)
            output_ports.append(
                _new_port(
                    node_id=node_id,
                    direction="out",
                    index=len(output_ports) + 1,
                    label=label,
                    node_kind=kind,
                    definition_source="edge_derived",
                    configuration=configuration,
                )
            )
        if not output_ports and not outgoing[node_id] and kind in {"end", "exception"}:
            label = "최종 사용자 안내" if kind == "end" else "오류 안내"
            output_ports.append(
                _new_port(
                    node_id=node_id,
                    direction="out",
                    index=1,
                    label=label,
                    node_kind=kind,
                    definition_source="implementation_proposal",
                )
            )
        state[node_id] = {"inputs": input_ports, "outputs": output_ports, "input_edge_port": {}, "output_edge_port": {}}

    # Assign source ports first so an edge-derived target input inherits the
    # source's intended Langflow type instead of arbitrarily becoming Data.
    for node in ordered_nodes:
        node_id = node["node_id"]
        for edge_index, edge in enumerate(outgoing[node_id], start=1):
            state[node_id]["output_edge_port"][edge["edge_id"]] = state[node_id]["outputs"][edge_index - 1]

    bindings: list[dict[str, Any]] = []
    for node in ordered_nodes:
        node_id = node["node_id"]
        for edge_index, edge in enumerate(incoming[node_id], start=1):
            source_port = state[edge["source_node_id"]]["output_edge_port"][edge["edge_id"]]
            target_ports = state[node_id]["inputs"]
            used_port_ids = {
                port["port_id"]
                for port in state[node_id]["input_edge_port"].values()
            }
            target_port = next(
                (
                    port
                    for port in target_ports
                    if port["port_id"] not in used_port_ids
                    and _edge_matches_declared_input(edge, source_port, port)
                ),
                None,
            )
            if target_port is None:
                label, configuration = _generated_input_label(edge, edge_index)
                target_port = _new_port(
                    node_id=node_id,
                    direction="in",
                    index=len(target_ports) + 1,
                    label=label,
                    node_kind=node["node_kind"],
                    definition_source="edge_derived",
                    configuration=configuration,
                )
                target_port["data_type"] = source_port["data_type"]
                target_port["type_label"] = _type_label(source_port["data_type"])
                target_ports.append(target_port)
            state[node_id]["input_edge_port"][edge["edge_id"]] = target_port
            bindings.append({"edge": edge, "source_port": source_port, "target_port": target_port})

    # A declared port may fan out to several targets.  When types disagree,
    # a structured Data handoff is the explicit, safe Langflow 1.11 fallback.
    changed = True
    while changed:
        changed = False
        for key in ("source_port", "target_port"):
            grouped: dict[str, list[dict[str, Any]]] = {}
            for binding in bindings:
                grouped.setdefault(binding[key]["port_id"], []).append(binding)
            for group in grouped.values():
                types = {binding["source_port"]["data_type"] for binding in group} | {binding["target_port"]["data_type"] for binding in group}
                expected = next(iter(types)) if len(types) == 1 else "Data"
                for binding in group:
                    for port_key in ("source_port", "target_port"):
                        port = binding[port_key]
                        if port["data_type"] != expected:
                            port["data_type"] = expected
                            port["type_label"] = _type_label(expected)
                            changed = True

    for node in ordered_nodes:
        node_id = node["node_id"]
        if not state[node_id]["inputs"] and not incoming[node_id]:
            label = "업무 설명" if node["node_kind"] == "start" else "구현에 필요한 외부 입력"
            state[node_id]["inputs"].append(
                _new_port(
                    node_id=node_id,
                    direction="in",
                    index=1,
                    label=label,
                    node_kind=node["node_kind"],
                    definition_source="implementation_proposal",
                )
            )

    # Build the renderer-facing records only after all type reconciliation is
    # complete, so each "A.Output → B.Input" line is internally consistent.
    for node in ordered_nodes:
        node_id = node["node_id"]
        catalog_items = catalog_by_node.get(node_id, [])
        plan_status, status_label, plan_note = _node_plan_status(catalog_items)
        input_records: list[dict[str, Any]] = []
        external_inputs: list[dict[str, Any]] = []
        input_bindings = {binding["target_port"]["port_id"]: binding for binding in bindings if binding["edge"]["target_node_id"] == node_id}
        for port in state[node_id]["inputs"]:
            binding = input_bindings.get(port["port_id"])
            if binding is not None:
                source_node = node_by_id[binding["edge"]["source_node_id"]]
                source_port = binding["source_port"]
                connection_label = (
                    f"{source_node['title']} · {source_port['label']} [{source_port['data_type']}] "
                    f"→ {node['title']} · {port['label']} [{port['data_type']}]"
                )
                input_records.append(
                    {
                        **port,
                        "binding_kind": "upstream_output",
                        "source_node_id": source_node["node_id"],
                        "source_node_title": source_node["title"],
                        "source_output_port_id": source_port["port_id"],
                        "source_output_label": source_port["label"],
                        "source_output_data_type": source_port["data_type"],
                        "connection_label": connection_label,
                        "note": "그래프 edge를 기준으로 생성한 연결 청사진입니다.",
                    }
                )
                continue
            recommended_node_type, recommended_input_name = _external_input_node_type(port["data_type"])
            binding_kind = "configuration" if port["data_type"] == "환경 설정" else "external_input"
            note = (
                "Canvas 연결 포트가 아니라 환경 설정에서 비공개 값으로 지정합니다."
                if binding_kind == "configuration"
                else "앞 단계 연결이 없으므로 이 Flow 실행 시 외부 입력으로 제공합니다."
            )
            input_records.append(
                {
                    **port,
                    "binding_kind": binding_kind,
                    "source_node_id": "",
                    "source_node_title": "외부 입력",
                    "source_output_port_id": "",
                    "source_output_label": "",
                    "source_output_data_type": "",
                    "connection_label": f"{recommended_node_type} · {recommended_input_name} → 이 단계 · {port['label']} [{port['data_type']}]",
                    "note": note,
                }
            )
            external_inputs.append(
                {
                    "input_port_id": port["port_id"],
                    "label": port["label"],
                    "data_type": port["data_type"],
                    "type_label": port["type_label"],
                    "required": port["required"],
                    "recommended_node_type": recommended_node_type,
                    "recommended_input_name": recommended_input_name,
                    "note": note,
                }
            )

        output_records: list[dict[str, Any]] = []
        for port in state[node_id]["outputs"]:
            downstream: list[dict[str, Any]] = []
            for binding in bindings:
                if binding["source_port"]["port_id"] != port["port_id"]:
                    continue
                edge = binding["edge"]
                target_node = node_by_id[edge["target_node_id"]]
                target_port = binding["target_port"]
                downstream.append(
                    {
                        "binding_kind": "downstream_input",
                        "target_node_id": target_node["node_id"],
                        "target_node_title": target_node["title"],
                        "target_input_port_id": target_port["port_id"],
                        "target_input_label": target_port["label"],
                        "target_input_data_type": target_port["data_type"],
                        "target_node_type": _component_type(target_node),
                        "edge_label": _safe_plan_text(edge.get("label"), "다음")[0],
                        "edge_kind": edge["edge_kind"] if edge["edge_kind"] in _EDGE_KINDS else "control",
                        "connection_label": (
                            f"이 단계 · {port['label']} [{port['data_type']}] → "
                            f"{target_node['title']} · {target_port['label']} [{target_port['data_type']}]"
                        ),
                        "note": "그래프 edge를 기준으로 생성한 연결 청사진입니다.",
                    }
                )
            if not downstream and not port["configuration"]:
                target_node_type, target_input_name, target_input_type = _terminal_output_node_type(port["data_type"])
                note = "종단 결과를 사용자에게 표시하거나 다음 Flow로 전달하기 위한 출력 노드 제안입니다."
                downstream.append(
                    {
                        "binding_kind": "external_output",
                        "target_node_id": f"external-output:{node_id}:{port['port_id']}",
                        "target_node_title": "종단 출력",
                        "target_input_port_id": target_input_name,
                        "target_input_label": target_input_name,
                        "target_input_data_type": target_input_type,
                        "target_node_type": target_node_type,
                        "edge_label": "최종 결과 전달",
                        "edge_kind": "control",
                        "connection_label": f"이 단계 · {port['label']} [{port['data_type']}] → {target_node_type} · {target_input_name} [{target_input_type}]",
                        "note": note,
                    }
                )
            output_records.append(
                {
                    **port,
                    "note": "아래 다음 단계 연결 또는 종단 출력 노드에 전달합니다.",
                    "downstream_bindings": downstream,
                }
            )

        plan = {
            "schema_version": _IO_PLAN_SCHEMA,
            "langflow_version": _LANGFLOW_VERSION,
            "plan_status": plan_status,
            "plan_status_label": status_label,
            "plan_note": plan_note,
            "component_type": _component_type(node),
            "implementation_note": _implementation_note(node),
            "inputs": input_records,
            "external_inputs": external_inputs,
            "outputs": output_records,
        }
        _validate_implementation_io_plan(plan)
        detail_ref = node["detail_ref"]
        details[detail_ref]["implementation_io_plan"] = plan


def _graph_projection(raw: Any, *, selected: list[dict[str, Any]], graph_name: str) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    selected_keys = {(item["asset_id"], item["version"]): item for item in selected}
    details: dict[str, dict[str, Any]] = {}
    nodes: list[dict[str, Any]] = []
    raw_nodes: dict[str, dict[str, Any]] = {}
    catalog_by_node: dict[str, list[dict[str, Any]]] = {}
    for index, node in enumerate(raw.get("nodes") if isinstance(raw.get("nodes"), list) else []):
        if not isinstance(node, dict):
            continue
        node_id = _text(node.get("node_id"), 128)
        if not node_id:
            continue
        refs: list[dict[str, str]] = []
        for ref in node.get("catalog_asset_refs") if isinstance(node.get("catalog_asset_refs"), list) else []:
            if not isinstance(ref, dict):
                continue
            key = (_text(ref.get("asset_id"), 64).lower(), _text(ref.get("version") or "unknown", 100) or "unknown")
            if key in selected_keys and {"asset_id": key[0], "version": key[1]} not in refs:
                refs.append({"asset_id": key[0], "version": key[1]})
        detail_ref = f"{graph_name}:{node_id}"
        catalog_info = [selected_keys[(ref["asset_id"], ref["version"])] for ref in refs]
        raw_nodes[node_id] = node
        catalog_by_node[node_id] = catalog_info
        # Keep the drawer deliberately task-oriented.  The graph node itself
        # supplies the title; technical fields and raw catalog objects remain in
        # the closed internal view model rather than becoming viewer-facing JSON.
        details[detail_ref] = {
            "current_work": _text(node.get("summary"), 5_000),
            "problems": _strings(node.get("problems")),
            "improvement": _text(node.get("improvement"), 5_000),
            "inputs": _strings(node.get("inputs")),
            "outputs": _strings(node.get("outputs")),
            "catalog_recommendations": _catalog_recommendations(catalog_info),
        }
        nodes.append({
            "node_id": node_id,
            "node_kind": _text(node.get("node_kind") or "work_step", 64),
            "title": _text(node.get("title") or f"업무 단계 {index + 1}", 500),
            "summary": _text(node.get("summary"), 5_000),
            "sequence": node.get("sequence") if isinstance(node.get("sequence"), int) else index,
            "implementation_source": _text(node.get("implementation_source") or "human_task", 64),
            "detail_ref": detail_ref,
            "catalog_refs": refs,
        })
    edges: list[dict[str, Any]] = []
    node_ids = {node["node_id"] for node in nodes}
    for index, edge in enumerate(raw.get("edges") if isinstance(raw.get("edges"), list) else []):
        if not isinstance(edge, dict):
            continue
        source = _text(edge.get("source_node_id"), 128)
        target = _text(edge.get("target_node_id"), 128)
        if source not in node_ids or target not in node_ids:
            continue
        edges.append({"edge_id": _text(edge.get("edge_id") or f"{graph_name}-edge-{index + 1}", 128), "source_node_id": source, "target_node_id": target, "edge_kind": _text(edge.get("edge_kind") or "control", 32), "label": _text(edge.get("label") or "다음", 500), "condition": _text(edge.get("condition"), 5_000), "is_default": bool(edge.get("is_default"))})
    if graph_name == "to-be":
        _attach_implementation_io_plans(
            nodes=nodes,
            edges=edges,
            details=details,
            raw_nodes=raw_nodes,
            catalog_by_node=catalog_by_node,
        )
    _enrich_current_work_descriptions(nodes=nodes, edges=edges, details=details)
    fallback = [f"{node['sequence'] + 1}. {node['title']}: {node['summary']}".strip() for node in sorted(nodes, key=lambda item: (item["sequence"], item["node_id"]))]
    return {"nodes": nodes, "edges": edges, "details": details, "text_fallback": fallback}


def _completion_label(status: str, gap_count: int) -> str:
    if status == "COMPLETED_WITH_GAPS" or gap_count:
        return "설계 초안 생성 · 보완 필요"
    return "설계 완료"


def _short_refinement_instruction(value: Any) -> str:
    """Return a single reader-safe line of the optional second-pass instruction.

    The original request remains available in the closed input contract.  The
    report only needs a short reminder of what the requester asked the final
    pass to emphasize; it must not render a dict/list or arbitrary internal
    execution payload as text.
    """
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value.replace("\x00", "")).strip()[:1_200]


def _refinement_summary(design: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    """Project an optional future refinement result into a tiny UI contract.

    A later Flow revision may add a second LLM pass under ``design.refinement``.
    This projection intentionally recognizes only its outcome state.  Provider
    names, prompts, raw quality findings, timings, and other implementation
    traces are deliberately not copied into the report view model.
    """
    raw = design.get("refinement") if isinstance(design.get("refinement"), dict) else {}
    raw_status = _text(raw.get("status"), 64).upper()
    if raw_status in {"APPLIED", "COMPLETED", "REFINED", "SUCCESS"}:
        status = "APPLIED"
    elif raw_status in {"SKIPPED", "FALLBACK", "FAILED", "NOT_APPLIED", "UNAVAILABLE"}:
        status = "SKIPPED"
    elif raw_status in {"", "NONE", "NOT_REQUESTED"}:
        status = "NONE"
    else:
        # An unrecognized result must never be presented as successfully
        # applied.  The base draft is the safe, still-usable fallback.
        status = "SKIPPED"

    instructions = _short_refinement_instruction(request.get("final_refinement_instructions"))
    instructions_provided = bool(instructions)
    if status == "APPLIED":
        summary = "초안 점검과 보완 지시를 반영해 최종 설계를 한 번 더 다듬었습니다."
        status_label = "보완 반영 완료"
    elif status == "SKIPPED":
        summary = "2차 보완 결과를 적용하지 못해 검증된 기본 초안을 기준으로 보고서를 작성했습니다."
        status_label = "기본 초안 사용"
    elif instructions_provided:
        summary = "보완 지시는 제공됐지만 2차 보완 결과가 없어 기본 초안을 기준으로 보고서를 작성했습니다."
        status_label = "기본 초안 사용"
    else:
        summary = "2차 보완 단계는 요청되지 않아 기본 초안을 기준으로 보고서를 작성했습니다."
        status_label = "기본 초안 사용"

    return {
        "status": status,
        "status_provided": bool(raw_status),
        "status_label": status_label,
        "summary": summary,
        "final_refinement_instructions_provided": instructions_provided,
        "final_refinement_instructions": instructions,
    }


class ReportViewModelBuilderV2Component(Component):
    """06. Build the closed, renderer-safe report-view-model/v2 contract."""

    display_name = "06 Report View Model 생성"
    description = "정규화된 업무 설계를 화면용 보고서 계약으로 결정론적으로 투영합니다."
    icon = "LayoutTemplate"
    name = "ReportViewModelBuilderV2"

    inputs = [DataInput(name="design_result", display_name="정규화 설계 결과", required=True)]
    outputs = [Output(name="report_view_model", display_name="Report View Model", method="build_view_model", types=["Data"])]

    def build_view_model(self) -> Data:
        design = _payload(self.design_result)
        if design.get("schema_version") != _RESULT_SCHEMA:
            raise ValueError("[REPORT_VIEW_MODEL_INVALID] business-design-result/v2가 필요합니다. 05 node의 출력을 연결해 주세요.")
        if _contains_secret(design):
            raise ValueError("[REPORT_VIEW_MODEL_INVALID] 설계 결과에 민감정보로 의심되는 값이 있습니다. 마스킹된 입력으로 다시 실행해 주세요.")
        analysis = design.get("work_analysis") if isinstance(design.get("work_analysis"), dict) else {}
        request = design.get("request") if isinstance(design.get("request"), dict) else {}
        application = design.get("catalog_application") if isinstance(design.get("catalog_application"), dict) else {}
        selected = [_safe_catalog_item(item) for item in application.get("selected", []) if isinstance(item, dict)]
        considered = [_safe_catalog_item(item) for item in application.get("considered", []) if isinstance(item, dict)]
        not_used = [_safe_catalog_item(item) for item in application.get("not_used", []) if isinstance(item, dict)]
        all_catalog = selected + considered + not_used
        seen = {(item["asset_id"], item["version"]) for item in all_catalog}
        if len(seen) != len(all_catalog):
            raise ValueError("[REPORT_VIEW_MODEL_INVALID] 카탈로그 적용 계획의 자산이 중복됩니다. 05 정규화 결과를 확인해 주세요.")
        gaps = [item for item in design.get("information_gaps", []) if isinstance(item, dict)][:100]
        to_be = design.get("to_be_design") if isinstance(design.get("to_be_design"), dict) else {}
        problems = _strings(analysis.get("problems"))
        goal = _text(analysis.get("goal"))
        title = _text(analysis.get("title") or "업무 방식 및 개선 실행 보고서", 500)
        source_input = {
            "description_original_sha256": _text(request.get("description_original_sha256") or request.get("source_description_sha256"), 80) or None,
            "description_display_redacted": _text(request.get("description_display_redacted") or request.get("description") or "업무 설명이 제공되지 않았습니다."),
            "additional_instructions": _text(request.get("additional_instructions")),
            "redactions": _strings(request.get("redactions")),
            "redaction_count": request.get("redaction_count") if isinstance(request.get("redaction_count"), int) and request.get("redaction_count") >= 0 else 0,
        }
        refinement_summary = _refinement_summary(design, request)
        business_report = {
            "executive_summary": _block(to_be.get("summary") or goal or "입력된 업무를 기준으로 현재 방식과 개선 실행안을 정리했습니다.", [_fact("업무", title), _fact("설계 상태", "보완 필요" if gaps else "설계 완료")], [f"카탈로그 후보 {len(all_catalog)}개를 검토했습니다.", f"적용 권고 {len(selected)}개, 연결 검토 후보 {len(considered)}개입니다."]),
            "work_overview": _block(goal or "업무 목적은 입력 설명에서 추가 확인이 필요합니다.", [_fact("업무 범위", ", ".join(_strings(analysis.get("scope_in"))) or "확인 필요", "analysis")], _strings(analysis.get("success_criteria"))),
            "operating_context": _block(_text(analysis.get("trigger_and_frequency")) or "업무 실행 시점과 빈도는 설명에 따라 확인합니다.", [_fact("담당", ", ".join(_strings(analysis.get("actors"))) or "확인 필요"), _fact("사용 시스템", ", ".join(_strings(analysis.get("systems"))) or "확인 필요")], _strings(analysis.get("constraints"))),
            "as_is_analysis": _block("현재 업무 단계와 문제점을 바탕으로 현행 Flow를 정리했습니다.", [_fact("현재 단계", str(len(_graph_projection(design.get("as_is_graph"), selected=selected, graph_name="as-is")["nodes"])), "analysis")], problems),
            "improvement_direction": _block(_text(to_be.get("summary")) or "반복 작업은 자동화 후보로, 중요한 판단은 사람 검토 단계로 남깁니다.", [], _strings(to_be.get("principles"))),
            "to_be_operating_plan": _block("권장 TO-BE Flow는 카탈로그 재사용 후보와 신규 구현 필요 항목을 함께 표시합니다.", [_fact("적용 권고", str(len(selected)), "catalog"), _fact("연결 검토", str(len(considered)), "catalog")], []),
            "implementation_allocation": _block("카탈로그 자산은 기술 계약과 권한을 확인한 뒤 연결합니다.", [], [f"{item['title']} · {item['technical_contract_status']}" for item in selected]),
            "implementation_roadmap": _block("구현은 작은 검증 단위로 진행하고, 완료 기준을 충족한 뒤 다음 단계로 이동합니다.", [], [item.get("title", "구현 단계") for item in to_be.get("implementation_roadmap", []) if isinstance(item, dict)]),
            "risks_and_controls": _block("권한, 데이터 품질, 실패 시 게시 차단 기준을 구현 전에 확인합니다.", [], [item.get("risk", "위험 확인 필요") for item in to_be.get("risks_and_controls", []) if isinstance(item, dict)]),
            "validation_plan": _block("정상·예외·권한·중복 데이터 시나리오로 검증합니다.", [], [item.get("title", "검증 시나리오") for item in to_be.get("test_scenarios", []) if isinstance(item, dict)]),
            "open_items": _block("다음 실행 전에 보완할 사항을 확인하세요.", [_fact("보완 필요", str(len(gaps)), "analysis")], [item.get("question", "추가 정보 확인 필요") for item in gaps]),
        }
        as_is_graph = _graph_projection(design.get("as_is_graph"), selected=selected, graph_name="as-is")
        to_be_graph = _graph_projection(to_be, selected=selected, graph_name="to-be")
        trace = design.get("trace") if isinstance(design.get("trace"), dict) else {}
        technical_trace = {
            "source_description_sha256": _text(trace.get("source_description_sha256"), 80),
            "request_sha256": _text(trace.get("request_sha256"), 80),
            "catalog_file_sha256": _text(trace.get("catalog_file_sha256"), 80),
            "candidate_set_sha256": _text(trace.get("candidate_set_sha256"), 80),
            "top_n": trace.get("top_n") if isinstance(trace.get("top_n"), int) else len(all_catalog),
            "ranking_algorithm": _text(trace.get("ranking_algorithm") or "local-lexical-rrf/v1", 128),
            "model_identifier": _text(trace.get("model_identifier") or "unknown", 256),
            "renderer_version": _RENDERER,
        }
        result: dict[str, Any] = {
            "schema_version": _SCHEMA,
            "renderer_version": _RENDERER,
            "report_id": "",
            "source_contract_hash": _sha(design),
            "title": "업무 방식 및 개선 실행 보고서",
            "source_input": source_input,
            # This is the only refinement-related object allowed into the
            # reader-facing report.  It contains no LLM/provider trace data.
            "refinement_summary": refinement_summary,
            "completion_status": {"code": "COMPLETED_WITH_GAPS" if gaps else "COMPLETED", "label": _completion_label("COMPLETED_WITH_GAPS" if gaps else "COMPLETED", len(gaps)), "information_gap_count": len(gaps), "catalog_candidate_count": len(all_catalog), "catalog_selected_count": len(selected)},
            "business_report": business_report,
            "information_gaps": gaps,
            "as_is_graph": as_is_graph,
            "to_be_graph": to_be_graph,
            "catalog_application_plan": {"selected": selected, "considered": considered, "not_used": not_used},
            "implementation_plan": [item for item in to_be.get("implementation_roadmap", []) if isinstance(item, dict)],
            "risks_and_controls": [item for item in to_be.get("risks_and_controls", []) if isinstance(item, dict)],
            "validation_plan": [item for item in to_be.get("test_scenarios", []) if isinstance(item, dict)],
            "technical_trace": technical_trace,
        }
        material = {key: value for key, value in result.items() if key != "report_id"}
        result["report_id"] = "report-" + hashlib.sha256(_canonical(material).encode("utf-8")).hexdigest()[:24]
        self.status = f"보고서 View Model 생성 완료 · 적용 권고 {len(selected)}개 · 보완 필요 {len(gaps)}건"
        return Data(data=result)
