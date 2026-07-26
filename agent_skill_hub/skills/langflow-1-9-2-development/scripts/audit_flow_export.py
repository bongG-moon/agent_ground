#!/usr/bin/env python3
"""Audit portable Langflow 1.9.2 JSON exports without loading Langflow."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

VERSION = "1.9.2"
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
SENSITIVE_NAMES = {
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "client_secret",
    "private_key",
    "authorization",
}
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
SKIP_TEXT_FIELDS = {"code", "description", "documentation", "info", "prompt"}


@dataclass
class Finding:
    severity: str
    code: str
    location: str
    message: str


@dataclass
class Audit:
    files: int = 0
    flows: int = 0
    nodes: int = 0
    edges: int = 0
    findings: list[Finding] = field(default_factory=list)

    def add(self, severity: str, code: str, location: str, message: str) -> None:
        self.findings.append(Finding(severity, code, location, message))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit Langflow JSON version stamps, graph integrity, secrets, URLs, and child Flow IDs."
    )
    parser.add_argument("path", type=Path, help="A Langflow JSON file or a directory containing JSON files.")
    parser.add_argument("--expected-version", default=VERSION)
    parser.add_argument(
        "--allow-host",
        action="append",
        default=[],
        help="Approved internal hostname or suffix. Repeat as needed.",
    )
    parser.add_argument("--strict", action="store_true", help="Fail on warnings as well as errors.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit a JSON report.")
    return parser.parse_args()


def json_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(item for item in path.rglob("*.json") if item.is_file())
    raise FileNotFoundError(path)


def extract_flows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("flows"), list):
        return [item for item in payload["flows"] if isinstance(item, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict) and isinstance(item.get("data"), dict)]
    return []


def normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def is_placeholder(value: str) -> bool:
    text = value.strip()
    return (
        not text
        or ENV_NAME.fullmatch(text) is not None
        or text.startswith(("${", "{{", "<"))
        or text.endswith((">", "}}"))
        or text.casefold() in {"none", "null", "changeme", "placeholder"}
    )


def approved_host(host: str, allowed: Iterable[str]) -> bool:
    normalized = host.casefold().rstrip(".")
    if normalized in LOCAL_HOSTS or normalized.endswith(".localhost"):
        return True
    for item in allowed:
        approved = item.casefold().lstrip(".").rstrip(".")
        if normalized == approved or normalized.endswith("." + approved):
            return True
    return False


def walk_template(
    value: Any,
    path: tuple[str, ...] = (),
    sensitive: bool = False,
) -> Iterable[tuple[tuple[str, ...], Any, bool]]:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = normalize_key(str(key))
            child_sensitive = sensitive or normalized in SENSITIVE_NAMES
            if normalized in SKIP_TEXT_FIELDS:
                continue
            yield from walk_template(child, path + (str(key),), child_sensitive)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_template(child, path + (str(index),), sensitive)
    else:
        yield path, value, sensitive


def audit_template(
    template: Any,
    location: str,
    allowed_hosts: list[str],
    audit: Audit,
) -> None:
    if not isinstance(template, dict):
        return
    for path, value, sensitive in walk_template(template):
        if not isinstance(value, str):
            continue
        field_path = ".".join(path)
        leaf_name = normalize_key(path[-1]) if path else ""
        is_configured_value = leaf_name == "value" or leaf_name in SENSITIVE_NAMES
        if sensitive and is_configured_value and not is_placeholder(value):
            audit.add(
                "error",
                "literal-secret",
                f"{location}.{field_path}",
                "A non-placeholder value appears in a secret-bearing field; value suppressed.",
            )
        text = value.strip()
        is_option_metadata = any(normalize_key(part) == "options" for part in path)
        if not is_option_metadata and text.startswith(("http://", "https://")):
            host = (urlparse(text).hostname or "").strip()
            if host and not approved_host(host, allowed_hosts):
                audit.add(
                    "error",
                    "external-url",
                    f"{location}.{field_path}",
                    f"Unapproved URL host: {host}",
                )
        leaf = normalize_key(path[-2] if len(path) >= 2 and path[-1] == "value" else path[-1] if path else "")
        if leaf in {"flow_id", "flow_id_selected", "selected_flow_id"} and UUID.fullmatch(text):
            audit.add(
                "warning",
                "environment-flow-id",
                f"{location}.{field_path}",
                "A child Flow UUID is embedded; portable exports should normally leave it empty.",
            )


def audit_flow(
    flow: dict[str, Any],
    file_label: str,
    index: int,
    expected_version: str,
    allowed_hosts: list[str],
    audit: Audit,
) -> None:
    name = str(flow.get("name") or f"flow[{index}]")
    location = f"{file_label}:{name}"
    audit.flows += 1

    if flow.get("last_tested_version") != expected_version:
        audit.add(
            "error",
            "flow-version",
            location,
            f"last_tested_version must be {expected_version!r}.",
        )

    data = flow.get("data")
    if not isinstance(data, dict):
        audit.add("error", "flow-data", location, "Flow data must be an object.")
        return
    nodes = data.get("nodes")
    edges = data.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        audit.add("error", "graph-shape", location, "Flow data must contain node and edge arrays.")
        return

    audit.nodes += len(nodes)
    audit.edges += len(edges)
    node_ids = [str(node.get("id")) for node in nodes if isinstance(node, dict) and node.get("id")]
    unique_ids = set(node_ids)
    duplicates = sorted({node_id for node_id in node_ids if node_ids.count(node_id) > 1})
    for node_id in duplicates:
        audit.add("error", "duplicate-node-id", location, f"Duplicate node ID: {node_id}")

    for node in nodes:
        if not isinstance(node, dict):
            audit.add("error", "node-shape", location, "A node entry is not an object.")
            continue
        node_id = str(node.get("id") or "<missing-id>")
        node_location = f"{location}:{node_id}"
        serialized = node.get("data", {}).get("node", {})
        if not isinstance(serialized, dict):
            audit.add("error", "node-template", node_location, "Serialized node template is missing.")
            continue
        if serialized.get("lf_version") != expected_version:
            audit.add(
                "error",
                "node-version",
                node_location,
                f"lf_version must be {expected_version!r}.",
            )
        audit_template(serialized.get("template"), node_location, allowed_hosts, audit)

    for edge_index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            audit.add("error", "edge-shape", location, f"Edge {edge_index} is not an object.")
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source not in unique_ids:
            audit.add("error", "dangling-edge-source", location, f"Edge {edge_index} source is missing: {source!r}")
        if target not in unique_ids:
            audit.add("error", "dangling-edge-target", location, f"Edge {edge_index} target is missing: {target!r}")


def run(args: argparse.Namespace) -> Audit:
    audit = Audit()
    for path in json_files(args.path):
        audit.files += 1
        label = str(path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            audit.add("error", "json-read", label, str(exc))
            continue
        flows = extract_flows(payload)
        if not flows:
            audit.add("error", "flow-shape", label, "No Langflow flow object was found.")
            continue
        for index, flow in enumerate(flows):
            audit_flow(flow, label, index, args.expected_version, args.allow_host, audit)
    return audit


def render(audit: Audit, as_json: bool) -> None:
    counts = {
        "errors": sum(item.severity == "error" for item in audit.findings),
        "warnings": sum(item.severity == "warning" for item in audit.findings),
    }
    report = {
        "summary": {
            "files": audit.files,
            "flows": audit.flows,
            "nodes": audit.nodes,
            "edges": audit.edges,
            **counts,
        },
        "findings": [item.__dict__ for item in audit.findings],
    }
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print(
        "files={files} flows={flows} nodes={nodes} edges={edges} "
        "errors={errors} warnings={warnings}".format(**report["summary"])
    )
    for finding in audit.findings:
        print(f"{finding.severity.upper()} {finding.code} {finding.location}: {finding.message}")


def main() -> int:
    args = parse_args()
    try:
        audit = run(args)
    except FileNotFoundError as exc:
        print(f"ERROR path-not-found: {exc}", file=sys.stderr)
        return 2
    render(audit, args.as_json)
    has_error = any(item.severity == "error" for item in audit.findings)
    has_warning = any(item.severity == "warning" for item in audit.findings)
    return 1 if has_error or (args.strict and has_warning) else 0


if __name__ == "__main__":
    raise SystemExit(main())
