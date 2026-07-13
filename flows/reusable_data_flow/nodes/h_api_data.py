from __future__ import annotations

import json
import re
from copy import deepcopy
from importlib import import_module
from typing import Any, Dict

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MessageTextInput, Output
from lfx.schema.data import Data


def _make_data(payload: Dict[str, Any]) -> Any:
    """Langflow 버전에 따라 Data 생성자 모양이 달라지는 부분을 흡수합니다."""
    try:
        return Data(data=payload)
    except TypeError:
        return Data(payload)


def _payload_from_value(value: Any) -> Dict[str, Any]:
    """Data/Message/Text/JSON 입력을 내부 처리용 dict로 맞춥니다."""
    # 입력이 비어 있으면 뒤쪽 로직이 안전하게 skipped 처리하도록 빈 dict를 반환합니다.
    if value is None:
        return {}

    # dict는 이미 payload이므로 원본을 변경하지 않게 복사합니다.
    if isinstance(value, dict):
        return deepcopy(value)

    # Langflow Data 객체는 .data에 실제 payload가 들어오는 경우가 많습니다.
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
            # JSON이 아니면 원문을 보존해 디버깅할 수 있게 합니다.
            return {"text": text}
    return {}


def _request_body(value: Any) -> Dict[str, Any]:
    """data_request/body wrapper를 벗겨 실제 요청 dict만 반환합니다."""
    payload = _payload_from_value(value)
    # Data Request Normalizer 출력은 data_request 안에 실제 요청을 담습니다.
    if isinstance(payload.get("data_request"), dict):
        return deepcopy(payload["data_request"])

    # flow_text_v1 envelope나 adapter 출력은 body 안에 요청을 담을 수 있습니다.
    if isinstance(payload.get("body"), dict):
        return deepcopy(payload["body"])

    # 별도 wrapper가 없으면 payload 자체를 요청으로 간주합니다.
    return payload


def _as_list(value: Any) -> list[Any]:
    """None/단일 값/tuple/set을 같은 반복 로직에서 처리할 수 있게 list로 맞춥니다."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    return [value]


def _normalize_key(value: Any) -> str:
    """대소문자, 공백, underscore 차이를 무시하고 key를 비교하기 위한 문자열로 바꿉니다."""
    return re.sub(r"[\s_-]+", "", str(value or "").strip().lower())


def _dict_get_ci(mapping: Dict[str, Any], key: Any, default: Any = None) -> Any:
    """params/credentials에서 key 표기 차이를 흡수해 값을 찾습니다."""
    if not isinstance(mapping, dict):
        return default
    text = str(key or "").strip()
    if text in mapping:
        return mapping[text]
    normalized = _normalize_key(text)
    for item_key, value in mapping.items():
        # LOT_ID, lot id, lot-id 같은 표기를 같은 key로 봅니다.
        if _normalize_key(item_key) == normalized:
            return value
    return default


def _source_type(value: Any) -> str:
    """H-API source_type 별칭을 표준값 h_api로 맞춥니다."""
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return {"hapi": "h_api"}.get(text, text)


def _request_items(request_body: Dict[str, Any]) -> list[Dict[str, Any]]:
    """단일 request와 multi requests를 같은 list[dict] 형태로 맞춥니다."""
    if isinstance(request_body.get("requests"), list):
        return [deepcopy(item) for item in request_body["requests"] if isinstance(item, dict)]
    if isinstance(request_body.get("request"), dict):
        return [deepcopy(request_body["request"])]
    return [deepcopy(request_body)] if request_body else []


def _source_config(request: Dict[str, Any]) -> Dict[str, Any]:
    """H-API 호출에 필요한 URL, timeout, response_path 설정만 모읍니다."""
    # source_catalog에서 온 source_config를 기본으로 사용합니다.
    config = deepcopy(request.get("source_config")) if isinstance(request.get("source_config"), dict) else {}

    # 직접 JSON에서 api_url 같은 실행 key를 상위에 둔 경우도 source_config로 흡수합니다.
    for key in ("api_url", "url", "timeout", "response_path"):
        if request.get(key) not in (None, "", [], {}):
            config.setdefault(key, deepcopy(request[key]))

    # url 별칭은 최종적으로 api_url로 통일합니다.
    if config.get("url") and not config.get("api_url"):
        config["api_url"] = config["url"]
    return config


def _params(request: Dict[str, Any]) -> Dict[str, Any]:
    """params와 variables를 합쳐 bindParams 생성에 사용할 변수 묶음을 만듭니다."""
    # params가 표준 입력이고 variables는 LLM이 다른 이름으로 만든 경우를 위한 보조 입력입니다.
    params = deepcopy(request.get("params")) if isinstance(request.get("params"), dict) else {}
    if isinstance(request.get("variables"), dict):
        for key, value in request["variables"].items():
            # params에 이미 같은 key가 있으면 더 명시적인 params 값을 우선합니다.
            params.setdefault(key, deepcopy(value))
    return params


def _param_value(params: Dict[str, Any], key: Any) -> Any:
    """bindParams에 넣을 값을 key 표기 차이를 무시하고 찾습니다."""
    return _dict_get_ci(params, key)


def _param_order(request: Dict[str, Any]) -> list[str]:
    """H-API bindParams 배열에 넣을 파라미터 순서를 결정합니다."""
    order: list[str] = []

    # bindParams는 순서가 중요하므로 catalog/request의 param_order를 최우선으로 사용합니다.
    for item in _as_list(request.get("param_order")):
        text = str(item or "").strip()
        if text:
            order.append(text)
    if order:
        return order

    # param_order가 없으면 required_params 순서를 대신 사용합니다.
    for item in _as_list(request.get("required_params")):
        text = str(item or "").strip()
        if text:
            order.append(text)
    return order


def _missing_required_params(params: Dict[str, Any], required_params: list[Any]) -> list[str]:
    """required_params 중 실제 값이 비어 있는 항목을 찾습니다."""
    missing = []
    for item in required_params:
        key = str(item or "").strip()
        if key and _param_value(params, key) in (None, "", []):
            missing.append(key)
    return missing


def _extract_path(payload: Any, path: str) -> Any:
    """점(.)으로 구분한 response_path를 따라 API 응답 내부 값을 꺼냅니다."""
    current = payload
    for token in [part.strip() for part in str(path or "").split(".") if part.strip()]:
        if isinstance(current, dict):
            # dict에서는 token 이름의 key를 따라갑니다.
            current = current.get(token)
        elif isinstance(current, list) and token.isdigit():
            # list에서는 숫자 token만 index로 허용합니다.
            index = int(token)
            current = current[index] if 0 <= index < len(current) else None
        else:
            # 중간 값이 dict/list가 아니면 더 이상 경로를 따라갈 수 없습니다.
            return None
    return current


def _rows_from_api_payload(payload: Any, response_path: str = "") -> list[Dict[str, Any]]:
    """API 응답 JSON에서 response_path 또는 흔한 data/rows/items 필드를 row 목록으로 변환합니다."""
    # response_path가 지정되어 있으면 먼저 그 경로를 따라 실제 데이터 배열을 찾아봅니다.
    # 예: response_path="data.row"이면 payload["data"]["row"]를 우선 사용합니다.
    original_payload = payload
    if response_path:
        payload = _extract_path(payload, response_path)
        if payload in (None, [], {}):
            # 경로가 잘못되었거나 비어 있으면 전체 응답에서 흔한 배열 필드를 다시 찾습니다.
            # 테스트 중 response_path를 잘못 넣어도 응답 전체를 잃지 않기 위한 방어 코드입니다.
            payload = original_payload
    if isinstance(payload, list):
        # API가 곧바로 list를 반환하는 경우는 이 list 자체를 row 후보로 봅니다.
        rows = payload
    elif isinstance(payload, dict):
        # 대부분의 API는 data/rows/items/result/results 중 하나에 실제 row 배열을 담습니다.
        # 사용자가 response_path를 생략해도 이 흔한 이름들을 자동으로 확인합니다.
        rows = payload.get("data") or payload.get("rows") or payload.get("items") or payload.get("result") or payload.get("results") or []
        if isinstance(rows, dict):
            # row 한 건만 object로 온 경우도 표 형태로 다루기 위해 list로 감쌉니다.
            rows = [rows]
        elif not rows:
            # 위 표준 key에 없더라도 dict 내부 값 중 list[dict] 모양이 있으면 그것을 데이터로 사용합니다.
            for value in payload.values():
                if isinstance(value, list) and any(isinstance(item, dict) for item in value):
                    rows = value
                    break
        if not rows:
            # 어떤 데이터 배열도 못 찾았지만 응답 자체가 dict이면, 응답 전체를 한 row로 보여줍니다.
            # 이렇게 해야 "응답은 있는데 빈 list"로 사라지는 문제를 줄일 수 있습니다.
            rows = [payload]
    else:
        rows = []
    result: list[Dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            # 표준 결과는 list[dict]입니다.
            result.append(dict(row))
        elif row not in (None, ""):
            # scalar 값도 버리지 않고 value 컬럼 하나짜리 row로 바꿉니다.
            result.append({"value": row})
    return result


def _rows_columns(rows: list[Dict[str, Any]]) -> list[str]:
    """row 목록에서 처음 등장한 순서대로 컬럼명을 수집합니다."""
    columns: list[str] = []
    for row in rows:
        for key in row:
            text = str(key)
            if text not in columns:
                columns.append(text)
    return columns


def _json_ready(value: Any) -> Any:
    """API 응답 값을 JSON 직렬화 가능한 기본 타입으로 정리합니다."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        # dict key는 문자열로 맞추고 value는 재귀적으로 정리합니다.
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_ready(item) for item in value]
    # 그 외 객체는 화면 확인이 가능하도록 문자열로 둡니다.
    return str(value)


def _result(request: Dict[str, Any], rows: list[Dict[str, Any]], extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """각 data node가 공통으로 내보내는 최소 결과 형태를 만듭니다."""
    # merger가 모든 source 결과를 같은 방식으로 읽을 수 있도록 공통 필드를 유지합니다.
    request_params = _params(request)
    payload = {
        "success": True,
        "name": str(request.get("name") or request.get("dataset_key") or request.get("tool_name") or "h_api"),
        "source_type": "h_api",
        "request_params": _json_ready(request_params),
        "request_label": request.get("request_label") or request.get("label") or "",
        "data_result": rows,
        "columns": _rows_columns(rows),
        "row_count": len(rows),
        "error_message": "",
    }
    if extra:
        # request_body 같은 H-API 디버깅 값을 필요한 경우에만 추가합니다.
        payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# 테스트 전용 더미 데이터
# 실제 실행으로 바꿀 때는 이 블록을 주석 처리하거나 삭제하고, _run_h_api의 실제 실행 블록을 사용합니다.
# ---------------------------------------------------------------------------


def _dummy_param_fields(params: Dict[str, Any]) -> Dict[str, Any]:
    """더미 row에서 입력 파라미터가 어떻게 들어왔는지 확인할 수 있게 컬럼 dict로 만듭니다."""
    fields: Dict[str, Any] = {}
    for key, value in params.items():
        fields[str(key)] = _json_ready(value)
    return fields


def _dummy_rows(request: Dict[str, Any], params: Dict[str, Any], api_url: str, body: Dict[str, Any]) -> list[Dict[str, Any]]:
    """테스트 화면에서 URL, request body, 변수 매핑을 확인할 수 있는 샘플 row를 만듭니다."""
    row = {
        "source_type": "h_api",
        "source_name": str(request.get("name") or request.get("dataset_key") or request.get("tool_name") or "h_api"),
        "dummy_data": True,
        "api_url": api_url,
        "request_body": _json_ready(body),
    }
    row.update(_dummy_param_fields(params))
    return [row]


def _error_result(request: Dict[str, Any], message: str, failure_type: str) -> Dict[str, Any]:
    """data node 실패를 merger가 읽을 수 있는 표준 실패 payload로 만듭니다."""
    return {
        "success": False,
        "name": str(request.get("name") or request.get("dataset_key") or request.get("tool_name") or "h_api"),
        "source_type": "h_api",
        "request_params": _json_ready(_params(request)),
        "request_label": request.get("request_label") or request.get("label") or "",
        "data_result": [],
        "columns": [],
        "row_count": 0,
        "error_message": message,
        "failure_type": failure_type,
    }


def _run_h_api(request: Dict[str, Any], h_api_token: str, fetch_limit: int) -> Dict[str, Any]:
    """단일 H-API 요청을 검증하고, 더미 또는 실제 API 응답을 표준 결과로 바꿉니다."""
    # 1) request 안의 params/variables를 합쳐 실제 API 호출에 쓸 변수 dict를 만듭니다.
    #    required_params가 비어 있으면 검사하지 않고, 있으면 값 누락 여부를 먼저 확인합니다.
    params = _params(request)
    missing = _missing_required_params(params, _as_list(request.get("required_params")))
    if missing:
        return _error_result(request, f"Missing required parameter(s): {', '.join(missing)}", "missing_required_params")

    # 2) source_config에서 api_url과 response_path 같은 실행 설정을 읽습니다.
    #    api_url은 어떤 endpoint를 호출할지 결정하는 핵심 값입니다.
    config = _source_config(request)
    api_url = str(config.get("api_url") or "").strip()
    if not api_url:
        return _error_result(request, "H-API source_config must include api_url.", "missing_api_url")

    # 3) H-API 예시는 bindParams 배열을 사용합니다.
    #    param_order에 적힌 순서대로 params 값을 꺼내 request body를 구성합니다.
    bind_params = []
    for key in _param_order(request):
        bind_params.append(_param_value(params, key))
    body = {"bindParams": bind_params}

    # 4) 더미 함수가 있으면 실제 API 대신 더미 row를 반환합니다.
    dummy_builder = globals().get("_dummy_rows")
    if callable(dummy_builder):
        rows = dummy_builder(request, params, api_url, body)[:fetch_limit]
        return _result(request, rows, {"request_body": body})

    # 5) 실제 H-API 실행부입니다. 더미 블록을 주석 처리한 뒤 아래 블록을 사용합니다.
    token = str(h_api_token or _dict_get_ci(request.get("credentials", {}), "h_api_token") or "").strip()
    if not token:
        return _error_result(request, "H-API token is empty.", "missing_h_api_token")
    headers = {"h-api-token": token, "Content-Type": "application/json"}
    # try:
    #     requests = import_module("requests")
    #     response = requests.post(api_url, headers=headers, json=body, timeout=float(config.get("timeout", 30)))
    #     if hasattr(response, "raise_for_status"):
    #         response.raise_for_status()
    #     rows = _rows_from_api_payload(response.json(), str(config.get("response_path") or ""))[:fetch_limit]
    #     return _result(request, rows, {"request_body": body})
    # except Exception as exc:
    #     return _error_result(request, str(exc), "retrieval_failed")
    return _error_result(request, "Real H-API execution is disabled while dummy rows are not configured.", "real_execution_disabled")


def retrieve_simple_h_api_data(data_request_value: Any, h_api_token: str = "", fetch_limit_value: Any = "5000") -> Dict[str, Any]:
    """전체 data_request 중 source_type이 h_api인 요청만 골라 실행합니다."""
    # 1) 앞 노드가 넘긴 data_request wrapper를 벗기고 fetch_limit을 숫자로 정리합니다.
    request_body = _request_body(data_request_value)
    try:
        fetch_limit = max(1, int(fetch_limit_value or 5000))
    except Exception:
        fetch_limit = 5000

    # 2) multi request 구조에서는 oracle/datalake/goodocs 요청도 함께 들어올 수 있습니다.
    #    이 data node는 h_api 요청만 처리하고 나머지는 그대로 건너뜁니다.
    source_requests = []
    for item in _request_items(request_body):
        source_type = _source_type(item.get("source_type") or item.get("source") or _source_config(item).get("source_type"))
        if source_type == "h_api":
            source_requests.append(item)
    if not source_requests:
        return {"skipped": True, "source_type": "h_api", "skip_reason": "No h_api request.", "items": []}

    # 3) 같은 flow 안에서 여러 H-API source를 요청할 수 있으므로 item별 결과를 list로 유지합니다.
    items = []
    for item in source_requests:
        items.append(_run_h_api(item, h_api_token, fetch_limit))
    return {"source_type": "h_api", "items": items}


class HApiData(Component):
    """전체 data_request 중 H-API 요청만 처리하는 전용 data 노드입니다."""
    display_name = "H-API Data"
    description = "data_request 중 h_api 요청만 골라 실행합니다."
    icon = "Network"
    name = "HApiData"

    inputs = [
        DataInput(name="data_request", display_name="Data Request", input_types=["Data", "JSON"]),
        MessageTextInput(name="h_api_token", display_name="H-API Token", value=""),
        MessageTextInput(name="fetch_limit", display_name="Fetch Limit", value="5000", advanced=True),
    ]
    outputs = [Output(name="source_result", display_name="Data Result", method="build_source_result", types=["Data"])]

    def build_source_result(self):
        """H-API 요청 실행 결과를 Data Result 출력으로 내보냅니다."""
        payload = retrieve_simple_h_api_data(getattr(self, "data_request", None), getattr(self, "h_api_token", ""), getattr(self, "fetch_limit", "5000"))
        self.status = {"source_type": "h_api", "result_count": len(payload.get("items", [])), "skipped": bool(payload.get("skipped"))}
        return _make_data(payload)
