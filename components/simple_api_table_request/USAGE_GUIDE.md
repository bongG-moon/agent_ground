# 단순 API 테이블 조회 사용 가이드

## 이 Component가 하는 일

`단순 API 테이블 조회`는 일반 HTTP API를 한 번 호출하고 JSON 응답에서 필요한 값만 꺼내 Langflow `DataFrame`으로 반환합니다.

API를 선택하는 Router, Catalog 조회, 데이터 병합, 재시도, 페이지네이션 같은 기능은 포함하지 않습니다. 호출에 필요한 값을 입력에 직접 넣고 성공한 데이터 표만 받는 최소 단위 Component입니다.

```text
URL / 방식 / 헤더 / Query / Body
                ↓
          HTTP 요청 1회
                ↓
       response_path 값 선택
                ↓
       데이터 테이블(DataFrame)
```

Component ID와 class 이름은 각각 `simple_api_table_request`, `SimpleApiTableRequest`입니다.

## 입력 항목

| 화면 이름 | 코드 이름 | 기본값 | 설명 |
| --- | --- | --- | --- |
| API 주소 | `api_url` | 없음 | 호출할 전체 `http` 또는 `https` 주소 |
| HTTP API 사용 허용 | `allow_insecure_http` | `false` | 폐쇄된 테스트망의 HTTP에서만 명시적으로 켜는 고급 설정 |
| HTTP 방식 | `http_method` | `GET` | 조회 목적의 `GET` 또는 `POST` 중 하나 |
| 요청 헤더 JSON | `headers_json` | `{}` | 인증값을 포함할 수 있어 비밀 입력으로 처리되는 헤더 객체 |
| URL 쿼리 파라미터 JSON | `query_params_json` | `{}` | URL query parameter 객체 |
| 요청 본문 JSON | `body_json` | 빈 값 | POST 요청에 보낼 JSON |
| 응답 데이터 경로 | `response_path` | 빈 값 | 표로 바꿀 JSON 값의 점 경로. 빈 값은 전체 응답 |
| 제한 시간(초) | `timeout_seconds` | `30` | 연결과 개별 응답 읽기 작업 timeout. 전체 실행시간 제한은 아님 |
| 최대 응답 크기(바이트) | `max_response_bytes` | `10485760` | 응답 메모리 보호 한도. 기본값은 10MiB |
| 최대 반환 행 수 | `max_rows` | `5000` | 반환할 최대 데이터 행 수 |

## GET 요청 예시

```text
API 주소
https://api.example.com/v1/orders

HTTP 방식
GET

요청 헤더 JSON
{"Authorization":"Bearer 실제_토큰"}

URL 쿼리 파라미터 JSON
{"factory":"FAB1","date":"20260712"}

요청 본문 JSON
(비워 둠)

응답 데이터 경로
data.items
```

`headers_json`은 화면에서 값이 그대로 드러나지 않는 `SecretStrInput`입니다. 인증 토큰을 URL이나 URL 쿼리 파라미터에 넣지 않는 것을 권장합니다.

## POST 요청 예시

```text
API 주소
https://api.example.com/v1/search

HTTP 방식
POST

요청 헤더 JSON
{"Authorization":"Bearer 실제_토큰","X-Client-Id":"agent-ground"}

URL 쿼리 파라미터 JSON
{}

요청 본문 JSON
{"keyword":"DDR5","limit":100}

응답 데이터 경로
results
```

요청 본문은 `requests`의 `json=` 인자로 전달됩니다. GET에 본문을 입력하면 모호한 동작을 피하기 위해 실행을 중단하고 URL 쿼리 파라미터를 사용하라고 안내합니다.

## 응답을 표로 만드는 규칙

- `response_path`는 `data.items`, `result.rows`처럼 점으로 구분합니다.
- 배열 위치가 필요하면 `data.groups.0.rows`처럼 숫자를 사용합니다.
- 선택한 값이 객체 배열이면 객체 한 개가 표의 한 행이 됩니다.
- 객체 하나는 한 행짜리 표가 됩니다.
- 문자열이나 숫자 배열은 `value` 열 하나가 됩니다.
- 행 안의 중첩 객체와 배열은 JSON 문자열인 한 셀로 보관합니다.
- 경로가 비어 있으면 전체 JSON을 사용합니다.
- 경로가 틀리면 `data`, `rows`, `items` 같은 다른 필드를 추측하지 않고 오류로 중단합니다.
- 빈 2xx 응답은 빈 DataFrame이 됩니다. 단, 비어 있지 않은 `response_path`를 지정했다면 경로 오류가 발생합니다.

## 출력 계약

출력은 `data_table` 한 개뿐이며 자료형은 Langflow `DataFrame`입니다.

HTTP 상태, URL, 헤더, 원본 JSON 같은 메타데이터를 업무 데이터 행에 추가하지 않습니다. 오류도 데이터 행으로 반환하지 않으므로 다음 Component는 성공한 표만 받습니다.

외부 데이터가 바뀌었는데 예전 결과가 재사용되는 일을 막기 위해 출력 캐시는 끈 상태(`cache=False`)입니다. 인증 헤더와 임의 URL을 Agent Tool 계약으로 노출하지 않도록 Tool 변환도 끈 상태(`tool_mode=False`)입니다.

## 기본 안전 경계

- URL은 `http` 또는 `https`만 허용하며 HTTP는 기본 차단합니다.
- URL 안의 사용자 이름·비밀번호와 `#fragment`를 거부합니다.
- `Host`, `Content-Length`, `Transfer-Encoding`, `Connection`, `Proxy-Authorization` 헤더를 직접 지정할 수 없습니다.
- 헤더 이름과 줄바꿈 문자를 검증해 헤더 삽입을 막습니다.
- SSL 인증서 검증은 항상 켜져 있습니다.
- 자동 리다이렉트를 끄고 응답을 한 단계씩 확인합니다.
- GET 요청만 최대 3회까지 리다이렉트를 따라갑니다.
- 리다이렉트의 scheme·host·유효 port 중 하나라도 최초 origin과 다르면 실행 전에 거부합니다.
- HTTPS에서 HTTP로 낮아지는 리다이렉트를 거부합니다.
- POST 리다이렉트는 중복 호출 위험 때문에 자동 실행하지 않습니다.
- 응답 크기와 행 수를 제한합니다.
- 실행 상태와 오류 메시지에 헤더, Query, Body를 출력하지 않습니다.

이 Component는 사내 API를 호출해야 하므로 사설 IP와 사내 host 자체를 차단하지 않습니다. 따라서 `api_url`을 LLM이 임의로 만들게 하지 말고 Flow 설계자가 검토한 고정 URL을 사용하는 것이 중요합니다. DNS rebinding이나 네트워크 단위 접근 통제까지 Component 하나로 해결할 수는 없으므로 운영 환경의 방화벽과 allowlist도 함께 사용해야 합니다.

## JSON 전용 범위

이 Component는 JSON 응답만 표로 변환합니다.

- `Content-Type`이 존재하면서 JSON이 아니면 오류로 중단합니다.
- 본문이 JSON으로 파싱되지 않아도 오류로 중단합니다.
- CSV, XML, HTML, 이미지, 파일 다운로드는 지원하지 않습니다.
- 재시도와 페이지네이션은 수행하지 않습니다.

이번 최소 Component는 조회 목적의 `GET`과 `POST`만 허용합니다. 외부 상태를 변경할 가능성이 큰 `PUT`, `PATCH`, `DELETE`는 지원하지 않습니다. 재시도를 자동으로 넣지 않은 이유도 POST 요청이 중복 실행될 위험을 피하기 위해서입니다. 페이지가 여러 개인 API는 별도 페이지네이션 Flow 또는 전용 Component로 구성하는 편이 안전합니다.

## Builder에 등록하는 방법

1. [simple_api_table_request.py](./simple_api_table_request.py)의 전체 내용을 복사합니다.
2. Langflow에서 새 Custom Component를 만듭니다.
3. 기본 코드를 모두 지우고 복사한 코드를 붙여 넣습니다.
4. Build 후 `단순 API 테이블 조회`와 `데이터 테이블` 출력 한 개가 보이는지 확인합니다.
5. 공개 테스트 API 또는 사내 테스트 API로 행과 열을 확인합니다.

다른 Agent Ground 파일을 import하지 않으므로 Python 파일 하나만으로 등록할 수 있습니다. 실행 환경에는 `lfx`와 `requests`가 있어야 합니다. Langflow 1.8.2 Desktop에는 두 패키지가 포함되어 있습니다.

## 테스트용 전송 객체 주입

단위 테스트에서는 `request_api_table(..., transport=fake_transport)`처럼 가짜 전송 객체를 전달할 수 있습니다. Builder 화면에는 이 인자가 노출되지 않습니다.

가짜 전송 객체는 `request(method, url, **options)` 함수를 제공하면 됩니다. 이를 사용해 다음 내용을 실제 외부 호출 없이 검증할 수 있습니다.

- HTTP 방식, URL, 헤더, Query, Body 전달값
- timeout, SSL 확인, 응답 streaming, 자동 리다이렉트 차단 옵션
- 같은 origin 리다이렉트와 다른 host·scheme·port 차단
- JSON 경로, 표 변환, 최대 응답 크기와 최대 행 수
- HTTP 오류, 비 JSON 응답, 잘못된 경로 처리

## 실제 환경 확인 항목

운영 API 주소와 인증값은 저장소에 포함하지 않으므로 다음 항목은 사용자 테스트가 필요합니다.

- 실제 인증 헤더 이름과 값 형식
- 사내 인증서가 표준 신뢰 저장소에서 검증되는지
- 운영 주소가 HTTPS인지, 불가피한 HTTP라면 폐쇄망·방화벽 정책이 적용됐는지
- `response_path`가 실제 응답 구조와 같은지
- 30초, 10MiB, 5,000행 기본값이 업무 조회량에 맞는지
- API가 리다이렉트를 사용한다면 동일 host의 GET 이동인지
