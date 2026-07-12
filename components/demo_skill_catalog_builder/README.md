# 데모 Skill 카탈로그 빌더

검증된 세 가지 업무 Skill 카탈로그와 Supervisor Agent용 지시사항을 생성합니다.

## 상태

- ID: `demo_skill_catalog_builder`
- 버전: `0.1.0`
- 상태: `user_testing`
- Standalone: `true`
- 사용 범위: `skill_based_agent_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| Skill 카탈로그 JSON | `catalog_json` | `MultilineInput` | False | False | True |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| Skill 카탈로그 | `skill_catalog` | `Data` | `build_catalog` |
| Agent 지시사항 | `agent_instructions` | `Message` | `build_instructions` |



## 등록

`demo_skill_catalog_builder.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
