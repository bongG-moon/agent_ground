from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
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
ALLOWED_IMPORT_ROOTS = {
    "__future__", "base64", "bson", "codecs", "collections", "copy", "datetime",
    "gridfs", "hashlib", "hmac", "html", "httpx", "json", "lfx", "math", "numpy",
    "pathlib", "pymongo", "re", "requests", "socket", "typing", "unicodedata",
    "urllib", "uuid",
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


def _component_files() -> list[Path]:
    return sorted(PROJECT.glob("components/*/[0-9][0-9]_*.py"))


def _standalone_violations(tree: ast.AST) -> list[str]:
    violations: list[str] = []
    for node in ast.walk(tree):
        if _joined_string(node) == "__import__":
            violations.append("dynamic import string construction")
        if isinstance(node, ast.ImportFrom) and (node.module or "") == "builtins" and any(
            alias.name in DYNAMIC_CALL_NAMES for alias in node.names
        ):
            violations.append("builtins dynamic code/import alias")
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".", 1)[0] in DYNAMIC_IMPORT_ROOTS:
            violations.append("dynamic import helper module")
        elif isinstance(node, ast.Import) and any(alias.name.split(".", 1)[0] in DYNAMIC_IMPORT_ROOTS for alias in node.names):
            violations.append("dynamic import helper module")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in DYNAMIC_CALL_NAMES:
                violations.append("dynamic code/import call")
            elif isinstance(node.func, ast.Attribute) and node.func.attr in DYNAMIC_CALL_ATTRIBUTES:
                violations.append("dynamic code/import attribute call")
            elif (
                isinstance(node.func, ast.Subscript)
                and isinstance(node.func.value, ast.Call)
                and isinstance(node.func.value.func, ast.Name)
                and node.func.value.func.id == "globals"
                and isinstance(node.func.slice, ast.Constant)
                and node.func.slice.value == "__import__"
            ):
                violations.append("globals dynamic import")
            elif (
                isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
            ):
                attribute = _joined_string(node.args[1]) if len(node.args) >= 2 else None
                if (
                    attribute is None
                    or attribute in DYNAMIC_CALL_ATTRIBUTES | FORBIDDEN_INTROSPECTION_ATTRIBUTES | DANGEROUS_EXECUTION_ATTRIBUTES
                    or attribute.startswith(("exec", "spawn"))
                ):
                    violations.append("getattr dynamic code/import")
        if isinstance(node, ast.Name) and node.id in {"__builtins__", *DYNAMIC_CALL_NAMES}:
            violations.append("dynamic builtin access")
        if isinstance(node, ast.Constant) and node.value == "__import__":
            violations.append("dynamic import string access")
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value == "__import__":
            violations.append("subscript dynamic import")
        if isinstance(node, ast.Attribute) and (
            node.attr in FORBIDDEN_INTROSPECTION_ATTRIBUTES
            or node.attr in DANGEROUS_EXECUTION_ATTRIBUTES
            or node.attr.startswith(("exec", "spawn"))
            or (node.attr.startswith("__") and node.attr.endswith("__") and node.attr != "__name__")
        ):
            violations.append("dynamic builtin attribute access")
        if isinstance(node, ast.Subscript):
            key = _joined_string(node.slice)
            if key in FORBIDDEN_INTROSPECTION_ATTRIBUTES or (
                isinstance(key, str) and key.startswith("__") and key.endswith("__") and key != "__name__"
            ):
                violations.append("dynamic builtin subscript access")
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "sys"
            and isinstance(node.value, ast.Name)
            and node.value.id in {"os", "pathlib"}
        ):
            violations.append("indirect sys access")
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "sys" and node.attr == "path":
            violations.append("sys.path access")
    return violations


def _import_violations(tree: ast.AST) -> list[str]:
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if node.level or (root and root not in ALLOWED_IMPORT_ROOTS):
                violations.append(f"local or unapproved import: {node.module}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] not in ALLOWED_IMPORT_ROOTS:
                    violations.append(f"local or unapproved import: {alias.name}")
    return violations


def test_every_custom_component_is_one_file_one_component_subclass() -> None:
    files = _component_files()
    assert files
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        subclasses = [
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and any(
                (isinstance(base, ast.Name) and base.id == "Component")
                or (isinstance(base, ast.Attribute) and base.attr == "Component")
                for base in node.bases
            )
        ]
        assert len(subclasses) == 1, f"{path.name}: expected exactly one Component subclass"


def test_components_do_not_import_sibling_or_local_modules() -> None:
    for path in _component_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        assert _import_violations(tree) == [], f"{path.name}: {_import_violations(tree)}"
        assert _standalone_violations(tree) == [], f"{path.name}: {_standalone_violations(tree)}"


def test_project_manifest_exactly_owns_every_standalone_component_prefix() -> None:
    import json

    manifest = json.loads((PROJECT / "manifest.json").read_text(encoding="utf-8"))
    actual: dict[str, list[str]] = {}
    for path in _component_files():
        actual.setdefault(path.parent.name, []).append(path.name[:2])
    actual = {key: sorted(value) for key, value in sorted(actual.items())}
    declared = {key: sorted(value) for key, value in sorted(manifest["component_groups"].items())}
    flat = [item for values in declared.values() for item in values]
    assert declared == actual
    assert len(flat) == len(set(flat)) == manifest["standalone_component_count"] == len(_component_files())
    assert manifest["production_readiness"]["requires_hitl_expiry_sweeper"] is True
    assert manifest["production_readiness"]["silent_demo_fallback"] is False


def test_standalone_policy_rejects_dynamic_import_and_path_bypass_patterns() -> None:
    prohibited_sources = [
        "import importlib\nimportlib.import_module('local_component')",
        "import runpy\nrunpy.run_path('local_component.py')",
        "from importlib.machinery import SourceFileLoader\nSourceFileLoader('x', 'x.py')",
        "globals()['__import__']('local_component')",
        "import sys\nsys.path.append('components')",
        "import sys\nsys.path = ['components']",
        "import sys as s\ns.path.append('components')",
        "import sys\ngetattr(sys, 'path').append('components')",
        "from builtins import __import__ as loader\nloader('local_component')",
        "__builtins__['__import__']('local_component')",
        "getattr(__builtins__, '__import__')('local_component')",
        "import builtins\nvars(builtins).get('__import__')('local_component')",
        "import os\nloader=vars(os.sys.modules['builtins']).get('__im'+'port__')\nloader('services')",
        "from urllib.parse import quote\nb=quote.__globals__['__builtins__']\nloader=b.get('__IMPORT__'.lower())\nloader('services')",
        "from urllib.parse import quote\nb=getattr(quote,'__GLOBALS__'.lower())['__builtins__']\nloader=b.get('__IMPORT__'.lower())\nloader('services')",
        "from urllib.parse import quote\nquote.__getattribute__('__globals__')['__builtins__'].get('__IMPORT__'.lower())('services')",
        "try:\n  1/0\nexcept Exception as exc:\n  b=exc.__traceback__.tb_frame.f_globals['__builtins__']\n  key=''.join(chr(x) for x in [95,95,105,109,112,111,114,116,95,95])\n  b[key]('services')",
        "import pathlib\npathlib.os.system('whoami')",
        "import urllib.request\nurllib.request.os.system('whoami')",
        "import pickle\npickle.loads(b'cservices\\nfoo\\n.')",
    ]
    for source in prohibited_sources:
        assert _standalone_violations(ast.parse(source)), source
    for source in ("import local_helper", "from helper import value", "from .helper import value"):
        assert _import_violations(ast.parse(source)), source
    assert _import_violations(ast.parse("import httpx")) == []


def test_runtime_validator_uses_the_same_dynamic_and_import_guards() -> None:
    path = PROJECT / "scripts" / "validate_langflow_1_11_runtime.py"
    spec = importlib.util.spec_from_file_location("test_runtime_standalone_validator", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for source in (
        "from builtins import __import__ as loader\nloader('local_component')",
        "__builtins__['__import__']('local_component')",
        "getattr(__builtins__, '__import__')('local_component')",
        "import builtins\nvars(builtins).get('__import__')('local_component')",
        "import os\nloader=vars(os.sys.modules['builtins']).get('__im'+'port__')\nloader('services')",
        "from urllib.parse import quote\nb=quote.__globals__['__builtins__']\nloader=b.get('__IMPORT__'.lower())\nloader('services')",
        "from urllib.parse import quote\nb=getattr(quote,'__GLOBALS__'.lower())['__builtins__']\nloader=b.get('__IMPORT__'.lower())\nloader('services')",
        "from urllib.parse import quote\nquote.__getattribute__('__globals__')['__builtins__'].get('__IMPORT__'.lower())('services')",
        "try:\n  1/0\nexcept Exception as exc:\n  b=exc.__traceback__.tb_frame.f_globals['__builtins__']\n  key=''.join(chr(x) for x in [95,95,105,109,112,111,114,116,95,95])\n  b[key]('services')",
        "import pathlib\npathlib.os.system('whoami')",
        "import urllib.request\nurllib.request.os.system('whoami')",
        "import pickle\npickle.loads(b'cservices\\nfoo\\n.')",
    ):
        assert module._standalone_violation(ast.parse(source)), source
    for source in ("import local_helper", "from helper import value", "from .helper import value"):
        assert module._import_violation(ast.parse(source)), source


def test_flow_generator_uses_the_same_dynamic_and_import_guards() -> None:
    path = PROJECT / "scripts" / "build_langflow_1_11_flows.py"
    spec = importlib.util.spec_from_file_location("test_flow_generator_standalone_guard", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    for source in (
        "from builtins import __import__ as loader\nloader('local_component')",
        "__builtins__['__import__']('local_component')",
        "getattr(__builtins__, '__import__')('local_component')",
        "import builtins\nvars(builtins).get('__import__')('local_component')",
        "import os\nloader=vars(os.sys.modules['builtins']).get('__im'+'port__')\nloader('services')",
        "from urllib.parse import quote\nb=quote.__globals__['__builtins__']\nloader=b.get('__IMPORT__'.lower())\nloader('services')",
        "from urllib.parse import quote\nb=getattr(quote,'__GLOBALS__'.lower())['__builtins__']\nloader=b.get('__IMPORT__'.lower())\nloader('services')",
        "from urllib.parse import quote\nquote.__getattribute__('__globals__')['__builtins__'].get('__IMPORT__'.lower())('services')",
        "try:\n  1/0\nexcept Exception as exc:\n  b=exc.__traceback__.tb_frame.f_globals['__builtins__']\n  key=''.join(chr(x) for x in [95,95,105,109,112,111,114,116,95,95])\n  b[key]('services')",
        "import pathlib\npathlib.os.system('whoami')",
        "import urllib.request\nurllib.request.os.system('whoami')",
        "import pickle\npickle.loads(b'cservices\\nfoo\\n.')",
    ):
        assert module._standalone_violation(ast.parse(source)), source
    for source in ("import local_helper", "from helper import value", "from .helper import value"):
        assert module._import_violation(ast.parse(source)), source


def test_component_sources_compile() -> None:
    for path in _component_files():
        compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_every_source_builds_with_the_langflow_111_component_template_api() -> None:
    """Exercise the same source-to-template path used by Langflow's code editor.

    A plain Python import is insufficient here: Langflow also validates input and
    output names while it constructs the component template.  This catches
    collisions that only appear during a real 1.11.1 import.
    """

    from lfx.custom.custom_component.component import Component as SourceComponent
    from lfx.custom.utils import build_custom_component_template

    for path in _component_files():
        source = path.read_text(encoding="utf-8")
        template, instance = build_custom_component_template(SourceComponent(_code=source))
        assert template["template"]["code"]["value"] == source, path.name
        assert instance.name, path.name
        assert template["outputs"], path.name
        assert all(output.get("method") for output in template["outputs"]), path.name
