# Agent Ground

코딩이 익숙하지 않은 사용자도 Agent Builder(Langflow)에서 업무용 AI Agent를 만들 수 있도록 교육자료, 기능 단위 Component, 재사용 Flow, 문제 해결 기록을 한곳에서 제공하는 프로젝트입니다.

이 프로젝트에서 **Standalone은 한 파일로 등록하는 포장 방식**이고, **Component는 Flow 밖에서도 직접 재사용할 수 있는 기능 단위**입니다. Flow에 들어가는 Python 파일이라는 이유만으로 Component로 등록하지 않으며, 특정 Flow에 종속된 변환·프롬프트 조립·데모·출력 포장 단계는 `flows/<flow_id>/nodes/`의 내부 노드로 관리합니다.

## 현재 구현 범위

- 새로 설계한 통합 교육 포털
- `reusable_data_flow` 재구축용 12개 Flow 내부 노드와 연결 설계 (현재 export 불일치로 Flow import 중단)
- 기존 `html_report_flow` 기반 Flow, 재사용 Component 3개와 Flow 내부 노드 6개
- 신규 `enterprise_document_rag_flow`, 재사용 Component 6개와 Flow 내부 노드 3개
- 신규 하이브리드 `skill_based_agent_flow`와 회의 전용 하위 Flow
  - Flow 내부 데모 Skill 카탈로그와 동적 Agent 지침
  - 경비 사전 점검·휴가 평일 계산은 개별 Standalone Component를 직접 Tool Mode로 연결
  - 회의 액션아이템은 개선형 이름 기반 Run Flow Tool로 `meeting_action_skill_flow` 호출
- 신규 `ppt_reference_html_flow`
  - 표지·본문 이미지는 Base64 Data URL로 변환한 뒤 Vision 모델이 디자인 규칙만 관찰
  - 발표 brief와 실제 dataset을 사실 근거로 사용하고 표·KPI·막대·선·산점도를 계약에 따라 선택
  - 검증된 계획을 외부 CDN 없는 16:9 HTML 슬라이드로 렌더링하고 품질 Gate를 통과한 결과만 출력
- 독립 사용 사례와 입출력 계약을 갖춘 공용·업무·RAG·HTML·프레젠테이션 Component 20개
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
- 다른 PC로 복사해 바로 적용할 수 있는 Agent Ground 개발 Skill 4종
- Business Agent Design 24-node 실행 Flow, 공용 Library와 분리된 전용 Standalone 실행 Node 15개, Import Bundle

현재 실행 가능한 Flow와 Component 상태는 `user_testing`입니다. Flow 내부 노드는 소유 Flow 상태를 따르며 별도 공개 자산이나 registry 항목으로 세지 않습니다. `reusable_data_flow`는 실제 JSON이 과거 업무 설계 Flow로 확인되어 `building`으로 낮추고 전체 Bundle에서 제외했습니다. 사용자가 실제 Agent Builder 환경에서 확인하고 완료를 승인한 뒤에만 `approved`로 전환합니다.

공용 Component 추천 30종 중 `multi_image_base64_encoder`를 선택해 구현했고, 외부 프로젝트의 개선형 Run Flow 설계를 일반화한 `cached_named_run_flow_tool`도 추가했습니다. Run Flow Tool은 provider가 내부 node key의 특수문자를 바꿔 질문이 유실되지 않도록 `flow_tweak_data` 안의 동적 업무 입력을 필수 `question` 하나로 고정하고 실행 시 현재 Chat Input ID로 변환합니다. 또한 `reusable_data_flow`의 다중 요청·라우팅 설계와 12개 Flow 내부 Python 원본을 보존하면서 Oracle, H-API, Datalake, GooDocs와 일반 JSON API를 각각 한 번 조회하는 최소 단위 Component를 새 ID로 분리했습니다. 새 자산은 모두 `user_testing`입니다. 별도 Utility 전용 Flow는 만들지 않았지만 `cached_named_run_flow_tool`은 하이브리드 Skill Agent의 회의 하위 Flow 호출에 재사용합니다.

`skill_based_agent_flow`는 Langflow가 `SKILL.md`를 자동 탐색한다고 가정하지 않습니다. LLM에는 `expense_precheck_skill`, `leave_policy_skill`, `meeting_action_skill` 세 Tool 이름이 보이지만 실행 방식은 다릅니다. 경비·휴가는 개별 계산 Component를 직접 호출하고, 회의는 `cached_named_run_flow_tool`이 같은 프로젝트의 `meeting_action_skill_flow`를 이름으로 찾아 실행합니다. 하위 Flow 입력은 내부 node ID가 아니라 provider-safe `question` 계약으로 전달됩니다. 승인, 저장, 메일 발송과 같은 외부 변경 Tool은 예제에 포함하지 않았습니다.

`ppt_reference_html_flow`에서는 참고 이미지를 내용 근거가 아닌 디자인 근거로만 사용합니다. 이미지 속 문구·숫자·지시사항은 신뢰하지 않고, 발표 내용과 차트 값은 사용자가 입력한 brief·dataset에서만 가져옵니다. LLM은 HTML을 직접 작성하지 않고 디자인 관찰 JSON과 슬라이드 계획 JSON만 제안하며, Python Normalizer와 `html_presentation_renderer`가 실제 데이터 바인딩·허용 목록·escaping을 적용해 자체 포함 HTML을 만듭니다.

## 바로 열기

- 통합 포털: [`html/index.html`](html/index.html)
- 전체 교육자료: [`html/training/index.html`](html/training/index.html)
- 초보자 학습 안내: [`html/training/overview.html`](html/training/overview.html)
- Flow 목록: [`html/flows/index.html`](html/flows/index.html)
- Component 목록: [`html/components/index.html`](html/components/index.html)
- Component 카탈로그 범위 기준: [`components/COMPONENT_CATALOG_SCOPE.md`](components/COMPONENT_CATALOG_SCOPE.md)
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
- PPT 참조 이미지 HTML 프레젠테이션 가이드: [`flows/ppt_reference_html_flow/README.md`](flows/ppt_reference_html_flow/README.md)
- PPT 참조 이미지 HTML 프레젠테이션 개별 Import: [`flows/ppt_reference_html_flow/ppt_reference_html_flow.json`](flows/ppt_reference_html_flow/ppt_reference_html_flow.json)
- 발표 데이터 입력 예시: [`flows/ppt_reference_html_flow/samples/sample_presentation_data.json`](flows/ppt_reference_html_flow/samples/sample_presentation_data.json)
- Agent Ground 실행 가능 6개 Flow 일괄 Import: [`flows/00_AGENT_GROUND_ALL_FLOWS.json`](flows/00_AGENT_GROUND_ALL_FLOWS.json)
- 재사용 데이터 Flow export 불일치 기록: [`html/troubleshooting/reusable-data-flow-export-mismatch.html`](html/troubleshooting/reusable-data-flow-export-mismatch.html)
- 이동식 개발 Skill 묶음: [`skills/skill-pack.json`](skills/skill-pack.json)
- 프로젝트 기준: [`AGENT_GROUND_PROJECT_MASTER_GUIDE.md`](AGENT_GROUND_PROJECT_MASTER_GUIDE.md)
- 현재 검증 결과: [`VALIDATION_REPORT.md`](VALIDATION_REPORT.md)

## 폴더

| 폴더 | 역할 |
| --- | --- |
| `components/` | 독립 사용 사례와 안정된 입출력 계약을 가진 기능 단위 Component 원본 |
| `flows/` | Flow JSON, 연결 가이드, 샘플, 참고문서와 `nodes/`의 Flow 내부 Standalone Python 노드 |
| `training/` | 이후 확장할 교육 원본과 샘플 |
| `html/` | 브라우저에서 여는 통합 포털과 모든 HTML 설명서 |
| `registry/` | 자산 상태와 문서 경로의 기준 데이터 |
| `business_agent_design/` | 업무 Agent 설계 실행 Flow, 전용 Standalone Component, Prompt, 테스트와 문서 |
| `scripts/` | manifest·registry·HTML 생성 및 검증 도구 |
| `skills/` | 다른 PC의 Codex/Agent Skill 경로로 폴더째 복사할 수 있는 개발 규칙 4종과 설치 스크립트 |

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

## 다른 환경에서 이어서 개발하기

저장소의 `skills/` 폴더를 함께 동기화합니다. Codex 사용자 Skill 경로에 설치하려면 프로젝트 루트에서 다음을 실행합니다.

```powershell
powershell -ExecutionPolicy Bypass -File skills/install.ps1
```

기본 목적지는 `$CODEX_HOME/skills`이며 `CODEX_HOME`이 없으면 `$HOME/.codex/skills`입니다. 기존 Skill은 자동으로 덮어쓰지 않으며, 의도적으로 갱신할 때만 `-Force`를 사용합니다.

| Skill | 역할 |
| --- | --- |
| `maintain-agent-ground` | 프로젝트 구조, 승인 상태, 기준 원본과 집·회사 환경 간 병합 |
| `build-langflow-standalone-component` | Component 승격 판단과 한글 Standalone 구현·계약·검증 |
| `build-langflow-flow-package` | Flow JSON, Component 참조, 내부 노드, Run Flow 안전 계약과 bundle |
| `maintain-agent-ground-portal` | registry 기반 포털, 교육자료, 계약·코드 화면과 반응형 QA |
