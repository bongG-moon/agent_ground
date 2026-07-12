from __future__ import annotations

"""00 리포트 요청/데이터 불러오기 노드.

이 파일은 Langflow 체험용 첫 번째 노드입니다.
사용자가 입력한 질문, 보고 싶은 방식, CSV/JSON 텍스트, File Read 결과를 하나의
표준 payload로 정리해서 뒤 노드들이 같은 형식으로 처리할 수 있게 합니다.
"""

import csv
import io
import json
from copy import deepcopy
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MessageTextInput, Output
from lfx.schema.data import Data


def build_demo_report_request(
    question: str,
    view_request: str = "",
    data_text: str = "",
    file_data: Any = None,
) -> dict[str, Any]:
    """질문과 입력 데이터를 HTML 리포트 flow의 표준 요청 payload로 만듭니다.

    `data_text`에 직접 붙여넣은 CSV/JSON이 있으면 그것을 우선 사용하고,
    비어 있으면 `file_data`에 연결된 File Read 결과에서 텍스트를 꺼냅니다.
    이후 CSV/JSON/JSONL 여부를 판단해 rows/columns 형태로 맞춰 둡니다.
    여러 dataset이 들어온 경우에는 공통 key를 찾아 자동으로 결합 view를 만들고,
    단일 CSV/rows 입력은 기존처럼 하나의 입력 데이터 view로 처리합니다.
    """

    text = _select_input_text(data_text, file_data)
    datasets = _parse_datasets(text)
    visual_request = _infer_visual_request(question, view_request)
    data_views, relationship_candidates, active_view = _build_data_views(datasets, str(question or ""), str(view_request or ""))
    rows = _rows(active_view.get("rows"))
    columns = _strings(active_view.get("columns")) or _columns_from_rows(rows)
    row_count = _positive_int(active_view.get("row_count"), len(rows))

    compact_datasets = []
    for item in datasets:
        # 뒤 노드에는 전체 rows를 여러 번 넘기지 않고, 데이터셋 목록에는 요약만 남깁니다.
        item_rows = _rows(item.get("rows"))
        item_columns = _strings(item.get("columns")) or _columns_from_rows(item_rows)
        compact_datasets.append(
            {
                "dataset_id": str(item.get("dataset_id") or ""),
                "label": str(item.get("label") or item.get("dataset_id") or ""),
                "columns": item_columns,
                "row_count": _positive_int(item.get("row_count"), len(item_rows)),
                "data_ref": _dict(item.get("data_ref")),
            }
        )

    compact_views = []
    for view in data_views:
        compact_views.append(
            {
                "data_view_id": str(view.get("data_view_id") or ""),
                "label": str(view.get("label") or view.get("data_view_id") or ""),
                "strategy": str(view.get("strategy") or "select"),
                "source_dataset_ids": _strings(view.get("source_dataset_ids")),
                "join_keys": _strings(view.get("join_keys")),
                "columns": _strings(view.get("columns")),
                "row_count": _positive_int(view.get("row_count"), len(_rows(view.get("rows")))),
            }
        )

    payload = {
        "payload_version": "html-report-demo-v1",
        "flow_type": "html_report_demo",
        "status": "ok",
        "request": {
            "question": str(question or ""),
            "view_request": str(view_request or ""),
            "selected_dataset_id": str(active_view.get("source_dataset_ids", [""])[0] if active_view.get("source_dataset_ids") else ""),
            "active_data_view_id": str(active_view.get("data_view_id") or ""),
            "visual_request": visual_request,
        },
        "available_datasets": compact_datasets,
        "available_data_views": compact_views,
        "data_views": data_views,
        "relationship_candidates": relationship_candidates,
        "api_response": {
            "status": "ok",
            "response_type": "demo_data",
            "message": "Demo data loaded for HTML report generation.",
            "data": {
                "data_view_id": str(active_view.get("data_view_id") or ""),
                "label": str(active_view.get("label") or ""),
                "strategy": str(active_view.get("strategy") or "select"),
                "source_dataset_ids": _strings(active_view.get("source_dataset_ids")),
                "join_keys": _strings(active_view.get("join_keys")),
                "columns": columns,
                "rows": rows,
                "row_count": row_count,
                "data_ref": _dict(active_view.get("data_ref")),
                "data_is_preview": row_count > len(rows),
            },
        },
        "warnings": [],
        "errors": [],
    }
    if not rows:
        payload["warnings"].append("No rows were loaded from data_text or file_data.")
    if len(datasets) > 1:
        payload["warnings"].append(
            f"Multiple datasets loaded; active data view is '{active_view.get('data_view_id')}' using strategy '{active_view.get('strategy')}'."
        )
    return payload


def _infer_visual_request(question: str, view_request: str) -> dict[str, Any]:
    """질문/표시 요구에 직접 드러난 키워드만 약한 힌트로 추출합니다.

    사람마다 작성 방식이 다르기 때문에 이 값은 최종 해석이 아닙니다.
    LLM은 원문 질문과 원문 표시 요구를 우선하고, 이 힌트는 fallback 초안에만 참고합니다.
    """

    raw_view_request = str(view_request or "").strip()
    text = " ".join(part for part in (str(question or ""), raw_view_request) if part).lower()
    requested_blocks: list[str] = []

    def add(block_id: str) -> None:
        if block_id not in requested_blocks:
            requested_blocks.append(block_id)

    if _contains(text, ["kpi", "card", "\uce74\ub4dc", "\uc9c0\ud45c", "\uc8fc\uc694\uac12", "\uc8fc\uc694 \uac12"]):
        add("kpi_card_grid")
    if _contains(text, ["\ucd94\uc774", "\uc2dc\uacc4\uc5f4", "\uc120\uadf8\ub798\ud504", "\ub77c\uc778", "trend", "line"]):
        add("trend_line_chart")
    if _contains(text, ["\ube44\uad50", "\ub9c9\ub300", "bar", "comparison", "\ud56d\ubaa9\ubcc4", "\uc720\ud615\ubcc4", "\ubb38\ud56d\ubcc4"]):
        add("comparison_bar_chart")
    if _contains(text, ["\ub3c4\ub11b", "\uc6d0\ud615", "\ud30c\uc774", "donut", "pie", "\ube44\uc911", "\uad6c\uc131\ube44", "\uc810\uc720\uc728"]):
        add("donut_chart")
    if _contains(text, ["\ubb36\uc74c", "\ub098\ub780\ud788", "\ubcf5\uc218 \uc9c0\ud45c", "grouped", "clustered"]):
        add("grouped_bar_chart")
    if _contains(text, ["\ub204\uc801", "stacked", "breakdown", "\uc0c1\ud0dc\ubcc4", "\uad6c\ubd84\ubcc4"]):
        add("stacked_comparison_bar")
    if _contains(text, ["\ud788\ud2b8\ub9f5", "\uad50\ucc28", "\uad50\ucc28\ud45c", "\ub9e4\ud2b8\ub9ad\uc2a4", "pivot", "matrix", "heatmap"]):
        add("heatmap_matrix")
    if _contains(text, ["\ubd84\ud3ec", "\ud3b8\ucc28", "\uc0b0\ud3ec", "histogram", "distribution"]):
        add("distribution_histogram")
    if _contains(text, ["\uc0c1\uad00", "\uc0b0\uc810\ub3c4", "scatter", "correlation", "relationship"]):
        add("scatter_plot")
    if _contains(text, ["\uc21c\uc704", "\ub7ad\ud0b9", "top", "bottom", "ranking"]):
        add("ranking_table")
    if _contains(text, ["\uc0c1\uc138", "\uc0c1\uc138\ud45c", "\ud45c", "\ud14c\uc774\ube14", "\ubaa9\ub85d", "\ub9ac\uc2a4\ud2b8", "detail", "table", "raw"]):
        add("detail_data_table")
    if _contains(text, ["\ud574\uc11d", "\uc694\uc57d", "insight", "\ud575\uc2ec"]):
        add("insight_bullets")
    if _contains(text, ["\ucd94\ucc9c", "\uc870\uce58", "\uac1c\uc120", "\ub2e4\uc74c \ud655\uc778", "recommend"]):
        add("recommendation_list")

    complexity = "auto"
    if _contains(text, ["\uac04\ub2e8", "\uc9e7\uac8c", "\uc694\uc57d\ub9cc", "simple", "brief"]):
        complexity = "simple"
    elif _contains(text, ["\uc0c1\uc138", "\ud48d\ubd80", "\ub9ce\uc774", "\uc804\uccb4", "detailed", "full"]):
        complexity = "detailed"
    elif _contains(text, ["\ucd18\ucd18", "\uc791\uac8c", "compact"]):
        complexity = "compact"

    target_block_count = _target_block_count(text, requested_blocks, complexity)
    return {
        "source": "rule_based_keyword_hint",
        "confidence": "low",
        "usage_note": "Raw question and view_request must win over this hint; use it only for deterministic fallback drafts.",
        "raw_text": raw_view_request,
        "complexity": complexity,
        "requested_blocks": requested_blocks,
        "target_block_count": target_block_count,
        "style_keywords": _style_keywords(text),
    }


def _target_block_count(text: str, requested_blocks: list[str], complexity: str) -> int:
    """요청 문장과 선택된 요소를 보고 목표 블록 수를 정합니다."""

    explicit = _explicit_count(text)
    if explicit:
        # 사용자가 KPI/차트/표 같은 콘텐츠 요소를 함께 적은 경우에는
        # 보고서 헤더와 데이터 범위 요약이 앞에서 잘라먹지 않도록 여유를 둡니다.
        content_floor = len(requested_blocks) + 2 if requested_blocks else explicit
        return max(3, min(max(explicit, content_floor), 10))
    if complexity == "simple":
        return 5
    if complexity == "detailed":
        return 10
    base_count = 2 + len(requested_blocks)
    if "insight_bullets" not in requested_blocks and _contains(text, ["\ud574\uc11d", "\uc694\uc57d", "\ub9ac\ud3ec\ud2b8", "report"]):
        base_count += 1
    return max(5, min(base_count, 10))


def _explicit_count(text: str) -> int:
    """'5개', '5 blocks'처럼 직접 적은 개수를 읽습니다."""

    import re

    match = re.search(r"(\d{1,2})\s*(?:\uac1c|blocks?|components?|\ube14\ub85d|\uc694\uc18c)", text)
    return int(match.group(1)) if match else 0


def _style_keywords(text: str) -> list[str]:
    """색상/분위기 관련 표현을 모읍니다."""

    result: list[str] = []
    for keyword in ["\ud30c\ub780\uc0c9", "\ucd08\ub85d", "\ud68c\uc0c9", "\ube68\uac04\uc0c9", "\uacbd\uace0", "\uac15\uc870", "\ucc28\ubd84\ud55c", "\uae54\ub054\ud55c", "\uc784\uc6d0\uc6a9", "compact", "comfortable"]:
        if keyword.lower() in text and keyword not in result:
            result.append(keyword)
    return result


def _contains(text: str, needles: list[str]) -> bool:
    """문장 안에 지정한 키워드 중 하나라도 포함됐는지 확인합니다."""

    haystack = str(text or "").lower()
    return any(needle.lower() in haystack for needle in needles)


def _select_input_text(data_text: str, file_data: Any) -> str:
    """직접 입력 텍스트와 File Read 결과 중 실제로 사용할 텍스트를 고릅니다."""

    direct = str(data_text or "").strip()
    file_text = _extract_text(file_data).strip()
    return direct or file_text


def _extract_text(value: Any) -> str:
    """Langflow의 다양한 데이터 객체에서 가능한 텍스트 내용을 꺼냅니다.

    File Read 컴포넌트는 Langflow 버전/파일 유형에 따라 Data, Message, DataFrame 등
    서로 다른 모양으로 값을 넘길 수 있습니다. 그래서 흔히 쓰이는 속성과 key를
    차례로 확인하면서 CSV/JSON으로 변환 가능한 값을 찾습니다.
    """

    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if hasattr(value, "to_csv"):
        try:
            # pandas DataFrame처럼 `to_csv`가 있으면 CSV 문자열로 바꿉니다.
            return value.to_csv(index=False)
        except Exception:
            pass
    if hasattr(value, "to_dict"):
        try:
            # pandas DataFrame처럼 `to_dict`가 있으면 row dict 목록으로 바꿉니다.
            rows = value.to_dict(orient="records")
            if isinstance(rows, list):
                return json.dumps(rows, ensure_ascii=False, default=str)
        except Exception:
            pass
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        for key in ("text", "content", "data", "raw", "file_content", "file_contents"):
            if isinstance(data.get(key), str):
                return data[key]
        for key in ("dataframe", "structured_content", "structuredContent"):
            if data.get(key) is not None:
                extracted = _extract_text(data.get(key))
                if extracted:
                    return extracted
        if isinstance(data.get("rows"), list):
            return json.dumps(data, ensure_ascii=False, default=str)
        if isinstance(data.get("records"), list):
            return json.dumps(data["records"], ensure_ascii=False, default=str)
        if isinstance(data.get("columns"), list) and isinstance(data.get("data"), list):
            return json.dumps(data, ensure_ascii=False, default=str)
    if isinstance(data, list):
        return json.dumps(data, ensure_ascii=False, default=str)
    for attr in ("text", "content"):
        text = getattr(value, attr, None)
        if isinstance(text, str):
            return text
    for attr in ("dataframe", "structured_content", "structuredContent", "value"):
        nested = getattr(value, attr, None)
        if nested is not None and nested is not value:
            extracted = _extract_text(nested)
            if extracted:
                return extracted
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _parse_datasets(text: str) -> list[dict[str, Any]]:
    """입력 문자열을 데이터셋 목록으로 파싱합니다.

    우선 JSON을 시도하고, 실패하면 JSONL, 마지막으로 CSV로 해석합니다.
    아무 입력도 없으면 빈 demo 데이터셋을 만들어 flow가 끊기지 않게 합니다.
    """

    raw = str(text or "").strip()
    if not raw:
        return [_dataset("demo_dataset", "입력 데이터", [], [])]
    parsed = _try_json(raw)
    if parsed is not None:
        return _datasets_from_json(parsed)
    parsed_rows = _try_jsonl(raw)
    if parsed_rows is not None:
        return [_dataset("demo_dataset", "입력 데이터", _columns_from_rows(parsed_rows), parsed_rows)]
    rows = _parse_csv(raw)
    return [_dataset("demo_dataset", "입력 데이터", _columns_from_rows(rows), rows)]


def _datasets_from_json(value: Any) -> list[dict[str, Any]]:
    """JSON 값에서 rows/columns/data_ref 구조를 찾아 데이터셋 목록으로 바꿉니다."""

    if isinstance(value, list):
        if all(isinstance(item, dict) for item in value):
            return [_dataset("demo_dataset", "입력 데이터", _columns_from_rows(value), value)]
        return [_dataset("demo_dataset", "입력 데이터", ["value"], [{"value": item} for item in value])]
    if not isinstance(value, dict):
        return [_dataset("demo_dataset", "입력 데이터", ["value"], [{"value": value}])]

    if isinstance(value.get("datasets"), list):
        datasets = []
        for index, item in enumerate(value["datasets"], start=1):
            if not isinstance(item, dict):
                continue
            rows = _rows(item.get("rows") or _dict(item.get("data")).get("rows"))
            columns = _strings(item.get("columns")) or _strings(_dict(item.get("data")).get("columns")) or _columns_from_rows(rows)
            datasets.append(
                _dataset(
                    str(item.get("dataset_id") or item.get("id") or f"dataset_{index}"),
                    str(item.get("label") or item.get("name") or item.get("dataset_id") or f"Dataset {index}"),
                    columns,
                    rows,
                    data_ref=_dict(item.get("data_ref")),
                    row_count=_positive_int(item.get("row_count"), len(rows)),
                )
            )
        return datasets or [_dataset("demo_dataset", "입력 데이터", [], [])]

    data = _dict(value.get("data"))
    rows = _rows(value.get("rows") or data.get("rows"))
    columns = _strings(value.get("columns")) or _strings(data.get("columns")) or _columns_from_rows(rows)
    return [
        _dataset(
            str(value.get("dataset_id") or value.get("id") or "demo_dataset"),
            str(value.get("label") or value.get("name") or "입력 데이터"),
            columns,
            rows,
            data_ref=_dict(value.get("data_ref") or data.get("data_ref")),
            row_count=_positive_int(value.get("row_count") or data.get("row_count"), len(rows)),
        )
    ]


def _try_json(text: str) -> Any | None:
    """문자열 전체를 JSON으로 읽어 봅니다. 실패하면 None을 반환합니다."""

    try:
        return json.loads(text)
    except Exception:
        return None


def _try_jsonl(text: str) -> list[dict[str, Any]] | None:
    """한 줄에 JSON 객체 하나씩 있는 JSONL 형식을 읽어 봅니다."""

    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except Exception:
            return None
        if not isinstance(parsed, dict):
            return None
        rows.append(parsed)
    return rows if rows else None


def _parse_csv(text: str) -> list[dict[str, Any]]:
    """CSV 문자열을 dict row 목록으로 변환합니다."""

    sample = text[:2048]
    try:
        # 구분자가 쉼표인지 탭인지 등을 csv.Sniffer가 샘플을 보고 추정합니다.
        dialect = csv.Sniffer().sniff(sample)
    except Exception:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    return [_clean_row(row) for row in reader if isinstance(row, dict)]


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    """CSV row에서 빈 컬럼명은 제거하고 값은 숫자면 숫자로 변환합니다."""

    return {str(key or "").strip(): _coerce(value) for key, value in row.items() if str(key or "").strip()}


def _coerce(value: Any) -> Any:
    """문자열 값을 가능한 경우 int/float 숫자로 바꿉니다."""

    text = str(value or "").strip()
    if text == "":
        return ""
    normalized = text.replace(",", "")
    try:
        number = float(normalized)
    except Exception:
        return text
    return int(number) if number.is_integer() else number


def _dataset(
    dataset_id: str,
    label: str,
    columns: list[str],
    rows: list[dict[str, Any]],
    data_ref: dict[str, Any] | None = None,
    row_count: int | None = None,
) -> dict[str, Any]:
    """뒤 노드들이 기대하는 데이터셋 딕셔너리 형식을 만듭니다."""

    return {
        "dataset_id": str(dataset_id or "demo_dataset"),
        "label": str(label or dataset_id or "Demo Dataset"),
        "columns": columns,
        "rows": rows,
        "row_count": len(rows) if row_count is None else int(row_count),
        "data_ref": data_ref or {},
    }


def _build_data_views(
    datasets: list[dict[str, Any]],
    question: str = "",
    view_request: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """여러 dataset을 렌더러가 사용할 수 있는 data view 목록으로 변환합니다.

    - 단일 dataset이면 기존과 동일하게 해당 rows를 active view로 사용합니다.
    - 여러 dataset이면 개별 view를 유지하면서, 공통 key가 있으면 자동 join view를 추가합니다.
    - 같은 구조의 dataset 묶음이면 union view도 만들 수 있습니다.
    """

    normalized = [deepcopy(item) for item in datasets if isinstance(item, dict)]
    if not normalized:
        normalized = [_dataset("demo_dataset", "입력 데이터", [], [])]

    views = [_view_from_dataset(item) for item in normalized]
    candidates = _relationship_candidates(normalized)
    intent_text = f"{question} {view_request}".lower()
    union_view = _union_view(normalized)
    join_view = _joined_view(normalized, candidates)

    if union_view:
        views.append(union_view)
    if join_view:
        views.append(join_view)

    active = views[0]
    if len(normalized) > 1:
        if _contains_text(intent_text, ["union", "append", "concat", "세로", "합쳐", "누적"]) and union_view:
            active = union_view
        elif join_view:
            active = join_view
        elif union_view:
            active = union_view
    return views, candidates, active


def _view_from_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    """하나의 원본 dataset을 select 전략 data view로 감쌉니다."""

    rows = _rows(dataset.get("rows"))
    columns = _strings(dataset.get("columns")) or _columns_from_rows(rows)
    dataset_id = str(dataset.get("dataset_id") or "demo_dataset")
    return {
        "data_view_id": dataset_id,
        "label": str(dataset.get("label") or dataset_id),
        "strategy": "select",
        "source_dataset_ids": [dataset_id],
        "join_keys": [],
        "columns": columns,
        "rows": rows,
        "row_count": _positive_int(dataset.get("row_count"), len(rows)),
        "data_ref": _dict(dataset.get("data_ref")),
    }


def _relationship_candidates(datasets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """dataset 쌍 사이의 공통 key 후보를 찾습니다."""

    candidates = []
    for left_index, left in enumerate(datasets):
        for right in datasets[left_index + 1 :]:
            keys = _common_join_keys(left, right)
            candidates.append(
                {
                    "left_dataset": str(left.get("dataset_id") or ""),
                    "right_dataset": str(right.get("dataset_id") or ""),
                    "join_keys": keys,
                    "confidence": "high" if keys else "none",
                    "reason": "공통 key 후보가 있어 자동 결합할 수 있습니다." if keys else "공통 key 후보가 부족해 자동 결합하지 않습니다.",
                }
            )
    return candidates


def _common_join_keys(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    """두 dataset 사이에서 join key로 쓰기 좋은 공통 컬럼을 고릅니다."""

    left_rows = _rows(left.get("rows"))
    right_rows = _rows(right.get("rows"))
    common = [
        column
        for column in _strings(left.get("columns")) or _columns_from_rows(left_rows)
        if column in set(_strings(right.get("columns")) or _columns_from_rows(right_rows))
    ]
    preferred = []
    for column in common:
        if _looks_like_join_key(column, left_rows, right_rows):
            preferred.append(column)
    if preferred:
        return preferred[:4]
    # 컬럼명이 같고 값이 숫자 metric처럼 보이지 않는 경우를 마지막 후보로 사용합니다.
    fallback = [column for column in common if not _looks_numeric_metric(column, left_rows + right_rows)]
    return fallback[:3]


def _looks_like_join_key(column: str, left_rows: list[dict[str, Any]], right_rows: list[dict[str, Any]]) -> bool:
    """컬럼명과 값 분포를 보고 join key 가능성을 판단합니다."""

    text = str(column or "").lower()
    if any(token in text for token in ["date", "dt", "day", "month", "week", "year", "날짜", "일자", "기준일"]):
        return True
    if any(token in text for token in ["process", "oper", "line", "product", "item", "category", "shift", "region", "warehouse", "공정", "라인", "제품", "구분"]):
        return True
    if any(token in text for token in ["id", "code", "key", "no", "seq", "코드", "번호"]):
        return True
    return not _looks_numeric_metric(column, left_rows + right_rows)


def _looks_numeric_metric(column: str, rows: list[dict[str, Any]]) -> bool:
    """값 대부분이 숫자이고 key 이름이 아니면 metric 컬럼으로 봅니다."""

    values = [row.get(column) for row in rows if row.get(column) not in (None, "")]
    if not values:
        return False
    if any(token in str(column).lower() for token in ["id", "code", "no", "seq", "date", "dt"]):
        return False
    numeric_count = sum(1 for value in values if isinstance(value, (int, float)) and not isinstance(value, bool))
    return numeric_count / max(len(values), 1) >= 0.8


def _joined_view(datasets: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """여러 dataset을 공통 key 기준으로 outer join한 view를 만듭니다."""

    if len(datasets) < 2:
        return None
    common_keys = set(_strings(candidates[0].get("join_keys")) if candidates else [])
    for candidate in candidates[1:]:
        common_keys &= set(_strings(candidate.get("join_keys")))
    join_keys = [column for column in (_strings(datasets[0].get("columns")) or _columns_from_rows(_rows(datasets[0].get("rows")))) if column in common_keys]
    if not join_keys:
        return None

    joined_by_key: dict[tuple[str, ...], dict[str, Any]] = {}
    output_columns = list(join_keys)
    source_ids = [str(item.get("dataset_id") or f"dataset_{index}") for index, item in enumerate(datasets, start=1)]
    non_key_counts: dict[str, int] = {}
    for dataset in datasets:
        for column in _strings(dataset.get("columns")) or _columns_from_rows(_rows(dataset.get("rows"))):
            if column not in join_keys:
                non_key_counts[column] = non_key_counts.get(column, 0) + 1

    for dataset in datasets:
        dataset_id = str(dataset.get("dataset_id") or "")
        rows = _rows(dataset.get("rows"))
        for row in rows:
            key = tuple(str(row.get(column, "")) for column in join_keys)
            target = joined_by_key.setdefault(key, {column: row.get(column, "") for column in join_keys})
            for column, value in row.items():
                if column in join_keys:
                    continue
                output_name = _merged_column_name(column, dataset_id, non_key_counts.get(column, 0) > 1, output_columns)
                target[output_name] = value
                if output_name not in output_columns:
                    output_columns.append(output_name)

    rows = list(joined_by_key.values())
    label = " + ".join(str(item.get("label") or item.get("dataset_id") or "") for item in datasets[:3])
    return {
        "data_view_id": "joined_auto",
        "label": f"자동 결합 데이터 ({label})",
        "strategy": "join",
        "source_dataset_ids": source_ids,
        "join_keys": join_keys,
        "columns": output_columns,
        "rows": rows,
        "row_count": len(rows),
        "data_ref": {},
    }


def _merged_column_name(column: str, dataset_id: str, duplicated: bool, output_columns: list[str]) -> str:
    """join 시 같은 이름의 metric이 충돌하면 dataset_id suffix를 붙입니다."""

    if not duplicated:
        return column
    suffix = "".join(ch if ch.isalnum() else "_" for ch in str(dataset_id or "dataset")).strip("_") or "dataset"
    return f"{column}__{suffix}"


def _union_view(datasets: list[dict[str, Any]]) -> dict[str, Any] | None:
    """같은 구조의 여러 dataset을 세로로 붙인 union view를 만듭니다."""

    if len(datasets) < 2:
        return None
    column_sets = [set(_strings(item.get("columns")) or _columns_from_rows(_rows(item.get("rows")))) for item in datasets]
    if not column_sets or len({tuple(sorted(cols)) for cols in column_sets}) != 1:
        return None
    columns = list(_strings(datasets[0].get("columns")) or _columns_from_rows(_rows(datasets[0].get("rows"))))
    output_columns = ["__dataset_id", "__dataset_label", *columns]
    rows = []
    for dataset in datasets:
        dataset_id = str(dataset.get("dataset_id") or "")
        label = str(dataset.get("label") or dataset_id)
        for row in _rows(dataset.get("rows")):
            rows.append({"__dataset_id": dataset_id, "__dataset_label": label, **{column: row.get(column, "") for column in columns}})
    return {
        "data_view_id": "union_auto",
        "label": "자동 통합 데이터",
        "strategy": "union",
        "source_dataset_ids": [str(item.get("dataset_id") or "") for item in datasets],
        "join_keys": [],
        "columns": output_columns,
        "rows": rows,
        "row_count": len(rows),
        "data_ref": {},
    }


def _contains_text(text: str, needles: list[str]) -> bool:
    """문장에 특정 단어가 하나라도 포함되어 있는지 확인합니다."""

    haystack = str(text or "").lower()
    return any(needle.lower() in haystack for needle in needles)


def _select_dataset(datasets: list[dict[str, Any]]) -> dict[str, Any]:
    """현재 체험 flow에서는 첫 번째 데이터셋을 분석 대상으로 선택합니다."""

    return deepcopy(datasets[0]) if datasets else _dataset("demo_dataset", "Demo Dataset", [], [])


def _rows(value: Any) -> list[dict[str, Any]]:
    """값이 dict 목록이면 안전하게 복사해서 반환하고, 아니면 빈 목록을 반환합니다."""

    return [deepcopy(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _columns_from_rows(rows: list[dict[str, Any]]) -> list[str]:
    """rows에 실제로 등장한 컬럼명을 처음 나온 순서대로 모읍니다."""

    columns: list[str] = []
    for row in rows:
        for key in row:
            text = str(key)
            if text not in columns:
                columns.append(text)
    return columns


def _strings(value: Any) -> list[str]:
    """문자열 목록만 깨끗하게 정리하고 중복을 제거합니다."""

    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _dict(value: Any) -> dict[str, Any]:
    """값이 dict면 복사본을, 아니면 빈 dict를 반환합니다."""

    return deepcopy(value) if isinstance(value, dict) else {}


def _positive_int(value: Any, default: int) -> int:
    """정수로 변환하되 음수는 0으로 보정합니다."""

    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(0, parsed)


class DemoReportRequestLoader(Component):
    """Langflow 화면에 표시되는 00번 커스텀 컴포넌트 클래스."""

    display_name = "00 리포트 요청/데이터 불러오기"
    description = "질문, 표시 요청, 직접 입력 데이터 또는 File Read 결과를 HTML 리포트용 요청으로 정리합니다."
    icon = "FileInput"
    inputs = [
        MessageTextInput(name="question", display_name="질문", required=True),
        MessageTextInput(name="view_request", display_name="보고 싶은 방식", required=False),
        MessageTextInput(name="data_text", display_name="데이터 직접 입력", required=False),
        DataInput(
            name="file_data",
            display_name="파일 데이터",
            input_types=[
                "Data",
                "DataFrame",
                "Message",
                "Text",
                "File",
                "JSON",
                "StructuredContent",
                "Structured Content",
            ],
            required=False,
        ),
    ]
    outputs = [Output(name="payload", display_name="요청 데이터", method="build_payload")]

    def build_payload(self) -> Data:
        """Langflow가 이 노드를 실행할 때 호출하는 출력 생성 함수입니다."""

        result = build_demo_report_request(
            getattr(self, "question", ""),
            getattr(self, "view_request", ""),
            getattr(self, "data_text", ""),
            getattr(self, "file_data", None),
        )
        self.status = {
            "datasets": len(result.get("available_datasets", [])),
            "selected": (result.get("request") or {}).get("selected_dataset_id"),
            "rows": ((result.get("api_response") or {}).get("data") or {}).get("row_count", 0),
        }
        return Data(data=result)
