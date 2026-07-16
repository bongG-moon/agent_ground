from __future__ import annotations

import io
import re
import tempfile
import warnings
from importlib import import_module
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from lfx.custom import Component
from lfx.io import BoolInput, DataInput, FileInput, IntInput, MessageTextInput, Output, SecretStrInput
from lfx.schema import Data, Message


SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".doc",
    ".docx",
    ".hwp",
    ".hwpx",
    ".txt",
    ".csv",
    ".rtf",
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".tif",
    ".tiff",
}
SUPPORTED_FILE_TYPES = [
    "bmp",
    "csv",
    "doc",
    "docx",
    "hwp",
    "hwpx",
    "jpeg",
    "jpg",
    "pdf",
    "png",
    "ppt",
    "pptx",
    "rtf",
    "tif",
    "tiff",
    "txt",
    "xls",
    "xlsx",
]
DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_MAX_FILES = 10
DEFAULT_MAX_FILE_SIZE_MB = 50
DEFAULT_MAX_TOTAL_SIZE_MB = 200
DEFAULT_MAX_RESPONSE_MB = 20
MAX_TIMEOUT_SECONDS = 600
MAX_FILES = 50
MAX_FILE_SIZE_MB = 500
MAX_TOTAL_SIZE_MB = 1000
MAX_RESPONSE_MB = 100
CHUNK_SIZE = 64 * 1024


class DrmTextExtractionError(RuntimeError):
    """토큰, endpoint 전체 주소와 문서 본문을 노출하지 않는 실행 오류입니다."""


def _text_value(value: Any) -> str:
    if value is None:
        return ""
    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        return str(getter() or "")
    text = getattr(value, "text", None)
    if isinstance(text, str):
        return text
    return str(value)


def _bool_value(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    text = _text_value(value).strip().lower()
    if text in {"true", "1", "yes", "y", "on"}:
        return True
    if text in {"false", "0", "no", "n", "off", ""}:
        return False
    raise ValueError(f"{field_name} 값은 true 또는 false여야 합니다.")


def _bounded_int(value: Any, field_name: str, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}은(는) 정수여야 합니다.") from exc
    if number < minimum or number > maximum:
        raise ValueError(f"{field_name}은(는) {minimum:,} 이상 {maximum:,} 이하여야 합니다.")
    return number


def _normalize_files(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return [item for item in value if item is not None and item != ""]
    return [value]


def _path_from_item(item: Any) -> Path | None:
    if isinstance(item, (str, Path)):
        return Path(item)
    if isinstance(item, dict):
        for key in ("path", "file_path"):
            value = item.get(key)
            if isinstance(value, (str, Path)) and str(value).strip():
                return Path(value)
        return None
    for attribute in ("path", "file_path"):
        value = getattr(item, attribute, None)
        if isinstance(value, (str, Path)) and str(value).strip():
            return Path(value)
    return None


def _safe_filename(value: Any, position: int) -> str:
    name = Path(str(value or "")).name
    name = re.sub(r"[\x00-\x1f\x7f<>:\"/\\|?*]", "_", name).strip(" .")
    return name[:180] or f"document_{position}.bin"


def _filename_from_item(item: Any, position: int) -> str:
    if isinstance(item, dict):
        for key in ("filename", "name"):
            if item.get(key):
                return _safe_filename(item[key], position)
    for attribute in ("filename", "name"):
        value = getattr(item, attribute, None)
        if value:
            return _safe_filename(value, position)
    path = _path_from_item(item)
    if path is not None:
        return _safe_filename(path.name, position)
    return _safe_filename("", position)


def _read_file_bytes(item: Any, position: int, maximum_bytes: int) -> tuple[bytes, str]:
    filename = _filename_from_item(item, position)
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise ValueError(f"지원하지 않는 파일 형식입니다: {filename}. 지원 형식: {supported}")

    path = _path_from_item(item)
    if path is not None:
        try:
            if not path.is_file():
                raise FileNotFoundError
            declared_size = int(path.stat().st_size)
            if declared_size > maximum_bytes:
                raise ValueError(f"파일이 개별 크기 제한을 초과했습니다: {filename}")
            with path.open("rb") as handle:
                content = handle.read(maximum_bytes + 1)
        except ValueError:
            raise
        except (OSError, PermissionError, FileNotFoundError):
            raise ValueError(f"업로드 파일을 읽을 수 없습니다: {filename}") from None
    else:
        stream = item.get("file") if isinstance(item, dict) else getattr(item, "file", item)
        if isinstance(stream, (bytes, bytearray, memoryview)):
            content = bytes(stream[: maximum_bytes + 1])
        elif hasattr(stream, "read"):
            original_position: int | None = None
            try:
                if hasattr(stream, "tell"):
                    original_position = int(stream.tell())
                if hasattr(stream, "seek"):
                    stream.seek(0)
                content = stream.read(maximum_bytes + 1)
            except Exception:
                raise ValueError(f"업로드 파일을 읽을 수 없습니다: {filename}") from None
            finally:
                if original_position is not None and hasattr(stream, "seek"):
                    try:
                        stream.seek(original_position)
                    except Exception:
                        pass
            if not isinstance(content, (bytes, bytearray, memoryview)):
                raise ValueError(f"업로드 파일이 바이너리 형식이 아닙니다: {filename}")
            content = bytes(content)
        else:
            raise ValueError(f"지원하지 않는 파일 입력 형식입니다: {filename}")

    if len(content) > maximum_bytes:
        raise ValueError(f"파일이 개별 크기 제한을 초과했습니다: {filename}")
    if not content:
        raise ValueError(f"빈 파일은 처리할 수 없습니다: {filename}")
    return content, filename


def _parse_api_url(value: Any, allow_insecure_http: Any) -> tuple[str, str, bool]:
    url = _text_value(value).strip()
    if not url:
        raise ValueError("DRM API 주소를 입력해 주세요.")
    try:
        parsed = urlsplit(url)
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("DRM API 주소의 포트 형식이 올바르지 않습니다.") from exc
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("DRM API 주소는 서버가 포함된 http 또는 https URL이어야 합니다.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("DRM API URL 안에 사용자 이름이나 비밀번호를 넣을 수 없습니다.")
    if parsed.fragment:
        raise ValueError("DRM API URL에는 #fragment를 넣을 수 없습니다.")
    allow_http = _bool_value(allow_insecure_http, "HTTP DRM API 사용 허용")
    if scheme == "http" and not allow_http:
        raise ValueError(
            "문서와 인증값을 HTTP로 보낼 수 없습니다. HTTPS URL을 사용하거나, "
            "폐쇄된 사내 테스트망에서만 'HTTP DRM API 사용 허용'을 켜 주세요."
        )
    return url, str(parsed.hostname).lower().rstrip("."), scheme == "https"


def _parse_allowed_hosts(value: Any) -> list[str]:
    text = _text_value(value).strip().lower()
    rules = [item.strip().rstrip(".") for item in re.split(r"[,\s]+", text) if item.strip()]
    if not rules:
        raise ValueError("허용 DRM 서버를 한 개 이상 입력해 주세요.")
    for rule in rules:
        host = rule[1:] if rule.startswith(".") else rule
        if not host or "://" in host or "/" in host or ":" in host:
            raise ValueError("허용 DRM 서버에는 scheme, path, port를 넣을 수 없습니다.")
    return rules


def _host_is_allowed(host: str, rules: list[str]) -> bool:
    for rule in rules:
        if rule.startswith(".") and (host == rule[1:] or host.endswith(rule)):
            return True
        if host == rule:
            return True
    return False


def _header_value(response: Any, name: str) -> str:
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
    content_length = _header_value(response, "Content-Length").strip()
    if content_length:
        try:
            declared = int(content_length)
        except ValueError:
            declared = 0
        if declared > maximum_bytes:
            raise DrmTextExtractionError("DRM API 응답이 설정한 최대 크기를 초과했습니다.")

    iterator = getattr(response, "iter_content", None)
    if callable(iterator):
        chunks: list[bytes] = []
        total = 0
        for chunk in iterator(chunk_size=CHUNK_SIZE):
            if not chunk:
                continue
            raw_chunk = bytes(chunk)
            total += len(raw_chunk)
            if total > maximum_bytes:
                raise DrmTextExtractionError("DRM API 응답이 설정한 최대 크기를 초과했습니다.")
            chunks.append(raw_chunk)
        return b"".join(chunks)

    raw = getattr(response, "content", b"") or b""
    content = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
    if len(content) > maximum_bytes:
        raise DrmTextExtractionError("DRM API 응답이 설정한 최대 크기를 초과했습니다.")
    return content


def _decode_text_response(response: Any, raw: bytes) -> tuple[str, str]:
    content_type = _header_value(response, "Content-Type")
    match = re.search(r"charset\s*=\s*[\"']?([^;\s\"']+)", content_type, flags=re.I)
    candidates: list[str] = []
    if match:
        candidates.append(match.group(1))
    try:
        apparent = str(getattr(response, "apparent_encoding", "") or "").strip()
    except Exception:
        # requests의 stream 응답은 iter_content 이후 apparent_encoding 접근이 실패할 수 있다.
        apparent = ""
    if apparent:
        candidates.append(apparent)
    candidates.extend(["utf-8-sig", "cp949", "euc-kr"])

    checked: set[str] = set()
    for encoding in candidates:
        key = encoding.lower()
        if key in checked:
            continue
        checked.add(key)
        try:
            return raw.decode(encoding), encoding
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def _close_response(response: Any) -> None:
    closer = getattr(response, "close", None)
    if callable(closer):
        closer()


def _post_file(
    *,
    client: Any,
    url: str,
    token: str,
    employee_no: str,
    filename: str,
    content: bytes,
    timeout_seconds: int,
    verify_tls: bool,
    max_response_bytes: int,
) -> dict[str, Any]:
    sender = getattr(client, "post", None)
    if not callable(sender):
        raise ValueError("HTTP 전송 객체에 post 함수가 없습니다.")

    with io.BytesIO(content) as stream:
        options = {
            "files": {"file": (filename, stream, "application/octet-stream")},
            "headers": {"Authorization": f"Bearer {token}"},
            "params": {"empNo": employee_no},
            "timeout": timeout_seconds,
            "verify": verify_tls,
            "allow_redirects": False,
            "stream": True,
        }
        try:
            if verify_tls:
                response = sender(url, **options)
            else:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    response = sender(url, **options)
        except Exception:
            raise DrmTextExtractionError(f"DRM API에 연결하지 못했습니다: {filename}") from None

    try:
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code < 200 or status_code >= 300:
            raise DrmTextExtractionError(
                f"DRM API가 성공 상태를 반환하지 않았습니다: {filename} · HTTP {status_code}"
            )
        raw = _read_response_bytes(response, max_response_bytes)
        if not raw:
            raise DrmTextExtractionError(f"DRM API가 빈 텍스트를 반환했습니다: {filename}")
        text, encoding = _decode_text_response(response, raw)
        if not text.strip():
            raise DrmTextExtractionError(f"DRM API가 빈 텍스트를 반환했습니다: {filename}")
        return {
            "file_name": filename,
            "extension": Path(filename).suffix.lower(),
            "source_bytes": len(content),
            "response_bytes": len(raw),
            "char_count": len(text),
            "encoding": encoding,
            "http_status": status_code,
            "text": text,
        }
    finally:
        _close_response(response)


def extract_document_texts(
    document_files: Any,
    drm_api_url: Any,
    drm_token: Any,
    employee_no: Any,
    allowed_drm_hosts: Any,
    *,
    allow_insecure_http: Any = False,
    verify_tls: Any = True,
    timeout_seconds: Any = DEFAULT_TIMEOUT_SECONDS,
    max_files: Any = DEFAULT_MAX_FILES,
    max_file_size_mb: Any = DEFAULT_MAX_FILE_SIZE_MB,
    max_total_size_mb: Any = DEFAULT_MAX_TOTAL_SIZE_MB,
    max_response_mb: Any = DEFAULT_MAX_RESPONSE_MB,
    transport: Any | None = None,
) -> dict[str, Any]:
    """업로드 문서를 DRM text API에 하나씩 보내고 평문 텍스트 결과를 반환합니다."""

    files = _normalize_files(document_files)
    file_limit = _bounded_int(max_files, "최대 파일 수", 1, MAX_FILES)
    if not files:
        raise ValueError("DRM API로 처리할 문서 또는 이미지 파일을 한 개 이상 입력해 주세요.")
    if len(files) > file_limit:
        raise ValueError(f"업로드 파일 수가 최대 {file_limit}개를 초과했습니다.")

    token = _text_value(drm_token).strip()
    emp_no = _text_value(employee_no).strip()
    if not token:
        raise ValueError("DRM 토큰을 입력해 주세요.")
    if not emp_no:
        raise ValueError("사번을 입력해 주세요.")
    if len(token) > 8192 or "\r" in token or "\n" in token:
        raise ValueError("DRM 토큰 형식이 올바르지 않습니다.")
    if len(emp_no) > 128 or "\r" in emp_no or "\n" in emp_no:
        raise ValueError("사번 형식이 올바르지 않습니다.")

    url, host, is_https = _parse_api_url(drm_api_url, allow_insecure_http)
    allowed_hosts = _parse_allowed_hosts(allowed_drm_hosts)
    if not _host_is_allowed(host, allowed_hosts):
        raise ValueError("DRM API 서버가 허용 서버 목록에 없습니다.")

    tls_verification = _bool_value(verify_tls, "TLS 인증서 검증")
    if not is_https:
        tls_verification = False
    timeout = _bounded_int(timeout_seconds, "제한 시간", 1, MAX_TIMEOUT_SECONDS)
    per_file_bytes = _bounded_int(
        max_file_size_mb, "파일당 최대 크기", 1, MAX_FILE_SIZE_MB
    ) * 1024 * 1024
    total_limit_bytes = _bounded_int(
        max_total_size_mb, "전체 최대 크기", 1, MAX_TOTAL_SIZE_MB
    ) * 1024 * 1024
    response_limit_bytes = _bounded_int(
        max_response_mb, "파일당 최대 응답 크기", 1, MAX_RESPONSE_MB
    ) * 1024 * 1024

    prepared: list[tuple[bytes, str]] = []
    total_bytes = 0
    for position, item in enumerate(files, start=1):
        content, filename = _read_file_bytes(item, position, per_file_bytes)
        total_bytes += len(content)
        if total_bytes > total_limit_bytes:
            raise ValueError("업로드 파일의 전체 크기가 설정한 제한을 초과했습니다.")
        prepared.append((content, filename))

    try:
        client = transport or import_module("requests")
    except ModuleNotFoundError as exc:
        raise ValueError("DRM API 호출에 필요한 requests 패키지가 설치되어 있지 않습니다.") from exc

    results = [
        _post_file(
            client=client,
            url=url,
            token=token,
            employee_no=emp_no,
            filename=filename,
            content=content,
            timeout_seconds=timeout,
            verify_tls=tls_verification,
            max_response_bytes=response_limit_bytes,
        )
        for content, filename in prepared
    ]
    return {
        "success": True,
        "data": results,
        "errors": [],
        "meta": {
            "file_count": len(results),
            "total_source_bytes": total_bytes,
            "total_response_bytes": sum(item["response_bytes"] for item in results),
            "total_char_count": sum(item["char_count"] for item in results),
            "order_preserved": True,
            "redirects_allowed": False,
            "response_type": "plain_text",
        },
    }


def _data_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, Data):
        return dict(value.data)
    if isinstance(value, dict):
        nested = value.get("data")
        return dict(nested) if isinstance(nested, dict) else dict(value)
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return dict(data)
    raise TypeError("EWS 파일 입력은 file_path를 포함한 Data여야 합니다.")


def process_file_record(
    file_record: Any,
    drm_api_url: Any,
    drm_token: Any,
    employee_no: Any,
    allowed_drm_hosts: Any,
    *,
    allow_insecure_http: Any = False,
    verify_tls: Any = True,
    timeout_seconds: Any = DEFAULT_TIMEOUT_SECONDS,
    max_file_size_mb: Any = DEFAULT_MAX_FILE_SIZE_MB,
    max_response_mb: Any = DEFAULT_MAX_RESPONSE_MB,
    output_root: str | Path | None = None,
    transport: Any | None = None,
) -> dict[str, Any]:
    """EWS 항목 한 개를 처리하고 Read File이 읽을 평문 TXT 경로를 반환합니다."""

    record = _data_payload(file_record)
    source_kind = str(record.get("source_kind") or "ews_attachment")
    source_path = Path(str(record.get("file_path") or ""))
    original_name = _safe_filename(record.get("file_name") or source_path.name, 1)
    if not source_path.is_file():
        raise FileNotFoundError(f"DRM 처리 입력 파일을 찾을 수 없습니다: {original_name}")

    if source_kind in {"mail_body", "extraction_error"}:
        record["drm_status"] = "not_applicable"
        record["drm_error"] = ""
        return record

    result = extract_document_texts(
        [{"file_path": str(source_path), "filename": original_name}],
        drm_api_url,
        drm_token,
        employee_no,
        allowed_drm_hosts,
        allow_insecure_http=allow_insecure_http,
        verify_tls=verify_tls,
        timeout_seconds=timeout_seconds,
        max_files=1,
        max_file_size_mb=max_file_size_mb,
        max_total_size_mb=max_file_size_mb,
        max_response_mb=max_response_mb,
        transport=transport,
    )
    item = result["data"][0]
    destination_dir = Path(output_root) if output_root else Path(
        tempfile.mkdtemp(prefix="langflow-drm-text-")
    )
    destination_dir.mkdir(parents=True, exist_ok=True)
    text_name = f"{Path(original_name).stem}_drm_text.txt"
    destination_path = destination_dir / _safe_filename(text_name, 1)
    try:
        destination_path.write_text(str(item["text"]), encoding="utf-8")
    except OSError as exc:
        raise DrmTextExtractionError(f"DRM 평문 작업 파일을 저장하지 못했습니다: {original_name}") from exc

    record.update(
        {
            "original_file_path": str(source_path),
            "original_file_name": original_name,
            "file_path": str(destination_path),
            "file_name": destination_path.name,
            "content_type": "text/plain",
            "drm_status": "text_extracted",
            "drm_error": "",
            "drm_response_encoding": item["encoding"],
            "drm_response_bytes": item["response_bytes"],
            "drm_text_char_count": item["char_count"],
        }
    )
    return record


def format_extraction_message(result: dict[str, Any]) -> str:
    items = result.get("data") if isinstance(result, dict) else None
    if not isinstance(items, list) or not items:
        raise ValueError("추출된 문서 텍스트가 없습니다.")
    blocks = ["# DRM 문서 텍스트 추출 결과"]
    total = len(items)
    for index, item in enumerate(items, start=1):
        blocks.append(
            "\n".join(
                [
                    f"[FILE {index}/{total}] {item['file_name']}",
                    f"문자 수: {item['char_count']:,}",
                    "",
                    str(item["text"]),
                    f"[END FILE {index}/{total}]",
                ]
            )
        )
    return "\n\n".join(blocks)


class DrmDocumentTextExtractor(Component):
    display_name = "DRM 문서 텍스트 추출"
    description = "문서·이미지를 DRM text API에 전송해 평문 Message 또는 EWS용 TXT 작업 파일로 반환합니다."
    icon = "FileLock2"
    name = "DrmDocumentTextExtractor"

    inputs = [
        FileInput(
            name="document_files",
            display_name="문서 파일",
            file_types=SUPPORTED_FILE_TYPES,
            info="PDF·Office·HWP·텍스트·CSV·일반 이미지를 입력 순서대로 DRM API에 전송합니다.",
            required=False,
            is_list=True,
            value=[],
            temp_file=True,
        ),
        DataInput(
            name="file_record",
            display_name="EWS 파일 항목",
            info="EWS Flow에서 file_path·file_name·source_kind가 포함된 Data 한 건을 연결합니다.",
            required=False,
            advanced=True,
        ),
        MessageTextInput(
            name="drm_api_url",
            display_name="DRM API 주소",
            info="파일을 multipart/form-data의 file 필드로 받을 DRM text API 전체 주소입니다.",
            required=True,
            value="",
        ),
        SecretStrInput(
            name="drm_token",
            display_name="DRM 토큰",
            info="Authorization: Bearer 헤더에 넣을 토큰입니다. Flow JSON 기본값에는 저장하지 않습니다.",
            required=True,
            value="",
        ),
        SecretStrInput(
            name="employee_no",
            display_name="사번",
            info="DRM API의 empNo 쿼리 파라미터로 전달합니다. 개인 식별값이므로 비밀 입력으로 처리합니다.",
            required=True,
            value="",
        ),
        MessageTextInput(
            name="allowed_drm_hosts",
            display_name="허용 DRM 서버",
            info="업로드를 허용할 host만 쉼표로 입력합니다. 예: drm.example.internal 또는 .example.internal",
            required=True,
            value="",
        ),
        BoolInput(
            name="allow_insecure_http",
            display_name="HTTP DRM API 사용 허용",
            info="기본값은 꺼짐입니다. HTTPS를 지원하지 않는 폐쇄된 사내 테스트망에서만 켜세요.",
            value=False,
            advanced=True,
        ),
        BoolInput(
            name="verify_tls",
            display_name="TLS 인증서 검증",
            info="HTTPS 인증서를 검증합니다. 사내 CA를 설치한 운영 환경에서는 켠 상태를 유지하세요.",
            value=True,
            advanced=True,
        ),
        IntInput(
            name="timeout_seconds",
            display_name="제한 시간(초)",
            info="파일 하나의 연결·응답 읽기 timeout입니다. 1~600초 범위입니다.",
            value=DEFAULT_TIMEOUT_SECONDS,
            advanced=True,
        ),
        IntInput(
            name="max_files",
            display_name="최대 파일 수",
            info="한 번 실행에서 처리할 파일 수를 1~50개로 제한합니다.",
            value=DEFAULT_MAX_FILES,
            advanced=True,
        ),
        IntInput(
            name="max_file_size_mb",
            display_name="파일당 최대 크기(MB)",
            info="DRM API에 업로드할 파일 하나의 최대 크기입니다.",
            value=DEFAULT_MAX_FILE_SIZE_MB,
            advanced=True,
        ),
        IntInput(
            name="max_total_size_mb",
            display_name="전체 최대 크기(MB)",
            info="한 번 실행에서 메모리에 읽을 전체 파일의 최대 크기입니다.",
            value=DEFAULT_MAX_TOTAL_SIZE_MB,
            advanced=True,
        ),
        IntInput(
            name="max_response_mb",
            display_name="파일당 최대 응답 크기(MB)",
            info="DRM API의 평문 응답을 이 크기까지만 읽습니다.",
            value=DEFAULT_MAX_RESPONSE_MB,
            advanced=True,
        ),
    ]

    outputs = [
        Output(
            name="extracted_text",
            display_name="추출된 문서 텍스트",
            method="build_extracted_text",
            types=["Message"],
            cache=False,
            tool_mode=False,
        ),
        Output(
            name="processed_file",
            display_name="DRM 평문 작업 파일",
            method="build_processed_file",
            types=["Data"],
            cache=False,
            tool_mode=False,
        ),
    ]

    def build_extracted_text(self) -> Message:
        result = extract_document_texts(
            document_files=getattr(self, "document_files", None),
            drm_api_url=getattr(self, "drm_api_url", ""),
            drm_token=getattr(self, "drm_token", ""),
            employee_no=getattr(self, "employee_no", ""),
            allowed_drm_hosts=getattr(self, "allowed_drm_hosts", ""),
            allow_insecure_http=getattr(self, "allow_insecure_http", False),
            verify_tls=getattr(self, "verify_tls", True),
            timeout_seconds=getattr(self, "timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
            max_files=getattr(self, "max_files", DEFAULT_MAX_FILES),
            max_file_size_mb=getattr(self, "max_file_size_mb", DEFAULT_MAX_FILE_SIZE_MB),
            max_total_size_mb=getattr(self, "max_total_size_mb", DEFAULT_MAX_TOTAL_SIZE_MB),
            max_response_mb=getattr(self, "max_response_mb", DEFAULT_MAX_RESPONSE_MB),
        )
        meta = result["meta"]
        self.status = (
            f"추출 완료 · 파일 {meta['file_count']}개 · "
            f"응답 {meta['total_response_bytes']:,}바이트 · 텍스트 {meta['total_char_count']:,}자"
        )
        return Message(text=format_extraction_message(result))

    def build_processed_file(self) -> Data:
        record = process_file_record(
            file_record=getattr(self, "file_record", None),
            drm_api_url=getattr(self, "drm_api_url", ""),
            drm_token=getattr(self, "drm_token", ""),
            employee_no=getattr(self, "employee_no", ""),
            allowed_drm_hosts=getattr(self, "allowed_drm_hosts", ""),
            allow_insecure_http=getattr(self, "allow_insecure_http", False),
            verify_tls=getattr(self, "verify_tls", True),
            timeout_seconds=getattr(self, "timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
            max_file_size_mb=getattr(self, "max_file_size_mb", DEFAULT_MAX_FILE_SIZE_MB),
            max_response_mb=getattr(self, "max_response_mb", DEFAULT_MAX_RESPONSE_MB),
        )
        self.status = (
            f"DRM 상태: {record.get('drm_status')} · "
            f"{record.get('original_file_name') or record.get('file_name')}"
        )
        return Data(data=record)
