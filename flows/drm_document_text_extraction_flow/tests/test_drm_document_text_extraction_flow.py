from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[3]
FLOW_ROOT = ROOT / "flows" / "drm_document_text_extraction_flow"
FLOW_PATH = FLOW_ROOT / "drm_document_text_extraction_flow.json"
COMPONENT_SOURCE = (
    ROOT
    / "components"
    / "drm_document_text_extractor"
    / "drm_document_text_extractor.py"
)


class FakeResponse:
    def __init__(
        self,
        content: bytes,
        *,
        status_code: int = 200,
        content_type: str = "text/plain; charset=utf-8",
        apparent_encoding: str = "utf-8",
    ) -> None:
        self.content = content
        self.status_code = status_code
        self.headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(content)),
        }
        self.apparent_encoding = apparent_encoding
        self.closed = False

    def iter_content(self, chunk_size: int):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start : start + chunk_size]

    def close(self) -> None:
        self.closed = True


class FakeClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url: str, **kwargs):
        file_tuple = kwargs["files"]["file"]
        self.calls.append(
            {
                "url": url,
                "filename": file_tuple[0],
                "content": file_tuple[1].read(),
                "mime": file_tuple[2],
                "headers": dict(kwargs["headers"]),
                "params": dict(kwargs["params"]),
                "timeout": kwargs["timeout"],
                "verify": kwargs["verify"],
                "allow_redirects": kwargs["allow_redirects"],
                "stream": kwargs["stream"],
            }
        )
        return self.responses.pop(0)


def load_component_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("drm_document_text_extractor_test", COMPONENT_SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_flow() -> dict:
    return json.loads(FLOW_PATH.read_text(encoding="utf-8"))


def decode_handle(value: str) -> dict:
    return json.loads(value.replace("œ", '"'))


def test_package_contract_and_supported_formats() -> None:
    manifest = json.loads((FLOW_ROOT / "manifest.json").read_text(encoding="utf-8"))
    refs = json.loads((FLOW_ROOT / "component_refs.json").read_text(encoding="utf-8"))
    internal = json.loads((FLOW_ROOT / "internal_nodes.json").read_text(encoding="utf-8"))
    component_manifest = json.loads(
        (COMPONENT_SOURCE.parent / "manifest.json").read_text(encoding="utf-8")
    )

    assert manifest["status"] == "user_testing"
    assert manifest["version"] == "0.2.0"
    assert manifest["runtime_ready"] is True
    assert refs == {
        "flow_id": "drm_document_text_extraction_flow",
        "components": [{"id": "drm_document_text_extractor", "version": "0.2.0"}],
    }
    assert internal["nodes"] == []
    file_input = next(item for item in component_manifest["inputs"] if item["name"] == "document_files")
    assert {"pdf", "pptx", "xlsx", "docx", "hwp", "txt", "csv", "png", "jpg"} <= set(
        file_input["file_types"]
    )
    assert file_input["required"] is False
    assert {item["name"] for item in component_manifest["inputs"]} >= {
        "document_files",
        "file_record",
        "drm_api_url",
        "drm_token",
        "employee_no",
    }
    assert {item["name"]: item["types"] for item in component_manifest["outputs"]} == {
        "extracted_text": ["Message"],
        "processed_file": ["Data"],
    }


def test_flow_embeds_component_source_and_connects_message_to_chat_output() -> None:
    flow = load_flow()
    assert flow["name"] == "drm_document_text_extraction_flow"
    assert len(flow["data"]["nodes"]) == 2
    assert len(flow["data"]["edges"]) == 1
    nodes = {node["id"]: node for node in flow["data"]["nodes"]}
    extractor = nodes["DrmDocumentTextExtractor-drmDocument"]
    template = extractor["data"]["node"]["template"]
    assert template["code"]["value"] == COMPONENT_SOURCE.read_text(encoding="utf-8")
    assert template["drm_api_url"]["value"] == ""
    assert template["drm_token"]["value"] == ""
    assert template["employee_no"]["value"] == ""
    assert template["drm_token"]["password"] is True
    assert template["employee_no"]["password"] is True
    assert template["allowed_drm_hosts"]["value"] == ""
    assert template["allow_insecure_http"]["value"] is False
    assert template["verify_tls"]["value"] is True
    assert template["document_files"]["file_path"] in ("", [])
    assert template["document_files"]["required"] is True
    assert template["file_record"]["show"] is False

    edge = flow["data"]["edges"][0]
    assert (edge["source"], edge["target"]) == (
        "DrmDocumentTextExtractor-drmDocument",
        "ChatOutput-drmDocumentText",
    )
    assert decode_handle(edge["sourceHandle"]) == edge["data"]["sourceHandle"]
    assert decode_handle(edge["targetHandle"]) == edge["data"]["targetHandle"]
    assert edge["data"]["sourceHandle"]["name"] == "extracted_text"
    assert edge["data"]["targetHandle"]["fieldName"] == "input_value"


def test_extracts_multiple_files_with_original_request_contract(tmp_path: Path) -> None:
    module = load_component_module()
    pdf = tmp_path / "guide.pdf"
    xlsx = tmp_path / "cost.xlsx"
    pdf.write_bytes(b"%PDF fake")
    xlsx.write_bytes(b"PK fake-xlsx")
    first_text = "PDF 본문입니다."
    second_text = "엑셀 본문입니다."
    responses = [
        FakeResponse(first_text.encode("utf-8")),
        FakeResponse(
            second_text.encode("cp949"),
            content_type="text/plain; charset=cp949",
            apparent_encoding="cp949",
        ),
    ]
    client = FakeClient(responses)

    result = module.extract_document_texts(
        [str(pdf), str(xlsx)],
        "https://drm.example.internal/DRM/decrypt/text",
        "TOKEN_VALUE",
        "X000000",
        "drm.example.internal",
        transport=client,
    )

    assert result["success"] is True
    assert [item["file_name"] for item in result["data"]] == ["guide.pdf", "cost.xlsx"]
    assert [item["text"] for item in result["data"]] == [first_text, second_text]
    assert result["meta"]["file_count"] == 2
    assert all(response.closed for response in responses)
    assert [call["content"] for call in client.calls] == [b"%PDF fake", b"PK fake-xlsx"]
    for call in client.calls:
        assert call["mime"] == "application/octet-stream"
        assert call["headers"] == {"Authorization": "Bearer TOKEN_VALUE"}
        assert call["params"] == {"empNo": "X000000"}
        assert call["timeout"] == 180
        assert call["verify"] is True
        assert call["allow_redirects"] is False
        assert call["stream"] is True

    message = module.format_extraction_message(result)
    assert "[FILE 1/2] guide.pdf" in message
    assert first_text in message
    assert "[FILE 2/2] cost.xlsx" in message
    assert second_text in message


def test_allowlist_and_http_policy_fail_before_upload(tmp_path: Path) -> None:
    module = load_component_module()
    document = tmp_path / "review.docx"
    document.write_bytes(b"PK fake-docx")
    client = FakeClient([FakeResponse(b"unused")])

    with pytest.raises(ValueError, match="허용 서버 목록"):
        module.extract_document_texts(
            str(document),
            "https://other.example.internal/DRM/decrypt/text",
            "TOKEN_VALUE",
            "X000000",
            "drm.example.internal",
            transport=client,
        )
    assert client.calls == []

    with pytest.raises(ValueError, match="HTTP로 보낼 수 없습니다"):
        module.extract_document_texts(
            str(document),
            "http://drm.example.internal/DRM/decrypt/text",
            "TOKEN_VALUE",
            "X000000",
            "drm.example.internal",
            transport=client,
        )
    assert client.calls == []


def test_http_can_be_explicitly_enabled_for_closed_network(tmp_path: Path) -> None:
    module = load_component_module()
    document = tmp_path / "legacy.xls"
    document.write_bytes(b"legacy-xls")
    response = FakeResponse("셀 내용".encode("utf-8"))
    client = FakeClient([response])

    result = module.extract_document_texts(
        str(document),
        "http://drm.example.internal/DRM/decrypt/text",
        "TOKEN_VALUE",
        "X000000",
        "drm.example.internal",
        allow_insecure_http=True,
        transport=client,
    )
    assert result["data"][0]["text"] == "셀 내용"
    assert client.calls[0]["verify"] is False


def test_invalid_format_and_size_fail_before_upload(tmp_path: Path) -> None:
    module = load_component_module()
    text_file = tmp_path / "archive.zip"
    text_file.write_bytes(b"not supported")
    client = FakeClient([FakeResponse(b"unused")])
    with pytest.raises(ValueError, match="지원하지 않는 파일 형식"):
        module.extract_document_texts(
            str(text_file),
            "https://drm.example.internal/DRM/decrypt/text",
            "TOKEN_VALUE",
            "X000000",
            "drm.example.internal",
            transport=client,
        )

    pdf = tmp_path / "large.pdf"
    pdf.write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="개별 크기 제한"):
        module.extract_document_texts(
            str(pdf),
            "https://drm.example.internal/DRM/decrypt/text",
            "TOKEN_VALUE",
            "X000000",
            "drm.example.internal",
            max_file_size_mb=1,
            transport=client,
        )
    assert client.calls == []


def test_http_error_does_not_expose_response_body_or_secret(tmp_path: Path) -> None:
    module = load_component_module()
    document = tmp_path / "review.pptx"
    document.write_bytes(b"PK fake-pptx")
    secret = "TOP_SECRET_TOKEN"
    response = FakeResponse(
        b"sensitive server details TOP_SECRET_TOKEN",
        status_code=500,
    )
    client = FakeClient([response])

    with pytest.raises(module.DrmTextExtractionError) as caught:
        module.extract_document_texts(
            str(document),
            "https://drm.example.internal/DRM/decrypt/text",
            secret,
            "X000000",
            "drm.example.internal",
            transport=client,
        )
    message = str(caught.value)
    assert "HTTP 500" in message
    assert secret not in message
    assert "sensitive server details" not in message
    assert response.closed is True


def test_samples_and_exports_do_not_contain_real_credentials() -> None:
    texts = []
    for path in [
        FLOW_ROOT / "README.md",
        FLOW_ROOT / "samples" / "INPUT_EXAMPLE.md",
        COMPONENT_SOURCE.parent / "USAGE_GUIDE.md",
        FLOW_PATH,
    ]:
        texts.append(path.read_text(encoding="utf-8"))
    combined = "\n".join(texts)
    assert "TOKEN_INFO" not in combined
    assert "X123456" not in combined
    assert "drmtest.com" not in combined
