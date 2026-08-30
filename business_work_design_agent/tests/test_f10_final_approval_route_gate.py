from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "components" / "work_definition" / "43_f10_final_approval_route_gate.py"


def _install_lfx_stubs() -> dict[str, types.ModuleType | None]:
    names = ("lfx", "lfx.custom", "lfx.io", "lfx.schema")
    originals = {name: sys.modules.get(name) for name in names}

    class Component:
        def __init__(self):
            self.stopped_outputs = []

        def stop(self, output_name):
            self.stopped_outputs.append(output_name)

    class Port:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.__dict__.update(kwargs)

    class Data:
        def __init__(self, data=None):
            self.data = data or {}

    modules = {name: types.ModuleType(name) for name in names}
    modules["lfx.custom"].Component = Component
    modules["lfx.io"].DataInput = Port
    modules["lfx.io"].Output = Port
    modules["lfx.schema"].Data = Data
    sys.modules.update(modules)
    return originals


def _restore_modules(originals: dict[str, types.ModuleType | None]) -> None:
    for name, module in originals.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def _load_component():
    spec = importlib.util.spec_from_file_location("f10_final_approval_route_gate_test", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ORIGINALS = _install_lfx_stubs()
try:
    MODULE = _load_component()
finally:
    _restore_modules(_ORIGINALS)


class _Graph:
    def __init__(self, action: str | None):
        self.run_id = "run-final-1"
        self.human_input_decisions = (
            {"native-human:run-final-1": {"action_id": action}} if action is not None else {}
        )
        self.edges = [
            types.SimpleNamespace(
                source_id="native-human",
                target_id="node-43",
                target_handle=types.SimpleNamespace(field_name="approval_triggers"),
            )
        ]
        self.exclusions = []

    def exclude_branches_conditionally(self, vertex_id, output_names):
        self.exclusions.append((vertex_id, list(output_names)))


class FinalApprovalRouteGateTests(unittest.TestCase):
    def test_selected_reject_excludes_the_two_unselected_store_branches(self):
        component = MODULE.F10FinalApprovalRouteGateComponent()
        component._id = "node-43"
        component._current_output = "branch_reject"
        component.graph = _Graph("reject")

        result = component.route_final_action()

        self.assertEqual(result.data["route"], "branch_reject")
        self.assertEqual(component.stopped_outputs, ["branch_approve", "branch_cancel", "blocked_path"])
        self.assertEqual(
            component.graph.exclusions,
            [("node-43", ["branch_approve", "branch_cancel", "blocked_path"])],
        )

    def test_nonselected_group_output_is_empty_even_when_the_gate_is_already_built(self):
        component = MODULE.F10FinalApprovalRouteGateComponent()
        component._id = "node-43"
        component._current_output = "branch_cancel"
        component.graph = _Graph("approve")

        result = component.route_final_action()

        self.assertEqual(result.data, {})

    def test_missing_human_decision_fails_closed(self):
        component = MODULE.F10FinalApprovalRouteGateComponent()
        component._id = "node-43"
        component._current_output = "blocked_path"
        component.graph = _Graph(None)

        result = component.route_final_action()

        self.assertFalse(result.data["ok"])
        self.assertEqual(result.data["error"]["code"], "FINAL_APPROVAL_DECISION_UNAVAILABLE")
        self.assertEqual(
            component.graph.exclusions,
            [("node-43", ["branch_approve", "branch_reject", "branch_cancel"])],
        )


if __name__ == "__main__":
    unittest.main()
