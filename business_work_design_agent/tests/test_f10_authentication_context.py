from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_PATH = PROJECT_ROOT / "components" / "work_definition" / "45_f10_authentication_context.py"


def load_component() -> ModuleType:
    names = ("lfx", "lfx.custom", "lfx.io", "lfx.schema")
    originals = {name: sys.modules.get(name) for name in names}

    class Component:
        pass

    class Port:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.args = args
            self.__dict__.update(kwargs)

    class Data:
        def __init__(self, data: Any = None) -> None:
            self.data = data or {}

    modules = {name: types.ModuleType(name) for name in names}
    modules["lfx.custom"].Component = Component
    for port_name in ("DataInput", "DropdownInput", "MessageTextInput", "Output"):
        setattr(modules["lfx.io"], port_name, Port)
    modules["lfx.schema"].Data = Data
    sys.modules.update(modules)

    module_name = "test_f10_authentication_context_runtime"
    spec = importlib.util.spec_from_file_location(module_name, COMPONENT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        for name, original in originals.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original
    return module


@pytest.fixture(scope="module")
def module() -> ModuleType:
    return load_component()


def test_local_demo_fixture_is_usable_but_not_marked_as_trusted(module: ModuleType) -> None:
    result = module.build_f10_authentication_context(
        authentication_source="local_demo_fixture",
        local_demo_employee_actor_id="employee-demo",
        trusted_gateway_groups="[]",
        trace_id="auth-trace",
    )
    assert result == {
        "ok": True,
        "status": "AUTHENTICATION_READY",
        "schema_version": "f10-authentication-context/v1",
        "artifact_refs": [],
        "source": "local_demo_fixture",
        "subject_id": "employee-demo",
        "groups": [],
        "authenticated_subject_verified": False,
        "trace_id": "auth-trace",
    }


def test_trusted_gateway_never_falls_back_to_employee_actor(module: ModuleType) -> None:
    missing_gateway = module.build_f10_authentication_context(
        authentication_source="trusted_gateway",
        local_demo_employee_actor_id="employee-demo",
        trusted_gateway_subject_id="",
    )
    assert missing_gateway["ok"] is False
    assert missing_gateway["error"]["code"] == "TRUSTED_GATEWAY_SUBJECT_REQUIRED"

    result = module.build_f10_authentication_context(
        authentication_source="trusted_gateway",
        local_demo_employee_actor_id="employee-demo",
        trusted_gateway_subject_id="employee-gateway",
        trusted_gateway_groups='["REPORTERS", "ops", "reporters"]',
    )
    assert result["ok"] is True
    assert result["subject_id"] == "employee-gateway"
    assert result["groups"] == ["ops", "reporters"]
    assert result["authenticated_subject_verified"] is True


@pytest.mark.parametrize(
    ("source", "groups", "expected_code"),
    [
        ("unsupported", "[]", "AUTHENTICATION_SOURCE_INVALID"),
        ("local_demo_fixture", "ops", "LOCAL_DEMO_GROUPS_NOT_ALLOWED"),
        ("trusted_gateway", "[not-json", "AUTHENTICATION_GROUPS_INVALID"),
    ],
)
def test_authentication_context_fails_closed_on_invalid_source_or_groups(
    module: ModuleType,
    source: str,
    groups: Any,
    expected_code: str,
) -> None:
    result = module.build_f10_authentication_context(
        authentication_source=source,
        local_demo_employee_actor_id="employee-demo",
        trusted_gateway_subject_id="employee-gateway",
        trusted_gateway_groups=groups,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == expected_code


def test_component_routes_one_group_output_and_excludes_the_other(module: ModuleType) -> None:
    component = module.F10AuthenticationContextComponent()
    component.authentication_source = "trusted_gateway"
    component.local_demo_employee_actor_id = "employee-demo"
    component.trusted_gateway_subject_id = "employee-gateway"
    component.trusted_gateway_groups = "ops"
    component.trace_id = "component-trace"
    stopped: list[str] = []
    component.stop = stopped.append

    routed = component.route_context().data
    assert routed["ok"] is True
    assert routed["source"] == "trusted_gateway"
    assert stopped == ["blocked_path"]
    assert component.status["route"] == "success_path"

    outputs = {output.name: output for output in module.F10AuthenticationContextComponent.outputs}
    assert set(outputs) == {"success_path", "blocked_path"}
    assert all(output.group_outputs is True for output in outputs.values())
