# 사내 문서 RAG Flow 연결 가이드

## 1. 가져오기

가장 간단한 방법은 `enterprise_document_rag_flow.json` 한 파일을 Langflow Builder로 가져오는 것입니다. JSON에는 9개 Standalone Component code와 Chat Input/Output node가 모두 포함됩니다. 별도 Python 파일 등록은 수정·재사용할 때만 필요합니다.

## 2. 기본 연결표

| 순서 | From | Output | To | Input | 전달 타입 |
| --- | --- | --- | --- | --- | --- |
| 1 | 00 Document Input Normalizer | `documents` | 01 PII & Confidential Data Guard | `documents` | Data |
| 2 | 01 PII & Confidential Data Guard | `safe_documents` | 02 Document Chunk & Payload Index Builder | `documents` | Data |
| 3 | Chat Input | `message` | 03 RAG Request Context Normalizer | `question` | Message |
| 4 | 03 RAG Request Context Normalizer | `request` | 04 ACL Evidence Retriever | `request` | Data |
| 5 | 02 Document Chunk & Payload Index Builder | `document_index` | 04 ACL Evidence Retriever | `document_index` | Data |
| 6 | 04 ACL Evidence Retriever | `retrieval` | 05 Retrieval Quality Gate | `retrieval` | Data |
| 7 | 05 Retrieval Quality Gate | `gate` | 06 RAG Prompt Builder | `gate` | Data |
| 8 | 05 Retrieval Quality Gate | `gate` | 07 Grounded Answer Builder | `gate` | Data |
| 9 | 07 Grounded Answer Builder | `answer` | 08 Citation Response Builder | `answer` | Data |
| 10 | 08 Citation Response Builder | `message` | Chat Output | `input_value` | Message |

## 3. 문서 입력 계약

`Document Input Normalizer.documents_input`에는 Data로 다음 중 하나를 연결할 수 있습니다.

```json
{
  "documents": [
    {
      "document_id": "policy-ai-001",
      "title": "사내 AI 데이터 취급 지침",
      "version": "2026.1",
      "content": "...",
      "page": 3,
      "source_locator": "AI-DATA-HANDBOOK#p3",
      "classification": "internal",
      "allowed_roles": ["employee"],
      "allowed_groups": ["all-employees"],
      "active": true,
      "deleted": false
    }
  ]
}
```

`page`를 알 수 없으면 실제 문서의 section/anchor를 `source_locator`로 제공해야 합니다. 둘 다 없는 문서는 답변 인용에 사용할 수 없습니다. PDF text extraction 결과가 페이지 경계를 보존하지 못한다면 page placeholder를 유지하는 parser 또는 별도 OCR flow를 사용합니다.

## 4. 신원·권한 연결

기본 `Use Demo Identity=true`는 오직 샘플 실행용입니다. 운영에서는 다음과 같이 연결합니다.

```text
사내 인증 Gateway / JWT 검증 Component
  -> trusted_context Data
  -> 03 RAG Request Context Normalizer
```

검증된 payload의 최소 예시는 다음과 같습니다.

```json
{
  "identity_verified": true,
  "subject_id": "opaque-user-id",
  "roles": ["employee"],
  "groups": ["all-employees", "finance"],
  "tenant_id": "company"
}
```

사용자 질문, Chat Input text, 임의 HTTP body에 적힌 역할은 권한으로 인정하면 안 됩니다. `Use Demo Identity=false`인데 검증된 context가 없으면 fail-closed가 정상입니다.

## 5. 선택적 LLM 연결

```text
06 RAG Prompt Builder.prompt
  -> 승인된 Language Model의 input
  -> 07 Grounded Answer Builder.llm_response
```

모델에게는 evidence를 신뢰할 수 없는 데이터 구역으로 구분해 전달하며, 응답은 `{ "answer": "...", "used_evidence_ids": ["ev-..."] }` 형태여야 합니다. 존재하지 않거나 권한 밖 evidence ID, 모델이 새로 만든 문서명·페이지·URL은 폐기합니다. 최종 인용은 `08 Citation Response Builder`가 허용된 evidence에서 다시 만듭니다.

## 6. 운영 Vector DB로 바꾸는 기준

- document/version/chunk 단위 stable ID upsert와 tombstone 삭제
- tenant/role/group/classification filter가 similarity search 실행 전에 적용됨
- ACL metadata가 없는 record는 deny
- 동일 문서 재적재 시 row 수가 증가하지 않음
- 검색 결과에 page 또는 source locator가 항상 존재함
- 권한 밖 문서의 제목·ID·후보 수가 사용자 응답과 trace에 노출되지 않음
- 실제 collection row count와 대표 검색 결과를 통합 테스트에서 확인함

Langflow `1.8.2` 기본 Milvus Component는 이 기준의 ACL filter와 stable-ID replacement를 보장하지 않으므로, 운영 전환에는 전용 Standalone adapter가 필요합니다.
