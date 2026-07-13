# 캐시된 이름 기반 Run Flow 도구

하위 Flow를 정확한 이름으로 다시 조회하고 실제 ID 기준으로 그래프만 캐시하며, 고정 question 인자를 현재 Chat Input으로 변환해 실행합니다.

## 상태

- ID: `cached_named_run_flow_tool`
- 버전: `0.2.0`
- 상태: `user_testing`
- 패키징: `standalone`
- Component 범위: `general`
- 자격 판정: `qualified_component`
- 사용 범위: `enterprise_utility_components`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| 대상 Flow 이름 | `flow_name_selected` | `StrInput` | False | True | False |
| 해석된 Flow ID | `flow_id_selected` | `StrInput` | False | False | False |
| 세션 ID | `session_id` | `MessageTextInput` | False | False | True |
| Flow 그래프 캐시 | `cache_flow` | `BoolInput` | False | False | True |
| 다른 폴더 Flow 허용 | `allow_cross_folder` | `BoolInput` | False | False | True |
| 도구 이름 | `tool_name` | `StrInput` | False | True | False |
| 도구 설명 | `tool_description` | `MultilineInput` | False | True | False |
| 하위 결과 직접 반환 | `return_direct` | `BoolInput` | False | False | True |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| Flow 도구 | `component_as_tool` | `Tool` | `to_toolkit` |


## 상세 사용 가이드

[`USAGE_GUIDE.md`](USAGE_GUIDE.md)에서 연결 방법, 운영 조건과 사용자 확인 항목을 확인합니다.


## 처음 보는 사용자를 위한 설명

[`BEGINNER_GUIDE.md`](BEGINNER_GUIDE.md)에서 기본 Run Flow를 그대로 사용하면서 별도 Component로 감싼 이유를 쉬운 예시로 확인합니다.


## 등록

`cached_named_run_flow_tool.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
