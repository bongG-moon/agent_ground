# Agent Ground

코딩이 익숙하지 않은 사용자도 Agent Builder(Langflow)에서 업무용 AI Agent를 만들 수 있도록 교육자료, Standalone Component, 재사용 Flow, 문제 해결 기록을 한곳에서 제공하는 프로젝트입니다.

## 현재 구현 범위

- 새로 설계한 통합 교육 포털
- 기존 `reusable_data_flow` 기반 Flow와 12개 Standalone Component
- 기존 `html_report_flow` 기반 Flow와 9개 Standalone Component
- 신규 `enterprise_document_rag_flow`와 9개 보안·검색·인용 Standalone Component
- 신규 하이브리드 `skill_based_agent_flow`와 회의 전용 하위 Flow
  - 데모 Skill 카탈로그와 동적 Agent 지침
  - 경비 사전 점검·휴가 평일 계산은 개별 Standalone Component를 직접 Tool Mode로 연결
  - 회의 액션아이템은 개선형 이름 기반 Run Flow Tool로 `meeting_action_skill_flow` 호출
- 사내 공용 Standalone Component 추천 후보 30종과 선택 구현 2종
  - `multi_image_base64_encoder`
  - `cached_named_run_flow_tool`
- 기존 유연 조회 Flow와 분리한 최소 단위 직접 데이터 조회 Component 5종
  - `oracle_table_query`
  - `h_api_table_request`
  - `datalake_table_query`
  - `goodocs_table_reader`
  - `simple_api_table_request`
- 상위 Flow들의 사용자용 HTML 설명서
- Component manifest와 통합 registry
- Business Agent Design 24-node 실행 Flow, 15개 전용 Standalone Component, Import Bundle

현재 Flow와 Component 상태는 `user_testing`입니다. 사용자가 실제 Agent Builder 환경에서 확인하고 완료를 승인한 뒤 `approved`로 전환합니다.

공용 Component 추천 30종 중 `multi_image_base64_encoder`를 선택해 구현했고, 외부 프로젝트의 개선형 Run Flow 설계를 일반화한 `cached_named_run_flow_tool`도 추가했습니다. Run Flow Tool은 provider가 내부 node key의 특수문자를 바꿔 질문이 유실되지 않도록 `flow_tweak_data` 안의 동적 업무 입력을 필수 `question` 하나로 고정하고 실행 시 현재 Chat Input ID로 변환합니다. 또한 기존 `reusable_data_flow`의 다중 요청·라우팅 계약은 그대로 보존하면서 Oracle, H-API, Datalake, GooDocs와 일반 JSON API를 각각 한 번 조회하는 최소 단위 Component를 새 ID로 분리했습니다. 새 자산은 모두 `user_testing`입니다. 별도 Utility 전용 Flow는 만들지 않았지만 `cached_named_run_flow_tool`은 하이브리드 Skill Agent의 회의 하위 Flow 호출에 재사용합니다.

`skill_based_agent_flow`는 Langflow가 `SKILL.md`를 자동 탐색한다고 가정하지 않습니다. LLM에는 `expense_precheck_skill`, `leave_policy_skill`, `meeting_action_skill` 세 Tool 이름이 보이지만 실행 방식은 다릅니다. 경비·휴가는 개별 계산 Component를 직접 호출하고, 회의는 `cached_named_run_flow_tool`이 같은 프로젝트의 `meeting_action_skill_flow`를 이름으로 찾아 실행합니다. 하위 Flow 입력은 내부 node ID가 아니라 provider-safe `question` 계약으로 전달됩니다. 승인, 저장, 메일 발송과 같은 외부 변경 Tool은 예제에 포함하지 않았습니다.

## 바로 열기

- 통합 포털: [`html/index.html`](html/index.html)
- 전체 교육자료: [`html/training/index.html`](html/training/index.html)
- 초보자 학습 안내: [`html/training/overview.html`](html/training/overview.html)
- Flow 목록: [`html/flows/index.html`](html/flows/index.html)
- Component 목록: [`html/components/index.html`](html/components/index.html)
- 직접 데이터 조회 Component 포털: [`html/components/direct-data-access/index.html`](html/components/direct-data-access/index.html)
- 직접 데이터 조회 Component 통합 가이드: [`components/DIRECT_DATA_ACCESS_COMPONENTS_GUIDE.md`](components/DIRECT_DATA_ACCESS_COMPONENTS_GUIDE.md)
- 사내 공용 Component 추천 포털: [`html/components/enterprise-utility/index.html`](html/components/enterprise-utility/index.html)
- 사내 공용 Component 웹 조사·ITEM 목록: [`components/ENTERPRISE_UTILITY_COMPONENT_ITEM_LIST.md`](components/ENTERPRISE_UTILITY_COMPONENT_ITEM_LIST.md)
- 다중 이미지 Base64 인코더 가이드: [`components/multi_image_base64_encoder/USAGE_GUIDE.md`](components/multi_image_base64_encoder/USAGE_GUIDE.md)
- 캐시된 이름 기반 Run Flow 도구 가이드: [`components/cached_named_run_flow_tool/USAGE_GUIDE.md`](components/cached_named_run_flow_tool/USAGE_GUIDE.md)
- Business Agent Design 설계: [`business_agent_design/BUSINESS_AGENT_DESIGN_IMPLEMENTATION_SPEC.md`](business_agent_design/BUSINESS_AGENT_DESIGN_IMPLEMENTATION_SPEC.md)
- 사내 Agent Flow/Component 수요 조사: [`business_agent_design/ENTERPRISE_AGENT_FLOW_COMPONENT_RESEARCH.md`](business_agent_design/ENTERPRISE_AGENT_FLOW_COMPONENT_RESEARCH.md)
- Business Agent Design 개별 Import: [`business_agent_design/flow/business_agent_design_complete.json`](business_agent_design/flow/business_agent_design_complete.json)
- 사내 문서 RAG 가이드: [`flows/enterprise_document_rag_flow/README.md`](flows/enterprise_document_rag_flow/README.md)
- 사내 문서 RAG 개별 Import: [`flows/enterprise_document_rag_flow/enterprise_document_rag_flow.json`](flows/enterprise_document_rag_flow/enterprise_document_rag_flow.json)
- Skill 기반 Agent 가이드: [`flows/skill_based_agent_flow/README.md`](flows/skill_based_agent_flow/README.md)
- Skill 기반 Agent 2개 Flow 일괄 Import: [`flows/skill_based_agent_flow/00_SKILL_BASED_AGENT_ALL_FLOWS.json`](flows/skill_based_agent_flow/00_SKILL_BASED_AGENT_ALL_FLOWS.json)
- Skill 기반 Agent 상위 Flow: [`flows/skill_based_agent_flow/skill_based_agent_flow.json`](flows/skill_based_agent_flow/skill_based_agent_flow.json)
- 회의 후속 조치 하위 Flow: [`flows/skill_based_agent_flow/meeting_action_skill_flow.json`](flows/skill_based_agent_flow/meeting_action_skill_flow.json)
- Agent Ground 6개 Flow 일괄 Import: [`flows/00_AGENT_GROUND_ALL_FLOWS.json`](flows/00_AGENT_GROUND_ALL_FLOWS.json)
- 프로젝트 기준: [`AGENT_GROUND_PROJECT_MASTER_GUIDE.md`](AGENT_GROUND_PROJECT_MASTER_GUIDE.md)
- 현재 검증 결과: [`VALIDATION_REPORT.md`](VALIDATION_REPORT.md)

## 폴더

| 폴더 | 역할 |
| --- | --- |
| `components/` | 파일 하나만 등록해 사용할 수 있는 Standalone Component 기준 원본 |
| `flows/` | Flow JSON, 연결 가이드, 샘플, 참고문서 |
| `training/` | 이후 확장할 교육 원본과 샘플 |
| `html/` | 브라우저에서 여는 통합 포털과 모든 HTML 설명서 |
| `registry/` | 자산 상태와 문서 경로의 기준 데이터 |
| `business_agent_design/` | 업무 Agent 설계 실행 Flow, 전용 Standalone Component, Prompt, 테스트와 문서 |
| `scripts/` | manifest·registry·HTML 생성 및 검증 도구 |

## 기본 작업 순서

```text
구현
-> 로컬 검사
-> user_testing
-> 사용자 실제 환경 확인
-> 실패 기록과 수정
-> 사용자 완료 승인
-> approved 및 정식 포털/추천 카탈로그 반영
```

기존 `langflow교육자료`와 `기능flow` 폴더는 이관 출처로만 사용했으며 수정하지 않았습니다.
