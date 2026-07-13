from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MessageTextInput, Output
from lfx.schema.data import Data


GOODOCS_SYSTEM_COLUMNS = {"ROW_INDEX", "LastUser", "LastTime", "LastEditType", "FirstUser", "FirstTime", "ROW_ID"}
GOODOCS_SYSTEM_COLUMN_KEYS = {re.sub(r"[^a-z0-9]", "", column.lower()) for column in GOODOCS_SYSTEM_COLUMNS}


class Goodocs:
    """실제 환경에서 Goodocs class 코드를 이 파일 안에 붙여 넣어 사용합니다."""

    def __init__(self, auth: Dict[str, Any]):
        # 실제 Goodocs 구현체가 붙기 전까지 인증값 형태만 보존합니다.
        self.auth = auth

    def read_all(self) -> Any:
        # 이 기본 class는 자리 표시자입니다.
        # 실제 환경에서는 Goodocs class 구현을 이 파일 안에 붙여 넣습니다.
        raise RuntimeError("Goodocs class implementation is not configured. Paste the real class.")


def _make_data(payload: Dict[str, Any]) -> Any:
    """Langflow 버전에 따라 Data 생성자 모양이 달라지는 부분을 흡수합니다."""
    try:
        return Data(data=payload)
    except TypeError:
        return Data(payload)


def _payload_from_value(value: Any) -> Dict[str, Any]:
    """Data/Message/Text/JSON 입력을 내부 처리용 dict로 맞춥니다."""
    # 입력이 비어 있으면 뒤쪽 필터링 단계에서 skipped 처리되도록 빈 dict를 반환합니다.
    if value is None:
        return {}

    # 이미 dict로 들어온 payload는 원본 변경을 피하기 위해 복사합니다.
    if isinstance(value, dict):
        return deepcopy(value)

    # Langflow Data 객체는 .data에 실제 payload가 들어옵니다.
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return deepcopy(data)

    # Message/Text 객체는 text/content를 JSON으로 파싱합니다.
    text = getattr(value, "text", None) or getattr(value, "content", None)
    if isinstance(text, str):
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {"text": text}
        except Exception:
            # JSON이 아니면 원문을 보존해 진단할 수 있게 합니다.
            return {"text": text}
    return {}


def _request_body(value: Any) -> Dict[str, Any]:
    """data_request/body wrapper를 벗겨 실제 요청 dict만 반환합니다."""
    payload = _payload_from_value(value)
    # normalizer의 표준 출력은 data_request 안에 실제 요청을 담습니다.
    if isinstance(payload.get("data_request"), dict):
        return deepcopy(payload["data_request"])

    # flow_text_v1 envelope나 adapter 출력은 body 안에 요청을 담을 수 있습니다.
    if isinstance(payload.get("body"), dict):
        return deepcopy(payload["body"])
    return payload


def _source_type(value: Any) -> str:
    """Goodocs source_type 별칭을 표준값 goodocs로 맞춥니다."""
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return {"goodoc": "goodocs"}.get(text, text)


def _request_items(request_body: Dict[str, Any]) -> list[Dict[str, Any]]:
    """단일 request와 multi requests를 같은 list[dict] 형태로 맞춥니다."""
    if isinstance(request_body.get("requests"), list):
        return [deepcopy(item) for item in request_body["requests"] if isinstance(item, dict)]
    if isinstance(request_body.get("request"), dict):
        return [deepcopy(request_body["request"])]
    return [deepcopy(request_body)] if request_body else []


def _source_config(request: Dict[str, Any]) -> Dict[str, Any]:
    """Goodocs 실행에 필요한 문서번호와 시트 설정만 모읍니다."""
    # source_catalog에서 온 source_config를 기본으로 사용합니다.
    config = deepcopy(request.get("source_config")) if isinstance(request.get("source_config"), dict) else {}

    # 직접 JSON에서 doc_id 같은 실행 key를 상위에 둔 경우도 source_config로 흡수합니다.
    for key in ("doc_id", "document_id", "sheet_name"):
        if request.get(key) not in (None, "", [], {}):
            config.setdefault(key, deepcopy(request[key]))

    # document_id 별칭은 최종적으로 doc_id로 통일합니다.
    if config.get("document_id") and not config.get("doc_id"):
        config["doc_id"] = config["document_id"]
    return config


def _params(request: Dict[str, Any]) -> Dict[str, Any]:
    """Goodocs 요청에 함께 들어온 params/variables를 request_params로 보존하기 위해 모읍니다."""
    # Goodocs 자체는 doc_id 중심으로 조회하지만, 사용자가 넘긴 변수도 결과 metadata에서 확인할 수 있게 합니다.
    params = deepcopy(request.get("params")) if isinstance(request.get("params"), dict) else {}
    if isinstance(request.get("variables"), dict):
        for key, value in request["variables"].items():
            # params가 이미 있으면 더 명시적인 params 값을 유지합니다.
            params.setdefault(key, deepcopy(value))
    return params


def _json_ready(value: Any) -> Any:
    """Goodocs 결과에 섞인 날짜/Decimal/NaN 등을 JSON 안전값으로 바꿉니다."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        # dict key는 문자열로 맞추고 value는 재귀적으로 정리합니다.
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_ready(item) for item in value]
    try:
        # pandas/numpy NaN은 자기 자신과 같지 않으므로 None으로 바꿉니다.
        if value != value:
            return None
    except Exception:
        pass
    return str(value)


def _is_goodocs_system_column(column_name: Any) -> bool:
    """Goodocs 제외 컬럼을 대소문자/underscore 차이를 줄여 판단합니다."""
    normalized = re.sub(r"[^a-z0-9]", "", str(column_name or "").strip().lower())
    return normalized in GOODOCS_SYSTEM_COLUMN_KEYS


def _is_empty_goodocs_value(value: Any) -> bool:
    """Goodocs row 값이 null 또는 빈 문자열로 볼 수 있는지 확인합니다."""
    if value is None:
        return True
    try:
        # pandas/numpy NaN은 자기 자신과 같지 않습니다.
        if value != value:
            return True
    except Exception:
        pass
    text = str(value).strip()
    return text == "" or text.lower() in {"nan", "none", "null"}


def _drop_system_columns_from_rows(rows: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """Goodocs 관리용 컬럼을 제거하고, 모든 값이 빈 row도 제외합니다."""
    cleaned_rows: list[Dict[str, Any]] = []
    for row in rows:
        # Goodocs가 자동으로 붙이는 편집자/편집시간/행 ID 컬럼은 업무 데이터가 아니므로 제거합니다.
        cleaned: Dict[str, Any] = {}
        for key, value in row.items():
            if not _is_goodocs_system_column(key):
                cleaned[key] = value
        # 시스템 컬럼을 제거한 뒤 아무 값도 남지 않는 행은 결과에서 제외합니다.
        if _row_has_value(cleaned):
            cleaned_rows.append(cleaned)
    return cleaned_rows


def _row_has_value(row: Dict[str, Any]) -> bool:
    """None, 빈 문자열, NaN만 있는 행인지 확인합니다."""
    for value in row.values():
        if not _is_empty_goodocs_value(value):
            return True
    return False


def _frame_to_rows(frame: Any) -> list[Dict[str, Any]]:
    """Goodocs/Pandas/list 결과를 JSON으로 직렬화 가능한 row 목록으로 바꿉니다."""
    # Goodocs read_all() 결과는 pandas DataFrame인 경우가 많으므로 index를 먼저 정리합니다.
    # reset_index가 실패해도 원본 데이터로 계속 변환할 수 있게 예외는 흡수합니다.
    if hasattr(frame, "reset_index"):
        try:
            frame = frame.reset_index(drop=True)
        except Exception:
            pass

    # DataFrame 단계에서 제거 가능한 시스템 컬럼은 먼저 drop합니다.
    # list[dict] 입력도 아래 _drop_system_columns_from_rows에서 다시 한 번 정제됩니다.
    if hasattr(frame, "drop"):
        try:
            drop_columns = []
            for column in getattr(frame, "columns", []):
                if _is_goodocs_system_column(column):
                    drop_columns.append(column)
            if drop_columns:
                frame = frame.drop(columns=drop_columns)
        except Exception:
            pass
    if hasattr(frame, "to_dict"):
        try:
            # pandas DataFrame은 records 방향이 reusable flow의 list[dict] 결과와 가장 잘 맞습니다.
            rows = frame.to_dict(orient="records")
        except TypeError:
            # DataFrame 유사 객체가 orient 키워드를 지원하지 않는 경우를 보조로 처리합니다.
            rows = frame.to_dict("records")
    elif isinstance(frame, list):
        # 테스트 코드나 외부 Goodocs 래퍼가 이미 list[dict]로 반환하는 경우도 허용합니다.
        rows = frame
    else:
        rows = []

    # datetime/Decimal/NaN 등을 JSON 안전값으로 변환한 뒤 빈 행과 시스템 컬럼을 최종 제거합니다.
    ready_rows: list[Dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            ready_rows.append(_json_ready(dict(row)))
    return _drop_system_columns_from_rows(ready_rows)


def _rows_columns(rows: list[Dict[str, Any]]) -> list[str]:
    """row 목록에서 처음 등장한 순서대로 컬럼명을 수집합니다."""
    columns: list[str] = []
    for row in rows:
        for key in row:
            text = str(key)
            if text not in columns:
                columns.append(text)
    return columns


def _result(request: Dict[str, Any], rows: list[Dict[str, Any]], extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """각 data node가 공통으로 내보내는 최소 결과 형태를 만듭니다."""
    # merger가 모든 source 결과를 같은 방식으로 읽을 수 있도록 공통 필드를 유지합니다.
    request_params = _params(request)
    payload = {
        "success": True,
        "name": str(request.get("name") or request.get("dataset_key") or request.get("tool_name") or "goodocs"),
        "source_type": "goodocs",
        "request_params": _json_ready(request_params),
        "request_label": request.get("request_label") or request.get("label") or "",
        "data_result": rows,
        "columns": _rows_columns(rows),
        "row_count": len(rows),
        "error_message": "",
    }
    if extra:
        # doc_id 같은 Goodocs 디버깅 값을 필요한 경우에만 추가합니다.
        payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# 테스트 전용 더미 데이터
# 실제 실행으로 바꿀 때는 이 블록을 주석 처리하거나 삭제하고, _run_goodocs의 실제 실행 블록을 사용합니다.
# ---------------------------------------------------------------------------


def _dummy_param_fields(params: Dict[str, Any]) -> Dict[str, Any]:
    """더미 row에서 입력 파라미터가 어떻게 들어왔는지 확인할 수 있게 컬럼 dict로 만듭니다."""
    fields: Dict[str, Any] = {}
    for key, value in params.items():
        fields[str(key)] = _json_ready(value)
    return fields


def _dummy_rows(request: Dict[str, Any], params: Dict[str, Any], doc_id: str) -> list[Dict[str, Any]]:
    """테스트 화면에서 문서번호와 변수 매핑을 확인할 수 있는 샘플 row를 만듭니다."""
    row = {
        "source_type": "goodocs",
        "source_name": str(request.get("name") or request.get("dataset_key") or request.get("tool_name") or "goodocs"),
        "dummy_data": True,
        "doc_id": doc_id,
    }
    row.update(_dummy_param_fields(params))
    return [row]


def _error_result(request: Dict[str, Any], message: str, failure_type: str) -> Dict[str, Any]:
    """data node 실패를 merger가 읽을 수 있는 표준 실패 payload로 만듭니다."""
    return {
        "success": False,
        "name": str(request.get("name") or request.get("dataset_key") or request.get("tool_name") or "goodocs"),
        "source_type": "goodocs",
        "request_params": _json_ready(_params(request)),
        "request_label": request.get("request_label") or request.get("label") or "",
        "data_result": [],
        "columns": [],
        "row_count": 0,
        "error_message": message,
        "failure_type": failure_type,
    }


def _run_goodocs(request: Dict[str, Any], user_id: str, token_source: str, token_key: str, fetch_limit: int) -> Dict[str, Any]:
    """단일 Goodocs 요청을 검증하고, 더미 또는 실제 문서 결과를 표준 결과로 바꿉니다."""
    # 1) 사용자가 넘긴 params를 request_params로 남기기 위해 먼저 모아둡니다.
    params = _params(request)

    # 2) Goodocs 조회의 핵심 실행값은 문서번호(doc_id)입니다.
    #    doc_id는 source_catalog의 source_config에서 가져옵니다.
    config = _source_config(request)
    doc_id = str(config.get("doc_id") or "").strip()
    if not doc_id:
        return _error_result(request, "Goodocs source_config must include doc_id.", "missing_doc_id")

    # 3) 더미 함수가 있으면 실제 Goodocs 대신 더미 row를 반환합니다.
    dummy_builder = globals().get("_dummy_rows")
    if callable(dummy_builder):
        rows = dummy_builder(request, params, doc_id)[:fetch_limit]
        return _result(request, rows, {"doc_id": doc_id})

    # 4) 실제 Goodocs 실행부입니다. 더미 블록을 주석 처리한 뒤 아래 블록을 사용합니다.
    missing_credentials = []
    if not str(user_id or "").strip():
        missing_credentials.append("USER_ID")
    if not str(token_source or "").strip():
        missing_credentials.append("TOKEN_SOURCE")
    if not str(token_key or "").strip():
        missing_credentials.append("TOKEN_KEY")
    if missing_credentials:
        return _error_result(request, f"Missing Goodocs credential(s): {', '.join(missing_credentials)}", "missing_goodocs_credentials")
    auth = {"USER_ID": user_id, "DOC_ID": doc_id, "TOKEN_SOURCE": token_source, "TOKEN_KEY": token_key}
    # try:
    #     gdcs = Goodocs(auth)
    #     rows = _frame_to_rows(gdcs.read_all())[:fetch_limit]
    #     return _result(request, rows, {"doc_id": doc_id})
    # except Exception as exc:
    #     return _error_result(request, str(exc), "retrieval_failed")
    return _error_result(request, "Real Goodocs execution is disabled while dummy rows are not configured.", "real_execution_disabled")


def retrieve_simple_goodocs_data(
    data_request_value: Any,
    goodocs_user_id: str = "",
    goodocs_token_source: str = "",
    goodocs_token_key: str = "",
    fetch_limit_value: Any = "5000",
) -> Dict[str, Any]:
    """전체 data_request 중 source_type이 goodocs인 요청만 골라 실행합니다."""
    # 1) 앞 노드의 wrapper를 벗기고 fetch_limit을 숫자로 정리합니다.
    request_body = _request_body(data_request_value)
    try:
        fetch_limit = max(1, int(fetch_limit_value or 5000))
    except Exception:
        fetch_limit = 5000

    # 2) 여러 source 요청 중 Goodocs 요청만 처리합니다.
    source_requests = []
    for item in _request_items(request_body):
        source_type = _source_type(item.get("source_type") or item.get("source") or _source_config(item).get("source_type"))
        if source_type == "goodocs":
            source_requests.append(item)
    if not source_requests:
        return {"skipped": True, "source_type": "goodocs", "skip_reason": "No goodocs request.", "items": []}

    # 3) 같은 flow에서 여러 Goodocs 문서를 조회할 수 있으므로 item별 결과를 list로 유지합니다.
    items = []
    for item in source_requests:
        items.append(_run_goodocs(item, goodocs_user_id, goodocs_token_source, goodocs_token_key, fetch_limit))
    return {"source_type": "goodocs", "items": items}


class GoodocsData(Component):
    """전체 data_request 중 Goodocs 요청만 처리하는 전용 data 노드입니다."""
    display_name = "Goodocs Data"
    description = "data_request 중 goodocs 요청만 골라 실행합니다."
    icon = "FileSpreadsheet"
    name = "GoodocsData"

    inputs = [
        DataInput(name="data_request", display_name="Data Request", input_types=["Data", "JSON"]),
        MessageTextInput(name="goodocs_user_id", display_name="USER_ID", value=""),
        MessageTextInput(name="goodocs_token_source", display_name="TOKEN_SOURCE", value=""),
        MessageTextInput(name="goodocs_token_key", display_name="TOKEN_KEY", value=""),
        MessageTextInput(name="fetch_limit", display_name="Fetch Limit", value="5000", advanced=True),
    ]
    outputs = [Output(name="source_result", display_name="Data Result", method="build_source_result", types=["Data"])]

    def build_source_result(self):
        """Goodocs 요청 실행 결과를 Data Result 출력으로 내보냅니다."""
        payload = retrieve_simple_goodocs_data(
            getattr(self, "data_request", None),
            getattr(self, "goodocs_user_id", ""),
            getattr(self, "goodocs_token_source", ""),
            getattr(self, "goodocs_token_key", ""),
            getattr(self, "fetch_limit", "5000"),
        )
        self.status = {"source_type": "goodocs", "result_count": len(payload.get("items", [])), "skipped": bool(payload.get("skipped"))}
        return _make_data(payload)
