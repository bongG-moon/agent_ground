# Skills 기반 업무 Agent 예시 Flow

사용자의 자연어 요청을 받은 Langflow Agent가 세 가지 업무 Skill 중 하나를 선택하고, 각각의 Standalone Component Tool을 직접 실행하는 교육용 예시입니다.

| Skill | Agent Tool | 실제 실행 |
| --- | --- | --- |
| 경비 사전 점검 | `expense_precheck_skill` | 금액 합산과 데모 한도 비교 |
| 휴가 정책 점검 | `leave_policy_skill` | 주말·지정 휴일을 제외한 평일 계산 |
| 회의 후속 조치 | `meeting_action_skill` | 담당자·할 일·ISO 기한 구조화 |

현재 버전은 `0.3.0`, 대상 환경은 Langflow `1.9.2` / LFX `0.4.2`, 공개 상태는 `user_testing`입니다. 이름 기반 Run Flow Tool은 실제 환경 오류가 확인되어 제거했고, 세 업무 모두 독립 Component Tool로 연결했습니다.

## 가져올 파일

- `skill_based_agent_flow.json`: Builder에 직접 가져올 실행 Flow
- `00_SKILL_BASED_AGENT_ALL_FLOWS.json`: 전체 Flow 가져오기 화면용 1개 Flow Bundle

하위 Flow를 이름으로 조회하지 않으므로 같은 이름의 Flow나 같은 폴더 조건은 없습니다.

## 실행 구조

```text
사용자 요청
  -> Skill Supervisor Agent
       |-- 경비 요청 -> expense_precheck_skill(request)
       |-- 휴가 요청 -> leave_policy_skill(request)
       `-- 회의 요청 -> meeting_action_skill(request)
  -> Chat Output
```

`데모 Skill 카탈로그 빌더`는 세 Skill의 사용 조건·금지 행동과 Agent 지시사항을 만듭니다. 이 Node는 예시 Flow에 종속되므로 Component Library에는 공개하지 않습니다.

## Main Flow 연결

이 Flow는 9개 Node와 9개 Edge로 구성됩니다.

```text
데모 Skill 카탈로그 빌더 --agent_instructions--> Skill Supervisor Agent
데모 Skill 카탈로그 빌더 --skill_catalog-------> 경비 Component Tool
데모 Skill 카탈로그 빌더 --skill_catalog-------> 휴가 Component Tool
데모 Skill 카탈로그 빌더 --skill_catalog-------> 회의 Component Tool

경비 Component Tool --component_as_tool--+
휴가 Component Tool --component_as_tool--+--> Agent.tools
회의 Component Tool --component_as_tool--+

Chat Input --message--> Agent.input_value
Agent --response--> Chat Output
```

세 Component의 Agent 동적 입력 이름은 모두 `request`입니다. `skill_catalog`, 휴가의 `holiday_dates_json` 같은 운영 설정은 Tool 인자로 노출하지 않습니다.

## 빠른 실행

1. Langflow `1.9.2` Builder에서 `skill_based_agent_flow.json`을 가져옵니다.
2. `Skill Supervisor Agent`에서 회사가 승인한 Tool Calling 지원 모델을 선택합니다.
3. API Key는 Langflow Secret 또는 안전한 전역 변수로 설정합니다.
4. Agent Tools에 세 Tool 이름이 보이는지 확인합니다.
5. `samples/TEST_QUESTIONS_AND_EXPECTED.md`의 경비·휴가·회의 질문을 실행합니다.
6. 실행 상세에서 선택된 Tool과 `request` 인자를 확인합니다.

## 안전 경계

| Skill | 수행하는 것 | 수행하지 않는 것 |
| --- | --- | --- |
| 경비 | 금액 파싱, 합계, 데모 한도 비교 | 승인, ERP 등록, 결재, 송금 |
| 휴가 | 날짜 파싱, 평일·휴일 계산 | 신청, HR 변경, 승인·반려 |
| 회의 | 담당자·할 일·기한 추출 | 메일 발송, 일정 생성, 담당자 저장 |

공통 금지 행동은 `external_write`, `external_send`, `approve`, `submit`입니다. 실제 상태를 바꾸는 Tool을 추가하려면 권한·Human Approval·감사 로그가 적용된 고정 Workflow 또는 MCP Tool이 필요합니다.

## MCP 확장

현재 Flow 실행에는 MCP 서버가 필요하지 않습니다. 외부 문서·DB·ERP·캘린더 조회가 필요할 때 Langflow 기본 MCP Tools를 Agent의 `tools`에 선택적으로 추가할 수 있습니다. 쓰기 Tool은 조회 Tool과 분리하고 별도 승인을 적용합니다.

자세한 내용은 [MCP 확장 가이드](MCP_EXTENSION_GUIDE.md)를 참고합니다.

## `SKILL.md` 자동 탐색과의 차이

이 예시는 로컬 `skills/` 폴더를 자동으로 검색하거나 설치하지 않습니다. Flow 내부 카탈로그의 Skill 설명과 실제 연결된 Tool을 Agent가 선택합니다. Skill 수가 많아지면 Registry 또는 Retriever로 관련 Skill만 주입하는 구조를 별도로 추가할 수 있습니다.

## 파일 구성

- `skill_based_agent_flow.json`: 실행 Flow
- `00_SKILL_BASED_AGENT_ALL_FLOWS.json`: 1개 Flow Bundle
- `component_refs.json`: 세 Standalone Component와 버전
- `internal_nodes.json`, `nodes/`: 데모 Skill 카탈로그 내부 Node
- `CONNECTION_GUIDE.md`: 정확한 Edge와 Tool 설정
- `MCP_EXTENSION_GUIDE.md`: MCP 선택 확장
- `samples/`: 카탈로그·요청·사용자 테스트 자료

## 검증 상태

Component 계약, Tool schema, Flow JSON 생성과 LFX Graph 파싱을 자동 검증합니다. 실제 모델의 Tool 선택 정확도는 사용자 Langflow 환경에서 확인해야 하며, 확인 전까지 `user_testing` 상태를 유지합니다.
