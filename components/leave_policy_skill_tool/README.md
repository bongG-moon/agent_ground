# 휴가 정책 점검 Skill

ISO 날짜 두 개의 평일과 선택적으로 연결한 휴일을 계산하며 실제 휴가 신청이나 승인은 하지 않습니다.

## 상태

- ID: `leave_policy_skill_tool`
- 버전: `0.1.0`
- 상태: `user_testing`
- 패키징: `standalone`
- Component 범위: `domain`
- 자격 판정: `qualified_component`
- 사용 범위: `skill_based_agent_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| Skill 카탈로그 | `skill_catalog` | `Data, JSON` | False | True | False |
| 휴가 점검 요청 | `request` | `MessageTextInput` | False | True | False |
| 휴일 날짜 JSON | `holiday_dates_json` | `MessageTextInput` | False | False | True |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| Skill 실행 결과 | `skill_result` | `Data` | `run_skill` |
| 하위 Flow 응답 | `skill_message` | `Message` | `run_skill_message` |



## 등록

`leave_policy_skill_tool.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
