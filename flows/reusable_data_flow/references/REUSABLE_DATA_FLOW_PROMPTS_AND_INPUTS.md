# Reusable Data Flow Prompts And Inputs

이 문서는 `reusable_data_flow_components`를 Langflow 화면에서 구성할 때 Prompt Template에 넣을 내용과 입력 예시만 따로 모아 둔 문서입니다.

구조 설명과 전체 연결 순서는 [REUSABLE_DATA_FLOW_GUIDE.md](REUSABLE_DATA_FLOW_GUIDE.md)를 봅니다.

## Catalog 작성용 Prompt Template

이 Prompt Template은 사람이 줄글로 작성한 데이터 설명을 `source_catalog` 후보 JSON으로 바꿉니다.

Langflow 기본 Prompt Template은 `{...}`를 변수로 인식합니다. 그래서 이 템플릿에서 실제 변수는 `{source_description}` 하나만 사용합니다. JSON 예시는 템플릿 안에 직접 넣지 않습니다.

```text
You convert a human-written data source description into a source catalog object for a reusable data retrieval flow.

Return only valid JSON.
Do not wrap the answer in Markdown.
Do not add explanations outside JSON.

The output must describe one or more data sources.

If the source description contains multiple data sources, split them into separate source objects.
Each source object must have its own name, source_type, required_params, param_order, param_formats, keywords, example_questions, and source_config.

For each source, infer these fields when possible:
- name
- source_type: oracle, h_api, datalake, or goodocs
- description
- keywords
- aliases
- example_questions
- required_params
- param_order
- param_formats
- source_config

Runtime config rules:
- For oracle, source_config should include db_key and query_template.
- For h_api, source_config should include api_url, response_path, and optional timeout. The request body key is always bindParams.
- For datalake, source_config should include query_template.
- For goodocs, source_config should include doc_id.

Parameter format rules:
- If the source description mentions the value format of any parameter, put that parameter and format into param_formats.
- Treat phrases like PARAM_NAME is FORMAT, PARAM_NAME format is FORMAT, PARAM_NAME=FORMAT, and their Korean equivalents as parameter format rules.
- Use the actual parameter name and actual format from the source description. Do not hardcode DATE or any sample variable name.
- param_formats belongs at the source object level, not inside source_config.

Keep multiline SQL exactly as written by the user.
Preserve placeholder tokens inside query_template.
Do not replace placeholders with sample values.
Do not invent credentials, tokens, passwords, user IDs, or API keys.
If required information is missing, return needs_more_info as true and include questions with specific missing details.

Source description:
{source_description}
```

### KOR 버전

아래 내용은 위 영어 Prompt Template과 같은 역할을 하는 한글 버전입니다. Prompt Template에 넣을 때 변수명 `{source_description}`은 그대로 유지합니다.

```text
사람이 작성한 데이터 소스 설명을 재사용 데이터 조회 Flow에서 사용할 source_catalog 후보 JSON으로 변환하세요.

반드시 유효한 JSON만 반환하세요.
Markdown 코드블록으로 감싸지 마세요.
JSON 밖에 설명 문장을 추가하지 마세요.

출력은 하나 이상의 데이터 source를 설명해야 합니다.

소스 설명에 여러 데이터 source가 포함되어 있으면 source별로 분리하세요.
각 source 객체는 name, source_type, required_params, param_order, param_formats, keywords, example_questions, source_config를 각각 가져야 합니다.

각 source에 대해 가능하면 아래 필드를 추론하세요.
- name
- source_type: oracle, h_api, datalake, goodocs 중 하나
- description
- keywords
- aliases
- example_questions
- required_params
- param_order
- param_formats
- source_config

실행 설정 규칙:
- oracle은 source_config에 db_key와 query_template을 넣으세요.
- h_api는 source_config에 api_url, response_path, 선택적으로 timeout을 넣으세요. 요청 body key는 항상 bindParams입니다.
- datalake는 source_config에 query_template을 넣으세요.
- goodocs는 source_config에 doc_id를 넣으세요.

파라미터 형식 규칙:
- source 설명에서 어떤 파라미터든 값 형식을 언급하면 해당 파라미터명과 형식을 param_formats에 넣으세요.
- `파라미터명은 형식값 형식`, `파라미터명 형식은 형식값`, `파라미터명=형식값` 같은 표현은 모두 파라미터 형식 규칙으로 해석하세요.
- DATE 같은 특정 예시 변수명을 고정으로 사용하지 말고, source 설명에 실제로 나온 파라미터명과 형식값을 그대로 사용하세요.
- param_formats는 source_config 안이 아니라 source 객체 본문에 두세요.

여러 줄 SQL은 사용자가 작성한 내용을 최대한 그대로 유지하세요.
query_template 안의 placeholder 토큰은 그대로 보존하세요.
placeholder를 예시 값으로 치환하지 마세요.
credential, token, password, user ID, API key를 임의로 만들거나 출력하지 마세요.
필수 정보가 부족하면 needs_more_info를 true로 반환하고, 어떤 정보가 필요한지 questions에 구체적인 질문을 넣으세요.

Source description:
{source_description}
```

## Catalog 작성용 입력 예시

아래 값을 `Text Input.source_description`에 넣습니다. 여러 source를 한 번에 넣을 때는 `---`로 나누면 LLM이 source를 분리하기 쉽습니다.

### 기본 데이터 확인용 입력 예시

아래 예시는 Flow 연결과 파라미터 매핑이 정상인지 빠르게 확인하기 위한 최소 입력입니다. 실제 시스템 쿼리라기보다, source 분리와 `{변수}` 치환이 되는지 확인하는 용도로 사용합니다.

```text
source: production
production 데이터는 Oracle PKG_RPT에서 조회한다.
생산, 생산실적, 생산량, output 같은 말이 나오면 production source를 사용한다.
DATE가 필수 파라미터다.
DATE 형식은 YYYYMMDD다.
예시 질문은 "2026년 5월 20일 production 데이터 가져와줘", "어제 생산실적 보여줘"이다.

쿼리는 아래와 같다.

SELECT WORK_DT, MODE, TECH, PKG_TYPE1, PKG_TYPE2, LEAD, MCP_NO, PRODUCTION
FROM PRODUCTION_TABLE
WHERE WORK_DT = {DATE}

---

source: lot_trace
lot_trace 데이터는 H-API로 조회한다.
lot trace, LOT 이력, trace 같은 말이 나오면 lot_trace source를 사용한다.
LOT_ID가 필수 파라미터이고 bindParams 순서는 LOT_ID다.
LOT_ID 형식은 text다.
응답 데이터는 data 필드 안에 row 배열로 들어온다.
api url은 http://example.com/datahub/v1/api/lot-trace 이다.
예시 질문은 "LOT-001 trace 데이터 가져와줘", "LOT-001 이력 조회해줘"이다.

---

source: monthly_yield_summary
monthly_yield_summary 데이터는 Datalake에서 조회한다.
monthly yield, yield, 월별 수율, 수율 같은 말이 나오면 monthly_yield_summary source를 사용한다.
YM이 필수 파라미터다.
YM 형식은 YYYYMM이다.
예시 질문은 "2026년 5월 monthly yield summary 가져와줘", "202605 수율 데이터 조회해줘"이다.

쿼리는 아래와 같다.

SELECT {YM} AS YM, PRODUCT, YIELD_RATE
FROM TABLE_TABLE

---

source: goodocs_schedule
goodocs_schedule 데이터는 Goodocs 문서에서 조회한다.
schedule, 스케줄, 일정 같은 말이 나오면 goodocs_schedule source를 사용한다.
예시 질문은 "Goodocs schedule 문서 데이터 가져와줘", "스케줄 문서 조회해줘"이다.
문서번호는 GOODOCS_DOCUMENT_ID 이다.
```

### 실제 데이터 기준 입력 예시

아래 예시는 실제 분석 질문에 가깝게 변수 종류와 쿼리 형태를 늘린 입력입니다. 운영용 catalog를 설계할 때는 이 형태처럼 source별 키워드, 실제 질문 예시, 필수 변수, 줄바꿈 SQL을 함께 적는 편이 좋습니다.

```text
source: production_summary
production_summary 데이터는 Oracle PKG_RPT에서 조회한다.
생산, 생산실적, 생산량, output, 투입, pkg out 같은 말이 나오면 production_summary source를 사용한다.
DATE, FACTORY, OPER_NAME, MODE, PRODUCT_KEYWORD가 필수 파라미터다.
DATE 형식은 YYYYMMDD이고 FACTORY, OPER_NAME, MODE, PRODUCT_KEYWORD 형식은 text다.
예시 질문은 "2026년 5월 20일 FAB1 D/A1 공정 생산량을 제품별로 가져와줘", "어제 PKG OUT 실적을 MODE/TECH별로 조회해줘", "오늘 B/G1 공정에서 MOBILE 제품 생산실적 가져와줘"이다.

쿼리는 아래와 같다.

WITH base AS (
    SELECT
        WORK_DT,
        FACTORY,
        OPER_NAME,
        MODE,
        TECH,
        DEN,
        PKG_TYPE1,
        PKG_TYPE2,
        LEAD,
        NVL(MCP_NO, 'EMPTY') AS MCP_NO,
        DEVICE_DESC,
        PRODUCTION
    FROM PRODUCTION_TABLE
    WHERE WORK_DT = {DATE}
      AND FACTORY = {FACTORY}
      AND OPER_NAME = {OPER_NAME}
)
SELECT
    WORK_DT,
    FACTORY,
    OPER_NAME,
    MODE,
    TECH,
    DEN,
    PKG_TYPE1,
    PKG_TYPE2,
    LEAD,
    MCP_NO,
    SUM(PRODUCTION) AS PRODUCTION
FROM base
WHERE ({MODE} IS NULL OR MODE = {MODE})
  AND ({PRODUCT_KEYWORD} IS NULL OR DEVICE_DESC LIKE '%' || {PRODUCT_KEYWORD} || '%')
GROUP BY WORK_DT, FACTORY, OPER_NAME, MODE, TECH, DEN, PKG_TYPE1, PKG_TYPE2, LEAD, MCP_NO

---

source: lot_trace
lot_trace 데이터는 H-API로 조회한다.
lot trace, LOT 이력, trace 같은 말이 나오면 lot_trace source를 사용한다.
LOT_ID, START_OPER, END_OPER가 필수 파라미터이고 bindParams 순서는 LOT_ID, START_OPER, END_OPER다.
LOT_ID, START_OPER, END_OPER 형식은 text다.
응답 데이터는 data 필드 안에 row 배열로 들어온다.
api url은 http://example.com/datahub/v1/api/lot-trace 이다.
예시 질문은 "LOT A12345가 D/A1부터 B/G1까지 어떻게 흘렀는지 trace 조회해줘", "LOT B7788의 PKG INPUT 이후 이력 가져와줘"이다.

---

source: monthly_yield_summary
monthly_yield_summary 데이터는 Datalake에서 조회한다.
monthly yield, yield, 월별 수율, 수율, 불량률, fail rate 같은 말이 나오면 monthly_yield_summary source를 사용한다.
FROM_YM, TO_YM, PRODUCT_FAMILY가 필수 파라미터다.
FROM_YM, TO_YM 형식은 YYYYMM이고 PRODUCT_FAMILY 형식은 text다.
예시 질문은 "2026년 1월부터 5월까지 DDR5 월별 수율 추이 가져와줘", "올해 상반기 MOBILE 제품군의 월별 불량률 데이터를 조회해줘"이다.

쿼리는 아래와 같다.

WITH monthly AS (
    SELECT
        WORK_MONTH,
        PRODUCT_FAMILY,
        MODE,
        TECH,
        SUM(INPUT_QTY) AS INPUT_QTY,
        SUM(GOOD_QTY) AS GOOD_QTY,
        SUM(FAIL_QTY) AS FAIL_QTY
    FROM LAKEHOUSE_YIELD_TABLE
    WHERE WORK_MONTH BETWEEN {FROM_YM} AND {TO_YM}
      AND PRODUCT_FAMILY = {PRODUCT_FAMILY}
    GROUP BY WORK_MONTH, PRODUCT_FAMILY, MODE, TECH
)
SELECT
    WORK_MONTH,
    PRODUCT_FAMILY,
    MODE,
    TECH,
    INPUT_QTY,
    GOOD_QTY,
    FAIL_QTY,
    CASE WHEN INPUT_QTY = 0 THEN NULL ELSE GOOD_QTY / INPUT_QTY * 100 END AS YIELD_RATE
FROM monthly

---

source: goodocs_schedule
goodocs_schedule 데이터는 Goodocs 문서에서 조회한다.
schedule, 스케줄, 일정, 투입계획, 생산계획 같은 말이 나오면 goodocs_schedule source를 사용한다.
예시 질문은 "오늘 Goodocs 스케줄에서 가장 많이 투입되는 제품 가져와줘", "이번 주 투입 계획 문서 조회해줘"이다.
문서번호는 GOODOCS_DOCUMENT_ID 이다.
```

LLM이 정상 동작하면 아래처럼 `sources` 배열 또는 `sources` 객체를 반환합니다.

```json
{
  "sources": [
    {
      "name": "production_summary",
      "source_type": "oracle",
      "description": "Production summary data from Oracle.",
      "keywords": ["생산", "생산실적", "생산량", "output", "투입", "pkg out"],
      "aliases": [],
      "example_questions": ["2026년 5월 20일 FAB1 D/A1 공정 생산량을 제품별로 가져와줘", "어제 PKG OUT 실적을 MODE/TECH별로 조회해줘"],
      "required_params": ["DATE", "FACTORY", "OPER_NAME", "MODE", "PRODUCT_KEYWORD"],
      "param_order": ["DATE", "FACTORY", "OPER_NAME", "MODE", "PRODUCT_KEYWORD"],
      "param_formats": {
        "DATE": "YYYYMMDD",
        "FACTORY": "text",
        "OPER_NAME": "text",
        "MODE": "text",
        "PRODUCT_KEYWORD": "text"
      },
      "source_config": {
        "db_key": "PKG_RPT",
        "query_template": "WITH base AS (...)\nSELECT WORK_DT, FACTORY, OPER_NAME, MODE, TECH, SUM(PRODUCTION) AS PRODUCTION\nFROM base\nWHERE ({MODE} IS NULL OR MODE = {MODE})\n  AND ({PRODUCT_KEYWORD} IS NULL OR DEVICE_DESC LIKE '%' || {PRODUCT_KEYWORD} || '%')\nGROUP BY WORK_DT, FACTORY, OPER_NAME, MODE, TECH"
      }
    },
    {
      "name": "lot_trace",
      "source_type": "h_api",
      "description": "Lot trace data from H-API.",
      "keywords": ["lot trace", "LOT 이력", "trace"],
      "aliases": [],
      "example_questions": ["LOT A12345가 D/A1부터 B/G1까지 어떻게 흘렀는지 trace 조회해줘"],
      "required_params": ["LOT_ID", "START_OPER", "END_OPER"],
      "param_order": ["LOT_ID", "START_OPER", "END_OPER"],
      "param_formats": {
        "LOT_ID": "text",
        "START_OPER": "text",
        "END_OPER": "text"
      },
      "source_config": {
        "api_url": "http://example.com/datahub/v1/api/lot-trace",
        "response_path": "data.row"
      }
    }
  ]
}
```

이 값을 `Catalog Normalizer.llm_result`로 넣으면 조회 Flow에서 사용할 수 있는 정규화된 `source_catalog`가 나옵니다.

`Catalog Normalizer.catalog_message`는 정규화된 source catalog를 JSON 문자열 Message로 내보냅니다. Text Input의 `Text` 입력은 실제로 Message 연결을 받으므로 이 출력을 Text Input에 연결할 수 있습니다. `catalog_data` 출력은 같은 catalog를 Data 형태로 내보내므로 Flow를 나눠 실행할 때 구조화된 결과 확인용으로 사용할 수 있습니다.

긴 `query_template`가 있는 catalog를 재사용해야 하면 `catalog_message` preview를 복사하지 말고 `Catalog MongoDB Store`에 저장합니다. 이후 데이터 조회 Flow에서는 `Catalog MongoDB Loader`에서 같은 MongoDB collection을 불러오면 Catalog 작성 LLM을 다시 실행하지 않아도 됩니다.

MongoDB 저장은 `source_catalog.sources` 안의 source key 기준입니다. 사람이 별도로 source 이름을 입력하지 않습니다. 여러 source가 한 번에 들어오면 각 source가 별도 MongoDB 문서로 저장되고, 이미 같은 source 이름이 있으면 업데이트하고, 새 source 이름이면 추가합니다. source catalog 묶음을 나눠 운영해야 하면 `catalog_name`을 쓰지 않고 MongoDB `collection_name`을 다르게 지정합니다. Loader의 `catalog_text`는 Text Input으로 연결할 수 있는 Message 출력이고, `catalog_data`는 로드 상태와 source_catalog를 JSON으로 확인하는 용도입니다.

## 데이터 조회용 Prompt Template

이 Prompt Template은 사용자의 자연어 조회 요청을 짧은 `data_request` JSON으로 바꿉니다.

Langflow 기본 Prompt Template 변수명은 `source_catalog`, `user_request` 두 개만 사용합니다. 예시 질문은 Prompt Template이 아니라 `source_catalog`의 `example_questions`에 넣습니다.

```text
You convert a natural-language data retrieval request into one JSON request for a reusable data flow.

Return only JSON. Do not wrap it in Markdown.

Allowed source_type values:
- oracle
- h_api
- datalake
- goodocs

Use the Catalog to choose the best source.
Use source name, aliases, keywords, description, and example_questions.
The Catalog contains runtime details such as query_template, api_url, doc_id, required_params, param_order, and param_formats.
Do not copy those runtime details into the output unless the user directly provided them.

For a single source request, return a JSON object with:
- name
- source_type when it is useful or clear
- params

For multiple source requests, return a JSON object with a requests array. Each item should contain name, optional source_type, and params.

If required source information or required parameter values are missing, return an object with:
- needs_more_info: true
- questions: array of specific questions to ask the user
- requests: empty array

Rules:
- Put user-provided variable values into params.
- Convert parameter values to the format required by param_formats. For example, if DATE is YYYYMMDD, use 20260520. If DATE is YYYY-MM-DD, use 2026-05-20.
- Never include credentials, tokens, passwords, user IDs, or API keys in the JSON.
- Do not render SQL yourself. The Normalizer and data node will use source_catalog query_template.

Catalog:
{source_catalog}

User request:
{user_request}
```

### KOR 버전

아래 내용은 위 영어 Prompt Template과 같은 역할을 하는 한글 버전입니다. Prompt Template에 넣을 때 변수명 `{source_catalog}`, `{user_request}`는 그대로 유지합니다.

```text
사용자의 자연어 데이터 조회 요청을 재사용 데이터 Flow에서 실행할 수 있는 JSON 요청으로 변환하세요.

JSON만 반환하세요. Markdown 코드블록으로 감싸지 마세요.

허용되는 source_type 값:
- oracle
- h_api
- datalake
- goodocs

Catalog를 참고해서 가장 알맞은 source를 선택하세요.
source name, aliases, keywords, description, example_questions를 함께 참고하세요.
Catalog에는 query_template, api_url, doc_id, required_params, param_order, param_formats 같은 정보가 들어 있습니다.
사용자가 직접 제공한 경우가 아니라면 이런 실행 정보를 출력 JSON에 복사하지 마세요.

단일 source 조회라면 아래 필드를 가진 JSON 객체를 반환하세요.
- name
- source_type: 유용하거나 명확할 때만 포함
- params

여러 source를 조회해야 한다면 requests 배열을 가진 JSON 객체를 반환하세요.
각 requests 항목은 name, 선택적인 source_type, params를 포함해야 합니다.

필수 source 정보나 필수 파라미터 값이 부족하면 아래 형식의 객체를 반환하세요.
- needs_more_info: true
- questions: 사용자에게 물어볼 구체적인 질문 배열
- requests: 빈 배열

규칙:
- 사용자가 말한 변수 값은 params에 넣으세요.
- param_formats에 적힌 형식에 맞춰 params 값을 변환하세요. 예를 들어 DATE가 YYYYMMDD면 20260520, DATE가 YYYY-MM-DD면 2026-05-20을 사용하세요.
- credential, token, password, user ID, API key는 절대 JSON에 넣지 마세요.
- SQL을 직접 만들지 마세요. Normalizer와 data node가 source_catalog의 query_template을 사용합니다.

Catalog:
{source_catalog}

User request:
{user_request}
```

## 데이터 조회용 source_catalog 입력 예시

아래 값은 수동 테스트용 source catalog 예시입니다. 직접 붙여 넣는 경우에는 Prompt Template의 `source_catalog`와 `Data Request Normalizer.Data Catalog`에 같은 값을 넣습니다.

앞 단계의 LLM 기반 catalog 생성 결과를 Text Input 경유로 쓰려면 `Catalog Normalizer.catalog_message -> Text Input.Text -> Prompt Template.source_catalog`로 연결합니다.

조회 요청 정규화에도 같은 catalog를 쓰려면 `Text Input.Output Text -> Data Request Normalizer.Data Catalog`로 연결합니다.

쿼리가 길어서 화면 preview나 Freeze 결과가 `...`으로 잘릴 수 있는 경우에는 `Catalog MongoDB Loader.catalog_text -> Text Input.Text`로 연결합니다. 로드된 JSON을 확인하려면 `Catalog MongoDB Loader.catalog_data` 출력을 봅니다.

```text
source: production_summary
source_type: oracle
description: 날짜/공장/공정/제품 조건별 생산 실적 집계 데이터
keywords: production, 생산, 생산실적, 실적, output, 투입, pkg out
example_questions: 2026년 5월 20일 FAB1 D/A1 공정 생산량을 제품별로 가져와줘, 어제 PKG OUT 실적을 MODE/TECH별로 조회해줘, 오늘 B/G1 공정에서 MOBILE 제품 생산실적 가져와줘
required_params: DATE, FACTORY, OPER_NAME, MODE, PRODUCT_KEYWORD
param_order: DATE, FACTORY, OPER_NAME, MODE, PRODUCT_KEYWORD
param_formats: DATE=YYYYMMDD, FACTORY=text, OPER_NAME=text, MODE=text, PRODUCT_KEYWORD=text
db_key: PKG_RPT
query_template:
WITH base AS (
    SELECT
        WORK_DT,
        FACTORY,
        OPER_NAME,
        MODE,
        TECH,
        DEN,
        PKG_TYPE1,
        PKG_TYPE2,
        LEAD,
        NVL(MCP_NO, 'EMPTY') AS MCP_NO,
        DEVICE_DESC,
        PRODUCTION
    FROM PRODUCTION_TABLE
    WHERE WORK_DT = {DATE}
      AND FACTORY = {FACTORY}
      AND OPER_NAME = {OPER_NAME}
)
SELECT
    WORK_DT,
    FACTORY,
    OPER_NAME,
    MODE,
    TECH,
    DEN,
    PKG_TYPE1,
    PKG_TYPE2,
    LEAD,
    MCP_NO,
    SUM(PRODUCTION) AS PRODUCTION
FROM base
WHERE MODE = {MODE}
  AND DEVICE_DESC LIKE '%' || {PRODUCT_KEYWORD} || '%'
GROUP BY WORK_DT, FACTORY, OPER_NAME, MODE, TECH, DEN, PKG_TYPE1, PKG_TYPE2, LEAD, MCP_NO
---
source: lot_trace
source_type: h_api
description: LOT 단위 공정 이동 이력과 현재 상태를 H-API에서 조회
keywords: lot trace, trace, lot 이력, 공정 이력, LOT 이동
example_questions: LOT A12345가 D/A1부터 B/G1까지 어떻게 흘렀는지 trace 조회해줘, LOT B7788의 PKG INPUT 이후 이력 가져와줘
required_params: LOT_ID, START_OPER, END_OPER
param_order: LOT_ID, START_OPER, END_OPER
param_formats: LOT_ID=text, START_OPER=text, END_OPER=text
api_url: http://example.com/datahub/v1/api/lot-trace
response_path: data.row
---
source: monthly_yield_summary
source_type: datalake
description: 월별 제품군/모드/기술별 수율과 불량 수량 집계
keywords: monthly yield, yield, 월별 수율, 수율, 불량률, fail rate
example_questions: 2026년 1월부터 5월까지 DDR5 월별 수율 추이 가져와줘, 올해 상반기 MOBILE 제품군의 월별 불량률 데이터를 조회해줘
required_params: FROM_YM, TO_YM, PRODUCT_FAMILY
param_order: FROM_YM, TO_YM, PRODUCT_FAMILY
param_formats: FROM_YM=YYYYMM, TO_YM=YYYYMM, PRODUCT_FAMILY=text
query_template:
WITH monthly AS (
    SELECT
        WORK_MONTH,
        PRODUCT_FAMILY,
        MODE,
        TECH,
        SUM(INPUT_QTY) AS INPUT_QTY,
        SUM(GOOD_QTY) AS GOOD_QTY,
        SUM(FAIL_QTY) AS FAIL_QTY
    FROM LAKEHOUSE_YIELD_TABLE
    WHERE WORK_MONTH BETWEEN {FROM_YM} AND {TO_YM}
      AND PRODUCT_FAMILY = {PRODUCT_FAMILY}
    GROUP BY WORK_MONTH, PRODUCT_FAMILY, MODE, TECH
)
SELECT
    WORK_MONTH,
    PRODUCT_FAMILY,
    MODE,
    TECH,
    INPUT_QTY,
    GOOD_QTY,
    FAIL_QTY,
    CASE WHEN INPUT_QTY = 0 THEN NULL ELSE GOOD_QTY / INPUT_QTY * 100 END AS YIELD_RATE
FROM monthly
---
source: goodocs_schedule
source_type: goodocs
description: Goodocs 생산 스케줄과 투입 계획 문서
keywords: schedule, 스케줄, 일정, 투입계획, 생산계획
example_questions: 오늘 Goodocs 스케줄에서 가장 많이 투입되는 제품 가져와줘, 이번 주 투입 계획 문서 조회해줘
doc_id: GOODOCS_DOCUMENT_ID
```

## Oracle Data용 TNS 입력 예시

Oracle Data 노드 안에는 기본 TNS 예시를 넣어두지 않습니다. 실제 Flow에서는 Text Input에 아래처럼 DB key별 TNS 문자열을 넣고 `Text Input.Output Text -> Oracle Data.oracle_config`로 연결합니다.

각 DB key는 `source_catalog`의 `db_key` 값과 같아야 합니다. 예를 들어 `source_catalog`에 `db_key: PKG_RPT`가 있으면 Oracle Data는 아래 입력 중 `PKG_RPT` 블록의 TNS를 사용합니다.

```text
PKG_RPT:
(DESCRIPTION=
  (ADDRESS=(PROTOCOL=TCP)(HOST=pkg-rpt-db.example.com)(PORT=1521))
  (CONNECT_DATA=(SERVICE_NAME=PKGRPT))
)

PKG_PLAN:
(DESCRIPTION=
  (ADDRESS=(PROTOCOL=TCP)(HOST=pkg-plan-db.example.com)(PORT=1521))
  (CONNECT_DATA=(SERVICE_NAME=PKGPLAN))
)

PKG_HIST:
(DESCRIPTION=
  (ADDRESS=(PROTOCOL=TCP)(HOST=pkg-hist-db.example.com)(PORT=1521))
  (CONNECT_DATA=(SERVICE_NAME=PKGHIST))
)
```

source_catalog에는 TNS를 넣지 않고 `db_key`, `query_template`만 둡니다. TNS 입력은 Oracle Data의 실행 입력으로만 사용합니다.

## 데이터 조회용 user_request 입력 예시

Oracle:

```text
2026년 5월 20일 FAB1 D/A1 공정에서 DDR5 제품 생산량을 MODE별로 가져와줘
```

LLM 예상 출력:

```json
{
  "name": "production_summary",
  "params": {
    "DATE": "20260520",
    "FACTORY": "FAB1",
    "OPER_NAME": "D/A1",
    "MODE": "DDR5",
    "PRODUCT_KEYWORD": "DDR5"
  }
}
```

H-API:

```text
LOT A12345가 D/A1부터 B/G1까지 어떻게 흘렀는지 trace 조회해줘
```

LLM 예상 출력:

```json
{
  "name": "lot_trace",
  "params": {
    "LOT_ID": "A12345",
    "START_OPER": "D/A1",
    "END_OPER": "B/G1"
  }
}
```

Datalake:

```text
2026년 1월부터 5월까지 DDR5 월별 수율 추이 가져와줘
```

LLM 예상 출력:

```json
{
  "name": "monthly_yield_summary",
  "params": {
    "FROM_YM": "202601",
    "TO_YM": "202605",
    "PRODUCT_FAMILY": "DDR5"
  }
}
```

Goodocs:

```text
오늘 Goodocs 스케줄에서 가장 많이 투입되는 제품 가져와줘
```

LLM 예상 출력:

```json
{
  "name": "goodocs_schedule",
  "params": {}
}
```

Multi-source:

```text
2026년 5월 20일 FAB1 D/A1 공정 DDR5 생산실적이랑 LOT A12345의 D/A1부터 B/G1까지 trace를 같이 가져와줘
```

LLM 예상 출력:

```json
{
  "requests": [
    {
      "name": "production_summary",
      "params": {
        "DATE": "20260520",
        "FACTORY": "FAB1",
        "OPER_NAME": "D/A1",
        "MODE": "DDR5",
        "PRODUCT_KEYWORD": "DDR5"
      }
    },
    {
      "name": "lot_trace",
      "params": {
        "LOT_ID": "A12345",
        "START_OPER": "D/A1",
        "END_OPER": "B/G1"
      }
    }
  ]
}
```

필수 정보가 부족한 경우:

```text
FAB1 생산 데이터 가져와줘
```

LLM 예상 출력:

```json
{
  "needs_more_info": true,
  "questions": ["production_summary 조회에 필요한 DATE, OPER_NAME, MODE, PRODUCT_KEYWORD 값을 알려주세요."],
  "requests": []
}
```
