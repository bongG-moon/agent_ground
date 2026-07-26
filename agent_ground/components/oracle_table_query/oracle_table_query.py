from __future__ import annotations

"""Oracle에서 읽기 전용 SQL을 실행하고 결과 테이블만 반환하는 Standalone Component입니다."""

import base64
import re
from datetime import date, datetime
from decimal import Decimal
from importlib import import_module
from typing import Any, Callable, Mapping

from lfx.custom.custom_component.component import Component
from lfx.io import DictInput, IntInput, MultilineInput, Output, SecretStrInput, StrInput
from lfx.schema import DataFrame


DEFAULT_MAX_ROWS = 5000
MAX_ALLOWED_ROWS = 100000
DEFAULT_QUERY_TIMEOUT_SECONDS = 60
MAX_QUERY_TIMEOUT_SECONDS = 3600
MAX_CELL_BYTES = 5 * 1024 * 1024

# 이 검사는 실수로 변경 SQL을 실행하는 것을 막는 1차 안전장치입니다.
# 최종 보안 경계는 반드시 SELECT 권한만 가진 Oracle 계정이어야 합니다.
FORBIDDEN_SQL_KEYWORDS = {
    "ALTER",
    "BEGIN",
    "CALL",
    "COMMIT",
    "CREATE",
    "DECLARE",
    "DELETE",
    "DROP",
    "EXEC",
    "EXECUTE",
    "FUNCTION",
    "GRANT",
    "INSERT",
    "LOCK",
    "MERGE",
    "PROCEDURE",
    "REVOKE",
    "ROLLBACK",
    "SAVEPOINT",
    "TRUNCATE",
    "UPDATE",
    "UPSERT",
}


def _secret_text(value: Any) -> str:
    """SecretStr 또는 일반 문자열을 실제 접속에 사용할 문자열로 바꿉니다."""

    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        return str(getter())
    return "" if value is None else str(value)


def _sql_guard_text(sql: str) -> str:
    """주석과 문자열 literal을 공백으로 가린 SQL을 만들어 안전성 검사에 사용합니다.

    반환 문자열의 길이를 원문과 같게 유지하므로 허용 가능한 마지막 세미콜론의
    위치를 원문에서도 동일하게 찾을 수 있습니다.
    """

    result: list[str] = []
    index = 0
    state = "normal"
    while index < len(sql):
        char = sql[index]
        next_char = sql[index + 1] if index + 1 < len(sql) else ""

        if state == "normal":
            if char == "-" and next_char == "-":
                result.extend((" ", " "))
                index += 2
                state = "line_comment"
                continue
            if char == "/" and next_char == "*":
                result.extend((" ", " "))
                index += 2
                state = "block_comment"
                continue
            if char == "'":
                result.append(" ")
                index += 1
                state = "single_quote"
                continue
            if char == '"':
                result.append(" ")
                index += 1
                state = "double_quote"
                continue
            result.append(char)
            index += 1
            continue

        if state == "line_comment":
            if char in "\r\n":
                result.append(char)
                state = "normal"
            else:
                result.append(" ")
            index += 1
            continue

        if state == "block_comment":
            if char == "*" and next_char == "/":
                result.extend((" ", " "))
                index += 2
                state = "normal"
            else:
                result.append("\n" if char == "\n" else " ")
                index += 1
            continue

        quote = "'" if state == "single_quote" else '"'
        if char == quote and next_char == quote:
            result.extend((" ", " "))
            index += 2
            continue
        result.append("\n" if char == "\n" else " ")
        index += 1
        if char == quote:
            state = "normal"

    if state in {"single_quote", "double_quote", "block_comment"}:
        raise ValueError("조회 SQL의 따옴표 또는 블록 주석이 닫히지 않았습니다.")
    return "".join(result)


def validate_read_only_sql(sql: Any) -> str:
    """단일 SELECT/WITH 조회문인지 확인하고 driver에 전달할 SQL을 반환합니다."""

    text = str(sql or "").strip()
    if not text:
        raise ValueError("조회 SQL을 입력해 주세요.")

    guard_text = _sql_guard_text(text)
    semicolon_indexes = [index for index, char in enumerate(guard_text) if char == ";"]
    if len(semicolon_indexes) > 1:
        raise ValueError("한 번에 하나의 조회 SQL만 실행할 수 있습니다.")
    if semicolon_indexes:
        semicolon_index = semicolon_indexes[0]
        if guard_text[semicolon_index + 1 :].strip():
            raise ValueError("조회 SQL 뒤에 다른 문장을 함께 실행할 수 없습니다.")
        # Oracle Python driver에는 마지막 세미콜론을 제외한 SQL을 전달합니다.
        text = (text[:semicolon_index] + text[semicolon_index + 1 :]).strip()
        guard_text = _sql_guard_text(text)

    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_$#]*", guard_text.upper())
    if not tokens or tokens[0] not in {"SELECT", "WITH"}:
        raise ValueError("조회 전용 Component에서는 SELECT 또는 WITH로 시작하는 SQL만 허용합니다.")

    forbidden = sorted(set(tokens).intersection(FORBIDDEN_SQL_KEYWORDS))
    if forbidden:
        raise ValueError(f"조회 SQL에 허용되지 않는 명령이 포함되어 있습니다: {', '.join(forbidden)}")
    return text


def normalize_bind_parameters(value: Any) -> dict[str, Any]:
    """DictInput 또는 Langflow Data에 담긴 Oracle bind 변수 map을 정리합니다."""

    if value in (None, ""):
        return {}
    if isinstance(value, Mapping):
        source = value
    else:
        data = getattr(value, "data", None)
        if not isinstance(data, Mapping):
            raise ValueError("바인드 변수는 JSON 객체 형태로 입력해 주세요.")
        source = data

    result: dict[str, Any] = {}
    for key, item in source.items():
        name = str(key).strip()
        if not name:
            raise ValueError("바인드 변수 이름은 비어 있을 수 없습니다.")
        if name.startswith(":"):
            raise ValueError(f"바인드 변수 객체의 키에는 콜론을 붙이지 마세요: {name}")
        result[name] = item
    return result


def normalize_max_rows(value: Any) -> int:
    """최대 조회 행 수를 운영 안전 범위로 제한합니다."""

    try:
        max_rows = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("최대 조회 행 수는 정수여야 합니다.") from exc
    if not 1 <= max_rows <= MAX_ALLOWED_ROWS:
        raise ValueError(f"최대 조회 행 수는 1~{MAX_ALLOWED_ROWS:,} 사이여야 합니다.")
    return max_rows


def normalize_query_timeout(value: Any) -> int:
    """Oracle 한 번의 DB 왕복 제한시간을 초 단위로 검증합니다."""

    try:
        timeout = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("SQL 제한시간은 정수여야 합니다.") from exc
    if not 1 <= timeout <= MAX_QUERY_TIMEOUT_SECONDS:
        raise ValueError(f"SQL 제한시간은 1~{MAX_QUERY_TIMEOUT_SECONDS:,}초 사이여야 합니다.")
    return timeout


def _load_oracle_connect() -> Callable[..., Any]:
    """설치된 oracledb의 connect 함수를 불러오며 실행 중 패키지를 설치하지 않습니다."""

    try:
        module = import_module("oracledb")
    except (ImportError, ModuleNotFoundError) as exc:
        raise RuntimeError(
            "Oracle 조회에 필요한 'oracledb' 패키지가 없습니다. "
            "Agent Builder 운영 환경에 관리자가 패키지를 먼저 설치해야 합니다."
        ) from exc
    return module.connect


def _column_names(description: Any) -> list[str]:
    """cursor description에서 중복되지 않는 컬럼명을 생성합니다."""

    columns: list[str] = []
    counts: dict[str, int] = {}
    for index, column in enumerate(description or [], 1):
        raw_name = getattr(column, "name", None)
        if raw_name is None:
            try:
                raw_name = column[0]
            except (TypeError, IndexError):
                raw_name = f"COLUMN_{index}"
        base_name = str(raw_name or f"COLUMN_{index}")
        counts[base_name] = counts.get(base_name, 0) + 1
        suffix = counts[base_name]
        columns.append(base_name if suffix == 1 else f"{base_name}_{suffix}")
    return columns


def _table_value(value: Any) -> Any:
    """Oracle 결과 값을 DataFrame과 JSON 화면에서 확인 가능한 값으로 바꿉니다."""

    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_CELL_BYTES:
            raise ValueError(f"Oracle 셀 값이 허용 크기 {MAX_CELL_BYTES:,}바이트를 초과합니다.")
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        if len(raw) > MAX_CELL_BYTES:
            raise ValueError(f"Oracle 셀 값이 허용 크기 {MAX_CELL_BYTES:,}바이트를 초과합니다.")
        return base64.b64encode(raw).decode("ascii")

    # CLOB/BLOB 같은 Oracle LOB 객체는 실제 값만 읽어서 테이블에 넣습니다.
    reader = getattr(value, "read", None)
    if callable(reader):
        try:
            loaded = reader(1, MAX_CELL_BYTES + 1)
        except Exception:
            raise ValueError("Oracle LOB 값을 제한 크기로 읽지 못했습니다.") from None
        return _table_value(loaded)
    return str(value)


def _rows_to_records(rows: list[Any], columns: list[str]) -> list[dict[str, Any]]:
    """cursor row를 컬럼명이 있는 record 목록으로 변환합니다."""

    records: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, Mapping):
            record = {str(key): _table_value(item) for key, item in row.items()}
        else:
            record = {column: _table_value(item) for column, item in zip(columns, row, strict=False)}
        records.append(record)
    return records


def _close_quietly(resource: Any) -> None:
    """원래 조회 오류를 가리지 않도록 cursor/connection 종료 오류는 무시합니다."""

    closer = getattr(resource, "close", None)
    if callable(closer):
        try:
            closer()
        except Exception:
            pass


def execute_oracle_query(
    *,
    dsn: str,
    username: str,
    password: str,
    sql: str,
    bind_parameters: Mapping[str, Any] | None,
    max_rows: int,
    query_timeout_seconds: int,
    connect_fn: Callable[..., Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str], bool]:
    """Oracle 조회를 실행합니다.

    ``connect_fn``을 주입할 수 있어 실제 DB 없이 connection/cursor 가짜 객체로
    조회 계약과 리소스 종료를 테스트할 수 있습니다.
    """

    connection_factory = connect_fn or _load_oracle_connect()
    connection = None
    cursor = None
    try:
        connect_kwargs: dict[str, Any] = {"dsn": dsn}
        if username and password:
            connect_kwargs.update({"user": username, "password": password})
        connection = connection_factory(**connect_kwargs)
        # oracledb call_timeout은 각 DB 왕복 호출의 최대 시간이며 millisecond 단위입니다.
        connection.call_timeout = query_timeout_seconds * 1000
        cursor = connection.cursor()
        parameters = dict(bind_parameters or {})
        if parameters:
            cursor.execute(sql, parameters)
        else:
            cursor.execute(sql)

        description = getattr(cursor, "description", None)
        if description is None:
            raise RuntimeError("Oracle 조회 결과에 컬럼 정보가 없습니다.")
        columns = _column_names(description)
        fetched = list(cursor.fetchmany(max_rows + 1))
        truncated = len(fetched) > max_rows
        return _rows_to_records(fetched[:max_rows], columns), columns, truncated
    finally:
        _close_quietly(cursor)
        _close_quietly(connection)


def _safe_error_text(error: Exception, sensitive_values: list[str]) -> str:
    """접속 정보가 노출되지 않도록 driver 오류 문자열을 짧게 정리합니다."""

    if error.__class__.__module__ not in {"builtins", __name__}:
        return f"외부 Oracle driver 오류가 발생했습니다 ({error.__class__.__name__})."
    text = str(error).strip() or error.__class__.__name__
    for value in sensitive_values:
        if value:
            text = text.replace(value, "[보호됨]")
    return text[:500]


def build_dataframe(rows: list[dict[str, Any]], columns: list[str]) -> DataFrame:
    """0행 결과도 컬럼 구조를 유지하는 Langflow DataFrame을 만듭니다."""

    if rows:
        return DataFrame(rows)
    return DataFrame({column: [] for column in columns})


class OracleTableQuery(Component):
    """Oracle 단일 조회를 수행하고 결과 데이터 테이블만 반환합니다."""

    display_name = "Oracle 테이블 조회"
    description = "Oracle 접속 정보와 읽기 SQL을 직접 입력받아 조회 결과를 DataFrame으로 반환합니다."
    icon = "Database"
    name = "OracleTableQuery"

    inputs = [
        MultilineInput(
            name="dsn",
            display_name="Oracle DSN/TNS",
            required=True,
            info="한 개 Oracle DB의 DSN, TNS 별칭 또는 전체 TNS 문자열을 입력합니다.",
        ),
        StrInput(
            name="username",
            display_name="사용자 ID",
            value="",
            info="일반 인증이면 입력합니다. 외부 인증/Wallet 환경이면 비워둘 수 있습니다.",
        ),
        SecretStrInput(
            name="password",
            display_name="비밀번호",
            value="",
            info="사용자 ID를 입력했다면 비밀번호도 함께 입력해야 합니다.",
        ),
        MultilineInput(
            name="sql_query",
            display_name="조회 SQL",
            required=True,
            info="SELECT 또는 조회용 WITH 문 한 개만 입력합니다. 값은 바인드 변수를 사용하세요.",
        ),
        DictInput(
            name="bind_parameters",
            display_name="바인드 변수",
            value={},
            info="SQL의 :DATE 같은 변수에 전달할 JSON 객체입니다. 키에는 콜론을 붙이지 않습니다.",
        ),
        IntInput(
            name="max_rows",
            display_name="최대 조회 행 수",
            value=DEFAULT_MAX_ROWS,
            advanced=True,
            info=f"1~{MAX_ALLOWED_ROWS:,}행 사이에서 설정합니다.",
        ),
        IntInput(
            name="query_timeout_seconds",
            display_name="SQL 제한시간(초)",
            value=DEFAULT_QUERY_TIMEOUT_SECONDS,
            advanced=True,
            info="Oracle 각 DB 왕복 호출에 적용할 제한시간입니다. 기본값은 60초입니다.",
        ),
    ]
    outputs = [
        Output(
            name="data_table",
            display_name="조회 데이터 테이블",
            method="build_data_table",
            types=["DataFrame"],
            cache=False,
            tool_mode=False,
        )
    ]

    def build_data_table(self) -> DataFrame:
        """입력값을 검증하고 Oracle 조회 결과만 DataFrame으로 반환합니다."""

        dsn = str(getattr(self, "dsn", "") or "").strip()
        if not dsn:
            raise ValueError("Oracle DSN/TNS를 입력해 주세요.")

        username = str(getattr(self, "username", "") or "").strip()
        password = _secret_text(getattr(self, "password", ""))
        if bool(username) != bool(password):
            raise ValueError("사용자 ID와 비밀번호는 둘 다 입력하거나 둘 다 비워야 합니다.")

        sql = validate_read_only_sql(getattr(self, "sql_query", ""))
        parameters = normalize_bind_parameters(getattr(self, "bind_parameters", {}))
        max_rows = normalize_max_rows(getattr(self, "max_rows", DEFAULT_MAX_ROWS))
        query_timeout = normalize_query_timeout(
            getattr(self, "query_timeout_seconds", DEFAULT_QUERY_TIMEOUT_SECONDS)
        )

        try:
            rows, columns, truncated = execute_oracle_query(
                dsn=dsn,
                username=username,
                password=password,
                sql=sql,
                bind_parameters=parameters,
                max_rows=max_rows,
                query_timeout_seconds=query_timeout,
            )
        except Exception as exc:
            message = _safe_error_text(exc, [dsn, username, password, sql, *map(str, parameters.values())])
            raise RuntimeError(f"Oracle 데이터 조회에 실패했습니다: {message}") from None

        table = build_dataframe(rows, columns)
        self.status = {
            "조회 행 수": len(rows),
            "컬럼 수": len(columns),
            "최대 행 제한 적용": truncated,
        }
        return table
