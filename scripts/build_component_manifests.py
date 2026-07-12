from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
COMPONENTS_DIR = ROOT / "components"

REUSABLE_DATA_IDS = {
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
}

ENTERPRISE_DOCUMENT_RAG_IDS = {
    "document_input_normalizer",
    "pii_confidential_data_guard",
    "document_chunk_index_builder",
    "rag_request_context_normalizer",
    "acl_evidence_retriever",
    "retrieval_quality_gate",
    "rag_prompt_builder",
    "grounded_answer_builder",
    "citation_response_builder",
}

SKILL_BASED_AGENT_IDS = {
    "demo_skill_catalog_builder",
    "expense_precheck_skill_tool",
    "leave_policy_skill_tool",
    "meeting_action_skill_tool",
}

ENTERPRISE_UTILITY_IDS = {
    "multi_image_base64_encoder",
    "cached_named_run_flow_tool",
}

COMPONENT_VERSION_OVERRIDES = {
    # 외부 Tool 입력 계약이 node-ID 기반에서 고정 question으로 변경된 호환성 수정입니다.
    "cached_named_run_flow_tool": "0.2.0",
}

DIRECT_DATA_ACCESS_IDS = {
    "oracle_table_query",
    "h_api_table_request",
    "datalake_table_query",
    "goodocs_table_reader",
    "simple_api_table_request",
}

DEPENDENCY_OVERRIDES = {
    "cached_named_run_flow_tool": ["langflow", "lfx"],
    "oracle_table_query": ["oracledb"],
    "datalake_table_query": ["mysql-connector-python"],
}
DEPENDENCY_EXCLUDES = {
    # 사용자가 설치해야 하는 pip 패키지명만 보여 주고 내부 import 경로는 숨깁니다.
    "datalake_table_query": {"mysql.connector"},
}

SOURCE_FAMILY_USAGE_LABELS = {
    "direct_data_access_components": "직접 데이터 조회 Component (특정 Flow 미지정)",
    "enterprise_utility_components": "사내 공용 독립 Component (특정 Flow 미지정)",
}

ADDITIONAL_GUIDES = {
    "cached_named_run_flow_tool": "USAGE_GUIDE.md",
    "multi_image_base64_encoder": "USAGE_GUIDE.md",
    "oracle_table_query": "USAGE_GUIDE.md",
    "h_api_table_request": "USAGE_GUIDE.md",
    "datalake_table_query": "USAGE_GUIDE.md",
    "goodocs_table_reader": "USAGE_GUIDE.md",
    "simple_api_table_request": "USAGE_GUIDE.md",
}

BEGINNER_GUIDES = {
    "cached_named_run_flow_tool": "BEGINNER_GUIDE.md",
}

RISK_TAGS = {
    "oracle_data": ["external_data_access", "credentials_required"],
    "h_api_data": ["external_api_access", "credentials_required"],
    "datalake_data": ["external_data_access", "credentials_required"],
    "goodocs_data": ["external_document_access", "credentials_required"],
    "llm_caller": ["external_llm", "api_key"],
    "source_catalog_mongodb_store": ["database_write", "credentials_required"],
    "source_catalog_mongodb_loader": ["database_read", "credentials_required"],
    "html_template_renderer": ["html_output"],
    "report_api_publisher": ["external_publish", "html_output"],
    "document_input_normalizer": ["document_content", "demo_corpus"],
    "pii_confidential_data_guard": ["pii_baseline", "confidential_content"],
    "document_chunk_index_builder": ["ephemeral_index", "document_content"],
    "rag_request_context_normalizer": ["access_control", "demo_identity"],
    "acl_evidence_retriever": ["access_control", "document_content"],
    "retrieval_quality_gate": ["answer_policy"],
    "rag_prompt_builder": ["prompt_injection_boundary", "document_content"],
    "grounded_answer_builder": ["llm_output_validation", "document_content"],
    "citation_response_builder": ["citation_output", "document_content"],
    "demo_skill_catalog_builder": [
        "dynamic_agent_instruction",
        "skill_catalog",
        "tool_allowlist",
        "prompt_injection_boundary",
    ],
    "expense_precheck_skill_tool": [
        "agent_tool",
        "skill_as_tool",
        "read_only_demo",
        "deterministic_calculation",
    ],
    "leave_policy_skill_tool": [
        "agent_tool",
        "skill_as_tool",
        "read_only_demo",
        "date_calculation",
    ],
    "meeting_action_skill_tool": [
        "agent_tool",
        "skill_as_tool",
        "read_only_demo",
        "structured_text",
    ],
    "multi_image_base64_encoder": [
        "file_upload",
        "image_content",
        "base64_payload",
        "payload_size",
        "svg_optional",
    ],
    "cached_named_run_flow_tool": [
        "subflow_execution",
        "agent_tool",
        "graph_cache",
        "session_inheritance",
        "exact_flow_name",
        "version_coupled_internal_api",
        "provider_tool_parameter_compatibility",
        "stable_question_schema",
        "runtime_chat_input_mapping",
    ],
    "oracle_table_query": ["database_read", "credentials_required", "read_only_sql", "external_data_access"],
    "h_api_table_request": [
        "external_api_access",
        "credentials_required",
        "response_size_limit",
        "https_default",
    ],
    "datalake_table_query": [
        "database_read",
        "credentials_required",
        "cluster_api",
        "read_only_sql",
        "endpoint_allowlist",
        "verified_tls_required",
    ],
    "goodocs_table_reader": ["external_document_access", "credentials_required", "module_replacement_required"],
    "simple_api_table_request": [
        "external_api_access",
        "ssrf_review",
        "response_size_limit",
        "json_response_only",
        "https_default",
        "same_origin_redirect",
    ],
}


def component_release(component_id: str) -> dict[str, Any]:
    if component_id in REUSABLE_DATA_IDS:
        return {
            "source_family": "reusable_data_flow",
            "version": "0.9.0",
            "verified_environment": "legacy-export-1.8.2-local-static-check",
            "last_verified_at": "2026-07-10",
            "used_by_flows": ["reusable_data_flow"],
        }
    if component_id in ENTERPRISE_DOCUMENT_RAG_IDS:
        return {
            "source_family": "enterprise_document_rag_flow",
            "version": "0.1.0",
            "verified_environment": "langflow-1.8.2-local-runtime-validation",
            "last_verified_at": "2026-07-11",
            "used_by_flows": ["enterprise_document_rag_flow"],
        }
    if component_id in SKILL_BASED_AGENT_IDS:
        used_by = ["skill_based_agent_flow"]
        if component_id == "demo_skill_catalog_builder":
            used_by = ["skill_based_agent_flow", "meeting_action_skill_flow"]
        elif component_id == "meeting_action_skill_tool":
            used_by = ["meeting_action_skill_flow"]
        return {
            "source_family": "skill_based_agent_flow",
            "version": "0.1.0",
            "verified_environment": "langflow-1.8.2-template-and-isolated-contract-validation",
            "last_verified_at": "2026-07-12",
            "used_by_flows": used_by,
        }
    if component_id in ENTERPRISE_UTILITY_IDS:
        used_by = ["skill_based_agent_flow"] if component_id == "cached_named_run_flow_tool" else []
        return {
            "source_family": "enterprise_utility_components",
            "version": COMPONENT_VERSION_OVERRIDES.get(component_id, "0.1.0"),
            "verified_environment": "langflow-1.8.2-local-runtime-validation",
            "last_verified_at": "2026-07-12",
            "used_by_flows": used_by,
        }
    if component_id in DIRECT_DATA_ACCESS_IDS:
        return {
            "source_family": "direct_data_access_components",
            "version": "0.1.0",
            "verified_environment": "langflow-1.8.2-template-and-isolated-contract-validation",
            "last_verified_at": "2026-07-12",
            "used_by_flows": [],
        }
    return {
        "source_family": "html_report_flow",
        "version": "0.9.0",
        "verified_environment": "legacy-export-1.8.2-local-static-check",
        "last_verified_at": "2026-07-10",
        "used_by_flows": ["html_report_flow"],
    }


def literal(node: ast.AST | None, constants: dict[str, Any] | None = None) -> Any:
    if node is None:
        return None
    constants = constants or {}
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.List):
        return [literal(item, constants) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(literal(item, constants) for item in node.elts)
    if isinstance(node, ast.Set):
        return {literal(item, constants) for item in node.elts}
    if isinstance(node, ast.Dict):
        return {
            literal(key, constants): literal(value, constants)
            for key, value in zip(node.keys, node.values, strict=True)
        }
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for item in node.values:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                parts.append(item.value)
            elif isinstance(item, ast.FormattedValue):
                value = literal(item.value, constants)
                if value is None:
                    return None
                parts.append(str(value))
            else:
                return None
        return "".join(parts)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = literal(node.operand, constants)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value if isinstance(node.op, ast.UAdd) else -value
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv)):
        left = literal(node.left, constants)
        right = literal(node.right, constants)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if right != 0:
                return left // right
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return None


def module_constants(tree: ast.Module) -> dict[str, Any]:
    """Resolve simple module constants without importing component code."""
    result: dict[str, Any] = {}
    for statement in tree.body:
        target_name: str | None = None
        value_node: ast.AST | None = None
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            if isinstance(target, ast.Name):
                target_name = target.id
                value_node = statement.value
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            target_name = statement.target.id
            value_node = statement.value
        if target_name and value_node is not None:
            value = literal(value_node, result)
            if value is not None:
                result[target_name] = value
    return result


def call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return "Unknown"


def class_attributes(class_node: ast.ClassDef, constants: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for statement in class_node.body:
        target_name: str | None = None
        value_node: ast.AST | None = None
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            if isinstance(target, ast.Name):
                target_name = target.id
                value_node = statement.value
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            target_name = statement.target.id
            value_node = statement.value

        if not target_name or value_node is None:
            continue
        if target_name in {"display_name", "description", "icon", "name"}:
            result[target_name] = literal(value_node, constants)
        if target_name in {"inputs", "outputs"} and isinstance(value_node, (ast.List, ast.Tuple)):
            fields = []
            for item in value_node.elts:
                if not isinstance(item, ast.Call):
                    continue
                field: dict[str, Any] = {"type": call_name(item.func)}
                for keyword in item.keywords:
                    if keyword.arg in {
                        "name",
                        "display_name",
                        "method",
                        "required",
                        "advanced",
                        "value",
                        "info",
                        "file_types",
                        "fileTypes",
                        "is_list",
                        "list",
                        "options",
                        "input_types",
                        "types",
                        "group_outputs",
                        "show",
                        "override_skip",
                        "tool_mode",
                    }:
                        field[keyword.arg] = literal(keyword.value, constants)
                fields.append(field)
            result[target_name] = fields
    return result


def get_runtime_class(tree: ast.Module) -> tuple[ast.ClassDef, dict[str, Any]]:
    constants = module_constants(tree)
    candidates = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        attributes = class_attributes(node, constants)
        if attributes.get("display_name") and attributes.get("outputs"):
            candidates.append((node, attributes))
    if len(candidates) != 1:
        names = [item[0].name for item in candidates]
        raise ValueError(f"Expected one Langflow component class, found {names}")
    return candidates[0]


def dependencies(tree: ast.Module) -> list[str]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise ValueError(f"Relative import is not allowed: {node.module}")
            if node.module:
                found.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"import_module", "ensure_package"} and node.args:
                package_name = literal(node.args[0])
                if isinstance(package_name, str) and package_name:
                    found.add(package_name)
    standard = set(sys.stdlib_module_names) | {"__future__"}
    return sorted(name for name in found if name not in standard)


def markdown_table(fields: list[dict[str, Any]], output: bool = False) -> str:
    if not fields:
        return "등록된 항목이 없습니다."
    if output:
        rows = ["| 화면 이름 | 코드 이름 | 타입 | 실행 method |", "| --- | --- | --- | --- |"]
        for field in fields:
            contract_types = field.get("types") or field.get("type", "-")
            type_text = ", ".join(str(item) for item in contract_types) if isinstance(contract_types, list) else str(contract_types)
            rows.append(
                f"| {field.get('display_name', '-')} | `{field.get('name', '-')}` | "
                f"`{type_text}` | `{field.get('method', '-')}` |"
            )
    else:
        rows = ["| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |", "| --- | --- | --- | --- | --- | --- |"]
        for field in fields:
            contract_types = field.get("input_types") or field.get("type", "-")
            type_text = ", ".join(str(item) for item in contract_types) if isinstance(contract_types, list) else str(contract_types)
            list_value = bool(field.get("is_list", field.get("list", False)))
            rows.append(
                f"| {field.get('display_name', '-')} | `{field.get('name', '-')}` | "
                f"`{type_text}` | {list_value} | {bool(field.get('required', False))} | "
                f"{bool(field.get('advanced', False))} |"
            )
    return "\n".join(rows)


def main() -> None:
    manifests = []
    for component_dir in sorted(path for path in COMPONENTS_DIR.iterdir() if path.is_dir()):
        component_id = component_dir.name
        source_files = list(component_dir.glob("*.py"))
        if len(source_files) != 1:
            raise ValueError(f"{component_id}: expected one Python file, found {len(source_files)}")
        source_file = source_files[0]
        source_text = source_file.read_text(encoding="utf-8-sig")
        tree = ast.parse(source_text, filename=str(source_file))
        class_node, attributes = get_runtime_class(tree)
        release = component_release(component_id)
        source_family = release["source_family"]
        manifest = {
            "id": component_id,
            "name_ko": attributes["display_name"],
            "class_name": class_node.name,
            "asset_type": "component",
            "status": "user_testing",
            "version": release["version"],
            "standalone": True,
            "reusability": "shared",
            "source_family": source_family,
            "summary_ko": attributes.get("description", ""),
            "source_file": source_file.name,
            "inputs": attributes.get("inputs", []),
            "outputs": attributes.get("outputs", []),
            "dependencies": sorted(
                (set(dependencies(tree)) | set(DEPENDENCY_OVERRIDES.get(component_id, [])))
                - set(DEPENDENCY_EXCLUDES.get(component_id, set()))
            ),
            "used_by_flows": release["used_by_flows"],
            "risk_tags": RISK_TAGS.get(component_id, []),
            "verified_environment": release["verified_environment"],
            "last_verified_at": release["last_verified_at"],
            "documentation_path": f"html/components/{component_id}/index.html",
            "approved_by_user_at": None,
        }
        if guide_file := ADDITIONAL_GUIDES.get(component_id):
            manifest["guide_file"] = guide_file
        if beginner_guide_file := BEGINNER_GUIDES.get(component_id):
            manifest["beginner_guide_file"] = beginner_guide_file
            manifest["beginner_documentation_path"] = f"html/components/{component_id}/beginner.html"
        (component_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        usage_label = (
            source_family
            if manifest["used_by_flows"]
            else SOURCE_FAMILY_USAGE_LABELS.get(source_family, "공용 독립 Component (특정 Flow 미지정)")
        )
        guide_section = ""
        if manifest.get("guide_file"):
            guide_section = f"""
## 상세 사용 가이드

[`{manifest['guide_file']}`]({manifest['guide_file']})에서 연결 방법, 운영 조건과 사용자 확인 항목을 확인합니다.
"""
        if manifest.get("beginner_guide_file"):
            guide_section += f"""

## 처음 보는 사용자를 위한 설명

[`{manifest['beginner_guide_file']}`]({manifest['beginner_guide_file']})에서 기본 Run Flow를 그대로 사용하면서 별도 Component로 감싼 이유를 쉬운 예시로 확인합니다.
"""
        readme = f"""# {manifest['name_ko']}

{manifest['summary_ko']}

## 상태

- ID: `{component_id}`
- 버전: `{manifest['version']}`
- 상태: `user_testing`
- Standalone: `true`
- 사용 범위: `{usage_label}`

## 입력

{markdown_table(manifest['inputs'])}

## 출력

{markdown_table(manifest['outputs'], output=True)}

{guide_section}

## 등록

`{source_file.name}` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
"""
        (component_dir / "README.md").write_text(readme, encoding="utf-8")
        manifests.append(manifest)
    print(f"Generated {len(manifests)} component manifests and README files")


if __name__ == "__main__":
    main()
