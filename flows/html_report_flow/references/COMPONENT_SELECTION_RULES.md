# 리포트 요소 선택 규칙

이 규칙은 `02_html_component_catalog_builder.py`가 deterministic 추천 블록을 만들 때 쓰는 기준이며, 이후 HTML Plan LLM 프롬프트에도 같은 기준을 넣습니다.

## 데이터 분석 신호

| 신호 | 의미 |
| --- | --- |
| `numeric_columns` | metric 후보. KPI, chart, ranking에 사용 |
| `dimension_columns` | group/category 후보. 비교 chart, ranking, pivot에 사용 |
| `time_columns` | time axis 후보. trend chart, period table에 사용 |
| `text_columns` | description/detail 후보. detail table에 사용 |
| `row_count` | 실제 전체 row 수. preview row 수와 구분 |
| `data_is_preview` | 일부 row만 표시 중인지 여부 |
| `warnings/errors` | warning_box 필요 여부 |

## 기본 우선순위

1. `report_header`는 항상 포함합니다.
2. `scope_summary`는 row_count, data_ref, filter, preview 여부가 있으면 포함합니다.
3. row_count가 0이면 `empty_state`를 우선하고 다른 chart는 제외합니다.
4. warnings/errors 또는 preview 상태면 `warning_box`를 포함합니다.
5. 숫자 metric이 있으면 `kpi_card_grid`를 고려합니다.
6. time + metric이 있으면 `trend_line_chart`를 고려합니다.
7. dimension + metric이 있으면 `comparison_bar_chart`와 `ranking_table`을 고려합니다.
8. row-level 확인 의도이거나 조회 결과면 `detail_data_table`을 포함합니다.
9. 너무 많은 블록은 피하고 기본 4-7개 안에서 조합합니다.

## 요청 의도 힌트

| 사용자 표현 | 우선 추천 요소 |
| --- | --- |
| 요약, 현황, 핵심, 주요 값 | kpi_card_grid, insight_bullets |
| 비교, 차이, 공정별, 제품별, 설비별 | comparison_bar_chart, comparison_table |
| 추이, 변화, 기간별, 날짜별 | trend_line_chart, period_comparison_table |
| 상위, 하위, top, bottom, 가장 많은 | ranking_table, comparison_bar_chart |
| 상세, 목록, row, 원본, 보여줘 | detail_data_table |
| 이상, 문제, 경고, 리스크, 초과 | warning_box, outlier_exception_table |
| 보고서, 리포트, 공유 | report_header, scope_summary, insight_bullets, recommendation_list |

## 안전 규칙

- 없는 컬럼은 plan에서 제거합니다.
- time 컬럼이 없으면 trend block을 사용하지 않습니다.
- numeric 컬럼이 없으면 KPI numeric aggregation과 chart를 사용하지 않습니다.
- 조회/상세 질문에는 group_by형 chart를 강제하지 않습니다.
- `data_ref`는 사용자 표시용 설명에만 쓰고, HTML 안에 credential/path를 노출하지 않습니다.
