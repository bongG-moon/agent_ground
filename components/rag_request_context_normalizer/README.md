# RAG Request Context Normalizer

사용자 질문과 서버에서 검증한 사용자·조직·역할 컨텍스트를 fail-closed 요청으로 정규화합니다.

## 상태

- ID: `rag_request_context_normalizer`
- 버전: `0.1.0`
- 상태: `user_testing`
- Standalone: `true`
- 사용 범위: `enterprise_document_rag_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| Question | `question` | `MessageTextInput` | False | True | False |
| Trusted Context | `trusted_context` | `Data, JSON` | False | False | False |
| Use Demo Identity | `use_demo_identity` | `BoolInput` | False | False | False |
| Demo User ID | `demo_user_id` | `MessageTextInput` | False | False | True |
| Demo Tenant ID | `demo_tenant_id` | `MessageTextInput` | False | False | True |
| Demo Roles | `demo_roles` | `MessageTextInput` | False | False | True |
| Demo Groups | `demo_groups` | `MessageTextInput` | False | False | True |
| Demo Clearance | `demo_clearance` | `DropdownInput` | False | False | True |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| Request | `request` | `Data` | `build_request` |



## 등록

`rag_request_context_normalizer.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
