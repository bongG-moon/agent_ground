# Grounded Answer Builder

LLM의 근거 ID를 서버 allowlist와 교차 검증하고, 부적합 응답은 허용 근거의 deterministic 답변으로 대체합니다.

## 상태

- ID: `grounded_answer_builder`
- 버전: `0.1.0`
- 상태: `user_testing`
- Standalone: `true`
- 사용 범위: `enterprise_document_rag_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| Gate | `gate` | `Data, JSON` | False | True | False |
| LLM Response | `llm_response` | `MessageTextInput` | False | False | False |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| Answer | `answer` | `Data` | `build_answer` |



## 등록

`grounded_answer_builder.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
