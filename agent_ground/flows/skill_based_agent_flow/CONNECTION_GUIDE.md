# Skills 기반 업무 Agent Flow 연결 가이드

## 1. 가져오기와 모델 설정

Langflow `1.9.2` Builder에서 `skill_based_agent_flow.json`을 가져옵니다. 전체 Flow 가져오기 화면을 사용할 때는 `00_SKILL_BASED_AGENT_ALL_FLOWS.json`을 사용합니다.

`Skill Supervisor Agent`에는 조직에서 승인한 Tool Calling 지원 모델과 API Key를 설정합니다. 비밀값은 JSON에 저장하지 않습니다.

Agent Tools에는 다음 세 이름이 보여야 합니다.

```text
expense_precheck_skill
leave_policy_skill
meeting_action_skill
```

## 2. 정확한 Edge

Flow는 9개 Node와 9개 Edge로 구성됩니다.

| 순서 | From | Output | To | Input | 형식 |
| --- | --- | --- | --- | --- | --- |
| 1 | 데모 Skill 카탈로그 빌더 | `agent_instructions` | Skill Supervisor Agent | `system_prompt` | Message |
| 2 | 데모 Skill 카탈로그 빌더 | `skill_catalog` | 경비 사전 점검 Skill | `skill_catalog` | Data |
| 3 | 데모 Skill 카탈로그 빌더 | `skill_catalog` | 휴가 정책 점검 Skill | `skill_catalog` | Data |
| 4 | 데모 Skill 카탈로그 빌더 | `skill_catalog` | 회의 후속 조치 Skill | `skill_catalog` | Data |
| 5 | Chat Input | `message` | Skill Supervisor Agent | `input_value` | Message |
| 6 | 경비 Skill · Tool Mode | `component_as_tool` | Skill Supervisor Agent | `tools` | Tool |
| 7 | 휴가 Skill · Tool Mode | `component_as_tool` | Skill Supervisor Agent | `tools` | Tool |
| 8 | 회의 Skill · Tool Mode | `component_as_tool` | Skill Supervisor Agent | `tools` | Tool |
| 9 | Skill Supervisor Agent | `response` | Chat Output | `input_value` | Message |

## 3. Component Tool 설정

| Tool | Component | 동적 인자 | 비-Tool 입력 |
| --- | --- | --- | --- |
| `expense_precheck_skill` | `ExpensePrecheckSkillTool` | `request` | `skill_catalog` |
| `leave_policy_skill` | `LeavePolicySkillTool` | `request` | `skill_catalog`, `holiday_dates_json` |
| `meeting_action_skill` | `MeetingActionSkillTool` | `request` | `skill_catalog` |

세 Component 모두 Tool Mode가 켜져 있고 `component_as_tool` 출력을 Agent의 `tools` 입력에 연결합니다. 각 Component의 결과는 직접 반환되며 승인·저장·발송을 수행하지 않습니다.

## 4. 기대 실행 경로

| 사용자 의도 | 기대 Tool | Agent 인자 |
| --- | --- | --- |
| 경비 항목과 금액 점검 | `expense_precheck_skill` | `request` |
| 휴가 기간의 평일 계산 | `leave_policy_skill` | `request` |
| 회의 담당자·할 일·기한 구조화 | `meeting_action_skill` | `request` |

지원 범위가 아니면 Tool을 호출하지 않습니다. 서로 다른 두 업무가 한 요청에 섞이면 한 번에 하나씩 입력하도록 안내합니다.

## 5. 결과 확인

세 Tool은 공통적으로 다음 구조의 Data를 반환합니다.

```text
status
skill
result
governance
trace
disclaimer
```

`trace`에는 원문 대신 `request_sha256`, `request_length`를 남깁니다. 다만 Agent 실행 상세에는 Tool 인자가 보일 수 있으므로 운영 로그의 접근 권한과 보존 정책은 별도로 설정합니다.

## 6. 문제 확인

### Tool이 보이지 않음

- Component의 Tool Mode가 켜져 있는지 확인합니다.
- 세 `component_as_tool -> Agent.tools` 연결을 확인합니다.
- Tool Calling을 지원하는 모델인지 확인합니다.

### 잘못된 Tool을 선택함

- `agent_instructions -> Agent.system_prompt` 연결을 확인합니다.
- 한 요청에 한 가지 업무만 넣어 다시 시험합니다.
- Tool 이름·설명의 사용 조건과 제외 조건을 확인합니다.

### 회의 결과가 비어 있음

- 회의 항목을 `담당자 | 할 일 | YYYY-MM-DD` 형식으로 입력합니다.
- `MeetingActionSkillTool`을 Agent와 분리해 단독 실행합니다.
- Tool 호출 인자가 `request`인지 확인합니다.

## 7. 운영 전 확인

- 실제 사규와 소유 부서·시행일 연결
- 사용자 권한을 신뢰 가능한 인증 Context에서 주입
- 외부 쓰기 전 Human Approval
- Tool 선택 회귀 테스트
- 민감정보 로그 마스킹과 보존 정책
