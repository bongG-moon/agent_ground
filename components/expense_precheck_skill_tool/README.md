# 경비 사전 점검 Skill

식대·교통비·숙박비·기타 금액을 교육용 한도와 비교하며 실제 승인이나 저장은 하지 않습니다.

## 상태

- ID: `expense_precheck_skill_tool`
- 버전: `0.1.0`
- 상태: `user_testing`
- Standalone: `true`
- 사용 범위: `skill_based_agent_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| Skill 카탈로그 | `skill_catalog` | `Data, JSON` | False | True | False |
| 경비 점검 요청 | `request` | `MessageTextInput` | False | True | False |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| Skill 실행 결과 | `skill_result` | `Data` | `run_skill` |
| 하위 Flow 응답 | `skill_message` | `Message` | `run_skill_message` |



## 등록

`expense_precheck_skill_tool.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
