# Datalake 테이블 조회 Component 사용 가이드

## 무엇을 하는 Component인가요?

Datalake Cluster 상태 API에서 실행 중인 StarRocks의 MySQL 접속 주소를 찾고, 읽기 SQL 한 개를 실행한 뒤 조회 결과를 Langflow `DataFrame`으로 반환합니다.

기존 `datalake_data`처럼 `data_request`를 해석하거나 여러 소스 요청을 순회하지 않습니다. 더미 데이터와 자동 패키지 설치도 포함하지 않습니다.

```text
Cluster API 설정 + 계정 + SQL + 바인드 변수
                    ↓
          RUNNING Cluster 주소 확인
                    ↓
             MySQL 방식 조회
                    ↓
             조회 데이터 테이블
```

## 등록 방법

`datalake_table_query.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 다른 Agent Ground Python 파일을 import하지 않는 Standalone 방식입니다.

운영 환경에는 다음 패키지가 미리 설치되어 있어야 합니다.

- `aiohttp`
- `mysql-connector-python`

Component는 실행 도중 `pip install`을 수행하지 않습니다. 패키지가 없으면 외부 시스템을 호출하기 전에 한글 오류로 중단합니다.

## 입력 항목

| 화면 이름 | 코드 이름 | 필수 | 설명 |
| --- | --- | --- | --- |
| Datalake API 기준 URL | `api_base_url` | 예 | Cluster 상태 API의 기준 URL |
| HTTP API 사용 허용 | `allow_insecure_api_http` | 아니요 | 기본 꺼짐. 폐쇄된 테스트망의 HTTP에서만 명시적으로 켬 |
| Cluster 상태 경로 | `cluster_status_path` | 예 | 기준 URL 뒤에 붙는 상대 경로 |
| Cluster 유형 | `cluster_type` | 예 | 기본값 `starrocks` |
| MySQL Endpoint 필드 | `jdbc_endpoint_key` | 예 | `endpoints` 객체에서 주소를 찾을 key |
| 허용 MySQL Host | `allowed_mysql_hosts` | 예 | 정확한 host 또는 `.example.com` suffix 허용목록 |
| Datalake 사용자 ID | `lake_user_id` | 예 | API header와 MySQL 사용자로 사용 |
| Datalake JWT 토큰 | `lake_jwt_token` | 예 | Bearer Token과 MySQL 비밀번호로 사용 |
| MySQL CA 파일 경로 | `mysql_ssl_ca_path` | 예 | 서버 인증서와 hostname 검증용 CA PEM 파일 |
| 조회 SQL | `sql_query` | 예 | `SELECT` 또는 조회용 `WITH` 문 한 개 |
| 바인드 변수 | `bind_parameters` | 아니요 | MySQL native bind에 전달할 JSON 객체 |
| 최대 조회 행 수 | `max_rows` | 아니요 | 기본 5,000행, 최대 100,000행 |
| API/DB 연결 제한시간 | `api_timeout_seconds` | 아니요 | API 한 번의 요청과 MySQL 연결 timeout |
| SQL 읽기 제한시간 | `query_timeout_seconds` | 아니요 | SQL 실행과 결과 읽기 timeout. 기본 60초 |
| Cluster 전체 대기시간 | `cluster_wait_seconds` | 아니요 | RUNNING까지 기다리는 전체 시간 |
| Cluster 확인 간격 | `poll_interval_seconds` | 아니요 | 상태 재확인 간격 |

기본 경로는 다음과 같습니다.

```text
API 기준 URL: https://api-server.lake.skhynix.com/api/v4/
Cluster 상태 경로: runtime/cluster/{cluster_type}/running
Cluster 유형: starrocks
MySQL Endpoint 필드: jdbc-external
```

기본 API 주소는 HTTPS이며 `HTTP API 사용 허용`의 기본값은 꺼짐입니다. 기존 사내 endpoint가 HTTP만 지원한다면 폐쇄된 테스트망인지 확인하고 사용자가 위험을 인지한 뒤 URL과 토글을 함께 바꿉니다.

`허용 MySQL Host` 예시:

```text
.skhynix.com
starrocks.internal.example.com
```

`.example.com`은 해당 도메인과 하위 host를 허용합니다. IP 주소는 suffix로 허용할 수 없고 정확한 IP만 입력해야 합니다. Cluster API가 목록 밖 주소를 반환하면 JWT를 MySQL 비밀번호로 보내기 전에 실행을 중단합니다.

`{cluster_type}`에는 화면에서 입력한 Cluster 유형이 들어갑니다. 상태 경로는 외부 전체 URL이 아닌 상대 경로만 허용합니다.

## Cluster API 응답 계약

Component는 다음과 같은 응답을 기대합니다.

```json
{
  "status": "RUNNING",
  "endpoints": {
    "jdbc-external": "starrocks.example.com:9030"
  }
}
```

MySQL 주소는 다음 형식을 지원합니다.

- `host:port`
- `mysql://host:port/database`
- `jdbc:mysql://host:port/database`

URL에 계정이나 비밀번호가 포함되어 있으면 실행을 거부합니다. 인증 정보는 Component 입력값만 사용합니다.

## SQL과 바인드 변수 예시

조회 SQL:

```sql
SELECT
    WORK_MONTH,
    PRODUCT_FAMILY,
    SUM(INPUT_QTY) AS INPUT_QTY,
    SUM(GOOD_QTY) AS GOOD_QTY
FROM LAKEHOUSE_YIELD_TABLE
WHERE WORK_MONTH BETWEEN %(from_ym)s AND %(to_ym)s
  AND PRODUCT_FAMILY = %(product_family)s
GROUP BY WORK_MONTH, PRODUCT_FAMILY
```

바인드 변수:

```json
{
  "from_ym": "202601",
  "to_ym": "202606",
  "product_family": "DDR5"
}
```

Datalake의 MySQL native named bind는 `%(name)s` 형식입니다. Oracle의 `:NAME` 형식과 다르므로 그대로 섞어 쓰면 안 됩니다.

## 출력 계약

출력은 `data_table` 포트의 `DataFrame` 한 개뿐입니다.

`success`, `items`, `source_type`, `executed_query`, `error_message` 같은 Flow용 metadata를 출력 데이터에 섞지 않습니다. 정상적으로 조회 결과가 0행이면 컬럼 구조가 있는 빈 DataFrame을 반환합니다.

## 읽기 SQL 안전장치

- `SELECT` 또는 `WITH`로 시작하는 문장 한 개만 허용
- 여러 SQL 문 실행 차단
- 변경·DDL·권한 명령 차단
- `SELECT ... INTO OUTFILE/DUMPFILE`과 MySQL 실행형 주석 차단
- native bind 변수 사용
- cursor의 `fetchmany(max_rows + 1)`로 실제 읽는 행 수 제한
- `allow_local_infile=False`
- MySQL `read_timeout`·`write_timeout` 적용
- `ssl_ca`, `ssl_verify_cert=True`, `ssl_verify_identity=True`로 TLS와 서버 신원 검증

문자열 기반 검사는 1차 안전장치입니다. 실제 Datalake 계정에도 조회 권한만 부여해야 합니다.

## API와 인증 보안

- JWT 토큰은 `SecretStrInput`으로만 받습니다.
- API redirect를 따라가지 않아 다른 host로 Bearer Token이 전달되는 것을 막습니다.
- HTTP API는 기본 차단하며 폐쇄된 테스트망에서만 명시적 opt-in을 허용합니다.
- API 기준 URL에는 계정, query string, fragment를 넣을 수 없습니다.
- Cluster 상태 경로에는 전체 외부 URL이나 `..` 상위 경로 이동을 넣을 수 없습니다.
- Cluster API가 반환한 MySQL host를 사용자 허용목록과 대조합니다.
- CA 파일이 없거나 서버 인증서·hostname 검증에 실패하면 DB 연결을 중단합니다.
- Cluster API 응답은 최대 1MiB까지만 읽고, 전체 대기시간은 실제 monotonic deadline으로 제한합니다.
- 토큰, 사용자 ID, API 주소, MySQL host, SQL 전문을 Component 상태와 외부 오류 원문에 표시하지 않습니다.

## 실패가 발생하는 경우

- 필수 접속 정보가 비어 있음
- 허용되지 않은 API URL 또는 상태 경로
- HTTP 주소에 대한 명시적 허용이 없음
- Cluster API가 허용목록 밖 MySQL host를 반환함
- MySQL CA 파일이 없거나 인증서·hostname 검증에 실패함
- API 인증 실패 또는 잘못된 JSON 응답
- Cluster가 제한시간 내 RUNNING 상태가 되지 않음
- `jdbc-external` 주소가 없거나 형식이 잘못됨
- `mysql-connector-python` 또는 `aiohttp`가 설치되지 않음
- MySQL 접속, 인증, SQL 실행에 실패함
- 변경 SQL 또는 여러 SQL 문을 입력함

실패를 빈 테이블로 숨기지 않습니다. 원인을 수정할 수 있도록 한글 오류로 Component 실행을 중단합니다.
