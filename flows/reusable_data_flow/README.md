# 재사용 데이터 조회 Flow

사용자의 자연어 요청을 짧은 `data_request`로 만들고, Source Catalog에서 실행 설정을 채운 뒤 여러 데이터 소스의 결과를 공통 형식으로 합치도록 설계한 Flow입니다.

> **현재 Flow JSON은 가져오면 안 됩니다.** 감사 결과 `reusable_data_flow.json`은 아래 12개 데이터 내부 노드가 연결된 export가 아니라 과거 `업무분석flow`와 동일한 파일로 확인됐습니다. Python 원본과 연결 설계는 보존하지만 올바른 Builder export 제공 또는 신규 재구축 전까지 실행 자산으로 배포하지 않습니다.

## 현재 상태

- 상태: `building` (Flow export 복구 대기)
- 격리 파일: `reusable_data_flow.json` (실제 이름 `업무분석flow`, import 금지)
- 확인 환경: Langflow `1.8.2` export/로컬 DB 읽기 전용 감사
- 기능 단위 Component: 0개
- Flow 내부 Standalone 노드: 12개
- 남은 확인: 올바른 Flow 재구축, import, 포트 연결, 대표 질문 실행

## 핵심 데이터 흐름

```text
사용자 질문 + Source Catalog
-> Prompt Template
-> LLM Caller
-> Data Request Normalizer
-> Oracle / H-API / Datalake / Goodocs
-> Data Result Merger
-> Data Output Builder
-> data_json 또는 HTML Report Datasets Adapter
```

LLM은 데이터베이스 주소나 SQL 전체를 만들지 않고 데이터 소스 이름과 파라미터 중심의 짧은 요청을 만듭니다. `Data Request Normalizer`가 같은 Source Catalog를 보고 실행에 필요한 설정을 채웁니다.

## 두 가지 사용 과정

### 1. Source Catalog 준비

사람이 적은 데이터 설명을 `Source Catalog Normalizer`로 표준화합니다. 결과를 바로 연결하거나 MongoDB Store/Loader로 보관할 수 있습니다.

### 2. 데이터 조회

질문을 실행 요청으로 바꾸고 소스별 노드가 자신에게 해당하는 요청만 처리합니다. Merger와 Output Builder가 결과를 자동화용 JSON과 사용자 확인용 메시지로 나눕니다.

## 중요 계약

- `data_request`: 실행할 소스, 파라미터, 카탈로그에서 채운 설정
- `source_result`: 한 데이터 소스의 성공 여부, 행, 컬럼, 오류, 요청 파라미터
- `data_json.data_result`: 요청 순서대로 정리된 실제 row 목록
- `data_json.source_results`: 소스별 상세 추적 정보
- `datasets`: HTML Report Flow에 넘기기 위한 데이터셋 배열

## 파일

- `reusable_data_flow.json`: 불일치 증거 보존용 격리 파일이며 가져오기용이 아님
- [`CONNECTION_GUIDE.md`](CONNECTION_GUIDE.md): 초보자용 연결 가이드
- [`component_refs.json`](component_refs.json): 현재 직접 참조하는 공용 Component 없음
- [`internal_nodes.json`](internal_nodes.json), `nodes/`: 재구축할 12개 Flow 내부 단계와 Python 원본
- [`../../html/troubleshooting/reusable-data-flow-export-mismatch.html`](../../html/troubleshooting/reusable-data-flow-export-mismatch.html): 확인 범위와 복구 조건
- `references/`: 기존 구현의 상세 프롬프트와 구조 분석 자료

## 주의

기존 소스 노드에는 빠른 연결 확인을 위한 dummy row 경로가 포함되어 있습니다. 실제 Oracle/H-API/Datalake/Goodocs 호출로 전환하기 전에 해당 파일의 실행 경로, 패키지, 인증값, 서버 네트워크 정책을 확인해야 합니다.

## 최소 단위 직접 조회 Component와의 관계

이 Flow의 기존 12개 Python 파일은 Source Catalog, 분기, 병합과 출력 envelope에 결합된 내부 노드로 `internal_nodes.json`에 보존합니다. 현재 JSON에는 이 노드들이 없으므로 Flow 복구 전까지 실행 계약으로 간주하지 않습니다.

단일 소스를 직접 한 번 조회하고 결과 테이블만 받고 싶을 때는 다음 신규 Standalone Component를 별도로 등록합니다.

- `oracle_table_query`
- `h_api_table_request`
- `datalake_table_query`
- `goodocs_table_reader`
- `simple_api_table_request`

신규 Component는 모두 출력 포트가 `data_table` 하나이고 자료형은 `DataFrame`입니다. 성공·실패 envelope, Source Catalog 라우팅, 다중 요청 병합, dummy row는 포함하지 않습니다. 자세한 입력과 실제 환경 확인 항목은 [`../../components/DIRECT_DATA_ACCESS_COMPONENTS_GUIDE.md`](../../components/DIRECT_DATA_ACCESS_COMPONENTS_GUIDE.md)를 참고합니다.
