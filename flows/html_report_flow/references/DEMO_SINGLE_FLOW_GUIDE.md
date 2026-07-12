# Demo Single HTML Report Flow Guide

별도 dataset flow가 없는 상태에서 `html_report_flow`를 체험하기 위한 단일 flow 구성입니다.

## 입력

사용자는 `00 리포트 요청/데이터 불러오기`에 아래 값을 넣습니다.

- `질문`: 무엇을 보고 싶은지
- `보고 싶은 방식`: KPI, 도넛, 추이, 상세 표 등 원하는 표현
- `데이터 직접 입력`: CSV/JSON 직접 붙여넣기
- 또는 `파일 데이터`: Langflow `Read File.Structured Content` 연결

## 기본 노드 흐름

```text
00 리포트 요청/데이터 불러오기
-> 01 데이터 구조 분석
-> 02 리포트 요소 카탈로그
-> 03 기본 리포트 계획
-> 03a 프롬프트 변수 준비
-> Prompt Template
-> LLM
-> 03b LLM 계획 검증
-> 04 HTML 렌더링
```

Prompt Template의 template 칸에는 `docs/PROMPT_TEMPLATE.md`의 `Prompt Template 본문`을 복사해 넣습니다. `03a 프롬프트 변수 준비`는 `사용자_요청_JSON`, `리포트_컨텍스트_JSON`, `디자인_지시`, `렌더링_규칙`, `출력_스키마_JSON`만 출력하므로, 이 5개를 Prompt Template의 같은 이름 변수 입력에 연결합니다.

최종 출력은 목적에 따라 둘 중 하나를 연결합니다.

```text
04 HTML 렌더링 -> 05-1 HTML 원문 출력 -> Chat Output
04 HTML 렌더링 -> 05-2 공유 링크 출력 -> Chat Output
```

## Minimal No-Server Experience

Report API 없이 Playground에서 전체 HTML 코드를 보려면:

```text
04 HTML 렌더링.HTML 생성 결과
-> 05-1 HTML 원문 출력.HTML 생성 결과
-> 05-1 HTML 원문 출력.HTML 원문
-> Chat Output.input
```

`05-1.HTML 원문`은 HTML이 실행되지 않도록 코드블록으로 감싸서 출력합니다.

## Full Link Experience

다운로드 링크까지 받으려면 각 PC에서 로컬 Report API 서버를 실행하고 `05-2 공유 링크 출력`을 연결합니다. MongoDB는 필요 없고, 생성 파일은 `report_api/storage/reports`에 저장됩니다.

```text
04 HTML 렌더링.HTML 생성 결과
-> 05-2 공유 링크 출력.HTML 생성 결과
-> 05-2 공유 링크 출력.링크 메시지
-> Chat Output.input
```

`05-2` 입력 예시:

| 화면 입력명 | 값 |
| --- | --- |
| `Report API 주소` | `http://127.0.0.1:8010` |
| `링크 유효시간` | `24` |

서버 실행 상세 절차는 아래 문서에 있습니다.

```text
docs/LOCAL_REPORT_API_GUIDE.md
```

## File Read Mode

```text
Read File.Structured Content
-> 00 리포트 요청/데이터 불러오기.파일 데이터
```

`00`에서는 `데이터 직접 입력`만 비워두면 됩니다. CSV/JSON/JSONL 형식은 자동으로 판별합니다.

`invalid handles`가 나오면 기존 edge를 삭제하고 0번 컴포넌트를 refresh/reload한 뒤 다시 연결하세요.

## Text Input Mode

CSV 전체를 `00.데이터 직접 입력`에 `Ctrl+A`, `Ctrl+C`, `Ctrl+V`로 넣어도 됩니다.

| 화면 입력명 | 값 |
| --- | --- |
| `데이터 직접 입력` | CSV 전체 내용 |

## 샘플 조합

질문, 보고 싶은 방식, `00.데이터 직접 입력`, `02.요소 양식 JSON` 샘플은 아래 문서 하나에 모아두었습니다.

```text
samples/INPUT_EXAMPLES.md
```

## 지원 데이터 형태

CSV:

```csv
DATE,OPER_SHORT_DESC,PRODUCT,WIP,PRODUCTION
2026-06-16,DA,A,120,80
2026-06-17,WB,B,90,100
```

JSON rows:

```json
[
  {"DATE": "2026-06-16", "OPER_SHORT_DESC": "DA", "PRODUCT": "A", "WIP": 120, "PRODUCTION": 80},
  {"DATE": "2026-06-17", "OPER_SHORT_DESC": "WB", "PRODUCT": "B", "WIP": 90, "PRODUCTION": 100}
]
```

JSON multiple datasets:

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

여러 dataset을 넣어도 단일 CSV/rows 입력 방식은 바뀌지 않습니다. 여러 dataset이 들어온 경우에는 `DATE`, `LINE`, `PROCESS`처럼 공통으로 보이는 key 컬럼을 찾아 `joined_auto` 분석 view를 만들고, 같은 schema의 dataset이면 `union_auto` 누적 view도 만듭니다. LLM 계획에서는 기본적으로 active view를 사용하되, 특정 블록만 원본 dataset을 보게 하고 싶으면 `block.data_view_id`로 `wip_status`, `production_result`, `joined_auto` 같은 view id를 지정할 수 있습니다.

멀티 데이터 테스트는 `samples/00_data_inputs/sample_multi_wip_output_quality.json` 전체를 `00.데이터 직접 입력`에 붙여넣으면 됩니다.
