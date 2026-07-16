# PPT 참고 이미지 기반 HTML 프레젠테이션 Flow

`ppt_reference_html_flow`는 사용자가 제공한 **표지 참고 이미지**, **본문 참고 이미지**, 발표 목적과 데이터셋을 바탕으로 실제 슬라이드처럼 넘겨 볼 수 있는 단일 HTML 프레젠테이션을 생성하는 Langflow Flow입니다.

참고 이미지의 문구를 복사하는 Flow가 아닙니다. 이미지는 색상, 여백, 타이포그래피 계층, 콘텐츠 그리드와 반복 모티프를 분석하기 위한 디자인 근거로만 사용합니다. 발표 내용과 수치는 사용자가 입력한 `brief`와 `datasets`만 사실 근거로 삼습니다. Hallmark식 구성 원칙과 Emil식 모션 기준은 Prompt만이 아니라 별도 정책 계약, Normalizer, 결정론적 Renderer와 Quality Gate에 나누어 적용합니다.

## 현재 상태

- Flow ID: `ppt_reference_html_flow`
- 상태: `user_testing`
- 대상 환경: Langflow `1.8.2`
- 기능 단위 Component: 2개 필수, 1개 선택
- Flow 전용 Standalone 내부 Node: 7개
- 출력: 독립 실행 가능한 HTML 원문, 품질 검사 결과, 선택적 Report API 공유 링크
- 첫 실행 자산: 16:9 합성 참고 이미지 3장과 Builder 개별 입력 양식
- 검증 범위: Python 단위·통합 계약, Langflow 1.8.2/LFX 0.3.4 template 평가, 17-node/22-edge JSON 내장 source·schema 검증, 디자인·모션 정책 정적 검사
- 미검증 범위: 실제 Vision Language Model을 연결한 이미지 이해 E2E, 실제 Langflow Builder에서의 전체 실행, 브라우저별 시각 회귀

`user_testing`은 설계가 완료되었다는 의미가 아닙니다. 특히 모델마다 이미지 해석과 구조화 JSON 생성 방식이 달라질 수 있으므로 실제 사내 Vision 모델로 확인한 뒤 프롬프트와 입력 계약을 보정해야 합니다.

## 전체 처리 흐름

```text
00 표지 참고 이미지
  -> Multi Image Base64 Encoder
01 본문 참고 이미지
  -> Multi Image Base64 Encoder

두 Encoder 결과 + brief + datasets
  -> 02 presentation_request_builder
  -> 03 presentation_reference_analyzer
       └─ Vision Language Model 직접 호출
  -> 04 presentation_design_policy_builder
       └─ Hallmark식 구성 + Emil식 모션 정책 JSON
  -> 05 presentation_plan_generator
       └─ Text Language Model 직접 호출
  -> 06 presentation_plan_normalizer
  -> 07 HTML Presentation Renderer
  -> 08 presentation_quality_gate
  -> 09 presentation_html_source_output
  -> Chat Output

선택 경로:
08 presentation_quality_gate
  -> Report API Publisher
  -> Chat Output
```

## Component와 내부 Node 구분

### 독립 Component

| ID | 필수 여부 | Input | Output | 역할 |
| --- | --- | --- | --- | --- |
| `multi_image_base64_encoder` | 필수 | 여러 이미지 파일 | 순서가 보존된 Base64/Data URL 목록 | 표지·본문 이미지를 안전하게 검증하고 Vision 입력으로 변환 |
| `html_presentation_renderer` | 필수 | 검증된 프레젠테이션 계획 Data | HTML 프레젠테이션 Data | 허용된 레이아웃과 시각 요소만 결정론적으로 렌더링 |
| `report_api_publisher` | 선택 | HTML 생성 결과, API 주소, TTL | 다운로드 링크 Message | 기존 Report API에 HTML을 게시하고 공유 링크 반환 |

### Flow 내부 Node

| 순서 | ID | 역할 |
| --- | --- | --- |
| 02 | `presentation_request_builder` | 두 Encoder 결과, 사용자 brief와 datasets를 하나의 요청 계약으로 정규화 |
| 03 | `presentation_reference_analyzer` | 표지와 본문 이미지를 Vision 모델로 분석해 디자인 시스템 후보 생성 |
| 04 | `presentation_design_policy_builder` | Hallmark식 구성 원칙과 Emil식 모션 기준을 구조화된 정책 계약으로 고정 |
| 05 | `presentation_plan_generator` | 디자인 분석·정책·데이터를 바탕으로 역할이 있는 슬라이드 계획 생성 |
| 06 | `presentation_plan_normalizer` | 모델 출력을 허용 schema, 밀도, 역할, 실제 데이터 계약으로 결정론적 보정 |
| 08 | `presentation_quality_gate` | 데이터·접근성·외부 의존성뿐 아니라 정책, 반복 layout과 모션 위반 검사 |
| 09 | `presentation_html_source_output` | 검증된 HTML 원문을 Chat Output에서 확인 가능한 Message로 변환 |

내부 Node는 다른 Flow에서 독립적으로 사용할 안정적인 업무 기능이라기보다 이 Flow의 실행 순서와 payload를 맞추는 단계입니다. 따라서 Standalone Python 파일이어도 Component Library에는 올리지 않습니다.

## 입력 요약

참고 이미지 기반 경로의 권장 입력은 다음과 같습니다. Import된 `02 발표 요청 정리` Node에는 제목·부제·목적·대상 청중·톤·목차·마지막 의사결정·본문·목표 슬라이드 수가 표시되고, 고급 입력에는 발표 언어와 구조화 요청이 있습니다. 첫 실행용 예시값도 들어 있습니다.

1. 표지 참고 이미지 1개 이상
2. 본문 참고 이미지 1개 이상
3. 발표 제목, 목적, 대상 독자, 발표 언어와 목표 슬라이드 수
4. 슬라이드에서 전달할 내용 또는 데이터셋

첫 실행에는 다음 프로젝트 샘플을 그대로 사용할 수 있습니다.

- 표지: `samples/reference_images/reference_cover_navy_teal.png`
- 본문 1: `samples/reference_images/reference_body_trend.png`
- 본문 2: `samples/reference_images/reference_body_comparison_table.png`
- 입력 양식: `samples/INPUT_FORM.md`

현재 Request Builder는 참고 이미지가 없으면 차단하지 않고 경고와 함께 프로젝트 기본 디자인으로 진행할 수 있습니다. 이 경우 결과는 “참고 이미지 기반 재현”이 아니라 일반 HTML 프레젠테이션 fallback입니다. 잘못된 Base64나 허용되지 않은 이미지 형식이 실제로 제공된 경우에는 구조화 오류를 반환합니다.

데이터는 `datasets[].columns`에 의미 타입을 명시하고 `datasets[].rows`에 실제 행을 넣는 방식을 권장합니다. 사용자는 `preferred_visual`을 `auto`, `table`, `kpi`, `bar`, `line`, `scatter`, `histogram`, `stacked_bar` 중 하나로 지정할 수 있습니다. `auto`이면 Flow가 정확한 값 확인, 범주 비교, 시간 추세와 두 수치의 관계 중 무엇이 핵심인지 판단합니다. 0.1.0에서 `histogram`과 `stacked_bar`는 임의 계산을 만들지 않고 원본 표로 낮춘 뒤 경고합니다.

전체 계약은 [PRESENTATION_SCHEMA.md](references/PRESENTATION_SCHEMA.md), 데이터 선택 원칙은 [DATA_VISUALIZATION_RULES.md](references/DATA_VISUALIZATION_RULES.md)를 확인합니다.

## 중요한 안전 원칙

- 참고 이미지 속 문장과 숫자는 발표 사실로 사용하지 않습니다.
- OCR이 불확실한 한글, 정확한 좌표, 색상 구분과 개수는 추측하지 않습니다.
- 모델은 HTML이나 JavaScript를 직접 작성하지 않고 계획 JSON만 제안합니다.
- 디자인·모션 정책은 Prompt 문구가 아니라 `design_policy` JSON으로 모든 후속 단계에 전달합니다.
- Renderer는 허용 목록에 포함된 레이아웃과 요소만 출력합니다.
- 키보드 탐색에는 모션을 적용하지 않고, 포인터 모션은 300ms 이하의 transform·opacity로 제한합니다.
- 제공되지 않은 데이터, 합계, 증감률과 출처를 만들어 내지 않습니다.
- 모든 표와 차트에 단위, 기준 기간, 출처를 가능한 범위에서 표시합니다.
- 표지와 본문 이미지를 구분하고 각 역할을 바꿔 사용하지 않습니다.
- 실패나 낮은 신뢰도는 `warnings`와 `quality_report`에 남깁니다.

## 관련 파일

- [CONNECTION_GUIDE.md](CONNECTION_GUIDE.md): Builder 연결 순서와 입출력 설명
- [VISION_ANALYSIS_PROMPT.md](references/VISION_ANALYSIS_PROMPT.md): 참고 이미지 분석 프롬프트
- [PRESENTATION_PLAN_PROMPT.md](references/PRESENTATION_PLAN_PROMPT.md): 슬라이드 계획 프롬프트
- [PRESENTATION_AUTHORING_SKILL.md](references/PRESENTATION_AUTHORING_SKILL.md): 재사용 가능한 프레젠테이션 작성 지침
- [DESIGN_MOTION_POLICY.md](references/DESIGN_MOTION_POLICY.md): Hallmark·Emil 정책의 실제 적용 계층과 검사 기준
- [DATA_VISUALIZATION_RULES.md](references/DATA_VISUALIZATION_RULES.md): 표·차트·KPI 선택 규칙
- [PRESENTATION_SCHEMA.md](references/PRESENTATION_SCHEMA.md): 요청·분석·계획·출력 schema
- [WEB_RESEARCH_SOURCES.md](references/WEB_RESEARCH_SOURCES.md): 공식 웹 근거와 프로젝트 반영 내용
- [sample_presentation_data.json](samples/sample_presentation_data.json): 데이터 입력 예시
- [TEST_CASES.md](samples/TEST_CASES.md): 사용자 테스트 시나리오
- [INPUT_FORM.md](samples/INPUT_FORM.md): Builder 실제 입력 필드와 이미지 업로드 순서
- [reference_images](samples/reference_images/README.md): 첫 실행용 16:9 합성 참고 이미지 세트
