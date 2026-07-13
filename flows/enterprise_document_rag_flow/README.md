# 사내 문서 RAG Flow

사내 규정·매뉴얼·FAQ를 질문할 때, 사용자가 볼 수 있는 문서만 검색하고 답변 문장과 출처를 함께 반환하는 Langflow `1.8.2` 실행 예시입니다. 6개 기능 단위 Component와 3개 Flow 내부 노드는 모두 파일 하나로 등록되는 Standalone 방식입니다.

## 이번 예시가 실제로 보장하는 범위

- 별도 LLM API key, 임베딩 모델, Vector DB 없이 기본 질문을 실행할 수 있습니다.
- 문서 정규화 → 민감정보 baseline 검사 → 안정적인 chunk ID 생성 → 권한 선필터 → 검색 품질 판정 → 답변/거절 → 인용 재구성 순서를 한 Flow에서 확인합니다.
- 검색 결과가 없거나 품질이 낮으면 모르는 내용을 만들지 않고 답변을 거절합니다.
- 답변의 인용은 모델이 작성한 출처를 그대로 믿지 않고, 허용된 evidence ID에서 다시 만듭니다.
- 샘플 문서는 Flow 안에 포함되어 있어 JSON을 가져온 직후 구조와 기본 실행을 확인할 수 있습니다.

## 의도적으로 포함하지 않은 운영 기능

이 버전의 검색 backend는 `payload_lexical_v1`입니다. 같은 실행 안의 작은 문서 묶음을 영문·한글 token/부분 문자열로 찾는 검증용 색인이며, semantic vector search나 영속 저장소가 아닙니다.

- 서버 재시작 후 유지되는 증분 색인
- 대규모 PDF/Office 문서의 비동기 OCR·파싱
- 사내 SSO/JWT에서 검증한 실제 사용자 권한
- 운영 Vector DB에서 metadata filter를 포함한 검색
- 완전한 DLP/PII 탐지
- 생성형 모델이 작성하는 장문 답변

이 항목들은 데모 계약이 확인된 뒤 각각 별도 ingestion flow, identity adapter, vector-store adapter, DLP service, LLM adapter로 교체해야 합니다. 특히 Langflow `1.8.2` 기본 Milvus 검색은 ACL metadata filter를 입력으로 받지 않으므로 이 예시의 안전한 retriever를 그대로 대체할 수 없습니다.

## 캔버스 구조

```text
[문서 준비]
00 Document Input Normalizer
  -> 01 PII & Confidential Data Guard
  -> 02 Document Chunk & Payload Index Builder
                                         \
                                          -> 04 ACL Evidence Retriever
                                         /
[질문 준비]
Chat Input
  -> 03 RAG Request Context Normalizer
  -> 04 ACL Evidence Retriever
  -> 05 Retrieval Quality Gate
      -> 06 RAG Prompt Builder (선택적 LLM 연결 지점)
      -> 07 Grounded Answer Builder
  -> 08 Citation Response Builder
  -> Chat Output
```

기본 답변 경로에는 외부 모델이 없습니다. `06 RAG Prompt Builder`의 Message 출력을 사내 승인 모델에 연결하고, 그 응답을 `07 Grounded Answer Builder.llm_response`에 연결하면 선택적으로 생성형 답변을 사용할 수 있습니다. 응답이 비었거나 JSON이 잘못되었거나 존재하지 않는 evidence ID를 참조하면 deterministic 근거 답변으로 되돌아갑니다.

## 첫 실행

1. Langflow Builder에서 `enterprise_document_rag_flow.json`을 가져옵니다.
2. 별도 문서를 넣지 않은 상태에서 `Use Demo Corpus`가 켜져 있는지 확인합니다.
3. Chat Input에 `RAG를 왜 문서 적재 flow와 사용자 질문 flow로 나눠야 해?`를 입력합니다.
4. Chat Output에 답변, `[1]` 인용, 문서명과 page/locator가 함께 표시되는지 확인합니다.
5. `회사 대표의 혈액형은?`을 입력해 근거 부족 거절이 나오는지 확인합니다.
6. Request Context의 demo 역할을 `employee`로 둔 상태에서 보안 전용 문서 질문이 노출되지 않는지 확인합니다.

## 운영 전환 순서

1. `Use Demo Identity`를 끄고 `trusted_context`를 사내 인증 gateway의 검증된 Data에 연결합니다.
2. 문서 입력 앞에 승인된 file parser/OCR 및 malware 검사 단계를 둡니다.
3. `Document Chunk & Payload Index Builder`를 stable ID upsert/delete가 가능한 영속 index writer로 교체합니다.
4. `ACL Evidence Retriever` backend를 검색 시점에 ACL filter를 강제하는 전용 vector retriever로 교체합니다.
5. PII guard를 사내 DLP 서비스로 보강하고 보존·삭제·감사 정책을 연결합니다.
6. 평가 질문과 권한별 negative test를 통과한 뒤에만 `approved`로 변경합니다.

## 개발자 재검증

Langflow Desktop의 실제 Python runtime으로 Flow JSON을 다시 생성·검사합니다.

```powershell
$lfPython = "$env:LOCALAPPDATA\com.LangflowDesktop\.langflow-venv\Scripts\python.exe"
& $lfPython scripts\build_enterprise_document_rag_flow.py --check
& $lfPython -m pytest -q flows\enterprise_document_rag_flow\tests\test_enterprise_document_rag.py
python scripts\validate_project.py
```

JSON Upload의 HTTP 201은 DB 저장만 확인하므로 완료 조건이 아닙니다. 실제 Builder에서 전체 실행 후 모든 실행 node가 valid인지, Chat Output에 답변·숫자 인용·문서명·page가 있는지 확인합니다.

## 파일

- `enterprise_document_rag_flow.json`: 단일 Flow 가져오기 파일
- `CONNECTION_GUIDE.md`: 포트 단위 연결·교체 가이드
- `component_refs.json`: Flow 밖에서도 재사용하는 6개 Component
- `internal_nodes.json`, `nodes/`: 이 Flow에 종속된 요청·prompt·최종 인용 조립 노드 3개
- `samples/sample_enterprise_documents.json`: 외부 Data 입력 계약 예시
- `samples/TEST_QUESTIONS_AND_EXPECTED.md`: 허용·거절·보안 테스트
- `tests/test_enterprise_document_rag.py`: Component·내부 노드·payload·ACL·인용 회귀 테스트
- `../../html/troubleshooting/enterprise-document-rag-langflow-1-8-2.html`: 실제 구현 중 발견한 오탐·Builder 호환성 문제 해결 기록
