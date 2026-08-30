from __future__ import annotations

"""Build the deterministic sample Report View Model and self-contained HTML report."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = PROJECT_ROOT / "samples"
COMPONENTS_DIR = PROJECT_ROOT / "components" / "report"


def _load_module(path: Path) -> ModuleType:
    module_name = "sample_runtime_" + path.stem
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load component source: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _read_json(name: str) -> dict[str, Any]:
    value = json.loads((SAMPLES_DIR / name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{name} must contain a JSON object")
    return value


def build_sample_artifacts() -> tuple[dict[str, Any], dict[str, Any]]:
    """Execute the F20 handoff loader, then components 30 -> 31."""

    report_handoff = _read_json("f20_report_handoff.json")
    handoff_module = _load_module(COMPONENTS_DIR / "33_f30_report_handoff_loader.py")
    view_module = _load_module(COMPONENTS_DIR / "30_report_view_model_builder.py")
    render_module = _load_module(COMPONENTS_DIR / "31_responsive_report_renderer.py")

    handoff = handoff_module.load_f20_report_handoff(report_handoff)

    builder = view_module.ReportViewModelBuilderComponent(
        work_definition=handoff["work_definition"],
        agent_blueprint=handoff["agent_blueprint"],
        retrieval_trace=handoff["retrieval_trace"],
        report_title="주간 업무보고 업무 방식 및 Agent 설계",
        max_nodes=500,
        max_edges=1000,
    )
    view_model = dict(builder.build_report_view_model().data)

    renderer = render_module.ResponsiveReportRendererComponent(
        report_view_model=view_model,
        renderer_version=render_module.RENDERER_VERSION,
        allowed_hosts_json='["localhost"]',
        max_nodes=500,
        max_edges=1000,
        max_html_bytes=10_000_000,
    )
    render_result = dict(renderer.render_report().data)
    return view_model, render_result


def write_sample_artifacts() -> tuple[Path, Path]:
    view_model, render_result = build_sample_artifacts()
    view_model_path = SAMPLES_DIR / "report_view_model.json"
    html_path = SAMPLES_DIR / "generated_sample_report.html"
    view_model_path.write_text(
        json.dumps(view_model, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    html_path.write_text(str(render_result["html"]), encoding="utf-8")
    return view_model_path, html_path


def main() -> int:
    view_model_path, html_path = write_sample_artifacts()
    print(f"wrote {view_model_path}")
    print(f"wrote {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
