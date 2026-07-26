# 2.2 카탈로그 JSON 변환 Prompt Template

Langflow 기본 `Prompt Template` 컴포넌트에 아래 코드블록 내용을 그대로 붙여넣습니다.

## 연결 변수

| Prompt Template 변수 | 연결할 값 |
| --- | --- |
| `raw_catalog_text` | `2.2 카탈로그 JSON 프롬프트 변수 준비`의 `카탈로그 원문` |
| `operator_note` | `2.2 카탈로그 JSON 프롬프트 변수 준비`의 `운영자 메모` |
| `catalog_instructions` | `2.2 카탈로그 JSON 프롬프트 변수 준비`의 `카탈로그 변환 지침` |
| `catalog_output_schema` | `2.2 카탈로그 JSON 프롬프트 변수 준비`의 `출력 스키마 JSON` |

## 프롬프트

```text
당신은 Langflow 기능/개선 사례 카탈로그 운영자입니다.
아래 자연어 설명을 읽고 MongoDB에 저장할 수 있는 표준 카탈로그 JSON으로 변환하세요.

[카탈로그 원문]
{raw_catalog_text}

[운영자 메모]
{operator_note}

[작성 원칙]
{catalog_instructions}

[반환 JSON 스키마]
{catalog_output_schema}
```
