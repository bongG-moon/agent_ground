# H-API 테이블 조회

URL, Token, bindParams를 직접 받아 H-API를 한 번 호출하고 데이터 테이블만 반환합니다.

## 상태

- ID: `h_api_table_request`
- 버전: `0.1.0`
- 상태: `user_testing`
- 패키징: `standalone`
- Component 범위: `general`
- 자격 판정: `qualified_component`
- 사용 범위: `직접 데이터 조회 Component (특정 Flow 미지정)`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| H-API 주소 | `api_url` | `MessageTextInput` | False | True | False |
| HTTP API 사용 허용 | `allow_insecure_http` | `BoolInput` | False | False | True |
| H-API 인증 토큰 | `h_api_token` | `SecretStrInput` | False | True | False |
| 요청 파라미터 JSON | `bind_params_json` | `MultilineInput` | False | True | False |
| 응답 데이터 경로 | `response_path` | `MessageTextInput` | False | False | False |
| 제한 시간(초) | `timeout_seconds` | `IntInput` | False | False | True |
| 최대 반환 행 수 | `max_rows` | `IntInput` | False | False | True |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| 데이터 테이블 | `data_table` | `DataFrame` | `build_data_table` |


## 상세 사용 가이드

[`USAGE_GUIDE.md`](USAGE_GUIDE.md)에서 연결 방법, 운영 조건과 사용자 확인 항목을 확인합니다.


## 등록

`h_api_table_request.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
