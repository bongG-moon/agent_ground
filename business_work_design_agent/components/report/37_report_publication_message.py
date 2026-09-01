from __future__ import annotations

"""Turn the F30 publisher envelope into a concise Playground message.

The publisher deliberately returns ``Data`` so downstream flows can make
decisions from a stable contract.  A Playground user, however, should not
have to read that contract or copy a URL out of a JSON object.  This
standalone component is the presentation-only boundary: it accepts the
publisher result and returns a safe Markdown ``Message`` with real links.
"""

import json
import re
import urllib.parse
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, Output
from lfx.schema import Message


_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_MARKDOWN_LINK = re.compile(r"^\s*\[[^\]]*\]\((https?://[^\s)]+)\)\s*$", re.IGNORECASE)
_SAFE_CODE = re.compile(r"[^A-Za-z0-9_.-]+")
_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:mongodb(?:\+srv)?://[^\s,;]+|"
    r"(?:api[_ -]?key|token|secret|password|authorization|bearer)\s*[:=]\s*[^\s,;]+)"
)


def _payload(value: Any) -> dict[str, Any]:
    """Read the small set of Data/Message transport shapes Langflow may use."""

    if isinstance(value, dict):
        return dict(value)
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return dict(data)
    text = getattr(value, "text", value if isinstance(value, str) else "")
    if not isinstance(text, str) or not text.strip():
        return {}
    try:
        parsed = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE))
    except json.JSONDecodeError:
        return {"message": text}
    return dict(parsed) if isinstance(parsed, dict) else {"message": text}


def _safe_code(value: Any) -> str:
    return _SAFE_CODE.sub("_", str(value or "").strip()).strip("_.-")[:96]


def _safe_text(value: Any, *, limit: int = 360) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return ""
    return _SENSITIVE_TEXT.sub("[민감정보 제거]", text)[:limit]


def _unwrap_markdown_url(value: Any) -> str:
    """Accept a legacy Markdown-looking link without rendering nested Markdown."""

    text = str(value or "").strip()
    matched = _MARKDOWN_LINK.fullmatch(text)
    return matched.group(1) if matched else text


def _safe_url(value: Any) -> str | None:
    """Return only a normal absolute HTTP(S) report link.

    The Report API publisher already validates server responses.  The second
    check here protects this UI component when it is connected directly to a
    manually supplied Data object while still retaining legitimate view-token
    query strings.
    """

    url = _unwrap_markdown_url(value)
    if not url or _CONTROL_CHARACTERS.search(url) or any(character in url for character in " <>\"'`()[]"):
        return None
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        return None
    # The normalized form prevents a raw newline/control character from being
    # interpreted by the Markdown renderer while preserving an API query token.
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def _storage_text(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    backend = _safe_text(value.get("backend"), limit=80)
    database = _safe_text(value.get("database"), limit=120)
    collection = _safe_text(value.get("collection"), limit=120)
    location = ".".join(item for item in (database, collection) if item)
    if backend and location:
        return f"{backend} · {location}"
    return backend or location


def _message_for_published(payload: dict[str, Any]) -> str:
    view_url = _safe_url(payload.get("view_url"))
    download_url = _safe_url(payload.get("download_url"))
    lines = [
        "## 업무 Agent 설계 보고서가 게시되었습니다",
        "업무 개요·현행 문제·개선 방향·권장 Flow·카탈로그 기반 구현 분담을 보고서에서 확인할 수 있습니다.",
    ]
    links: list[str] = []
    if view_url:
        links.append(f"[보고서 열기]({view_url})")
    if download_url:
        links.append(f"[HTML 다운로드]({download_url})")
    if links:
        lines.extend(["", " · ".join(links)])
    else:
        lines.extend(["", "보고서 링크를 받지 못했습니다. Report API 응답을 확인해 주세요."])

    details: list[str] = []
    report_id = _safe_text(payload.get("report_id"), limit=160)
    if report_id:
        details.append(f"- 보고서 ID: `{report_id}`")
    expires_at = _safe_text(payload.get("expires_at"), limit=120)
    if expires_at:
        details.append(f"- 링크 만료: {expires_at}")
    ttl = payload.get("ttl_hours")
    if isinstance(ttl, int) and ttl > 0:
        details.append(f"- 보관 시간: {ttl}시간")
    storage = _storage_text(payload.get("storage"))
    if storage:
        details.append(f"- 저장 위치: {storage}")
    if details:
        lines.extend(["", *details])
    return "\n".join(lines)


def _message_for_test(payload: dict[str, Any]) -> str:
    lines = [
        "## 보고서 테스트 실행이 완료되었습니다",
        "HTML 렌더링과 게시 요청 구성을 확인했습니다. 테스트 실행이므로 Report API에는 저장하지 않았고, 열람 링크도 생성되지 않습니다.",
    ]
    details: list[str] = []
    renderer_report_id = _safe_text(payload.get("renderer_report_id"), limit=160)
    if renderer_report_id:
        details.append(f"- 렌더링 보고서 ID: `{renderer_report_id}`")
    content_bytes = payload.get("content_bytes")
    if isinstance(content_bytes, int) and content_bytes >= 0:
        details.append(f"- 생성된 HTML 크기: {content_bytes:,} bytes")
    ttl = payload.get("ttl_hours")
    if isinstance(ttl, int) and ttl > 0:
        details.append(f"- 실제 게시 시 링크 보관 시간: {ttl}시간")
    if details:
        lines.extend(["", *details])
    return "\n".join(lines)


def _message_for_failure(payload: dict[str, Any]) -> str:
    status = _safe_code(payload.get("status")).upper()
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    code = _safe_code(error.get("code")) or _safe_code(payload.get("code"))
    detail = _safe_text(error.get("message") or payload.get("message"))
    retryable = error.get("retryable") is True
    if status == "BLOCKED":
        title = "## 보고서 게시 전 단계에서 중단되었습니다"
        guidance = "업무 정의·Agent 설계·보고서 렌더링 결과를 보완한 뒤 다시 실행해 주세요."
    elif status == "PUBLISH_FAILED":
        title = "## 보고서 게시에 실패했습니다"
        guidance = "Report API 주소, 실행 상태, 네트워크 연결을 확인한 뒤 다시 실행해 주세요."
    else:
        title = "## 보고서 게시 결과를 확인할 수 없습니다"
        guidance = "F30 Publisher의 입력과 Report API 응답을 확인한 뒤 다시 실행해 주세요."
    lines = [title, guidance]
    if code or detail or retryable:
        lines.append("")
    if code:
        lines.append(f"- 오류 코드: `{code}`")
    if detail:
        lines.append(f"- 내용: {detail}")
    if retryable:
        lines.append("- 다시 시도 가능: 예")
    return "\n".join(lines)


def render_report_publication_message(publish_result: Any = None) -> str:
    """Create an operator-friendly terminal message without exposing raw JSON."""

    payload = _payload(publish_result)
    status = _safe_code(payload.get("status")).lower()
    if payload.get("ok") is True and status == "published":
        return _message_for_published(payload)
    if payload.get("ok") is True and status == "would_publish":
        return _message_for_test(payload)
    if payload:
        return _message_for_failure(payload)
    return "## 보고서 게시 결과가 없습니다\nF30 Publisher 출력이 연결되었는지 확인해 주세요."


class ReportPublicationMessageComponent(Component):
    display_name = "37 보고서 게시 결과 메시지"
    description = "F30 게시 결과를 읽기 쉬운 안내와 보고서·다운로드 링크로 변환합니다."
    icon = "MessageSquareText"
    name = "ReportPublicationMessage"

    inputs = [
        DataInput(
            name="publish_result",
            display_name="게시 결과 (자동 연결)",
            input_types=["Data", "JSON"],
            required=True,
            info="Business Flow Report Publisher의 Publish Result를 연결합니다.",
        )
    ]
    outputs = [
        Output(
            name="message",
            display_name="게시 안내 메시지",
            method="build_message",
            types=["Message"],
        )
    ]

    def build_message(self) -> Message:
        text = render_report_publication_message(getattr(self, "publish_result", None))
        self.status = "보고서 게시 결과 안내를 준비했습니다."
        return Message(text=text)
