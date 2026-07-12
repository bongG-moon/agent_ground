from __future__ import annotations

"""11 HTML Report Datasets Adapter.

06 Data Output Builder의 data_json을 HTML 생성 flow의 00번 노드가 이해하는
`{"datasets": [...]}` JSON으로 바꾸는 얇은 어댑터입니다.
단일 조회도 datasets 1개짜리로 맞춰서 뒤 flow 입력 형태를 단순하게 유지합니다.
"""

import json
import re
from copy import deepcopy
from typing import Any, Dict

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message


def _make_data(payload: Dict[str, Any]) -> Any:
    """Langflow 버전에 따른 Data 생성자 차이를 흡수합니다."""

    try:
        return Data(data=payload)
    except TypeError:
        return Data(payload)


def _make_message(text: str) -> Any:
    """Langflow 버전에 따른 Message 생성자 차이를 흡수합니다."""

    try:
        return Message(text=text)
    except TypeError:
        try:
            return Message(content=text)
        except TypeError:
            return Message(text)


def _payload_from_value(value: Any) -> Dict[str, Any]:
    """Data/Message/dict/JSON 문자열 중 무엇이 들어와도 dict로 맞춥니다."""

    if value is None:
        return {}
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


def _rows(value: Any) -> list[Dict[str, Any]]:
    """list[dict] row만 안전하게 복사합니다."""

    if not isinstance(value, list):
        return []
    return [deepcopy(row) for row in value if isinstance(row, dict)]


def _result_groups(payload: Dict[str, Any]) -> list[list[Dict[str, Any]]]:
    """06번 data_json에서 조회 단위별 row 묶음을 꺼냅니다."""

    data_result = payload.get("data_result")
    if isinstance(data_result, list):
        if all(isinstance(item, dict) for item in data_result):
            return [_rows(data_result)]
        groups = []
        for item in data_result:
            rows = _rows(item)
            if rows:
                groups.append(rows)
        if groups:
            return groups

    source_results = payload.get("source_results")
    if isinstance(source_results, list):
        groups = []
        for source in source_results:
            if isinstance(source, dict):
                rows = _rows(source.get("data_result")) or _rows(source.get("rows"))
                if rows:
                    groups.append(rows)
        if groups:
            return groups

    rows = _rows(payload.get("rows"))
    return [rows] if rows else []


def _source_results(payload: Dict[str, Any]) -> list[Dict[str, Any]]:
    """source_results metadata를 list[dict]로 정리합니다."""

    value = payload.get("source_results")
    return [deepcopy(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _dataset_id(source: Dict[str, Any], index: int) -> str:
    """source 이름을 dataset_id로 쓰되, 비어 있거나 이상하면 dataset_N으로 대체합니다."""

    raw = str(source.get("name") or source.get("source_name") or source.get("source_type") or f"dataset_{index}").strip()
    text = re.sub(r"[^0-9A-Za-z가-힣_.-]+", "_", raw).strip("_.-")
    return text or f"dataset_{index}"


def _dataset_label(source: Dict[str, Any], dataset_id: str) -> str:
    """HTML 리포트에서 보이는 dataset label을 만듭니다."""

    name = str(source.get("name") or source.get("source_name") or dataset_id).strip()
    source_type = str(source.get("source_type") or "").strip()
    return f"{name} ({source_type})" if source_type and source_type not in name else name or dataset_id


def build_html_report_datasets(data_json_value: Any) -> Dict[str, Any]:
    """06번 data_json을 HTML flow의 00번 입력용 datasets JSON으로 변환합니다."""

    payload = _payload_from_value(data_json_value)
    groups = _result_groups(payload)
    sources = _source_results(payload)
    datasets = []

    for index, rows in enumerate(groups, start=1):
        source = sources[index - 1] if index - 1 < len(sources) else {}
        dataset_id = _dataset_id(source, index)
        datasets.append(
            {
                "dataset_id": dataset_id,
                "label": _dataset_label(source, dataset_id),
                "rows": rows,
            }
        )

    return {
        "datasets": datasets,
        "source": "reusable_data_flow.data_json",
        "success": bool(payload.get("success", bool(datasets))),
        "mode": "single" if len(datasets) == 1 else "multi" if len(datasets) > 1 else "empty",
    }


class HtmlReportDatasetsAdapter(Component):
    """Data Output Builder.data_json을 HTML Report Flow 입력 JSON으로 바꾸는 노드입니다."""

    display_name = "11 HTML Report Datasets Adapter"
    description = "06 Data JSON을 HTML 생성 Flow의 datasets JSON 입력으로 변환합니다."
    icon = "ArrowRightLeft"
    name = "HtmlReportDatasetsAdapter"

    inputs = [
        DataInput(name="data_json", display_name="Data JSON", input_types=["Data", "JSON", "Message", "Text"]),
    ]
    outputs = [
        Output(name="html_datasets_data", display_name="HTML Datasets Data", method="build_datasets_data", group_outputs=True, types=["Data"]),
        Output(name="html_datasets_text", display_name="HTML Datasets Text", method="build_datasets_text", group_outputs=True, types=["Message"]),
    ]

    def _payload(self) -> Dict[str, Any]:
        """두 output이 같은 변환 결과를 공유하도록 캐시합니다."""

        cached = getattr(self, "_cached_payload", None)
        if isinstance(cached, dict):
            return cached
        payload = build_html_report_datasets(getattr(self, "data_json", None))
        self._cached_payload = payload
        self.status = {
            "mode": payload.get("mode"),
            "datasets": len(payload.get("datasets", [])),
        }
        return payload

    def build_datasets_data(self):
        """다른 DataInput 노드에 연결할 구조화 Data 출력입니다."""

        return _make_data(self._payload())

    def build_datasets_text(self):
        """HTML flow 00번의 직접 입력칸에 붙여넣거나 Message로 연결할 JSON 문자열입니다."""

        return _make_message(json.dumps(self._payload(), ensure_ascii=False, indent=2, default=str))
