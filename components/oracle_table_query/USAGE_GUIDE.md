# Oracle 테이블 조회 Component 사용 가이드

## 무엇을 하는 Component인가요?

Oracle DB에 한 번 접속해 읽기 SQL 한 개를 실행하고, 조회된 행과 컬럼을 Langflow `DataFrame`으로 반환합니다.

기존 `oracle_data`처럼 `data_request`를 해석하거나 `source_type`을 고르지 않습니다. 여러 요청을 순회하거나 결과를 `items`, `data_result`, `success` 같은 구조로 감싸지도 않습니다.

```text
DSN/TNS + 계정 + SQL + 바인드 변수
                ↓
          Oracle 단일 조회
                ↓
          조회 데이터 테이블
```

## 등록 방법

`oracle_table_query.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 다른 Agent Ground Python 파일을 import하지 않는 Standalone 방식입니다.

운영 환경에는 `oracledb` 패키지가 미리 설치되어 있어야 합니다. Component가 실행 중에 `pip install`을 수행하지는 않습니다.

## 입력 항목

| 화면 이름 | 코드 이름 | 필수 | 설명 |
| --- | --- | --- | --- |
| Oracle DSN/TNS | `dsn` | 예 | DSN, TNS 별칭 또는 전체 TNS 문자열 한 개 |
| 사용자 ID | `username` | 조건부 | 일반 계정 인증일 때 입력 |
| 비밀번호 | `password` | 조건부 | 사용자 ID와 함께 입력하는 보호 필드 |
| 조회 SQL | `sql_query` | 예 | `SELECT` 또는 조회용 `WITH` 문 한 개 |
| 바인드 변수 | `bind_parameters` | 아니요 | Oracle native bind에 전달할 JSON 객체 |
| 최대 조회 행 수 | `max_rows` | 아니요 | 기본 5,000행, 최대 100,000행 |
| SQL 제한시간(초) | `query_timeout_seconds` | 아니요 | 각 DB 왕복 호출 제한시간. 기본 60초, 최대 3,600초 |

사용자 ID와 비밀번호는 둘 다 입력하거나 둘 다 비워야 합니다. 둘 다 비우면 `oracledb.connect(dsn=...)` 형태로 연결하므로, Oracle Wallet이나 외부 인증이 설정된 환경에서 사용할 수 있습니다.

## 실행 예시

조회 SQL:

```sql
SELECT
    WORK_DT,
    FACTORY,
    OPER_NAME,
    SUM(PRODUCTION) AS PRODUCTION
FROM PRODUCTION_TABLE
WHERE WORK_DT = :DATE
  AND FACTORY = :FACTORY
GROUP BY WORK_DT, FACTORY, OPER_NAME
```

바인드 변수:

```json
{
  "DATE": "20260712",
  "FACTORY": "FAB1"
}
```

SQL 안에는 `:DATE`처럼 콜론을 붙이고, 바인드 변수 객체의 키에는 `DATE`처럼 콜론을 붙이지 않습니다. 값은 문자열로 SQL에 끼워 넣지 않고 Oracle driver에 별도 전달됩니다.

## 출력 계약

출력은 `data_table`이라는 포트의 `DataFrame` 한 개뿐입니다.

```text
DataFrame
├─ WORK_DT
├─ FACTORY
├─ OPER_NAME
└─ PRODUCTION
```

정상 조회 결과가 0행이어도 cursor가 제공한 컬럼 구조는 유지합니다. 오류를 빈 행이나 오류용 컬럼으로 섞지 않고 Component 실행 오류로 표시합니다.

## 읽기 SQL 안전장치

Component는 다음 조건을 검사합니다.

- `SELECT` 또는 `WITH`로 시작하는 문장 한 개만 허용
- 여러 SQL 문을 함께 실행하는 세미콜론 차단
- `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `CREATE`, `DROP`, `ALTER`, `TRUNCATE`, `CALL` 등 변경 명령 차단
- 최대 조회 행 수 제한
- Oracle 각 DB 왕복 호출에 `call_timeout` 적용

이 검사는 잘못된 SQL 실행을 줄이는 보조 장치입니다. 완전한 SQL parser나 권한 시스템을 대신하지 않으므로 실제 접속 계정에는 반드시 조회 권한만 부여해야 합니다.

## 데이터 변환

- 날짜와 시간: ISO 형식 문자열
- `Decimal`: 숫자
- `bytes`, BLOB: 최대 5MiB까지 Base64 문자열
- CLOB: 최대 5MiB까지 실제 문자열 값
- 동일한 이름의 컬럼: 두 번째 컬럼부터 `_2`, `_3` 접미사 추가

## 보안 주의사항

- 비밀번호를 일반 Text Input이나 SQL에 넣지 마세요.
- DSN, 계정, 비밀번호, SQL 전문은 Component 상태에 표시하지 않습니다.
- SQL의 값은 문자열 조합 대신 바인드 변수를 사용하세요.
- 테이블명과 컬럼명은 바인드할 수 없으므로 운영자가 검토한 SQL을 사용하세요.
- 외부 인증을 쓸 때는 Agent Builder 프로세스에 Wallet과 Oracle 환경 설정이 먼저 적용되어 있어야 합니다.

## 실패가 발생하는 경우

- DSN/TNS가 비어 있음
- 사용자 ID와 비밀번호 중 하나만 입력함
- 변경 SQL 또는 여러 SQL 문을 입력함
- 바인드 변수 형식이 올바르지 않음
- `oracledb`가 설치되지 않음
- 접속, 인증, SQL 실행에 실패함
- SQL 호출이 설정한 제한시간을 초과함
- 한 셀의 문자열·BLOB·CLOB가 5MiB를 초과함

실패 시 조회 결과처럼 보이는 빈 테이블을 반환하지 않습니다. 원인을 수정할 수 있도록 한글 오류로 실행을 중단합니다.
