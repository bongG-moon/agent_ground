# AI Agent 구성요소와 Harness 개념 정리

## 자료 구성

- 본문 1장: MCP·Tool·Skill·Main Agent·Sub Agent·Harness의 역할과 전체 관계 설명
- 부록 1장: Skill과 Harness의 차이, 파일 형식에 대한 오해 및 실제 동작 예시
- 대상: AI Agent 구조를 처음 접하거나 개념을 단편적으로 알고 있는 구성원
- 핵심 메시지: **Agent는 하나의 모델이 모든 일을 처리하는 구조가 아니라, 판단·절차·실행·연결·운영 역할을 조합한 시스템**

---

# 1장. AI Agent는 역할이 다른 구성요소를 연결해 업무를 수행

## 제목

**AI Agent는 판단·절차·실행·연결·운영 요소의 조합**

## 부제

각 구성요소의 역할을 구분해야 복잡한 업무도 안정적으로 연결하고 운영 가능

## 핵심 설명

LLM은 사용자의 질문을 이해하고 다음 행동을 판단할 수 있지만, 자체적으로 사내 데이터를 조회하거나 시스템을 변경하지는 못함. 실제 업무를 수행하려면 업무 절차를 알려주는 Skill, 실행 기능을 제공하는 Tool, Tool을 연결하는 MCP, 실행 전 과정을 통제하는 Harness가 함께 필요함.

복잡한 업무에서는 상위 Agent가 모든 작업을 직접 처리하기보다, 전문 영역별 Sub Agent에 일부 판단을 위임할 수도 있음. 다만 Sub Agent는 필수 구성요소가 아니며, 단순한 업무는 하나의 Main Agent가 Skill과 Tool을 직접 사용해 처리하는 편이 효율적임.

## 전체 구조

```text
┌──────────────────────────── Agent Harness ────────────────────────────┐
│                                                                      │
│  사용자 요청                                                         │
│      ↓                                                               │
│  Super / Main Agent                                                  │
│  질문 이해 · 실행계획 수립 · 작업 조정 · 결과 통합                    │
│      │                                                               │
│      ├─ Skill 참조                                                   │
│      │   어떤 순서와 기준으로 업무를 수행할지 확인                    │
│      │                                                               │
│      ├─ Sub Agent 위임 · 선택 사항                                   │
│      │   전문 판단이나 독립적인 복합 작업을 별도 Agent에 위임          │
│      │                                                               │
│      └─ Tool 선택 및 호출                                             │
│             ↓                                                        │
│       MCP Client · Tool 호출 관리                                    │
│                                                                      │
│  Harness가 실행 반복·Context·권한·승인·재시도·검증·로그를 관리         │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ MCP 표준 연결
                                ↓
                      MCP Server / Tool 제공 영역
                                ↓
                데이터 조회 · 로직 실행 · 파일/보고서 생성 · 시스템 처리
                                ↓
                         DB · API · 기존 업무시스템
```

## 구성요소별 쉬운 설명

| 구성요소 | 쉽게 말하면 | 실제 역할 |
|---|---|---|
| **Super / Main Agent** | 업무 조정자 | 사용자 요청을 이해하고 필요한 Skill·Tool·Sub Agent를 선택하며, 실행계획과 최종 결과를 통합. `Super Agent`는 공식 표준 명칭이라기보다 Main Agent·Supervisor·Orchestrator와 유사한 상위 조정 역할을 표현한 용어 |
| **Sub Agent** | 전문 담당자 | 특정 전문영역이나 독립적인 복합 작업을 담당. Main Agent와 분리된 Context와 Tool을 가질 수 있으며 결과를 Main Agent에 반환. 단순 업무에서는 불필요 |
| **Skill** | AI용 업무 절차서 | 어떤 상황에서 어떤 절차·판단 기준·주의사항·결과 형식을 적용할지 Agent에 설명. 반복 업무의 수행 방식을 일관되게 만드는 역할 |
| **Tool** | 실제 실행 기능 | 데이터 조회, 계산·분석 로직 적용, API 호출, 시스템 변경, HTML·PPT·파일 생성 등 실제 동작 수행. 단위 기능 또는 완결된 복합 업무 단위로 구현 가능 |
| **MCP** | Agent와 기능 사이의 공통 연결 방식 | Agent가 외부의 Tool·Resource·Prompt를 발견하고 호출하기 위한 Client–Server 통신 규약. Tool 자체가 아니라 Tool을 공통 방식으로 연결하는 Protocol |
| **Harness** | Agent의 실행·통제 장치 | 모델 호출과 Tool 실행을 반복하는 Agent Loop, Context·Memory, 권한·승인, 실행 횟수, Timeout·Retry, 결과 검증, 오류 처리, 로그·추적 등을 관리하는 Runtime |

## 한 줄로 구분하면

> **Main Agent는 조정하고, Skill은 방법을 설명하며, Tool은 실제로 실행함. MCP는 Tool을 연결하고, Harness는 전체 실행을 통제함. Sub Agent는 필요한 경우 전문 판단을 분담함.**

## 권장 화면 구성

- 중앙: 사용자 → Main Agent → MCP → Tool → 업무시스템으로 이어지는 실행 흐름
- Main Agent 옆: Skill과 Sub Agent를 보조 요소로 배치
- 전체 Agent 실행 영역을 큰 외곽선으로 감싸고 `Harness`로 표현
- 하단: 여섯 개 구성요소의 한 줄 정의 또는 핵심 구분 메시지 배치
- `핵심 설명` 두 문단은 발표자 설명으로 활용하고, 실제 장표에는 구조도와 구성요소별 쉬운 설명을 중심으로 배치해 과밀도 방지

---

# 부록 1. Skill은 업무 수행 방법, Harness는 실행 환경과 통제 구조

## 제목

**Skill과 Harness는 모두 Agent의 안정성을 높이지만 담당하는 역할이 다름**

## Skill과 Harness의 차이

| 구분 | Skill | Harness |
|---|---|---|
| **목적** | 특정 업무를 어떤 방식으로 수행할지 설명 | Agent가 실제로 동작하도록 실행하고 통제 |
| **적용 대상** | 모델이 참고하고 따라야 하는 업무 절차와 지식 | 모델 호출, Tool 실행, Context, 권한 및 운영 전반 |
| **주요 내용** | 적용 조건, 작업 순서, 판단 기준, 주의사항, 결과 형식 | Agent Loop, Tool Routing, Memory, 권한·승인, Timeout·Retry, 검증, 로그·추적 |
| **일반적인 형식** | Agent Skills 표준에서는 폴더 내 `SKILL.md`가 필수. 필요하면 `scripts`, `references`, `assets` 등을 함께 구성 | 공통된 단일 파일 표준은 없음. 일반적으로 Agent Framework·Orchestrator 코드와 설정으로 구현하며, 일부 운영 지침을 Markdown이나 Prompt로 포함 가능 |
| **통제 방식** | 모델이 지침을 읽고 따르는 방식 | Runtime이 실제 실행을 허용·차단하거나 재시도·중단·검증하는 방식 |
| **예시** | “데이터 확인 → 이상값 검토 → 원인 분석 → 종합의견 → 보고서 작성” 절차 정의 | 권한 없는 Tool 호출 차단, 실행시간 초과 시 중단, 실패 시 1회 재시도, 출력 Schema 검증, 실행 로그 저장 |

## “Harness도 MD 파일로 넣는 것인가?”에 대한 답변

> **일부만 맞지만, Harness 전체를 Markdown 파일이라고 보기는 어려움.**

- Agent의 공통 행동원칙이나 운영 지침은 `harness.md`, 시스템 Prompt 또는 다른 Markdown 문서로 관리할 수 있음.
- 그러나 해당 Markdown은 Harness가 참고하는 **지침 또는 설정의 일부**이며 Harness 자체는 아님.
- 권한 확인, Tool 승인, 실행 횟수 제한, Timeout, Retry, Sandbox, 입력·출력 검증, 로그와 추적은 모델의 지침 준수에만 의존하면 안정적으로 보장하기 어려움.
- 실제 시스템 연계에서는 이러한 항목을 Agent Runtime, Orchestrator, Gateway 또는 Tool 내부 코드에서 강제해야 함.
- 즉, **Skill은 Markdown 중심으로 구현 가능하지만 Harness는 Markdown + 실행 코드 + 운영 설정의 결합 구조**로 이해하는 것이 적절함.

## 실제 업무가 동작하는 예시

### 사용자 요청

“지난달 운영 데이터를 분석해서 주요 이슈와 개선 의견을 정리하고 PPT로 만들어줘.”

### 구성요소별 역할

1. **Main Agent**가 요청을 데이터 조회·분석·의견 정리·PPT 작성 작업으로 구분
2. **Skill**에서 분석 순서, 이상값 판단 기준, 종합의견 작성 기준과 결과 형식을 확인
3. 복잡한 분석이 필요한 경우 **분석 Sub Agent**에 해당 작업만 위임
4. **MCP**를 통해 데이터 조회 Tool, 분석 Tool, PPT 작성 Tool을 발견하고 호출
5. **Tool**이 실제 데이터 조회·분석 로직·PPT 파일 생성을 수행
6. **Harness**가 사용 권한, 실행 순서, Timeout·Retry, 결과 형식, 오류 처리와 실행 로그를 관리
7. **Main Agent**가 각 결과를 취합해 사용자에게 최종 보고서와 근거를 제공

## 설계 시 주의할 점

- Tool이 몇 개 없고 업무가 단순하면 Sub Agent 없이 Main Agent가 Tool을 직접 호출하는 구조가 더 단순하고 안정적임.
- Skill에 모든 제어를 맡기지 않음. 반드시 지켜야 하는 권한·검증·실행 제한은 Harness나 Tool에서 강제함.
- MCP를 사용한다고 Tool의 품질이나 보안이 자동으로 보장되는 것은 아님. MCP는 연결 규약이며 실제 권한·검증·오류 처리는 별도 설계가 필요함.
- Tool은 무조건 작은 기능으로 나누기보다, 순서·상태·예외를 포함해 하나의 업무를 안정적으로 완결할 수 있는 책임 범위로 설계 가능.
- 조직별 구현 플랫폼과 Tool 내부 로직은 달라도 Main Agent에서 발견·호출·해석할 수 있는 연결 형식은 일관되게 유지하는 방향이 적절함.

## 최종 정리

```text
Skill   = Agent에게 업무 수행 방법을 알려주는 절차와 지식
Tool    = 데이터 조회·분석·생성·변경을 실제 수행하는 기능
MCP     = Agent와 Tool·Resource·Prompt를 연결하는 공통 Protocol
Harness = Agent의 실행·권한·검증·복구·추적을 책임지는 Runtime
Main Agent = 사용자 요청을 이해하고 전체 작업을 조정하는 상위 Agent
Sub Agent  = 필요한 경우 전문 작업을 분담하는 선택적 Agent
```

---

# 사실관계 확인 및 참고자료

## 사용자가 이해한 개념에 대한 검토

- **Tool에 대한 이해:** 대체로 맞음. 실제 기능을 실행하는 요소이며, 단순 함수뿐 아니라 완결된 복합 업무나 보고서 생성 기능도 Tool로 구현 가능.
- **MCP에 대한 이해:** 대체로 맞지만 “Tool 형식”보다는 “Agent와 MCP Server 사이의 발견·호출·데이터 교환 Protocol”로 이해하는 것이 정확함. MCP는 Tool 외에도 Resource와 Prompt를 제공할 수 있음.
- **Skill에 대한 이해:** 대체로 맞음. 반복 업무의 절차와 판단 기준을 Agent가 필요할 때 불러와 참고하는 구조. 다만 Skill은 실행 통제 장치가 아니며 권한·재시도·검증을 강제하는 Harness와 구분 필요.
- **Harness가 MD 파일이라는 이해:** 일부 시스템이 Markdown 기반 운영 지침을 사용할 수는 있으나, 보편적인 `HARNESS.md` 표준은 확인되지 않음. Harness는 기본적으로 실행 Runtime과 제어 구조를 의미함.
- **Super Agent에 대한 이해:** `Super Agent`는 단일한 국제 표준 용어가 아님. 플랫폼에 따라 Main Agent, Supervisor, Manager, Orchestrator 등으로 표현되며 핵심 역할은 전문 Agent와 Tool을 조정하고 결과를 통합하는 것.

## 참고한 공식·기술 문서

1. [Model Context Protocol — Architecture overview](https://modelcontextprotocol.io/docs/learn/architecture)
   - MCP의 Client–Server 구조와 Tool·Resource·Prompt Primitive, Tool 발견 및 호출 방식 확인
2. [Agent Skills — Specification and documentation](https://github.com/agentskills/agentskills)
   - Skill이 `SKILL.md`를 필수로 하는 폴더 구조이며 scripts·references·assets를 선택적으로 포함할 수 있음을 확인
3. [Microsoft Agent Framework — Agent Harnesses](https://learn.microsoft.com/en-us/agent-framework/agents/harness)
   - Harness가 Agent Loop, Context, Tool 승인, Memory, 관측성, Background Agent 등을 포함하는 Runtime임을 확인
4. [LangChain — Subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)
   - Main Agent가 Sub Agent를 선택·호출하고 결과를 통합하는 Supervisor 구조와 Context 분리 방식 확인
