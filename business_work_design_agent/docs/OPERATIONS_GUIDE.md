# 설치·Import·운영 가이드

## 1. 지원 범위

- 검증 런타임: `langflow==1.11.1`, `langflow-base==0.11.5`, `lfx==1.11.5`
- Python: 3.11~3.13
- Custom Component: 한 파일 완결형 Standalone
- 서버: FastAPI/Uvicorn
- 운영 저장소: MongoDB. WorkDefinition audit transaction을 사용하려면 replica set 또는 transaction 지원 cluster가 필요하다.

로컬에 다른 Langflow Desktop 버전이 설치되어 있어도 이 프로젝트의 Flow JSON과 source 검증은 위 고정 버전 환경에서 수행해야 한다.

## 2. Python 환경

PowerShell 예시:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q tests
```

Flow와 bundle을 다시 생성할 때는 `scripts/build_langflow_1_11_flows.py`를 사용한다. 생성 파일을 손으로 수정하지 않는다.

## 3. 환경 변수

`.env.example`을 배포 환경의 secret store에 옮겨 설정한다. `.env` 파일이나 token을 Flow JSON에 넣지 않는다.

필수 운영값:

- `APP_ENV=production`
- `MONGODB_URI`, `MONGODB_DATABASE`
- `MONGODB_COLLECTION_PREFIX=`: 반드시 빈 값. core collection 이름을 Standalone Component와 companion service가 공유한다.
- `CATALOG_WORKER_STORAGE_MODE=mongodb`, `CATALOG_WORKER_API_BEARER_TOKEN`, 32 byte 이상 `CATALOG_APPROVAL_ATTESTATION_SECRET`
- `EMBEDDING_ENDPOINT`, `EMBEDDING_APPROVED_HOSTS`, `EMBEDDING_API_KEY`, `EMBEDDING_MODEL`, `EMBEDDING_VERSION`, `EMBEDDING_DIMENSION`
- `CATALOG_MAX_STAGE_INVOCATIONS`, `CATALOG_MAX_TOTAL_SECONDS`, `CATALOG_STAGE_TIMEOUT_SECONDS`, `CATALOG_APPROVAL_TTL_SECONDS`
- `LANGFLOW_BASE_URL`, `LANGFLOW_API_KEY`, `LANGFLOW_F10_FLOW_ID`
- `REPORT_API_BEARER_TOKEN`, 32 UTF-8 byte 이상 `REPORT_VIEW_SIGNING_SECRET`, `REPORT_VIEW_TOKEN_TTL_SECONDS`, `HITL_API_BEARER_TOKEN`
- `REPORT_PUBLIC_BASE_URL`, `REPORT_STORAGE_MODE`, `REPORT_PROCESSING_LEASE_SECONDS`, `REPORT_RETENTION_DAYS`. processing lease는 30~3600초이며 마지막 값은 idempotency TTL일 뿐 artifact lifecycle 설정이 아니다.
- 외부 게시 endpoint allowlist

비-loopback HTTP URL, URL credential/query/fragment, redirect 응답은 outbound component에서 거절한다. production에서 memory store 또는 인증 없는 local mode는 readiness 실패다.

## 4. Companion API 실행

Catalog worker:

```powershell
.\.venv\Scripts\python.exe -m uvicorn services.catalog_worker.app:create_app --factory --host 127.0.0.1 --port 8092
```

HITL API:

```powershell
.\.venv\Scripts\python.exe -m uvicorn services.hitl_form_api.app:app --host 127.0.0.1 --port 8090
```

Report API:

```powershell
.\.venv\Scripts\python.exe -m uvicorn services.report_api.app:app --host 127.0.0.1 --port 8091 --no-access-log
```

`0.0.0.0`으로 listen하도록 배포할 수는 있지만 사용자에게 제공하는 URL은 gateway의 실제 HTTPS 주소여야 한다. 세 companion service는 사내 gateway 뒤에 두고 bearer, tenant, actor header를 gateway identity에서 발급하는 구성을 권장한다. Report API 예시는 signed capability query 유출을 막기 위해 Uvicorn access log를 끈다. 운영 reverse proxy가 access log를 필요로 하면 query 전체를 제외하거나 `capability` 값을 redaction한 형식만 사용한다.

Readiness 확인:

```http
GET http://127.0.0.1:8092/healthz
GET http://127.0.0.1:8090/api/health
GET http://127.0.0.1:8091/api/health
```

F00의 Component 09 node에는 worker URL, exact host allowlist, 위 worker와 같은 bearer token, tenant ID와 actor ID를 Global Variable/secret으로 주입한다. loopback 밖에서는 HTTPS만 허용되며 redirect, URL credential/query/fragment는 거절한다. `CATALOG_APPROVAL_ATTESTATION_SECRET`은 worker와 trusted admin gateway에만 두고 Langflow Flow/node에 넣지 않는다.

이 프로젝트에는 사내 SSO/관리자 권한을 검증해 attestation을 발급하는 gateway endpoint가 포함되어 있지 않다. 배포 전 별도 gateway integration으로 F00 pending/run/job/decision 조회와 claim 발급·worker 직접 호출을 구현해야 하며, 이 연동이 없으면 catalog activation readiness는 실패로 본다.

activation 호출이 pointer 전환 전에 중단되어 worker의 내부 one-time evidence를 잃은 경우, 같은 attestation JTI나 같은 idempotency key를 재사용하지 않는다. trusted gateway가 F00 결정과 현재 validation report를 다시 확인한 뒤 새 JTI의 attestation과 새 idempotency key를 발급한다. 반대로 pointer 전환 뒤 응답만 유실된 동일 요청 replay라면 worker가 active pointer를 기준으로 snapshot/assets/chunks/job/approval projection을 재조정한 뒤 같은 활성 결과를 반환한다.

## 5. MongoDB 준비

최소 권한 service account를 나눈다.

- catalog worker 계정: restricted source/GridFS, staging/assets/chunks/snapshot, activation approval/pointer, worker lease read/write
- catalog search 계정: active pointer 및 redacted asset/chunk read only
- HITL 계정: work definition, clarification batch, runtime state/event read/write
- Report 계정: report metadata/GridFS write/read

`catalog_asset_chunks`에는 lexical Search index와 vector Search index를 같은 collection 기준으로 만든다. 실제 index 이름을 Flow의 `lexical_index_name`, `vector_index_name`과 일치시킨다. tenant, snapshot, ACL field를 filter 가능하게 설정하고 대표 질의 평가 전에는 snapshot을 활성화하지 않는다.

exact title/alias/asset-id lane은 `catalog_assets` parent collection의 `title_normalized`·`aliases_normalized`·identity를 조회한다. lexical/vector lane은 chunk collection에서 후보 근거를 만들지만 최종 추천의 README, technical contract, ports, relations, popularity는 같은 tenant/snapshot/ACL 조건으로 다시 읽은 authoritative parent metadata를 사용한다.

원본 GridFS는 일반 검색 계정에서 읽을 수 없어야 하며 encryption at rest, retention, audit 정책을 별도로 적용한다.

## 6. Langflow Import 순서

1. `flows/F00_catalog_ingestion_admin.json`을 관리자 프로젝트에 import한다.
2. `flows/F10_work_definition_parent.json`, `flows/F11_work_definition_chat_turn.json`, `flows/F20_agent_blueprint_design.json`, `flows/F30_responsive_report.json`, `flows/F90_search_evaluation.json`을 일반/검증 프로젝트에 import한다.
3. 각 Flow의 고정 placeholder를 실제 Global Variable 또는 secret으로 교체한다. F20에는 approved WorkDefinition/ACL/snapshot 외에 별도 추가 설계 프롬프트 입력을 연결한다.
4. F10의 UUID를 `LANGFLOW_F10_FLOW_ID`에 기록한다.
5. F10의 세 clarification Human Input에서 `Submit Answers`가 `branch_submit_answers`로, 최종 Human Input에서 `Approve`, `Reject`, `Cancel`이 각 Store command로 연결되는지 확인한다. Component 18의 일반 `request_changes` primitive는 현재 F10/F11 공개 경로에 연결하지 않는다.
6. F20/F30 child에는 Human Input 또는 tool approval pause가 없는지 확인한다.
7. `flows/build_manifest.json`의 source hash와 import된 Custom Component source를 대조한다.

Flow JSON은 import 가능한 orchestration skeleton이다. 사내 LLM gateway, embedding endpoint, MongoDB Search index, bearer/global variables가 설정되기 전에는 각 Flow metadata의 `configuration_required`, `structured_command_external_state_required`, `trusted_backend_only_configuration_required`, `evaluation_configuration_required` 상태를 유지하며 운영 준비 완료로 간주하지 않는다.

## 7. 카탈로그 초기 적재

1. Catalog worker의 MongoDB, component root, embedding endpoint/allowlist와 bearer 설정을 완료하고 `/healthz`를 확인한다.
2. 원본 JSON/JSONL을 F00에 업로드한다.
3. `00`의 `restricted_storage_acknowledged`를 실제 보안 저장소 확인 후에만 켠다.
4. Component 01의 secret scanner 결과를 확인한다. quarantine 결과는 worker로 진행하지 않는다.
5. Component 09가 worker에 작은 job ref를 제출한다. worker가 lease/deadline/stage timeout을 적용해 standalone `02`~`07`을 durable cursor부터 반복하고 `VALIDATED` summary를 반환하는지 확인한다.
6. validator 결과에서 record count, source hash, vector finite/dimension, embedding contract와 validation hash를 확인한다.
7. F00 최상위 Human Input은 승인/거절 결정을 기록·출력하고 종료한다. 같은 suspended run에 사후 생성 claim을 주입할 수 있다고 가정하지 않는다.
8. trusted admin gateway가 F00 run/job/request/decision, approver identity, snapshot/job/validation hash를 서버 API로 재검증한다.
9. gateway는 `catalog-activation-attestation/v1` claim에 tenant/actor/snapshot/job/validation hash, `decision=activate_snapshot`, `iat`, `exp`, 단회 `jti`를 넣어 서명하고 worker `/activate`를 직접 호출한다.
10. worker가 claim signature/scope/clock skew/TTL/jti를 검증하고 raw nonce를 내부 발급·소비해 standalone Component 08을 실행한 뒤 sanitized active pointer만 반환하는지 확인한다.

UI의 boolean이나 자유 텍스트만으로 snapshot을 활성화할 수 없다. signing secret과 raw nonce는 Langflow `Data` edge, log, public response로 전달하지 않으며 재시도는 attestation `jti`, idempotency key와 현재 active pointer를 함께 확인한다. Component 33을 사용하려면 trusted gateway가 발급한 short-lived claim이 실행 시작 전에 `SecretStrInput`으로 준비된 별도 secured activation 호출이어야 하며 F00 suspended run 뒤에 사후 주입한다고 가정하지 않는다.

## 8. F10 실행과 HITL 연결

F10은 `/api/v2/workflows` background mode로 시작한다. 이 요청을 실행하는 Langflow service account는 HITL API의 `LANGFLOW_API_KEY` 소유자와 같아야 하며, 배포 smoke test에서 다른 계정 job이 pending 조회에 섞이거나 누락되지 않는지 검증한다. suspend가 관측되면 pending 목록의 job/request/session과 Component 13의 batch를 HITL API에 등록한다. Component 34가 각 질문 전 `WAITING_ANSWER`, 제출과 새 semantic revision reconciliation의 `MERGING`, review 직전 `READY_FOR_REVIEW`, 승인 대기 `WAITING_APPROVAL`, 취소 `CANCELLED`, router 실패 `BLOCKED`를 별도 runtime collection/event에 기록하는지 확인한다. runtime persistence 실패 branch가 Human Input/Answer Loader나 다음 의미 단계에 도달하면 안 된다. Component 34 성공 결과의 top-level `work_definition`이 Component 35의 필수 field gate에 전달되어야 한다. 최초 store, loader, merger, answered store, review graph/preview/store/approval과 최종 action 결과는 Component 35의 `ok=true`·필수 payload 검사를 통과한 success path만 다음 단계에 연결되어야 한다. 세부 순서는 `HITL_STATE_MACHINE.md`를 따른다.

현재 저장소에는 suspend된 pending request와 answer deadline을 주기적으로 조회해 자동 종료하는 HITL expiry sweeper가 구현되어 있지 않다. production 배포 전에 별도 sweeper가 만료 batch/pending request를 찾아 runtime `BLOCKED` 또는 `CANCELLED`와 audit event를 기록하고, HITL 저장소와 실제 Langflow pending 상태를 reconciliation하도록 구현해야 한다. 이 sweeper와 실제 시간 흐름 E2E가 없으면 HITL production readiness는 실패다.

Component 10은 request/additional prompt에서 credential literal을 탐지하면 원문 저장 전에 차단한다. 다만 정상 업무 원문은 provenance를 위해 `source_requests`에 남으므로, production `work_definitions`/events 읽기는 인증된 tenant와 owner 또는 승인된 관리자에게만 허용하고 encryption at rest/KMS, 접근 audit, retention·삭제·legal hold 정책을 적용한다. DB backup과 운영 export에도 같은 정책을 적용하며, 일반 catalog 검색·embedding·report service account에는 raw source read 권한을 주지 않는다.

Answer Form은 질문의 `answer_type`을 그대로 렌더링하고 choice membership, 실제 boolean, finite number, 필수 응답을 client와 server 양쪽에서 검사한다. 서버가 수락한 답변은 immutable `answer_deadline_at`과 `submitted_at`으로 기한 내 제출을 판정하며, TTL purge용 `expires_at`은 현재 구현의 7일 답변 보존 기간까지 연장한다.

Playground 통합을 확인할 때 F11에는 외부 저장소에서 읽은 현재 WorkDefinition과 질문 batch, 구조화 command payload를 함께 전달한다. Component 36은 command JSON을 중복 key 검출 파서로 읽고 nested/unknown field를 거절하며 정확히 `start`, `submit_answers`, `approve`, `reject`, `cancel`만 단일 group output으로 연다. `request_changes`는 지원하지 않으므로 수정이 필요하면 현재 session을 `cancel`하고 새 `start`를 사용한다. F11 자체가 대화 상태를 영속 복원하는 것으로 간주하지 않으며, F11의 runtime/store/loader/merger/graph/preview/approval/action도 Component 34/35의 verified success path만 사용한다. 같은 WorkDefinition/session을 F10과 F11 양쪽에서 처리하지 않는다.

F20을 실행할 때 추가 설계 프롬프트는 승인 Skill context와 별도 입력으로 전달한다. Query Planner가 승인 WorkDefinition·tenant/ACL·active snapshot·추가 prompt를 `design_scope_sha256`/`query_plan_sha256`으로 고정하고, embedding 결과가 두 lock을 보존하며 Retriever가 query plan canonical hash와 vector lock을 다시 확인하는지 검사한다. top-level retrieval trace와 Component 22 context trace에는 tenant/snapshot/work definition ID·revision/approved hash/design scope/query plan hash가 모두 있어야 하며 기존 trace와 값이 다르면 중단해야 한다. Skill/Blueprint 단계도 design scope canonical hash를 재계산해야 한다.

Flow의 approved WorkDefinition, ACL, active snapshot, Skill registry node tweak는 신뢰 근거가 아니라 전달 형식이다. production 호출자는 브라우저나 임의 Langflow client가 보낸 값을 그대로 연결하면 안 된다. backend-only trusted orchestrator가 인증된 tenant/actor로 MongoDB의 현재 승인 WorkDefinition과 `approved_hash`/revision, active catalog pointer와 snapshot, identity 기반 ACL, 활성 immutable Skill registry를 다시 읽고 검증한 뒤 node tweak를 구성해야 한다. `design_scope_sha256`은 이 서버 측 입력을 봉인하지만, 공격자가 제공한 입력 자체를 권위 데이터로 승격하지 않는다.

## 9. Report 게시

F30의 Component 30→31 결과를 먼저 화면에서 확인한다. Component 30이 node/port/edge/source/runtime/secret/permission 계약으로 `build_readiness`와 blocker를 다시 계산하고 Blueprint의 `readiness_assessment`와 일치시키는지, retrieval trace의 tenant/snapshot/work/revision/approved/design/query lock이 승인 입력과 일치하는지 확인한다. 전달된 `groups` metadata는 보존되지만 현재 renderer에는 group overlay·접기/펼치기가 없으므로 이를 제공된 UI 기능으로 표시하지 않는다. Component 32는 기본 `dry_run=true`로 hash, scheme, allowed host를 검증한다. 실제 게시 시에만 dry-run을 끄고 Report API URL/token/tenant/actor/idempotency를 제공한다. header-auth view/download/metadata는 생성 actor 본인만 접근할 수 있으며 같은 tenant의 다른 actor에도 404를 반환한다.

Report API는 HTML을 immutable GridFS blob으로 저장하고 metadata를 tenant scope로 보존한다. HTML 응답에는 renderer가 산출한 script/style hash 기반 CSP가 붙는다.

Component 32가 반환하는 `view_url`/`download_url`에는 Report API가 발급한 purpose별 `report-capability/v1` query가 포함되므로 일반 브라우저에서 별도 header 없이 열 수 있다. capability는 tenant/actor/report/content hash, `view` 또는 `download`, `iat`/`exp`/`jti`에 서명되고 설정 가능한 수명은 60~3600초, 기본 900초다. 서명 위조, 만료, purpose 교차 사용, 저장 artifact의 identity/hash 불일치와 capability+Authorization/tenant/actor header 혼용이 모두 거절되는지 확인한다. 이 URL은 만료 전 반복 사용할 수 있는 단기 bearer credential이다. Uvicorn과 reverse proxy access log는 query string을 기록하지 않거나 `capability` 값을 redaction하고, chat trace·analytics·외부 referrer에도 남기지 않는다. service bearer나 signing secret을 query/browser code에 넣지 않는다.

`REPORT_RETENTION_DAYS`는 현재 `report_idempotency` reservation의 TTL만 설정한다. `reports` metadata와 GridFS HTML blob은 자동 삭제되지 않으므로, 법적 보존·hold·감사 정책을 확인하는 외부 sweeper 또는 lifecycle service가 metadata와 대응 blob을 함께 삭제하도록 운영해야 한다.

Report API는 `REPORT_PROCESSING_LEASE_SECONDS` 동안 idempotency reservation을 소유한다. 프로세스가 reservation 생성 뒤 종료되면 동일 tenant/key/request/report 조합만 만료 lease를 atomic reclaim할 수 있고, 아직 유효한 lease는 `IDEMPOTENCY_IN_PROGRESS`로 유지한다. metadata가 이미 생성된 경우 replay가 stored report를 검증하고 reservation을 `COMPLETED`로 reconciliation한다. GridFS upload와 metadata insert 사이의 hard crash가 남긴 orphan blob은 lifecycle sweeper가 함께 정리해야 한다.

## 10. 검증 명령과 합격 기준

```powershell
$env:PYTHONPATH=(Resolve-Path '.').Path
$env:PYTEST_ADDOPTS='-p no:cacheprovider'
.\.venv\Scripts\python.exe -m compileall -q components services scripts tests
.\.venv\Scripts\python.exe -m pytest -q tests
.\.venv\Scripts\python.exe scripts\build_langflow_1_11_flows.py --check
.\.venv\Scripts\python.exe scripts\validate_langflow_1_11_runtime.py
```

합격 조건:

- 모든 Custom Component source가 `build_custom_component_template`을 통과
- 로컬/상대 import, dynamic import/eval/exec 없음
- Flow node source byte와 repository source SHA-256 일치
- edge의 source/target handle이 실제 1.11.1 template에 존재
- Human Input은 F10과 F00 top-level에만 존재
- F00은 Component 09까지만 사용해 validation/decision을 출력하고, trusted gateway가 attestation을 검증·발급해 `/activate`를 직접 호출함
- signing secret/raw nonce가 Langflow edge·공개 응답에 노출되지 않고 Component 33은 별도 pre-attested 호출에서만 사용됨
- F10 runtime persistence 실패 branch가 Human Input/Answer Loader로 연결되지 않음
- F10 runtime checkpoint가 `WAITING_ANSWER`, `MERGING`, `READY_FOR_REVIEW`, `WAITING_APPROVAL`, `CANCELLED`, `BLOCKED` 전이를 기록하고 새 semantic revision의 reconciliation을 거침
- F10 credential literal이 저장 전에 차단되고 WorkDefinition raw source의 tenant/owner ACL·KMS·retention/delete가 검증됨
- F10/F11의 Component 35가 명시적 `ok=true`와 필수 payload만 success path로 보내고 원 오류 또는 canonical 오류를 blocked path로 보냄
- F11 Component 36이 duplicate/nested/unknown field를 차단하고 `start`/`submit_answers`/`approve`/`reject`/`cancel` 외 command를 열지 않음
- HITL expiry sweeper가 만료 pending request와 저장 상태를 fail-closed로 reconciliation하며 실제 시간 기반 E2E를 통과함
- F20의 추가 설계 프롬프트가 Skill context와 분리되고, trusted backend가 canonical 승인 상태/identity/ACL/snapshot/Skill registry를 읽어 node tweak를 구성하며, design scope/lock이 downstream에서 유지됨
- retrieval trace provenance가 tenant/snapshot/work/revision/approved/design/query lock에 고정되고 F30 경계에서 다시 검증됨
- F30이 `build_readiness`와 `readiness_assessment`를 입력 문자열 그대로 신뢰하지 않고 graph/contract로 다시 계산함
- report 링크가 purpose별 signed capability로 tenant/actor/report/content hash/만료에 고정되고, tamper/expiry/purpose mismatch/header 혼용이 차단되며 service bearer/signing secret이 브라우저에 노출되지 않음
- report metadata와 GridFS blob의 보존·삭제를 담당하는 외부 sweeper/lifecycle 검증 완료
- schema/reference/hash/CSP/XSS 테스트 통과
- production 설정 누락 시 readiness 실패

실제 배포 승인은 여기에 더해 live MongoDB transaction, Atlas Search, embedding provider, 사내 LLM gateway, Workflow suspend/resume, gateway 인증, 인증된 Report URL 조회와 artifact purge를 한 tenant와 교차 tenant 모두에서 E2E 검증해야 한다.
