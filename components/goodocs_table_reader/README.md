# GooDocs 테이블 조회기

문서 ID와 인증값으로 GooDocs 문서 한 개를 조회하고 데이터 테이블만 출력합니다. 실제 사내 GooDocs 모듈을 코드의 교체 구역에 넣어야 합니다.

## 상태

- ID: `goodocs_table_reader`
- 버전: `0.1.0`
- 상태: `user_testing`
- 패키징: `standalone`
- Component 범위: `general`
- 자격 판정: `qualified_component`
- 사용 범위: `직접 데이터 조회 Component (특정 Flow 미지정)`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| 문서 ID | `document_id` | `StrInput` | False | True | False |
| 사용자 ID | `user_id` | `StrInput` | False | True | False |
| 토큰 소스 | `token_source` | `SecretStrInput` | False | True | False |
| 토큰 키 | `token_key` | `SecretStrInput` | False | True | False |
| 시트 이름 | `sheet_name` | `StrInput` | False | False | True |
| 최대 출력 행 수 | `max_rows` | `IntInput` | False | True | True |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| 데이터 테이블 | `data_table` | `DataFrame` | `build_data_table` |


## 상세 사용 가이드

[`USAGE_GUIDE.md`](USAGE_GUIDE.md)에서 연결 방법, 운영 조건과 사용자 확인 항목을 확인합니다.


## 등록

`goodocs_table_reader.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
