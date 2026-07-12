# Data Request Normalizer

LLM 응답을 data_request로 정규화하고 source_catalog의 실행 설정을 채웁니다.

## 상태

- ID: `data_request_normalizer`
- 버전: `0.9.0`
- 상태: `user_testing`
- Standalone: `true`
- 사용 범위: `reusable_data_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| LLM Result | `llm_result` | `Message, Data, Text, JSON` | False | False | False |
| Data Catalog | `source_catalog` | `MultilineInput` | False | False | False |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| Data Request | `data_request` | `Data` | `build_data_request` |



## 등록

`data_request_normalizer.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
