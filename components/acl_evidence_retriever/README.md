# ACL Evidence Retriever

문서 ACL을 먼저 적용한 뒤 허용된 문서만 점수화하며, 의심스러운 문서 지시는 검색 근거에서 제외합니다.

## 상태

- ID: `acl_evidence_retriever`
- 버전: `0.1.0`
- 상태: `user_testing`
- Standalone: `true`
- 사용 범위: `enterprise_document_rag_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| Request | `request` | `Data, JSON` | False | True | False |
| Document Index | `document_index` | `Data, JSON` | False | True | False |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| Retrieval | `retrieval` | `Data` | `build_retrieval` |



## 등록

`acl_evidence_retriever.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
