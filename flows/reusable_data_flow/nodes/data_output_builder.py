from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Dict

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MessageTextInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message


DEFAULT_MAX_MESSAGE_ROWS = 20
DEFAULT_MAX_CELL_CHARS = 120


def _make_data(payload: Dict[str, Any]) -> Any:
    """Langflow 버전에 따라 Data 생성자 모양이 달라지는 부분을 흡수합니다."""
    try:
        return Data(data=payload)
    except TypeError:
        return Data(payload)


def _make_message(text: str) -> Any:
    """Langflow 버전에 따라 Message 생성자 모양이 달라지는 부분을 흡수합니다."""
    try:
        return Message(text=text)
    except TypeError:
        try:
            return Message(content=text)
        except TypeError:
            return Message(text)


def _payload_from_value(value: Any) -> Dict[str, Any]:
    """Merger 출력이 Data/JSON/Text 중 무엇이든 내부 처리용 dict로 맞춥니다."""
    # 연결이 비어 있으면 출력할 body가 없는 상태입니다.
    if value is None:
        return {}

    # dict payload는 원본 변경을 피하기 위해 복사합니다.
    if isinstance(value, dict):
        return deepcopy(value)

    # Langflow Data 객체는 .data 안에 merger 결과를 담습니다.
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return deepcopy(data)

    # Message/Text로 JSON이 들어온 경우도 허용합니다.
    text = getattr(value, "text", None) or getattr(value, "content", None)
    if isinstance(text, str):
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {"text": text}
        except Exception:
            # JSON이 아니면 원문만 남기고 body 변환 단계에서 빈 결과로 처리됩니다.
            return {"text": text}
    return {}


def _body_from_value(value: Any) -> Dict[str, Any]:
    """data_result wrapper가 있으면 벗기고, 없으면 payload 자체를 결과 body로 사용합니다."""
    # Data Result Merger 표준 출력은 {"data_result": {...}} 모양입니다.
    payload = _payload_from_value(value)
    body = payload.get("data_result") if isinstance(payload.get("data_result"), dict) else payload
    return deepcopy(body) if isinstance(body, dict) else {}


def _as_rows(value: Any) -> list[Dict[str, Any]]:
    """list[dict] 형태의 row만 안전하게 복사합니다."""
    if not isinstance(value, list):
        return []
    rows: list[Dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            rows.append(deepcopy(item))
    return rows


def _result_rows(body: Dict[str, Any]) -> list[Dict[str, Any]]:
    """현재 표준 필드인 data_result를 우선 사용하고, 과거 rows 필드도 읽어 줍니다."""
    rows = _as_rows(body.get("data_result"))
    if rows:
        return rows
    return _as_rows(body.get("rows"))


def _data_rows_from_source(source: Dict[str, Any]) -> list[Dict[str, Any]]:
    """source_results 한 항목에서 실제 row 배열만 꺼내 최상위 data_result 항목으로 만듭니다."""
    # 최상위 data_result는 조회 단위로 나누되, 각 항목 안에 data_result wrapper를 다시 만들지 않습니다.
    # 조회 결과가 1행이어도 항상 list[dict]로 유지해야 downstream에서 같은 구조로 처리할 수 있습니다.
    return _as_rows(source.get("data_result"))


def _top_level_data_result(body: Dict[str, Any]) -> list[list[Dict[str, Any]]]:
    """최상위 data_result를 source_results 기준의 row 배열 목록으로 만듭니다."""
    source_results = body.get("source_results") if isinstance(body.get("source_results"), list) else []
    if source_results:
        return [_data_rows_from_source(source) for source in source_results if isinstance(source, dict)]

    # source_results가 없는 예전 payload는 한 번의 조회 결과로 보고 한 묶음 안에 담습니다.
    rows = _result_rows(body)
    return [rows] if rows else []


def _rows_from_grouped_results(grouped_results: Any) -> list[Dict[str, Any]]:
    """조회 단위 data_result 목록에서 Chat Output 표시용 row 목록만 평탄화합니다."""
    # 이 평탄화는 확인용 메시지 행 수/표시에만 사용합니다.
    # API용 data_json.data_result는 조회 단위로 나뉜 실제 데이터 목록을 유지합니다.
    if not isinstance(grouped_results, list):
        return []

    rows: list[Dict[str, Any]] = []
    for item in grouped_results:
        if isinstance(item, dict):
            rows.append(deepcopy(item))
        elif isinstance(item, list):
            for row in _as_rows(item):
                rows.append(row)
    return rows


def _message_rows(body: Dict[str, Any]) -> list[Dict[str, Any]]:
    """Chat Output 표시에 사용할 전체 row 목록을 만듭니다."""
    # 최상위 data_result는 조회 단위로 묶지만, Chat Output 표는 전체 row 수를 보여주는 편이 읽기 쉽습니다.
    grouped_rows = _rows_from_grouped_results(_top_level_data_result(body))
    if grouped_rows:
        return grouped_rows
    return _result_rows(body)


def _clean_data_json_body(body: Dict[str, Any]) -> Dict[str, Any]:
    """외부로 내보낼 JSON을 필요한 필드만 남긴 단순한 결과 본문으로 정리합니다."""
    # 이전 버전 payload에 columns/row_count/sources/errors가 남아 있어도 최종 출력에는 싣지 않습니다.
    source_results = body.get("source_results") if isinstance(body.get("source_results"), list) else []
    mode = str(body.get("mode") or ("single" if len(source_results) == 1 else "multi" if source_results else "empty"))
    return {
        "success": bool(body.get("success", False)),
        "mode": mode,
        "data_result": _top_level_data_result(body),
        "source_results": deepcopy(source_results),
    }


def _positive_int(value: Any, default_value: int) -> int:
    """advanced input 값을 양수 정수로 정리하고, 잘못된 값이면 기본값을 사용합니다."""
    try:
        number = int(str(value or "").strip())
    except Exception:
        return default_value
    return number if number > 0 else default_value


def _columns_from_body(body: Dict[str, Any], rows: list[Dict[str, Any]]) -> list[str]:
    """body.columns와 실제 row key를 합쳐 표시 컬럼 순서를 만듭니다."""
    columns: list[str] = []

    # merger가 넘긴 columns를 우선 사용합니다.
    for column in body.get("columns", []):
        text = str(column or "").strip()
        if text and text not in columns:
            columns.append(text)

    # columns에 없는 key가 row에 있으면 뒤에 추가해 데이터가 누락되어 보이지 않게 합니다.
    for row in rows:
        for key in row:
            text = str(key)
            if text not in columns:
                columns.append(text)
    return columns


def _cell_text(value: Any, max_cell_chars: int = DEFAULT_MAX_CELL_CHARS) -> str:
    """Markdown 표 한 칸에 넣을 값을 짧고 안전한 문자열로 바꿉니다."""
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        # 복잡한 값은 JSON 문자열로 바꿔 표 안에서 구조를 볼 수 있게 합니다.
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)

    # Markdown 표 문법을 깨는 줄바꿈과 파이프 문자를 정리합니다.
    text = text.replace("\r", " ").replace("\n", " ").replace("|", "\\|").strip()
    if len(text) > max_cell_chars:
        # 너무 긴 셀은 Chat Output 가독성을 위해 잘라냅니다.
        return text[: max_cell_chars - 1] + "..."
    return text


def _markdown_table(rows: list[Dict[str, Any]], columns: list[str], max_message_rows: int = DEFAULT_MAX_MESSAGE_ROWS, max_cell_chars: int = DEFAULT_MAX_CELL_CHARS) -> str:
    """테스트용 Message 출력에서 데이터를 빠르게 확인할 Markdown 표를 만듭니다."""
    # 조회 결과가 없을 때도 Chat Output이 빈 화면처럼 보이지 않도록 안내 문구를 반환합니다.
    if not rows:
        return "_표시할 데이터가 없습니다._"

    # columns가 비어 있으면 row의 key 순서를 모아 표 헤더를 자동 생성합니다.
    if not columns:
        columns = _columns_from_body({}, rows)

    # Markdown 표는 헤더, 구분선, 데이터 행 순서로 구성합니다.
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join(["---"] * len(columns)) + " |"
    lines = [header, separator]

    # Chat Output에서 너무 긴 표가 화면을 덮지 않도록 설정된 행 수만 표시합니다.
    for row in rows[:max_message_rows]:
        values = [_cell_text(row.get(column), max_cell_chars) for column in columns]
        lines.append("| " + " | ".join(values) + " |")
    if len(rows) > max_message_rows:
        # 전체 row 수는 남겨 사용자가 결과가 잘린 것을 알 수 있게 합니다.
        lines.append(f"\n_총 {len(rows)}행 중 {max_message_rows}행만 표시했습니다._")
    return "\n".join(lines)


def _request_params_text(source: Dict[str, Any], max_cell_chars: int = DEFAULT_MAX_CELL_CHARS) -> str:
    """source_results의 request_params를 사람이 읽기 좋은 한 줄로 만듭니다."""
    params = source.get("request_params")
    if not isinstance(params, dict) or not params:
        return ""
    parts = []
    for key, value in params.items():
        parts.append(f"{key}={_cell_text(value, max_cell_chars)}")
    return ", ".join(parts)


def build_simple_data_message(data_result_value: Any, max_message_rows_value: Any = DEFAULT_MAX_MESSAGE_ROWS, max_cell_chars_value: Any = DEFAULT_MAX_CELL_CHARS) -> str:
    """Chat Output에 연결해 사람이 확인할 수 있는 요약 메시지를 만듭니다."""
    max_message_rows = _positive_int(max_message_rows_value, DEFAULT_MAX_MESSAGE_ROWS)
    max_cell_chars = _positive_int(max_cell_chars_value, DEFAULT_MAX_CELL_CHARS)
    # 1) merger 결과에서 실제 body와 rows/columns를 꺼냅니다.
    #    data_result 필드를 우선 사용하고, 과거 rows 필드는 보조로만 읽습니다.
    body = _body_from_value(data_result_value)
    rows = _message_rows(body)
    columns = _columns_from_body(body, rows)
    row_count = len(rows)
    mode = str(body.get("mode") or "-")
    success = bool(body.get("success", False))
    status = "성공" if success else "실패"

    # 2) 사람이 먼저 봐야 하는 실행 상태를 간단한 bullet로 만듭니다.
    lines = [
        "### 데이터 조회 결과",
        f"- 상태: {status}",
        f"- 모드: {mode}",
        f"- 행 수: {row_count}",
    ]

    # 3) 여러 source가 함께 실행된 경우 source_results를 기준으로 어떤 source가 몇 행을 냈는지 요약합니다.
    source_results = body.get("source_results") if isinstance(body.get("source_results"), list) else []
    if source_results:
        source_names = []
        for source in source_results:
            if not isinstance(source, dict):
                continue
            name = str(source.get("name") or source.get("source_type") or "").strip()
            source_type = str(source.get("source_type") or "").strip()
            source_rows = _as_rows(source.get("data_result"))
            row_text = str(source.get("row_count", len(source_rows))).strip()
            label = name
            if source_type:
                label = f"{label}({source_type})" if label else source_type
            if row_text:
                label = f"{label}: {row_text}행"
            if label:
                source_names.append(label)
        if source_names:
            lines.append("- 소스: " + ", ".join(source_names))

    # 4) 실패 source가 있으면 source_results 안의 error_message를 표 위에 먼저 보여줍니다.
    errors = []
    for source in source_results:
        if not isinstance(source, dict):
            continue
        if bool(source.get("success")):
            continue
        error_message = source.get("error_message")
        if error_message:
            errors.append(
                {
                    "name": source.get("name") or source.get("source_type") or "source",
                    "error_message": error_message,
                }
            )
    if errors:
        lines.append("")
        lines.append("#### 오류")
        for error in errors:
            if isinstance(error, dict):
                lines.append(f"- {error.get('name', 'source')}: {error.get('error_message', error)}")
            else:
                lines.append(f"- {error}")

    # 5) 같은 source_type/source_name이 여러 번 실행된 경우에는 결과를 요청별로 나눠 보여줍니다.
    #    API용 data_json.data_result는 조회 순서별 실제 데이터 목록이고, source_results에는 상세 정보를 남깁니다.
    if len(source_results) > 1:
        lines.append("")
        lines.append("#### 요청별 결과")
        for index, source in enumerate(source_results, start=1):
            if not isinstance(source, dict):
                continue
            name = str(source.get("name") or source.get("source_type") or f"source_{index}")
            source_type = str(source.get("source_type") or "")
            title = f"{index}. {name}"
            if source_type:
                title += f" ({source_type})"
            lines.append("")
            lines.append(f"##### {title}")
            params_text = _request_params_text(source, max_cell_chars)
            if params_text:
                lines.append(f"- params: {params_text}")
                # bullet 바로 다음 줄에 표가 오면 일부 Chat Output 렌더러가 표를 일반 텍스트로 처리합니다.
                # 빈 줄을 하나 넣어 표를 독립 Markdown 블록으로 분리합니다.
                lines.append("")
            source_rows = _as_rows(source.get("data_result"))
            source_columns = _columns_from_body(source, source_rows)
            lines.append(_markdown_table(source_rows, source_columns, max_message_rows, max_cell_chars))
        return "\n".join(lines)

    # 6) 단일 source이거나 source_results가 없는 예전 payload는 기존처럼 한 표로 보여줍니다.
    lines.append("")
    lines.append(_markdown_table(rows, columns, max_message_rows, max_cell_chars))
    return "\n".join(lines)


def build_simple_data_response(data_result_value: Any, max_message_rows_value: Any = DEFAULT_MAX_MESSAGE_ROWS, max_cell_chars_value: Any = DEFAULT_MAX_CELL_CHARS) -> Dict[str, Any]:
    """Run Flow/API 연결용 JSON과 테스트용 메시지를 함께 준비합니다."""
    # 1) API/Run Flow에서 받을 JSON은 envelope 없이 실제 결과 본문만 남깁니다.
    body = _body_from_value(data_result_value)
    data_json = _clean_data_json_body(body)

    # 2) char_count는 Langflow 저장/전달 크기 점검용입니다.
    #    test_message는 실제 API 응답이 아니라 Chat Output으로 눈으로 확인하기 위한 표 메시지입니다.
    data_json_text = json.dumps(data_json, ensure_ascii=False, separators=(",", ":"), default=str)
    test_message = build_simple_data_message({"data_result": body}, max_message_rows_value, max_cell_chars_value)
    return {
        "data_json": data_json,
        "test_message": test_message,
        "char_count": len(data_json_text),
    }


class DataOutputBuilder(Component):
    """최종 조회 결과를 API용 JSON과 사람이 확인할 Message 두 갈래로 내보내는 노드입니다."""
    display_name = "Data Output Builder"
    description = "조회 결과를 API용 Data JSON과 Chat Output 확인용 표 메시지로 반환합니다."
    icon = "Reply"
    name = "DataOutputBuilder"

    inputs = [
        DataInput(name="data_result", display_name="Data Result", input_types=["Data", "JSON"]),
        MessageTextInput(name="max_message_rows", display_name="Max Message Rows", value=str(DEFAULT_MAX_MESSAGE_ROWS), advanced=True),
        MessageTextInput(name="max_cell_chars", display_name="Max Cell Chars", value=str(DEFAULT_MAX_CELL_CHARS), advanced=True),
    ]
    outputs = [
        Output(name="data_json", display_name="Data JSON", method="build_data_json", group_outputs=True, types=["Data"]),
        Output(name="test_message", display_name="Test Message", method="build_test_message", group_outputs=True, types=["Message"]),
    ]

    def _payload(self) -> Dict[str, Any]:
        """두 output이 같은 결과를 공유하도록 응답 생성 결과를 캐시합니다."""
        # Langflow가 data_json/test_message output을 각각 호출해도 결과 생성은 한 번만 수행합니다.
        cached = getattr(self, "_cached_payload", None)
        if isinstance(cached, dict):
            return cached

        # merger 결과를 API용 JSON과 테스트용 Message로 동시에 변환합니다.
        payload = build_simple_data_response(
            getattr(self, "data_result", None),
            getattr(self, "max_message_rows", DEFAULT_MAX_MESSAGE_ROWS),
            getattr(self, "max_cell_chars", DEFAULT_MAX_CELL_CHARS),
        )
        self._cached_payload = payload

        # status에는 외부 응답 성공 여부와 JSON 크기만 표시합니다.
        self.status = {
            "success": bool(payload.get("data_json", {}).get("success")),
            "char_count": payload.get("char_count", 0),
        }
        return payload

    def build_data_json(self):
        """Run Flow나 다른 노드에 연결할 구조화 JSON 응답을 Data로 내보냅니다."""
        return _make_data(self._payload().get("data_json", {}))

    def build_test_message(self):
        """Chat Output으로 연결해 확인할 Markdown 표 메시지를 내보냅니다."""
        return _make_message(str(self._payload().get("test_message", "")))
