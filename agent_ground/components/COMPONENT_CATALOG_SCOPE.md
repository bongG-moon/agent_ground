# Component 자격과 카탈로그 범위

## 결론

Flow JSON에 Custom Python node로 들어간다는 사실만으로 프로젝트 Component가 되지는 않는다.
Agent Ground는 다음 다섯 기준을 모두 만족하는 기능 단위만 `components/`에서 관리한다.

1. 앞뒤 Flow node의 내부 구현을 몰라도 입력과 출력을 설명할 수 있다.
2. 조회, 계산, 검증, 변환, 발행 중 하나의 목적을 끝까지 수행한다.
3. 같은 목적을 가진 다른 Flow 또는 같은 도메인의 다른 Flow에서 다시 조합할 수 있다.
4. 특정 upstream payload의 키를 옮기기만 하는 어댑터가 아니다.
5. 입력 검증 또는 실패·오류 처리 경계가 구현되어 있다.

이 감사 기준에 따라 기존 자산을 Component와 Flow 내부 node로 재분류했다. 이후 PPT 참고 이미지 프레젠테이션, 메일 첨부 처리와 DRM 문서 텍스트 추출 자산을 같은 기준으로 추가했다.

- **Component 21개**: `components/<component_id>/`에 유지
- **Flow 내부 node 30개**: `flows/<flow_id>/nodes/`로 이동

## Component 21개

### General Component 8개

특정 업무 Flow에 종속되지 않고 여러 Flow에서 직접 조합하는 공용 최소 기능이다.

1. `drm_document_text_extractor`
2. `multi_image_base64_encoder`
3. `cached_named_run_flow_tool`
4. `oracle_table_query`
5. `h_api_table_request`
6. `datalake_table_query`
7. `goodocs_table_reader`
8. `simple_api_table_request`

manifest에는 다음 값이 기록된다.

```json
{
  "asset_type": "component",
  "packaging": "standalone",
  "component_scope": "general"
}
```

### Domain Component 13개

특정 도메인의 계약을 사용하지만 그 도메인 안에서 독립적으로 재사용할 수 있는 완결 기능이다.

#### Skill 기반 Agent

1. `expense_precheck_skill_tool`
2. `leave_policy_skill_tool`
3. `meeting_action_skill_tool`

#### Enterprise Document RAG

1. `document_input_normalizer`
2. `pii_confidential_data_guard`
3. `document_chunk_index_builder`
4. `acl_evidence_retriever`
5. `retrieval_quality_gate`
6. `grounded_answer_builder`

#### HTML 리포트

1. `html_report_data_profile_builder`
2. `html_template_renderer`
3. `report_api_publisher`

#### HTML 프레젠테이션

1. `html_presentation_renderer`

Domain Component manifest의 `component_scope`은 `domain`이다. `owner_usage`에는 관리 책임 Flow와 실제 사용 Flow를 함께 기록한다.

## Flow 내부 node 30개

내부 node도 Langflow에 import할 때는 Standalone Python일 수 있다. 그러나 특정 Flow의 payload 전달, prompt 변수 준비, 종단 출력처럼 Flow 구현에 종속되므로 Component Library 자산으로 발행하지 않는다.

| 소유 Flow | 내부 node 수 | 저장 위치 |
| --- | ---: | --- |
| `reusable_data_flow` | 12 | `flows/reusable_data_flow/nodes/` |
| `html_report_flow` | 6 | `flows/html_report_flow/nodes/` |
| `enterprise_document_rag_flow` | 3 | `flows/enterprise_document_rag_flow/nodes/` |
| `skill_based_agent_flow` | 1 | `flows/skill_based_agent_flow/nodes/` |
| `mail_attachment_summary_flow` | 1 | `flows/mail_attachment_summary_flow/nodes/` |
| `ppt_reference_html_flow` | 7 | `flows/ppt_reference_html_flow/nodes/` |
| `drm_document_text_extraction_flow` | 0 | 공용 Component만 사용 |

각 Flow는 `internal_nodes.json`에서 내부 node의 ID, class, 버전, 소스 경로와 실제로 embed된 Flow JSON을 관리한다.

```json
{
  "asset_type": "flow_node",
  "owner_flow": "html_report_flow",
  "flow_id": "html_report_flow",
  "nodes": [
    {
      "id": "html_source_output",
      "asset_type": "flow_node",
      "owner_flow": "html_report_flow",
      "source_path": "nodes/html_source_output.py"
    }
  ]
}
```

내부 node는 `component_refs.json`과 `registry/capabilities.json`에 넣지 않는다.

## Manifest 자격 기록

모든 Component manifest는 다음 정보를 포함한다.

- `packaging: standalone`: Python 파일 하나로 Custom Component에 등록 가능
- `component_scope: general | domain`: 공용 또는 도메인 기능 범위
- `qualification`: Component 자격 기준과 판정
- `owner_usage`: 관리 책임과 실제 사용 Flow

`qualification.criteria`는 독립 입출력, 기능 완결성, 재사용 단위, payload 결합도, 검증·오류 처리를 명시한다. 새 ID는 생성기의 명시적 정책 목록에 먼저 등록하지 않으면 manifest를 만들 수 없다. 알 수 없는 ID를 HTML Report Component로 간주하던 기존 fallback은 허용하지 않는다.

## Registry 발행 규칙

Registry에는 다음 28개 자산만 들어간다.

- 자격을 통과한 Component 21개
- 최상위 Flow manifest 7개

Flow 내부 node 30개는 Registry 추천 자산이 아니다. Business Agent Design은 Registry에 있으면서 사용자 승인까지 끝난 `approved` 자산만 추천 대상으로 사용해야 한다.

## 변경 절차

새 Python node를 추가할 때는 먼저 어느 쪽인지 결정한다.

```text
독립적인 기능 계약과 재사용 목적이 있다
→ 자격 검토
→ components/<id>/
→ manifest 정책에 ID, scope, owner_usage를 명시적으로 등록

특정 Flow의 payload·prompt·분기·출력 배선이다
→ flows/<flow_id>/nodes/<id>.py
→ internal_nodes.json에 등록
→ Registry에서는 제외
```

분류를 바꿀 때는 Python 파일만 옮기지 않는다. `component_refs.json`, `internal_nodes.json`, Flow 생성기, 테스트, 포털 생성기와 Registry를 함께 갱신하고 `scripts/validate_project.py`를 통과해야 한다.
