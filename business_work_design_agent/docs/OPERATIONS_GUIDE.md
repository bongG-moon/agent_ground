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
- helper/API runtime용 `MONGODB_URI`, `MONGODB_DATABASE=business_work_design` (Flow의 Database 기본값과 동일). Langflow에는 같은 URI를 Secret Global Variable **`MONGO_URL`**로 한 번만 등록한다.
- `MONGODB_COLLECTION_PREFIX=`: 반드시 빈 값. F00, F10의 Component 36, 검색 Component가 canonical collection 이름을 공유한다.
- F00/F20/F90의 built-in `Embedding Model`에 같은 승인 provider/model과 credential을 Langflow Secret 또는 Global Variable로 설정한다. advanced `Dimensions`는 provider가 output-size override를 의도적으로 지원할 때만 설정하며, 기본값은 비워 둔다. Writer/검색 계약은 이 UI override가 아니라 live runtime의 반환 vector length를 사용한다. 별도 `EMBEDDING_ENDPOINT`, token, model/version/dimension 환경변수와 Component 29 HTTP embedding endpoint는 사용하지 않는다. live 실행은 schema version·runtime class·configured model identity·첫 vector dimension·fingerprint로 `embedding-runtime-contract/v2` 계약을 만들며 model ID 해석 실패는 readiness 실패다.
- F00·F10·F20·F90의 active MongoDB node는 export에서 이미 같은 `MONGO_URL` Secret Global Variable에 자동 연결된다. 대상은 F00 Writer 1개, F10 Component 13 (3개)·39 (3개)·18 (4개)·36 (1개), F20/F90 Retriever 각 1개이며 Database는 모두 `business_work_design`이다. collection 기본값은 내부 고정이므로 일반 사용자가 입력하지 않는다.
- `REPORT_API_BEARER_TOKEN`, 32 UTF-8 byte 이상 `REPORT_VIEW_SIGNING_SECRET`, `REPORT_VIEW_TOKEN_TTL_SECONDS`
- `REPORT_PUBLIC_BASE_URL`, `REPORT_STORAGE_MODE`, `REPORT_PROCESSING_LEASE_SECONDS`, `REPORT_RETENTION_DAYS`. processing lease는 30~3600초이며 마지막 값은 idempotency TTL일 뿐 artifact lifecycle 설정이 아니다.
- 외부 게시 endpoint allowlist

일반 Playground 실행과 Playground-native 보완 답변에는 `LANGFLOW_BASE_URL`, `LANGFLOW_API_KEY`, `LANGFLOW_F10_FLOW_ID`, `HITL_API_BEARER_TOKEN`이 필요하지 않다. 이 값들은 별도 운영 자동화 client 또는 legacy `hitl_form_api`에만 해당하며, F10 Flow의 환경 설정으로 취급하지 않는다.

비-loopback HTTP URL, URL credential/query/fragment, redirect 응답은 outbound component에서 거절한다. production에서 memory store 또는 인증 없는 local mode는 readiness 실패다.

## 4. Companion API와 native HITL

F10의 active HITL은 `42 보완 답변 HITL`이 Langflow Playground의 native `node_input` schema 카드에 직접 답변칸을 표시하는 방식이다. 따라서 F10을 사용하기 위해 별도 Answer Form 웹 서버를 실행하거나, 답변 batch를 HTTP API에 등록하거나, browser에 API key/request ID를 노출할 필요가 없다.

현재 active companion service는 Report API다.

```powershell
.\.venv\Scripts\python.exe -m uvicorn services.report_api.app:app --host 127.0.0.1 --port 8091 --no-access-log
```

`0.0.0.0`으로 listen하도록 배포할 수는 있지만 사용자에게 제공하는 URL은 gateway의 실제 HTTPS 주소여야 한다. Report API는 사내 gateway 뒤에 두고 bearer, tenant, actor header를 gateway identity에서 발급하는 구성을 권장한다. 예시는 signed capability query 유출을 막기 위해 Uvicorn access log를 끈다. 운영 reverse proxy가 access log를 필요로 하면 query 전체를 제외하거나 `capability` 값을 redaction한 형식만 사용한다.

Readiness 확인:

```http
GET http://127.0.0.1:8091/api/health
```

`services/hitl_form_api`는 **legacy/reference 전용**으로 남아 있을 수 있다. 과거 외부 Answer Form 연동을 재현하거나 마이그레이션을 확인할 때만 별도 실행하며, 현행 F10 Playground flow의 운영 prerequisite나 E2E 단계가 아니다.

F00은 Companion API를 사용하지 않는다. `02 MongoDB Catalog Vector Writer`의 URI는 자동 연결된 `MONGO_URL`을 쓰며, provider credential과 model 선택은 F00/F20/F90의 built-in `Embedding Model`에 Global Variable/secret으로 직접 설정한다. Component 29는 이 handle을 받아 query ID를 보존한 vector와 runtime v2 계약을 만들며 별도 HTTP embedding endpoint를 호출하지 않는다.

## 5. MongoDB 준비

최소 권한 service account를 나눈다.

- catalog ingest 계정: `catalog_assets`, `catalog_asset_chunks`, `catalog_active_pointers` read/write
- catalog search 계정: active pointer 및 redacted asset/chunk read only
- HITL/F10 계정: `work_definitions`, `work_definition_events`, `clarification_batches` read/write와 승인 뒤 active catalog pointer·active Skill registry read
- Report 계정: report metadata/GridFS write/read

`catalog_asset_chunks`에는 lexical Search index와 vector Search index를 같은 collection 기준으로 만든다. 실제 index 이름을 Flow의 `lexical_index_name`, `vector_index_name`과 일치시킨다. vector index dimension은 F00의 live 실행이 active pointer에 기록한 runtime v2 `embedding_contract.dimension`과 같아야 한다. tenant, snapshot, ACL field를 filter 가능하게 설정하고 대표 질의 평가 전에는 snapshot을 활성화하지 않는다.

F00의 세 catalog collection은 이름만 나눈 중복 저장소가 아니다.

| Collection | 저장 대상 | 사용하는 단계 |
| --- | --- | --- |
| `catalog_assets` | 자산당 한 건의 parent 메타데이터, redacted 원문, 기술 계약 | exact title/alias 조회와 후보의 최종 상세 정보 재확인 |
| `catalog_asset_chunks` | parent를 나눈 여러 검색 chunk, `lexical_text_redacted`, `embedding.vector` | lexical/vector Search 후보 생성 |
| `catalog_active_pointers` | 검증을 끝낸 active snapshot ID와 vector 계약 | F20이 현재 검색해도 되는 snapshot 결정 |

같은 자산에 대해 parent와 vector chunk를 분리하므로 큰 vector/긴 본문을 exact lookup에 실어 나르지 않는다. pointer는 모든 parent/chunk/vector write와 count 검증이 끝난 뒤에만 바뀌므로, 중간 적재 실패 시 이전 snapshot을 계속 검색한다.

exact title/alias/asset-id lane은 `catalog_assets` parent collection의 `title_normalized`·`aliases_normalized`·identity를 조회한다. lexical/vector lane은 chunk collection에서 후보 근거를 만들지만 최종 추천의 README, technical contract, ports, relations, popularity는 같은 tenant/snapshot/ACL 조건으로 다시 읽은 authoritative parent metadata를 사용한다.

원본 record는 `catalog_assets.raw_record_redacted`에 저장하고 일반 검색 projection에서는 제외한다. catalog ingest 계정과 별도 승인된 운영자만 읽도록 encryption at rest, retention, audit 정책을 적용한다.

## 6. Langflow Import 순서

1. `flows/F00_catalog_file_vector_ingest.json`을 관리자 프로젝트에 import한다.
2. 일반/검증 프로젝트에는 child인 `flows/F20_agent_blueprint_design.json`을 먼저 import하고, `flows/F30_responsive_report.json`, `flows/F90_search_evaluation.json`을 import한다.
3. 같은 project/folder에 `flows/F10_work_definition_parent.json`을 import한다. F10의 Run Flow node가 export에 고정된 F20 UUID와 이름을 선택하고, 동적 입력·출력 port가 표시되는지 확인한다.
4. Langflow Settings에 Secret Global Variable `MONGO_URL`이 있는지 확인한다. URI binding은 F00/F10/F20/F90에 이미 자동 적용되어 있으므로 node마다 다시 선택하지 않는다. F00/F20/F90의 built-in Embedding Model에는 같은 승인 provider/model을 설정하고, F10의 Component 36에는 trusted gateway의 인증 owner/group 입력을 연결한다. 사용자가 WorkDefinition·ACL·snapshot·Skill registry를 F20에 따로 입력하도록 만들지 않는다.
5. F10 native Playground 경로에는 `LANGFLOW_F10_FLOW_ID` 환경 변수를 설정하지 않는다. 별도 legacy `hitl_form_api`를 명시적으로 시험하는 경우에만 그 서비스 전용 설정으로 UUID를 기록한다.
6. F10의 세 `42 보완 답변 HITL`에서 `질문 Batch`가 Component 13의 `재질문 Batch`에 연결되는지 확인한다. 같은 회차에서 42의 `답변 제출 Data`는 Component 39의 `Native Answer Submission`으로, 42의 `Submit Answers`는 Component 39의 `Submit Trigger`로 각각 연결된다. 최종 built-in Human Input의 `Approve`, `Reject`, `Cancel`은 `43 최종 승인 경로 Gate`의 자동 입력으로 모인 뒤 해당 Store command 하나만 연다. Component 18의 일반 `request_changes` primitive는 현재 F10 공개 경로에 연결하지 않는다.
7. 승인 branch만 `36 Approved Design Invocation Loader(strict JSON text) → TypeConverter(Message) → Run Flow(F20, tool_mode=false) → Chat Output`으로 이어지고 loader의 blocked branch는 별도 Chat Output으로 끝나는지 확인한다.
8. F20/F30 child에는 Human Input 또는 tool approval pause가 없는지 확인한다.
9. `flows/build_manifest.json`의 source hash와 import된 Custom Component source를 대조한다.

Flow JSON은 import 가능한 orchestration skeleton이다. 사내 LLM gateway, built-in Embedding Model provider/model, MongoDB Search index, bearer/global variables가 설정되기 전에는 각 Flow metadata의 `configuration_required`, `trusted_backend_only_configuration_required`, `evaluation_configuration_required` 상태를 유지하며 운영 준비 완료로 간주하지 않는다.

## 7. 카탈로그 초기 적재

전체 예제 실행은 `EXAMPLE_END_TO_END_TEST_GUIDE.md`를 따른다.

1. F00 Canvas에서 실행 node 6개/edge 5개와 설명용 Sticky Note 2개를 확인한다. 실행 경로는 `00 Catalog JSON Loader → 01 Deterministic Chunker → 02 MongoDB Catalog Vector Writer → Data to Message → Chat Output`, side edge는 `Embedding Model → MongoDB Writer`다. Sticky Note는 edge가 없는 설명 전용 node다.
2. Loader에는 `samples/f00_catalog_assets_example.json` 파일 하나만 올린다. 일반 입력은 JSON array, `{items:[...]}` 또는 JSONL 파일 하나다. F00은 내부적으로 `tenant_id=default`, `catalog_id=internal-assets`를 고정해 저장하며 이 둘은 화면 입력이 아니다.
3. Chunker에 chunk size/overlap과 record별·전체 chunk 상한을 설정한다.
4. F00/F20/F90의 built-in Embedding Model에 같은 승인 provider/model과 Secret을 설정한다. F00 Writer의 URI는 자동 연결된 `MONGO_URL`을 쓰고, Database와 세 canonical collection 기본값을 확인한다. Writer는 청크 하나당 Embedding Model 호출 하나를 사용하므로 고급 설정 `임베딩 호출 간격(초)`는 기본값·최소값인 1초로 둔다. `MongoDB 일괄 저장 문서 수`는 임베딩 요청 수와 관계없이 vector 문서를 bulk write로 묶는 수다.
5. Writer Canvas의 **테스트 실행 (저장하지 않음)**(`dry_run=true`)으로 record/chunk 수와 source/ingest hash를 먼저 확인한다. 테스트 실행 응답은 canonical text 원문을 반환하지 않으며 embedding 또는 MongoDB network를 호출하지 않는다. 그러므로 runtime contract의 `state`는 `DEFERRED`, snapshot ID는 `null`이다.
6. 테스트 실행을 끈 `dry_run=false`로 실제 저장 실행을 하고 `catalog_assets` parent와 `catalog_asset_chunks.embedding.vector`가 같은 tenant/snapshot으로 저장됐는지 확인한다. active pointer와 chunk/query vector가 `schema_version`, `runtime_class`, `model_id`, `dimension`, `fingerprint`를 포함한 같은 `embedding-runtime-contract/v2` 계약을 사용하는지 확인한다.
7. 전체 asset/chunk upsert가 성공한 뒤에만 `catalog_active_pointers`가 새 snapshot과 runtime v2 contract를 가리키는지 확인한다.
8. F20 검색 전에 MongoDB lexical/vector index가 `lexical_text_redacted`와 `embedding.vector` 경로 및 active pointer와 동일한 runtime dimension으로 준비됐는지 확인한다.

F00은 다른 Flow나 Catalog Worker API를 호출하지 않는다. 일부 record, 청크별 embedding 호출 또는 MongoDB write가 실패하면 active pointer를 바꾸지 않고 실패 결과를 반환한다. Langflow built-in MongoDB Atlas node는 고정 런타임의 선택 의존성 import 결함과 F20 nested vector/active pointer 계약 불일치 때문에 사용하지 않으며, 별도 standalone Writer가 동일 MongoDB collection 계약을 구현한다.

## 8. F10 실행과 HITL 연결

F10 시작 Text Input 두 개와 `10 업무 요청 Envelope`에는 `samples/f10_work_request_example.json`의 예제가 기본값으로 들어 있다. F00은 고정 scope `default`/`internal-assets`로 적재하고 `samples/skill_registry_example.json`을 `scripts/seed_example_skill_registry.py --apply`로 같은 DB에 넣은 뒤 사용한다. `session_id`는 화면에서 입력하지 않으며 실행을 시작한 Langflow caller가 run마다 하나를 제공하고 Component 10이 graph session으로 읽는다.

F10은 Playground에서 바로 실행하는 것이 기본이다. 부족한 정보가 있으면 회차별 `12 → 질문 LLM → 13 → 42 보완 답변 HITL → 39` 경로가 열린다. Component 42는 `node_input` schema를 보내 질문별 실제 text input을 같은 Playground 카드에 표시한다. 사용자는 카드에서 답변을 입력하고 `Submit Answers`를 선택한다. `Cancel`은 종료하고, Component 39는 제출값을 canonical 질문 batch와 대조해 MongoDB CAS 병합·다음 회차·검토·차단 중 하나만 선택한다. 외부 Answer Form 등록 API, 별도 HITL 웹 서버, 수동 `/resume` 호출은 active F10 경로에 없다. Component 12·13·16·17·18·39·40·42·43의 group output은 실패 결과 또는 선택되지 않은 저장 branch를 다음 의미 단계에 전달하지 않는다. 세부 순서는 `HITL_STATE_MACHINE.md`를 따른다.

현재 저장소에는 suspend된 pending request와 answer deadline을 주기적으로 조회해 자동 종료하는 HITL expiry sweeper가 구현되어 있지 않다. production 배포 전에 별도 sweeper가 만료 batch/pending request를 찾아 runtime `BLOCKED` 또는 `CANCELLED`와 audit event를 기록하고, HITL 저장소와 실제 Langflow pending 상태를 reconciliation하도록 구현해야 한다. 이 sweeper와 실제 시간 흐름 E2E가 없으면 HITL production readiness는 실패다.

Component 10은 request/additional prompt에서 credential literal을 탐지하면 원문 저장 전에 차단한다. 다만 정상 업무 원문은 provenance를 위해 `source_requests`에 남으므로, production `work_definitions`/events 읽기는 인증된 tenant와 owner 또는 승인된 관리자에게만 허용하고 encryption at rest/KMS, 접근 audit, retention·삭제·legal hold 정책을 적용한다. DB backup과 운영 export에도 같은 정책을 적용하며, 일반 catalog 검색·embedding·report service account에는 raw source read 권한을 주지 않는다.

Component 42의 Playground card는 Langflow 1.11.1 특성상 질문별 text input으로 렌더링한다. `single_choice`는 안내된 선택지 하나를 정확히, `multi_choice`는 쉼표로 구분해, `boolean`은 `true/false` 또는 `예/아니오`, `number`는 숫자로 입력한다. `single_choice_with_text`의 기타 설명은 `{"choice":"__other__","text":"설명"}` 형식을 사용한다. Component 42와 39이 choice membership, 실제 boolean/number, 필수 응답, immutable `answer_deadline_at` 대비 `submitted_at`을 다시 검증한다. TTL purge용 `expires_at`만 현재 구현의 7일 답변 보존 기간까지 연장한다.

최종 `Approve` 뒤에는 사용자가 F20을 따로 실행하거나 WorkDefinition을 복사하지 않는다. Component 36은 F10 approval receipt와 원 request envelope의 identity를 대조하고, MongoDB의 canonical `APPROVED` WorkDefinition을 다시 읽어 revision·approved hash·owner/session/native channel을 검증한다. `authenticated_subject_id`는 요청마다 trusted gateway identity context에서 주입하고 canonical owner와 정확히 일치시킨다. `authenticated_groups`도 gateway가 제공한 bounded group만 연결하며 고정 전역값, Chat Input, 사용자 자유 입력을 직접 연결하지 않는다.

Component 36은 같은 tenant의 `catalog_active_pointers`와 `status=active` Skill registry를 읽어 `agent-design-invocation/v1` 하나를 만든다. 추가 설계 프롬프트는 원 request envelope에서 가져와 길이/hash/secret 검사를 거치며 승인 Skill context와 분리한다. loader의 `success_path`는 strict JSON `text`가 들어 있는 Data를 built-in TypeConverter의 Message 출력으로 바꾼 뒤 F20 Run Flow 입력으로 가고, `blocked_path`는 진단 output으로 끝나야 한다. Run Flow는 `tool_mode=false`, `cache_flow=false` direct mode이고 다른 Flow HTTP API나 Agent-selected tool call을 사용하지 않는다.

F20의 공개 graph 입력은 ChatInput 하나이며 `should_store_message=false`여야 한다. Run Flow가 전달한 strict invocation JSON을 내부 TypeConverter가 파싱하고 Query Planner가 닫힌 schema/status/identity/trust boundary를 다시 검증한다. Query Planner가 canonical 승인 WorkDefinition·tenant/ACL·active snapshot·추가 prompt를 `design_scope_sha256`/`query_plan_sha256`으로 고정하고, built-in Embedding Model과 Component 29가 두 lock 및 runtime v2 contract를 보존하며 Retriever가 query plan canonical hash와 active catalog contract를 다시 확인하는지 검사한다. top-level retrieval trace와 Component 22 context trace에는 tenant/snapshot/work definition ID·revision/approved hash/design scope/query plan hash가 모두 있어야 하며 기존 trace와 값이 다르면 중단해야 한다. Skill/Blueprint 단계도 design scope canonical hash를 재계산해야 한다. F20 ChatInput에 임의 invocation을 직접 붙여 넣는 실행은 production 경로가 아니다.

## 9. Report 게시

F30의 Component 30→31 결과를 먼저 화면에서 확인한다. Component 30이 node/port/edge/source/runtime/secret/permission 계약으로 `build_readiness`와 blocker를 다시 계산하고 Blueprint의 `readiness_assessment`와 일치시키는지, retrieval trace의 tenant/snapshot/work/revision/approved/design/query lock이 승인 입력과 일치하는지 확인한다. 전달된 `groups` metadata는 보존되지만 현재 renderer에는 group overlay·접기/펼치기가 없으므로 이를 제공된 UI 기능으로 표시하지 않는다. Component 32는 기본 **테스트 실행 (저장하지 않음)**(`dry_run=true`)으로 hash, scheme, allowed host를 검증한다. 실제 게시 시에만 테스트 실행을 끄고 Report API URL/token/tenant/actor/idempotency를 제공한다. header-auth view/download/metadata는 생성 actor 본인만 접근할 수 있으며 같은 tenant의 다른 actor에도 404를 반환한다.

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
- native pause는 F10 top-level에만 존재하며, 보완 답변은 세 `42 보완 답변 HITL` schema card에서 직접 입력하고 최종 built-in Human Input은 승인 결정만 담당함
- F00은 `00 Loader → 01 Chunker → 02 Writer → Data to Message → Chat Output`과 `Embedding Model → Writer`의 실행 node 6개/edge 5개를 사용하고, 별도의 설명용 Sticky Note 2개만 추가한다. 다른 Flow/Catalog Worker API를 호출하지 않음
- F00이 전체 parent/chunk/vector 저장 성공 뒤에만 active pointer를 전환하고 실패 시 이전 pointer를 유지함
- F10의 세 번의 Component 42→39 경로가 Playground schema `values`를 question ID로 복원하고 identity·batch·deadline·idempotency·revision을 검증한 뒤 CAS로 반영함
- F10의 Component 12·13·16·17·18·39·40 차단 output이 후속 LLM·Human Input·저장 node로 연결되지 않음
- F10 credential literal이 저장 전에 차단되고 WorkDefinition raw source의 tenant/owner ACL·KMS·retention/delete가 검증됨
- Component 36이 canonical `APPROVED` WorkDefinition의 revision/hash/owner/session, authenticated owner/groups, active catalog pointer, active Skill registry를 다시 검증하고 실패 시 Run Flow를 열지 않음
- HITL expiry sweeper가 만료 pending request와 저장 상태를 fail-closed로 reconciliation하며 실제 시간 기반 E2E를 통과함
- F10의 Run Flow가 `tool_mode=false` direct mode로 F20의 단일 ChatInput/최종 ChatOutput port를 사용하며 HTTP Flow API나 사용자 WorkDefinition 재입력이 없음
- F20의 추가 설계 프롬프트가 Skill context와 분리되고, `agent-design-invocation/v1`의 canonical 승인 상태/identity/ACL/snapshot/Skill registry로 만든 design scope/lock이 downstream에서 유지됨
- retrieval trace provenance가 tenant/snapshot/work/revision/approved/design/query lock에 고정되고 F30 경계에서 다시 검증됨
- F30이 `build_readiness`와 `readiness_assessment`를 입력 문자열 그대로 신뢰하지 않고 graph/contract로 다시 계산함
- report 링크가 purpose별 signed capability로 tenant/actor/report/content hash/만료에 고정되고, tamper/expiry/purpose mismatch/header 혼용이 차단되며 service bearer/signing secret이 브라우저에 노출되지 않음
- report metadata와 GridFS blob의 보존·삭제를 담당하는 외부 sweeper/lifecycle 검증 완료
- schema/reference/hash/CSP/XSS 테스트 통과
- production 설정 누락 시 readiness 실패

실제 배포 승인은 여기에 더해 live MongoDB transaction, Atlas Search, embedding provider, 사내 LLM gateway, Workflow suspend/resume, gateway 인증, 인증된 Report URL 조회와 artifact purge를 한 tenant와 교차 tenant 모두에서 E2E 검증해야 한다.
