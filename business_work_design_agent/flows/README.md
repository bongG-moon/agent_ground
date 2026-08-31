# Langflow 1.11.1 Flow exports

이 폴더의 `F00`~`F90` JSON은 `scripts/build_langflow_1_11_flows.py`가 생성한 Langflow 1.11.1 import 파일이다. Custom Component source는 각 node의 `template.code.value`에 원본 byte와 동일하게 embed되며, node metadata의 `standalone_source_sha256`으로 원본 파일에 고정된다.

## Import 단위

- `F00_catalog_file_vector_ingest.json`: 파일 loader·chunker·Embedding Model·MongoDB writer가 분리되어 보이는 top-level 적재 Flow
- `F10_work_definition_parent.json`: native `node_input` 보완 질문 카드와 최종 승인 후 F20→F30 Run Flow를 연속 실행하는 top-level 업무 정의 Flow
- `F20_agent_blueprint_design.json`: 단일 trusted invocation을 사용하는 HITL-free Agent Blueprint child Flow
- `F30_responsive_report.json`: sealed F20 report handoff를 검증해 보고서를 만드는 HITL-free child Flow
- `F90_search_evaluation.json`: Component 36의 Verified Design Invocation 전체 JSON을 받는 evaluation-only hybrid retrieval 평가 Flow
- `00_business_work_design_ALL_FLOWS.json`: 위 다섯 Flow의 이관용 bundle

개별 Flow JSON이 Langflow UI/API의 직접 import 대상이다. Bundle은 여러 Flow를 함께 이관하는 상위 artifact이므로 단일 Flow import endpoint에 넣지 않는다.

## Canvas 단계 설명

각 Flow에는 실행 노드와 별도로 단계별 한국어 설명을 담은 Langflow `Sticky Note`가 배치되어 있다. Note는 port나 edge가 없는 `noteNode`이므로 실행 Graph, HITL, 검색 및 저장 계약에는 영향을 주지 않는다. 이 문서와 manifest의 `node_count`는 Canvas 전체 node 수를, Langflow `Graph.from_payload` 검증은 Note를 제외한 실행 node 수를 기준으로 기록한다.

| Flow | Sticky Note 수 | 설명 범위 |
| --- | ---: | --- |
| F00 | 2 | 파일 업로드·정규화·청킹 / 임베딩·MongoDB 저장·게시 |
| F10 | 5 | 업무 추출·native 질문 카드로 최대 3회 HITL 보완, 검토·승인, F20→F30 direct 실행 |
| F20 | 4 | trusted invocation, hybrid search, Blueprint 검증, sealed report handoff 출력 |
| F30 | 1 | sealed handoff 검증 → View Model → 반응형 HTML → 게시 |
| F90 | 2 | Verified Design Invocation 입력·쿼리 계획·임베딩 / 검색·후보 문맥 출력 |

## 중요한 실행 조건

Flow JSON은 secret이나 production endpoint를 포함하지 않는다. Import 후 `build_manifest.json`과 각 Flow의 `metadata.required_configuration`을 확인하여 MongoDB, embedding provider, 승인된 model, tenant/ACL, Report API를 명시적으로 설정해야 한다. 특히 F20은 trusted backend가 canonical 승인 상태·identity/ACL·snapshot·Skill registry를 구성하고 실제 사내 자산 port를 검증하기 전까지 `trusted_backend_only_configuration_required`이며 `import_ready`로 간주하지 않는다.

F10은 top-level native pause/resume을 사용한다. F00 Writer의 부분 적재 `node_input` Continue/Stop 카드는 **Langflow durable background job**에서만 표시된다. 일반 Canvas **Run Flow**는 durable job을 만들지 않으므로 F00은 checkpoint를 저장한 뒤 `PARTIAL_EMBEDDINGS_SAVED`와 `hitl.reason=durable_background_job_required`를 반환하며, 같은 파일·모델 설정의 새 실행으로 이어간다. F10의 보완 질문은 Component 42가 `node_input`/`schema`로 만드는 Playground 입력 카드이며, built-in `Human Input`은 F10 최종 `Approve`/`Reject`/`Cancel` 승인 단계 하나뿐이다. F00에는 built-in Human Input node를 추가하지 않으며, F20/F30/F90은 HITL-free child/evaluation Flow다.

F00은 실행 node 6개와 edge 5개를 사용하며, Canvas 상단에 설명용 Sticky Note 2개가 추가되어 있다.

```text
00 Catalog JSON Loader (standalone)
  → 01 Deterministic Chunker (standalone)
  → 02 MongoDB Catalog Vector Writer (standalone)
  → Data to Message
  → Chat Output

Embedding Model (built-in)
  → 02 MongoDB Catalog Vector Writer
```

Loader에는 업로드 파일만 보인다. 이 파일은 **현재 전체 카탈로그**여야 하며, F00은 신규분을 기존 snapshot에 병합하지 않는다. 실제 게시 시 업로드 파일 전체가 다음 active snapshot이므로 delta 파일에서 누락된 자산은 다음 검색 대상에서 제외된다. `tenant_id=default`와 `catalog_id=internal-assets`는 F00 안에 고정되어 bundle과 MongoDB 문서에 보존되지만 사용자 입력이나 Canvas 설정이 아니다. Chunker에는 chunk size/overlap, built-in Embedding Model에는 실제 provider/model/secret, Writer에는 자동 연결된 Langflow Secret `MONGO_URL`·DB·세 canonical collection·`부분 적재 후 계속 여부 확인 (HITL)`과 고급 설정이 보인다. Writer는 청크 하나씩만 임베딩하고 다음 호출과 재시도 전 최소 1초를 기다리는 `임베딩 호출 간격(초)`을 사용한다. 이는 청크 크기가 아니며, `MongoDB 저장 체크포인트 청크 수`는 이미 생성된 vector 문서를 checkpoint로 저장하는 간격이다. Writer에는 model/version/dimension 입력이 없다.

F00 Writer와 F20/F90 query embedding node는 built-in Embeddings runtime에서 `embedding-runtime-contract/v2`를 자동 생성한다. 계약은 `schema_version`, `runtime_class`, configured `available_models` identity 또는 지원된 runtime metadata에서 해석한 `model_id`, 첫 vector의 실제 `dimension`, 이 값을 묶은 `fingerprint`로 구성된다. built-in node의 advanced `Dimensions`는 provider가 output-size override를 의도적으로 지원할 때만 설정하며, 저장·검색 계약은 이 값이 아니라 반환 vector length를 사용한다. 세 Flow의 Embedding Model에는 같은 승인 provider/model을 설정해야 하며, model ID를 해석할 수 없거나 runtime 계약이 다르면 검색·게시를 중단한다. Loader가 JSON array, `{items:[...]}` 또는 JSONL 파일 하나를 검증·정규화하고, Chunker가 redacted canonical text를 결정론적으로 분할하며, Writer가 built-in Embeddings handle로 vector를 만든 뒤 `catalog_assets`, `catalog_asset_chunks.embedding.vector`, `catalog_active_pointers`를 저장한다. Writer는 모든 parent/chunk/vector 검증 뒤에만 pointer를 전환하며 Catalog Worker나 다른 Langflow Flow를 HTTP API로 호출하지 않는다.

| Collection | 역할 |
| --- | --- |
| `catalog_assets` | 자산별 parent 메타데이터·redacted 원문. exact title/alias 조회와 최종 상세 정보의 권위 원본이다. |
| `catalog_asset_chunks` | parent를 나눈 검색 단위와 `embedding.vector`. lexical/vector 후보 검색은 이 collection에서 수행한다. |
| `catalog_active_pointers` | 모든 저장·count 검증을 통과한 active snapshot 하나를 가리키는 게시 포인터다. 실패한 적재는 이 값을 바꾸지 않는다. |

Langflow built-in `MongoDB Atlas` node는 이 Flow에서 사용하지 않는다. 고정 검증 런타임의 선택 의존성 import 결함이 있고, built-in의 기본 저장 모양·출력은 F20이 요구하는 nested `embedding.vector`, parent/chunk 분리, active pointer 게시 계약과 일치하지 않기 때문이다. MongoDB writer를 별도 standalone node로 둬 저장 단계는 명시적으로 보이게 하면서 F20 호환 계약을 유지한다.

첫 실행에는 `samples/f00_catalog_assets_example.json`을 Loader에 업로드한다. Flow export의 FileInput 값은 의도적으로 비어 있으며 로컬 repository 경로를 저장하지 않는다. Writer Canvas의 **테스트 실행 (저장하지 않음)**은 기본으로 켜져 있으며 내부 호환성 필드 `dry_run=true`를 사용해 file/record/chunk/hash만 확인하고 provider 또는 MongoDB를 호출하지 않는다. 다만 Langflow가 Writer의 입력을 해석할 때 연결된 Embedding Model node를 먼저 build할 수 있으므로, 테스트 실행 전에도 승인 provider/model과 build에 필요한 Secret은 설정한다. 이 단계에서는 실제 embedding 요청·1초 대기·MongoDB network가 일어나지 않는다. 따라서 결과의 runtime 계약은 `embedding_contract.state=DEFERRED`, `snapshot_id`는 `null`이다. 실제 저장은 테스트 실행을 끈 `dry_run=false` 상태에서 **전체 카탈로그 파일 확인 (실제 저장용)**도 켠 경우에만 수행된다. 청크가 남은 경우 durable background job에서는 Writer가 checkpoint 저장 뒤 native HITL 카드로 멈추며, 계속 적재를 선택하면 검증된 부분 chunk를 재사용해 다음 batch를 처리한다. 일반 Canvas Run Flow에서는 카드 없이 checkpoint를 남기고 `PARTIAL_EMBEDDINGS_SAVED`를 반환하므로 같은 전체 파일·chunk 정책·runtime 계약으로 새 실행해 이어쓴다. 마지막 pointer 갱신은 compare-and-swap으로 동시 실행의 오래된 결과가 최신 pointer를 되돌리지 못하게 한다.

F10은 실행 node 39개, Sticky Note 6개, edge 109개인 compact 상위 Flow다. 시작의 `Text Input` 두 개에서 업무 설명 원문과 추가 설계 프롬프트를 각각 받고, `10 업무 요청 Envelope`에는 팀 명·사번만 설정한다. 업무 추출 뒤 보완은 최대 3회이며 각 회차는 정확히 `12 완전성 평가 → 질문 LLM → 13 재질문 Batch → 42 보완 답변 HITL → 39 답변 반영·다음 단계`로 보인다. Component 42는 `graph.request_pause`의 `kind=node_input`과 `schema`를 사용해 생성된 질문마다 답변 필드 하나를 가진 Playground 카드와 `Submit Answers`/`추가 입력 건너뛰기`/`Cancel` 선택지를 표시한다. 1차 질문이 실제로 필요한 경우에만 Component 13이 질문 batch와 동일한 identity의 revision 0 WorkDefinition을 내부적으로 idempotent하게 준비한다. Component 39는 Component 42의 native 제출값을 `clarification_batches`에 감사용으로 먼저 기록한 뒤, 답변 검증·병합·revision CAS 저장·재평가를 수행해 다음 질문, 검토, 취소, 차단 중 정확히 하나만 연다. 세 번째 답변 뒤에도 blocking gap이 남으면 `CLARIFICATION_ROUND_LIMIT`으로 차단하며 네 번째 질문 카드나 4차 질문 회차는 만들지 않는다. `40 검토 진입 Joiner`는 초기/질문 전후/답변 후의 9개 review entry 중 유효한 성공 결과 하나만 골라 검토로 보낸다. 검토 단계의 Component 18 `review_and_request_approval`은 Preview 검증본을 저장하면서 `WAITING_APPROVAL`로 전환한다. Revision·중복 실행 방지 키·컬렉션은 자동/내부값이며 MongoDB URI는 자동 연결된 `MONGO_URL`, Database만 환경 설정이다. built-in Human Input의 최종 Approve, Reject, Cancel은 `43 최종 승인 경로 Gate`를 거쳐 하나의 Component 18 상태 명령만 열며, 선택하지 않은 상태 저장 node는 즉시 제외된다. `41 F10 결과 메시지`는 하나의 event-list 입력으로 모든 intentional cancel/reject/blocked terminal 결과를 민감정보 없이 짧게 표시한다. 승인 성공 경로의 `45 F10 인증 Context 경계`는 local demo fixture와 production trusted gateway identity를 구분하며, Component 36은 이 sealed context만 받아 canonical owner와 대조한다.

`14 Work Answer Loader`, `15 Work Answer Merger`, `34 Work Runtime State Store`, `35 Result Gate`, Answer Form/HITL API 연동, F11/Playground 분리 Flow, 4차 질문 회차는 현재 F10 Canvas 경로가 아니다. 14·15·34·35와 Answer Form/HITL API는 독립 검증 또는 과거 재사용을 위한 historical source/연동으로만 남아 있으며, 현재 실행 경로는 Component 42·39·40·41·43의 compact 계약을 사용한다.

`samples/f10_work_request_example.json`의 업무 설명·추가 프롬프트는 시작 Text Input 두 개에, 팀 명·사번은 `10 업무 요청 Envelope`에 로컬 데모 기본값으로 들어 있다. `session_id` 입력란은 없으며 Component 10이 Langflow 실행 session을 사용한다. 현재 공용 catalog/Skill 영역은 내부 `default` scope이므로 팀 명은 표시·감사 메타데이터다. production에서는 팀 명·사번과 Component 36 인증 주체를 trusted gateway가 주입하고, 팀별 격리가 필요할 때만 catalog/Skill scope도 함께 분리한다.

F10의 승인 성공 경로는 `36 Approved Design Invocation Loader → built-in TypeConverter → Run Flow` 순서다. Component 36은 승인 receipt를 MongoDB canonical WorkDefinition, active catalog pointer, active immutable Skill registry와 다시 대조한다. 인증 gateway가 주입한 subject가 업무 owner와 일치하고 canonical/request channel이 모두 정확히 `native_hitl`일 때만 `agent-design-invocation/v1`을 만든다. TypeConverter가 invocation의 strict JSON `text`를 Message로 바꾼 뒤 Langflow 1.11.1 `Run Flow` direct mode의 F20 입력으로 전달한다. HTTP API나 별도 F20 수동 실행은 사용하지 않으며 reject/cancel/blocked 경로는 F20을 호출하지 않는다. F20 Flow의 UUID가 import 과정에서 유지되어야 하며, remap된 경우 같은 프로젝트/폴더에서 Run Flow node의 F20 선택을 한 번 갱신한다.

F20과 F90은 각각 built-in `Embedding Model → 29 Query Embedding Batcher` edge로 query vector를 만든다. HTTP embedding endpoint/token node는 사용하지 않는다. F00과 같은 provider 경계이므로 29는 query batch 사이에 기본·최소 1초를 기다린다. F20은 단일 invocation 안의 승인 WorkDefinition, tenant/ACL, 활성 snapshot ID, 승인 Skill registry와 추가 설계 프롬프트를 Query Planner에서 `design_scope_sha256`/`query_plan_sha256`으로 고정한다. 내부 ChatInput은 `should_store_message=false`이고 엄격한 JSON만 TypeConverter로 파싱하므로 trusted invocation이 일반 대화 이력에 복제되지 않는다. F90도 동일한 하나의 ChatInput을 갖지만 **evaluation-only**이며 Component 36의 `Verified Design Invocation` 전체 JSON만 붙여 넣어야 한다. 원문 업무 설명이나 임의 invocation은 Query Planner의 approval/ACL/snapshot lock 검증에서 차단된다. 승인 Skill context를 추가 설계 프롬프트처럼 재사용하지 않는다. query embedding 결과는 두 lock과 runtime v2 계약을 보존하고 Retriever는 query plan canonical hash 및 active catalog runtime 계약을 재검증하며, Skill/Blueprint 단계는 design scope hash를 다시 계산한다. 추가 설계 프롬프트는 검색 query와 Blueprint prompt 양쪽에서 사용하지만 catalog/Skill 본문처럼 실행 지시로 취급하지 않는다. Run Flow의 tool mode는 꺼져 있고, 브라우저나 Agent가 권한성 child 입력을 고를 수 없다.

F30의 Publisher는 공유 HTML Report API에 맞춘 단일 `/reports` POST adapter다. 기본 `Report API URL`은 `http://127.0.0.1:5000`이고, base URL 또는 이미 `/reports`가 붙은 URL 모두 입력할 수 있다. 기본 테스트 실행은 HTML·URL·TTL만 검증하며 네트워크 요청을 보내지 않는다. 실제 게시에는 API가 `view_url`과 `download_url`을 반환해야 하며, 연결·HTTP·응답 형식 오류는 Flow 예외가 아니라 `PUBLISH_FAILED` 결과로 Chat Output에 표시된다.

## 재생성 및 drift 검사

```powershell
& 'C:\Users\qkekt\AppData\Local\Temp\business_work_design_lf1111\Scripts\python.exe' scripts\build_langflow_1_11_flows.py
& 'C:\Users\qkekt\AppData\Local\Temp\business_work_design_lf1111\Scripts\python.exe' scripts\build_langflow_1_11_flows.py --check
```

생성기는 실제 resolved runtime인 `langflow==1.11.1`, `langflow-base==0.11.5`, `lfx==1.11.5`에서만 실행된다.
