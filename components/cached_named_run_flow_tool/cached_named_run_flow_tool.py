from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from langflow.helpers.flow import list_flows, list_flows_by_flow_folder
from lfx.base.tools.run_flow import RunFlowBaseComponent
from lfx.io import BoolInput, MessageTextInput, MultilineInput, Output, StrInput


_MAX_FLOW_NAME_CHARS = 255
_MAX_TOOL_NAME_CHARS = 64
_MAX_TOOL_DESCRIPTION_CHARS = 2000
_MAX_SESSION_ID_CHARS = 255
_TOOL_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def _as_iso_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _validated_tool_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name:
        raise ValueError("도구 이름이 필요합니다.")
    if len(name) > _MAX_TOOL_NAME_CHARS:
        raise ValueError(f"도구 이름은 {_MAX_TOOL_NAME_CHARS}자 이하여야 합니다.")
    if _TOOL_NAME_PATTERN.fullmatch(name) is None:
        raise ValueError(
            "도구 이름의 첫 글자는 영문 또는 숫자여야 하며, "
            "이후에는 영문, 숫자, 밑줄과 하이픈만 사용할 수 있습니다."
        )
    return name


def _validated_tool_description(value: Any) -> str:
    description = str(value or "").strip()
    if not description:
        raise ValueError("도구 설명이 필요합니다.")
    if len(description) > _MAX_TOOL_DESCRIPTION_CHARS:
        raise ValueError(f"도구 설명은 {_MAX_TOOL_DESCRIPTION_CHARS}자 이하여야 합니다.")
    return description


def _validated_session_id(value: Any) -> str:
    session_id = str(value or "").strip()
    if not session_id:
        return ""
    if len(session_id) > _MAX_SESSION_ID_CHARS:
        raise ValueError(f"세션 ID는 {_MAX_SESSION_ID_CHARS}자 이하여야 합니다.")
    if re.search(r"[\x00-\x1f\x7f]", session_id):
        raise ValueError("세션 ID에는 제어 문자를 사용할 수 없습니다.")
    return session_id


def _single_chat_input_id(vertices: Any) -> str:
    """현재 하위 Flow에서 사용자 질문을 받을 Chat Input ID를 정확히 하나만 찾습니다.

    하위 Flow를 다시 import하면 node ID가 바뀔 수 있으므로 export 시점 ID를
    저장하지 않고, 실행에 사용할 현재 그래프에서 매번 확인합니다.
    """

    chat_input_ids = [
        str(vertex.id)
        for vertex in list(vertices or [])
        if (getattr(vertex, "data", {}) or {}).get("type") == "ChatInput"
        or getattr(vertex, "display_name", "") == "Chat Input"
    ]
    if len(chat_input_ids) != 1:
        raise ValueError("대상 Flow에는 사용자 입력용 Chat Input이 정확히 하나 있어야 합니다.")
    return chat_input_ids[0]


def _question_tweaks(chat_input_id: Any, flow_tweak_data: Any) -> dict[str, dict[str, str]]:
    """고정 `question` Tool 인자를 현재 Chat Input용 내부 tweak로 변환합니다.

    `ChatInput-...~input_value` 같은 내부 키를 LLM Tool schema 밖에서만 만들기
    때문에 provider가 하이픈과 물결표를 밑줄로 바꾸더라도 질문이 유실되지 않습니다.
    """

    node_id = str(chat_input_id or "").strip()
    if not node_id:
        raise ValueError("현재 하위 Flow의 Chat Input ID를 확인할 수 없습니다.")

    tool_values = flow_tweak_data.model_dump() if hasattr(flow_tweak_data, "model_dump") else flow_tweak_data
    if not isinstance(tool_values, dict):
        tool_values = {}
    question = str(tool_values.get("question") or "").strip()
    if not question:
        raise ValueError("하위 Flow에 전달할 사용자 질문이 비어 있습니다.")
    return {node_id: {"input_value": question}}


class CachedNamedRunFlowTool(RunFlowBaseComponent):
    display_name = "캐시된 이름 기반 Run Flow 도구"
    description = (
        "하위 Flow를 정확한 이름으로 다시 조회하고 실제 ID 기준으로 그래프만 캐시하며, "
        "고정 question 인자를 현재 Chat Input으로 변환해 실행합니다."
    )
    name = "CachedNamedRunFlowTool"
    icon = "Workflow"

    inputs = [
        StrInput(
            name="flow_name_selected",
            display_name="대상 Flow 이름",
            info=(
                "현재 Router와 같은 폴더에 있고 이름이 고유한 하위 Flow의 정확한 이름입니다. "
                "실행할 때 현재 DB ID를 다시 조회합니다."
            ),
            required=True,
        ),
        StrInput(
            name="flow_id_selected",
            display_name="해석된 Flow ID",
            info="대상 Flow 이름에서 실행 시 해석하는 숨김 ID입니다. export 시점 ID를 고정하지 않습니다.",
            value="",
            show=False,
            override_skip=True,
        ),
        MessageTextInput(
            name="session_id",
            display_name="세션 ID",
            info="직접 지정할 때만 입력합니다. 비우면 부모 Flow 그래프의 세션을 자동 상속합니다.",
            value="",
            advanced=True,
        ),
        BoolInput(
            name="cache_flow",
            display_name="Flow 그래프 캐시",
            info=(
                "현재 사용자와 실제 Flow ID를 기준으로 파싱한 그래프만 캐시합니다. "
                "데이터, 도구 결과와 최종 답변은 캐시하지 않습니다."
            ),
            value=True,
            advanced=True,
        ),
        BoolInput(
            name="allow_cross_folder",
            display_name="다른 폴더 Flow 허용",
            info=(
                "기본값은 꺼짐이며 부모 Router와 같은 폴더에서만 이름을 찾습니다. "
                "켜면 현재 사용자의 모든 폴더에서 고유한 동일 이름을 찾습니다."
            ),
            value=False,
            advanced=True,
        ),
        StrInput(
            name="tool_name",
            display_name="도구 이름",
            info=(
                "Agent에 노출할 64자 이하 이름입니다. 첫 글자는 영문 또는 숫자로 시작하고, "
                "이후에는 영문, 숫자, 밑줄과 하이픈만 사용합니다."
            ),
            required=True,
        ),
        MultilineInput(
            name="tool_description",
            display_name="도구 설명",
            info="Agent가 이 하위 Flow를 선택할 조건과 선택하지 않을 조건을 함께 설명합니다.",
            required=True,
        ),
        BoolInput(
            name="return_direct",
            display_name="하위 결과 직접 반환",
            info="부모 Agent의 추가 재작성 단계를 거치지 않고 하위 Flow 결과를 그대로 반환합니다.",
            value=True,
            advanced=True,
        ),
    ]

    outputs = [
        Output(
            name="component_as_tool",
            display_name="Flow 도구",
            method="to_toolkit",
            types=["Tool"],
            tool_mode=True,
        )
    ]

    async def get_graph(
        self,
        flow_name_selected: str | None = None,
        flow_id_selected: str | None = None,
        updated_at: str | None = None,
    ):
        del flow_id_selected, updated_at
        flow_name = str(flow_name_selected or getattr(self, "flow_name_selected", "") or "").strip()
        if not flow_name:
            raise ValueError("대상 Flow 이름이 필요합니다.")
        if len(flow_name) > _MAX_FLOW_NAME_CHARS:
            raise ValueError(f"대상 Flow 이름은 {_MAX_FLOW_NAME_CHARS}자 이하여야 합니다.")
        if not getattr(self, "user_id", None):
            raise ValueError("현재 Langflow 사용자 ID를 확인할 수 없습니다.")

        parent_graph = getattr(self, "graph", None)
        parent_flow_id = str(getattr(parent_graph, "flow_id", "") or "").strip()
        allow_cross_folder = bool(getattr(self, "allow_cross_folder", False))
        if allow_cross_folder:
            candidates = await list_flows(user_id=self.user_id)
            scope_label = "현재 사용자의 전체 폴더"
        else:
            if not parent_flow_id:
                raise ValueError("같은 폴더를 확인할 부모 Flow ID가 없습니다.")
            candidates = await list_flows_by_flow_folder(user_id=self.user_id, flow_id=parent_flow_id)
            scope_label = "부모 Router와 같은 폴더"

        matches: list[dict[str, Any]] = []
        for candidate in candidates:
            candidate_data = getattr(candidate, "data", None) or {}
            if isinstance(candidate_data, dict) and str(candidate_data.get("name") or "") == flow_name:
                matches.append(candidate_data)
        if not matches:
            raise ValueError(f"{scope_label}에서 이름이 정확히 일치하는 Flow를 찾지 못했습니다: {flow_name}")
        if len(matches) > 1:
            raise ValueError(f"{scope_label}에 같은 이름의 Flow가 여러 개 있습니다: {flow_name}")

        actual_id = str(matches[0].get("id") or "").strip()
        actual_updated_at = _as_iso_text(matches[0].get("updated_at"))
        if not actual_id:
            raise ValueError(f"대상 Flow의 실제 DB ID를 확인할 수 없습니다: {flow_name}")
        if parent_flow_id and actual_id == parent_flow_id:
            raise ValueError("현재 부모 Flow 자신을 하위 Run Flow 도구로 실행할 수 없습니다.")

        self.flow_name_selected = flow_name
        self.flow_id_selected = actual_id
        self._attributes["flow_name_selected"] = flow_name
        self._attributes["flow_id_selected"] = actual_id
        self._attributes["flow_name_selected_updated_at"] = actual_updated_at
        self._cached_flow_updated_at = actual_updated_at
        graph = await super().get_graph(
            flow_name_selected=flow_name,
            flow_id_selected=actual_id,
            updated_at=actual_updated_at,
        )
        self._resolved_chat_input_id = _single_chat_input_id(getattr(graph, "vertices", []))
        return graph

    def _is_cached_flow_up_to_date(self, cached_flow, updated_at: str | None) -> bool:
        """microsecond를 보존해 같은 초 안의 Flow 수정도 캐시 무효화에 반영합니다."""
        cached_timestamp = self._parse_timestamp(getattr(cached_flow, "updated_at", None))
        current_timestamp = self._parse_timestamp(updated_at)
        return bool(cached_timestamp and current_timestamp and cached_timestamp >= current_timestamp)

    @staticmethod
    def _parse_timestamp(value: str | None) -> datetime | None:
        """ISO timestamp를 UTC 기준으로 변환하되 microsecond를 제거하지 않습니다."""
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def get_new_fields(self, inputs_vertex):
        chat_input_id = _single_chat_input_id(inputs_vertex)
        self._resolved_chat_input_id = chat_input_id
        fields = super().get_new_fields(inputs_vertex)
        internal_name = f"{chat_input_id}{self.IOPUT_SEP}input_value"
        compact_fields = [field for field in fields if field.get("name") == internal_name]
        if len(compact_fields) != 1:
            raise ValueError("대상 Flow의 Chat Input 입력 필드를 확인할 수 없습니다.")

        # LFX 0.3.4 Tool schema 생성기는 dotdict의 `.type`, `.required` 속성을 읽습니다.
        # 일반 dict로 복사하지 않고 기반 Component가 만든 field 객체를 그대로 유지합니다.
        question_field = compact_fields[0]
        question_field.update(
            {
                "name": "question",
                "display_name": "사용자 질문",
                "info": "현재 사용자 질문 원문입니다. 하위 Flow의 Chat Input에 그대로 전달합니다.",
                "required": True,
                "value": "",
                "tool_mode": True,
            }
        )
        return [question_field]

    async def get_required_data(self):
        graph = await self.get_graph(self.flow_name_selected, None, None)
        self._sync_flow_outputs(self._format_flow_outputs(graph))
        fields = self.update_input_types(self.get_new_fields_from_graph(graph))
        description = graph.description or _validated_tool_description(getattr(self, "tool_description", ""))
        return description, [field for field in fields if field.get("tool_mode", False)]

    def _build_flow_tweak_data(self) -> dict[str, dict[str, str]]:
        """Agent의 `question`을 현재 그래프의 실제 Chat Input ID에 적용합니다."""

        return _question_tweaks(
            getattr(self, "_resolved_chat_input_id", ""),
            self._attributes.get("flow_tweak_data"),
        )

    async def _get_tools(self):
        tools = await super()._get_tools()
        if len(tools) != 1:
            raise ValueError("대상 Flow에는 Agent 도구로 사용할 최종 출력이 정확히 하나 있어야 합니다.")

        tool = tools[0]
        tool_name = _validated_tool_name(getattr(self, "tool_name", ""))
        tool_description = _validated_tool_description(getattr(self, "tool_description", ""))
        tool.name = tool_name
        tool.description = tool_description
        tool.tags = [tool_name]
        tool.return_direct = bool(getattr(self, "return_direct", True))
        self.status = {
            "status": "도구 준비 완료",
            "flow_name": str(getattr(self, "flow_name_selected", "") or "")[:_MAX_FLOW_NAME_CHARS],
            "tool_name": tool_name,
            "cache_flow": bool(getattr(self, "cache_flow", True)),
            "return_direct": tool.return_direct,
        }
        return [tool]

    def _pre_run_setup(self) -> None:
        super()._pre_run_setup()
        explicit = _validated_session_id(getattr(self, "session_id", ""))
        parent_session = _validated_session_id(getattr(getattr(self, "graph", None), "session_id", ""))
        inherited = explicit or parent_session
        if inherited:
            self.session_id = inherited
            self._attributes["session_id"] = inherited
