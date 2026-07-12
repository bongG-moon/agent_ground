# Reusable Data Flow Guide

이 문서는 `reusable_data_flow_components` 폴더에 있는 재사용 데이터 조회 Flow를 한 번에 이해하고 연결하기 위한 현재 기준 가이드입니다.

이 Flow는 `PTMORE PKG AGENT` 본체와 별개로 사용할 수 있습니다. 다른 프로젝트에서 Oracle, H-API, Datalake, Goodocs 데이터를 간단히 불러오고 싶을 때 이 폴더의 노드만 가져가면 됩니다.

## 핵심 구조

Flow는 두 단계로 나눠 생각하면 가장 쉽습니다.

1. Source catalog 작성 단계

   사람이 줄글로 적은 데이터 설명을 LLM이 `source_catalog`로 변환합니다. 저장소에 저장하지 않고 결과를 그대로 조회 Flow에 넘기거나 복사해서 씁니다.

2. 데이터 조회 단계

   사용자의 자연어 질문을 LLM이 짧은 `data_request`로 만들고, `Data Request Normalizer`가 `source_catalog`에서 실행 설정을 채운 뒤 전용 data node들이 데이터를 가져옵니다.

## Component Map

| 파일 | 노드 이름 | 역할 |
| --- | --- | --- |
| `00_data_request_normalizer.py` | Data Request Normalizer | LLM 응답을 실행 가능한 `data_request`로 정규화하고 `source_catalog`에서 설정을 채움 |
| `01_oracle_data.py` | Oracle Data | `source_type=oracle` 요청만 실행 |
| `02_h_api_data.py` | H-API Data | `source_type=h_api` 요청만 실행 |
| `03_datalake_data.py` | Datalake Data | `source_type=datalake` 요청만 실행 |
| `04_goodocs_data.py` | Goodocs Data | `source_type=goodocs` 요청만 실행 |
| `05_data_result_merger.py` | Data Result Merger | 각 data node 결과를 하나의 `data_result`로 병합 |
| `06_data_output_builder.py` | Data Output Builder | 자동화용 `data_json`과 테스트용 `test_message` 생성 |
| `07_source_catalog_normalizer.py` | Catalog Normalizer | LLM이 만든 source catalog JSON을 조회 Flow에서 쓰기 좋은 형태로 정규화 |
| `08_llm_caller.py` | LLM Caller | 기본 Agent 대신 Prompt Template 결과를 LLM에 호출하고 `llm_result`로 반환 |
| `09_source_catalog_mongodb_store.py` | Catalog MongoDB Store | 긴 SQL을 포함한 source_catalog를 MongoDB에 source 이름 기준으로 저장 또는 업데이트 |
| `10_source_catalog_mongodb_loader.py` | Catalog MongoDB Loader | MongoDB collection에 저장된 source들을 다시 source_catalog 형태로 로드 |
| `11_html_report_datasets_adapter.py` | HTML Report Datasets Adapter | `06 Data JSON`을 HTML 생성 Flow의 `datasets` 입력으로 변환 |

붙여넣기용 Prompt Template과 입력 예시는 [REUSABLE_DATA_FLOW_PROMPTS_AND_INPUTS.md](REUSABLE_DATA_FLOW_PROMPTS_AND_INPUTS.md)를 봅니다.

## Catalog 작성 Flow

이 단계는 선택 사항입니다. `source_catalog`를 사람이 직접 작성할 수 있으면 건너뛰어도 됩니다.

```text
Text Input.source_description
-> Prompt Template.source_description

Prompt Template.prompt
-> LLM Caller.prompt

LLM Caller.llm_result
-> Catalog Normalizer.llm_result

Catalog Normalizer.catalog_message
-> Text Input.Text

Text Input.Output Text
-> Prompt Template.source_catalog

Text Input.Output Text
-> Data Request Normalizer.Data Catalog
```

`catalog_message`는 정규화된 source catalog를 JSON 문자열로 담은 Message 출력입니다. Text Input의 `Text` 입력도 실제 연결 타입은 Message이므로 이 출력을 Text Input에 연결할 수 있습니다.

`catalog_data`는 같은 catalog를 구조화된 Data로 내보냅니다. MongoDB에 저장할 때는 `Catalog MongoDB Store.source_catalog_data`로 연결하고, 조회 정규화에는 Text Input을 거친 문자열을 `Data Request Normalizer.Data Catalog`에 넣습니다.

조회 요청 정규화에도 같은 catalog를 쓰려면 `Text Input.Output Text`를 `Data Request Normalizer.Data Catalog`에 연결합니다.

긴 SQL이 들어간 `query_template`가 있으면 `catalog_message`를 복사하거나 Freeze preview를 재사용하지 않는 편이 안전합니다. 실행을 나눠 재사용해야 하면 `Catalog MongoDB Store/Loader`로 MongoDB에 저장 후 `catalog_text -> Text Input.Text -> Data Request Normalizer.Data Catalog`로 불러옵니다. 이렇게 하면 Catalog 작성 LLM을 다시 실행하지 않아도 되고, UI에서 `...`으로 잘린 문자열 때문에 SQL이 깨지는 문제도 피할 수 있습니다.

MongoDB 저장은 같은 collection 안의 `source_name` 기준으로 동작합니다. 여기서 `source_name`은 사람이 별도 입력하는 값이 아니라 `source_catalog.sources` 안의 key입니다. 예를 들어 `production_summary`, `lot_trace`, `goodocs_schedule`이 한 번에 들어오면 각각 별도 문서로 저장됩니다. 같은 source 이름이 이미 있으면 업데이트하고, 없는 source 이름이면 추가합니다. source catalog 묶음을 나눠 운영해야 하면 `catalog_name`을 쓰지 않고 MongoDB `collection_name`을 다르게 지정합니다. 로드할 때는 해당 collection의 source들을 다시 하나의 `source_catalog`로 조립합니다.

MongoDB Loader에서 Text Input에 연결할 때는 `catalog_text`를 사용합니다. 이 출력도 JSON 문자열을 `Message.text`에 담아 내보냅니다. `catalog_data`는 로드 상태와 source_catalog를 JSON으로 확인하는 용도입니다.

## 데이터 조회 Flow

자연어 질문으로 조회할 때의 권장 연결입니다.

```text
Chat Input.message
-> Prompt Template.user_request

Text Input 또는 고정값.source_catalog
-> Prompt Template.source_catalog
-> Data Request Normalizer.Data Catalog

Catalog Normalizer.catalog_message
-> Text Input.Text

Text Input.Output Text
-> Prompt Template.source_catalog

Text Input.Output Text
-> Data Request Normalizer.Data Catalog

Catalog MongoDB Loader.catalog_text
-> Text Input.Text

Catalog MongoDB Loader.catalog_data
-> JSON 결과 확인

Prompt Template.prompt
-> LLM Caller.prompt

LLM Caller.llm_result
-> Data Request Normalizer.llm_result

Data Request Normalizer.data_request
-> Oracle Data.data_request
-> H-API Data.data_request
-> Datalake Data.data_request
-> Goodocs Data.data_request

Oracle Data.Data Result
-> Data Result Merger.oracle_result

H-API Data.Data Result
-> Data Result Merger.h_api_result

Datalake Data.Data Result
-> Data Result Merger.datalake_result

Goodocs Data.Data Result
-> Data Result Merger.goodocs_result

Data Result Merger.data_result
-> Data Output Builder.data_result

Data Output Builder.test_message
-> Chat Output.input

Data Output Builder.data_json
-> downstream JSON/API consumer, if needed

Data Output Builder.data_json
-> HTML Report Datasets Adapter.data_json

HTML Report Datasets Adapter.html_datasets_text
-> HTML Report Flow 00.데이터 직접 입력
```

`source_catalog`를 Prompt Template과 Normalizer에 둘 다 넣는 이유는 역할이 다르기 때문입니다.

- Prompt Template: LLM이 어떤 source를 고를지 판단합니다.
- Normalizer: LLM이 고른 source 이름과 파라미터를 실행 가능한 설정으로 채웁니다.

LLM은 SQL, API URL, doc_id 같은 실행 설정을 매번 복사하지 않아도 됩니다. 그 정보는 `source_catalog`에 한 번만 있어야 합니다.

운영에서는 긴 SQL을 Prompt Template이나 Text Input으로 계속 전달하기보다, 한 번 생성한 `source_catalog`를 MongoDB collection에 저장하고 같은 collection에서 다시 불러오는 방식을 권장합니다.

## Catalog 계약

`source_catalog`는 source 이름별로 아래 정보를 가집니다.

| 필드 | 의미 |
| --- | --- |
| `source_type` | `oracle`, `h_api`, `datalake`, `goodocs` 중 하나 |
| `description` | 데이터 설명 |
| `keywords` | 사용자가 어떤 단어를 말했을 때 이 source를 고를지 |
| `aliases` | source 이름의 별칭 |
| `example_questions` | 이 source를 선택해야 하는 예시 질문 |
| `required_params` | 조회에 필요한 파라미터 |
| `param_order` | H-API `bindParams` 또는 실행 변수 순서 |
| `param_formats` | `DATE=YYYYMMDD`, `FROM_YM=YYYYMM`처럼 LLM이 params 값을 만들 때 따라야 하는 형식 |
| `source_config` | 쿼리, API URL, 문서번호 같은 실행 설정 |

아래 plain text는 source catalog 작성용 입력 예시입니다. 이 값을 바로 `Data Request Normalizer.Data Catalog`에 넣기보다는 먼저 `Catalog Normalizer`로 정규화한 뒤 `catalog_message -> Text Input.Text -> Data Request Normalizer.Data Catalog` 경로로 데이터 조회 Flow에 넣습니다.

```text
source: production_summary
source_type: oracle
description: 날짜/공장/공정/제품 조건별 생산 실적 집계 데이터
keywords: production, 생산, 생산실적, 실적, output, 투입, pkg out
example_questions: 2026년 5월 20일 FAB1 D/A1 공정 생산량을 제품별로 가져와줘, 오늘 B/G1 공정에서 MOBILE 제품 생산실적 가져와줘
required_params: DATE, FACTORY, OPER_NAME, MODE, PRODUCT_KEYWORD
param_order: DATE, FACTORY, OPER_NAME, MODE, PRODUCT_KEYWORD
param_formats: DATE=YYYYMMDD, FACTORY=text, OPER_NAME=text, MODE=text, PRODUCT_KEYWORD=text
db_key: PKG_RPT
query_template:
WITH base AS (
    SELECT WORK_DT, FACTORY, OPER_NAME, MODE, TECH, DEVICE_DESC, PRODUCTION
    FROM PRODUCTION_TABLE
    WHERE WORK_DT = {DATE}
      AND FACTORY = {FACTORY}
      AND OPER_NAME = {OPER_NAME}
)
SELECT WORK_DT, FACTORY, OPER_NAME, MODE, TECH, SUM(PRODUCTION) AS PRODUCTION
FROM base
WHERE MODE = {MODE}
  AND DEVICE_DESC LIKE '%' || {PRODUCT_KEYWORD} || '%'
GROUP BY WORK_DT, FACTORY, OPER_NAME, MODE, TECH
---
source: lot_trace
source_type: h_api
description: lot trace history from H-API
keywords: lot trace, trace, lot 이력, 공정 이력
example_questions: LOT A12345가 D/A1부터 B/G1까지 어떻게 흘렀는지 trace 조회해줘
required_params: LOT_ID, START_OPER, END_OPER
param_order: LOT_ID, START_OPER, END_OPER
param_formats: LOT_ID=text, START_OPER=text, END_OPER=text
api_url: http://example.com/datahub/v1/api/lot-trace
response_path: data.row
---
source: monthly_yield_summary
source_type: datalake
description: monthly yield summary from Datalake
keywords: monthly yield, yield, 월별 수율, 수율, 불량률
example_questions: 2026년 1월부터 5월까지 DDR5 월별 수율 추이 가져와줘
required_params: FROM_YM, TO_YM, PRODUCT_FAMILY
param_order: FROM_YM, TO_YM, PRODUCT_FAMILY
param_formats: FROM_YM=YYYYMM, TO_YM=YYYYMM, PRODUCT_FAMILY=text
query_template:
WITH monthly AS (
    SELECT WORK_MONTH, PRODUCT_FAMILY, MODE, TECH,
           SUM(INPUT_QTY) AS INPUT_QTY,
           SUM(GOOD_QTY) AS GOOD_QTY
    FROM LAKEHOUSE_YIELD_TABLE
    WHERE WORK_MONTH BETWEEN {FROM_YM} AND {TO_YM}
      AND PRODUCT_FAMILY = {PRODUCT_FAMILY}
    GROUP BY WORK_MONTH, PRODUCT_FAMILY, MODE, TECH
)
SELECT WORK_MONTH, PRODUCT_FAMILY, MODE, TECH,
       CASE WHEN INPUT_QTY = 0 THEN NULL ELSE GOOD_QTY / INPUT_QTY * 100 END AS YIELD_RATE
FROM monthly
---
source: goodocs_schedule
source_type: goodocs
description: Goodocs 생산 스케줄과 투입 계획 문서
keywords: schedule, 스케줄, 일정, 투입계획, 생산계획
example_questions: 오늘 Goodocs 스케줄에서 가장 많이 투입되는 제품 가져와줘
doc_id: GOODOCS_DOCUMENT_ID
```

## Credential 입력 위치

Credential은 `source_catalog`에 넣지 않습니다. 각 data node 노드의 input에 넣습니다.

| Source | Credential input | Catalog에 두는 값 |
| --- | --- | --- |
| `oracle` | `oracle_config` 입력 (`db_key`별 Oracle TNS 목록) | `db_key`, `query_template` |
| `h_api` | `h_api_token` | `api_url`, `response_path` |
| `datalake` | `lake_user_id`, `lake_jwt_tk` | `query_template`, 선택 값 `cluster_type` |
| `goodocs` | `goodocs_user_id`, `goodocs_token_source`, `goodocs_token_key` | `doc_id` |

## 테스트 실행 모드

현재 `01`부터 `04`까지의 data node는 배선 확인용 더미 row 생성 함수를 유지합니다. 다만 `USE_DUMMY_DATA = True/False` 같은 모드 스위치는 없습니다.

각 data node 파일 안의 `_dummy_rows()` 블록이 있으면 실제 Oracle, H-API, Datalake, Goodocs를 호출하지 않고 샘플 row를 반환합니다. 실제 호출로 바꾸려면 해당 더미 블록을 주석 처리하거나 삭제한 뒤, 같은 `_run_*` 함수 안에 주석으로 남겨둔 실제 실행 블록을 사용합니다.

Oracle과 Datalake의 실제 실행 경로에는 `ensure_package()`가 있습니다. Oracle은 `oracledb`, Datalake는 SmallData 방식에 필요한 `aiohttp`, `mysql-connector-python`, `pandas`를 실제 실행 시점에만 확인합니다. H-API는 `requests`, Goodocs는 노드 안의 `Goodocs` class를 사용합니다.

더미 row에는 배선 확인용으로 Oracle `executed_query`, H-API `request_body`, Goodocs `doc_id` 같은 값이 들어갑니다. merger는 이 실행 확인 정보도 `source_results`에 함께 보존합니다.

## 최종 출력 형태

`Data Output Builder.data_json`은 자동화나 API 연결에 쓰기 좋은 JSON 객체를 반환합니다.

요청을 실행하면 `data_json.data_result`는 조회 요청 순서대로 나뉜 배열입니다. 각 index에는 해당 요청의 row 배열이 바로 들어가며, 1행이어도 list[object]로 유지합니다. `data_result[0].data_result`처럼 다시 감싸지 않고, 상세 metadata는 `data_json.source_results`에서 확인합니다.

```json
{
  "success": true,
  "mode": "single",
  "data_result": [
    [
      {
        "WORK_DT": "20260520",
        "MODE": "LPDDR5",
        "PRODUCTION": 1000
      }
    ]
  ],
  "source_results": [
    {
      "name": "production_summary",
      "source_type": "oracle",
      "success": true,
      "row_count": 1,
      "columns": ["WORK_DT", "OPER_NAME", "PRODUCTION"],
      "data_result": [
        {
          "WORK_DT": "20260520",
          "OPER_NAME": "D/A",
          "PRODUCTION": 1000
        }
      ],
      "error_message": "",
      "request_params": {
        "DATE": "20260520",
        "OPER_NAME": "D/A"
      }
    }
  ]
}
```

`Data Output Builder.test_message`는 Chat Output에서 확인하기 좋은 Markdown 표입니다. Advanced 입력인 `Max Message Rows`, `Max Cell Chars`는 이 확인용 메시지에만 적용됩니다. `data_json.data_result`에 보관되는 실제 데이터 내용에는 영향을 주지 않습니다.

```text
### 데이터 조회 결과
- 상태: 성공
- 모드: single
- 행 수: 1

| WORK_DT | MODE | PRODUCTION |
| --- | --- | --- |
| 20260520 | LPDDR5 | 1000 |
```

## 빠른 검증

```powershell
python -m compileall -q .\langflow_main\reusable_data_flow_components
python -m pytest .\tests\test_langflow_main_simplified_flow.py -q -k "reusable_data"
```
