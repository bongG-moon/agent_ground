# Data Output Builder

조회 결과를 API용 Data JSON과 Chat Output 확인용 표 메시지로 반환합니다.

## 상태

- ID: `data_output_builder`
- 버전: `0.9.0`
- 상태: `user_testing`
- Standalone: `true`
- 사용 범위: `reusable_data_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| Data Result | `data_result` | `Data, JSON` | False | False | False |
| Max Message Rows | `max_message_rows` | `MessageTextInput` | False | False | True |
| Max Cell Chars | `max_cell_chars` | `MessageTextInput` | False | False | True |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| Data JSON | `data_json` | `Data` | `build_data_json` |
| Test Message | `test_message` | `Message` | `build_test_message` |



## 등록

`data_output_builder.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
