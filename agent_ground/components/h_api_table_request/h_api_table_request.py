from __future__ import annotations

import json
from importlib import import_module
from typing import Any
from urllib.parse import urlsplit

from lfx.custom.custom_component.component import Component
from lfx.io import BoolInput, IntInput, MessageTextInput, MultilineInput, Output, SecretStrInput
from lfx.schema.dataframe import DataFrame


MAX_TIMEOUT_SECONDS = 300
MAX_ROWS = 100_000
MAX_RESPONSE_BYTES = 10 * 1024 * 1024


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


def _validated_url(value: Any, *, allow_insecure_http: bool = False) -> str:
    """호출 가능한 HTTP(S) URL인지 확인합니다."""
    url = _text_value(value).strip()
    if not url:
        raise ValueError("H-API URL을 입력해 주세요.")
    try:
        parsed = urlsplit(url)
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("H-API URL의 포트 형식이 올바르지 않습니다.") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("H-API URL은 http 또는 https 형식이어야 합니다.")
    if not parsed.hostname:
        raise ValueError("H-API URL에서 서버 주소를 확인할 수 없습니다.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL 안에 사용자 이름이나 비밀번호를 넣을 수 없습니다.")
    if parsed.fragment:
        raise ValueError("H-API URL에는 #fragment를 넣을 수 없습니다.")
    if parsed.scheme.lower() == "http" and not allow_insecure_http:
        raise ValueError(
            "H-API 인증 토큰을 HTTP로 보낼 수 없습니다. HTTPS URL을 사용하거나, "
            "폐쇄된 테스트망에서만 'HTTP API 사용 허용'을 명시적으로 켜 주세요."
        )
    return url


def _url_origin(url: str) -> tuple[str, str, int]:
    """scheme, 정규화 host, 유효 port로 H-API 응답 origin을 비교합니다."""
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    return scheme, str(parsed.hostname or "").lower().rstrip("."), parsed.port or (443 if scheme == "https" else 80)


def _parse_bind_params(value: Any) -> list[Any]:
    """화면에 입력한 JSON 배열을 H-API bindParams 목록으로 변환합니다."""
    text = _text_value(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"요청 파라미터 JSON이 올바르지 않습니다. {exc.lineno}행 {exc.colno}열을 확인해 주세요."
        ) from exc
    if not isinstance(parsed, list):
        raise ValueError('요청 파라미터는 ["값1", "값2"] 형태의 JSON 배열이어야 합니다.')
    return parsed


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
    """응답을 제한 크기까지만 읽어 과도한 메모리 사용을 막습니다."""
    content_length = _header_value(response, "Content-Length").strip()
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError:
            declared_size = 0
        if declared_size > maximum_bytes:
            raise ValueError(f"H-API 응답이 허용 크기 {maximum_bytes:,}바이트를 초과합니다.")

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
                raise ValueError(f"H-API 응답이 허용 크기 {maximum_bytes:,}바이트를 초과합니다.")
            chunks.append(chunk_bytes)
        return b"".join(chunks)

    raw = getattr(response, "content", b"") or b""
    raw_bytes = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
    if len(raw_bytes) > maximum_bytes:
        raise ValueError(f"H-API 응답이 허용 크기 {maximum_bytes:,}바이트를 초과합니다.")
    return raw_bytes


def _json_response(response: Any) -> Any:
    """HTTP 응답 본문을 JSON으로만 해석합니다."""
    raw = _read_response_bytes(response, MAX_RESPONSE_BYTES)
    if not raw:
        return None
    content_type = _header_value(response, "Content-Type").lower()
    if content_type and "json" not in content_type:
        raise ValueError("H-API 응답의 Content-Type이 JSON이 아닙니다.")
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("H-API 응답 본문이 올바른 JSON 형식이 아닙니다.") from exc


def _close_response(response: Any) -> None:
    """응답 객체가 close를 제공하면 네트워크 자원을 정리합니다."""
    closer = getattr(response, "close", None)
    if callable(closer):
        closer()


def request_h_api_table(
    api_url: Any,
    h_api_token: Any,
    bind_params_json: Any,
    response_path: Any = "data.row",
    timeout_seconds: Any = 30,
    max_rows: Any = 5000,
    allow_insecure_http: Any = False,
    transport: Any | None = None,
) -> list[dict[str, Any]]:
    """H-API를 한 번 호출하고 응답 데이터 행만 반환합니다.

    transport는 테스트에서 가짜 HTTP 전송 객체를 주입하기 위한 선택 인자입니다.
    실제 Component 실행에서는 requests 모듈을 사용합니다.
    """
    allow_http = _bool_value(allow_insecure_http, "HTTP API 사용 허용")
    url = _validated_url(api_url, allow_insecure_http=allow_http)
    token = _text_value(h_api_token).strip()
    if not token:
        raise ValueError("H-API Token을 입력해 주세요.")
    bind_params = _parse_bind_params(bind_params_json)
    timeout = _positive_int(timeout_seconds, "제한 시간", MAX_TIMEOUT_SECONDS)
    row_limit = _positive_int(max_rows, "최대 반환 행 수", MAX_ROWS)

    try:
        client = transport or import_module("requests")
    except ModuleNotFoundError as exc:
        raise ValueError("H-API 호출에 필요한 requests 패키지가 설치되어 있지 않습니다.") from exc
    sender = getattr(client, "request", None)
    if not callable(sender):
        raise ValueError("HTTP 전송 객체에 request 함수가 없습니다.")

    try:
        response = sender(
            "POST",
            url,
            headers={
                "h-api-token": token,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json={"bindParams": bind_params},
            timeout=timeout,
            verify=True,
            allow_redirects=False,
            stream=True,
        )
    except Exception:
        raise ValueError("H-API 서버에 연결하지 못했습니다.") from None

    try:
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code < 200 or status_code >= 300:
            if 300 <= status_code < 400:
                raise ValueError("H-API가 다른 주소로 이동을 요청했습니다. 고정된 최종 URL을 입력해 주세요.")
            raise ValueError(f"H-API가 성공 상태를 반환하지 않았습니다: HTTP {status_code}")

        final_url = str(getattr(response, "url", url) or url)
        if _url_origin(final_url) != _url_origin(url):
            raise ValueError("H-API 응답의 최종 origin(서버·scheme·port)이 입력 주소와 다릅니다.")

        payload = _json_response(response)
        selected = _extract_path(payload, _text_value(response_path))
        return _rows_from_json(selected)[:row_limit]
    finally:
        _close_response(response)


class HApiTableRequest(Component):
    """H-API 한 곳을 호출하여 조회 결과 표만 반환하는 최소 단위 Component입니다."""

    display_name = "H-API 테이블 조회"
    description = "URL, Token, bindParams를 직접 받아 H-API를 한 번 호출하고 데이터 테이블만 반환합니다."
    icon = "Table2"
    name = "HApiTableRequest"

    inputs = [
        MessageTextInput(
            name="api_url",
            display_name="H-API 주소",
            info="호출할 H-API의 전체 http 또는 https 주소입니다.",
            required=True,
        ),
        BoolInput(
            name="allow_insecure_http",
            display_name="HTTP API 사용 허용",
            info="기본값은 꺼짐입니다. HTTPS를 지원하지 않는 폐쇄된 테스트망에서만 명시적으로 켜세요.",
            value=False,
            advanced=True,
        ),
        SecretStrInput(
            name="h_api_token",
            display_name="H-API 인증 토큰",
            info="h-api-token 요청 헤더에 넣을 인증 값입니다. 실행 상태와 출력에는 표시하지 않습니다.",
            required=True,
        ),
        MultilineInput(
            name="bind_params_json",
            display_name="요청 파라미터 JSON",
            info='H-API가 요구하는 순서대로 값을 넣은 JSON 배열입니다. 예: ["A12345", "D/A1", "B/G1"]',
            value="[]",
            required=True,
        ),
        MessageTextInput(
            name="response_path",
            display_name="응답 데이터 경로",
            info="표로 바꿀 JSON 값의 점 경로입니다. 경로가 틀리면 자동 대체하지 않고 오류로 중단합니다.",
            value="data.row",
        ),
        IntInput(
            name="timeout_seconds",
            display_name="제한 시간(초)",
            info="연결과 응답 읽기 작업에 적용되는 timeout입니다. 전체 wall-clock 제한은 아닙니다.",
            value=30,
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
        """H-API 조회 결과를 Langflow DataFrame으로 반환합니다."""
        rows = request_h_api_table(
            api_url=self.api_url,
            h_api_token=self.h_api_token,
            bind_params_json=self.bind_params_json,
            response_path=self.response_path,
            timeout_seconds=self.timeout_seconds,
            max_rows=self.max_rows,
            allow_insecure_http=self.allow_insecure_http,
        )
        result = DataFrame(rows)
        self.status = f"{len(result):,}행 · {len(result.columns):,}열"
        return result
