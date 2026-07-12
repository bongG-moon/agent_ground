# Skills 기반 Agent MCP 확장 가이드

## 1. 현재 Bundle에는 MCP 연결이 없습니다

`00_SKILL_BASED_AGENT_ALL_FLOWS.json`은 다음 세 실행 경로만 포함합니다.

```text
경비 -> Standalone Component Tool
휴가 -> Standalone Component Tool
회의 -> CachedNamedRunFlowTool -> meeting_action_skill_flow
```

Langflow 기본 `MCP Tools`는 사내 DB, 문서, ERP, 캘린더 같은 외부 시스템을 연결할 때 선택적으로 추가하는 확장 수단입니다. 따라서 Bundle을 가져오는 것만으로 MCP 서버가 등록되거나 인증되는 것은 아닙니다.

## 2. Component Tool, Run Flow Tool, MCP Tool 선택 기준

| 구분 | 적합한 기능 | 장점 | 주요 주의사항 |
| --- | --- | --- | --- |
| 개별 Component Tool | 금액 계산, 날짜 계산, 문자열 변환, 형식 검증 | 빠르고 계약이 단순하며 코드로 결정론적 통제 가능 | 로직이 커지면 한 Component가 비대해질 수 있음 |
| Run Flow Tool | 여러 노드가 필요한 Skill, 독립 테스트·재사용이 필요한 업무 | 하위 Workflow의 순서와 분기를 Builder에서 관리 가능 | 대상 Flow 이름·폴더·Chat Input·세션 계약 필요 |
| MCP Tool | DB 조회, 문서 검색, ERP 등록, 메일·캘린더 등 외부 시스템 기능 | 외부 Tool을 표준 방식으로 재사용 가능 | 서버 등록, 인증, 권한, 네트워크, 감사 로그가 별도로 필요 |

추천 원칙은 다음과 같습니다.

```text
순수 계산과 검증 = Component Tool
여러 단계 업무 절차 = Run Flow Tool
외부 시스템의 조회·행동 = MCP Tool
```

하나의 Skill Flow 안에서 세 방식을 함께 사용할 수도 있습니다.

```text
MCP로 사규 조회
  -> Component로 금액 계산
  -> Run Flow로 검토 절차 실행
  -> Human Approval
  -> 쓰기 MCP Tool 실행
```

## 3. Langflow 1.8.2에서 MCP 서버 먼저 등록하기

1. Langflow의 MCP 서버 관리 화면에서 서버를 등록합니다.
2. 서버 방식에 맞게 STDIO 또는 Streamable HTTP 설정을 입력합니다.
3. 서버 연결을 확인하고 노출되는 Tool 목록을 새로 고칩니다.
4. 조회 전용 Action 하나를 선택해 단독 테스트합니다.
5. 성공한 뒤에만 Agent Flow에 연결합니다.

서버 등록과 인증 정보는 이 프로젝트의 Flow JSON에 포함하지 않습니다.

- HTTP URL, Authorization Header, API Token은 사용자 환경의 MCP 서버 설정 또는 Secret·전역 변수에서 관리합니다.
- STDIO command, args, env는 해당 Langflow 실행 환경에 맞게 별도로 등록합니다.
- 인증서 검증은 기본적으로 켭니다. 자체 서명 인증서를 쓰는 개발 환경이 아니라면 `Verify SSL Certificate`를 끄지 않습니다.
- Flow를 다른 사용자나 서버에 가져오면 그 환경에서 MCP 서버와 인증을 다시 등록해야 합니다.

Langflow 1.8.2의 MCP Tools는 현재 사용자 DB에 등록된 서버 설정을 우선 사용합니다. 따라서 JSON에 같은 서버 이름이 남아 있더라도 현재 사용자 DB에 서버가 없거나 권한이 없으면 Tool을 불러올 수 없습니다.

## 4. 기존 Agent에 MCP Tools 추가하기

다음 방식은 기존 경비·휴가·회의 Tool을 유지하면서 조회용 MCP Tool을 추가합니다.

1. `skill_based_agent_flow`를 엽니다.
2. 기본 Component 목록에서 `MCP Tools`를 추가합니다.
3. `MCP Server`에서 미리 등록한 서버를 선택합니다.
4. Tool Mode를 켭니다.
5. `Actions` 또는 Tool 목록에서 Agent에 허용할 Action만 선택합니다.
6. MCP Tools가 제공하는 Tool 출력을 `Hybrid Skill Supervisor Agent.tools`에 연결합니다.
7. Agent 지시사항과 MCP Tool 설명에 언제 사용하고 언제 사용하지 않는지 추가합니다.
8. 조회 전용 질문으로 실제 Tool 이름과 입력 스키마를 확인합니다.

개념적인 연결은 다음과 같습니다.

```text
Expense Component Tool --------+
Leave Component Tool ----------+
Meeting Run Flow Tool ---------+--> Hybrid Skill Supervisor Agent.tools
MCP Tools의 허용 Action -------+
```

표준 Agent의 `tools` 입력은 여러 Tool을 받을 수 있으므로 MCP Tool을 기존 Tool과 함께 연결할 수 있습니다. 하지만 연결된 Tool이 많아질수록 모델의 선택 오류 가능성이 커집니다. 서버의 모든 Action을 한꺼번에 노출하지 말고 해당 Agent에 필요한 최소 Action만 선택합니다.

## 5. 기존 Tool을 MCP Tool로 교체하기

기존 기능과 동일한 계약을 제공하는 MCP Tool이 있다면 직접 Component 또는 Run Flow Tool을 교체할 수 있습니다.

예를 들어 사내 휴가 정책 조회 MCP가 있다면 다음처럼 구성할 수 있습니다.

```text
변경 전
leave_policy_skill -> 로컬 데모 날짜 계산

변경 후
hr_leave_policy_lookup -> MCP 서버의 실제 정책 조회
```

교체 절차:

1. 기존 Tool edge를 바로 삭제하기 전에 MCP Action을 단독 실행합니다.
2. Tool 이름, 설명, 필수 인자, 반환 형식을 기록합니다.
3. Agent 지시사항의 `tool_name`, 선택 조건과 금지 행동을 MCP 계약에 맞게 바꿉니다.
4. 기존 Tool edge를 끊고 MCP Tool 출력을 `Agent.tools`에 연결합니다.
5. 기존 회귀 질문으로 Tool 선택과 결과 구조를 다시 검증합니다.
6. 실패 시 기존 Component Tool로 되돌릴 수 있도록 원본 Flow JSON을 보관합니다.

이름만 같게 만드는 것으로는 충분하지 않습니다. MCP Tool의 인자와 결과 계약이 기존 Tool과 다르면 Agent 지시사항, 결과 정규화 Component, 테스트 기대값을 함께 바꿔야 합니다.

## 6. Actions 선택과 권한 경계

MCP 서버 하나가 여러 Tool을 제공할 수 있습니다. Langflow Builder에서 Agent에 노출할 Actions를 선택할 때 다음 기준을 사용합니다.

### 기본 허용 후보

- 검색
- 조회
- 목록 확인
- 미리보기
- 초안 생성
- 유효성 검증

### 승인 Workflow 없이 연결하지 않을 후보

- 생성
- 수정
- 삭제
- 제출
- 승인·반려
- 메일·메신저 발송
- 캘린더 등록
- 결제·송금

조회와 쓰기 Action이 같은 서버에 있다면 조회 전용 Agent와 실행 Agent를 분리하는 것이 안전합니다. 쓰기 Action은 사용자 권한 확인, 입력 미리보기, Human Approval, 실행 결과 감사 로그를 거쳐야 합니다.

```text
Supervisor Agent
  -> 조회 MCP Tool
  -> 실행 계획 표시
  -> Human Approval
  -> 쓰기 전용 Agent 또는 고정 Workflow
  -> 쓰기 MCP Tool
```

Tool 설명에는 최소한 다음 내용을 포함합니다.

```text
무엇을 하는가
언제 사용하는가
언제 사용하지 않는가
필수 입력은 무엇인가
외부 상태를 변경하는가
어떤 결과를 반환하는가
```

## 7. `input_value`와 MCP Action 인자를 혼동하지 않기

상위 Agent의 `input_value`는 Chat Input에서 받은 사용자 Message입니다.

```text
Chat Input.message -> Agent.input_value
```

MCP Action의 입력 이름은 서버가 공개한 JSON Schema에 따라 달라집니다. `query`, `question`, `text`, `input_value`, `document_id`처럼 서버마다 다를 수 있습니다.

Tool Mode로 Agent에 연결한 경우에는 MCP Action의 동적 인자를 Chat Input에 직접 연결하지 않고 Agent가 Tool schema에 맞게 채우도록 둡니다. Tool 실행 기록에서 실제 인자 이름과 값이 맞는지 확인합니다.

```text
사용자 질문 -> Agent.input_value
Agent 판단 -> MCP Action 선택
Agent -> MCP Action의 실제 schema 인자 전달
```

MCP Tools를 Agent 없이 단독 실행할 때는 Action을 하나 선택한 뒤 표시되는 입력 필드에 값을 직접 연결합니다. Message가 연결되는 입력은 텍스트로 변환될 수 있지만, 숫자·Boolean·중첩 JSON은 서버 schema와 정확히 일치하는 형식을 사용해야 합니다.

Run Flow Tool의 `question`과 MCP Tool의 인자도 서로 다른 계약입니다.

- 회의 Run Flow Tool: 항상 provider-safe `question`
- MCP Tool: 서버 Action이 정의한 실제 인자 이름

MCP Action 인자를 Run Flow의 내부 `ChatInput-...~input_value` 형식으로 바꾸지 않습니다.

## 8. 세션 주의사항

현재 하이브리드 Flow의 세션 처리 방식은 다음과 같습니다.

- Chat Input과 Agent는 현재 Langflow 실행 세션을 사용합니다.
- `CachedNamedRunFlowTool`은 `session_id`를 비우면 부모 Agent 세션을 하위 Flow에 상속합니다.
- Langflow 1.8.2의 MCP Tools는 그래프의 Langflow 세션 ID와 서버 이름을 이용해 지속형 MCP 세션 Context를 구분할 수 있습니다.

따라서 같은 사용자 대화를 유지하려면 호출마다 동일한 Langflow `session_id`를 사용합니다. 반대로 서로 다른 사용자나 테스트 케이스에는 같은 세션 ID를 재사용하지 않습니다.

주의할 점:

- MCP 서버 자체의 인증 세션과 Langflow 대화 세션은 같은 개념이 아닙니다.
- MCP Tool이 상태를 보존한다고 가정하지 말고 서버 문서를 확인합니다.
- 세션 ID에 민감정보를 넣지 않습니다.
- Agent 대화 기록에서 질문을 복구하는 방식으로 필수 Tool 인자 누락을 숨기지 않습니다.
- Run Flow와 MCP 모두 필수 질문·ID·조건은 명시적인 Tool 인자로 전달합니다.

## 9. MCP Tools 캐시와 서버 변경

Langflow MCP Tools의 `Use Cached Server`를 켜면 서버와 Tool 목록을 다시 불러오는 비용을 줄일 수 있습니다. 다만 서버의 Tool schema 또는 권한을 변경한 직후에는 캐시를 끄고 목록을 새로 불러와 계약을 다시 확인하는 편이 안전합니다.

다음은 서로 다른 캐시입니다.

| 캐시 | 대상 | 포함하지 않는 것 |
| --- | --- | --- |
| CachedNamedRunFlowTool | 파싱한 Langflow 하위 Flow 그래프 | 질문, 실행 결과, 최종 답변 |
| MCP Tools `Use Cached Server` | MCP 서버 연결 정보와 Tool 목록 | 업무 결과의 영구 저장을 의미하지 않음 |

캐시를 켰다고 인증·권한 검사가 생략되는 것으로 이해하면 안 됩니다.

## 10. 추천 확장 예시

### 조회용 MCP를 기존 계산 앞에 추가

```text
사용자: 이번 출장 숙박비 기준도 같이 확인해줘
  -> Agent가 사규 조회 MCP Tool 호출
  -> 조회된 한도와 시행일 확인
  -> 경비 계산 Component 실행
  -> 근거와 계산 결과 반환
```

이 경우 조회 결과를 경비 Component의 정책 입력으로 안전하게 정규화하는 Adapter 또는 하위 Skill Flow가 추가로 필요합니다. 현재 경비 Tool은 내장 데모 기준만 사용하므로 MCP Tool을 연결하는 것만으로 자동 반영되지는 않습니다.

### 회의 하위 Flow 안에 MCP 조회 추가

```text
meeting_action_skill
  -> meeting_action_skill_flow
  -> 담당자 Directory 조회 MCP
  -> 회의 항목 파싱 Component
  -> 검토용 결과 반환
```

담당자 조회까지만 자동화하고 메일·캘린더 등록은 별도 승인 Workflow로 분리하는 구성이 적합합니다.

## 11. MCP 확장 테스트 체크리스트

- 현재 Langflow 사용자에게 MCP 서버가 등록되어 있습니다.
- 인증값이 Flow JSON과 Sample 파일에 포함되지 않습니다.
- TLS 인증서 검증이 운영 환경에서 켜져 있습니다.
- Agent에는 필요한 최소 Actions만 노출됩니다.
- Tool 이름이 기존 세 Tool과 충돌하지 않습니다.
- Tool 설명에 사용 조건과 금지 조건이 모두 있습니다.
- Agent Tool 호출 인자가 MCP JSON Schema와 일치합니다.
- 같은 대화에서는 일관된 Langflow 세션 ID를 사용합니다.
- 다른 사용자 사이에 세션 ID를 공유하지 않습니다.
- 조회 Tool과 쓰기 Tool을 구분했습니다.
- 쓰기 Action 앞에는 권한 확인과 Human Approval이 있습니다.
- 실패와 Timeout에서 외부 작업 완료를 주장하지 않습니다.
- 서버·Tool schema 변경 후 캐시를 비우고 회귀 테스트했습니다.

MCP 확장은 연결 자체보다 **서버 등록, 최소 Action 노출, 입력 계약, 세션 분리, 승인 경계**를 함께 설계해야 안전하게 사용할 수 있습니다.
