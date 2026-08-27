# 구현 데이터 계약

이 문서는 `TECHNICAL_SPECIFICATION.md`의 목표 설계를 현재 코드가 실제로 주고받는 계약에 맞춰 요약한 운영용 문서다. 예시는 설명을 위한 축약본이며, 최종 검증 기준은 `schemas/`, 각 Standalone Component의 입력 정의, 그리고 이 문서 순서다.

## 1. 공통 결과 Envelope

Custom Component의 구조화 출력은 가능한 한 아래 형태를 유지한다.

```json
{
  "ok": true,
  "status": "READY_FOR_REVIEW",
  "artifact_refs": [{"kind": "work_definition", "id": "wd-...", "revision": 2}],
  "trace_id": "trace-...",
  "error": null
}
```

- `ok=false`이면 `status=BLOCKED`이고 `error.code`, `error.message`, `error.retryable`, `error.details`를 제공한다.
- 오류가 발생한 경우 이전 단계의 정상 payload를 성공처럼 전달하지 않는다.
- `tenant_id`, `owner_id`, `session_id`, `work_definition_id`, `revision`은 식별·권한·동시성 필드다. LLM이 생성하거나 변경할 수 없다.
- credential/카탈로그 restricted 원문은 일반 output, report, retrieval trace에 포함하지 않는다. WorkDefinition provenance의 정상 업무 원문은 restricted 내부 계약(`source_requests`)으로만 전달·저장하며 tenant+owner ACL, 암호화, audit, retention/delete 통제를 적용한다.

## 2. WorkDefinition 계약

생성·검증·저장은 `10`~`18`, clarification 분기와 join은 `27`~`28`, 실행 중 상태 저장은 `34`, 결과 fail-closed 분기는 `35`, Playground command 검증·분기는 `36`, JSON Schema는 `schemas/work_definition.schema.json`이다.

주요 root 필드:

| 필드 | 의미 | 변경 권한 |
| --- | --- | --- |
| `work_definition_id` | tenant 안에서 업무 정의를 식별하는 불변 ID | Component 10 |
| `tenant_id`, `owner_id`, `session_id` | 권한 및 대화 범위 | 신뢰된 호출자 입력 |
| `channel_mode` | `native_hitl` 또는 `playground` | 최초 생성 시 고정 |
| `revision` | MongoDB CAS 기준 정수 | Store/Answer Merger |
| `status` | 업무 정의 상태 머신 값 | 검증된 전이만 허용 |
| `goal`, `trigger`, `sla`, `success_criteria` | 단일 의미 사실 | Normalizer/Merger |
| `actors`, `inputs`, `outputs`, `steps`, `decisions` 등 | 구조화 목록 | Normalizer/Merger |
| `preview_hash` | 승인 화면의 정규화된 의미 내용 hash | Component 17 |
| `approved_hash` | 승인한 `preview_hash` | Component 18의 `approve` command만 |

Component 10은 `request_text`와 `additional_prompt`를 분리해 provenance 원문으로 보존하되, credential assignment, bearer/basic token, JWT, private key, credential URL은 저장 전에 `WORK_REQUEST_SECRET_MATERIAL_DETECTED`로 차단하고 값은 오류에 포함하지 않는다. 저장된 `source_requests`는 tenant+owner ACL, encryption at rest/KMS, audit, retention/delete/legal-hold가 필요한 restricted 업무 원문이다. Component 20의 승인 projection은 이를 포함하지 않으며 검색·embedding·report로 전달하지 않는다.

사실 필드는 값을 provenance와 함께 저장한다.

```json
{
  "value": "매주 월요일 오전 9시",
  "status": "confirmed",
  "confidence": 1.0,
  "evidence_turn_ids": ["turn-2"],
  "last_updated_revision": 2
}
```

단일 사실은 위 root shape를 사용하고, 목록 사실의 각 item은 `provenance` 객체 안에 같은 provenance 필드를 둔다. `status`는 `confirmed`, `inferred`, `unknown`, `conflicting` 중 하나다. 모델 추출은 스스로 `confirmed`를 만들 수 없고, 사용자 답변 병합 또는 명시적 승인 규칙만 확정 상태로 올린다.

## 3. Clarification 계약

Component 12의 완전성 결과는 현재 revision에 대한 blocking gap 목록이다. Component 13은 회차마다 최대 세 개의 질문을 선택하여 다음 batch를 만들고, Component 27이 clarification/review/blocked 경로를 정확히 하나만 연다. 사람에게 묻는 회차는 최대 세 번이며 round 4는 새 질문을 만들지 않는 최종 gate다.

```json
{
  "schema_version": "clarification-question-batch/v1",
  "batch_id": "qb-...",
  "work_definition_id": "wd-...",
  "tenant_id": "tenant-a",
  "owner_id": "user-a",
  "session_id": "session-a",
  "channel_mode": "native_hitl",
  "revision": 2,
  "round_number": 1,
  "status": "WAITING_ANSWER",
  "expires_at": "2030-01-01T01:00:00Z",
  "questions": [
    {
      "question_id": "q-...",
      "text": "저장 전에 담당자 확인이 필요한가요?",
      "target_paths": ["risks_controls"],
      "answer_type": "single_choice",
      "choices": ["필요", "불필요"],
      "required": true,
      "reason_code": "WRITE_APPROVAL_UNKNOWN"
    }
  ]
}
```

질문 계약은 `contract_sha256`와 함께 `clarification_batches`에 immutable하게 저장된다. HITL API는 같은 문서에 workflow reference와 답변 상태만 원자적으로 부착하며 질문 본문을 바꾸지 않는다. 세 번째 답변 뒤에도 blocking gap이 남으면 round 4 gate는 `CLARIFICATION_ROUND_LIMIT`로 실패하고 네 번째 Human Input을 만들지 않는다.

답변은 `work-answer-submission/v1`이며 정확한 batch/session/revision, 모든 필수 `question_id`, idempotency key가 필요하다. `text`, `single_choice`, `single_choice_with_text`, `multi_choice`, `boolean`, `number`를 질문 계약의 타입·choice·크기·finite 숫자 제한에 맞춰 검증하며, 문자열 `"true"`를 boolean으로 추정하거나 catalog에 없는 choice를 허용하지 않는다. multi-choice는 입력 순서를 유지한 채 중복만 제거한다. Component 14가 같은 규칙으로 다시 검증한 제출만 Component 15가 병합한다.

질문 가능 기한은 immutable `answer_deadline_at`로 보존한다. 기한 안에 수락한 답변은 `submitted_at < answer_deadline_at`이어야 하며, 수락 뒤 TTL purge용 `expires_at`은 현재 구현의 7일 보존 기간으로 연장한다. 따라서 제출 직후 원래 질문 기한이 지나더라도 저장된 정상 답변이 TTL로 먼저 삭제되거나 Loader의 현재 시각 때문에 거절되지 않는다.

Component 34는 `work_runtime_states`의 runtime revision과 `work_runtime_events`의 append-only event로 `WAITING_ANSWER`, `MERGING`, `READY_FOR_REVIEW`, `WAITING_APPROVAL`, `CANCELLED`, `BLOCKED` 등 실행 상태를 기록한다. 답변 저장 뒤 새 semantic revision으로 넘어갈 때는 먼저 새 revision의 `MERGING` reconciliation checkpoint를 기록한다. `semantic_revision`은 입력 WorkDefinition의 revision을 참조할 뿐 증가시키지 않으며, owner/tenant/session/semantic revision, 허용 상태 전이, idempotency와 CAS를 검증한다. 성공 envelope에는 gate가 검증할 top-level `work_definition` deep copy를 포함한다. `success_path`만 Human Input·Answer Loader 또는 다음 의미 단계로 진행하고 `blocked_path`는 진단 경로로 끝난다.

Component 35는 F10/F11의 저장·답변 조회·병합·graph·preview·approval/action 결과 envelope를 공통으로 검사한다. `ok is True`이고 설정한 점 표기 `required_field`가 존재할 때만 검증된 원 envelope를 `success_path`로 내보낸다. `ok=false`와 구조화 `error`는 원 failure envelope를 `blocked_path`로 보존하고, `ok`가 없거나 필수 payload가 없거나 JSON envelope가 잘못된 경우에는 `RESULT_ENVELOPE_INVALID` 또는 `RESULT_REQUIRED_FIELD_MISSING`의 canonical `BLOCKED` envelope로 정규화한다. 두 output은 group output이며 선택하지 않은 경로를 `stop`하므로 Data 객체의 truthiness만으로 실패가 다음 단계에 유입되지 않는다.

Component 36은 F11 입력을 `playground-command/v1`의 닫힌 최상위 JSON으로 파싱한다. `object_pairs_hook`로 같은 객체 안의 중복 key를 거절하고 nested command 및 command별 허용 목록 밖의 필드를 차단한다. 공개 command는 정확히 `start`, `submit_answers`, `approve`, `reject`, `cancel`이며, 검증된 command 하나의 group output만 열고 나머지는 `stop`한다. `submit_answers`는 `channel_mode=playground`, 비어 있지 않고 300자를 넘지 않는 work/batch/session identity와 `idempotency_key`, 0 이상의 정수 `expected_revision`, object 또는 array `answers`가 필요하다. `request_changes`는 F11 공개 계약이 아니다. 수정하려면 현재 session을 `cancel`하고 새로운 `start`를 사용한다.

현재 구현에는 answer deadline이 지난 suspend request를 주기적으로 종료하는 expiry sweeper가 없다. production에서는 별도 sweeper가 HITL 저장소와 Langflow pending 상태를 함께 확인해 runtime `BLOCKED` 또는 `CANCELLED`와 audit event를 기록·reconciliation해야 하며, 구현 및 실제 시간 기반 E2E 전에는 production-ready로 분류하지 않는다.

## 4. Catalog 적재 계약

Catalog pipeline은 대형 배열을 Flow edge로 넘기지 않는다. F00의 `00`→`01`→`09` 사이에는 작은 job reference만 이동한다. Component 09가 bounded companion worker를 호출하고, worker가 standalone stage `02`~`07`을 durable cursor부터 반복 실행한다. F00 Human Input은 `VALIDATED` 결과에 대한 승인/거절 결정을 기록·출력한 뒤 끝난다. trusted admin gateway가 F00 run/job/request/decision과 validation hash를 재검증하고 아래 signed claim을 worker `/activate`에 직접 전달한다. worker가 raw nonce를 내부 발급·소비해 standalone Component 08을 실행한다. Component 33은 claim이 실행 전에 준비된 별도 secured activation invocation에서만 사용하며 F00 edge에는 없다.

`catalog-activation-attestation/v1`은 최소 `tenant_id`, `actor_id`, `snapshot_id`, `job_id`, `validation_hash`, `decision=activate_snapshot`, `iat`, `exp`, 단회 `jti`를 서명 범위에 포함한다. worker는 HMAC signature, scope, clock skew, 최대 TTL과 jti를 검증한다. signing secret은 gateway와 worker 밖으로 나가지 않고, Component 33에는 short-lived signed claim만 `SecretStrInput`으로 전달한다. worker 내부 raw nonce는 Langflow edge·log·공개 응답에 포함되지 않는다.

이 저장소는 attestation verifier만 제공하며 사내 SSO/관리자 decision을 권위 있게 확인하는 issuer endpoint는 포함하지 않는다. gateway 연동 전 F00 승인 결과는 activation handoff일 뿐 `catalog_active_pointers` 전환 완료가 아니다.

activation 재시도는 실패 지점에 따라 구분한다. pointer 전환 전 내부 nonce/evidence가 사라졌다면 trusted gateway가 결정을 다시 검증하고 새 attestation JTI와 새 idempotency key를 사용한다. pointer 전환 뒤 응답만 유실된 동일 idempotency replay는 worker가 active pointer를 권위 상태로 삼아 snapshot, parent/chunk asset, job, approval projection을 `ACTIVE`로 reconciliation하고 같은 결과를 반환한다.

주요 저장 단위:

| 저장소 | 역할 |
| --- | --- |
| restricted GridFS `catalog_source_files_blob` | 업로드 byte 원본 보존 |
| `catalog_sources` | 원본 hash, 크기, uploader, retention metadata |
| `catalog_ingest_jobs` | stage, cursor, count, 오류, heartbeat |
| `catalog_ingest_staging` | parser가 만든 bounded staging record |
| `catalog_assets` | redaction·정규화된 parent 자산 metadata |
| `catalog_asset_chunks` | lexical text, vector, ACL, embedding contract를 가진 검색 단위 |
| `catalog_snapshots` | immutable snapshot manifest와 검증 상태 |
| `catalog_active_pointers` | tenant별 활성 snapshot 및 embedding contract |
| `catalog_activation_approvals` | 서버가 발급한 단회 activation 승인 증거 |
| `catalog_worker_leases` | tenant/job별 단일 worker 실행 lease와 만료 |
| `work_runtime_states` | 의미 revision과 분리된 최신 workflow runtime 상태 |
| `work_runtime_events` | runtime 상태 전이의 append-only audit event |

`catalog_active_pointers.embedding_contract`와 query vector의 아래 세 필드는 반드시 일치해야 한다.

```json
{"model": "approved-embedding-model", "version": "2026-08", "dimension": 1024}
```

불일치·누락 시 hybrid retrieval은 vector 검색을 생략해 lexical 결과로 조용히 대체하지 않고 실패한다.

## 5. Hybrid Search 계약

Component 20은 승인된 WorkDefinition의 의미 필드를 Component 17과 같은 canonical 규칙으로 다시 hash하고 `approved_hash`와 constant-time 비교한다. 승인 뒤 목표·절차·의사결정·위험·입출력 등이 바뀌면 `WORK_DEFINITION_APPROVAL_HASH_MISMATCH`로 차단한다. 정상 hash로 재승인됐더라도 의미 projection 안에 credential literal 또는 secret-bearing 값이 있으면 `WORK_DEFINITION_SECRET_MATERIAL_DETECTED`로 차단한다. `source_requests`, extension, 처리 batch, UI·시간·trace 필드는 design scope로 전달하지 않고 승인된 의미 projection과 업무 identity만 사용한다. 그 결과를 tenant/ACL, 활성 snapshot과 별도의 추가 설계 프롬프트에 결합하여 변경 불가능한 `design_scope`, `design_scope_sha256`, `query_plan_sha256`을 만든다. 승인 Skill context는 추가 설계 프롬프트 입력으로 재사용하지 않는다. Component 19는 exact lower-case status와 prompt secret gate를 포함한 registry 계약을 적용한다. Component 19/23은 design scope canonical hash를 재계산하고 Component 21은 query plan canonical hash를 재계산한다. Component 29는 plan의 모든 `query_id`에 정확히 한 개의 finite vector를 제공하고 두 hash를 vector 결과에 보존한다.

```json
{
  "schema_version": "query-vectors/v1",
  "vectors": {"q-1": [0.01, 0.02]},
  "embedding_contract": {"model": "m", "version": "v1", "dimension": 2},
  "provider_receipts": [{"query_id": "q-1", "provider_mode": "http_json"}]
}
```

Component 21은 다음을 검색 전에 고정한다.

- tenant, active snapshot, ACL subject/group
- query ID의 exact coverage
- embedding model/version/dimension
- provider mode: `native_rank_fusion`, `native_score_fusion`, `application_rrf` 중 하나
- Query Planner가 만든 design scope/lock과 downstream payload의 일치
- query vector 결과의 `design_scope_sha256`/`query_plan_sha256`와 query plan의 두 lock 일치

각 vector query는 독립 후보 source로 실행한다. 결과 trace는 실제 기여한 query ID와 exact/lexical/vector/relation match source만 기록하며, 실행하지 않았거나 후보에 기여하지 않은 query를 근거로 표시하지 않는다. top-level `retrieval_trace`는 `tenant_id`, `snapshot_id`, `work_definition_id`, 정수 `work_definition_revision`, `approved_hash`, `design_scope_sha256`, `query_plan_sha256`, `candidate_allowlist_sha256`를 필수 provenance lock으로 갖는다. 검색 결과의 자산 ID와 버전은 MongoDB 결과에서만 오며, LLM은 새 catalog identity를 만들 수 없다. Component 22는 후보별 `asset_id`, `version`, `asset_type`, `technical_contract_status`, canonical `port_contract_sha256` projection과 그 전체 SHA-256을 만든다. 따라서 port의 type/cardinality/semantic role/secret/permission/network zone을 바꾸고 기존 hash를 재사용할 수 없다. Component 23은 full candidate의 port hash를 다시 계산하고 allowlist hash를 Blueprint에 봉인하며, Component 30/31 report boundary도 catalog node의 exact asset/port binding과 provenance lock을 재검증한다.

exact lane은 Unicode NFKC·casefold·공백 정규화된 title/alias와 asset identity를 `catalog_assets` parent에서 직접 조회하고, 한 자산의 여러 chunk가 exact 순위를 독점하지 못하게 한다. lexical/vector 후보의 matched chunk와 score trace는 보존하되 최종 README, technical contract, ports, relations, popularity는 동일 tenant/snapshot/ACL filter로 다시 조회한 authoritative parent에서 병합한다. parent가 없으면 해당 후보는 제거하고 trace에 누락 수를 기록한다. popularity와 updated time은 fused relevance가 같은 후보 사이의 tie-breaker로만 사용한다.

## 6. AgentBlueprint 계약

JSON Schema는 `schemas/agent_blueprint.schema.json`, 생성·검증은 `23`~`26`이다.

각 node의 `implementation_source`는 다음 중 하나다.

- `builtin`
- `catalog_component`
- `catalog_flow`
- `new_standalone_component`
- `companion_service`
- `human_task`

Catalog 자산은 반드시 retrieval allowlist의 `asset_id`, `version`, `asset_type`, `technical_contract_status`, `port_contract_sha256`를 그대로 사용한다. Component 23은 LLM draft의 `asset_ref`나 승인 Skill object 전체를 복사하지 않고 각각 닫힌 권위 projection으로 재구성해 추가 필드가 Blueprint에 유입되지 않게 한다. 신규 Custom Component node는 `generation_request_ref`를 가지며 Component 26이 복사 가능한 생성 요청 프롬프트를 만든다. Component 26의 bulk boundary는 신규 Custom node가 0개인 경우까지 `terminal_contract=true`로 봉인한다. 이 terminal stage에서는 scope/query/allowlist hash와 generation request registry가 필수이며, 각 신규 Custom node와 요청은 정확히 1:1이어야 한다.

`build_readiness=import_ready`는 모든 필수 자산 source, port, edge handle, secret/permission, Langflow 1.11.1 import가 검증된 경우에만 허용한다. metadata 설명만 있는 추천은 `design_only` 또는 `proposed_unverified`다. root `readiness_assessment`는 `status_axis=build_readiness`, 현재 readiness, 결정론적으로 계산한 `blockers`/`import_requirements`를 함께 보존한다. Component 30은 이 입력 문자열을 그대로 표시하지 않고 non-empty graph, node source/runtime 상태, port/edge 계약과 blocker를 다시 계산해 root readiness/assessment가 과장되지 않았는지 검증한다.

## 7. Report 계약

Component 30은 성공한 F10/F20 envelope를 명시적으로 unwrap하고 실패 envelope를 거부한 뒤, `work-definition/v1`/`agent-blueprint.v1`, tenant, work identity/revision, 실제 재계산한 승인 semantic hash, retrieval provenance lock, 재계산한 readiness가 모두 일치하는 계약만 `report_view_model.v1`로 투영한다. 신규 Custom의 generation request도 ref/target/template/prompt hash를 다시 검증한다. immutable `report_id`는 `report_id` 자신을 제외한 전체 canonical view model을 hash한다. 따라서 work identity/revision/approved hash, catalog snapshot, 전체 AgentBlueprint, retrieval trace, title, section, `business-report-renderer.v1` 계약 중 하나라도 달라지면 별도 artifact가 된다. 같은 입력은 시간에 의존하는 필드 없이 동일 view model·HTML·content hash를 만든다. Component 31은 닫힌 view-model shape, secret 미노출, renderer version과 canonical `report_id`를 다시 검사한 후 JSON을 escape하고 자체 CSS/JS/SVG renderer로 HTML을 만든다. LLM이 HTML/JavaScript를 작성하지 않는다. graph의 `groups` metadata는 view model에 보존하지만 현재 renderer는 group overlay·접기/펼치기를 구현하지 않으며, Skill은 node badge와 detail drawer에서 확인한다.

Renderer 결과의 핵심 필드:

```json
{
  "ok": true,
  "status": "RENDERED",
  "report_id": "report-...",
  "renderer_version": "business-report-renderer.v1",
  "html": "<!doctype html>...",
  "content_sha256": "sha256:...",
  "script_csp_hash": "sha256-...",
  "style_csp_hash": "sha256-...",
  "byte_count": 123456,
  "allowed_hosts": ["localhost"],
  "accessibility_summary": {"keyboard_node_selection": true}
}
```

Component 32와 Report API는 `content_sha256`을 다시 계산한다. 게시 요청은 tenant/actor bearer context와 `Idempotency-Key`가 필요하며 같은 key에 다른 HTML을 재사용하면 409다. 생성 API와 header-auth 열람 경로는 기존 인증을 유지하고 header-auth view/download/metadata는 생성 actor 본인에게만 404 fail-closed로 허용한다. 생성 응답의 `view_url`과 `download_url`에는 브라우저가 직접 사용할 수 있는 purpose별 `report-capability/v1` query가 포함된다. claim은 `tenant_id`, actor의 secret-HMAC binding, `report_id`, `content_sha256`, `purpose=view|download`, `iat`, `exp`, `jti`를 서명 범위에 포함하고 TTL은 60~3600초다. signed link 요청은 Authorization/tenant/actor header와 혼용할 수 없고, 저장 report의 tenant/actor/content hash와 다시 일치해야 한다. capability는 만료 전 replay 가능한 bearer credential이므로 Uvicorn/reverse proxy access log에서 query를 suppression 또는 redaction하고 chat trace·analytics·외부 referrer에도 남기지 않는다. 응답은 `Referrer-Policy: no-referrer`, `Cache-Control: private, no-store`를 사용한다.

`REPORT_RETENTION_DAYS`는 현재 `report_idempotency` reservation의 TTL만 설정한다. `reports` metadata와 GridFS HTML blob의 보존·hold·삭제는 별도 lifecycle sweeper가 함께 처리해야 한다.

## 8. Standalone source 계약

`components/*/[0-9][0-9]_*.py` 37개 각각은 다음 조건을 만족한다.

- `from lfx.custom import Component` 기반 Component subclass가 정확히 하나다.
- 형제 파일, 프로젝트 package, 상대 import, `sys.path` 조작을 사용하지 않는다.
- 파일 안의 helper·상수만 사용한다.
- Flow에 배치된 source는 byte 전체가 해당 Flow JSON node template에 포함되고 SHA-256 manifest로 검증된다. Component 33처럼 별도 secured invocation용으로 현재 여섯 Flow에 미배치된 source는 standalone build/hash 검증 대상으로 남는다.
- `tests/test_standalone_contract.py`가 Langflow 1.11.1의 `build_custom_component_template` 경로로 모든 파일을 빌드한다.

AST/import guard는 저장소가 배포하는 source의 정책 위반을 잡는 정적 방어선이지 arbitrary Python의 보안 sandbox가 아니다. 운영 배포는 별도의 관리자 code review, 제한된 이미지와 OS/network 권한, secret 분리를 전제로 한다.
