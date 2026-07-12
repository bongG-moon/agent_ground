from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from importlib import import_module
from typing import Any, Dict

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MessageTextInput, MultilineInput, Output
from lfx.schema.data import Data


DEFAULT_ORACLE_DB_KEY = "PKG_RPT"


def ensure_package(package_name: str) -> None:
    """필요한 Python package가 없으면 사내 Nexus trusted-host 옵션으로 설치합니다."""
    # sys.modules는 런타임이 넣은 임시 placeholder도 포함할 수 있으므로
    # 실제 설치 여부는 importlib.util.find_spec 기준으로 확인합니다.
    if importlib.util.find_spec(package_name) is None:
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


def _normalize_triple_quoted_json(text: str) -> str:
    """triple quote로 감싼 Oracle TNS 값을 JSON 문자열로 바꿉니다."""
    def replace(match: re.Match[str]) -> str:
        # match.group(2)는 따옴표 안쪽 실제 문자열입니다.
        # json.dumps를 사용하면 줄바꿈/따옴표가 JSON에서 안전한 형태로 escape됩니다.
        return json.dumps(match.group(2))

    # """...""" 또는 '''...''' 블록을 찾아 JSON string literal로 치환합니다.
    return re.sub(r'("""|\'\'\')(.*?)(\1)', replace, str(text or ""), flags=re.DOTALL)


def _looks_like_tns(text: str) -> bool:
    """Text Input에서 들어온 값이 JSON이 아니라 Oracle TNS 문자열인지 가볍게 판단합니다."""
    upper_text = str(text or "").upper()
    return "(DESCRIPTION=" in upper_text or ("(ADDRESS=" in upper_text and "(CONNECT_DATA=" in upper_text)


def _parse_named_tns_blocks(text: str) -> Dict[str, Any]:
    """Text Input의 'DB_KEY: TNS' 블록을 Oracle 설정 dict로 바꿉니다."""
    # 예:
    # PKG_RPT:
    # (DESCRIPTION=...)
    # PKG_PLAN:
    # (DESCRIPTION=...)
    configs: Dict[str, Any] = {}
    current_key = ""
    current_lines: list[str] = []

    def save_current() -> None:
        nonlocal current_key, current_lines
        tns = "\n".join(current_lines).strip()
        if current_key and _looks_like_tns(tns):
            configs[current_key] = {"tns": tns}
        current_key = ""
        current_lines = []

    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        # PKG_RPT:, PKG_PLAN:처럼 TNS 블록 앞의 DB key 라인을 찾습니다.
        key_match = re.match(r"^([A-Za-z][A-Za-z0-9_-]*)\s*:\s*(.*)$", line)
        if key_match and not line.startswith("("):
            possible_tns = key_match.group(2).strip()
            # TNS 안쪽 줄에는 ADDRESS= 같은 key가 나오지만, DB_KEY: 형태는 보통 맨 앞 식별자입니다.
            save_current()
            current_key = key_match.group(1).strip()
            if possible_tns:
                current_lines.append(possible_tns)
            continue
        if current_key:
            current_lines.append(raw_line)

    save_current()
    return configs


def parse_jsonish(value: Any) -> tuple[Any, list[str]]:
    """JSON 설정이나 Text Input의 raw TNS 문자열을 Oracle 설정 dict로 변환합니다."""
    # 1) 이미 dict/list로 들어온 값은 Langflow Data 연결이므로 그대로 복사해서 사용합니다.
    if value is None:
        return {}, []
    if isinstance(value, (dict, list)):
        return deepcopy(value), []

    # 2) 일반 JSON 문자열과 Python literal 문자열을 차례로 시도합니다.
    text = str(value or "").strip()
    if not text:
        return {}, []
    errors: list[str] = []
    for parser in (json.loads, ast.literal_eval):
        try:
            return parser(text), []
        except Exception as exc:
            errors.append(str(exc))

    # 3) 기존 JSON 설정이 여러 줄 triple quote를 포함할 수 있어 JSON 문자열로 치환한 뒤 다시 시도합니다.
    normalized = _normalize_triple_quoted_json(text)
    if normalized != text:
        for parser in (json.loads, ast.literal_eval):
            try:
                return parser(normalized), []
            except Exception as exc:
                errors.append(str(exc))

    named_tns = _parse_named_tns_blocks(text)
    if named_tns:
        return named_tns, []

    if _looks_like_tns(text):
        # Text Input에는 JSON wrapper 없이 TNS 문자열만 넣을 수 있게 합니다.
        # 실제 접속 시 db_key가 다르게 들어오면 OracleConnector가 단일 설정 fallback으로 이 값을 사용합니다.
        return {DEFAULT_ORACLE_DB_KEY: {"tns": text}}, []
    return {}, errors


def _payload_from_value(value: Any) -> Dict[str, Any]:
    """Data/Message/Text/JSON 어느 입력이 와도 내부 처리용 dict로 맞춥니다."""
    # 입력이 비어 있으면 빈 dict로 통일합니다.
    if value is None:
        return {}

    # dict는 이미 payload이므로 복사해서 사용합니다.
    if isinstance(value, dict):
        return deepcopy(value)

    # Langflow Data 출력은 .data 안에 dict를 담는 경우가 많습니다.
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return deepcopy(data)

    # Message/Text 출력은 문자열을 JSONish parser로 해석합니다.
    text = getattr(value, "text", None) or getattr(value, "content", None)
    if isinstance(text, str):
        parsed, _errors = parse_jsonish(text)
        return parsed if isinstance(parsed, dict) else {"text": text}
    return {}


def _request_body(value: Any) -> Dict[str, Any]:
    """앞 노드가 감싼 data_request/body wrapper를 벗겨 실제 요청 dict만 남깁니다."""
    # adapter/normalizer가 Data wrapper로 넘긴 payload를 먼저 dict로 맞춥니다.
    payload = _payload_from_value(value)

    # reusable flow의 표준 출력은 data_request에 실제 요청을 담습니다.
    if isinstance(payload.get("data_request"), dict):
        return deepcopy(payload["data_request"])

    # flow_text_v1 같은 envelope에서는 body가 실제 요청인 경우가 있습니다.
    if isinstance(payload.get("body"), dict):
        return deepcopy(payload["body"])

    # wrapper가 없다면 payload 자체를 요청으로 사용합니다.
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
    # PKG_RPT, pkg-rpt, pkg rpt처럼 구분자만 다른 key를 같은 값으로 비교합니다.
    return re.sub(r"[\s_-]+", "", str(value or "").strip().lower())


def _dict_get_ci(mapping: Dict[str, Any], key: Any, default: Any = None) -> Any:
    """dict에서 key를 대소문자/공백/underscore 차이를 무시하고 찾습니다."""
    if not isinstance(mapping, dict):
        return default
    text = str(key or "").strip()
    if text in mapping:
        return mapping[text]
    normalized = _normalize_key(text)
    for item_key, value in mapping.items():
        # DATE/date, MCP_NO/mcp no처럼 표기가 달라도 같은 변수로 취급합니다.
        if _normalize_key(item_key) == normalized:
            return value
    return default


def _source_type(value: Any) -> str:
    """Oracle source_type 별칭을 표준값 oracle로 맞춥니다."""
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return {"oracle_db": "oracle", "oracledb": "oracle"}.get(text, text)


def _request_items(request_body: Dict[str, Any]) -> list[Dict[str, Any]]:
    """단일 request와 multi requests를 같은 list[dict] 형태로 맞춥니다."""
    if isinstance(request_body.get("requests"), list):
        # multi source 요청입니다. dict가 아닌 항목은 실행할 수 없으므로 제외합니다.
        return [deepcopy(item) for item in request_body["requests"] if isinstance(item, dict)]
    if isinstance(request_body.get("request"), dict):
        # request 키 하나로 감싼 단일 요청도 허용합니다.
        return [deepcopy(request_body["request"])]
    # wrapper가 없으면 request_body 자체를 단일 요청으로 봅니다.
    return [deepcopy(request_body)] if request_body else []


def _source_config(request: Dict[str, Any]) -> Dict[str, Any]:
    """source_config와 상위 호환 key를 합쳐 Oracle 실행 설정만 추립니다."""
    # source_catalog에서 온 source_config를 기본값으로 사용합니다.
    config = deepcopy(request.get("source_config")) if isinstance(request.get("source_config"), dict) else {}

    # LLM이나 직접 JSON이 실행 key를 상위에 둔 경우도 source_config 안으로 모읍니다.
    for key in ("db_key", "query_template", "sql_template", "oracle_sql", "sql", "query"):
        if request.get(key) not in (None, "", [], {}):
            config.setdefault(key, deepcopy(request[key]))

    # 쿼리 템플릿 별칭은 최종적으로 query_template 하나로 통일합니다.
    for alias in ("sql_template", "oracle_sql", "sql", "query"):
        if config.get(alias) and not config.get("query_template"):
            config["query_template"] = config[alias]
    return config


def _params(request: Dict[str, Any]) -> Dict[str, Any]:
    """params와 variables를 합쳐 SQL 템플릿 치환에 사용할 변수 묶음을 만듭니다."""
    # params가 표준 입력이고, variables는 LLM이 다른 이름으로 만든 경우를 위한 보조 입력입니다.
    params = deepcopy(request.get("params")) if isinstance(request.get("params"), dict) else {}
    if isinstance(request.get("variables"), dict):
        for key, value in request["variables"].items():
            # 이미 params에 같은 key가 있으면 사용자가 명시한 params를 우선합니다.
            params.setdefault(key, deepcopy(value))
    return params


def _param_value(params: Dict[str, Any], key: Any) -> Any:
    """params에서 템플릿 변수 값을 유연하게 찾습니다."""
    return _dict_get_ci(params, key)


def _missing_required_params(params: Dict[str, Any], required_params: list[Any]) -> list[str]:
    """required_params 중 값이 비어 있는 항목 이름을 모읍니다."""
    missing = []
    for item in required_params:
        key = str(item or "").strip()
        # None/빈 문자열/빈 list는 실행에 필요한 값이 없다고 판단합니다.
        if key and _param_value(params, key) in (None, "", []):
            missing.append(key)
    return missing


def _sql_literal(value: Any) -> str:
    """Python 값을 SQL에 직접 넣을 수 있는 literal 문자열로 변환합니다."""
    if value is None:
        return "NULL"
    if isinstance(value, (datetime, date)):
        # 날짜 객체는 현장 쿼리에서 자주 쓰는 YYYYMMDD 문자열로 감쌉니다.
        return f"'{value.strftime('%Y%m%d')}'"
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # 숫자는 따옴표 없이 그대로 넣습니다.
        return str(value)
    # 문자열은 작은따옴표를 escape해서 SQL literal로 만듭니다.
    text = str(value).replace("'", "''")
    return f"'{text}'"


def _render_sql_template(template: str, params: Dict[str, Any]) -> tuple[str, list[str]]:
    """{DATE} 같은 템플릿 변수를 SQL literal로 치환하고 누락 변수를 함께 반환합니다."""
    missing: list[str] = []

    def replace(match: re.Match[str]) -> str:
        # {DATE}에서 DATE 부분만 꺼내 params에서 값을 찾습니다.
        key = match.group(1).strip()
        value = _param_value(params, key)
        if value in (None, "", []):
            # 값이 없으면 원래 placeholder를 남겨두고 missing 목록에 기록합니다.
            missing.append(key)
            return match.group(0)
        # 값이 있으면 SQL literal로 바꿔 안전하게 삽입합니다.
        return _sql_literal(value)

    # 중괄호 placeholder를 모두 찾아 replace 함수로 치환합니다.
    return re.sub(r"\{([^{}]+)\}", replace, str(template or "")), missing


def _json_ready(value: Any) -> Any:
    """DB 결과에 섞인 날짜/Decimal 등을 JSON 직렬화 가능한 값으로 바꿉니다."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        # dict key는 JSON에서 문자열이어야 하므로 str로 맞춥니다.
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_ready(item) for item in value]
    # 그 외 객체는 화면에서 확인 가능하도록 문자열로 둡니다.
    return str(value)


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
    # merger가 source별 결과를 같은 방식으로 읽을 수 있도록 공통 필드를 유지합니다.
    request_params = _params(request)
    payload = {
        "success": True,
        "name": str(request.get("name") or request.get("dataset_key") or request.get("tool_name") or "oracle"),
        "source_type": "oracle",
        "request_params": _json_ready(request_params),
        "request_label": request.get("request_label") or request.get("label") or "",
        "data_result": rows,
        "columns": _rows_columns(rows),
        "row_count": len(rows),
        "error_message": "",
    }
    if extra:
        # db_key/executed_query처럼 source별 디버깅에 필요한 값만 추가로 붙입니다.
        payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# 테스트 전용 더미 데이터
# 실제 실행으로 바꿀 때는 이 블록을 주석 처리하거나 삭제하고, _run_oracle의 실제 실행 블록을 사용합니다.
# ---------------------------------------------------------------------------


def _dummy_param_fields(params: Dict[str, Any]) -> Dict[str, Any]:
    """더미 row에서 입력 파라미터가 어떻게 들어왔는지 확인할 수 있게 컬럼 dict로 만듭니다."""
    fields: Dict[str, Any] = {}
    for key, value in params.items():
        fields[str(key)] = _json_ready(value)
    return fields


def _dummy_rows(request: Dict[str, Any], params: Dict[str, Any], db_key: str, sql: str) -> list[Dict[str, Any]]:
    """테스트 화면에서 파라미터 매핑을 확인할 수 있도록 변수 값을 컬럼으로 넣습니다."""
    row = {
        "source_type": "oracle",
        "source_name": str(request.get("name") or request.get("dataset_key") or request.get("tool_name") or "oracle"),
        "dummy_data": True,
        "db_key": db_key,
        "executed_query": sql,
    }
    row.update(_dummy_param_fields(params))
    return [row]


def _error_result(request: Dict[str, Any], message: str, failure_type: str) -> Dict[str, Any]:
    """data node 실패를 merger가 읽을 수 있는 표준 실패 payload로 만듭니다."""
    return {
        "success": False,
        "name": str(request.get("name") or request.get("dataset_key") or request.get("tool_name") or "oracle"),
        "source_type": "oracle",
        "request_params": _json_ready(_params(request)),
        "request_label": request.get("request_label") or request.get("label") or "",
        "data_result": [],
        "columns": [],
        "row_count": 0,
        "error_message": message,
        "failure_type": failure_type,
    }


class OracleConnector:
    """실제 Oracle 접속을 담당하는 작은 래퍼입니다. 테스트 모드에서는 호출되지 않습니다."""

    def __init__(self, config: Dict[str, Any], oracle_module: Any | None = None):
        self.config = config
        self.oracle_module = oracle_module

    def _oracledb(self) -> Any:
        if self.oracle_module is not None:
            return self.oracle_module
        # Langflow는 컴포넌트 저장 시 class를 먼저 만들기 때문에,
        # 패키지 설치/임포트는 실제 Oracle 연결이 필요한 시점까지 미룹니다.
        ensure_package("oracledb")
        self.oracle_module = import_module("oracledb")
        return self.oracle_module

    def get_connection(self, db_key: str) -> Any:
        # 1) 사용자가 db_key 대소문자나 underscore를 다르게 써도 최대한 같은 DB 설정을 찾습니다.
        resolved = next((key for key in self.config if _normalize_key(key) == _normalize_key(db_key)), "")
        if not resolved and len(self.config) == 1:
            # Text Input에 TNS만 넣은 경우에는 db_key wrapper가 없으므로 단일 설정을 그대로 사용합니다.
            resolved = next(iter(self.config))
        if not resolved:
            raise ValueError(f"Unknown Oracle config key: {db_key}")

        # 2) 다양한 현장 표기(user/username/id, dsn/tns 등)를 받아 하나의 접속값으로 정리합니다.
        config = self.config[resolved]
        user = str(config.get("user") or config.get("username") or config.get("id") or "").strip()
        password = str(config.get("password") or config.get("pw") or "").strip()
        dsn = str(config.get("dsn") or config.get("tns") or config.get("tns_name") or config.get("tns_alias") or "").strip()
        if not dsn:
            raise ValueError(f"Oracle config for {db_key} must include dsn or tns.")
        if user and password:
            return self._oracledb().connect(user=user, password=password, dsn=dsn)
        # TNS만 입력한 환경에서는 외부 인증/지갑 설정을 사용하는 경우가 있어 dsn만 전달합니다.
        return self._oracledb().connect(dsn=dsn)

    def execute_query(self, db_key: str, sql: str, fetch_limit: int | None = None) -> list[Dict[str, Any]]:
        # 1) 커넥션과 커서는 finally에서 닫기 위해 미리 None으로 둡니다.
        conn = None
        cursor = None
        try:
            # 2) SQL 실행 후 cursor.description에서 컬럼명을 가져와 row dict를 만듭니다.
            conn = self.get_connection(db_key)
            cursor = conn.cursor()
            cursor.execute(sql)
            columns = [column[0] for column in cursor.description]
            rows = cursor.fetchmany(fetch_limit) if fetch_limit else cursor.fetchall()
            result: list[Dict[str, Any]] = []
            for row in rows:
                result.append(_json_ready(dict(zip(columns, row))))
            return result
        finally:
            # 3) 성공/실패와 관계없이 리소스를 정리합니다.
            if cursor:
                cursor.close()
            if conn:
                conn.close()


def _run_oracle(request: Dict[str, Any], oracle_config: Dict[str, Any], fetch_limit: int) -> Dict[str, Any]:
    """단일 Oracle 요청을 검증하고, 더미 또는 실제 실행 결과로 변환합니다."""
    # 1) 요청 파라미터와 required_params를 먼저 검증해서 SQL 실행 전에 빠르게 실패시킵니다.
    params = _params(request)
    missing = _missing_required_params(params, _as_list(request.get("required_params")))
    if missing:
        return _error_result(request, f"Missing required parameter(s): {', '.join(missing)}", "missing_required_params")
    # 2) source_config에서 query_template을 가져오고, {DATE} 같은 placeholder를 치환합니다.
    config = _source_config(request)
    query_template = str(config.get("query_template") or "").strip()
    if not query_template:
        return _error_result(request, "Oracle source_config must include query_template.", "missing_query_template")
    sql, template_missing = _render_sql_template(query_template, params)
    if template_missing:
        return _error_result(request, f"Missing SQL template parameter(s): {', '.join(template_missing)}", "missing_template_params")
    db_key = str(config.get("db_key") or request.get("db_key") or DEFAULT_ORACLE_DB_KEY).strip()

    # 3) 더미 함수가 있으면 실제 DB 대신 더미 row를 반환합니다.
    dummy_builder = globals().get("_dummy_rows")
    if callable(dummy_builder):
        rows = dummy_builder(request, params, db_key, sql)[:fetch_limit]
        return _result(request, rows, {"db_key": db_key, "executed_query": sql})

    # 4) 실제 Oracle 실행부입니다. 더미 블록을 주석 처리한 뒤 아래 블록을 사용합니다.
    if not oracle_config:
        return _error_result(request, "Oracle config is empty.", "missing_oracle_config")
    # try:
    #     rows = OracleConnector(oracle_config).execute_query(db_key, sql, fetch_limit)
    #     return _result(request, rows, {"db_key": db_key, "executed_query": sql})
    # except Exception as exc:
    #     return _error_result(request, str(exc), "retrieval_failed")
    return _error_result(request, "Real Oracle execution is disabled while dummy rows are not configured.", "real_execution_disabled")


def retrieve_simple_oracle_data(data_request_value: Any, oracle_config_value: Any = "", fetch_limit_value: Any = "5000") -> Dict[str, Any]:
    """전체 data_request 중 source_type이 oracle인 요청만 골라 실행합니다."""
    # 1) 이전 노드가 넘긴 data_request와 fetch_limit 입력을 실행 가능한 값으로 정리합니다.
    request_body = _request_body(data_request_value)
    try:
        fetch_limit = max(1, int(fetch_limit_value or 5000))
    except Exception:
        fetch_limit = 5000

    # 2) multi request일 수 있으므로 oracle 요청만 필터링하고 다른 source 요청은 건너뜁니다.
    oracle_config, oracle_errors = parse_jsonish(oracle_config_value)
    oracle_config = oracle_config if isinstance(oracle_config, dict) else {}
    source_requests = []
    for item in _request_items(request_body):
        source_type = _source_type(item.get("source_type") or item.get("source") or _source_config(item).get("source_type"))
        if source_type == "oracle":
            source_requests.append(item)
    if not source_requests:
        return {"skipped": True, "source_type": "oracle", "skip_reason": "No oracle request.", "items": []}

    # 3) 설정 파싱 실패가 있으면 각 oracle 요청을 동일한 실패 결과로 반환합니다.
    if oracle_errors:
        items = []
        for item in source_requests:
            items.append(_error_result(item, "Oracle config parse failed: " + "; ".join(oracle_errors), "config_parse_failed"))
    else:
        items = []
        for item in source_requests:
            items.append(_run_oracle(item, oracle_config, fetch_limit))
    return {"source_type": "oracle", "items": items}


class OracleData(Component):
    """전체 data_request 중 Oracle 요청만 처리하는 전용 data 노드입니다."""
    display_name = "Oracle Data"
    description = "data_request 중 oracle 요청만 골라 실행합니다."
    icon = "Database"
    name = "OracleData"

    inputs = [
        DataInput(name="data_request", display_name="Data Request", input_types=["Data", "JSON"]),
        MultilineInput(name="oracle_config", display_name="Oracle TNS", value=""),
        MessageTextInput(name="fetch_limit", display_name="Fetch Limit", value="5000", advanced=True),
    ]
    outputs = [Output(name="source_result", display_name="Data Result", method="build_source_result", types=["Data"])]

    def build_source_result(self):
        """Oracle 요청 실행 결과를 Data Result 출력으로 내보냅니다."""
        payload = retrieve_simple_oracle_data(getattr(self, "data_request", None), getattr(self, "oracle_config", ""), getattr(self, "fetch_limit", "5000"))
        self.status = {"source_type": "oracle", "result_count": len(payload.get("items", [])), "skipped": bool(payload.get("skipped"))}
        return _make_data(payload)
