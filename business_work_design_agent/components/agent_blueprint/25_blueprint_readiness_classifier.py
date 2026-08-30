from __future__ import annotations

import json
import uuid
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, Output
from lfx.schema import Data


TECHNICAL_STATUSES = {"metadata_only", "ports_extracted", "flow_graph_extracted", "verified_runtime"}
CONNECTION_STATUSES = {"unverified", "contract_compatible", "verified_runtime"}
BUILD_READINESS = {"design_only", "proposed_unverified", "import_ready"}
CATALOG_SOURCES = {"catalog_component", "catalog_flow"}


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


def _blueprint(value: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = _payload(value)
    nested = payload.get("blueprint")
    blueprint = nested if isinstance(nested, dict) else payload
    issues = payload.get("validation_issues") if isinstance(payload.get("validation_issues"), list) else []
    return blueprint, [item for item in issues if isinstance(item, dict)]


def _forward_blocked_envelope(value: Any, *, trace_id: str) -> dict[str, Any] | None:
    """Preserve the precise preceding F20 failure for the parent Run Flow."""
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


def classify_blueprint_readiness(validated_blueprint: Any) -> dict[str, Any]:
    trace_id = str(uuid.uuid4())
    blocked = _forward_blocked_envelope(validated_blueprint, trace_id=trace_id)
    if blocked is not None:
        return blocked
    blueprint, validation_issues = _blueprint(validated_blueprint)
    if not blueprint or not isinstance(blueprint.get("nodes"), list) or not isinstance(blueprint.get("edges"), list):
        return _error(trace_id, "INVALID_BLUEPRINT", "port 검증을 거친 blueprint가 필요합니다.")
    approved_hash = str(blueprint.get("approved_hash") or "")
    snapshot_id = str(blueprint.get("catalog_snapshot_id") or "")
    if not approved_hash.startswith("sha256:") or not snapshot_id:
        return _error(trace_id, "BLUEPRINT_LOCK_MISSING", "approved_hash와 catalog_snapshot_id가 필요합니다.")

    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    import_requirements: list[dict[str, Any]] = []
    for issue in validation_issues:
        if issue.get("severity") == "error":
            blockers.append({"code": str(issue.get("code") or "PORT_VALIDATION_ERROR"), "ref": issue.get("edge_id") or issue.get("node_id")})

    generation_request_refs = {
        str(item.get("node_id") or item.get("target_node_id") or "")
        for item in blueprint.get("generation_requests", [])
        if isinstance(item, dict)
    }
    for node in blueprint["nodes"]:
        if not isinstance(node, dict):
            blockers.append({"code": "INVALID_NODE", "ref": None})
            continue
        node_id = str(node.get("node_id") or "")
        source = str(node.get("implementation_source") or "")
        technical_status = node.get("technical_contract_status")
        if source in CATALOG_SOURCES:
            if technical_status not in TECHNICAL_STATUSES:
                blockers.append({"code": "INVALID_TECHNICAL_CONTRACT_STATUS", "ref": node_id})
            elif technical_status == "metadata_only":
                blockers.append({"code": "METADATA_ONLY_EXECUTION_NODE", "ref": node_id})
            elif technical_status != "verified_runtime":
                warnings.append({"code": "CATALOG_RUNTIME_NOT_VERIFIED", "ref": node_id})
                import_requirements.append({"code": "VERIFY_CATALOG_RUNTIME", "ref": node_id})
        elif technical_status is not None:
            blockers.append({"code": "TECHNICAL_STATUS_ON_NON_CATALOG_NODE", "ref": node_id})

        runtime_status = str(node.get("runtime_validation_status") or "unverified")
        if source in {"builtin", "new_standalone_component"} and runtime_status != "verified_runtime":
            warnings.append({"code": "NODE_RUNTIME_NOT_VERIFIED", "ref": node_id})
            import_requirements.append({"code": "VERIFY_NODE_RUNTIME", "ref": node_id})
        if source == "new_standalone_component":
            has_contract = isinstance(node.get("generation_contract"), dict) and bool(node.get("generation_contract"))
            has_request = bool(node.get("generation_request_ref")) or node_id in generation_request_refs
            if not has_contract:
                blockers.append({"code": "GENERATION_CONTRACT_MISSING", "ref": node_id})
            if not has_request:
                warnings.append({"code": "GENERATION_REQUEST_PENDING", "ref": node_id})
                import_requirements.append({"code": "BUILD_STANDALONE_COMPONENT", "ref": node_id})
        if source == "companion_service" and str(node.get("service_contract_status") or "unverified") != "verified_runtime":
            warnings.append({"code": "COMPANION_SERVICE_NOT_VERIFIED", "ref": node_id})
            import_requirements.append({"code": "VERIFY_COMPANION_SERVICE", "ref": node_id})

        for secret in node.get("required_secrets", []) if isinstance(node.get("required_secrets"), list) else []:
            if isinstance(secret, dict) and secret.get("required", True) and secret.get("configured") is not True:
                warnings.append({"code": "SECRET_NOT_CONFIGURED", "ref": f"{node_id}:{secret.get('name') or secret.get('port_id')}"})
                import_requirements.append({"code": "CONFIGURE_SECRET", "ref": node_id})
        for permission in node.get("required_permissions", []) if isinstance(node.get("required_permissions"), list) else []:
            if isinstance(permission, dict) and permission.get("required", True) and permission.get("granted") is not True:
                warnings.append({"code": "PERMISSION_NOT_GRANTED", "ref": f"{node_id}:{permission.get('name')}"})
                import_requirements.append({"code": "GRANT_PERMISSION", "ref": node_id})

    for edge in blueprint["edges"]:
        if not isinstance(edge, dict):
            blockers.append({"code": "INVALID_EDGE", "ref": None})
            continue
        edge_id = str(edge.get("edge_id") or "")
        connection_status = edge.get("connection_validation_status")
        if connection_status not in CONNECTION_STATUSES:
            blockers.append({"code": "INVALID_CONNECTION_VALIDATION_STATUS", "ref": edge_id})
        elif connection_status == "unverified":
            blockers.append({"code": "EDGE_CONTRACT_INVALID_OR_UNKNOWN", "ref": edge_id})
        elif connection_status == "contract_compatible":
            warnings.append({"code": "EDGE_RUNTIME_NOT_VERIFIED", "ref": edge_id})
            import_requirements.append({"code": "VERIFY_EDGE_RUNTIME", "ref": edge_id})

    unresolved = blueprint.get("unresolved") if isinstance(blueprint.get("unresolved"), list) else []
    for item in unresolved:
        if isinstance(item, dict) and item.get("blocking", True):
            blockers.append({"code": "UNRESOLVED_BLOCKING_ITEM", "ref": item.get("path") or item.get("id")})
        elif item:
            warnings.append({"code": "UNRESOLVED_NONBLOCKING_ITEM", "ref": str(item)[:200]})

    all_edges_runtime = all(
        isinstance(edge, dict) and edge.get("connection_validation_status") == "verified_runtime" for edge in blueprint["edges"]
    )
    all_runtime_requirements_met = not import_requirements
    flow_import_verified = blueprint.get("flow_import_verified") is True
    if blockers:
        readiness = "design_only"
    elif all_edges_runtime and all_runtime_requirements_met and flow_import_verified:
        readiness = "import_ready"
    else:
        readiness = "proposed_unverified"
        if not flow_import_verified:
            import_requirements.append({"code": "VERIFY_FLOW_IMPORT", "ref": blueprint.get("blueprint_id")})

    result_blueprint = dict(blueprint)
    result_blueprint["build_readiness"] = readiness
    result_blueprint["readiness_assessment"] = {
        "status_axis": "build_readiness",
        "technical_status_axis": "technical_contract_status",
        "connection_status_axis": "connection_validation_status",
        "blockers": blockers,
        "warnings": warnings,
        "import_requirements": _dedupe(import_requirements),
        "flow_import_verified": flow_import_verified,
    }
    return {
        "ok": True,
        "status": "COMPLETED",
        "blueprint": result_blueprint,
        "build_readiness": readiness,
        "blockers": blockers,
        "warnings": warnings,
        "trace_id": trace_id,
    }


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        key = (str(item.get("code")), str(item.get("ref")))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _error(trace_id: str, code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "BLOCKED",
        "artifact_refs": [],
        "error": {"code": code, "message": message, "retryable": False, "details": {}},
        "resume": None,
        "trace_id": trace_id,
    }


class BlueprintReadinessClassifierComponent(Component):
    display_name = "25 Blueprint Readiness Classifier"
    description = "기술 계약, edge 연결 검증과 전체 build readiness를 분리해 design/proposed/import 등급을 결정합니다."
    icon = "BadgeCheck"
    name = "BlueprintReadinessClassifier"

    inputs = [DataInput(name="validated_blueprint", display_name="Port-validated Blueprint", required=True)]
    outputs = [Output(name="classified_blueprint", display_name="Classified Blueprint", method="build_classified_blueprint", types=["Data"])]

    def build_classified_blueprint(self) -> Data:
        result = classify_blueprint_readiness(self.validated_blueprint)
        self.status = f"Blueprint readiness: {result.get('build_readiness', result.get('status'))} / blockers={len(result.get('blockers', []))}"
        return Data(data=result)
