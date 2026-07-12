# 업무 AI Agent 설계 Flow 연결 가이드

이 가이드는 Langflow 캔버스에서 어떤 노드의 어떤 출력 포트를 어떤 입력 포트에 연결해야 하는지 설명합니다.

## 0. 가장 빠른 확인: JSON Import

- 개별 Flow: [`flow/business_agent_design_complete.json`](flow/business_agent_design_complete.json)
- Business Bundle: [`flow/00_business_agent_design_ALL_FLOWS.json`](flow/00_business_agent_design_ALL_FLOWS.json)
- Agent Ground 6개 Flow Bundle: [`../flows/00_AGENT_GROUND_ALL_FLOWS.json`](../flows/00_AGENT_GROUND_ALL_FLOWS.json)

개별 Flow를 Import하면 메인 영역과 카탈로그 운영 영역이 한 Canvas에 표시됩니다. 기본 실행 경로는 Report API가 없어도 확인할 수 있도록 `07 사용자 요약 출력 → Chat Output`으로 연결되어 있습니다. `08 HTML 원문 출력`과 `09 공유 링크 발행`은 필요할 때 Chat Output 연결을 바꾸어 사용합니다.

Import 후 두 Agent node의 Model Provider만 실제 환경에 맞게 지정하고, `Text Input`에 업무 설명을 넣어 실행합니다. Mongo URI는 첫 확인에서 비워 두어도 됩니다.

## 1. 컴포넌트 등록

Langflow 커스텀 컴포넌트 경로에 아래 폴더를 추가합니다.

```text
C:\Users\<사용자명>\Desktop\Agent_ground\business_agent_design\components
```

화면에 컴포넌트가 보이지 않으면 Langflow를 새로고침하거나 서버를 재시작합니다.

각 컴포넌트 py 파일은 로컬 공통 모듈을 import하지 않는 standalone 방식입니다. 다만 모든 로직을 통째로 반복하지 않고, 해당 노드 실행에 필요한 최소 helper만 파일 내부에 포함합니다. 따라서 `_service_common.py` 같은 별도 로컬 모듈을 함께 넣거나 Python path를 추가로 수정하지 않아도 됩니다.

## 2. 메인 Flow: LLM 없이 빠른 확인

LLM 노드 없이 fallback 설계와 HTML 렌더링만 확인할 때 사용합니다.

| 순서 | From | Output | To | Input |
| --- | --- | --- | --- | --- |
| 1 | 00 업무 설명 입력 | 업무 요청 | 02 업무 구조화 결과 정리 | 업무 요청 |
| 2 | 02 업무 구조화 결과 정리 | 업무 구조화 결과 | 03 MongoDB 기능/사례 검색 | 업무 구조화 결과 |
| 3 | 03 MongoDB 기능/사례 검색 | 추천 컨텍스트 | 05 AI Agent 설계 결과 검증 | 추천 컨텍스트 |
| 4 | 05 AI Agent 설계 결과 검증 | AI Agent 설계 결과 | 06 HTML 업무 Flow 렌더링 | AI Agent 설계 결과 |
| 5 | 06 HTML 업무 Flow 렌더링 | HTML 생성 결과 | 07 사용자 요약 출력 | HTML 생성 결과 |
| 6 | 07 사용자 요약 출력 | 요약 메시지 | Chat Output | input |

HTML 원문을 확인하려면 5번 뒤에 아래 연결을 추가합니다.

| 순서 | From | Output | To | Input |
| --- | --- | --- | --- | --- |
| 7 | 06 HTML 업무 Flow 렌더링 | HTML 생성 결과 | 08 HTML 원문 출력 | HTML 생성 결과 |
| 8 | 08 HTML 원문 출력 | HTML 원문 | Chat Output | input |

다운로드 링크를 바로 받고 싶으면 `html_report_flow`의 Report API 서버를 실행한 뒤 아래처럼 연결합니다.

```powershell
cd C:\Users\<사용자명>\Desktop\Agent_ground\flows\html_report_flow\report_api
python server.py
```

| 순서 | From | Output | To | Input |
| --- | --- | --- | --- | --- |
| 7 | 06 HTML 업무 Flow 렌더링 | HTML 생성 결과 | 09 공유 링크 발행 | HTML 생성 결과 |
| 8 | 09 공유 링크 발행 | 다운로드 링크 메시지 | Chat Output | input |

`09 공유 링크 발행`의 `Report API 주소`는 기본값 `http://127.0.0.1:8010`을 그대로 쓰면 됩니다.
다른 PC나 서버에서 접근해야 하면 `html_report_flow/report_api/server.py`의 `BASE_URL`과 이 입력값을 실제 접속 가능한 주소로 맞춥니다.

## 3. 메인 Flow: Prompt Template + Agent 포함 권장 연결

업무 구조화와 AI Agent 설계를 Langflow 기본 `Prompt Template`과 `Agent`로 더 풍부하게 만들도록 연결하는 방식입니다.
`01 업무 구조화 프롬프트 변수 준비`는 완성된 프롬프트를 만들지 않고, Prompt Template에 넣을 변수만 제공합니다.

### 3.1 업무 구조화 Prompt Template 생성

Langflow 기본 컴포넌트에서 `Prompt Template`을 하나 추가하고,
[01 업무 구조화 Prompt Template](prompts/01_BUSINESS_PROFILE_PROMPT_TEMPLATE.md)의 `프롬프트` 코드블록을 그대로 붙여넣습니다.

Prompt Template을 저장하면 아래 변수 입력 포트가 생깁니다.

| Prompt Template 변수 | 연결할 값 |
| --- | --- |
| `work_description` | `01 업무 구조화 프롬프트 변수 준비`의 `업무 설명` |
| `profile_instructions` | `01 업무 구조화 프롬프트 변수 준비`의 `구조화 지침` |
| `profile_output_schema` | `01 업무 구조화 프롬프트 변수 준비`의 `출력 스키마 JSON` |

### 3.2 AI Agent 설계 Prompt Template 생성

Langflow 기본 컴포넌트에서 `Prompt Template`을 하나 더 추가하고,
[04 AI Agent 설계 Prompt Template](prompts/04_AGENT_DESIGN_PROMPT_TEMPLATE.md)의 `프롬프트` 코드블록을 그대로 붙여넣습니다.

Prompt Template을 저장하면 아래 변수 입력 포트가 생깁니다.

| Prompt Template 변수 | 연결할 값 |
| --- | --- |
| `business_profile_json` | `04 AI Agent 설계 프롬프트 변수 준비`의 `업무 프로필 JSON` |
| `catalog_items_json` | `04 AI Agent 설계 프롬프트 변수 준비`의 `추천 카탈로그 JSON` |
| `recommendation_trace_json` | `04 AI Agent 설계 프롬프트 변수 준비`의 `추천 근거 JSON` |
| `design_instructions` | `04 AI Agent 설계 프롬프트 변수 준비`의 `설계 지침` |
| `design_output_schema` | `04 AI Agent 설계 프롬프트 변수 준비`의 `출력 스키마 JSON` |

설계 응답에는 `flow_visualization.before/after`, `change_map`, `improvement_details`가 포함되어야 합니다. Decision node에서 나가는 모든 edge에는 `branch_label`과 `condition`이 필요합니다.
`improvement_details`는 AFTER의 변경 node에 있는 `개선 설명` 버튼과 연결되며, 변경 이유, 구현 방법, 사용할 자산, 포트 연결, 검증 기준과 사람 확인 지점을 표시합니다.
카탈로그 항목의 `source_links`는 추천 결과에 보존되며 HTML의 `추천 Flow / Component` 섹션에서 확인합니다.

### 3.3 전체 연결

| 순서 | From | Output | To | Input |
| --- | --- | --- | --- | --- |
| 1 | 00 업무 설명 입력 | 업무 요청 | 01 업무 구조화 프롬프트 변수 준비 | 업무 요청 |
| 2 | 01 업무 구조화 프롬프트 변수 준비 | 업무 설명 | 업무 구조화 Prompt Template | `work_description` |
| 3 | 01 업무 구조화 프롬프트 변수 준비 | 구조화 지침 | 업무 구조화 Prompt Template | `profile_instructions` |
| 4 | 01 업무 구조화 프롬프트 변수 준비 | 출력 스키마 JSON | 업무 구조화 Prompt Template | `profile_output_schema` |
| 5 | 업무 구조화 Prompt Template | Prompt 또는 Message | Agent | input |
| 6 | 00 업무 설명 입력 | 업무 요청 | 02 업무 구조화 결과 정리 | 업무 요청 |
| 7 | Agent | text 또는 message | 02 업무 구조화 결과 정리 | Agent/LLM 구조화 응답 |
| 8 | 02 업무 구조화 결과 정리 | 업무 구조화 결과 | 03 MongoDB 기능/사례 검색 | 업무 구조화 결과 |
| 9 | 03 MongoDB 기능/사례 검색 | 추천 컨텍스트 | 04 AI Agent 설계 프롬프트 변수 준비 | 추천 컨텍스트 |
| 10 | 04 AI Agent 설계 프롬프트 변수 준비 | 업무 프로필 JSON | AI Agent 설계 Prompt Template | `business_profile_json` |
| 11 | 04 AI Agent 설계 프롬프트 변수 준비 | 추천 카탈로그 JSON | AI Agent 설계 Prompt Template | `catalog_items_json` |
| 12 | 04 AI Agent 설계 프롬프트 변수 준비 | 추천 근거 JSON | AI Agent 설계 Prompt Template | `recommendation_trace_json` |
| 13 | 04 AI Agent 설계 프롬프트 변수 준비 | 설계 지침 | AI Agent 설계 Prompt Template | `design_instructions` |
| 14 | 04 AI Agent 설계 프롬프트 변수 준비 | 출력 스키마 JSON | AI Agent 설계 Prompt Template | `design_output_schema` |
| 15 | AI Agent 설계 Prompt Template | Prompt 또는 Message | Agent | input |
| 16 | 03 MongoDB 기능/사례 검색 | 추천 컨텍스트 | 05 AI Agent 설계 결과 검증 | 추천 컨텍스트 |
| 17 | Agent | text 또는 message | 05 AI Agent 설계 결과 검증 | Agent/LLM 설계 응답 |
| 18 | 05 AI Agent 설계 결과 검증 | AI Agent 설계 결과 | 06 HTML 업무 Flow 렌더링 | AI Agent 설계 결과 |
| 19 | 06 HTML 업무 Flow 렌더링 | HTML 생성 결과 | 07 사용자 요약 출력 | HTML 생성 결과 |
| 20 | 07 사용자 요약 출력 | 요약 메시지 | Chat Output | input |
| 21 | 06 HTML 업무 Flow 렌더링 | HTML 생성 결과 | 08 HTML 원문 출력 | HTML 생성 결과 |
| 22 | 08 HTML 원문 출력 | HTML 원문 | Chat Output | input |
| 23 | 06 HTML 업무 Flow 렌더링 | HTML 생성 결과 | 09 공유 링크 발행 | HTML 생성 결과 |
| 24 | 09 공유 링크 발행 | 다운로드 링크 메시지 | Chat Output | input |

Agent는 2개를 쓰는 것이 가장 명확합니다.
첫 번째 Agent는 업무 설명을 구조화하고, 두 번째 Agent는 추천 컨텍스트를 바탕으로 개선 설계를 만듭니다.
하나의 Agent를 재사용해도 되지만, 디버깅할 때 두 역할의 응답이 섞이기 쉬우므로 체험 Flow에서는 분리 구성을 권장합니다.

### 3.4 다운로드 링크 출력

다운로드 링크 출력은 기존 [html_report_flow Report API](../html_report_flow/report_api/server.py)를 그대로 사용합니다.
먼저 아래 서버를 실행합니다.

```powershell
cd C:\Users\<사용자명>\Desktop\Agent_ground\flows\html_report_flow\report_api
python server.py
```

`09 공유 링크 발행` 입력값:

| 입력 | 값 |
| --- | --- |
| `HTML 생성 결과` | `06 HTML 업무 Flow 렌더링`의 `HTML 생성 결과` |
| `Report API 주소` | `http://127.0.0.1:8010` |
| `링크 유효시간` | `24` |

출력 메시지에는 다운로드 링크와 만료 시간이 표시됩니다.
같은 서버를 쓰기 때문에 `html_report_flow`에서 생성한 리포트와 `business_agent_design_flow`에서 생성한 HTML 업무 Flow가 같은 `report_api/storage/reports` 폴더에 저장됩니다.

## 4. 00 업무 설명 입력 예시

```text
매일 아침 생산 실적과 불량 데이터를 엑셀로 내려받아 설비별 이상 징후를 확인합니다.
불량률이 전일 대비 급증한 설비는 최근 작업 이력과 정비 이력을 같이 조회해서 원인 후보를 정리합니다.
결과는 아침 회의 전에 팀장에게 공유하고, 긴급 건은 담당자에게 메일 초안을 작성합니다.
메일은 자동 발송하지 말고 사람이 확인한 뒤 보내야 합니다.
```

## 5. MongoDB 설정

`03 MongoDB 기능/사례 검색`의 고급 입력값:

| 입력 | 설명 | 기본값 |
| --- | --- | --- |
| Mongo URI | MongoDB 연결 URI | 비워두면 seed fallback |
| DB 이름 | 카탈로그 DB | `business_agent_design` |
| 기능 컬렉션 | 기능 목록 컬렉션 | `agent_capability_catalog` |
| 사례 컬렉션 | 개선 사례 컬렉션 | `agent_improvement_cases` |
| 검색 개수 | 상위 검색 개수 | `8` |

MongoDB 연결이 없어도 Flow는 동작합니다.
이 경우 `03` 노드는 기본 seed 카탈로그를 사용합니다.
fallback 여부는 `추천 컨텍스트.catalog_context.catalog_meta.source`, `추천 컨텍스트.catalog_context.catalog_meta.retrieval_status`, `추천 컨텍스트.catalog_context.catalog_meta.fallback_reason`에서 확인할 수 있습니다.
추천 근거 추적용 값은 `추천 컨텍스트.catalog_context.recommendation_trace.retrieval_source`와 `추천 컨텍스트.catalog_context.recommendation_trace.trace_id`에 남습니다.

## 6. 카탈로그 등록 Flow 연결

운영자용 Flow입니다.

### 6.1 카탈로그 변환 Prompt Template 생성

Langflow 기본 컴포넌트에서 `Prompt Template`을 하나 추가하고,
[2.2 카탈로그 JSON 변환 Prompt Template](prompts/02_2_CATALOG_JSON_PROMPT_TEMPLATE.md)의 `프롬프트` 코드블록을 그대로 붙여넣습니다.

Prompt Template을 저장하면 아래 변수 입력 포트가 생깁니다.

| Prompt Template 변수 | 연결할 값 |
| --- | --- |
| `raw_catalog_text` | `2.2 카탈로그 JSON 프롬프트 변수 준비`의 `카탈로그 원문` |
| `operator_note` | `2.2 카탈로그 JSON 프롬프트 변수 준비`의 `운영자 메모` |
| `catalog_instructions` | `2.2 카탈로그 JSON 프롬프트 변수 준비`의 `카탈로그 변환 지침` |
| `catalog_output_schema` | `2.2 카탈로그 JSON 프롬프트 변수 준비`의 `출력 스키마 JSON` |

### 6.2 전체 연결

| 순서 | From | Output | To | Input |
| --- | --- | --- | --- | --- |
| 1 | 2.1 카탈로그 원문 입력 | 카탈로그 원문 데이터 | 2.2 카탈로그 JSON 프롬프트 변수 준비 | 카탈로그 원문 데이터 |
| 2 | 2.2 카탈로그 JSON 프롬프트 변수 준비 | 카탈로그 원문 | 카탈로그 변환 Prompt Template | `raw_catalog_text` |
| 3 | 2.2 카탈로그 JSON 프롬프트 변수 준비 | 운영자 메모 | 카탈로그 변환 Prompt Template | `operator_note` |
| 4 | 2.2 카탈로그 JSON 프롬프트 변수 준비 | 카탈로그 변환 지침 | 카탈로그 변환 Prompt Template | `catalog_instructions` |
| 5 | 2.2 카탈로그 JSON 프롬프트 변수 준비 | 출력 스키마 JSON | 카탈로그 변환 Prompt Template | `catalog_output_schema` |
| 6 | 카탈로그 변환 Prompt Template | Prompt 또는 Message | Agent | input |
| 7 | 2.1 카탈로그 원문 입력 | 카탈로그 원문 데이터 | 2.3 카탈로그 JSON 검증 | 카탈로그 원문 데이터 |
| 8 | Agent | text 또는 message | 2.3 카탈로그 JSON 검증 | Agent/LLM 카탈로그 응답 |
| 9 | 2.3 카탈로그 JSON 검증 | 카탈로그 항목 | 2.4 MongoDB 카탈로그 저장 | 카탈로그 항목 |
| 10 | 2.4 MongoDB 카탈로그 저장 | 저장 결과 | 2.5 카탈로그 저장 결과 출력 | 저장 결과 |
| 11 | 2.5 카탈로그 저장 결과 출력 | 저장 결과 메시지 | Chat Output | input |

Agent 없이 형식만 테스트하려면 아래처럼 연결합니다.

| 순서 | From | Output | To | Input |
| --- | --- | --- | --- | --- |
| 1 | 2.1 카탈로그 원문 입력 | 카탈로그 원문 데이터 | 2.3 카탈로그 JSON 검증 | 카탈로그 원문 데이터 |
| 2 | 2.3 카탈로그 JSON 검증 | 카탈로그 항목 | 2.4 MongoDB 카탈로그 저장 | 카탈로그 항목 |
| 3 | 2.4 MongoDB 카탈로그 저장 | 저장 결과 | 2.5 카탈로그 저장 결과 출력 | 저장 결과 |
| 4 | 2.5 카탈로그 저장 결과 출력 | 저장 결과 메시지 | Chat Output | input |

## 7. 카탈로그 원문 입력 예시

```text
HTML 리포트 생성 Flow는 데이터 조회 결과를 KPI 카드, 그래프, 표가 포함된 HTML 대시보드로 만들어준다.
회의 전 공유나 분석 결과 확인 업무에 적합하다.
외부 공유 전에는 사람이 내용을 확인해야 한다.
```

## 8. 출력 확인 포인트

- `03 추천 컨텍스트`에 `recommendation_trace`가 포함되는지 확인합니다.
- `05 AI Agent 설계 결과`에 `design_validation.passed=true`가 포함되는지 확인합니다.
- `06 HTML 생성 결과`의 `security_report.passed`가 `true`인지 확인합니다.
- `08 HTML 원문`은 복사해 `.html` 파일로 저장하면 브라우저에서 열 수 있습니다.
