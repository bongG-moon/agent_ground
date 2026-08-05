from __future__ import annotations

import pytest

from app.uploads import UploadPolicyError, validate_upload


def test_validate_upload_accepts_supported_text_file() -> None:
    result = validate_upload(
        filename="memo.txt",
        content_type="text/plain",
        data=b"weekly trend report",
        max_bytes=1024,
    )

    assert result.safe_filename == "memo.txt"
    assert result.media_type == "text/plain"


def test_validate_upload_rejects_executable_even_with_spoofed_mime() -> None:
    with pytest.raises(UploadPolicyError, match="지원하지 않는 파일 형식"):
        validate_upload(
            filename="malware.exe",
            content_type="text/plain",
            data=b"MZ" + b"\x00" * 20,
            max_bytes=1024,
        )


def test_validate_upload_rejects_oversized_file() -> None:
    with pytest.raises(UploadPolicyError, match="파일 크기"):
        validate_upload(
            filename="large.pdf",
            content_type="application/pdf",
            data=b"%PDF" + b"x" * 20,
            max_bytes=8,
        )

