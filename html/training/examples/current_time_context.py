from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# 현재 시간은 많은 업무 flow에서 "보이지 않는 입력값"처럼 쓰입니다.
# 예: "이번 주", "오늘 기준", "마감까지 남은 시간" 같은 상대 날짜 질문을 안정적으로 처리할 때 필요합니다.
from lfx.custom import Component
from lfx.io import BoolInput, DropdownInput, IntInput, Output
from lfx.schema import Data
from lfx.schema.message import Message


def _safe_timezone(name: str) -> ZoneInfo | timezone:
    # 서버에 timezone database가 없거나 잘못된 이름이 들어오면 flow가 실패할 수 있습니다.
    # Windows 서버에서는 tzdata package가 없으면 ZoneInfo("Asia/Seoul")도 실패할 수 있습니다.
    # 자주 쓰는 timezone은 고정 offset fallback을 두어 "현재 시간" 답변이 크게 틀어지지 않게 합니다.
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        fixed_offsets = {
            "Asia/Seoul": timezone(timedelta(hours=9), name="Asia/Seoul"),
            "UTC": timezone.utc,
            "America/New_York": timezone(timedelta(hours=-5), name="America/New_York"),
            "Europe/London": timezone.utc,
        }
        if name in fixed_offsets:
            return fixed_offsets[name]
        return timezone.utc


def _format_now(now: datetime, output_format: str, include_weekday: bool) -> str:
    # downstream Prompt Template에 넣기 쉬운 사람이 읽는 문자열을 만듭니다.
    # Data output에는 ISO 값이 따로 들어가므로, 여기서는 답변 품질용 문장에 집중합니다.
    weekday_names = ["월", "화", "수", "목", "금", "토", "일"]
    weekday = f" ({weekday_names[now.weekday()]})" if include_weekday else ""

    if output_format == "date_only":
        return f"{now:%Y-%m-%d}{weekday}"
    if output_format == "compact":
        return f"{now:%Y%m%d_%H%M%S}"
    if output_format == "korean":
        return f"{now:%Y년 %m월 %d일}{weekday} {now:%H시 %M분}"
    return now.isoformat(timespec="seconds")


def _safe_offset_days(value: object) -> int:
    # Langflow UI/API를 거치면 IntInput도 문자열처럼 들어올 수 있습니다.
    # 테스트용 offset이 너무 커지는 것도 막아 현재 시간 component가 예측 가능하게 동작하게 합니다.
    try:
        offset_days = int(value or 0)
    except Exception:
        offset_days = 0
    return max(-365, min(365, offset_days))


class CurrentTimeContext(Component):
    display_name = "Current Time Context"
    description = "Create current date/time context for prompts and routing decisions."
    icon = "Clock"
    name = "CurrentTimeContext"

    inputs = [
        DropdownInput(
            name="timezone_name",
            display_name="Timezone",
            info="현재 시간을 계산할 timezone입니다. 사내 서비스 기준이 한국이면 Asia/Seoul을 씁니다.",
            options=["Asia/Seoul", "UTC", "America/New_York", "Europe/London"],
            value="Asia/Seoul",
        ),
        DropdownInput(
            name="output_format",
            display_name="Output Format",
            info="Prompt에 넣을 표시 형식입니다. ISO는 시스템 처리, Korean은 사용자 답변에 적합합니다.",
            options=["iso", "korean", "date_only", "compact"],
            value="korean",
        ),
        BoolInput(
            name="include_weekday",
            display_name="Include Weekday",
            info="오늘/이번 주 질문에서 요일이 중요하면 켭니다.",
            value=True,
        ),
        IntInput(
            name="offset_days",
            display_name="Offset Days",
            info="어제=-1, 내일=1처럼 기준일을 테스트할 때 씁니다.",
            value=0,
            advanced=True,
        ),
    ]

    outputs = [
        Output(
            name="time_context",
            display_name="Time Context",
            method="build_time_context",
            types=["Data"],
            group_outputs=True,
        ),
        Output(
            name="time_message",
            display_name="Time Message",
            method="build_time_message",
            types=["Message"],
            group_outputs=True,
        ),
    ]

    def _now(self) -> datetime:
        # datetime.now()를 그대로 쓰면 서버 timezone에 따라 결과가 달라집니다.
        # 사용자가 선택한 timezone을 명시해서 prompt와 로그 기준을 고정합니다.
        tz = _safe_timezone(str(self.timezone_name or "Asia/Seoul"))

        offset_days = _safe_offset_days(getattr(self, "offset_days", 0))
        return datetime.now(tz=tz) + timedelta(days=offset_days)

    def build_time_context(self) -> Data:
        # Data output은 Smart Router, Prompt Template, JSON Operations가 읽기 쉬운 구조화 값입니다.
        now = self._now()
        formatted = _format_now(now, str(self.output_format or "korean"), bool(self.include_weekday))

        result = {
            "success": True,
            "timezone": str(self.timezone_name or "Asia/Seoul"),
            "now_iso": now.isoformat(timespec="seconds"),
            "date": now.date().isoformat(),
            "time": now.strftime("%H:%M:%S"),
            "weekday": now.strftime("%A"),
            "formatted": formatted,
            "offset_days": _safe_offset_days(getattr(self, "offset_days", 0)),
            "errors": [],
        }

        self.status = f"Current time: {formatted}"
        return Data(data=result, text=json.dumps(result, ensure_ascii=False, default=str))

    def build_time_message(self) -> Message:
        # Message output은 Prompt Template의 {current_time} 변수나 Chat Output 확인용으로 바로 연결합니다.
        data = self.build_time_context().data
        return Message(text=f"현재 기준 시간: {data['formatted']} / timezone={data['timezone']}")
