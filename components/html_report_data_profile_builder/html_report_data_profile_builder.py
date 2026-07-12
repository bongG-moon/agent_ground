from __future__ import annotations

"""01 데이터 구조 분석 노드.

이 파일은 00번 노드가 만든 rows/columns 데이터를 훑어서
어떤 컬럼이 숫자 metric인지, 어떤 컬럼이 날짜 축인지, 어떤 컬럼이 범주/상태인지
추정합니다. 이 분석 결과는 02번 요소 추천과 03번 리포트 계획 생성의 근거가 됩니다.
"""

import json
import re
from copy import deepcopy
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, Output
from lfx.schema.data import Data


# 컬럼명에 자주 등장하는 단어를 보고 의미를 추정하기 위한 정규식입니다.
# 예: `DATE`, `기준일`은 시간축 후보, `STATUS`, `오류`는 상태/예외 후보가 됩니다.
DATE_RE = re.compile(r"date|dt|time|day|week|month|year|날짜|일자|기준일|월|주차|시간", re.I)
ID_RE = re.compile(r"(^|_)(id|key|code|no|seq)($|_)|코드|번호|식별", re.I)
DELTA_RE = re.compile(r"delta|diff|change|증감|변화|차이|gap|rate|ratio|percent|%|율", re.I)
STATUS_RE = re.compile(r"status|state|flag|error|warning|issue|risk|상태|오류|경고|이상|위험", re.I)


def build_data_profile(analysis_payload: Any, question: str = "") -> dict[str, Any]:
    """입력 payload를 분석해서 리포트 계획에 필요한 데이터 프로파일을 만듭니다.

    주요 결과:
    - `columns`: 각 컬럼의 타입 추정 결과와 샘플 값
    - `column_groups`: 숫자/범주/날짜/상태 컬럼 묶음
    - `question_hints`: 질문 문구에서 요약/비교/추이 등의 의도 힌트
    - `report_signals`: 어떤 리포트 요소가 필요할지 판단하는 신호
    """

    payload = _payload(analysis_payload)
    request = _dict(payload.get("request"))
    resolved_question = str(question or "").strip() or str(request.get("question") or payload.get("question") or "").strip()
    view_request = str(request.get("view_request") or payload.get("view_request") or "").strip()
    intent_text = " ".join(text for text in (resolved_question, view_request) if text)
    api = _dict(payload.get("api_response")) or payload
    data = _dict(api.get("data")) or _dict(payload.get("data"))
    rows = _rows(data.get("rows")) or _rows(api.get("rows")) or _rows(payload.get("rows"))
    columns = _strings(data.get("columns")) or _strings(api.get("columns")) or _columns_from_rows(rows)
    row_count = _int(data.get("row_count"), _int(api.get("row_count"), len(rows)))
    data_ref = _dict(data.get("data_ref")) or _dict(api.get("data_ref"))
    warnings = _unique([*_list(payload.get("warnings")), *_list(api.get("warnings"))])
    errors = _unique([*_list(payload.get("errors")), *_list(api.get("errors"))])
    data_views = _list(payload.get("data_views"))
    available_data_views = _list(payload.get("available_data_views"))
    relationship_candidates = _list(payload.get("relationship_candidates"))
    # 너무 많은 row를 모두 훑으면 Langflow 실행이 무거워질 수 있어 앞 200개만 샘플로 분석합니다.
    column_profiles = [_profile_column(column, rows[:200]) for column in columns]
    groups = _groups(column_profiles)
    hints = _hints(intent_text)
    shape = {
        "row_count": row_count,
        "preview_row_count": len(rows),
        "column_count": len(columns),
        "data_is_preview": bool(data.get("data_is_preview") or api.get("data_is_preview") or row_count > len(rows)),
        "data_ref_present": bool(data_ref),
        "data_ref_loaded": bool(data.get("data_ref_loaded") or api.get("data_ref_loaded")),
        "data_ref_load_mode": data.get("data_ref_load_mode") or api.get("data_ref_load_mode") or "",
        "active_data_view_id": data.get("data_view_id") or request.get("active_data_view_id") or "",
        "active_data_view_strategy": data.get("strategy") or "",
    }
    signals = {
        "has_numeric_metrics": bool(groups["numeric_columns"]),
        "has_dimensions": bool(groups["dimension_columns"]),
        "has_time_axis": bool(groups["time_columns"]),
        "has_delta_or_rate": bool(groups["delta_columns"]),
        "has_status_or_exception": bool(groups["status_columns"] or warnings or errors),
        "looks_like_query_result": hints["detail"] or (row_count > 0 and not hints["summary"]),
        "looks_like_aggregate_result": row_count <= 50 and bool(groups["numeric_columns"]),
        "needs_preview_warning": bool(shape["data_is_preview"] or warnings or errors),
    }
    return {
        "profile_version": "html-report-data-profile-v1",
        "question": resolved_question,
        "view_request": view_request,
        "intent_text": intent_text,
        "shape": shape,
        "columns": column_profiles,
        "column_groups": groups,
        "question_hints": hints,
        "report_signals": signals,
        "available_datasets": _list(payload.get("available_datasets")),
        "available_data_views": available_data_views,
        "relationship_candidates": relationship_candidates,
        "data_view_profiles": [_profile_data_view(view) for view in data_views[:8] if isinstance(view, dict)],
        "data_ref": data_ref,
        "warnings": warnings,
        "errors": errors,
        "preview_rows": deepcopy(rows[:10]),
    }


def _profile_data_view(view: dict[str, Any]) -> dict[str, Any]:
    """여러 데이터셋/결합 view를 LLM이 이해할 수 있도록 작게 요약합니다."""

    rows = _rows(view.get("rows"))
    columns = _strings(view.get("columns")) or _columns_from_rows(rows)
    profiles = [_profile_column(column, rows[:120]) for column in columns]
    return {
        "data_view_id": str(view.get("data_view_id") or ""),
        "label": str(view.get("label") or view.get("data_view_id") or ""),
        "strategy": str(view.get("strategy") or "select"),
        "source_dataset_ids": _strings(view.get("source_dataset_ids")),
        "join_keys": _strings(view.get("join_keys")),
        "shape": {
            "row_count": _int(view.get("row_count"), len(rows)),
            "preview_row_count": len(rows),
            "column_count": len(columns),
        },
        "columns": [
            {
                "name": item.get("name"),
                "inferred_type": item.get("inferred_type"),
                "semantic_hint": item.get("semantic_hint", ""),
                "sample_values": _list(item.get("sample_values"))[:3],
                "top_values": _list(item.get("top_values"))[:8],
                "numeric_stats": _dict(item.get("numeric_stats")),
            }
            for item in profiles
        ],
        "column_groups": _groups(profiles),
        "preview_rows": deepcopy(rows[:3]),
    }


def _profile_column(column: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """한 컬럼의 값 분포를 보고 타입, 결측 수, 샘플 값, 숫자 통계를 계산합니다."""

    values = [row.get(column) for row in rows]
    non_empty = [value for value in values if value not in (None, "", [], {})]
    unique_values = _unique(non_empty)
    inferred = _infer_type(column, non_empty, unique_values)
    numeric_values = [number for number in (_number(value) for value in non_empty) if number is not None]
    result: dict[str, Any] = {
        "name": column,
        "inferred_type": inferred,
        "non_empty_count": len(non_empty),
        "null_count": len(values) - len(non_empty),
        "unique_count": len(unique_values),
        "sample_values": _sample(non_empty),
        "top_values": _top_values(non_empty),
        "is_metric_candidate": inferred == "numeric",
        "is_dimension_candidate": inferred in {"dimension", "id", "status"},
        "is_time_candidate": inferred == "time",
        "is_text_candidate": inferred == "text",
    }
    if DELTA_RE.search(column):
        # 증감률/변화량처럼 보이는 컬럼은 카드나 해석에서 별도로 활용할 수 있게 표시합니다.
        result["semantic_hint"] = "delta_or_rate"
    if STATUS_RE.search(column):
        # 오류/경고/상태 컬럼은 예외 테이블 후보가 됩니다.
        result["semantic_hint"] = "status_or_exception"
    if numeric_values:
        result["numeric_stats"] = {
            "min": min(numeric_values),
            "max": max(numeric_values),
            "sum": sum(numeric_values),
            "avg": sum(numeric_values) / len(numeric_values),
        }
    return result


def _infer_type(column: str, values: list[Any], unique_values: list[Any]) -> str:
    """컬럼명과 실제 값 샘플을 함께 보고 간단한 타입을 추정합니다."""

    if not values:
        return "empty"
    if DATE_RE.search(column) or _date_ratio(values) >= 0.6:
        return "time"
    if _numeric_ratio(values) >= 0.8:
        return "numeric"
    if STATUS_RE.search(column):
        return "status"
    if ID_RE.search(column) and len(unique_values) >= min(len(values), 20):
        return "id"
    if sum(len(str(value)) for value in values) / max(len(values), 1) >= 50:
        return "text"
    return "dimension"


def _groups(column_profiles: list[dict[str, Any]]) -> dict[str, list[str]]:
    """컬럼별 프로파일을 숫자/범주/날짜/상태 같은 그룹으로 다시 묶습니다."""

    groups = {
        "numeric_columns": [],
        "dimension_columns": [],
        "time_columns": [],
        "text_columns": [],
        "id_columns": [],
        "status_columns": [],
        "delta_columns": [],
    }
    for profile in column_profiles:
        name = str(profile.get("name") or "")
        inferred = profile.get("inferred_type")
        if inferred == "numeric":
            groups["numeric_columns"].append(name)
        if inferred in {"dimension", "status"}:
            groups["dimension_columns"].append(name)
        if inferred == "time":
            groups["time_columns"].append(name)
        if inferred == "text":
            groups["text_columns"].append(name)
        if inferred == "id":
            groups["id_columns"].append(name)
        if inferred == "status" or profile.get("semantic_hint") == "status_or_exception":
            groups["status_columns"].append(name)
        if profile.get("semantic_hint") == "delta_or_rate":
            groups["delta_columns"].append(name)
    return groups


def _hints(question: str) -> dict[str, bool]:
    """사용자 질문 문장에 포함된 단어로 리포트 의도를 추정합니다."""

    text = str(question or "").lower()
    return {
        "summary": _has(text, ["요약", "현황", "핵심", "주요", "지표", "카드", "summary", "overview", "kpi", "card"]),
        "comparison": _has(text, ["비교", "차이", "별", "막대", "그래프", "차트", "compare", "comparison", "versus", "vs", "bar", "chart"]),
        "trend": _has(text, ["추이", "변화", "기간", "날짜", "일별", "주별", "월별", "시계열", "선그래프", "선 그래프", "라인", "trend", "daily", "weekly", "monthly", "line", "timeline"]),
        "ranking": _has(text, ["상위", "하위", "top", "bottom", "순위", "랭킹", "가장 많은", "가장 적은"]),
        "detail": _has(text, ["상세", "목록", "리스트", "표", "테이블", "상세표", "row", "raw", "table", "원본", "그대로", "보여줘", "조회"]),
        "exception": _has(text, ["이상", "문제", "경고", "오류", "리스크", "초과", "미달", "exception", "error", "warning", "risk"]),
        "report": _has(text, ["리포트", "보고서", "html", "dashboard", "대시보드", "공유"]),
    }


def _has(text: str, needles: list[str]) -> bool:
    """문장 안에 후보 단어 중 하나라도 포함되어 있는지 확인합니다."""

    return any(needle.lower() in text for needle in needles)


def _payload(value: Any) -> dict[str, Any]:
    """Langflow Data/Message/dict/JSON 문자열을 일반 dict로 맞춥니다."""

    if isinstance(value, dict):
        return deepcopy(value)
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return deepcopy(data)
    text = getattr(value, "text", None) or getattr(value, "content", None)
    if isinstance(text, str) and text.strip():
        try:
            parsed = json.loads(text)
        except Exception:
            return {"text": text}
        return deepcopy(parsed) if isinstance(parsed, dict) else {"text": text}
    return {}


def _rows(value: Any) -> list[dict[str, Any]]:
    """dict row 목록만 안전하게 복사해서 반환합니다."""

    return [deepcopy(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _columns_from_rows(rows: list[dict[str, Any]]) -> list[str]:
    """rows에 등장한 컬럼명을 중복 없이 수집합니다."""

    columns: list[str] = []
    for row in rows:
        for key in row:
            text = str(key)
            if text not in columns:
                columns.append(text)
    return columns


def _strings(value: Any) -> list[str]:
    """문자열 목록을 정리하고 중복을 제거합니다."""

    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _dict(value: Any) -> dict[str, Any]:
    """dict가 아닌 값은 빈 dict로 바꿔 뒤 로직을 단순하게 만듭니다."""

    return deepcopy(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    """list/tuple/set이면 list로 바꾸고, 아니면 빈 list를 반환합니다."""

    return list(value) if isinstance(value, (list, tuple, set)) else []


def _int(value: Any, fallback: int = 0) -> int:
    """문자열/실수를 정수로 바꾸되 실패하면 fallback을 반환합니다."""

    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value == value:
        return int(value)
    try:
        return int(str(value).replace(",", ""))
    except Exception:
        return fallback


def _number(value: Any) -> float | None:
    """값이 숫자로 해석 가능하면 float로, 아니면 None으로 반환합니다."""

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value == value:
        return float(value)
    text = str(value or "").strip().replace(",", "").replace("%", "")
    try:
        return float(text) if text else None
    except Exception:
        return None


def _numeric_ratio(values: list[Any]) -> float:
    """값 목록 중 숫자로 해석 가능한 값의 비율을 계산합니다."""

    return sum(1 for value in values if _number(value) is not None) / len(values) if values else 0.0


def _date_ratio(values: list[Any]) -> float:
    """값 목록 중 날짜처럼 보이는 값의 비율을 계산합니다."""

    return sum(1 for value in values if _looks_like_date(value)) / len(values) if values else 0.0


def _looks_like_date(value: Any) -> bool:
    """간단한 날짜 형식인지 확인합니다. 예: 2026-06-20, 20260620."""

    text = str(value or "").strip()
    return bool(re.match(r"^\d{4}[-/]\d{1,2}([-/]\d{1,2})?", text) or re.match(r"^\d{6}(\d{2})?$", text))


def _sample(values: list[Any], limit: int = 5) -> list[Any]:
    """중복을 제거한 샘플 값을 최대 limit개 반환합니다."""

    return _unique(values)[:limit]


def _top_values(values: list[Any], limit: int = 12) -> list[dict[str, Any]]:
    """범주/상태/코드 값의 빈도를 LLM이 이해하기 쉬운 형태로 요약합니다.

    예를 들어 `ALERT_LEVEL` 컬럼에 HIGH/WARN/NORMAL 값이 있으면
    LLM이 정확한 값 문자열을 사용해 filter_rules나 highlight_rules를 만들 수 있습니다.
    """

    counts: dict[str, dict[str, Any]] = {}
    for value in values:
        if value in (None, "", [], {}):
            continue
        signature = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if signature not in counts:
            counts[signature] = {"value": deepcopy(value), "count": 0}
        counts[signature]["count"] += 1
    ranked = sorted(counts.values(), key=lambda item: (-int(item["count"]), str(item["value"])))
    return ranked[:limit]


def _unique(values: list[Any]) -> list[Any]:
    """값 목록에서 빈 값과 중복 값을 제거합니다."""

    result: list[Any] = []
    signatures: set[str] = set()
    for value in values:
        if value in (None, "", [], {}):
            continue
        signature = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if signature not in signatures:
            signatures.add(signature)
            result.append(deepcopy(value))
    return result


class HtmlReportDataProfileBuilder(Component):
    """Langflow 화면에 표시되는 01번 커스텀 컴포넌트 클래스."""

    display_name = "01 데이터 구조 분석"
    description = "리포트에 쓸 컬럼 유형, 숫자/범주/날짜 후보, 데이터 품질 신호를 요약합니다."
    icon = "ScanSearch"
    inputs = [
        DataInput(name="analysis_payload", display_name="요청 데이터", required=True),
    ]
    outputs = [Output(name="data_profile", display_name="데이터 분석 결과", method="build_profile")]

    def build_profile(self) -> Data:
        """00번 요청 데이터를 받아 데이터 분석 결과를 출력합니다."""

        result = build_data_profile(getattr(self, "analysis_payload", None))
        groups = result.get("column_groups") or {}
        self.status = {
            "rows": (result.get("shape") or {}).get("row_count", 0),
            "columns": (result.get("shape") or {}).get("column_count", 0),
            "numeric": len(groups.get("numeric_columns", [])),
            "time": len(groups.get("time_columns", [])),
        }
        return Data(data=result)
