from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_ROOT = PROJECT_ROOT / "components" / "single_flow"


def _load_component(filename: str):
    module_name = "single_flow_test_" + filename.replace(".py", "").replace("-", "_")
    path = COMPONENT_ROOT / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


M00 = _load_component("00_business_design_input.py")
M01 = _load_component("01_catalog_json_loader.py")
M02 = _load_component("02_local_catalog_ranker.py")
M03 = _load_component("03_business_design_prompt_builder.py")


def _request(description: str, instructions: str = "", final_refinement_instructions: str = "") -> dict:
    component = M00.BusinessDesignInputComponent()
    component.description = description
    component.additional_instructions = instructions
    component.final_refinement_instructions = final_refinement_instructions
    component.language = "ko"
    component.max_model_description_chars = 16_000
    return component.build_request().data


def _load_catalog(path: Path) -> dict:
    component = M01.LocalCatalogJsonLoaderComponent()
    component.catalog_json_file = str(path)
    component.max_file_size_mib = 20
    component.max_items = 5_000
    component.max_item_raw_chars = 200_000
    component.max_search_text_chars = 6_000
    component.max_json_depth = 12
    return component.load_catalog().data


def _rank(
    request: dict,
    catalog: dict,
    top_n: int | None = None,
) -> dict:
    component = M02.LocalCatalogRankerComponent()
    component.request = request
    component.catalog_bundle = catalog
    if top_n is not None:
        component.top_n = top_n
    component.max_candidate_chars = 700
    component.max_context_chars = 56_000
    return component.rank_catalog().data


def _shortlist(request: dict, retrieval: dict, *, count: int = 12) -> dict:
    """Create the authoritative 03 envelope for prompt-builder unit tests.

    These tests intentionally exercise 04 in isolation.  The preceding 03
    model has already chosen the review scope, so 04 must never substitute the
    whole 100-item lexical result for this envelope.
    """

    chosen = retrieval["candidates"][:count]
    return {
        "ok": True,
        "status": "COMPLETED",
        "schema_version": "catalog-shortlist/v1",
        "request_sha256": request["request_sha256"],
        "candidate_set_sha256": retrieval["candidate_set_sha256"],
        "catalog_file_sha256": retrieval["catalog_file_sha256"],
        "selection_policy": {
            "max_shortlisted_catalog_items": count,
            "zero_shortlist_allowed": True,
            "selection_scope": "candidate_shortlist_only",
            "selection_method": "llm-structured-shortlist/v1",
            "selection_source": "canvas_node_03",
        },
        "shortlisted_candidates": [
            {
                "asset_id": candidate["asset_id"],
                "version": candidate["version"],
                "shortlist_rank": position,
                "reason": "업무 설명과 관련성이 있어 후속 설계 검토 후보로 선별했습니다.",
            }
            for position, candidate in enumerate(chosen, start=1)
        ],
        "shortlisted_count": len(chosen),
        "unshortlisted_candidate_count": len(retrieval["candidates"]) - len(chosen),
        "warnings": [],
        "trace": {},
    }


def test_request_redacts_secret_and_keeps_description_hash_when_only_instruction_is_redacted():
    description = "매주 금요일 Outlook과 JIRA의 진행 상황을 취합하여 주간 보고서를 작성하고 팀장 승인 후 게시합니다."
    request = _request(
        description,
        "API_KEY=super-secret-value-987654321 을 사용하고 기존 후보를 우선 검토합니다.",
        "최종 보고서는 api_key=another-secret-value-987654321 를 절대 드러내지 말고 승인 예외를 강조합니다.",
    )

    assert "super-secret-value" not in json.dumps(request, ensure_ascii=False)
    assert "another-secret-value" not in json.dumps(request, ensure_ascii=False)
    assert request["description_original_sha256"] == "sha256:" + hashlib.sha256(description.encode("utf-8")).hexdigest()
    assert request["redaction_count"] == 2
    assert "SECRET_MATERIAL_REDACTED" in request["warnings"]
    assert "[REDACTED]" in request["additional_instructions"]
    assert "[REDACTED]" in request["final_refinement_instructions"]
    assert request["final_refinement_instructions"]
    request_schema = json.loads(
        (PROJECT_ROOT / "schemas" / "business_design_request.v2.schema.json").read_text(encoding="utf-8")
    )
    request_schema_errors = sorted(Draft202012Validator(request_schema).iter_errors(request), key=str)
    assert not request_schema_errors, "\n".join(error.message for error in request_schema_errors)

    description_with_secret = description + " 연결 정보는 api_key=do-not-retain-this-secret-value 입니다."
    redacted_description_request = _request(description_with_secret)
    assert redacted_description_request["description_original_sha256"] is None
    assert "do-not-retain-this-secret-value" not in redacted_description_request["description_display_redacted"]


def test_loader_derives_only_canonical_agent_hub_links(tmp_path: Path):
    component_id = "4deabfbd-b270-49ee-92e5-38b86cc5f908"
    flow_id = "b4d10e39-79d3-4c0e-8466-234ccd4cce51"
    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": component_id,
                        "title": "식당 메뉴 검색 봇",
                        "type": "py",
                        "description": "식당과 날짜 정보를 입력받아 사내 식당 메뉴를 검색합니다.",
                        "url": "https://not-allowed.example/secret-link",
                    },
                    {
                        "id": flow_id,
                        "title": "주간 보고 Flow",
                        "type": "json",
                        "description": "메일을 정리해 주간 보고 초안을 만듭니다.",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    bundle = _load_catalog(catalog_file)

    assert bundle["counts"]["ignored_source_url_fields"] == 1
    assert bundle["items"][0]["catalog_url"] == f"https://agent-hub.skhynix.com/#/component/{component_id}"
    assert bundle["items"][1]["catalog_url"] == f"https://agent-hub.skhynix.com/#/flow/{flow_id}"
    assert "url" not in bundle["items"][0]
    assert bundle["items"][0]["content_sha256"].startswith("sha256:")


def test_100_item_catalog_ranking_is_deterministic_and_bounded():
    catalog_path = PROJECT_ROOT / "samples" / "catalog_assets_100_example.json"
    catalog = _load_catalog(catalog_path)
    request = _request(
        "매주 금요일 Outlook 메일과 JIRA 이슈를 프로젝트별로 모아 완료 업무, 진행 중 업무, 이슈와 다음 주 계획을 정리한 뒤 팀장 승인 후 게시하는 주간 업무보고를 만듭니다."
    )
    first = _rank(request, catalog)
    second = _rank(request, catalog)

    assert catalog["counts"]["valid_items"] == 100
    assert first["top_n_requested"] == 100
    assert first["top_n_returned"] == 100
    assert first["expanded_detail_count_requested"] == M02._DEFAULT_EXPANDED_DETAIL_COUNT
    assert first["expanded_detail_count_returned"] == M02._DEFAULT_EXPANDED_DETAIL_COUNT
    assert first["candidate_set_sha256"] == second["candidate_set_sha256"]
    assert [candidate["asset_id"] for candidate in first["candidates"]] == [candidate["asset_id"] for candidate in second["candidates"]]
    assert all(
        len(json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"))) <= 700
        for candidate in first["candidates"]
    )
    assert len(first["expanded_candidate_details"]) == M02._DEFAULT_EXPANDED_DETAIL_COUNT
    assert len(
        json.dumps(
            {
                "candidates": first["candidates"],
                "expanded_candidate_details": first["expanded_candidate_details"],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    ) <= 56_000
    candidate_identities = {(candidate["asset_id"], candidate["version"]) for candidate in first["candidates"]}
    assert all(
        (detail["asset_id"], detail["version"]) in candidate_identities
        for detail in first["expanded_candidate_details"]
    )
    retrieval_schema = json.loads(
        (PROJECT_ROOT / "schemas" / "local_catalog_retrieval.v1.schema.json").read_text(encoding="utf-8")
    )
    schema_errors = sorted(Draft202012Validator(retrieval_schema).iter_errors(first), key=str)
    assert not schema_errors, "\n".join(error.message for error in schema_errors)


def test_internal_detail_context_is_fixed_and_schema_valid():
    catalog = _load_catalog(PROJECT_ROOT / "samples" / "catalog_assets_100_example.json")
    request = _request("업무 보고서의 메일·JIRA 수집, 오류 점검, 승인과 게시 흐름을 개선합니다.")
    retrieval = _rank(request, catalog)

    input_names = {input_spec.name for input_spec in M02.LocalCatalogRankerComponent.inputs}
    assert "expanded_detail_count" not in input_names
    assert retrieval["top_n_requested"] == 100
    assert retrieval["expanded_detail_count_requested"] == M02._DEFAULT_EXPANDED_DETAIL_COUNT == 30
    assert retrieval["expanded_detail_count_returned"] == 30
    assert len(retrieval["expanded_candidate_details"]) == 30
    assert [detail["rank"] for detail in retrieval["expanded_candidate_details"]] == list(range(1, 31))
    retrieval_schema = json.loads(
        (PROJECT_ROOT / "schemas" / "local_catalog_retrieval.v1.schema.json").read_text(encoding="utf-8")
    )
    schema_errors = sorted(Draft202012Validator(retrieval_schema).iter_errors(retrieval), key=str)
    assert not schema_errors, "\n".join(error.message for error in schema_errors)


def test_fixed_internal_detail_context_reaches_shortlisted_prompt_without_leaking_100_candidate_pool():
    catalog = _load_catalog(PROJECT_ROOT / "samples" / "catalog_assets_100_example.json")
    request = _request("업무 보고서의 메일·JIRA 수집, 오류 점검, 승인과 게시 흐름을 개선합니다.")
    retrieval = _rank(request, catalog)

    component = M03.BusinessDesignPromptBuilderComponent()
    component.request = request
    component.retrieval_result = retrieval
    component.catalog_shortlist = _shortlist(request, retrieval, count=12)
    component.max_prompt_chars = 64_000
    component.max_estimated_tokens = 20_000
    prompt = component.build_prompt()

    assert retrieval["expanded_detail_count_requested"] == M02._DEFAULT_EXPANDED_DETAIL_COUNT == 30
    assert retrieval["expanded_detail_count_returned"] == 30
    assert prompt.data["retrieval_candidate_count"] == 100
    assert prompt.data["candidate_index_count"] == 12
    assert prompt.data["expanded_candidate_requested_count"] == 12
    assert prompt.data["expanded_candidate_returned_count"] == 12
    assert prompt.data["expanded_candidate_count"] == 12


def test_ranker_has_no_canvas_detail_limit_but_preserves_100_candidate_retrieval():
    catalog = _load_catalog(PROJECT_ROOT / "samples" / "catalog_assets_100_example.json")
    request = _request("Outlook 메일과 JIRA 작업을 프로젝트별로 정리해 승인 후 게시하는 업무 보고서 자동화")
    retrieval = _rank(request, catalog, top_n=100)

    input_names = {input_spec.name for input_spec in M02.LocalCatalogRankerComponent.inputs}
    assert "top_n" in input_names
    assert "expanded_detail_count" not in input_names
    assert retrieval["top_n_returned"] == 100
    assert retrieval["expanded_detail_count_returned"] == M02._DEFAULT_EXPANDED_DETAIL_COUNT


def test_prompt_has_only_03_shortlisted_candidates_in_untrusted_boundary_without_system_prompt_duplication():
    catalog = _load_catalog(PROJECT_ROOT / "samples" / "catalog_assets_100_example.json")
    request = _request(
        "매주 수집되는 생산 이슈, Outlook 메일, JIRA 작업을 검토해 보고서 초안을 만들고 오류나 누락이 있으면 게시하지 않는 업무를 개선합니다.",
        "기존 카탈로그를 우선 후보로 제시하되 실제 적용 여부는 업무 적합성으로 판단해 주세요.",
        "최종 보완 단계에서는 예외 처리와 승인 기준을 더 구체적으로 작성해 주세요.",
    )
    retrieval = _rank(request, catalog)
    component = M03.BusinessDesignPromptBuilderComponent()
    component.request = request
    component.retrieval_result = retrieval
    component.catalog_shortlist = _shortlist(request, retrieval, count=12)
    component.max_prompt_chars = 64_000
    component.max_estimated_tokens = 20_000
    prompt = component.build_prompt()

    assert "<untrusted_catalog_candidates>" in prompt.text
    assert "</untrusted_catalog_candidates>" in prompt.text
    assert "<response_contract>" in prompt.text
    assert prompt.text.rstrip().endswith("</response_contract>")
    fixed_prompt = (PROJECT_ROOT / "prompts" / "single_flow_business_design.md").read_text(encoding="utf-8")
    assert "업무 자체를 분석합니다." in fixed_prompt
    assert "Human Input 또는 재질문 loop를 새로 제안하지 마세요." in fixed_prompt
    assert prompt.data["retrieval_candidate_count"] == retrieval["top_n_returned"] == 100
    assert prompt.data["candidate_count"] == 12
    assert prompt.data["candidate_index_count"] == 12
    assert 0 < prompt.data["expanded_candidate_count"] <= 12
    assert prompt.data["candidate_context_schema"] == "catalog-candidate-context/v2"
    assert prompt.data["final_refinement_instructions_included"] is False
    assert prompt.data["system_message_sha256"] == M03.SYSTEM_MESSAGE_SHA256
    assert prompt.data["estimated_token_count"] <= 20_000
    assert "최종 보완 단계에서는 예외 처리와 승인 기준" not in prompt.text
    boundary_start = prompt.text.index("<untrusted_catalog_candidates>") + len("<untrusted_catalog_candidates>")
    boundary_end = prompt.text.index("</untrusted_catalog_candidates>")
    candidate_context = json.loads(prompt.text[boundary_start:boundary_end].splitlines()[-1])
    assert candidate_context["schema_version"] == "catalog-candidate-context/v2"
    assert candidate_context["candidate_index_record_fields"] == list(M03._INDEX_RECORD_FIELDS)
    assert len(candidate_context["candidate_index"]) == 12
    assert len(candidate_context["expanded_candidates"]) == prompt.data["expanded_candidate_count"]
    index_asset_position = candidate_context["candidate_index_record_fields"].index("asset_id")
    assert [record[index_asset_position] for record in candidate_context["candidate_index"]] == [
        candidate["asset_id"] for candidate in retrieval["candidates"][:12]
    ]
    assert [item["rank"] for item in candidate_context["expanded_candidates"]] == list(
        range(1, len(candidate_context["expanded_candidates"]) + 1)
    )
    assert len(json.dumps(candidate_context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))) <= 32_000
    for candidate in retrieval["candidates"][:12]:
        assert candidate["asset_id"] in prompt.text
    for candidate in retrieval["candidates"][12:]:
        assert candidate["asset_id"] not in prompt.text

    repeated = component.build_prompt()
    assert repeated.text == prompt.text
    assert {key: value for key, value in repeated.data.items() if key != "timestamp"} == {
        key: value for key, value in prompt.data.items() if key != "timestamp"
    }


def test_sources_do_not_import_sibling_components():
    for source in sorted(COMPONENT_ROOT.glob("0[0-3]_*.py")):
        text = source.read_text(encoding="utf-8")
        assert "from ." not in text
        assert "import components" not in text
        assert "business_work_design_agent" not in text
