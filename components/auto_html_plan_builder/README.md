# 03 기본 리포트 계획

요소 추천 결과를 바탕으로 LLM이 보완할 수 있는 기본 리포트 계획을 만듭니다.

## 상태

- ID: `auto_html_plan_builder`
- 버전: `0.9.0`
- 상태: `user_testing`
- Standalone: `true`
- 사용 범위: `html_report_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| 요청 데이터 | `payload` | `DataInput` | False | True | False |
| 데이터 분석 결과 | `data_profile` | `DataInput` | False | True | False |
| 요소 추천 결과 | `html_component_catalog` | `DataInput` | False | True | False |
| 블록 수 제한 | `max_blocks` | `MessageTextInput` | False | False | True |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| 기본 계획 | `payload_out` | `Output` | `build_payload` |



## 등록

`auto_html_plan_builder.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
