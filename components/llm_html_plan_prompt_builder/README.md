# 03a 프롬프트 변수 준비

Langflow 기본 Prompt Template에 연결할 변수 값을 개별 출력으로 준비합니다.

## 상태

- ID: `llm_html_plan_prompt_builder`
- 버전: `0.9.0`
- 상태: `user_testing`
- Standalone: `true`
- 사용 범위: `html_report_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| 기본 계획 | `payload` | `DataInput` | False | True | False |
| 추가 구현 지시사항 | `design_instruction` | `MessageTextInput` | False | False | False |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| 사용자_요청_JSON | `user_request_json` | `Output` | `build_user_request_json` |
| 리포트_컨텍스트_JSON | `report_context_json` | `Output` | `build_report_context_json` |
| 디자인_지시 | `design_instruction_text` | `Output` | `build_design_instruction_text` |
| 렌더링_규칙 | `rendering_rules` | `Output` | `build_rendering_rules` |
| 출력_스키마_JSON | `output_schema_json` | `Output` | `build_output_schema_json` |



## 등록

`llm_html_plan_prompt_builder.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
