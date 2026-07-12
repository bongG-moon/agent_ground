# 02 기본 요소 양식/추천

내부 기본 요소 양식을 바탕으로 데이터와 요청 의도에 맞는 HTML 리포트 블록 후보를 추천합니다.

## 상태

- ID: `html_component_catalog_builder`
- 버전: `0.9.0`
- 상태: `user_testing`
- Standalone: `true`
- 사용 범위: `html_report_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| 데이터 분석 결과 | `data_profile` | `DataInput` | False | True | False |
| 요소 양식 JSON (선택) | `component_catalog_json` | `MessageTextInput` | False | False | True |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| 요소 추천 결과 | `html_component_catalog` | `Output` | `build_catalog` |



## 등록

`html_component_catalog_builder.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
