# Skills 기반 업무 Agent 하이브리드 예시 Flow

사용자의 자연어 요청을 받은 Langflow Agent가 업무 Skill을 선택하고, 업무 크기와 재사용 방식에 따라 서로 다른 실행 수단을 사용하는 교육용 예시입니다.

| Skill | Agent에 연결되는 Tool | 실제 실행 방식 |
| --- | --- | --- |
| 경비 사전 점검 | `expense_precheck_skill` | Standalone 계산 Component를 Tool Mode로 직접 실행 |
| 휴가 정책 점검 | `leave_policy_skill` | Standalone 계산 Component를 Tool Mode로 직접 실행 |
| 회의 후속 조치 | `meeting_action_skill` | `CachedNamedRunFlowTool`이 별도 `meeting_action_skill_flow` 실행 |

즉, 모든 기능을 같은 방식으로 억지로 구현하지 않습니다. 작고 결정론적인 계산은 개별 Component Tool로 두고, 여러 노드로 확장하거나 독립적으로 재사용할 업무는 하위 Flow Tool로 분리합니다.

현재 버전은 `0.2.0`, 대상 환경은 Langflow `1.8.2`, 공개 상태는 `user_testing`입니다. 실제 Agent 실행에는 조직에서 승인한 Tool Calling 지원 모델과 API Key가 필요합니다.

## 가장 먼저 가져올 파일

Langflow Builder에는 다음 일괄 Bundle을 가져옵니다.

```text
00_SKILL_BASED_AGENT_ALL_FLOWS.json
```

Bundle에는 다음 두 Flow가 함께 들어 있습니다.

1. `skill_based_agent_flow`: 사용자의 요청을 분류하고 세 Tool 중 하나를 선택하는 상위 Agent Flow
2. `meeting_action_skill_flow`: 회의 후속 조치를 실제로 구조화하는 하위 Skill Flow

회의 Tool은 이름으로 하위 Flow를 찾으므로 두 Flow를 같은 프로젝트·폴더에 함께 가져오는 것이 기본입니다. 상위 Flow JSON만 단독으로 가져오면 경비와 휴가는 실행할 수 있어도 회의 Tool은 대상 Flow를 찾지 못합니다.

## 전체 실행 구조

```text
사용자 요청
  -> Hybrid Skill Supervisor Agent
       |-- 경비 요청 -> expense_precheck_skill
       |                -> 경비 Standalone Component 직접 계산
       |
       |-- 휴가 요청 -> leave_policy_skill
       |                -> 휴가 Standalone Component 직접 계산
       |
       `-- 회의 요청 -> meeting_action_skill
                        -> CachedNamedRunFlowTool
                        -> meeting_action_skill_flow
                        -> 회의 Standalone Component
                        -> Chat Output
```

Flow 내부 카탈로그 빌더는 세 Skill의 사용 조건, 허용 행동과 금지 행동을 Agent 지시사항으로 만듭니다. 이 단계는 예시 Flow에 종속된 demo seed이므로 Component Library에는 포함하지 않습니다. Agent는 어떤 Tool을 사용할지만 선택하고, 금액 계산·날짜 계산·회의 항목 파싱은 Component 또는 하위 Workflow가 결정론적으로 수행합니다.

## 왜 직접 Tool과 Run Flow Tool을 섞었는가

### 개별 Component Tool이 적합한 경우

- 입력과 출력이 작고 명확합니다.
- 하나의 Python Component 안에서 계산이 끝납니다.
- 실행 순서나 중간 노드를 별도로 보여줄 필요가 없습니다.
- 다른 Flow를 조회하는 비용 없이 빠르게 실행하고 싶습니다.

경비와 휴가 Skill은 이 조건에 해당하므로 Agent에 직접 Tool Mode로 연결했습니다. 두 Tool의 외부 인자는 provider-safe한 `request` 하나뿐입니다.

### Run Flow Tool이 적합한 경우

- 업무가 여러 단계로 커질 가능성이 있습니다.
- 하위 업무를 독립 Flow로 테스트하고 재사용하고 싶습니다.
- 상위 Agent에는 하나의 Tool처럼 보이게 하되 내부 연결은 Builder에서 관리하고 싶습니다.
- 하위 Flow를 수정해도 상위 Agent의 Tool 계약은 안정적으로 유지하고 싶습니다.

회의 후속 조치는 별도 `meeting_action_skill_flow`로 분리했습니다. 현재는 파싱 Component 하나가 핵심이지만, 앞으로 회의록 정규화, 담당자 확인, 검토 승인, 결과 저장 같은 노드를 하위 Flow 안에 추가할 수 있습니다.

## 개선된 Run Flow Tool의 질문 전달 방식

회의 Tool은 Agent에 다음처럼 단순한 인자만 노출합니다.

```json
{
  "flow_tweak_data": {
    "question": "회의 후속 조치를 정리해줘.\n김대리 | 견적 비교표 작성 | 2026-07-15"
  }
}
```

`flow_tweak_data`는 Langflow 기본 Run Flow Tool이 사용하는 바깥 포장이고, Agent가 채우는 실제 동적 업무 필드는 그 안의 provider-safe한 `question` 하나입니다.

`CachedNamedRunFlowTool`은 실행 시점의 `meeting_action_skill_flow` 그래프를 조회하고, 현재 Chat Input ID를 찾아 내부 tweak 키를 만듭니다.

```text
Agent의 question
  -> 실행 시 현재 하위 Flow 조회
  -> Chat Input이 정확히 하나인지 확인
  -> 현재 Chat Input ID 확인
  -> 내부 ChatInput-ID~input_value tweak 생성
  -> 하위 Flow 실행
```

따라서 `ChatInput-...~input_value` 같은 내부 키를 LLM Tool schema에 노출하지 않습니다. Provider가 하이픈이나 물결표를 밑줄로 바꾸더라도 질문이 비어 버리는 문제를 피할 수 있습니다.

또한 다음 경계를 적용합니다.

- Flow 이름은 `meeting_action_skill_flow`와 정확히 일치해야 합니다.
- 기본값에서는 상위 Flow와 같은 폴더만 조회합니다.
- 같은 이름의 Flow가 없거나 여러 개면 조용히 다른 Flow를 고르지 않고 오류를 냅니다.
- 하위 Flow에는 Chat Input과 최종 출력이 각각 정확히 하나 있어야 합니다.
- 세션 ID를 직접 지정하지 않으면 부모 Agent 세션을 상속합니다.
- 캐시는 파싱한 Flow 그래프만 대상으로 하며 질문·실행 결과·최종 답변은 캐시하지 않습니다.
- `return_direct=True`로 하위 Flow의 결과를 부모 Agent의 추가 재작성 없이 반환합니다.

## Main Flow 연결

상위 `skill_based_agent_flow`는 9개 노드와 8개 edge로 구성됩니다.

```text
데모 Skill 카탈로그 빌더 --agent_instructions--> Hybrid Skill Supervisor Agent
데모 Skill 카탈로그 빌더 --skill_catalog-------> 경비 Component Tool
데모 Skill 카탈로그 빌더 --skill_catalog-------> 휴가 Component Tool

경비 Component Tool --------component_as_tool----+
휴가 Component Tool --------component_as_tool----+--> Agent.tools
CachedNamedRunFlowTool ------component_as_tool----+

Chat Input ------------------message-------------> Agent.input_value
Agent -----------------------response------------> Chat Output
```

회의 Skill은 하위 Flow 안에서 카탈로그를 다시 검증한 뒤 회의 Component를 실행합니다. 하위 `meeting_action_skill_flow`는 5개 노드와 3개 edge로 구성됩니다.

## 빠른 실행

1. Langflow `1.8.2` Builder에서 `00_SKILL_BASED_AGENT_ALL_FLOWS.json`을 가져옵니다.
2. `skill_based_agent_flow`와 `meeting_action_skill_flow`가 같은 프로젝트·폴더에 생성됐는지 확인합니다.
3. `Hybrid Skill Supervisor Agent`에서 회사가 승인한 Tool Calling 지원 모델을 선택합니다.
4. API Key는 Langflow Secret 또는 안전한 전역 변수로 설정합니다. JSON에 직접 저장하지 않습니다.
5. Agent Tools에 `expense_precheck_skill`, `leave_policy_skill`, `meeting_action_skill`이 보이는지 확인합니다.
6. 경비, 휴가, 회의 질문을 각각 한 번씩 실행하고 실제 Tool 호출 기록을 확인합니다.
7. 회의 요청에서는 Tool 인자가 `question`이고 하위 Flow 이름이 `meeting_action_skill_flow`인지 확인합니다.
8. `samples/TEST_QUESTIONS_AND_EXPECTED.md`의 비대상·복합 의도·Prompt Injection 사례까지 점검합니다.

## 세 Skill의 안전 경계

| Skill | 수행하는 것 | 수행하지 않는 것 |
| --- | --- | --- |
| 경비 사전 점검 | 금액 파싱, 분류별 합계, 데모 한도 비교 | 경비 승인, ERP 등록, 결재 상신, 송금 |
| 휴가 정책 점검 | 날짜 파싱, 평일·휴일 계산, 데모 정책 점검 | 휴가 신청, HR 시스템 변경, 승인·반려 |
| 회의 후속 조치 | 구조화된 줄 파싱, 담당자·할 일·기한 추출 | 메일·메신저 발송, 일정 생성, 담당자 배정 저장 |

공통 금지 행동은 `external_write`, `external_send`, `approve`, `submit`입니다. 표준 Agent는 연결된 세 Tool을 매 요청마다 모두 볼 수 있으므로, 이번 예시는 외부 변경이 없는 계산·추출 기능만 제공합니다.

승인·저장·발송처럼 실제 상태를 변경하는 기능을 추가하려면 다음 중 하나가 필요합니다.

- 선택된 Skill의 exact allowlist만 전달하는 Tool Gate
- 승인 단계를 포함한 고정 하위 Workflow
- 사용자·권한·감사 로그가 적용된 MCP Tool

## MCP는 선택적 확장 수단

이 Flow를 실행하는 데 MCP 서버는 필요하지 않습니다. 현재 세 업무는 Standalone Component와 Run Flow만으로 완결됩니다.

다만 향후 사내 시스템을 연결할 때는 Langflow 기본 MCP 기능을 다음 위치에 선택적으로 사용할 수 있습니다.

```text
Agent
  -> 조회용 MCP Tool
  -> 조회 결과
  -> 경비/휴가 Component 계산 또는 Skill Flow 실행
  -> 사람이 검토
```

MCP는 DB, 문서 시스템, ERP, 캘린더 같은 외부 기능을 표준 Tool로 연결하는 수단입니다. 업무 규칙 자체를 MCP에 모두 넣기보다는, 조회·등록 같은 외부 행동은 MCP Tool로 두고 계산 규칙과 실행 순서는 Component·Skill Flow에서 통제하는 구성이 적합합니다. 쓰기 Tool은 인증, 권한, 승인, 감사 로그를 추가한 뒤 연결해야 합니다.

구체적인 연결·교체 방법은 [MCP 확장 가이드](MCP_EXTENSION_GUIDE.md)를 참고합니다.

## `SKILL.md` 자동 탐색과의 차이

이 예시는 로컬 `skills/` 폴더를 탐색하거나 `SKILL.md`를 자동으로 설치하는 구현이 아닙니다. 카탈로그에 등록된 Skill 설명과 Flow에 실제 연결된 Tool을 Agent가 선택하는 구조입니다.

Skill 수가 많아지면 별도 Registry 또는 Retriever로 관련 Skill만 찾는 Level-2 구조를 추가할 수 있습니다. 순서 통제가 필요한 Skill은 이번 회의 예시처럼 하위 Flow Tool로 분리하는 방식으로 확장할 수 있습니다.

## 카탈로그 바꾸기

`데모 Skill 카탈로그 빌더`의 `catalog_json`에 `samples/sample_skill_catalog.json`과 같은 JSON을 넣을 수 있습니다.

```text
contract = agent_ground.demo_skill_catalog.v1
catalog_id = enterprise_skill_demo
version = 1.0.0
skills = 정확히 3개
```

카탈로그는 Tool을 새로 생성하지 않습니다. JSON의 `tool_name`은 실제 연결된 Tool 이름과 일치해야 하며, 회의 Skill의 실제 실행 대상 Flow 이름은 `CachedNamedRunFlowTool`의 `flow_name_selected`에서 별도로 관리합니다.

## 파일 구성

- `00_SKILL_BASED_AGENT_ALL_FLOWS.json`: 상위 Agent Flow와 회의 하위 Flow 일괄 가져오기 Bundle
- `skill_based_agent_flow.json`: 상위 하이브리드 Agent Flow 단독 JSON
- `meeting_action_skill_flow.json`: 회의 후속 조치 하위 Skill Flow 단독 JSON
- `component_refs.json`: 필요한 Standalone Component와 버전
- `internal_nodes.json`, `nodes/`: 상위·하위 Flow가 함께 쓰는 데모 Skill 카탈로그 내부 노드
- `CONNECTION_GUIDE.md`: 실제 edge, Run Flow 설정, 오류 해결
- `MCP_EXTENSION_GUIDE.md`: Langflow 기본 MCP Tools 연결·교체와 인증·세션 주의사항
- `samples/sample_skill_catalog.json`: 카탈로그 입력 예시
- `samples/sample_requests.json`: 자동화·수동 점검용 요청과 예상 실행 경로
- `samples/TEST_QUESTIONS_AND_EXPECTED.md`: Playground 사용자 테스트 시나리오

## 검증 상태

Component 계약, Tool schema, Flow JSON 구조와 그래프 파싱은 프로젝트 테스트에서 확인합니다. 실제 사용자 DB에서 이름 기반으로 하위 Flow를 찾고 승인된 모델이 올바른 Tool을 선택하는 E2E 검증은 사용자 Langflow 환경에서 별도로 수행해야 합니다. 이 문서는 라이브 Agent 실행 성공을 주장하지 않습니다.
