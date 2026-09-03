"""Regression tests for the fixed Pydantic Structured Output boundary."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "components" / "single_flow" / "04_business_design_structured_output.py"


def _module():
    name = "single_flow_structured_output_component_test"
    spec = importlib.util.spec_from_file_location(name, COMPONENT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _langflow_dynamic_exec_namespace() -> dict[str, object]:
    """Load custom-component source the way Langflow's dynamic loader can.

    Langflow evaluates exported standalone component source with ``exec`` rather
    than importing it as a normal Python module.  In that situation Python
    assigns classes to the ``builtins`` module unless the loader supplies a
    module name.  With postponed annotations, Pydantic must therefore have
    already rebuilt the fixed model using an explicit type namespace.
    """

    namespace: dict[str, object] = {}
    source = COMPONENT.read_text(encoding="utf-8")
    exec(compile(source, str(COMPONENT), "exec"), namespace, namespace)  # noqa: S102
    return namespace


class _FakeRunnable:
    def __init__(self, schema):
        self.schema = schema
        self.messages = None
        self.config = None

    def invoke(self, messages, *, config):
        self.messages = messages
        self.config = config
        return self.schema(
            schema_version="business-design-draft/v1",
            work_analysis={"title": "주간 보고"},
            information_gaps=[],
            as_is_graph={"nodes": [], "edges": []},
            to_be_design={"nodes": [], "edges": []},
            catalog_decisions=[],
        )


class _FakeModel:
    def __init__(self):
        self.schema = None
        self.runnable = None

    def with_structured_output(self, schema):
        self.schema = schema
        self.runnable = _FakeRunnable(schema)
        return self.runnable


class _InvalidRawRunnable:
    def invoke(self, messages, *, config):
        return {
            "schema_version": "business-design-draft/v1",
            "work_analysis": {},
            "information_gaps": [],
            "as_is_graph": {},
            "to_be_design": {},
            "catalog_decisions": [],
            "unexpected": "must not pass through",
        }


class _InvalidRawModel:
    def with_structured_output(self, schema):
        return _InvalidRawRunnable()


class _ProviderResponseError(RuntimeError):
    """Representative provider error with an actionable reason and accidental secret echo."""


class _ProviderFailureRunnable:
    def invoke(self, messages, *, config):
        raise _ProviderResponseError(
            "HTTP 429 quota exhausted; api_key=sk-live-should-never-reach-the-user"
        )


class _ProviderFailureModel:
    def with_structured_output(self, schema):
        return _ProviderFailureRunnable()


class _NativeStructuredOutputUnsupportedModel:
    """A provider that has the method but rejects the native response schema feature."""

    def with_structured_output(self, schema):
        raise NotImplementedError(
            "response_schema is unsupported by this model; "
            "Authorization: Bearer bearer-token-that-must-not-leak"
        )


class _PlainJsonCompatibilityFallbackModel:
    """A provider with normal chat but no native structured-response support."""

    def __init__(self):
        self.plain_messages = None
        self.plain_config = None

    def with_structured_output(self, schema):
        raise NotImplementedError("response_schema is unsupported by this model")

    def invoke(self, messages, *, config):
        self.plain_messages = messages
        self.plain_config = config
        return json.dumps(
            {
                "schema_version": "business-design-draft/v1",
                "work_analysis": {"title": "일일 보고"},
                "information_gaps": [],
                "as_is_graph": {"nodes": [], "edges": []},
                "to_be_design": {"nodes": [], "edges": []},
                "catalog_decisions": [],
            },
            ensure_ascii=False,
        )


@pytest.mark.parametrize(
    "prompt_value",
    [
        "사용자 업무 설명",
        {"text": "사용자 업무 설명"},
        {"data": {"text": "사용자 업무 설명"}},
    ],
)
def test_fixed_component_accepts_langflow_message_transport_shapes(prompt_value):
    module = _module()
    model = _FakeModel()
    component = module.BusinessDesignStructuredOutputComponent()
    component.model = model
    component.input_value = prompt_value

    result = component.build_structured_output().data

    expected = {
        "schema_version": "business-design-draft/v1",
        "work_analysis": {"title": "주간 보고"},
        "information_gaps": [],
        "as_is_graph": {"nodes": [], "edges": []},
        "to_be_design": {"nodes": [], "edges": []},
        "catalog_decisions": [],
    }
    assert {key: value for key, value in result.items() if key != "catalog_shortlist_policy"} == expected
    assert result["catalog_shortlist_policy"] == {
        "max_shortlisted_catalog_items": 12,
        "selection_scope": "candidate_shortlist_only",
        "selection_source": "default",
    }
    assert list(model.schema.model_fields) == [
        "schema_version",
        "work_analysis",
        "information_gaps",
        "as_is_graph",
        "to_be_design",
        "catalog_decisions",
    ]
    assert model.runnable.messages[0].content == module.FIXED_SYSTEM_PROMPT
    assert model.runnable.messages[1].content == "사용자 업무 설명"
    assert model.runnable.config == {"callbacks": []}


def test_fixed_component_accepts_actual_langflow_message_and_data_objects():
    module = _module()
    for prompt_value in (
        module.Message(text="Message transport"),
        module.Data(data={"text": "Data transport"}),
    ):
        model = _FakeModel()
        component = module.BusinessDesignStructuredOutputComponent()
        component.model = model
        component.input_value = prompt_value

        component.build_structured_output()

        assert model.runnable.messages[1].content in {"Message transport", "Data transport"}


def test_fixed_component_rebuilds_pydantic_contract_when_langflow_execs_source_dynamically(monkeypatch):
    """Regression for PydanticUserError: ``Literal`` is not fully defined.

    A normal ``importlib`` import gives the model a module namespace, which
    masks the issue.  This deliberately uses an anonymous exec namespace just
    like a custom-component loader can, then verifies both direct validation
    and the complete structured-output invocation path.
    """

    namespace = _langflow_dynamic_exec_namespace()
    draft_type = namespace["BusinessDesignDraftV1"]
    component_type = namespace["BusinessDesignStructuredOutputComponent"]

    payload = {
        "schema_version": "business-design-draft/v1",
        "work_analysis": {"title": "동적 로더 검증"},
        "information_gaps": [],
        "as_is_graph": {"nodes": [], "edges": []},
        "to_be_design": {"nodes": [], "edges": []},
        "catalog_decisions": [],
    }
    draft = draft_type.model_validate(payload)
    assert draft.model_dump(mode="json") == payload

    # LFX normally obtains source through ``inspect.getmodule`` during
    # construction.  An anonymous exec namespace deliberately has no module
    # object, so bypass only that unrelated source-introspection hook.  The
    # class under test and all of its Pydantic globals still come from the
    # anonymous dynamic-exec namespace above.
    source = COMPONENT.read_text(encoding="utf-8")
    monkeypatch.setattr(component_type, "set_class_code", lambda self: setattr(self, "_code", source))

    model = _FakeModel()
    component = component_type()
    component.model = model
    component.input_value = "동적 Langflow custom component 실행"

    result = component.build_structured_output().data
    assert result["schema_version"] == "business-design-draft/v1"
    assert model.schema is draft_type


def test_fixed_component_embeds_the_system_prompt_and_exposes_no_import_fragile_prompt_input():
    module = _module()

    assert module.FIXED_SYSTEM_PROMPT
    assert "business-design-draft/v1" in module.FIXED_SYSTEM_PROMPT
    inputs = {item.name: item for item in module.BusinessDesignStructuredOutputComponent.inputs}
    assert "system_prompt" not in inputs
    assert inputs["input_value"].input_types == ["Message", "Data", "JSON"]


def test_fixed_component_rejects_a_model_without_native_structured_output():
    module = _module()
    component = module.BusinessDesignStructuredOutputComponent()
    component.model = object()
    component.input_value = "사용자 업무 설명"

    with pytest.raises(ValueError, match="STRUCTURED_OUTPUT_UNSUPPORTED"):
        component.build_structured_output()


def test_fixed_component_validates_a_raw_dict_before_returning_it():
    module = _module()
    component = module.BusinessDesignStructuredOutputComponent()
    component.model = _InvalidRawModel()
    component.input_value = "사용자 업무 설명"

    with pytest.raises(ValueError, match="BUSINESS_DESIGN_STRUCTURED_OUTPUT_INVALID"):
        component.build_structured_output()


def test_fixed_component_preserves_a_sanitized_provider_failure_diagnostic():
    """Users need the actual failure class/reason without a provider leaking credentials."""
    module = _module()
    component = module.BusinessDesignStructuredOutputComponent()
    component.model = _ProviderFailureModel()
    component.input_value = "사용자 업무 설명"

    with pytest.raises(ValueError, match="BUSINESS_DESIGN_STRUCTURED_OUTPUT_FAILED") as raised:
        component.build_structured_output()

    message = str(raised.value)
    assert "_ProviderResponseError" in message
    assert "HTTP 429 quota exhausted" in message
    assert "api_key" not in message.casefold()
    assert "sk-live-should-never-reach-the-user" not in message


def test_fixed_component_identifies_native_structured_output_incompatibility_without_leaking_auth():
    """A method existing on the model is insufficient: the selected model may reject the feature."""
    module = _module()
    component = module.BusinessDesignStructuredOutputComponent()
    component.model = _NativeStructuredOutputUnsupportedModel()
    component.input_value = "사용자 업무 설명"

    with pytest.raises(ValueError, match="STRUCTURED_OUTPUT_UNSUPPORTED") as raised:
        component.build_structured_output()

    message = str(raised.value)
    assert "NotImplementedError" in message
    assert "response_schema is unsupported by this model" in message
    assert "Authorization" not in message
    assert "bearer-token-that-must-not-leak" not in message


def test_fixed_component_uses_validated_json_compatibility_fallback_when_native_mode_is_unsupported():
    """A usable chat model should still run when its provider lacks response-schema support."""
    module = _module()
    model = _PlainJsonCompatibilityFallbackModel()
    component = module.BusinessDesignStructuredOutputComponent()
    component.model = model
    component.input_value = "사용자 업무 설명"

    result = component.build_structured_output().data

    expected = {
        "schema_version": "business-design-draft/v1",
        "work_analysis": {"title": "일일 보고"},
        "information_gaps": [],
        "as_is_graph": {"nodes": [], "edges": []},
        "to_be_design": {"nodes": [], "edges": []},
        "catalog_decisions": [],
    }
    assert {key: value for key, value in result.items() if key != "catalog_shortlist_policy"} == expected
    assert result["catalog_shortlist_policy"]["max_shortlisted_catalog_items"] == 12
    assert model.plain_messages[0].content == module.FIXED_SYSTEM_PROMPT
    assert model.plain_messages[1].content == "사용자 업무 설명"
    assert model.plain_config == {"callbacks": []}
    assert "호환성" in component.status


def test_fixed_component_reports_a_missing_04_prompt_without_conflating_it_with_system_prompt():
    module = _module()
    component = module.BusinessDesignStructuredOutputComponent()
    component.model = _FakeModel()
    component.input_value = {"data": {}}

    with pytest.raises(ValueError, match="04 업무 설계 요청을 받지 못했습니다"):
        component.build_structured_output()
