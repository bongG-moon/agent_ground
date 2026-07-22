from __future__ import annotations

import json
from typing import Any

from lfx.custom import Component
from lfx.inputs.inputs import BoolInput, DropdownInput, HandleInput, MessageTextInput, MultilineInput
from lfx.schema.dataframe import DataFrame
from lfx.schema.message import Message
from lfx.template.field.base import Output


class _BlankDefaultDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


def dataframe_rows(value: Any) -> list[dict[str, Any]]:
    """Langflow DataFrame을 순서를 보존한 일반 dict 행으로 변환합니다."""

    if isinstance(value, DataFrame):
        return [dict(row) for row in value.to_dict(orient="records")]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            rows = to_dict(orient="records")
        except TypeError:
            rows = None
        if isinstance(rows, list) and all(isinstance(row, dict) for row in rows):
            return [dict(row) for row in rows]
    raise TypeError("입력은 Langflow DataFrame이어야 합니다.")


def format_dataframe(
    value: Any,
    *,
    mode: str,
    pattern: str,
    separator: str,
    clean_data: bool,
) -> str:
    rows = dataframe_rows(value)
    if mode == "Stringify":
        blocks: list[str] = []
        for row in rows:
            for key in ("text", "message", "content"):
                text = row.get(key)
                if text is not None and str(text).strip():
                    blocks.append(str(text).strip() if clean_data else str(text))
                    break
            else:
                blocks.append(json.dumps(row, ensure_ascii=False, default=str))
        return separator.join(blocks)

    if mode != "Parser":
        raise ValueError("포맷 모드는 Parser 또는 Stringify여야 합니다.")
    if not pattern:
        raise ValueError("Parser 모드에는 출력 템플릿이 필요합니다.")
    blocks = [pattern.format_map(_BlankDefaultDict(row)) for row in rows]
    if clean_data:
        blocks = ["\n".join(line.rstrip() for line in block.strip().splitlines()) for block in blocks]
    return separator.join(blocks)


class MailDataFrameFormatter(Component):
    display_name = "메일 DataFrame 내용 정리"
    description = "Read File 또는 Loop Done의 DataFrame을 정확한 타입 계약으로 Message로 변환합니다."
    icon = "Rows3"
    name = "MailDataFrameFormatter"

    inputs = [
        HandleInput(
            name="input_data",
            display_name="DataFrame 입력",
            info="Read File의 dataframe 또는 Loop의 done 출력을 연결합니다.",
            input_types=["DataFrame"],
            required=True,
        ),
        DropdownInput(
            name="mode",
            display_name="포맷 모드",
            options=["Parser", "Stringify"],
            value="Parser",
            required=True,
        ),
        MultilineInput(
            name="pattern",
            display_name="출력 템플릿",
            value="{text}",
            required=False,
        ),
        MessageTextInput(
            name="sep",
            display_name="행 구분자",
            value="\n\n--- FILE BOUNDARY ---\n\n",
            advanced=True,
        ),
        BoolInput(
            name="clean_data",
            display_name="빈 줄 정리",
            value=True,
            advanced=True,
        ),
    ]

    outputs = [
        Output(
            name="parsed_text",
            display_name="정리된 메일 내용",
            method="build_message",
            types=["Message"],
            cache=False,
        )
    ]

    def build_message(self) -> Message:
        text = format_dataframe(
            self.input_data,
            mode=str(getattr(self, "mode", "Parser") or "Parser"),
            pattern=str(getattr(self, "pattern", "") or ""),
            separator=str(getattr(self, "sep", "\n\n") or "\n\n"),
            clean_data=bool(getattr(self, "clean_data", True)),
        )
        self.status = f"DataFrame {len(dataframe_rows(self.input_data))}행 · {len(text):,}자"
        return Message(text=text)
