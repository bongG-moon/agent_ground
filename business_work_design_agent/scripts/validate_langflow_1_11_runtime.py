from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.custom.utils import build_custom_component_template
from lfx.graph import Graph


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FLOW_ROOT = PROJECT_ROOT / "flows"
EXPECTED_VERSIONS = {
    "langflow": "1.11.1",
    "langflow-base": "0.11.5",
    "lfx": "1.11.5",
}
FLOW_FILES = (
    "F00_catalog_ingestion_admin.json",
    "F10_work_definition_parent.json",
    "F11_work_definition_chat_turn.json",
    "F20_agent_blueprint_design.json",
    "F30_responsive_report.json",
    "F90_search_evaluation.json",
)
ALLOWED_IMPORT_ROOTS = {
    "__future__", "base64", "bson", "codecs", "collections", "copy", "datetime",
    "gridfs", "hashlib", "hmac", "html", "httpx", "json", "lfx", "math", "numpy",
    "pathlib", "pymongo", "re", "requests", "socket", "typing", "unicodedata",
    "urllib", "uuid",
}
DYNAMIC_IMPORT_ROOTS = {
    "builtins", "ctypes", "importlib", "marshal", "pickle", "pkgutil", "pydoc",
    "runpy", "shelve", "sys", "zipimport",
}
DYNAMIC_CALL_NAMES = {"eval", "exec", "compile", "__import__", "vars", "globals", "locals"}
DYNAMIC_CALL_ATTRIBUTES = {"import_module", "run_path", "run_module", "SourceFileLoader", "__import__"}
DANGEROUS_EXECUTION_ATTRIBUTES = {"os", "popen", "subprocess", "system"}
FORBIDDEN_INTROSPECTION_ATTRIBUTES = {
    "__base__", "__bases__", "__builtins__", "__class__", "__dict__", "__globals__",
    "__closure__", "__code__", "__getattr__", "__getattribute__", "__import__", "__mro__",
    "__subclasses__", "__traceback__", "ag_frame", "cell_contents", "cr_frame", "f_builtins",
    "f_globals", "f_locals", "func_globals", "gi_frame", "modules", "tb_frame",
}


def _joined_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _joined_string(node.left)
        right = _joined_string(node.right)
        return left + right if left is not None and right is not None else None
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"lower", "casefold", "upper"}
        and not node.args
        and not node.keywords
    ):
        value = _joined_string(node.func.value)
        if value is not None:
            return value.lower() if node.func.attr in {"lower", "casefold"} else value.upper()
    return None


def _standalone_violation(tree: ast.AST) -> str:
    for node in ast.walk(tree):
        if _joined_string(node) == "__import__":
            return "dynamic import string construction is prohibited"
        if isinstance(node, ast.ImportFrom) and (node.module or "") == "builtins" and any(
            alias.name in DYNAMIC_CALL_NAMES for alias in node.names
        ):
            return "builtins dynamic code/import aliases are prohibited"
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".", 1)[0] in DYNAMIC_IMPORT_ROOTS:
            return "dynamic import helper modules are prohibited"
        if isinstance(node, ast.Import) and any(alias.name.split(".", 1)[0] in DYNAMIC_IMPORT_ROOTS for alias in node.names):
            return "dynamic import helper modules are prohibited"
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in DYNAMIC_CALL_NAMES:
                return "dynamic code/import execution is prohibited"
            if isinstance(node.func, ast.Attribute) and node.func.attr in DYNAMIC_CALL_ATTRIBUTES:
                return "dynamic code/import attribute calls are prohibited"
            if (
                isinstance(node.func, ast.Subscript)
                and isinstance(node.func.value, ast.Call)
                and isinstance(node.func.value.func, ast.Name)
                and node.func.value.func.id == "globals"
                and isinstance(node.func.slice, ast.Constant)
                and node.func.slice.value == "__import__"
            ):
                return "globals dynamic import is prohibited"
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
            ):
                attribute = _joined_string(node.args[1]) if len(node.args) >= 2 else None
                if (
                    attribute is None
                    or attribute in DYNAMIC_CALL_ATTRIBUTES | FORBIDDEN_INTROSPECTION_ATTRIBUTES | DANGEROUS_EXECUTION_ATTRIBUTES
                    or attribute.startswith(("exec", "spawn"))
                ):
                    return "getattr dynamic code/import access is prohibited"
        if isinstance(node, ast.Name) and node.id in {"__builtins__", *DYNAMIC_CALL_NAMES}:
            return "dynamic builtin access is prohibited"
        if isinstance(node, ast.Constant) and node.value == "__import__":
            return "dynamic import string access is prohibited"
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value == "__import__":
            return "subscript dynamic import is prohibited"
        if isinstance(node, ast.Attribute) and (
            node.attr in FORBIDDEN_INTROSPECTION_ATTRIBUTES
            or node.attr in DANGEROUS_EXECUTION_ATTRIBUTES
            or node.attr.startswith(("exec", "spawn"))
            or (node.attr.startswith("__") and node.attr.endswith("__") and node.attr != "__name__")
        ):
            return "dynamic builtin attribute access is prohibited"
        if isinstance(node, ast.Subscript):
            key = _joined_string(node.slice)
            if key in FORBIDDEN_INTROSPECTION_ATTRIBUTES or (
                isinstance(key, str) and key.startswith("__") and key.endswith("__") and key != "__name__"
            ):
                return "dynamic builtin subscript access is prohibited"
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "sys"
            and isinstance(node.value, ast.Name)
            and node.value.id in {"os", "pathlib"}
        ):
            return "indirect sys access is prohibited"
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "sys" and node.attr == "path":
            return "sys.path access is prohibited"
    return ""


def _import_violation(tree: ast.AST) -> str:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if node.level or (root and root not in ALLOWED_IMPORT_ROOTS):
                return f"local, relative, or unapproved import is not standalone: {node.module}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] not in ALLOWED_IMPORT_ROOTS:
                    return f"local or unapproved import is not standalone: {alias.name}"
    return ""


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"Expected JSON object: {path}")
    return value


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _component_files() -> list[Path]:
    return sorted(PROJECT_ROOT.glob("components/*/[0-9][0-9]_*.py"))


def _validate_source(path: Path) -> dict[str, Any]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    subclasses = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and any(isinstance(base, ast.Name) and base.id == "Component" for base in node.bases)
    ]
    if len(subclasses) != 1:
        raise ValueError(f"{path}: expected exactly one Component subclass")
    if "from lfx.custom import Component" not in source:
        raise ValueError(f"{path}: must use the public lfx Component import")

    import_violation = _import_violation(tree)
    if import_violation:
        raise ValueError(f"{path}: {import_violation}")
    violation = _standalone_violation(tree)
    if violation:
        raise ValueError(f"{path}: {violation}")

    template, instance = build_custom_component_template(Component(_code=source))
    if template["template"]["code"]["value"] != source:
        raise ValueError(f"{path}: Langflow template changed source bytes")
    if not template.get("outputs"):
        raise ValueError(f"{path}: component has no outputs")
    return {
        "path": path.relative_to(PROJECT_ROOT).as_posix(),
        "component_name": str(getattr(instance, "name", "") or type(instance).__name__),
        "sha256": _sha256(path.read_bytes()),
    }


def _validate_flow(path: Path) -> dict[str, Any]:
    flow = _load_json(path)
    graph = Graph.from_payload(
        flow["data"],
        flow_id=flow["id"],
        flow_name=flow["name"],
        user_id="standalone-runtime-validator",
    )
    for wrapper in flow["data"]["nodes"]:
        node = wrapper["data"]["node"]
        metadata = node.get("metadata") or {}
        if metadata.get("standalone") is not True:
            continue
        source_path = PROJECT_ROOT / metadata["standalone_source_path"]
        source_bytes = source_path.read_bytes()
        embedded_bytes = node["template"]["code"]["value"].encode("utf-8")
        if embedded_bytes != source_bytes:
            raise ValueError(f"{path.name}: embedded source differs for {source_path.name}")
        if metadata["standalone_source_sha256"] != _sha256(source_bytes):
            raise ValueError(f"{path.name}: embedded source hash differs for {source_path.name}")
    return {
        "filename": path.name,
        "flow_id": flow["id"],
        "nodes": len(graph.vertices),
        "edges": len(graph.edges),
        "operational_readiness": flow["metadata"]["operational_readiness"],
    }


def _validate_manifest() -> dict[str, Any]:
    manifest = _load_json(FLOW_ROOT / "build_manifest.json")
    for record in manifest["flows"]:
        payload = (FLOW_ROOT / record["filename"]).read_bytes()
        if _sha256(payload) != record["sha256"]:
            raise ValueError(f"Flow manifest hash mismatch: {record['filename']}")
    bundle = (FLOW_ROOT / manifest["bundle"]["filename"]).read_bytes()
    if _sha256(bundle) != manifest["bundle"]["sha256"]:
        raise ValueError("Flow bundle hash mismatch")
    return {
        "schema_version": manifest["schema_version"],
        "flow_count": len(manifest["flows"]),
        "bundle_sha256": manifest["bundle"]["sha256"],
    }


def _validate_project_manifest(component_paths: list[Path]) -> dict[str, Any]:
    manifest = _load_json(PROJECT_ROOT / "manifest.json")
    groups = manifest.get("component_groups")
    if not isinstance(groups, dict):
        raise ValueError("Project manifest component_groups must be an object")
    actual: dict[str, list[str]] = {}
    for path in component_paths:
        actual.setdefault(path.parent.name, []).append(path.name[:2])
    actual = {key: sorted(value) for key, value in sorted(actual.items())}
    declared: dict[str, list[str]] = {}
    for key, value in groups.items():
        if not isinstance(key, str) or not isinstance(value, list) or any(type(item) is not str for item in value):
            raise ValueError("Project manifest component group entries are invalid")
        declared[key] = sorted(value)
    if declared != actual:
        raise ValueError("Project manifest component_groups do not match standalone source files")
    flat = [item for values in declared.values() for item in values]
    if len(flat) != len(set(flat)) or manifest.get("standalone_component_count") != len(component_paths):
        raise ValueError("Project manifest standalone component count or prefix ownership is invalid")
    return {
        "standalone_component_count": len(component_paths),
        "component_groups": actual,
    }


def main() -> int:
    resolved = {name: version(name) for name in EXPECTED_VERSIONS}
    if resolved != EXPECTED_VERSIONS:
        raise RuntimeError(f"Exact runtime required: {EXPECTED_VERSIONS}; resolved: {resolved}")

    component_paths = _component_files()
    components = [_validate_source(path) for path in component_paths]
    if len(components) != 37:
        raise ValueError(f"Expected 37 standalone components, found {len(components)}")
    flows = [_validate_flow(FLOW_ROOT / filename) for filename in FLOW_FILES]
    manifest = _validate_manifest()
    project_manifest = _validate_project_manifest(component_paths)
    drift = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "build_langflow_1_11_flows.py"), "--check"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    if drift.returncode:
        raise RuntimeError((drift.stdout + drift.stderr).strip())

    result = {
        "ok": True,
        "runtime": resolved,
        "standalone_component_count": len(components),
        "flow_count": len(flows),
        "flows": flows,
        "manifest": manifest,
        "project_manifest": project_manifest,
        "generator_check": drift.stdout.strip(),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
