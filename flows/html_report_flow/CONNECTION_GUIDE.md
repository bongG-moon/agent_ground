# HTML 분석 리포트 Flow 연결 가이드

## 1. Component 등록

`component_refs.json`에 있는 9개 Standalone `.py` 파일을 Agent Builder에 등록합니다.

## 2. 기본 연결

| 순서 | From | Output | To | Input |
| --- | --- | --- | --- | --- |
| 1 | 00 리포트 요청/데이터 불러오기 | 요청 데이터 | 01 데이터 구조 분석 | 요청 데이터 |
| 2 | 00 | 요청 데이터 | 03 기본 리포트 계획 | 요청 데이터 |
| 3 | 01 데이터 구조 분석 | 데이터 분석 결과 | 02 기본 요소 양식/추천 | 데이터 분석 결과 |
| 4 | 01 | 데이터 분석 결과 | 03 기본 리포트 계획 | 데이터 분석 결과 |
| 5 | 02 기본 요소 양식/추천 | 요소 추천 결과 | 03 기본 리포트 계획 | 요소 추천 결과 |
| 6 | 03 기본 리포트 계획 | 기본 계획 | 03a 프롬프트 변수 준비 | 기본 계획 |
| 7 | 03 기본 리포트 계획 | 기본 계획 | 03b LLM 계획 검증 | 기본 계획 |
| 8 | 03a의 5개 출력 | 같은 이름 변수 | Prompt Template | 같은 이름 변수 |
| 9 | Prompt Template | Prompt | LLM | Input |
| 10 | LLM | Text/Message | 03b LLM 계획 검증 | LLM 응답 |
| 11 | 03b LLM 계획 검증 | 최종 계획 | 04 HTML 렌더링 | 최종 계획 |

## 3. 출력 분기

서버 없이 전체 HTML 원문 확인:

| From | Output | To | Input |
| --- | --- | --- | --- |
| 04 HTML 렌더링 | HTML 생성 결과 | 05-1 HTML 원문 출력 | HTML 생성 결과 |
| 05-1 HTML 원문 출력 | HTML 원문 | Chat Output | Input |

공유 링크 출력:

| From | Output | To | Input |
| --- | --- | --- | --- |
| 04 HTML 렌더링 | HTML 생성 결과 | 05-2 공유 링크 출력 | HTML 생성 결과 |
| 05-2 공유 링크 출력 | 링크 메시지 | Chat Output | Input |

## 4. Prompt Template 변수

`03a`가 다음 다섯 값을 출력합니다.

- `사용자_요청_JSON`
- `리포트_컨텍스트_JSON`
- `디자인_지시`
- `렌더링_규칙`
- `출력_스키마_JSON`

`references/PROMPT_TEMPLATE.md`의 본문을 Prompt Template에 넣고 같은 이름의 입력에 연결합니다.

## 5. 첫 테스트

1. `samples/00_data_inputs/sample_wip.csv` 전체를 `데이터 직접 입력`에 붙여넣습니다.
2. 질문에는 `공정별 WIP와 생산량을 요약해줘`를 넣습니다.
3. 보고 싶은 방식에는 `상단 KPI, 중간 비교 막대그래프, 하단 상세 표`를 넣습니다.
4. `01`에서 숫자·날짜·범주 후보가 맞는지 확인합니다.
5. `03b`의 최종 계획에서 실제 데이터에 없는 컬럼이 제거됐는지 확인합니다.
6. `04` 결과에 완전한 HTML과 report plan이 있는지 확인합니다.
7. 먼저 `05-1`로 확인한 뒤, 필요할 때만 Report API를 연결합니다.
