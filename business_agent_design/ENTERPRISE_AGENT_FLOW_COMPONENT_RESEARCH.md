# 사내 Agent Builder 활용 수요 조사 및 Agent Ground 개발 우선순위

> 조사일: 2026-07-11  
> 대상 환경: Agent Ground / Langflow 1.8.2 / Standalone Custom Component  
> 문서 성격: 구현 전 수요 조사와 개발 의사결정 기준  
> 주의: 이 문서의 후보는 `approved` 자산이 아니다. 구현·검증·사용자 승인 후에만 Business Agent Design 추천 카탈로그에 반영한다.

---

## 1. 결론

웹 조사와 현재 프로젝트 상태를 함께 보면, 사내 사용자가 실제로 원하는 Agent는 처음부터 모든 업무를 자율 실행하는 범용 Agent가 아니다. 수요는 다음 세 단계로 모인다.

1. **읽기 전용 Assistant**  
   사내 문서와 데이터를 찾아 근거와 함께 답하고, 비교·요약·보고서 초안을 만든다.
2. **사람과 함께 일하는 Copilot**  
   문서·메일·티켓을 분류하고 답변이나 조치안을 준비하되, 최종 판단과 발송은 사람이 맡는다.
3. **승인형 Transaction Agent**  
   휴가 신청, 티켓 갱신, 계정 요청처럼 범위와 권한이 명확한 작업만 미리보기와 승인을 거쳐 실행한다.

Agent Ground의 권장 개발 순서는 다음과 같다.

```text
현재 자산 계약 복구
→ 공통 보안·검증·Trace Component
→ 사내 문서 RAG Flow
→ 티켓/메일 분류·초안 Flow
→ 승인형 실행 Flow
→ MCP/Run Flow 기반 Agent Hub
→ 제한된 Multi-agent
```

특히 현재 `reusable_data_flow`는 manifest와 실제 Import JSON이 다른 것으로 확인됐다. 신규 Flow 구현보다 먼저 이 계약을 바로잡아야 한다. 상세 내용은 [8. 구현 전 P0 선행 정리](#8-구현-전-p0-선행-정리)에 정리했다.

---

## 2. 조사 방법과 해석 범위

다음 자료를 교차 확인했다.

- 2024~2026년 기업·직원 대상 AI/Agent 조사
- Microsoft, ServiceNow, Salesforce 등 사내 Agent 플랫폼의 실제 기능 구성
- Langflow 1.8.x 공식 문서, 공식 템플릿과 공개 GitHub 요구
- 현재 Agent Ground의 Flow, Component, registry와 실행 JSON

Langflow는 공개 텔레메트리 기반의 “가장 많이 쓰는 Component 순위”를 제공하지 않는다. 따라서 이 문서의 우선순위는 다음 신호를 합친 결과다.

1. 여러 기업 조사에서 반복되는 업무 수요
2. Agent 제품에서 공통으로 제공하는 기능
3. Langflow 공식 템플릿과 커뮤니티 요구
4. Agent Ground에서 이미 구현된 자산의 재사용 가능성
5. Langflow 1.8.2에서의 구현 가능성과 운영 위험

공급사가 공개한 고객 성과 수치는 동일한 성과를 보장하는 값이 아니다. 기능 패턴과 우선순위를 판단하는 참고 근거로만 사용한다.

조사별 표본도 다르다. Microsoft 자료는 대규모 업무 사용자를 포함하지만 일부 지표는 자기보고이고, LangChain 2026 조사는 응답자의 63%가 기술 업종이어서 일반 사무직 전체의 절대 비율로 해석하면 안 된다. 또한 현재 환경에 맞춘 Langflow 1.8.x 문서는 더 이상 유지보수되는 최신 문서가 아니므로, 호환성 확인에는 사용하되 최신 보안 개선이 자동으로 포함된다고 가정하지 않는다.

---

## 3. 사람들이 실제로 AI와 Agent를 사용하는 업무

### 3.1 최근 조사에서 반복되는 수요

| 근거 | 확인된 수요 | Agent Ground에 주는 의미 |
| --- | --- | --- |
| Microsoft 2026, Microsoft 365 Copilot 대화 10만 건 이상 분석 | 사용 목표의 49%가 분석·문제 해결·평가 같은 인지 업무였고, 19%는 사람과 협업, 17%는 결과물 작성, 15%는 정보 탐색이었다. AI 사용자의 86%는 AI 출력을 최종 답이 아닌 시작점으로 본다. | 분석·검색·초안 Flow와 사람 검토가 가장 넓게 재사용된다. |
| LangChain 2026, 1,340명 조사 | 주 사용 사례는 고객 서비스 26.5%, 조사·데이터 분석 24.4%, 내부 Workflow 자동화 18%였다. 1만 명 이상 기업은 내부 생산성이 26.8%로 가장 높았다. | 고객 응대뿐 아니라 사내 업무 지원과 분석 Flow가 핵심이다. |
| Microsoft 2025 Work Trend Index | 리더의 46%가 Agent로 업무 흐름 또는 프로세스를 자동화하고 있다고 답했으며, 주요 투자 영역은 고객 서비스, 마케팅, 제품 개발이었다. | 도메인별 Agent보다 공통 분류·검색·초안·승인 Component가 먼저 필요하다. |
| McKinsey 2025 | 조직의 71%가 최소 한 업무 기능에서 생성형 AI를 정기적으로 사용했다. 사용이 많은 기능은 마케팅·영업, 제품·서비스 개발, 서비스 운영, 소프트웨어 개발과 IT였다. | 한 부서 전용 기능보다 문서·데이터·서비스 요청을 처리하는 공용 기반이 유리하다. |
| PwC 2025, 미국 임원 300명 조사 | Agent 도입 기업 중 생산성 가치가 있다고 답한 비율은 66%였다. 데이터 분석에 대한 신뢰는 38%였지만 금융 거래는 20%, 자율적 직원 상호작용은 22%였다. | 읽기·분석은 빠르게 시작하고, 쓰기·거래·인사 행동에는 승인과 최소 권한을 강제한다. |

출처:

- [Microsoft 2026 Work Trend Index](https://www.microsoft.com/en-us/worklab/work-trend-index/agents-human-agency-and-the-opportunity-for-every-organization)
- [LangChain 2026 State of Agent Engineering](https://www.langchain.com/state-of-agent-engineering)
- [Microsoft 2025 Work Trend Index](https://blogs.microsoft.com/blog/2025/04/23/the-2025-annual-work-trend-index-the-frontier-firm-is-born/)
- [McKinsey, The state of AI: How organizations are rewiring to capture value](https://www.mckinsey.com/capabilities/quantumblack/our-insights/the-state-of-ai-how-organizations-are-rewiring-to-capture-value)
- [PwC AI Agent Survey](https://www.pwc.com/us/en/tech-effect/ai-analytics/ai-agent-survey.html)

### 3.2 실제로 만들고 싶어 하는 업무 유형

#### A. 사내 지식 검색과 근거형 Q&A

- 사규, 업무 매뉴얼, 기술 문서, FAQ, 과거 사례 검색
- 긴 문서 요약과 여러 문서 비교
- 답변에 출처, 문서명, 페이지, 링크 표시
- 근거가 부족하면 추측하지 않고 추가 자료 요청
- 사용자·부서·직급별 접근 권한 반영

Langflow 공식 RAG 튜토리얼도 `문서 적재 Flow`와 `검색·답변 Flow`를 분리한다. 이 분리는 문서를 한 번 적재해 여러 Agent가 재사용하는 사내 운영 방식과 잘 맞는다. [Langflow 1.8 Vector RAG 튜토리얼](https://docs.langflow.org/1.8.0/chat-with-rag)

#### B. 조사·분석·보고서 작성

- 자연어 질문을 데이터 조회 단계로 변환
- DB, API, 파일을 함께 조회
- KPI 계산, 비교, 이상값 확인
- 회의용 요약, 표, 차트, HTML 보고서 생성
- 기준일, 필터, 사용 데이터와 Query 근거 표시
- 후속 질문에 앞선 분석 결과 재사용

이 영역은 현재 `reusable_data_flow`와 `html_report_flow`가 목표로 하는 영역이다. 새로운 보고서 Flow를 중복 구현하기보다 두 Flow의 실제 계약을 복구하고 조합하는 편이 우선이다.

#### C. 문서 접수·분류·구조화

- PDF, DOCX, 이메일, 이미지, CSV 접수
- 계약서, 인보이스, 신청서, 보고서 유형 분류
- 문서에서 필요한 필드와 표 추출
- JSON Schema 검증과 누락 필드 표시
- 저신뢰 결과를 사람 검토 Queue로 전달

Langflow의 Structured Output은 비정형 입력을 JSON/Table로 바꾸는 용도로 공식 문서화되어 있고, 공식 템플릿에도 계약서와 문서 정보 추출 사례가 반복된다. [Structured Output 1.8](https://docs.langflow.org/1.8.0/structured-output), [Document Data Intelligence](https://www.langflow.org/templates/use-langflow-to-extract-structured-data-from-documents)

#### D. 티켓·메일·상담 요청 분류와 답변 초안

- 이메일, 채팅, 폼, Webhook 입력 통합
- 유형, 긴급도, 감정, SLA 위험, 담당팀 분류
- 과거 티켓, 고객 이력, 정책 검색
- 답변·해결 노트·지식문서 초안 생성
- 중복 티켓과 불충분한 요청 탐지
- 고위험·저신뢰 요청을 담당자에게 전체 문맥과 함께 이관

고객 서비스는 Agent 조사에서 가장 흔한 단일 사용 사례였지만, 사내 환경에서는 “고객에게 자동 답변”보다 상담원이나 업무 담당자에게 근거와 초안을 제공하는 방식이 먼저 적용하기 안전하다.

#### E. 회의·협업 업무 보조

- 회의 전 참석자·관련 프로젝트·최근 이력 Briefing
- 회의록 요약, 결정 사항, Action Item, 담당자, 기한 추출
- 이메일 Thread와 첨부 문서 요약
- 공유용 초안 작성과 사람 확인
- 완료되지 않은 Action Item 후속 알림

#### F. 승인형 업무 실행

- 휴가·증명서·출장·계정·소프트웨어 신청
- 티켓 상태 갱신과 담당자 변경
- 승인된 메일 또는 공지 발송
- 제한된 CRM·업무시스템 Record 갱신
- 정해진 조건의 알림과 후속 작업

이 영역은 Agent가 바로 실행하는 구조가 아니라 `현재 상태 조회 → 변경안 미리보기 → 위험 평가 → 승인 → 실행 → 실행 결과 확인 → Audit` 순서가 되어야 한다.

#### G. IT·운영·이상 대응

- Incident 요약, 과거 유사 사례와 Runbook 검색
- 장애 유형·영향·우선순위 분류
- 로그·모니터링 결과 요약
- 복구 절차 추천과 실행 전 승인
- 실패 시 사람 이관과 재처리 목록 생성

#### H. 개발·QA Agent

- 코드 설명, 변경 영향 분석, 테스트 생성
- 문서·코드베이스 검색
- 오류 재현 절차와 수정 초안
- PR·배포 검사와 실패 요약

LangChain 조사에서는 Coding Agent가 일상적으로 가장 많이 언급된 Agent였다. 다만 Agent Ground의 핵심 사용자는 비개발자도 포함하므로, 공용 플랫폼의 1차 Flow보다는 전문 부서용 후속 Flow로 분류한다.

---

## 4. 실제 사내 Agent 서비스가 공통으로 갖는 기능

Microsoft Copilot Studio, ServiceNow AI Agents, Salesforce Agentforce와 Langflow의 공식 기능을 비교하면 다음 계층이 반복된다.

| 계층 | 반복되는 기능 | Agent Ground 구현 방향 |
| --- | --- | --- |
| 입력·Trigger | Chat, 파일, Form, Webhook, Schedule, 업무시스템 Event | 입력별 Component를 만들되 공통 request envelope로 즉시 변환 |
| 지식·Context | 문서 색인, RAG, 실시간 API 조회, 대화 상태, 사용자 Context | 정적 지식 검색과 실시간 Record 조회를 분리 |
| Tool·Action | Connector, API, DB, MCP, 하위 Flow, deterministic workflow | 조회 Tool과 변경 Tool을 다른 catalog와 위험 등급으로 관리 |
| 이해·계획 | 의도 분류, Router, 단계 계획, Tool 선택, 구조화 추출 | LLM 결과를 다음 단계 전에 Schema Validator로 검증 |
| 사람 협업 | 승인, 추가 정보 요청, 담당자 이관, 전체 문맥 전달 | Agent 판단만으로 외부 발송·쓰기 작업을 실행하지 않음 |
| 권한·보안 | 사용자 인증, 역할, ACL, 최소 권한, PII, Guardrail | 요청의 사용자 권한보다 Tool 권한이 넓어지지 않게 필터링 |
| 운영 | Trace, Audit, Eval, Feedback, 비용·지연·실패율, Agent lifecycle | 모든 Flow가 공통 trace envelope와 평가 결과를 남김 |
| 배포 | Teams/Chat, API, MCP Tool, 웹, 업무시스템 | 한 Flow를 여러 채널에 재사용할 수 있는 입력·출력 adapter 제공 |

공식 기능 근거:

- Microsoft Copilot Studio는 Connector, MCP Server, 반복 가능한 deterministic workflow를 서로 다른 Tool 유형으로 구분한다. [Available tools for agents](https://learn.microsoft.com/en-us/microsoft-copilot-studio/agents-experience/tools-available)
- Microsoft의 지식 Connector는 원본 접근 권한을 존중하며, 별도의 실시간 Connector는 Record 조회와 변경 Action에 사용된다. [Copilot connectors as knowledge](https://learn.microsoft.com/en-us/microsoft-copilot-studio/knowledge-copilot-connectors)
- Copilot Studio의 Agent Flow 예시는 티켓 처리, 회의 Briefing, 비용 검토에서 Tool·Knowledge·Human escalation을 함께 사용한다. [Agent node in a workflow](https://learn.microsoft.com/en-us/microsoft-copilot-studio/agent-node-workflow)
- ServiceNow는 Role, Tool, Data, Agentic Workflow, Orchestrator와 Control Tower를 구분하고 IT·HR·CRM·Risk 업무에 적용한다. [ServiceNow AI Agents](https://www.servicenow.com/products/ai-agents.html)
- Salesforce는 Guardrail, Human handoff, Audit Trail과 Agent/User 권한을 핵심 통제로 둔다. [Agent trust and guardrails](https://help.salesforce.com/s/articleView?id=000372738&language=en_US&type=3), [Agentforce security](https://help.salesforce.com/s/articleView?id=005315874&language=en_US&type=1)

이 비교에서 중요한 점은 “Agent 하나에 Tool을 많이 연결하는 것”이 완성된 사내 서비스가 아니라는 것이다. 실제 서비스는 입력, 권한, 구조 검증, 승인, 실행, Trace와 평가를 함께 제공한다.

---

## 5. Agent Ground의 현재 강점

### 5.1 이미 있는 공용 자산

| 영역 | 현재 자산 | 재사용 가치 |
| --- | --- | --- |
| 데이터 요청 표준화 | `data_request_normalizer` | 자연어 요청을 데이터 조회 계약으로 바꾸는 출발점 |
| 사내 데이터 Source | `oracle_data`, `h_api_data`, `datalake_data`, `goodocs_data` | Source별 조회 Component 원형 |
| 결과 통합 | `data_result_merger`, `data_output_builder`, `html_report_datasets_adapter` | 여러 Source 결과를 공통 datasets로 연결 |
| Source catalog | `source_catalog_normalizer`, MongoDB store/loader | Source 정보를 코드와 분리해 관리하는 기반 |
| HTML Report | 데이터 Profile, 요소 Catalog, Plan Builder/Normalizer, deterministic Renderer | 데이터 결과를 서비스 수준 HTML로 변환 |
| 결과 공유 | `report_api_publisher`와 공용 Report API | HTML 결과를 Link로 전달 |
| Business 설계 | 한 칸 업무 입력, catalog 검색, BEFORE/AFTER graph, human review, 안전한 HTML | 업무 설명에서 구현안을 만드는 상위 서비스 |

기준 파일:

- [`registry/capabilities.json`](../registry/capabilities.json)
- [`flows/reusable_data_flow/manifest.json`](../flows/reusable_data_flow/manifest.json)
- [`flows/html_report_flow/manifest.json`](../flows/html_report_flow/manifest.json)
- [`business_agent_design/manifest.json`](manifest.json)

### 5.2 교육 예제에서 승격할 수 있는 원형

다음 파일은 공용 Component 후보의 출발점으로 사용할 수 있다. 바로 registry에 올리지 않고 manifest, contract test, HTML 설명서와 사용자 검증을 추가한 뒤 승격한다.

| 원형 | 활용 후보 |
| --- | --- |
| `html/training/examples/company_payload_normalizer.py` | 공통 요청 Context |
| `html/training/examples/internal_api_retriever.py` | 범용 사내 API Connector |
| `html/training/examples/file_dataset_loader.py` | 문서·파일 입력 |
| `html/training/examples/smart_route_payload_builder.py` | Router 결정 정규화 |
| `html/training/examples/route_gate.py` | 실제 branch gate |
| `html/training/examples/run_flow_payload_adapter.py` | 하위 Flow Tool 경계 |
| `html/training/examples/safe_dataframe_profiler.py` | LLM 전송 전 안전한 데이터 요약 |
| `html/training/examples/pdf_page_image_extractor.py` | PDF·멀티모달 전처리 |
| `html/training/examples/multimodal_milvus_chunk_builder.py` | 문서 Chunk와 Vector metadata |

---

## 6. 현재 빠져 있는 공통 기능

현재 프로젝트는 `데이터 조회 → HTML 리포트 → 업무 Agent 설계` 축은 강하지만 다음 기능이 없다.

### 입력·지식

- PDF, DOCX, 이메일, 이미지의 공통 Document envelope
- 문서 ID, 페이지, 버전, 보안 등급이 있는 Chunk metadata
- Incremental index와 stable document/chunk ID
- 사용자 권한을 반영한 Retrieval filter
- 근거 점수, 원문 위치와 Citation builder

### 통합·실행

- 범용 REST API Tool
- Webhook payload normalizer
- Schedule/Event trigger 표준
- MCP/Run Flow 결과 adapter
- Retry, timeout, rate limit, circuit breaker
- Idempotency key와 부분 실패·재처리 계약

### 보안·사람 통제

- PII·기밀정보 탐지와 마스킹
- 사용자 Context와 Tool permission filter
- 실제 승인 요청 저장, 승인/반려와 만료
- 승인 후 별도 Flow로 안전하게 재개하는 구조
- 담당자 Handoff와 전체 문맥 전달

### 운영·품질

- 공통 Audit event
- Flow 실행 Trace 요약
- 평가 dataset과 회귀 테스트
- 사용자 feedback·전문가 검수 Queue
- 비용·지연·실패율·완료율 Dashboard
- Prompt/Model/Component/Flow version 기록

---

## 7. 권장 Flow 우선순위

### P0. 기반 정상화와 공통 통제

새 업무 Flow보다 먼저 다음을 처리한다.

1. `reusable_data_flow` 실제 JSON과 manifest 계약 일치
2. 데이터 Source Component의 `mock/live` 실행 모드 분리
3. 공통 요청 Context, Schema Validator, PII Guard, Approval, Trace Component
4. 대표 입력, 오류, timeout, 권한 실패 contract test
5. 사용자 확인을 통과한 최소 자산의 `approved` 전환

### P1. 수요가 크고 위험이 낮은 Flow

| 우선순위 | Flow 후보 | 핵심 업무 | 기존 자산 재사용 | 위험 |
| ---: | --- | --- | --- | --- |
| 1 | `enterprise_document_rag_flow` | 사내 문서 검색, 비교, 근거형 답변 | 파일·멀티모달 교육 원형, LLM, Business catalog | 읽기 전용, ACL과 근거 품질 필요 |
| 2 | `ticket_mail_triage_flow` | 요청 분류, 긴급도, 담당팀, 답변 초안 | Router 원형, LLM, Source catalog | 외부 발송 없이 초안까지만 |
| 3 | `enterprise_data_insight_flow` | 자연어 데이터 조회와 HTML 보고 | `reusable_data_flow` + `html_report_flow` | 기존 P0 계약 복구 후 구현 |
| 4 | `research_brief_flow` | 여러 자료 조사·요약·비교와 Briefing | RAG, API, HTML Report | 출처와 최신성 검증 필요 |
| 5 | `meeting_action_flow` | 회의록, 결정, 담당자, 기한 추출 | Document intake, Structured Output | 알림·등록은 승인 후 추가 |

### P2. 사람과 함께 처리하는 업무 Flow

| Flow 후보 | 설명 | 필수 통제 |
| --- | --- | --- |
| `document_intake_extraction_flow` | 계약서·신청서·인보이스 접수, 필드 추출, 검토 | Schema Validator, PII, 저신뢰 Review |
| `internal_service_desk_flow` | IT/HR 정책 Q&A, 티켓 분류, 해결 노트 초안 | ACL, Human handoff, SLA trace |
| `knowledge_article_lifecycle_flow` | 해결 이력에서 문서 후보 생성, 검수, 게시 | 중복 검색, 소유자 승인, 버전 |
| `policy_change_review_flow` | 규정 버전 비교, 변경점과 영향 요약 | 원문 Citation, 법무/담당자 검토 |
| `incident_response_copilot_flow` | Incident·로그 요약, Runbook 검색, 조치안 | 읽기 우선, 실행 승인, Audit |

### P3. 승인형 Transaction Flow

| Flow 후보 | 가능한 작업 | 반드시 필요한 조건 |
| --- | --- | --- |
| `human_approval_action_flow` | 다른 Flow의 변경안을 승인 후 실행 | 승인 저장소, 요청 hash, 만료, 실행 확인 |
| `employee_request_action_flow` | 휴가·증명서·계정 요청 | 사용자 인증, 역할, 승인자, 상태 조회 |
| `ticket_update_action_flow` | 담당자·상태·해결 노트 갱신 | 대상 Record 미리보기, idempotency |
| `approved_message_delivery_flow` | 승인된 메일·메신저 초안 발송 | 수신자·내용 미리보기, 승인 이력 |

### P4. 기반이 갖춰진 뒤 검토할 Flow

- 여러 부서 Agent가 자유롭게 협업하는 범용 Multi-agent
- 장기 실행형 자율 Research Agent
- 장기 Memory 기반 선제 실행 Agent
- 사람 승인 없는 외부 발송, DB 수정·삭제, 금전·법무·인사 결정

Multi-agent 자체는 1차 목표가 아니다. 먼저 Router, Tool 계약, 권한, 승인, Trace, 평가가 완성되어야 한다.

---

## 8. 구현 전 P0 선행 정리

### 8.1 `reusable_data_flow` JSON과 manifest 불일치

`flows/reusable_data_flow/manifest.json`은 Oracle, H-API, Datalake, Goodocs를 조회해 결과를 합치는 Flow라고 설명한다. 그러나 `flows/reusable_data_flow/reusable_data_flow.json`의 실제 node에는 다음 과거 업무 설계 Component가 포함되어 있다.

- `BusinessWorkInputLoader`
- `WorkProcessStructurer`
- `AgentCapabilityCatalog`
- `AgentDesignPromptBuilder`
- `AgentDesignNormalizer`

Flow JSON의 이름도 `업무분석flow`다. 따라서 현재 프로젝트 전체 Bundle의 첫 Flow 역시 manifest가 설명하는 데이터 조회 Canvas가 아니다.

조치 기준:

1. 12개 데이터 Flow 내부 Node를 실제로 연결한 Langflow 1.8.2 JSON 재생성
2. manifest, `internal_nodes.json`, JSON node class와 port 자동 대조
3. 대표 자연어 요청으로 Oracle/H-API/Datalake/Goodocs branch 확인
4. Project Bundle 재생성

### 8.2 데이터 Source 내부 Node가 항상 dummy 결과를 반환

현재 4개 Source 내부 Node에는 `_dummy_rows`가 항상 존재하고 실행 함수가 `callable(dummy_builder)`를 확인한다. 따라서 실제 연결 설정을 넣어도 dummy branch가 먼저 실행된다. Oracle의 실제 실행 코드는 주석 상태이고 다른 Source도 같은 패턴이다.

조치 기준:

- 화면에서 명시적으로 보이는 `execution_mode = mock | live` 추가
- 기본값은 `mock`
- `live`는 자격증명, endpoint, timeout, retry와 허용 범위를 검증
- 결과에 `execution_mode`, 실제/가상 Source, 실행 시간 기록
- mock 성공을 live 검증으로 오인하지 않게 HTML과 status에 표시

### 8.3 `approved` 자산이 0개

현재 registry의 Flow 2개와 Component 21개는 모두 `user_testing`이다. Business Agent Design은 `approved` 항목만 추천하므로 현재 로컬 핵심 자산을 추천할 수 없다.

조치 기준:

1. 한 자산군씩 실제 Langflow에서 검증
2. 대표 입력과 오류 조건을 통과한 자산만 사용자 승인
3. manifest와 registry 상태 변경
4. Registry → Business catalog/MongoDB 동기화
5. 관련 HTML와 교육자료 링크 갱신

### 8.4 테스트와 Report API 접근 통제

- 자동 기능 테스트는 Business Agent Design 영역에 집중돼 있다. 현재 기능 단위 Component Library 20개도 각 자산 위험에 맞는 정상·빈 입력·인증 실패·timeout 테스트를 유지해야 한다.
- Report API의 `USE_ACCESS_TOKEN` 기본값은 현재 `False`다. 사내 공유 범위, URL 노출 가능성, 보관 정책을 확정하기 전 외부 배포 기본값으로 사용하면 안 된다.

---

## 9. 먼저 만들 공용 Standalone Component

### P0 공통 Component

| Component ID 후보 | 역할 | 핵심 출력·검증 |
| --- | --- | --- |
| `request_context_normalizer` | 사용자, 부서, 역할, session, request ID 표준화 | `request_context`, 누락·위조 가능 필드 표시 |
| `document_input_normalizer` | File/Message/Data/DataFrame을 공통 문서 계약으로 변환 | 문서 ID, mime, page, source, version |
| `structured_output_contract_validator` | LLM JSON/Table의 필드·타입·허용값 검증 | `valid`, `errors`, `normalized_data` |
| `enterprise_api_tool` | 사내 REST API 읽기/쓰기 공통 처리 | timeout, retry, HTTP status, safe error |
| `webhook_payload_normalizer` | `data`, `payload`, `value` wrapper와 빈 실행 처리 | 표준 event envelope |
| `retry_fallback_handler` | retry, backoff, 대체 처리, 최종 실패 | 시도 횟수와 fallback reason |
| `audit_event_builder` | 누가 어떤 Tool을 어떤 인자로 실행했는지 기록 | 비밀값 제거된 audit event |
| `trace_summary_builder` | node별 지연, 실패, token/cost 정보를 요약 | 운영용 trace summary |

### P1 검색·근거 Component

| Component ID 후보 | 역할 | 필수 규칙 |
| --- | --- | --- |
| `chunk_metadata_builder` | 문서·페이지·버전·보안 등급 metadata 생성 | stable document/chunk ID |
| `incremental_rag_indexer` | 변경 문서만 upsert, 삭제/비활성화 반영 | 중복 적재 방지 |
| `retriever_with_evidence` | 점수, 출처, 원문 위치 포함 검색 | ACL filter 선적용 |
| `retrieval_quality_gate` | 근거 수와 점수가 낮으면 답변 차단 | 근거 부족 응답과 질문 생성 |
| `citation_response_builder` | 답변과 출처 목록 표준화 | 인용된 내용과 Source 연결 |
| `safe_sql_query_tool` | 자연어 SQL의 읽기 전용 실행 | 허용 schema, row limit, 금지 구문 |

### P1 승인·보안 Component

| Component ID 후보 | 역할 | 필수 규칙 |
| --- | --- | --- |
| `pii_confidential_data_guard` | PII·기밀값 탐지, 마스킹, 차단 | 원문 재출력 금지 |
| `tool_permission_filter` | 사용자 역할과 위험도에 따라 Tool 노출 제한 | 요청 사용자 권한보다 넓지 않음 |
| `approval_request_gate` | 부작용 작업을 실행하지 않고 승인 요청 생성 | 대상, 인자, diff, 위험, 만료, hash |
| `approval_resume_verifier` | 승인자, 만료, hash와 인자 위변조 확인 | 승인된 요청만 실행 Flow로 전달 |
| `idempotency_guard` | 중복 Webhook·업무 요청 실행 방지 | 동일 key의 결과 재사용 또는 차단 |
| `human_handoff_builder` | 담당자에게 요청·근거·대화·실패를 전달 | 사람이 바로 이어서 처리 가능한 package |

### P1 평가·Feedback Component

| Component ID 후보 | 역할 | 핵심 지표 |
| --- | --- | --- |
| `response_quality_evaluator` | 정확성·근거성·정책·표현 평가 | 점수와 실패 이유 분리 |
| `evaluation_case_runner` | 고정 test set 회귀 실행 | Flow/Prompt/Model version 기록 |
| `feedback_collector` | 사용자 평가와 수정 답 수집 | 민감정보 제거, 관련 run ID 유지 |
| `batch_result_aggregator` | 성공·실패·skip·재처리 목록 집계 | 부분 성공을 전체 성공으로 표시하지 않음 |

구현 원칙은 모두 동일하다.

- 파일 하나로 등록 가능한 Standalone
- 형제 모듈 import 금지
- 정상 결과와 오류 결과를 같은 envelope로 반환
- 자격증명과 원문 PII를 status·trace·HTML에 기록하지 않음
- 화면에서 사용자가 만지는 Boolean은 `BoolInput` Toggle 사용
- Input/Output contract와 대표 실패 예시를 HTML 설명서에 포함

---

## 10. 첫 대표 Flow의 상세 권고

### 10.1 첫 번째: `enterprise_document_rag_flow`

가장 넓은 부서에 적용되고 읽기 전용으로 시작할 수 있으며, 현재 프로젝트에서 비어 있는 Document/RAG/Citation/ACL 기반을 한 번에 검증할 수 있다.

```text
문서 입력/API
→ Document Input Normalizer
→ PII/기밀 검사
→ 문서·페이지·버전·ACL metadata
→ Chunk/Embedding/Vector upsert
→ 사용자 질문 + Request Context
→ ACL filter Retrieval
→ Retrieval Quality Gate
→ 답변 생성
→ Citation Response Builder
→ Output Contract Validator
→ Feedback + Trace
```

완료 기준:

- 근거가 없는 질문은 추측하지 않는다.
- 모든 사실 답변은 문서명과 페이지 또는 원문 위치를 제공한다.
- 권한 없는 문서의 존재나 내용이 답변·trace에 노출되지 않는다.
- 같은 문서를 다시 넣어도 중복 Chunk가 생기지 않는다.
- 문서 버전 변경과 삭제가 검색 결과에 반영된다.

### 10.2 두 번째: `ticket_mail_triage_flow`

```text
Webhook/메일/폼
→ Webhook Payload Normalizer
→ Request Context
→ PII Guard
→ 유형·긴급도·위험도 Structured Classification
→ 정책·과거 사례 Retrieval
→ 답변·처리안 초안
→ Quality Gate
→ 자동 완료 / 사람 이관 분기
→ Audit + Feedback
```

첫 버전은 외부 발송이나 티켓 갱신을 하지 않는다. 정확한 분류, 초안과 Handoff package까지만 제공한다.

### 10.3 세 번째: `human_approval_action_flow`

Langflow 1.8.2에서는 장시간 중단 후 재개하는 범용 승인 엔진을 핵심 기능만으로 가정하지 않는다. 한 실행을 계속 대기시키기보다 두 Flow로 나눈다.

```text
[요청 Flow]
현재 상태 조회 → 변경안/dry-run → Risk → Approval Request 저장 → 승인 링크/ID 출력

[실행 Flow]
승인 Event/Webhook → 승인자·만료·hash 확인 → Idempotency → Action 실행
→ 실행 후 상태 재조회 → Audit → 결과 통지
```

완료 기준:

- 승인 전 Action 호출 0건
- 승인 대상과 실제 실행 인자 100% 일치
- 동일 승인 ID 중복 실행 0건
- 실패 시 수동 복구 안내와 원인 기록
- 외부 발송·쓰기 작업의 실행자와 승인자 추적 가능

---

## 11. 공통 데이터 계약 제안

새 Component가 서로 다른 임의 key를 만들지 않도록 다음 공통 envelope를 기준으로 한다.

```json
{
  "request": {
    "request_id": "uuid",
    "raw_input": "사용자 원문 또는 event 요약",
    "received_at": "ISO-8601",
    "channel": "chat|file|webhook|schedule"
  },
  "actor_context": {
    "user_id": "verified identity",
    "department": "string",
    "roles": ["role"],
    "session_id": "string"
  },
  "artifacts": [],
  "evidence": [],
  "proposed_actions": [],
  "risk": {
    "level": "R0|R1|R2|R3|R4",
    "reasons": [],
    "human_review_required": true
  },
  "approval": {
    "status": "not_required|pending|approved|rejected|expired",
    "request_hash": "string"
  },
  "execution": {
    "status": "not_started|running|partial|completed|failed",
    "attempts": [],
    "errors": []
  },
  "trace": {
    "flow_id": "string",
    "flow_version": "string",
    "component_versions": {},
    "started_at": "ISO-8601",
    "finished_at": "ISO-8601"
  }
}
```

### Action 위험 등급

| 등급 | 예시 | 기본 처리 |
| --- | --- | --- |
| `R0` | 요약, 분류, 내부 초안 | 자동 가능, 결과 검토 권장 |
| `R1` | 권한 범위 내 읽기 조회 | 자동 가능, Source와 Trace 필수 |
| `R2` | 되돌릴 수 있는 내부 Record 갱신 | 미리보기와 명시적 승인 |
| `R3` | 외부 발송, 인사·권한 변경, 고객 영향 작업 | 지정 승인자, 강한 Audit, 재확인 |
| `R4` | 삭제, 금전 거래, 법적 결정, 안전 관련 자동 판단 | 공용 Flow 자동 실행 금지, 별도 통제 설계 |

---

## 12. Langflow 1.8.2 호환성 기준

현재 환경에서 공식적으로 확인되는 기능:

- Agent와 Tool Mode
- Agent가 API·DB·검색·하위 Tool을 호출하는 구조
- MCP Client/Server와 Flow의 MCP Tool 노출
- Read File, Vector RAG, Structured Output
- SQL Database와 자연어 SQL 구성
- Webhook, If-Else, Loop, Message History
- Guardrails, Trace, Knowledge Base
- Standalone Custom Component

관련 공식 문서:

- [Langflow 1.8 Agent](https://docs.langflow.org/1.8.0/agents)
- [Langflow 1.8 MCP Server](https://docs.langflow.org/1.8.0/mcp-server)
- [Langflow 1.8 Structured Output](https://docs.langflow.org/1.8.0/structured-output)
- [Langflow 1.8 SQL Database](https://docs.langflow.org/1.8.0/sql-database)
- [Langflow 1.8 Read File](https://docs.langflow.org/1.8.0/read-file)
- [Langflow 1.8 Release Notes](https://docs.langflow.org/1.8.0/release-notes)

주의할 기능:

- 최신 Langflow 1.10 문서의 `Policies`, 새로운 Router·Transform·Memory 기능을 1.8.2에 있다고 가정하지 않는다.
- 최신 공식 템플릿 JSON을 1.8.2에 그대로 Import하지 않고 개념만 참고하여 1.8.2 node schema로 다시 만든다.
- 범용 승인 대기·재개는 Standalone Component와 외부 저장소/Webhook을 사용해 두 Flow로 구성한다.
- MCP Flow는 Tool 이름, 설명, Input contract가 명확해야 한다. 1.8 공식 문서도 모호한 Tool 이름이 잘못된 Tool 선택을 유발할 수 있다고 설명한다.
- 모든 생성 JSON은 프로젝트에서 검증한 `œ` handle delimiter와 UTF-8 BOM 없는 형식을 유지한다.

---

## 13. 평가와 승인 기준

### 공통 운영 지표

| 지표 | 설명 |
| --- | --- |
| Task completion rate | 사용자가 원한 업무 결과에 도달한 비율 |
| Human override rate | 사람 수정·반려·재처리 비율 |
| Grounded answer rate | 근거로 뒷받침된 사실 답변 비율 |
| Unsafe action count | 승인·권한을 우회한 실행 건수, 목표는 0 |
| p50/p95 latency | 일반·최악 응답 지연 |
| Cost per successful task | 성공 업무 한 건당 모델·Tool 비용 |
| Trace coverage | LLM·검색·Tool·승인의 기록 완전성 |
| User time saved | 기존 업무 대비 절감 시간 |

### Flow별 대표 기준

| Flow | 반드시 측정할 항목 |
| --- | --- |
| 문서 RAG | Citation 정확성, ACL 누출 0건, 근거 부족 Abstention |
| 문서 추출 | 필드 정확도, 누락률, 사람 수정률 |
| 티켓 분류 | 유형·긴급도 F1, 담당팀 정확도, Handoff 성공률 |
| 데이터 분석 | Query/필터 정확성, 수치 재현성, 기준일 표시 |
| 승인형 Action | 무승인 실행 0건, 중복 실행 0건, 승인-실행 인자 일치 |
| Report | 데이터와 화면 수치 일치, 링크 접근 통제, 만료·삭제 |

자산 승인 단계:

```text
idea
→ building
→ local contract test
→ user_testing
→ 실제 Agent Builder 대표 시나리오 검증
→ 사용자 완료 승인
→ approved
→ Business Agent Design catalog 반영
```

---

## 14. 단계별 개발 제안

### Phase 0. 현재 계약 복구

- `reusable_data_flow` JSON 재생성
- Source Component mock/live 분리
- 공용 Component 기능 테스트 추가
- Report API 접근 정책 확정

### Phase 1. 공통 제어 Component

- Request Context
- Output Schema Validator
- PII/기밀 Guard
- Retry/Fallback
- Audit/Trace
- Retrieval Evidence/Citation

### Phase 2. 읽기 전용 대표 Flow

- `enterprise_document_rag_flow`
- `ticket_mail_triage_flow`의 분류·초안 범위
- 기존 데이터·리포트 Flow의 합성 예제

### Phase 3. 승인형 실행

- Approval Request/Resume
- Tool Permission
- Idempotency
- Safe API Executor
- Human Handoff

### Phase 4. 운영과 확장

- 평가 dataset과 회귀 Test
- Feedback/전문가 검수 Queue
- 비용·지연·성공률 Dashboard
- MCP/Run Flow Tool catalog
- 승인된 Flow만 사용하는 Agent Hub

### Phase 5. 제한된 Multi-agent

- 단일 Agent/Router로 해결하기 어려운 업무만 대상
- Agent별 책임, Tool, 예산, timeout, handoff를 명시
- 전체 실행 Trace와 비상 중지 제공

---

## 15. 최종 권고

Agent Ground의 다음 구현 단위는 새로운 결과 화면이 아니라 **사내 Agent의 공통 안전 기반**이어야 한다.

가장 현실적인 순서는 다음과 같다.

1. 현재 `reusable_data_flow`의 JSON·실행 계약을 복구한다.
2. Request Context, Schema Validator, PII Guard, Evidence, Trace를 공용 Standalone Component로 만든다.
3. 첫 대표 Flow로 `enterprise_document_rag_flow`를 구현한다.
4. 같은 기반으로 `ticket_mail_triage_flow`를 만들되 초안과 Handoff까지만 자동화한다.
5. 실제 Approval 저장·재개 구조가 준비된 뒤에만 외부 발송과 시스템 쓰기를 추가한다.
6. 각 Flow가 사용자 검증을 거쳐 `approved`가 되면 Business Agent Design 추천 catalog에 자동 반영한다.

이 순서는 현재 웹 조사에서 확인된 사용 수요, 기업 Agent 제품의 공통 기능, Langflow 1.8.2의 구현 가능성, Agent Ground의 기존 자산을 동시에 만족한다.

---

## 16. 주요 출처

### 기업·직원 조사

- [Microsoft 2026 Work Trend Index](https://www.microsoft.com/en-us/worklab/work-trend-index/agents-human-agency-and-the-opportunity-for-every-organization)
- [LangChain 2026 State of Agent Engineering](https://www.langchain.com/state-of-agent-engineering)
- [Microsoft 2025 Work Trend Index](https://blogs.microsoft.com/blog/2025/04/23/the-2025-annual-work-trend-index-the-frontier-firm-is-born/)
- [McKinsey 2025 State of AI](https://www.mckinsey.com/capabilities/quantumblack/our-insights/the-state-of-ai-how-organizations-are-rewiring-to-capture-value)
- [PwC 2025 AI Agent Survey](https://www.pwc.com/us/en/tech-effect/ai-analytics/ai-agent-survey.html)
- [NIST AI 600-1 Generative AI Profile](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence)
- [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)

### 기업 Agent 기능과 사례

- [Microsoft Copilot Studio tools](https://learn.microsoft.com/en-us/microsoft-copilot-studio/agents-experience/tools-available)
- [Microsoft Copilot enterprise knowledge connectors](https://learn.microsoft.com/en-us/microsoft-copilot-studio/knowledge-copilot-connectors)
- [Microsoft Copilot Agent Flow](https://learn.microsoft.com/en-us/microsoft-copilot-studio/agent-node-workflow)
- [ServiceNow AI Agents](https://www.servicenow.com/products/ai-agents.html)
- [Salesforce Agent trust and guardrails](https://help.salesforce.com/s/articleView?id=000372738&language=en_US&type=3)
- [Salesforce Agentforce security and shared responsibility](https://help.salesforce.com/s/articleView?id=005315874&language=en_US&type=1)

### Langflow

- [Langflow Business Use Cases](https://www.langflow.org/use-cases?categories=Business)
- [Langflow 1.8 Vector RAG](https://docs.langflow.org/1.8.0/chat-with-rag)
- [Langflow 1.8 Agent](https://docs.langflow.org/1.8.0/agents)
- [Langflow 1.8 MCP Server](https://docs.langflow.org/1.8.0/mcp-server)
- [Langflow 1.8 Structured Output](https://docs.langflow.org/1.8.0/structured-output)
- [Langflow 1.8 SQL Database](https://docs.langflow.org/1.8.0/sql-database)
- [Langflow 1.8 Read File](https://docs.langflow.org/1.8.0/read-file)
- [Langflow 1.8 Release Notes](https://docs.langflow.org/1.8.0/release-notes)
- [Webhook + Parse JSON issue #8705](https://github.com/langflow-ai/langflow/issues/8705)
- [User-scoped file upload issue #7728](https://github.com/langflow-ai/langflow/issues/7728)
