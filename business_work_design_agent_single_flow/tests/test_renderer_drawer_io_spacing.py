"""Regression checks for the Langflow implementation-guidance spacing."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "components" / "single_flow" / "07_responsive_report_renderer_v2.py"


def _renderer_module():
    spec = importlib.util.spec_from_file_location("single_flow_renderer_drawer_spacing", RENDERER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_langflow_implementation_notes_have_their_own_spaced_group() -> None:
    renderer = _renderer_module()

    assert ".drawer-io-notes{display:grid;gap:14px;margin-top:23px}" in renderer.DRAWER_IO_CSS
    assert ".drawer-io-section+.drawer-block{padding-top:21px}" in renderer.DRAWER_IO_CSS
    assert ".drawer-io-section .drawer-io-note{margin:0;padding:16px 17px" in renderer.DRAWER_IO_CSS
    assert 'element("section", "drawer-block drawer-io-section")' in renderer.DRAWER_INTERACTION_JS
    assert 'element("div", "drawer-io-notes")' in renderer.DRAWER_INTERACTION_JS
