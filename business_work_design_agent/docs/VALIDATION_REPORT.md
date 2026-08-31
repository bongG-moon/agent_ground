# 검증 결과

검증 대상 일자: 2026-08-31 (Asia/Seoul)  
상태: `LOCAL_VALIDATION_PASSED` / `PRODUCTION_INFRA_VALIDATION_PENDING`

F00은 파일 로더·결정론적 청커·Embedding Model·MongoDB Writer가 각각 보이는 구조로 유지했다. F10은 최대 세 번의 native HITL 보완을 유지하면서 반복 상태 저장/게이트를 `13`·`39`·`40`·`41` standalone component 안으로 묶고, `42 보완 답변 HITL`이 질문 batch를 Playground의 `node_input` schema 입력칸으로 표시한다. 시작부에는 업무 설명·추가 설계 프롬프트용 Text Input 두 개가 있고 Envelope 화면에는 팀 명·사번만 보이며 session은 Langflow graph runtime에서 자동으로 가져온다. Component 42에는 답변 제출 외에 명시적 **`추가 입력 건너뛰기`** action이 있으며, Component 39가 이를 empty answer로 해석하지 않고 batch skip audit과 WorkDefinition `unresolved`를 기록한 뒤 normal preview/review로 보낸다. `Cancel`은 별개의 terminal action이며 skip은 4차 질문이 아니다. 검토 단계는 Component 18의 `review_and_request_approval` 단일 저장으로 Preview 검증본을 `WAITING_APPROVAL`로 전환한다. 최종 built-in Human Input 뒤의 Component `43`이 선택되지 않은 Component 18 상태 저장 branch를 즉시 Langflow 조건부 제외하고, Component `41`은 넓은 optional fan-in 대신 하나의 event-list만 읽어 실행되지 않은 sibling node를 요청하지 않는다. 승인 경로의 Component `36`은 MongoDB가 복원한 `datetime`을 Data와 F20 strict JSON 모두에서 UTC ISO-8601 문자열로 정규화한다. 외부 Answer Form API는 active F10 경로가 아니다. 승인 경로는 Component 36 권위 재조회→Run Flow(F20)→sealed handoff gate→Run Flow(F30)로 이어진다. F20의 `38`은 승인 WorkDefinition·terminal Blueprint·retrieval trace를 strict `f20-report-handoff/v1` JSON으로 고정하고, F10의 `44`와 F30의 `33`이 hash 및 binding을 이중 검증한다. MongoDB URI 입력은 Langflow Secret Global Variable `MONGO_URL`을 `load_from_db=true`로 자동 참조하며 실제 URI는 export에 포함하지 않는다. 실제 MongoDB/embedding provider 부하와 장애 복구 검증은 별도 운영 항목으로 남아 있다.

아래 수치와 hash는 `추가 입력 건너뛰기` 및 최종 승인 branch 제외 수정을 포함해 다시 생성한 Flow와 이번 변경의 핵심 로컬 회귀 테스트 결과다. 실제 Langflow Playground의 suspend/resume 및 운영 MongoDB·Embedding provider E2E는 별도 운영 검증으로 남아 있다.

## 고정 런타임

- `langflow==1.11.1`
- `langflow-base==0.11.5`
- `lfx==1.11.5`
- Python `3.13.14`

검증은 전용 환경 `C:\Users\qkekt\AppData\Local\Temp\business_work_design_lf1111`에서 수행한다. 별도 설치된 Langflow Desktop은 변경하지 않는다.

## 이번 변경의 검증 기준

F00은 다음 구조여야 한다.

```text
00 Catalog JSON Loader & Normalizer
  -> 01 Catalog Deterministic Chunker
  -> 02 MongoDB Catalog Vector Writer
  -> Data to Message
  -> Chat Output

Embedding Model
  -> 02 MongoDB Catalog Vector Writer
```

- 사용자가 `FileInput`으로 JSON/JSONL/NDJSON 파일 하나를 업로드한다.
- Loader Canvas에는 `tenant_id`/`catalog_id` 입력이 없다. F00은 내부적으로 `tenant_id=default`, `catalog_id=internal-assets`를 bundle과 MongoDB 문서에 기록한다.
- `00_catalog_json_loader.py`, `01_catalog_deterministic_chunker.py`, `02_catalog_mongodb_vector_writer.py`는 각자 한 파일에 완결된 Standalone Component다.
- Loader는 parse·normalize·민감정보 제거·canonical 원문 보존, Chunker는 결정론적 분할과 hash 계약, Writer는 built-in Embedding Model의 `Embeddings` handle을 청크 1개씩 순차 호출(첫 호출 뒤 최소 1초 간격)해 vector를 만들고 MongoDB write batch로 게시한다. 실제 provider/model/API key는 Embedding Model node에만 설정한다. Writer에는 model/version/dimension 입력이 없으며 첫 live vector로 dimension을 얻고, runtime class·해석 가능한 model ID·dimension·SHA-256 fingerprint를 가진 `embedding-runtime-contract/v2`를 저장한다.
- 다른 Flow나 별도 ingest service를 HTTP API로 호출하지 않는 direct 실행이다.
- core write 대상은 `catalog_assets`, `catalog_asset_chunks`, `catalog_active_pointers`다.
- `catalog_assets`는 parent 메타데이터/redacted 원문, `catalog_asset_chunks`는 검색 chunk와 `embedding.vector`, `catalog_active_pointers`는 검증 완료 snapshot 게시 pointer를 담당한다.
- parent/chunk embedding 저장과 count 검증이 모두 끝난 뒤에만 active pointer를 마지막에 갱신한다. 실패 시 기존 pointer를 유지한다.
- Writer의 **테스트 실행 (저장하지 않음)**은 내부 `dry_run=true`로 provider와 MongoDB를 호출하지 않으므로 live contract를 추측하지 않고 `embedding_contract.state=DEFERRED`, `snapshot_id=null`을 반환한다.
- F20/F90은 각각 built-in `Embedding Model → 29 Search Query Embedding Batcher` edge로 query vector를 만들며, F00과 같은 승인 provider/model의 runtime contract가 active pointer와 완전히 일치하지 않으면 retrieval을 차단한다.
- F00 Flow는 실행 node 6개/edge 5개와 설명용 Sticky Note 2개(Canvas 총 8개)로 구성되고 Human Input을 포함하지 않는다.
- F10/F20/F30/F90에도 각각 6/4/1/2개의 Sticky Note가 있으며, 모든 Note는 edge가 없는 `noteNode`라서 Langflow 실행 Graph vertex에는 포함되지 않는다.
- 전체 Standalone Component inventory는 38개다.
- F10의 각 보완 회차는 `12 완전성 평가 → 질문 LLM → 13 질문 Batch → 42 Playground 답변 카드 → 39 답변 반영`으로 보인다. Component 42는 `schema`를 포함한 native `node_input` pause를 보내고 Playground가 `decision.values` 또는 `skip_additional_input`을 반환하는지 검증 대상이다. 답변 제출은 재평가하고, skip은 audit·`unresolved`를 기록한 뒤 review로 간다. 1·2차는 최대 3문항, 마지막 3차는 최대 4문항이며 skip은 4차 질문이 아니다. `40`은 검토 진입 중 유효한 하나를 합치고 `43`은 최종 Approve/Reject/Cancel 중 선택되지 않은 두 상태 저장 branch를 제외하며, `41`은 선택된 terminal event-list만 안전하게 표시한다.
- 업무 정의 Flow는 F10 native HITL 하나이고 전체 Flow inventory는 F00/F10/F20/F30/F90 5개다.
- F10 approve success는 Component 45 인증 Context 경계를 거쳐 Component 36으로 들어가며 canonical 승인본·sealed subject/groups·active catalog pointer·active Skill registry를 재검증한다.
- Component 36 성공 결과 `agent-design-invocation/v1`만 `Run Flow(tool_mode=false)`의 F20 단일 ChatInput에 연결된다. F20 `38`이 만든 sealed handoff는 F10 `44`의 schema/hash 검증을 통과한 경우에만 F30 ChatInput으로 전달되고 F30 ChatOutput이 F10 결과로 돌아온다.
- Component 36은 canonical/request channel이 모두 정확히 `native_hitl`인 경우만 허용하고, invocation을 Python repr가 아닌 strict JSON `text`로 투영한다.
- F10의 built-in TypeConverter가 invocation Data를 Message로 변환하며 F20/F30 ChatInput은 `should_store_message=false`라서 권한성 payload를 대화 이력에 저장하지 않는다.
- 수동 WorkDefinition 재입력, 별도 F20 사용자 실행, 다른 Flow HTTP API 호출이 없다.
- F00의 FileInput은 배포 가능한 빈 값으로 유지하고 `samples/f00_catalog_assets_example.json`을 사용자가 한 번 업로드한다. 부분 checkpoint가 남으면 Writer가 native Continue/Stop 카드를 만들며, Continue는 저장된 vector를 재사용해 다음 bounded batch만 처리하고 Stop은 pointer 미게시 상태의 checkpoint를 보존한다.
- F10 시작 Text Input 두 개와 `10 업무 요청 Envelope`는 `samples/f10_work_request_example.json`의 안전한 데모 값으로 채워져 있으며 Component 원본의 범용 기본값은 비어 있다.
- `samples/skill_registry_example.json`은 runtime Skill 계약과 prompt SHA-256을 통과하며 seed helper는 `--apply` 없이는 MongoDB에 쓰지 않는다.

## 자동 검증

| 항목 | 결과 |
| --- | --- |
| 전체 pytest | `336 passed, 8 warnings` — target Langflow 1.11.1 venv에서 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 및 별도 pytest base temp로 실행. F00 full-snapshot/CAS/resume, F10 HITL·skip·terminal fan-in·auth context, F20/F30/F90 handoff·retrieval·report 계약을 포함 |
| Standalone source build | `38/38` build 및 manifest inventory 일치 |
| F00 visible vector pipeline | 전체 snapshot 명시 확인, deterministic ID, partial resume, CAS 활성화, provider 호출 간격 회귀를 포함해 통과 |
| F10 미빌드 sibling 방지 | planner/batch/HITL/commit/review/store/auth/loader의 blocked·cancel·reject terminal 28개를 Component 41 event-list 입력으로만 표시하며 선택되지 않은 sibling build를 요구하지 않음 |
| F10 인증 경계 | local demo fixture는 `authenticated_subject_verified=false`, `trusted_gateway`는 독립 subject/groups 없이는 차단하며 Component 36은 sealed context만 수용하는 회귀를 통과 |
| Flow `Graph.from_payload` / embedded source | 5/5 통과. Sticky Note를 제외한 실행 node/edge 수, embedded source hash 및 handle 호환성이 manifest와 일치 |
| `lfx validate --level 3 --skip-credentials` | target Langflow 1.11.1에서 F00/F10/F20/F30/F90 `5/5 passed`; 설치된 Desktop 1.11.0에서도 동일 JSON 구조 검증 `5/5 passed` |
| Runtime validator | target `langflow=1.11.1`, `langflow-base=0.11.5`, `lfx=1.11.5`에서 `ok=true`, Flow 5개·Standalone Component 38개 확인 |
| Flow generator drift | 개별 Flow 5개와 bundle `--check` 통과 |
| Python compile / diff hygiene | `compileall` 및 `git diff --check` 통과 |
| warning 사유 | 8건: Pydantic/SQLModel/Starlette/metadata upstream deprecation 경고. 제품 계약 실패 없음 |
| Bundle SHA-256 | `5bc55a0ce1ded8aaf94954c73051ca88cb555da1bcffad46e71de33e302499e0` |

최종 검증에는 다음 항목을 포함한다.

- Component subclass 수와 전체 inventory 38개 일치
- 표준 라이브러리, 승인 dependency, `lfx` public import allowlist
- 상대·형제·로컬·private/dynamic import와 `sys.path` 조작 부재
- 모든 Component의 Langflow 1.11.1 template 단독 build
- Flow embedded source byte/hash와 실제 source 일치
- 실제 output/input handle 호환성과 `Graph.from_payload` 역직렬화
- F00에는 `00` Loader, `01` Chunker, built-in Embedding Model, `02` Writer, ParseData, ChatOutput만 있고 legacy 일체형 ingest와 Human Input node가 없음
- JSON object/array/items wrapper/JSONL/NDJSON parse와 입력 상한
- 민감 key/token/email redaction, 안전한 원문/hash 보존, secret 비노출
- deterministic snapshot/document ID와 같은 파일 재실행 idempotency
- F00 Writer와 F20/F90 query batcher의 runtime class/model ID/첫 vector dimension/fingerprint `embedding-runtime-contract/v2`, finite vector, full-contract fail-closed 검사
- Writer/Query Batcher에 수동 model/version/dimension 또는 HTTP endpoint/token/precomputed-vector 입력이 없는지 검사
- MongoDB parent/chunk write와 count 검증 후 active pointer last 순서
- embedding/MongoDB 실패 및 테스트 실행(`dry_run=true`)에서 기존 pointer가 바뀌지 않음
- F10 native HITL의 `42` schema/decision values 매핑, 대기 중 빈 branch payload, Submit/Skip/Cancel route 상호 배타 판정, skip audit/unresolved/unknown provenance/review projection, Component `43`의 final branch conditional exclusion, Component `41` event-list terminal projection, WorkDefinition revision/state, Component 36 authority reload, F10→F20→F30 direct Run Flow, Skill/검색 ACL, blueprint/readiness, report CSP/XSS 회귀 범위
- Component 36 strict JSON Message handoff, F20 `38` sealed report handoff, F10 `44` gate, F30 `33` full binding 검증, legacy `playground` channel 차단, F20/F30 ChatInput 비저장 설정

F10의 `lfx validate --level 3 --skip-credentials`는 성공(exit 0)했다. 경고는 Sticky Note의 의도된 orphan/unused 표시, export-version heuristic, 현재 `lfx` CLI의 registry loader 호환 표시, 그리고 custom `Data`/`Message` port를 generic `other`/`str`로 추정하는 type heuristic이다. Runtime Graph는 `noteNode`를 실행 vertex로 해석하지 않으며 별도 contract test가 Note의 무연결 상태를 검증한다. Required input/credential은 import 시 실제 provider·MongoDB configuration을 넣어야 하는 intentional placeholder이므로 이 정적 skeleton 검사에서는 제외했다. 별도 runtime validator는 실제 Langflow 1.11.1 source로 모든 embedded Component를 build하고 `Graph.from_payload` 및 handle 호환성을 확인해 `ok=true`를 반환했다. 또한 Desktop에 설치된 Langflow 1.11.0/LFX 1.11.0에서도 final Reject 선택 후 unselected Component 18 두 개가 조건부 제외되는 것을 구조적으로 검증했다.

정적 build·unit contract 통과만으로 Playground 화면에 text input이 보였다고 결론내리지는 않는다. 실제 Langflow 1.11.1 import 후 질문이 발생하는 예제로 F10을 실행해, `42 보완 답변 HITL` 카드에 `answer_01` 등의 입력칸과 `추가 입력 건너뛰기` action이 보이는지, 입력 후 `Submit Answers`가 `decision.values`를 Component 39까지 전달하는지, skip이 answer value를 만들지 않고 audit/unresolved 뒤 review로 가는지를 별도 E2E로 확인해야 한다. 이 확인 전에는 외부 Answer Form/API가 필요 없다는 UI 동작을 production 승인을 위한 완료 증적으로 취급하지 않는다.

생성 manifest의 Flow별 결과:

| Flow | Canvas / 실행 / Note / Edge | SHA-256 |
| --- | ---: | --- |
| F00 | 8 / 6 / 2 / 5 | `724980e4197ea9f2ee753edfcdcf72696b4fe9062350c94f04f0d3b7109dfe36` |
| F10 | 45 / 39 / 6 / 109 | `956e06ed0325de42ed92ff81b1f0acce60efa86b579daf4749511433e005327c` |
| F20 | 25 / 21 / 4 / 29 | `96ef295a5fc6f71a44aeca940ef68ded840079e85e9a736c7afe9b1484847636` |
| F30 | 9 / 8 / 1 / 10 | `e4f1b02451c7f944a2414dba8301ad7686e994975dba68001f3edc42d1b93c6c` |
| F90 | 11 / 9 / 2 / 9 | `180bc5d7dbdba4ec4a1be6e8d96217335b34b94d31ca4fc50d8b0c610884161c` |

## 브라우저 반응형 QA

F30 renderer source와 sample report artifact는 변경하지 않았다. 다만 F30 Canvas에는 sealed handoff ChatInput/TypeConverter/Loader/ChatOutput boundary가 추가됐다. renderer 자체의 browser visual QA는 반복하지 않았고, 새 boundary는 component chain·Graph 역직렬화·dry-run 회귀 테스트로 검증했다.

| 항목 | 결과 |
| --- | --- |
| 1440×900 desktop layout/overflow | `NOT_RERUN_F30_UNCHANGED` |
| 390×844 mobile layout/bottom sheet | `NOT_RERUN_F30_UNCHANGED` |
| node/edge 선택과 상세 drawer | `NOT_RERUN_F30_UNCHANGED` |
| fit/zoom/keyboard focus | `NOT_RERUN_F30_UNCHANGED` |
| console error/warning | `NOT_RERUN_F30_UNCHANGED` |
| JavaScript 비활성·인쇄 fallback | `NOT_RERUN_F30_UNCHANGED` |

## 실제 인프라에서 남은 검증

아래는 로컬 계약/역직렬화 검증만으로 완료 처리할 수 없다.

- 운영 MongoDB에서 `catalog_assets`, `catalog_asset_chunks`, `catalog_active_pointers` write와 active pointer last 동작
- Atlas Vector Search index가 `catalog_asset_chunks.embedding.vector`와 F00 live runtime dimension에 맞는지 확인
- 실제 F00/F20/F90 built-in Embedding Model provider의 동일 model/runtime contract, timeout·batch 성능과 model ID 해석 가능 여부
- 사내 원본 catalog 2만~3만 행의 메모리·처리시간·부분 장애·재실행 검증
- 사내 LLM endpoint의 구조화 출력
- 실제 Langflow project에서 F10 native HITL suspend/resume와 최대 3회 질문(1·2차 최대 3문항, 3차 최대 4문항) 및 explicit skip E2E
- Component 45/36의 sealed authentication context·canonical 승인 WorkDefinition·active catalog pointer·active Skill registry 조립과 caller 위조 차단
- 실제 Langflow project에서 F10 Run Flow direct mode가 F20/F30 동적 입력/출력 port를 해결하고 HTTP 호출·수동 재입력 없이 보고서 결과를 반환하는 E2E
- Report API 실제 게시, purpose별 signed capability URL browser 조회, tamper/expiry/purpose/header-mix 차단과 교차 tenant 차단
- Uvicorn/reverse proxy/access analytics의 signed capability query redaction 또는 suppression
- report metadata/GridFS artifact retention·hold·purge sweeper와 backup/restore
- 만료된 native HITL pending job을 중단하고 terminal runtime event를 기록하는 운영 sweeper

현재 문서 상태는 로컬 자동 검증 통과다. 위 실제 인프라 항목을 모두 완료하기 전에는 production-ready로 승격하지 않는다.
