from __future__ import annotations

import json
import re
from importlib import import_module
from typing import Any
from urllib.parse import urljoin, urlsplit

from lfx.custom.custom_component.component import Component
from lfx.io import BoolInput, DropdownInput, IntInput, MessageTextInput, MultilineInput, Output, SecretStrInput
from lfx.schema.dataframe import DataFrame


ALLOWED_METHODS = {"GET", "POST"}
REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 3
MAX_TIMEOUT_SECONDS = 300
MAX_ROWS = 100_000
MAX_RESPONSE_BYTES = 100 * 1024 * 1024
HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
BLOCKED_HEADERS = {"host", "content-length", "transfer-encoding", "connection", "proxy-authorization"}
_MISSING = object()


def _text_value(value: Any) -> str:
    """일반 문자열과 SecretStr 값을 실제 입력 문자열로 변환합니다."""
    if value is None:
        return ""
    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        return str(getter() or "")
    text = getattr(value, "text", None)
    if isinstance(text, str):
        return text
    return str(value)


def _positive_int(value: Any, field_name: str, maximum: int) -> int:
    """정수 입력을 안전한 실행 범위로 검증합니다."""
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}은(는) 정수여야 합니다.") from exc
    if number < 1 or number > maximum:
        raise ValueError(f"{field_name}은(는) 1 이상 {maximum:,} 이하여야 합니다.")
    return number


def _bool_value(value: Any, field_name: str) -> bool:
    """BoolInput과 문자열 동의 값을 안전하게 해석합니다."""
    if isinstance(value, bool):
        return value
    text = _text_value(value).strip().lower()
    if text in {"true", "1", "yes", "y", "on"}:
        return True
    if text in {"false", "0", "no", "n", "off", ""}:
        return False
    raise ValueError(f"{field_name} 값은 true 또는 false여야 합니다.")


def _parsed_url(value: Any, field_name: str = "API 주소"):
    """호출 가능한 HTTP(S) URL을 검증하고 파싱 결과를 함께 반환합니다."""
    url = _text_value(value).strip()
    if not url:
        raise ValueError(f"{field_name}을(를) 입력해 주세요.")
    try:
        parsed = urlsplit(url)
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(f"{field_name}의 포트 형식이 올바르지 않습니다.") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError(f"{field_name}은(는) http 또는 https 형식이어야 합니다.")
    if not parsed.hostname:
        raise ValueError(f"{field_name}에서 서버 주소를 확인할 수 없습니다.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL 안에 사용자 이름이나 비밀번호를 넣을 수 없습니다.")
    if parsed.fragment:
        raise ValueError("API URL에는 #fragment를 넣을 수 없습니다.")
    return url, parsed


def _parse_json(value: Any, field_name: str, expected_type: type | None = None, *, blank_value: Any = None) -> Any:
    """화면 문자열을 JSON으로 파싱하고 필요한 최상위 자료형을 확인합니다."""
    text = _text_value(value).strip()
    if not text:
        return blank_value
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name}이 올바른 JSON이 아닙니다. {exc.lineno}행 {exc.colno}열을 확인해 주세요.") from exc
    if expected_type is not None and not isinstance(parsed, expected_type):
        type_label = "객체({})" if expected_type is dict else expected_type.__name__
        raise ValueError(f"{field_name}의 최상위 값은 JSON {type_label}여야 합니다.")
    return parsed


def _safe_headers(value: Any) -> dict[str, str]:
    """헤더 JSON을 requests에 전달 가능한 안전한 문자열 사전으로 변환합니다."""
    parsed = _parse_json(value, "요청 헤더 JSON", dict, blank_value={})
    result: dict[str, str] = {}
    for raw_name, raw_value in parsed.items():
        name = str(raw_name).strip()
        lowered = name.lower()
        if not HEADER_NAME_PATTERN.fullmatch(name):
            raise ValueError(f"요청 헤더 이름 '{name}'의 형식이 올바르지 않습니다.")
        if lowered in BLOCKED_HEADERS:
            raise ValueError(f"안전을 위해 '{name}' 헤더는 직접 지정할 수 없습니다.")
        if raw_value is None or isinstance(raw_value, (dict, list)):
            raise ValueError(f"요청 헤더 '{name}'의 값은 문자열이나 숫자여야 합니다.")
        text = str(raw_value)
        if "\r" in text or "\n" in text:
            raise ValueError(f"요청 헤더 '{name}'의 값에는 줄바꿈을 넣을 수 없습니다.")
        result[name] = text
    return result


def _extract_path(payload: Any, response_path: str) -> Any:
    """점으로 구분한 응답 경로를 엄격하게 따라가 실제 데이터만 꺼냅니다."""
    path = str(response_path or "").strip()
    if not path:
        return payload

    current = payload
    for token in [part.strip() for part in path.split(".") if part.strip()]:
        if isinstance(current, dict):
            if token not in current:
                raise ValueError(f"응답 경로 '{path}'에서 '{token}' 키를 찾을 수 없습니다.")
            current = current[token]
        elif isinstance(current, list) and token.isdigit():
            index = int(token)
            if index >= len(current):
                raise ValueError(f"응답 경로 '{path}'의 배열 위치 {index}가 범위를 벗어났습니다.")
            current = current[index]
        else:
            raise ValueError(f"응답 경로 '{path}'를 끝까지 따라갈 수 없습니다.")
    return current


def _table_cell(value: Any) -> Any:
    """중첩 JSON 값은 한 셀에서 안전하게 볼 수 있도록 JSON 문자열로 만듭니다."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _rows_from_json(value: Any) -> list[dict[str, Any]]:
    """JSON 값 하나를 DataFrame에 바로 넣을 수 있는 행 목록으로 변환합니다."""
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    rows: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            rows.append({str(key): _table_cell(cell) for key, cell in item.items()})
        else:
            rows.append({"value": _table_cell(item)})
    return rows


def _header_value(response: Any, name: str) -> str:
    """응답 헤더를 대소문자 차이와 관계없이 읽습니다."""
    headers = getattr(response, "headers", None)
    if not hasattr(headers, "get"):
        return ""
    direct = headers.get(name)
    if direct is not None:
        return str(direct)
    lowered = name.lower()
    for key, value in getattr(headers, "items", lambda: [])():
        if str(key).lower() == lowered:
            return str(value)
    return ""


def _read_response_bytes(response: Any, maximum_bytes: int) -> bytes:
    """응답을 사용자가 지정한 제한 크기까지만 읽습니다."""
    content_length = _header_value(response, "Content-Length").strip()
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError:
            declared_size = 0
        if declared_size > maximum_bytes:
            raise ValueError(f"API 응답이 설정한 최대 크기 {maximum_bytes:,}바이트를 초과합니다.")

    iterator = getattr(response, "iter_content", None)
    if callable(iterator):
        chunks: list[bytes] = []
        total = 0
        for chunk in iterator(chunk_size=64 * 1024):
            if not chunk:
                continue
            chunk_bytes = bytes(chunk)
            total += len(chunk_bytes)
            if total > maximum_bytes:
                raise ValueError(f"API 응답이 설정한 최대 크기 {maximum_bytes:,}바이트를 초과합니다.")
            chunks.append(chunk_bytes)
        return b"".join(chunks)

    raw = getattr(response, "content", b"") or b""
    raw_bytes = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
    if len(raw_bytes) > maximum_bytes:
        raise ValueError(f"API 응답이 설정한 최대 크기 {maximum_bytes:,}바이트를 초과합니다.")
    return raw_bytes


def _json_response(response: Any, maximum_bytes: int) -> Any:
    """HTTP 응답 본문을 JSON으로만 해석합니다."""
    raw = _read_response_bytes(response, maximum_bytes)
    if not raw:
        return None
    content_type = _header_value(response, "Content-Type").lower()
    if content_type and "json" not in content_type:
        raise ValueError("API 응답의 Content-Type이 JSON이 아닙니다.")
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("API 응답 본문이 올바른 JSON 형식이 아닙니다.") from exc


def _close_response(response: Any) -> None:
    """응답 객체가 close를 제공하면 네트워크 자원을 정리합니다."""
    closer = getattr(response, "close", None)
    if callable(closer):
        closer()


def _origin(parsed: Any) -> tuple[str, str, int]:
    """scheme, 정규화 host, 유효 port를 포함한 origin을 반환합니다."""
    scheme = str(parsed.scheme or "").lower()
    host = str(parsed.hostname or "").lower().rstrip(".")
    port = parsed.port or (443 if scheme == "https" else 80)
    return scheme, host, port


def _validate_redirect_target(current_url: str, target_url: str, original_origin: tuple[str, str, int]) -> str:
    """리다이렉트가 같은 서버 안에서 안전한 방향으로만 이동하는지 확인합니다."""
    joined = urljoin(current_url, target_url)
    validated, parsed = _parsed_url(joined, "리다이렉트 URL")
    current_parsed = urlsplit(current_url)
    if current_parsed.scheme.lower() == "https" and parsed.scheme.lower() != "https":
        raise ValueError("HTTPS에서 HTTP로 낮아지는 API 리다이렉트는 허용하지 않습니다.")
    if _origin(parsed) != original_origin:
        raise ValueError("다른 origin(서버·scheme·port)으로 이동하는 API 리다이렉트는 허용하지 않습니다.")
    return validated


def _request_without_unsafe_redirects(
    client: Any,
    method: str,
    url: str,
    headers: dict[str, str],
    query_params: dict[str, Any],
    body: Any,
    timeout_seconds: int,
) -> Any:
    """자동 리다이렉트를 끄고 같은 host의 GET 리다이렉트만 제한적으로 따라갑니다."""
    sender = getattr(client, "request", None)
    if not callable(sender):
        raise ValueError("HTTP 전송 객체에 request 함수가 없습니다.")

    original_origin = _origin(urlsplit(url))
    current_url = url
    current_query: dict[str, Any] | None = query_params

    for redirect_count in range(MAX_REDIRECTS + 1):
        request_options: dict[str, Any] = {
            "headers": headers,
            "params": current_query,
            "timeout": timeout_seconds,
            "verify": True,
            "allow_redirects": False,
            "stream": True,
        }
        if body is not _MISSING:
            request_options["json"] = body

        try:
            response = sender(method, current_url, **request_options)
        except Exception:
            raise ValueError("API 서버에 연결하지 못했습니다.") from None

        status_code = int(getattr(response, "status_code", 0) or 0)
        actual_url = str(getattr(response, "url", current_url) or current_url)
        try:
            _validate_redirect_target(current_url, actual_url, original_origin)
        except Exception:
            _close_response(response)
            raise

        if status_code not in REDIRECT_STATUS_CODES:
            return response

        if method != "GET":
            _close_response(response)
            raise ValueError("POST 요청의 리다이렉트는 중복 호출 위험 때문에 자동 실행하지 않습니다.")
        if redirect_count >= MAX_REDIRECTS:
            _close_response(response)
            raise ValueError(f"API 리다이렉트가 허용 횟수 {MAX_REDIRECTS}회를 초과했습니다.")
        location = _header_value(response, "Location").strip()
        if not location:
            _close_response(response)
            raise ValueError("API 리다이렉트 응답에 Location 헤더가 없습니다.")
        try:
            next_url = _validate_redirect_target(current_url, location, original_origin)
        finally:
            _close_response(response)
        current_url = next_url
        current_query = None

    raise ValueError("API 리다이렉트를 처리하지 못했습니다.")


def request_api_table(
    api_url: Any,
    http_method: Any = "GET",
    headers_json: Any = "{}",
    query_params_json: Any = "{}",
    body_json: Any = "",
    response_path: Any = "",
    timeout_seconds: Any = 30,
    max_response_bytes: Any = 10 * 1024 * 1024,
    max_rows: Any = 5000,
    allow_insecure_http: Any = False,
    transport: Any | None = None,
) -> list[dict[str, Any]]:
    """API를 한 번 호출하고 JSON 응답의 데이터 행만 반환합니다.

    transport는 테스트에서 가짜 HTTP 전송 객체를 주입하기 위한 선택 인자입니다.
    실제 Component 실행에서는 requests 모듈을 사용합니다.
    """
    url, parsed_url = _parsed_url(api_url)
    allow_http = _bool_value(allow_insecure_http, "HTTP API 사용 허용")
    if parsed_url.scheme.lower() == "http" and not allow_http:
        raise ValueError(
            "요청 헤더나 본문을 HTTP로 보낼 수 없습니다. HTTPS URL을 사용하거나, "
            "폐쇄된 테스트망에서만 'HTTP API 사용 허용'을 명시적으로 켜 주세요."
        )
    method = _text_value(http_method).strip().upper()
    if method not in ALLOWED_METHODS:
        raise ValueError(f"지원하지 않는 HTTP 방식입니다: {method or '(비어 있음)'}")

    headers = _safe_headers(headers_json)
    query_params = _parse_json(query_params_json, "URL 쿼리 파라미터 JSON", dict, blank_value={})
    body_text = _text_value(body_json).strip()
    body = _MISSING if not body_text else _parse_json(body_text, "요청 본문 JSON")
    if method == "GET" and body is not _MISSING:
        raise ValueError("GET 요청에는 요청 본문을 넣을 수 없습니다. URL 쿼리 파라미터를 사용해 주세요.")

    timeout = _positive_int(timeout_seconds, "제한 시간", MAX_TIMEOUT_SECONDS)
    response_limit = _positive_int(max_response_bytes, "최대 응답 크기", MAX_RESPONSE_BYTES)
    row_limit = _positive_int(max_rows, "최대 반환 행 수", MAX_ROWS)

    try:
        client = transport or import_module("requests")
    except ModuleNotFoundError as exc:
        raise ValueError("API 호출에 필요한 requests 패키지가 설치되어 있지 않습니다.") from exc
    response = _request_without_unsafe_redirects(
        client=client,
        method=method,
        url=url,
        headers=headers,
        query_params=query_params,
        body=body,
        timeout_seconds=timeout,
    )
    try:
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code < 200 or status_code >= 300:
            raise ValueError(f"API가 성공 상태를 반환하지 않았습니다: HTTP {status_code}")
        payload = _json_response(response, response_limit)
        selected = _extract_path(payload, _text_value(response_path))
        return _rows_from_json(selected)[:row_limit]
    finally:
        _close_response(response)


class SimpleApiTableRequest(Component):
    """일반 JSON API 한 곳을 호출하여 조회 결과 표만 반환하는 최소 단위 Component입니다."""

    display_name = "단순 API 테이블 조회"
    description = "HTTP API를 한 번 호출하고 지정한 JSON 응답 경로의 값을 데이터 테이블로 반환합니다."
    icon = "Webhook"
    name = "SimpleApiTableRequest"

    inputs = [
        MessageTextInput(
            name="api_url",
            display_name="API 주소",
            info="호출할 API의 전체 http 또는 https 주소입니다.",
            required=True,
        ),
        BoolInput(
            name="allow_insecure_http",
            display_name="HTTP API 사용 허용",
            info="기본값은 꺼짐입니다. HTTPS를 지원하지 않는 폐쇄된 테스트망에서만 명시적으로 켜세요.",
            value=False,
            advanced=True,
        ),
        DropdownInput(
            name="http_method",
            display_name="HTTP 방식",
            info="한 번 실행할 HTTP 요청 방식입니다.",
            options=["GET", "POST"],
            value="GET",
        ),
        SecretStrInput(
            name="headers_json",
            display_name="요청 헤더 JSON",
            info='인증값을 포함할 수 있어 비밀 입력으로 처리합니다. 예: {"Authorization":"Bearer ..."}',
            value="{}",
        ),
        MultilineInput(
            name="query_params_json",
            display_name="URL 쿼리 파라미터 JSON",
            info='URL 뒤에 붙일 query parameter 객체입니다. 예: {"page":1,"size":100}',
            value="{}",
        ),
        MultilineInput(
            name="body_json",
            display_name="요청 본문 JSON",
            info="POST 요청에 보낼 JSON입니다. 본문이 없으면 비워 두세요.",
            value="",
        ),
        MessageTextInput(
            name="response_path",
            display_name="응답 데이터 경로",
            info="표로 바꿀 JSON 값의 점 경로입니다. 비우면 전체 JSON을 사용하며, 틀린 경로를 자동 대체하지 않습니다.",
            value="",
        ),
        IntInput(
            name="timeout_seconds",
            display_name="제한 시간(초)",
            info="연결과 응답 읽기 작업에 적용되는 timeout입니다. 전체 wall-clock 제한은 아닙니다.",
            value=30,
            advanced=True,
        ),
        IntInput(
            name="max_response_bytes",
            display_name="최대 응답 크기(바이트)",
            info="응답이 이 크기를 넘으면 메모리 보호를 위해 중단합니다. 기본값은 10MiB입니다.",
            value=10 * 1024 * 1024,
            advanced=True,
        ),
        IntInput(
            name="max_rows",
            display_name="최대 반환 행 수",
            info="응답에서 앞부분 기준으로 반환할 최대 행 수입니다.",
            value=5000,
            advanced=True,
        ),
    ]

    outputs = [
        Output(
            name="data_table",
            display_name="데이터 테이블",
            method="build_data_table",
            types=["DataFrame"],
            cache=False,
            tool_mode=False,
        )
    ]

    def build_data_table(self) -> DataFrame:
        """API 조회 결과를 Langflow DataFrame으로 반환합니다."""
        rows = request_api_table(
            api_url=self.api_url,
            http_method=self.http_method,
            headers_json=self.headers_json,
            query_params_json=self.query_params_json,
            body_json=self.body_json,
            response_path=self.response_path,
            timeout_seconds=self.timeout_seconds,
            max_response_bytes=self.max_response_bytes,
            max_rows=self.max_rows,
            allow_insecure_http=self.allow_insecure_http,
        )
        result = DataFrame(rows)
        self.status = f"{len(result):,}행 · {len(result.columns):,}열"
        return result
