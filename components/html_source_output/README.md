# 05-1 HTML 원문 출력

Report API 없이 04에서 생성된 전체 HTML 코드를 Playground에 그대로 출력합니다.

## 상태

- ID: `html_source_output`
- 버전: `0.9.0`
- 상태: `user_testing`
- Standalone: `true`
- 사용 범위: `html_report_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| HTML 생성 결과 | `payload` | `DataInput` | False | True | False |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| HTML 원문 | `html_message` | `Output` | `build_message` |



## 등록

`html_source_output.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
