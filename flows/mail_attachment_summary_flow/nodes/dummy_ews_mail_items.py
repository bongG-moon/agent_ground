from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from lfx.custom import Component
from lfx.io import BoolInput, IntInput, Output, StrInput
from lfx.schema.dataframe import DataFrame


_MAIL_SAMPLES = (
    {
        "subject": "[프로젝트 Alpha] 주간 일정 및 담당 업무 공유",
        "sender": "김민수",
        "sender_email": "minsu.kim@example.internal",
        "received_time": "2026-07-22T09:15:00+09:00",
        "body": (
            "프로젝트 Alpha 주간 진행 상황을 공유합니다.\n"
            "설계 검토는 7월 24일까지 완료하고, 시험 계획서는 7월 25일 오전까지 회신해 주세요.\n"
            "담당자는 설계 검토 김민수, 시험 계획 취합 이서연입니다."
        ),
        "attachment_name": "alpha_weekly_action_items.txt",
        "attachment": (
            "Alpha 주간 실행 항목\n"
            "1. 설계 검토 완료 - 담당 김민수 - 기한 2026-07-24\n"
            "2. 시험 계획서 취합 - 담당 이서연 - 기한 2026-07-25\n"
            "3. 미확정 위험: 시험 장비 예약 일정 확인 필요"
        ),
    },
    {
        "subject": "설비 점검 결과 및 후속조치 요청",
        "sender": "박지영",
        "sender_email": "jiyoung.park@example.internal",
        "received_time": "2026-07-22T10:40:00+09:00",
        "body": (
            "금일 설비 정기 점검 결과를 전달합니다.\n"
            "온도 센서 2번의 편차가 기준을 초과하여 7월 23일 재점검이 필요합니다.\n"
            "재점검 결과와 교체 필요 여부를 회신해 주세요."
        ),
        "attachment_name": "equipment_follow_up.txt",
        "attachment": (
            "설비 점검 후속조치\n"
            "- 대상: 온도 센서 2번\n"
            "- 상태: 기준 편차 초과\n"
            "- 요청: 2026-07-23 재점검 후 교체 여부 결정\n"
            "- 담당: 설비기술팀"
        ),
    },
)


def _bounded(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def build_dummy_mail_rows(
    *,
    mail_count: Any = 2,
    include_attachments: bool = True,
    output_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    count = _bounded(mail_count, 2, 1, 10)
    root = Path(output_root) if output_root else Path(tempfile.mkdtemp(prefix="langflow-dummy-ews-"))
    root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []

    for index in range(1, count + 1):
        sample = _MAIL_SAMPLES[(index - 1) % len(_MAIL_SAMPLES)]
        mail_dir = root / f"mail_{index:03d}"
        mail_dir.mkdir(parents=True, exist_ok=True)
        common = {
            "mail_index": index,
            "mail_subject": sample["subject"],
            "sender": sample["sender"],
            "sender_email": sample["sender_email"],
            "received_time": sample["received_time"],
            "is_read": index % 2 == 0,
        }
        body_path = mail_dir / f"mail_{index:03d}_body.txt"
        body_path.write_text(
            "\n".join(
                [
                    f"메일 순번: {index}",
                    f"제목: {sample['subject']}",
                    f"보낸 사람: {sample['sender']} <{sample['sender_email']}>",
                    f"받은 시각: {sample['received_time']}",
                    "",
                    "메일 본문:",
                    str(sample["body"]),
                ]
            ),
            encoding="utf-8",
        )
        rows.append(
            {
                **common,
                "file_path": str(body_path),
                "file_name": body_path.name,
                "source_kind": "mail_body",
                "attachment_index": 0,
                "is_inline": False,
                "content_type": "text/plain",
                "drm_status": "not_applicable",
                "extraction_error": "",
            }
        )

        if include_attachments:
            attachment_path = mail_dir / f"001_{sample['attachment_name']}"
            attachment_path.write_text(str(sample["attachment"]), encoding="utf-8")
            rows.append(
                {
                    **common,
                    "file_path": str(attachment_path),
                    "file_name": str(sample["attachment_name"]),
                    "source_kind": "ews_attachment",
                    "attachment_index": 1,
                    "is_inline": False,
                    "content_type": "text/plain",
                    "drm_status": "pending",
                    "extraction_error": "",
                }
            )
    return rows


class DummyEwsMailItems(Component):
    display_name = "01T 테스트 EWS 메일·첨부 데이터"
    description = "EWS 연결 없이 실제 조회 결과와 같은 메일 본문·첨부 DataFrame과 로컬 파일을 만듭니다."
    icon = "MailCheck"
    name = "DummyEwsMailItems"

    inputs = [
        IntInput(
            name="mail_count",
            display_name="더미 메일 수",
            value=2,
            info="1~10통의 테스트 메일을 만듭니다.",
        ),
        BoolInput(
            name="include_attachments",
            display_name="더미 첨부파일 포함",
            value=True,
        ),
        StrInput(
            name="scenario_label",
            display_name="테스트 시나리오 표시",
            value="EWS 연결 없는 로컬 통합 테스트",
            info="상태 표시용이며 메일 내용에는 포함되지 않습니다.",
            advanced=True,
        ),
    ]

    outputs = [
        Output(
            name="mail_items",
            display_name="더미 메일 본문·첨부 항목",
            method="build_mail_items",
            types=["DataFrame"],
            cache=False,
        )
    ]

    def build_mail_items(self) -> DataFrame:
        rows = build_dummy_mail_rows(
            mail_count=getattr(self, "mail_count", 2),
            include_attachments=bool(getattr(self, "include_attachments", True)),
        )
        result = DataFrame(rows)
        self.status = result
        return result
