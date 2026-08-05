from __future__ import annotations


def test_complete_draft_to_wiki_flow(client) -> None:
    session_response = client.post("/api/session")
    assert session_response.status_code == 201
    session_data = session_response.json()["data"]
    assert len(session_data["employeeId"]) == 7

    draft_response = client.post(
        "/api/drafts",
        json={"description": "매주 품질 Trend를 확인하고 보고서를 작성합니다."},
    )
    assert draft_response.status_code == 201
    draft = draft_response.json()["data"]
    draft_id = draft["draftId"]
    assert draft["status"] == "COLLECTING"

    questions_response = client.post(f"/api/drafts/{draft_id}/questions")
    assert questions_response.status_code == 200
    questions = questions_response.json()["data"]
    assert questions["questions"] == ["이상으로 판단하는 기준은 무엇인가요?"]

    answer_response = client.post(
        f"/api/drafts/{draft_id}/messages",
        json={"content": "관리 기준을 초과하면 이상입니다."},
    )
    assert answer_response.status_code == 201

    generate_response = client.post(f"/api/drafts/{draft_id}/generate")
    assert generate_response.status_code == 200
    generated = generate_response.json()["data"]
    assert generated["status"] == "REVIEW_READY"
    assert generated["title"] == "주간 품질 Trend 보고 SOP"
    assert "# 목적" in generated["markdown"]
    assert "flowchart LR" in generated["mermaid"]

    assert client.get("/api/wiki").json()["data"] == []

    approve_response = client.post(
        f"/api/drafts/{draft_id}/approve",
        json={"targetVisibility": "PUBLIC", "confirmed": True, "sensitiveContentReviewed": True},
    )
    assert approve_response.status_code == 201
    publication = approve_response.json()["data"]
    assert publication["status"] == "PUBLISHED"
    assert "type: boi/sop" in publication["markdown"]
    assert "visibility: public" in publication["markdown"]
    assert "local_only: false" in publication["markdown"]
    assert "promotion_status: promoted" in publication["markdown"]
    assert "employee_id:" not in publication["markdown"]
    assert "local_owner_ref:" not in publication["markdown"]

    local_draft = client.get(f"/api/drafts/{draft_id}").json()["data"]
    assert "visibility: local-private" in local_draft["markdown"]
    assert "local_only: true" in local_draft["markdown"]

    wiki_response = client.get("/api/wiki")
    assert wiki_response.status_code == 200
    assert len(wiki_response.json()["data"]) == 1

    detail_response = client.get(f"/api/wiki/{publication['documentId']}")
    assert detail_response.status_code == 200
    assert detail_response.json()["data"]["title"] == "주간 품질 Trend 보고 SOP"


def test_draft_is_scoped_to_browser_session(client) -> None:
    draft = client.post(
        "/api/drafts",
        json={"description": "내 개인 업무입니다."},
    ).json()["data"]

    client.cookies.clear()
    client.post("/api/session")

    response = client.get(f"/api/drafts/{draft['draftId']}")
    assert response.status_code == 404


def test_review_edit_regenerates_all_artifacts(client) -> None:
    draft = client.post(
        "/api/drafts",
        json={"description": "읽기 화면 수정 반영을 검증하는 업무 설명입니다."},
    ).json()["data"]
    generated = client.post(f"/api/drafts/{draft['draftId']}/generate").json()["data"]
    revised_ir = generated["ir"]
    revised_ir["title"] = "수정된 주간 보고 SOP"
    revised_ir["steps"][0]["title"] = "수정된 자료 수집"

    response = client.post(f"/api/drafts/{draft['draftId']}/revise", json={"ir": revised_ir})

    assert response.status_code == 200
    revised = response.json()["data"]
    assert revised["status"] == "REVIEW_READY"
    assert revised["title"] == "수정된 주간 보고 SOP"
    assert "# 수정된 주간 보고 SOP" in revised["markdown"]
    assert "수정된 자료 수집" in revised["mermaid"]


def test_revision_requires_generated_review_ready_draft(client) -> None:
    draft = client.post(
        "/api/drafts",
        json={"description": "아직 생성 전인 초안 수정 제한을 검증합니다."},
    ).json()["data"]
    response = client.post(
        f"/api/drafts/{draft['draftId']}/revise",
        json={
            "ir": {
                "title": "x",
                "description": "desc",
                "purpose": "purpose",
                "steps": [{"number": 1, "title": "step", "description": "desc", "actor": "담당자"}],
            }
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DRAFT_NOT_READY"


def test_approval_requires_explicit_confirmation(client) -> None:
    draft = client.post(
        "/api/drafts",
        json={"description": "승인 확인 테스트 업무"},
    ).json()["data"]
    client.post(f"/api/drafts/{draft['draftId']}/generate")

    response = client.post(
        f"/api/drafts/{draft['draftId']}/approve",
        json={"targetVisibility": "PUBLIC", "confirmed": False, "sensitiveContentReviewed": False},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CONFIRMATION_REQUIRED"


def test_approval_requires_sensitive_content_review(client) -> None:
    draft = client.post(
        "/api/drafts",
        json={"description": "민감정보 검토 확인 테스트 업무"},
    ).json()["data"]
    client.post(f"/api/drafts/{draft['draftId']}/generate")

    response = client.post(
        f"/api/drafts/{draft['draftId']}/approve",
        json={"targetVisibility": "TEAM", "confirmed": True, "sensitiveContentReviewed": False},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SENSITIVE_REVIEW_REQUIRED"


def test_text_source_is_saved_in_private_draft(client) -> None:
    draft = client.post(
        "/api/drafts",
        json={"description": "매일 설비 점검 결과를 정리하는 업무입니다."},
    ).json()["data"]

    upload = client.post(
        f"/api/drafts/{draft['draftId']}/sources",
        files={"file": ("checklist.txt", "온도 확인\n압력 확인", "text/plain")},
    )

    assert upload.status_code == 201
    assert upload.json()["data"]["originalName"] == "checklist.txt"
    detail = client.get(f"/api/drafts/{draft['draftId']}").json()["data"]
    assert len(detail["sources"]) == 1


def test_security_headers_are_present(client) -> None:
    response = client.get("/")

    assert "업무 설명 예시" in response.text
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_model_failure_returns_actionable_json_error(store) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    class FailingProvider:
        model_id = "failing-test-model"

        def propose_questions(self, description, messages, sources):
            raise RuntimeError("secret upstream error")

        def build_sop(self, description, messages, sources):
            raise RuntimeError("secret upstream error")

    app = create_app(store=store, provider=FailingProvider(), demo_mode=True)
    with TestClient(app) as isolated_client:
        draft = isolated_client.post(
            "/api/drafts",
            json={"description": "모델 오류 응답을 검증하는 업무 설명입니다."},
        ).json()["data"]
        response = isolated_client.post(f"/api/drafts/{draft['draftId']}/questions")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "MODEL_REQUEST_FAILED"
    assert "secret upstream error" not in response.text


def test_answer_retry_updates_same_question_instead_of_appending(client) -> None:
    draft = client.post(
        "/api/drafts",
        json={"description": "답변 재시도 중복 방지를 검증하는 업무입니다."},
    ).json()["data"]

    first = client.post(
        f"/api/drafts/{draft['draftId']}/messages",
        json={"content": "첫 번째 답변", "questionIndex": 0},
    )
    second = client.post(
        f"/api/drafts/{draft['draftId']}/messages",
        json={"content": "수정된 답변", "questionIndex": 0},
    )
    detail = client.get(f"/api/drafts/{draft['draftId']}").json()["data"]

    assert first.status_code == 201
    assert second.status_code == 201
    assert len(detail["messages"]) == 1
    assert detail["messages"][0]["content"] == "수정된 답변"
    assert detail["messages"][0]["questionIndex"] == 0
