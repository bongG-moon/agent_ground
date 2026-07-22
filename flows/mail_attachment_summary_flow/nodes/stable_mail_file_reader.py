from __future__ import annotations

from pathlib import Path
from typing import Any

from lfx.components.files_and_knowledge.file import FileComponent
from lfx.io import Output


class StableMailFileReader(FileComponent):
    display_name = "04 원본 또는 DRM 평문 파일 읽기"
    description = (
        "Read File의 문서·이미지 파싱 기능은 유지하면서 출력 포트를 항상 DataFrame으로 고정합니다."
    )
    icon = "FileText"
    name = "StableMailFileReader"

    # Docling 2.x는 호환 확장자 목록에 TXT 등을 포함하더라도 실제 변환기에서
    # "File format not allowed"를 반환할 수 있습니다. 이 형식들은 Langflow의
    # 표준 텍스트 파서가 직접 처리하는 편이 더 정확하고 빠릅니다.
    STANDARD_PARSER_EXTENSIONS = {
        ".csv",
        ".htm",
        ".html",
        ".js",
        ".json",
        ".md",
        ".mdx",
        ".py",
        ".sh",
        ".sql",
        ".ts",
        ".tsx",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }

    outputs = [
        Output(
            display_name="파일 내용 DataFrame",
            name="dataframe",
            method="load_files",
            types=["DataFrame"],
            cache=False,
        )
    ]

    def update_outputs(
        self,
        frontend_node: dict[str, Any],
        field_name: str,
        field_value: Any,
    ) -> dict[str, Any]:
        """파일 수·확장자가 바뀌어도 04→05의 DataFrame 출력 계약을 유지합니다."""

        return frontend_node

    def process_files(self, file_list):
        """파일별로 표준 텍스트 파서와 Advanced Parser를 안전하게 선택합니다."""

        requested_advanced_mode = bool(getattr(self, "advanced_mode", False))
        processed = []
        try:
            for file in file_list:
                extension = Path(file.path).suffix.lower()
                self.advanced_mode = requested_advanced_mode and extension not in self.STANDARD_PARSER_EXTENSIONS
                processed.extend(super().process_files([file]))
        finally:
            self.advanced_mode = requested_advanced_mode
        return processed
