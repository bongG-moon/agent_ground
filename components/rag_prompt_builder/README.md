# RAG Prompt Builder

품질 게이트를 통과한 ACL 허용 근거만 비신뢰 데이터로 감싸고 엄격한 근거 ID 출력 계약을 만듭니다.

## 상태

- ID: `rag_prompt_builder`
- 버전: `0.1.0`
- 상태: `user_testing`
- Standalone: `true`
- 사용 범위: `enterprise_document_rag_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| Gate | `gate` | `Data, JSON` | False | True | False |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| Prompt | `prompt` | `Message` | `build_prompt` |



## 등록

`rag_prompt_builder.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
