# Business Agent Design

사용자가 자연어로 업무를 설명하면 현재 업무(BEFORE)와 Agent 적용 후 업무(AFTER)를 연결된 Flow Chart로 설계하고, 변경 node별 구현 방법을 HTML로 보여 주는 Langflow 서비스입니다.

사내에서 실제로 수요가 높은 Agent 업무, 기업 Agent 제품의 공통 기능, Agent Ground의 현재 공백과 다음 개발 우선순위는 [`ENTERPRISE_AGENT_FLOW_COMPONENT_RESEARCH.md`](ENTERPRISE_AGENT_FLOW_COMPONENT_RESEARCH.md)에 정리했습니다.

## 현재 상태

- 상태: `user_testing`
- 기준 환경: Langflow `1.8.2`
- 전체 Canvas: 24 nodes / 34 connections
- 메인 Standalone Component: 10개 (`00`~`09`)
- 카탈로그 운영 Standalone Component: 5개 (`2.1`~`2.5`)
- LLM 응답 오류 시 deterministic fallback graph 생성
- 실제 사용자 Agent Builder 실행 확인 전에는 `approved`로 전환하지 않음

## 바로 Import할 JSON

| 파일 | 용도 |
| --- | --- |
| [`flow/business_agent_design_complete.json`](flow/business_agent_design_complete.json) | Business Agent 메인 Flow와 카탈로그 운영 Flow를 한 Canvas에서 보는 개별 Import 파일 |
| [`flow/00_business_agent_design_ALL_FLOWS.json`](flow/00_business_agent_design_ALL_FLOWS.json) | Langflow 전체 Flow Import 화면용 Business Bundle |
| [`../flows/00_AGENT_GROUND_ALL_FLOWS.json`](../flows/00_AGENT_GROUND_ALL_FLOWS.json) | 재사용 데이터, HTML 리포트, 문서 RAG, Skill Agent 상위·회의 하위 Flow, Business Agent Design까지 6개를 한 번에 넣는 프로젝트 Bundle |

개별 파일은 최상위가 `{ "data": ... }`인 Langflow Flow입니다. Bundle은 반드시 `{"flows":[`로 시작하며, 세 Import 파일 모두 UTF-8 BOM 없이 생성됩니다.

## 실행 Flow

```text
업무 설명 입력
→ 업무 구조화 Prompt + Agent
→ 업무 프로필 검증
→ approved 카탈로그 검색 (MongoDB 없으면 승인된 seed)
→ Agent 설계 Prompt + Agent
→ BEFORE/AFTER graph 정규화·검증
→ 분기형 Flow Chart HTML Renderer
→ 사용자 요약 / HTML 원문 / 선택적 공유 링크
```

두 번째 Canvas 영역의 `2.1`~`2.5` node는 카탈로그 원문을 JSON으로 정규화하여 MongoDB에 upsert하는 운영자용 보조 Flow입니다. 실제 추천 조회는 `status=approved` 항목만 사용합니다.

## 결과 계약

- BEFORE와 AFTER 각각 `nodes`와 `edges`를 가진 graph
- `start`, `process`, `decision`, `merge`, `human_review`, `end` node
- Decision에서 나가는 모든 edge의 `branch_label`과 `condition`
- 동일 업무 단계 비교를 위한 `comparison_key`
- `unchanged`, `modified`, `added`, `human_review`, `removed` 상태
- AFTER 변경 node와 `improvement_detail_id`의 1:1 연결
- SVG 연결선, decision 마름모, 상태 색상·텍스트, `개선 설명` 버튼

Renderer는 LLM이 만든 HTML을 실행하지 않습니다. 검증된 graph JSON의 텍스트를 escape하고, Renderer가 소유한 고정 script 하나만 node 위치·연결선·상세 패널 전환에 사용합니다.

## 실행 전 설정

1. JSON을 Langflow 1.8.2에 Import합니다.
2. 두 Agent node에서 사용할 Model Provider와 인증 정보를 설정합니다.
3. 먼저 `Mongo URI`를 비워 둔 채 seed fallback으로 실행합니다.
4. Playground의 `Text Input`에 실제 업무 설명을 입력합니다.
5. 결과 요약 또는 HTML 원문을 확인합니다.
6. 공유 링크가 필요할 때만 기존 `html_report_flow/report_api`를 실행하고 `09 공유 링크 발행`을 사용합니다.

연결 상세는 [`CONNECTION_GUIDE.md`](CONNECTION_GUIDE.md), graph 스키마와 완료 기준은 [`BUSINESS_AGENT_DESIGN_IMPLEMENTATION_SPEC.md`](BUSINESS_AGENT_DESIGN_IMPLEMENTATION_SPEC.md), 다음 Flow/Component 우선순위는 [`ENTERPRISE_AGENT_FLOW_COMPONENT_RESEARCH.md`](ENTERPRISE_AGENT_FLOW_COMPONENT_RESEARCH.md)에서 확인합니다.

## 재생성과 검증

```powershell
python scripts\build_business_agent_design_flow.py
python -m unittest discover -s business_agent_design\tests -p "test_*.py" -v
python scripts\build_site.py
python scripts\validate_project.py
```

생성기는 이전 작업에서 확인된 Langflow 1.8.2 UI 계약에 맞춰 edge handle 문자열의 quote delimiter로 `œ`를 사용하고, 각 문자열을 `JSON.parse(text.replaceAll("œ", '"'))`와 같은 방식으로 다시 해석할 수 있는지 검사합니다. 오류가 났던 구분자 `┇`는 허용하지 않습니다.
