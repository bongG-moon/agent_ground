# HTML 리포트 사용 가능 요소 가이드

이 문서는 `00 리포트 요청/데이터 불러오기`의 `보고 싶은 방식`에 어떤 요소를 요청할 수 있는지 정리한 참고 문서입니다.

샘플 입력 문서 [INPUT_EXAMPLES.md](../samples/INPUT_EXAMPLES.md)를 작성하거나 수정할 때 아래 표현을 그대로 섞어 쓰면 됩니다. 사용자는 `block_id`를 몰라도 되지만, 구체적인 차트 이름, 배치, 기준 컬럼, 정렬 기준을 적을수록 LLM이 더 잘 반영합니다.

## 1. 요청 작성 기본형

아래처럼 작성하면 가장 안정적입니다.

```text
상단에는 KPI 카드 5개를 배치해줘.
중간에는 DATE별 OUTPUT_QTY 추이 선 그래프를 full width로 보여줘.
우측에는 ALERT_LEVEL별 WIP_QTY 비중 도넛 차트를 배치해줘.
하단 표는 BACKLOG_QTY 내림차순으로 정렬하고, 컬럼은 DATE, LINE, PROCESS, WIP_QTY만 보여줘.
```

작성할 때 같이 적으면 좋은 항목:

| 항목 | 예시 |
| --- | --- |
| 배치 | 상단, 중간, 하단, 좌측 2/3, 우측 1/3, half, full width |
| 차트 종류 | KPI 카드, 추이 선 그래프, 막대그래프, 도넛 차트, 히스토그램, 산점도, 히트맵 |
| 기준 컬럼 | DATE별, PROCESS별, ALERT_LEVEL별, PRODUCT_FAMILY 기준 |
| 수치 컬럼 | WIP_QTY, OUTPUT_QTY, DEFECT_QTY, YIELD_RATE |
| 정렬/제한 | 내림차순, 상위 5개, HIGH만, WARN 이상만 |
| 스타일 | compact, comfortable, 임원용, 운영자용, 경고 강조, primary 색상 |

## 2. 요소 빠른 선택표

| 보고 싶은 것 | 요청 표현 예시 | 내부 요소 |
| --- | --- | --- |
| 제목/요약 영역 | 리포트 제목과 데이터 범위를 상단에 보여줘 | `report_header` |
| 데이터 범위 요약 | 사용된 데이터 row 수와 컬럼 수를 요약해줘 | `scope_summary` |
| 핵심 숫자 요약 | KPI 카드 5개를 상단에 배치해줘 | `kpi_card_grid` |
| 증감/달성률 요약 | 현재값과 목표 대비 증감률을 카드로 보여줘 | `metric_delta_card_grid` |
| 시간 변화 | DATE별 OUTPUT_QTY 추이 선 그래프를 보여줘 | `trend_line_chart` |
| 범주 비교 | PROCESS별 WIP_QTY 막대그래프로 비교해줘 | `comparison_bar_chart` |
| 여러 지표 비교 | PROCESS별 WIP_QTY와 OUTPUT_QTY를 묶음 막대로 비교해줘 | `grouped_bar_chart` |
| 구성/비중 | ALERT_LEVEL별 WIP_QTY 비중을 도넛 차트로 보여줘 | `donut_chart` |
| 누적 구성 | PROCESS별 STATUS 구성을 누적 막대로 보여줘 | `stacked_comparison_bar` |
| 순위 | BACKLOG_QTY 상위 10개를 순위 표로 보여줘 | `ranking_table` |
| 순위 변화 | 이전 순위와 현재 순위 변동을 표로 보여줘 | `rank_change_table` |
| 상세 row 확인 | 원본 데이터를 상세 표로 보여줘 | `detail_data_table` |
| 기간별 정확한 값 | 날짜별 수치를 표로 비교해줘 | `period_comparison_table` |
| 이상/예외 row | HIGH 또는 WARN인 행만 강조 표로 보여줘 | `outlier_exception_table` |
| 교차 비교 | PROCESS와 PRODUCT_FAMILY 교차 히트맵을 보여줘 | `heatmap_matrix` |
| 교차표 | PROCESS x PRODUCT_FAMILY 교차표를 보여줘 | `pivot_matrix_table` |
| 분포 | YIELD_RATE 분포 히스토그램을 보여줘 | `distribution_histogram` |
| 상관/관계 | DEFECT_QTY와 CYCLE_TIME_HR 관계를 산점도로 보여줘 | `scatter_plot` |
| 해석 문장 | 핵심 발견사항을 bullet로 요약해줘 | `insight_bullets` |
| 다음 조치 | 다음 확인 사항 2개를 제안해줘 | `recommendation_list` |
| 생성 기준 | 집계 기준과 주의사항을 하단에 넣어줘 | `method_note` |
| 오류/주의 | preview 제한이나 경고를 박스로 보여줘 | `warning_box` |
| 결과 없음 | 조건에 맞는 데이터가 없을 때 안내를 보여줘 | `empty_state` |

## 3. 컨텍스트/요약 요소

### 리포트 제목: `report_header`

리포트 맨 위의 큰 제목 영역입니다. Material dashboard 스타일의 primary gradient hero로 렌더링됩니다.

사용하기 좋은 경우:

- 전체 리포트의 주제와 데이터 범위를 먼저 보여주고 싶을 때
- 임원 보고나 운영 대시보드처럼 첫 화면의 제목이 중요할 때

요청 예시:

```text
상단에는 "품질 및 공정 위험 진단 리포트"라는 제목과 분석 범위를 크게 보여줘.
```

### 데이터 범위 요약: `scope_summary`

데이터셋, row 수, preview 여부, 컬럼 수를 카드로 요약합니다.

요청 예시:

```text
제목 아래에는 분석에 사용된 데이터 범위와 row 수, 컬럼 수를 요약해줘.
```

### 주의사항 박스: `warning_box`

preview 데이터, 오류, 경고, 데이터 제한사항을 노란 계열 notice로 보여줍니다.

요청 예시:

```text
데이터 품질 경고나 preview 제한이 있으면 상단에 주의사항 박스로 보여줘.
```

## 4. KPI/카드 요소

### KPI 카드 묶음: `kpi_card_grid`

핵심 숫자 지표를 카드로 보여줍니다. 현재 기본 렌더러는 최대 6개까지 안정적으로 표시합니다.

주요 설정:

| 설정 | 의미 | 예시 |
| --- | --- | --- |
| `metrics.label` | 카드에 보일 이름 | 총 WIP |
| `metrics.column` | 집계할 숫자 컬럼 | WIP_QTY |
| `metrics.aggregation` | 집계 방식 | sum, avg, min, max, count, nunique |

요청 예시:

```text
상단 첫 줄에는 KPI 카드 5개를 한 줄로 배치해줘.
KPI는 총 WIP_QTY, 총 OUTPUT_QTY, 총 DEFECT_QTY, 평균 YIELD_RATE, HIGH 건수로 구성해줘.
```

### 증감 카드: `metric_delta_card_grid`

현재 렌더링은 KPI 카드와 같은 카드 UI를 사용합니다. 목표 대비 증감률 같은 표현을 요청할 때 사용합니다.

요청 예시:

```text
목표 대비 생산량 달성률과 전일 대비 WIP 증감을 카드로 보여줘.
```

## 5. 차트 요소

### 추이 선 그래프: `trend_line_chart`

시간/날짜 컬럼에 따른 숫자 metric 변화를 보여줍니다.

필요한 데이터:

| 필수 | 설명 |
| --- | --- |
| x | 날짜/시간/기간 컬럼 |
| y | 숫자 metric 컬럼 |

요청 예시:

```text
DATE별 OUTPUT_QTY 추이를 full width 선 그래프로 크게 보여줘.
```

### 비교 막대 그래프: `comparison_bar_chart`

하나의 범주 기준으로 하나의 숫자 지표를 비교합니다.

요청 예시:

```text
PROCESS별 BACKLOG_QTY를 막대그래프로 비교하고, 값이 큰 순서로 보여줘.
```

### 묶음 막대 그래프: `grouped_bar_chart`

같은 범주 안에서 여러 숫자 metric을 나란히 비교합니다.

요청 예시:

```text
PROCESS별 WIP_QTY, OUTPUT_QTY, DEFECT_QTY를 묶음 막대그래프로 비교해줘.
```

### 도넛 구성비 차트: `donut_chart`

범주별 비중/구성비를 보여줍니다. 범주가 너무 많으면 6-8개 정도로 제한하는 것이 좋습니다.

요청 예시:

```text
ALERT_LEVEL별 WIP_QTY 비중을 도넛 차트로 보여줘.
범주는 HIGH, WARN, NORMAL 순서로 읽기 쉽게 보여줘.
```

### 누적 구성 막대: `stacked_comparison_bar`

큰 범주 안의 내부 구성을 비교합니다.

필요한 데이터:

| 설정 | 설명 |
| --- | --- |
| x | 큰 범주 |
| series | 내부 구분 |
| y | 숫자 metric |

요청 예시:

```text
PROCESS별 ALERT_LEVEL 구성을 누적 막대그래프로 보여줘.
```

### 분포 히스토그램: `distribution_histogram`

숫자 컬럼의 분포, 구간별 빈도, 평균, 중앙값, 범위를 보여줍니다.

요청 예시:

```text
YIELD_RATE 분포 히스토그램을 full width로 크게 보여줘.
구간별 count와 평균 위치가 보이게 해줘.
YIELD_RATE가 낮은 구간은 warning 느낌으로 강조해줘.
```

### 산점도: `scatter_plot`

두 숫자 metric 사이의 관계, 산포, 평균선, 추세선, 상관계수를 보여줍니다.

요청 예시:

```text
DEFECT_QTY와 CYCLE_TIME_HR의 관계를 산점도로 보여줘.
x축은 DEFECT_QTY, y축은 CYCLE_TIME_HR로 하고 상관계수와 추세선을 같이 보여줘.
```

### 교차 히트맵: `heatmap_matrix`

두 범주 축의 조합별 숫자 크기를 색상 강도와 수치로 보여줍니다. 행/열 총계도 함께 표시됩니다.

요청 예시:

```text
PROCESS와 PRODUCT_FAMILY를 교차한 DEFECT_QTY 합계를 히트맵으로 보여줘.
값이 큰 cell은 진하게 보이게 하고 총계도 같이 보여줘.
```

### 교차표: `pivot_matrix_table`

현재 기본 렌더러에서는 히트맵형 교차표 UI로 표시됩니다. 색상보다 정확한 교차값 확인이 중요할 때 요청합니다.

요청 예시:

```text
PROCESS x PRODUCT_FAMILY 교차표로 WIP_QTY를 보여줘.
```

## 6. 표 요소

### 순위 표: `ranking_table`

상위/하위 N개 항목을 보여줍니다.

요청 예시:

```text
BACKLOG_QTY가 높은 상위 10개를 순위 표로 보여줘.
PROCESS, LINE, BACKLOG_QTY, WIP_QTY, YIELD_RATE 컬럼만 사용해줘.
```

### 순위 변동 표: `rank_change_table`

현재 순위와 이전 순위의 변동을 표로 보여줄 때 사용합니다. 데이터에 이전 순위나 기준 기간 컬럼이 있어야 자연스럽게 구성됩니다.

요청 예시:

```text
이전 기간 대비 PROCESS별 BACKLOG_QTY 순위 변동을 표로 보여줘.
```

### 상세 데이터 표: `detail_data_table`

분석 결과 row를 그대로 확인하는 표입니다. 많은 컬럼을 넣으면 full width를 권장합니다.

요청 예시:

```text
마지막에는 상세 데이터 표를 full width로 보여줘.
표 컬럼은 DATE, LINE, PROCESS, STATUS, WIP_QTY, BACKLOG_QTY, DEFECT_QTY, YIELD_RATE만 사용해줘.
```

### 기간 비교 표: `period_comparison_table`

기간별 값을 정확히 비교하는 표입니다.

요청 예시:

```text
DATE별 OUTPUT_QTY와 DEFECT_QTY를 기간 비교 표로 보여줘.
```

### 이상/예외 표: `outlier_exception_table`

위험 row, 조건에 맞는 row, 임계치 초과 row를 강조해서 보여줍니다.

요청 예시:

```text
ALERT_LEVEL이 HIGH 또는 WARN인 행만 이상/예외 표로 보여줘.
DEFECT_QTY 내림차순으로 정렬하고 HIGH는 빨간색으로 강조해줘.
```

## 7. 해석/조치 요소

### 핵심 해석 문장: `insight_bullets`

LLM이 만든 핵심 발견사항을 bullet로 보여줍니다.

요청 예시:

```text
차트 아래에는 핵심 발견사항 3개를 bullet로 요약해줘.
```

### 다음 확인 사항: `recommendation_list`

후속 확인/조치 항목을 제안합니다.

요청 예시:

```text
마지막에는 현장에서 바로 확인할 다음 조치 2개를 짧게 제안해줘.
```

### 생성 기준: `method_note`

집계 방식, 데이터 제한, 해석 주의사항을 하단에 보여줍니다.

요청 예시:

```text
하단에는 사용한 집계 기준과 데이터 한계를 짧게 적어줘.
```

## 8. 레이아웃/스타일 요청어

아래 표현을 `보고 싶은 방식`에 넣으면 LLM이 block 순서와 width, density, emphasis로 변환합니다.

| 표현 | 의미 |
| --- | --- |
| `full width` | 한 줄 전체 너비 |
| `half` 또는 `좌우 반반` | 한 줄에 2개 카드 |
| `좌측 2/3, 우측 1/3` | 중요한 차트는 넓게, 보조 차트는 좁게 |
| `compact` | 운영자용, 촘촘한 테이블/카드 |
| `comfortable` | 임원 보고용, 여백 넓게 |
| `high emphasis` | 중요한 카드/차트를 더 눈에 띄게 |
| `primary 색상` | teal/blue 계열 앱바와 hero에 어울리는 기본 색상 |
| `warning 강조` | 주황/노랑 계열 강조 |
| `danger 강조` | 빨강 계열 강조 |

## 9. 복합 요청 예시

```text
품질 엔지니어가 원인을 추적하는 진단 리포트로 만들어줘.
상단에는 KPI 카드 4개를 보여줘.
첫 번째 본문은 full width로 YIELD_RATE 분포 히스토그램을 크게 보여줘.
두 번째 본문은 좌측 half에 DEFECT_QTY와 CYCLE_TIME_HR 산점도,
우측 half에 PROCESS x PRODUCT_FAMILY 평균 DEFECT_QTY 히트맵을 배치해줘.
마지막에는 ALERT_LEVEL이 HIGH이거나 YIELD_RATE가 95 이하인 행만 이상/예외 표로 보여줘.
표는 DEFECT_QTY 내림차순으로 정렬하고 DATE, LINE, PROCESS, DEFECT_QTY, YIELD_RATE, ALERT_LEVEL만 사용해줘.
전체는 primary 색상을 사용하되 HIGH와 낮은 수율만 danger/warning으로 강조해줘.
```

## 10. 주의할 점

- 실제 데이터에 없는 컬럼명을 쓰면 `03b LLM 계획 검증`에서 제거될 수 있습니다.
- 차트는 `x`, `y`, `series`, `metrics`, `columns`, `sort`, `limit`을 구체적으로 적을수록 잘 나옵니다.
- 표 컬럼이 많으면 `full width`를 같이 요청하는 것이 좋습니다.
- 도넛/누적/히트맵은 범주가 많으면 `상위 6개`, `상위 8개`처럼 limit을 요청하는 것이 좋습니다.
- LLM이 HTML을 직접 만드는 구조가 아니라, 이 문서의 요소를 조합한 `report_plan`을 만들고 `04 HTML 렌더링`이 최종 HTML을 생성합니다.
