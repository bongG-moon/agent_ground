"""Regression checks for readable workflow edge labels."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "components" / "single_flow" / "07_responsive_report_renderer_v2.py"


def _renderer_module():
    spec = importlib.util.spec_from_file_location("single_flow_renderer_edge_labels", RENDERER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_workflow_edge_labels_are_single_line_and_keep_full_text_accessible() -> None:
    renderer = _renderer_module()

    # Short Korean branch labels must remain clean one-line pills.  Long names
    # are clipped visually but retain their complete text for tooltip and AT.
    assert "white-space:nowrap" in renderer.GRAPH_LAYOUT_CSS
    assert "text-overflow:ellipsis" in renderer.GRAPH_LAYOUT_CSS
    assert "pointer-events:auto" in renderer.GRAPH_LAYOUT_CSS
    assert "const edgeLabelWidth" in renderer.GRAPH_LAYOUT_JS
    assert "edgeLabelMaxWidth = 112" in renderer.GRAPH_LAYOUT_JS
    assert "label.title = route.label;" in renderer.GRAPH_LAYOUT_JS
    assert 'label.setAttribute("aria-label", route.label);' in renderer.GRAPH_LAYOUT_JS
    assert 'event.target.closest("button, .edge-label")' in renderer.GRAPH_LAYOUT_JS
    assert 'label.setAttribute("aria-hidden", "true")' not in renderer.GRAPH_LAYOUT_JS
