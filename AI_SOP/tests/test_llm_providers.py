from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.config import Settings
from app.domain import InterviewPlan, SopDraftIR
from app.gemini_provider import (
    GoogleGeminiProvider,
    _evidence_text,
    _gemini_generation_schema,
)
from app.main import create_app
from app.storage import MemoryStore


class StubModels:
    def __init__(self) -> None:
        self.config = None

    def generate_content(self, **kwargs):
        self.config = kwargs["config"]
        return SimpleNamespace(
            parsed=None,
            text='{"summary":"weekly report","questions":[],"coveredFields":[],"missingFields":[]}',
        )


def test_gemini_uses_json_schema_instead_of_proto_schema() -> None:
    provider = GoogleGeminiProvider.__new__(GoogleGeminiProvider)
    provider.model_id = "test-model"
    models = StubModels()
    provider.client = SimpleNamespace(models=models)

    result = provider._generate("test", InterviewPlan, [])

    assert result.summary == "weekly report"
    assert models.config.response_schema is None
    assert models.config.response_json_schema["type"] == "object"


def test_gemini_sop_schema_is_simplified_but_keeps_business_fields() -> None:
    schema = _gemini_generation_schema(SopDraftIR)
    serialized = str(schema)

    assert "$defs" not in schema
    assert "$ref" not in serialized
    assert "title" in schema["properties"]
    assert "steps" in schema["properties"]
    assert "actor" in schema["properties"]["steps"]["items"]["properties"]
    for unsupported in (
        "additionalProperties",
        "default",
        "maxItems",
        "maxLength",
        "minItems",
        "minLength",
        "minimum",
    ):
        assert unsupported not in serialized


def test_duplicate_answers_are_included_once_in_model_evidence() -> None:
    evidence = _evidence_text(
        "weekly report",
        [
            {"content": "threshold is 10"},
            {"content": "threshold is 10"},
            {"content": "share in Teams"},
        ],
        [],
    )

    assert evidence.count("threshold is 10") == 1
    assert evidence.count("share in Teams") == 1


def test_langchain_openai_settings_are_valid_without_gemini_credentials() -> None:
    settings = Settings(
        _env_file=None,
        ai_sop_demo_mode=False,
        ai_sop_session_secret="test-session-secret",
        ai_sop_llm_provider="langchain_openai",
        openai_api_key="test-token",
        openai_base_url="https://gateway.example/v1",
        openai_model="internal-model",
        gemini_api_key="",
        gemini_model="",
    )

    settings.validate_production()


def test_app_selects_langchain_openai_provider_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("AI_SOP_LLM_PROVIDER", "langchain_openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-token")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setenv("OPENAI_MODEL", "internal-model")

    app = create_app(store=MemoryStore(), demo_mode=False)
    with TestClient(app) as client:
        status = client.get("/api/status").json()["data"]

    assert status["provider"] == "langchain_openai"
    assert status["model"] == "internal-model"
