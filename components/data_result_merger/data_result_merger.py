from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Dict

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, Output
from lfx.schema.data import Data


def _make_data(payload: Dict[str, Any]) -> Any:
    """Langflow 버전에 따라 Data 생성자 모양이 달라지는 부분을 흡수합니다."""
    try:
        return Data(data=payload)
    except TypeError:
        return Data(payload)


def _payload_from_value(value: Any) -> Dict[str, Any]:
    """Data node 출력이 Data/JSON/Text 중 무엇이든 내부 처리용 dict로 맞춥니다."""
    # 연결이 비어 있으면 처리할 source가 없는 것으로 봅니다.
    if value is None:
        return {}

    # 이미 dict면 원본을 건드리지 않도록 복사합니다.
    if isinstance(value, dict):
        return deepcopy(value)

    # Langflow Data 객체는 .data에 payload를 담습니다.
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return deepcopy(data)

    # Message/Text로 들어온 JSON도 최대한 dict로 해석합니다.
    text = getattr(value, "text", None) or getattr(value, "content", None)
    if isinstance(text, str):
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {"text": text}
        except Exception:
            # JSON이 아니면 원문만 남기고 실제 병합에서는 payload 없음처럼 처리됩니다.
            return {"text": text}
    return {}


def _source_payload(value: Any) -> Dict[str, Any]:
    """source_result wrapper를 벗겨 data node가 만든 payload만 반환합니다."""
    # 각 data node 출력이 source_result로 한 번 감싸져 있어도 같은 방식으로 읽을 수 있게 합니다.
    payload = _payload_from_value(value)
    if isinstance(payload.get("source_result"), dict):
        return deepcopy(payload["source_result"])
    return payload


def _columns_from_rows(rows: list[Dict[str, Any]]) -> list[str]:
    """여러 source row를 합친 뒤 표시할 컬럼 순서를 만듭니다."""
    # row마다 컬럼이 다를 수 있으므로 처음 등장한 key 순서를 유지해 전체 컬럼 목록을 만듭니다.
    columns: list[str] = []
    for row in rows:
        for key in row:
            text = str(key)
            if text not in columns:
                columns.append(text)
    return columns


def _source_group(item: Dict[str, Any]) -> Dict[str, Any]:
    """같은 source_type 요청이 여러 개일 때도 요청별 결과 묶음을 보존합니다."""
    # merger 내부 data_result는 기존 연결 호환을 위해 전체 row를 합쳐 두고,
    # 최종 API 출력은 Data Output Builder가 source_results 기준으로 다시 나눕니다.
    source_rows = item.get("data_result")
    if not isinstance(source_rows, list):
        source_rows = item.get("rows", [])
    rows = [deepcopy(row) for row in source_rows if isinstance(row, dict)]

    group = {
        "name": item.get("name", ""),
        "source_type": item.get("source_type", ""),
        "success": bool(item.get("success")),
        "row_count": int(item.get("row_count", len(rows)) or 0),
        "columns": item.get("columns") if isinstance(item.get("columns"), list) else _columns_from_rows(rows),
        "data_result": rows,
        "error_message": item.get("error_message", ""),
    }
    if item.get("failure_type") not in (None, "", [], {}):
        group["failure_type"] = item.get("failure_type")

    # data node가 request_params/request_label을 제공하는 경우 그대로 노출합니다.
    # 같은 Oracle source를 날짜/공정만 바꿔 여러 번 조회할 때 구분용으로 유용합니다.
    if isinstance(item.get("request_params"), dict):
        group["request_params"] = deepcopy(item["request_params"])
    if item.get("request_label") not in (None, "", [], {}):
        group["request_label"] = item.get("request_label")

    # 실제 데이터 row에는 넣지 않지만 source별 실행 확인에 필요한 값은 source_results에 보존합니다.
    for key in ("db_key", "executed_query", "request_body", "doc_id"):
        if item.get(key) not in (None, "", [], {}):
            group[key] = deepcopy(item[key])
    return group


def merge_simple_data_results(
    oracle_result_value: Any = None,
    h_api_result_value: Any = None,
    datalake_result_value: Any = None,
    goodocs_result_value: Any = None,
) -> Dict[str, Any]:
    """Oracle/H-API/Datalake/Goodocs 결과를 한 개의 data_result로 합칩니다."""
    # 각 전용 data node는 같은 data_request를 받아도 자기 source_type이 없으면 skipped=True를 냅니다.
    # merger는 네 data node 출력을 한 번에 받아 실제 처리된 source만 최종 결과에 포함합니다.
    source_values = [oracle_result_value, h_api_result_value, datalake_result_value, goodocs_result_value]
    source_items: list[Dict[str, Any]] = []
    source_groups: list[Dict[str, Any]] = []
    combined_rows: list[Dict[str, Any]] = []

    for value in source_values:
        # Langflow Data wrapper 또는 source_result wrapper를 벗겨 data node payload만 남깁니다.
        payload = _source_payload(value)
        if not payload:
            continue
        if payload.get("skipped"):
            # 다른 타입 data node가 "내 요청이 아님"이라고 알린 경우라서 최종 payload에는 싣지 않습니다.
            continue

        # 현재 data node 표준은 items이지만, 이전 테스트 payload와의 호환을 위해 results도 허용합니다.
        items = payload.get("items")
        if not isinstance(items, list):
            items = payload.get("results") if isinstance(payload.get("results"), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            # 전체 성공 여부를 판단하기 위해 data node item을 보관합니다.
            source_items.append(deepcopy(item))
            # source_results에는 요청별 상세 결과를 보존합니다.
            source_groups.append(_source_group(item))
            if item.get("success"):
                # 성공한 source의 data_result만 합쳐 최종 표 데이터로 만듭니다.
                # 과거 rows 필드로 오는 payload도 data_result로 취급합니다.
                source_rows = item.get("data_result")
                if not isinstance(source_rows, list):
                    source_rows = item.get("rows", [])
                for row in source_rows:
                    if isinstance(row, dict):
                        combined_rows.append(deepcopy(row))

    # 하나라도 실패 source가 있으면 전체 success는 false로 둡니다.
    # source가 한 개면 single, 여러 개면 multi로 표시해 Chat Output에서 흐름을 빠르게 볼 수 있게 합니다.
    has_failure = any(not bool(item.get("success")) for item in source_items)
    success = bool(source_items) and not has_failure and any(bool(item.get("success")) for item in source_items)
    data_result = {
        "success": success,
        "mode": "single" if len(source_items) == 1 else "multi",
        "data_result": combined_rows,
        "source_results": source_groups,
    }
    if not source_items:
        # 어떤 data node도 처리하지 않았다면 flow 연결 또는 source_type 매칭 문제일 가능성이 큽니다.
        data_result["mode"] = "empty"
        data_result["source_results"] = [
            {
                "name": "",
                "source_type": "",
                "success": False,
                "row_count": 0,
                "columns": [],
                "data_result": [],
                "error_message": "No source data node produced data.",
                "failure_type": "no_source_result",
            }
        ]
    return {"data_result": data_result}


class DataResultMerger(Component):
    """네 data node의 결과를 한 번에 받아 최종 data_result 하나로 합치는 노드입니다."""
    display_name = "Data Result Merger"
    description = "Oracle, H-API, Datalake, Goodocs 조회 결과를 하나의 data_result로 병합합니다."
    icon = "Merge"
    name = "DataResultMerger"

    inputs = [
        DataInput(name="oracle_result", display_name="Oracle Result", input_types=["Data", "JSON"]),
        DataInput(name="h_api_result", display_name="H-API Result", input_types=["Data", "JSON"]),
        DataInput(name="datalake_result", display_name="Datalake Result", input_types=["Data", "JSON"]),
        DataInput(name="goodocs_result", display_name="Goodocs Result", input_types=["Data", "JSON"]),
    ]
    outputs = [Output(name="data_result", display_name="Data Result", method="build_data_result", types=["Data"])]

    def build_data_result(self):
        """각 source_result를 병합하고 상태 표시용 row/source 개수를 기록합니다."""
        # 네 data node 출력 포트를 모두 받아 하나의 병합 함수에 넘깁니다.
        payload = merge_simple_data_results(
            getattr(self, "oracle_result", None),
            getattr(self, "h_api_result", None),
            getattr(self, "datalake_result", None),
            getattr(self, "goodocs_result", None),
        )
        result = payload.get("data_result", {})

        # Langflow 노드 status에는 병합 성공 여부와 규모만 작게 표시합니다.
        self.status = {
            "success": bool(result.get("success")),
            "mode": result.get("mode", ""),
            "source_count": len(result.get("source_results", [])) if isinstance(result.get("source_results"), list) else 0,
            "row_count": len(result.get("data_result", [])) if isinstance(result.get("data_result"), list) else 0,
        }
        return _make_data(payload)
