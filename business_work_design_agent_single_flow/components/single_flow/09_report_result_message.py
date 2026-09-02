from __future__ import annotations

"""Create the short, human-readable Playground result for the single Flow."""

import json
import re
import urllib.parse
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, Output
from lfx.schema import Message


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SENSITIVE = re.compile(r"(?i)(?:mongodb(?:\+srv)?://[^\s,;]+|(?:api[_ -]?key|token|secret|password|authorization|bearer)\s*[:=]\s*[^\s,;]+)")


def _payload(value: Any) -> dict[str, Any]:
    raw = getattr(value, "data", None)
    if isinstance(raw, dict):
        value = raw
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"message": value}
        return parsed if isinstance(parsed, dict) else {"message": value}
    return {}


def _safe_text(value: Any, limit: int = 500) -> str:
    return _SENSITIVE.sub("[민감정보 제거]", re.sub(r"\s+", " ", str(value or "").strip()))[:limit]


def _safe_url(value: Any) -> str | None:
    url = str(value or "").strip()
    if not url or _CONTROL.search(url) or any(character in url for character in " <>\"'`()[]"):
        return None
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        return None
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


class ReportResultMessageComponent(Component):
    """09. Keep HTML out of chat while exposing the useful final report summary."""

    display_name = "09 보고서 결과 안내"
    description = "게시 상태와 안전한 링크를 사람이 읽기 쉬운 Playground Message로 만듭니다."
    icon = "MessageSquareText"
    name = "ReportResultMessage"

    inputs = [DataInput(name="publish_result", display_name="게시 결과", required=True)]
    outputs = [Output(name="message", display_name="결과 안내", method="build_message", types=["Message"])]

    def build_message(self) -> Message:
        result = _payload(self.publish_result)
        status = _safe_text(result.get("status"), 80).upper()
        summary = result.get("report_summary") if isinstance(result.get("report_summary"), dict) else {}
        gaps = summary.get("information_gap_count") if isinstance(summary.get("information_gap_count"), int) else 0
        candidates = summary.get("catalog_candidate_count") if isinstance(summary.get("catalog_candidate_count"), int) else 0
        selected = summary.get("catalog_selected_count") if isinstance(summary.get("catalog_selected_count"), int) else 0
        considered = summary.get("catalog_considered_count") if isinstance(summary.get("catalog_considered_count"), int) else 0
        if status == "PUBLISHED":
            lines = ["## 업무 설계 보고서 생성·게시 완료", f"- 결과 상태: {'보완 필요 '+str(gaps)+'건' if gaps else '설계 완료'}", f"- 검토한 카탈로그: {candidates}개", f"- 적용 권고: {selected}개", f"- 연결 검토 후보: {considered}개"]
            links = []
            view_url = _safe_url(result.get("view_url"))
            download_url = _safe_url(result.get("download_url"))
            if view_url:
                links.append(f"[보고서 열기]({view_url})")
            if download_url:
                links.append(f"[HTML 다운로드]({download_url})")
            if links:
                lines.extend(["", " · ".join(links)])
            lines.extend(["", "보고서의 추가 보완 필요 항목을 업무 설명에 반영한 뒤 Flow 전체를 다시 실행하면 더 구체적인 설계를 받을 수 있습니다."])
        elif status == "PUBLISH_FAILED":
            error = result.get("error") if isinstance(result.get("error"), dict) else {}
            lines = ["## 업무 설계 HTML 보고서 생성 완료", "Report API 게시만 실패했으며, 생성된 HTML은 10 보고서 결과 Data에 그대로 보존되어 있습니다.", f"- 결과 상태: {'보완 필요 '+str(gaps)+'건' if gaps else '설계 완료'}", f"- 게시 실패 사유: {_safe_text(error.get('message') or result.get('message'), 280) or 'Report API 응답을 확인해 주세요.'}", "", "Report API URL·서버 상태·네트워크를 확인한 뒤 다시 실행하거나, Flow API의 10 보고서 결과 Data에서 HTML을 사용하세요."]
        elif status == "GENERATED_ONLY":
            lines = ["## 업무 설계 HTML 보고서 생성 완료", f"- 결과 상태: {'보완 필요 '+str(gaps)+'건' if gaps else '설계 완료'}", f"- 검토한 카탈로그: {candidates}개", f"- 적용 권고: {selected}개", "- 게시 상태: Report API 주소가 없어 게시하지 않음", "", "Playground에서는 생성 상태를 확인하고, Flow API 또는 테스트에서는 10 보고서 결과 Data의 render_result.html을 사용하세요. 공유 링크가 필요하면 Report API URL을 입력해 다시 실행해 주세요."]
        else:
            lines = ["## 보고서 결과를 확인할 수 없습니다", _safe_text(result.get("message"), 400) or "08 보고서 링크 게시 결과를 확인해 주세요."]
        message = "\n".join(lines)
        self.status = "사용자용 보고서 결과 안내 생성 완료"
        return Message(text=message)
