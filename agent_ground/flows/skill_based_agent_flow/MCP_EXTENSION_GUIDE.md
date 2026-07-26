# Skills 기반 Agent MCP 확장 가이드

## 1. 현재 Flow의 실행 범위

현재 `skill_based_agent_flow`는 MCP 서버 없이 다음 세 Component Tool을 직접 실행합니다.

```text
경비 -> ExpensePrecheckSkillTool
휴가 -> LeavePolicySkillTool
회의 -> MeetingActionSkillTool
```

Langflow 기본 `MCP Tools`는 사내 DB·문서·ERP·캘린더 같은 외부 시스템을 연결할 때만 선택적으로 추가합니다. Flow를 가져오는 것만으로 MCP 서버나 인증이 등록되지는 않습니다.

## 2. Component Tool과 MCP Tool의 구분

| 구분 | 적합한 기능 | 주의사항 |
| --- | --- | --- |
| Component Tool | 금액·날짜 계산, 문자열 변환, 형식 검증 | 로직이 커지면 여러 Component나 고정 Workflow로 분리 |
| MCP Tool | DB 조회, 문서 검색, ERP 등록, 메일·캘린더 | 서버 등록, 인증, 권한, 네트워크와 감사 로그 필요 |

```text
순수 계산과 검증 = Component Tool
외부 시스템의 조회와 행동 = MCP Tool
승인·저장·발송 순서 통제 = 고정 Workflow + Human Approval
```

## 3. MCP 조회 Tool 추가

```text
MCP Tools.component_as_tool
  -> Skill Supervisor Agent.tools
```

연결 후 Agent 지시사항에 다음을 명시합니다.

- 어떤 질문에서 MCP 조회 Tool을 사용하는지
- 계산 Component와 어떤 순서로 사용하는지
- 조회 결과가 없거나 권한 오류일 때 중단하는 방법
- 쓰기 Tool을 임의로 호출하지 않는 규칙

## 4. 쓰기 Tool은 분리

메일 발송·일정 생성·ERP 등록처럼 외부 상태를 바꾸는 Action은 조회 Action과 같은 MCP Tool 목록에 섞지 않는 것을 권장합니다.

```text
사용자 요청
  -> 조회 MCP Tool
  -> Component 계산·검증
  -> 변경 내용 미리보기
  -> Human Approval
  -> 쓰기 MCP Tool
  -> 결과와 감사 로그
```

최소 통제 항목:

- 사용자와 조직 권한 확인
- 입력 schema 검증
- 실행 전 변경 내용 표시
- 명시적 승인
- 중복 실행 방지용 idempotency key
- 실행자·시각·대상·결과 감사 로그

## 5. 인증정보

- 토큰·비밀번호를 Flow JSON, Skill 카탈로그와 Sample에 저장하지 않습니다.
- Langflow Secret/Global Variable 또는 사내 Secret Manager를 사용합니다.
- 사내 CA와 TLS 검증을 우선 사용합니다.
- 운영·테스트 서버를 분리하고 Tool 설명에 환경을 표시합니다.

## 6. 세션과 입력

MCP Action 입력 이름은 서버가 공개한 JSON Schema에 따라 달라집니다. `query`, `text`, `document_id`처럼 서버마다 다를 수 있으므로 Tool 실행 기록에서 실제 schema와 전달값을 확인합니다.

Tool Mode로 Agent에 연결했다면 MCP Action의 동적 인자는 Agent가 schema에 맞게 채웁니다. 숫자·Boolean·중첩 JSON은 문자열로 임의 변환하지 않습니다.

## 7. 사용자 테스트

- Component Tool 세 개만 연결했을 때 기존 질문이 정상 동작하는지 확인
- 조회 MCP Tool 추가 후 관련 질문에서만 호출되는지 확인
- 권한 밖 데이터가 결과·오류·로그에 노출되지 않는지 확인
- MCP 서버 중단 시 다른 Tool을 임의 호출하지 않는지 확인
- 쓰기 Tool이 승인 없이 실행되지 않는지 확인
- 인증정보가 Export JSON에 포함되지 않는지 확인
