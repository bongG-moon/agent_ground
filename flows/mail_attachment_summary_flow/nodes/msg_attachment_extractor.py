from __future__ import annotations

import re
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

from lfx.custom import Component
from lfx.io import BoolInput, FileInput, IntInput, Output
from lfx.schema.dataframe import DataFrame


_OLE_COMPOUND_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
_MAX_MSG_FILES = 50
_MAX_MSG_SIZE_MB = 200
_MAX_ATTACHMENTS_PER_MSG = 200
_MAX_TOTAL_EXTRACTED_MB = 1024


class MsgExtractionError(RuntimeError):
    """사용자가 조치할 수 있는 MSG 분해 오류."""


class _HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())

    def text(self) -> str:
        return "\n".join(self.parts)


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


def _safe_filename(value: Any, fallback: str) -> str:
    name = Path(str(value or "")).name
    name = re.sub(r"[\x00-\x1f\x7f<>:\"/\\|?*]", "_", name).strip(" .")
    return name[:180] or fallback


def _safe_text(value: Any, limit: int = 20_000) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    return text.replace("\x00", "").strip()[:limit]


def _html_to_text(value: Any) -> str:
    html = _safe_text(value, 1_000_000)
    if not html:
        return ""
    parser = _HtmlTextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return html
    return parser.text()


def _default_msg_opener(path_or_bytes: str | bytes):
    try:
        import extract_msg
    except ImportError as exc:
        raise MsgExtractionError(
            "MSG 분해 의존성이 없습니다. Langflow Desktop 환경에 승인된 "
            "extract-msg 0.55.x를 설치하거나 _default_msg_opener를 사내 MSG 파서로 교체하세요."
        ) from exc
    return extract_msg.openMsg(path_or_bytes)


def _message_body_text(message: Any) -> str:
    body = _safe_text(getattr(message, "body", None), 1_000_000)
    if body:
        return body
    return _html_to_text(getattr(message, "htmlBody", None))


def _mail_body_document(message: Any, msg_name: str) -> str:
    fields = (
        ("MSG 파일", msg_name),
        ("제목", _safe_text(getattr(message, "subject", None), 2_000)),
        ("보낸 사람", _safe_text(getattr(message, "sender", None), 2_000)),
        ("받는 사람", _safe_text(getattr(message, "to", None), 4_000)),
        ("참조", _safe_text(getattr(message, "cc", None), 4_000)),
        ("보낸 시각", _safe_text(getattr(message, "date", None), 500)),
    )
    header = "\n".join(f"{label}: {value or '확인되지 않음'}" for label, value in fields)
    body = _message_body_text(message) or "[본문을 추출하지 못했습니다.]"
    return f"{header}\n\n메일 본문:\n{body}\n"


def _attachment_name(attachment: Any, index: int) -> str:
    for attribute in ("longFilename", "shortFilename", "name"):
        value = getattr(attachment, attribute, None)
        if value:
            return _safe_filename(value, f"attachment_{index:03d}.bin")
    return f"attachment_{index:03d}.bin"


def _write_notice(directory: Path, stem: str, message: str) -> Path:
    path = directory / f"{_safe_filename(stem, 'attachment')}_extraction_error.txt"
    path.write_text(message, encoding="utf-8")
    return path


def extract_msg_records(
    msg_files: Any,
    *,
    include_inline_images: bool = False,
    max_msg_files: Any = 10,
    max_msg_size_mb: Any = 50,
    max_attachments_per_msg: Any = 50,
    max_total_extracted_mb: Any = 200,
    opener: Callable[[str | bytes], Any] | None = None,
    output_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    """MSG 본문과 첨부를 Read File의 Server File Path 계약으로 평탄화한다."""

    files = _normalize_files(msg_files)
    file_limit = _clamp_int(max_msg_files, 10, 1, _MAX_MSG_FILES)
    per_file_limit = _clamp_int(max_msg_size_mb, 50, 1, _MAX_MSG_SIZE_MB) * 1024 * 1024
    attachment_limit = _clamp_int(max_attachments_per_msg, 50, 1, _MAX_ATTACHMENTS_PER_MSG)
    total_limit = _clamp_int(max_total_extracted_mb, 200, 1, _MAX_TOTAL_EXTRACTED_MB) * 1024 * 1024
    if not files:
        raise MsgExtractionError("업로드된 .msg 파일이 없습니다.")
    if len(files) > file_limit:
        raise MsgExtractionError(f"MSG 파일 수가 제한({file_limit}개)을 초과했습니다.")

    destination = Path(output_root) if output_root else Path(tempfile.mkdtemp(prefix="langflow-msg-"))
    destination.mkdir(parents=True, exist_ok=True)
    open_msg = opener or _default_msg_opener
    rows: list[dict[str, Any]] = []
    total_extracted = 0

    for msg_index, item in enumerate(files, 1):
        source_path = _path_from_item(item)
        if source_path is None or not source_path.is_file():
            raise MsgExtractionError(f"{msg_index}번째 MSG 업로드 파일을 읽을 수 없습니다.")
        msg_name = _safe_filename(source_path.name, f"mail_{msg_index:02d}.msg")
        if source_path.suffix.lower() != ".msg":
            raise MsgExtractionError(f"MSG 파일만 업로드할 수 있습니다: {msg_name}")
        size = source_path.stat().st_size
        if size > per_file_limit:
            raise MsgExtractionError(f"MSG 파일이 개별 크기 제한을 초과했습니다: {msg_name}")
        with source_path.open("rb") as handle:
            if handle.read(len(_OLE_COMPOUND_MAGIC)) != _OLE_COMPOUND_MAGIC:
                raise MsgExtractionError(
                    f"MSG 컨테이너 서명을 확인하지 못했습니다: {msg_name}. "
                    "MSG 자체가 DRM으로 보호됐다면 MSG 분해 전에 별도 해제가 필요합니다."
                )

        message = open_msg(str(source_path))
        msg_dir = destination / f"msg_{msg_index:02d}"
        msg_dir.mkdir(parents=True, exist_ok=True)
        try:
            subject = _safe_text(getattr(message, "subject", None), 2_000)
            body_text = _mail_body_document(message, msg_name)
            body_path = msg_dir / f"{msg_index:02d}_mail_body.txt"
            body_bytes = body_text.encode("utf-8")
            total_extracted += len(body_bytes)
            if total_extracted > total_limit:
                raise MsgExtractionError("MSG 본문과 첨부파일의 전체 추출 크기 제한을 초과했습니다.")
            body_path.write_bytes(body_bytes)
            rows.append(
                {
                    "file_path": str(body_path),
                    "file_name": body_path.name,
                    "source_kind": "mail_body",
                    "parent_msg": msg_name,
                    "mail_subject": subject,
                    "attachment_index": 0,
                    "is_inline": False,
                    "drm_status": "not_applicable",
                    "extraction_error": "",
                }
            )

            attachments = list(getattr(message, "attachments", None) or [])
            if len(attachments) > attachment_limit:
                raise MsgExtractionError(
                    f"첨부파일 수가 메일당 제한({attachment_limit}개)을 초과했습니다: {msg_name}"
                )
            for attachment_index, attachment in enumerate(attachments, 1):
                is_inline = bool(getattr(attachment, "hidden", False) or getattr(attachment, "cid", None))
                if is_inline and not include_inline_images:
                    continue
                attachment_name = _attachment_name(attachment, attachment_index)
                data = getattr(attachment, "data", None)
                if attachment_name.lower().endswith(".msg") or not isinstance(
                    data, (bytes, bytearray, memoryview)
                ):
                    notice = _write_notice(
                        msg_dir,
                        f"{attachment_index:03d}_{attachment_name}",
                        "내장 MSG 또는 비표준 첨부 형식은 자동 분해하지 않았습니다. 원본 메일에서 별도로 확인하세요.",
                    )
                    rows.append(
                        {
                            "file_path": str(notice),
                            "file_name": attachment_name,
                            "source_kind": "extraction_error",
                            "parent_msg": msg_name,
                            "mail_subject": subject,
                            "attachment_index": attachment_index,
                            "is_inline": is_inline,
                            "drm_status": "not_applicable",
                            "extraction_error": "embedded_or_non_binary_attachment",
                        }
                    )
                    continue

                content = bytes(data)
                total_extracted += len(content)
                if total_extracted > total_limit:
                    raise MsgExtractionError("MSG 본문과 첨부파일의 전체 추출 크기 제한을 초과했습니다.")
                output_name = f"{attachment_index:03d}_{attachment_name}"
                attachment_path = msg_dir / output_name
                attachment_path.write_bytes(content)
                rows.append(
                    {
                        "file_path": str(attachment_path),
                        "file_name": attachment_name,
                        "source_kind": "msg_attachment",
                        "parent_msg": msg_name,
                        "mail_subject": subject,
                        "attachment_index": attachment_index,
                        "is_inline": is_inline,
                        "drm_status": "pending",
                        "extraction_error": "",
                    }
                )
        finally:
            close = getattr(message, "close", None)
            if callable(close):
                close()

    return rows


class MsgAttachmentExtractor(Component):
    display_name = "01 MSG 본문·첨부파일 분해"
    description = "여러 Outlook MSG를 본문과 첨부파일 단위의 안전한 작업 경로로 분해합니다."
    icon = "MailOpen"
    name = "MsgAttachmentExtractor"

    inputs = [
        FileInput(
            name="msg_files",
            display_name="Outlook MSG 파일",
            file_types=["msg"],
            required=True,
            is_list=True,
            value=[],
            temp_file=True,
            info="여러 .msg 파일을 한 번에 업로드합니다. Outlook 또는 Microsoft Graph 연결은 사용하지 않습니다.",
        ),
        BoolInput(
            name="include_inline_images",
            display_name="인라인 이미지 포함",
            value=False,
            advanced=True,
            info="메일 서명 로고 같은 hidden/CID 이미지를 포함하려면 켭니다.",
        ),
        IntInput(name="max_msg_files", display_name="최대 MSG 수", value=10, advanced=True),
        IntInput(name="max_msg_size_mb", display_name="MSG당 최대 크기(MB)", value=50, advanced=True),
        IntInput(
            name="max_attachments_per_msg",
            display_name="메일당 최대 첨부 수",
            value=50,
            advanced=True,
        ),
        IntInput(
            name="max_total_extracted_mb",
            display_name="전체 추출 최대 크기(MB)",
            value=200,
            advanced=True,
        ),
    ]

    outputs = [
        Output(
            name="extracted_items",
            display_name="분해된 메일 항목",
            method="build_extracted_items",
            types=["DataFrame"],
        )
    ]

    def build_extracted_items(self) -> DataFrame:
        rows = extract_msg_records(
            getattr(self, "msg_files", None),
            include_inline_images=getattr(self, "include_inline_images", False),
            max_msg_files=getattr(self, "max_msg_files", 10),
            max_msg_size_mb=getattr(self, "max_msg_size_mb", 50),
            max_attachments_per_msg=getattr(self, "max_attachments_per_msg", 50),
            max_total_extracted_mb=getattr(self, "max_total_extracted_mb", 200),
        )
        self.status = f"MSG 분해 완료 · 항목 {len(rows)}개"
        return DataFrame(rows)
