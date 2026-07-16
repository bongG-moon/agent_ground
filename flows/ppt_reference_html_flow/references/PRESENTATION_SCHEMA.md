# PPT 참고 이미지 기반 HTML 프레젠테이션 Schema

이 문서는 Flow 단계 사이에서 전달할 논리적 JSON 계약을 정의합니다. Langflow의 `Data` 객체에서는 아래 object가 `data`에 들어갑니다. 실제 Python 클래스의 포트명과 타입이 다르면 내장 코드 계약을 우선하되 key의 의미는 이 문서를 유지합니다.

## 0. Builder 입력 양식

`presentation_request_builder`는 구조화 Data뿐 아니라 다음 사용자 입력 필드를 직접 받습니다.

| 필드 | 의미 |
| --- | --- |
| `presentation_title` | 발표 제목 |
| `presentation_subtitle` | 발표 부제 |
| `presentation_purpose` | 발표 목적과 기대 결정 |
| `target_audience` | 대상 청중 |
| `presentation_language` | 발표 언어 코드 또는 이름, 기본값 `ko` |
| `presentation_tone` | 표현 톤 |
| `content_outline` | 줄바꿈으로 구분한 목차 |
| `call_to_action` | 마지막 요청 또는 의사결정 |
| `content` | 실제 발표 본문 |
| `datasets_json` / `dataset_files` | 실제 데이터 |
| `target_slide_count` | 3~30장 목표 분량 |

구조화된 `presentation_request.brief`가 있으면 해당 값을 우선합니다. 없으면 개별 양식과 고급 호환 입력 `brief`를 합쳐 아래 요청 payload를 만듭니다.

## 1. 요청 payload

`presentation_request_builder` 출력 기준입니다.

```json
{
  "schema_version": "1.0",
  "request": {
    "brief": {
      "title": "2026년 상반기 운영 실적",
      "subtitle": "처리량 증가와 오류 감소 과제",
      "purpose": "경영진의 개선 우선순위 결정",
      "audience": "경영진",
      "language": "ko",
      "slide_count": 7,
      "tone": "간결하고 근거 중심",
      "content": "",
      "content_outline": ["핵심 요약", "근거", "다음 행동"],
      "call_to_action": "하반기 개선 과제의 우선순위를 승인한다."
    },
    "template_mode": "analyze_and_rebuild",
    "reference_images": {
      "cover": [
        {
          "reference_id": "cover_01",
          "role": "cover",
          "position": 1,
          "filename": "cover.png",
          "mime_type": "image/png",
          "byte_size": 1024,
          "data_url": "data:image/png;base64,..."
        }
      ],
      "body": []
    },
    "datasets": []
  },
  "recommendations": [],
  "errors": [],
  "warnings": [
    "유효한 본문 참고 이미지가 없습니다. 기본 본문 스타일을 사용합니다."
  ],
  "meta": {
    "dataset_count": 0,
    "cover_image_count": 1,
    "body_image_count": 0,
    "status": "warning"
  }
}
```

### 참고 이미지 기반 결과의 권장값

- `request.brief.title`
- `request.brief.purpose`
- `request.brief.audience`
- `request.reference_images.cover[0]`
- `request.reference_images.body[0]`

현재 구현은 제목·대상 독자에 기본값을 넣고 이미지 누락은 경고와 기본 스타일 fallback으로 처리합니다. 따라서 위 값은 참고 이미지 기반 품질을 위한 권장값이며, Python 입력 검증의 절대 필수값은 아닙니다. 잘못된 이미지가 실제로 들어오거나 데이터 파일 파싱에 실패한 경우에는 `errors`가 생깁니다. 데이터가 없는 서술형 발표도 허용하지만 데이터 기반 차트나 KPI를 계획해서는 안 됩니다.

### `template_mode`

| 값 | 의미 |
| --- | --- |
| `analyze_and_rebuild` | 참고 이미지에서 디자인 토큰을 추출해 HTML/CSS로 재구성. 기본값 |

0.2.0 실행 구현은 `analyze_and_rebuild`만 지원합니다. 참고 이미지를 실제 배경으로 그대로 삽입하는 모드는 이미지 안의 민감정보·문구·로고가 결과에 재노출될 수 있어 별도 검토 전까지 제공하지 않습니다.

## 2. Encoder 결과 수용 계약

`Multi Image Base64 Encoder`의 Data에서 다음 항목을 사용합니다.

```json
{
  "success": true,
  "output_format": "data_url",
  "order_preserved": true,
  "items": [
    {
      "position": 1,
      "filename": "cover.png",
      "mime_type": "image/png",
      "sha256": "...",
      "encoding": "data_url",
      "value": "data:image/png;base64,..."
    }
  ],
  "warnings": [],
  "errors": []
}
```

`items=[]`이면 해당 역할의 기본 스타일 fallback 경고를 남깁니다. `value`가 유효한 Base64 또는 Data URL이면 Request Builder가 안전한 Data URL로 통일하며, MIME이 허용 목록에 없거나 Base64가 깨졌으면 구조화 오류를 남깁니다. 파일의 원본 경로는 다음 단계로 전달하지 않습니다.

## 3. 참고 이미지 분석 계약

`presentation_reference_analyzer` 출력 기준입니다.

```json
{
  "schema_version": "1.0",
  "analysis": {
    "observations": [
      {
        "reference_id": "cover_01",
        "role": "cover",
        "layout": "left_aligned",
        "hierarchy": "큰 제목과 짧은 보조 문구",
        "spacing": "좌우 여백이 넓음",
        "motifs": ["rounded_rectangle"],
        "confidence": 0.82
      }
    ],
    "design_system": {
      "colors": {
        "background": "#F7F8FA",
        "surface": "#FFFFFF",
        "text": "#17212B",
        "muted_text": "#607080",
        "primary": "#173B57",
        "accent": "#E78A2F"
      },
      "typography": {
        "heading_family": "sans-serif",
        "body_family": "sans-serif",
        "heading_weight": 700,
        "body_weight": 400,
        "scale": "balanced"
      },
      "layout": {
        "traits": ["left_aligned", "grid", "minimal"],
        "density": "balanced",
        "whitespace": "generous"
      },
      "shapes": ["rounded_rectangle", "line"],
      "image_treatment": "cover",
      "chart_style": {"gridlines": "subtle", "labels": "direct", "legend": "when_needed"},
      "table_style": {"header": "filled", "row_dividers": "subtle", "zebra": false}
    },
    "confidence": 0.82,
    "warnings": []
  },
  "errors": [],
  "meta": {"status": "ready", "reference_count": 3, "source": "vision_model"}
}
```

분석 결과는 이미지의 정확한 픽셀 복제가 아니라 Renderer용 디자인 후보입니다. 모든 추정값은 기본 토큰으로 대체할 수 있어야 합니다.

## 4. 데이터셋 입력 계약

```json
{
  "dataset_id": "monthly_operations",
  "title": "월별 처리량과 오류율",
  "description": "확정 처리 기준",
  "source": "운영 DW",
  "period": "2026-01~2026-06",
  "preferred_visual": "auto",
  "columns": [
    {
      "name": "month",
      "label": "월",
      "semantic_type": "temporal",
      "unit": "",
      "format": "YYYY-MM"
    }
  ],
  "rows": [
    {"month": "2026-01"}
  ]
}
```

허용 의미 타입은 `quantitative`, `temporal`, `ordinal`, `nominal`, `text`, `identifier`입니다.

Request Builder는 입력값을 다음 실행용 형태로 다시 정규화합니다. 사용자용 `label`·`format`, `preferred_visual`과 `ordinal`·`identifier` 의미 타입도 0.2.0 결과에 보존합니다.

```json
{
  "dataset_id": "monthly_operations",
  "title": "월별 처리량과 오류율",
  "source": "운영 DW",
  "period": "2026-01~2026-06",
  "columns": [
    {
      "name": "month",
      "label": "월",
      "semantic_type": "temporal",
      "unit": "",
      "format": "YYYY-MM",
      "description": "",
      "non_empty_count": 6,
      "distinct_count": 6
    }
  ],
  "rows": [
    {"month": "2026-01"}
  ],
  "row_count": 1,
  "truncated": false,
  "preferred_visual": "auto",
  "recommended_visuals": ["line", "table"]
}
```

## 5. 프레젠테이션 계획 계약

`presentation_plan_normalizer` 출력 기준입니다.

```json
{
  "presentation_plan": {
    "plan_version": "ppt-reference-plan-v1",
    "title": "2026년 상반기 운영 실적",
    "language": "ko",
    "audience": "경영진",
    "purpose": "개선 우선순위 결정",
    "target_slide_count": 7,
    "design_system": {
      "aspect_ratio": "16:9",
      "colors": {
        "primary": "#173B57",
        "secondary": "#4E7992",
        "accent": "#E78A2F",
        "background": "#F7F8FA",
        "text": "#17212B",
        "muted": "#607080"
      },
      "typography": {
        "heading_family": "Pretendard, Noto Sans KR, sans-serif",
        "body_family": "Pretendard, Noto Sans KR, sans-serif",
        "heading_scale": "strong"
      },
      "safe_margin_percent": {"top": 5, "right": 5, "bottom": 5, "left": 5},
      "motifs": ["좌측 정렬 제목", "둥근 수치 카드"]
    },
    "design_policy": {
      "contract_version": "presentation-design-policy-v1",
      "policy_id": "hallmark-emil-balanced-v1",
      "composition": {
        "one_message_per_slide": true,
        "max_consecutive_same_layout": 2,
        "max_content_elements": 6,
        "max_bullets": 6,
        "require_design_role": true,
        "allow_decorative_gradient": false
      },
      "motion": {
        "profile": "purposeful-subtle",
        "keyboard_navigation_motion": false,
        "max_ui_duration_ms": 300,
        "slide_enter_duration_ms": 180,
        "reduced_motion_required": true,
        "hover_pointer_gate_required": true
      }
    },
    "storyline": [],
    "data_views": [],
    "slides": [],
    "normalization_warnings": [],
    "warnings": [],
    "errors": []
  }
}
```

### 허용 layout

- `cover`
- `section`
- `title-content`
- `two-column`
- `kpi-grid`
- `chart-focus`
- `table-focus`
- `comparison`
- `timeline`
- `conclusion`

### 허용 element type

- `title`
- `subtitle`
- `text`
- `bullet_list`
- `kpi`
- `image`
- `shape`
- `table`
- `bar_chart`
- `line_chart`
- `stacked_bar`
- `scatter_plot`
- `histogram`
- `process`
- `timeline`
- `source_note`
- `speaker_note`

## 6. 슬라이드 계약

```json
{
  "slide_no": 3,
  "layout": "chart-focus",
  "design_role": "evidence",
  "visual_weight": "balanced",
  "key_message": "처리량은 증가했지만 오류율 개선 속도는 둔화되었다.",
  "title": "처리량 증가와 함께 오류율 개선이 정체되었습니다",
  "subtitle": "2026년 1~6월",
  "elements": [
    {
      "element_id": "s03-chart",
      "element_type": "line_chart",
      "content": "",
      "data_view_id": "monthly_volume_error",
      "encoding": {
        "x": "month",
        "y": ["processed_count"],
        "series": "",
        "unit": "건"
      },
      "alt_text": "2026년 1월부터 6월까지 월별 처리량 변화",
      "source_note": "출처: 운영 DW"
    }
  ],
  "speaker_notes": "오류율은 별도 표에서 정확한 값을 확인한다."
}
```

`slide_no`는 1부터 연속이어야 하며 `element_id`는 문서 전체에서 고유해야 합니다.

## 7. Renderer 출력 계약

```json
{
  "success": true,
  "status": "ok",
  "payload_version": "html-presentation-artifact-v1",
  "flow_type": "ppt_image_to_html",
  "report_plan": {
    "title": "2026년 상반기 운영 실적",
    "layout": "html_presentation_16_9",
    "slide_count": 7
  },
  "presentation_artifact": {
    "title": "2026년 상반기 운영 실적",
    "html": "<!doctype html>...",
    "filename_hint": "2026_상반기_운영_실적",
    "slide_count": 7,
    "aspect_ratio": "16:9",
    "self_contained": true,
    "byte_size": 12345,
    "sha256": "...",
    "rendered_at": "2026-07-13T21:00:00+09:00",
    "design_policy_id": "hallmark-emil-balanced-v1",
    "motion_profile": "purposeful-subtle"
  },
  "html_report": {
    "title": "2026년 상반기 운영 실적",
    "html": "<!doctype html>..."
  },
  "warnings": [],
  "errors": [],
  "trace": []
}
```

`html_report`는 기존 `Report API Publisher`와 바로 연결하기 위한 호환 alias이며 `presentation_artifact`와 같은 HTML을 담습니다.

HTML에는 Base64 이미지와 필요한 스타일을 포함할 수 있지만 원본 로컬 파일 경로, API 키, 모델 입력 원문은 포함하지 않습니다.

## 8. Quality Gate 계약

Quality Gate는 Renderer 결과를 받아 HTML과 계획을 함께 검사합니다.

```json
{
  "payload_version": "ppt-reference-html-v1",
  "quality_report": {
    "status": "pass|warning|fail",
    "checks": [
      {
        "check_id": "data-field-reference",
        "status": "pass|warning|fail",
        "message": "모든 차트 필드가 원본 데이터에 존재합니다.",
        "slide_no": 0
      }
    ],
    "blocking_errors": [],
    "warnings": [],
    "metrics": {
      "planned_slide_count": 7,
      "rendered_slide_count": 7,
      "html_bytes": 12345,
      "overflow_risk_slide_count": 0
    }
  },
  "presentation_plan": {"title": "2026년 상반기 운영 실적", "slides": []},
  "warnings": [],
  "errors": [],
  "meta": {"status": "pass"},
  "presentation_artifact": {"html": "<!doctype html>..."},
  "html_report": {"html": "<!doctype html>..."}
}
```

Gate 상태가 `fail`이면 마지막 두 HTML alias를 출력하지 않습니다. Report API Publisher는 Gate의 이 Data만 받으므로 검사 전 HTML을 우회 게시할 수 없습니다.

차단 기준:

- plan JSON 파싱 실패
- 존재하지 않는 dataset 또는 field 참조
- 허용되지 않은 element type
- 데이터가 없는 수치 주장
- HTML에 실행 가능한 사용자 입력이 유입될 위험
- 모든 슬라이드가 비어 있음
- `require_design_policy=true`인데 정책 ID 또는 슬라이드 역할이 없음
- 동일 layout 3회 연속, 정책 한도를 넘는 요소·bullet
- 장식용 gradient, `transition: all`, `scale(0)`, `ease-in`, 300ms 초과 UI 모션
- reduced-motion 또는 fine-pointer hover gate 누락

표지 또는 본문 이미지가 없으면 현재 기본 정책은 차단이 아니라 fallback 경고입니다. 사내 운영 정책에서 참고 이미지 재현을 필수로 정하려면 후속 버전에서 `require_reference_images` 입력과 차단 검사를 추가해야 합니다. 0.2.0에는 이 설정이 없습니다.

## 9. Message 출력 계약

`presentation_html_source_output`은 HTML 확인용 Message를 반환합니다. HTML이 매우 길면 UI가 느려질 수 있으므로 공유가 목적일 때는 `Report API Publisher` 경로를 권장합니다.

오류 시 성공 문구를 반환하지 않고 다음 형태로 요약합니다.

```text
HTML 프레젠테이션 생성에 실패했습니다.
- 단계: presentation_quality_gate
- 오류: 존재하지 않는 필드 sales_total을 참조했습니다.
```
