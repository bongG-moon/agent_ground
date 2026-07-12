# 04 AI Agent 설계 Prompt Template

Langflow 기본 `Prompt Template` 컴포넌트에 아래 코드블록 내용을 그대로 붙여넣습니다.

## 연결 변수

| Prompt Template 변수 | 연결할 값 |
| --- | --- |
| `business_profile_json` | `04 AI Agent 설계 프롬프트 변수 준비`의 `업무 프로필 JSON` |
| `catalog_items_json` | `04 AI Agent 설계 프롬프트 변수 준비`의 `추천 카탈로그 JSON` |
| `recommendation_trace_json` | `04 AI Agent 설계 프롬프트 변수 준비`의 `추천 근거 JSON` |
| `design_instructions` | `04 AI Agent 설계 프롬프트 변수 준비`의 `설계 지침` |
| `design_output_schema` | `04 AI Agent 설계 프롬프트 변수 준비`의 `출력 스키마 JSON` |

## 프롬프트

```text
당신은 초보 Langflow 개발자가 실제로 구현할 수 있는 AI Agent 업무 개선안을 설계하는 컨설턴트입니다.
아래 업무 프로필과 추천 카탈로그를 참고해 현재 업무와 AI Agent 적용 후 업무를 각각 연결된 Flow Chart로 설계하세요.
결과는 `flow_visualization` graph JSON이어야 하며 decision 분기의 모든 edge에 `branch_label`과 조건을 포함하세요.

[업무 프로필 JSON]
{business_profile_json}

[사용 가능한 기능/사례 카탈로그 JSON]
{catalog_items_json}

[추천 근거 Trace]
{recommendation_trace_json}

[설계 원칙]
{design_instructions}

[반환 JSON 스키마]
{design_output_schema}
```

## 결과에 반드시 포함되어야 하는 핵심 구조

- `flow_visualization.before.nodes/edges`: 현재 업무의 연결된 graph
- `flow_visualization.after.nodes/edges`: Agent 적용 후 연결된 graph
- `flow_visualization.change_map`: 같은 `comparison_key`를 기준으로 한 변경 비교
- `improvement_details`: AFTER 변경 node의 버튼에서 열 상세 설명
- `recommended_capabilities`: 제공된 카탈로그 ID에 근거한 추천
- `implementation_roadmap`, `risk_controls`: 구현 순서와 사람 확인 지점

Decision node에서 나가는 edge에는 `branch_label`과 `condition`이 반드시 있어야 합니다. 분기가 합쳐지면 `merge` node를 사용합니다. LLM은 HTML이나 좌표를 만들지 않으며, 다음 Standalone Component가 graph를 검증하고 고정 HTML Renderer가 Flow Chart를 그립니다.
