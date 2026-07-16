# Hallmark·Emil 디자인/모션 정책 적용 구조

이 Flow는 디자인 품질을 Prompt 한 곳에 맡기지 않습니다. Prompt는 슬라이드 계획을 제안하고, 동일한 구조화 정책을 Normalizer·Renderer·Quality Gate가 각각 강제합니다.

## 적용 계층

| 계층 | 실제 파일/Node | 책임 |
| --- | --- | --- |
| 정책 계약 | `presentation_design_policy_builder.py` | Hallmark식 구성 원칙과 Emil식 모션 기준을 `design_policy` JSON으로 고정 |
| 계획 제안 | `presentation_plan_generator.py` | LLM에 정책 요약을 전달하고 `design_role`, `visual_weight`, `key_message`가 있는 계획 JSON만 요청 |
| 계약 강제 | `presentation_plan_normalizer.py` | 역할 기본값, 최대 요소 6개, 최대 bullet 6개, 허용 토큰과 실제 데이터 참조를 결정론적으로 보정 |
| 표현 강제 | `html_presentation_renderer.py` | LLM의 CSS/JS를 실행하지 않고 정책에 허용된 HTML/CSS/JS만 정적 템플릿으로 생성 |
| 위반 차단 | `presentation_quality_gate.py` | 정책 존재, 역할·핵심 메시지, 반복 layout, gradient, motion duration/easing, reduced-motion, pointer hover를 검사 |

## `design_policy` 핵심 계약

```json
{
  "contract_version": "presentation-design-policy-v1",
  "policy_id": "hallmark-emil-balanced-v1",
  "composition": {
    "one_message_per_slide": true,
    "max_consecutive_same_layout": 2,
    "max_content_elements": 6,
    "max_bullets": 6,
    "require_design_role": true,
    "avoid_uniform_card_grid": true,
    "allow_decorative_gradient": false
  },
  "motion": {
    "profile": "purposeful-subtle",
    "pointer_only_slide_motion": true,
    "keyboard_navigation_motion": false,
    "max_ui_duration_ms": 300,
    "button_press_duration_ms": 120,
    "slide_enter_duration_ms": 180,
    "easing": "cubic-bezier(0.23, 1, 0.32, 1)",
    "reduced_motion_required": true,
    "hover_pointer_gate_required": true
  }
}
```

## Hallmark식 구성 정책

- 사용자 brief와 회사 요구가 최우선이고, 참고 이미지의 디자인 DNA, 프로젝트 기본값 순으로 적용합니다.
- 슬라이드마다 `cover`, `framing`, `evidence`, `comparison`, `transition`, `action` 중 하나의 역할을 가집니다.
- 한 슬라이드에는 하나의 핵심 메시지만 둡니다.
- 같은 layout의 3회 연속 사용, 모든 내용을 동일 카드 격자로 만드는 패턴, 장식용 gradient를 피합니다.
- 제목·근거·메타데이터의 시각 위계를 구분하고 표지·전환·결론은 `strong`, 일반 근거는 `balanced`를 기본값으로 사용합니다.

## Emil식 모션 정책

- 키보드 탐색은 즉시 전환하고 모션을 적용하지 않습니다.
- 포인터로 이전·다음 버튼을 누른 경우에만 `transform`과 `opacity` 기반 180ms 진입 모션을 사용합니다.
- 버튼 피드백은 개별 속성만 120ms로 전환합니다. `transition: all`, `scale(0)`, `ease-in`, layout 속성 animation을 금지합니다.
- hover 스타일은 `@media (hover: hover) and (pointer: fine)` 안에서만 적용합니다.
- `prefers-reduced-motion: reduce`에서는 transition·animation·transform 효과를 제거합니다.

## Prompt가 담당하지 않는 것

LLM은 CSS duration, easing, animation 코드, 임의 색상 표현을 만들지 않습니다. 이러한 값은 Renderer가 정책에서 읽어 결정론적으로 출력합니다. 모델이 잘못된 role이나 과도한 요소를 제안해도 Normalizer가 보정하고 Quality Gate가 최종 위반을 차단합니다.
