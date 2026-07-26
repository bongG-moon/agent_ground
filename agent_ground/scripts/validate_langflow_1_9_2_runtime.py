from __future__ import annotations

"""Agent Ground 전체 자산을 Langflow 1.9.2 실제 loader와 Graph로 검사합니다."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault(
    "LANGFLOW_CONFIG_DIR",
    str(Path(tempfile.gettempdir()) / "agent-ground-langflow-1-9-2-runtime"),
)

from lfx.custom.eval import eval_custom_component_code  # noqa: E402
from lfx.custom.utils import create_component_template  # noqa: E402
from lfx.graph.graph.base import Graph  # noqa: E402

from langflow_1_9_2_compat import (  # noqa: E402
    HANDLE_QUOTE,
    OBSOLETE_HANDLE_QUOTE,
    TARGET_LANGFLOW_VERSION,
    TARGET_LFX_VERSION,
    assert_target_runtime,
)


FLOW_FILES = (
    ROOT / "flows" / "reusable_data_flow" / "reusable_data_flow.json",
    ROOT / "flows" / "html_report_flow" / "html_report_flow.json",
    ROOT / "flows" / "enterprise_document_rag_flow" / "enterprise_document_rag_flow.json",
    ROOT / "flows" / "skill_based_agent_flow" / "meeting_action_skill_flow.json",
    ROOT / "flows" / "skill_based_agent_flow" / "skill_based_agent_flow.json",
    ROOT / "flows" / "ppt_reference_html_flow" / "ppt_reference_html_flow.json",
    ROOT / "flows" / "mail_attachment_summary_flow" / "mail_attachment_summary_flow.json",
    ROOT / "flows" / "mail_attachment_summary_flow" / "mail_attachment_summary_dummy_flow.json",
    ROOT / "flows" / "drm_document_text_extraction_flow" / "drm_document_text_extraction_flow.json",
    ROOT / "business_agent_design" / "flow" / "business_agent_design_complete.json",
)


def standalone_sources() -> list[Path]:
    paths: list[Path] = []
    paths.extend(sorted(ROOT.glob("components/*/*.py")))
    paths.extend(sorted(ROOT.glob("flows/*/nodes/*.py")))
    paths.extend(sorted(ROOT.glob("business_agent_design/components/*/*.py")))
    return [path for path in paths if path.name != "__init__.py"]


def _lfx_dependency_version(config: dict[str, Any]) -> str | None:
    dependencies = (
        config.get("metadata", {})
        .get("dependencies", {})
        .get("dependencies", [])
    )
    for item in dependencies:
        if isinstance(item, dict) and item.get("name") == "lfx":
            version = item.get("version")
            return None if version is None else str(version)
    return None


def validate_standalone_sources() -> int:
    paths = standalone_sources()
    seen_classes: dict[str, Path] = {}
    for path in paths:
        code = path.read_text(encoding="utf-8")
        component_class = eval_custom_component_code(code)
        config, instance = create_component_template(
            {"code": code, "output_types": []},
            module_name=(
                "agent_ground.validation."
                + ".".join(path.relative_to(ROOT).with_suffix("").parts)
            ),
        )
        if instance.__class__.__name__ != component_class.__name__:
            raise AssertionError(
                f"{path}: loader class가 다릅니다 "
                f"{component_class.__name__} != {instance.__class__.__name__}"
            )
        if not config.get("field_order") and getattr(component_class, "inputs", None):
            raise AssertionError(f"{path}: 1.9.2 입력 schema가 비었습니다.")
        if not config.get("outputs"):
            raise AssertionError(f"{path}: 1.9.2 출력 schema가 비었습니다.")
        if not config.get("metadata", {}).get("code_hash"):
            raise AssertionError(f"{path}: code hash가 없습니다.")
        lfx_version = _lfx_dependency_version(config)
        if lfx_version not in {TARGET_LFX_VERSION, None}:
            raise AssertionError(
                f"{path}: embedded LFX dependency가 다릅니다: {lfx_version}"
            )
        seen_classes.setdefault(component_class.__name__, path)
    return len(paths)


def _validate_flow_metadata(flow: dict[str, Any], path: Path) -> None:
    if flow.get("last_tested_version") != TARGET_LANGFLOW_VERSION:
        raise AssertionError(
            f"{path}: last_tested_version={flow.get('last_tested_version')}"
        )
    nodes = flow.get("data", {}).get("nodes", [])
    for graph_node in nodes:
        data = graph_node.get("data", {})
        if data.get("type") == "note":
            continue
        config = data.get("node", {})
        if config.get("lf_version") != TARGET_LANGFLOW_VERSION:
            raise AssertionError(
                f"{path}:{graph_node.get('id')} lf_version={config.get('lf_version')}"
            )
        template = config.get("template", {})
        code = template.get("code", {}).get("value") if isinstance(template, dict) else None
        if isinstance(code, str) and code.strip():
            if not config.get("metadata", {}).get("code_hash"):
                raise AssertionError(
                    f"{path}:{graph_node.get('id')} embedded code hash가 없습니다."
                )


def _validate_handles(flow: dict[str, Any], path: Path) -> None:
    for edge in flow.get("data", {}).get("edges", []):
        for key in ("sourceHandle", "targetHandle"):
            encoded = edge.get(key)
            if (
                not isinstance(encoded, str)
                or HANDLE_QUOTE not in encoded
                or OBSOLETE_HANDLE_QUOTE in encoded
            ):
                raise AssertionError(f"{path}:{edge.get('id')} {key} 인코딩 오류")
            decoded = json.loads(encoded.replace(HANDLE_QUOTE, '"'))
            if decoded != edge.get("data", {}).get(key):
                raise AssertionError(
                    f"{path}:{edge.get('id')} {key} 문자열/data 불일치"
                )


def validate_flows() -> tuple[int, int, int]:
    node_count = 0
    edge_count = 0
    for path in FLOW_FILES:
        flow = json.loads(path.read_text(encoding="utf-8-sig"))
        _validate_flow_metadata(flow, path)
        _validate_handles(flow, path)
        Graph.from_payload(
            flow["data"],
            flow_id=str(flow.get("id") or ""),
            flow_name=str(flow.get("name") or path.stem),
        )
        node_count += len(flow["data"].get("nodes", []))
        edge_count += len(flow["data"].get("edges", []))
    return len(FLOW_FILES), node_count, edge_count


def main() -> None:
    assert_target_runtime()
    source_count = validate_standalone_sources()
    flow_count, node_count, edge_count = validate_flows()
    print(
        json.dumps(
            {
                "langflow": TARGET_LANGFLOW_VERSION,
                "lfx": TARGET_LFX_VERSION,
                "standalone_sources": source_count,
                "flow_files": flow_count,
                "flow_nodes": node_count,
                "flow_edges": edge_count,
                "status": "ok",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
