from __future__ import annotations

"""FastAPI companion service that stores and serves immutable report HTML."""

import hashlib
import hmac
import base64
import binascii
import json
import os
import re
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field


REPORT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
CSP_HASH_PATTERN = re.compile(r"^sha256-[A-Za-z0-9+/]{43}=$")
CONTENT_HASH_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")
MAX_REPORT_BYTES = 15 * 1024 * 1024
CAPABILITY_VERSION = "report-capability/v1"
CAPABILITY_CLOCK_SKEW_SECONDS = 30
DEFAULT_PROCESSING_LEASE_SECONDS = 300


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _hash_html(html: str) -> str:
    return "sha256:" + hashlib.sha256(html.encode("utf-8")).hexdigest()


def _request_digest(
    tenant_id: str,
    actor_id: str,
    content_hash: str,
    report_id: str,
    metadata: dict[str, Any],
) -> str:
    material = json.dumps(
        {
            "tenant_id": tenant_id,
            "actor_id": actor_id,
            "content_sha256": content_hash,
            "report_id": report_id,
            "metadata": metadata,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _safe_public_base(value: str) -> str:
    parsed = urlsplit(value.strip())
    hostname = (parsed.hostname or "").lower()
    loopback = hostname in {"localhost", "127.0.0.1", "::1"}
    if not hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError("REPORT_PUBLIC_BASE_URL must be an absolute URL without credentials, query, or fragment")
    if parsed.scheme not in ({"http", "https"} if loopback else {"https"}):
        raise RuntimeError("REPORT_PUBLIC_BASE_URL must use HTTPS outside loopback development")
    return value.strip().rstrip("/")


@dataclass(frozen=True)
class Settings:
    environment: str
    storage_mode: str
    mongodb_uri: str
    mongodb_database: str
    collection_prefix: str
    bearer_token: str
    allow_insecure_local: bool
    public_base_url: str
    retention_days: int
    view_signing_secret: str = ""
    view_token_ttl_seconds: int = 900
    processing_lease_seconds: int = DEFAULT_PROCESSING_LEASE_SECONDS

    @classmethod
    def from_env(cls) -> "Settings":
        environment = os.getenv("APP_ENV", "development").strip().lower()
        storage_mode = os.getenv("REPORT_STORAGE_MODE", "mongodb").strip().lower()
        settings = cls(
            environment=environment,
            storage_mode=storage_mode,
            mongodb_uri=os.getenv("MONGODB_URI", "").strip(),
            mongodb_database=os.getenv("MONGODB_DATABASE", "business_work_design").strip(),
            collection_prefix=os.getenv("MONGODB_COLLECTION_PREFIX", "").strip(),
            bearer_token=os.getenv("REPORT_API_BEARER_TOKEN", "").strip(),
            allow_insecure_local=_truthy(os.getenv("ALLOW_INSECURE_LOCAL")),
            public_base_url=os.getenv("REPORT_PUBLIC_BASE_URL", "http://localhost:8091/api").strip(),
            retention_days=max(1, min(int(os.getenv("REPORT_RETENTION_DAYS", "90")), 3650)),
            view_signing_secret=os.getenv("REPORT_VIEW_SIGNING_SECRET", "").strip(),
            view_token_ttl_seconds=max(60, min(int(os.getenv("REPORT_VIEW_TOKEN_TTL_SECONDS", "900")), 3600)),
            processing_lease_seconds=max(30, min(int(os.getenv("REPORT_PROCESSING_LEASE_SECONDS", "300")), 3600)),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        _safe_public_base(self.public_base_url)
        if self.storage_mode not in {"mongodb", "memory"}:
            raise RuntimeError("REPORT_STORAGE_MODE must be mongodb or memory")
        production = self.environment in {"production", "prod"}
        if production and self.storage_mode != "mongodb":
            raise RuntimeError("production Report API requires MongoDB storage")
        if self.storage_mode == "mongodb" and (not self.mongodb_uri or not self.mongodb_database):
            raise RuntimeError("MongoDB configuration is required for REPORT_STORAGE_MODE=mongodb")
        if production and (not self.bearer_token or self.allow_insecure_local):
            raise RuntimeError("production Report API requires bearer authentication")
        if not self.bearer_token and not self.allow_insecure_local:
            raise RuntimeError("set REPORT_API_BEARER_TOKEN or explicitly enable local insecure mode")
        if len(self.view_signing_secret.encode("utf-8")) < 32:
            raise RuntimeError("REPORT_VIEW_SIGNING_SECRET must contain at least 32 UTF-8 bytes")
        if not 60 <= int(self.view_token_ttl_seconds) <= 3600:
            raise RuntimeError("REPORT_VIEW_TOKEN_TTL_SECONDS must be between 60 and 3600")
        if not 30 <= int(self.processing_lease_seconds) <= 3600:
            raise RuntimeError("REPORT_PROCESSING_LEASE_SECONDS must be between 30 and 3600")


class ReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: str | None = Field(default=None, max_length=128)
    content_sha256: str = Field(min_length=71, max_length=71)
    html: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StoredReport(BaseModel):
    model_config = ConfigDict(extra="allow")

    report_id: str
    tenant_id: str
    content_sha256: str
    html: str
    metadata: dict[str, Any]
    created_at: datetime
    actor_id: str


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    if not value or len(value) > 8192 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("invalid base64url")
    padded = value + "=" * (-len(value) % 4)
    return base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)


def _capability_material(claim: dict[str, Any]) -> bytes:
    return json.dumps(claim, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _actor_binding(settings: Settings, actor_id: str) -> str:
    return hmac.new(
        settings.view_signing_secret.encode("utf-8"),
        b"report-actor-binding\0" + actor_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _sign_report_capability(
    settings: Settings,
    report: StoredReport,
    purpose: str,
    *,
    now: datetime | None = None,
) -> str:
    issued_at = int((now or _now()).timestamp())
    claim = {
        "schema_version": CAPABILITY_VERSION,
        "tenant_id": report.tenant_id,
        "actor_binding": _actor_binding(settings, report.actor_id),
        "report_id": report.report_id,
        "content_sha256": report.content_sha256,
        "purpose": purpose,
        "iat": issued_at,
        "exp": issued_at + int(settings.view_token_ttl_seconds),
        "jti": secrets.token_urlsafe(18),
    }
    encoded = _b64url_encode(_capability_material(claim))
    signature = hmac.new(settings.view_signing_secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    return encoded + "." + _b64url_encode(signature)


def _verify_report_capability(
    settings: Settings,
    token: str,
    *,
    report_id: str,
    purpose: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    try:
        if len(token) > 12_000 or token.count(".") != 1:
            raise ValueError("invalid token structure")
        encoded, encoded_signature = token.split(".", 1)
        supplied_signature = _b64url_decode(encoded_signature)
        expected_signature = hmac.new(
            settings.view_signing_secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise ValueError("signature mismatch")
        claim = json.loads(_b64url_decode(encoded).decode("utf-8"))
        required_fields = {
            "schema_version",
            "tenant_id",
            "actor_binding",
            "report_id",
            "content_sha256",
            "purpose",
            "iat",
            "exp",
            "jti",
        }
        if not isinstance(claim, dict) or set(claim) != required_fields:
            raise ValueError("invalid claim schema")
        if claim.get("schema_version") != CAPABILITY_VERSION:
            raise ValueError("unsupported capability")
        if claim.get("report_id") != report_id or claim.get("purpose") != purpose:
            raise ValueError("capability scope mismatch")
        if not REPORT_ID_PATTERN.fullmatch(str(claim.get("report_id") or "")):
            raise ValueError("invalid report scope")
        if not CONTENT_HASH_PATTERN.fullmatch(str(claim.get("content_sha256") or "")):
            raise ValueError("invalid content scope")
        tenant_id = str(claim.get("tenant_id") or "")
        actor_binding = str(claim.get("actor_binding") or "")
        if not tenant_id or len(tenant_id) > 128 or not re.fullmatch(r"[a-f0-9]{64}", actor_binding):
            raise ValueError("invalid identity scope")
        if (
            not isinstance(claim.get("iat"), int)
            or isinstance(claim.get("iat"), bool)
            or not isinstance(claim.get("exp"), int)
            or isinstance(claim.get("exp"), bool)
        ):
            raise ValueError("invalid capability timestamps")
        issued_at = claim["iat"]
        expires_at = claim["exp"]
        current = int((now or _now()).timestamp())
        ttl = int(settings.view_token_ttl_seconds)
        if issued_at > current + CAPABILITY_CLOCK_SKEW_SECONDS:
            raise ValueError("future capability")
        if expires_at <= current or expires_at <= issued_at:
            raise ValueError("expired capability")
        if expires_at - issued_at > ttl or expires_at > current + ttl + CAPABILITY_CLOCK_SKEW_SECONDS:
            raise ValueError("capability TTL exceeded")
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", str(claim.get("jti") or "")):
            raise ValueError("invalid capability identifier")
        return claim
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise ValueError("invalid report capability") from exc


class ReportRepository(Protocol):
    def put(self, report: StoredReport, idempotency_key: str, request_hash: str) -> tuple[StoredReport, bool]: ...

    def get(self, tenant_id: str, report_id: str) -> StoredReport | None: ...

    def health(self) -> dict[str, Any]: ...

    def close(self) -> None: ...


class MemoryReportRepository:
    """Explicit development/test adapter; never selected implicitly in production."""

    def __init__(self) -> None:
        self._reports: dict[tuple[str, str], StoredReport] = {}
        self._idempotency: dict[tuple[str, str], tuple[str, str]] = {}
        self._lock = RLock()

    def put(self, report: StoredReport, idempotency_key: str, request_hash: str) -> tuple[StoredReport, bool]:
        with self._lock:
            idem_key = (report.tenant_id, idempotency_key)
            previous = self._idempotency.get(idem_key)
            if previous:
                previous_hash, previous_id = previous
                if previous_hash != request_hash:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return self._reports[(report.tenant_id, previous_id)], False
            report_key = (report.tenant_id, report.report_id)
            existing = self._reports.get(report_key)
            if existing and (
                existing.content_sha256 != report.content_sha256
                or existing.actor_id != report.actor_id
                or existing.metadata != report.metadata
            ):
                raise ValueError("REPORT_ID_CONFLICT")
            stored = existing or report
            self._reports[report_key] = stored
            self._idempotency[idem_key] = (request_hash, stored.report_id)
            return stored, existing is None

    def get(self, tenant_id: str, report_id: str) -> StoredReport | None:
        with self._lock:
            return self._reports.get((tenant_id, report_id))

    def health(self) -> dict[str, Any]:
        with self._lock:
            return {"ready": True, "storage": "memory", "reports": len(self._reports)}

    def close(self) -> None:
        return None


class MongoReportRepository:
    def __init__(self, settings: Settings) -> None:
        from gridfs import GridFSBucket
        from pymongo import ASCENDING, MongoClient

        self._client = MongoClient(
            settings.mongodb_uri,
            connectTimeoutMS=5000,
            serverSelectionTimeoutMS=5000,
            retryReads=True,
            retryWrites=True,
        )
        self._client.admin.command("ping")
        self._database = self._client[settings.mongodb_database]
        self._reports = self._database[settings.collection_prefix + "reports"]
        self._idempotency = self._database[settings.collection_prefix + "report_idempotency"]
        self._bucket = GridFSBucket(self._database, bucket_name=settings.collection_prefix + "report_html")
        self._retention_days = settings.retention_days
        self._processing_lease_seconds = settings.processing_lease_seconds
        self._reports.create_index([("tenant_id", ASCENDING), ("report_id", ASCENDING)], unique=True)
        self._idempotency.create_index([("tenant_id", ASCENDING), ("idempotency_key", ASCENDING)], unique=True)
        self._idempotency.create_index("expires_at", expireAfterSeconds=0)

    def put(self, report: StoredReport, idempotency_key: str, request_hash: str) -> tuple[StoredReport, bool]:
        from pymongo.errors import DuplicateKeyError

        idem_query = {"tenant_id": report.tenant_id, "idempotency_key": idempotency_key}
        now = _now()
        lease_owner = secrets.token_urlsafe(24)
        lease_duration = timedelta(seconds=self._processing_lease_seconds)
        reservation = {
            **idem_query,
            "request_hash": request_hash,
            "report_id": report.report_id,
            "status": "PROCESSING",
            "created_at": now,
            "updated_at": now,
            "lease_owner": lease_owner,
            "lease_expires_at": now + lease_duration,
            "attempt": 1,
            "expires_at": now + timedelta(days=self._retention_days),
        }

        def mark_completed() -> None:
            completed_at = _now()
            completed = self._idempotency.update_one(
                {**idem_query, "request_hash": request_hash, "report_id": report.report_id},
                {
                    "$set": {
                        "status": "COMPLETED",
                        "completed_at": completed_at,
                        "updated_at": completed_at,
                        "lease_expires_at": completed_at,
                    },
                    "$unset": {"lease_owner": ""},
                },
            )
            if int(getattr(completed, "matched_count", 0)) != 1:
                raise RuntimeError("report idempotency reservation disappeared before completion")

        reserved_here = False
        try:
            self._idempotency.insert_one(reservation)
            reserved_here = True
        except DuplicateKeyError:
            previous = self._idempotency.find_one(idem_query)
            if not previous or previous.get("request_hash") != request_hash or previous.get("report_id") != report.report_id:
                raise ValueError("IDEMPOTENCY_CONFLICT")
            stored = self.get(report.tenant_id, report.report_id)
            if stored is not None:
                mark_completed()
                return stored, False
            if previous.get("status") != "PROCESSING":
                raise RuntimeError("completed report idempotency record points to missing report")
            reclaimed_at = _now()
            stale_without_lease = reclaimed_at - lease_duration
            reclaimed = self._idempotency.update_one(
                {
                    **idem_query,
                    "request_hash": request_hash,
                    "report_id": report.report_id,
                    "status": "PROCESSING",
                    "$or": [
                        {"lease_expires_at": {"$lte": reclaimed_at}},
                        {
                            "lease_expires_at": {"$exists": False},
                            "created_at": {"$lte": stale_without_lease},
                        },
                    ],
                },
                {
                    "$set": {
                        "lease_owner": lease_owner,
                        "lease_expires_at": reclaimed_at + lease_duration,
                        "updated_at": reclaimed_at,
                    },
                    "$inc": {"attempt": 1},
                },
            )
            if int(getattr(reclaimed, "matched_count", 0)) != 1:
                raise ValueError("IDEMPOTENCY_IN_PROGRESS")
            reserved_here = True

        report_query = {"tenant_id": report.tenant_id, "report_id": report.report_id}
        html_id = None
        try:
            existing_doc = self._reports.find_one(report_query)
            if existing_doc:
                if (
                    existing_doc.get("content_sha256") != report.content_sha256
                    or existing_doc.get("actor_id") != report.actor_id
                    or existing_doc.get("metadata") != report.metadata
                ):
                    raise ValueError("REPORT_ID_CONFLICT")
                stored = self.get(report.tenant_id, report.report_id)
                if stored is None:
                    raise RuntimeError("report metadata points to missing HTML")
                mark_completed()
                return stored, False

            html_id = self._bucket.upload_from_stream(
                f"{report.tenant_id}/{report.report_id}.html",
                report.html.encode("utf-8"),
                metadata={"tenant_id": report.tenant_id, "report_id": report.report_id, "content_sha256": report.content_sha256},
            )
            document = report.model_dump(exclude={"html"})
            document["html_file_id"] = html_id
            try:
                self._reports.insert_one(document)
            except DuplicateKeyError:
                self._bucket.delete(html_id)
                existing = self.get(report.tenant_id, report.report_id)
                if (
                    existing is None
                    or existing.content_sha256 != report.content_sha256
                    or existing.actor_id != report.actor_id
                    or existing.metadata != report.metadata
                ):
                    raise ValueError("REPORT_ID_CONFLICT")
                mark_completed()
                return existing, False
            mark_completed()
            return report, True
        except Exception:
            report_exists = self._reports.find_one(report_query)
            if html_id is not None and not report_exists:
                try:
                    self._bucket.delete(html_id)
                except Exception:
                    pass
            if reserved_here and not report_exists:
                self._idempotency.delete_one(
                    {
                        **idem_query,
                        "request_hash": request_hash,
                        "report_id": report.report_id,
                        "status": "PROCESSING",
                        "lease_owner": lease_owner,
                    }
                )
            raise

    def get(self, tenant_id: str, report_id: str) -> StoredReport | None:
        document = self._reports.find_one({"tenant_id": tenant_id, "report_id": report_id})
        if not document:
            return None
        stream = self._bucket.open_download_stream(document["html_file_id"])
        try:
            html = stream.read(MAX_REPORT_BYTES + 1).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError("stored report HTML is not UTF-8") from exc
        if len(html.encode("utf-8")) > MAX_REPORT_BYTES or _hash_html(html) != document.get("content_sha256"):
            raise RuntimeError("stored report failed integrity verification")
        document.pop("_id", None)
        document.pop("html_file_id", None)
        document["html"] = html
        return StoredReport.model_validate(document)

    def health(self) -> dict[str, Any]:
        self._client.admin.command("ping")
        return {"ready": True, "storage": "mongodb"}

    def close(self) -> None:
        self._client.close()


def _repository(settings: Settings) -> ReportRepository:
    if settings.storage_mode == "memory":
        return MemoryReportRepository()
    return MongoReportRepository(settings)


def _csp(metadata: dict[str, Any]) -> str:
    script_hash = str(metadata.get("script_csp_hash") or "")
    style_hash = str(metadata.get("style_csp_hash") or "")
    if not CSP_HASH_PATTERN.fullmatch(script_hash) or not CSP_HASH_PATTERN.fullmatch(style_hash):
        raise HTTPException(status_code=500, detail="Stored report is missing valid CSP hashes")
    return (
        "default-src 'none'; "
        f"script-src '{script_hash}'; style-src-elem '{style_hash}'; style-src-attr 'unsafe-inline'; "
        "img-src data:; font-src 'none'; connect-src 'none'; object-src 'none'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )


def _response_headers(report: StoredReport, *, attachment: bool = False) -> dict[str, str]:
    disposition = "attachment" if attachment else "inline"
    return {
        "Content-Security-Policy": _csp(report.metadata),
        "Cache-Control": "private, no-store, max-age=0",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "ETag": '"' + report.content_sha256.split(":", 1)[1] + '"',
        "Content-Disposition": f'{disposition}; filename="{report.report_id}.html"',
    }


def create_app(settings: Settings | None = None, repository: ReportRepository | None = None) -> FastAPI:
    resolved = settings

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        nonlocal resolved
        resolved = resolved or Settings.from_env()
        resolved.validate()
        application.state.settings = resolved
        application.state.repository = repository or _repository(resolved)
        try:
            yield
        finally:
            application.state.repository.close()

    application = FastAPI(title="Business Work Design Report API", version="1.0.0", lifespan=lifespan)

    async def actor(
        request: Request,
        authorization: str | None = Header(default=None),
        x_tenant_id: str | None = Header(default=None),
        x_actor_id: str | None = Header(default=None),
    ) -> dict[str, str]:
        current: Settings = request.app.state.settings
        tenant_id = str(x_tenant_id or "").strip()
        actor_id = str(x_actor_id or "").strip()
        if not tenant_id or len(tenant_id) > 128 or not actor_id or len(actor_id) > 128:
            raise HTTPException(status_code=400, detail="Valid X-Tenant-ID and X-Actor-ID are required")
        if current.allow_insecure_local and not current.bearer_token:
            client_host = request.client.host if request.client else ""
            if client_host not in {"127.0.0.1", "::1", "testclient"}:
                raise HTTPException(status_code=401, detail="Local insecure mode accepts loopback clients only")
        else:
            expected = "Bearer " + current.bearer_token
            if not authorization or not hmac.compare_digest(authorization, expected):
                raise HTTPException(status_code=401, detail="Invalid bearer token")
        return {"tenant_id": tenant_id, "actor_id": actor_id}

    def repo(request: Request) -> ReportRepository:
        return request.app.state.repository

    @application.middleware("http")
    async def body_limit(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_REPORT_BYTES + 1_048_576:
                    return Response(status_code=413, content="Request body too large")
            except ValueError:
                return Response(status_code=400, content="Invalid Content-Length")
        return await call_next(request)

    @application.get("/api/health")
    def health(request: Request):
        try:
            return {"service": "report_api", **request.app.state.repository.health()}
        except Exception:
            return Response(status_code=503, content='{"service":"report_api","ready":false}', media_type="application/json")

    @application.post("/api/reports", status_code=status.HTTP_201_CREATED)
    def create_report(
        payload: ReportCreate,
        request: Request,
        response: Response,
        identity: dict[str, str] = Depends(actor),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        storage: ReportRepository = Depends(repo),
    ):
        if not idempotency_key or len(idempotency_key) > 200:
            raise HTTPException(status_code=400, detail="Idempotency-Key is required")
        if len(payload.html.encode("utf-8")) > MAX_REPORT_BYTES:
            raise HTTPException(status_code=413, detail="Report HTML exceeds 15 MiB")
        if not CONTENT_HASH_PATTERN.fullmatch(payload.content_sha256) or _hash_html(payload.html) != payload.content_sha256:
            raise HTTPException(status_code=422, detail="content_sha256 does not match HTML")
        report_id = payload.report_id or "report-" + hashlib.sha256(
            f"{identity['tenant_id']}|{idempotency_key}|{payload.content_sha256}".encode("utf-8")
        ).hexdigest()[:32]
        if not REPORT_ID_PATTERN.fullmatch(report_id):
            raise HTTPException(status_code=422, detail="Invalid report_id")
        _csp(payload.metadata)
        created = StoredReport(
            report_id=report_id,
            tenant_id=identity["tenant_id"],
            content_sha256=payload.content_sha256,
            html=payload.html,
            metadata=payload.metadata,
            created_at=_now(),
            actor_id=identity["actor_id"],
        )
        try:
            stored, was_created = storage.put(
                created,
                idempotency_key,
                _request_digest(
                    identity["tenant_id"],
                    identity["actor_id"],
                    payload.content_sha256,
                    report_id,
                    payload.metadata,
                ),
            )
        except ValueError as exc:
            code = str(exc)
            if code in {"IDEMPOTENCY_CONFLICT", "IDEMPOTENCY_IN_PROGRESS", "REPORT_ID_CONFLICT"}:
                raise HTTPException(status_code=409, detail=code) from exc
            raise
        if not was_created:
            response.status_code = status.HTTP_200_OK
        base = _safe_public_base(request.app.state.settings.public_base_url)
        view_capability = _sign_report_capability(request.app.state.settings, stored, "view")
        download_capability = _sign_report_capability(request.app.state.settings, stored, "download")
        return {
            "report_id": stored.report_id,
            "content_sha256": stored.content_sha256,
            "view_url": f"{base}/reports/{stored.report_id}?{urlencode({'capability': view_capability})}",
            "download_url": f"{base}/reports/{stored.report_id}/download?{urlencode({'capability': download_capability})}",
            "created_at": stored.created_at.isoformat(),
        }

    def _load(
        report_id: str,
        identity: dict[str, str],
        storage: ReportRepository,
        *,
        enforce_actor_owner: bool = True,
    ) -> StoredReport:
        if not REPORT_ID_PATTERN.fullmatch(report_id):
            raise HTTPException(status_code=404, detail="Report not found")
        try:
            report = storage.get(identity["tenant_id"], report_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail="Stored report failed integrity verification") from exc
        if report is None:
            raise HTTPException(status_code=404, detail="Report not found")
        if enforce_actor_owner and not hmac.compare_digest(report.actor_id, identity["actor_id"]):
            raise HTTPException(status_code=404, detail="Report not found")
        return report

    async def _html_identity(
        request: Request,
        report_id: str,
        purpose: str,
        authorization: str | None,
        x_tenant_id: str | None,
        x_actor_id: str | None,
    ) -> tuple[dict[str, str], dict[str, Any] | None]:
        query_items = list(request.query_params.multi_items())
        capability_values = request.query_params.getlist("capability")

        def capability_error(detail: str = "Invalid or expired report capability") -> HTTPException:
            return HTTPException(
                status_code=401,
                detail=detail,
                headers={"Cache-Control": "private, no-store, max-age=0", "Referrer-Policy": "no-referrer"},
            )

        if capability_values:
            if len(capability_values) != 1 or len(query_items) != 1 or query_items[0][0] != "capability":
                raise capability_error()
            if authorization is not None or x_tenant_id is not None or x_actor_id is not None:
                raise capability_error("Signed report links cannot be combined with identity headers")
            try:
                claim = _verify_report_capability(
                    request.app.state.settings,
                    capability_values[0],
                    report_id=report_id,
                    purpose=purpose,
                )
            except ValueError as exc:
                raise capability_error() from exc
            return {"tenant_id": str(claim["tenant_id"]), "actor_id": "signed-capability"}, claim
        if query_items:
            raise HTTPException(status_code=400, detail="Unexpected report query parameters")
        identity = await actor(
            request,
            authorization=authorization,
            x_tenant_id=x_tenant_id,
            x_actor_id=x_actor_id,
        )
        return identity, None

    def _verify_loaded_capability(report: StoredReport, claim: dict[str, Any] | None) -> None:
        if claim is None:
            return
        if (
            report.tenant_id != claim.get("tenant_id")
            or _actor_binding(application.state.settings, report.actor_id) != claim.get("actor_binding")
            or report.content_sha256 != claim.get("content_sha256")
        ):
            raise HTTPException(
                status_code=401,
                detail="Invalid or expired report capability",
                headers={"Cache-Control": "private, no-store, max-age=0", "Referrer-Policy": "no-referrer"},
            )

    @application.get("/api/reports/{report_id}", response_class=HTMLResponse)
    async def view_report(
        report_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_tenant_id: str | None = Header(default=None),
        x_actor_id: str | None = Header(default=None),
        storage: ReportRepository = Depends(repo),
    ):
        identity, claim = await _html_identity(
            request, report_id, "view", authorization, x_tenant_id, x_actor_id
        )
        report = _load(report_id, identity, storage, enforce_actor_owner=claim is None)
        _verify_loaded_capability(report, claim)
        return HTMLResponse(content=report.html, headers=_response_headers(report))

    @application.get("/api/reports/{report_id}/download", response_class=HTMLResponse)
    async def download_report(
        report_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_tenant_id: str | None = Header(default=None),
        x_actor_id: str | None = Header(default=None),
        storage: ReportRepository = Depends(repo),
    ):
        identity, claim = await _html_identity(
            request, report_id, "download", authorization, x_tenant_id, x_actor_id
        )
        report = _load(report_id, identity, storage, enforce_actor_owner=claim is None)
        _verify_loaded_capability(report, claim)
        return HTMLResponse(content=report.html, headers=_response_headers(report, attachment=True))

    @application.get("/api/reports/{report_id}/metadata")
    def report_metadata(report_id: str, identity: dict[str, str] = Depends(actor), storage: ReportRepository = Depends(repo)):
        report = _load(report_id, identity, storage)
        return {
            "report_id": report.report_id,
            "content_sha256": report.content_sha256,
            "metadata": report.metadata,
            "created_at": report.created_at.isoformat(),
        }

    return application


app = create_app()
