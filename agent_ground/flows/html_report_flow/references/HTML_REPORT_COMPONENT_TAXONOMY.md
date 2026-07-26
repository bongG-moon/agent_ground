# HTML 리포트 요소 유형

이 문서는 데이터 분석 리포트와 데이터 조회 결과 확인 화면에서 반복적으로 쓰이는 요소를 Langflow HTML report block으로 유형화한 기준입니다.

## 요소 그룹

| 그룹 | 목적 | 대표 block_id |
| --- | --- | --- |
| Context | 사용자가 무엇을 보고 있는지 알려줌 | report_header, scope_summary, method_note |
| KPI | 핵심 수치를 빠르게 확인 | kpi_card_grid, metric_delta_card_grid |
| Comparison | 그룹 간 차이 비교 | comparison_bar_chart, stacked_comparison_bar, comparison_table |
| Trend | 시간에 따른 변화 확인 | trend_line_chart, period_comparison_table |
| Ranking | 상위/하위 항목 확인 | ranking_table, rank_change_table |
| Detail | 원본/상세 row 확인 | detail_data_table, pivot_matrix_table |
| Quality | 이상치/예외/주의사항 확인 | outlier_exception_table, warning_box, empty_state |
| Narrative | 해석과 다음 행동 전달 | insight_bullets, recommendation_list |

## Block Catalog

### report_header

리포트 제목, 생성 기준, 데이터 범위, row 수를 표시합니다. 대부분의 HTML 결과에서 첫 블록으로 사용합니다.

Use when:
- 항상 권장
- 여러 블록을 조합하는 dashboard/report 형태

Data needs:
- title
- row_count
- optional dataset/source/date scope

### scope_summary

적용된 필터, 조회 조건, 데이터셋, preview 여부, data_ref 여부를 요약합니다.

Use when:
- 조회 결과 확인
- 후속 질문에서 어떤 범위가 유지되는지 보여줘야 할 때
- preview rows만 표시 중인 경우

Data needs:
- applied_scope
- columns
- row_count
- data_ref/data_is_preview

### kpi_card_grid

총합, 평균, 최대/최소, 건수, 고유 항목 수 같은 핵심 지표를 카드 형태로 보여줍니다.

Use when:
- 숫자 metric 컬럼이 있음
- 사용자가 요약, 현황, 전체 규모를 알고 싶어 함
- 비교/추이 전에 핵심 수치를 먼저 보여주는 것이 좋은 경우

Avoid when:
- 숫자 컬럼이 없음
- row-level 목록만 요청한 경우

### metric_delta_card_grid

현재값과 기준값, 목표값, 이전 기간 대비 증감을 함께 보여줍니다.

Use when:
- target, baseline, previous, delta, rate 계열 컬럼이 있음
- 사용자가 증감, 달성률, 변화폭을 물음

### comparison_bar_chart

공정별, 제품별, 설비별처럼 범주별 숫자 차이를 막대 그래프로 보여줍니다.

Use when:
- dimension 컬럼 1개 이상
- numeric metric 컬럼 1개 이상
- 상위 N 또는 그룹 비교 의도가 있음

### stacked_comparison_bar

하나의 dimension 안에서 category composition을 보여줍니다.

Use when:
- dimension 컬럼 2개 이상
- numeric metric 컬럼 1개 이상
- 구성비/비중/상태별 breakdown이 중요함

### trend_line_chart

시간 축을 기준으로 metric 변화 흐름을 보여줍니다.

Use when:
- 날짜/시간 컬럼이 있음
- 사용자가 추이, 변화, 기간별 비교를 물음

Avoid when:
- 시간 컬럼이 없거나 row 수가 너무 적음

### period_comparison_table

기간별 실적을 표 형태로 비교합니다. 추이 chart와 함께 쓰기 좋습니다.

Use when:
- 일/주/월/분기 컬럼이 있음
- 여러 기간의 값을 정확히 비교해야 함

### ranking_table

Top N, Bottom N, 순위, 가장 큰/작은 항목을 보여줍니다.

Use when:
- 사용자가 상위/하위/많은/적은/높은/낮은 항목을 요구
- dimension + metric 조합이 있음

### rank_change_table

순위 변동을 보여줍니다.

Use when:
- current_rank, previous_rank, rank_delta 같은 컬럼이 있음
- 기간별 순위 변화가 중요함

### detail_data_table

분석 결과 또는 조회 결과 row를 그대로 확인합니다.

Use when:
- 사용자가 목록, 상세, row, 원본, 결과 확인을 요청
- 전체 row가 아니라 preview만 보여줄 때도 마지막 블록으로 유용

Important:
- 이 블록은 group_by를 강제하지 않습니다.
- row-level 질문이면 chart보다 detail table이 우선입니다.

### pivot_matrix_table

두 dimension의 교차표 형태로 metric을 보여줍니다.

Use when:
- 공정 x 제품, 날짜 x 공정처럼 matrix가 자연스러운 데이터
- 한 화면에서 많은 조합을 비교해야 함

### distribution_histogram

숫자 분포를 보여줍니다.

Use when:
- 편차, 분포, 산포, 이상치 가능성을 확인해야 함
- raw/detail row가 충분히 있음

### outlier_exception_table

임계치 초과, 결측, 상태 이상, 실패 항목을 강조합니다.

Use when:
- warning/error/status/threshold 계열 컬럼이 있음
- 사용자가 문제, 이상, 리스크, 예외를 물음

### warning_box

데이터가 preview인지, row가 잘렸는지, 오류/경고가 있는지 보여줍니다.

Use when:
- warnings/errors 존재
- data_is_preview=true
- data_ref만 있고 전체 row가 복원되지 않음

### insight_bullets

LLM이 만든 해석 문장 또는 deterministic 요약을 bullet로 보여줍니다.

Use when:
- dashboard/report 형태에서는 거의 항상 유용
- 단순 조회 결과에서는 선택 사항

### recommendation_list

다음 액션 또는 확인할 항목을 제안합니다.

Use when:
- 진단, 원인 분석, 개선 제안 요청
- 현업 보고서용 결과

### empty_state

조건에 맞는 결과가 없을 때 표시합니다.

Use when:
- row_count=0
- rows가 비어 있음

## 자주 쓰는 리포트 배치

### 조회 결과 검토

```text
report_header
scope_summary
warning_box optional
detail_data_table
```

### KPI 요약 리포트

```text
report_header
scope_summary
kpi_card_grid
insight_bullets
detail_data_table optional
```

### 비교 대시보드

```text
report_header
scope_summary
kpi_card_grid
comparison_bar_chart
ranking_table
detail_data_table
```

### 추이 리포트

```text
report_header
scope_summary
kpi_card_grid
trend_line_chart
period_comparison_table
detail_data_table optional
```

### 예외/진단 리포트

```text
report_header
scope_summary
warning_box
outlier_exception_table
comparison_bar_chart optional
recommendation_list
```

## LLM 역할

LLM이 선택할 것:

- which blocks are needed
- block order
- title/caption
- column bindings
- aggregation/sort/limit

LLM이 생성하면 안 되는 것:

- raw HTML
- script tags
- arbitrary CSS
- external CDN references
- file paths
