# Data Result Merger

Oracle, H-API, Datalake, Goodocs 조회 결과를 하나의 data_result로 병합합니다.

## 상태

- ID: `data_result_merger`
- 버전: `0.9.0`
- 상태: `user_testing`
- Standalone: `true`
- 사용 범위: `reusable_data_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| Oracle Result | `oracle_result` | `Data, JSON` | False | False | False |
| H-API Result | `h_api_result` | `Data, JSON` | False | False | False |
| Datalake Result | `datalake_result` | `Data, JSON` | False | False | False |
| Goodocs Result | `goodocs_result` | `Data, JSON` | False | False | False |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| Data Result | `data_result` | `Data` | `build_data_result` |



## 등록

`data_result_merger.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
