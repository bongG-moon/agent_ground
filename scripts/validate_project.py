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
    flow_dirs = sorted(path for path in (ROOT / "flows").iterdir() if path.is_dir())
    for flow_dir in flow_dirs:
        manifest = json.loads((flow_dir / "manifest.json").read_text(encoding="utf-8"))
        refs = json.loads((flow_dir / manifest["component_refs_file"]).read_text(encoding="utf-8"))
        flow_export = json.loads((flow_dir / manifest["flow_file"]).read_text(encoding="utf-8-sig"))
        if not flow_export.get("data", {}).get("nodes"):
            raise AssertionError(f"{flow_dir.name}: no nodes in Flow JSON")
        if not flow_export.get("data", {}).get("edges"):
            raise AssertionError(f"{flow_dir.name}: no edges in Flow JSON")
        for ref in refs["components"]:
            component = component_manifests.get(ref["id"])
            if component is None:
                raise AssertionError(f"{flow_dir.name}: missing component {ref['id']}")
            if component["version"] != ref["version"]:
                raise AssertionError(f"{flow_dir.name}: version mismatch for {ref['id']}")
        if not (ROOT / manifest["documentation_path"]).is_file():
            raise AssertionError(f"{flow_dir.name}: documentation page missing")
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
    expected = len(list((ROOT / "components").glob("*/manifest.json"))) + len(
        list((ROOT / "flows").glob("*/manifest.json"))
    )
    if len(assets) != expected:
        raise AssertionError(f"Registry count {len(assets)} != {expected}")
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
