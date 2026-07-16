# PPT 참고 이미지 기반 HTML 프레젠테이션 Flow 연결 가이드

이 문서는 `ppt_reference_html_flow`를 Langflow Builder에서 확인하거나 Standalone Component를 교체할 때 사용하는 연결 기준입니다.

## 1. 준비할 입력

첫 실행용 파일은 `samples/reference_images/`에 있습니다. 표지에는 `reference_cover_navy_teal.png`, 본문에는 `reference_body_trend.png`와 `reference_body_comparison_table.png`를 순서대로 사용합니다.

### 표지 이미지 Encoder

`Multi Image Base64 Encoder`를 하나 배치하고 표지 참고 이미지만 넣습니다.

- `output_format`: `data_url`
- `error_policy`: `reject_batch`
- `allow_svg`: 기본 `false`
- Output: `encoded_images`

### 본문 이미지 Encoder

동일 Component를 하나 더 배치하고 본문 레이아웃 참고 이미지를 순서대로 넣습니다.

- `output_format`: `data_url`
- `error_policy`: `skip_invalid`
- 첫 번째 이미지는 기본 본문 레이아웃으로 간주합니다.
- 추가 이미지는 표, 차트, 비교, 섹션 구분 등 변형 레이아웃 참고용입니다.
- Output: `encoded_images`

두 Encoder를 하나로 합치지 않는 이유는 파일명이나 업로드 순서에 의존하지 않고 표지와 본문의 역할을 명확히 하기 위해서입니다.

## 2. 기본 연결표

| 순서 | From | Output | To | Input | 전달 타입 |
| --- | --- | --- | --- | --- | --- |
| 1 | 표지 Multi Image Base64 Encoder | `encoded_images` | `presentation_request_builder` | `cover_images` | Data |
| 2 | 본문 Multi Image Base64 Encoder | `encoded_images` | `presentation_request_builder` | `body_images` | Data |
| 3 | Chat Input | `message` | `presentation_request_builder` | `user_request` | Message |
| 4 | `presentation_request_builder` | `request` | `presentation_reference_analyzer` | `request` | Data |
| 5 | Language Model | `model_output` | `presentation_reference_analyzer` | `model` | LanguageModel |
| 6 | `presentation_request_builder` | `request` | `presentation_design_policy_builder` | `request` | Data |
| 7 | `presentation_reference_analyzer` | `analysis` | `presentation_design_policy_builder` | `analysis` | Data |
| 8 | `presentation_request_builder` | `request` | `presentation_plan_generator` | `request` | Data |
| 9 | `presentation_reference_analyzer` | `analysis` | `presentation_plan_generator` | `analysis` | Data |
| 10 | `presentation_design_policy_builder` | `design_policy` | `presentation_plan_generator` | `design_policy` | Data |
| 11 | Language Model | `model_output` | `presentation_plan_generator` | `model` | LanguageModel |
| 12 | `presentation_request_builder` | `request` | `presentation_plan_normalizer` | `request` | Data |
| 13 | `presentation_reference_analyzer` | `analysis` | `presentation_plan_normalizer` | `analysis` | Data |
| 14 | `presentation_plan_generator` | `plan_draft` | `presentation_plan_normalizer` | `plan_draft` | Data |
| 15 | `presentation_design_policy_builder` | `design_policy` | `presentation_plan_normalizer` | `design_policy` | Data |
| 16 | `presentation_plan_normalizer` | `normalized_plan` | HTML Presentation Renderer | `presentation_plan` | Data |
| 17 | HTML Presentation Renderer | `presentation_artifact` | `presentation_quality_gate` | `presentation_artifact` | Data |
| 18 | `presentation_plan_normalizer` | `normalized_plan` | `presentation_quality_gate` | `presentation_plan` | Data |
| 19 | HTML Presentation Renderer | `presentation_artifact` | `presentation_html_source_output` | `payload` | Data |
| 20 | `presentation_quality_gate` | `quality_report` | `presentation_html_source_output` | `quality_report` | Data |
| 21 | `presentation_html_source_output` | `message` | Chat Output | `input_value` | Message |
| 22 | `presentation_quality_gate` | `quality_report` | Report API Publisher | `payload` | Data · 선택 |

실제 JSON의 포트명이 위 표와 다르면 JSON에 내장된 Python 클래스의 `inputs`와 `outputs`를 우선합니다. 이 문서는 의미 계약과 연결 방향을 설명합니다.

## 3. Language Model 연결 방식

### Vision 모델

`presentation_reference_analyzer`가 Vision Language Model 입력을 직접 호출하거나, 환경에 따라 Vision 모델 Component를 연결합니다.

필수 조건:

- Base64 본문이 아니라 `data:image/...;base64,...` 형식의 Data URL 전달
- 표지와 본문 이미지를 서로 다른 입력 블록으로 표시
- 이미지 세부 분석이 가능한 모델 사용
- 모델 출력은 JSON object로 제한
- 이미지 속 숫자와 문구를 발표 사실로 사용하지 않도록 고정 지침 적용

출력 포트는 `analysis`이며 Data 안의 `analysis` 객체에 색상, 타이포그래피 계열, 레이아웃, 반복 모티프, 표·차트 스타일, 신뢰도와 경고를 포함합니다.

### 계획 모델

`presentation_plan_generator`는 Vision 분석 결과, 실제 `brief`·`datasets`, `design_policy`를 받아 `presentation_plan`을 만듭니다. Vision 모델과 같은 모델을 사용할 수도 있지만, 이미지 Base64를 다시 넣지 않고 정규화된 분석 결과만 전달합니다. 정책은 Prompt에 요약되지만 모션 CSS/JS는 모델이 만들지 않습니다.

### 디자인·모션 정책 Node

`presentation_design_policy_builder`는 모델을 호출하지 않습니다. Hallmark식 구성 원칙과 Emil식 모션 검사 기준을 `design_policy` JSON으로 만들고 Generator와 Normalizer에 동시에 전달합니다. 따라서 Prompt가 규칙을 누락해도 Normalizer, Renderer와 Quality Gate가 동일 계약으로 보정·차단할 수 있습니다.

## 4. 요청 Data 예시

`02 발표 요청 정리` Node에는 다음 개별 양식이 기본 표시됩니다.

- `presentation_title`, `presentation_subtitle`
- `presentation_purpose`, `target_audience`, `presentation_tone`
- `presentation_language`: 기본 `ko`, 고급 입력
- `content_outline`: 한 줄에 한 목차 항목
- `call_to_action`, `content`
- `datasets_json` 또는 `dataset_files`
- `target_slide_count`

기존 `brief` 입력은 고급 호환 필드로 유지합니다. 구조화된 `presentation_request.brief`가 연결되면 이를 우선하고, 그렇지 않으면 개별 양식 값을 기존 brief 계약으로 합칩니다.

```json
{
  "brief": {
    "title": "2026년 상반기 운영 실적",
    "purpose": "경영진이 개선 우선순위를 결정하도록 돕는다.",
    "audience": "경영진",
    "language": "ko",
    "slide_count": 7,
    "tone": "간결하고 근거 중심"
  },
  "template_mode": "analyze_and_rebuild",
  "datasets": [
    {
      "dataset_id": "monthly_operations",
      "title": "월별 처리량과 오류율",
      "source": "운영 DW",
      "period": "2026-01~2026-06",
      "preferred_visual": "auto",
      "columns": [
        {"name": "month", "semantic_type": "temporal"},
        {"name": "processed_count", "semantic_type": "quantitative", "unit": "건"},
        {"name": "error_rate", "semantic_type": "quantitative", "unit": "%"}
      ],
      "rows": [
        {"month": "2026-01", "processed_count": 1250, "error_rate": 3.2},
        {"month": "2026-02", "processed_count": 1390, "error_rate": 2.8}
      ]
    }
  ]
}
```

이미지 Data URL은 사용자가 직접 이 JSON에 복사하지 않습니다. 두 Encoder 결과를 `presentation_request_builder`가 `reference_images.cover`와 `reference_images.body`에 배치합니다.

## 5. Renderer 연결

HTML Presentation Renderer에는 `presentation_plan_normalizer`가 계약 검증을 마친 payload만 연결합니다. Renderer 다음의 `presentation_quality_gate`는 생성된 HTML 자체를 포함해 접근성 표식, 외부 의존성, 정적 overflow 위험과 슬라이드 수를 검사합니다.

Renderer가 받아야 하는 최소 항목:

- `presentation_plan.design_system`
- `presentation_plan.design_policy`
- `presentation_plan.slides`
- `presentation_plan.data_views`

Renderer 출력에는 최소 다음 항목이 있어야 하며, 이 Data를 Quality Gate로 전달합니다.

- `presentation_artifact.html`
- `presentation_artifact.title`
- `presentation_artifact.filename_hint`
- `presentation_artifact.slide_count`
- `presentation_artifact.design_policy_id`
- `presentation_artifact.motion_profile`
- `html_report.html` — 기존 Report API Publisher 호환 alias
- `warnings`
- `errors`

## 6. 선택적 Report API 연결

HTML을 다운로드 링크로 공유하려면 `presentation_quality_gate`의 `quality_report` 출력을 `Report API Publisher`에 연결합니다. Gate는 `pass` 또는 `warning`일 때만 `html_report` alias를 출력하고 `fail`이면 HTML을 제거하므로 검사 전 원문을 우회 발행하지 않습니다.

```text
presentation_quality_gate.quality_report
  -> Report API Publisher.payload

Report API Publisher.link_message
  -> Chat Output.input_value
```

`report_api_url`에는 기존 HTML Report Flow가 사용하는 Report API 주소를 넣습니다. 서버가 꺼져 있어도 HTML 생성 자체는 성공해야 하며 게시 실패는 별도 오류로 표시합니다.

## 7. 실행 전 확인 목록

- 표지와 본문 Encoder가 바뀌어 연결되지 않았는가
- Encoder 출력이 `data_url`인가
- `brief.title`, `purpose`, `audience`가 들어 있는가
- 데이터 컬럼의 의미 타입과 단위가 명시되어 있는가
- 실제 모델이 이미지 입력과 구조화 JSON 출력을 지원하는가
- Plan Normalizer 실패 결과가 Renderer로 무조건 통과하지 않는가
- Policy Node가 Generator와 Normalizer 양쪽에 연결되어 있는가
- Quality Gate의 `require_design_policy`가 `true`인가
- Renderer 결과가 HTML Quality Gate를 거치지 않고 사용자에게 전달되지 않는가
- 공유 링크가 필요하지 않으면 Report API 경로를 제외했는가

## 8. 현재 검증 경계

현재 상태는 `user_testing`입니다. 아래 항목은 사용자 환경에서 반드시 확인해야 합니다.

1. 실제 Vision 모델이 표지와 본문을 올바르게 구분하는지
2. 한글이 포함된 참고 이미지에서 디자인 분석이 안정적인지
3. 모델이 긴 Base64 입력을 허용하는지와 요청 크기 제한
4. 차트·표가 16:9 화면에서 잘리지 않는지
5. Chrome, Edge와 사내 표준 브라우저에서 키보드 탐색과 인쇄가 정상인지
6. Report API 링크가 실제 사내 네트워크에서 열리는지

실제 Vision E2E가 완료되기 전에는 `approved`나 `stable`로 상태를 올리지 않습니다.
