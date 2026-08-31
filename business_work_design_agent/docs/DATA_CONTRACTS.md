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
- `tenant_id`, `owner_id`, `session_id`, `work_definition_id`, `revision`은 내부 식별·권한·동시성 필드다. LLM이 생성하거나 변경할 수 없다.
- credential/카탈로그 restricted 원문은 일반 output, report, retrieval trace에 포함하지 않는다. WorkDefinition provenance의 정상 업무 원문은 restricted 내부 계약(`source_requests`)으로만 전달·저장하며 tenant+owner ACL, 암호화, audit, retention/delete 통제를 적용한다.

## 2. WorkDefinition 계약

생성·검증·저장은 `10`~`18`이고, 현재 F10의 compact clarification/검토 경계는 `42`(Playground `node_input`/`schema` 답변 카드와 explicit skip event), `39`(native 제출/skip audit·검증·답변 반영 또는 unresolved 기록·CAS·재평가), `40`(9개 review entry 중 하나 결합), `43`(최종 승인 선택 branch 고정), `41`(모든 intentional cancel/reject/blocked event-list terminal 메시지), `44`(F20→F30 handoff gate), `45`(로컬 demo/운영 gateway 인증 context 경계)이다. `14`·`15`·`27`·`28`·`34`·`35`는 독립 검증 또는 과거 재사용을 위한 standalone source로 남아 있지만 현재 F10 Canvas에는 배치하지 않는다. Answer Form/HITL API, F11/Playground 분리 Flow와 4차 질문 회차도 현행 F10 계약에는 없다. JSON Schema는 `schemas/work_definition.schema.json`이며, 공개 업무 정의 channel은 F10의 native HITL 하나뿐이다.

주요 root 필드:

| 필드 | 의미 | 변경 권한 |
| --- | --- | --- |
| `work_definition_id` | tenant 안에서 업무 정의를 식별하는 불변 ID | Component 10 |
| `team_name`, `employee_id` | F10 화면의 팀 명·사번 입력 | Component 10이 원문 그대로 보존 |
| `tenant_id`, `owner_id`, `session_id` | 내부 권한·대화 범위 | Component 10이 공용 scope(`default`), 사번 기반 owner, Langflow 실행 session으로 생성 |
| `channel_mode` | 정확히 `native_hitl` | 최초 생성 시 고정 |
| `revision` | MongoDB CAS 기준 정수 | Store/Component 39 |
| `status` | 업무 정의 상태 머신 값 | 검증된 전이만 허용 |
| `goal`, `trigger`, `sla`, `success_criteria` | 단일 의미 사실 | Normalizer/Component 39 |
| `actors`, `inputs`, `outputs`, `steps`, `decisions` 등 | 구조화 목록 | Normalizer/Component 39 |
| `preview_hash` | 승인 화면의 정규화된 의미 내용 hash | Component 17 |
| `approved_hash` | 승인한 `preview_hash` | Component 18의 `approve` command만 |

Component 10은 시작 Text Input에서 받은 `request_text`와 `additional_prompt`를 분리해 provenance 원문으로 보존한다. 화면에는 `team_name`, `employee_id`만 입력하며 `session_id`는 Langflow graph runtime에서 자동으로 받아 native HITL pending job과 일치시킨다. 현재 `team_name`은 표시·감사 메타데이터이고 catalog tenant partition은 공용 `default`다. credential assignment, bearer/basic token, JWT, private key, credential URL은 저장 전에 `WORK_REQUEST_SECRET_MATERIAL_DETECTED`로 차단하고 값은 오류에 포함하지 않는다. 저장된 `source_requests`는 tenant+owner ACL, encryption at rest/KMS, audit, retention/delete/legal-hold가 필요한 restricted 업무 원문이다. Component 20의 승인 projection은 이를 포함하지 않으며 검색·embedding·report로 전달하지 않는다.

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

Component 12의 완전성 결과는 현재 revision에 대한 blocking gap 목록이며 `clarification_path`·`review_path`·`blocked_path` 중 하나만 연다. 각 보완 회차는 `12 → 질문 LLM → 13 → 42 → 39` 순서로 최대 세 번만 실행한다. Component 13은 1·2차에는 최대 세 개, 마지막 3차에는 최대 네 개의 질문을 선택해 batch를 MongoDB에 저장하고, 첫 질문이 필요한 경우에는 같은 identity의 revision 0 WorkDefinition을 idempotent하게 준비한다. Component 42는 `graph.request_pause`의 `kind=node_input`과 question별 `schema` field로 Playground 답변 카드를 만들고 `Submit Answers`·`추가 입력 건너뛰기`·`Cancel` 중 정확히 하나만 연다. Component 39는 답변 제출을 감사·검증·병합하고, 건너뛰기는 별도 audit과 미확정 항목으로 기록한 뒤 review로 보낸다. 사람에게 묻는 회차는 최대 세 번이며 1·2차에는 최대 세 문항, 마지막 3차에는 최대 네 문항이다. 마지막 네 번째 입력칸까지 답한 뒤에도 gap이 남으면 `CLARIFICATION_ROUND_LIMIT`으로 차단한다. `추가 입력 건너뛰기`는 이 제한과 별개인 현재 카드의 review 진입 action이며, built-in `Human Input`은 이후 최종 `Approve`/`Reject`/`Cancel` 단계 하나뿐이다.

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

질문 계약은 `contract_sha256`와 함께 `clarification_batches`에 immutable하게 저장된다. 1차 batch에서 준비하는 revision 0 WorkDefinition은 batch identity와 일치해야 하며 재시도해도 중복 생성되지 않는다. 현재 F10은 별도 Answer Form이나 HITL API를 호출하지 않는다. Component 42의 native 답변 제출은 Component 39가 같은 batch의 감사 기록으로 원자적으로 부착하고, 검증 전 `ANSWERED_PENDING_RESUME`, 검증 뒤 `RESUMED` 상태로 해석한다. 마지막 3차에는 네 번째 질문 **입력칸**을 허용하지만, 4차 질문 batch는 만들지 않는다.

Component 42의 답변 제출 출력은 `native-clarification-answer-submission/v1`이다. 이 출력에는 `batch_id`, `work_definition_id`, `tenant_id`, `owner_id`, `session_id`, `channel_mode`, `revision`, `round_number`, `request_id`, `action_id`, 그리고 `{question_id, value, evidence_turn_id?}` 배열이 포함된다. question별 안전한 schema field 이름은 원래 `question_id`와 결정론적으로 다시 연결된다. Component 39는 이 native 제출을 canonical `work-answer-submission/v1` 감사 기록으로 검증·정규화하며 정확한 batch/session/revision, 모든 필수 `question_id`, idempotency를 요구한다. `text`, `single_choice`, `single_choice_with_text`, `multi_choice`, `boolean`, `number`는 질문 계약의 타입·choice·크기·finite 숫자 제한에 맞춰 검증하고, catalog에 없는 choice를 허용하지 않는다. multi-choice는 입력 순서를 유지한 채 중복만 제거한다. Component 39만 이 검증을 통과한 제출을 WorkDefinition에 병합한다.

`추가 입력 건너뛰기`는 빈 `work-answer-submission`이 아니라 Component 42가 만든 별도 `native-clarification-skip-submission/v1` event다. action ID는 `skip_additional_input`이며, 현재 card에 표시된 `question_id` 전체를 `skipped_question_ids`로 보존한다. Component 39는 동일 batch/session/revision/owner/deadline/idempotency를 검증한 뒤 `clarification_batches.skip_submission`에 `work-clarification-skip/v1` audit을 남긴다. 이어 WorkDefinition의 `clarification_skip_history`와 `unresolved`에 질문별 reason code·target path·`unknown` provenance를 기록하고 revision을 한 번 증가시킨다. 이 경로는 answer value를 만들거나 confirmed 값을 생성하지 않는다.

질문 가능 기한은 immutable `answer_deadline_at`로 보존한다. 기한 안에 수락한 답변은 `submitted_at < answer_deadline_at`이어야 하며, 수락 뒤 TTL purge용 `expires_at`은 현재 구현의 7일 보존 기간으로 연장한다. 따라서 제출 직후 원래 질문 기한이 지나더라도 저장된 정상 답변이 TTL로 먼저 삭제되거나 Component 39의 현재 시각 때문에 거절되지 않는다.

Component 39는 Component 42의 native 제출 또는 native skip event를 읽을 때 owner, tenant, session, batch contract, deadline, idempotency, target path, 현재 revision을 모두 확인하고 MongoDB CAS로 반영한다. 답변 제출은 재평가 뒤 다음 질문·검토·취소·차단 중 하나를 고르지만, 명시적 skip은 existing WorkDefinition과 기록한 `unresolved`만으로 `READY_FOR_REVIEW`/`review_path`를 연다. 따라서 skip은 `CANCELLED`도, 누락 정보를 채운 성공 제출도, 네 번째 보완 회차도 아니다. Component 40은 초기 검토, 1~3차 질문/답변 뒤 검토와 skip 뒤 검토로 열린 entry 중 유효한 성공 결과 정확히 하나만 선택하며, 둘 이상이 열리면 fail-closed 한다. Component 16·17·18도 각각 성공/차단 group output을 직접 제공한다. 검토 단계의 Component 18 `review_and_request_approval`은 `READY_FOR_REVIEW` + Preview hash가 있는 검증본만 `WAITING_APPROVAL`로 저장하며, revision·중복 실행 방지 키는 자동 계산하고 `work_definitions`/`work_definition_events` 컬렉션은 내부 고정한다. Component 43은 최종 Human Input 선택과 함께 미선택 18 저장 branch를 조건부 제외하고, Component 41은 하나의 event-list로 취소·반려·차단 결과를 secret 없는 짧은 terminal Message로 투영한다. 따라서 compact F10은 별도 Runtime State Store·Result Gate 노드 없이도 실패 envelope를 후속 LLM, Component 42/39 보완 경로, 저장, Preview 또는 F20 Run Flow 단계로 넘기지 않는다.

Component 45는 F10의 사번 기반 실행자 값을 audit/owner hint와 인증 assertion으로 혼동하지 않게 하는 명시적 경계다. 기본 `local_demo_fixture`는 sample Flow를 실행할 수 있게 하지만 `authenticated_subject_verified=false`로 남고 gateway group을 받을 수 없다. 운영에서는 `trusted_gateway`를 선택하고 SSO/gateway가 제공한 subject/group output만 Component 45에 연결한다. Component 36은 이 sealed `f10-authentication-context/v1`만 받아 `trusted_gateway`의 verified subject 또는 명시적으로 unverified인 local demo를 구분한다. 이어 F10 최종 `APPROVED` 성공 경로에서 F20 호출 권위를 다시 조립한다. edge로 받은 승인 결과와 원 request envelope는 identity와 approval receipt 확인에만 사용하며, MongoDB `work_definitions`의 canonical 문서를 다시 읽어 schema, `status=APPROVED`, revision, `approved_hash`, owner, session을 대조하고 의미 hash를 다시 계산한다. canonical 문서와 request envelope의 `channel_mode`는 서로 같은 것만으로 충분하지 않고 둘 다 정확히 `native_hitl`이어야 한다. sealed context의 subject는 canonical owner와 정확히 일치해야 하며 bounded group 목록만 ACL projection에 포함한다. 이어 같은 tenant의 `catalog_active_pointers`와 `status=active` Skill registry를 읽고 `agent-design-invocation/v1` 하나를 만든다. 성공 `success_path`는 strict JSON `text`를 가진 Data로 나온 뒤 built-in TypeConverter를 거쳐 Langflow `Run Flow` direct mode(`tool_mode=false`)의 F20 ChatInput에 연결한다. 실패 `blocked_path`는 Component 41 terminal 경로로만 가며 child 호출은 없다.

승인 설계 invocation의 축약 계약은 다음과 같다.

```json
{
  "ok": true,
  "status": "READY_FOR_DESIGN",
  "schema_version": "agent-design-invocation/v1",
  "tenant_id": "tenant-a",
  "work_definition_id": "wd-...",
  "work_definition_revision": 3,
  "approved_hash": "sha256:...",
  "owner_id": "user-a",
  "session_id": "session-a",
  "work_definition": {"schema_version": "work-definition/v1", "status": "APPROVED"},
  "acl_context": {"subject_id": "user-a", "groups": ["team-a"]},
  "catalog_snapshot_id": "snapshot-...",
  "skill_registry": {"skills": [], "count": 0, "truncated": false, "maximum": 200},
  "design_prompt": "추가 설계 조건",
  "trust_boundary": {
    "work_definition_source": "mongodb-canonical-approved",
    "catalog_snapshot_source": "mongodb-active-pointer",
    "skill_registry_source": "mongodb-active-only",
    "authenticated_subject_verified": true
  }
}
```

이 object는 F20의 유일한 ChatInput에 JSON text로 전달된다. F20의 TypeConverter가 JSON으로 파싱하고 Component 20이 닫힌 필드, schema/status, identity, authority marker를 다시 검사한다. 사용자가 WorkDefinition·ACL·snapshot·Skill을 따로 붙여 넣거나 F20을 별도 실행하는 것은 production 계약이 아니다. F10의 `Run Flow`는 direct mode(`tool_mode=false`)이며 다른 Flow의 HTTP API를 호출하지 않는다.

현재 구현에는 answer deadline이 지난 suspend request를 주기적으로 종료하는 expiry sweeper가 없다. production에서는 별도 sweeper가 HITL 저장소와 Langflow pending 상태를 함께 확인해 runtime `BLOCKED` 또는 `CANCELLED`와 audit event를 기록·reconciliation해야 하며, 구현 및 실제 시간 기반 E2E 전에는 production-ready로 분류하지 않는다.

## 4. Catalog 적재 계약

F00은 책임이 분리된 세 Standalone Component와 built-in Embedding Model을 사용한다. `00 Catalog JSON Loader`의 사용자 입력은 JSON array, `{items:[...]}` 또는 JSONL 파일 한 개뿐이며 파일 크기·record 수·record 크기를 제한해 bounded `catalog_bundle: Data`를 만든다. 이 Loader는 `tenant_id=default`, `catalog_id=internal-assets`를 내부 상수로 넣어 이후 bundle과 MongoDB 문서의 scope로 보존한다. 따라서 tenant/catalog은 F00 Canvas에서 입력하거나 바꾸는 값이 아니다. `01 Deterministic Chunker`는 이 bundle만 받아 chunk size/overlap, asset별 chunk 수와 전체 chunk 수를 적용한 `chunk_bundle: Data`를 만든다. `02 MongoDB Catalog Vector Writer`는 chunk bundle과 built-in `Embeddings` handle만 받아 vector를 생성·검증하고 MongoDB에 게시한다. 이 세 edge payload는 닫힌 schema와 size/count 상한을 가지며 Catalog Worker·다른 Langflow Flow API를 호출하지 않는다.

Loader는 각 record의 `id`, `title`, `type`, `description`, `category`, `version`, `readme`와 선택 metadata를 정규화한다. 원본 object는 제한된 `raw_record_redacted`로 parent에 보존하고, 검색 text는 정해진 필드 순서의 canonical text로 만든다. Chunker는 canonical text를 겹침이 있는 bounded chunk로 결정론적으로 나눈다. Writer는 Canvas의 model/provider로 구성된 Embeddings handle을 청크 1개씩 순차 호출하며, 첫 호출 전에는 대기하지 않고 이후 호출 사이에는 최소 1초 interval을 적용한다. 모든 vector가 finite number인지와 실제 길이가 runtime contract v2의 `dimension`과 일치하는지 확인하고, 벡터 문서는 별도 MongoDB write batch로 모아 저장한다. F20/F90 query vector도 같은 v2 contract를 만든다. vector는 `catalog_asset_chunks.embedding.vector`에 저장하고 parent metadata는 `catalog_assets`, 활성 embedding 계약은 `catalog_active_pointers.embedding_contract`에서 읽는다.

snapshot ID는 ingest contract version, 내부 고정 tenant/catalog, 파일 hash, runtime embedding contract v2, chunk 크기·overlap·상한에서 결정론적으로 만든다. built-in Embedding Model이 provider/model/API key를 소유하고 vector를 만든다. advanced `Dimensions`는 provider가 output-size override를 의도적으로 지원할 때만 설정하고 기본값은 비워 둔다. Writer/검색 contract는 이 UI 값이 아니라 반환 vector의 실제 길이를 사용한다. generic Embeddings handle에서 model/version을 임의로 추측하지 않는다. Writer와 query batcher는 실행 runtime class, configured `available_models` identity 또는 지원된 runtime metadata에서 해석한 model ID, 첫 vector의 실제 dimension, 이 값을 묶은 SHA-256 `fingerprint`로 `embedding-runtime-contract/v2` 계약을 만들며 model ID를 해석하지 못하면 실패한다. F00/F20/F90에 설정된 provider/model은 같아야 하고 Retriever는 저장된 v2 contract와 query v2 contract 전체를 비교한다. Writer가 parent와 모든 chunk/vector upsert를 성공시키고 count/dimension을 검증한 뒤에만 같은 MongoDB 실행의 마지막 단계에서 `catalog_active_pointers`를 새 snapshot으로 바꾼다. loader, chunker, embedding 또는 write 하나라도 실패하면 이전 pointer를 유지하고 실패 결과를 반환한다. Canvas의 **테스트 실행 (저장하지 않음)**은 내부 `dry_run=true`로 provider/MongoDB를 호출하지 않으므로 `embedding_contract.state`는 `DEFERRED`, `snapshot_id`는 `null`이며 live contract를 주장하지 않는다.

`catalog_assets`는 자산별 parent 메타데이터와 redacted 원문을 저장하는 권위 원본이고, `catalog_asset_chunks`는 parent의 여러 검색 chunk와 nested `embedding.vector`를 저장하는 검색용 collection이다. `catalog_active_pointers`는 이 고정 scope에서 검증 완료된 snapshot을 가리키는 작은 게시 문서다. F20은 pointer가 가리키는 snapshot만 검색하므로, 작성 중이거나 부분 실패한 parent/chunk는 검색 결과에 섞이지 않는다.

주요 저장 단위:

| 저장소 | 역할 |
| --- | --- |
| `catalog_assets` | redaction·정규화된 parent 자산 metadata |
| `catalog_asset_chunks` | lexical text, vector, ACL, embedding contract를 가진 검색 단위 |
| `catalog_active_pointers` | tenant별 활성 snapshot 및 embedding contract |
| `work_runtime_states` | 의미 revision과 분리된 최신 workflow runtime 상태 |
| `work_runtime_events` | runtime 상태 전이의 append-only audit event |

`catalog_active_pointers.embedding_contract`와 query vector의 runtime v2 계약은 아래 필드가 모두 일치해야 한다.

```json
{
  "schema_version": "embedding-runtime-contract/v2",
  "runtime_class": "package.module.EmbeddingRuntime",
  "model_id": "approved-provider/approved-model",
  "dimension": 1024,
  "fingerprint": "sha256:..."
}
```

불일치·누락 시 hybrid retrieval은 vector 검색을 생략해 lexical 결과로 조용히 대체하지 않고 실패한다.

## 5. Hybrid Search 계약

Component 20은 Component 36이 MongoDB 권위 자료로 만든 단일 `agent-design-invocation/v1`만 입력받는다. 먼저 닫힌 invocation schema, `READY_FOR_DESIGN`, tenant/work/revision/hash/owner/session, ACL, active snapshot, Skill registry와 trust-boundary marker를 검사한다. 이어 승인된 WorkDefinition의 의미 필드를 Component 17과 같은 canonical 규칙으로 다시 hash하고 `approved_hash`와 constant-time 비교한다. 승인 뒤 목표·절차·의사결정·위험·입출력 등이 바뀌면 `WORK_DEFINITION_APPROVAL_HASH_MISMATCH`로 차단한다. 정상 hash로 재승인됐더라도 의미 projection 안에 credential literal 또는 secret-bearing 값이 있으면 `WORK_DEFINITION_SECRET_MATERIAL_DETECTED`로 차단한다. `source_requests`, extension, 처리 batch, UI·시간·trace 필드는 design scope로 전달하지 않고 승인된 의미 projection과 업무 identity만 사용한다. 그 결과를 invocation의 tenant/ACL, 활성 snapshot과 별도의 추가 설계 프롬프트에 결합하여 변경 불가능한 `design_scope`, `design_scope_sha256`, `query_plan_sha256`을 만든다. 승인 Skill context는 추가 설계 프롬프트 입력으로 재사용하지 않는다. Component 19는 exact lower-case status와 prompt secret gate를 포함한 registry 계약을 적용한다. Component 19/23은 design scope canonical hash를 재계산하고 Component 21은 query plan canonical hash를 재계산한다. Component 29는 built-in Embeddings handle로 plan의 모든 `query_id`에 정확히 한 개의 finite vector와 runtime v2 contract를 제공하고 두 hash를 vector 결과에 보존한다.

```json
{
  "schema_version": "query-vectors/v1",
  "vectors": {"q-1": [0.01, 0.02]},
  "embedding_contract": {
    "schema_version": "embedding-runtime-contract/v2",
    "runtime_class": "package.module.EmbeddingRuntime",
    "model_id": "approved-provider/approved-model",
    "dimension": 2,
    "fingerprint": "sha256:..."
  }
}
```

Component 21은 다음을 검색 전에 고정한다.

- tenant, active snapshot, ACL subject/group
- query ID의 exact coverage
- runtime v2 embedding contract (`schema_version`, `runtime_class`, `model_id`, `dimension`, `fingerprint`)
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

Component 32는 Renderer가 만든 HTML을 변경하지 않고 공유 HTML Report API에 다음 request만 전송한다: `html`, `title`, `question`, `view_request`, `available_datasets`, `report_plan`, `ttl_hours`, `filename_hint`. API URL은 base URL 또는 `/reports` endpoint를 허용하며 최종 호출은 `POST {base}/reports`다. `ttl_hours`는 1~168로 제한한다. F30가 사용하는 성공 응답의 필수 필드는 절대 `http(s)` URL인 `view_url`, `download_url` 두 개이며, `report_id`, `expires_at`, `ttl_hours`, `storage`는 API가 제공할 때 함께 반환한다.

Component 32의 `dry_run=true`는 네트워크를 호출하지 않는다. URL, HTML, TTL 오류와 실제 API의 연결/HTTP/응답 형식 오류는 Component 예외가 아니라 `{ok:false,status:"PUBLISH_FAILED",error:{code,message,retryable},target_url}`로 반환되어 Chat Output에서 확인할 수 있다. API의 인증, token, 저장소 및 retention 정책은 공유 Report API의 운영 계약에 속하며 F30은 로컬 저장 fallback을 만들지 않는다.

## 8. Standalone source 계약

`components/*/[0-9][0-9]_*.py` 38개 각각은 다음 조건을 만족한다.

- `from lfx.custom import Component` 기반 Component subclass가 정확히 하나다.
- 형제 파일, 프로젝트 package, 상대 import, `sys.path` 조작을 사용하지 않는다.
- 파일 안의 helper·상수만 사용한다.
- Flow에 배치된 source는 byte 전체가 해당 Flow JSON node template에 포함되고 SHA-256 manifest로 검증된다. 현재 Flow에 미배치된 재사용 source도 standalone build/hash 검증 대상으로 남는다.
- `tests/test_standalone_contract.py`가 Langflow 1.11.1의 `build_custom_component_template` 경로로 모든 파일을 빌드한다.

AST/import guard는 저장소가 배포하는 source의 정책 위반을 잡는 정적 방어선이지 arbitrary Python의 보안 sandbox가 아니다. 운영 배포는 별도의 관리자 code review, 제한된 이미지와 OS/network 권한, secret 분리를 전제로 한다.
