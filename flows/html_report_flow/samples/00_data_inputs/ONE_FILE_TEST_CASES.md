# HTML Report Flow 한 파일 테스트 입력 모음

이 문서는 Langflow에서 테스트할 때 여러 파일을 열지 않아도 되도록 만든 복사용 입력 모음입니다.

각 테스트 케이스는 아래 순서대로 사용합니다.

1. `00 리포트 요청/데이터 불러오기`의 `질문`에 `00.질문` 값을 넣습니다.
2. 같은 노드의 `보고 싶은 방식`에 `00.보고 싶은 방식` 값을 넣습니다.
3. 같은 노드의 `데이터 직접 입력`에 `00.데이터 직접 입력` 코드블록 전체를 넣습니다.
4. LLM 프롬프트 노드를 쓰는 경우 `03a.추가 구현 지시사항` 값을 추가 지시 입력에 넣습니다.
5. `02 기본 요소 양식/추천`의 `요소 양식 JSON`에는 문서 하단의 카탈로그 JSON 중 하나를 복사해서 넣거나 비워둡니다.

처음 테스트할 때는 `02.요소 양식 JSON`을 비워두고, 특정 리포트 톤을 강하게 보고 싶을 때만 하단 카탈로그를 넣는 것을 권장합니다.

## 어디에 무엇을 넣어야 하나요?

### 00.질문

사용자가 알고 싶은 핵심 질문을 짧게 넣습니다.

예시:

```text
공정별 WIP와 생산량을 비교하고 날짜별 추이를 보여줘
```

### 00.보고 싶은 방식

화면 구성, 차트 종류, 배치, 표 컬럼, 정렬, 필터처럼 "어떤 모양으로 보고 싶은지"를 넣습니다.

예시:

```text
상단에는 KPI 카드 5개를 배치해줘.
좌측 2/3에는 DATE별 OUTPUT_QTY 추이 선 그래프를 보여주고,
우측 1/3에는 ALERT_LEVEL별 WIP_QTY 비중 도넛 차트를 보여줘.
마지막 표는 ALERT_LEVEL이 HIGH 또는 WARN인 행만 보여줘.
```

### 00.데이터 직접 입력

CSV 원문 또는 JSON 원문을 그대로 넣습니다.

단일 데이터는 CSV를 그대로 넣어도 되고, 여러 데이터는 아래처럼 `datasets` 배열 JSON으로 넣습니다.

```json
{
  "datasets": [
    {
      "dataset_id": "wip_status",
      "label": "WIP status by process",
      "rows": []
    }
  ]
}
```

### 03a.추가 구현 지시사항

여기에 "어떤 데이터의 어떤 컬럼/값이 무엇을 의미하는지"를 넣습니다.

이 값은 Langflow 기본 Prompt Template의 `{디자인_지시}` 변수로 들어가며, LLM이 리포트 계획을 만들 때 보조 근거로 사용합니다. 이름은 디자인_지시지만 실제로는 디자인뿐 아니라 데이터 의미, 컬럼 설명, 값 설명, 임계값, join key, 필터 기준을 넣는 곳입니다.

예시:

```text
wip_status 데이터의 ALERT_LEVEL 값은 NORMAL, WARN, HIGH이고 HIGH가 가장 위험한 상태야.
wip_status 데이터의 WIP_QTY는 공정에 쌓여있는 재공 수량이야.
production_result 데이터의 OUTPUT_QTY는 생산량이고 YIELD_RATE는 수율이야.
YIELD_RATE가 95 이하이면 주의 구간으로 봐줘.
DATE, LINE, PROCESS가 세 데이터셋을 연결하는 key야.
```

### 02.요소 양식 JSON

기본 컴포넌트/스타일/리포트 톤을 참고시키는 선택 입력입니다.

컬럼 의미나 값 의미를 넣는 곳이 아닙니다. 운영형, 품질진단형, 임원요약형처럼 리포트 성격을 강하게 잡고 싶을 때만 넣습니다.

### LLM 없이 빠른 확인을 할 때

LLM 없이 `03 기본 리포트 계획`으로 바로 가는 흐름에서는 `03a.추가 구현 지시사항`이 사용되지 않습니다.

이 경우에는 중요한 데이터 의미를 `00.보고 싶은 방식` 끝에 같이 적어주세요. 다만 사용자 요구사항과 데이터 의미를 세밀하게 반영하려면 LLM 연결 흐름을 사용하는 것이 더 적합합니다.

---

## 테스트 1. 단일 CSV - 공정 WIP 운영 대시보드

### 00.질문

```text
공정별 WIP와 생산량을 비교하고 날짜별 추이를 보여줘
```

### 00.보고 싶은 방식

```text
상단에는 KPI 카드로 총 WIP, 총 생산량, 평균 생산량을 보여줘.
중간에는 날짜별 생산량 추이 선 그래프를 크게 보여주고, 각 포인트에 값을 표시해줘.
공정별 WIP와 생산량은 묶음 막대그래프로 비교해줘.
마지막에는 STATUS가 warning인 행을 상세 표로 보여줘.
전체는 운영자가 빠르게 볼 수 있는 compact 대시보드로 구성해줘.
```

### 00.데이터 직접 입력

```csv
DATE,OPER_SHORT_DESC,PRODUCT,WIP,PRODUCTION,STATUS
2026-06-14,DA,Alpha,120,85,normal
2026-06-14,WB,Beta,80,110,normal
2026-06-14,TEST,Gamma,135,72,warning
2026-06-15,DA,Alpha,150,92,normal
2026-06-15,WB,Beta,95,130,normal
2026-06-15,TEST,Gamma,110,86,normal
2026-06-16,DA,Alpha,140,105,normal
2026-06-16,WB,Beta,70,142,normal
2026-06-16,TEST,Gamma,160,65,warning
2026-06-17,DA,Alpha,130,118,normal
2026-06-17,WB,Beta,90,150,normal
2026-06-17,TEST,Gamma,125,94,normal
```

### 03a.추가 구현 지시사항

```text
OPER_SHORT_DESC는 공정명이고 WIP는 재공 수량이야.
PRODUCTION은 생산량이고 STATUS가 warning이면 현장 확인이 필요한 상태야.
warning 행은 상세 표에서 빠지면 안 돼.
```

### 02.요소 양식 JSON

문서 하단의 `catalog_operations_compact`를 복사하거나 비워둡니다.

---

## 테스트 2. 멀티 JSON - WIP, 생산, 품질 결합 리포트

### 00.질문

```text
WIP, 생산, 품질 데이터를 DATE, LINE, PROCESS 기준으로 함께 보고 병목과 위험 공정을 확인하고 싶어
```

### 00.보고 싶은 방식

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

### 00.데이터 직접 입력

```json
{
  "datasets": [
    {
      "dataset_id": "wip_status",
      "label": "WIP status by process",
      "rows": [
        {"DATE": "2026-06-01", "LINE": "L1", "PROCESS": "DA", "STATUS": "RUN", "ALERT_LEVEL": "NORMAL", "WIP_QTY": 118},
        {"DATE": "2026-06-02", "LINE": "L1", "PROCESS": "DA", "STATUS": "RUN", "ALERT_LEVEL": "NORMAL", "WIP_QTY": 126},
        {"DATE": "2026-06-03", "LINE": "L1", "PROCESS": "DA", "STATUS": "DELAY", "ALERT_LEVEL": "WARN", "WIP_QTY": 171},
        {"DATE": "2026-06-04", "LINE": "L1", "PROCESS": "TEST", "STATUS": "DELAY", "ALERT_LEVEL": "HIGH", "WIP_QTY": 215},
        {"DATE": "2026-06-05", "LINE": "L2", "PROCESS": "TEST", "STATUS": "RUN", "ALERT_LEVEL": "WARN", "WIP_QTY": 184},
        {"DATE": "2026-06-06", "LINE": "L2", "PROCESS": "PACK", "STATUS": "RUN", "ALERT_LEVEL": "NORMAL", "WIP_QTY": 93},
        {"DATE": "2026-06-07", "LINE": "L2", "PROCESS": "PACK", "STATUS": "HOLD", "ALERT_LEVEL": "HIGH", "WIP_QTY": 204},
        {"DATE": "2026-06-08", "LINE": "L3", "PROCESS": "WB", "STATUS": "RUN", "ALERT_LEVEL": "NORMAL", "WIP_QTY": 142}
      ]
    },
    {
      "dataset_id": "production_result",
      "label": "Daily output and yield",
      "rows": [
        {"DATE": "2026-06-01", "LINE": "L1", "PROCESS": "DA", "OUTPUT_QTY": 1040, "YIELD_RATE": 97.4, "CYCLE_TIME_HR": 8.2},
        {"DATE": "2026-06-02", "LINE": "L1", "PROCESS": "DA", "OUTPUT_QTY": 1095, "YIELD_RATE": 97.1, "CYCLE_TIME_HR": 8.5},
        {"DATE": "2026-06-03", "LINE": "L1", "PROCESS": "DA", "OUTPUT_QTY": 980, "YIELD_RATE": 95.8, "CYCLE_TIME_HR": 9.1},
        {"DATE": "2026-06-04", "LINE": "L1", "PROCESS": "TEST", "OUTPUT_QTY": 820, "YIELD_RATE": 94.2, "CYCLE_TIME_HR": 10.7},
        {"DATE": "2026-06-05", "LINE": "L2", "PROCESS": "TEST", "OUTPUT_QTY": 910, "YIELD_RATE": 95.3, "CYCLE_TIME_HR": 9.8},
        {"DATE": "2026-06-06", "LINE": "L2", "PROCESS": "PACK", "OUTPUT_QTY": 1180, "YIELD_RATE": 98.1, "CYCLE_TIME_HR": 7.6},
        {"DATE": "2026-06-07", "LINE": "L2", "PROCESS": "PACK", "OUTPUT_QTY": 870, "YIELD_RATE": 94.9, "CYCLE_TIME_HR": 10.2},
        {"DATE": "2026-06-08", "LINE": "L3", "PROCESS": "WB", "OUTPUT_QTY": 1015, "YIELD_RATE": 96.8, "CYCLE_TIME_HR": 8.9}
      ]
    },
    {
      "dataset_id": "quality_backlog",
      "label": "Quality and backlog events",
      "rows": [
        {"DATE": "2026-06-01", "LINE": "L1", "PROCESS": "DA", "DEFECT_QTY": 18, "BACKLOG_QTY": 42, "REWORK_QTY": 9},
        {"DATE": "2026-06-02", "LINE": "L1", "PROCESS": "DA", "DEFECT_QTY": 22, "BACKLOG_QTY": 55, "REWORK_QTY": 11},
        {"DATE": "2026-06-03", "LINE": "L1", "PROCESS": "DA", "DEFECT_QTY": 38, "BACKLOG_QTY": 97, "REWORK_QTY": 21},
        {"DATE": "2026-06-04", "LINE": "L1", "PROCESS": "TEST", "DEFECT_QTY": 64, "BACKLOG_QTY": 156, "REWORK_QTY": 36},
        {"DATE": "2026-06-05", "LINE": "L2", "PROCESS": "TEST", "DEFECT_QTY": 49, "BACKLOG_QTY": 118, "REWORK_QTY": 28},
        {"DATE": "2026-06-06", "LINE": "L2", "PROCESS": "PACK", "DEFECT_QTY": 15, "BACKLOG_QTY": 34, "REWORK_QTY": 8},
        {"DATE": "2026-06-07", "LINE": "L2", "PROCESS": "PACK", "DEFECT_QTY": 57, "BACKLOG_QTY": 141, "REWORK_QTY": 33},
        {"DATE": "2026-06-08", "LINE": "L3", "PROCESS": "WB", "DEFECT_QTY": 29, "BACKLOG_QTY": 76, "REWORK_QTY": 14}
      ]
    }
  ]
}
```

### 03a.추가 구현 지시사항

```text
wip_status 데이터의 ALERT_LEVEL 값은 NORMAL, WARN, HIGH이고 HIGH가 가장 위험한 상태야.
wip_status 데이터의 WIP_QTY는 공정에 쌓여있는 재공 수량이야.
production_result 데이터의 OUTPUT_QTY는 생산량이고 YIELD_RATE는 수율이야. YIELD_RATE가 95 이하이면 주의 구간으로 봐줘.
quality_backlog 데이터의 DEFECT_QTY는 불량 수량이고 BACKLOG_QTY는 미처리 물량이야.
DATE, LINE, PROCESS가 세 데이터셋을 연결하는 key야.
위험 상세 표는 ALERT_LEVEL이 HIGH 또는 WARN이거나 YIELD_RATE가 95 이하인 행만 보여줘.
```

### 02.요소 양식 JSON

문서 하단의 `catalog_operations_compact`를 복사하거나 비워둡니다.

---

## 테스트 3. 단일 CSV - 품질 진단 리포트

### 00.질문

```text
불량 수 분포와 수율 관계, warning row를 진단해줘
```

### 00.보고 싶은 방식

```text
품질 엔지니어가 원인을 추적하는 진단 리포트로 만들어줘.
상단에는 평균 수율, 총 불량 수, warning/danger 건수를 KPI로 보여줘.
첫 번째 분석 영역에는 DEFECT_COUNT 분포 히스토그램을 full width로 보여줘.
두 번째 분석 영역에는 CYCLE_TIME_SEC와 YIELD_RATE의 관계를 산점도로 보여주고 각 점이 잘 보이게 처리해줘.
하단에는 STATUS가 warning 또는 danger인 행만 표로 보여주고 DEFECT_COUNT 내림차순으로 정렬해줘.
위험 상태는 빨간색 또는 주황색으로 강조해줘.
```

### 00.데이터 직접 입력

```csv
DATE,LINE,PROCESS,DEFECT_TYPE,DEFECT_COUNT,INSPECTION_COUNT,YIELD_RATE,CYCLE_TIME_SEC,TEMPERATURE_C,STATUS
2026-06-01,L1,DA,Scratch,12,1200,0.987,42,23.1,normal
2026-06-01,L1,WB,Bond,18,1180,0.982,51,24.0,watch
2026-06-01,L2,TEST,Electrical,25,1110,0.974,66,25.3,warning
2026-06-02,L1,DA,Particle,9,1210,0.990,43,23.4,normal
2026-06-02,L2,WB,Bond,21,1160,0.979,54,24.8,watch
2026-06-02,L2,TEST,Electrical,31,1095,0.969,69,26.1,warning
2026-06-03,L1,DA,Scratch,14,1235,0.986,44,23.6,normal
2026-06-03,L1,WB,Particle,16,1198,0.984,52,24.1,normal
2026-06-03,L2,TEST,Crack,34,1070,0.966,73,26.5,danger
2026-06-04,L1,DA,Scratch,11,1220,0.988,43,23.0,normal
2026-06-04,L2,WB,Bond,19,1174,0.981,55,24.9,watch
2026-06-04,L2,TEST,Electrical,28,1102,0.972,68,25.9,warning
2026-06-05,L1,DA,Particle,8,1245,0.992,41,22.8,normal
2026-06-05,L1,WB,Bond,17,1201,0.984,53,24.2,normal
2026-06-05,L2,TEST,Crack,38,1065,0.963,75,26.8,danger
2026-06-06,L1,DA,Scratch,13,1218,0.987,44,23.2,normal
2026-06-06,L2,WB,Particle,22,1156,0.978,57,25.1,watch
2026-06-06,L2,TEST,Electrical,30,1088,0.970,70,26.3,warning
```

### 03a.추가 구현 지시사항

```text
DEFECT_COUNT는 불량 수량이고 INSPECTION_COUNT는 검사 수량이야.
YIELD_RATE는 0-1 사이의 수율 값이므로 화면에는 퍼센트로 읽히게 보여줘.
STATUS 값 중 warning과 danger는 원인 확인이 필요한 이상 상태야.
CYCLE_TIME_SEC가 길고 YIELD_RATE가 낮은 조합을 위험 신호로 봐줘.
```

### 02.요소 양식 JSON

문서 하단의 `catalog_quality_diagnostics`를 복사하거나 비워둡니다.

---

## 테스트 4. 단일 CSV - 재고 흐름 및 창고 상태

### 00.질문

```text
창고별 재고 상태 구성과 입출고 흐름을 비교해서 재고 위험을 확인하고 싶어
```

### 00.보고 싶은 방식

```text
상단에는 총 ON_HAND, 총 INBOUND, 총 OUTBOUND, 평균 DAYS_OF_SUPPLY를 KPI 카드로 보여줘.
중간에는 CATEGORY별 ON_HAND 비중을 도넛 차트로 보여주고, WAREHOUSE별 INBOUND와 OUTBOUND를 묶음 막대그래프로 비교해줘.
그 아래에는 WAREHOUSE별 STOCK_STATUS 구성을 누적 막대그래프로 보여줘.
마지막 표는 STOCK_STATUS가 watch, warning, danger인 행만 보여주고 DAYS_OF_SUPPLY 오름차순으로 정렬해줘.
표 컬럼은 DATE, WAREHOUSE, CATEGORY, PRODUCT, ON_HAND, INBOUND, OUTBOUND, DAYS_OF_SUPPLY, STOCK_STATUS만 사용해줘.
```

### 00.데이터 직접 입력

```csv
DATE,WAREHOUSE,CATEGORY,PRODUCT,ON_HAND,INBOUND,OUTBOUND,DAYS_OF_SUPPLY,STOCK_STATUS
2026-06-01,WH-A,Raw,Wafer,4200,900,760,18,normal
2026-06-01,WH-A,Substrate,ABF,1800,300,420,9,watch
2026-06-01,WH-B,Chemical,Photoresist,620,90,110,7,warning
2026-06-02,WH-A,Raw,Wafer,4380,840,690,20,normal
2026-06-02,WH-B,Substrate,ABF,1660,260,390,8,watch
2026-06-02,WH-B,Chemical,Developer,740,100,98,11,normal
2026-06-03,WH-A,Raw,Wafer,4100,720,930,16,normal
2026-06-03,WH-A,Substrate,Leadframe,2050,360,310,14,normal
2026-06-03,WH-B,Chemical,Photoresist,540,60,130,5,warning
2026-06-04,WH-A,Raw,Wafer,3920,650,850,15,normal
2026-06-04,WH-B,Substrate,ABF,1510,240,420,7,warning
2026-06-04,WH-B,Chemical,Developer,700,80,120,9,watch
2026-06-05,WH-A,Raw,Wafer,4560,1100,690,22,normal
2026-06-05,WH-A,Substrate,Leadframe,2190,430,350,15,normal
2026-06-05,WH-B,Chemical,Photoresist,500,45,120,4,danger
2026-06-06,WH-A,Raw,Wafer,4710,980,810,21,normal
2026-06-06,WH-B,Substrate,ABF,1600,390,300,10,watch
2026-06-06,WH-B,Chemical,Developer,760,130,95,12,normal
```

### 03a.추가 구현 지시사항

```text
ON_HAND는 현재 재고, INBOUND는 입고량, OUTBOUND는 출고량이야.
DAYS_OF_SUPPLY는 며칠치 재고가 남았는지를 의미하고 낮을수록 위험해.
STOCK_STATUS는 normal, watch, warning, danger 순서로 위험도가 높아져.
```

### 02.요소 양식 JSON

문서 하단의 `catalog_composition_dashboard`를 복사하거나 비워둡니다.

---

## 테스트 5. 단일 CSV - 에너지 사용량과 다운타임

### 00.질문

```text
장비별 에너지 사용량과 다운타임 관계를 보고 비효율 장비를 찾고 싶어
```

### 00.보고 싶은 방식

```text
엔지니어가 원인을 추적하는 진단 화면으로 만들어줘.
상단 KPI는 총 KWH, 총 OUTPUT_UNITS, 평균 TEMPERATURE_C, 총 DOWNTIME_MIN으로 구성해줘.
첫 번째 차트는 DATE별 KWH 추이를 full width 선 그래프로 보여주고 각 포인트에 값을 표시해줘.
두 번째 줄은 좌측에 KWH와 DOWNTIME_MIN의 관계를 산점도로 보여주고, 우측에는 EQUIPMENT별 KWH 비교 막대그래프를 보여줘.
하단 표는 STATUS가 warning이거나 DOWNTIME_MIN이 큰 행을 우선 보여주고 DOWNTIME_MIN 내림차순으로 정렬해줘.
전체는 차분한 엔지니어링 리포트처럼 구성해줘.
```

### 00.데이터 직접 입력

```csv
DATE,AREA,EQUIPMENT,SHIFT,KWH,OUTPUT_UNITS,TEMPERATURE_C,DOWNTIME_MIN,STATUS
2026-06-01,FAB-1,Compressor-A,Day,1280,940,23.2,12,normal
2026-06-01,FAB-1,Compressor-B,Night,1190,880,22.8,18,normal
2026-06-01,FAB-2,Chiller-A,Day,1560,1020,24.1,25,watch
2026-06-02,FAB-1,Compressor-A,Day,1325,960,23.5,10,normal
2026-06-02,FAB-1,Compressor-B,Night,1215,890,23.0,16,normal
2026-06-02,FAB-2,Chiller-A,Day,1620,1015,24.8,34,watch
2026-06-03,FAB-1,Compressor-A,Day,1410,970,24.2,15,watch
2026-06-03,FAB-1,Compressor-B,Night,1260,900,23.6,22,normal
2026-06-03,FAB-2,Chiller-A,Day,1750,1005,25.5,48,warning
2026-06-04,FAB-1,Compressor-A,Day,1350,965,23.7,11,normal
2026-06-04,FAB-1,Compressor-B,Night,1240,905,23.3,20,normal
2026-06-04,FAB-2,Chiller-A,Day,1695,1012,25.1,39,watch
2026-06-05,FAB-1,Compressor-A,Day,1460,982,24.5,18,watch
2026-06-05,FAB-1,Compressor-B,Night,1290,910,23.9,24,normal
2026-06-05,FAB-2,Chiller-A,Day,1825,998,26.0,55,warning
2026-06-06,FAB-1,Compressor-A,Day,1385,975,24.0,14,normal
2026-06-06,FAB-1,Compressor-B,Night,1275,908,23.5,21,normal
2026-06-06,FAB-2,Chiller-A,Day,1770,1008,25.6,42,watch
```

### 03a.추가 구현 지시사항

```text
KWH는 에너지 사용량이고 OUTPUT_UNITS는 산출량이야.
DOWNTIME_MIN은 설비 비가동 시간이고 클수록 비효율 또는 이상 가능성이 높아.
KWH가 높고 DOWNTIME_MIN도 높은 장비를 비효율 우선 확인 대상으로 봐줘.
```

### 02.요소 양식 JSON

문서 하단의 `catalog_quality_diagnostics`를 복사하거나 비워둡니다.

---

## 테스트 6. 단일 CSV - 고객 퍼널 전환율

### 00.질문

```text
고객 퍼널 단계별 전환율과 이탈 규모를 보고 개선 우선순위를 정하고 싶어
```

### 00.보고 싶은 방식

```text
상단 KPI는 총 LEADS, 총 CONVERTED, 평균 CONVERSION_RATE, 총 DROP_OFF로 보여줘.
중간에는 STAGE별 LEADS와 CONVERTED를 묶음 막대그래프로 비교하고, SEGMENT별 CONVERTED 비중은 도넛 차트로 보여줘.
CONVERSION_RATE는 STAGE 순서대로 추이처럼 읽히도록 선 그래프나 단계별 비교 그래프로 보여줘.
하단 표는 STATUS가 watch 또는 warning인 행만 보여주고 DROP_OFF 내림차순으로 정렬해줘.
임원도 볼 수 있게 설명은 짧고 핵심 발견과 권장 조치를 포함해줘.
```

### 00.데이터 직접 입력

```csv
DATE,SEGMENT,STAGE,LEADS,CONVERTED,CONVERSION_RATE,AVG_DEAL_SIZE,DROP_OFF,STATUS
2026-06-01,Enterprise,Awareness,5000,980,0.196,0,4020,normal
2026-06-01,Enterprise,Evaluation,980,260,0.265,180000,720,watch
2026-06-01,Enterprise,Purchase,260,92,0.354,220000,168,normal
2026-06-01,SMB,Awareness,8200,1540,0.188,0,6660,normal
2026-06-01,SMB,Evaluation,1540,430,0.279,62000,1110,watch
2026-06-01,SMB,Purchase,430,148,0.344,78000,282,normal
2026-06-02,Enterprise,Awareness,5300,1045,0.197,0,4255,normal
2026-06-02,Enterprise,Evaluation,1045,281,0.269,185000,764,watch
2026-06-02,Enterprise,Purchase,281,101,0.359,224000,180,normal
2026-06-02,SMB,Awareness,7900,1490,0.189,0,6410,normal
2026-06-02,SMB,Evaluation,1490,398,0.267,61000,1092,warning
2026-06-02,SMB,Purchase,398,132,0.332,75000,266,normal
2026-06-03,Enterprise,Awareness,5600,1098,0.196,0,4502,normal
2026-06-03,Enterprise,Evaluation,1098,305,0.278,188000,793,normal
2026-06-03,Enterprise,Purchase,305,118,0.387,231000,187,normal
2026-06-03,SMB,Awareness,8400,1620,0.193,0,6780,normal
2026-06-03,SMB,Evaluation,1620,418,0.258,59000,1202,warning
2026-06-03,SMB,Purchase,418,139,0.333,77000,279,normal
```

### 03a.추가 구현 지시사항

```text
LEADS는 유입 수, CONVERTED는 다음 단계 전환 수, CONVERSION_RATE는 전환율이야.
DROP_OFF는 이탈 수이고 클수록 개선 우선순위가 높아.
STAGE는 Awareness, Evaluation, Purchase 순서로 읽어줘.
```

### 02.요소 양식 JSON

문서 하단의 `catalog_executive_summary`를 복사하거나 비워둡니다.

---

## 테스트 7. 단일 CSV - 상세 지시 반영 스트레스 테스트

### 00.질문

```text
라인/공정별 WIP, 생산량, 불량, 수율, 지연 상태를 종합해서 병목과 위험 공정을 확인하고 싶어
```

### 00.보고 싶은 방식

```text
현장 운영자가 아침 회의에서 바로 볼 수 있는 한 화면짜리 compact 대시보드로 만들어줘.
상단 첫 줄에는 KPI 카드 5개를 한 줄로 배치해줘. KPI는 총 WIP_QTY, 총 OUTPUT_QTY, 총 DEFECT_QTY, 평균 YIELD_RATE, ALERT_LEVEL이 HIGH인 건수로 구성해줘.
KPI 카드 중 HIGH 건수와 DEFECT_QTY는 빨간색/주황색 계열로 강조하고, 나머지는 primary 블루/보라 계열로 차분하게 보여줘.
두 번째 줄은 좌측 2/3 너비에 DATE별 OUTPUT_QTY 추이 선 그래프를 크게 배치하고 각 포인트 값을 표시해줘.
우측 1/3 너비에는 ALERT_LEVEL별 WIP_QTY 비중 도넛 차트를 배치해줘.
세 번째 줄은 PROCESS별 WIP_QTY와 BACKLOG_QTY를 묶음 막대그래프로 보여주고, 같은 줄 오른쪽에는 PROCESS별 평균 YIELD_RATE를 비교 막대그래프로 보여줘.
마지막에는 ALERT_LEVEL이 HIGH 또는 WARN인 행만 표로 보여줘.
표 컬럼은 DATE, LINE, PROCESS, STATUS, WIP_QTY, BACKLOG_QTY, DEFECT_QTY, YIELD_RATE만 사용하고, BACKLOG_QTY 내림차순으로 정렬해줘.
설명 문장은 짧게 하고, 각 차트에는 가장 위험한 공정 1개만 annotation으로 표시해줘.
```

### 00.데이터 직접 입력

```csv
DATE,LINE,PROCESS,PRODUCT_FAMILY,SHIFT,STATUS,WIP_QTY,OUTPUT_QTY,DEFECT_QTY,YIELD_RATE,CYCLE_TIME_HR,BACKLOG_QTY,ENERGY_KWH,ALERT_LEVEL
2026-06-01,LINE_A,DA,MOBILE,DAY,NORMAL,1240,980,18,98.2,5.4,260,1450,NORMAL
2026-06-01,LINE_A,WB,MOBILE,DAY,DELAYED,920,820,21,97.4,6.2,310,1320,WARN
2026-06-01,LINE_B,TEST,AUTO,NIGHT,NORMAL,710,690,9,98.7,4.8,120,1180,NORMAL
2026-06-01,LINE_B,PACK,AUTO,NIGHT,HOLD,540,430,12,97.2,7.1,210,970,WARN
2026-06-02,LINE_A,DA,MOBILE,DAY,NORMAL,1180,1010,14,98.6,5.1,190,1425,NORMAL
2026-06-02,LINE_A,WB,MOBILE,DAY,DELAYED,1015,850,30,96.5,6.8,365,1360,WARN
2026-06-02,LINE_B,TEST,AUTO,NIGHT,NORMAL,760,735,11,98.5,4.7,105,1210,NORMAL
2026-06-02,LINE_B,PACK,AUTO,NIGHT,HOLD,590,470,16,96.6,7.4,245,995,WARN
2026-06-03,LINE_A,DA,MOBILE,DAY,NORMAL,1090,1040,12,98.8,4.9,160,1390,NORMAL
2026-06-03,LINE_A,WB,MOBILE,DAY,CRITICAL,1160,790,44,94.7,8.2,510,1485,HIGH
2026-06-03,LINE_B,TEST,AUTO,NIGHT,NORMAL,735,760,10,98.7,4.6,90,1195,NORMAL
2026-06-03,LINE_B,PACK,AUTO,NIGHT,HOLD,640,455,19,95.8,7.9,300,1015,WARN
2026-06-04,LINE_A,DA,MOBILE,NIGHT,NORMAL,1125,1025,13,98.7,5.0,175,1410,NORMAL
2026-06-04,LINE_A,WB,MOBILE,NIGHT,CRITICAL,1245,760,52,93.6,8.9,620,1530,HIGH
2026-06-04,LINE_B,TEST,AUTO,DAY,NORMAL,790,780,8,99.0,4.4,80,1235,NORMAL
2026-06-04,LINE_B,PACK,AUTO,DAY,HOLD,700,480,22,95.4,8.1,335,1040,WARN
2026-06-05,LINE_A,DA,MOBILE,NIGHT,NORMAL,1070,1085,11,99.0,4.7,130,1375,NORMAL
2026-06-05,LINE_A,WB,MOBILE,NIGHT,DELAYED,1110,875,28,96.8,6.9,390,1440,WARN
2026-06-05,LINE_B,TEST,AUTO,DAY,NORMAL,820,805,9,98.9,4.3,75,1250,NORMAL
2026-06-05,LINE_B,PACK,AUTO,DAY,HOLD,675,515,17,96.7,7.3,280,1030,WARN
2026-06-06,LINE_A,DA,SERVER,DAY,NORMAL,980,970,10,99.0,4.8,145,1340,NORMAL
2026-06-06,LINE_A,WB,SERVER,DAY,DELAYED,1045,910,25,97.3,6.3,320,1415,WARN
2026-06-06,LINE_B,TEST,SERVER,NIGHT,NORMAL,880,850,13,98.5,4.9,130,1290,NORMAL
2026-06-06,LINE_B,PACK,SERVER,NIGHT,NORMAL,610,590,8,98.6,5.6,95,990,NORMAL
2026-06-07,LINE_A,DA,SERVER,DAY,NORMAL,940,1015,8,99.2,4.5,105,1325,NORMAL
2026-06-07,LINE_A,WB,SERVER,DAY,DELAYED,990,930,20,97.8,6.0,260,1380,WARN
2026-06-07,LINE_B,TEST,SERVER,NIGHT,NORMAL,910,880,12,98.6,4.8,115,1305,NORMAL
2026-06-07,LINE_B,PACK,SERVER,NIGHT,NORMAL,580,610,7,98.9,5.3,70,965,NORMAL
2026-06-08,LINE_A,DA,SERVER,NIGHT,NORMAL,910,1035,7,99.3,4.4,85,1310,NORMAL
2026-06-08,LINE_A,WB,SERVER,NIGHT,NORMAL,950,970,16,98.4,5.5,180,1350,NORMAL
2026-06-08,LINE_B,TEST,SERVER,DAY,NORMAL,940,910,11,98.8,4.6,100,1320,NORMAL
2026-06-08,LINE_B,PACK,SERVER,DAY,NORMAL,560,640,6,99.1,5.1,55,950,NORMAL
```

### 03a.추가 구현 지시사항

```text
WIP_QTY는 공정 재공 수량, OUTPUT_QTY는 생산량, DEFECT_QTY는 불량 수량이야.
YIELD_RATE는 이미 퍼센트 단위 값이고 95 이하이면 수율 저하 위험이야.
ALERT_LEVEL은 NORMAL, WARN, HIGH 순서로 위험도가 높아.
BACKLOG_QTY는 미처리 물량이고 큰 값일수록 병목 가능성이 높아.
```

### 02.요소 양식 JSON

문서 하단의 `catalog_operations_compact`를 복사하거나 비워둡니다.

---

## 테스트 8. 멀티 JSON - 간단 결합 확인

### 00.질문

```text
WIP 데모 데이터와 생산 데모 데이터를 날짜, 공정, 제품 기준으로 함께 보고 싶어
```

### 00.보고 싶은 방식

```text
두 데이터셋을 DATE, OPER_SHORT_DESC, PRODUCT 기준으로 결합해서 보여줘.
상단에는 총 WIP와 총 PRODUCTION KPI를 보여줘.
중간에는 DATE별 PRODUCTION 추이 선 그래프와 OPER_SHORT_DESC별 WIP 비교 막대그래프를 보여줘.
마지막에는 STATUS가 warning인 WIP 행을 상세 표로 보여줘.
멀티 데이터셋이 어떻게 결합됐는지 method note 또는 caveat에 짧게 설명해줘.
```

### 00.데이터 직접 입력

```json
{
  "datasets": [
    {
      "dataset_id": "wip_demo",
      "label": "WIP Demo Data",
      "rows": [
        {"DATE": "2026-06-14", "OPER_SHORT_DESC": "DA", "PRODUCT": "Alpha", "WIP": 120, "STATUS": "normal"},
        {"DATE": "2026-06-15", "OPER_SHORT_DESC": "DA", "PRODUCT": "Alpha", "WIP": 150, "STATUS": "normal"},
        {"DATE": "2026-06-16", "OPER_SHORT_DESC": "TEST", "PRODUCT": "Gamma", "WIP": 160, "STATUS": "warning"}
      ]
    },
    {
      "dataset_id": "production_demo",
      "label": "Production Demo Data",
      "rows": [
        {"DATE": "2026-06-14", "OPER_SHORT_DESC": "DA", "PRODUCT": "Alpha", "PRODUCTION": 85},
        {"DATE": "2026-06-15", "OPER_SHORT_DESC": "DA", "PRODUCT": "Alpha", "PRODUCTION": 92},
        {"DATE": "2026-06-16", "OPER_SHORT_DESC": "WB", "PRODUCT": "Beta", "PRODUCTION": 142}
      ]
    }
  ]
}
```

### 03a.추가 구현 지시사항

```text
wip_demo 데이터의 WIP는 공정별 재공 수량이고 STATUS가 warning이면 주의 대상이야.
production_demo 데이터의 PRODUCTION은 생산량이야.
DATE, OPER_SHORT_DESC, PRODUCT가 두 데이터의 공통 key야.
```

### 02.요소 양식 JSON

문서 하단의 `catalog_operations_compact`를 복사하거나 비워둡니다.

---

## 테스트 9. 단일 CSV - 채널/지역 매출 구성

### 00.질문

```text
채널별 매출 비중을 보고 지역별 매출과 주문 수를 비교해줘
```

### 00.보고 싶은 방식

```text
임원이 빠르게 읽을 수 있는 요약형 리포트로 만들어줘.
상단 KPI는 총 매출, 총 주문 수, 평균 마진율 3개만 크게 보여줘.
중간에는 CHANNEL별 REVENUE 비중을 도넛 차트로 보여주고, REGION별 REVENUE와 ORDERS는 묶음 막대그래프로 비교해줘.
마지막에는 매출 상위 조합을 순위 표로 보여줘.
RETURN_COUNT가 많거나 STATUS가 warning인 조합은 주의 항목으로 설명해줘.
전체 여백은 comfortable하게 구성해줘.
```

### 00.데이터 직접 입력

```csv
DATE,REGION,CHANNEL,PRODUCT,REVENUE,ORDERS,MARGIN_RATE,RETURN_COUNT,STATUS
2026-06-01,Seoul,Online,Alpha,12500000,420,0.31,8,normal
2026-06-01,Seoul,Retail,Beta,8200000,260,0.24,12,watch
2026-06-01,Busan,Partner,Gamma,5400000,180,0.19,6,normal
2026-06-02,Seoul,Online,Alpha,13200000,438,0.32,7,normal
2026-06-02,Busan,Retail,Beta,7600000,245,0.22,14,watch
2026-06-02,Incheon,Partner,Gamma,6100000,194,0.20,5,normal
2026-06-03,Seoul,Online,Delta,14800000,470,0.35,9,normal
2026-06-03,Busan,Retail,Alpha,8800000,276,0.25,16,watch
2026-06-03,Incheon,Partner,Beta,5900000,185,0.18,10,normal
2026-06-04,Seoul,Online,Alpha,15100000,486,0.34,11,normal
2026-06-04,Busan,Retail,Delta,9100000,290,0.26,15,watch
2026-06-04,Incheon,Partner,Gamma,6400000,202,0.21,7,normal
2026-06-05,Seoul,Online,Beta,13900000,452,0.30,13,normal
2026-06-05,Busan,Retail,Alpha,9600000,304,0.27,18,warning
2026-06-05,Incheon,Partner,Delta,7200000,224,0.23,9,normal
2026-06-06,Seoul,Online,Gamma,15800000,502,0.36,10,normal
2026-06-06,Busan,Retail,Beta,8700000,275,0.24,21,warning
2026-06-06,Incheon,Partner,Alpha,6900000,216,0.22,8,normal
```

### 03a.추가 구현 지시사항

```text
REVENUE는 매출, ORDERS는 주문 수, MARGIN_RATE는 마진율이야.
MARGIN_RATE는 0-1 사이 값이므로 퍼센트로 읽히게 보여줘.
RETURN_COUNT는 반품 수량이고 큰 값일수록 운영 주의가 필요해.
CHANNEL별 매출 비중은 도넛 차트로, REGION별 매출/주문 비교는 묶음 막대그래프로 보여줘.
```

### 02.요소 양식 JSON

문서 하단의 `catalog_executive_summary`를 복사하거나 비워둡니다.

---

# 02 요소 양식 JSON 복사용 카탈로그

아래 카탈로그는 필요할 때만 `02 기본 요소 양식/추천`의 `요소 양식 JSON`에 복사합니다.

## catalog_operations_compact

```json
{
  "catalog_notes": [
    "운영 리포트는 compact하고 훑어보기 쉬우며 현재 상태 확인에 집중합니다.",
    "KPI 카드와 가장 중요한 차트는 첫 번째 콘텐츠 row에 배치합니다.",
    "긴 표, 추이 차트, 여러 지표를 담은 묶음 막대 차트는 full width로 둡니다.",
    "나란히 놓인 카드 높이가 맞도록 annotation은 짧게 유지합니다."
  ],
  "style_presets": {
    "operations_compact": {
      "density": "compact",
      "font_scale": "normal",
      "accent_color": "#0f766e",
      "secondary_color": "#2563eb",
      "max_width": "wide"
    }
  },
  "template_defaults": {
    "audience": "operator",
    "report_goal": "monitor",
    "layout": "dashboard",
    "visual_style": {
      "density": "compact",
      "font_scale": "normal",
      "accent_color": "#0f766e",
      "secondary_color": "#2563eb",
      "max_width": "wide"
    },
    "block_defaults": {
      "report_header": {
        "width": "full",
        "emphasis": "high"
      },
      "scope_summary": {
        "width": "full",
        "density": "compact"
      },
      "kpi_card_grid": {
        "width": "half",
        "emphasis": "high",
        "density": "compact",
        "description": "운영자가 먼저 확인해야 하는 핵심 수치를 보여줍니다."
      },
      "trend_line_chart": {
        "width": "full",
        "emphasis": "high",
        "chart_policy": {
          "chart_type": "line",
          "show_values": false
        }
      },
      "grouped_bar_chart": {
        "width": "full",
        "emphasis": "high",
        "chart_policy": {
          "chart_type": "grouped_bar",
          "limit": 8,
          "show_values": true
        }
      },
      "comparison_bar_chart": {
        "width": "half",
        "emphasis": "medium",
        "chart_policy": {
          "chart_type": "horizontal_bar",
          "orientation": "horizontal",
          "limit": 10,
          "show_values": true
        }
      },
      "detail_data_table": {
        "width": "full",
        "density": "compact",
        "table_policy": {
          "limit": 50,
          "show_row_numbers": true
        }
      },
      "method_note": {
        "width": "full",
        "emphasis": "low"
      }
    }
  },
  "components": [
    {
      "component_id": "kpi_card_grid",
      "template_guidance": "운영 상태, 총량, backlog, 생산량을 요약할 때 첫 번째 요약 블록으로 사용합니다."
    },
    {
      "component_id": "grouped_bar_chart",
      "template_guidance": "공정별 WIP와 생산량처럼 같은 범주에서 2-3개 지표를 비교해야 할 때 사용합니다."
    },
    {
      "component_id": "detail_data_table",
      "template_guidance": "마지막 검증 블록으로 사용하며 full width와 compact 밀도를 유지합니다."
    }
  ]
}
```

## catalog_quality_diagnostics

```json
{
  "catalog_notes": [
    "품질 진단 리포트는 예외, 분포, 상관관계, 원인 탐색을 우선합니다.",
    "STATUS가 warning 또는 danger인 row는 warning/danger 톤으로 강조합니다.",
    "히스토그램과 산점도는 둘 다 compact하고 크기가 비슷할 때만 나란히 배치합니다.",
    "불량 또는 예외 검토용 상세 표는 full width로 둡니다."
  ],
  "style_presets": {
    "quality_diagnostics": {
      "density": "compact",
      "font_scale": "normal",
      "accent_color": "#dc2626",
      "secondary_color": "#7c3aed",
      "max_width": "wide"
    }
  },
  "template_defaults": {
    "audience": "engineer",
    "report_goal": "diagnose",
    "layout": "diagnosis",
    "visual_style": {
      "density": "compact",
      "font_scale": "normal",
      "accent_color": "#dc2626",
      "secondary_color": "#7c3aed",
      "max_width": "wide"
    },
    "block_defaults": {
      "warning_box": {
        "width": "full",
        "emphasis": "critical"
      },
      "kpi_card_grid": {
        "width": "full",
        "emphasis": "high",
        "description": "불량 수량, 검사 수량, 수율 관련 지표를 보여줍니다."
      },
      "distribution_histogram": {
        "width": "half",
        "emphasis": "high",
        "chart_policy": {
          "chart_type": "histogram",
          "bin_count": 8,
          "show_values": true
        }
      },
      "scatter_plot": {
        "width": "half",
        "emphasis": "high",
        "chart_policy": {
          "chart_type": "scatter",
          "limit": 120
        }
      },
      "heatmap_matrix": {
        "width": "full",
        "emphasis": "medium",
        "chart_policy": {
          "chart_type": "heatmap",
          "limit": 8,
          "show_values": true
        }
      },
      "outlier_exception_table": {
        "width": "full",
        "density": "compact",
        "emphasis": "high",
        "table_policy": {
          "limit": 50,
          "show_row_numbers": true
        },
        "highlight_rules": [
          {
            "column": "STATUS",
            "operator": "eq",
            "value": "warning",
            "tone": "warning"
          },
          {
            "column": "STATUS",
            "operator": "eq",
            "value": "danger",
            "tone": "danger"
          }
        ]
      },
      "recommendation_list": {
        "width": "full",
        "emphasis": "medium"
      }
    }
  },
  "components": [
    {
      "component_id": "distribution_histogram",
      "template_guidance": "불량 수, cycle time, 온도, 수율의 분포를 확인할 때 사용합니다."
    },
    {
      "component_id": "scatter_plot",
      "template_guidance": "불량 수와 cycle time, 수율과 온도, downtime과 kWh처럼 두 숫자 지표의 관계를 볼 때 사용합니다."
    },
    {
      "component_id": "outlier_exception_table",
      "template_guidance": "warning/danger 상태 row를 검토할 때 사용하며 full width를 유지합니다."
    }
  ]
}
```

## catalog_executive_summary

```json
{
  "catalog_notes": [
    "임원/요약 리포트는 결론을 먼저 보여주고 블록 수를 줄여 크게 배치합니다.",
    "첫 화면에는 여백을 넉넉히 두고 밀도 높은 표를 피합니다.",
    "도넛 차트는 범주 수가 적고 구성비 의도가 명확할 때만 사용합니다.",
    "사용자가 보고서나 의사결정용 요약을 요청하면 추천사항이나 핵심 발견을 상단에 둡니다."
  ],
  "style_presets": {
    "executive_summary": {
      "density": "comfortable",
      "font_scale": "large",
      "accent_color": "#2563eb",
      "secondary_color": "#0f766e",
      "max_width": "normal"
    }
  },
  "template_defaults": {
    "audience": "executive",
    "report_goal": "explain",
    "layout": "executive_summary",
    "visual_style": {
      "density": "comfortable",
      "font_scale": "large",
      "accent_color": "#2563eb",
      "secondary_color": "#0f766e",
      "max_width": "normal"
    },
    "narrative": {
      "data_quality_notes": [
        "데이터가 preview 일부라면 보이는 row 기준의 결론임을 명시합니다."
      ]
    },
    "block_defaults": {
      "report_header": {
        "width": "full",
        "emphasis": "high"
      },
      "kpi_card_grid": {
        "width": "full",
        "emphasis": "high",
        "description": "상세 분석 전에 비즈니스 영향이 큰 핵심 지표를 먼저 보여줍니다."
      },
      "insight_bullets": {
        "width": "half",
        "emphasis": "high",
        "description": "가장 중요한 발견을 짧은 문장으로 요약합니다."
      },
      "donut_chart": {
        "width": "half",
        "emphasis": "medium",
        "chart_policy": {
          "chart_type": "donut",
          "limit": 6,
          "show_percent": true,
          "show_legend": true
        }
      },
      "comparison_bar_chart": {
        "width": "half",
        "emphasis": "medium",
        "chart_policy": {
          "chart_type": "horizontal_bar",
          "limit": 8,
          "show_values": true
        }
      },
      "recommendation_list": {
        "width": "half",
        "emphasis": "medium"
      },
      "detail_data_table": {
        "width": "full",
        "density": "compact",
        "table_policy": {
          "limit": 20,
          "show_row_numbers": false
        }
      }
    }
  },
  "components": [
    {
      "component_id": "insight_bullets",
      "template_guidance": "사용자가 보고서, 요약, 의사결정용 화면을 요청하면 KPI 카드 바로 뒤에 배치합니다."
    },
    {
      "component_id": "donut_chart",
      "template_guidance": "비중과 구성비를 볼 때 사용하되 범주 수를 줄이고 짧은 해석 문장과 함께 배치합니다."
    },
    {
      "component_id": "detail_data_table",
      "template_guidance": "원본 row 확인용 표는 하단에 두고 검증에 필요한 컬럼만 제한해서 보여줍니다."
    }
  ]
}
```

## catalog_composition_dashboard

```json
{
  "catalog_notes": [
    "구성비 대시보드는 전체 비중과 세부 breakdown을 쉽게 비교할 수 있어야 합니다.",
    "전체 구성비는 도넛 차트로, 범주별 내부 구성 비교는 누적 막대로 보여줍니다.",
    "두 dimension을 교차 비교해야 하면 히트맵을 사용합니다.",
    "도넛 차트 범주는 6-8개로 제한하고 나머지 상세는 표에 둡니다."
  ],
  "style_presets": {
    "composition_dashboard": {
      "density": "compact",
      "font_scale": "normal",
      "accent_color": "#0891b2",
      "secondary_color": "#f59e0b",
      "max_width": "wide"
    }
  },
  "template_defaults": {
    "audience": "analyst",
    "report_goal": "compare",
    "layout": "dashboard",
    "visual_style": {
      "density": "compact",
      "font_scale": "normal",
      "accent_color": "#0891b2",
      "secondary_color": "#f59e0b",
      "max_width": "wide"
    },
    "block_defaults": {
      "donut_chart": {
        "width": "half",
        "emphasis": "high",
        "description": "선택한 지표의 전체 구성비를 보여줍니다.",
        "chart_policy": {
          "chart_type": "donut",
          "limit": 8,
          "show_percent": true,
          "show_legend": true
        }
      },
      "stacked_comparison_bar": {
        "width": "full",
        "emphasis": "high",
        "description": "주요 범주별 내부 breakdown을 비교합니다.",
        "chart_policy": {
          "chart_type": "stacked_bar",
          "limit": 8,
          "show_legend": true,
          "normalize": false
        }
      },
      "heatmap_matrix": {
        "width": "full",
        "emphasis": "medium",
        "description": "두 dimension 사이에서 값이 큰 교차 지점을 보여줍니다.",
        "chart_policy": {
          "chart_type": "heatmap",
          "limit": 8,
          "show_values": true
        }
      },
      "ranking_table": {
        "width": "half",
        "density": "compact",
        "emphasis": "medium",
        "table_policy": {
          "limit": 10,
          "show_row_numbers": true
        }
      },
      "detail_data_table": {
        "width": "full",
        "density": "compact",
        "table_policy": {
          "limit": 50,
          "show_row_numbers": true
        }
      }
    }
  },
  "components": [
    {
      "component_id": "donut_chart",
      "template_guidance": "CHANNEL, PRODUCT, CATEGORY, STATUS, STAGE 같은 범주의 전체 비중을 볼 때 사용합니다."
    },
    {
      "component_id": "stacked_comparison_bar",
      "template_guidance": "창고별 재고상태, 세그먼트별 funnel stage처럼 두 dimension의 breakdown을 볼 때 사용합니다."
    },
    {
      "component_id": "heatmap_matrix",
      "template_guidance": "사용자가 교차표, matrix, heatmap, 교차 분석을 요청할 때 사용합니다."
    }
  ]
}
```
