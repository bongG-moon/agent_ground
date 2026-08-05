from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from app.domain import AppError, SopDraftIR, SourceMaterial, Visibility
from app.gemini_provider import SopModelProvider
from app.rendering import render_mermaid, render_sop_markdown, source_refs_for_materials
from app.source_extractor import extract_text
from app.storage import Store
from app.template_manager import TemplateManager
from app.uploads import UploadPolicyError, validate_upload
from app.workspace import WorkspaceBuilder


logger = logging.getLogger(__name__)


class SopService:
    def __init__(self, *, store: Store, provider: SopModelProvider, templates: TemplateManager, workspace: WorkspaceBuilder, max_upload_bytes: int) -> None:
        self.store = store
        self.provider = provider
        self.templates = templates
        self.workspace = workspace
        self.max_upload_bytes = max_upload_bytes

    def create_session(self) -> dict:
        session_id = secrets.token_urlsafe(32)
        employee_id = str(secrets.randbelow(9_000_000) + 1_000_000)
        expires_at = datetime.now(timezone.utc) + timedelta(days=7)
        self.store.create_session(session_id, employee_id, expires_at)
        return {"sessionId": session_id, "employeeId": employee_id, "expiresAt": expires_at}

    def create_draft(self, employee_id: str, description: str) -> dict:
        return self.store.create_draft(employee_id, description, self.templates.active_commit())

    def require_draft(self, employee_id: str, draft_id: str) -> dict:
        draft = self.store.get_draft(employee_id, draft_id)
        if draft is None:
            raise AppError(404, "DRAFT_NOT_FOUND", "초안을 찾을 수 없습니다.")
        return draft

    def add_message(
        self,
        employee_id: str,
        draft_id: str,
        content: str,
        question_index: int | None = None,
    ) -> dict:
        self.require_draft(employee_id, draft_id)
        return self.store.add_message(employee_id, draft_id, content, question_index) or {}

    def upload(self, employee_id: str, draft_id: str, filename: str, content_type: str | None, data: bytes) -> dict:
        self.require_draft(employee_id, draft_id)
        try:
            validated = validate_upload(filename=filename, content_type=content_type, data=data, max_bytes=self.max_upload_bytes)
            extracted = extract_text(validated.safe_filename, validated.media_type, validated.data)
        except UploadPolicyError as exc:
            raise AppError(422, "INVALID_UPLOAD", str(exc)) from exc
        except Exception as exc:
            raise AppError(422, "EXTRACTION_FAILED", "첨부 자료를 읽지 못했습니다.", str(exc)) from exc
        return self.store.add_source(
            employee_id,
            draft_id,
            original_name=validated.safe_filename,
            media_type=validated.media_type,
            data=validated.data,
            extracted_text=extracted,
        ) or {}

    def questions(self, employee_id: str, draft_id: str) -> dict:
        draft = self.require_draft(employee_id, draft_id)
        sources = self.store.get_source_materials(employee_id, draft_id)
        try:
            plan = self.provider.propose_questions(draft["description"], draft.get("messages", []), sources)
        except Exception as exc:
            logger.exception("Question generation failed with model %s", self.provider.model_id)
            raise AppError(
                502,
                "MODEL_REQUEST_FAILED",
                "AI 모델이 보완 질문을 생성하지 못했습니다. 잠시 후 다시 시도하거나 모델 연결 설정을 확인해 주세요.",
            ) from exc
        return self.store.save_questions(employee_id, draft_id, plan.model_dump(by_alias=True)) or {}

    def generate(self, employee_id: str, draft_id: str) -> dict:
        draft = self.require_draft(employee_id, draft_id)
        sources = self.store.get_source_materials(employee_id, draft_id)
        try:
            ir = self.provider.build_sop(draft["description"], draft.get("messages", []), sources)
        except Exception as exc:
            logger.exception("SOP generation failed with model %s", self.provider.model_id)
            raise AppError(
                502,
                "MODEL_REQUEST_FAILED",
                "AI 모델이 SOP 초안을 생성하지 못했습니다. 잠시 후 다시 시도하거나 모델 연결 설정을 확인해 주세요.",
            ) from exc
        return self._save_artifacts(employee_id, draft, ir, sources)

    def revise(self, employee_id: str, draft_id: str, ir: SopDraftIR) -> dict:
        """Regenerate every artifact from the reviewed IR without another model call.

        The Reading View is the human-controlled source of truth after generation.
        Re-rendering from the validated IR keeps Markdown and Mermaid in lockstep
        and makes a revision auditable and deterministic.
        """
        draft = self.require_draft(employee_id, draft_id)
        if draft.get("status") != "REVIEW_READY":
            raise AppError(409, "DRAFT_NOT_READY", "검토 가능한 초안을 먼저 생성해 주세요.")
        sources = self.store.get_source_materials(employee_id, draft_id)
        return self._save_artifacts(employee_id, draft, ir, sources)

    def _save_artifacts(self, employee_id: str, draft: dict, ir: SopDraftIR, sources: list[SourceMaterial]) -> dict:
        source_refs = source_refs_for_materials(sources)
        if not source_refs:
            source_refs = [{"type": "user-description", "ref": "browser-session"}]
        model_id = draft.get("modelId") or self.provider.model_id
        markdown = render_sop_markdown(
            ir,
            employee_id=employee_id,
            template_commit=draft["templateCommit"],
            model_id=model_id,
            source_refs=source_refs,
        )
        mermaid = render_mermaid(ir)
        workspace_path = None
        validation = {"passed": True, "mode": "structural", "output": "OKF/BoI 필수 섹션과 Mermaid 구조 생성 완료"}
        template = self.templates.active_template()
        if template is not None:
            workspace_path, validation = self.workspace.build(
                template_root=template,
                employee_id=employee_id,
                draft_id=draft["draftId"],
                title=ir.title,
                markdown=markdown,
                mermaid=mermaid,
            )
        return self.store.save_generated(
            employee_id,
            draft["draftId"],
            ir=ir.model_dump(by_alias=True),
            markdown=markdown,
            mermaid=mermaid,
            title=ir.title,
            model_id=model_id,
            validation=validation,
            workspace_path=workspace_path,
        ) or {}

    def approve(
        self,
        employee_id: str,
        draft_id: str,
        visibility: Visibility,
        confirmed: bool,
        sensitive_content_reviewed: bool,
    ) -> dict:
        draft = self.require_draft(employee_id, draft_id)
        if not confirmed:
            raise AppError(422, "CONFIRMATION_REQUIRED", "등록 전 사용자 확인이 필요합니다.")
        if not sensitive_content_reviewed:
            raise AppError(
                422,
                "SENSITIVE_REVIEW_REQUIRED",
                "공개 범위에 맞게 민감정보를 확인했다는 동의가 필요합니다.",
            )
        if visibility not in {Visibility.TEAM, Visibility.PUBLIC}:
            raise AppError(422, "INVALID_TARGET_VISIBILITY", "Team 또는 Public 공개 범위를 선택해 주세요.")
        if draft.get("status") != "REVIEW_READY":
            raise AppError(409, "DRAFT_NOT_READY", "검토 가능한 초안을 먼저 생성해 주세요.")
        return self.store.publish(employee_id, draft_id, target_visibility=visibility) or {}
