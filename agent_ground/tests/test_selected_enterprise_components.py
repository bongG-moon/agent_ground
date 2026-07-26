from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest
from lfx.custom.eval import eval_custom_component_code
from lfx.custom.utils import create_component_template


ROOT = Path(__file__).resolve().parents[1]
COMPONENT_ID = "multi_image_base64_encoder"


def load_component() -> ModuleType:
    path = ROOT / "components" / COMPONENT_ID / f"{COMPONENT_ID}.py"
    spec = importlib.util.spec_from_file_location(f"test_{COMPONENT_ID}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Component를 불러올 수 없습니다: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def image_module() -> ModuleType:
    return load_component()


def png_bytes(label: bytes = b"agent-ground") -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\x0dIHDR"
        + b"\x00\x00\x00\x01"
        + b"\x00\x00\x00\x01"
        + label
    )


def jpeg_bytes(label: bytes = b"agent-ground") -> bytes:
    return b"\xff\xd8\xff\xe0" + label + b"\xff\xd9"


def test_multi_image_preserves_order_and_round_trips(
    image_module: ModuleType,
    tmp_path: Path,
) -> None:
    first = tmp_path / "before.png"
    second = tmp_path / "after.jpg"
    first_bytes = png_bytes(b"first")
    second_bytes = jpeg_bytes(b"second")
    first.write_bytes(first_bytes)
    second.write_bytes(second_bytes)

    result = image_module.encode_image_files([str(first), str(second)])

    assert result["success"] is True
    assert result["order_preserved"] is True
    assert [(item["index"], item["position"], item["filename"]) for item in result["items"]] == [
        (0, 1, "before.png"),
        (1, 2, "after.jpg"),
    ]
    assert base64.b64decode(result["items"][0]["value"], validate=True) == first_bytes
    assert base64.b64decode(result["items"][1]["value"], validate=True) == second_bytes
    assert str(tmp_path) not in json.dumps(result, ensure_ascii=False)


def test_multi_image_data_url_errors_limits_and_svg(
    image_module: ModuleType,
    tmp_path: Path,
) -> None:
    valid = tmp_path / "valid.png"
    disguised = tmp_path / "disguised.jpg"
    valid.write_bytes(png_bytes())
    disguised.write_bytes(png_bytes(b"disguised"))

    data_url = image_module.encode_image_files([str(valid)], output_format="data_url")
    assert data_url["items"][0]["value"].startswith("data:image/png;base64,")

    partial = image_module.encode_image_files(
        [str(valid), str(disguised)],
        error_policy="skip_invalid",
    )
    assert partial["success"] is True
    assert [item["index"] for item in partial["items"]] == [0]
    assert partial["errors"][0]["code"] == "extension_signature_mismatch"

    rejected = image_module.encode_image_files(
        [str(valid), str(disguised)],
        error_policy="reject_batch",
    )
    assert rejected["success"] is False
    assert rejected["items"] == []

    safe_svg = tmp_path / "safe.svg"
    unsafe_svg = tmp_path / "unsafe.svg"
    safe_svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>',
        encoding="utf-8",
    )
    unsafe_svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        encoding="utf-8",
    )
    assert image_module.encode_image_files([str(safe_svg)])["errors"][0]["code"] == "svg_disabled"
    assert image_module.encode_image_files([str(safe_svg)], allow_svg=True)["success"] is True
    assert (
        image_module.encode_image_files([str(unsafe_svg)], allow_svg=True)["errors"][0]["code"]
        == "unsafe_svg"
    )


def test_multi_image_compiles_to_langflow_192_template() -> None:
    path = ROOT / "components" / COMPONENT_ID / f"{COMPONENT_ID}.py"
    source = path.read_text(encoding="utf-8")
    component_class = eval_custom_component_code(source)
    config, instance = create_component_template(
        {"code": source, "output_types": []},
        module_name="agent_ground.selected_test.multi_image",
    )
    assert instance.__class__.__name__ == component_class.__name__
    assert config["field_order"] == [item.name for item in component_class.inputs]
    assert [item["name"] for item in config["outputs"]] == [
        item.name for item in component_class.outputs
    ]
    assert config["template"]["image_files"]["list"] is True
    assert config["template"]["image_files"]["display_name"] == "이미지 파일"
