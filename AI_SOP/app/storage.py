from __future__ import annotations

import copy
import hashlib
import io
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import uuid4

from app.domain import DraftStatus, SourceMaterial, Visibility
from app.rendering import render_promoted_sop_markdown


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Store(Protocol):
    def create_session(self, session_id: str, employee_id: str, expires_at: datetime) -> None: ...

    def create_draft(self, employee_id: str, description: str, template_commit: str) -> dict[str, Any]: ...

    def list_drafts(self, employee_id: str) -> list[dict[str, Any]]: ...

    def get_draft(self, employee_id: str, draft_id: str) -> dict[str, Any] | None: ...

    def add_message(
        self,
        employee_id: str,
        draft_id: str,
        content: str,
        question_index: int | None = None,
    ) -> dict[str, Any] | None: ...

    def save_questions(self, employee_id: str, draft_id: str, plan: dict[str, Any]) -> dict[str, Any] | None: ...

    def add_source(
        self,
        employee_id: str,
        draft_id: str,
        *,
        original_name: str,
        media_type: str,
        data: bytes,
        extracted_text: str,
    ) -> dict[str, Any] | None: ...

    def get_source_materials(self, employee_id: str, draft_id: str) -> list[SourceMaterial]: ...

    def save_generated(
        self,
        employee_id: str,
        draft_id: str,
        *,
        ir: dict[str, Any],
        markdown: str,
        mermaid: str,
        title: str,
        model_id: str,
        validation: dict[str, Any],
        workspace_path: str | None = None,
    ) -> dict[str, Any] | None: ...

    def publish(
        self,
        employee_id: str,
        draft_id: str,
        *,
        target_visibility: Visibility,
    ) -> dict[str, Any] | None: ...

    def list_publications(self) -> list[dict[str, Any]]: ...

    def get_publication(self, document_id: str) -> dict[str, Any] | None: ...


class MemoryStore:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.drafts: dict[str, dict[str, Any]] = {}
        self.source_blobs: dict[str, bytes] = {}
        self.publications: dict[str, dict[str, Any]] = {}

    def create_session(self, session_id: str, employee_id: str, expires_at: datetime) -> None:
        self.sessions[session_id] = {
            "sessionId": session_id,
            "employeeId": employee_id,
            "expiresAt": expires_at,
        }

    def create_draft(self, employee_id: str, description: str, template_commit: str) -> dict[str, Any]:
        draft_id = uuid4().hex
        now = _now()
        title = description.strip().splitlines()[0][:80]
        draft = {
            "draftId": draft_id,
            "employeeId": employee_id,
            "title": title,
            "description": description.strip(),
            "status": DraftStatus.COLLECTING.value,
            "templateCommit": template_commit,
            "modelId": None,
            "messages": [],
            "questions": [],
            "sources": [],
            "markdown": "",
            "mermaid": "",
            "ir": None,
            "validation": None,
            "workspacePath": None,
            "createdAt": now,
            "updatedAt": now,
            "expiresAt": now + timedelta(days=30),
        }
        self.drafts[draft_id] = draft
        return copy.deepcopy(draft)

    def list_drafts(self, employee_id: str) -> list[dict[str, Any]]:
        items = [copy.deepcopy(item) for item in self.drafts.values() if item["employeeId"] == employee_id]
        return sorted(items, key=lambda item: item["updatedAt"], reverse=True)

    def get_draft(self, employee_id: str, draft_id: str) -> dict[str, Any] | None:
        draft = self.drafts.get(draft_id)
        if draft is None or draft["employeeId"] != employee_id:
            return None
        return copy.deepcopy(draft)

    def add_message(
        self,
        employee_id: str,
        draft_id: str,
        content: str,
        question_index: int | None = None,
    ) -> dict[str, Any] | None:
        draft = self.drafts.get(draft_id)
        if draft is None or draft["employeeId"] != employee_id:
            return None
        if question_index is not None:
            existing = next(
                (
                    message
                    for message in draft["messages"]
                    if message.get("questionIndex") == question_index
                ),
                None,
            )
            if existing is not None:
                existing["content"] = content
                existing["updatedAt"] = _now()
                draft["status"] = DraftStatus.INTERVIEWING.value
                draft["updatedAt"] = _now()
                return copy.deepcopy(existing)
        message = {
            "messageId": uuid4().hex,
            "role": "USER",
            "content": content,
            "questionIndex": question_index,
            "createdAt": _now(),
        }
        draft["messages"].append(message)
        draft["status"] = DraftStatus.INTERVIEWING.value
        draft["updatedAt"] = _now()
        return copy.deepcopy(message)

    def save_questions(self, employee_id: str, draft_id: str, plan: dict[str, Any]) -> dict[str, Any] | None:
        draft = self.drafts.get(draft_id)
        if draft is None or draft["employeeId"] != employee_id:
            return None
        draft["questions"] = plan.get("questions", [])
        draft["interviewPlan"] = plan
        draft["status"] = DraftStatus.INTERVIEWING.value
        draft["updatedAt"] = _now()
        return copy.deepcopy(plan)

    def add_source(
        self,
        employee_id: str,
        draft_id: str,
        *,
        original_name: str,
        media_type: str,
        data: bytes,
        extracted_text: str,
    ) -> dict[str, Any] | None:
        draft = self.drafts.get(draft_id)
        if draft is None or draft["employeeId"] != employee_id:
            return None
        source_id = uuid4().hex
        sha256 = hashlib.sha256(data).hexdigest()
        source = {
            "sourceId": source_id,
            "draftId": draft_id,
            "originalName": original_name,
            "mediaType": media_type,
            "sha256": sha256,
            "size": len(data),
            "extractionStatus": "EXTRACTED" if extracted_text else "STORED",
            "extractedText": extracted_text,
            "createdAt": _now(),
        }
        self.source_blobs[source_id] = data
        draft["sources"].append(source)
        draft["updatedAt"] = _now()
        return copy.deepcopy(source)

    def get_source_materials(self, employee_id: str, draft_id: str) -> list[SourceMaterial]:
        draft = self.drafts.get(draft_id)
        if draft is None or draft["employeeId"] != employee_id:
            return []
        return [
            SourceMaterial(
                source_id=source["sourceId"],
                original_name=source["originalName"],
                media_type=source["mediaType"],
                sha256=source["sha256"],
                extracted_text=source.get("extractedText", ""),
                data=self.source_blobs.get(source["sourceId"], b""),
            )
            for source in draft["sources"]
        ]

    def save_generated(
        self,
        employee_id: str,
        draft_id: str,
        *,
        ir: dict[str, Any],
        markdown: str,
        mermaid: str,
        title: str,
        model_id: str,
        validation: dict[str, Any],
        workspace_path: str | None = None,
    ) -> dict[str, Any] | None:
        draft = self.drafts.get(draft_id)
        if draft is None or draft["employeeId"] != employee_id:
            return None
        draft.update(
            {
                "title": title,
                "status": DraftStatus.REVIEW_READY.value,
                "modelId": model_id,
                "ir": copy.deepcopy(ir),
                "markdown": markdown,
                "mermaid": mermaid,
                "validation": copy.deepcopy(validation),
                "workspacePath": workspace_path,
                "updatedAt": _now(),
            }
        )
        return copy.deepcopy(draft)

    def publish(
        self,
        employee_id: str,
        draft_id: str,
        *,
        target_visibility: Visibility,
    ) -> dict[str, Any] | None:
        draft = self.drafts.get(draft_id)
        if draft is None or draft["employeeId"] != employee_id or not draft.get("markdown"):
            return None
        snapshot_hash = hashlib.sha256(
            (draft["markdown"] + "\n" + draft["mermaid"]).encode("utf-8")
        ).hexdigest()
        existing = next(
            (
                item
                for item in self.publications.values()
                if item["draftId"] == draft_id
                and item["approvedSnapshotSha256"] == snapshot_hash
                and item["targetVisibility"] == target_visibility.value
            ),
            None,
        )
        if existing:
            return copy.deepcopy(existing)
        document_id = uuid4().hex
        published_at = _now()
        promoted_markdown = render_promoted_sop_markdown(
            draft["markdown"],
            target_visibility=target_visibility.value,
            document_id=document_id,
            source_sha256=snapshot_hash,
            published_at=published_at,
        )
        publication = {
            "documentId": document_id,
            "version": 1,
            "draftId": draft_id,
            "title": draft["title"],
            "description": draft["ir"]["description"],
            "targetVisibility": target_visibility.value,
            "status": "PUBLISHED",
            "markdown": promoted_markdown,
            "mermaid": draft["mermaid"],
            "ir": copy.deepcopy(draft["ir"]),
            "templateCommit": draft["templateCommit"],
            "modelId": draft["modelId"],
            "approvedSnapshotSha256": snapshot_hash,
            "publishedAt": published_at,
        }
        self.publications[document_id] = publication
        draft["status"] = DraftStatus.PUBLISHED.value
        draft["updatedAt"] = _now()
        return copy.deepcopy(publication)

    def list_publications(self) -> list[dict[str, Any]]:
        return sorted(
            [copy.deepcopy(item) for item in self.publications.values() if item["status"] == "PUBLISHED"],
            key=lambda item: item["publishedAt"],
            reverse=True,
        )

    def get_publication(self, document_id: str) -> dict[str, Any] | None:
        item = self.publications.get(document_id)
        if item is None or item["status"] != "PUBLISHED":
            return None
        return copy.deepcopy(item)


class MongoStore:
    def __init__(self, uri: str, database_name: str, draft_retention_days: int = 30) -> None:
        from gridfs import GridFSBucket
        from pymongo import ASCENDING, DESCENDING, MongoClient

        self.client = MongoClient(uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)
        self.db = self.client[database_name]
        self.private_assets = GridFSBucket(self.db, bucket_name="private_assets")
        self.published_assets = GridFSBucket(self.db, bucket_name="published_assets")
        self.draft_retention_days = draft_retention_days
        self.db.sessions.create_index("sessionId", unique=True)
        self.db.sessions.create_index("expiresAt", expireAfterSeconds=0)
        self.db.drafts.create_index("draftId", unique=True)
        self.db.drafts.create_index([("employeeId", ASCENDING), ("status", ASCENDING), ("updatedAt", DESCENDING)])
        self.db.drafts.create_index("expiresAt", expireAfterSeconds=0)
        self.db.draft_messages.create_index([("draftId", ASCENDING), ("questionIndex", ASCENDING)])
        self.db.sources.create_index("sourceId", unique=True)
        self.db.sources.create_index([("draftId", ASCENDING), ("createdAt", DESCENDING)])
        self.db.publications.create_index([("documentId", ASCENDING), ("version", ASCENDING)], unique=True)
        self.db.publications.create_index([("status", ASCENDING), ("publishedAt", DESCENDING)])
        self.db.template_versions.create_index("commitSha", unique=True)

    def ping(self) -> None:
        self.client.admin.command("ping")

    @staticmethod
    def _clean(document: dict[str, Any] | None) -> dict[str, Any] | None:
        if document is None:
            return None
        cleaned = copy.deepcopy(document)
        cleaned.pop("_id", None)
        for key in ("markdownGridfsId", "mermaidGridfsId", "privateGridfsId", "extractedGridfsId"):
            if key in cleaned and cleaned[key] is not None:
                cleaned[key] = str(cleaned[key])
        return cleaned

    def create_session(self, session_id: str, employee_id: str, expires_at: datetime) -> None:
        self.db.sessions.update_one(
            {"sessionId": session_id},
            {"$set": {"employeeId": employee_id, "expiresAt": expires_at, "updatedAt": _now()}},
            upsert=True,
        )

    def create_draft(self, employee_id: str, description: str, template_commit: str) -> dict[str, Any]:
        draft_id = uuid4().hex
        now = _now()
        document = {
            "draftId": draft_id,
            "employeeId": employee_id,
            "title": description.strip().splitlines()[0][:80],
            "description": description.strip(),
            "status": DraftStatus.COLLECTING.value,
            "templateCommit": template_commit,
            "modelId": None,
            "questions": [],
            "markdownGridfsId": None,
            "mermaidGridfsId": None,
            "ir": None,
            "validation": None,
            "workspacePath": None,
            "createdAt": now,
            "updatedAt": now,
            "expiresAt": now + timedelta(days=self.draft_retention_days),
        }
        self.db.drafts.insert_one(document)
        return self.get_draft(employee_id, draft_id)  # type: ignore[return-value]

    def _hydrate_draft(self, document: dict[str, Any] | None) -> dict[str, Any] | None:
        cleaned = self._clean(document)
        if cleaned is None:
            return None
        draft_id = cleaned["draftId"]
        cleaned["messages"] = list(self.db.draft_messages.find({"draftId": draft_id}, {"_id": 0}).sort("createdAt", 1))
        cleaned["sources"] = [self._clean(item) for item in self.db.sources.find({"draftId": draft_id})]
        markdown_id = document.get("markdownGridfsId") if document else None
        mermaid_id = document.get("mermaidGridfsId") if document else None
        cleaned["markdown"] = self.private_assets.open_download_stream(markdown_id).read().decode("utf-8") if markdown_id else ""
        cleaned["mermaid"] = self.private_assets.open_download_stream(mermaid_id).read().decode("utf-8") if mermaid_id else ""
        return cleaned

    def list_drafts(self, employee_id: str) -> list[dict[str, Any]]:
        return [
            self._hydrate_draft(document)  # type: ignore[list-item]
            for document in self.db.drafts.find({"employeeId": employee_id}).sort("updatedAt", -1)
        ]

    def get_draft(self, employee_id: str, draft_id: str) -> dict[str, Any] | None:
        return self._hydrate_draft(self.db.drafts.find_one({"draftId": draft_id, "employeeId": employee_id}))

    def add_message(
        self,
        employee_id: str,
        draft_id: str,
        content: str,
        question_index: int | None = None,
    ) -> dict[str, Any] | None:
        if self.db.drafts.count_documents({"draftId": draft_id, "employeeId": employee_id}) == 0:
            return None
        existing = None
        if question_index is not None:
            existing = self.db.draft_messages.find_one(
                {"draftId": draft_id, "questionIndex": question_index}
            )
        if existing is not None:
            self.db.draft_messages.update_one(
                {"_id": existing["_id"]},
                {"$set": {"content": content, "updatedAt": _now()}},
            )
            message = self.db.draft_messages.find_one({"_id": existing["_id"]}) or existing
        else:
            message = {
                "messageId": uuid4().hex,
                "draftId": draft_id,
                "role": "USER",
                "content": content,
                "questionIndex": question_index,
                "createdAt": _now(),
            }
            self.db.draft_messages.insert_one(message)
        message.pop("_id", None)
        self.db.drafts.update_one(
            {"draftId": draft_id},
            {"$set": {"status": DraftStatus.INTERVIEWING.value, "updatedAt": _now()}},
        )
        return message

    def save_questions(self, employee_id: str, draft_id: str, plan: dict[str, Any]) -> dict[str, Any] | None:
        result = self.db.drafts.update_one(
            {"draftId": draft_id, "employeeId": employee_id},
            {"$set": {"questions": plan.get("questions", []), "interviewPlan": plan, "status": DraftStatus.INTERVIEWING.value, "updatedAt": _now()}},
        )
        return copy.deepcopy(plan) if result.matched_count else None

    def add_source(
        self,
        employee_id: str,
        draft_id: str,
        *,
        original_name: str,
        media_type: str,
        data: bytes,
        extracted_text: str,
    ) -> dict[str, Any] | None:
        if self.db.drafts.count_documents({"draftId": draft_id, "employeeId": employee_id}) == 0:
            return None
        source_id = uuid4().hex
        sha256 = hashlib.sha256(data).hexdigest()
        original_id = self.private_assets.upload_from_stream(
            original_name,
            io.BytesIO(data),
            metadata={"draftId": draft_id, "sourceId": source_id, "kind": "original", "sha256": sha256},
        )
        extracted_id = None
        if extracted_text:
            extracted_id = self.private_assets.upload_from_stream(
                f"{source_id}-extracted.txt",
                io.BytesIO(extracted_text.encode("utf-8")),
                metadata={"draftId": draft_id, "sourceId": source_id, "kind": "extracted"},
            )
        document = {
            "sourceId": source_id,
            "draftId": draft_id,
            "employeeId": employee_id,
            "originalName": original_name,
            "mediaType": media_type,
            "sha256": sha256,
            "size": len(data),
            "privateGridfsId": original_id,
            "extractedGridfsId": extracted_id,
            "extractionStatus": "EXTRACTED" if extracted_text else "STORED",
            "createdAt": _now(),
        }
        self.db.sources.insert_one(document)
        document.pop("_id", None)
        self.db.drafts.update_one({"draftId": draft_id}, {"$set": {"updatedAt": _now()}})
        return self._clean(document)

    def get_source_materials(self, employee_id: str, draft_id: str) -> list[SourceMaterial]:
        if self.db.drafts.count_documents({"draftId": draft_id, "employeeId": employee_id}) == 0:
            return []
        materials = []
        for source in self.db.sources.find({"draftId": draft_id, "employeeId": employee_id}):
            data = self.private_assets.open_download_stream(source["privateGridfsId"]).read()
            extracted = ""
            if source.get("extractedGridfsId"):
                extracted = self.private_assets.open_download_stream(source["extractedGridfsId"]).read().decode("utf-8")
            materials.append(
                SourceMaterial(
                    source_id=source["sourceId"],
                    original_name=source["originalName"],
                    media_type=source["mediaType"],
                    sha256=source["sha256"],
                    extracted_text=extracted,
                    data=data,
                )
            )
        return materials

    def _replace_private_file(self, previous_id: Any, filename: str, data: bytes, metadata: dict[str, Any]) -> Any:
        new_id = self.private_assets.upload_from_stream(filename, io.BytesIO(data), metadata=metadata)
        if previous_id:
            try:
                self.private_assets.delete(previous_id)
            except Exception:
                pass
        return new_id

    def save_generated(
        self,
        employee_id: str,
        draft_id: str,
        *,
        ir: dict[str, Any],
        markdown: str,
        mermaid: str,
        title: str,
        model_id: str,
        validation: dict[str, Any],
        workspace_path: str | None = None,
    ) -> dict[str, Any] | None:
        draft = self.db.drafts.find_one({"draftId": draft_id, "employeeId": employee_id})
        if draft is None:
            return None
        markdown_id = self._replace_private_file(
            draft.get("markdownGridfsId"),
            f"{draft_id}-sop.md",
            markdown.encode("utf-8"),
            {"draftId": draft_id, "kind": "sop"},
        )
        mermaid_id = self._replace_private_file(
            draft.get("mermaidGridfsId"),
            f"{draft_id}-diagram.md",
            mermaid.encode("utf-8"),
            {"draftId": draft_id, "kind": "diagram"},
        )
        self.db.drafts.update_one(
            {"draftId": draft_id},
            {"$set": {
                "title": title,
                "status": DraftStatus.REVIEW_READY.value,
                "modelId": model_id,
                "ir": ir,
                "markdownGridfsId": markdown_id,
                "mermaidGridfsId": mermaid_id,
                "validation": validation,
                "workspacePath": workspace_path,
                "updatedAt": _now(),
            }},
        )
        return self.get_draft(employee_id, draft_id)

    def publish(
        self,
        employee_id: str,
        draft_id: str,
        *,
        target_visibility: Visibility,
    ) -> dict[str, Any] | None:
        draft = self.get_draft(employee_id, draft_id)
        if draft is None or not draft.get("markdown"):
            return None
        snapshot_hash = hashlib.sha256((draft["markdown"] + "\n" + draft["mermaid"]).encode("utf-8")).hexdigest()
        existing = self.db.publications.find_one(
            {
                "draftId": draft_id,
                "approvedSnapshotSha256": snapshot_hash,
                "targetVisibility": target_visibility.value,
            }
        )
        if existing:
            return self._hydrate_publication(existing)
        document_id = uuid4().hex
        now = _now()
        promoted_markdown = render_promoted_sop_markdown(
            draft["markdown"],
            target_visibility=target_visibility.value,
            document_id=document_id,
            source_sha256=snapshot_hash,
            published_at=now,
        )
        publication = {
            "documentId": document_id,
            "version": 1,
            "draftId": draft_id,
            "title": draft["title"],
            "description": draft["ir"]["description"],
            "targetVisibility": target_visibility.value,
            "status": "PUBLISHING",
            "ir": draft["ir"],
            "templateCommit": draft["templateCommit"],
            "modelId": draft["modelId"],
            "approvedSnapshotSha256": snapshot_hash,
            "publishedAt": now,
        }
        self.db.publications.insert_one(publication)
        try:
            markdown_id = self.published_assets.upload_from_stream(
                f"{document_id}-v1-sop.md",
                io.BytesIO(promoted_markdown.encode("utf-8")),
                metadata={"documentId": document_id, "version": 1, "kind": "sop", "sha256": snapshot_hash},
            )
            mermaid_id = self.published_assets.upload_from_stream(
                f"{document_id}-v1-diagram.md",
                io.BytesIO(draft["mermaid"].encode("utf-8")),
                metadata={"documentId": document_id, "version": 1, "kind": "diagram"},
            )
            self.db.publications.update_one(
                {"documentId": document_id},
                {"$set": {"status": "PUBLISHED", "markdownGridfsId": markdown_id, "mermaidGridfsId": mermaid_id}},
            )
            self.db.drafts.update_one(
                {"draftId": draft_id},
                {"$set": {"status": DraftStatus.PUBLISHED.value, "updatedAt": _now()}},
            )
        except Exception:
            self.db.publications.update_one({"documentId": document_id}, {"$set": {"status": "PUBLISH_FAILED"}})
            raise
        return self.get_publication(document_id)

    def _hydrate_publication(self, document: dict[str, Any] | None) -> dict[str, Any] | None:
        cleaned = self._clean(document)
        if cleaned is None:
            return None
        markdown_id = document.get("markdownGridfsId") if document else None
        mermaid_id = document.get("mermaidGridfsId") if document else None
        cleaned["markdown"] = self.published_assets.open_download_stream(markdown_id).read().decode("utf-8") if markdown_id else ""
        cleaned["mermaid"] = self.published_assets.open_download_stream(mermaid_id).read().decode("utf-8") if mermaid_id else ""
        return cleaned

    def list_publications(self) -> list[dict[str, Any]]:
        return [
            self._hydrate_publication(document)  # type: ignore[list-item]
            for document in self.db.publications.find({"status": "PUBLISHED"}).sort("publishedAt", -1)
        ]

    def get_publication(self, document_id: str) -> dict[str, Any] | None:
        return self._hydrate_publication(self.db.publications.find_one({"documentId": document_id, "status": "PUBLISHED"}))

    def cleanup_expired_private_data(self) -> dict[str, int]:
        """TTL로 draft 문서가 사라지기 전에 연결된 개인 GridFS 데이터를 정리한다."""
        expired = list(self.db.drafts.find({"expiresAt": {"$lte": _now()}}, {"draftId": 1, "markdownGridfsId": 1, "mermaidGridfsId": 1}))
        deleted_files = 0
        for draft in expired:
            draft_id = draft["draftId"]
            sources = list(self.db.sources.find({"draftId": draft_id}, {"privateGridfsId": 1, "extractedGridfsId": 1}))
            file_ids = [draft.get("markdownGridfsId"), draft.get("mermaidGridfsId")]
            for source in sources:
                file_ids.extend([source.get("privateGridfsId"), source.get("extractedGridfsId")])
            for file_id in (item for item in file_ids if item is not None):
                try:
                    self.private_assets.delete(file_id)
                    deleted_files += 1
                except Exception:
                    pass
            self.db.sources.delete_many({"draftId": draft_id})
            self.db.draft_messages.delete_many({"draftId": draft_id})
            self.db.drafts.delete_one({"draftId": draft_id})
        return {"drafts": len(expired), "files": deleted_files}
