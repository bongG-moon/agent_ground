# 05-2 공유 링크 출력

생성된 HTML을 Report API에 저장하고 간단한 다운로드 링크 메시지를 출력합니다.

## 상태

- ID: `report_api_publisher`
- 버전: `0.9.0`
- 상태: `user_testing`
- 패키징: `standalone`
- Component 범위: `domain`
- 자격 판정: `qualified_component`
- 사용 범위: `html_report_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| HTML 생성 결과 | `payload` | `DataInput` | False | True | False |
| Report API 주소 | `report_api_url` | `MessageTextInput` | False | False | False |
| 링크 유효시간 | `ttl_hours` | `MessageTextInput` | False | False | False |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| 링크 메시지 | `link_message` | `Message` | `build_message` |



## 등록

`report_api_publisher.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
