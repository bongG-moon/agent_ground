from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[3]
FLOW_ROOT = ROOT / "flows" / "mail_attachment_summary_flow"
FLOW_PATH = FLOW_ROOT / "mail_attachment_summary_flow.json"
DUMMY_FLOW_PATH = FLOW_ROOT / "mail_attachment_summary_dummy_flow.json"
EWS_SOURCE = FLOW_ROOT / "nodes" / "ews_mail_attachment_reader.py"
DUMMY_SOURCE = FLOW_ROOT / "nodes" / "dummy_ews_mail_items.py"
FORMATTER_SOURCE = FLOW_ROOT / "nodes" / "mail_dataframe_formatter.py"
STABLE_FILE_SOURCE = FLOW_ROOT / "nodes" / "stable_mail_file_reader.py"
DRM_SOURCE = ROOT / "components" / "drm_document_text_extractor" / "drm_document_text_extractor.py"

ALLOWED_NODE_TYPES = {
    "DummyEwsMailItems",
    "MailDataFrameFormatter",
    "OutlookEwsMailAttachmentReader",
    "StableMailFileReader",
    "DrmDocumentTextExtractor",
    "LoopComponent",
    "ParserComponent",
    "LanguageModelComponent",
    "ChatInput",
    "Prompt",
    "ChatOutput",
    "note",
}

FIND_ITEM_XML = """<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"
 xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages"
 xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types">
 <s:Body><m:FindItemResponse><m:ResponseMessages>
  <m:FindItemResponseMessage ResponseClass="Success"><m:ResponseCode>NoError</m:ResponseCode>
   <m:RootFolder><t:Items><t:Message>
    <t:ItemId Id="item-1" ChangeKey="change-1"/><t:Subject>검토 요청</t:Subject>
    <t:Sender><t:Mailbox><t:Name>보낸 사람</t:Name><t:EmailAddress>sender@example.invalid</t:EmailAddress></t:Mailbox></t:Sender>
    <t:DateTimeReceived>2026-07-16T01:00:00Z</t:DateTimeReceived><t:IsRead>false</t:IsRead><t:HasAttachments>true</t:HasAttachments>
   </t:Message></t:Items></m:RootFolder>
  </m:FindItemResponseMessage>
 </m:ResponseMessages></m:FindItemResponse></s:Body>
</s:Envelope>"""

GET_ITEM_XML = """<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"
 xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages"
 xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types">
 <s:Body><m:GetItemResponse><m:ResponseMessages>
  <m:GetItemResponseMessage ResponseClass="Success"><m:ResponseCode>NoError</m:ResponseCode><m:Items><t:Message>
   <t:ItemId Id="item-1"/><t:Subject>검토 요청</t:Subject>
   <t:From><t:Mailbox><t:Name>보낸 사람</t:Name><t:EmailAddress>sender@example.invalid</t:EmailAddress></t:Mailbox></t:From>
   <t:DateTimeReceived>2026-07-16T01:00:00Z</t:DateTimeReceived><t:IsRead>false</t:IsRead>
   <t:Body BodyType="Text">첨부 문서를 검토해 주세요.</t:Body>
   <t:Attachments><t:FileAttachment><t:AttachmentId Id="attachment-1"/><t:Name>review.pdf</t:Name>
    <t:ContentType>application/pdf</t:ContentType><t:Size>8</t:Size><t:IsInline>false</t:IsInline>
   </t:FileAttachment></t:Attachments>
  </t:Message></m:Items></m:GetItemResponseMessage>
 </m:ResponseMessages></m:GetItemResponse></s:Body>
</s:Envelope>"""

GET_ATTACHMENT_XML = """<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"
 xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages"
 xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types">
 <s:Body><m:GetAttachmentResponse><m:ResponseMessages>
  <m:GetAttachmentResponseMessage ResponseClass="Success"><m:ResponseCode>NoError</m:ResponseCode><m:Attachments>
   <t:FileAttachment><t:AttachmentId Id="attachment-1"/><t:Name>review.pdf</t:Name>
    <t:ContentType>application/pdf</t:ContentType><t:IsInline>false</t:IsInline><t:Content>JVBERi0xLjQ=</t:Content>
   </t:FileAttachment>
  </m:Attachments></m:GetAttachmentResponseMessage>
 </m:ResponseMessages></m:GetAttachmentResponse></s:Body>
</s:Envelope>"""


def load_flow(path: Path = FLOW_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def node_by_id(flow: dict, node_id: str) -> dict:
    return next(node for node in flow["data"]["nodes"] if node["id"] == node_id)


def decode_handle(value: str) -> dict:
    return json.loads(value.replace("œ", '"'))


def load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_package_contract_reuses_drm_component_and_registers_internal_nodes() -> None:
    manifest = json.loads((FLOW_ROOT / "manifest.json").read_text(encoding="utf-8"))
    refs = json.loads((FLOW_ROOT / "component_refs.json").read_text(encoding="utf-8"))
    internal = json.loads((FLOW_ROOT / "internal_nodes.json").read_text(encoding="utf-8"))

    assert manifest["status"] == "user_testing"
    assert manifest["version"] == "0.7.0"
    assert manifest["source_export_version"] == "1.8.2"
    assert manifest["test_flow_file"] == "mail_attachment_summary_dummy_flow.json"
    assert refs == {
        "flow_id": "mail_attachment_summary_flow",
        "components": [{"id": "drm_document_text_extractor", "version": "0.5.0"}],
    }
    assert internal["flow_id"] == "mail_attachment_summary_flow"
    assert {item["id"] for item in internal["nodes"]} == {
        "ews_mail_attachment_reader",
        "dummy_ews_mail_items",
        "mail_dataframe_formatter",
        "stable_mail_file_reader",
    }


def test_flow_contains_expected_ews_drm_graph() -> None:
    flow = load_flow()
    nodes = flow["data"]["nodes"]
    edges = flow["data"]["edges"]
    assert len(nodes) == 14
    assert len(edges) == 12
    assert {node["data"]["type"] for node in nodes} <= ALLOWED_NODE_TYPES
    assert not {"Agent", "APIRequest", "MCPTools", "RunFlow"} & {
        node["data"]["type"] for node in nodes
    }

    ids = {node["id"] for node in nodes}
    nodes_by_id = {node["id"]: node for node in nodes}
    for edge in edges:
        assert edge["source"] in ids
        assert edge["target"] in ids
        assert "┇" not in edge["sourceHandle"]
        assert "┇" not in edge["targetHandle"]
        assert decode_handle(edge["sourceHandle"]) == edge["data"]["sourceHandle"]
        assert decode_handle(edge["targetHandle"]) == edge["data"]["targetHandle"]

        target_handle = edge["data"]["targetHandle"]
        field_name = target_handle.get("fieldName")
        if field_name:
            target_field = nodes_by_id[edge["target"]]["data"]["node"]["template"][field_name]
            assert target_field["show"] is True
            assert target_field["advanced"] is False

    edge_pairs = {(edge["source"], edge["target"]) for edge in edges}
    assert (
        "OutlookEwsMailAttachmentReader-mailAttachments",
        "LoopComponent-mailAttachments",
    ) in edge_pairs
    assert ("LoopComponent-mailAttachments", "DrmDocumentTextExtractor-mailAttachments") in edge_pairs
    assert (
        "LanguageModelComponent-mailAttachmentVision",
        "DrmDocumentTextExtractor-mailAttachments",
    ) in edge_pairs
    assert (
        "DrmDocumentTextExtractor-mailAttachments",
        "StableMailFileReader-mailAttachments",
    ) in edge_pairs
    assert (
        "StableMailFileReader-mailAttachments",
        "MailDataFrameFormatter-mailAttachmentItem",
    ) in edge_pairs

    file_to_formatter = next(
        edge
        for edge in edges
        if edge["source"] == "StableMailFileReader-mailAttachments"
        and edge["target"] == "MailDataFrameFormatter-mailAttachmentItem"
    )
    assert file_to_formatter["data"]["sourceHandle"]["output_types"] == ["DataFrame"]
    assert file_to_formatter["data"]["targetHandle"]["inputTypes"] == ["DataFrame"]


def test_dummy_flow_replaces_only_ews_source_and_keeps_typed_pipeline() -> None:
    flow = load_flow(DUMMY_FLOW_PATH)
    nodes = flow["data"]["nodes"]
    edges = flow["data"]["edges"]
    assert len(nodes) == 14
    assert len(edges) == 12
    assert any(node["id"] == "DummyEwsMailItems-mailAttachments" for node in nodes)
    assert not any(node["id"] == "OutlookEwsMailAttachmentReader-mailAttachments" for node in nodes)
    assert any(
        edge["source"] == "DummyEwsMailItems-mailAttachments"
        and edge["target"] == "LoopComponent-mailAttachments"
        and edge["data"]["sourceHandle"]["output_types"] == ["DataFrame"]
        for edge in edges
    )


def test_internal_and_shared_component_sources_are_embedded_exactly() -> None:
    flow = load_flow()
    source_by_node = {
        "OutlookEwsMailAttachmentReader-mailAttachments": EWS_SOURCE.read_text(encoding="utf-8"),
        "MailDataFrameFormatter-mailAttachmentItem": FORMATTER_SOURCE.read_text(encoding="utf-8"),
        "MailDataFrameFormatter-mailAttachmentAggregate": FORMATTER_SOURCE.read_text(encoding="utf-8"),
        "StableMailFileReader-mailAttachments": STABLE_FILE_SOURCE.read_text(encoding="utf-8"),
        "DrmDocumentTextExtractor-mailAttachments": DRM_SOURCE.read_text(encoding="utf-8"),
    }
    for node_id, source in source_by_node.items():
        embedded = node_by_id(flow, node_id)["data"]["node"]["template"]["code"]["value"]
        assert embedded == source
    drm_source = source_by_node["DrmDocumentTextExtractor-mailAttachments"]
    assert "process_file_record" in drm_source
    assert '"Authorization": f"Bearer {token}"' in drm_source
    assert '"params": {"empNo": employee_no}' in drm_source

    dummy_flow = load_flow(DUMMY_FLOW_PATH)
    assert (
        node_by_id(dummy_flow, "DummyEwsMailItems-mailAttachments")["data"]["node"]["template"]["code"]["value"]
        == DUMMY_SOURCE.read_text(encoding="utf-8")
    )


def test_dummy_rows_and_formatter_run_without_ews_or_drm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dummy_module = load_module(DUMMY_SOURCE, "mail_flow_dummy_source_test")
    formatter_module = load_module(FORMATTER_SOURCE, "mail_flow_formatter_test")
    rows = dummy_module.build_dummy_mail_rows(mail_count=2, include_attachments=True, output_root=tmp_path)
    assert len(rows) == 4
    assert [row["source_kind"] for row in rows] == [
        "mail_body",
        "ews_attachment",
        "mail_body",
        "ews_attachment",
    ]
    assert all(Path(row["file_path"]).is_file() for row in rows)
    assert all("attachment_id" not in row and "item_id" not in row for row in rows)

    monkeypatch.setattr(dummy_module, "build_dummy_mail_rows", lambda **_kwargs: rows)
    dummy_component = dummy_module.DummyEwsMailItems()
    dummy_component.mail_count = 2
    dummy_component.include_attachments = True
    result = dummy_component.build_mail_items()
    assert dummy_component.status is result
    assert len(result) == 4

    from lfx.schema.dataframe import DataFrame

    message = formatter_module.format_dataframe(
        DataFrame([{"mail_index": 1, "text": "테스트 본문"}]),
        mode="Parser",
        pattern="메일 {mail_index}: {text} / {missing_key}",
        separator="\n",
        clean_data=True,
    )
    assert message == "메일 1: 테스트 본문 /"


def test_stable_file_reader_keeps_dataframe_output_and_routes_text_away_from_docling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = load_flow()
    file_node = node_by_id(flow, "StableMailFileReader-mailAttachments")
    module = load_module(STABLE_FILE_SOURCE, "mail_flow_stable_file_reader_test")
    frontend_node = deepcopy(file_node["data"]["node"])

    for field_name, field_value in (
        ("path", ["sample.txt"]),
        ("path", ["sample.xlsx"]),
        ("advanced_mode", False),
        ("advanced_mode", True),
    ):
        updated = module.StableMailFileReader.update_outputs(
            object(), frontend_node, field_name, field_value
        )
        assert [(output["name"], output["types"]) for output in updated["outputs"]] == [
            ("dataframe", ["DataFrame"])
        ]

    parser_modes: list[tuple[str, bool]] = []

    def fake_process_files(component, batch):
        parser_modes.append((Path(batch[0].path).suffix, component.advanced_mode))
        return batch

    monkeypatch.setattr(module.FileComponent, "process_files", fake_process_files)
    component = module.StableMailFileReader()
    component.advanced_mode = True
    files = [SimpleNamespace(path=Path("sample.txt")), SimpleNamespace(path=Path("sample.pdf"))]
    assert component.process_files(files) == files
    assert parser_modes == [(".txt", False), (".pdf", True)]
    assert component.advanced_mode is True


def test_ews_secrets_are_blank_and_read_file_uses_dynamic_local_path() -> None:
    flow = load_flow()
    ews_template = node_by_id(flow, "OutlookEwsMailAttachmentReader-mailAttachments")["data"]["node"]["template"]
    for field in ("email_addr", "username", "password", "ews_url", "nexus_url", "trusted_host"):
        assert ews_template[field]["value"] == ""
    assert ews_template["password"]["password"] is True
    assert ews_template["verify_tls"]["value"] is False
    assert ews_template["auto_install_dependencies"]["value"] is True

    drm_node = node_by_id(flow, "DrmDocumentTextExtractor-mailAttachments")
    drm_template = drm_node["data"]["node"]["template"]
    for field in ("drm_api_url", "drm_token", "employee_no"):
        assert drm_template[field]["value"] == ""
    assert drm_template["drm_token"]["password"] is True
    assert drm_template["employee_no"]["password"] is True
    assert drm_template["processing_mode"]["value"] == "자동(로컬 우선)"
    assert drm_template["allow_insecure_http"]["value"] is False
    assert drm_template["verify_tls"]["value"] is True
    assert drm_template["file_record"]["show"] is True
    assert drm_template["file_record"]["advanced"] is False
    assert drm_template["vision_model"]["show"] is True
    assert drm_template["vision_model"]["advanced"] is False
    assert drm_node["data"]["selected_output"] == "processed_file"

    file_node = node_by_id(flow, "StableMailFileReader-mailAttachments")
    file_template = file_node["data"]["node"]["template"]
    assert file_template["storage_location"]["value"] == [{"name": "Local", "icon": "hard-drive"}]
    assert file_template["path"]["file_path"] == []
    assert file_template["advanced_mode"]["value"] is True
    assert file_template["ocr_engine"]["value"] == "easyocr"
    assert file_template["delete_server_file_after_processing"]["value"] is True
    assert file_template["file_path"]["show"] is True
    assert file_template["file_path"]["advanced"] is False
    assert file_node["data"]["selected_output"] == "dataframe"


def test_models_have_no_default_provider_or_secret() -> None:
    flow = load_flow()
    for node_id in (
        "LanguageModelComponent-mailAttachmentVision",
        "LanguageModelComponent-mailAttachmentItem",
        "LanguageModelComponent-mailAttachmentFinal",
    ):
        template = node_by_id(flow, node_id)["data"]["node"]["template"]
        assert template["model"]["value"] in ([], "", None)
        assert template["api_key"]["value"] == ""
        assert template["temperature"]["value"] == 0.1
        assert template["stream"]["value"] is False


def test_ews_soap_builders_and_parsers() -> None:
    module = load_module(EWS_SOURCE, "mail_flow_ews_parser_test")
    find_soap = module.build_find_item_soap(5, "mailbox&test@example.invalid")
    assert 'MaxEntriesReturned="5"' in find_soap
    assert "mailbox&amp;test@example.invalid" in find_soap
    assert 'Order="Descending"' in find_soap

    mails = module.parse_find_item_response(FIND_ITEM_XML, keyword="검토", max_count=10)
    assert len(mails) == 1
    assert mails[0]["item_id"] == "item-1"
    assert module.parse_find_item_response(FIND_ITEM_XML, keyword="없는 제목", max_count=10) == []

    details = module.parse_get_item_response(GET_ITEM_XML)
    assert details["item-1"]["body"] == "첨부 문서를 검토해 주세요."
    assert details["item-1"]["attachments"][0]["attachment_id"] == "attachment-1"

    attachments = module.parse_get_attachment_response(GET_ATTACHMENT_XML)
    assert attachments["attachment-1"]["content"] == "JVBERi0xLjQ="


def test_ews_pipeline_downloads_body_and_file_without_exposing_ids(tmp_path: Path) -> None:
    module = load_module(EWS_SOURCE, "mail_flow_ews_pipeline_test")

    class Response:
        status_code = 200

        def __init__(self, text: str) -> None:
            self.text = text

    class Session:
        def __init__(self) -> None:
            self.responses = iter((FIND_ITEM_XML, GET_ITEM_XML, GET_ATTACHMENT_XML))
            self.calls: list[dict] = []

        def post(self, url: str, **kwargs):
            self.calls.append({"url": url, **kwargs})
            return Response(next(self.responses))

    session = Session()
    rows = module.fetch_ews_mail_records(
        session,
        ews_url="https://mail.example.invalid/EWS/Exchange.asmx",
        email_addr="mailbox@example.invalid",
        output_root=tmp_path / "ews-output",
    )
    assert [row["source_kind"] for row in rows] == ["mail_body", "ews_attachment"]
    assert len(session.calls) == 3
    assert Path(rows[0]["file_path"]).read_text(encoding="utf-8").endswith("첨부 문서를 검토해 주세요.\n")
    assert Path(rows[1]["file_path"]).read_bytes() == b"%PDF-1.4"
    assert rows[1]["mail_subject"] == "검토 요청"
    assert rows[1]["drm_status"] == "pending"
    assert all("item_id" not in row and "attachment_id" not in row for row in rows)


def test_ews_pipeline_rejects_http_and_non_https_endpoint(tmp_path: Path) -> None:
    module = load_module(EWS_SOURCE, "mail_flow_ews_endpoint_test")
    with pytest.raises(module.EwsReaderError, match="https://"):
        module.fetch_ews_mail_records(
            object(),
            ews_url="http://mail.example.invalid/EWS/Exchange.asmx",
            output_root=tmp_path,
        )


def test_drm_component_passes_mail_body_without_api_credentials(tmp_path: Path) -> None:
    module = load_module(DRM_SOURCE, "mail_flow_drm_body_bypass_test")
    source = tmp_path / "body.txt"
    source.write_text("mail body", encoding="utf-8")
    body = module.process_file_record(
        {"file_path": str(source), "file_name": "body.txt", "source_kind": "mail_body"},
        "",
        "",
        "",
    )
    assert body["file_path"] == str(source)
    assert body["drm_status"] == "not_applicable"


def test_drm_component_posts_ews_attachment_and_writes_plain_text(tmp_path: Path) -> None:
    module = load_module(DRM_SOURCE, "mail_flow_drm_text_api_test")
    source = tmp_path / "001_protected.xlsx"
    source.write_bytes(b"PK protected workbook")

    class Response:
        status_code = 200
        content = "엑셀이 아닌 첨부도 같은 API 응답을 읽습니다.".encode("utf-8")
        apparent_encoding = "utf-8"
        headers = {
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Length": str(len(content)),
        }

        def iter_content(self, chunk_size: int):
            yield self.content

        def close(self) -> None:
            self.closed = True

    class Client:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def post(self, url: str, **kwargs):
            uploaded = kwargs["files"]["file"]
            self.calls.append(
                {
                    "url": url,
                    "filename": uploaded[0],
                    "content": uploaded[1].read(),
                    "mime": uploaded[2],
                    "headers": kwargs["headers"],
                    "params": kwargs["params"],
                    "timeout": kwargs["timeout"],
                    "verify": kwargs["verify"],
                    "allow_redirects": kwargs["allow_redirects"],
                }
            )
            return Response()

    client = Client()
    result = module.process_file_record(
        {
            "file_path": str(source),
            "file_name": "protected.xlsx",
            "source_kind": "ews_attachment",
            "mail_subject": "검토 요청",
        },
        "http://drm.example.internal/DRM/decrypt/text",
        "TOKEN_VALUE",
        "X000000",
        allow_insecure_http=True,
        output_root=tmp_path / "drm-text-output",
        transport=client,
    )
    assert result["mail_subject"] == "검토 요청"
    assert result["original_file_path"] == str(source)
    assert result["original_file_name"] == "protected.xlsx"
    assert result["drm_status"] == "text_extracted"
    assert result["file_name"] == "protected_drm_text.txt"
    assert Path(result["file_path"]).read_text(encoding="utf-8") == "엑셀이 아닌 첨부도 같은 API 응답을 읽습니다."
    assert Path(result["file_path"]).resolve() != source.resolve()
    assert source.read_bytes() == b"PK protected workbook"
    assert client.calls == [
        {
            "url": "http://drm.example.internal/DRM/decrypt/text",
            "filename": "protected.xlsx",
            "content": b"PK protected workbook",
            "mime": "application/octet-stream",
            "headers": {"Authorization": "Bearer TOKEN_VALUE"},
            "params": {"empNo": "X000000"},
            "timeout": 180,
            "verify": False,
            "allow_redirects": False,
        }
    ]


def test_unsupported_archive_becomes_notice_and_does_not_stop_loop(tmp_path: Path) -> None:
    module = load_module(DRM_SOURCE, "mail_flow_unsupported_attachment_test")
    source = tmp_path / "evidence.zip"
    source.write_bytes(b"PK archive content is intentionally not opened")

    result = module.process_file_record(
        {
            "file_path": str(source),
            "file_name": "evidence.zip",
            "source_kind": "ews_attachment",
            "mail_subject": "첨부 검토",
        },
        output_root=tmp_path / "unsupported-output",
    )

    assert result["mail_subject"] == "첨부 검토"
    assert result["processing_path"] == "skipped_unsupported"
    assert result["extraction_error"] == "unsupported_file_type"
    assert result["drm_status"] == "not_applicable"
    assert result["file_name"] == "evidence_unsupported_notice.txt"
    assert "지원하지 않는 첨부파일 형식" in Path(result["file_path"]).read_text(encoding="utf-8")
    assert source.read_bytes() == b"PK archive content is intentionally not opened"


def test_jpeg_attachment_uses_connected_vision_model_and_writes_text(tmp_path: Path) -> None:
    module = load_module(DRM_SOURCE, "mail_flow_jpeg_vision_test")
    source = tmp_path / "equipment.jpg"
    source.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-payload")

    class VisionModel:
        def __init__(self) -> None:
            self.calls: list = []

        async def ainvoke(self, messages):
            self.calls.append(messages)
            return SimpleNamespace(content="설비 전면 사진이며 경고등이 켜져 있습니다.")

    model = VisionModel()
    result = asyncio.run(
        module.process_file_record_with_vision(
            {
                "file_path": str(source),
                "file_name": "equipment.jpg",
                "source_kind": "ews_attachment",
                "mail_subject": "설비 상태 확인",
            },
            model,
            output_root=tmp_path / "vision-output",
        )
    )

    assert result["processing_path"] == "vision_model"
    assert result["vision_status"] == "text_extracted"
    assert result["drm_status"] == "not_applicable"
    assert result["file_name"] == "equipment_vision_text.txt"
    assert Path(result["file_path"]).read_text(encoding="utf-8") == "설비 전면 사진이며 경고등이 켜져 있습니다."
    assert len(model.calls) == 1
    content = model.calls[0][0].content
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_jpeg_without_vision_model_becomes_failure_notice(tmp_path: Path) -> None:
    module = load_module(DRM_SOURCE, "mail_flow_jpeg_missing_model_test")
    source = tmp_path / "equipment.jpeg"
    source.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-payload")

    result = asyncio.run(
        module.process_file_record_with_vision(
            {
                "file_path": str(source),
                "file_name": "equipment.jpeg",
                "source_kind": "ews_attachment",
            },
            None,
            output_root=tmp_path / "vision-notice-output",
        )
    )

    assert result["processing_path"] == "vision_failed"
    assert result["vision_status"] == "failed"
    assert result["extraction_error"] == "vision_model_missing"
    assert "Language Model이 연결되지 않았습니다" in Path(result["file_path"]).read_text(
        encoding="utf-8"
    )


def test_drm_component_auto_and_bypass_modes_pass_original_file_without_api(tmp_path: Path) -> None:
    module = load_module(DRM_SOURCE, "mail_flow_plain_file_modes_test")
    plain = tmp_path / "plain.txt"
    plain.write_text("일반 첨부 본문", encoding="utf-8")

    automatic = module.process_file_record(
        {"file_path": str(plain), "file_name": "plain.txt", "source_kind": "ews_attachment"},
        processing_mode=module.PROCESSING_MODE_AUTO,
    )
    assert automatic["file_path"] == str(plain)
    assert automatic["drm_status"] == "not_required"
    assert automatic["processing_path"] == "original_file"
    assert automatic["local_text_char_count"] == len("일반 첨부 본문")

    legacy = tmp_path / "plain.xls"
    legacy.write_bytes(b"plain legacy workbook")
    bypassed = module.process_file_record(
        {"file_path": str(legacy), "file_name": "plain.xls", "source_kind": "ews_attachment"},
        processing_mode=module.PROCESSING_MODE_BYPASS_DRM,
    )
    assert bypassed["file_path"] == str(legacy)
    assert bypassed["drm_status"] == "bypassed_by_mode"
    assert bypassed["processing_path"] == "original_file"


def test_prompts_are_grounded_and_samples_are_safe() -> None:
    flow = load_flow()
    item_system = node_by_id(flow, "LanguageModelComponent-mailAttachmentItem")["data"]["node"][
        "template"
    ]["system_message"]["value"]
    final_system = node_by_id(flow, "LanguageModelComponent-mailAttachmentFinal")["data"]["node"][
        "template"
    ]["system_message"]["value"]
    prompt_text = node_by_id(flow, "Prompt-mailAttachmentFinal")["data"]["node"]["template"][
        "template"
    ]["value"]
    assert "신뢰할 수 없는 데이터" in item_system
    assert "외부 도구" in final_system
    assert "원문에 없는" in prompt_text
    assert "충돌" in prompt_text
    assert "읽지 못한" in prompt_text

    samples = FLOW_ROOT / "samples"
    required = {
        "EWS_COMPONENT_CONFIG.md",
        "UPLOAD_GUIDE.md",
        "DRM_COMPONENT_CONTRACT.md",
        "sample_ews_mail_records.json",
        "TEST_CASES.md",
    }
    assert all((samples / name).is_file() for name in required)
    combined = "\n".join((samples / name).read_text(encoding="utf-8") for name in required)
    assert "Authorization: Bearer TOKEN_INFO" not in combined
    assert "1234567" not in combined
    assert "email.aaa.com" not in combined
