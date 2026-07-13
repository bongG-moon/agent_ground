# H-API 테이블 조회 사용 가이드

## 이 Component가 하는 일

`H-API 테이블 조회`는 H-API 한 곳에 요청을 한 번 보내고, 응답 JSON에서 지정한 데이터만 꺼내 Langflow `DataFrame`으로 반환하는 최소 단위 Component입니다.

기존 `reusable_data_flow`의 `H-API Data`처럼 전체 `data_request`를 해석하거나, Catalog에서 설정을 찾거나, 여러 source를 골라 실행하지 않습니다. 필요한 값을 Component 입력에 직접 넣습니다.

```text
URL + Token + bindParams
        ↓
H-API POST 1회
        ↓
response_path 값 선택
        ↓
데이터 테이블(DataFrame) 1개
```

기존 `h_api_data`는 재사용 데이터 Flow에 종속된 내부 노드이므로 `flows/reusable_data_flow/nodes/h_api_data.py`에 보존됩니다. 새 기능 단위 Component의 ID와 class 이름은 각각 `h_api_table_request`, `HApiTableRequest`입니다.

## 입력 항목

| 화면 이름 | 코드 이름 | 필수 | 설명 |
| --- | --- | --- | --- |
| H-API 주소 | `api_url` | 예 | 호출할 H-API의 전체 `http` 또는 `https` 주소 |
| HTTP API 사용 허용 | `allow_insecure_http` | 아니요 | 기본 꺼짐. 폐쇄된 테스트망의 HTTP에서만 명시적으로 켬 |
| H-API 인증 토큰 | `h_api_token` | 예 | `h-api-token` 요청 헤더에 들어갈 인증값 |
| 요청 파라미터 JSON | `bind_params_json` | 예 | H-API가 요구하는 순서대로 값을 넣은 JSON 배열 |
| 응답 데이터 경로 | `response_path` | 아니요 | 표로 바꿀 JSON 값의 점 경로. 기본값은 `data.row` |
| 제한 시간(초) | `timeout_seconds` | 아니요 | 연결과 개별 응답 읽기에 적용되는 timeout. 기본값은 30초 |
| 최대 반환 행 수 | `max_rows` | 아니요 | 반환할 최대 행 수. 기본값은 5,000행 |

H-API마다 파라미터 개수가 다르므로 `LOT_ID`, `START_OPER` 같은 입력 칸을 코드에 고정하지 않습니다. 대신 순서가 보존되는 JSON 배열 하나를 사용합니다.

예를 들어 파라미터 순서가 `LOT_ID → START_OPER → END_OPER`라면 다음처럼 입력합니다.

```json
["A12345", "D/A1", "B/G1"]
```

실제 요청은 다음과 같습니다.

```http
POST {api_url}
h-api-token: {h_api_token}
Accept: application/json
Content-Type: application/json
```

```json
{
  "bindParams": ["A12345", "D/A1", "B/G1"]
}
```

## 응답을 표로 만드는 규칙

다음 응답에서 `response_path`를 `data.row`로 입력했다고 가정합니다.

```json
{
  "data": {
    "row": [
      {"LOT_ID": "A12345", "OPER": "D/A1"},
      {"LOT_ID": "A12345", "OPER": "B/G1"}
    ]
  }
}
```

출력 표는 다음과 같습니다.

| LOT_ID | OPER |
| --- | --- |
| A12345 | D/A1 |
| A12345 | B/G1 |

변환 규칙은 단순하고 고정되어 있습니다.

- 선택한 값이 객체 배열이면 객체 한 개가 표의 한 행이 됩니다.
- 선택한 값이 객체 하나면 한 행짜리 표가 됩니다.
- 선택한 값이 문자열이나 숫자 배열이면 `value` 열 하나를 만듭니다.
- 행 안의 중첩 객체와 배열은 한 셀에서 볼 수 있도록 JSON 문자열로 보관합니다.
- `response_path`가 비어 있으면 전체 응답 JSON을 사용합니다.
- 경로가 틀리면 다른 `data`, `rows`, `items` 필드를 임의로 찾지 않고 오류로 중단합니다.

## 출력 계약

출력은 `data_table` 한 개뿐이며 자료형은 Langflow `DataFrame`입니다.

성공 여부, URL, Token, 요청 본문 같은 메타데이터를 출력 행에 섞지 않습니다. 호출 실패도 `error` 열을 가진 가짜 데이터 행으로 반환하지 않고 Component 실행 오류로 표시합니다. 따라서 다음 Component는 성공한 업무 데이터만 받습니다.

외부 데이터가 바뀌었는데 예전 결과가 재사용되는 일을 막기 위해 출력 캐시는 끈 상태(`cache=False`)입니다. 인증 정보와 임의 URL을 Agent Tool 계약으로 노출하지 않도록 Tool 변환도 끈 상태(`tool_mode=False`)입니다.

## 오류가 발생하는 경우

- URL이 비어 있거나 `http`/`https` 형식이 아닐 때
- HTTP URL인데 `HTTP API 사용 허용`을 켜지 않았을 때
- URL 안에 사용자 이름, 비밀번호 또는 fragment가 들어 있을 때
- Token이 비어 있을 때
- 요청 파라미터가 JSON 배열이 아닐 때
- 제한 시간이 1~300초 범위를 벗어날 때
- 응답 상태가 2xx가 아닐 때
- 서버가 리다이렉트를 반환할 때
- 응답이 JSON이 아니거나 10MiB를 넘을 때
- `response_path`를 따라갈 수 없을 때

오류 메시지와 실행 상태에는 Token과 요청 본문을 출력하지 않습니다.

## Builder에 등록하는 방법

1. [h_api_table_request.py](./h_api_table_request.py)의 전체 내용을 복사합니다.
2. Langflow에서 새 Custom Component를 만듭니다.
3. 기본 코드를 모두 지우고 복사한 코드를 붙여 넣습니다.
4. Build 후 `H-API 테이블 조회`라는 이름과 `데이터 테이블` 출력 한 개가 보이는지 확인합니다.
5. 테스트 URL과 Token을 입력하고 실제 응답 행과 열을 확인합니다.

다른 Agent Ground 파일을 import하지 않으므로 Python 파일 하나만으로 등록할 수 있습니다. 실행 환경에는 `lfx`와 `requests`가 있어야 합니다. Langflow 1.8.2 Desktop에는 두 패키지가 포함되어 있습니다.

## 테스트용 전송 객체 주입

실제 서버를 호출하지 않는 단위 테스트에서는 `request_h_api_table(..., transport=fake_transport)`처럼 가짜 전송 객체를 전달할 수 있습니다. Builder 화면에는 이 입력이 노출되지 않으며 실제 실행에서는 `requests`를 사용합니다.

가짜 전송 객체는 `request(method, url, **options)` 함수를 제공하면 됩니다. 이를 통해 다음 내용을 검증할 수 있습니다.

- `POST` 방식과 실제 URL
- `h-api-token` 헤더
- `{"bindParams": [...]}` 요청 본문
- timeout, SSL 확인, 자동 리다이렉트 차단 옵션
- 응답 경로와 행 제한

## 실제 환경 확인 항목

현재 저장소에는 실제 H-API 주소와 Token이 없으므로 운영 서버 호출까지 자동 검증할 수는 없습니다. 사용자 테스트 단계에서 다음 내용을 확인해야 합니다.

- Token 헤더 이름이 실제로 `h-api-token`인지
- bindParams의 값 순서가 API 명세와 같은지
- 응답 경로가 실제 JSON 구조와 같은지
- 사내 인증서가 표준 신뢰 저장소에서 검증되는지
- 운영 주소가 HTTPS인지, 불가피한 HTTP라면 폐쇄망·방화벽 정책이 적용됐는지
- 5,000행과 10MiB 제한이 실제 조회량에 맞는지
