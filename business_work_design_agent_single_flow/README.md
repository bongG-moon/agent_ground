# 업무 설명 기반 단일 Flow

Langflow **1.11.0**에서 업무 설명과 기능 카탈로그 JSON 한 개를 입력하면, 관련 후보 100개를 로컬에서 찾고 1차 LLM이 Canvas에서 정한 최대 수만 **후속 설계 검토 후보**로 선별합니다. 이후 2차 LLM은 그 후보 범위 안에서만 실제 적용 여부를 다시 판단하며, 업무와 무관한 후보를 사용하지 않은 채 self-contained HTML 보고서를 만들 수 있습니다.

이 프로젝트는 이전의 F00/F10/F20/F30 연결 구조를 대체하기 위한 별도 폴더입니다. 이전 프로젝트의 MongoDB, embedding, Human Input, Run Flow, tenant/session/revision 상태를 사용하지 않습니다.

## 구조

```text
00 업무 설명 입력 ───────┐
                          ├─ 02 관련 기능 카탈로그 검색(상위 100개) ─ 03 1차 Prompt ─┐
01 카탈로그 JSON 파일 ───┘                                                           │
04 Language Model(모델 설정) ─────────────────────────────────────────────────────────┼─ 05 1차 설계 JSON ─ 06 1차 검증
00 업무 설명 입력 ───────────────────────────────────────────────────────────────────┘                       │
                                                                                                             07 품질 점검·보완 Prompt
04 Language Model(같은 모델 객체) ───────────────────────────────────────────────────────────────────────────┼─ 08 최종 설계 JSON 보완
                                                                                                             │
                                                                                                             09 최종 정규화·검증
                                                                                                             │
                                                                                                             10 화면 계약 → 11 HTML → 12 선택 게시
                                                                                                                              ┌───┴────┐
                                                                                                                        13 안내 메시지  14 결과 Data
                                                                                                                              │
                                                                                                                        15 Chat Output
```

- 실행 노드: 16개, 실행 edge: 24개
- 최종 결과: Playground용 **15 Chat Output**과 API/테스트용 **14 Report Artifact Data**
- LLM 호출: 05 1차 업무 설계 JSON 생성과 08 최종 업무 설계 JSON 보완에서 최대 두 번입니다. 04는 같은 provider/model 설정만 전달합니다. 05는 관련 후보 shortlist만 선별하며, 09는 그 후보 범위를 고정합니다. 08은 후보 안에서 실제 적용·검토·미사용을 다시 결정할 수 있습니다.
- 안전한 fallback: 08이 provider·structured output 문제로 실행되지 않으면 09는 같은 요청·카탈로그 후보 집합으로 검증된 06의 결과만 사용합니다.
- 재실행: 보고서의 보완 필요 항목을 보고 00의 업무 설명을 수정한 뒤 Flow 전체를 다시 실행

## Canvas에서 설정할 값

| 위치 | 값 | 설정 방법 |
| --- | --- | --- |
| 00 업무 설명 입력 | 업무 설명 원문 | 긴 텍스트로 직접 입력합니다. 예시가 기본값으로 들어 있습니다. |
| 00 업무 설명 입력 | 추가 설계 요청 | 선택입니다. 예: `사람 승인 유지, 카탈로그 우선` |
| 00 업무 설명 입력 | 최종 설계 보완 지시 | 선택입니다. 1차 설계에는 넣지 않고 07·08의 두 번째 품질 보완에만 반영합니다. 예: `분기와 예외 경로를 더 구체적으로 표시` |
| 01 기능 카탈로그 JSON 파일 | 기능 카탈로그 JSON | UTF-8 JSON 파일 하나를 업로드합니다. |
| 02 관련 기능 카탈로그 검색 | 검색 후보 수 | 기본 100입니다. 키워드·BM25·문자 n-gram RRF로 찾는 후보 풀이며, 적용 확정 수가 아닙니다. |
| 02 관련 기능 카탈로그 검색 | LLM 상세 검토 후보 수 | 기본 12개, 범위는 1~30개입니다. 상위 후보의 README·기능·제약·포트 요약을 더 많이 전달할 때 조정합니다. 100개 전체의 압축 후보 인덱스는 이 값과 관계없이 유지됩니다. |
| 03 1차 업무 설계 요청 구성 | LLM 선별 후보 최대 수 | 기본 12개, 범위는 1~30개입니다. 1차 LLM이 후속 설계에 전달할 관련 카탈로그 후보 shortlist의 최대 개수입니다. 맞는 후보가 적으면 억지로 채우지 않으며, 선별되었다고 해서 실제 적용되지는 않습니다. |
| 04 Language Model (모델 설정) | provider/model/credential | Structured Output 또는 tool calling을 지원하는 운영 모델을 선택합니다. 32k 이상 context를 권장합니다. |
| 05 / 08 업무 설계 JSON 생성 | 설정 불필요 | 두 standalone custom component 소스에 고정 Pydantic 업무 설계 계약과 시스템 지시가 내장됩니다. 05는 1차 초안과 관련 후보 shortlist 선별, 08은 07의 품질 점검을 반영한 최종 설계·실제 적용 여부 판단을 수행합니다. 09는 shortlist 범위만 고정하므로 08은 무관한 후보를 새로 가져올 수 없지만, 후보를 사용하지 않을 수 있습니다. |
| 12 보고서 링크 게시 | Report API URL | 비워 두면 HTML만 생성합니다. 입력하면 선택적으로 게시합니다. 기존 Report API의 엄격한 요청 계약을 사용합니다. |

JSON 형식은 프롬프트 지시만으로 기대하지 않습니다. `05 1차 업무 설계 JSON 생성`과 `08 최종 업무 설계 JSON 보완`은 provider의 native structured-output 기능에 고정 Pydantic 객체를 먼저 전달해 JSON Data 하나를 받도록 구성되어 있습니다. native JSON schema 기능만 미지원·거부된 경우에는 같은 고정 지시로 일반 모델 호출을 한 번 수행하고, **응답 전체가 JSON object인지**와 Pydantic 계약을 다시 검증합니다. 문장 중 일부 중괄호를 찾아내거나 자유형 설명문을 설계 JSON으로 추정하지 않습니다.

05의 provider 호출 실패는 원인 유형과 안전하게 정리된 문구로 중단됩니다. 반면 08의 보완 호출 실패는 1차 검증 결과를 대체하지 않습니다. 09가 동일 요청·후보 집합을 확인한 뒤 1차 결과로 보고서를 완성하고, 보고서에는 `기본 초안 사용`만 간결하게 표시합니다. API key·토큰·Authorization 값은 오류와 trace에 표시하지 않습니다.

`12 보고서 링크 게시`은 기존 Report API의 closed request contract에 맞춰 `html`, `title`, `question`, `view_request`, `available_datasets`, `report_plan`, `ttl_hours`, `filename_hint`만 보냅니다. Renderer의 report ID, version, HTML hash는 허용된 `report_plan` 안에 보관하므로, API가 최상위 미등록 필드를 거부하는 환경에서도 게시할 수 있습니다. HTML 크기도 해당 API의 기본 한도인 10 MiB를 넘기기 전에 Flow에서 명확히 차단합니다.

## 기능 카탈로그 파일 형식

카탈로그 항목은 적어도 `id`, `title`, `type`, `description`, `category`, `version`을 가집니다. `id`는 UUID여야 합니다.

```json
{
  "id": "4deabfbd-b270-49ee-92e5-38b86cc5f908",
  "title": "식당 메뉴 검색 봇",
  "type": "py",
  "description": "식당과 날짜 정보를 입력받아 사내 식당 메뉴를 검색하고 보여줍니다.",
  "category": "Utility",
  "version": "v1.1.1"
}
```

상세 링크는 파일 안의 URL이 아니라 `id`와 `type`으로 안전하게 다시 만듭니다.

- `type: "py"` 또는 `component` → `https://agent-hub.skhynix.com/#/component/{id}`
- `type: "json"` 또는 `flow` → `https://agent-hub.skhynix.com/#/flow/{id}`

## 결과 해석

보고서에는 다음이 포함됩니다.

- 사용자가 입력한 안전한 업무 설명 원문
- 현재 업무(AS-IS) 단계, 분기, 예외, 문제점
- 다음 실행 전에 설명에 넣으면 좋은 보완 항목과 문장 예시
- 카탈로그 후보 전체의 선택/검토/미사용 구분과 Agent Hub 링크
- 카탈로그 기반 TO-BE 업무 Flow, 사람이 검토할 지점, 오류·재시도 처리
- 구현 단계, 위험 통제, 테스트 시나리오
- 선택한 최종 설계 보완 지시와 2차 보완 반영 여부(기술 trace·provider 오류는 표시하지 않음)

업무 설명이 부족해도 Flow는 멈추지 않습니다. 결과 상태는 `COMPLETED_WITH_GAPS`가 될 수 있으며, 이 경우 보고서의 보완 문장을 00 입력에 반영하고 다시 Run 하면 됩니다.

## 생성 및 검증

운영 서버의 Langflow 1.11.0 가상환경으로 실행합니다.

```powershell
& 'C:\Users\qkekt\AppData\Local\com.LangflowDesktop\.langflow-venv\Scripts\python.exe' .\scripts\build_single_flow.py
& 'C:\Users\qkekt\AppData\Local\com.LangflowDesktop\.langflow-venv\Scripts\python.exe' .\scripts\validate_single_flow_1_11_0.py
```

첫 명령은 `flows\F01_business_work_design_single.json`에 현재 standalone component source를 embed합니다. 05와 08의 고정 시스템 지시는 각각의 standalone source 안에 있으므로 Langflow 1.11 import·refresh 때 Canvas 템플릿 값이 초기화되어도 사라지지 않습니다. 둘째 명령은 다음을 확인합니다.

- 실제 Langflow/LFX 1.11.x runtime
- custom component 하나당 독립 파일 하나와 local import 부재
- Flow JSON에 embed된 source와 파일 source의 byte/hash 일치
- 16개 노드, 23개 edge, required 연결 input의 정확히 하나의 upstream
- Run Flow/HITL/MongoDB/embedding node와 입력 부재
- 04 모델 설정 → 05 업무 설계 JSON 생성의 고정 Pydantic 계약 연결과 Chat Output 비저장 설정
- LFX `Graph.from_payload` import와 재생성 drift

Custom component를 수정했으면 Flow JSON을 수동 수정하지 말고 위의 build → validate 순서로 다시 생성하세요.

## LLM 없이 HTML 화면 확인

실제 provider 호출 없이도 복잡한 업무·100개 카탈로그·고정 모델 응답을 사용해 보고서 화면을 재현할 수 있습니다.

```powershell
& 'C:\Users\qkekt\AppData\Local\com.LangflowDesktop\.langflow-venv\Scripts\python.exe' .\scripts\render_sample_report.py
```

예를 들어 상세 후보 20개까지 함께 확인하려면 `--expanded-detail-count 20`을 추가합니다.

이 명령은 `00 → 01 → 02 → 1차 정규화(mock response) → 최종 정규화(mock response) → 화면 계약 → HTML 생성`만 실행하고, 결과를 `samples\generated_sample_report.html`에 만듭니다. 실제 03/04/05/07/08 LLM 경로, MongoDB, embedding, Report API는 호출하지 않습니다.

## 폴더

| 경로 | 용도 |
| --- | --- |
| `docs\SINGLE_FLOW_REBUILD_SPECIFICATION.md` | 구현 기준 상세 명세 |
| `components\single_flow\` | 서로 import하지 않는 standalone custom component source |
| `prompts\single_flow_business_design.md` | 업무 설계 지시의 작성·검토 기준 문서 (05의 실행 지시는 standalone source에 내장) |
| `flows\F01_business_work_design_single.json` | Langflow import artifact |
| `scripts\build_single_flow.py` | source 기반 Flow export generator |
| `scripts\validate_single_flow_1_11_0.py` | 운영 runtime 구조 검증 |
| `scripts\render_sample_report.py` | LLM 없이 생성 HTML을 재현하는 visual-QA 경로 |
| `samples\` | 복잡 업무와 카탈로그 입력 예시 |
