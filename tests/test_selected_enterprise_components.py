from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from lfx.base.tools.run_flow import RunFlowBaseComponent
from lfx.custom.eval import eval_custom_component_code
from lfx.custom.utils import create_component_template
from lfx.io.schema import create_input_schema_from_dict
from lfx.schema import Data
from lfx.schema.dotdict import dotdict


ROOT = Path(__file__).resolve().parents[1]
COMPONENT_IDS = ("multi_image_base64_encoder", "cached_named_run_flow_tool")


def load_component(component_id: str) -> ModuleType:
    path = ROOT / "components" / component_id / f"{component_id}.py"
    spec = importlib.util.spec_from_file_location(f"test_{component_id}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Component를 불러올 수 없습니다: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def modules() -> dict[str, ModuleType]:
    return {component_id: load_component(component_id) for component_id in COMPONENT_IDS}


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


def component_instance(module: ModuleType, component_id: str):
    source = (ROOT / "components" / component_id / f"{component_id}.py").read_text(encoding="utf-8")
    class_name = {
        "multi_image_base64_encoder": "MultiImageBase64Encoder",
        "cached_named_run_flow_tool": "CachedNamedRunFlowTool",
    }[component_id]
    return getattr(module, class_name)(_code=source)


def test_multi_image_preserves_order_and_round_trips(modules: dict[str, ModuleType], tmp_path: Path) -> None:
    first = tmp_path / "before.png"
    second = tmp_path / "after.jpg"
    first_bytes = png_bytes(b"first")
    second_bytes = jpeg_bytes(b"second")
    first.write_bytes(first_bytes)
    second.write_bytes(second_bytes)

    result = modules["multi_image_base64_encoder"].encode_image_files([str(first), str(second)])

    assert result["success"] is True
    assert result["order_preserved"] is True
    assert [(item["index"], item["position"], item["filename"]) for item in result["items"]] == [
        (0, 1, "before.png"),
        (1, 2, "after.jpg"),
    ]
    assert base64.b64decode(result["items"][0]["value"], validate=True) == first_bytes
    assert base64.b64decode(result["items"][1]["value"], validate=True) == second_bytes
    assert str(tmp_path) not in json.dumps(result, ensure_ascii=False)


def test_multi_image_data_url_errors_limits_and_svg(modules: dict[str, ModuleType], tmp_path: Path) -> None:
    valid = tmp_path / "valid.png"
    disguised = tmp_path / "disguised.jpg"
    valid.write_bytes(png_bytes())
    disguised.write_bytes(png_bytes(b"disguised"))

    data_url = modules["multi_image_base64_encoder"].encode_image_files(
        [str(valid)], output_format="data_url"
    )
    assert data_url["items"][0]["value"].startswith("data:image/png;base64,")

    partial = modules["multi_image_base64_encoder"].encode_image_files(
        [str(valid), str(disguised)], error_policy="skip_invalid"
    )
    assert partial["success"] is True
    assert [item["index"] for item in partial["items"]] == [0]
    assert partial["errors"][0]["code"] == "extension_signature_mismatch"

    rejected = modules["multi_image_base64_encoder"].encode_image_files(
        [str(valid), str(disguised)], error_policy="reject_batch"
    )
    assert rejected["success"] is False and rejected["items"] == []

    too_large = tmp_path / "large.jpg"
    too_large.write_bytes(jpeg_bytes(b"x" * (1024 * 1024)))
    size_result = modules["multi_image_base64_encoder"].encode_image_files(
        [str(too_large)], max_file_size_mb=1
    )
    assert size_result["success"] is False
    assert size_result["errors"][0]["code"] == "file_size_exceeded"

    safe_svg = tmp_path / "safe.svg"
    unsafe_svg = tmp_path / "unsafe.svg"
    safe_svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>', encoding="utf-8")
    unsafe_svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', encoding="utf-8")
    assert modules["multi_image_base64_encoder"].encode_image_files([str(safe_svg)])["errors"][0]["code"] == "svg_disabled"
    assert modules["multi_image_base64_encoder"].encode_image_files(
        [str(safe_svg)], allow_svg=True
    )["success"] is True
    assert modules["multi_image_base64_encoder"].encode_image_files(
        [str(unsafe_svg)], allow_svg=True
    )["errors"][0]["code"] == "unsafe_svg"


def test_cached_tool_resolves_one_same_folder_flow_by_actual_id(
    modules: dict[str, ModuleType], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = modules["cached_named_run_flow_tool"]
    component = component_instance(module, "cached_named_run_flow_tool")
    component._user_id = "user-1"
    component._vertex = SimpleNamespace(graph=SimpleNamespace(flow_id="parent-flow", session_id="parent-session"))
    component.flow_name_selected = "child-flow"
    component.allow_cross_folder = False

    folder_calls: list[tuple[str, str]] = []

    async def fake_list_folder(*, user_id: str, flow_id: str):
        folder_calls.append((user_id, flow_id))
        return [Data(data={"id": "child-id", "name": "child-flow", "updated_at": "2026-07-12T01:02:03.123456Z"})]

    async def fail_list_user(*, user_id: str):
        raise AssertionError(f"전체 사용자 조회를 호출하면 안 됩니다: {user_id}")

    base_calls: list[tuple[str | None, str | None, str | None]] = []
    expected_graph = SimpleNamespace(
        description="child",
        updated_at="2026-07-12T01:02:03.123456Z",
        vertices=[SimpleNamespace(id="ChatInput-runtime", data={"type": "ChatInput"}, display_name="Chat Input")],
    )

    async def fake_base_get_graph(self, flow_name_selected=None, flow_id_selected=None, updated_at=None):
        base_calls.append((flow_name_selected, flow_id_selected, updated_at))
        return expected_graph

    monkeypatch.setattr(module, "list_flows_by_flow_folder", fake_list_folder)
    monkeypatch.setattr(module, "list_flows", fail_list_user)
    monkeypatch.setattr(RunFlowBaseComponent, "get_graph", fake_base_get_graph)

    resolved = asyncio.run(component.get_graph("child-flow"))

    assert resolved is expected_graph
    assert folder_calls == [("user-1", "parent-flow")]
    assert base_calls == [("child-flow", "child-id", "2026-07-12T01:02:03.123456Z")]
    assert component.flow_id_selected == "child-id"
    assert component._resolved_chat_input_id == "ChatInput-runtime"
    assert component._attributes["flow_name_selected_updated_at"].endswith(".123456Z")

    # 하위 Flow 재import로 Chat Input ID가 바뀌면 같은 Component가 현재 그래프 ID를 다시 사용합니다.
    expected_graph.vertices = [
        SimpleNamespace(id="ChatInput-reimported", data={"type": "ChatInput"}, display_name="Chat Input")
    ]
    asyncio.run(component.get_graph("child-flow"))
    assert component._resolved_chat_input_id == "ChatInput-reimported"


@pytest.mark.parametrize("records", [[], [
    {"id": "one", "name": "child-flow", "updated_at": "2026-07-12T01:00:00Z"},
    {"id": "two", "name": "child-flow", "updated_at": "2026-07-12T01:01:00Z"},
]])
def test_cached_tool_rejects_missing_or_duplicate_names(
    modules: dict[str, ModuleType], monkeypatch: pytest.MonkeyPatch, records: list[dict]
) -> None:
    module = modules["cached_named_run_flow_tool"]
    component = component_instance(module, "cached_named_run_flow_tool")
    component._user_id = "user-1"
    component._vertex = SimpleNamespace(graph=SimpleNamespace(flow_id="parent-flow", session_id="session"))
    component.allow_cross_folder = False

    async def fake_list_folder(*, user_id: str, flow_id: str):
        del user_id, flow_id
        return [Data(data=record) for record in records]

    monkeypatch.setattr(module, "list_flows_by_flow_folder", fake_list_folder)
    with pytest.raises(ValueError):
        asyncio.run(component.get_graph("child-flow"))


def test_cached_tool_rejects_direct_self_recursion(
    modules: dict[str, ModuleType], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = modules["cached_named_run_flow_tool"]
    component = component_instance(module, "cached_named_run_flow_tool")
    component._user_id = "user-1"
    component._vertex = SimpleNamespace(graph=SimpleNamespace(flow_id="parent-flow", session_id="session"))
    component.allow_cross_folder = True

    async def fake_list_user(*, user_id: str):
        del user_id
        return [Data(data={"id": "parent-flow", "name": "router-flow", "updated_at": "2026-07-12T01:00:00Z"})]

    monkeypatch.setattr(module, "list_flows", fake_list_user)
    with pytest.raises(ValueError, match="자신"):
        asyncio.run(component.get_graph("router-flow"))


def test_cached_tool_cross_folder_requires_explicit_opt_in(
    modules: dict[str, ModuleType], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = modules["cached_named_run_flow_tool"]
    component = component_instance(module, "cached_named_run_flow_tool")
    component._user_id = "user-1"
    component._vertex = SimpleNamespace(graph=SimpleNamespace(flow_id="parent-flow", session_id="session"))
    component.allow_cross_folder = True

    async def fake_list_user(*, user_id: str):
        assert user_id == "user-1"
        return [Data(data={"id": "child-id", "name": "remote-child", "updated_at": "2026-07-12T01:00:00Z"})]

    async def fail_list_folder(*, user_id: str, flow_id: str):
        raise AssertionError(f"같은 폴더 조회를 호출하면 안 됩니다: {user_id}/{flow_id}")

    async def fake_base_get_graph(self, flow_name_selected=None, flow_id_selected=None, updated_at=None):
        del self
        return SimpleNamespace(
            flow_name=flow_name_selected,
            flow_id=flow_id_selected,
            updated_at=updated_at,
            vertices=[SimpleNamespace(id="ChatInput-remote", data={"type": "ChatInput"}, display_name="Chat Input")],
        )

    monkeypatch.setattr(module, "list_flows", fake_list_user)
    monkeypatch.setattr(module, "list_flows_by_flow_folder", fail_list_folder)
    monkeypatch.setattr(RunFlowBaseComponent, "get_graph", fake_base_get_graph)
    result = asyncio.run(component.get_graph("remote-child"))
    assert result.flow_id == "child-id"

    async def duplicate_list_user(*, user_id: str):
        assert user_id == "user-1"
        return [
            Data(data={"id": "child-one", "name": "remote-child", "updated_at": "2026-07-12T01:00:00Z"}),
            Data(data={"id": "child-two", "name": "remote-child", "updated_at": "2026-07-12T01:01:00Z"}),
        ]

    monkeypatch.setattr(module, "list_flows", duplicate_list_user)
    with pytest.raises(ValueError, match="여러 개"):
        asyncio.run(component.get_graph("remote-child"))


def test_cached_tool_compacts_chat_input_customizes_tool_and_inherits_session(
    modules: dict[str, ModuleType], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = modules["cached_named_run_flow_tool"]
    component = component_instance(module, "cached_named_run_flow_tool")
    chat_vertex = SimpleNamespace(id="chat-1", data={"type": "ChatInput"}, display_name="Chat Input")
    second_chat_vertex = SimpleNamespace(id="chat-2", data={"type": "ChatInput"}, display_name="Chat Input")

    def fake_get_new_fields(self, inputs_vertex):
        del self
        return [
            *[
                dotdict(
                    {
                        "name": f"{vertex.id}~input_value",
                        "display_name": "입력",
                        "info": "",
                        "required": False,
                        "value": "",
                        "tool_mode": True,
                        "input_types": ["Message"],
                        "type": str,
                        "advanced": False,
                    }
                )
                for vertex in inputs_vertex
                if vertex.data.get("type") == "ChatInput"
            ],
            dotdict(
                {
                    "name": "prompt-1~template",
                    "display_name": "내부 Prompt",
                    "info": "",
                    "required": False,
                    "value": "",
                    "tool_mode": True,
                    "input_types": [],
                    "type": str,
                    "advanced": False,
                }
            ),
        ]

    monkeypatch.setattr(RunFlowBaseComponent, "get_new_fields", fake_get_new_fields)
    question_fields = component.get_new_fields([chat_vertex])
    assert len(question_fields) == 1
    assert question_fields[0]["name"] == "question"
    assert question_fields[0]["display_name"] == "사용자 질문"
    assert question_fields[0]["required"] is True
    assert question_fields[0]["tool_mode"] is True
    assert "~" not in question_fields[0]["name"] and "-" not in question_fields[0]["name"]
    assert component._resolved_chat_input_id == "chat-1"

    question_schema = create_input_schema_from_dict(question_fields, param_key="flow_tweak_data")
    schema_json = question_schema.model_json_schema()
    question_ref = schema_json["properties"]["flow_tweak_data"]["$ref"].split("/")[-1]
    question_inner = schema_json["$defs"][question_ref]
    assert schema_json["required"] == ["flow_tweak_data"]
    assert list(question_inner["properties"]) == ["question"]
    assert question_inner["required"] == ["question"]
    question_schema(flow_tweak_data={"question": "현재 도메인 알려줘"})
    with pytest.raises(Exception):
        question_schema(flow_tweak_data={})
    with pytest.raises(Exception):
        question_schema(flow_tweak_data={"ChatInput_xVKPV_input_value": "잘못된 키"})
    with pytest.raises(ValueError, match="정확히 하나"):
        component.get_new_fields([])
    with pytest.raises(ValueError, match="정확히 하나"):
        component.get_new_fields([chat_vertex, second_chat_vertex])

    assert module._question_tweaks("ChatInput-runtime", {"question": "  현재 도메인 알려줘  "}) == {
        "ChatInput-runtime": {"input_value": "현재 도메인 알려줘"}
    }

    class ToolQuestion:
        def model_dump(self):
            return {"question": "현재 등록된 계산 로직 알려줘"}

    component._resolved_chat_input_id = "ChatInput-imported"
    component._attributes = {"flow_tweak_data": ToolQuestion()}
    assert component._build_flow_tweak_data() == {
        "ChatInput-imported": {"input_value": "현재 등록된 계산 로직 알려줘"}
    }
    assert component._build_inputs(component._build_flow_tweak_data()) == [
        {"components": ["ChatInput-imported"], "input_value": "현재 등록된 계산 로직 알려줘"}
    ]

    with pytest.raises(ValueError, match="질문이 비어"):
        module._question_tweaks(
            "ChatInput-runtime",
            {"ChatInput_runtime_input_value": "provider가 바꾼 잘못된 키"},
        )
    with pytest.raises(ValueError, match="질문이 비어"):
        module._question_tweaks("ChatInput-runtime", {"question": "   "})

    tool = SimpleNamespace(name="", description="", tags=[], return_direct=False)

    async def fake_get_tools(self):
        del self
        return [tool]

    monkeypatch.setattr(RunFlowBaseComponent, "_get_tools", fake_get_tools)
    component.tool_name = "run_child_flow"
    component.tool_description = "문서 조회 요청일 때만 사용합니다."
    component.return_direct = True
    component.flow_name_selected = "child-flow"
    component.cache_flow = True
    tools = asyncio.run(component._get_tools())
    assert tools == [tool]
    assert tool.name == "run_child_flow" and tool.return_direct is True
    assert component.status["status"] == "도구 준비 완료"
    assert "문서 조회" not in json.dumps(component.status, ensure_ascii=False)

    component.tool_name = "잘못된 도구 이름"
    with pytest.raises(ValueError, match="영문"):
        asyncio.run(component._get_tools())
    component.tool_name = "_invalid_start"
    with pytest.raises(ValueError, match="첫 글자"):
        asyncio.run(component._get_tools())
    component.tool_name = "a" * 65
    with pytest.raises(ValueError, match="64자"):
        asyncio.run(component._get_tools())
    component.tool_name = "run_child_flow"

    monkeypatch.setattr(RunFlowBaseComponent, "_pre_run_setup", lambda self: None)
    component._attributes = {}
    component.session_id = ""
    component._vertex = SimpleNamespace(graph=SimpleNamespace(flow_id="parent", session_id="parent-session"))
    component._pre_run_setup()
    assert component.session_id == "parent-session"
    assert component._attributes["session_id"] == "parent-session"

    component.session_id = "bad\nsession"
    with pytest.raises(ValueError, match="제어 문자"):
        component._pre_run_setup()
    component.session_id = "s" * 256
    with pytest.raises(ValueError, match="255자"):
        component._pre_run_setup()


def test_cached_tool_preserves_timestamp_microseconds(modules: dict[str, ModuleType]) -> None:
    module = modules["cached_named_run_flow_tool"]
    component = component_instance(module, "cached_named_run_flow_tool")
    cached = SimpleNamespace(updated_at="2026-07-12T01:00:00.100000Z")
    assert component._is_cached_flow_up_to_date(cached, "2026-07-12T01:00:00.200000Z") is False
    cached.updated_at = "2026-07-12T01:00:00.300000Z"
    assert component._is_cached_flow_up_to_date(cached, "2026-07-12T01:00:00.200000Z") is True


def test_selected_components_compile_to_langflow_182_templates() -> None:
    for component_id in COMPONENT_IDS:
        path = ROOT / "components" / component_id / f"{component_id}.py"
        source = path.read_text(encoding="utf-8")
        component_class = eval_custom_component_code(source)
        config, instance = create_component_template(
            {"code": source, "output_types": []},
            module_name=f"agent_ground.selected_test.{component_id}",
        )
        assert instance.__class__.__name__ == component_class.__name__
        assert config["field_order"] == [item.name for item in component_class.inputs]
        assert [item["name"] for item in config["outputs"]] == [item.name for item in component_class.outputs]

    image_source = (ROOT / "components/multi_image_base64_encoder/multi_image_base64_encoder.py").read_text(
        encoding="utf-8"
    )
    image_config, _ = create_component_template(
        {"code": image_source, "output_types": []},
        module_name="agent_ground.selected_test.image_contract",
    )
    assert image_config["template"]["image_files"]["list"] is True
    assert image_config["template"]["image_files"]["display_name"] == "이미지 파일"

    cached_source = (ROOT / "components/cached_named_run_flow_tool/cached_named_run_flow_tool.py").read_text(
        encoding="utf-8"
    )
    cached_config, _ = create_component_template(
        {"code": cached_source, "output_types": []},
        module_name="agent_ground.selected_test.cached_contract",
    )
    assert cached_config["template"]["flow_id_selected"]["show"] is False
    assert cached_config["template"]["flow_id_selected"]["override_skip"] is True
    assert cached_config["template"]["allow_cross_folder"]["value"] is False
    assert cached_config["outputs"][0]["types"] == ["Tool"]
    assert cached_config["outputs"][0]["tool_mode"] is True
