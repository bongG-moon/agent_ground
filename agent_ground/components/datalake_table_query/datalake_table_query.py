from __future__ import annotations

"""Datalake Cluster를 찾고 읽기 SQL 결과 테이블만 반환하는 Standalone Component입니다."""

import asyncio
import base64
import ipaddress
import inspect
import json
import re
import time
from datetime import date, datetime
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import quote, unquote, urljoin, urlsplit

from lfx.custom.custom_component.component import Component
from lfx.io import BoolInput, DictInput, IntInput, MultilineInput, Output, SecretStrInput, StrInput
from lfx.schema import DataFrame


DEFAULT_API_BASE_URL = "https://api-server.lake.skhynix.com/api/v4/"
DEFAULT_CLUSTER_STATUS_PATH = "runtime/cluster/{cluster_type}/running"
DEFAULT_CLUSTER_TYPE = "starrocks"
DEFAULT_JDBC_ENDPOINT_KEY = "jdbc-external"
DEFAULT_ALLOWED_MYSQL_HOSTS = ".skhynix.com"
DEFAULT_MAX_ROWS = 5000
MAX_ALLOWED_ROWS = 100000
DEFAULT_API_TIMEOUT_SECONDS = 15
DEFAULT_CLUSTER_WAIT_SECONDS = 120
DEFAULT_POLL_INTERVAL_SECONDS = 3
MYSQL_AUTH_PLUGIN = "mysql_clear_password"
MAX_CLUSTER_RESPONSE_BYTES = 1024 * 1024

# 이 목록은 실수로 변경 SQL을 실행하는 것을 막는 보조 안전장치입니다.
# 실제 Datalake 계정에도 조회 권한만 부여해야 합니다.
FORBIDDEN_SQL_KEYWORDS = {
    "ALTER",
    "BEGIN",
    "CALL",
    "COMMIT",
    "CREATE",
    "DECLARE",
    "DELETE",
    "DROP",
    "DUMPFILE",
    "EXEC",
    "EXECUTE",
    "FUNCTION",
    "GRANT",
    "INSERT",
    "INTO",
    "LOCK",
    "MERGE",
    "PROCEDURE",
    "REVOKE",
    "ROLLBACK",
    "SAVEPOINT",
    "TRUNCATE",
    "UPDATE",
    "UPSERT",
    "OUTFILE",
}


def _secret_text(value: Any) -> str:
    """SecretStr 또는 일반 문자열에서 실제 인증 토큰을 가져옵니다."""

    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        return str(getter())
    return "" if value is None else str(value)


def _sql_guard_text(sql: str) -> str:
    """SQL 주석과 문자열 literal을 같은 길이의 공백으로 바꿉니다."""

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

        quote_char = "'" if state == "single_quote" else '"'
        if char == quote_char and next_char == quote_char:
            result.extend((" ", " "))
            index += 2
            continue
        result.append("\n" if char == "\n" else " ")
        index += 1
        if char == quote_char:
            state = "normal"

    if state in {"single_quote", "double_quote", "block_comment"}:
        raise ValueError("조회 SQL의 따옴표 또는 블록 주석이 닫히지 않았습니다.")
    return "".join(result)


def validate_read_only_sql(sql: Any) -> str:
    """단일 SELECT/WITH 조회문인지 확인합니다."""

    text = str(sql or "").strip()
    if not text:
        raise ValueError("조회 SQL을 입력해 주세요.")
    if re.search(r"/\*!", text):
        raise ValueError("실행 가능한 MySQL 주석(/*! ... */)은 조회 SQL에 사용할 수 없습니다.")

    guard_text = _sql_guard_text(text)
    semicolon_indexes = [index for index, char in enumerate(guard_text) if char == ";"]
    if len(semicolon_indexes) > 1:
        raise ValueError("한 번에 하나의 조회 SQL만 실행할 수 있습니다.")
    if semicolon_indexes:
        semicolon_index = semicolon_indexes[0]
        if guard_text[semicolon_index + 1 :].strip():
            raise ValueError("조회 SQL 뒤에 다른 문장을 함께 실행할 수 없습니다.")
        text = (text[:semicolon_index] + text[semicolon_index + 1 :]).strip()
        guard_text = _sql_guard_text(text)

    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_$]*", guard_text.upper())
    if not tokens or tokens[0] not in {"SELECT", "WITH"}:
        raise ValueError("조회 전용 Component에서는 SELECT 또는 WITH로 시작하는 SQL만 허용합니다.")

    forbidden = sorted(set(tokens).intersection(FORBIDDEN_SQL_KEYWORDS))
    if forbidden:
        raise ValueError(f"조회 SQL에 허용되지 않는 명령이 포함되어 있습니다: {', '.join(forbidden)}")
    return text


def normalize_bind_parameters(value: Any) -> dict[str, Any]:
    """DictInput 또는 Langflow Data에 담긴 MySQL native bind 변수 map을 정리합니다."""

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
        result[name] = item
    return result


def normalize_int(value: Any, *, label: str, minimum: int, maximum: int) -> int:
    """정수 입력을 지정한 운영 안전 범위로 제한합니다."""

    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}은(는) 정수여야 합니다.") from exc
    if not minimum <= number <= maximum:
        raise ValueError(f"{label}은(는) {minimum:,}~{maximum:,} 사이여야 합니다.")
    return number


def normalize_bool(value: Any, *, label: str) -> bool:
    """BoolInput 또는 문자열 형태의 명시적 동의 값을 안전하게 해석합니다."""

    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes", "y", "on"}:
        return True
    if text in {"false", "0", "no", "n", "off", ""}:
        return False
    raise ValueError(f"{label} 값은 true 또는 false여야 합니다.")


def validate_api_base_url(value: Any, *, allow_insecure_http: bool = False) -> str:
    """Bearer Token을 보낼 API 기준 URL의 기본 구조를 확인합니다."""

    text = str(value or "").strip()
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Datalake API 기준 URL은 http 또는 https 전체 URL이어야 합니다.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Datalake API 기준 URL에는 계정, query string, fragment를 넣을 수 없습니다.")
    if parsed.scheme == "http" and not allow_insecure_http:
        raise ValueError(
            "Datalake JWT를 HTTP로 보낼 수 없습니다. HTTPS URL을 사용하거나, "
            "폐쇄된 테스트망에서만 'HTTP API 사용 허용'을 명시적으로 켜 주세요."
        )
    return text.rstrip("/") + "/"


def _normalize_host(value: Any) -> str:
    """비교용 host를 소문자와 후행점 제거 형태로 정규화합니다."""

    host = str(value or "").strip().lower().rstrip(".")
    if not host:
        raise ValueError("MySQL host가 비어 있습니다.")
    return host


def normalize_allowed_mysql_hosts(value: Any) -> tuple[str, ...]:
    """정확한 host 또는 `.example.com` suffix 허용 목록을 정리합니다."""

    raw_items = re.split(r"[,;\r\n]+", str(value or ""))
    rules: list[str] = []
    for raw in raw_items:
        rule = raw.strip().lower().rstrip(".")
        if not rule:
            continue
        if rule.startswith("*."):
            rule = "." + rule[2:]
        is_suffix = rule.startswith(".")
        host_part = rule[1:] if is_suffix else rule
        if not host_part or "://" in host_part or "/" in host_part or ":" in host_part:
            raise ValueError("허용 MySQL host에는 scheme, path, port를 넣을 수 없습니다.")
        try:
            ipaddress.ip_address(host_part)
            if is_suffix:
                raise ValueError("IP 주소는 suffix가 아닌 정확한 주소로만 허용할 수 있습니다.")
        except ValueError as exc:
            if "suffix" in str(exc):
                raise
            labels = host_part.split(".")
            if any(
                not label
                or len(label) > 63
                or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
                for label in labels
            ):
                raise ValueError(f"허용 MySQL host 형식이 올바르지 않습니다: {raw.strip()}") from exc
        normalized = f".{host_part}" if is_suffix else host_part
        if normalized not in rules:
            rules.append(normalized)
    if not rules:
        raise ValueError("허용 MySQL host를 한 개 이상 입력해 주세요.")
    return tuple(rules)


def ensure_allowed_mysql_host(host: Any, allowed_hosts: tuple[str, ...]) -> str:
    """Cluster API가 돌려준 host가 사용자가 지정한 허용 범위인지 확인합니다."""

    normalized = _normalize_host(host)
    try:
        ipaddress.ip_address(normalized)
        is_ip = True
    except ValueError:
        is_ip = False

    for rule in allowed_hosts:
        if rule.startswith("."):
            if is_ip:
                continue
            suffix = rule[1:]
            if normalized == suffix or normalized.endswith("." + suffix):
                return normalized
        elif normalized == rule:
            return normalized
    raise PermissionError("Cluster API가 반환한 MySQL host가 허용 목록에 없습니다.")


def validate_mysql_ssl_ca_path(value: Any) -> str:
    """MySQL 서버 인증서 검증에 사용할 CA 파일을 확인합니다."""

    text = str(value or "").strip()
    if not text:
        raise ValueError("MySQL 서버 인증서 검증용 CA 파일 경로를 입력해 주세요.")
    path = Path(text).expanduser()
    if not path.is_file():
        raise ValueError("MySQL CA 파일을 찾을 수 없습니다. Agent Builder 실행 환경의 경로를 확인해 주세요.")
    return str(path.resolve())


def build_cluster_status_url(api_base_url: str, status_path: Any, cluster_type: Any) -> str:
    """입력한 API 기준 URL과 상대 경로를 안전하게 결합합니다."""

    cluster = str(cluster_type or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", cluster):
        raise ValueError("Cluster 유형은 영문, 숫자, 밑줄, 하이픈만 사용할 수 있습니다.")

    path_template = str(status_path or "").strip().replace("{cluster_type}", quote(cluster, safe=""))
    if not path_template:
        raise ValueError("Cluster 상태 경로를 입력해 주세요.")
    if "{" in path_template or "}" in path_template:
        raise ValueError("Cluster 상태 경로에는 {cluster_type} 변수만 사용할 수 있습니다.")

    parsed_path = urlsplit(path_template)
    if parsed_path.scheme or parsed_path.netloc or parsed_path.query or parsed_path.fragment:
        raise ValueError("Cluster 상태 경로는 query string이 없는 상대 경로여야 합니다.")
    path_segments = [segment for segment in parsed_path.path.replace("\\", "/").split("/") if segment]
    if any(segment == ".." for segment in path_segments):
        raise ValueError("Cluster 상태 경로에는 상위 경로 이동(..)을 사용할 수 없습니다.")
    return urljoin(api_base_url, "/".join(path_segments))


def validate_endpoint_key(value: Any) -> str:
    """Cluster 응답에서 MySQL 주소를 찾을 endpoints key를 확인합니다."""

    key = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", key):
        raise ValueError("MySQL Endpoint 필드는 영문, 숫자, 점, 밑줄, 하이픈만 사용할 수 있습니다.")
    return key


def parse_mysql_endpoint(value: Any) -> tuple[str, int, str]:
    """host:port, mysql://, jdbc:mysql:// 주소를 MySQL 연결값으로 나눕니다."""

    text = str(value or "").strip()
    if not text:
        raise ValueError("Cluster 응답에 MySQL 접속 주소가 없습니다.")
    if text.lower().startswith("jdbc:mysql://"):
        text = text[5:]
    if "://" not in text:
        text = "mysql://" + text

    parsed = urlsplit(text)
    if parsed.scheme.lower() != "mysql" or not parsed.hostname:
        raise ValueError("MySQL 접속 주소 형식이 올바르지 않습니다.")
    if parsed.username or parsed.password:
        raise ValueError("Cluster 응답의 MySQL 주소에 계정 정보가 포함되어서는 안 됩니다.")
    if parsed.query or parsed.fragment:
        raise ValueError("Cluster 응답의 MySQL 주소에는 query string이나 fragment가 없어야 합니다.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("MySQL 접속 주소의 port가 올바르지 않습니다.") from exc
    if port is None or not 1 <= port <= 65535:
        raise ValueError("MySQL 접속 주소에는 1~65535 범위의 port가 필요합니다.")
    database = unquote(parsed.path.lstrip("/").split("/", 1)[0]) if parsed.path.strip("/") else ""
    return parsed.hostname, port, database


def _load_aiohttp() -> Any:
    """설치된 aiohttp를 불러오며 실행 중 패키지를 설치하지 않습니다."""

    try:
        return import_module("aiohttp")
    except (ImportError, ModuleNotFoundError) as exc:
        raise RuntimeError(
            "Datalake Cluster API 호출에 필요한 'aiohttp' 패키지가 없습니다. "
            "Agent Builder 운영 환경에 관리자가 패키지를 먼저 설치해야 합니다."
        ) from exc


def _load_mysql_connect() -> Callable[..., Any]:
    """설치된 mysql-connector-python의 connect 함수를 불러옵니다."""

    try:
        module = import_module("mysql.connector")
    except (ImportError, ModuleNotFoundError) as exc:
        raise RuntimeError(
            "Datalake SQL 조회에 필요한 'mysql-connector-python' 패키지가 없습니다. "
            "Agent Builder 운영 환경에 관리자가 패키지를 먼저 설치해야 합니다."
        ) from exc
    return module.connect


async def _poll_cluster_status(
    *,
    status_url: str,
    headers: Mapping[str, str],
    endpoint_key: str,
    allowed_mysql_hosts: tuple[str, ...],
    request_timeout_seconds: int,
    cluster_wait_seconds: int,
    poll_interval_seconds: int,
    status_fetcher: Callable[[str, Mapping[str, str], int], Any],
    sleep_fn: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> tuple[str, int, str]:
    """실제 전체 deadline 안에서 Cluster 상태와 허용된 MySQL 주소를 확인합니다."""

    max_attempts = max(1, cluster_wait_seconds // poll_interval_seconds + 1)
    deadline = monotonic_fn() + cluster_wait_seconds
    last_status = "UNKNOWN"
    for attempt in range(1, max_attempts + 1):
        remaining = deadline - monotonic_fn()
        if remaining <= 0:
            break
        per_request_timeout = max(1, min(request_timeout_seconds, int(remaining + 0.999)))
        fetched = status_fetcher(status_url, headers, per_request_timeout)
        if inspect.isawaitable(fetched):
            try:
                payload = await asyncio.wait_for(fetched, timeout=remaining)
            except asyncio.TimeoutError as exc:
                raise TimeoutError("Datalake Cluster 상태 조회가 전체 대기시간을 초과했습니다.") from exc
        else:
            payload = fetched
        if not isinstance(payload, Mapping):
            raise RuntimeError("Datalake Cluster API 응답이 JSON 객체가 아닙니다.")

        last_status = str(payload.get("status") or "UNKNOWN").upper()
        if last_status == "RUNNING":
            endpoints = payload.get("endpoints")
            if not isinstance(endpoints, Mapping):
                raise RuntimeError("RUNNING 응답에 endpoints 객체가 없습니다.")
            host, port, database = parse_mysql_endpoint(endpoints.get(endpoint_key))
            return ensure_allowed_mysql_host(host, allowed_mysql_hosts), port, database

        if attempt < max_attempts:
            remaining = deadline - monotonic_fn()
            if remaining <= 0:
                break
            await sleep_fn(min(poll_interval_seconds, remaining))
    raise TimeoutError(f"Datalake Cluster가 제한시간 안에 RUNNING 상태가 되지 않았습니다. 마지막 상태: {last_status}")


async def discover_mysql_endpoint(
    *,
    status_url: str,
    user_id: str,
    token: str,
    endpoint_key: str,
    allowed_mysql_hosts: tuple[str, ...],
    request_timeout_seconds: int,
    cluster_wait_seconds: int,
    poll_interval_seconds: int,
    status_fetcher: Callable[[str, Mapping[str, str], int], Any] | None = None,
    sleep_fn: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> tuple[str, int, str]:
    """Datalake API를 통해 실행 중인 Cluster의 MySQL endpoint를 찾습니다.

    테스트에서는 ``status_fetcher``를 주입해 네트워크 호출 없이 상태 전이와
    endpoint parsing을 확인할 수 있습니다.
    """

    headers = {
        "accept": "application/json;charset=UTF-8",
        "Authorization": f"Bearer {token}",
        "user_id": user_id,
    }

    if status_fetcher is not None:
        return await _poll_cluster_status(
            status_url=status_url,
            headers=headers,
            endpoint_key=endpoint_key,
            allowed_mysql_hosts=allowed_mysql_hosts,
            request_timeout_seconds=request_timeout_seconds,
            cluster_wait_seconds=cluster_wait_seconds,
            poll_interval_seconds=poll_interval_seconds,
            status_fetcher=status_fetcher,
            sleep_fn=sleep_fn,
            monotonic_fn=monotonic_fn,
        )

    aiohttp = _load_aiohttp()
    timeout = aiohttp.ClientTimeout(total=request_timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout) as session:

        async def fetch_status(url: str, request_headers: Mapping[str, str], _timeout: int) -> Any:
            # redirect를 따라가면 Bearer Token이 다른 host로 전달될 수 있어 차단합니다.
            async with session.get(url, headers=dict(request_headers), allow_redirects=False) as response:
                if response.status in {401, 403}:
                    raise PermissionError(f"Datalake Cluster API 인증에 실패했습니다. HTTP {response.status}")
                if not 200 <= response.status < 300:
                    raise RuntimeError(f"Datalake Cluster API 호출에 실패했습니다. HTTP {response.status}")
                content_length = response.headers.get("Content-Length", "")
                if content_length:
                    try:
                        if int(content_length) > MAX_CLUSTER_RESPONSE_BYTES:
                            raise RuntimeError("Datalake Cluster API 응답이 허용 크기를 초과했습니다.")
                    except ValueError:
                        pass
                try:
                    raw = await response.content.read(MAX_CLUSTER_RESPONSE_BYTES + 1)
                    if len(raw) > MAX_CLUSTER_RESPONSE_BYTES:
                        raise RuntimeError("Datalake Cluster API 응답이 허용 크기를 초과했습니다.")
                    return json.loads(raw)
                except RuntimeError:
                    raise
                except Exception as exc:
                    raise RuntimeError("Datalake Cluster API 응답을 JSON으로 해석할 수 없습니다.") from exc

        return await _poll_cluster_status(
            status_url=status_url,
            headers=headers,
            endpoint_key=endpoint_key,
            allowed_mysql_hosts=allowed_mysql_hosts,
            request_timeout_seconds=request_timeout_seconds,
            cluster_wait_seconds=cluster_wait_seconds,
            poll_interval_seconds=poll_interval_seconds,
            status_fetcher=fetch_status,
            sleep_fn=sleep_fn,
            monotonic_fn=monotonic_fn,
        )


def _column_names(cursor: Any) -> list[str]:
    """MySQL cursor에서 중복되지 않는 컬럼명을 생성합니다."""

    raw_columns = getattr(cursor, "column_names", None)
    if not raw_columns:
        description = getattr(cursor, "description", None) or []
        raw_columns = [getattr(item, "name", None) or item[0] for item in description]

    columns: list[str] = []
    counts: dict[str, int] = {}
    for index, raw_name in enumerate(raw_columns or [], 1):
        base_name = str(raw_name or f"COLUMN_{index}")
        counts[base_name] = counts.get(base_name, 0) + 1
        suffix = counts[base_name]
        columns.append(base_name if suffix == 1 else f"{base_name}_{suffix}")
    return columns


def _table_value(value: Any) -> Any:
    """StarRocks/MySQL 결과 값을 DataFrame 화면에서 안전하게 확인 가능한 값으로 바꿉니다."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode("ascii")
    try:
        if value != value:
            return None
    except Exception:
        pass
    return str(value)


def _rows_to_records(rows: list[Any], columns: list[str]) -> list[dict[str, Any]]:
    """MySQL cursor row를 컬럼명이 있는 record 목록으로 변환합니다."""

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


def execute_mysql_query(
    *,
    host: str,
    port: int,
    database: str,
    user_id: str,
    token: str,
    sql: str,
    bind_parameters: Mapping[str, Any] | None,
    max_rows: int,
    connect_timeout_seconds: int,
    query_timeout_seconds: int,
    ssl_ca_path: str,
    connect_fn: Callable[..., Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str], bool]:
    """StarRocks MySQL endpoint에서 SQL을 실행합니다.

    ``connect_fn``을 주입할 수 있어 실제 Datalake 없이 connection/cursor 가짜
    객체로 native bind, 최대 행 수, 리소스 종료 계약을 테스트할 수 있습니다.
    """

    connection_factory = connect_fn or _load_mysql_connect()
    connection = None
    cursor = None
    try:
        connect_kwargs: dict[str, Any] = {
            "host": host,
            "port": port,
            "user": user_id,
            "password": token,
            "use_pure": True,
            "ssl_disabled": False,
            "ssl_ca": ssl_ca_path,
            "ssl_verify_cert": True,
            "ssl_verify_identity": True,
            "auth_plugin": MYSQL_AUTH_PLUGIN,
            "allow_local_infile": False,
            "connection_timeout": connect_timeout_seconds,
            "read_timeout": query_timeout_seconds,
            "write_timeout": query_timeout_seconds,
        }
        if database:
            connect_kwargs["database"] = database
        connection = connection_factory(**connect_kwargs)
        cursor = connection.cursor()
        parameters = dict(bind_parameters or {})
        if parameters:
            cursor.execute(sql, parameters)
        else:
            cursor.execute(sql)

        columns = _column_names(cursor)
        if not columns:
            raise RuntimeError("Datalake 조회 결과에 컬럼 정보가 없습니다.")
        fetched = list(cursor.fetchmany(max_rows + 1))
        truncated = len(fetched) > max_rows
        return _rows_to_records(fetched[:max_rows], columns), columns, truncated
    finally:
        _close_quietly(cursor)
        _close_quietly(connection)


def _safe_error_text(error: Exception, sensitive_values: list[str]) -> str:
    """인증 정보와 내부 주소가 노출되지 않도록 오류 문자열을 정리합니다."""

    if error.__class__.__module__ not in {"builtins", __name__}:
        return f"외부 MySQL driver 오류가 발생했습니다 ({error.__class__.__name__})."
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


class DatalakeTableQuery(Component):
    """Datalake의 StarRocks Cluster를 찾아 단일 조회 결과 테이블만 반환합니다."""

    display_name = "Datalake 테이블 조회"
    description = "Cluster API와 MySQL 조회 설정을 직접 입력받아 Datalake 결과를 DataFrame으로 반환합니다."
    icon = "Waves"
    name = "DatalakeTableQuery"

    inputs = [
        StrInput(
            name="api_base_url",
            display_name="Datalake API 기준 URL",
            value=DEFAULT_API_BASE_URL,
            required=True,
            info="Cluster 상태 API의 기준 URL입니다. 신뢰할 수 있는 사내 주소만 사용하세요.",
        ),
        BoolInput(
            name="allow_insecure_api_http",
            display_name="HTTP API 사용 허용",
            value=False,
            advanced=True,
            info="기본값은 꺼짐입니다. HTTPS를 지원하지 않는 폐쇄된 테스트망에서만 명시적으로 켜세요.",
        ),
        StrInput(
            name="cluster_status_path",
            display_name="Cluster 상태 경로",
            value=DEFAULT_CLUSTER_STATUS_PATH,
            required=True,
            info="기준 URL 뒤에 붙는 상대 경로입니다. {cluster_type} 변수를 사용할 수 있습니다.",
        ),
        StrInput(
            name="cluster_type",
            display_name="Cluster 유형",
            value=DEFAULT_CLUSTER_TYPE,
            required=True,
            info="기본값은 starrocks입니다.",
        ),
        StrInput(
            name="jdbc_endpoint_key",
            display_name="MySQL Endpoint 필드",
            value=DEFAULT_JDBC_ENDPOINT_KEY,
            required=True,
            advanced=True,
            info="Cluster 응답의 endpoints 객체에서 MySQL 주소를 찾을 필드명입니다.",
        ),
        MultilineInput(
            name="allowed_mysql_hosts",
            display_name="허용 MySQL Host",
            value=DEFAULT_ALLOWED_MYSQL_HOSTS,
            required=True,
            info="정확한 host 또는 .example.com suffix를 쉼표/줄바꿈으로 입력합니다. API가 다른 host를 반환하면 차단합니다.",
        ),
        StrInput(
            name="lake_user_id",
            display_name="Datalake 사용자 ID",
            required=True,
            info="Cluster API의 user_id와 MySQL 접속 사용자로 전달됩니다.",
        ),
        SecretStrInput(
            name="lake_jwt_token",
            display_name="Datalake JWT 토큰",
            required=True,
            info="Bearer Token과 MySQL 비밀번호로 전달되는 보호 필드입니다.",
        ),
        StrInput(
            name="mysql_ssl_ca_path",
            display_name="MySQL CA 파일 경로",
            required=True,
            info="MySQL 서버 인증서와 host 신원을 검증할 CA PEM 파일의 Agent Builder 실행 환경 경로입니다.",
        ),
        MultilineInput(
            name="sql_query",
            display_name="조회 SQL",
            required=True,
            info="SELECT 또는 조회용 WITH 문 한 개만 입력합니다. 값은 native bind를 사용하세요.",
        ),
        DictInput(
            name="bind_parameters",
            display_name="바인드 변수",
            value={},
            info="SQL의 %(from_ym)s 같은 변수에 전달할 JSON 객체입니다.",
        ),
        IntInput(
            name="max_rows",
            display_name="최대 조회 행 수",
            value=DEFAULT_MAX_ROWS,
            advanced=True,
            info=f"1~{MAX_ALLOWED_ROWS:,}행 사이에서 설정합니다.",
        ),
        IntInput(
            name="api_timeout_seconds",
            display_name="API/DB 연결 제한시간(초)",
            value=DEFAULT_API_TIMEOUT_SECONDS,
            advanced=True,
            info="Cluster API 한 번의 요청과 MySQL 연결에 적용할 제한시간입니다.",
        ),
        IntInput(
            name="query_timeout_seconds",
            display_name="SQL 읽기 제한시간(초)",
            value=60,
            advanced=True,
            info="MySQL SQL 실행과 결과 읽기에 적용할 제한시간입니다.",
        ),
        IntInput(
            name="cluster_wait_seconds",
            display_name="Cluster 전체 대기시간(초)",
            value=DEFAULT_CLUSTER_WAIT_SECONDS,
            advanced=True,
            info="Cluster가 RUNNING이 될 때까지 기다릴 전체 시간입니다.",
        ),
        IntInput(
            name="poll_interval_seconds",
            display_name="Cluster 확인 간격(초)",
            value=DEFAULT_POLL_INTERVAL_SECONDS,
            advanced=True,
            info="RUNNING 상태를 다시 확인하는 간격입니다.",
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

    async def build_data_table(self) -> DataFrame:
        """Cluster endpoint를 찾고 Datalake 조회 결과만 DataFrame으로 반환합니다."""

        allow_insecure_http = normalize_bool(
            getattr(self, "allow_insecure_api_http", False),
            label="HTTP API 사용 허용",
        )
        api_base_url = validate_api_base_url(
            getattr(self, "api_base_url", ""),
            allow_insecure_http=allow_insecure_http,
        )
        status_url = build_cluster_status_url(
            api_base_url,
            getattr(self, "cluster_status_path", ""),
            getattr(self, "cluster_type", ""),
        )
        endpoint_key = validate_endpoint_key(getattr(self, "jdbc_endpoint_key", ""))
        allowed_hosts = normalize_allowed_mysql_hosts(getattr(self, "allowed_mysql_hosts", ""))
        user_id = str(getattr(self, "lake_user_id", "") or "").strip()
        token = _secret_text(getattr(self, "lake_jwt_token", "")).strip()
        if not user_id:
            raise ValueError("Datalake 사용자 ID를 입력해 주세요.")
        if not token:
            raise ValueError("Datalake JWT 토큰을 입력해 주세요.")
        ssl_ca_path = validate_mysql_ssl_ca_path(getattr(self, "mysql_ssl_ca_path", ""))

        sql = validate_read_only_sql(getattr(self, "sql_query", ""))
        parameters = normalize_bind_parameters(getattr(self, "bind_parameters", {}))
        max_rows = normalize_int(
            getattr(self, "max_rows", DEFAULT_MAX_ROWS),
            label="최대 조회 행 수",
            minimum=1,
            maximum=MAX_ALLOWED_ROWS,
        )
        api_timeout = normalize_int(
            getattr(self, "api_timeout_seconds", DEFAULT_API_TIMEOUT_SECONDS),
            label="API/DB 연결 제한시간",
            minimum=1,
            maximum=300,
        )
        query_timeout = normalize_int(
            getattr(self, "query_timeout_seconds", 60),
            label="SQL 읽기 제한시간",
            minimum=1,
            maximum=3600,
        )
        cluster_wait = normalize_int(
            getattr(self, "cluster_wait_seconds", DEFAULT_CLUSTER_WAIT_SECONDS),
            label="Cluster 전체 대기시간",
            minimum=1,
            maximum=1800,
        )
        poll_interval = normalize_int(
            getattr(self, "poll_interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS),
            label="Cluster 확인 간격",
            minimum=1,
            maximum=60,
        )

        host = ""
        try:
            # 의존성이 없으면 API를 호출하기 전에 즉시 안내합니다.
            mysql_connect = _load_mysql_connect()
            host, port, endpoint_database = await discover_mysql_endpoint(
                status_url=status_url,
                user_id=user_id,
                token=token,
                endpoint_key=endpoint_key,
                allowed_mysql_hosts=allowed_hosts,
                request_timeout_seconds=api_timeout,
                cluster_wait_seconds=cluster_wait,
                poll_interval_seconds=poll_interval,
            )
            rows, columns, truncated = await asyncio.to_thread(
                execute_mysql_query,
                host=host,
                port=port,
                database=endpoint_database,
                user_id=user_id,
                token=token,
                sql=sql,
                bind_parameters=parameters,
                max_rows=max_rows,
                connect_timeout_seconds=api_timeout,
                query_timeout_seconds=query_timeout,
                ssl_ca_path=ssl_ca_path,
                connect_fn=mysql_connect,
            )
        except Exception as exc:
            message = _safe_error_text(
                exc,
                [api_base_url, status_url, host, user_id, token, sql, *map(str, parameters.values())],
            )
            raise RuntimeError(f"Datalake 데이터 조회에 실패했습니다: {message}") from None

        table = build_dataframe(rows, columns)
        self.status = {
            "조회 행 수": len(rows),
            "컬럼 수": len(columns),
            "최대 행 제한 적용": truncated,
        }
        return table
