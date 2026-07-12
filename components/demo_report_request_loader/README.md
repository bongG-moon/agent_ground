# 00 리포트 요청/데이터 불러오기

질문, 표시 요청, 직접 입력 데이터 또는 File Read 결과를 HTML 리포트용 요청으로 정리합니다.

## 상태

- ID: `demo_report_request_loader`
- 버전: `0.9.0`
- 상태: `user_testing`
- Standalone: `true`
- 사용 범위: `html_report_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| 질문 | `question` | `MessageTextInput` | False | True | False |
| 보고 싶은 방식 | `view_request` | `MessageTextInput` | False | False | False |
| 데이터 직접 입력 | `data_text` | `MessageTextInput` | False | False | False |
| 파일 데이터 | `file_data` | `Data, DataFrame, Message, Text, File, JSON, StructuredContent, Structured Content` | False | False | False |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| 요청 데이터 | `payload` | `Output` | `build_payload` |



## 등록

`demo_report_request_loader.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
