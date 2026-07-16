from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any, Callable

from lfx.custom import Component
from lfx.io import DataInput, Output
from lfx.schema.data import Data


class DrmAdapterNotConfigured(RuntimeError):
    """사내 DRM 구현이 아직 연결되지 않았음을 나타낸다."""


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, Data):
        return dict(value.data)
    if isinstance(value, dict):
        nested = value.get("data")
        return dict(nested) if isinstance(nested, dict) else dict(value)
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return dict(data)
    raise TypeError("DRM 입력은 file_path를 포함한 Data여야 합니다.")


def _safe_filename(value: Any) -> str:
    name = Path(str(value or "attachment.bin")).name
    name = re.sub(r"[\x00-\x1f\x7f<>:\"/\\|?*]", "_", name).strip(" .")
    return name[:180] or "attachment.bin"


def company_drm_unlock(source_path: Path, destination_path: Path) -> str:
    """사내 DRM SDK 연동 지점.

    구현 계약:
    1. source_path를 수정하거나 덮어쓰지 않는다.
    2. 해제 결과 또는 비보호 파일 복사본을 destination_path에 생성한다.
    3. 반환값은 `unlocked` 또는 `not_protected` 중 하나다.
    4. 키, 토큰, 파일 본문을 로그에 남기지 않는다.
    """

    raise DrmAdapterNotConfigured(
        "DRM 어댑터가 아직 구현되지 않았습니다. "
        "nodes/drm_unlock_adapter.py의 company_drm_unlock 함수에 승인된 사내 SDK를 연결하세요."
    )


def unlock_record(
    value: Any,
    *,
    unlocker: Callable[[Path, Path], str] | None = None,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    record = _payload(value)
    source_kind = str(record.get("source_kind") or "msg_attachment")
    source_path = Path(str(record.get("file_path") or ""))
    file_name = _safe_filename(record.get("file_name") or source_path.name)
    if not source_path.is_file():
        raise FileNotFoundError(f"DRM 처리 입력 파일을 찾을 수 없습니다: {file_name}")

    if source_kind in {"mail_body", "extraction_error"}:
        record["drm_status"] = "not_applicable"
        record["drm_error"] = ""
        return record

    destination_dir = Path(output_root) if output_root else Path(tempfile.mkdtemp(prefix="langflow-drm-"))
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination_path = destination_dir / file_name
    active_unlocker = unlocker or company_drm_unlock
    try:
        status = str(active_unlocker(source_path, destination_path) or "unlocked")
    except Exception as exc:
        if isinstance(exc, DrmAdapterNotConfigured):
            raise
        raise RuntimeError(f"DRM 처리에 실패했습니다: {file_name}") from exc
    if status not in {"unlocked", "not_protected"}:
        raise ValueError("DRM 구현 반환값은 unlocked 또는 not_protected여야 합니다.")
    if not destination_path.is_file():
        raise RuntimeError(f"DRM 구현이 출력 파일을 생성하지 않았습니다: {file_name}")
    if destination_path.resolve() == source_path.resolve():
        raise RuntimeError("DRM 결과는 원본과 다른 작업 파일이어야 합니다.")

    record["original_file_path"] = str(source_path)
    record["file_path"] = str(destination_path)
    record["drm_status"] = status
    record["drm_error"] = ""
    return record


class DrmUnlockAdapter(Component):
    display_name = "03 첨부파일 DRM 해제"
    description = "MSG 첨부파일을 사내 DRM SDK로 작업용 복사본에 해제합니다. 기본 구현은 fail-closed입니다."
    icon = "LockKeyholeOpen"
    name = "DrmUnlockAdapter"

    inputs = [
        DataInput(
            name="file_record",
            display_name="MSG 분해 항목",
            required=True,
            info="MsgAttachmentExtractor가 만든 file_path·source_kind·parent_msg 메타데이터를 받습니다.",
        )
    ]
    outputs = [
        Output(
            name="unlocked_file",
            display_name="DRM 처리된 파일",
            method="build_unlocked_file",
            types=["Data"],
        )
    ]

    def build_unlocked_file(self) -> Data:
        result = unlock_record(getattr(self, "file_record", None))
        self.status = f"DRM 상태: {result.get('drm_status')} · {result.get('file_name')}"
        return Data(data=result)
