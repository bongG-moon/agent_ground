# HTML 분석 리포트 Flow

CSV 또는 JSON 데이터와 사용자가 원하는 표현 방식을 받아, 데이터 구조를 분석하고 적절한 블록을 조합해 독립 실행 가능한 HTML 리포트를 만드는 Flow입니다.

## 현재 상태

- 상태: `user_testing`
- 기준 Flow: `html_report_flow.json`
- 기존 export 기록: Langflow `1.8.2`
- 기능 단위 Component: 3개
- Flow 내부 Standalone 노드: 6개
- 지원 출력: HTML 원문, 선택적 Report API 공유 링크

## 핵심 원칙

- LLM은 HTML 코드를 직접 만들지 않습니다.
- LLM은 허용된 블록으로 구성된 `report_plan`만 제안합니다.
- `LLM 계획 검증`이 데이터에 없는 컬럼과 잘못된 블록을 정리합니다.
- `HTML Template Renderer`가 검증된 계획과 데이터를 deterministic 방식으로 렌더링합니다.

## 기본 흐름

```text
00 리포트 요청/데이터 불러오기
-> 01 데이터 구조 분석
-> 02 기본 요소 양식/추천
-> 03 기본 리포트 계획
-> 03a 프롬프트 변수 준비
-> Prompt Template
-> LLM
-> 03b LLM 계획 검증
-> 04 HTML 렌더링
-> 05-1 HTML 원문 또는 05-2 공유 링크
```

## 입력 방법

- CSV/JSON을 `데이터 직접 입력`에 붙여넣기
- Langflow Read File의 Structured Content를 `파일 데이터`에 연결
- `reusable_data_flow`의 HTML Report Datasets Adapter 결과 연결

## 출력 방법

### 서버 없이 확인

`04 HTML 렌더링 -> 05-1 HTML 원문 출력 -> Chat Output`

### 링크로 공유

`04 HTML 렌더링 -> 05-2 공유 링크 출력 -> Chat Output`

두 번째 방식은 이 폴더의 `report_api/` 보조 서버가 필요합니다.

## 파일

- [`html_report_flow.json`](html_report_flow.json): 가져오기용 Flow
- [`CONNECTION_GUIDE.md`](CONNECTION_GUIDE.md): 초보자용 연결 가이드
- [`component_refs.json`](component_refs.json): 필요한 Component와 버전
- [`internal_nodes.json`](internal_nodes.json), `nodes/`: 이 Flow에 종속된 요청·카탈로그·계획·출력 단계
- `samples/`: CSV/JSON 입력과 요소 카탈로그 샘플
- `report_api/`: HTML 저장·보기·다운로드용 보조 서버
- `references/`: 블록 분류, Prompt Template, 기존 상세 가이드
