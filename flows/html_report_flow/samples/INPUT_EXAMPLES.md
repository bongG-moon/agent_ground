# HTML Report Flow 입력 예시

이 문서는 Langflow에서 바로 테스트할 수 있도록 `00 리포트 요청/데이터 불러오기`와 `02 기본 요소 양식/추천`에 넣을 값을 정리한 예시입니다.

실제로 값을 복사해서 테스트할 때는 아래 파일 하나만 열어도 됩니다.

```text
samples/00_data_inputs/ONE_FILE_TEST_CASES.md
```

위 문서에는 각 테스트 케이스별 `질문`, `보고 싶은 방식`, `데이터 직접 입력`, `03a 추가 구현 지시사항`, `02 요소 양식 JSON` 원문이 모두 들어 있습니다.

샘플 파일 위치:

```text
samples/
├─ 00_data_inputs/        # 00.데이터 직접 입력에 넣는 CSV/JSON
└─ 02_component_catalogs/ # 02.요소 양식 JSON에 넣는 선택 입력
```

## 사용 방법

이 문서의 예시는 빠른 목록 확인용입니다. 실제 복사 입력은 `ONE_FILE_TEST_CASES.md`를 사용합니다.

각 예시에서 아래 값만 그대로 사용합니다.

- `00.질문`
- `00.보고 싶은 방식`
- `00.데이터 직접 입력`: `ONE_FILE_TEST_CASES.md`의 해당 코드블록을 복사해서 붙여넣기
- `02.요소 양식 JSON`: 비워두거나 `ONE_FILE_TEST_CASES.md` 하단의 catalog JSON을 복사해서 붙여넣기

처음 테스트할 때는 `02.요소 양식 JSON`을 비워두는 것을 권장합니다. 특정 톤이나 구성을 더 강하게 테스트하고 싶을 때만 catalog 파일을 넣습니다.

## 예시 1. 공정 WIP 운영 대시보드

### 00에 넣을 값

`질문`:

```text
공정별 WIP와 생산량을 비교하고 날짜별 추이를 보여줘
```

`보고 싶은 방식`:

```text
상단에는 KPI 카드로 총 WIP, 총 생산량, 평균 수율을 보여줘.
중간에는 날짜별 생산량 추이 선 그래프를 크게 보여주고, 공정별 WIP와 생산량은 묶음 막대그래프로 비교해줘.
마지막에는 원본 상세 표를 보여줘.
전체는 운영자가 빠르게 볼 수 있는 compact 대시보드로 구성해줘.
```

`데이터 직접 입력`:

```text
samples/00_data_inputs/sample_wip.csv
```

### 02에 넣을 값

`요소 양식 JSON`:

```text
samples/02_component_catalogs/catalog_operations_compact.json
```

비워둬도 실행됩니다.

## 예시 2. 품질 진단 리포트

### 00에 넣을 값

`질문`:

```text
불량 수 분포와 수율 관계, warning row를 진단해줘
```

`보고 싶은 방식`:

```text
품질 엔지니어가 원인을 추적하는 진단 리포트로 만들어줘.
상단에는 평균 수율, 총 불량 수, warning 건수를 KPI로 보여줘.
첫 번째 분석 영역에는 DEFECT_COUNT 분포 히스토그램을 full width로 보여줘.
두 번째 분석 영역에는 CYCLE_TIME_SEC와 YIELD_RATE의 관계를 산점도로 보여줘.
하단에는 STATUS가 warning 또는 danger인 행만 표로 보여주고 DEFECT_COUNT 내림차순으로 정렬해줘.
위험 상태는 빨간색 또는 주황색으로 강조해줘.
```

`데이터 직접 입력`:

```text
samples/00_data_inputs/sample_quality_diagnostics.csv
```

### 02에 넣을 값

`요소 양식 JSON`:

```text
samples/02_component_catalogs/catalog_quality_diagnostics.json
```

비워둬도 실행됩니다.

## 예시 3. 채널/지역 매출 구성 리포트

### 00에 넣을 값

`질문`:

```text
채널별 매출 비중을 보고 지역별 매출과 주문 수를 비교해줘
```

`보고 싶은 방식`:

```text
임원이 빠르게 읽을 수 있는 요약형 리포트로 만들어줘.
상단 KPI는 총 매출, 총 주문 수, 평균 마진율 3개만 크게 보여줘.
중간에는 CHANNEL별 REVENUE 비중을 도넛 차트로 보여주고, REGION별 REVENUE와 ORDERS는 묶음 막대그래프로 비교해줘.
마지막에는 매출 상위 조합을 순위 표로 보여줘.
전체 여백은 comfortable하게 구성해줘.
```

`데이터 직접 입력`:

```text
samples/00_data_inputs/sample_sales_channel_mix.csv
```

### 02에 넣을 값

`요소 양식 JSON`:

```text
samples/02_component_catalogs/catalog_executive_summary.json
```

비워둬도 실행됩니다.

## 예시 4. 여러 데이터셋 결합 운영 리포트

### 00에 넣을 값

`질문`:

```text
WIP, 생산, 품질 데이터를 DATE, LINE, PROCESS 기준으로 함께 보고 병목과 위험 공정을 확인하고 싶어
```

`보고 싶은 방식`:

```text
여러 데이터셋을 DATE, LINE, PROCESS 기준으로 결합해서 하나의 운영 리포트로 만들어줘.
상단 첫 줄에는 KPI 카드 5개를 배치해줘. KPI는 총 WIP_QTY, 총 OUTPUT_QTY, 평균 YIELD_RATE, 총 DEFECT_QTY, 총 BACKLOG_QTY로 구성해줘.
두 번째 줄은 좌측 2/3 너비에 DATE별 OUTPUT_QTY 추이 선 그래프를 보여주고 각 포인트에 값을 표시해줘.
오른쪽 1/3 너비에는 WIP 데이터 기준 ALERT_LEVEL별 WIP_QTY 비중을 도넛 차트로 보여줘.
세 번째 줄은 PROCESS별 WIP_QTY, OUTPUT_QTY, DEFECT_QTY를 묶음 막대그래프로 비교해줘.
마지막에는 ALERT_LEVEL이 HIGH 또는 WARN이거나 YIELD_RATE가 95 이하인 행만 상세 표로 보여줘.
표 컬럼은 DATE, LINE, PROCESS, STATUS, ALERT_LEVEL, WIP_QTY, OUTPUT_QTY, BACKLOG_QTY, DEFECT_QTY, YIELD_RATE만 사용하고 BACKLOG_QTY 내림차순으로 정렬해줘.
색상은 보라/블루 primary 계열을 쓰고 위험 상태만 주황/빨강으로 강조해줘.
```

`데이터 직접 입력`:

```text
samples/00_data_inputs/sample_multi_wip_output_quality.json
```

`03a.추가 구현 지시사항`에 넣으면 좋은 값:

```text
wip_status 데이터의 ALERT_LEVEL 값은 NORMAL, WARN, HIGH이고 HIGH가 가장 위험한 상태야.
wip_status 데이터의 WIP_QTY는 공정에 쌓여있는 재공 수량이야.
production_result 데이터의 OUTPUT_QTY는 생산량이고 YIELD_RATE는 수율이야. YIELD_RATE가 95 이하이면 주의 구간으로 봐줘.
quality_backlog 데이터의 DEFECT_QTY는 불량 수량이고 BACKLOG_QTY는 미처리 물량이야.
위험 상세 표는 ALERT_LEVEL이 HIGH 또는 WARN이거나 YIELD_RATE가 95 이하인 행만 보여줘.
```

### 02에 넣을 값

`요소 양식 JSON`:

```text
samples/02_component_catalogs/catalog_operations_compact.json
```

비워둬도 실행됩니다.

## 예시 5. 상세 지시 반영 스트레스 테스트

### 00에 넣을 값

`질문`:

```text
라인/공정별 WIP, 생산량, 불량, 수율, 지연 상태를 종합해서 병목과 위험 공정을 확인하고 싶어
```

`보고 싶은 방식`:

```text
현장 운영자가 아침 회의에서 바로 볼 수 있는 한 화면짜리 compact 대시보드로 만들어줘.
상단 첫 줄에는 KPI 카드 5개를 한 줄로 배치해줘. KPI는 총 WIP_QTY, 총 OUTPUT_QTY, 총 DEFECT_QTY, 평균 YIELD_RATE, ALERT_LEVEL이 HIGH인 건수로 구성해줘.
KPI 카드 중 HIGH 건수와 DEFECT_QTY는 빨간색/주황색 계열로 강조하고, 나머지는 초록 계열로 차분하게 보여줘.
두 번째 줄은 좌측 2/3 너비에 DATE별 OUTPUT_QTY 추이 선 그래프를 크게 배치하고, 우측 1/3 너비에는 ALERT_LEVEL별 WIP_QTY 비중 도넛 차트를 배치해줘.
세 번째 줄은 PROCESS별 WIP_QTY와 BACKLOG_QTY를 묶음 막대그래프로 보여주고, 같은 줄 오른쪽에는 PROCESS별 평균 YIELD_RATE를 비교 막대그래프로 보여줘.
마지막에는 ALERT_LEVEL이 HIGH 또는 WARN인 행만 표로 보여줘. 표 컬럼은 DATE, LINE, PROCESS, STATUS, WIP_QTY, BACKLOG_QTY, DEFECT_QTY, YIELD_RATE만 사용하고, BACKLOG_QTY 내림차순으로 정렬해줘.
설명 문장은 짧게 하고, 각 차트에는 가장 위험한 공정 1개만 annotation으로 표시해줘.
```

`데이터 직접 입력`:

```text
samples/00_data_inputs/sample_instruction_stress.csv
```

### 02에 넣을 값

`요소 양식 JSON`:

```text
samples/02_component_catalogs/catalog_operations_compact.json
```

비워둬도 실행됩니다.

## 예시 6. 재고 흐름 및 창고 상태 리포트

### 00에 넣을 값

`질문`:

```text
창고별 재고 상태 구성과 입출고 흐름을 비교해서 재고 위험을 확인하고 싶어
```

`보고 싶은 방식`:

```text
상단에는 총 ON_HAND, 총 INBOUND, 총 OUTBOUND, 평균 DAYS_OF_SUPPLY를 KPI 카드로 보여줘.
중간에는 CATEGORY별 ON_HAND 비중을 도넛 차트로 보여주고, WAREHOUSE별 INBOUND와 OUTBOUND를 묶음 막대그래프로 비교해줘.
그 아래에는 WAREHOUSE별 STOCK_STATUS 구성을 누적 막대그래프로 보여줘.
마지막 표는 STOCK_STATUS가 watch 또는 risk인 행만 보여주고 DAYS_OF_SUPPLY 오름차순으로 정렬해줘.
표 컬럼은 DATE, WAREHOUSE, CATEGORY, PRODUCT, ON_HAND, INBOUND, OUTBOUND, DAYS_OF_SUPPLY, STOCK_STATUS만 사용해줘.
```

`데이터 직접 입력`:

```text
samples/00_data_inputs/sample_inventory_flow.csv
```

### 02에 넣을 값

`요소 양식 JSON`:

```text
samples/02_component_catalogs/catalog_composition_dashboard.json
```

비워둬도 실행됩니다.

## 예시 7. 에너지 사용량과 다운타임 진단

### 00에 넣을 값

`질문`:

```text
장비별 에너지 사용량과 다운타임 관계를 보고 비효율 장비를 찾고 싶어
```

`보고 싶은 방식`:

```text
엔지니어가 원인을 추적하는 진단 화면으로 만들어줘.
상단 KPI는 총 KWH, 총 OUTPUT_UNITS, 평균 TEMPERATURE_C, 총 DOWNTIME_MIN으로 구성해줘.
첫 번째 차트는 DATE별 KWH 추이를 full width 선 그래프로 보여줘.
두 번째 줄은 좌측에 KWH와 DOWNTIME_MIN의 관계를 산점도로 보여주고, 우측에는 EQUIPMENT별 KWH 비교 막대그래프를 보여줘.
하단 표는 STATUS가 warning이거나 DOWNTIME_MIN이 큰 행을 우선 보여주고 DOWNTIME_MIN 내림차순으로 정렬해줘.
전체는 차분한 엔지니어링 리포트처럼 구성해줘.
```

`데이터 직접 입력`:

```text
samples/00_data_inputs/sample_energy_usage.csv
```

### 02에 넣을 값

`요소 양식 JSON`:

```text
samples/02_component_catalogs/catalog_quality_diagnostics.json
```

비워둬도 실행됩니다.

## 예시 8. 고객 퍼널 전환율 리포트

### 00에 넣을 값

`질문`:

```text
고객 퍼널 단계별 전환율과 이탈 규모를 보고 개선 우선순위를 정하고 싶어
```

`보고 싶은 방식`:

```text
상단 KPI는 총 LEADS, 총 CONVERTED, 평균 CONVERSION_RATE, 총 DROP_OFF로 보여줘.
중간에는 STAGE별 LEADS와 CONVERTED를 묶음 막대그래프로 비교하고, SEGMENT별 CONVERTED 비중은 도넛 차트로 보여줘.
CONVERSION_RATE는 STAGE 순서대로 추이처럼 읽히도록 선 그래프나 단계별 비교 그래프로 보여줘.
하단 표는 STATUS가 watch 또는 risk인 행만 보여주고 DROP_OFF 내림차순으로 정렬해줘.
임원도 볼 수 있게 설명은 짧고 핵심 발견과 권장 조치를 포함해줘.
```

`데이터 직접 입력`:

```text
samples/00_data_inputs/sample_customer_funnel.csv
```

### 02에 넣을 값

`요소 양식 JSON`:

```text
samples/02_component_catalogs/catalog_executive_summary.json
```

비워둬도 실행됩니다.

## 예시 9. 간단 멀티 데이터셋 결합 확인

### 00에 넣을 값

`질문`:

```text
WIP 데모 데이터와 생산 데모 데이터를 날짜, 공정, 제품 기준으로 함께 보고 싶어
```

`보고 싶은 방식`:

```text
두 데이터셋을 DATE, OPER_SHORT_DESC, PRODUCT 기준으로 결합해서 보여줘.
상단에는 총 WIP와 총 PRODUCTION KPI를 보여줘.
중간에는 DATE별 PRODUCTION 추이 선 그래프와 OPER_SHORT_DESC별 WIP 비교 막대그래프를 보여줘.
마지막에는 STATUS가 warning인 WIP 행을 상세 표로 보여줘.
멀티 데이터셋이 어떻게 결합됐는지 method note 또는 caveat에 짧게 설명해줘.
```

`데이터 직접 입력`:

```text
samples/00_data_inputs/sample_multi_dataset.json
```

`03a.추가 구현 지시사항`에 넣으면 좋은 값:

```text
wip_demo 데이터의 WIP는 공정별 재공 수량이고 STATUS가 warning이면 주의 대상이야.
production_demo 데이터의 PRODUCTION은 생산량이야.
DATE, OPER_SHORT_DESC, PRODUCT가 두 데이터의 공통 key야.
```

### 02에 넣을 값

`요소 양식 JSON`:

```text
samples/02_component_catalogs/catalog_operations_compact.json
```

비워둬도 실행됩니다.

## 전체 샘플 파일 목록

### 00 데이터 직접 입력용

```text
samples/00_data_inputs/sample_wip.csv
samples/00_data_inputs/sample_multi_dataset.json
samples/00_data_inputs/sample_multi_wip_output_quality.json
samples/00_data_inputs/sample_sales_channel_mix.csv
samples/00_data_inputs/sample_quality_diagnostics.csv
samples/00_data_inputs/sample_inventory_flow.csv
samples/00_data_inputs/sample_energy_usage.csv
samples/00_data_inputs/sample_customer_funnel.csv
samples/00_data_inputs/sample_instruction_stress.csv
```

### 02 요소 양식 JSON용

```text
samples/02_component_catalogs/catalog_operations_compact.json
samples/02_component_catalogs/catalog_executive_summary.json
samples/02_component_catalogs/catalog_quality_diagnostics.json
samples/02_component_catalogs/catalog_composition_dashboard.json
```
