# HTML 생성 FLOW 상세 연결 가이드

이 문서는 `html_report_flow`를 처음 체험하는 사람이 **Langflow flow 생성부터 로컬 Report API 서버 실행까지** 순서대로 따라 할 수 있게 정리한 가이드입니다.

핵심 목표는 아래 두 가지입니다.

- 사용자가 질문과 데이터를 넣으면 LLM이 요청 의도를 해석해 HTML 리포트 구조를 설계합니다.
- 결과를 Playground에 HTML 원문으로 출력하거나, 로컬 API 서버에 저장해 다운로드 링크로 받을 수 있습니다.

중요한 구분:

- `langflow_components/html_report_flow/*.py` 파일은 Langflow 커스텀 컴포넌트 코드입니다. 이 파일들은 직접 `python 00_...py`처럼 실행하지 않습니다.
- 직접 실행하는 Python 파일은 공유 링크 기능에 쓰는 [report_api/server.py](report_api/server.py)입니다.

## 1. 전체 폴더 구조

기준 폴더:

```text
C:\Users\<사용자명>\Desktop\기능flow\html_report_flow
```

주요 폴더:

| 경로 | 용도 |
| --- | --- |
| `langflow_components/html_report_flow` | Langflow에 등록할 커스텀 컴포넌트 py 파일 |
| `samples` | `00`과 `02`에 넣는 샘플 입력과 통합 예시 문서 |
| `report_api` | HTML 저장/다운로드 링크 생성을 위한 로컬 FastAPI 서버 |
| `docs` | 보조 설명 문서 |

## 2. 체험 전 준비물

필수:

- Python 3.10 이상
- Langflow 실행 환경
- Langflow 기본 `Prompt Template` 또는 `Prompt` 노드 1개
- LLM 노드 1개
- 이 폴더 전체: `C:\Users\<사용자명>\Desktop\기능flow\html_report_flow`

선택:

- Playground에 HTML 원문만 출력할 경우 Report API 서버는 필요 없습니다.
- 다운로드 링크까지 만들 경우 `report_api/server.py`를 실행해야 합니다.

## 3. 결과 출력 방식 선택

처음 테스트할 때는 `05-1 HTML 원문 출력` 방식이 가장 단순합니다.

| 방식 | 사용 노드 | 서버 필요 여부 | 결과 |
| --- | --- | --- | --- |
| HTML 원문 출력 | `05-1 HTML 원문 출력` | 필요 없음 | Playground에 전체 HTML 코드 출력 |
| 다운로드 링크 출력 | `05-2 공유 링크 출력` | 필요 | 다운로드 링크와 만료 시간 출력 |

## 4. Langflow 새 Flow 생성

1. Langflow를 실행합니다.
2. 새 Flow를 생성합니다.
3. Flow 이름을 예를 들어 `HTML Report Generator Demo`로 지정합니다.
4. 아래 커스텀 컴포넌트를 Langflow에 등록합니다.

커스텀 컴포넌트 파일 위치:

```text
C:\Users\<사용자명>\Desktop\기능flow\html_report_flow\langflow_components\html_report_flow
```

등록해야 할 파일:

| 순서 | 파일 | Langflow 표시 이름 |
| --- | --- | --- |
| 00 | `00_demo_report_request_loader.py` | `00 리포트 요청/데이터 불러오기` |
| 01 | `01_data_profile_builder.py` | `01 데이터 구조 분석` |
| 02 | `02_html_component_catalog_builder.py` | `02 기본 요소 양식/추천` |
| 03 | `03_auto_html_plan_builder.py` | `03 기본 리포트 계획` |
| 03a | `03a_llm_html_plan_prompt_builder.py` | `03a 프롬프트 변수 준비` |
| 03b | `03b_llm_html_plan_normalizer.py` | `03b LLM 계획 검증` |
| 04 | `04_html_template_renderer.py` | `04 HTML 렌더링` |
| 05-1 | `05_1_html_source_output.py` | `05-1 HTML 원문 출력` |
| 05-2 | `05_report_api_publisher.py` | `05-2 공유 링크 출력` |

Langflow 환경마다 커스텀 컴포넌트 등록 방식이 조금 다를 수 있습니다. 현재 환경에서 파일을 직접 읽어오거나 코드 붙여넣기로 등록하는 방식이라면, 위 py 파일을 각각 그대로 등록하면 됩니다.

## 5. 샘플 데이터 준비

샘플 입력은 아래 문서 하나에 정리되어 있습니다.

```text
C:\Users\<사용자명>\Desktop\기능flow\html_report_flow\samples\INPUT_EXAMPLES.md
```

이 문서에는 각 예시별로 아래 값이 함께 들어 있습니다.

- `00.질문`
- `00.보고 싶은 방식`
- `00.데이터 직접 입력`에 넣을 파일
- `02.요소 양식 JSON`에 넣을 선택 catalog 파일

| 방식 | 설명 |
| --- | --- |
| 직접 붙여넣기 | `samples/00_data_inputs`의 CSV/JSON 파일 내용을 전체 복사해서 `00.데이터 직접 입력`에 붙여넣기 |
| File Read 사용 | Langflow `Read File` 노드로 CSV를 읽고 `00.파일 데이터`에 연결 |

처음에는 직접 붙여넣기가 가장 단순합니다. File Read 연결 타입 문제가 날 때도 우회할 수 있습니다.

## 6. 기본 Flow 노드 배치

캔버스에 아래 노드를 놓습니다.

필수 커스텀 노드:

```text
00 리포트 요청/데이터 불러오기
01 데이터 구조 분석
02 기본 요소 양식/추천
03 기본 리포트 계획
03a 프롬프트 변수 준비
03b LLM 계획 검증
04 HTML 렌더링
```

Langflow 기본 노드:

```text
Prompt Template 또는 Prompt
LLM 노드
Chat Output
```

출력 방식에 따라 둘 중 하나를 추가합니다.

```text
05-1 HTML 원문 출력
```

또는

```text
05-2 공유 링크 출력
```

## 7. 전체 연결도

### 7.1 Playground에 HTML 원문 출력

```text
00 리포트 요청/데이터 불러오기
-> 01 데이터 구조 분석
-> 02 기본 요소 양식/추천
-> 03 기본 리포트 계획
-> 03a 프롬프트 변수 준비
-> Prompt Template
-> LLM
-> 03b LLM 계획 검증
-> 04 HTML 렌더링
-> 05-1 HTML 원문 출력
-> Chat Output
```

### 7.2 로컬 API 저장 후 다운로드 링크 출력

```text
00 리포트 요청/데이터 불러오기
-> 01 데이터 구조 분석
-> 02 기본 요소 양식/추천
-> 03 기본 리포트 계획
-> 03a 프롬프트 변수 준비
-> Prompt Template
-> LLM
-> 03b LLM 계획 검증
-> 04 HTML 렌더링
-> 05-2 공유 링크 출력
-> Chat Output
```

## 8. 00 노드 설정

노드:

```text
00 리포트 요청/데이터 불러오기
```

직접 CSV 붙여넣기 기준 입력:

| 입력명 | 값 |
| --- | --- |
| `질문` | `공정별 WIP 비교와 날짜별 생산량 추이를 HTML 리포트로 보여줘` |
| `보고 싶은 방식` | `상단에는 KPI 카드, 가운데에는 공정별 비교 그래프와 날짜별 추이 그래프, 아래에는 상세 표를 배치해줘` |
| `데이터 직접 입력` | `sample_wip.csv` 전체 내용 |
| `파일 데이터` | 비워둠 |

File Read 사용 기준 입력:

| From | Output | To | Input |
| --- | --- | --- | --- |
| `Read File` | `Structured Content` | `00 리포트 요청/데이터 불러오기` | `파일 데이터` |

File Read를 사용할 때는 `00.데이터 직접 입력`을 비워둡니다.

`00` 출력 연결:

| From | Output | To | Input |
| --- | --- | --- | --- |
| `00 리포트 요청/데이터 불러오기` | `요청 데이터` | `01 데이터 구조 분석` | `요청 데이터` |
| `00 리포트 요청/데이터 불러오기` | `요청 데이터` | `03 기본 리포트 계획` | `요청 데이터` |

`00`의 역할:

- 질문 원문을 보관합니다.
- 보고 싶은 방식 원문을 보관합니다.
- CSV/JSON/JSONL 텍스트를 row 데이터로 변환합니다.
- 여러 dataset JSON이 들어오면 공통 key 기준의 `joined_auto` 분석 view와, schema가 같은 경우의 `union_auto` view를 만듭니다.
- `visual_request`라는 약한 힌트를 만듭니다.

주의: `visual_request`는 최종 판단이 아닙니다. 사람마다 요청을 쓰는 방식이 다르기 때문에 최종 요청 해석은 LLM이 `request_interpretation`으로 다시 수행합니다.

여러 데이터를 한 리포트에서 같이 보고 싶을 때는 `00.데이터 직접 입력`에 아래처럼 `datasets` 배열을 넣습니다. 단일 데이터만 볼 때는 기존처럼 CSV 전체를 그대로 붙여넣으면 됩니다.

```json
{
  "datasets": [
    {
      "dataset_id": "wip_status",
      "label": "WIP status by process",
      "rows": [
        {"DATE": "2026-06-01", "LINE": "L1", "PROCESS": "DA", "WIP_QTY": 118}
      ]
    },
    {
      "dataset_id": "production_result",
      "label": "Daily output and yield",
      "rows": [
        {"DATE": "2026-06-01", "LINE": "L1", "PROCESS": "DA", "OUTPUT_QTY": 1040, "YIELD_RATE": 97.4}
      ]
    }
  ]
}
```

전체 테스트용 예시는 아래 파일을 열어서 전부 복사하면 됩니다.

```text
C:\Users\<사용자명>\Desktop\기능flow\html_report_flow\samples\00_data_inputs\sample_multi_wip_output_quality.json
```

이 예시는 `DATE`, `LINE`, `PROCESS`가 공통 key라서 `joined_auto` view가 active view로 선택됩니다. LLM은 기본적으로 이 결합 view를 사용하고, 필요하면 특정 블록에 `data_view_id`를 지정해 원본 dataset만 따로 볼 수 있습니다.

## 9. 01 노드 연결

노드:

```text
01 데이터 구조 분석
```

입력:

| 입력명 | 연결 |
| --- | --- |
| `요청 데이터` | `00 리포트 요청/데이터 불러오기.요청 데이터` |

출력 연결:

| From | Output | To | Input |
| --- | --- | --- | --- |
| `01 데이터 구조 분석` | `데이터 분석 결과` | `02 기본 요소 양식/추천` | `데이터 분석 결과` |
| `01 데이터 구조 분석` | `데이터 분석 결과` | `03 기본 리포트 계획` | `데이터 분석 결과` |

`01`의 역할:

- 컬럼 타입을 추정합니다.
- 숫자 컬럼, 범주 컬럼, 날짜 컬럼 후보를 분류합니다.
- 질문에서 추이/비교/상세/요약 힌트를 추출합니다.
- LLM과 렌더러가 사용할 데이터 구조 요약을 만듭니다.

## 10. 02 노드 연결

노드:

```text
02 기본 요소 양식/추천
```

입력:

| 입력명 | 연결 또는 값 |
| --- | --- |
| `데이터 분석 결과` | `01 데이터 구조 분석.데이터 분석 결과` |
| `요소 양식 JSON` | 기본 내장 양식만 쓸 때는 비워둠 |

출력 연결:

| From | Output | To | Input |
| --- | --- | --- | --- |
| `02 기본 요소 양식/추천` | `요소 추천 결과` | `03 기본 리포트 계획` | `요소 추천 결과` |

`02`의 역할:

- 사용할 수 있는 리포트 블록 목록을 준비합니다.
- 데이터 구조와 요청 힌트를 보고 기본 추천 요소를 만듭니다.
- LLM이 참고할 기본 양식, 스타일 기본값, 컴포넌트 설명을 전달합니다.

`요소 양식 JSON`에 넣을 수 있는 예시:

```text
C:\Users\<사용자명>\Desktop\기능flow\html_report_flow\samples\02_component_catalogs
```

예시 파일:

| 파일 | 추천 데이터 | 특징 |
| --- | --- | --- |
| `catalog_operations_compact.json` | `sample_wip.csv` | 운영자용 compact 대시보드 |
| `catalog_executive_summary.json` | `sample_sales_channel_mix.csv` | 임원용 요약, 큰 글자, 적은 블록 |
| `catalog_quality_diagnostics.json` | `sample_quality_diagnostics.csv` | 품질 진단, 분포/산점도/예외 표 |
| `catalog_composition_dashboard.json` | `sample_inventory_flow.csv` | 구성비, 도넛, 누적 막대 중심 |

처음 테스트에서는 `요소 양식 JSON`을 비워두는 것을 권장합니다.

## 11. 03 노드 연결

노드:

```text
03 기본 리포트 계획
```

입력:

| 입력명 | 연결 또는 값 |
| --- | --- |
| `요청 데이터` | `00 리포트 요청/데이터 불러오기.요청 데이터` |
| `데이터 분석 결과` | `01 데이터 구조 분석.데이터 분석 결과` |
| `요소 추천 결과` | `02 기본 요소 양식/추천.요소 추천 결과` |
| `블록 수 제한` | `auto` 권장 |

출력 연결:

| From | Output | To | Input |
| --- | --- | --- | --- |
| `03 기본 리포트 계획` | `기본 계획` | `03a 프롬프트 변수 준비` | `기본 계획` |
| `03 기본 리포트 계획` | `기본 계획` | `03b LLM 계획 검증` | `기본 계획` |

`03`의 역할:

- LLM이 실패하더라도 HTML을 만들 수 있는 기본 계획을 만듭니다.
- 이 기본 계획은 `deterministic_fallback_draft`입니다.
- 최종 구성은 LLM이 원문 질문과 보고 싶은 방식을 보고 다시 설계합니다.

## 12. 03a 노드와 Prompt Template/LLM 연결

노드:

```text
03a 프롬프트 변수 준비
Prompt Template 또는 Prompt
LLM 노드
```

`03a` 입력:

| 입력명 | 연결 또는 값 |
| --- | --- |
| `기본 계획` | `03 기본 리포트 계획.기본 계획` |
| `추가 구현 지시사항` | 선택. 데이터 의미/값/스타일 보강. 예시는 아래 참고 |

`추가 구현 지시사항`에는 디자인뿐 아니라 컬럼과 값의 의미를 직접 적어도 됩니다. LLM이 컬럼/값 조건을 더 정확히 반영해야 할 때 특히 유용합니다.

```text
wip_status 데이터의 ALERT_LEVEL 값은 NORMAL, WARN, HIGH이고 HIGH가 가장 위험한 상태야.
production_result 데이터의 YIELD_RATE는 수율이며 95 이하이면 주의가 필요해.
quality_backlog 데이터의 BACKLOG_QTY는 미처리 물량이므로 상세 표는 BACKLOG_QTY 내림차순으로 보여줘.
ALERT_LEVEL이 HIGH 또는 WARN인 행만 위험 상세 표에 포함해줘.
전체 화면은 보라/블루 primary 계열로 구성하고 위험 상태만 주황/빨강으로 강조해줘.
```

이 입력을 쓰지 않아도 `01 데이터 구조 분석`과 `03a 리포트_컨텍스트_JSON`에 컬럼별 `sample_values`, `top_values`, `numeric_stats`, `data_dictionary`가 자동으로 들어갑니다. 다만 사내 용어처럼 컬럼명만으로 의미가 애매한 경우에는 위처럼 직접 보강해주면 결과가 더 안정적입니다.

Prompt Template 노드에서 먼저 할 일:

1. Langflow 기본 노드에서 `Prompt Template` 또는 `Prompt`를 추가합니다.
2. 아래 파일의 `Prompt Template 본문` 코드블록 전체를 복사합니다.
3. Prompt Template의 template 입력 칸에 붙여넣고 저장합니다.
4. 템플릿 안의 `{사용자_요청_JSON}`, `{리포트_컨텍스트_JSON}`, `{디자인_지시}`, `{렌더링_규칙}`, `{출력_스키마_JSON}` 변수가 입력 포트로 보이면 아래 표대로 연결합니다.

template 입력 칸에 넣을 내용:

```text
C:\Users\<사용자명>\Desktop\기능flow\html_report_flow\docs\PROMPT_TEMPLATE.md
```

`03a`는 template 본문을 출력하지 않습니다. template 본문은 위 md 파일에서 복사하고, `03a`는 Prompt Template 변수 5개만 연결합니다.

Prompt Template 변수 연결:

| From | Output | To | Input |
| --- | --- | --- | --- |
| `03a 프롬프트 변수 준비` | `사용자_요청_JSON` | `Prompt Template` | `사용자_요청_JSON` |
| `03a 프롬프트 변수 준비` | `리포트_컨텍스트_JSON` | `Prompt Template` | `리포트_컨텍스트_JSON` |
| `03a 프롬프트 변수 준비` | `디자인_지시` | `Prompt Template` | `디자인_지시` |
| `03a 프롬프트 변수 준비` | `렌더링_규칙` | `Prompt Template` | `렌더링_규칙` |
| `03a 프롬프트 변수 준비` | `출력_스키마_JSON` | `Prompt Template` | `출력_스키마_JSON` |
| `Prompt Template` | `prompt/message 출력` | `LLM 노드` | `prompt/message 입력` |

LLM 노드 설정:

- 일반적인 Chat LLM 노드를 사용합니다.
- 입력은 `Prompt Template`의 출력 메시지를 연결합니다.
- 출력은 `03b.LLM 응답`으로 연결합니다.

LLM이 해야 하는 일:

1. `질문`과 `보고 싶은 방식` 원문을 읽습니다.
2. `request_interpretation`을 만듭니다.
3. 사용 가능한 요소 양식 중 필요한 블록을 고릅니다.
4. 블록 순서, 너비, 색상, 밀도, 차트 설정, 표 설정을 정합니다.
5. HTML 코드가 아니라 JSON 계획만 반환합니다.

LLM이 반환해도 되는 대표 형태:

```json
{
  "request_interpretation": {
    "user_goal": "공정별 WIP와 생산량 추이를 한눈에 확인",
    "requested_visuals": ["KPI", "bar", "line", "table"],
    "layout_intent": "상단 KPI, 중간 차트, 하단 상세 표",
    "style_intent": "운영자가 빠르게 볼 수 있는 compact 대시보드",
    "target_block_count": 6
  },
  "report_plan": {
    "title": "공정별 WIP 및 생산량 리포트",
    "layout": "dashboard",
    "blocks": []
  }
}
```

실제로는 `blocks` 안에 `kpi_card_grid`, `comparison_bar_chart`, `trend_line_chart`, `detail_data_table` 같은 블록 설정이 들어갑니다.

## 13. 03b 노드 연결

노드:

```text
03b LLM 계획 검증
```

입력:

| 입력명 | 연결 |
| --- | --- |
| `기본 계획` | `03 기본 리포트 계획.기본 계획` |
| `LLM 응답` | LLM 노드의 text/message 출력 |

출력 연결:

| From | Output | To | Input |
| --- | --- | --- | --- |
| `03b LLM 계획 검증` | `최종 계획` | `04 HTML 렌더링` | `최종 계획` |

`03b`의 역할:

- LLM 응답에서 JSON을 추출합니다.
- `request_interpretation`을 보존합니다.
- 허용되지 않는 `block_id`는 제거합니다.
- 실제 데이터에 없는 컬럼명은 제거하거나 기본값으로 보정합니다.
- 유효한 LLM 블록은 최대한 유지합니다.
- JSON이 없거나 유효한 블록이 하나도 없을 때만 `03` 기본 계획으로 fallback합니다.

## 14. 04 노드 연결

노드:

```text
04 HTML 렌더링
```

입력:

| 입력명 | 연결 |
| --- | --- |
| `최종 계획` | `03b LLM 계획 검증.최종 계획` |

출력:

| Output | 설명 |
| --- | --- |
| `HTML 생성 결과` | HTML 원문과 report_plan, 데이터 요약이 들어 있는 payload |

다음 단계는 출력 방식에 따라 `05-1` 또는 `05-2`로 나뉩니다.

## 15. 05-1 HTML 원문 출력

이 방식은 서버 없이 가장 빠르게 확인하는 방법입니다.

노드:

```text
05-1 HTML 원문 출력
```

입력:

| 입력명 | 연결 |
| --- | --- |
| `HTML 생성 결과` | `04 HTML 렌더링.HTML 생성 결과` |

출력 연결:

| From | Output | To | Input |
| --- | --- | --- | --- |
| `05-1 HTML 원문 출력` | `HTML 원문` | `Chat Output` | `input` |

Playground 실행 결과:

- Chat Output에 `<!doctype html>...`로 시작하는 전체 HTML 코드가 나옵니다.
- 이 코드를 별도 파일로 저장하면 브라우저에서 열 수 있습니다.
- 로컬 저장이 어려운 환경에서는 Playground 출력값을 그대로 확인하는 용도로 씁니다.

## 16. 05-2 공유 링크 출력

이 방식은 HTML을 로컬 Report API 서버에 저장하고 다운로드 링크를 받는 방법입니다.

노드:

```text
05-2 공유 링크 출력
```

입력:

| 입력명 | 연결 또는 값 |
| --- | --- |
| `HTML 생성 결과` | `04 HTML 렌더링.HTML 생성 결과` |
| `Report API 주소` | `http://127.0.0.1:8010` |
| `링크 유효시간` | `24` |

출력 연결:

| From | Output | To | Input |
| --- | --- | --- | --- |
| `05-2 공유 링크 출력` | `링크 메시지` | `Chat Output` | `input` |

Playground 실행 결과 예시:

```text
HTML 리포트가 생성되었습니다: 공정별 WIP 및 생산량 리포트

다운로드 링크: http://127.0.0.1:8010/reports/download/20260624123000_abcd...
만료 시간: 2026-06-25 12:30 KST
```

다운로드 시 보이는 파일명은 LLM이 만든 `filename_hint` 또는 리포트 제목을 바탕으로 정해집니다.
서버 저장소 안의 실제 파일명은 중복 방지를 위해 `report_id.html` 형식으로 유지됩니다.

`127.0.0.1` 링크는 서버를 실행한 자기 PC에서만 열립니다.

## 17. Report API 서버 처음 설치

공유 링크 출력을 쓰려면 먼저 서버를 설치합니다.

PowerShell을 엽니다.

```powershell
cd C:\Users\<사용자명>\Desktop\기능flow\html_report_flow\report_api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

`Activate.ps1` 실행 정책 오류가 나오면 같은 PowerShell 창에서 아래를 실행합니다.

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

설치는 처음 한 번만 하면 됩니다.

## 18. Report API 서버 실행

설치가 끝난 뒤 서버를 켤 때마다 아래를 실행합니다.

```powershell
cd C:\Users\<사용자명>\Desktop\기능flow\html_report_flow\report_api
.\.venv\Scripts\Activate.ps1
python server.py
```

정상 실행 로그 예시:

```text
Local HTML Report API: http://127.0.0.1:8010
Storage folder: C:\Users\<사용자명>\Desktop\기능flow\html_report_flow\report_api\storage\reports
Uvicorn running on http://127.0.0.1:8010
```

이 PowerShell 창은 닫지 않습니다. 창을 닫으면 서버도 종료되고, `05-2`가 링크를 만들 수 없습니다.

서버 종료:

```text
Ctrl + C
```

## 19. Report API 정상 실행 확인

브라우저에서 아래 주소를 엽니다.

```text
http://127.0.0.1:8010/
```

아래 텍스트가 보이면 정상입니다.

```text
alive!
```

PowerShell에서 확인하려면:

```powershell
Invoke-RestMethod http://127.0.0.1:8010/
```

## 20. Report API 단독 테스트

Langflow 연결 전에 API만 확인하고 싶으면 서버를 켠 상태에서 새 PowerShell 창을 열고 실행합니다.

```powershell
$body = @{
  title = "Local Test Report"
  question = "테스트 리포트"
  view_request = "보기/다운로드 링크 확인"
  html = "<!doctype html><html><body><h1>Local Test Report</h1><p>Hello</p></body></html>"
  ttl_hours = 24
  filename_hint = "local_test_report"
} | ConvertTo-Json -Depth 20

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8010/reports" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

정상 응답에는 아래 값이 포함됩니다.

| 응답 필드 | 의미 |
| --- | --- |
| `view_url` | 브라우저에서 바로 볼 수 있는 주소 |
| `download_url` | HTML 파일 다운로드 주소 |
| `expires_at` | 만료 시간 |
| `ttl_hours` | 적용된 유효시간 |

## 21. 저장 위치와 생성 파일

기본 저장 폴더:

```text
C:\Users\<사용자명>\Desktop\기능flow\html_report_flow\report_api\storage\reports
```

리포트 1개가 생성되면 파일 2개가 생깁니다.

```text
20260624123000_abcd....html
20260624123000_abcd....json
```

| 파일 | 내용 |
| --- | --- |
| `.html` | 실제 HTML 리포트 |
| `.json` | 제목, 질문, 만료 시간, 다운로드 파일명, row 수 같은 메타데이터 |

사용자가 링크를 눌러 내려받는 파일명은 `.json` 메타데이터의 `download_filename` 값을 사용합니다.
이 값은 LLM이 만든 `filename_hint`를 기반으로 하며, 한글 파일명도 지원합니다.

## 22. 서버 설정 변경

설정은 `.env`가 아니라 [report_api/server.py](report_api/server.py) 상단에서 바꿉니다.

기본 설정:

```python
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8010
BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"
STORAGE_DIR = Path(__file__).resolve().parent / "storage"
DEFAULT_TTL_HOURS = 24
MAX_TTL_HOURS = 24 * 7
MAX_HTML_BYTES = 10 * 1024 * 1024
MAX_STORAGE_BYTES = 512 * 1024 * 1024
USE_ACCESS_TOKEN = False
```

자주 바꾸는 값:

| 설정값 | 기본값 | 의미 |
| --- | --- | --- |
| `SERVER_HOST` | `127.0.0.1` | 서버가 열릴 주소 |
| `SERVER_PORT` | `8010` | 서버 포트 |
| `BASE_URL` | `http://127.0.0.1:8010` | Langflow 메시지에 표시될 링크 주소 |
| `STORAGE_DIR` | `report_api\storage` | 저장소 루트 폴더 |
| `DEFAULT_TTL_HOURS` | `24` | 기본 링크 유효시간 |
| `MAX_TTL_HOURS` | `168` | 최대 링크 유효시간 |
| `MAX_STORAGE_BYTES` | `512MB` | 저장 용량 제한 |

포트가 이미 사용 중이면:

```python
SERVER_PORT = 8011
BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"
```

그리고 Langflow `05-2.Report API 주소`도 아래처럼 바꿉니다.

```text
http://127.0.0.1:8011
```

## 23. 다른 PC나 서버에서 링크를 열어야 할 때

기본값 `127.0.0.1`은 자기 PC 전용 주소입니다.

Langflow는 서버에 있고 Report API는 내 PC에서 실행하는 상황이라면, Langflow 서버가 내 PC의 API 주소에 접근할 수 있어야 합니다. 이 경우 보통 아래처럼 설정해야 합니다.

1. `server.py`에서 host를 외부 접속 가능하게 바꿉니다.

```python
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8010
BASE_URL = "http://내_PC_IP:8010"
```

2. Langflow `05-2.Report API 주소`에 아래처럼 입력합니다.

```text
http://내_PC_IP:8010
```

3. Windows 방화벽에서 Python 또는 8010 포트 접근을 허용합니다.

내 PC IP 확인:

```powershell
ipconfig
```

주의:

- `BASE_URL`은 링크에 표시될 주소입니다.
- `SERVER_HOST = "0.0.0.0"`은 서버가 모든 네트워크 인터페이스에서 요청을 받게 하는 설정입니다.
- 회사 네트워크 정책에 따라 외부 접속이 막힐 수 있습니다.

## 24. 만료와 자동 삭제

Report API는 생성된 파일을 계속 쌓아두지 않기 위해 만료와 용량 제한을 사용합니다.

동작 방식:

- `05-2.링크 유효시간` 값이 `ttl_hours`로 서버에 전달됩니다.
- 기본값은 24시간입니다.
- 최대값은 `MAX_TTL_HOURS`입니다. 기본은 168시간입니다.
- 만료된 링크를 열면 `410 Gone`이 반환됩니다.
- 서버 시작 시, 새 리포트 생성 시, 만료된 링크 조회 시 만료 파일을 정리합니다.
- 저장소가 `MAX_STORAGE_BYTES`를 넘으면 오래된 리포트부터 삭제합니다.

삭제되는 파일:

```text
{report_id}.html
{report_id}.json
```

## 25. LLM 사용해서 빠른 확인

LLM까지 포함해서 사용자의 `질문`/`보고 싶은 방식`이 실제 리포트 구조에 반영되는지 확인하려면 아래처럼 연결합니다.

### 25.1 LLM 사용 + HTML 원문 출력

서버 없이 Playground에서 HTML 원문만 확인하는 기본 테스트 경로입니다.

| 순서 | From | Output | To | Input |
| --- | --- | --- | --- | --- |
| 1 | `00 리포트 요청/데이터 불러오기` | `요청 데이터` | `01 데이터 구조 분석` | `요청 데이터` |
| 2 | `01 데이터 구조 분석` | `데이터 분석 결과` | `02 기본 요소 양식/추천` | `데이터 분석 결과` |
| 3 | `00 리포트 요청/데이터 불러오기` | `요청 데이터` | `03 기본 리포트 계획` | `요청 데이터` |
| 4 | `01 데이터 구조 분석` | `데이터 분석 결과` | `03 기본 리포트 계획` | `데이터 분석 결과` |
| 5 | `02 기본 요소 양식/추천` | `요소 추천 결과` | `03 기본 리포트 계획` | `요소 추천 결과` |
| 6 | `03 기본 리포트 계획` | `기본 계획` | `03a 프롬프트 변수 준비` | `기본 계획` |
| 7 | `03a 프롬프트 변수 준비` | `사용자_요청_JSON` | `Prompt Template` | `사용자_요청_JSON` |
| 8 | `03a 프롬프트 변수 준비` | `리포트_컨텍스트_JSON` | `Prompt Template` | `리포트_컨텍스트_JSON` |
| 9 | `03a 프롬프트 변수 준비` | `디자인_지시` | `Prompt Template` | `디자인_지시` |
| 10 | `03a 프롬프트 변수 준비` | `렌더링_규칙` | `Prompt Template` | `렌더링_규칙` |
| 11 | `03a 프롬프트 변수 준비` | `출력_스키마_JSON` | `Prompt Template` | `출력_스키마_JSON` |
| 12 | `Prompt Template` | `prompt/message 출력` | `LLM 노드` | `prompt/message 입력` |
| 13 | `03 기본 리포트 계획` | `기본 계획` | `03b LLM 계획 검증` | `기본 계획` |
| 14 | `LLM 노드` | `text/message 출력` | `03b LLM 계획 검증` | `LLM 응답` |
| 15 | `03b LLM 계획 검증` | `최종 계획` | `04 HTML 렌더링` | `최종 계획` |
| 16 | `04 HTML 렌더링` | `HTML 생성 결과` | `05-1 HTML 원문 출력` | `HTML 생성 결과` |
| 17 | `05-1 HTML 원문 출력` | `HTML 원문` | `Chat Output` | `input` |

이 경로에서 확인할 것:

- Prompt Template 출력 안에 `request_interpretation` 문구가 있는지 확인합니다.
- LLM 응답이 JSON 형태인지 확인합니다.
- `03b`의 최종 계획에서 `plan_source`가 `llm`인지 확인합니다.
- Chat Output에 전체 HTML 원문이 출력되는지 확인합니다.

### 25.2 LLM 사용 + 공유 링크 출력

다운로드 링크까지 확인하려면 먼저 `report_api/server.py`가 실행 중이어야 합니다.

| 순서 | From | Output | To | Input |
| --- | --- | --- | --- | --- |
| 1 | `00 리포트 요청/데이터 불러오기` | `요청 데이터` | `01 데이터 구조 분석` | `요청 데이터` |
| 2 | `01 데이터 구조 분석` | `데이터 분석 결과` | `02 기본 요소 양식/추천` | `데이터 분석 결과` |
| 3 | `00 리포트 요청/데이터 불러오기` | `요청 데이터` | `03 기본 리포트 계획` | `요청 데이터` |
| 4 | `01 데이터 구조 분석` | `데이터 분석 결과` | `03 기본 리포트 계획` | `데이터 분석 결과` |
| 5 | `02 기본 요소 양식/추천` | `요소 추천 결과` | `03 기본 리포트 계획` | `요소 추천 결과` |
| 6 | `03 기본 리포트 계획` | `기본 계획` | `03a 프롬프트 변수 준비` | `기본 계획` |
| 7 | `03a 프롬프트 변수 준비` | `사용자_요청_JSON` | `Prompt Template` | `사용자_요청_JSON` |
| 8 | `03a 프롬프트 변수 준비` | `리포트_컨텍스트_JSON` | `Prompt Template` | `리포트_컨텍스트_JSON` |
| 9 | `03a 프롬프트 변수 준비` | `디자인_지시` | `Prompt Template` | `디자인_지시` |
| 10 | `03a 프롬프트 변수 준비` | `렌더링_규칙` | `Prompt Template` | `렌더링_규칙` |
| 11 | `03a 프롬프트 변수 준비` | `출력_스키마_JSON` | `Prompt Template` | `출력_스키마_JSON` |
| 12 | `Prompt Template` | `prompt/message 출력` | `LLM 노드` | `prompt/message 입력` |
| 13 | `03 기본 리포트 계획` | `기본 계획` | `03b LLM 계획 검증` | `기본 계획` |
| 14 | `LLM 노드` | `text/message 출력` | `03b LLM 계획 검증` | `LLM 응답` |
| 15 | `03b LLM 계획 검증` | `최종 계획` | `04 HTML 렌더링` | `최종 계획` |
| 16 | `04 HTML 렌더링` | `HTML 생성 결과` | `05-2 공유 링크 출력` | `HTML 생성 결과` |
| 17 | `05-2 공유 링크 출력` | `링크 메시지` | `Chat Output` | `input` |

`05-2 공유 링크 출력`에 직접 입력해야 하는 값:

| 입력명 | 값 |
| --- | --- |
| `Report API 주소` | `http://127.0.0.1:8010` |
| `링크 유효시간` | `24` |

이 경로에서 확인할 것:

- Report API 서버 PowerShell 창이 켜져 있는지 확인합니다.
- `http://127.0.0.1:8010/`에서 `alive!`가 보이는지 확인합니다.
- Chat Output에 다운로드 링크와 만료 시간이 줄바꿈되어 나오는지 확인합니다.
- 다운로드 링크를 열었을 때 `.html` 파일이 내려받아지는지 확인합니다.

## 26. LLM 없이 빠른 확인

LLM 노드가 준비되지 않았거나 렌더링만 확인하고 싶으면 아래처럼 연결합니다.

| 순서 | From | Output | To | Input |
| --- | --- | --- | --- | --- |
| 1 | `00 리포트 요청/데이터 불러오기` | `요청 데이터` | `01 데이터 구조 분석` | `요청 데이터` |
| 2 | `01 데이터 구조 분석` | `데이터 분석 결과` | `02 기본 요소 양식/추천` | `데이터 분석 결과` |
| 3 | `00 리포트 요청/데이터 불러오기` | `요청 데이터` | `03 기본 리포트 계획` | `요청 데이터` |
| 4 | `01 데이터 구조 분석` | `데이터 분석 결과` | `03 기본 리포트 계획` | `데이터 분석 결과` |
| 5 | `02 기본 요소 양식/추천` | `요소 추천 결과` | `03 기본 리포트 계획` | `요소 추천 결과` |
| 6 | `03 기본 리포트 계획` | `기본 계획` | `04 HTML 렌더링` | `최종 계획` |
| 7 | `04 HTML 렌더링` | `HTML 생성 결과` | `05-1 HTML 원문 출력` | `HTML 생성 결과` |
| 8 | `05-1 HTML 원문 출력` | `HTML 원문` | `Chat Output` | `input` |

이 방식은 LLM이 요청 의도를 세밀하게 해석하지 않기 때문에 리포트 구성이 단조로울 수 있습니다. 렌더러와 데이터 로딩이 정상인지 확인하는 용도로만 씁니다.

## 27. 다른 샘플 데이터로 테스트

샘플 입력 예시는 아래 문서에서 확인합니다.

```text
C:\Users\<사용자명>\Desktop\기능flow\html_report_flow\samples\INPUT_EXAMPLES.md
```

실제 파일은 `samples/00_data_inputs`, 선택 catalog는 `samples/02_component_catalogs`에 있습니다.

## 28. 최소 성공 체크리스트

처음 연결 후 아래 순서로 확인합니다.

1. `00` 실행 후 row 수가 0이 아닌지 확인합니다.
2. `01` 실행 후 숫자/범주/날짜 컬럼이 잡혔는지 확인합니다.
3. `02` 실행 후 `요소 추천 결과`가 비어 있지 않은지 확인합니다.
4. Prompt Template 출력에 `request_interpretation` 문구가 있는지 확인합니다.
5. LLM 출력이 JSON 형태인지 확인합니다.
6. `03b`의 `최종 계획.report_plan.plan_source`가 가능하면 `llm`인지 확인합니다.
7. `04` 결과에 `html_report.html`이 생성됐는지 확인합니다.
8. `05-1`이면 Chat Output에 HTML 원문이 나오는지 확인합니다.
9. `05-2`이면 서버가 켜져 있고 다운로드 링크가 나오는지 확인합니다.

## 29. 자주 나는 문제

| 증상 | 원인 후보 | 해결 |
| --- | --- | --- |
| `row_count`가 0 | CSV/JSON 입력이 비어 있음 | `00.데이터 직접 입력` 또는 `00.파일 데이터` 확인 |
| File Read 연결이 안 됨 | 출력 handle 타입이 맞지 않음 | 기존 edge 삭제 후 `Read File.Structured Content -> 00.파일 데이터`로 다시 연결 |
| HTML이 너무 비슷하게 나옴 | LLM이 아니라 `03 -> 04` fallback 경로를 보고 있음 | `03a -> Prompt Template -> LLM -> 03b -> 04` 연결 확인 |
| LLM 결과가 반영되지 않음 | LLM 출력이 `03b.LLM 응답`으로 가지 않음 | LLM output/message를 `03b.LLM 응답`에 연결 |
| Prompt Template 변수 입력이 안 보임 | template 칸에 변수 포함 템플릿이 들어가지 않음 | `docs/PROMPT_TEMPLATE.md`의 본문을 Prompt Template template 칸에 먼저 넣고 저장/새로고침 |
| `03b`가 fallback함 | LLM 응답에 JSON이 없거나 block이 전부 무효 | LLM 출력에 JSON만 나오게 설정하고 `blocks` 확인 |
| 차트가 안 나옴 | x/y 컬럼이 맞지 않거나 데이터 타입이 부적절 | 질문에 원하는 차트와 기준 컬럼을 더 구체적으로 작성 |
| `05-2` 연결 실패 | Report API 서버가 꺼져 있음 | `report_api` 폴더에서 `python server.py` 실행 |
| `Connection refused` | 주소/포트가 맞지 않음 | `05-2.Report API 주소`와 `SERVER_PORT` 확인 |
| 링크 열면 404 | report_id가 없거나 파일 삭제됨 | Langflow에서 다시 생성 |
| 링크 열면 410 | TTL 만료 | 다시 생성하거나 `링크 유효시간` 증가 |
| 다른 사람 PC에서 링크가 안 열림 | `127.0.0.1`은 자기 PC 전용 | 각자 서버 실행 또는 `BASE_URL`을 실제 IP로 설정 |
| VS Code에서 `@app.on_event`에 줄이 그어짐 | FastAPI의 최신 lifespan 권장 표시 | 현재 실행에는 문제 없음 |

## 30. 체험자에게 줄 짧은 안내

HTML 원문만 볼 때:

```text
00 -> 01 -> 02 -> 03 -> 03a -> Prompt Template -> LLM -> 03b -> 04 -> 05-1 -> Chat Output
```

다운로드 링크까지 만들 때:

```text
1. report_api 폴더에서 python server.py 실행
2. Langflow에서 05-2.Report API 주소를 http://127.0.0.1:8010으로 입력
3. 00 -> 01 -> 02 -> 03 -> 03a -> Prompt Template -> LLM -> 03b -> 04 -> 05-2 -> Chat Output 연결
4. Playground 실행
5. Chat Output의 다운로드 링크 확인
```

## 31. 최종 권장 테스트 순서

1. `sample_wip.csv` 내용을 `00.데이터 직접 입력`에 붙여넣습니다.
2. `05-1 HTML 원문 출력` 방식으로 먼저 실행합니다.
3. HTML 원문이 정상 출력되면 Report API를 설치합니다.
4. `python server.py`를 실행합니다.
5. 브라우저에서 `http://127.0.0.1:8010/`의 `alive!`를 확인합니다.
6. Flow 마지막을 `05-2 공유 링크 출력`으로 바꿉니다.
7. Playground에서 다시 실행합니다.
8. 다운로드 링크와 만료 시간이 줄바꿈되어 표시되는지 확인합니다.
