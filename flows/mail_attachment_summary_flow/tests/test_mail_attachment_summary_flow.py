from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[3]
FLOW_ROOT = ROOT / "flows" / "mail_attachment_summary_flow"
FLOW_PATH = FLOW_ROOT / "mail_attachment_summary_flow.json"
MSG_SOURCE = FLOW_ROOT / "nodes" / "msg_attachment_extractor.py"
DRM_SOURCE = FLOW_ROOT / "nodes" / "drm_unlock_adapter.py"

ALLOWED_NODE_TYPES = {
    "File",
    "MsgAttachmentExtractor",
    "DrmUnlockAdapter",
    "LoopComponent",
    "ParserComponent",
    "LanguageModelComponent",
    "ChatInput",
    "Prompt",
    "ChatOutput",
    "note",
}


def load_flow() -> dict:
    return json.loads(FLOW_PATH.read_text(encoding="utf-8"))


def node_by_id(flow: dict, node_id: str) -> dict:
    return next(node for node in flow["data"]["nodes"] if node["id"] == node_id)


def decode_handle(value: str) -> dict:
    return json.loads(value.replace("œ", '"'))


def load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_package_contract_registers_only_two_flow_internal_nodes() -> None:
    manifest = json.loads((FLOW_ROOT / "manifest.json").read_text(encoding="utf-8"))
    refs = json.loads((FLOW_ROOT / "component_refs.json").read_text(encoding="utf-8"))
    internal = json.loads((FLOW_ROOT / "internal_nodes.json").read_text(encoding="utf-8"))

    assert manifest["status"] == "user_testing"
    assert manifest["version"] == "0.2.0"
    assert manifest["source_export_version"] == "1.8.2"
    assert refs == {"flow_id": "mail_attachment_summary_flow", "components": []}
    assert internal["flow_id"] == "mail_attachment_summary_flow"
    assert {item["id"] for item in internal["nodes"]} == {
        "msg_attachment_extractor",
        "drm_unlock_adapter",
    }


def test_flow_contains_expected_msg_drm_graph() -> None:
    flow = load_flow()
    nodes = flow["data"]["nodes"]
    edges = flow["data"]["edges"]
    assert len(nodes) == 13
    assert len(edges) == 11
    assert {node["data"]["type"] for node in nodes} <= ALLOWED_NODE_TYPES
    assert not {"Agent", "APIRequest", "MCPTools", "RunFlow"} & {
        node["data"]["type"] for node in nodes
    }

    ids = {node["id"] for node in nodes}
    for edge in edges:
        assert edge["source"] in ids
        assert edge["target"] in ids
        assert "┇" not in edge["sourceHandle"]
        assert "┇" not in edge["targetHandle"]
        assert decode_handle(edge["sourceHandle"]) == edge["data"]["sourceHandle"]
        assert decode_handle(edge["targetHandle"]) == edge["data"]["targetHandle"]

    edge_pairs = {(edge["source"], edge["target"]) for edge in edges}
    assert (
        "MsgAttachmentExtractor-mailAttachments",
        "LoopComponent-mailAttachments",
    ) in edge_pairs
    assert ("LoopComponent-mailAttachments", "DrmUnlockAdapter-mailAttachments") in edge_pairs
    assert ("DrmUnlockAdapter-mailAttachments", "File-mailAttachments") in edge_pairs


def test_internal_source_is_embedded_exactly_and_drm_is_fail_closed() -> None:
    flow = load_flow()
    source_by_node = {
        "MsgAttachmentExtractor-mailAttachments": MSG_SOURCE.read_text(encoding="utf-8"),
        "DrmUnlockAdapter-mailAttachments": DRM_SOURCE.read_text(encoding="utf-8"),
    }
    for node_id, source in source_by_node.items():
        embedded = node_by_id(flow, node_id)["data"]["node"]["template"]["code"]["value"]
        assert embedded == source
    assert "raise DrmAdapterNotConfigured" in source_by_node["DrmUnlockAdapter-mailAttachments"]
    assert "company_drm_unlock" in source_by_node["DrmUnlockAdapter-mailAttachments"]


def test_msg_input_and_read_file_use_local_dynamic_path_contract() -> None:
    flow = load_flow()
    msg_template = node_by_id(flow, "MsgAttachmentExtractor-mailAttachments")["data"]["node"]["template"]
    file_node = node_by_id(flow, "File-mailAttachments")
    file_template = file_node["data"]["node"]["template"]

    assert msg_template["msg_files"]["fileTypes"] == ["msg"]
    assert msg_template["msg_files"]["list"] is True
    assert msg_template["msg_files"]["file_path"] in ("", [])
    assert file_template["storage_location"]["value"] == [{"name": "Local", "icon": "hard-drive"}]
    assert file_template["path"]["file_path"] == []
    assert file_template["advanced_mode"]["value"] is True
    assert file_template["ocr_engine"]["value"] == "easyocr"
    assert file_template["delete_server_file_after_processing"]["value"] is True
    assert file_node["data"]["selected_output"] == "dataframe"


def test_models_have_no_default_provider_or_secret() -> None:
    flow = load_flow()
    for node_id in (
        "LanguageModelComponent-mailAttachmentItem",
        "LanguageModelComponent-mailAttachmentFinal",
    ):
        template = node_by_id(flow, node_id)["data"]["node"]["template"]
        assert template["model"]["value"] in ([], "", None)
        assert template["api_key"]["value"] == ""
        assert template["temperature"]["value"] == 0.1
        assert template["stream"]["value"] is False


def test_msg_extractor_flattens_body_and_regular_attachments(tmp_path: Path) -> None:
    module = load_module(MSG_SOURCE, "mail_flow_msg_extractor_test")
    msg_path = tmp_path / "review.msg"
    msg_path.write_bytes(module._OLE_COMPOUND_MAGIC + b"fake-msg")

    class Attachment:
        longFilename = "review.pdf"
        shortFilename = None
        name = None
        hidden = False
        cid = None
        data = b"%PDF-1.4 fake"

    class HiddenAttachment:
        longFilename = "logo.png"
        shortFilename = None
        name = None
        hidden = True
        cid = "logo"
        data = b"PNG"

    class Message:
        subject = "검토 요청"
        sender = "sender@example.invalid"
        to = "reviewer@example.invalid"
        cc = ""
        date = "2026-07-16"
        body = "첨부 문서를 검토해 주세요."
        htmlBody = None
        attachments = [Attachment(), HiddenAttachment()]

        def close(self) -> None:
            self.closed = True

    rows = module.extract_msg_records(
        [str(msg_path)],
        opener=lambda _path: Message(),
        output_root=tmp_path / "output",
    )
    assert [row["source_kind"] for row in rows] == ["mail_body", "msg_attachment"]
    assert rows[0]["parent_msg"] == "review.msg"
    assert rows[1]["file_name"] == "review.pdf"
    assert rows[1]["drm_status"] == "pending"
    assert Path(rows[0]["file_path"]).read_text(encoding="utf-8").endswith(
        "첨부 문서를 검토해 주세요.\n"
    )
    assert Path(rows[1]["file_path"]).read_bytes() == b"%PDF-1.4 fake"


def test_msg_extractor_rejects_non_ole_or_whole_msg_drm(tmp_path: Path) -> None:
    module = load_module(MSG_SOURCE, "mail_flow_msg_extractor_invalid_test")
    msg_path = tmp_path / "protected.msg"
    msg_path.write_bytes(b"not-an-ole-msg")
    with pytest.raises(module.MsgExtractionError, match="MSG 자체가 DRM"):
        module.extract_msg_records(
            [str(msg_path)],
            opener=lambda _path: None,
            output_root=tmp_path / "output",
        )


def test_drm_adapter_passes_body_but_fails_closed_for_attachment(tmp_path: Path) -> None:
    module = load_module(DRM_SOURCE, "mail_flow_drm_adapter_fail_closed_test")
    source = tmp_path / "body.txt"
    source.write_text("mail body", encoding="utf-8")
    body = module.unlock_record(
        {"file_path": str(source), "file_name": "body.txt", "source_kind": "mail_body"}
    )
    assert body["file_path"] == str(source)
    assert body["drm_status"] == "not_applicable"

    with pytest.raises(module.DrmAdapterNotConfigured):
        module.unlock_record(
            {"file_path": str(source), "file_name": "attachment.txt", "source_kind": "msg_attachment"},
            output_root=tmp_path / "drm-output",
        )


def test_drm_adapter_requires_new_output_file_and_preserves_metadata(tmp_path: Path) -> None:
    module = load_module(DRM_SOURCE, "mail_flow_drm_adapter_success_test")
    source = tmp_path / "protected.pdf"
    source.write_bytes(b"protected")

    def fake_unlocker(source_path: Path, destination_path: Path) -> str:
        shutil.copy2(source_path, destination_path)
        return "unlocked"

    result = module.unlock_record(
        {
            "file_path": str(source),
            "file_name": "protected.pdf",
            "source_kind": "msg_attachment",
            "parent_msg": "review.msg",
        },
        unlocker=fake_unlocker,
        output_root=tmp_path / "drm-output",
    )
    assert result["parent_msg"] == "review.msg"
    assert result["original_file_path"] == str(source)
    assert result["drm_status"] == "unlocked"
    assert Path(result["file_path"]).read_bytes() == b"protected"
    assert Path(result["file_path"]).resolve() != source.resolve()


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
        "UPLOAD_GUIDE.md",
        "DRM_COMPONENT_CONTRACT.md",
        "sample_extracted_msg_records.json",
        "TEST_CASES.md",
    }
    assert all((samples / name).is_file() for name in required)
    combined = "\n".join((samples / name).read_text(encoding="utf-8") for name in required)
    assert "Bearer " not in combined
    assert "api_key" not in combined.lower()
