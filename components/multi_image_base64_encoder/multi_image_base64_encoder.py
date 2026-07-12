from __future__ import annotations

import base64
import hashlib
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from lfx.custom import Component
from lfx.io import BoolInput, DropdownInput, FileInput, IntInput, Output
from lfx.schema import Data


_SUPPORTED_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".svg",
}
_FILE_TYPES = ["png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff", "svg"]
_DEFAULT_MAX_FILES = 20
_DEFAULT_MAX_FILE_MB = 8
_DEFAULT_MAX_TOTAL_MB = 12
_MAX_FILE_COUNT = 100
_MAX_FILE_MB = 100
_MAX_TOTAL_MB = 200
_SVG_NAMESPACE = "http://www.w3.org/2000/svg"
_XLINK_NAMESPACE = "http://www.w3.org/1999/xlink"
_XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"
_STATIC_SVG_TAGS = {
    "circle",
    "clippath",
    "defs",
    "desc",
    "ellipse",
    "g",
    "line",
    "lineargradient",
    "marker",
    "mask",
    "path",
    "pattern",
    "polygon",
    "polyline",
    "radialgradient",
    "rect",
    "stop",
    "svg",
    "symbol",
    "text",
    "title",
    "tspan",
    "use",
}
_UNSAFE_SVG_VALUE_MARKERS = (
    "javascript:",
    "vbscript:",
    "data:",
    "file:",
    "http:",
    "https:",
    "@import",
    "expression(",
    "behavior:",
    "url(",
)


class ImageProcessingError(Exception):
    """경로나 원문을 노출하지 않는 안전한 구조화 오류를 전달합니다."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        byte_size: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.details = details or {}
        self.byte_size = byte_size


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except Exception:
        number = default
    return max(minimum, min(maximum, number))


def _normalize_files(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return [item for item in value if item is not None and item != ""]
    return [value]


def _safe_filename(value: Any, position: int) -> str:
    text = str(value or "").strip()
    if text:
        text = Path(text).name
    text = re.sub(r"[\x00-\x1f\x7f]", "_", text).strip()
    return text[:200] or f"image_{position}"


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


def _filename_from_item(item: Any, position: int) -> str:
    path = _path_from_item(item)
    if path is not None:
        return _safe_filename(path.name, position)
    if isinstance(item, dict):
        for key in ("filename", "name"):
            if item.get(key):
                return _safe_filename(item[key], position)
    for attribute in ("filename", "name"):
        value = getattr(item, attribute, None)
        if value:
            return _safe_filename(value, position)
    return _safe_filename("", position)


def _read_file_bytes(item: Any, position: int, max_bytes: int) -> tuple[bytes, str]:
    """오류에 원본 경로를 남기지 않고 크기 제한보다 한 바이트까지만 읽습니다."""
    filename = _filename_from_item(item, position)
    path = _path_from_item(item)

    if path is not None:
        try:
            if not path.is_file():
                raise ImageProcessingError("file_unavailable", "업로드 파일을 읽을 수 없습니다.")
            declared_size = int(path.stat().st_size)
            if declared_size > max_bytes:
                raise ImageProcessingError(
                    "file_size_exceeded",
                    "파일이 개별 크기 제한을 초과했습니다.",
                    details={"actual_bytes": declared_size, "limit_bytes": max_bytes},
                    byte_size=declared_size,
                )
            with path.open("rb") as handle:
                content = handle.read(max_bytes + 1)
        except ImageProcessingError:
            raise
        except (OSError, PermissionError) as exc:
            raise ImageProcessingError("file_read_failed", "업로드 파일을 읽지 못했습니다.") from exc
    else:
        stream = item.get("file") if isinstance(item, dict) else getattr(item, "file", item)
        if isinstance(stream, (bytes, bytearray, memoryview)):
            content = bytes(stream[: max_bytes + 1])
        elif hasattr(stream, "read"):
            original_position: int | None = None
            try:
                if hasattr(stream, "tell"):
                    original_position = int(stream.tell())
                if hasattr(stream, "seek"):
                    stream.seek(0)
                content = stream.read(max_bytes + 1)
            except Exception as exc:
                raise ImageProcessingError("file_read_failed", "업로드 파일을 읽지 못했습니다.") from exc
            finally:
                if original_position is not None and hasattr(stream, "seek"):
                    try:
                        stream.seek(original_position)
                    except Exception:
                        pass
            if not isinstance(content, (bytes, bytearray, memoryview)):
                raise ImageProcessingError("file_read_failed", "파일 내용이 바이너리 형식이 아닙니다.")
            content = bytes(content)
        else:
            raise ImageProcessingError("file_unavailable", "지원하지 않는 파일 입력 형식입니다.")

    if len(content) > max_bytes:
        raise ImageProcessingError(
            "file_size_exceeded",
            "파일이 개별 크기 제한을 초과했습니다.",
            details={"actual_bytes_at_least": max_bytes + 1, "limit_bytes": max_bytes},
            byte_size=max_bytes + 1,
        )
    return content, filename


def _split_xml_name(name: str) -> tuple[str, str]:
    if name.startswith("{") and "}" in name:
        namespace, local_name = name[1:].split("}", 1)
        return namespace, local_name
    return "", name


def _validate_static_svg(content: bytes) -> None:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImageProcessingError("invalid_svg_encoding", "SVG는 UTF-8 정적 XML이어야 합니다.") from exc

    lowered = text.lower()
    if re.search(r"<!\s*(?:doctype|entity)\b", lowered) or "<?xml-stylesheet" in lowered:
        raise ImageProcessingError("unsafe_svg", "외부 엔터티나 스타일시트를 포함한 SVG는 허용되지 않습니다.")
    without_declaration = re.sub(r"^\s*<\?xml\s+[^?]*\?>", "", lowered, count=1)
    if "<?" in without_declaration:
        raise ImageProcessingError("unsafe_svg", "XML 처리 지시문을 포함한 SVG는 허용되지 않습니다.")

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ImageProcessingError("invalid_svg", "SVG XML 구조가 올바르지 않습니다.") from exc

    root_namespace, root_name = _split_xml_name(str(root.tag))
    if root_name.lower() != "svg" or root_namespace not in {"", _SVG_NAMESPACE}:
        raise ImageProcessingError("invalid_svg", "루트 요소가 정적 SVG 형식이 아닙니다.")

    stack: list[tuple[ET.Element, int]] = [(root, 1)]
    node_count = 0
    while stack:
        element, depth = stack.pop()
        node_count += 1
        if node_count > 10000 or depth > 64:
            raise ImageProcessingError("svg_complexity_exceeded", "SVG 구조가 안전 복잡도 제한을 초과했습니다.")

        namespace, local_name = _split_xml_name(str(element.tag))
        local_name_lower = local_name.lower()
        if namespace not in {"", _SVG_NAMESPACE} or local_name_lower not in _STATIC_SVG_TAGS:
            raise ImageProcessingError("unsafe_svg", "동적이거나 지원하지 않는 SVG 요소가 포함되어 있습니다.")

        for raw_name, raw_value in element.attrib.items():
            attribute_namespace, attribute_name = _split_xml_name(str(raw_name))
            attribute_name_lower = attribute_name.lower()
            if attribute_namespace not in {"", _XLINK_NAMESPACE, _XML_NAMESPACE}:
                raise ImageProcessingError("unsafe_svg", "지원하지 않는 SVG 속성 네임스페이스가 포함되어 있습니다.")
            if attribute_name_lower.startswith("on") or attribute_name_lower in {"src", "formaction"}:
                raise ImageProcessingError("unsafe_svg", "실행 가능한 SVG 속성이 포함되어 있습니다.")

            value = str(raw_value or "").strip()
            value_lower = re.sub(r"\s+", "", value.lower())
            if attribute_name_lower == "href" and (not value.startswith("#") or len(value) <= 1):
                raise ImageProcessingError("unsafe_svg", "SVG의 href 속성은 문서 내부 조각 참조만 허용됩니다.")
            if any(marker in value_lower for marker in _UNSAFE_SVG_VALUE_MARKERS) or value_lower.startswith("//"):
                raise ImageProcessingError("unsafe_svg", "외부 또는 실행 가능한 SVG 참조가 포함되어 있습니다.")

        children = list(element)
        for child in reversed(children):
            stack.append((child, depth + 1))


def _detect_raster_format(content: bytes) -> tuple[str, str, set[str]] | None:
    if (
        len(content) >= 24
        and content.startswith(b"\x89PNG\r\n\x1a\n")
        and content[12:16] == b"IHDR"
        and int.from_bytes(content[16:20], "big") > 0
        and int.from_bytes(content[20:24], "big") > 0
    ):
        return "png", "image/png", {".png"}

    stripped_jpeg = content.rstrip(b"\x00\x09\x0a\x0d\x20")
    if len(stripped_jpeg) >= 6 and stripped_jpeg.startswith(b"\xff\xd8\xff") and stripped_jpeg.endswith(b"\xff\xd9"):
        return "jpeg", "image/jpeg", {".jpg", ".jpeg"}

    if len(content) >= 13 and content[:6] in {b"GIF87a", b"GIF89a"}:
        width = int.from_bytes(content[6:8], "little")
        height = int.from_bytes(content[8:10], "little")
        if width > 0 and height > 0:
            return "gif", "image/gif", {".gif"}

    if len(content) >= 16 and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        declared_size = int.from_bytes(content[4:8], "little") + 8
        if 12 <= declared_size <= len(content):
            return "webp", "image/webp", {".webp"}

    if len(content) >= 26 and content.startswith(b"BM"):
        declared_size = int.from_bytes(content[2:6], "little")
        pixel_offset = int.from_bytes(content[10:14], "little")
        if 26 <= declared_size <= len(content) and 14 <= pixel_offset <= declared_size:
            return "bmp", "image/bmp", {".bmp"}

    if len(content) >= 8 and content[:4] in {b"II*\x00", b"MM\x00*"}:
        byte_order = "little" if content[:2] == b"II" else "big"
        first_ifd_offset = int.from_bytes(content[4:8], byte_order)
        if first_ifd_offset == 0 or first_ifd_offset < len(content):
            return "tiff", "image/tiff", {".tif", ".tiff"}

    return None


def _validate_image(
    content: bytes,
    extension: str,
    *,
    allow_svg: bool,
) -> tuple[str, str]:
    if extension not in _SUPPORTED_EXTENSIONS:
        raise ImageProcessingError(
            "unsupported_extension",
            "지원하지 않는 이미지 확장자입니다.",
            details={"supported_extensions": sorted(_SUPPORTED_EXTENSIONS)},
        )
    if not content:
        raise ImageProcessingError("empty_file", "빈 파일은 이미지로 변환할 수 없습니다.")

    if extension == ".svg":
        if not allow_svg:
            raise ImageProcessingError("svg_disabled", "SVG 입력은 기본적으로 비활성화되어 있습니다.")
        _validate_static_svg(content)
        return "svg", "image/svg+xml"

    detected = _detect_raster_format(content)
    if detected is None:
        raise ImageProcessingError("invalid_image_signature", "실제 이미지 파일 서명을 확인할 수 없습니다.")

    image_format, mime_type, matching_extensions = detected
    if extension not in matching_extensions:
        raise ImageProcessingError(
            "extension_signature_mismatch",
            "파일 확장자와 실제 이미지 파일 서명이 일치하지 않습니다.",
            details={
                "detected_format": image_format,
                "expected_extensions": sorted(matching_extensions),
            },
        )
    return image_format, mime_type


def _error_record(
    error: ImageProcessingError,
    *,
    index: int,
    position: int,
    filename: str,
    recoverable: bool,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "code": error.code,
        "message": error.safe_message,
        "stage": "image_validation",
        "index": index,
        "position": position,
        "filename": filename,
        "recoverable": recoverable,
    }
    if error.details:
        record["details"] = error.details
    return record


def encode_image_files(
    image_files: Any,
    *,
    output_format: str = "base64",
    error_policy: str = "reject_batch",
    allow_svg: bool = False,
    max_files: int = _DEFAULT_MAX_FILES,
    max_file_size_mb: int = _DEFAULT_MAX_FILE_MB,
    max_total_size_mb: int = _DEFAULT_MAX_TOTAL_MB,
) -> dict[str, Any]:
    """이미지를 검증하고 입력 순서를 유지한 Data 출력용 묶음으로 반환합니다."""
    files = _normalize_files(image_files)
    selected_format = output_format if output_format in {"base64", "data_url"} else "base64"
    selected_policy = error_policy if error_policy in {"reject_batch", "skip_invalid"} else "reject_batch"
    file_limit = _clamp_int(max_files, _DEFAULT_MAX_FILES, 1, _MAX_FILE_COUNT)
    per_file_mb = _clamp_int(max_file_size_mb, _DEFAULT_MAX_FILE_MB, 1, _MAX_FILE_MB)
    total_mb = _clamp_int(max_total_size_mb, _DEFAULT_MAX_TOTAL_MB, 1, _MAX_TOTAL_MB)
    per_file_bytes = per_file_mb * 1024 * 1024
    total_limit_bytes = total_mb * 1024 * 1024
    recoverable = selected_policy == "skip_invalid"

    items: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    total_read_bytes = 0
    encoded_source_bytes = 0

    if not files:
        errors.append(
            {
                "code": "no_files",
                "message": "변환할 이미지 파일이 없습니다.",
                "stage": "input_validation",
                "recoverable": False,
            }
        )

    for index, file_item in enumerate(files):
        position = index + 1
        filename = _filename_from_item(file_item, position)
        if index >= file_limit:
            errors.append(
                _error_record(
                    ImageProcessingError(
                        "file_count_exceeded",
                        "파일 개수 제한을 초과한 입력입니다.",
                        details={"limit": file_limit},
                    ),
                    index=index,
                    position=position,
                    filename=filename,
                    recoverable=recoverable,
                )
            )
            continue

        try:
            content, filename = _read_file_bytes(file_item, position, per_file_bytes)
            total_read_bytes += len(content)
            extension = Path(filename).suffix.lower()
            image_format, mime_type = _validate_image(content, extension, allow_svg=bool(allow_svg))

            if encoded_source_bytes + len(content) > total_limit_bytes:
                raise ImageProcessingError(
                    "total_size_exceeded",
                    "누적 이미지 크기가 전체 제한을 초과했습니다.",
                    details={
                        "candidate_bytes": len(content),
                        "accepted_bytes": encoded_source_bytes,
                        "limit_bytes": total_limit_bytes,
                    },
                )

            encoded = base64.b64encode(content).decode("ascii")
            encoded_value = f"data:{mime_type};base64,{encoded}" if selected_format == "data_url" else encoded
            items.append(
                {
                    "index": index,
                    "position": position,
                    "filename": filename,
                    "extension": extension,
                    "image_format": image_format,
                    "mime_type": mime_type,
                    "byte_size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "encoding": selected_format,
                    "value": encoded_value,
                }
            )
            encoded_source_bytes += len(content)
            if image_format == "svg":
                warnings.append(
                    {
                        "code": "static_svg_enabled",
                        "message": "정적 SVG 검사에 통과한 파일만 포함했습니다.",
                        "index": index,
                        "position": position,
                    }
                )
        except ImageProcessingError as error:
            if error.byte_size is not None:
                total_read_bytes += max(0, error.byte_size)
            errors.append(
                _error_record(
                    error,
                    index=index,
                    position=position,
                    filename=filename,
                    recoverable=recoverable,
                )
            )

    rejected_count = sum(1 for error in errors if isinstance(error.get("position"), int))
    batch_rejected = selected_policy == "reject_batch" and bool(errors)
    if batch_rejected:
        items = []
        encoded_source_bytes = 0

    success = bool(items) and (selected_policy == "skip_invalid" or not errors)
    if selected_policy == "skip_invalid" and errors and items:
        warnings.append(
            {
                "code": "invalid_files_skipped",
                "message": "검증에 실패한 파일을 제외하고 유효한 파일만 변환했습니다.",
                "skipped_count": rejected_count,
            }
        )

    result = {
        "success": success,
        "output_format": selected_format,
        "error_policy": selected_policy,
        "order_preserved": True,
        "input_count": len(files),
        "encoded_count": len(items),
        "rejected_count": rejected_count,
        "error_count": len(errors),
        "batch_rejected": batch_rejected,
        "total_read_bytes": total_read_bytes,
        "encoded_source_bytes": encoded_source_bytes,
        "items": items,
        "errors": errors,
        "warnings": warnings,
        "limits": {
            "max_files": file_limit,
            "max_file_size_bytes": per_file_bytes,
            "max_total_size_bytes": total_limit_bytes,
            "svg_enabled": bool(allow_svg),
        },
        "trace": [
            {
                "stage": "multi_image_base64_encoder",
                "status": "success" if success else "failed",
                "input_count": len(files),
                "encoded_count": len(items),
                "rejected_count": rejected_count,
                "error_count": len(errors),
            }
        ],
    }
    return result


class MultiImageBase64Encoder(Component):
    display_name = "다중 이미지 Base64 인코더"
    description = "여러 이미지 파일을 검증하고 입력 순서대로 Base64 또는 Data URL 목록으로 변환합니다."
    icon = "Images"
    name = "MultiImageBase64Encoder"

    inputs = [
        FileInput(
            name="image_files",
            display_name="이미지 파일",
            file_types=_FILE_TYPES,
            info=(
                "여러 이미지를 추가한 순서대로 처리합니다. 실제 파일 서명과 확장자가 일치해야 하며 "
                "SVG는 별도 옵션을 켠 경우에도 정적 SVG만 허용됩니다."
            ),
            required=True,
            is_list=True,
            value=[],
            temp_file=True,
        ),
        DropdownInput(
            name="output_format",
            display_name="출력 형식",
            options=["base64", "data_url"],
            value="base64",
            info="Base64는 인코딩 본문만 반환하고, Data URL은 data:image/...;base64 접두사를 함께 반환합니다.",
        ),
        DropdownInput(
            name="error_policy",
            display_name="오류 처리 방식",
            options=["reject_batch", "skip_invalid"],
            value="reject_batch",
            info=(
                "전체 거부(reject_batch)는 한 파일이라도 실패하면 결과를 모두 비우고, "
                "잘못된 파일 제외(skip_invalid)는 유효한 파일만 반환합니다."
            ),
        ),
        BoolInput(
            name="allow_svg",
            display_name="엄격한 정적 SVG 허용",
            value=False,
            advanced=True,
            info="기본값은 꺼짐입니다. 켜더라도 스크립트, 이벤트, 외부 참조, 동적 요소가 없는 정적 SVG만 허용합니다.",
        ),
        IntInput(
            name="max_files",
            display_name="최대 파일 수",
            value=_DEFAULT_MAX_FILES,
            advanced=True,
            info=f"한 번에 처리할 파일 수를 1~{_MAX_FILE_COUNT}개 범위에서 제한합니다.",
        ),
        IntInput(
            name="max_file_size_mb",
            display_name="파일당 최대 크기(MB)",
            value=_DEFAULT_MAX_FILE_MB,
            advanced=True,
            info=f"파일 하나의 크기를 1~{_MAX_FILE_MB}MB 범위에서 제한합니다.",
        ),
        IntInput(
            name="max_total_size_mb",
            display_name="전체 원본 최대 크기(MB)",
            value=_DEFAULT_MAX_TOTAL_MB,
            advanced=True,
            info=(
                f"전체 원본 크기를 1~{_MAX_TOTAL_MB}MB 범위에서 제한합니다. "
                "Base64 결과는 원본보다 약 33% 커지므로 기본값을 보수적으로 설정했습니다."
            ),
        ),
    ]

    outputs = [
        Output(
            name="encoded_images",
            display_name="인코딩된 이미지",
            method="encode_images",
            types=["Data"],
        )
    ]

    def encode_images(self) -> Data:
        result = encode_image_files(
            getattr(self, "image_files", None),
            output_format=getattr(self, "output_format", "base64"),
            error_policy=getattr(self, "error_policy", "reject_batch"),
            allow_svg=getattr(self, "allow_svg", False),
            max_files=getattr(self, "max_files", _DEFAULT_MAX_FILES),
            max_file_size_mb=getattr(self, "max_file_size_mb", _DEFAULT_MAX_FILE_MB),
            max_total_size_mb=getattr(self, "max_total_size_mb", _DEFAULT_MAX_TOTAL_MB),
        )
        if result["success"] and result["rejected_count"]:
            status_label = "일부 변환 완료"
        elif result["success"]:
            status_label = "변환 완료"
        else:
            status_label = "변환 실패"
        policy_label = "전체 거부" if result["error_policy"] == "reject_batch" else "잘못된 파일 제외"
        format_label = "Base64" if result["output_format"] == "base64" else "Data URL"
        self.status = (
            f"{status_label} · 입력 {result['input_count']}개 · 변환 {result['encoded_count']}개 · "
            f"제외 {result['rejected_count']}개 · 오류 {result['error_count']}건 · "
            f"출력 {format_label} · 처리 방식 {policy_label}"
        )
        return Data(data=result)
