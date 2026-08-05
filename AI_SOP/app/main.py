from __future__ import annotations

from pathlib import Path
import secrets
from typing import Any

from fastapi import FastAPI, File, Header, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import PROJECT_ROOT, Settings
from app.domain import AddMessageRequest, AppError, ApprovalRequest, CreateDraftRequest, ReviseDraftRequest
from app.gemini_provider import (
    DemoGeminiProvider,
    GoogleGeminiProvider,
    LangChainOpenAIProvider,
    SopModelProvider,
)
from app.service import SopService
from app.storage import MemoryStore, MongoStore, Store
from app.template_manager import TemplateManager
from app.workspace import WorkspaceBuilder


def envelope(data: Any) -> dict[str, Any]:
    return {"data": data}


def create_app(*, store: Store | None = None, provider: SopModelProvider | None = None, demo_mode: bool | None = None) -> FastAPI:
    settings = Settings()
    if demo_mode is not None:
        settings.ai_sop_demo_mode = demo_mode
    settings.validate_production()

    selected_store = store or (
        MemoryStore()
        if settings.ai_sop_demo_mode
        else MongoStore(settings.mongodb_uri, settings.mongodb_database, settings.ai_sop_draft_retention_days)
    )
    if provider is not None:
        selected_provider = provider
    elif settings.ai_sop_demo_mode:
        selected_provider = DemoGeminiProvider()
    elif settings.ai_sop_llm_provider == "langchain_openai":
        selected_provider = LangChainOpenAIProvider(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            model_id=settings.openai_model,
            structured_output_method=settings.openai_structured_output_method,
            timeout_seconds=settings.openai_timeout_seconds,
            max_retries=settings.openai_max_retries,
            enable_multimodal_sources=settings.openai_enable_multimodal_sources,
        )
    else:
        selected_provider = GoogleGeminiProvider(
            api_key=settings.gemini_api_key,
            model_id=settings.gemini_model,
        )
    if not settings.ai_sop_demo_mode and isinstance(selected_store, MongoStore):
        selected_store.ping()
    templates = TemplateManager(
        runtime_root=settings.runtime_root,
        repository_url=settings.boi_template_repository,
        branch=settings.boi_template_branch,
        active_sha=settings.boi_template_active_sha,
        local_path=settings.boi_template_local_path,
    )
    service = SopService(
        store=selected_store,
        provider=selected_provider,
        templates=templates,
        workspace=WorkspaceBuilder(settings.runtime_root),
        max_upload_bytes=settings.ai_sop_max_upload_bytes,
    )

    app = FastAPI(title="AI SOP", version="0.1.0", docs_url="/api/docs", redoc_url=None)
    app.state.settings = settings
    app.state.store = selected_store
    app.state.service = service
    app.state.templates = templates
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.ai_sop_session_secret,
        session_cookie="ai_sop_session",
        max_age=7 * 24 * 60 * 60,
        same_site="lax",
        https_only=not settings.ai_sop_demo_mode,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ai_sop_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Admin-Token"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'"
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}})

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"error": {"code": "VALIDATION_ERROR", "message": "입력값을 확인해 주세요.", "details": exc.errors()}})

    def owner(request: Request) -> str:
        employee_id = request.session.get("employeeId")
        if not employee_id:
            created = service.create_session()
            request.session.update({"sessionId": created["sessionId"], "employeeId": created["employeeId"]})
            employee_id = created["employeeId"]
        return employee_id

    def require_admin(token: str | None) -> None:
        if not settings.ai_sop_admin_token or token is None or not secrets.compare_digest(token, settings.ai_sop_admin_token):
            raise AppError(403, "ADMIN_REQUIRED", "관리자 토큰이 필요합니다.")

    @app.post("/api/session", status_code=201)
    def create_session(request: Request):
        created = service.create_session()
        request.session.update({"sessionId": created["sessionId"], "employeeId": created["employeeId"]})
        return envelope(created)

    @app.get("/api/status")
    def status():
        return envelope({
            "mode": "DEMO" if settings.ai_sop_demo_mode else "CONNECTED",
            "storage": "MEMORY" if settings.ai_sop_demo_mode else "MONGODB",
            "provider": "demo" if settings.ai_sop_demo_mode else settings.ai_sop_llm_provider,
            "model": selected_provider.model_id,
            "templateCommit": templates.active_commit(),
            "templateAvailable": templates.active_template() is not None,
        })

    @app.post("/api/drafts", status_code=201)
    def create_draft(payload: CreateDraftRequest, request: Request):
        return envelope(service.create_draft(owner(request), payload.description))

    @app.get("/api/drafts")
    def list_drafts(request: Request):
        return envelope(selected_store.list_drafts(owner(request)))

    @app.get("/api/drafts/{draft_id}")
    def get_draft(draft_id: str, request: Request):
        return envelope(service.require_draft(owner(request), draft_id))

    @app.post("/api/drafts/{draft_id}/messages", status_code=201)
    def add_message(draft_id: str, payload: AddMessageRequest, request: Request):
        return envelope(
            service.add_message(
                owner(request),
                draft_id,
                payload.content,
                payload.question_index,
            )
        )

    @app.post("/api/drafts/{draft_id}/sources", status_code=201)
    async def add_source(draft_id: str, request: Request, file: UploadFile = File(...)):
        data = await file.read(settings.ai_sop_max_upload_bytes + 1)
        return envelope(service.upload(owner(request), draft_id, file.filename or "", file.content_type, data))

    @app.post("/api/drafts/{draft_id}/questions")
    def questions(draft_id: str, request: Request):
        return envelope(service.questions(owner(request), draft_id))

    @app.post("/api/drafts/{draft_id}/generate")
    def generate(draft_id: str, request: Request):
        return envelope(service.generate(owner(request), draft_id))

    @app.post("/api/drafts/{draft_id}/revise")
    def revise(draft_id: str, payload: ReviseDraftRequest, request: Request):
        return envelope(service.revise(owner(request), draft_id, payload.ir))

    @app.post("/api/drafts/{draft_id}/approve", status_code=201)
    def approve(draft_id: str, payload: ApprovalRequest, request: Request):
        return envelope(
            service.approve(
                owner(request),
                draft_id,
                payload.target_visibility,
                payload.confirmed,
                payload.sensitive_content_reviewed,
            )
        )

    @app.get("/api/wiki")
    def list_wiki():
        return envelope(selected_store.list_publications())

    @app.get("/api/wiki/{document_id}")
    def get_wiki(document_id: str):
        item = selected_store.get_publication(document_id)
        if item is None:
            raise AppError(404, "DOCUMENT_NOT_FOUND", "등록 문서를 찾을 수 없습니다.")
        return envelope(item)

    @app.get("/api/admin/templates")
    def template_status(x_admin_token: str | None = Header(default=None)):
        require_admin(x_admin_token)
        return envelope({"activeCommit": templates.active_commit(), "activePath": str(templates.active_template() or "")})

    @app.post("/api/admin/templates/sync")
    def template_sync(x_admin_token: str | None = Header(default=None)):
        require_admin(x_admin_token)
        return envelope(templates.sync())

    @app.post("/api/admin/templates/{commit_sha}/activate")
    def template_activate(commit_sha: str, x_admin_token: str | None = Header(default=None)):
        require_admin(x_admin_token)
        return envelope(templates.activate(commit_sha))

    static_root = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static_root), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> Response:
        return FileResponse(static_root / "index.html")

    return app


app = create_app()
