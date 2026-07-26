# HTML Report Flow

`html_report_flow`는 CSV/JSON 데이터나 분석 결과를 받아 HTML 리포트를 만드는 Langflow용 기능 flow입니다.

데이터는 `00 리포트 요청/데이터 불러오기`에 직접 붙여넣거나 Langflow `Read File` output으로 넣을 수 있습니다. LLM은 HTML 코드를 직접 쓰지 않고, 사용할 리포트 요소와 배치/스타일/표시 정책을 JSON plan으로 완성합니다. 실제 HTML은 `04 HTML 렌더링`이 생성합니다.

## 추천 흐름

### HTML 원문을 Playground에 출력

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
-> 05-1 HTML 원문 출력
-> Chat Output
```

### 로컬 Report API 저장 후 링크 출력

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
-> 05-2 공유 링크 출력
-> Chat Output
```

기존 `06 HTML Report Response Builder`는 제거했습니다. 최종 응답은 목적에 따라 `05-1` 또는 `05-2`에서 끝납니다.

## Component Files

```text
langflow_components/html_report_flow/
  00_demo_report_request_loader.py
  01_data_profile_builder.py
  02_html_component_catalog_builder.py
  03_auto_html_plan_builder.py
  03a_llm_html_plan_prompt_builder.py
  03b_llm_html_plan_normalizer.py
  04_html_template_renderer.py
  05_1_html_source_output.py
  05_report_api_publisher.py
```

## Payload 정리 원칙

중간 payload는 필요한 정보만 넘기도록 줄였습니다.

- `00`은 선택된 rows를 `api_response.data`에만 담고, `available_datasets`에는 데이터셋 목록/컬럼/row 수만 둡니다.
- `03`은 LLM 프롬프트/검증에 필요한 데이터 분석/요소 카탈로그 요약만 `llm_context`에 담습니다.
- `03a`는 Langflow 기본 Prompt Template에 연결할 변수 JSON을 만듭니다. Prompt Template 본문은 `docs/PROMPT_TEMPLATE.md`에서 복사해 넣습니다.
- `03b`는 `03.기본 계획`과 LLM 응답만 연결하면 되고, profile/catalog를 다시 연결하지 않습니다.
- `04` 출력은 렌더링에 필요한 요약, plan, HTML 결과 중심으로 정리됩니다.
- `05-2` 링크 출력은 게시 성공 후 HTML 원문과 원본 rows를 빼고 짧은 안내 문구와 다운로드 링크 중심으로 반환합니다.

## 주요 출력 노드

| 목적 | 연결 |
| --- | --- |
| Playground에 HTML 전체 코드 출력 | `04 HTML 렌더링.HTML 생성 결과 -> 05-1 HTML 원문 출력.HTML 생성 결과`, `05-1.HTML 원문 -> Chat Output.input` |
| 로컬 Report API 링크 출력 | `04 HTML 렌더링.HTML 생성 결과 -> 05-2 공유 링크 출력.HTML 생성 결과`, `05-2.링크 메시지 -> Chat Output.input` |

`05-2 공유 링크 출력`은 MongoDB 없이 각 PC의 로컬 Report API 서버에 HTML을 저장합니다. 서버 실행 방법은 아래 문서를 보면 됩니다.

```text
docs/LOCAL_REPORT_API_GUIDE.md
report_api/README.md
```

## 지원 시각화 요소

| Block ID | 화면 이름 | 주 사용처 |
| --- | --- | --- |
| `kpi_card_grid` | KPI 카드 묶음 | 핵심 숫자 지표 요약 |
| `trend_line_chart` | 추이 선 그래프 | 날짜/시간별 변화 |
| `comparison_bar_chart` | 비교 막대 그래프 | 범주별 단일 metric 비교 |
| `grouped_bar_chart` | 묶음 막대 그래프 | 범주별 여러 metric 비교 |
| `stacked_comparison_bar` | 누적 구성 막대 | 범주 안의 상태/제품/구분 breakdown |
| `donut_chart` | 도넛 구성비 차트 | 비중, 구성비, 점유율 |
| `distribution_histogram` | 분포 히스토그램 | 숫자 컬럼 분포, 편차, 산포 |
| `scatter_plot` | 산점도 | 두 숫자 metric의 관계/상관 |
| `heatmap_matrix` | 교차 히트맵 | 두 dimension 교차값 비교 |
| `ranking_table`, `detail_data_table` | 순위/상세 표 | 조회 결과 검증 |

Prompt Template에 들어가는 LLM prompt에는 차트 선택 기준과 레이아웃 균형 규칙이 포함되어 있습니다. 같은 row의 카드들은 `half+half`처럼 맞추고, 긴 표와 legend가 많은 차트는 full width로 배치하도록 유도합니다.

## Samples

```text
samples/INPUT_EXAMPLES.md
```

`INPUT_EXAMPLES.md`에 `00.질문`, `00.보고 싶은 방식`, `00.데이터 직접 입력`, `02.요소 양식 JSON` 예시가 함께 정리되어 있습니다.

실제 입력 파일은 아래 두 폴더에만 있습니다.

```text
samples/00_data_inputs
samples/02_component_catalogs
```

## 상세 연결 문서

Langflow에서 어떤 output을 어떤 input에 연결해야 하는지는 아래 문서를 기준으로 보면 됩니다.

```text
CONNECTION_GUIDE.md
```

Prompt Template 노드의 template 칸에 넣을 원문은 아래 문서에 따로 분리해두었습니다.

```text
docs/PROMPT_TEMPLATE.md
```
