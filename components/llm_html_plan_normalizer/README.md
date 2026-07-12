# 03b LLM 계획 검증

LLM이 만든 리포트 계획 JSON을 검증하고 렌더러가 쓸 최종 계획으로 정리합니다.

## 상태

- ID: `llm_html_plan_normalizer`
- 버전: `0.9.0`
- 상태: `user_testing`
- Standalone: `true`
- 사용 범위: `html_report_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| 기본 계획 | `base_payload` | `DataInput` | False | True | False |
| LLM 응답 | `llm_response` | `MessageTextInput` | False | True | False |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| 최종 계획 | `payload_out` | `Output` | `build_payload` |



## 등록

`llm_html_plan_normalizer.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
