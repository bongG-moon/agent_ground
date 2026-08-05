from __future__ import annotations

from collections.abc import Iterator
import os

import pytest
from fastapi.testclient import TestClient

os.environ["AI_SOP_DEMO_MODE"] = "true"

from app.domain import InterviewPlan, SopDraftIR, SopStep
from app.main import create_app
from app.storage import MemoryStore


class FakeGeminiProvider:
    model_id = "fake-gemini-test"

    def propose_questions(self, description, messages, sources):
        return InterviewPlan(
            summary="주간 품질 Trend 보고 업무",
            questions=["이상으로 판단하는 기준은 무엇인가요?"],
            covered_fields=["purpose", "inputs"],
            missing_fields=["decision_criteria"],
        )

    def build_sop(self, description, messages, sources):
        return SopDraftIR(
            title="주간 품질 Trend 보고 SOP",
            description="주간 품질 지표를 점검하고 결과를 공유하는 절차",
            purpose="품질 이상을 조기에 발견하고 관계자에게 공유한다.",
            inputs=["주간 품질 데이터", "이상 기준표"],
            steps=[
                SopStep(
                    number=1,
                    title="데이터 수집",
                    description="품질 시스템에서 주간 데이터를 내려받는다.",
                    actor="담당자",
                    system="품질 시스템",
                    source_refs=["user-description"],
                ),
                SopStep(
                    number=2,
                    title="이상 여부 판단",
                    description="기준표와 비교해 이상 여부를 판단한다.",
                    actor="담당자",
                    is_decision=True,
                    yes_target="보고서 작성",
                    no_target="종료",
                    source_refs=["answer-1"],
                ),
                SopStep(
                    number=3,
                    title="보고서 작성",
                    description="결과와 조치 의견을 보고서로 작성한다.",
                    actor="담당자",
                    source_refs=["user-description"],
                ),
            ],
            decision_criteria=["관리 기준을 초과하면 이상으로 판단"],
            exceptions=["데이터 누락 시 원천 시스템 담당자에게 확인"],
            completion_conditions=["보고서가 저장되고 공유 대상에게 전달됨"],
            open_questions=[],
            automation_candidates=["주간 데이터 수집 자동화"],
        )


@pytest.fixture()
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture()
def client(store: MemoryStore) -> Iterator[TestClient]:
    app = create_app(store=store, provider=FakeGeminiProvider(), demo_mode=True)
    with TestClient(app) as test_client:
        yield test_client
