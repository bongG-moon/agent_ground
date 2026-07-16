from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

COMPONENT_IDS = {
    "drm_document_text_extractor",
    "multi_image_base64_encoder",
    "cached_named_run_flow_tool",
    "oracle_table_query",
    "h_api_table_request",
    "datalake_table_query",
    "goodocs_table_reader",
    "simple_api_table_request",
    "expense_precheck_skill_tool",
    "leave_policy_skill_tool",
    "meeting_action_skill_tool",
    "document_input_normalizer",
    "pii_confidential_data_guard",
    "document_chunk_index_builder",
    "acl_evidence_retriever",
    "retrieval_quality_gate",
    "grounded_answer_builder",
    "html_report_data_profile_builder",
    "html_template_renderer",
    "html_presentation_renderer",
    "report_api_publisher",
}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    assets = []
    component_paths = {
        path.parent.name: path for path in (ROOT / "components").glob("*/manifest.json")
    }
    if set(component_paths) != COMPONENT_IDS:
        raise ValueError(
            f"Registry 동기화 중 Component {len(COMPONENT_IDS)}개 자격 목록이 일치하지 않습니다: "
            f"missing={sorted(COMPONENT_IDS - set(component_paths))}, "
            f"unexpected={sorted(set(component_paths) - COMPONENT_IDS)}"
        )
    for component_id in sorted(COMPONENT_IDS):
        asset = read_json(component_paths[component_id])
        if asset.get("asset_type") != "component":
            raise ValueError(f"{component_id}: asset_type은 component여야 합니다.")
        if asset.get("packaging") != "standalone":
            raise ValueError(f"{component_id}: packaging은 standalone이어야 합니다.")
        if asset.get("component_scope") not in {"general", "domain"}:
            raise ValueError(f"{component_id}: component_scope은 general 또는 domain이어야 합니다.")
        assets.append(asset)

    flow_paths = sorted((ROOT / "flows").glob("*/manifest.json"))
    if len(flow_paths) != 7:
        raise ValueError(f"Registry에는 Flow manifest 7개가 필요합니다: {flow_paths}")
    for manifest_path in flow_paths:
        asset = read_json(manifest_path)
        if asset.get("asset_type") != "flow":
            raise ValueError(f"{manifest_path.parent.name}: asset_type은 flow여야 합니다.")
        assets.append(asset)

    if any(asset.get("asset_type") == "flow_node" for asset in assets):
        raise ValueError("Flow 내부 node는 Registry 자산에 포함할 수 없습니다.")
    assets.sort(key=lambda item: (item["asset_type"], item["id"]))
    payload = {
        "schema_version": "1.0",
        "generated_at": datetime.now().astimezone().date().isoformat(),
        "source_of_truth": "21 qualified Component manifests and 7 Flow manifests; internal nodes are excluded",
        "publication_rule": "Only approved assets may be used by Business Agent Design recommendations.",
        "assets": assets,
    }
    target = ROOT / "registry" / "capabilities.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Synced {len(assets)} assets to {target}")


if __name__ == "__main__":
    main()
