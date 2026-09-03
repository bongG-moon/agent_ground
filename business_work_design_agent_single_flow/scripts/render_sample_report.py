from __future__ import annotations

"""Render the local sample report without an LLM provider or Report API.

This is visual-QA infrastructure for the single Flow.  It deliberately calls
the same standalone component methods used by F01 for 00 → 01 → 02 → 05 → 06
→ 08 → 09 → 10 → 11, but substitutes the checked-in mock JSON for both
Language Model passes.  It creates a contract-equivalent, deterministic 03
shortlist fixture from the mock's catalog decisions instead of calling a model.
It does not call a provider, network service, MongoDB, or publisher.
"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from lfx.schema import Data
from lfx.schema.message import Message


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_ROOT = PROJECT_ROOT / "components" / "single_flow"
SAMPLE_ROOT = PROJECT_ROOT / "samples"


def _load_module(filename: str) -> ModuleType:
    path = COMPONENT_ROOT / filename
    if not path.is_file():
        raise FileNotFoundError(f"Missing standalone component: {path}")
    module_name = f"single_flow_sample_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load component module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _component(filename: str, class_name: str) -> Any:
    module = _load_module(filename)
    component_class = getattr(module, class_name, None)
    if component_class is None:
        raise AttributeError(f"{filename} does not expose {class_name}")
    return component_class()


def _data(value: Any, name: str) -> dict[str, Any]:
    payload = getattr(value, "data", None)
    if not isinstance(payload, dict):
        raise TypeError(f"{name} did not return Data(object)")
    return payload


def _sample_catalog_shortlist(
    *,
    request: dict[str, Any],
    retrieval_result: dict[str, Any],
    mock_draft: dict[str, Any],
    maximum: int = 12,
) -> dict[str, Any]:
    """Build the exact 03 output envelope needed by the offline visual sample.

    This is intentionally a fixture, not an alternative candidate-selection
    implementation.  The real Flow still calls 03's Language Model.  Keeping
    the fixture bound to the current request/retrieval hashes makes the sample
    exercise the same 05 shortlist lock as a normal execution.
    """

    candidates = retrieval_result.get("candidates")
    if not isinstance(candidates, list):
        raise TypeError("02 LocalCatalogRanker did not return candidates")
    wanted: list[tuple[str, str]] = []
    decisions = mock_draft.get("catalog_decisions")
    if isinstance(decisions, list):
        for decision in decisions:
            if not isinstance(decision, dict):
                continue
            asset_id = str(decision.get("asset_id") or "").lower()
            version = str(decision.get("version") or "unknown")
            if asset_id and (asset_id, version) not in wanted:
                wanted.append((asset_id, version))

    chosen: list[dict[str, Any]] = []
    used: set[tuple[str, str]] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        key = (str(candidate.get("asset_id") or "").lower(), str(candidate.get("version") or "unknown"))
        if key in wanted and key not in used:
            chosen.append(candidate)
            used.add(key)
    for candidate in candidates:
        if not isinstance(candidate, dict) or len(chosen) >= maximum:
            continue
        key = (str(candidate.get("asset_id") or "").lower(), str(candidate.get("version") or "unknown"))
        if key[0] and key not in used:
            chosen.append(candidate)
            used.add(key)
    chosen = chosen[:maximum]
    return {
        "ok": True,
        "status": "COMPLETED",
        "schema_version": "catalog-shortlist/v1",
        "request_sha256": request["request_sha256"],
        "candidate_set_sha256": retrieval_result["candidate_set_sha256"],
        "catalog_file_sha256": retrieval_result["catalog_file_sha256"],
        "selection_policy": {
            "max_shortlisted_catalog_items": maximum,
            "zero_shortlist_allowed": True,
            "selection_scope": "candidate_shortlist_only",
            "selection_method": "llm-structured-shortlist/v1",
            "selection_source": "canvas_node_03",
        },
        "shortlisted_candidates": [
            {
                "asset_id": candidate["asset_id"],
                "version": candidate["version"],
                "shortlist_rank": index,
                "reason": "오프라인 시각 검증 fixture에서 모델 설계안의 카탈로그 결정을 재현하기 위해 포함했습니다.",
            }
            for index, candidate in enumerate(chosen, start=1)
        ],
        "shortlisted_count": len(chosen),
        "unshortlisted_candidate_count": max(0, len(candidates) - len(chosen)),
        "warnings": ["SAMPLE_FIXTURE_ONLY"],
        "trace": {"model_execution_mode": "sample_fixture"},
    }


def render_sample(
    *,
    description_path: Path,
    additional_request_path: Path,
    catalog_path: Path,
    model_response_path: Path,
    output_path: Path,
    top_n: int,
) -> dict[str, Any]:
    description = description_path.read_text(encoding="utf-8")
    additional_request = additional_request_path.read_text(encoding="utf-8")
    model_response = model_response_path.read_text(encoding="utf-8")
    # Strict JSON parsing keeps the mock boundary equivalent to the normalizer
    # expectation that a Language Model Message contains one JSON object.
    parsed_mock = json.loads(model_response)
    if not isinstance(parsed_mock, dict):
        raise TypeError("mock_model_response_complex.json must contain one JSON object")

    input_node = _component("00_business_design_input.py", "BusinessDesignInputComponent")
    input_node.description = description
    input_node.additional_instructions = additional_request
    input_node.final_refinement_instructions = "분기·예외·사람 확인 지점과 카탈로그 적용 근거가 보고서에서 분명히 보이도록 다듬어 주세요."
    input_node.language = "ko"
    input_node.max_model_description_chars = 16_000
    request = _data(input_node.build_request(), "00 BusinessDesignInput")

    loader = _component("01_catalog_json_loader.py", "LocalCatalogJsonLoaderComponent")
    loader.catalog_json_file = str(catalog_path.resolve())
    loader.max_file_size_mib = 20
    loader.max_items = 5_000
    loader.max_item_raw_chars = 200_000
    loader.max_search_text_chars = 6_000
    loader.max_json_depth = 12
    catalog_bundle = _data(loader.load_catalog(), "01 LocalCatalogJsonLoader")

    ranker = _component("02_local_catalog_ranker.py", "LocalCatalogRankerComponent")
    ranker.request = Data(data=request)
    ranker.catalog_bundle = Data(data=catalog_bundle)
    ranker.top_n = top_n
    ranker.max_candidate_chars = 700
    ranker.max_context_chars = 56_000
    retrieval_result = _data(ranker.rank_catalog(), "02 LocalCatalogRanker")
    catalog_shortlist = _sample_catalog_shortlist(
        request=request,
        retrieval_result=retrieval_result,
        mock_draft=parsed_mock,
    )

    normalizer = _component("05_business_design_result_normalizer.py", "BusinessDesignResultNormalizerComponent")
    normalizer.model_response = Message(text=json.dumps(parsed_mock, ensure_ascii=False))
    normalizer.request = Data(data=request)
    normalizer.retrieval_result = Data(data=retrieval_result)
    normalizer.catalog_shortlist = Data(data=catalog_shortlist)
    initial_design_result = _data(normalizer.normalize_design(), "06 first BusinessDesignResultNormalizer")

    # The sample has no provider call.  Reuse the checked-in contract-valid
    # mock as the final draft so the same second normalization/fallback wiring
    # used by F01 is exercised without pretending to run an LLM locally.
    final_normalizer = _component("05_business_design_result_normalizer.py", "BusinessDesignResultNormalizerComponent")
    final_normalizer.model_response = Message(text=json.dumps(parsed_mock, ensure_ascii=False))
    final_normalizer.request = Data(data=request)
    final_normalizer.retrieval_result = Data(data=retrieval_result)
    final_normalizer.catalog_shortlist = Data(data=catalog_shortlist)
    final_normalizer.fallback_design_result = Data(data=initial_design_result)
    design_result = _data(final_normalizer.normalize_design(), "09 final BusinessDesignResultNormalizer")

    view_builder = _component("06_report_view_model_builder_v2.py", "ReportViewModelBuilderV2Component")
    view_builder.design_result = Data(data=design_result)
    report_view_model = _data(view_builder.build_view_model(), "06 ReportViewModelBuilderV2")

    renderer = _component("07_responsive_report_renderer_v2.py", "ResponsiveReportRendererV2Component")
    renderer.report_view_model = Data(data=report_view_model)
    renderer.max_nodes = 500
    renderer.max_edges = 1_000
    renderer.max_html_bytes = 10_000_000
    render_result = _data(renderer.render_report(), "07 ResponsiveReportRendererV2")
    html = render_result.get("html")
    if not isinstance(html, str) or not html.startswith("<!doctype html>"):
        raise ValueError("07 renderer did not return self-contained HTML")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8", newline="\n")
    return {
        "ok": True,
        "output_path": str(output_path.resolve()),
        "report_id": render_result.get("report_id"),
        "content_sha256": render_result.get("content_sha256"),
        "html_bytes": len(html.encode("utf-8")),
        "catalog_candidates": retrieval_result.get("top_n_returned"),
        "catalog_internal_detail_context": retrieval_result.get("expanded_detail_count_returned"),
        "catalog_selected": len((design_result.get("catalog_application") or {}).get("selected") or []),
        "information_gaps": len(design_result.get("information_gaps") or []),
        "status": design_result.get("status"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--description", type=Path, default=SAMPLE_ROOT / "complex_work_description_ko.txt")
    parser.add_argument("--additional-request", type=Path, default=SAMPLE_ROOT / "additional_design_request_ko.txt")
    parser.add_argument("--catalog", type=Path, default=SAMPLE_ROOT / "catalog_assets_100_example.json")
    parser.add_argument("--mock-response", type=Path, default=SAMPLE_ROOT / "mock_model_response_complex.json")
    parser.add_argument("--output", type=Path, default=SAMPLE_ROOT / "generated_sample_report.html")
    parser.add_argument("--top-n", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.top_n <= 100:
        raise SystemExit("--top-n must be between 1 and 100")
    result = render_sample(
        description_path=args.description,
        additional_request_path=args.additional_request,
        catalog_path=args.catalog,
        model_response_path=args.mock_response,
        output_path=args.output,
        top_n=args.top_n,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
