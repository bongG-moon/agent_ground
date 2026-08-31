from __future__ import annotations

"""Export the latest sealed F20 input from Langflow Desktop for diagnosis.

This script is intentionally a *local diagnostic helper*, not an alternate
production route.  The normal path remains F10 Component 36 -> Run Flow(F20).
When a nested Run Flow fails, the extracted JSON can be pasted into F20's sole
Chat Input to determine whether the failure is inside F20 or at the parent
Run-Flow boundary.

The script only reads the Desktop SQLite database and deliberately projects
away Langflow's runtime-only ``text``/``default_value`` fields.  It never reads
or prints MongoDB credentials.
"""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any


F10_FLOW_NAME = "F10_work_definition_parent"
COMPONENT_DISPLAY_NAME = "36 Approved Design Invocation Loader"
SCHEMA_VERSION = "agent-design-invocation/v1"
ALLOWED_KEYS = {
    "ok",
    "status",
    "schema_version",
    "artifact_refs",
    "tenant_id",
    "work_definition_id",
    "work_definition_revision",
    "approved_hash",
    "owner_id",
    "session_id",
    "catalog_snapshot_id",
    "work_definition",
    "acl_context",
    "skill_registry",
    "design_prompt",
    "trust_boundary",
    "trace_id",
}
RUNTIME_ONLY_KEYS = {"text", "default_value"}


def default_desktop_database() -> Path:
    app_data = os.environ.get("APPDATA")
    if not app_data:
        raise RuntimeError("APPDATA 환경 변수를 찾지 못했습니다. --desktop-db로 database.db 경로를 지정하세요.")
    return Path(app_data) / "com.LangflowDesktop" / "data" / "database.db"


def load_json(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, str):
        raise RuntimeError(f"{label}이(가) JSON 문자열이 아닙니다.")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label} JSON을 해석하지 못했습니다: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{label}의 최상위 값은 object여야 합니다.")
    return parsed


def open_read_only_database(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise RuntimeError(f"Langflow Desktop database.db를 찾지 못했습니다: {resolved}")
    return sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)


def find_component_node_id(flow_data: dict[str, Any]) -> str:
    nodes = flow_data.get("nodes")
    if not isinstance(nodes, list):
        raise RuntimeError("F10 Flow data에 nodes 배열이 없습니다.")
    matches: list[str] = []
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") == "noteNode":
            continue
        component = node.get("data", {}).get("node", {})
        if isinstance(component, dict) and component.get("display_name") == COMPONENT_DISPLAY_NAME:
            node_id = node.get("id")
            if isinstance(node_id, str) and node_id:
                matches.append(node_id)
    if len(matches) != 1:
        raise RuntimeError(
            f"F10에서 '{COMPONENT_DISPLAY_NAME}' node를 하나로 찾지 못했습니다 (found={len(matches)})."
        )
    return matches[0]


def project_invocation(message: Any) -> dict[str, Any]:
    if not isinstance(message, dict):
        raise RuntimeError("Component 36 success output이 object가 아닙니다.")
    unexpected = set(message) - ALLOWED_KEYS - RUNTIME_ONLY_KEYS
    if unexpected:
        raise RuntimeError(f"Component 36 output에 예상하지 못한 필드가 있습니다: {sorted(unexpected)}")
    invocation = {key: message[key] for key in ALLOWED_KEYS if key in message}
    if invocation.get("ok") is not True or invocation.get("status") != "READY_FOR_DESIGN":
        raise RuntimeError("마지막 Component 36 성공 결과가 READY_FOR_DESIGN 상태가 아닙니다.")
    if invocation.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError("마지막 Component 36 성공 결과의 schema_version이 올바르지 않습니다.")
    missing = sorted(ALLOWED_KEYS - set(invocation))
    if missing:
        raise RuntimeError(f"마지막 Component 36 성공 결과에 필수 invocation 필드가 없습니다: {missing}")
    return invocation


def latest_invocation(desktop_db: Path, flow_name: str) -> tuple[dict[str, Any], str, str]:
    with open_read_only_database(desktop_db) as connection:
        row = connection.execute(
            "SELECT id, data FROM flow WHERE name = ? ORDER BY updated_at DESC LIMIT 1",
            (flow_name,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"Desktop에서 Flow '{flow_name}'을(를) 찾지 못했습니다.")
        flow_id, raw_flow_data = row
        flow_data = load_json(raw_flow_data, "F10 flow data")
        component_node_id = find_component_node_id(flow_data)
        build = connection.execute(
            """
            SELECT data
            FROM vertex_build
            WHERE flow_id = ? AND id = ? AND valid = 1
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (flow_id, component_node_id),
        ).fetchone()
        if build is None:
            raise RuntimeError(
                "Component 36의 성공 build 기록이 없습니다. F10을 승인 단계까지 한 번 실행한 뒤 다시 시도하세요."
            )
        build_data = load_json(build[0], "Component 36 build data")
    try:
        message = build_data["outputs"]["success_path"]["message"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError("Component 36의 success_path message를 찾지 못했습니다.") from exc
    return project_invocation(message), str(flow_id), component_node_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export the latest sealed F20 direct-test input from Langflow Desktop.")
    parser.add_argument("--desktop-db", type=Path, default=default_desktop_database())
    parser.add_argument("--flow-name", default=F10_FLOW_NAME)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("samples") / "f20_direct_playground_input.local.json",
        help="Local JSON output. This path is gitignored by default because it is tied to a live snapshot.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    invocation, flow_id, component_node_id = latest_invocation(args.desktop_db, args.flow_name)
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(invocation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"F20 direct-test input exported: {output}")
    print(f"source: {args.flow_name} ({flow_id}) / {component_node_id}")
    print(
        "identity: "
        f"work_definition_id={invocation['work_definition_id']}, "
        f"snapshot_id={invocation['catalog_snapshot_id']}, "
        f"revision={invocation['work_definition_revision']}"
    )
    print("Paste the entire JSON into F20's only Chat Input, then run F20 directly.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
