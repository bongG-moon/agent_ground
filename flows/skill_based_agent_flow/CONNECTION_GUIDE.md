# Skills 기반 업무 Agent 하이브리드 Flow 연결 가이드

## 1. Bundle 가져오기

Langflow `1.8.2` Builder에서 다음 파일을 가져옵니다.

```text
00_SKILL_BASED_AGENT_ALL_FLOWS.json
```

가져온 뒤 같은 프로젝트·폴더에 다음 두 Flow가 있는지 확인합니다.

```text
skill_based_agent_flow
meeting_action_skill_flow
```

`skill_based_agent_flow.json`만 단독으로 가져오면 상위 Agent Flow는 열리지만, 회의 요청을 실행할 때 `meeting_action_skill_flow`를 찾지 못합니다. 처음에는 반드시 Bundle 사용을 권장합니다.

상위 Flow의 `Hybrid Skill Supervisor Agent`에는 조직에서 승인한 Tool Calling 지원 모델과 API Key를 설정합니다. API Key, 토큰, 내부 URL은 Flow JSON이나 Sample JSON에 직접 저장하지 않습니다.

Agent Tools에는 다음 세 이름이 보여야 합니다.

```text
expense_precheck_skill
leave_policy_skill
meeting_action_skill
```

세 이름은 같아 보이지만 실행 방식은 다릅니다.

- `expense_precheck_skill`: 경비 Standalone Component를 직접 실행
- `leave_policy_skill`: 휴가 Standalone Component를 직접 실행
- `meeting_action_skill`: `CachedNamedRunFlowTool`이 회의 하위 Flow를 실행

`데모 Skill 카탈로그 빌더`는 이 예시의 고정 카탈로그와 지시사항을 만드는 Flow 내부 노드입니다. Standalone 파일이지만 독립 업무 기능으로 제공하지 않으므로 `internal_nodes.json`에서 관리합니다.

## 2. 상위 Agent Flow의 정확한 edge

상위 `skill_based_agent_flow`는 9개 노드와 8개 edge로 구성됩니다.

| 순서 | From | Output | To | Input | 전달 형식 |
| --- | --- | --- | --- | --- | --- |
| 1 | 데모 Skill 카탈로그 빌더 | `agent_instructions` | Hybrid Skill Supervisor Agent | `system_prompt` | Message |
| 2 | 데모 Skill 카탈로그 빌더 | `skill_catalog` | 경비 사전 점검 Skill | `skill_catalog` | Data |
| 3 | 데모 Skill 카탈로그 빌더 | `skill_catalog` | 휴가 정책 점검 Skill | `skill_catalog` | Data |
| 4 | Chat Input | `message` | Hybrid Skill Supervisor Agent | `input_value` | Message |
| 5 | 경비 Skill · Tool Mode | `component_as_tool` | Hybrid Skill Supervisor Agent | `tools` | Tool |
| 6 | 휴가 Skill · Tool Mode | `component_as_tool` | Hybrid Skill Supervisor Agent | `tools` | Tool |
| 7 | Cached Named Run Flow Tool | `component_as_tool` | Hybrid Skill Supervisor Agent | `tools` | Tool |
| 8 | Hybrid Skill Supervisor Agent | `response` | Chat Output | `input_value` | Message |

회의 Skill에는 카탈로그 edge를 직접 연결하지 않습니다. Agent가 회의 Tool을 선택하면 별도 하위 Flow 안에서 카탈로그와 회의 Component를 연결합니다.

## 3. 회의 하위 Flow의 정확한 edge

하위 `meeting_action_skill_flow`는 5개 노드와 3개 edge로 구성됩니다.

| 순서 | From | Output | To | Input | 전달 형식 |
| --- | --- | --- | --- | --- | --- |
| 1 | Chat Input | `message` | 회의 후속 조치 Skill | `request` | Message |
| 2 | 데모 Skill 카탈로그 빌더 | `skill_catalog` | 회의 후속 조치 Skill | `skill_catalog` | Data |
| 3 | 회의 후속 조치 Skill | `skill_message` | Chat Output | `input_value` | Message |

하위 Flow의 Chat Input은 정확히 하나여야 합니다. Run Flow Tool은 실행 시점에 이 Chat Input ID를 찾아 Agent의 `question`을 전달합니다.

## 4. 개별 Component Tool 설정

### 경비 사전 점검

| 항목 | 설정 |
| --- | --- |
| 노드 | `ExpensePrecheckSkillTool` |
| Tool Mode | 켜짐 |
| Agent 노출 이름 | `expense_precheck_skill` |
| Agent 동적 인자 | `request` |
| 비-Tool 입력 | `skill_catalog` |
| 실행 | 같은 상위 Flow 안에서 직접 계산 |

### 휴가 정책 점검

| 항목 | 설정 |
| --- | --- |
| 노드 | `LeavePolicySkillTool` |
| Tool Mode | 켜짐 |
| Agent 노출 이름 | `leave_policy_skill` |
| Agent 동적 인자 | `request` |
| 비-Tool 입력 | `skill_catalog`, `holiday_dates_json` |
| 실행 | 같은 상위 Flow 안에서 직접 계산 |

휴가 Tool의 `holiday_dates_json`은 운영자가 Flow에서 관리하며 Agent 인자로 노출하지 않습니다.

```json
["2026-08-17", "2026-10-05"]
```

두 Component 원본의 단독 실행 출력은 `skill_result` 또는 `skill_message`이지만, Agent에 연결할 때는 Tool Mode가 만든 `component_as_tool`을 사용합니다.

## 5. Cached Named Run Flow Tool 설정

회의 Tool 노드의 주요 값은 다음과 같습니다.

| 입력 | 값 | 의미 |
| --- | --- | --- |
| `flow_name_selected` | `meeting_action_skill_flow` | 실행 시 이름이 정확히 일치하는 하위 Flow 조회 |
| `tool_name` | `meeting_action_skill` | Agent에 보이는 Tool 이름 |
| `tool_description` | 회의 후속 조치 전용 설명 | Agent의 Tool 선택 기준 |
| `cache_flow` | `true` | 파싱한 그래프만 캐시 |
| `allow_cross_folder` | `false` | 상위 Flow와 같은 폴더만 조회 |
| `session_id` | 빈 값 | 부모 Agent 세션 상속 |
| `return_direct` | `true` | 하위 결과를 추가 재작성 없이 반환 |

Agent에 노출되는 동적 Tool 인자는 다음 하나입니다.

```json
{
  "flow_tweak_data": {
    "question": "현재 사용자 질문 원문"
  }
}
```

`flow_tweak_data`는 Run Flow 기반 Tool의 바깥 입력 객체이며, 그 안에는 내부 node ID가 없는 `question` 하나만 존재해야 합니다.

내부 `ChatInput-...~input_value` 키를 Agent에 노출하지 않는 것이 중요합니다. 하이픈과 물결표가 Provider 계층에서 밑줄로 정규화될 수 있기 때문입니다.

실행 순서는 다음과 같습니다.

```text
meeting_action_skill(question=...)
  -> 현재 사용자와 상위 Flow 폴더 확인
  -> meeting_action_skill_flow 이름 정확히 조회
  -> 실제 DB Flow ID 확인
  -> 현재 그래프 파싱 또는 그래프 캐시 사용
  -> 현재 Chat Input ID 확인
  -> question을 해당 Chat Input의 input_value로 변환
  -> 부모 session_id 상속
  -> 하위 Flow 실행
  -> 최종 Chat Output 반환
```

### 캐시 범위

캐시는 현재 사용자와 실제 Flow ID를 기준으로 파싱한 그래프만 저장합니다. 다음 값은 캐시하지 않습니다.

- 사용자 질문
- Tool 실행 결과
- 최종 답변
- 세션의 대화 내용

하위 Flow의 `updated_at`이 바뀌면 microsecond까지 비교하여 그래프 캐시를 무효화합니다.

## 6. Agent Tool 선택 규칙

| 사용자 의도 | 기대 Tool | 실행 경로 | Agent 인자 |
| --- | --- | --- | --- |
| 경비 항목과 금액 사전 점검 | `expense_precheck_skill` | 직접 Component Tool | `request` |
| 휴가 기간의 평일·차감 일수 계산 | `leave_policy_skill` | 직접 Component Tool | `request` |
| 회의 담당자·할 일·기한 구조화 | `meeting_action_skill` | Run Flow Tool → `meeting_action_skill_flow` | `question` |

지원 범위가 아니면 임의의 Tool을 호출하지 않습니다. 서로 다른 두 업무가 한 문장에 함께 들어오면 한 번에 하나씩 요청하도록 안내합니다. 세 Tool 모두 결과 직접 반환 계약이므로 한 요청에 한 Skill을 사용하는 것이 기본입니다.

## 7. 결과 확인

경비와 휴가 Component Tool은 공통적으로 다음 필드를 포함한 Data를 반환합니다.

```text
status
skill
result
governance
trace
disclaimer
```

회의 Run Flow Tool은 하위 Flow의 최종 Chat Output Message를 반환합니다. Message 본문에는 회의 Component가 만든 구조화 결과가 포함됩니다.

| Skill | 주요 결과 필드 |
| --- | --- |
| 경비 | `category_amounts`, `total_amount`, `limit_checks`, `overall_decision` |
| 휴가 | `start_date`, `end_date`, `weekday_count`, `excluded_holidays`, `chargeable_days`, `policy_result` |
| 회의 | `action_items`, `count`, `invalid_lines`, `ignored_header_line_count` |

Component 결과의 `trace`에는 원문 요청을 복제하지 않고 `request_sha256`, `request_length`만 기록합니다. 다만 Langflow Agent 실행 상세와 Run Flow 추적 정보에는 Tool 인자 `request` 또는 `question`이 표시될 수 있으므로 운영 로그 접근 권한과 보존 정책은 별도로 설정해야 합니다.

## 8. 자주 발생하는 문제

### 회의 Tool에서 대상 Flow를 찾지 못함

- `00_SKILL_BASED_AGENT_ALL_FLOWS.json`을 가져왔는지 확인합니다.
- 상위 Flow와 `meeting_action_skill_flow`가 같은 폴더인지 확인합니다.
- 하위 Flow 이름의 대소문자와 밑줄까지 정확한지 확인합니다.
- 같은 폴더에 `meeting_action_skill_flow`라는 이름이 여러 개 있지 않은지 확인합니다.
- 다른 폴더 사용이 꼭 필요할 때만 `allow_cross_folder`를 켭니다. 이 경우 현재 사용자의 전체 폴더에서 이름이 고유해야 합니다.

### 회의 Tool은 호출되지만 질문이 비어 있음

- Agent Tool 호출 인자가 `question`인지 확인합니다.
- 예전의 `ChatInput-...~input_value` 형태를 Tool 인자로 직접 사용하지 않습니다.
- 하위 Flow에 Chat Input이 정확히 하나인지 확인합니다.
- 현재 JSON에 포함된 개선된 `CachedNamedRunFlowTool` 코드인지 확인합니다.

### Agent가 Tool을 호출하지 않음

- Tool Calling 지원 모델인지 확인합니다.
- Provider API Key가 실행 환경에 설정됐는지 확인합니다.
- 경비·휴가의 Tool Mode와 세 `component_as_tool -> Agent.tools` 연결을 확인합니다.
- `agent_instructions -> Agent.system_prompt` 연결을 확인합니다.
- Playground 실행 기록에서 모델에 전달된 Tool 이름과 설명을 확인합니다.

### 직접 Component 결과가 기대와 다름

- 경비 카테고리명과 통화 표현을 확인합니다.
- 휴가 날짜는 `YYYY-MM-DD` 형식으로 입력합니다.
- 휴가 노드의 `holiday_dates_json`을 확인합니다.
- Agent와 분리하여 해당 Component를 단독 실행해 봅니다.

### 회의 결과가 기대와 다름

- 먼저 `meeting_action_skill_flow`를 직접 열어 동일 질문을 실행합니다.
- 회의 항목은 `담당자 | 할 일 | YYYY-MM-DD` 형식을 사용합니다.
- 직접 실행은 성공하고 상위 Agent에서만 실패하면 Run Flow의 이름, 폴더, `question` 전달을 확인합니다.

## 9. MCP 확장

현재 Flow 실행에는 MCP가 필요하지 않습니다. 외부 문서, DB, ERP, 캘린더 등을 연결하려면 Langflow 기본 MCP Tools를 Agent의 `tools`에 추가하거나 기존 Tool을 교체할 수 있습니다.

선택 기준과 인증·세션·입력 연결 주의사항은 [MCP 확장 가이드](MCP_EXTENSION_GUIDE.md)에 별도로 정리했습니다.

## 10. 운영 전 필수 변경

이 데모의 계산 기준을 실제 사규로 사용하면 안 됩니다. 운영 전에는 다음을 구현·검증합니다.

- 사규 원본, 버전, 시행일, 소유 부서 연결
- 사용자와 조직 권한을 신뢰 가능한 인증 Context에서 주입
- 정책 변경 승인과 감사 이력
- 외부 쓰기 전 Human Approval Workflow
- Tool 선택 회귀 테스트와 실패 시 안전한 중단
- 민감정보 로그 마스킹과 보존 정책

현재 Flow는 승인, 발송, 제출, 외부 쓰기를 의도적으로 수행하지 않습니다.
