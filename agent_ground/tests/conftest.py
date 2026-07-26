from __future__ import annotations

"""Langflow 1.9.2 테스트가 사용자 Desktop 설정을 읽거나 수정하지 않게 격리합니다."""

import os
import tempfile
from pathlib import Path


os.environ.setdefault(
    "LANGFLOW_CONFIG_DIR",
    str(Path(tempfile.gettempdir()) / "agent-ground-langflow-1-9-2-test"),
)
