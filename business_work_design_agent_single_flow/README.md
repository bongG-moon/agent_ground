# 업무 설명 기반 단일 Flow

Langflow **1.11.0**에서 업무 설명과 기능 카탈로그 JSON 한 개를 입력하면, 먼저 키워드·BM25·문자 n-gram 기반의 로컬 검색으로 관련 후보 100개를 찾습니다. 이어서 **전용 LLM 후보 선별 노드**가 Canvas에서 정한 최대 수(기본 12개)만 고정 shortlist로 만들고, 이후 두 설계 LLM은 그 shortlist 안에서만 실제 적용·검토·미사용을 판단합니다. shortlist에 들어갔다고 적용이 확정되는 것은 아니며, 후보를 하나도 쓰지 않고 self-contained HTML 보고서를 만들 수도 있습니다.

이 프로젝트는 이전의 F00/F10/F20/F30 연결 구조를 대체하기 위한 별도 폴더입니다. 이전 프로젝트의 MongoDB, embedding, Human Input, Run Flow, tenant/session/revision 상태를 사용하지 않습니다.

## 구조

```text
00 업무 설명 입력 ───────┐
                          ├─ 02 관련 기능 카탈로그 검색(상위 100개) ─┬─ 03 LLM 카탈로그 후보 선별 ─┬─ 04 업무 설계 요청 구성 ─ 06 1차 설계 JSON ─ 07 1차 정규화
01 카탈로그 JSON 파일 ───┘                                        │                              │                                      │
05 Language Model(공용 설정) ────────────────────────────────────────┴──────────────────────────────┴───────────────────────────────────────┘
00 업무 설명 입력 ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                                                                                                    │
                                                                                                                       08 품질 점검·보완 Prompt
05 Language Model(같은 모델 객체) ────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼─ 09 최종 설계 JSON 보완
                                                                                                                                    │
                                                                                                                       10 최종 정규화·검증
                                                                                                                                    │
                                                                                                                       11 화면 계약 → 12 HTML → 13 선택 게시
                                                                                                                                                    ┌───┴────┐
                                                                                                                                              14 안내 메시지  15 결과 Data
                                                                                                                                                    │
16 Chat Output
```

`02 → 04`의 내부 Data edge는 03 shortlist의 `asset_id`·`version`을 검색 registry와 대조하고 선택된 항목의 상세 정보를 안전하게 다시 결합하기 위한 것입니다. 02는 상위 후보 12개의 rich context를 내부 고정 한도로만 유지하며, 이 수는 Canvas에서 조정하지 않습니다. 04가 06에 만드는 실제 설계 prompt에는 shortlist 밖 후보를 넣지 않습니다.

- 실행 노드: 17개, 실행 edge: 29개
- 최종 결과: Playground용 **16 Chat Output**과 API/테스트용 **15 Report Artifact Data**
- LLM 호출: 03 후보 선별, 06 1차 업무 설계 JSON 생성, 09 최종 업무 설계 JSON 보완 순서로 최대 세 번입니다. 05는 같은 provider/model 설정만 공급합니다. 03은 shortlist 범위만 고정하고, 06·09는 그 범위 안에서만 실제 적용·검토·미사용을 판단합니다.
- 안전한 fallback: 09가 provider·structured output 문제로 실행되지 않으면 10은 같은 요청·고정 shortlist를 확인한 뒤 검증된 07의 1차 결과로 보고서를 완성합니다.
- 재실행: 보고서의 보완 필요 항목을 보고 00의 업무 설명을 수정한 뒤 Flow 전체를 다시 실행

## Canvas에서 설정할 값

| 위치 | 값 | 설정 방법 |
| --- | --- | --- |
| 00 업무 설명 입력 | 업무 설명 원문 | 긴 텍스트로 직접 입력합니다. 예시가 기본값으로 들어 있습니다. |
| 00 업무 설명 입력 | 추가 설계 요청 | 선택입니다. 예: `사람 승인 유지, 카탈로그 우선` |
| 00 업무 설명 입력 | 최종 설계 보완 지시 | 선택입니다. 1차 설계에는 넣지 않고 08·09의 두 번째 품질 보완에만 반영합니다. 예: `분기와 예외 경로를 더 구체적으로 표시` |
| 01 기능 카탈로그 JSON 파일 | 기능 카탈로그 JSON | UTF-8 JSON 파일 하나를 업로드합니다. |
| 02 관련 기능 카탈로그 검색 | 검색 후보 수 | 기본 100입니다. 키워드·BM25·문자 n-gram RRF로 찾는 후보 풀이며, 적용 확정 수가 아닙니다. 상위 12개 후보의 rich context는 내부 고정 한도로만 사용합니다. |
| 03 LLM 카탈로그 후보 선별 | LLM 선별 후보 최대 수 | 기본 12개, 범위는 1~30개입니다. 02가 검색한 100개에서 후속 설계가 볼 **고정 shortlist**만 만듭니다. 맞는 후보가 적으면 억지로 채우지 않으며, 선별되었다고 해서 실제 적용되지는 않습니다. |
| 05 Language Model (모델 설정) | provider/model/credential | 후보 선별(03), 1차 설계(06), 최종 보완(09)에 같은 `LanguageModel` 객체를 공급합니다. Structured Output 또는 tool calling을 지원하는 운영 모델을 선택합니다. 32k 이상 context를 권장합니다. |
| 04 / 06 / 09 설계 노드 | 설정 불필요 | 04는 02 registry로 03 shortlist의 identity를 검증·상세 보강한 뒤 **고정 shortlist만** 담아 설계 요청을 만듭니다. 06·09는 standalone custom component에 내장된 고정 Pydantic 계약으로 설계 JSON을 만듭니다. 실제 적용은 선택 사항이며 shortlist 밖 후보는 추가할 수 없습니다. |
| 13 보고서 링크 게시 | Report API URL | 비워 두면 HTML만 생성합니다. 입력하면 선택적으로 게시합니다. 기존 Report API의 엄격한 요청 계약을 사용합니다. |

JSON 형식은 프롬프트 지시만으로 기대하지 않습니다. `03 LLM 카탈로그 후보 선별`, `06 1차 업무 설계 JSON 생성`, `09 최종 업무 설계 JSON 보완`은 provider의 native structured-output 기능에 각각 고정 Pydantic 객체를 먼저 전달해 JSON Data 하나를 받도록 구성되어 있습니다. native JSON schema 기능만 미지원·거부된 경우에는 같은 고정 지시로 일반 모델 호출을 한 번 수행하고, **응답 전체가 JSON object인지**와 Pydantic 계약을 다시 검증합니다. 문장 중 일부 중괄호를 찾아내거나 자유형 설명문을 설계 JSON으로 추정하지 않습니다.

03 또는 06의 provider 호출 실패는 원인 유형과 안전하게 정리된 문구로 중단됩니다. 반면 09의 보완 호출 실패는 1차 검증 결과를 대체하지 않습니다. 10이 동일 요청·고정 shortlist를 확인한 뒤 1차 결과로 보고서를 완성하고, 보고서에는 `기본 초안 사용`만 간결하게 표시합니다. API key·토큰·Authorization 값은 오류와 trace에 표시하지 않습니다.

`13 보고서 링크 게시`은 기존 Report API의 closed request contract에 맞춰 `html`, `title`, `question`, `view_request`, `available_datasets`, `report_plan`, `ttl_hours`, `filename_hint`만 보냅니다. Renderer의 report ID, version, HTML hash는 허용된 `report_plan` 안에 보관하므로, API가 최상위 미등록 필드를 거부하는 환경에서도 게시할 수 있습니다. HTML 크기도 해당 API의 기본 한도인 10 MiB를 넘기기 전에 Flow에서 명확히 차단합니다.

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

첫 명령은 `flows\F01_business_work_design_single.json`에 현재 standalone component source를 embed합니다. 03, 06, 09의 고정 시스템 지시는 각각의 standalone source 안에 있으므로 Langflow 1.11 import·refresh 때 Canvas 템플릿 값이 초기화되어도 사라지지 않습니다. 둘째 명령은 다음을 확인합니다.

- 실제 Langflow/LFX 1.11.x runtime
- custom component 하나당 독립 파일 하나와 local import 부재
- Flow JSON에 embed된 source와 파일 source의 byte/hash 일치
- 17개 노드, 29개 edge, required 연결 input의 정확히 하나의 upstream
- Run Flow/HITL/MongoDB/embedding node와 입력 부재
- 05 모델 설정 → 03 후보 선별·06 1차 설계·09 최종 보완의 고정 Pydantic 계약 연결과 Chat Output 비저장 설정
- LFX `Graph.from_payload` import와 재생성 drift

Custom component를 수정했으면 Flow JSON을 수동 수정하지 말고 위의 build → validate 순서로 다시 생성하세요.

## LLM 없이 HTML 화면 확인

실제 provider 호출 없이도 복잡한 업무·100개 카탈로그·고정 모델 응답을 사용해 보고서 화면을 재현할 수 있습니다.

```powershell
& 'C:\Users\qkekt\AppData\Local\com.LangflowDesktop\.langflow-venv\Scripts\python.exe' .\scripts\render_sample_report.py
```

이 명령은 03의 shortlist와 06·09의 설계 결과를 결정론적 fixture로 대체해 `00 → 01 → 02 → shortlist fixture → 1차/최종 정규화 → 화면 계약 → HTML 생성`만 실행하고, 결과를 `samples\generated_sample_report.html`에 만듭니다. 실제 03·06·09 LLM 호출, MongoDB, embedding, Report API는 호출하지 않습니다.

## 폴더

| 경로 | 용도 |
| --- | --- |
| `docs\SINGLE_FLOW_REBUILD_SPECIFICATION.md` | 구현 기준 상세 명세 |
| `components\single_flow\` | 서로 import하지 않는 standalone custom component source |
| `prompts\single_flow_business_design.md` | 업무 설계 지시의 작성·검토 기준 문서 (06·09의 실행 지시는 standalone source에 내장) |
| `flows\F01_business_work_design_single.json` | Langflow import artifact |
| `scripts\build_single_flow.py` | source 기반 Flow export generator |
| `scripts\validate_single_flow_1_11_0.py` | 운영 runtime 구조 검증 |
| `scripts\render_sample_report.py` | LLM 없이 생성 HTML을 재현하는 visual-QA 경로 |
| `samples\` | 복잡 업무와 카탈로그 입력 예시 |
