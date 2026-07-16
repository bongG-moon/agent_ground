from __future__ import annotations

import base64
import importlib
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from lfx.custom import Component
from lfx.io import BoolInput, IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.dataframe import DataFrame


SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
MESSAGE_NS = "http://schemas.microsoft.com/exchange/services/2006/messages"
TYPE_NS = "http://schemas.microsoft.com/exchange/services/2006/types"
NS = {"soap": SOAP_NS, "m": MESSAGE_NS, "t": TYPE_NS}

_MAX_MAIL_COUNT = 100
_MAX_BODY_LENGTH = 200_000
_MAX_ATTACHMENT_SIZE_MB = 200
_MAX_TOTAL_ATTACHMENT_MB = 1024


class EwsReaderError(RuntimeError):
    """사용자에게 노출할 수 있는 EWS 조회 오류."""


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except Exception:
        number = default
    return max(minimum, min(maximum, number))


def _safe_filename(value: Any, fallback: str) -> str:
    name = Path(str(value or "")).name
    name = re.sub(r"[\x00-\x1f\x7f<>:\"/\\|?*]", "_", name).strip(" .")
    return name[:180] or fallback


def _text(parent: ET.Element | None, path: str) -> str:
    if parent is None:
        return ""
    node = parent.find(path, NS)
    return (node.text or "").strip() if node is not None else ""


def _bool_text(value: str) -> bool:
    return str(value or "").strip().lower() == "true"


def _secret_value(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    return str(getter() if callable(getter) else value or "")


def _parse_xml(xml_text: str, operation: str) -> ET.Element:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise EwsReaderError(f"{operation} 응답 XML을 해석하지 못했습니다.") from exc
    response_messages = root.findall(f".//m:{operation}ResponseMessage", NS)
    if not response_messages:
        raise EwsReaderError(f"{operation} 응답 메시지가 없습니다.")
    errors: list[str] = []
    for message in response_messages:
        if message.get("ResponseClass") == "Success":
            continue
        code = _text(message, "m:ResponseCode") or "UnknownError"
        text = _text(message, "m:MessageText")
        errors.append(f"{code}: {text}" if text else code)
    if errors and len(errors) == len(response_messages):
        raise EwsReaderError(f"{operation} 실패: {'; '.join(errors)}")
    return root


def build_find_item_soap(max_count: int, email_addr: str = "") -> str:
    mailbox = ""
    if email_addr.strip():
        mailbox = (
            "<t:Mailbox><t:EmailAddress>"
            f"{escape(email_addr.strip())}"
            "</t:EmailAddress></t:Mailbox>"
        )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="{SOAP_NS}" xmlns:m="{MESSAGE_NS}" xmlns:t="{TYPE_NS}">
  <soap:Header><t:RequestServerVersion Version="Exchange2013" /></soap:Header>
  <soap:Body>
    <m:FindItem Traversal="Shallow">
      <m:ItemShape>
        <t:BaseShape>Default</t:BaseShape>
        <t:AdditionalProperties>
          <t:FieldURI FieldURI="item:DateTimeReceived" />
          <t:FieldURI FieldURI="item:HasAttachments" />
          <t:FieldURI FieldURI="message:IsRead" />
        </t:AdditionalProperties>
      </m:ItemShape>
      <m:IndexedPageItemView MaxEntriesReturned="{max_count}" Offset="0" BasePoint="Beginning" />
      <m:SortOrder>
        <t:FieldOrder Order="Descending"><t:FieldURI FieldURI="item:DateTimeReceived" /></t:FieldOrder>
      </m:SortOrder>
      <m:ParentFolderIds><t:DistinguishedFolderId Id="inbox">{mailbox}</t:DistinguishedFolderId></m:ParentFolderIds>
    </m:FindItem>
  </soap:Body>
</soap:Envelope>"""


def build_get_item_soap(items: list[dict[str, Any]]) -> str:
    tags: list[str] = []
    for item in items:
        item_id = quoteattr(str(item["item_id"]))
        change_key = str(item.get("change_key") or "")
        change_attr = f" ChangeKey={quoteattr(change_key)}" if change_key else ""
        tags.append(f"<t:ItemId Id={item_id}{change_attr}/>")
    return f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="{SOAP_NS}" xmlns:m="{MESSAGE_NS}" xmlns:t="{TYPE_NS}">
  <soap:Header><t:RequestServerVersion Version="Exchange2013" /></soap:Header>
  <soap:Body>
    <m:GetItem>
      <m:ItemShape>
        <t:BaseShape>AllProperties</t:BaseShape>
        <t:BodyType>Text</t:BodyType>
      </m:ItemShape>
      <m:ItemIds>{''.join(tags)}</m:ItemIds>
    </m:GetItem>
  </soap:Body>
</soap:Envelope>"""


def build_get_attachment_soap(attachment_ids: list[str]) -> str:
    tags = "".join(f"<t:AttachmentId Id={quoteattr(value)}/>" for value in attachment_ids)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="{SOAP_NS}" xmlns:m="{MESSAGE_NS}" xmlns:t="{TYPE_NS}">
  <soap:Header><t:RequestServerVersion Version="Exchange2013" /></soap:Header>
  <soap:Body>
    <m:GetAttachment>
      <m:AttachmentShape />
      <m:AttachmentIds>{tags}</m:AttachmentIds>
    </m:GetAttachment>
  </soap:Body>
</soap:Envelope>"""


def parse_find_item_response(xml_text: str, keyword: str = "", max_count: int = 10) -> list[dict[str, Any]]:
    root = _parse_xml(xml_text, "FindItem")
    selected: list[dict[str, Any]] = []
    needle = keyword.strip().casefold()
    for position, message in enumerate(root.findall(".//t:Items/t:Message", NS), 1):
        item_id_node = message.find("t:ItemId", NS)
        if item_id_node is None or not item_id_node.get("Id"):
            continue
        subject = _text(message, "t:Subject")
        if needle and needle not in subject.casefold():
            continue
        selected.append(
            {
                "item_id": item_id_node.get("Id", ""),
                "change_key": item_id_node.get("ChangeKey", ""),
                "mail_index": len(selected) + 1,
                "subject": subject,
                "sender": _text(message, "t:Sender/t:Mailbox/t:Name"),
                "sender_email": _text(message, "t:Sender/t:Mailbox/t:EmailAddress"),
                "received_time": _text(message, "t:DateTimeReceived"),
                "is_read": _bool_text(_text(message, "t:IsRead")),
                "has_attachments": _bool_text(_text(message, "t:HasAttachments")),
                "find_position": position,
            }
        )
        if len(selected) >= max_count:
            break
    return selected


def parse_get_item_response(xml_text: str) -> dict[str, dict[str, Any]]:
    root = _parse_xml(xml_text, "GetItem")
    details: dict[str, dict[str, Any]] = {}
    for message in root.findall(".//m:GetItemResponseMessage/m:Items/t:Message", NS):
        item_id_node = message.find("t:ItemId", NS)
        item_id = item_id_node.get("Id", "") if item_id_node is not None else ""
        if not item_id:
            continue
        attachments: list[dict[str, Any]] = []
        attachment_root = message.find("t:Attachments", NS)
        if attachment_root is not None:
            for attachment in list(attachment_root):
                kind = attachment.tag.rsplit("}", 1)[-1]
                attachment_id_node = attachment.find("t:AttachmentId", NS)
                attachment_id = attachment_id_node.get("Id", "") if attachment_id_node is not None else ""
                if not attachment_id:
                    continue
                size_text = _text(attachment, "t:Size")
                try:
                    size = int(size_text or 0)
                except ValueError:
                    size = 0
                attachments.append(
                    {
                        "attachment_id": attachment_id,
                        "kind": kind,
                        "name": _text(attachment, "t:Name"),
                        "content_type": _text(attachment, "t:ContentType"),
                        "is_inline": _bool_text(_text(attachment, "t:IsInline")),
                        "size": size,
                    }
                )
        details[item_id] = {
            "subject": _text(message, "t:Subject"),
            "sender": _text(message, "t:From/t:Mailbox/t:Name")
            or _text(message, "t:Sender/t:Mailbox/t:Name"),
            "sender_email": _text(message, "t:From/t:Mailbox/t:EmailAddress")
            or _text(message, "t:Sender/t:Mailbox/t:EmailAddress"),
            "received_time": _text(message, "t:DateTimeReceived"),
            "is_read": _bool_text(_text(message, "t:IsRead")),
            "body": _text(message, "t:Body"),
            "attachments": attachments,
        }
    return details


def parse_get_attachment_response(xml_text: str) -> dict[str, dict[str, Any]]:
    root = _parse_xml(xml_text, "GetAttachment")
    result: dict[str, dict[str, Any]] = {}
    for attachment in root.findall(".//m:GetAttachmentResponseMessage/m:Attachments/*", NS):
        kind = attachment.tag.rsplit("}", 1)[-1]
        attachment_id_node = attachment.find("t:AttachmentId", NS)
        attachment_id = attachment_id_node.get("Id", "") if attachment_id_node is not None else ""
        if not attachment_id:
            continue
        result[attachment_id] = {
            "kind": kind,
            "name": _text(attachment, "t:Name"),
            "content_type": _text(attachment, "t:ContentType"),
            "is_inline": _bool_text(_text(attachment, "t:IsInline")),
            "content": _text(attachment, "t:Content"),
        }
    return result


def _post_soap(
    session: Any,
    ews_url: str,
    soap_body: str,
    *,
    headers: dict[str, str],
    verify: bool | str,
    timeout: int,
    operation: str,
) -> str:
    response = session.post(
        ews_url,
        data=soap_body.encode("utf-8"),
        headers=headers,
        verify=verify,
        timeout=timeout,
    )
    if int(getattr(response, "status_code", 0)) != 200:
        raise EwsReaderError(f"{operation} HTTP 실패: {getattr(response, 'status_code', 0)}")
    return str(getattr(response, "text", ""))


def _body_document(mail: dict[str, Any], body: str, body_length: int) -> str:
    return (
        f"메일 순번: {mail['mail_index']}\n"
        f"제목: {mail.get('subject') or '확인되지 않음'}\n"
        f"보낸 사람: {mail.get('sender') or '확인되지 않음'}\n"
        f"보낸 사람 주소: {mail.get('sender_email') or '확인되지 않음'}\n"
        f"받은 시각: {mail.get('received_time') or '확인되지 않음'}\n"
        f"읽음 여부: {mail.get('is_read', False)}\n\n"
        f"메일 본문:\n{(body or '[본문을 읽지 못했습니다.]')[:body_length]}\n"
    )


def _write_notice(directory: Path, stem: str, message: str) -> Path:
    path = directory / f"{_safe_filename(stem, 'attachment')}_ews_notice.txt"
    path.write_text(message, encoding="utf-8")
    return path


def fetch_ews_mail_records(
    session: Any,
    *,
    ews_url: str,
    email_addr: str = "",
    max_count: Any = 10,
    keyword: str = "",
    body_length: Any = 2_000,
    include_attachments: bool = True,
    include_inline_attachments: bool = False,
    max_attachment_size_mb: Any = 30,
    max_total_attachment_mb: Any = 100,
    verify: bool | str = False,
    timeout: Any = 60,
    user_agent: str = "Microsoft Office/16.0 (Microsoft Outlook 16.0; Pro)",
    output_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    """EWS 메일·첨부를 DRM과 Read File이 소비할 DataFrame 행으로 만든다."""

    if not str(ews_url or "").lower().startswith("https://"):
        raise EwsReaderError("EWS URL은 https:// 주소여야 합니다.")
    mail_limit = _clamp_int(max_count, 10, 1, _MAX_MAIL_COUNT)
    text_limit = _clamp_int(body_length, 2_000, 1, _MAX_BODY_LENGTH)
    attachment_limit = _clamp_int(max_attachment_size_mb, 30, 1, _MAX_ATTACHMENT_SIZE_MB) * 1024 * 1024
    total_limit = _clamp_int(max_total_attachment_mb, 100, 1, _MAX_TOTAL_ATTACHMENT_MB) * 1024 * 1024
    request_timeout = _clamp_int(timeout, 60, 5, 300)
    headers = {"Content-Type": "text/xml; charset=utf-8", "User-Agent": user_agent.strip()}
    destination = Path(output_root) if output_root else Path(tempfile.mkdtemp(prefix="langflow-ews-"))
    destination.mkdir(parents=True, exist_ok=True)

    find_xml = _post_soap(
        session,
        ews_url,
        build_find_item_soap(mail_limit, email_addr),
        headers=headers,
        verify=verify,
        timeout=request_timeout,
        operation="FindItem",
    )
    mails = parse_find_item_response(find_xml, keyword=keyword, max_count=mail_limit)
    if not mails:
        return []

    get_item_xml = _post_soap(
        session,
        ews_url,
        build_get_item_soap(mails),
        headers=headers,
        verify=verify,
        timeout=request_timeout,
        operation="GetItem",
    )
    details = parse_get_item_response(get_item_xml)
    attachment_owner: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    rows: list[dict[str, Any]] = []

    for mail in mails:
        detail = details.get(mail["item_id"], {})
        for key in ("subject", "sender", "sender_email", "received_time"):
            if detail.get(key):
                mail[key] = detail[key]
        if "is_read" in detail:
            mail["is_read"] = bool(detail["is_read"])
        mail_dir = destination / f"mail_{mail['mail_index']:03d}"
        mail_dir.mkdir(parents=True, exist_ok=True)
        body_path = mail_dir / f"mail_{mail['mail_index']:03d}_body.txt"
        body_path.write_text(_body_document(mail, str(detail.get("body") or ""), text_limit), encoding="utf-8")
        common = {
            "mail_index": mail["mail_index"],
            "mail_subject": mail.get("subject", ""),
            "sender": mail.get("sender", ""),
            "sender_email": mail.get("sender_email", ""),
            "received_time": mail.get("received_time", ""),
            "is_read": bool(mail.get("is_read", False)),
        }
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
            for attachment_index, attachment in enumerate(detail.get("attachments") or [], 1):
                attachment["attachment_index"] = attachment_index
                attachment_owner[attachment["attachment_id"]] = (common, attachment)

    if not include_attachments or not attachment_owner:
        return rows

    requested_ids: list[str] = []
    total_declared = 0
    for attachment_id, (common, attachment) in attachment_owner.items():
        if attachment.get("is_inline") and not include_inline_attachments:
            continue
        name = _safe_filename(attachment.get("name"), f"attachment_{attachment['attachment_index']:03d}.bin")
        size = int(attachment.get("size") or 0)
        if size > attachment_limit or total_declared + size > total_limit:
            mail_dir = destination / f"mail_{common['mail_index']:03d}"
            notice = _write_notice(mail_dir, name, "첨부파일 크기 제한으로 EWS Content를 가져오지 않았습니다.")
            rows.append(
                {
                    **common,
                    "file_path": str(notice),
                    "file_name": name,
                    "source_kind": "extraction_error",
                    "attachment_index": attachment["attachment_index"],
                    "is_inline": bool(attachment.get("is_inline")),
                    "content_type": attachment.get("content_type", ""),
                    "drm_status": "not_applicable",
                    "extraction_error": "attachment_size_limit",
                }
            )
            continue
        total_declared += size
        requested_ids.append(attachment_id)

    if not requested_ids:
        return rows
    attachment_xml = _post_soap(
        session,
        ews_url,
        build_get_attachment_soap(requested_ids),
        headers=headers,
        verify=verify,
        timeout=request_timeout,
        operation="GetAttachment",
    )
    attachments = parse_get_attachment_response(attachment_xml)
    total_downloaded = 0
    for attachment_id in requested_ids:
        common, metadata = attachment_owner[attachment_id]
        item = attachments.get(attachment_id, {})
        name = _safe_filename(item.get("name") or metadata.get("name"), f"attachment_{metadata['attachment_index']:03d}.bin")
        mail_dir = destination / f"mail_{common['mail_index']:03d}"
        if item.get("kind") != "FileAttachment" or not item.get("content"):
            notice = _write_notice(mail_dir, name, "EWS ItemAttachment 또는 Content 없는 첨부는 자동 파일 처리하지 않았습니다.")
            rows.append(
                {
                    **common,
                    "file_path": str(notice),
                    "file_name": name,
                    "source_kind": "extraction_error",
                    "attachment_index": metadata["attachment_index"],
                    "is_inline": bool(item.get("is_inline") or metadata.get("is_inline")),
                    "content_type": item.get("content_type") or metadata.get("content_type", ""),
                    "drm_status": "not_applicable",
                    "extraction_error": "unsupported_ews_attachment",
                }
            )
            continue
        try:
            content = base64.b64decode(re.sub(r"\s+", "", str(item["content"])), validate=True)
        except Exception as exc:
            raise EwsReaderError(f"첨부파일 Base64를 해석하지 못했습니다: {name}") from exc
        total_downloaded += len(content)
        if len(content) > attachment_limit or total_downloaded > total_limit:
            raise EwsReaderError("다운로드된 첨부파일이 설정한 크기 제한을 초과했습니다.")
        path = mail_dir / f"{metadata['attachment_index']:03d}_{name}"
        path.write_bytes(content)
        rows.append(
            {
                **common,
                "file_path": str(path),
                "file_name": name,
                "source_kind": "ews_attachment",
                "attachment_index": metadata["attachment_index"],
                "is_inline": bool(item.get("is_inline") or metadata.get("is_inline")),
                "content_type": item.get("content_type") or metadata.get("content_type", ""),
                "drm_status": "pending",
                "extraction_error": "",
            }
        )
    return rows


def _load_http_dependencies(
    *, auto_install: bool, nexus_url: str, trusted_host: str
) -> tuple[Any, Any]:
    try:
        import requests
        from requests_ntlm import HttpNtlmAuth

        return requests, HttpNtlmAuth
    except ImportError as exc:
        if not auto_install:
            raise EwsReaderError("requests와 requests-ntlm이 필요합니다.") from exc
        if not nexus_url.strip() or not trusted_host.strip():
            raise EwsReaderError("자동 설치를 사용하려면 내부 Nexus URL과 Trusted Host가 필요합니다.") from exc
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "requests",
            "requests-ntlm",
            "--index-url",
            nexus_url.strip(),
            "--trusted-host",
            trusted_host.strip(),
            "-q",
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True, timeout=180)
        except (OSError, subprocess.SubprocessError) as install_exc:
            raise EwsReaderError("내부 Nexus에서 EWS 의존성 설치에 실패했습니다.") from install_exc
        importlib.invalidate_caches()
        import requests
        from requests_ntlm import HttpNtlmAuth

        return requests, HttpNtlmAuth


class OutlookEwsMailAttachmentReader(Component):
    display_name = "01 Outlook 메일·첨부 읽기 (EWS)"
    description = "사내 EWS와 NTLM 인증으로 최근 메일 본문과 파일 첨부를 로컬 작업 경로에 가져옵니다."
    documentation: str = "https://docs.langflow.org/components-custom-components"
    icon = "MailSearch"
    name = "OutlookEwsMailAttachmentReader"

    inputs = [
        StrInput(name="email_addr", display_name="이메일 주소", info="조회할 Outlook 이메일 주소", value=""),
        StrInput(name="username", display_name="AD 계정", info="도메인을 제외한 AD 계정", value=""),
        SecretStrInput(name="password", display_name="AD 비밀번호", info="Flow JSON에 저장하지 않습니다.", value=""),
        StrInput(name="ad_domain", display_name="AD 도메인", value="hynixad", advanced=True),
        StrInput(name="ews_url", display_name="EWS URL", info="예: https://mail.example.invalid/EWS/Exchange.asmx", value=""),
        IntInput(name="max_count", display_name="읽을 메일 수", value=10),
        MessageTextInput(name="keyword", display_name="제목 필터 키워드", value=""),
        IntInput(name="body_length", display_name="본문 읽기 길이", value=2_000),
        BoolInput(name="include_attachments", display_name="첨부파일 읽기", value=True),
        BoolInput(name="include_inline_attachments", display_name="인라인 첨부 포함", value=False, advanced=True),
        IntInput(name="max_attachment_size_mb", display_name="첨부당 최대 크기(MB)", value=30, advanced=True),
        IntInput(name="max_total_attachment_mb", display_name="전체 첨부 최대 크기(MB)", value=100, advanced=True),
        BoolInput(name="verify_tls", display_name="TLS 인증서 검증", value=False, advanced=True),
        StrInput(name="ca_bundle_path", display_name="사내 CA Bundle 경로", value="", advanced=True),
        IntInput(name="request_timeout", display_name="EWS Timeout(초)", value=60, advanced=True),
        StrInput(
            name="user_agent",
            display_name="EWS User-Agent",
            value="Microsoft Office/16.0 (Microsoft Outlook 16.0.17031; Pro)",
            advanced=True,
        ),
        BoolInput(name="auto_install_dependencies", display_name="내부 Nexus에서 의존성 자동 설치", value=True, advanced=True),
        StrInput(name="nexus_url", display_name="내부 Nexus Simple URL", value="", advanced=True),
        StrInput(name="trusted_host", display_name="Nexus Trusted Host", value="", advanced=True),
    ]

    outputs = [
        Output(
            display_name="메일 본문·첨부 항목",
            name="mail_items",
            method="build_mail_items",
            types=["DataFrame"],
        )
    ]

    def build_mail_items(self) -> DataFrame:
        requests, HttpNtlmAuth = _load_http_dependencies(
            auto_install=bool(getattr(self, "auto_install_dependencies", True)),
            nexus_url=str(getattr(self, "nexus_url", "") or ""),
            trusted_host=str(getattr(self, "trusted_host", "") or ""),
        )
        username = str(getattr(self, "username", "") or "").strip()
        password = _secret_value(getattr(self, "password", ""))
        domain = str(getattr(self, "ad_domain", "hynixad") or "hynixad").strip()
        if not username or not password:
            raise EwsReaderError("AD 계정과 비밀번호가 필요합니다.")
        session = requests.Session()
        session.auth = HttpNtlmAuth(f"{domain}\\{username}", password, session=session)
        verify_tls = bool(getattr(self, "verify_tls", False))
        ca_bundle = str(getattr(self, "ca_bundle_path", "") or "").strip()
        verify: bool | str = ca_bundle if verify_tls and ca_bundle else verify_tls
        if verify is False:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        rows = fetch_ews_mail_records(
            session,
            ews_url=str(getattr(self, "ews_url", "") or ""),
            email_addr=str(getattr(self, "email_addr", "") or ""),
            max_count=getattr(self, "max_count", 10),
            keyword=str(getattr(self, "keyword", "") or ""),
            body_length=getattr(self, "body_length", 2_000),
            include_attachments=bool(getattr(self, "include_attachments", True)),
            include_inline_attachments=bool(getattr(self, "include_inline_attachments", False)),
            max_attachment_size_mb=getattr(self, "max_attachment_size_mb", 30),
            max_total_attachment_mb=getattr(self, "max_total_attachment_mb", 100),
            verify=verify,
            timeout=getattr(self, "request_timeout", 60),
            user_agent=str(getattr(self, "user_agent", "") or ""),
        )
        self.status = f"EWS 조회 완료 · 메일 항목 {len(rows)}개"
        return DataFrame(rows)
