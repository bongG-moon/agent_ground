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
from lfx.io import (
    BoolInput,
    DataInput,
    DropdownInput,
    FileInput,
    IntInput,
    MessageTextInput,
    Output,
    SecretStrInput,
)
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
PROCESSING_MODE_AUTO = "자동(로컬 우선)"
PROCESSING_MODE_ALWAYS_DRM = "항상 DRM API"
PROCESSING_MODE_BYPASS_DRM = "DRM 미사용"
PROCESSING_MODES = [
    PROCESSING_MODE_AUTO,
    PROCESSING_MODE_ALWAYS_DRM,
    PROCESSING_MODE_BYPASS_DRM,
]
LOCAL_EXTRACTION_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".csv"}


class DrmTextExtractionError(RuntimeError):
    """토큰, endpoint 전체 주소와 문서 본문을 노출하지 않는 실행 오류입니다."""


class LocalTextExtractionError(RuntimeError):
    """문서 본문이나 로컬 절대경로를 노출하지 않는 로컬 추출 오류입니다."""


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


def _processing_mode(value: Any) -> str:
    text = _text_value(value).strip()
    aliases = {
        "": PROCESSING_MODE_AUTO,
        "auto": PROCESSING_MODE_AUTO,
        PROCESSING_MODE_AUTO.lower(): PROCESSING_MODE_AUTO,
        "always_drm": PROCESSING_MODE_ALWAYS_DRM,
        PROCESSING_MODE_ALWAYS_DRM.lower(): PROCESSING_MODE_ALWAYS_DRM,
        "bypass_drm": PROCESSING_MODE_BYPASS_DRM,
        PROCESSING_MODE_BYPASS_DRM.lower(): PROCESSING_MODE_BYPASS_DRM,
    }
    normalized = aliases.get(text.lower())
    if normalized is None:
        raise ValueError(f"처리 모드는 {', '.join(PROCESSING_MODES)} 중 하나여야 합니다.")
    return normalized


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


def _validate_local_text(text: str, filename: str, maximum_chars: int) -> str:
    normalized = text.replace("\x00", "").strip()
    if not normalized:
        raise LocalTextExtractionError(f"로컬에서 읽을 수 있는 텍스트가 없습니다: {filename}")
    if len(normalized) > maximum_chars:
        raise LocalTextExtractionError(f"로컬 추출 텍스트가 설정한 최대 크기를 초과했습니다: {filename}")
    control_count = sum(
        1 for character in normalized if ord(character) < 32 and character not in {"\n", "\r", "\t"}
    )
    if control_count > max(4, len(normalized) // 100):
        raise LocalTextExtractionError(f"로컬 추출 결과가 텍스트 형식이 아닙니다: {filename}")
    return normalized


def _decode_local_text(content: bytes, filename: str) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            return content.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise LocalTextExtractionError(f"로컬에서 파일 문자 인코딩을 판별하지 못했습니다: {filename}")


def _extract_local_text(content: bytes, filename: str, maximum_chars: int) -> tuple[str, str]:
    """현 Langflow Desktop에 포함된 parser로 일반 파일의 텍스트를 추출합니다."""

    extension = Path(filename).suffix.lower()
    if extension not in LOCAL_EXTRACTION_EXTENSIONS:
        raise LocalTextExtractionError(
            f"이 형식은 로컬 추출을 지원하지 않아 DRM API 처리가 필요합니다: {filename}"
        )

    try:
        if extension == ".pdf":
            pdf_reader = import_module("pypdf").PdfReader(io.BytesIO(content))
            if bool(getattr(pdf_reader, "is_encrypted", False)):
                raise LocalTextExtractionError(f"암호화되었거나 보호된 PDF입니다: {filename}")
            text = "\n\n".join(page.extract_text() or "" for page in pdf_reader.pages)
            parser_name = "local-pypdf"
        elif extension == ".docx":
            document = import_module("docx").Document(io.BytesIO(content))
            blocks = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
            for table in document.tables:
                for row in table.rows:
                    values = [cell.text.strip() for cell in row.cells]
                    if any(values):
                        blocks.append("\t".join(values))
            text = "\n\n".join(blocks)
            parser_name = "local-python-docx"
        elif extension == ".pptx":
            presentation = import_module("pptx").Presentation(io.BytesIO(content))
            blocks: list[str] = []
            for slide_index, slide in enumerate(presentation.slides, start=1):
                slide_blocks: list[str] = []
                for shape in slide.shapes:
                    shape_text = str(getattr(shape, "text", "") or "").strip()
                    if shape_text:
                        slide_blocks.append(shape_text)
                    if bool(getattr(shape, "has_table", False)):
                        for row in shape.table.rows:
                            values = [cell.text.strip() for cell in row.cells]
                            if any(values):
                                slide_blocks.append("\t".join(values))
                if slide_blocks:
                    blocks.append(f"[SLIDE {slide_index}]\n" + "\n".join(slide_blocks))
            text = "\n\n".join(blocks)
            parser_name = "local-python-pptx"
        elif extension == ".xlsx":
            workbook = import_module("openpyxl").load_workbook(
                io.BytesIO(content), read_only=True, data_only=True
            )
            try:
                blocks = []
                current_chars = 0
                for worksheet in workbook.worksheets:
                    sheet_lines = [f"[SHEET] {worksheet.title}"]
                    for row in worksheet.iter_rows(values_only=True):
                        values = ["" if value is None else str(value) for value in row]
                        if not any(value.strip() for value in values):
                            continue
                        line = "\t".join(values).rstrip()
                        current_chars += len(line) + 1
                        if current_chars > maximum_chars:
                            raise LocalTextExtractionError(
                                f"로컬 추출 텍스트가 설정한 최대 크기를 초과했습니다: {filename}"
                            )
                        sheet_lines.append(line)
                    blocks.append("\n".join(sheet_lines))
                text = "\n\n".join(blocks)
            finally:
                workbook.close()
            parser_name = "local-openpyxl"
        else:
            text, parser_name = _decode_local_text(content, filename)
    except LocalTextExtractionError:
        raise
    except ModuleNotFoundError:
        raise LocalTextExtractionError(
            f"로컬 추출에 필요한 Langflow 패키지가 설치되어 있지 않습니다: {filename}"
        ) from None
    except Exception:
        raise LocalTextExtractionError(
            f"로컬에서 파일을 해석하지 못했습니다. DRM 파일일 수 있습니다: {filename}"
        ) from None

    return _validate_local_text(text, filename, maximum_chars), parser_name


def _local_result(content: bytes, filename: str, maximum_chars: int) -> dict[str, Any]:
    text, parser_name = _extract_local_text(content, filename, maximum_chars)
    return {
        "file_name": filename,
        "extension": Path(filename).suffix.lower(),
        "source_bytes": len(content),
        "response_bytes": 0,
        "char_count": len(text),
        "encoding": parser_name,
        "http_status": None,
        "processing_path": "local",
        "drm_status": "not_required",
        "text": text,
    }


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
            "processing_path": "drm_api",
            "drm_status": "text_extracted",
            "text": text,
        }
    finally:
        _close_response(response)


def extract_document_texts(
    document_files: Any,
    drm_api_url: Any = "",
    drm_token: Any = "",
    employee_no: Any = "",
    allowed_drm_hosts: Any = "",
    *,
    processing_mode: Any = PROCESSING_MODE_AUTO,
    allow_insecure_http: Any = False,
    verify_tls: Any = True,
    timeout_seconds: Any = DEFAULT_TIMEOUT_SECONDS,
    max_files: Any = DEFAULT_MAX_FILES,
    max_file_size_mb: Any = DEFAULT_MAX_FILE_SIZE_MB,
    max_total_size_mb: Any = DEFAULT_MAX_TOTAL_SIZE_MB,
    max_response_mb: Any = DEFAULT_MAX_RESPONSE_MB,
    transport: Any | None = None,
) -> dict[str, Any]:
    """처리 모드에 따라 로컬 또는 DRM API로 문서 텍스트를 추출합니다."""

    files = _normalize_files(document_files)
    mode = _processing_mode(processing_mode)
    file_limit = _bounded_int(max_files, "최대 파일 수", 1, MAX_FILES)
    if not files:
        raise ValueError("처리할 문서 또는 이미지 파일을 한 개 이상 입력해 주세요.")
    if len(files) > file_limit:
        raise ValueError(f"업로드 파일 수가 최대 {file_limit}개를 초과했습니다.")

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

    local_results: dict[int, dict[str, Any]] = {}
    drm_indexes: list[int] = []
    for index, (content, filename) in enumerate(prepared):
        if mode == PROCESSING_MODE_ALWAYS_DRM:
            drm_indexes.append(index)
            continue
        try:
            local_results[index] = _local_result(content, filename, response_limit_bytes)
        except LocalTextExtractionError:
            if mode == PROCESSING_MODE_BYPASS_DRM:
                raise ValueError(
                    f"DRM 미사용 모드에서 로컬 텍스트 추출에 실패했습니다: {filename}"
                ) from None
            drm_indexes.append(index)

    drm_results: dict[int, dict[str, Any]] = {}
    if drm_indexes:
        token = _text_value(drm_token).strip()
        emp_no = _text_value(employee_no).strip()
        if not token:
            raise ValueError("DRM API 호출이 필요한 파일이 있어 DRM 토큰을 입력해야 합니다.")
        if not emp_no:
            raise ValueError("DRM API 호출이 필요한 파일이 있어 사번을 입력해야 합니다.")
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
        try:
            client = transport or import_module("requests")
        except ModuleNotFoundError as exc:
            raise ValueError("DRM API 호출에 필요한 requests 패키지가 설치되어 있지 않습니다.") from exc

        for index in drm_indexes:
            content, filename = prepared[index]
            drm_results[index] = _post_file(
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

    results = [local_results.get(index) or drm_results[index] for index in range(len(prepared))]
    return {
        "success": True,
        "data": results,
        "errors": [],
        "meta": {
            "file_count": len(results),
            "total_source_bytes": total_bytes,
            "total_response_bytes": sum(item["response_bytes"] for item in results),
            "total_char_count": sum(item["char_count"] for item in results),
            "processing_mode": mode,
            "local_file_count": sum(item["processing_path"] == "local" for item in results),
            "drm_file_count": sum(item["processing_path"] == "drm_api" for item in results),
            "network_called": bool(drm_indexes),
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
    drm_api_url: Any = "",
    drm_token: Any = "",
    employee_no: Any = "",
    allowed_drm_hosts: Any = "",
    *,
    processing_mode: Any = PROCESSING_MODE_AUTO,
    allow_insecure_http: Any = False,
    verify_tls: Any = True,
    timeout_seconds: Any = DEFAULT_TIMEOUT_SECONDS,
    max_file_size_mb: Any = DEFAULT_MAX_FILE_SIZE_MB,
    max_response_mb: Any = DEFAULT_MAX_RESPONSE_MB,
    output_root: str | Path | None = None,
    transport: Any | None = None,
) -> dict[str, Any]:
    """EWS 항목을 원본 경로로 통과시키거나 DRM 평문 TXT 경로로 반환합니다."""

    record = _data_payload(file_record)
    mode = _processing_mode(processing_mode)
    source_kind = str(record.get("source_kind") or "ews_attachment")
    source_path = Path(str(record.get("file_path") or ""))
    original_name = _safe_filename(record.get("file_name") or source_path.name, 1)
    if not source_path.is_file():
        raise FileNotFoundError(f"DRM 처리 입력 파일을 찾을 수 없습니다: {original_name}")

    if source_kind in {"mail_body", "extraction_error"}:
        record["drm_status"] = "not_applicable"
        record["drm_error"] = ""
        record["drm_text_char_count"] = 0
        record["local_text_char_count"] = 0
        record["processing_mode"] = mode
        record["processing_path"] = "original_file"
        record["local_probe_status"] = "not_applicable"
        return record

    per_file_bytes = _bounded_int(
        max_file_size_mb, "파일당 최대 크기", 1, MAX_FILE_SIZE_MB
    ) * 1024 * 1024
    response_limit_bytes = _bounded_int(
        max_response_mb, "파일당 최대 응답 크기", 1, MAX_RESPONSE_MB
    ) * 1024 * 1024
    content, checked_name = _read_file_bytes(
        {"file_path": str(source_path), "filename": original_name}, 1, per_file_bytes
    )

    if mode == PROCESSING_MODE_BYPASS_DRM:
        record.update(
            {
                "original_file_path": str(source_path),
                "original_file_name": checked_name,
                "drm_status": "bypassed_by_mode",
                "drm_error": "",
                "drm_text_char_count": 0,
                "processing_mode": mode,
                "processing_path": "original_file",
                "local_probe_status": "skipped_by_mode",
            }
        )
        return record

    if mode == PROCESSING_MODE_AUTO:
        try:
            local_item = _local_result(content, checked_name, response_limit_bytes)
        except LocalTextExtractionError:
            local_item = None
        if local_item is not None:
            record.update(
                {
                    "original_file_path": str(source_path),
                    "original_file_name": checked_name,
                    "drm_status": "not_required",
                    "drm_error": "",
                    "drm_text_char_count": 0,
                    "local_text_char_count": local_item["char_count"],
                    "local_parser": local_item["encoding"],
                    "processing_mode": mode,
                    "processing_path": "original_file",
                    "local_probe_status": "succeeded",
                }
            )
            return record

    result = extract_document_texts(
        [{"file_path": str(source_path), "filename": original_name}],
        drm_api_url,
        drm_token,
        employee_no,
        allowed_drm_hosts,
        processing_mode=PROCESSING_MODE_ALWAYS_DRM,
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
            "processing_mode": mode,
            "processing_path": "drm_api",
            "local_probe_status": (
                "not_run" if mode == PROCESSING_MODE_ALWAYS_DRM else "failed"
            ),
        }
    )
    return record


def format_extraction_message(result: dict[str, Any]) -> str:
    items = result.get("data") if isinstance(result, dict) else None
    if not isinstance(items, list) or not items:
        raise ValueError("추출된 문서 텍스트가 없습니다.")
    blocks = ["# 문서 텍스트 추출 결과"]
    total = len(items)
    for index, item in enumerate(items, start=1):
        blocks.append(
            "\n".join(
                [
                    f"[FILE {index}/{total}] {item['file_name']}",
                    f"처리 경로: {'로컬 추출' if item['processing_path'] == 'local' else 'DRM API'}",
                    f"문자 수: {item['char_count']:,}",
                    "",
                    str(item["text"]),
                    f"[END FILE {index}/{total}]",
                ]
            )
        )
    return "\n\n".join(blocks)


class DrmDocumentTextExtractor(Component):
    display_name = "문서 텍스트 추출 (DRM 자동)"
    description = "처리 모드에 따라 일반 파일은 로컬에서 읽고 DRM 파일만 API로 보내 평문을 반환합니다."
    icon = "FileLock2"
    name = "DrmDocumentTextExtractor"

    inputs = [
        FileInput(
            name="document_files",
            display_name="문서 파일",
            file_types=SUPPORTED_FILE_TYPES,
            info="PDF·Office·HWP·텍스트·CSV·일반 이미지를 처리 모드에 따라 로컬 또는 DRM API로 읽습니다.",
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
        DropdownInput(
            name="processing_mode",
            display_name="처리 모드",
            options=PROCESSING_MODES,
            value=PROCESSING_MODE_AUTO,
            info=(
                "자동은 PDF·DOCX·PPTX·XLSX·TXT·CSV를 로컬에서 먼저 읽고 실패한 파일만 DRM API로 보냅니다. "
                "그 밖의 형식은 DRM API로 처리합니다. 항상 DRM은 모든 파일을 API로 보내며, "
                "DRM 미사용은 네트워크 호출을 금지합니다."
            ),
            required=True,
            real_time_refresh=True,
        ),
        MessageTextInput(
            name="drm_api_url",
            display_name="DRM API 주소",
            info="DRM API 호출이 필요한 모드에서 사용할 전체 주소입니다.",
            required=False,
            value="",
        ),
        SecretStrInput(
            name="drm_token",
            display_name="DRM 토큰",
            info="DRM API 호출 시 Authorization: Bearer 헤더에 넣습니다. Flow JSON에는 저장하지 않습니다.",
            required=False,
            value="",
        ),
        SecretStrInput(
            name="employee_no",
            display_name="사번",
            info="DRM API 호출 시 empNo로 전달합니다. 개인 식별값이므로 비밀 입력으로 처리합니다.",
            required=False,
            value="",
        ),
        MessageTextInput(
            name="allowed_drm_hosts",
            display_name="허용 DRM 서버",
            info="DRM API 호출을 허용할 host만 입력합니다. 예: drm.example.internal 또는 .example.internal",
            required=False,
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
            info="로컬 추출 또는 DRM API 업로드를 허용할 파일 하나의 최대 크기입니다.",
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
            info="로컬 추출 텍스트와 DRM API 평문 응답을 이 크기까지만 읽습니다.",
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
            display_name="처리된 파일",
            method="build_processed_file",
            types=["Data"],
            cache=False,
            tool_mode=False,
        ),
    ]

    def update_build_config(
        self,
        build_config: dict[str, Any],
        field_value: Any,
        field_name: str | None = None,
    ) -> dict[str, Any]:
        """DRM 미사용 모드에서는 네트워크 설정 입력을 숨깁니다."""

        if field_name != "processing_mode":
            return build_config
        uses_drm = _processing_mode(field_value) != PROCESSING_MODE_BYPASS_DRM
        for name in (
            "drm_api_url",
            "drm_token",
            "employee_no",
            "allowed_drm_hosts",
            "allow_insecure_http",
            "verify_tls",
            "timeout_seconds",
        ):
            if name in build_config:
                build_config[name]["show"] = uses_drm
        return build_config

    def build_extracted_text(self) -> Message:
        result = extract_document_texts(
            document_files=getattr(self, "document_files", None),
            drm_api_url=getattr(self, "drm_api_url", ""),
            drm_token=getattr(self, "drm_token", ""),
            employee_no=getattr(self, "employee_no", ""),
            allowed_drm_hosts=getattr(self, "allowed_drm_hosts", ""),
            processing_mode=getattr(self, "processing_mode", PROCESSING_MODE_AUTO),
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
            f"로컬 {meta['local_file_count']} · DRM {meta['drm_file_count']} · "
            f"텍스트 {meta['total_char_count']:,}자"
        )
        return Message(text=format_extraction_message(result))

    def build_processed_file(self) -> Data:
        record = process_file_record(
            file_record=getattr(self, "file_record", None),
            drm_api_url=getattr(self, "drm_api_url", ""),
            drm_token=getattr(self, "drm_token", ""),
            employee_no=getattr(self, "employee_no", ""),
            allowed_drm_hosts=getattr(self, "allowed_drm_hosts", ""),
            processing_mode=getattr(self, "processing_mode", PROCESSING_MODE_AUTO),
            allow_insecure_http=getattr(self, "allow_insecure_http", False),
            verify_tls=getattr(self, "verify_tls", True),
            timeout_seconds=getattr(self, "timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
            max_file_size_mb=getattr(self, "max_file_size_mb", DEFAULT_MAX_FILE_SIZE_MB),
            max_response_mb=getattr(self, "max_response_mb", DEFAULT_MAX_RESPONSE_MB),
        )
        self.status = (
            f"처리 경로: {record.get('processing_path')} · DRM 상태: {record.get('drm_status')} · "
            f"{record.get('original_file_name') or record.get('file_name')}"
        )
        return Data(data=record)
