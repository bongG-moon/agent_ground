from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ai_sop_demo_mode: bool = True
    ai_sop_session_secret: str = "change-this-before-production"
    ai_sop_admin_token: str = ""
    ai_sop_allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://127.0.0.1:8765", "http://localhost:8765"]
    )
    ai_sop_max_upload_bytes: int = 50 * 1024 * 1024
    ai_sop_draft_retention_days: int = 30

    mongodb_uri: str = "mongodb://127.0.0.1:27017"
    mongodb_database: str = "ai_sop"

    ai_sop_llm_provider: Literal["gemini", "langchain_openai"] = "gemini"

    gemini_api_key: str = ""
    gemini_model: str = ""
    gemini_enable_files_api: bool = False
    gemini_store_raw_requests: bool = False

    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model: str = ""
    openai_structured_output_method: Literal["json_schema", "function_calling", "json_mode"] = "json_schema"
    openai_timeout_seconds: float = 60.0
    openai_max_retries: int = 2
    openai_enable_multimodal_sources: bool = False

    boi_template_repository: str = "https://github.com/chokukil/boi-wiki-local.git"
    boi_template_branch: str = "main"
    boi_template_active_sha: str = ""
    boi_template_local_path: Path | None = None
    runtime_root: Path = PROJECT_ROOT / ".runtime"

    @field_validator("ai_sop_allowed_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    def validate_production(self) -> None:
        if self.ai_sop_demo_mode:
            return
        missing = []
        if self.ai_sop_llm_provider == "gemini":
            if not self.gemini_api_key:
                missing.append("GEMINI_API_KEY")
            if not self.gemini_model:
                missing.append("GEMINI_MODEL")
        elif self.ai_sop_llm_provider == "langchain_openai":
            if not self.openai_api_key:
                missing.append("OPENAI_API_KEY")
            if not self.openai_model:
                missing.append("OPENAI_MODEL")
        if self.ai_sop_session_secret == "change-this-before-production":
            missing.append("AI_SOP_SESSION_SECRET")
        if missing:
            raise ValueError(f"운영 모드에 필요한 환경 변수가 없습니다: {', '.join(missing)}")
