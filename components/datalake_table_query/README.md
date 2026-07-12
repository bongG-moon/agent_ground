# Datalake 테이블 조회

Cluster API와 MySQL 조회 설정을 직접 입력받아 Datalake 결과를 DataFrame으로 반환합니다.

## 상태

- ID: `datalake_table_query`
- 버전: `0.1.0`
- 상태: `user_testing`
- Standalone: `true`
- 사용 범위: `직접 데이터 조회 Component (특정 Flow 미지정)`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| Datalake API 기준 URL | `api_base_url` | `StrInput` | False | True | False |
| HTTP API 사용 허용 | `allow_insecure_api_http` | `BoolInput` | False | False | True |
| Cluster 상태 경로 | `cluster_status_path` | `StrInput` | False | True | False |
| Cluster 유형 | `cluster_type` | `StrInput` | False | True | False |
| MySQL Endpoint 필드 | `jdbc_endpoint_key` | `StrInput` | False | True | True |
| 허용 MySQL Host | `allowed_mysql_hosts` | `MultilineInput` | False | True | False |
| Datalake 사용자 ID | `lake_user_id` | `StrInput` | False | True | False |
| Datalake JWT 토큰 | `lake_jwt_token` | `SecretStrInput` | False | True | False |
| MySQL CA 파일 경로 | `mysql_ssl_ca_path` | `StrInput` | False | True | False |
| 조회 SQL | `sql_query` | `MultilineInput` | False | True | False |
| 바인드 변수 | `bind_parameters` | `DictInput` | False | False | False |
| 최대 조회 행 수 | `max_rows` | `IntInput` | False | False | True |
| API/DB 연결 제한시간(초) | `api_timeout_seconds` | `IntInput` | False | False | True |
| SQL 읽기 제한시간(초) | `query_timeout_seconds` | `IntInput` | False | False | True |
| Cluster 전체 대기시간(초) | `cluster_wait_seconds` | `IntInput` | False | False | True |
| Cluster 확인 간격(초) | `poll_interval_seconds` | `IntInput` | False | False | True |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| 조회 데이터 테이블 | `data_table` | `DataFrame` | `build_data_table` |


## 상세 사용 가이드

[`USAGE_GUIDE.md`](USAGE_GUIDE.md)에서 연결 방법, 운영 조건과 사용자 확인 항목을 확인합니다.


## 등록

`datalake_table_query.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
