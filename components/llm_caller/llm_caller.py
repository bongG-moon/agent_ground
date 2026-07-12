from __future__ import annotations

from importlib import import_module
from typing import Any, Dict

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MessageTextInput, Output
from lfx.schema.data import Data


def _make_data(payload: Dict[str, Any]) -> Any:
    """Langflow 버전에 따라 Data 생성자 모양이 달라지는 부분을 흡수합니다."""
    try:
        return Data(data=payload)
    except TypeError:
        return Data(payload)


def _text_from_value(value: Any) -> str:
    """Prompt Template, Message, Data, dict 어디에서 오든 실제 prompt 문자열을 꺼냅니다."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("prompt", "text", "content", "message", "output", "result", "value", "output_text"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item
            if isinstance(item, dict):
                nested = _text_from_value(item)
                if nested:
                    return nested
    for attr in ("text", "content", "message"):
        item = getattr(value, attr, None)
        if isinstance(item, str) and item.strip():
            return item
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return _text_from_value(data)
    return ""


def _load_llm(llm_api_key: str, model_name: str, temperature: float):
    """LLM 객체를 만드는 함수입니다. 다른 LLM을 쓰려면 이 함수만 교체하면 됩니다."""
    # 기존 main flow와 같은 LangChain Gemini wrapper를 사용합니다.
    # 사내 OpenAI 호환 API 등을 써야 하면 이 함수 안의 모듈/생성자만 바꾸면 됩니다.
    module = import_module("langchain_google_genai")
    return module.ChatGoogleGenerativeAI(
        api_key=llm_api_key,
        model=model_name,
        temperature=temperature,
        convert_system_message_to_human=True,
    )


def call_reusable_llm(prompt_value: Any, llm_api_key: str = "", model_name: str = "", temperature: Any = "0") -> Dict[str, Any]:
    """Prompt Template 결과를 LLM에 전달하고 Normalizer들이 읽기 쉬운 llm_result로 반환합니다."""
    prompt = _text_from_value(prompt_value)
    errors: list[str] = []
    llm_text = ""

    if str(llm_api_key or "").strip():
        try:
            selected_model = str(model_name or "").strip()
            if not selected_model:
                errors.append("Model Name is required when LLM API Key is set.")
            else:
                temp = float(temperature or 0)
                llm = _load_llm(str(llm_api_key).strip(), selected_model, temp)
                response = llm.invoke(prompt)
                llm_text = str(getattr(response, "content", response))
        except Exception as exc:
            errors.append(str(exc))

    return {
        "llm_result": {
            "llm_text": llm_text,
            "errors": errors,
            "prompt": prompt,
            "model_name": str(model_name or "").strip(),
        }
    }


class LLMCaller(Component):
    """기본 Agent 대신 Prompt Template 결과를 그대로 LLM에 호출하는 재사용 Flow용 노드입니다."""

    display_name = "LLM Caller"
    description = "Prompt Template 결과를 LLM에 전달하고, 다음 Normalizer가 읽을 llm_result를 반환합니다."
    icon = "Sparkles"
    name = "LLMCaller"

    inputs = [
        DataInput(name="prompt", display_name="Prompt", input_types=["Message", "Data", "Text"]),
        MessageTextInput(name="llm_api_key", display_name="LLM API Key", value="", advanced=True),
        MessageTextInput(name="model_name", display_name="Model Name", value="", advanced=True),
        MessageTextInput(name="temperature", display_name="Temperature", value="0", advanced=True),
    ]
    outputs = [Output(name="llm_result", display_name="LLM Result", method="build_result", types=["Data"])]

    def build_result(self):
        """입력 prompt를 LLM에 호출하고 결과 payload를 Data로 내보냅니다."""
        payload = call_reusable_llm(
            getattr(self, "prompt", None),
            getattr(self, "llm_api_key", ""),
            getattr(self, "model_name", ""),
            getattr(self, "temperature", "0"),
        )
        llm_result = payload.get("llm_result", {})
        self.status = {
            "prompt_chars": len(llm_result.get("prompt", "")),
            "text_chars": len(llm_result.get("llm_text", "")),
            "errors": len(llm_result.get("errors", [])),
        }
        return _make_data(payload)
