from __future__ import annotations

import json
import uuid
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, Output
from lfx.schema import Data


CONNECTION_STATUSES = {"unverified", "contract_compatible", "verified_runtime"}


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return data
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _blueprint(value: Any) -> dict[str, Any]:
    payload = _payload(value)
    nested = payload.get("blueprint")
    return nested if isinstance(nested, dict) else payload


def _forward_blocked_envelope(value: Any, *, trace_id: str) -> dict[str, Any] | None:
    """Keep a prior F20-stage failure visible instead of relabeling it.

    F20 is deliberately fail-closed, but its linear Data chain still invokes
    downstream components after an upstream component returns a structured
    ``BLOCKED`` envelope.  Without this guard, the actual error from the
    normalizer is replaced by ``INVALID_BLUEPRINT`` here and becomes
    impossible to diagnose from the parent Run Flow.
    """
    payload = _payload(value)
    error = payload.get("error")
    if payload.get("ok") is not False or str(payload.get("status") or "") != "BLOCKED" or not isinstance(error, dict):
        return None
    details = error.get("details")
    forwarded_details = dict(details) if isinstance(details, dict) else {}
    upstream_trace_id = str(payload.get("trace_id") or "").strip()
    if upstream_trace_id:
        forwarded_details.setdefault("upstream_trace_id", upstream_trace_id)
    return {
        "ok": False,
        "status": "BLOCKED",
        "artifact_refs": [],
        "error": {
            "code": str(error.get("code") or "UPSTREAM_BLUEPRINT_STAGE_BLOCKED"),
            "message": str(error.get("message") or "이전 Blueprint 단계가 차단되었습니다."),
            "retryable": error.get("retryable") is True,
            "details": forwarded_details,
        },
        "resume": None,
        "trace_id": trace_id,
    }


def _runtime_evidence(value: Any) -> dict[str, dict[str, Any]]:
    payload = _payload(value)
    items = payload.get("edge_evidence") if isinstance(payload.get("edge_evidence"), list) else []
    return {str(item.get("edge_id")): item for item in items if isinstance(item, dict) and item.get("edge_id")}


def _port_map(node: dict[str, Any], direction: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
    canonical_key = "outputs" if direction == "output" else "inputs"
    legacy_key = "output_ports" if direction == "output" else "input_ports"
    # Agent Blueprint schema names are authoritative.  The legacy aliases are
    # accepted only when the canonical field is absent, never merged, so two
    # conflicting contracts cannot silently coexist.
    raw = (
        node.get(canonical_key)
        if isinstance(node.get(canonical_key), list)
        else node.get(legacy_key)
        if isinstance(node.get(legacy_key), list)
        else []
    )
    result: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        port_id = str(item.get("port_id") or item.get("name") or "")
        if not port_id:
            continue
        if port_id in result:
            duplicates.append(port_id)
        result[port_id] = item
    return result, duplicates


def _type_compatible(source: str, target: str) -> bool:
    source_name = source.strip().lower()
    target_name = target.strip().lower()
    if not source_name or not target_name:
        return False
    if source_name == target_name:
        return True
    # Data is the only intentionally broad structured wrapper.  Message and
    # DataFrame are not silently coerced because that needs an explicit adapter.
    return target_name == "data" and source_name in {"data", "json", "dict"}


def validate_port_contracts(normalized_blueprint: Any, runtime_evidence: Any = None) -> dict[str, Any]:
    trace_id = str(uuid.uuid4())
    blocked = _forward_blocked_envelope(normalized_blueprint, trace_id=trace_id)
    if blocked is not None:
        return blocked
    blueprint = _blueprint(normalized_blueprint)
    evidence = _runtime_evidence(runtime_evidence)
    if not blueprint or not isinstance(blueprint.get("nodes"), list) or not isinstance(blueprint.get("edges"), list):
        return _error(trace_id, "INVALID_BLUEPRINT", "정규화된 blueprint와 nodes/edges가 필요합니다.")
    approved_hash = str(blueprint.get("approved_hash") or "")
    snapshot_id = str(blueprint.get("catalog_snapshot_id") or "")
    nodes = {str(item.get("node_id")): item for item in blueprint["nodes"] if isinstance(item, dict) and item.get("node_id")}
    node_ports: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
    issues: list[dict[str, Any]] = []
    for node_id, node in nodes.items():
        inputs, input_duplicates = _port_map(node, "input")
        outputs, output_duplicates = _port_map(node, "output")
        node_ports[node_id] = {"input": inputs, "output": outputs}
        for port_id in input_duplicates + output_duplicates:
            issues.append({"severity": "error", "code": "DUPLICATE_PORT_ID", "node_id": node_id, "port_id": port_id})

    incoming: set[tuple[str, str]] = set()
    validated_edges: list[dict[str, Any]] = []
    for raw in blueprint["edges"]:
        if not isinstance(raw, dict):
            continue
        edge = dict(raw)
        edge_id = str(edge.get("edge_id") or "")
        source_node_id = str(edge.get("source_node_id") or "")
        target_node_id = str(edge.get("target_node_id") or "")
        source_port_id = str(edge.get("source_port_id") or "")
        target_port_id = str(edge.get("target_port_id") or "")
        edge_issues: list[dict[str, Any]] = []
        if source_node_id not in nodes or target_node_id not in nodes:
            edge_issues.append({"code": "DANGLING_NODE_REFERENCE"})
            source_port = target_port = None
        else:
            source_port = node_ports[source_node_id]["output"].get(source_port_id)
            target_port = node_ports[target_node_id]["input"].get(target_port_id)
            if source_port is None:
                edge_issues.append({"code": "SOURCE_PORT_NOT_FOUND"})
            if target_port is None:
                edge_issues.append({"code": "TARGET_PORT_NOT_FOUND"})
        if source_port is not None and target_port is not None:
            if not _type_compatible(str(source_port.get("data_type") or ""), str(target_port.get("data_type") or "")):
                edge_issues.append({"code": "PORT_TYPE_MISMATCH"})
            source_cardinality = str(source_port.get("cardinality") or "one")
            target_cardinality = str(target_port.get("cardinality") or "one")
            if source_cardinality != target_cardinality:
                edge_issues.append({"code": "PORT_CARDINALITY_MISMATCH"})
            source_role = str(source_port.get("semantic_role") or "")
            target_role = str(target_port.get("semantic_role") or "")
            if source_role and target_role and source_role != target_role:
                edge_issues.append({"code": "PORT_SEMANTIC_ROLE_MISMATCH"})
            if bool(source_port.get("streaming")) != bool(target_port.get("streaming")):
                edge_issues.append({"code": "PORT_STREAMING_MISMATCH"})
            if bool(source_port.get("secret")) or bool(target_port.get("secret")):
                if not (bool(source_port.get("secret")) and bool(target_port.get("secret"))):
                    edge_issues.append({"code": "SECRET_PORT_MISMATCH"})
                if not str(target_port.get("permission") or ""):
                    edge_issues.append({"code": "SECRET_PERMISSION_MISSING"})
            source_permission = str(source_port.get("permission") or "")
            target_permission = str(target_port.get("permission") or "")
            if target_permission and source_permission != target_permission:
                edge_issues.append({"code": "PORT_PERMISSION_MISMATCH"})
            source_zone = str(source_port.get("network_zone") or nodes[source_node_id].get("network_zone") or "")
            target_zone = str(target_port.get("network_zone") or nodes[target_node_id].get("network_zone") or "")
            if source_zone and target_zone and source_zone != target_zone and not edge.get("approved_network_bridge_ref"):
                edge_issues.append({"code": "NETWORK_ZONE_MISMATCH"})
            incoming.add((target_node_id, target_port_id))

        compatible = not edge_issues
        status = "contract_compatible" if compatible else "unverified"
        runtime = evidence.get(edge_id)
        if compatible and runtime:
            if (
                runtime.get("status") == "verified_runtime"
                and runtime.get("approved_hash") == approved_hash
                and runtime.get("catalog_snapshot_id") == snapshot_id
                and runtime.get("smoke_test_passed") is True
            ):
                status = "verified_runtime"
            else:
                edge_issues.append({"code": "RUNTIME_EVIDENCE_SCOPE_MISMATCH"})
        edge["connection_validation_status"] = status
        edge["validation_issues"] = [item["code"] for item in edge_issues]
        for issue in edge_issues:
            issues.append({"severity": "error", "edge_id": edge_id, **issue})
        validated_edges.append(edge)

    for node_id, ports in node_ports.items():
        for port_id, port in ports["input"].items():
            if bool(port.get("required")) and not bool(port.get("has_default")) and (node_id, port_id) not in incoming:
                # Explicit secret/config references can satisfy a required port
                # without a normal graph edge.
                node = nodes[node_id]
                configured = port_id in (node.get("config") if isinstance(node.get("config"), dict) else {})
                secret_refs = {
                    str(item.get("port_id") or item.get("name") or "")
                    for item in node.get("required_secrets", [])
                    if isinstance(item, dict) and item.get("configured") is True
                }
                if not configured and port_id not in secret_refs:
                    issues.append({"severity": "error", "code": "REQUIRED_INPUT_UNCONNECTED", "node_id": node_id, "port_id": port_id})

    result_blueprint = dict(blueprint)
    result_blueprint["edges"] = validated_edges
    compatible_count = sum(item.get("connection_validation_status") == "contract_compatible" for item in validated_edges)
    runtime_count = sum(item.get("connection_validation_status") == "verified_runtime" for item in validated_edges)
    unverified_count = sum(item.get("connection_validation_status") == "unverified" for item in validated_edges)
    result_blueprint["connection_validation"] = {
        "status_axis": "connection_validation_status",
        "edge_count": len(validated_edges),
        "contract_compatible_count": compatible_count,
        "verified_runtime_count": runtime_count,
        "unverified_count": unverified_count,
        "error_count": len(issues),
    }
    return {
        "ok": True,
        "status": "COMPLETED_WITH_ISSUES" if issues else "COMPLETED",
        "blueprint": result_blueprint,
        "validation_issues": issues,
        "trace_id": trace_id,
    }


def _error(trace_id: str, code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "BLOCKED",
        "artifact_refs": [],
        "error": {"code": code, "message": message, "retryable": False, "details": {}},
        "resume": None,
        "trace_id": trace_id,
    }


class PortContractValidatorComponent(Component):
    display_name = "24 Port Contract Validator"
    description = "edge의 type, semantic role, cardinality, required, secret, permission, network zone 계약을 검증합니다."
    icon = "Cable"
    name = "PortContractValidator"

    inputs = [
        DataInput(name="normalized_blueprint", display_name="Normalized Blueprint", required=True),
        DataInput(name="runtime_evidence", display_name="Runtime Edge Evidence", required=False, advanced=True),
    ]
    outputs = [Output(name="validated_blueprint", display_name="Port-validated Blueprint", method="build_validated_blueprint", types=["Data"])]

    def build_validated_blueprint(self) -> Data:
        result = validate_port_contracts(self.normalized_blueprint, getattr(self, "runtime_evidence", None))
        summary = (result.get("blueprint") or {}).get("connection_validation", {})
        self.status = f"Port validation: {result.get('status')} / errors={summary.get('error_count', 0)}"
        return Data(data=result)
