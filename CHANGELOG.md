# Changelog

## 2026-07-12 — 하이브리드 Skill 기반 업무 Agent 예시 Flow

- Langflow 공식 Simple Agent의 `component_as_tool → Agent.tools` 계약 위에 직접 계산 Tool과 Run Flow Tool을 함께 연결
- 데모 Skill 카탈로그가 Agent 공통 지침과 세 Skill 정의를 만들고 LLM이 경비·휴가·회의 Tool 중 하나를 선택하도록 구성
- 경비 금액 합산과 휴가 평일 계산은 개별 Standalone Component를 Tool Mode로 직접 실행
- 회의 액션아이템 구조화는 `cached_named_run_flow_tool`이 같은 프로젝트의 `meeting_action_skill_flow`를 이름으로 찾아 실행
- 회의 하위 Flow에는 Chat Input 하나와 Chat Output 하나를 두고, 외부 Tool 계약의 고정 `question`을 현재 Chat Input ID로 내부 매핑
- 상위 Agent Flow와 회의 하위 Flow를 함께 넣는 `00_SKILL_BASED_AGENT_ALL_FLOWS.json` 및 프로젝트 6개 Flow Bundle 제공
- Agent가 세 Tool을 모두 보는 표준 연결은 per-turn hard filter가 아님을 문서화하고 외부 변경 확장에는 Tool Gate 또는 고정 하위 Workflow가 필요함을 명시
- 승인·저장·결제·메일·알림·캘린더 변경 Tool은 연결하지 않은 읽기 전용 교육 데모로 제한
- MCP 노드는 이번 예제에 직접 포함하지 않았으며, 사내 공용 MCP 서버가 준비되면 동일한 Agent `tools` 포트의 Tool로 교체·추가 가능
- 특정 모델과 API Key는 JSON에 저장하지 않으며, 사용자 환경에서 Tool Calling 모델과 동일 프로젝트 하위 Flow를 확인하기 전까지 Agent E2E 상태는 `user_testing`

## 2026-07-12 — Run Flow Tool 고정 `question` 계약

- Agent Tool schema의 `flow_tweak_data`에서 `ChatInput-...~input_value` 같은 내부 node ID를 제거하고 필수 `question` 하나만 노출
- 실행 직전에 현재 하위 Flow 그래프의 유일한 Chat Input ID를 찾아 `{현재_ID: {input_value: question}}` 내부 tweak로 변환
- Gemini/provider가 `-`와 `~`를 `_`로 정규화해 질문이 무시되던 `empty_question` 원인을 구조적으로 제거
- 하위 Flow 재import·Chat Input 교체로 node ID가 바뀌어도 현재 그래프 기준으로 다시 매핑
- 질문이 비었을 때 세션 이력이나 이전 상태에서 추정하지 않고 하위 Flow 실행 전에 오류로 중단
- Langflow `1.8.2` / LFX `0.3.4` 실제 Pydantic Tool schema에서 바깥 `flow_tweak_data`와 내부 `question` 단일 필드·필수 계약 검증

## 2026-07-12 — 최소 단위 직접 데이터 조회 Component 5종

- 기존 `reusable_data_flow`의 `0.9.0` Flow JSON과 Component 참조는 수정하지 않고 새 ID의 Standalone Component로 분리
- Oracle, H-API, Datalake, GooDocs, 일반 JSON API 호출을 각각 한 번만 수행하도록 입력 계약을 직접 노출
- 다섯 Component의 출력 포트를 `data_table` 하나로 통일하고 Langflow `DataFrame` 이외의 envelope·상태 행·dummy row를 제거
- Oracle·Datalake는 문자열 치환 대신 driver native bind를 사용하고 조회 전용 SQL, 최대 행 수, 자원 정리를 적용
- H-API와 일반 API는 JSON 응답·크기 제한·리다이렉트 경계를 적용하고, GooDocs는 사용자가 실제 모듈을 넣을 교체 구역과 최소 호출 계약을 명시
- 인증정보가 있는 HTTP는 기본 차단하고 일반 API 리다이렉트는 scheme·host·port가 같은 origin만 허용
- Datalake는 API 반환 DB host allowlist, CA 기반 인증서·hostname 검증, 실제 전체 대기 deadline과 `OUTFILE`·실행형 주석 차단을 적용
- 실제 Langflow `1.8.2` / LFX `0.3.4`에서 5개 template과 격리 계약 테스트 8개를 통과
- 실제 사내 endpoint·계정 호출, GooDocs 모듈 교체, Datalake용 `mysql-connector-python` 설치 확인 전까지 상태는 `user_testing`

## 2026-07-12 — Run Flow 초보자 설명 보강

- 기본 `RunFlowBaseComponent` 실행 엔진을 대체한 것이 아니라 Router·Standalone 배포 경계만 감싼 구조임을 초보자용 비유와 실행 흐름으로 설명
- Flow ID 재해석, Tool 입력 축소, 도구 설명, 세션 상속, Graph cache와 결과 직접 반환을 문제·보완·오해 순서로 정리
- 기본 Run Flow와의 선택 기준, Agent Ground 추가 안전장치와 당시 Chat Input 노드 ID 변경 시 발생하던 `empty_question` 제한을 명시(이 제한은 이후 상단의 고정 `question` 계약으로 해결)
- Component 상세 HTML, manifest, 자동 README와 공용 Component 목록에서 초보자 설명 문서로 이동하도록 연결

## 2026-07-12 — Selected enterprise utility implementations

- 추천 목록에서 `multi_image_base64_encoder`를 선택해 다중 FileInput, 입력 순서 보존, Base64/Data URL, signature·용량·SVG 정책을 가진 Standalone Component로 구현
- `metadata_driven_v5/langflow_components/route_flow_v2/01_cached_named_run_flow_tool.py`와 연결·Tool 설명을 검토해 `cached_named_run_flow_tool` 공용 Component로 일반화
- Run Flow 원본의 같은 사용자 전체 `.first()` 조회 문제를 보완해 기본적으로 부모 Router와 같은 폴더의 정확한 고유 이름만 허용
- 동명 Flow 충돌, 부모 자신 직접 실행, Tool 이름·설명·세션 형식과 microsecond cache 무효화 경계를 추가
- Component UI, 설명, 오류·경고, Python 주석·docstring과 사용 가이드를 한글 중심으로 작성
- 실제 Langflow `1.8.2` / LFX `0.3.4` template과 기능·보안 계약 테스트 10개 통과
- 포털의 기술용 자산군 ID를 한글 분류명으로 바꾸고 1280px·375px 화면에서 링크와 반응형 배치를 검수
- 두 Component는 사용자 Builder 확인 전까지 `user_testing`; 별도 Utility Flow JSON은 생성하지 않음

## 2026-07-12 — Enterprise Utility Component recommendation research

- 회사 업무에서 반복 활용할 공용 Standalone Component 후보를 공식 Langflow·보안·엔터프라이즈 자료로 조사
- 파일·이미지, Webhook·API, JSON 계약, 배치, 승인·감사와 운영 인프라의 추천 ITEM 30종을 P0/P1/P2로 분류
- 사용자 예시인 `multi_image_base64_encoder`의 예상 입력·출력과 구현 시 안전 조건을 설계안으로 정리
- 추천 목록과 반응형 HTML 카탈로그만 작성했으며 Component Python 코드, manifest, 테스트와 Flow JSON은 생성하지 않음
- 실제 구현은 사용자가 ITEM을 선택한 뒤 해당 항목만 Standalone 방식으로 진행

## 2026-07-10 — Initial integrated preview

- 프로젝트 마스터 가이드 작성
- 새 통합 폴더 구조 생성
- `reusable_data_flow`와 `html_report_flow` 이관
- 21개 Standalone Component 기준 원본 분리
- Component/Flow manifest와 capability registry 생성
- 새 디자인의 교육·Flow·Component·문제 해결 HTML 포털 생성
- Business Agent Design Flow 상세 구현 설계서 작성, 실행 구현은 보류
- 전체 자산 상태를 `user_testing`으로 설정

## 2026-07-10 — Training content redesign correction

- 기존 6,444줄 교육 포털의 모든 본문·제목·링크를 새 디자인에 이관
- 기존 예제 코드, 샘플 파일, 공식 화면 이미지 자산 함께 이관
- 기존 sidebar와 topbar를 Agent Ground 공통 헤더·새 목차·검색 툴바로 교체
- 기존 본문 markup과 제목·링크가 유지되는 자동 검증 추가
- 짧은 신규 학습 페이지는 `training/overview.html`로 분리해 보존

## 2026-07-10 — Business Agent Flow Chart result concept

- Business Agent Design 결과를 목록이 아닌 BEFORE/AFTER Flow Chart로 정의
- 실제 decision node, branch label과 연결 화살표가 있는 인터랙티브 결과 시안 추가
- 유지·변경·신규·사람 검토 상태별 node와 edge 색상 규칙 추가
- 변경 node의 `개선 설명` 버튼과 상세 panel 상호작용 추가
- graph node/edge/change map/improvement detail schema와 완료 기준 갱신

## 2026-07-10 — Business Agent Design executable Flow

- 기존 24-node / 34-edge Canvas를 Agent Ground 전용 실행 Flow로 재구축
- 메인 10개와 카탈로그 운영 5개 Component를 모두 Standalone 소스로 편입
- `nodes/edges`, decision branch, change map, improvement detail을 검증하는 Normalizer 구현
- BEFORE/AFTER node를 배치하고 SVG 연결선을 그리는 고정 HTML Renderer 구현
- 변경 node 상태 색상·텍스트, `개선 설명` 버튼, 상세 패널, 이전 화면 버튼 추가
- MongoDB 조회를 `status=approved`로 제한하고 미승인 로컬 Flow seed 제외
- Langflow 1.8.2의 `œ` handle delimiter와 UTF-8 BOM 없는 Import JSON 생성기 추가
- 당시 Business 개별 Import, Business Bundle, Agent Ground 3개 Flow 전체 Bundle 생성(현재 Bundle은 이후 자산과 회의 하위 Flow 추가로 6개)
- graph/보안/Import/handle/embedded source를 검증하는 6개 자동 테스트 추가
- 로컬 Langflow `1.8.2`와 health 응답 확인 후 상태를 `user_testing`으로 전환

## 2026-07-11 — Enterprise Agent Flow/Component research

- 2024~2026 기업 조사, 실제 Agent 서비스 기능, Langflow 1.8 공식 문서·템플릿을 교차 조사
- 사내 수요를 읽기 전용 Assistant, 사람 검토 Copilot, 승인형 Transaction Agent로 구분
- 공용 Flow와 Standalone Component 후보를 위험·재사용성·1.8.2 호환성 기준으로 우선순위화
- `reusable_data_flow` manifest/JSON 불일치, Source node dummy 고정, 승인 자산 0개를 신규 구현 전 P0 선행 항목으로 기록
- 문서 RAG → 티켓/메일 분류·초안 → 승인형 실행 순서와 공통 평가 기준 제안

## 2026-07-11 — Enterprise Document RAG executable example

- Langflow `1.8.2` / LFX `0.3.4` 실제 runtime 계약으로 `enterprise_document_rag_flow` 신규 구현
- 문서 정규화, PII baseline guard, stable chunk/index, trusted request context, ACL 선필터 검색, 품질 gate, prompt 경계, 근거 답변, 인용 재구성의 9개 Standalone Component 추가
- 별도 모델 key·Vector DB 없이 실행할 수 있는 demo corpus와 `payload_lexical_v1` 검색 경로 제공
- 권한 밖 문서 존재 여부 비노출, 근거 부족 거절, 잘못된 LLM evidence ID 폐기, server-side citation 재구성 규칙 반영
- 단일 Flow JSON과 4개 Flow 전체 Bundle, 포트 연결 가이드, 운영 전환 기준, 대표 질문·문서 샘플 추가
- 전용 Component/Flow HTML 설명서를 통합 포털과 registry에 연결
