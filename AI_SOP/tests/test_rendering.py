from __future__ import annotations

from datetime import datetime, timezone

import yaml

from app.domain import SopDraftIR, SopStep
from app.rendering import render_mermaid, render_promoted_sop_markdown, render_sop_markdown


def sample_ir() -> SopDraftIR:
    return SopDraftIR(
        title="설비 이상 대응 SOP",
        description="설비 이상 발생 시 초동 대응 절차",
        purpose="안전하게 설비를 정지하고 담당자에게 알린다.",
        inputs=["설비 알람", "점검 체크리스트"],
        steps=[
            SopStep(
                number=1,
                title="알람 확인",
                description="알람 코드와 발생 시각을 확인한다.",
                actor="운영자",
                system="설비 화면",
                source_refs=["source-1"],
            ),
            SopStep(
                number=2,
                title="긴급 정지 판단",
                description="안전 기준에 따라 긴급 정지 여부를 판단한다.",
                actor="운영자",
                is_decision=True,
                yes_target="설비 정지",
                no_target="상태 감시",
                source_refs=["answer-2"],
            ),
        ],
        decision_criteria=["안전 기준 초과 시 긴급 정지"],
        exceptions=["알람 코드 확인 불가 시 설비 담당자 호출"],
        completion_conditions=["조치 결과와 증빙이 기록됨"],
        open_questions=["야간 담당자 연락처 확인 필요"],
        automation_candidates=["알람 발생 시 자동 통보"],
    )


def test_render_sop_markdown_preserves_boi_private_contract() -> None:
    markdown = render_sop_markdown(
        sample_ir(),
        employee_id="1234567",
        template_commit="abc123",
        model_id="gemini-test",
        source_refs=[{"type": "upload", "ref": "source-1.png"}],
    )

    assert 'okf_version: "0.1"' in markdown
    assert 'employee_id: "1234567"' in markdown
    assert "local_owner_ref: local-private:1234567" in markdown
    assert "visibility: local-private" in markdown
    assert "local_only: true" in markdown
    assert "promotion_status: local_only" in markdown
    assert "# 목적" in markdown
    assert "# 입력" in markdown
    assert "# 절차" in markdown
    assert "# 판단 기준" in markdown
    assert "# 예외 상황" in markdown
    assert "# 완료 조건" in markdown
    assert "template_commit: abc123" in markdown
    assert "model_id: gemini-test" in markdown


def test_render_mermaid_labels_decision_edges() -> None:
    mermaid = render_mermaid(sample_ir())

    assert "flowchart LR" in mermaid
    assert 'decision_2{"02. 긴급 정지 판단"}' in mermaid
    assert '-- "예" -->' in mermaid
    assert '-- "아니오" -->' in mermaid
    assert "# Source Mapping" in mermaid


def test_render_mermaid_resolves_numbered_branch_targets() -> None:
    ir = sample_ir().model_copy(
        update={
            "steps": [
                SopStep(number=1, title="수집", description="자료를 수집한다."),
                SopStep(
                    number=2,
                    title="누락 판단",
                    description="누락 여부를 판단한다.",
                    is_decision=True,
                    yes_target="단계 3",
                    no_target="4단계",
                ),
                SopStep(number=3, title="재추출", description="자료를 재추출한다."),
                SopStep(number=4, title="비교", description="자료를 비교한다."),
            ]
        }
    )

    mermaid = render_mermaid(ir)

    assert 'decision_2 -- "예" --> stage_3' in mermaid
    assert 'decision_2 -- "아니오" --> stage_4' in mermaid
    assert 'decision_2_yes["단계 3"]' not in mermaid
    assert 'decision_2_no["4단계"]' not in mermaid


def test_render_mermaid_resolves_bare_numeric_branch_targets() -> None:
    ir = sample_ir().model_copy(
        update={
            "steps": [
                SopStep(number=1, title="수집", description="자료를 수집한다."),
                SopStep(
                    number=2,
                    title="누락 판단",
                    description="누락 여부를 판단한다.",
                    is_decision=True,
                    yes_target="3",
                    no_target="4",
                ),
                SopStep(number=3, title="재추출", description="자료를 재추출한다."),
                SopStep(number=4, title="비교", description="자료를 비교한다."),
            ]
        }
    )

    mermaid = render_mermaid(ir)

    assert 'decision_2 -- "예" --> stage_3' in mermaid
    assert 'decision_2 -- "아니오" --> stage_4' in mermaid
    assert 'decision_2_yes(["3"])' not in mermaid
    assert 'decision_2_no(["4"])' not in mermaid


def test_render_promoted_markdown_replaces_private_metadata() -> None:
    local_markdown = render_sop_markdown(
        sample_ir(),
        employee_id="1234567",
        template_commit="abc123",
        model_id="gemini-test",
        source_refs=[{"type": "upload", "ref": "source-1.png"}],
    )

    promoted = render_promoted_sop_markdown(
        local_markdown,
        target_visibility="PUBLIC",
        document_id="doc-123",
        source_sha256="a" * 64,
        published_at=datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
    )
    metadata = yaml.safe_load(promoted.split("---", 2)[1])

    assert metadata["boi_profile_version"] == "0.1"
    assert metadata["type"] == "boi/sop"
    assert metadata["boi_id"] == "boi:public:sop:doc-123"
    assert metadata["visibility"] == "public"
    assert metadata["classification"] == "public"
    assert metadata["local_only"] is False
    assert metadata["promotion_status"] == "promoted"
    assert metadata["contains_sensitive"] == "no"
    assert metadata["promotion_source_sha256"] == "a" * 64
    assert "employee_id" not in metadata
    assert "local_owner_ref" not in metadata
    assert "# 목적" in promoted
