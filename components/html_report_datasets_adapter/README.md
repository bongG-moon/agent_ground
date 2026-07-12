# 11 HTML Report Datasets Adapter

06 Data JSON을 HTML 생성 Flow의 datasets JSON 입력으로 변환합니다.

## 상태

- ID: `html_report_datasets_adapter`
- 버전: `0.9.0`
- 상태: `user_testing`
- Standalone: `true`
- 사용 범위: `reusable_data_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| Data JSON | `data_json` | `Data, JSON, Message, Text` | False | False | False |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| HTML Datasets Data | `html_datasets_data` | `Data` | `build_datasets_data` |
| HTML Datasets Text | `html_datasets_text` | `Message` | `build_datasets_text` |



## 등록

`html_report_datasets_adapter.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
