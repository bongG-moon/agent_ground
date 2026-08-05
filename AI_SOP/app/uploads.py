from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class UploadPolicyError(ValueError):
    pass


ALLOWED_EXTENSIONS = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


@dataclass(frozen=True)
class ValidatedUpload:
    safe_filename: str
    media_type: str
    data: bytes


def _signature_matches(extension: str, data: bytes) -> bool:
    if extension == ".pdf":
        return data.startswith(b"%PDF")
    if extension == ".png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if extension in {".jpg", ".jpeg"}:
        return data.startswith(b"\xff\xd8\xff")
    if extension in {".docx", ".xlsx", ".pptx"}:
        return data.startswith(b"PK")
    return True


def validate_upload(*, filename: str, content_type: str | None, data: bytes, max_bytes: int) -> ValidatedUpload:
    safe_filename = Path(filename or "").name
    if not safe_filename or safe_filename in {".", ".."}:
        raise UploadPolicyError("유효한 파일명이 필요합니다.")
    extension = Path(safe_filename).suffix.lower()
    expected_type = ALLOWED_EXTENSIONS.get(extension)
    if expected_type is None:
        raise UploadPolicyError("지원하지 않는 파일 형식입니다.")
    if len(data) > max_bytes:
        raise UploadPolicyError(f"파일 크기가 허용 한도 {max_bytes} bytes를 초과했습니다.")
    if not data:
        raise UploadPolicyError("빈 파일은 업로드할 수 없습니다.")
    if not _signature_matches(extension, data):
        raise UploadPolicyError("파일 내용과 확장자가 일치하지 않습니다.")
    return ValidatedUpload(safe_filename=safe_filename, media_type=expected_type, data=data)

