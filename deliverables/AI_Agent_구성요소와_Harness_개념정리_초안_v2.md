# AI Agent 구성요소와 Harness 개념 정리 — PPT 초안 v2

## 자료 구성

- 본문 1장: Agent 전체 구조와 MCP·Tool·Skill·Main Agent·Sub Agent·Harness의 관계
- 부록 1장: Harness의 실제 구현 방식과 Skill과의 차이
- 대상: AI Agent 구조에 익숙하지 않은 구성원
- 자료 목적: 개별 용어를 암기하기보다 **각 요소가 무엇을 판단하고, 무엇을 실행하며, 무엇을 통제하는지 이해**

## 전체 핵심 메시지

> **AI Agent는 하나의 LLM이 모든 일을 처리하는 구조가 아니라, Main Agent가 Skill·Sub Agent·Tool을 조정하고 Harness가 전체 실행을 통제하는 시스템**

---

# 1장. AI Agent는 판단·절차·실행·연결·통제를 결합한 시스템

## 제목

**AI Agent는 모델 하나가 아니라, 역할이 다른 구성요소의 조합**

## 부제

Main Agent가 필요한 기능을 조정하고 Harness가 전체 실행을 안정적으로 운영

## 도입 설명

LLM은 사용자의 질문을 이해하고 다음 행동을 판단할 수 있지만, 그 자체로 사내 데이터를 조회하거나 업무시스템을 변경하지는 못함. 실제 업무 수행을 위해서는 업무 절차를 알려주는 Skill, 실행 기능을 제공하는 Tool, Tool을 공통 방식으로 연결하는 MCP가 필요함.

이 구성요소들이 반복적으로 안전하게 동작하려면 모델 호출, Tool 실행, Context, 권한, 재시도, 검증과 로그를 관리하는 Harness가 필요함. 복잡한 전문 업무는 Sub Agent에 위임할 수 있지만, Sub Agent는 모든 업무에 필요한 필수 계층은 아님.

## 전체 구조

```text
┌──────────────────────────── 공통 Agent Harness ────────────────────────────┐
│                                                                            │
│  [실행 엔진] Agent Loop · Context/Memory · Tool 실행 · 작업 상태            │
│  [정책 설정] 사용 가능 Tool · 최대 실행 횟수 · Timeout/Retry · 승인 조건     │
│  [운영 지침] 공통 행동원칙 · 응답 방식 · 금지사항 · Markdown/Prompt          │
│                                                                            │
│  사용자 요청                                                              │
│      ↓                                                                     │
│  Super / Main Agent                                                        │
│  질문 이해 · 실행계획 수립 · 기능 선택 · 결과 통합                          │
│      │                                                                     │
│      ├─ Skill 참조                                                         │
│      │   어떤 순서·기준·주의사항으로 업무를 수행할지 확인                   │
│      │                                                                     │
│      ├─ Sub Agent 위임 — 필요한 경우만 사용                                │
│      │   전문 판단이나 독립적인 복합 작업을 별도 Agent에 위임               │
│      │                                                                     │
│      └─ MCP Client를 통해 Tool 탐색·호출                                   │
│                                                                            │
│  Harness가 권한·승인·실행 제한·오류 복구·결과 검증·로그를 공통 관리          │
└──────────────────────────────────┬─────────────────────────────────────────┘
                                   │ MCP 표준 연결
                                   ↓
                         MCP Server / Tool 제공 영역
                                   ↓
              데이터 조회 · 분석 로직 · API 호출 · 파일/보고서 생성 · 시스템 처리
                                   ↓
                            DB · API · 기존 업무시스템
```

## 구성요소별 쉬운 설명

| 구성요소 | 쉽게 말하면 | 실제 역할 |
|---|---|---|
| **Super / Main Agent** | 전체 업무 조정자 | 사용자 요청을 이해하고 필요한 Skill·Tool·Sub Agent를 선택하며 실행계획과 최종 결과를 통합. `Super Agent`는 표준 명칭이라기보다 Main Agent·Supervisor·Orchestrator와 유사한 상위 조정 역할을 표현한 용어 |
| **Sub Agent** | 전문 담당자 | 특정 전문영역이나 독립적인 복합 작업을 담당. 별도 Context와 Tool 구성을 가질 수 있으며 결과를 Main Agent에 반환. 단순 업무에서는 생략 가능 |
| **Skill** | AI용 업무 절차서 | 어떤 상황에 어떤 작업 순서·판단 기준·주의사항·결과 형식을 적용할지 Agent에 설명. Agent Skills 형식에서는 `SKILL.md`를 중심으로 구성 |
| **Tool** | 실제 실행 기능 | 데이터 조회, 계산·분석, API 호출, 시스템 변경, HTML·PPT·파일 생성 등 실제 동작 수행. 단위 기능 또는 완결된 복합 업무 단위로 구현 가능 |
| **MCP** | Agent와 기능 사이의 공통 연결 규약 | Agent가 외부의 Tool·Resource·Prompt를 발견하고 호출하기 위한 Client–Server Protocol. Tool 자체가 아니라 Tool을 공통 방식으로 연결하는 역할 |
| **Harness** | Agent의 실행 엔진과 통제 장치 | 모델과 Tool을 반복 호출하는 Agent Loop, Context·Memory, 권한·승인, Timeout·Retry, 결과 검증, 오류 처리, 로그·추적 등을 관리하는 Runtime |

## 핵심 구분

> **Main Agent는 조정하고, Skill은 방법을 설명하며, Tool은 실제로 실행함. MCP는 기능을 연결하고, Harness는 전체 실행을 운영·통제함. Sub Agent는 필요한 경우 전문 판단을 분담함.**

## 장표 구현 방향

- 중앙: 사용자 → Main Agent → MCP → Tool → 업무시스템의 실행 흐름
- Main Agent 측면: Skill과 Sub Agent를 선택적 보조 요소로 표현
- 전체 Agent 영역을 큰 외곽선으로 감싸고 Harness로 표시
- Harness 상단에 `실행 엔진 / 정책 설정 / 운영 지침`의 세 구성요소 배치
- 하단에는 여섯 개 개념의 핵심 구분 메시지 배치
- 도입 설명 두 문단은 발표자 설명으로 활용하고 장표에는 구조와 개념 정의 중심으로 표현

---

# 부록 1. Harness는 하드코딩된 하나의 프로그램이 아니라 실행 구조의 조합

## 제목

**Harness는 Framework·코드·정책 설정·운영 지침을 결합해 구성**

## 핵심 답변

> **Harness를 전부 직접 하드코딩하는 것도 아니고, Markdown 파일 하나로 구성하는 것도 아님.**

Agent Framework나 Agent Platform이 기본 실행 루프와 Context 관리를 제공하고, 조직은 필요한 권한·승인·검증·오류 처리 기능을 코드나 Middleware로 보완함. 자주 바뀌는 실행 한도와 사용 가능 Tool은 설정으로 관리하고, 모델이 참고할 행동원칙은 Markdown이나 Prompt로 제공하는 구조가 일반적임.

## Harness의 구성 방식

| 구성 영역 | 일반적인 구현 방식 | 담당 내용 |
|---|---|---|
| **기본 Runtime** | Agent Framework·Orchestrator·플랫폼 제공 기능 | 모델 호출 → Tool 실행 → 결과 반영 → 다음 행동 판단을 반복하는 Agent Loop, 대화 이력, 작업 상태, Context 관리 |
| **조직별 제어 로직** | 코드·Middleware·Gateway | 사용자 인증, Tool 호출 권한, 시스템 변경 승인, 중복 실행 방지, 입력·출력 검증, 오류 처리, 감사 로그 |
| **운영 정책** | YAML·JSON·DB·관리 화면 등 설정 | 사용 가능 Tool, 최대 실행 횟수, Token 한도, Timeout, Retry 횟수, 승인 필요 작업, Agent Profile |
| **Agent 운영 지침** | System Prompt·Markdown·운영 문서 | Agent의 공통 행동원칙, 답변 형식, 확인해야 할 사항, 금지 행동, 사용자와의 상호작용 방식 |
| **업무별 Skill** | `SKILL.md` + 선택적 Script·Reference·Asset | 특정 업무의 적용 조건, 수행 순서, 판단 기준, 주의사항과 결과 형식 |
| **Tool 내부 통제** | Tool·API·업무시스템 코드 | 실제 데이터 권한 확인, Transaction 처리, 업무 규칙 적용, 변경 요청 거절 또는 실행 |

## 무엇을 어디에서 관리해야 하는가

### 1. 코드 또는 Runtime에서 강제할 항목

반드시 지켜져야 하는 안전·운영 기준은 모델의 지침 준수에만 의존하지 않고 실행 계층에서 강제함.

- 권한이 없는 Tool 호출 차단
- 시스템 변경 전 승인 확인
- 최대 실행 횟수와 전체 실행시간 제한
- Timeout·Retry와 중복 실행 방지
- 입력·출력 Schema 검증
- 오류 발생 시 중단·복구·대체 경로 수행
- Tool 호출 내역과 결과의 감사 로그 저장

### 2. 설정으로 분리할 항목

조직·사용자·환경에 따라 달라지거나 운영 중 변경될 수 있는 값은 코드에 고정하지 않고 설정으로 관리함.

```yaml
agent_profile:
  available_tools:
    - data_query
    - data_analysis
    - ppt_generate

execution_policy:
  max_iterations: 8
  timeout_seconds: 120
  retry_count: 1

approval_policy:
  data_query: automatic
  system_change: required
```

### 3. Markdown·Prompt로 관리할 항목

모델이 판단할 때 참고하는 행동원칙과 업무 지식은 문서 형태로 관리 가능함.

```markdown
# Agent 공통 운영 지침

- 사용자 요청을 먼저 업무 단위로 구분한다.
- 분석 전에 데이터 기준 시점과 조회 범위를 확인한다.
- 시스템 변경 작업은 승인 여부를 확인한다.
- 최종 답변에 사용한 Tool과 주요 근거를 포함한다.
```

이 문서는 Harness가 모델에 제공하는 지침의 일부이며, 권한·Timeout·Retry를 실제로 집행하는 Harness Runtime 자체는 아님.

## Skill과 Harness의 차이

| 구분 | Skill | Harness |
|---|---|---|
| **핵심 질문** | “이 업무를 어떤 순서와 기준으로 수행할 것인가?” | “Agent를 어떤 조건에서 실행·허용·중단·복구할 것인가?” |
| **범위** | 특정 업무 또는 작업 유형 | Agent 전체 실행과 운영 환경 |
| **주요 형식** | `SKILL.md` 중심의 지침 패키지 | Framework·실행 코드·정책 설정·Prompt의 결합 |
| **제어 성격** | 모델이 참고하는 절차와 지식 | Runtime이 실제로 집행하는 실행 통제 |
| **예시** | 데이터 조회 → 검증 → 분석 → 종합의견 → PPT 작성 | 권한 확인 → 최대 8회 실행 → Timeout 120초 → 실패 시 1회 재시도 → 결과 검증·로그 저장 |

## “Harness도 MD 파일인가?”에 대한 정리

- 범용적으로 합의된 `HARNESS.md` 표준은 없음.
- 플랫폼에 따라 Harness 수준의 공통 지침을 Markdown으로 저장할 수는 있음.
- 그러나 그 파일은 Harness의 **운영 지침**이며, Harness 전체를 의미하지는 않음.
- Skill은 Markdown 중심으로 구성 가능하지만 Harness는 **Runtime + 코드 + 정책 설정 + 지침**의 결합으로 이해하는 것이 적절함.

## Main Agent와 Sub Agent의 Harness 관계

Harness를 Agent마다 완전히 별도로 개발해야 하는 것은 아님. 하나의 공통 Agent Engine 또는 Harness가 여러 Agent Profile을 실행할 수 있음.

```text
공통 Agent Engine / Harness
├─ Profile A: Skill A · Tool A/B · Sub Agent A
├─ Profile B: Skill B · Tool C/D
└─ Profile C: Skill C · Tool B/E · Sub Agent C
```

- 공통 Harness: 실행 루프, 권한·승인, Timeout·Retry, 검증, 로그 제공
- Agent Profile: 사용할 수 있는 Skill·Tool·Sub Agent와 업무 Context 정의
- Sub Agent: 동일한 Harness 위에서 별도 Context와 제한된 Tool 범위로 실행 가능
- Tool이 연결된 실제 시스템 권한은 Tool 또는 기존 인증체계에서 최종 확인

## 실제 동작 예시

### 사용자 요청

“지난달 운영 데이터를 분석해서 주요 이슈와 개선 의견을 정리하고 PPT로 만들어줘.”

### 처리 흐름

1. **Main Agent**가 요청을 데이터 조회·분석·의견 정리·PPT 작성으로 구분
2. **Skill**에서 분석 순서, 판단 기준, 결과 형식과 주의사항을 확인
3. 전문 분석이 필요한 경우 **분석 Sub Agent**에 해당 작업만 위임
4. **MCP**를 통해 데이터 조회 Tool, 분석 Tool, PPT 작성 Tool을 발견하고 호출
5. **Tool**이 실제 데이터 조회·분석 로직·PPT 파일 생성을 수행
6. **Harness Runtime**이 모델과 Tool 호출을 반복하고 작업 상태와 Context를 관리
7. **정책 설정과 제어 코드**가 권한·승인·Timeout·Retry·결과 검증·로그를 적용
8. **Main Agent**가 결과를 통합해 사용자에게 최종 산출물과 근거를 제공

## 최종 정리

```text
Main Agent = 사용자 요청을 이해하고 전체 작업을 조정
Sub Agent  = 필요한 경우 전문 작업을 분담
Skill      = 업무 수행 방법과 판단 기준을 설명
Tool       = 데이터 조회·분석·생성·변경을 실제 수행
MCP        = Agent와 Tool·Resource·Prompt를 연결
Harness    = Agent의 실행·권한·검증·복구·추적을 운영

Harness 구현 = Framework Runtime + 조직별 제어 코드 + 운영 정책 + Markdown/Prompt
```

---

# 사실관계 확인 및 참고자료

## 개념 검토 결과

- **Tool:** 실제 동작을 수행하는 기능이라는 이해가 맞음. 단순 함수뿐 아니라 순서·상태·예외를 포함한 완결된 복합 업무도 Tool 또는 Workflow Tool로 구현 가능.
- **MCP:** Tool 자체의 구현 형식이라기보다 Agent와 MCP Server 사이에서 Tool·Resource·Prompt를 발견하고 호출하는 Protocol.
- **Skill:** 반복 업무의 절차와 판단 기준을 Agent가 필요할 때 불러와 참고하는 구조. Agent Skills 형식은 `SKILL.md`를 필수 파일로 사용.
- **Harness:** Agent를 실제로 반복 실행하고 통제하는 Runtime. 일부 지침은 Markdown으로 관리할 수 있지만 Harness 자체가 MD 파일인 것은 아님.
- **Super Agent:** 단일한 표준 용어라기보다 Main Agent·Supervisor·Manager·Orchestrator와 유사한 상위 조정 역할을 표현한 명칭.

## 참고 문서

1. [Model Context Protocol — Architecture overview](https://modelcontextprotocol.io/docs/learn/architecture)
   - MCP Client–Server 구조와 Tool·Resource·Prompt, Tool 발견 및 호출 방식
2. [Agent Skills — Specification and documentation](https://github.com/agentskills/agentskills)
   - `SKILL.md` 필수 구조와 선택적 Script·Reference·Asset 구성
3. [Microsoft Agent Framework — Agent Harnesses](https://learn.microsoft.com/en-us/agent-framework/agents/harness)
   - Agent Loop, Context, Tool 승인, Memory, 관측성, Background Agent를 포함하는 Harness Runtime
4. [LangChain — Subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)
   - Main Agent가 Sub Agent를 호출하고 결과를 통합하는 Supervisor 구조와 Context 분리 방식
