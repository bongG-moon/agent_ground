# 직접 데이터 조회 Standalone Component 가이드

> 기준 환경: Langflow `1.8.2`, LFX `0.3.4`  
> 상태: `user_testing`  
> 구현 원칙: 접속·요청 정보를 직접 입력하고 정상 결과는 `DataFrame` 하나만 출력

## 1. 왜 새 Component를 분리했나

기존 `reusable_data_flow`의 다음 Component는 자연어 요청을 여러 소스로 유연하게 연결하기 위한 Flow 전용 실행 노드입니다.

- `oracle_data`
- `h_api_data`
- `datalake_data`
- `goodocs_data`

기존 노드는 단순 조회 외에도 다음 일을 함께 처리합니다.

- `data_request` 구조 해석
- 여러 요청 중 자신의 `source_type` 선택
- Source Catalog의 설정 병합
- 여러 요청 순회
- Merger가 읽을 성공·실패 envelope 생성
- 기존 배선 확인용 dummy row

새 Component는 이 유연한 계층을 제거하고 외부 소스 한 곳을 한 번 조회하는 최소 단위로 분리했습니다.

```text
기존 방식
자연어 → LLM → Catalog → Normalizer → Source 분기 → 공통 결과 포장

신규 방식
접속정보·URL·SQL·파라미터 직접 입력 → 외부 소스 한 번 호출 → DataFrame
```

## 2. 기존 Component를 덮어쓰지 않은 이유

기존 네 Component는 `reusable_data_flow`의 Flow JSON과 `component_refs.json`이 버전 `0.9.0`으로 참조합니다. 입력과 출력을 제자리에서 바꾸면 기존 Flow를 다시 가져왔을 때 연결 계약이 달라집니다.

따라서 기존 파일은 그대로 보존하고 다음 새 ID로 추가했습니다.

| Component ID | 역할 |
| --- | --- |
| [`oracle_table_query`](oracle_table_query/USAGE_GUIDE.md) | Oracle SQL 한 번 실행 |
| [`h_api_table_request`](h_api_table_request/USAGE_GUIDE.md) | H-API POST 한 번 실행 |
| [`datalake_table_query`](datalake_table_query/USAGE_GUIDE.md) | Datalake Cluster 확인 후 SQL 한 번 실행 |
| [`goodocs_table_reader`](goodocs_table_reader/USAGE_GUIDE.md) | GooDocs 문서 한 개 읽기 |
| [`simple_api_table_request`](simple_api_table_request/USAGE_GUIDE.md) | 일반 JSON API GET·POST 한 번 실행 |

이번 구현은 기존 `reusable_data_flow.json`에 이 Component들을 자동 교체하지 않습니다. 나중에 사용자가 새 직접 조회 구조가 완성됐다고 확인하면 별도의 간소화 Flow를 구성할 수 있습니다.

## 3. 공통 출력 계약

다섯 Component의 정상 출력은 모두 다음 하나입니다.

```text
data_table / DataFrame
```

출력 DataFrame에는 다음 정보를 섞지 않습니다.

- `success`
- `error_message`
- `source_type`
- 실행 SQL 또는 URL
- 사용자 ID·토큰·비밀번호
- 요청 metadata

정상적으로 조회 결과가 0건이면 빈 `DataFrame`을 반환합니다. 접속·인증·SQL·응답 형식이 실패한 경우에는 빈 테이블을 성공처럼 반환하지 않고 한글 오류로 실행을 중단합니다.

외부 조회 결과를 이전 실행에서 재사용하지 않도록 Output cache를 끕니다. 이 Component들은 기본적으로 Agent Tool이 아니라 Flow 내부의 직접 실행 노드로 사용합니다.

## 4. Oracle 테이블 조회

### 입력

| 화면 이름 | 설명 |
| --- | --- |
| Oracle DSN/TNS | 한 개 DB의 DSN, TNS 별칭 또는 전체 TNS 문자열 |
| 사용자 ID | 일반 인증 계정. Wallet·외부 인증이면 비울 수 있음 |
| 비밀번호 | 사용자 ID와 함께 사용하는 Secret 입력 |
| 조회 SQL | `SELECT` 또는 조회용 `WITH` 문 한 개 |
| 바인드 변수 | Oracle native bind에 전달할 JSON 객체 |
| 최대 조회 행 수 | 기본 5,000행 |
| SQL 제한시간 | 기본 60초. Oracle 각 DB 왕복 호출에 적용 |

예시 SQL:

```sql
SELECT WORK_DT, FACTORY, PRODUCTION
FROM PRODUCTION_TABLE
WHERE WORK_DT = :DATE
  AND FACTORY = :FACTORY
```

바인드 변수:

```json
{
  "DATE": "20260712",
  "FACTORY": "FAB1"
}
```

`{DATE}` 문자열 치환을 사용하지 않습니다. SQL에는 `:DATE`, 바인드 변수 객체에는 `DATE`를 입력하고 `oracledb` driver가 값을 별도로 전달하게 합니다.

### 운영 조건

- Langflow 실행 환경에 `oracledb`가 설치되어 있어야 합니다.
- 실제 계정은 조회 권한만 가져야 합니다.
- SQL 읽기 검사만으로 DB 권한을 대신할 수 없습니다.
- 각 셀의 문자열·BLOB·CLOB는 최대 5MiB까지만 읽습니다.

## 5. H-API 테이블 조회

### 입력

| 화면 이름 | 설명 |
| --- | --- |
| H-API URL | 호출할 전체 HTTP(S) 주소 |
| HTTP API 사용 허용 | 기본 꺼짐. 폐쇄된 테스트망의 HTTP에서만 켬 |
| H-API Token | `h-api-token` 헤더에 넣는 Secret 값 |
| Bind Parameters JSON | H-API 순서에 맞춘 JSON 배열 |
| 응답 데이터 경로 | 기본 `data.row` |
| 제한 시간 | 요청 timeout |
| 최대 반환 행 수 | 기본 5,000행 |

Bind Parameters 예시:

```json
["A12345", "D/A1", "B/G1"]
```

실제 요청 body:

```json
{
  "bindParams": ["A12345", "D/A1", "B/G1"]
}
```

응답 경로가 틀리면 전체 응답에서 다른 배열을 임의로 찾지 않고 오류로 중단합니다. 리다이렉트도 자동으로 따라가지 않습니다.

## 6. Datalake 테이블 조회

기존 환경과 같은 SmallData 경계를 사용합니다.

```text
Datalake Cluster API
→ RUNNING 상태와 jdbc-external endpoint 확인
→ MySQL protocol로 StarRocks 연결
→ SQL 실행
→ DataFrame
```

### 주요 입력

| 화면 이름 | 설명 |
| --- | --- |
| API 기준 URL | Datalake Cluster API의 기준 주소 |
| HTTP API 사용 허용 | 기본 꺼짐. 폐쇄된 테스트망의 HTTP에서만 켬 |
| Cluster 상태 경로 | `{cluster_type}`을 포함할 수 있는 상태 조회 경로 |
| Cluster 유형 | 기본 `starrocks` |
| JDBC endpoint key | 기본 `jdbc-external` |
| 허용 MySQL Host | 정확한 host 또는 `.example.com` suffix 목록 |
| 사용자 ID | API header와 DB 사용자로 사용 |
| JWT Token | Bearer token과 DB 비밀번호로 사용하는 Secret 값 |
| MySQL CA 파일 경로 | 서버 인증서와 hostname 검증용 CA PEM 파일 |
| 조회 SQL | `SELECT` 또는 조회용 `WITH` 문 |
| 바인드 변수 | MySQL native bind 객체 |
| 최대 조회 행 수 | 기본 5,000행 |
| API/SQL timeout·전체 대기시간·간격 | 고급 운영 제한 |

MySQL 계열 바인드 예시:

```sql
SELECT WORK_MONTH, PRODUCT_FAMILY, YIELD_RATE
FROM LAKEHOUSE_YIELD_TABLE
WHERE WORK_MONTH BETWEEN %(from_ym)s AND %(to_ym)s
```

```json
{
  "from_ym": "202601",
  "to_ym": "202606"
}
```

### 운영 조건

- Langflow 실행 환경에 `mysql-connector-python`이 설치되어 있어야 합니다.
- Component는 실행 중 자동으로 패키지를 설치하지 않습니다.
- 현재 로컬 Langflow 환경에는 해당 패키지가 없어 실제 사내 Datalake 연결 검증은 남아 있습니다.
- Cluster API URL은 운영 승인된 사내 주소로 고정하고 서버 egress 정책을 적용합니다.
- API가 반환한 MySQL host가 허용목록 밖이면 토큰을 보내기 전에 차단합니다.
- MySQL CA 파일과 인증서·hostname 검증은 필수이며 검증되지 않은 TLS fallback은 허용하지 않습니다.
- `INTO OUTFILE/DUMPFILE`, MySQL 실행형 주석과 변경 SQL을 차단합니다.

## 7. GooDocs 테이블 조회기

### 입력

| 화면 이름 | 설명 |
| --- | --- |
| 문서 ID | 조회할 GooDocs 문서 식별자 |
| 사용자 ID | GooDocs 인증 사용자 |
| 토큰 소스 | Secret 입력 |
| 토큰 키 | Secret 입력 |
| 시트 이름 | 선택 입력. 있으면 `SHEET_NAME`으로 전달 |
| 최대 출력 행 수 | `read_all()` 후 출력 행 제한 |

### 실제 모듈 교체

`goodocs_table_reader.py` 상단의 `실제 GooDocs 모듈 교체 구역`에 사내 구현을 넣습니다.

최소 호출 계약:

```python
client = Goodocs(auth)
result = client.read_all()
```

반환값은 pandas `DataFrame` 또는 `list[dict]`여야 합니다. 실제 모듈이 없을 때 더미 테이블을 반환하지 않으며 교체 위치를 알려주는 오류가 발생합니다.

파일 안에 실제 클래스를 포함하면 Standalone 방식입니다. 별도 모듈을 import하면 해당 모듈이 Langflow Python 환경에도 설치되어 있어야 합니다.

## 8. 단순 API 테이블 조회

### 입력

| 화면 이름 | 설명 |
| --- | --- |
| API URL | 전체 HTTP(S) 주소 |
| HTTP API 사용 허용 | 기본 꺼짐. 폐쇄된 테스트망의 HTTP에서만 켬 |
| HTTP 방식 | 조회 범위의 `GET` 또는 `POST` |
| 요청 헤더 JSON | Authorization 등을 넣는 Secret JSON |
| Query Parameters JSON | URL query parameter 객체 |
| Request Body JSON | POST 요청 body |
| 응답 데이터 경로 | 표로 바꿀 JSON 위치. 빈 값이면 전체 응답 |
| 제한 시간 | 요청 timeout |
| 최대 응답 크기 | 메모리 보호 byte 제한 |
| 최대 반환 행 수 | 기본 5,000행 |

응답은 JSON만 허용합니다. JSON 배열은 행 목록, JSON 객체는 한 행, scalar는 `value` 열 한 행으로 변환합니다. 중첩 객체와 배열은 한 셀에서 확인할 수 있도록 compact JSON 문자열로 바꿉니다.

### 보안 경계

- URL에 사용자명·비밀번호를 포함할 수 없습니다.
- 위험한 `Host`, `Content-Length`, proxy 인증 헤더는 직접 지정할 수 없습니다.
- 다른 scheme·host·port의 origin으로 이동하는 리다이렉트를 차단합니다.
- HTTPS에서 HTTP로 낮아지는 이동을 차단합니다.
- URL을 LLM이 자유롭게 생성하게 하지 않습니다.
- 운영에서는 승인 host allowlist와 서버 egress 정책을 함께 사용합니다.

## 9. 연결 예시

### 직접 확인

```text
Oracle 테이블 조회.data_table
→ Chat Output.input_value
```

Langflow Chat Output은 `DataFrame`을 표 형태 메시지로 변환할 수 있습니다.

### 후속 표 처리

```text
소스별 직접 조회.data_table
→ DataFrame Operations
→ Chart / HTML Report 입력 변환
```

다섯 Component를 동시에 연결해 자동 병합하려면 별도의 DataFrame 병합 규칙이 필요합니다. 신규 Component는 의도적으로 여러 소스 결과를 한 객체로 합치지 않습니다.

## 10. 사용자 확인 항목

- [ ] 다섯 Python 파일이 각각 Standalone Component로 등록된다.
- [ ] 모든 Component의 Output이 `DataFrame` 하나뿐이다.
- [ ] 정상 0건과 실행 실패가 구분된다.
- [ ] 더미 데이터나 실패 fallback이 없다.
- [ ] 토큰·비밀번호가 status, DataFrame과 오류에 노출되지 않는다.
- [ ] Oracle native bind가 driver에 별도 전달된다.
- [ ] H-API bindParams 순서와 response path가 실제 계약과 맞는다.
- [ ] Datalake Cluster endpoint와 MySQL 연결을 사내 환경에서 확인한다.
- [ ] 실제 GooDocs 모듈을 교체하고 한 문서를 조회한다.
- [ ] 일반 API의 승인 host와 응답 크기 정책을 확인한다.

사용자 실제 환경 검증과 완료 승인 전까지 상태는 `user_testing`입니다.
