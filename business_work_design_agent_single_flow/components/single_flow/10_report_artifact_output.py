from __future__ import annotations

"""Terminal Data leaf that preserves the renderer artifact for every outcome."""

import json
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, Output
from lfx.schema import Data


def _payload(value: Any) -> dict[str, Any]:
    raw = getattr(value, "data", None)
    if isinstance(raw, dict):
        value = raw
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("[REPORT_ARTIFACT_INVALID] 게시 결과가 JSON object가 아닙니다.") from exc
    if not isinstance(value, dict):
        raise ValueError("[REPORT_ARTIFACT_INVALID] 게시 결과가 없습니다. 08 node 연결을 확인해 주세요.")
    return value


class ReportArtifactOutputComponent(Component):
    """10. Terminal output; no storage, no publish, and no mutation of HTML."""

    display_name = "10 보고서 결과 Data"
    description = "게시 여부와 무관하게 생성된 HTML 보고서를 API·테스트용 Data로 그대로 반환합니다."
    icon = "FileOutput"
    name = "ReportArtifactOutput"

    inputs = [DataInput(name="publish_result", display_name="게시 결과", required=True)]
    outputs = [Output(name="result", display_name="Report Artifact Data", method="preserve_artifact", types=["Data"])]

    def preserve_artifact(self) -> Data:
        result = _payload(self.publish_result)
        status = result.get("status")
        rendered = result.get("render_result")
        if status not in {"GENERATED_ONLY", "PUBLISH_FAILED", "PUBLISHED"} or not isinstance(rendered, dict):
            raise ValueError("[REPORT_ARTIFACT_INVALID] GENERATED_ONLY, PUBLISH_FAILED 또는 PUBLISHED의 게시 결과가 필요합니다. 08 node 출력을 확인해 주세요.")
        if rendered.get("ok") is not True or rendered.get("status") != "RENDERED" or not isinstance(rendered.get("html"), str):
            raise ValueError("[REPORT_ARTIFACT_INVALID] 보존할 RENDERED HTML이 없습니다. 07 node 출력을 확인해 주세요.")
        # Data wraps the exact publisher payload.  It deliberately does not copy,
        # alter, truncate, or remove render_result.html on publish failure.
        self.status = f"보고서 artifact 보존 완료 · {status}"
        return Data(data=result)
