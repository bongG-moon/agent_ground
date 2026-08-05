from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )


class DraftStatus(StrEnum):
    COLLECTING = "COLLECTING"
    INTERVIEWING = "INTERVIEWING"
    GENERATING = "GENERATING"
    REVIEW_READY = "REVIEW_READY"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    PUBLISH_FAILED = "PUBLISH_FAILED"


class Visibility(StrEnum):
    PRIVATE = "PRIVATE"
    TEAM = "TEAM"
    PUBLIC = "PUBLIC"


class SopStep(ApiModel):
    number: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=3000)
    actor: str = Field(default="담당자", max_length=120)
    system: str | None = Field(default=None, max_length=120)
    is_decision: bool = False
    yes_target: str | None = Field(default=None, max_length=120)
    no_target: str | None = Field(default=None, max_length=120)
    source_refs: list[str] = Field(default_factory=list, max_length=30)


class SopDraftIR(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=500)
    purpose: str = Field(min_length=1, max_length=3000)
    inputs: list[str] = Field(default_factory=list, max_length=50)
    steps: list[SopStep] = Field(min_length=1, max_length=40)
    decision_criteria: list[str] = Field(default_factory=list, max_length=50)
    exceptions: list[str] = Field(default_factory=list, max_length=50)
    completion_conditions: list[str] = Field(default_factory=list, max_length=50)
    open_questions: list[str] = Field(default_factory=list, max_length=30)
    automation_candidates: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("inputs", "decision_criteria", "exceptions", "completion_conditions")
    @classmethod
    def remove_blank_items(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value and value.strip()]


class InterviewPlan(ApiModel):
    summary: str = Field(min_length=1, max_length=500)
    questions: list[str] = Field(default_factory=list, max_length=3)
    covered_fields: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)


class SourceMaterial(ApiModel):
    source_id: str
    original_name: str
    media_type: str
    sha256: str
    extracted_text: str = ""
    data: bytes = Field(default=b"", exclude=True)


class CreateDraftRequest(ApiModel):
    description: str = Field(min_length=5, max_length=20000)


class ReviseDraftRequest(ApiModel):
    """A user-authored IR replacement for deterministic artifact regeneration."""

    ir: SopDraftIR


class AddMessageRequest(ApiModel):
    content: str = Field(min_length=1, max_length=10000)
    question_index: int | None = Field(default=None, ge=0, le=2)


class ApprovalRequest(ApiModel):
    target_visibility: Visibility
    confirmed: bool
    sensitive_content_reviewed: bool = False


class ApiErrorPayload(ApiModel):
    code: str
    message: str
    details: Any | None = None


class AppError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: Any | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def utc_now() -> datetime:
    return datetime.now().astimezone()
