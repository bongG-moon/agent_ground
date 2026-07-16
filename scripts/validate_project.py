from __future__ import annotations

import ast
import importlib.util
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
HTML_ROOT = ROOT / "html"

GENERAL_COMPONENT_IDS = {
    "drm_document_text_extractor",
    "multi_image_base64_encoder",
    "cached_named_run_flow_tool",
    "oracle_table_query",
    "h_api_table_request",
    "datalake_table_query",
    "goodocs_table_reader",
    "simple_api_table_request",
}
DOMAIN_COMPONENT_IDS = {
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
COMPONENT_IDS = GENERAL_COMPONENT_IDS | DOMAIN_COMPONENT_IDS
INTERNAL_NODE_IDS_BY_FLOW = {
    "drm_document_text_extraction_flow": set(),
    "reusable_data_flow": {
        "data_request_normalizer",
        "oracle_data",
        "h_api_data",
        "datalake_data",
        "goodocs_data",
        "data_result_merger",
        "data_output_builder",
        "source_catalog_normalizer",
        "llm_caller",
        "source_catalog_mongodb_store",
        "source_catalog_mongodb_loader",
        "html_report_datasets_adapter",
    },
    "html_report_flow": {
        "demo_report_request_loader",
        "html_component_catalog_builder",
        "auto_html_plan_builder",
        "llm_html_plan_prompt_builder",
        "llm_html_plan_normalizer",
        "html_source_output",
    },
    "enterprise_document_rag_flow": {
        "rag_request_context_normalizer",
        "rag_prompt_builder",
        "citation_response_builder",
    },
    "skill_based_agent_flow": {"demo_skill_catalog_builder"},
    "mail_attachment_summary_flow": {
        "ews_mail_attachment_reader",
    },
    "ppt_reference_html_flow": {
        "presentation_request_builder",
        "presentation_reference_analyzer",
        "presentation_design_policy_builder",
        "presentation_plan_generator",
        "presentation_plan_normalizer",
        "presentation_quality_gate",
        "presentation_html_source_output",
    },
}
INTERNAL_NODE_IDS = set().union(*INTERNAL_NODE_IDS_BY_FLOW.values())


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.base = "./"
        self.references: list[tuple[str, str]] = []
        self.ids: set[str] = set()
        self.has_main = False
        self.has_home_link = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.add(str(attributes["id"]))
        if tag == "base" and attributes.get("href"):
            self.base = str(attributes["href"])
        if tag in {"a", "link"} and attributes.get("href"):
            self.references.append(("href", str(attributes["href"])))
        if tag in {"script", "img"} and attributes.get("src"):
            self.references.append(("src", str(attributes["src"])))
        if tag == "main":
            self.has_main = True
        if tag == "a" and attributes.get("href") in {"index.html", "../index.html", "../../index.html"}:
            self.has_home_link = True


def json_files() -> list[Path]:
    return sorted(path for path in ROOT.rglob("*.json") if ".git" not in path.parts)


def validate_json() -> int:
    for path in json_files():
        json.loads(path.read_text(encoding="utf-8-sig"))
    return len(json_files())


def validate_python() -> int:
    paths = sorted(path for path in ROOT.rglob("*.py") if ".git" not in path.parts)
    for path in paths:
        source = path.read_text(encoding="utf-8-sig")
        compile(source, str(path), "exec")
    return len(paths)


def validate_components() -> int:
    component_dirs = sorted(path for path in (ROOT / "components").iterdir() if path.is_dir())
    actual_ids = {path.name for path in component_dirs}
    if actual_ids != COMPONENT_IDS:
        raise AssertionError(
            f"components/ must contain exactly {len(COMPONENT_IDS)} qualified Components: "
            f"missing={sorted(COMPONENT_IDS - actual_ids)}, "
            f"unexpected={sorted(actual_ids - COMPONENT_IDS)}"
        )
    for component_dir in component_dirs:
        python_files = list(component_dir.glob("*.py"))
        if len(python_files) != 1:
            raise AssertionError(f"{component_dir.name}: expected one standalone Python file")
        tree = ast.parse(python_files[0].read_text(encoding="utf-8-sig"))
        relative_imports = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.level]
        if relative_imports:
            raise AssertionError(f"{component_dir.name}: relative import found")
        manifest = json.loads((component_dir / "manifest.json").read_text(encoding="utf-8"))
        if not manifest.get("standalone"):
            raise AssertionError(f"{component_dir.name}: standalone flag is false")
        if manifest["id"] != component_dir.name:
            raise AssertionError(f"{component_dir.name}: manifest id mismatch")
        if manifest["status"] != "user_testing":
            raise AssertionError(f"{component_dir.name}: unexpected publication status")
        if manifest.get("asset_type") != "component":
            raise AssertionError(f"{component_dir.name}: asset_type must be component")
        if manifest.get("packaging") != "standalone":
            raise AssertionError(f"{component_dir.name}: packaging must be standalone")
        expected_scope = "general" if component_dir.name in GENERAL_COMPONENT_IDS else "domain"
        if manifest.get("component_scope") != expected_scope:
            raise AssertionError(
                f"{component_dir.name}: component_scope must be {expected_scope}"
            )
        if "catalog_scope" in manifest:
            raise AssertionError(f"{component_dir.name}: obsolete catalog_scope field found")
        qualification = manifest.get("qualification")
        if not isinstance(qualification, dict) or qualification.get("decision") != "qualified_component":
            raise AssertionError(f"{component_dir.name}: qualification decision missing")
        criteria = qualification.get("criteria")
        required_true = {
            "independent_input_output",
            "functional_completeness",
            "reusable_unit",
            "validation_or_error_handling",
        }
        if not isinstance(criteria, dict) or any(criteria.get(key) is not True for key in required_true):
            raise AssertionError(f"{component_dir.name}: qualification criteria incomplete")
        if criteria.get("payload_coupling") not in {"low", "domain_contract"}:
            raise AssertionError(f"{component_dir.name}: invalid payload coupling qualification")
        owner_usage = manifest.get("owner_usage")
        if not isinstance(owner_usage, dict) or not owner_usage.get("owner") or not owner_usage.get("usage"):
            raise AssertionError(f"{component_dir.name}: owner_usage missing")
        if owner_usage.get("used_by_flows") != manifest.get("used_by_flows"):
            raise AssertionError(f"{component_dir.name}: owner_usage flow list mismatch")
        if not (ROOT / manifest["documentation_path"]).is_file():
            raise AssertionError(f"{component_dir.name}: documentation page missing")
        if manifest.get("beginner_documentation_path") and not (
            ROOT / manifest["beginner_documentation_path"]
        ).is_file():
            raise AssertionError(f"{component_dir.name}: beginner documentation page missing")
    return len(component_dirs)


def validate_flows() -> int:
    component_manifests = {
        path.parent.name: json.loads(path.read_text(encoding="utf-8"))
        for path in (ROOT / "components").glob("*/manifest.json")
    }
    if set(component_manifests) != COMPONENT_IDS:
        raise AssertionError(
            f"Flow validation requires the exact {len(COMPONENT_IDS)}-Component manifest set"
        )
    flow_dirs = sorted(path for path in (ROOT / "flows").iterdir() if path.is_dir())
    if {path.name for path in flow_dirs} != set(INTERNAL_NODE_IDS_BY_FLOW):
        raise AssertionError(
            f"Flow directory set mismatch: {[path.name for path in flow_dirs]}"
        )
    all_internal_ids: set[str] = set()
    for flow_dir in flow_dirs:
        manifest = json.loads((flow_dir / "manifest.json").read_text(encoding="utf-8"))
        refs = json.loads((flow_dir / manifest["component_refs_file"]).read_text(encoding="utf-8"))
        if manifest.get("internal_nodes_file") != "internal_nodes.json":
            raise AssertionError(f"{flow_dir.name}: internal_nodes_file must be internal_nodes.json")
        internal_path = flow_dir / manifest["internal_nodes_file"]
        if not internal_path.is_file():
            raise AssertionError(f"{flow_dir.name}: internal_nodes.json missing")
        internal_payload = json.loads(internal_path.read_text(encoding="utf-8"))
        if internal_payload.get("flow_id") != flow_dir.name:
            raise AssertionError(f"{flow_dir.name}: internal_nodes flow_id mismatch")
        if internal_payload.get("asset_type") != "flow_node":
            raise AssertionError(f"{flow_dir.name}: internal_nodes asset_type must be flow_node")
        if internal_payload.get("owner_flow") != flow_dir.name:
            raise AssertionError(f"{flow_dir.name}: internal_nodes owner_flow mismatch")
        internal_nodes = internal_payload.get("nodes")
        if not isinstance(internal_nodes, list):
            raise AssertionError(f"{flow_dir.name}: internal_nodes nodes must be a list")
        internal_by_id = {
            str(node.get("id")): node for node in internal_nodes if isinstance(node, dict)
        }
        if len(internal_by_id) != len(internal_nodes):
            raise AssertionError(f"{flow_dir.name}: duplicate or invalid internal node entry")
        expected_internal_ids = INTERNAL_NODE_IDS_BY_FLOW[flow_dir.name]
        if set(internal_by_id) != expected_internal_ids:
            raise AssertionError(
                f"{flow_dir.name}: internal node set mismatch; "
                f"expected={sorted(expected_internal_ids)}, got={sorted(internal_by_id)}"
            )
        all_internal_ids.update(internal_by_id)

        flow_export = json.loads((flow_dir / manifest["flow_file"]).read_text(encoding="utf-8-sig"))
        flow_exports_by_name = {manifest["flow_file"]: flow_export}
        for subflow_file in manifest.get("subflow_files", []):
            subflow_path = flow_dir / subflow_file
            if not subflow_path.is_file():
                raise AssertionError(f"{flow_dir.name}: missing subflow file {subflow_file}")
            flow_exports_by_name[subflow_file] = json.loads(
                subflow_path.read_text(encoding="utf-8-sig")
            )
        if not flow_export.get("data", {}).get("nodes"):
            raise AssertionError(f"{flow_dir.name}: no nodes in Flow JSON")
        if not flow_export.get("data", {}).get("edges"):
            raise AssertionError(f"{flow_dir.name}: no edges in Flow JSON")
        for ref in refs["components"]:
            if ref["id"] in INTERNAL_NODE_IDS:
                raise AssertionError(
                    f"{flow_dir.name}: internal node {ref['id']} must not be in component_refs.json"
                )
            component = component_manifests.get(ref["id"])
            if component is None:
                raise AssertionError(f"{flow_dir.name}: missing component {ref['id']}")
            if component["version"] != ref["version"]:
                raise AssertionError(f"{flow_dir.name}: version mismatch for {ref['id']}")
        expected_classes = {component_manifests[ref["id"]]["class_name"] for ref in refs["components"]}
        embedded_types = {
            node.get("data", {}).get("type")
            for exported_flow in flow_exports_by_name.values()
            for node in exported_flow.get("data", {}).get("nodes", [])
            if node.get("data", {}).get("type")
        }
        missing_classes = expected_classes - embedded_types
        if manifest.get("runtime_ready", True) and missing_classes:
            raise AssertionError(f"{flow_dir.name}: referenced component classes not embedded: {sorted(missing_classes)}")

        for node_id, node in internal_by_id.items():
            if node.get("asset_type") != "flow_node":
                raise AssertionError(f"{flow_dir.name}/{node_id}: asset_type must be flow_node")
            if node.get("owner_flow") != flow_dir.name:
                raise AssertionError(f"{flow_dir.name}/{node_id}: owner_flow mismatch")
            if not all(node.get(key) for key in ("class_name", "name_ko", "version", "source_path", "summary_ko")):
                raise AssertionError(f"{flow_dir.name}/{node_id}: internal node metadata incomplete")
            source_path = (flow_dir / str(node["source_path"])).resolve()
            nodes_root = (flow_dir / "nodes").resolve()
            if source_path.parent != nodes_root or not source_path.is_file() or source_path.suffix != ".py":
                raise AssertionError(f"{flow_dir.name}/{node_id}: invalid source_path {source_path}")
            source_tree = ast.parse(source_path.read_text(encoding="utf-8-sig"))
            if any(isinstance(item, ast.ImportFrom) and item.level for item in ast.walk(source_tree)):
                raise AssertionError(f"{flow_dir.name}/{node_id}: relative import found")
            class_names = {item.name for item in source_tree.body if isinstance(item, ast.ClassDef)}
            if node["class_name"] not in class_names:
                raise AssertionError(f"{flow_dir.name}/{node_id}: class_name not found in source")
            embedded_in = node.get("embedded_in")
            if not isinstance(embedded_in, list):
                raise AssertionError(f"{flow_dir.name}/{node_id}: embedded_in must be a list")
            for export_name in embedded_in:
                exported_flow = flow_exports_by_name.get(export_name)
                if exported_flow is None:
                    raise AssertionError(
                        f"{flow_dir.name}/{node_id}: unknown embedded Flow {export_name}"
                    )
                export_types = {
                    item.get("data", {}).get("type")
                    for item in exported_flow.get("data", {}).get("nodes", [])
                }
                if node["class_name"] not in export_types:
                    raise AssertionError(
                        f"{flow_dir.name}/{node_id}: class not embedded in {export_name}"
                    )
            if manifest.get("runtime_ready", True) and not embedded_in:
                raise AssertionError(
                    f"{flow_dir.name}/{node_id}: runtime-ready Flow node needs embedded_in"
                )
        if not manifest.get("runtime_ready", True):
            if manifest.get("status") != "building" or not manifest.get("integrity_issue"):
                raise AssertionError(f"{flow_dir.name}: quarantined Flow needs building status and integrity_issue")
        if not (ROOT / manifest["documentation_path"]).is_file():
            raise AssertionError(f"{flow_dir.name}: documentation page missing")
    if all_internal_ids != INTERNAL_NODE_IDS:
        raise AssertionError(
            f"Internal node ownership does not cover the exact {len(INTERNAL_NODE_IDS)}-node set"
        )
    project_bundle = json.loads((ROOT / "flows" / "00_AGENT_GROUND_ALL_FLOWS.json").read_text(encoding="utf-8"))
    project_names = [item.get("name") for item in project_bundle.get("flows", [])]
    if "업무분석flow" in project_names:
        raise AssertionError("quarantined reusable_data_flow donor must not be included in the project bundle")
    if len(project_names) != 7:
        raise AssertionError(f"project bundle must contain 7 runnable flows, got {project_names}")
    return len(flow_dirs)


def resolve_reference(page: Path, base: str, reference: str) -> tuple[Path, str]:
    split = urlsplit(reference)
    target_text = unquote(split.path)
    base_dir = (page.parent / base).resolve()
    target = (base_dir / target_text).resolve() if target_text else page.resolve()
    return target, split.fragment


def validate_html() -> tuple[int, int]:
    pages = sorted(HTML_ROOT.rglob("*.html"))
    outside = [path for path in ROOT.rglob("*.html") if HTML_ROOT not in path.parents]
    if outside:
        raise AssertionError(f"HTML files outside html/: {outside}")
    checked_links = 0
    for page in pages:
        parser = PageParser()
        parser.feed(page.read_text(encoding="utf-8"))
        if not parser.has_main:
            raise AssertionError(f"{page}: main landmark missing")
        if not parser.has_home_link:
            raise AssertionError(f"{page}: home navigation missing")
        for kind, reference in parser.references:
            if reference.startswith(("http://", "https://", "mailto:", "tel:", "data:")):
                continue
            target, fragment = resolve_reference(page, parser.base, reference)
            if not target.exists():
                raise AssertionError(f"{page}: broken {kind} {reference} -> {target}")
            if fragment and target == page.resolve() and fragment not in parser.ids:
                raise AssertionError(f"{page}: missing fragment #{fragment}")
            checked_links += 1
    return len(pages), checked_links


def validate_registry() -> int:
    registry = json.loads((ROOT / "registry" / "capabilities.json").read_text(encoding="utf-8"))
    assets = registry["assets"]
    component_assets = [item for item in assets if item.get("asset_type") == "component"]
    flow_assets = [item for item in assets if item.get("asset_type") == "flow"]
    unexpected_types = sorted(
        {str(item.get("asset_type")) for item in assets} - {"component", "flow"}
    )
    if unexpected_types:
        raise AssertionError(f"Registry contains non-publishable asset types: {unexpected_types}")
    if {item.get("id") for item in component_assets} != COMPONENT_IDS:
        raise AssertionError(
            f"Registry Component set must match the exact {len(COMPONENT_IDS)} qualified Components"
        )
    expected_flow_ids = set(INTERNAL_NODE_IDS_BY_FLOW)
    if {item.get("id") for item in flow_assets} != expected_flow_ids:
        raise AssertionError("Registry Flow set must match the exact 7 Flow manifests")
    if len(assets) != 28:
        raise AssertionError(f"Registry count {len(assets)} != 28")
    if any(item.get("id") in INTERNAL_NODE_IDS for item in assets):
        raise AssertionError(
            f"Registry must exclude all {len(INTERNAL_NODE_IDS)} Flow internal nodes"
        )
    if any(item["status"] == "approved" for item in assets):
        raise AssertionError("No asset should be approved before user validation")
    return len(assets)


def validate_site_contract() -> None:
    css = (HTML_ROOT / "assets" / "css" / "site.css").read_text(encoding="utf-8")
    for marker in ("@media (max-width: 980px)", "@media (max-width: 660px)", "prefers-reduced-motion"):
        if marker not in css:
            raise AssertionError(f"Responsive/accessibility marker missing: {marker}")
    business_root = ROOT / "business_agent_design"
    main_components = sorted((business_root / "components" / "main").glob("*.py"))
    admin_components = sorted((business_root / "components" / "catalog_admin").glob("*.py"))
    if len(main_components) != 10 or len(admin_components) != 5:
        raise AssertionError(f"Business Agent Design component count mismatch: main={len(main_components)}, admin={len(admin_components)}")
    individual_path = business_root / "flow" / "business_agent_design_complete.json"
    bundle_path = business_root / "flow" / "00_business_agent_design_ALL_FLOWS.json"
    project_bundle_path = ROOT / "flows" / "00_AGENT_GROUND_ALL_FLOWS.json"
    for path in (individual_path, bundle_path, project_bundle_path):
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            raise AssertionError(f"Langflow JSON must be UTF-8 without BOM: {path}")
    if not bundle_path.read_bytes().startswith(b'{"flows":['):
        raise AssertionError("Business bundle must start exactly with {\"flows\":[")
    if not project_bundle_path.read_bytes().startswith(b'{"flows":['):
        raise AssertionError("Project bundle must start exactly with {\"flows\":[")
    individual = json.loads(individual_path.read_text(encoding="utf-8"))
    if len(individual.get("data", {}).get("nodes", [])) != 24 or len(individual.get("data", {}).get("edges", [])) != 34:
        raise AssertionError("Business Agent Design flow must contain 24 nodes and 34 edges")
    for edge in individual["data"]["edges"]:
        for handle_name in ("sourceHandle", "targetHandle"):
            encoded = edge.get(handle_name, "")
            if "┇" in encoded or "œ" not in encoded:
                raise AssertionError(f"Invalid Langflow 1.8.2 handle delimiter: {edge.get('id')}")
            decoded = json.loads(encoded.replace("œ", '"'))
            if decoded != edge.get("data", {}).get(handle_name):
                raise AssertionError(f"Langflow handle data mismatch: {edge.get('id')} {handle_name}")
    business_page = (HTML_ROOT / "business-agent-design" / "index.html").read_text(encoding="utf-8")
    business_script = (HTML_ROOT / "assets" / "js" / "business-design.js").read_text(encoding="utf-8")
    required_markers = {
        "24 nodes · 34 connections",
        "business_agent_design_complete.json",
        "00_business_agent_design_ALL_FLOWS.json",
        'class="flow-board flow-board-before"',
        'class="flow-board flow-board-after"',
        'class="flow-decision"',
        'class="branch-split"',
        'id="improvementPanel"',
    }
    missing_markers = sorted(marker for marker in required_markers if marker not in business_page)
    if missing_markers:
        raise AssertionError(f"Business Flow Chart markers missing: {missing_markers}")
    improvement_ids = set(re.findall(r'data-improvement="([^"]+)"', business_page))
    if len(improvement_ids) < 8:
        raise AssertionError("Business Flow Chart needs improvement actions for changed nodes")
    for improvement_id in improvement_ids:
        if f'"{improvement_id}":' not in business_script and f"    {improvement_id}:" not in business_script:
            raise AssertionError(f"Missing improvement detail data: {improvement_id}")


def validate_training_preservation() -> dict[str, int | bool]:
    importer_path = ROOT / "scripts" / "import_training_portal.py"
    spec = importlib.util.spec_from_file_location("agent_ground_training_importer", importer_path)
    if spec is None or spec.loader is None:
        raise AssertionError("Could not load training portal importer")
    importer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(importer)

    legacy_path = importer.find_legacy_root() / "LANGFLOW_INTERNAL_TRAINING_PORTAL.html"
    source = legacy_path.read_text(encoding="utf-8-sig")
    target_path = HTML_ROOT / "training" / "index.html"
    target = target_path.read_text(encoding="utf-8")
    source_content = importer.extract_content(source)
    if source_content not in target:
        raise AssertionError("Legacy training content area is not intact in the redesigned page")

    source_audit = importer.ContentAudit()
    source_audit.feed(source)
    target_audit = importer.ContentAudit()
    target_audit.feed(target)
    if source_audit.headings != target_audit.headings:
        raise AssertionError("Legacy training headings were not preserved")
    if not set(source_audit.references).issubset(set(target_audit.references)):
        raise AssertionError("Legacy training references were not preserved")
    return {
        "source_lines": len(source.splitlines()),
        "headings": len(source_audit.headings),
        "references": len(source_audit.references),
        "content_markup_preserved": True,
        "design_mode": "agent_ground_restructured",
    }


def main() -> None:
    json_count = validate_json()
    python_count = validate_python()
    component_count = validate_components()
    flow_count = validate_flows()
    page_count, link_count = validate_html()
    registry_count = validate_registry()
    validate_site_contract()
    training_preservation = validate_training_preservation()
    print(
        "VALIDATION_OK",
        json.dumps(
            {
                "json_files": json_count,
                "python_files": python_count,
                "standalone_components": component_count,
                "flows": flow_count,
                "html_pages": page_count,
                "local_links": link_count,
                "registry_assets": registry_count,
                "business_agent_design": "user_testing_implemented",
                "training_preservation": training_preservation,
            },
            ensure_ascii=False,
        ),
    )


if __name__ == "__main__":
    main()
