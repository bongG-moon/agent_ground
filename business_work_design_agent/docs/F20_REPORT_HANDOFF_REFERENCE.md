# F20 → F30 Report Handoff JSON 필드 가이드

## 무엇을 위한 파일인가

[`f20_report_handoff.json`](../samples/f20_report_handoff.json)은 F20이 만든 Agent 설계 결과를 F30 반응형 보고서로 전달하는 **단일 sealed handoff**입니다. F30은 일반 대화형 Flow가 아니며, 승인된 업무 정의·최종 Agent Blueprint·카탈로그 검색 근거를 각각 따로 받지도 않습니다.

정상 경로에서는 F10의 최종 승인 뒤 F20과 F30이 자동으로 연결되므로 사용자가 이 JSON을 복사하거나 수정할 일이 없습니다. F30만 단독으로 시험할 때에만 이 파일의 **전체 JSON object**를 F30의 유일한 `Chat Input`에 그대로 넣습니다.

```text
F10 최종 승인
  → F20: 업무 정의 + 검색 근거 + Agent Blueprint 생성
  → 38 F20 Report Handoff Builder
  → f20-report-handoff/v1 JSON 하나
  → F30 Chat Input → Type Convert(JSON) → 33 Handoff Loader
  → 30 View Model → 31 HTML Renderer → 32 Publisher → Chat Output
```

> **중요:** `handoff_sha256`은 canonical JSON의 일관성을 확인하는 SHA-256 hash입니다. 접근 토큰, 비밀번호, 전자서명, 외부 위변조 방지 장치 자체는 아닙니다. F30 내에서 서로 다른 실행의 산출물을 섞거나 임의 편집한 값을 차단하기 위한 계약입니다.

## F30에 넣는 방법

1. F30을 단독 테스트할 때만 [`f20_report_handoff.json`](../samples/f20_report_handoff.json)을 엽니다.
2. `{`부터 마지막 `}`까지 **전체 object만** 복사합니다.
3. F30의 하나뿐인 `Chat Input`에 붙여 넣고 실행합니다.

아래 방식은 정상 입력이 아닙니다.

- JSON 코드 블록 표시(````json`)나 설명 문장을 함께 붙여 넣기
- `work_definition`, `agent_blueprint`, `retrieval_trace` 중 일부만 넣기
- 다른 F20 실행의 값을 섞어 새 JSON을 만들기
- 제목, 업무 문장, hash, revision, asset 목록을 수동으로 수정하기

수정이 필요하면 F10에서 업무를 수정·승인한 뒤 F20을 다시 실행해 새 handoff를 만듭니다. 실제 테스트 절차와 Publisher 설정은 [F30 반응형 Report 테스트 가이드](F30_REPORT_TEST_GUIDE.md)를 따릅니다.

## 최상위 12개 키

F30 `33 F30 Report Handoff Loader`는 최상위 키가 아래 12개와 **정확히 일치하는지** 검사합니다. 하나라도 없거나 별도 키를 더해도 차단됩니다.

| 키 | 샘플 값/형식 | 의미 | F30 검증·사용처 |
| --- | --- | --- | --- |
| `ok` | `true` | F20 handoff 생성 성공 여부 | 반드시 `true`여야 합니다. |
| `status` | `"COMPLETED"` | F20 완료 상태 | 반드시 `COMPLETED`여야 합니다. |
| `schema_version` | `"f20-report-handoff/v1"` | F20→F30 전달 계약 버전 | 정확히 이 버전이어야 합니다. |
| `work_definition` | object | F10에서 승인된 업무 정의 전체 | AS-IS 절차, 목표, 입력/출력, 리스크를 보고서에 표시합니다. |
| `agent_blueprint` | object | F20의 최종 Agent 설계 결과 envelope | TO-BE 노드·연결선, 재사용 자산, Skill, 신규 Component 요청을 표시합니다. |
| `retrieval_trace` | object | 하이브리드 검색 및 허용된 카탈로그 후보의 근거 | 추천 근거와 snapshot/allowlist를 보고서에 표시·검증합니다. |
| `execution_context` | object | 업무 정의와 실행 주체를 묶는 최소 식별 문맥 | 내부 identity/revision/hash 교차 검증에 사용됩니다. |
| `design_scope_sha256` | `sha256:<64 hex>` | 승인된 업무를 F20 설계 범위로 고정한 fingerprint | Blueprint와 retrieval trace가 같은 설계 범위를 사용했는지 확인합니다. |
| `query_plan_sha256` | `sha256:<64 hex>` | F20 카탈로그 검색 계획 fingerprint | Blueprint와 trace가 같은 검색 계획을 사용했는지 확인합니다. |
| `candidate_allowlist_sha256` | `sha256:<64 hex>` | 실제 설계에서 참조 가능한 자산 후보 목록 fingerprint | 임의의 자산을 설계에 끼워 넣지 못하게 합니다. |
| `handoff_sha256` | `sha256:<64 hex>` | 아래 핵심 본문의 canonical JSON hash | handoff 자체가 변경되지 않았는지 확인합니다. |
| `trace_id` | UUID 문자열 | 이 handoff 생성 실행을 추적하는 ID | 오류 분석·로그 상관관계용입니다. hash 본문에는 포함되지 않습니다. |

`handoff_sha256`의 계산 대상은 다음 여덟 값입니다. `ok`, `status`, `handoff_sha256`, `trace_id`는 계산 대상이 아닙니다.

```text
schema_version
work_definition
agent_blueprint
retrieval_trace
execution_context
design_scope_sha256
query_plan_sha256
candidate_allowlist_sha256
```

## `execution_context`: F30이 확인하는 최소 실행 문맥

이 object는 사용자가 별도로 입력하는 값이 아니라 F20 Builder가 승인된 WorkDefinition에서 자동으로 만듭니다. 현재 샘플은 다음 identity를 사용합니다.

```json
{
  "tenant_id": "default",
  "actor_id": "employee-demo",
  "work_definition_id": "wd-weekly-email-report",
  "work_definition_revision": 4,
  "approved_hash": "sha256:86a9c68d6acb5958adc46a74a3e3479cfe7cfd6e553082359174b42df9cd07ba"
}
```

| 키 | 의미 | 반드시 일치해야 하는 값 |
| --- | --- | --- |
| `tenant_id` | 내부 논리적 업무 영역 식별자. 현재 공용 예제에서는 `default` | WorkDefinition, Blueprint, retrieval trace의 `tenant_id` |
| `actor_id` | 실행 주체 식별자. F20이 WorkDefinition의 `owner_id`에서 복사 | WorkDefinition의 `owner_id` |
| `work_definition_id` | 승인된 업무 정의 ID | WorkDefinition·Blueprint·trace의 업무 ID |
| `work_definition_revision` | 승인된 업무 정의 revision | WorkDefinition·Blueprint·trace의 revision |
| `approved_hash` | 승인된 업무 의미 전체의 canonical hash | WorkDefinition·Blueprint·trace의 `approved_hash` |

Loader는 이 다섯 값을 검증한 뒤 `handoff_sha256`을 추가한 `Report Execution Context` 출력도 제공합니다. 현재 F30 Canvas에서 `Report View Model`로 연결되는 출력은 아래 세 개이며, `report_context`는 추적·진단용으로 남아 있습니다.

```text
Approved Work Definition  ─┐
Terminal Agent Blueprint  ─┼→ 30 Business Flow Report View Model
Retrieval Trace           ─┘
```

## `work_definition`: 승인된 업무 정의

샘플의 핵심 상태는 `work_definition_id=wd-weekly-email-report`, `status=APPROVED`, `revision=4`입니다. 이 object는 F30 보고서의 업무 요약, AS-IS Flow, 단계 상세, 리스크·통제 정보의 원본입니다.

### 식별·승인 키

| 키 | 샘플/형식 | 의미 |
| --- | --- | --- |
| `schema_version` | `"work-definition/v1"` | 업무 정의 데이터 계약 버전 |
| `work_definition_id` | `"wd-weekly-email-report"` | 업무 정의의 안정적인 식별자 |
| `tenant_id` | `"default"` | 내부 논리적 업무 영역 |
| `owner_id` | `"employee-demo"` | 업무 정의 책임자/실행 주체 식별자 |
| `session_id` | 문자열 | F10의 업무 정의/HITL 생성 세션 추적값. 사용자가 F30에 따로 입력하지 않습니다. |
| `channel_mode` | `"native_hitl"` | 이 업무 정의를 보완한 채널 방식 |
| `revision` | 0 이상의 정수 | 승인본의 변경 revision |
| `status` | `"APPROVED"` | F30가 사용할 수 있는 승인 상태 |
| `approved_hash` | `sha256:<64 hex>` | 승인된 업무 의미 전체의 canonical hash |
| `preview_hash` | `sha256:<64 hex>` | 승인 전 preview와의 일치 추적용 hash |

### 업무 의미·운영 키

| 키 | 값의 성격 | 보고서에서 나타내는 내용 |
| --- | --- | --- |
| `goal` | 상태·값 object | 업무가 달성해야 할 목표 |
| `trigger` | 상태·값 object | 실행 시작 조건 또는 주기 |
| `automation_intent` | 상태·값 object | 수동/반자동/자동화 의도와 수준 |
| `frequency_volume` | 상태·값 object | 실행 빈도와 예상 건수 |
| `sla` | 상태·값 object | 초안/승인 마감 등 시간 기준 |
| `actors` | 배열 | 업무 참여자와 역할 |
| `systems` | 배열 | 사용하는 시스템 또는 외부 서비스 |
| `inputs` | 배열 | 업무에 들어오는 데이터와 형식 |
| `outputs` | 배열 | 업무가 내보내는 결과물과 소비자 |
| `steps` | 순서형 배열 | 각 업무 단계의 ID, 순서, 담당자, 능력/작업 설명 |
| `scope_in` | 배열 | 업무 범위에 포함되는 대상 |
| `scope_out` | 배열 | 명시적으로 제외되는 대상 |
| `success_criteria` | 배열 | 완료·성공 판단 기준 |
| `constraints` | 배열 | 반드시 지켜야 할 제한 조건 |
| `decisions` | 배열 | 사람 또는 시스템이 판단해야 하는 지점 |
| `exceptions` | 배열 | 예외 상황과 처리 방식 |
| `pains` | 배열 | 현재 업무의 병목·불편 |
| `risks_controls` | 배열 | 위험, 통제, 승인/차단 조건 |
| `assumptions` | 배열 | 설계에 사용한 가정 |
| `unresolved` | 배열 | 아직 확정되지 않은 질문 또는 확인 항목 |
| `as_is_graph` | object | 현재 업무의 시각화용 AS-IS 노드·연결선 |

`goal`, `trigger`, `automation_intent`, `frequency_volume`, `sla` 같은 확인형 값은 일반적으로 `status`와 `value`를 함께 가집니다. 값이 확정되지 않았거나 `unresolved`가 남아 있으면 보고서에도 그 상태가 보입니다.

### `as_is_graph`: 현재 업무 시각화

| 키 | 의미 |
| --- | --- |
| `schema_version` | 현재 업무 그래프 계약 버전(`work-graph/v1`) |
| `nodes` | 시작·업무·사람 검토·종료 등 현재 업무의 노드 배열 |
| `edges` | 노드 간 순서/분기 연결선 배열 |
| `loop_policy` | 반복이 있는 경우의 정책, 없으면 `null` |

AS-IS 노드에는 `id`, `label`, `kind`, `current_work`, `problems`, `improvement`, `actor_ref`, `step_ref`, `detail_ref`, `change_state`가 들어갑니다. AS-IS 연결선에는 `id`, `source`, `target`, `branch_label`, `condition`, `default`가 들어갑니다. F30은 이를 클릭 가능한 현재 업무 Flow로 렌더링합니다.

## `agent_blueprint`: F20의 최종 Agent 설계

이 값은 Blueprint 본문만이 아니라 F20의 **최종 결과 envelope**입니다. F30은 envelope 내부 `blueprint`만을 실제 설계 본문으로 사용하며, 최종 결과가 성공했는지도 먼저 확인합니다.

### Envelope 키

| 키 | 의미 |
| --- | --- |
| `ok` / `status` | F20 Blueprint 생성 성공 여부와 완료 상태. 정상 handoff에서는 `true` / `COMPLETED`입니다. |
| `blueprint` | 아래에서 설명하는 실제 `agent-blueprint.v1` 설계 본문 |
| `generation_requests` | 신규 Standalone Custom Component 생성 요청 배열 |
| `generation_request` | 단일 요청 표시/호환용 값. 없거나 빈 object일 수 있습니다. |
| `generation_request_count` | 신규 생성 요청 개수의 요약 값 |
| `trace_id` | F20 Blueprint 생성 실행 추적 ID |

Envelope의 `generation_requests`가 존재하면 F30 View Model은 `blueprint.generation_requests`와 같은 내용인지도 확인합니다.

### `agent_blueprint.blueprint` 키

| 분류 | 키 | 의미 |
| --- | --- | --- |
| 계약·식별 | `schema_version` | 반드시 `agent-blueprint.v1` |
| 계약·식별 | `blueprint_id` | 설계 결과의 식별자 |
| 계약·식별 | `tenant_id`, `work_definition_id`, `work_definition_revision`, `approved_hash` | 어떤 승인 업무에서 나온 설계인지 묶는 identity |
| 카탈로그 | `catalog_snapshot_id` | 설계가 참조한 활성 카탈로그 snapshot |
| 설계 방식 | `pattern`, `pattern_reason`, `roles` | Agent 구조 패턴, 선택 이유, 참여 역할 |
| TO-BE 시각화 | `nodes`, `edges`, `to_be_graph` | 구현할 Agent 노드·연결선과 보고서 시각화 구조 |
| 재사용 | `recommended_assets`, `applied_skills` | 카탈로그 재사용 후보와 실제 적용 Skill |
| 신규 개발 | `generation_requests` | 새 Standalone Component가 필요한 경우의 생성 계약·요청 프롬프트 |
| 사람·보안 | `human_gates`, `secrets_permissions` | Human 승인 지점, 필요한 secret 참조/권한 요구 사항 |
| 오류·관측 | `failure_policy`, `observability`, `tests` | 실패 처리, trace/log/metric, 검증 계획 |
| 미확정 사항 | `assumptions`, `unresolved` | 설계 가정과 남은 확인 항목 |
| 준비도 | `flow_import_verified`, `build_readiness`, `readiness_assessment` | Flow import 여부와 실제 build/연결 준비 상태 |
| 연결 검증 | `connection_validation` | 노드 포트 계약의 정적/런타임 검증 요약 |
| F20 결속 | `design_scope_sha256`, `query_plan_sha256`, `candidate_allowlist_sha256` | 승인 범위·검색 계획·허용 후보 목록과의 동일성 proof |
| 최종성 | `terminal_contract` | `true`이면 F20의 최종 Blueprint 결과라는 뜻 |

`terminal_contract=true`은 **F20 설계가 끝났음**을 뜻할 뿐, 모든 신규 Component가 이미 제작되거나 Flow import/운영 연결까지 끝났다는 뜻은 아닙니다. 예를 들어 샘플은 `build_readiness="proposed_unverified"`, `flow_import_verified=false`로 설계가 아직 구현·검증 단계임을 명확히 표시합니다.

### TO-BE `nodes`와 `edges`

각 `nodes` 항목은 아래 키로 “어떤 노드를 왜, 어떻게 구현할지”를 설명합니다.

| 키 | 의미 |
| --- | --- |
| `node_id`, `title`, `node_type` | 노드의 안정 ID, 화면 제목, 유형 |
| `responsibility` | 노드가 맡는 한 가지 책임 |
| `current_work`, `problems`, `improvement` | AS-IS 업무, 문제, 개선 방향 |
| `implementation_source`, `implementation_label` | built-in / catalog asset / 신규 생성 등 구현 출처와 표시명 |
| `reuse_decision_reason`, `asset_ref` | 재사용 여부를 결정한 이유와 선택된 카탈로그 자산 참조 |
| `port_contract_sha256`, `technical_contract_status`, `runtime_validation_status` | 자산의 포트 계약, 기술 계약 상태, 실제 runtime 검증 상태 |
| `inputs`, `outputs`, `config` | 노드 입·출력 포트와 설정값 |
| `required_secrets`, `required_permissions`, `network_zone` | 필요한 secret **참조**, 권한, 네트워크 영역. secret 값은 넣지 않습니다. |
| `timeout_policy`, `failure_policy` | 시간 제한·재시도와 실패 시 처리 규칙 |
| `applied_skills`, `generation_contract`, `tests` | 적용 Skill, 신규 Component 생성 계약, 테스트 계획 |

각 `edges` 항목은 `edge_id`, `source_node_id`, `source_port_id`, `target_node_id`, `target_port_id`, `edge_kind`, `label`, `branch_label`, `condition`, `is_default`, `default`, `mapping`, `connection_validation_status`, `validation_issues`를 가집니다. 즉, 어떤 포트에서 어떤 포트로 어떤 데이터/제어 흐름이 가는지와 정적 연결 검증 결과를 함께 보존합니다.

## `retrieval_trace`: 카탈로그 검색과 허용 후보의 근거

이 object는 F20이 어떤 카탈로그 snapshot을 대상으로 검색했고, 어떤 자산만 재사용 후보로 허용했는지 기록합니다.

| 키 | 의미 |
| --- | --- |
| `exact_used`, `lexical_used`, `vector_used` | exact/filter, 키워드, vector 검색을 실제 사용했는지 |
| `fusion` | 검색 결과를 결합한 방식. 샘플은 `weighted_rrf` |
| `silent_fallback_used` | 조용한 fallback이 발생했는지. `true`면 근거 해석 시 주의가 필요합니다. |
| `tenant_id`, `snapshot_id` | 검색 영역과 참조한 catalog snapshot |
| `work_definition_id`, `work_definition_revision`, `approved_hash` | 어떤 승인 업무를 위해 검색했는지 |
| `design_scope_sha256`, `query_plan_sha256`, `candidate_allowlist_sha256` | Blueprint/최상위 handoff와 일치해야 하는 세 fingerprint |
| `candidate_allowlist` | 재사용이 허용된 카탈로그 자산의 배열 |
| `catalog_reference_policy` | 후보 자산 참조 정책. 샘플은 `allow_candidate_allowlist` |
| `catalog_candidate_status` | 후보 탐색 결과 상태. 샘플은 `available` |
| `empty_result_reason` | 후보가 없을 때만 나타날 수 있는 이유 설명 |

`candidate_allowlist`의 각 항목은 다음 다섯 키를 가집니다.

```json
{
  "asset_id": "카탈로그 자산 ID",
  "version": "자산 버전",
  "asset_type": "component 또는 flow",
  "technical_contract_status": "자산 계약 검증 상태",
  "port_contract_sha256": "입출력 포트 계약 hash"
}
```

Blueprint가 `catalog_component` 또는 `catalog_flow`를 구현 출처로 사용한다면, 그 자산은 이 allowlist 안에 존재하고 `port_contract_sha256`도 일치해야 합니다.

## 어떤 값들이 함께 묶여 검증되는가

```text
승인 WorkDefinition
  tenant_id + owner_id + work_definition_id + revision + approved_hash
       │
       ├── execution_context
       │    actor_id (= owner_id) + 같은 업무 ID/revision/hash
       │
       ├── Terminal Blueprint
       │    같은 업무 ID/revision/hash
       │    + catalog_snapshot_id
       │    + design_scope/query_plan/candidate_allowlist hash
       │
       └── Retrieval Trace
            같은 업무 ID/revision/hash + 같은 catalog_snapshot_id
            + 같은 design_scope/query_plan/candidate_allowlist hash

위 핵심 artifact 전체
  └── handoff_sha256 → F30 Loader가 다시 계산하여 검증
```

따라서 다음과 같은 혼합은 의도적으로 실패합니다.

| 실패 유형 | 일반적인 원인 |
| --- | --- |
| `F20 report handoff fields are invalid` | 12개 최상위 키 중 누락·추가가 있음 |
| `F20 report handoff hash is invalid` | JSON 내용은 수정했지만 `handoff_sha256`은 이전 값임 |
| `WorkDefinition binding is invalid` | tenant/owner/업무 ID/revision/승인 hash가 `execution_context`와 다름 |
| `Blueprint binding is invalid` | F20 terminal Blueprint가 다른 승인 업무 또는 다른 검색 범위에서 생성됨 |
| `Retrieval Trace binding is invalid` | catalog snapshot 또는 설계·검색·allowlist fingerprint가 다름 |

## 보안 및 공유 시 유의사항

- handoff에는 업무 원문, 담당자 식별자, 업무 시스템·카탈로그 자산명, 검색 후보, Custom Component 생성 요청 프롬프트, secret **참조 이름**이 포함될 수 있으므로 내부 업무 자료로 다룹니다.
- handoff에는 실제 비밀번호, bearer token, MongoDB URI, secret 값이 들어가면 안 됩니다. 그런 값이 보이면 공유하지 말고 생성 경로를 점검해야 합니다.
- F30 Chat Input은 `should_store_message=false`이므로 F30 대화 이력에 저장하지 않도록 설정되어 있습니다. 이는 전체 운영 로그·외부 저장소까지 자동으로 보호한다는 뜻은 아닙니다.
- 외부 공유용 예시가 필요하면 실행 가능한 handoff에서 일부 필드를 가리는 대신, 비식별 업무로 F10→F20을 새로 실행해 새 demo handoff를 만듭니다. 실행 가능한 JSON을 부분 마스킹하면 hash/binding 검증 때문에 F30에서 사용할 수 없습니다.

## 관련 파일

- 실행 입력 샘플: [`samples/f20_report_handoff.json`](../samples/f20_report_handoff.json)
- F20 handoff 생성 계약: [`38_f20_report_handoff_builder.py`](../components/agent_blueprint/38_f20_report_handoff_builder.py)
- F30 handoff 검증·분리 계약: [`33_f30_report_handoff_loader.py`](../components/report/33_f30_report_handoff_loader.py)
- F30 테스트 실행 절차: [F30 반응형 Report 테스트 가이드](F30_REPORT_TEST_GUIDE.md)
