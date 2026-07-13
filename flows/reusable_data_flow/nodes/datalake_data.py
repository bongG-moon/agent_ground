from __future__ import annotations

import importlib.util
import asyncio
import json
import logging
import re
import subprocess
import sys
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MessageTextInput, Output
from lfx.schema.data import Data


DATALAKE_API_BASE_URL = "http://api-server.lake.skhynix.com/api/v4/"
DATALAKE_CLUSTER_TYPE = "starrocks"
DATALAKE_CLUSTER_MAX_ATTEMPTS = 200
DATALAKE_CLUSTER_WAIT_SECONDS = 1
logger = logging.getLogger(__name__)


def _module_exists(module_name: str) -> bool:
    """모듈 설치 여부를 import 이름 기준으로 확인합니다."""
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ModuleNotFoundError, ValueError):
        return False


def ensure_package(package_name: str, import_name: str | None = None) -> None:
    """필요한 Python package가 없으면 사내 Nexus trusted-host 옵션으로 설치합니다."""
    # sys.modules는 런타임이 넣은 임시 placeholder도 포함할 수 있으므로
    # 실제 설치 여부는 importlib.util.find_spec 기준으로 확인합니다.
    module_name = import_name or package_name
    if not _module_exists(module_name):
        subprocess.check_call(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--trusted-host",
                "nexus.skhynix.com",
                package_name,
            ]
        )


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


def _as_list(value: Any) -> list[Any]:
    """None/단일 값/tuple/set을 반복 처리 가능한 list로 맞춥니다."""
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
    """params에서 key 표기 차이를 흡수해 값을 찾습니다."""
    if not isinstance(mapping, dict):
        return default
    text = str(key or "").strip()
    if text in mapping:
        return mapping[text]
    normalized = _normalize_key(text)
    for item_key, value in mapping.items():
        # YM, ym, y_m처럼 표기가 달라도 같은 변수로 취급합니다.
        if _normalize_key(item_key) == normalized:
            return value
    return default


def _source_type(value: Any) -> str:
    """Datalake source_type 별칭을 표준값 datalake로 맞춥니다."""
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return {"lake": "datalake", "lakehouse": "datalake"}.get(text, text)


def _request_items(request_body: Dict[str, Any]) -> list[Dict[str, Any]]:
    """단일 request와 multi requests를 같은 list[dict] 형태로 맞춥니다."""
    if isinstance(request_body.get("requests"), list):
        return [deepcopy(item) for item in request_body["requests"] if isinstance(item, dict)]
    if isinstance(request_body.get("request"), dict):
        return [deepcopy(request_body["request"])]
    return [deepcopy(request_body)] if request_body else []


def _source_config(request: Dict[str, Any]) -> Dict[str, Any]:
    """Datalake 실행에 필요한 query_template 계열 설정만 모읍니다."""
    # source_catalog에서 온 source_config를 기본으로 사용합니다.
    config = deepcopy(request.get("source_config")) if isinstance(request.get("source_config"), dict) else {}

    # 직접 JSON에서 query/sql을 상위에 둔 경우도 source_config로 흡수합니다.
    for key in ("query_template", "sql_template", "sql", "query"):
        if request.get(key) not in (None, "", [], {}):
            config.setdefault(key, deepcopy(request[key]))

    # 쿼리 템플릿 별칭은 최종적으로 query_template 하나로 통일합니다.
    for alias in ("sql_template", "sql", "query"):
        if config.get(alias) and not config.get("query_template"):
            config["query_template"] = config[alias]
    return config


def _params(request: Dict[str, Any]) -> Dict[str, Any]:
    """params와 variables를 합쳐 SQL 템플릿 치환에 사용할 변수 묶음을 만듭니다."""
    # params가 표준 입력이고 variables는 LLM이 다른 이름으로 만든 경우를 위한 보조 입력입니다.
    params = deepcopy(request.get("params")) if isinstance(request.get("params"), dict) else {}
    if isinstance(request.get("variables"), dict):
        for key, value in request["variables"].items():
            # params에 이미 있으면 더 명시적인 params 값을 유지합니다.
            params.setdefault(key, deepcopy(value))
    return params


def _param_value(params: Dict[str, Any], key: Any) -> Any:
    """SQL 템플릿 변수 값을 key 표기 차이를 무시하고 찾습니다."""
    return _dict_get_ci(params, key)


def _missing_required_params(params: Dict[str, Any], required_params: list[Any]) -> list[str]:
    """required_params 중 실제 값이 비어 있는 항목을 찾습니다."""
    missing = []
    for item in required_params:
        key = str(item or "").strip()
        if key and _param_value(params, key) in (None, "", []):
            missing.append(key)
    return missing


def _sql_literal(value: Any) -> str:
    """Python 값을 SQL에 직접 넣을 수 있는 literal 문자열로 변환합니다."""
    if value is None:
        return "NULL"
    if isinstance(value, (datetime, date)):
        # 날짜 객체는 YYYYMMDD 문자열로 감싸 현장 SQL에서 바로 쓰기 쉽게 합니다.
        return f"'{value.strftime('%Y%m%d')}'"
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    # 문자열은 작은따옴표를 escape해서 SQL literal로 만듭니다.
    text = str(value).replace("'", "''")
    return f"'{text}'"


def _render_template(template: str, params: Dict[str, Any]) -> tuple[str, list[str]]:
    """{YM} 같은 템플릿 변수를 SQL literal로 치환하고 누락 변수를 반환합니다."""
    missing: list[str] = []

    def replace(match: re.Match[str]) -> str:
        # {YM}에서 YM만 꺼내 params에서 값을 찾습니다.
        key = match.group(1).strip()
        value = _param_value(params, key)
        if value in (None, "", []):
            # 값이 없으면 placeholder는 유지하고 missing 목록에 기록합니다.
            missing.append(key)
            return match.group(0)
        # 값이 있으면 SQL literal로 바꿔 템플릿에 넣습니다.
        return _sql_literal(value)

    # 중괄호 placeholder 전체를 replace 함수로 치환합니다.
    return re.sub(r"\{([^{}]+)\}", replace, str(template or "")), missing


def _json_ready(value: Any) -> Any:
    """Datalake 결과에 섞인 날짜/Decimal/NaN 등을 JSON 안전값으로 바꿉니다."""
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


def _frame_to_rows(frame: Any) -> list[Dict[str, Any]]:
    """Spark/Pandas/list 결과를 JSON으로 직렬화 가능한 row 목록으로 바꿉니다."""
    # 예전 LakeHouse 경로는 Spark DataFrame, 새 SmallData 경로는 pandas DataFrame으로 올 수 있습니다.
    # 테스트나 다른 구현에서는 이미 pandas/list로 들어올 수 있어 가능한 형태를 순서대로 처리합니다.
    if hasattr(frame, "toPandas"):
        frame = frame.toPandas()
    if hasattr(frame, "to_dict"):
        try:
            # pandas DataFrame은 orient="records"가 가장 명확합니다.
            rows = frame.to_dict(orient="records")
        except TypeError:
            # 일부 DataFrame 유사 객체는 positional 인자만 받는 경우가 있어 보조로 처리합니다.
            rows = frame.to_dict("records")
    elif isinstance(frame, list):
        # 이미 list[dict]로 반환하는 테스트 구현이나 API 래퍼도 허용합니다.
        rows = frame
    else:
        rows = []
    # datetime/Decimal/NaN 등 JSON 직렬화에 걸릴 수 있는 값을 안전한 값으로 바꿉니다.
    return [_json_ready(dict(row)) for row in rows if isinstance(row, dict)]


def _load_small_data_runtime_modules():
    """Datalake SmallData 방식에 필요한 모듈을 실제 실행 시점에만 불러옵니다."""
    ensure_package("aiohttp")
    ensure_package("mysql-connector-python", "mysql.connector")
    ensure_package("pandas")
    import aiohttp
    import pandas as pd
    from mysql.connector import connect

    return aiohttp, connect, pd


def _run_async(coro: Any) -> Any:
    """Langflow 런타임에 이미 event loop가 있어도 async 함수를 실행할 수 있게 합니다."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    # Langflow/Jupyter 계열 런타임은 이미 loop가 돌 수 있어 nest_asyncio를 그때만 적용합니다.
    ensure_package("nest-asyncio", "nest_asyncio")
    import nest_asyncio

    nest_asyncio.apply()
    return loop.run_until_complete(coro)


def _endpoint_host_port(endpoint: Any) -> tuple[str, int]:
    """Datalake API의 jdbc-external 값을 host/port로 나눕니다."""
    text = str(endpoint or "").strip()
    host, separator, port_text = text.rpartition(":")
    if not separator or not host or not port_text:
        raise ValueError(f"Invalid Datalake jdbc-external endpoint: {text}")
    return host, int(port_text)


async def _get_running_cluster_endpoint(
    user_id: str,
    token: str,
    api_base_url: str = DATALAKE_API_BASE_URL,
    cluster_type: str = DATALAKE_CLUSTER_TYPE,
    max_attempts: int = DATALAKE_CLUSTER_MAX_ATTEMPTS,
    wait_seconds: int = DATALAKE_CLUSTER_WAIT_SECONDS,
) -> tuple[str, int]:
    """Datalake API에서 실행 중인 StarRocks cluster의 jdbc endpoint를 가져옵니다."""
    aiohttp, _, _ = _load_small_data_runtime_modules()
    headers = {
        "accept": "application/json;charset=UTF-8",
        "Authorization": f"Bearer {token}",
        "user_id": str(user_id or ""),
    }
    target_url = f"runtime/cluster/{cluster_type}/running"

    async with aiohttp.ClientSession(base_url=api_base_url) as session:
        last_error = ""
        for attempt in range(1, max_attempts + 1):
            try:
                async with session.get(target_url, headers=headers) as response:
                    payload = await response.json()
                if payload.get("status") == "RUNNING":
                    endpoints = payload.get("endpoints") if isinstance(payload.get("endpoints"), dict) else {}
                    return _endpoint_host_port(endpoints.get("jdbc-external"))
                last_error = f"cluster status={payload.get('status')}"
                logger.info("Datalake cluster is not RUNNING yet. attempt=%s status=%s", attempt, payload.get("status"))
            except aiohttp.ClientError as exc:
                last_error = str(exc)
                logger.warning("Datalake cluster status request failed. attempt=%s error=%s", attempt, exc)
            if attempt < max_attempts:
                await asyncio.sleep(wait_seconds)
        raise RuntimeError(f"Datalake cluster is not RUNNING after {max_attempts} attempts. last_error={last_error}")


def _run_small_data_sql(sql: str, credentials: Dict[str, str], config: Dict[str, Any], fetch_limit: int) -> list[Dict[str, Any]]:
    """running cluster endpoint를 얻은 뒤 MySQL connector로 Datalake SQL을 실행합니다."""
    _, connect, pd = _load_small_data_runtime_modules()
    # Datalake API base URL은 노드 입력으로 받지 않고 코드 상수로 관리합니다.
    api_base_url = str(DATALAKE_API_BASE_URL).strip()
    cluster_type = str(config.get("cluster_type") or DATALAKE_CLUSTER_TYPE).strip() or DATALAKE_CLUSTER_TYPE
    max_attempts = int(config.get("cluster_max_attempts") or DATALAKE_CLUSTER_MAX_ATTEMPTS)
    wait_seconds = int(config.get("cluster_wait_seconds") or DATALAKE_CLUSTER_WAIT_SECONDS)
    host, port = _run_async(
        _get_running_cluster_endpoint(
            credentials["user_id"],
            credentials["token"],
            api_base_url,
            cluster_type,
            max_attempts,
            wait_seconds,
        )
    )

    connection = None
    try:
        connection = connect(
            host=host,
            port=int(port),
            user=credentials["user_id"],
            password=credentials["token"],
            use_pure=True,
            ssl_disabled=False,
            auth_plugin="mysql_clear_password",
            allow_local_infile=True,
        )
        frame = pd.read_sql(sql, con=connection)
        rows = _frame_to_rows(frame)
        return rows[:fetch_limit] if fetch_limit else rows
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


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
        "name": str(request.get("name") or request.get("dataset_key") or request.get("tool_name") or "datalake"),
        "source_type": "datalake",
        "request_params": _json_ready(request_params),
        "request_label": request.get("request_label") or request.get("label") or "",
        "data_result": rows,
        "columns": _rows_columns(rows),
        "row_count": len(rows),
        "error_message": "",
    }
    if extra:
        # executed_query 같은 Datalake 디버깅 값을 필요한 경우에만 추가합니다.
        payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# 테스트 전용 더미 데이터
# 실제 실행으로 바꿀 때는 이 블록을 주석 처리하거나 삭제하고, _run_datalake의 실제 실행 블록을 사용합니다.
# ---------------------------------------------------------------------------


def _dummy_param_fields(params: Dict[str, Any]) -> Dict[str, Any]:
    """더미 row에서 입력 파라미터가 어떻게 들어왔는지 확인할 수 있게 컬럼 dict로 만듭니다."""
    fields: Dict[str, Any] = {}
    for key, value in params.items():
        fields[str(key)] = _json_ready(value)
    return fields


def _dummy_rows(request: Dict[str, Any], params: Dict[str, Any], sql: str) -> list[Dict[str, Any]]:
    """테스트 화면에서 렌더링된 SQL과 변수 매핑을 확인할 수 있는 샘플 row를 만듭니다."""
    row = {
        "source_type": "datalake",
        "source_name": str(request.get("name") or request.get("dataset_key") or request.get("tool_name") or "datalake"),
        "dummy_data": True,
        "executed_query": sql,
    }
    row.update(_dummy_param_fields(params))
    return [row]


def _error_result(request: Dict[str, Any], message: str, failure_type: str) -> Dict[str, Any]:
    """data node 실패를 merger가 읽을 수 있는 표준 실패 payload로 만듭니다."""
    return {
        "success": False,
        "name": str(request.get("name") or request.get("dataset_key") or request.get("tool_name") or "datalake"),
        "source_type": "datalake",
        "request_params": _json_ready(_params(request)),
        "request_label": request.get("request_label") or request.get("label") or "",
        "data_result": [],
        "columns": [],
        "row_count": 0,
        "error_message": message,
        "failure_type": failure_type,
    }


def _run_datalake(request: Dict[str, Any], credentials: Dict[str, str], fetch_limit: int) -> Dict[str, Any]:
    """단일 Datalake 요청을 검증하고, 더미 또는 실제 SmallData 결과를 표준 결과로 바꿉니다."""
    # 1) SQL 템플릿에 들어갈 params를 모으고 required_params 누락을 먼저 확인합니다.
    #    필수값이 없으면 Datalake를 호출하기 전에 명확한 오류를 돌려줍니다.
    params = _params(request)
    missing = _missing_required_params(params, _as_list(request.get("required_params")))
    if missing:
        return _error_result(request, f"Missing required parameter(s): {', '.join(missing)}", "missing_required_params")

    # 2) source_config에서 query_template을 가져오고 {YM} 같은 placeholder를 params 값으로 치환합니다.
    #    치환 후 SQL은 source_results에 기록해 실행 내용을 확인하기 쉽게 둡니다.
    config = _source_config(request)
    query_template = str(config.get("query_template") or "").strip()
    if not query_template:
        return _error_result(request, "Datalake source_config must include query_template.", "missing_query_template")
    sql, template_missing = _render_template(query_template, params)
    if template_missing:
        return _error_result(request, f"Missing SQL template parameter(s): {', '.join(template_missing)}", "missing_template_params")

    # 3) 더미 함수가 있으면 실제 Datalake 대신 더미 row를 반환합니다.
    dummy_builder = globals().get("_dummy_rows")
    if callable(dummy_builder):
        rows = dummy_builder(request, params, sql)[:fetch_limit]
        return _result(request, rows, {"executed_query": sql})

    # 4) 실제 Datalake 실행부입니다. 더미 블록을 주석 처리한 뒤 아래 블록을 사용합니다.
    missing_creds = []
    for key in ("user_id", "token"):
        if not str(credentials.get(key) or "").strip():
            missing_creds.append(key)
    if missing_creds:
        return _error_result(request, f"Missing Datalake credential(s): {', '.join(missing_creds)}", "missing_datalake_credentials")
    try:
        rows = _run_small_data_sql(sql, credentials, config, fetch_limit)
        return _result(request, rows, {"executed_query": sql, "cluster_type": str(config.get("cluster_type") or DATALAKE_CLUSTER_TYPE)})
    except Exception as exc:
        return _error_result(request, str(exc), "retrieval_failed")


def retrieve_simple_datalake_data(
    data_request_value: Any,
    lake_user_id: str = "",
    lake_jwt_tk: str = "",
    fetch_limit_value: Any = "5000",
) -> Dict[str, Any]:
    """전체 data_request 중 source_type이 datalake인 요청만 골라 실행합니다."""
    # 1) 앞 노드의 wrapper를 벗기고 fetch_limit을 숫자로 정리합니다.
    request_body = _request_body(data_request_value)
    try:
        fetch_limit = max(1, int(fetch_limit_value or 5000))
    except Exception:
        fetch_limit = 5000

    # 2) 여러 source 요청 중 Datalake 요청만 처리합니다.
    #    각 전용 data node가 자기 source_type만 맡으면 flow 연결이 단순해집니다.
    source_requests = []
    for item in _request_items(request_body):
        source_type = _source_type(item.get("source_type") or item.get("source") or _source_config(item).get("source_type"))
        if source_type == "datalake":
            source_requests.append(item)
    if not source_requests:
        return {"skipped": True, "source_type": "datalake", "skip_reason": "No datalake request.", "items": []}
    credentials = {
        "user_id": lake_user_id,
        "token": lake_jwt_tk,
    }

    # 3) 같은 flow에서 여러 Datalake source를 조회할 수 있으므로 source별 결과를 items로 반환합니다.
    items = []
    for item in source_requests:
        items.append(_run_datalake(item, credentials, fetch_limit))
    return {"source_type": "datalake", "items": items}


class DatalakeData(Component):
    """전체 data_request 중 Datalake 요청만 처리하는 전용 data 노드입니다."""
    display_name = "Datalake Data"
    description = "data_request 중 datalake 요청만 골라 실행합니다."
    icon = "Waves"
    name = "DatalakeData"

    inputs = [
        DataInput(name="data_request", display_name="Data Request", input_types=["Data", "JSON"]),
        MessageTextInput(name="lake_user_id", display_name="LAKE_USER_ID", value=""),
        MessageTextInput(name="lake_jwt_tk", display_name="LAKE_JWT_TK", value=""),
        MessageTextInput(name="fetch_limit", display_name="Fetch Limit", value="5000", advanced=True),
    ]
    outputs = [Output(name="source_result", display_name="Data Result", method="build_source_result", types=["Data"])]

    def build_source_result(self):
        """Datalake 요청 실행 결과를 Data Result 출력으로 내보냅니다."""
        payload = retrieve_simple_datalake_data(
            getattr(self, "data_request", None),
            getattr(self, "lake_user_id", ""),
            getattr(self, "lake_jwt_tk", ""),
            getattr(self, "fetch_limit", "5000"),
        )
        self.status = {"source_type": "datalake", "result_count": len(payload.get("items", [])), "skipped": bool(payload.get("skipped"))}
        return _make_data(payload)
