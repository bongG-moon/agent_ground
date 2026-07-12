# Agent Ground 현재 검증 보고서

> 검증일: 2026-07-12  
> 현재 상태: `user_testing`  
> 범위: 정적 구조, 원본 일치, JSON, Python, HTML, 링크, Registry, Enterprise Document RAG 실제 Langflow Build, 하이브리드 Skill 기반 Agent 예시, 공용 Component 선택 구현 2종, 최소 단위 직접 데이터 조회 Component 5종

## 통과한 검사

| 검사 | 결과 |
| --- | --- |
| 기존 Flow JSON과 새 위치의 파일 일치 | 2개 일치 |
| 기존 Component 원본과 분리한 파일 일치 | 21개 일치 |
| 신규 Enterprise Document RAG | 9개 Standalone Component, 13 nodes / 10 edges |
| 신규 하이브리드 Skill 기반 Agent | 상위 9 nodes / 8 edges, 회의 하위 5 nodes / 3 edges, 직접 계산 Tool 2개 + Run Flow Tool 1개 |
| Python 문법 검사 | 83개 통과 |
| Standalone 단일 파일·상대 import 검사 | 41개 통과 |
| JSON 파싱 | 79개 통과: 회의 하위 Flow와 Skill 전용 Bundle 포함 |
| Flow 구조 | `flows/` 아래 등록 자산 4개, 회의 하위 Flow를 포함한 실행 Flow JSON 5개에 node/edge 존재 |
| Component reference와 버전 | 41개 참조 통과 |
| HTML 파싱·구조 | 56개 페이지 통과 |
| 로컬 링크와 자산 경로 | 1,347개 통과 |
| Registry | 45개 자산 일치 |
| JavaScript 문법 | 통과 |
| HTML 파일 위치 | 모두 `html/` 아래에 위치 |
| Business Agent Design 실행 코드 | 메인 10개 + 카탈로그 운영 5개 Standalone Component |
| Business Agent Design Flow | 24 nodes / 34 edges, Langflow 1.8.2 Import JSON 생성 |
| Business Agent Design 기능 테스트 | 6개 통과: graph, 분기, 개선 상세, HTML 보안, Import shape, handle |
| Business/프로젝트 Bundle | BOM 없음, `{"flows":[` prefix, 프로젝트 6개 Flow 포함 |
| Langflow edge handle | 34개 edge의 `œ` decode 및 `edge.data` 일치 확인, `┇` 없음 |
| Business Agent BEFORE/AFTER Flow Chart | 2개 Chart, 분기, 변경 설명 action 확인 |
| 기존 교육 본문 영역 보존 | 전체 markup 포함 확인 |
| 기존 교육 제목 보존 | 141개 전체 일치 |
| 기존 교육 링크·자산 참조 보존 | 202개 전체 포함 |
| Document RAG 기능·보안 테스트 | 13개 통과: ACL, PII, 품질 gate, 거절, injection, version, citation, embedded source |
| Document RAG runtime template | 실제 Langflow `1.8.2` / LFX `0.3.4`에서 9개 code template 생성·재생성 일치 |
| Document RAG 실제 Upload | 임시 Flow HTTP 201, 13 nodes / 10 edges |
| Document RAG 실제 Full Build | HTTP 200, 실행 node 11/11 `valid=true`, invalid 0 |
| Document RAG 실제 Chat Output | 답변 322자, 숫자 인용 `[1]`, `RAG lifecycle separation` 및 `p.1` 확인 |
| 임시 Flow 정리 | DELETE 200, Flow 수 75 → 75, 잔여 검증 Flow 0 |
| 하이브리드 Skill Agent 기능·구조 테스트 | 18개 통과: 카탈로그, 계산·구조화, 실제 Tool 호출, 직접 Tool 2개, Run Flow Tool 1개, 상·하위 Graph, 전용·전체 Bundle |
| 하이브리드 Skill Tool 구성 | 경비·휴가 Component Tool Mode 2개, Cached Named Run Flow의 고유 Tool 출력 1개, 회의 하위 Flow의 구조화 Component 1개 |
| Skill Tool 호출 schema | 경비·휴가는 필수 `request` 하나, 회의 Run Flow는 내부 node ID 대신 `flow_tweak_data.question` 고정 계약 사용 |
| Skill Agent Graph 구조 | 실제 LFX에서 상위 7 vertices / 8 edges / Agent Tool 입력 3개, 하위 4 vertices / 3 edges parse 통과 |
| Skill Agent 포털 화면 | 하이브리드 구조·일괄 Bundle·MCP 확장 링크를 포함해 재생성, HTML·로컬 링크 정적 검증 통과 |
| Skill Agent 실제 모델 E2E | 특정 모델·API Key 미포함, 사용자 승인 Tool Calling 모델 연결 후 확인 필요 |
| 공용 Component 추천 조사 | P0 10개, P1 11개, P2 9개 등 총 30개 ITEM과 상태 정리 |
| 선택 공용 Component 구현 | `multi_image_base64_encoder`, `cached_named_run_flow_tool` 2개 Standalone·한글 UI·가이드 생성 |
| 선택 Component 기능·보안 테스트 | 10개 통과: 이미지 순서·decode·signature·용량·SVG, 동일 폴더·cross-folder opt-in·동명 충돌·직접 재귀·고정 question schema·현재 Chat Input 매핑·session·timestamp |
| 선택 Component runtime template | 실제 Langflow `1.8.2` / LFX `0.3.4`에서 2개 template과 다중 FileInput·Tool output 계약 생성 |
| 추천·구현 HTML 문서 | 2개 상세 페이지, 사용 가이드와 추천 현황 카탈로그 연결 |
| Run Flow 초보자 설명 | 기본 엔진 상속, 별도 구현 이유 6가지, 특수문자 key 변형 원인과 고정 `question` 해결 방식을 한글 문서·상세 페이지에 연결 |
| Run Flow 초보자 HTML 화면 | 1280px·375px에서 6개 이유 카드, 실행 순서, 비교표, 경고와 자료 링크 확인 |
| 추천·구현 포털 화면 | 1280px 데스크톱·375px 모바일에서 한글 분류명, 상세 이동, 자료 링크, 가로 넘침 없음 확인 |
| 최소 단위 직접 조회 구현 | Oracle·H-API·Datalake·GooDocs·일반 API 5개 Standalone, 출력 포트 `data_table` 한 개 |
| 직접 조회 기능·보안 계약 테스트 | 8개 통과: template, native bind, 최대 행, 빈 결과, HTTP 기본 차단, 응답 경로, origin redirect, Datalake host·CA·SQL 경계, 자원 정리, GooDocs 교체 경계, 기존 참조 보존 |
| 직접 조회 runtime template | 실제 Langflow `1.8.2` / LFX `0.3.4`에서 5개 template, 한글 UI, `DataFrame` 단일 출력, cache/tool 비활성 확인 |
| 직접 조회 로컬 의존성 | `oracledb`, `requests`, `aiohttp` 확인; `mysql-connector-python`은 현재 Langflow 환경에 없음 |
| 직접 조회 포털 화면 | 1280px·375px 가로 넘침·깨진 이미지·콘솔 오류 없음, 5개 카드와 보안 안내 확인 |
| 직접 조회 문서 이동 | 교육 안내 → 직접 조회 카탈로그 → 기존 재사용 Flow 실제 클릭, 기존 Flow의 역방향 링크 확인 |
| Datalake 상세 화면 | HTTP 토글, 허용 MySQL Host, CA 경로, `data_table`, 설치 패키지명 표시 확인 |

## 신규 공용 Component 선택 구현

2026-07-12에는 사내 업무에서 반복 활용할 Standalone Component 후보 30종을 조사하고 사용자가 선택한 두 항목을 구현했습니다.

- P0: 외부 인프라 없이 먼저 만들기 좋은 10개
- P1: 사내 API·승인·운영 연동 11개
- P2: 추가 패키지나 운영 시스템이 필요한 9개

추천 목록의 `multi_image_base64_encoder`와 외부 참조 기반 `cached_named_run_flow_tool`은 Python 원본, manifest, 테스트, 한글 사용 가이드와 HTML 상세 페이지를 생성했습니다. 별도 Utility 전용 Flow는 만들지 않았지만, `cached_named_run_flow_tool`은 현재 하이브리드 Skill Agent에서 회의 하위 Flow 호출에 재사용합니다.

두 Component는 실제 runtime template과 격리 기능 테스트를 통과했지만 사용자가 Builder에서 직접 등록·실행하기 전까지 `user_testing`입니다. Cached Run Flow 도구는 실제 LFX Pydantic Tool schema에서 바깥 `flow_tweak_data`와 내부 필수 `question` 하나가 생성되고 현재·재import Chat Input ID로 내부 매핑되는 것을 확인했습니다. 현재 Agent Ground에는 이를 사용하는 상위 Agent JSON과 `meeting_action_skill_flow` 하위 Flow JSON을 함께 생성했지만, 실제 Builder에서의 cold/warm 실행과 session 상속 E2E는 아직 수행하지 않았습니다. 다른 프로젝트에서 수행한 Flow 매핑·partial build 기록은 Agent Ground 검증으로 승계하지 않습니다.

## Skill 기반 업무 Agent 예시

`skill_based_agent_flow`는 Langflow 공식 Simple Agent와 같은 `component_as_tool → Agent.tools` 연결을 사용하되, 직접 계산 Tool과 Run Flow Tool을 함께 보여주는 하이브리드 구조입니다. 데모 Skill 카탈로그가 세 업무의 선택 조건·instructions·허용 action을 검증하고 Agent의 `system_prompt`를 동적으로 만듭니다. LLM에는 다음 세 Tool 이름이 보입니다.

- `expense_precheck_skill`: 경비 금액 합산과 데모 한도 비교
- `leave_policy_skill`: 두 ISO 날짜 사이 평일·설정 휴일 계산
- `meeting_action_skill`: 개선형 이름 기반 Run Flow Tool이 같은 프로젝트의 `meeting_action_skill_flow`를 호출해 `담당자 | 할 일 | YYYY-MM-DD` 행 구조화

경비·휴가 Tool은 외부 입력 schema에 필수 `request` 하나만 노출합니다. 회의 Run Flow Tool은 `flow_tweak_data` 안에 provider-safe한 필수 `question` 하나만 두고, 실행 시 현재 하위 Flow의 유일한 Chat Input ID로 변환합니다. 세 Tool 모두 `return_direct=True`이며, 실제 업무 계산 Component는 등록·활성화 Skill과 필수 action을 다시 확인합니다. 승인·저장·결제·메일·캘린더 변경 기능은 구현하지 않았고 입력 원문은 결과 trace에 복제하지 않고 SHA-256과 글자 수만 기록합니다.

표준 Agent는 연결된 세 Tool을 모두 볼 수 있으므로 카탈로그 지침은 선택을 유도하지만 per-turn hard filter는 아닙니다. 이 때문에 현재 자산은 읽기 전용 교육 데모입니다. 외부 변경 Skill로 확장할 때는 구조화된 Skill 선택 뒤 exact allowlist만 전달하는 Tool Gate 또는 고정 하위 Workflow가 필요합니다.

개별 계산 Component와 Cached Named Run Flow Tool의 Langflow `1.8.2` / LFX `0.3.4` 계약을 확인했습니다. 현재 0.2.0 상위·하위 JSON의 LFX Graph parse, Tool 입력 3개, Skill 전용 2개 Flow Bundle과 프로젝트 6개 Flow Bundle 순서·BOM 회귀 검증도 통과했습니다. 특정 모델과 API Key를 Flow JSON에 넣지 않았으므로 실제 LLM의 Skill 선택 정확도와 이름 기반 하위 Flow DB 실행은 사용자 승인 Tool Calling 모델을 연결한 뒤 확인해야 합니다.

## 최소 단위 직접 데이터 조회 Component

기존 `reusable_data_flow`는 자연어 요청, Source Catalog, 소스 분기, 다중 요청과 결과 병합을 포함한 완성 Flow이므로 기존 `0.9.0` Component와 JSON을 그대로 유지했습니다. 단일 소스를 직접 조회하려는 경우를 위해 다음 새 ID를 추가했습니다.

| Component | 직접 입력 | 출력 |
| --- | --- | --- |
| `oracle_table_query` | DSN, 계정, SQL, bind 변수, 최대 행 | `data_table: DataFrame` |
| `h_api_table_request` | URL, token, bindParams, 응답 경로, 제한 | `data_table: DataFrame` |
| `datalake_table_query` | Cluster API, HTTP opt-in, 허용 DB host, 인증, CA, SQL, bind 변수, 대기 제한 | `data_table: DataFrame` |
| `goodocs_table_reader` | 문서·사용자·token 입력, 시트, 최대 행 | `data_table: DataFrame` |
| `simple_api_table_request` | URL, GET/POST, header/query/body, 응답 경로, 제한 | `data_table: DataFrame` |

다섯 Component는 성공 여부 envelope, 오류 행, Source Catalog, 다중 요청 병합과 dummy fallback을 출력하지 않습니다. 정상적인 0건 조회는 컬럼을 유지한 빈 `DataFrame`이며, 접속·인증·SQL·응답 형식 실패는 한글 예외로 실행을 중단합니다.

로컬 검증은 실제 LFX template과 주입한 가짜 transport/driver를 이용한 계약 테스트입니다. HTTP 기본 차단, 다른 origin 리다이렉트 차단, Datalake endpoint allowlist와 CA 검증 인자도 확인했습니다. 실제 사내 endpoint와 계정은 호출하지 않았고, GooDocs 실제 모듈도 사용자가 교체해야 합니다. Datalake 실호출 전에는 현재 Langflow Python 환경에 `mysql-connector-python`을 설치하고 사내 CA 파일 경로를 준비해야 합니다.

## 교육자료 보존 방식

기존 `LANGFLOW_INTERNAL_TRAINING_PORTAL.html`을 기준 콘텐츠로 사용했습니다. 기존 sidebar, topbar와 CSS는 그대로 복원하지 않고 Agent Ground의 공통 헤더, 새 목차, 검색 툴바, 카드·표·코드 디자인과 반응형 레이아웃으로 교체했습니다. 기존 교육 본문 영역의 markup, 제목 순서, 링크·자산 참조는 그대로 유지되는지 검증합니다.

- 기존 전체 교육 포털: `html/training/index.html`
- 신규 초보자 학습 안내: `html/training/overview.html`
- 기존 예제·샘플·이미지: `html/training/examples`, `sample_files`, `assets`
- 변환·보존 검증 정보: `training/source/legacy_training_portal_manifest.json`

## 현재 Flow 정보

| Flow | 기존 export 기록 | Node | Edge | Component |
| --- | --- | ---: | ---: | ---: |
| `reusable_data_flow` | Langflow `1.8.2` | 16 | 21 | 12 |
| `html_report_flow` | Langflow `1.8.2` | 18 | 22 | 9 |
| `enterprise_document_rag_flow` | Langflow `1.8.2` | 13 | 10 | 9 Standalone + Chat I/O + Note |
| `skill_based_agent_flow` | Langflow `1.8.2` | 9 | 8 | 카탈로그 + 직접 Tool 2 + Run Flow Tool 1 + Agent + Chat I/O + Note |
| `meeting_action_skill_flow` | Langflow `1.8.2` | 5 | 3 | 카탈로그 + 회의 구조화 Component + Chat I/O + Note |
| `business_agent_design_complete` | Langflow `1.8.2` | 24 | 34 | 15 Standalone + 기본 node |

## 로컬 Langflow 확인

- `http://127.0.0.1:7860/api/v1/version`: `1.8.2`
- `http://127.0.0.1:7860/health`: `ok`
- `enterprise_document_rag_flow`는 고유한 임시 이름으로 실제 Upload와 Full Build를 수행함
- 실제 Chat Output에서 답변·숫자 인용·문서명·page를 확인함
- 현재 0.2.0 상위 Flow는 9 nodes / 8 edges로 생성하고 LFX에서 7 vertices / Agent Tool 입력 3개를 확인함
- 회의 하위 Flow는 5 nodes / 3 edges로 생성하고 LFX에서 4 vertices / 3 edges를 확인함
- 경비·휴가·회의 결정론적 Component와 Cached Named Run Flow schema는 개별 검증했지만 실제 모델/API Key 및 하위 Flow Agent E2E는 수행하지 않음
- 검증 Flow는 즉시 삭제했고 기존 Flow 수와 잔여 이름을 재확인함
- 다른 기존 Flow와 전체 Bundle은 현재 검증에서 자동 Import하지 않음

## Enterprise Document RAG 검증 범위

- 기본 backend: `payload_lexical_v1`
- persistence: `ephemeral_current_run`
- 기본 신원: `identity_trust=demo`로 명시된 employee demo identity
- 기본 모델: 없음. 외부 API key 없이 deterministic 근거 답변
- 운영 전환 시 필수 교체: 검증된 identity adapter, DLP, persistent index writer, 검색 쿼리 단계 ACL filter가 있는 vector retriever, 승인 LLM adapter

이번 검증은 ACL 선필터·검색·품질 gate·답변·인용 계약이 한 실행에서 동작함을 확인합니다. semantic vector search, 대규모 영속 색인, 운영 SSO와 완전한 DLP까지 구현·승인한 것으로 표시하지 않습니다.

발견한 정상 질문 거절 오탐과 Langflow 1.8.2 Milvus 경계는 `html/troubleshooting/enterprise-document-rag-langflow-1-8-2.html`에 별도로 기록했습니다.

## 사용자 환경에서 남은 확인

- [x] 로컬 Agent Builder 버전 `1.8.2`와 health 확인
- [x] 신규 RAG 9개 Component가 Langflow 1.8.2 runtime template으로 생성되는지 확인
- [x] 신규 RAG JSON actual upload와 11개 실행 node full build 확인
- [x] 신규 RAG 대표 질문의 답변·인용·문서명·page 확인
- [x] 신규 RAG 허용·거절·ACL·PII·injection·version 회귀 테스트 확인
- [x] 선택 Utility 2개 Component의 Langflow 1.8.2 runtime template과 한글 UI 계약 확인
- [x] 이미지 순서·decode·signature·용량·SVG와 Run Flow 폴더 격리·동명 충돌·재귀·session 회귀 테스트 확인
- [x] Run Flow Tool schema가 바깥 `flow_tweak_data`와 내부 필수 `question` 하나이고 provider 변형 키를 거부하며 현재 Chat Input ID로 변환되는지 확인
- [x] 추천 현황과 Run Flow 상세 페이지를 1280px·375px에서 열어 링크·버튼·가로 넘침 확인
- [x] 직접 조회 5개 Component의 Langflow 1.8.2 template, 한글 입력, `data_table: DataFrame` 단일 출력 확인
- [x] Oracle·Datalake native bind, HTTP 기본 차단, H-API body, API 응답 경로·origin redirect, Datalake host·CA·SQL 경계, GooDocs 교체 구역을 격리 테스트로 확인
- [x] 기존 `reusable_data_flow` JSON과 `component_refs.json`이 신규 Component로 자동 교체되지 않았는지 확인
- [x] 직접 조회 포털을 1280px·375px에서 열고 교육·카탈로그·기존 Flow 양방향 링크와 Datalake 보안 입력 표시 확인
- [x] 경비·휴가·회의 결정론적 결과, 비활성 Skill, 미등록 Tool, 지시 덮어쓰기 문구와 외부 변경 금지 계약 확인
- [x] Cached Named Run Flow Tool의 고정 `question`, 동일 폴더 기본값, 이름 중복·재귀 차단 계약 확인
- [x] 0.2.0 상위 Flow의 직접 Tool 2개 + Run Flow Tool 1개와 동적 `system_prompt` 연결을 실제 LFX Graph로 재확인
- [x] `meeting_action_skill_flow`의 Chat Input 하나·Chat Output 하나와 3개 edge를 실제 LFX Graph로 재확인
- [x] Skill 전용 2개 Flow Bundle과 프로젝트 6개 Flow Bundle의 순서·BOM·원본 일치 재확인
- [ ] 두 신규 Python 파일을 Builder에 직접 등록하고 입력·출력 이름 확인
- [ ] 여러 실제 이미지 업로드 후 순서와 Base64/Data URL 결과 확인
- [ ] 실제 같은 폴더 하위 Flow로 Cached Run Flow cold/warm 실행과 session 상속 확인
- [ ] 하위 Flow 재import·Chat Input 교체 후 Tool schema 재구성과 실제 질문 전달 확인
- [ ] 다른 폴더 동명 Flow·간접 순환 Flow가 운영 구성에 없는지 확인
- [ ] 직접 조회 5개 Python 파일을 Builder에 각각 등록하고 `data_table` 포트 확인
- [ ] 실제 조회 전용 Oracle 계정으로 native bind SQL과 0건·제한 초과 결과 확인
- [ ] 실제 H-API endpoint에서 bindParams 순서와 `data.row` 응답 계약 확인
- [ ] Langflow 환경에 승인된 `mysql-connector-python`을 설치하고 Datalake Cluster API·StarRocks 조회 확인
- [ ] `goodocs_table_reader.py`의 교체 구역에 실제 GooDocs 모듈을 넣고 문서·시트 조회 확인
- [ ] 승인된 일반 API에서 GET·POST, 인증 header, 응답 경로와 최대 크기 정책 확인
- [ ] Skill Supervisor Agent에 회사 승인 Tool Calling 모델과 API Key를 연결하고 세 단일 의도의 실제 Tool 선택 확인
- [ ] Skill Agent의 비대상·복합 의도·Prompt Injection 질문에서 Tool 미호출·요청 분리·외부 변경 금지 확인
- [ ] 기존 21개 Component가 화면에 정상 등록되는지 확인
- [ ] 입력 필드, 고급 설정, Output 이름이 HTML 설명서와 같은지 확인
- [ ] 기존 두 Flow JSON import 후 invalid handle 여부 확인
- [ ] Business Agent Design 개별 JSON import 후 24 nodes / 34 connections 확인
- [ ] Agent Ground 전체 Bundle import 화면에서 6개 Flow 확인
- [ ] 실제 업무 설명으로 BEFORE/AFTER 분기와 `개선 설명` 버튼 확인
- [ ] `reusable_data_flow` 대표 catalog와 질문 실행
- [ ] `html_report_flow` 샘플 CSV/JSON 실행과 HTML 확인
- [ ] 선택적으로 Report API 보기·다운로드 링크 확인
- [ ] 운영 RAG 전환 전 실제 identity/DLP/vector store adapter 통합 테스트
- [ ] 노출 가능성이 있는 기존 LLM API key 회전 및 command-line 외 secret 주입 확인
- [ ] 사용자가 모바일과 데스크톱에서 포털 디자인 직접 확인
- [ ] 발견된 차이를 문제 해결 HTML로 기록
- [ ] 사용자의 완료 승인 후 `approved` 전환

정적 검사가 통과했더라도 위 실제 환경 확인 전에는 완료 또는 승인 상태로 표시하지 않습니다.
