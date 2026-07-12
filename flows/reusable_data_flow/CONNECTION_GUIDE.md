# 재사용 데이터 조회 Flow 연결 가이드

## 1. 먼저 등록할 Component

`component_refs.json`에 있는 12개 Component의 `.py` 파일을 각각 Agent Builder에 등록합니다. 모든 파일은 형제 모듈 import가 없는 Standalone 구조입니다.

## 2. 데이터 조회 메인 연결

| 순서 | From | Output | To | Input |
| --- | --- | --- | --- | --- |
| 1 | Chat Input | Message | Prompt Template | `user_request` |
| 2 | Source Catalog Text | Text | Prompt Template | `source_catalog` |
| 3 | Source Catalog Text | Text | Data Request Normalizer | `Data Catalog` |
| 4 | Prompt Template | Prompt | LLM Caller | `Prompt` |
| 5 | LLM Caller | LLM Result | Data Request Normalizer | `LLM Result` |
| 6 | Data Request Normalizer | Data Request | Oracle Data | `Data Request` |
| 7 | Data Request Normalizer | Data Request | H-API Data | `Data Request` |
| 8 | Data Request Normalizer | Data Request | Datalake Data | `Data Request` |
| 9 | Data Request Normalizer | Data Request | Goodocs Data | `Data Request` |
| 10 | 각 Source Node | Data Result | Data Result Merger | 같은 이름의 Result 입력 |
| 11 | Data Result Merger | Data Result | Data Output Builder | `Data Result` |
| 12 | Data Output Builder | Test Message | Chat Output | Input |

자동화 또는 다른 Flow로 연결할 때는 `Data Output Builder.Data JSON`을 사용합니다.

## 3. HTML Report 연결

| From | Output | To | Input |
| --- | --- | --- | --- |
| Data Output Builder | Data JSON | HTML Report Datasets Adapter | Data JSON |
| HTML Report Datasets Adapter | HTML Datasets Text | HTML Report Flow 00 | 데이터 직접 입력 |

## 4. Source Catalog 작성 연결

```text
Text Input.source_description
-> Prompt Template.source_description
-> LLM Caller.prompt
-> Catalog Normalizer.llm_result
```

결과는 두 방식으로 사용할 수 있습니다.

- `Catalog(Text직접연결용)`: Text Input 또는 Data Request Normalizer로 연결
- `Catalog(DB저장용)`: Catalog MongoDB Store로 연결

MongoDB에서 다시 읽을 때:

```text
Catalog MongoDB Loader.catalog_text -> Text Input.Text
Catalog MongoDB Loader.catalog_data -> JSON 결과 확인
```

## 5. 최소 테스트

1. Source Catalog에 하나의 dummy 소스를 넣습니다.
2. 그 소스를 선택할 수 있는 질문을 Chat Input에 넣습니다.
3. `Data Request`에 소스 이름과 params가 채워졌는지 확인합니다.
4. 해당 Source Node만 `success=true`인지 확인합니다.
5. `Data JSON.data_result[0]`이 row 배열인지 확인합니다.
6. Chat Output에 표 형태의 테스트 메시지가 보이는지 확인합니다.

실제 자격증명은 Flow JSON이나 샘플에 저장하지 않습니다.
