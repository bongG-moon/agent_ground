# 예제 데이터 기반 전체 E2E 테스트 가이드

이 문서는 예제 업무 설명과 예제 Langflow 자산 메타데이터를 사용해 다음 경로를 순서대로 검증한다.

```text
업무 설명(F10)
  -> native HITL 질문/답변 및 승인
  -> MongoDB canonical 승인본 재검증
  -> Run Flow direct mode로 F20 실행
  -> MongoDB hybrid search
  -> Agent Blueprint 및 Standalone Component 생성 요청
```

F00은 이 경로가 검색할 참고 자산을 만드는 선행 Flow다. 업로드 파일 하나를 같은 Flow 안에서 정규화·청킹·임베딩하고 MongoDB에 저장한다. F10에서 F20으로 넘어갈 때는 **운영 Langflow 1.11.0 호환** built-in `Run Flow`를 `tool_mode=false`로 사용한다. 다른 Flow의 HTTP API를 호출하거나 사용자가 F20에 WorkDefinition을 다시 붙여 넣는 방식은 허용하지 않는다.

## 1. 테스트에 사용하는 파일

| 파일 | 용도 | MongoDB write 여부 |
| --- | --- | --- |
| `samples/f10_work_request_example.json` | F10의 업무 설명, 추가 설계 프롬프트, 팀 명·사번 예제 | 파일 자체는 쓰지 않음 |
| `samples/f00_catalog_assets_example.json` | F00에 업로드할 Component/Flow 참고 메타데이터 | F00 live 실행 시 저장 |
| `samples/skill_registry_example.json` | Component 36과 F20이 사용할 승인 Skill registry 예제 | seed 스크립트에 `--apply`를 줄 때만 저장 |
| `scripts/seed_example_skill_registry.py` | Skill JSON 계약 검증 및 선택적 MongoDB upsert | 기본 실행은 검증만 수행 |
| `scripts/verify_example_mongodb.py` | active catalog pointer, parent/chunk/vector와 Skill registry 일관성 확인 | 읽기 전용 |

예제 업무는 “메일·CUBE/Teams·JIRA·DataLake 생산 KPI·Hold/설비 이벤트·SOP를 함께 확인해 프로젝트·설비별 주간 리스크와 실행계획을 만들고, 데이터 오류·고위험·근거 상충·승인 반려를 분기 처리한 뒤 사람 승인 후에만 GoodDocs/CUBE에 게시한다”는 복합 시나리오다. 대상 범위·고위험 임계값·권위 데이터·승인/알림 정책·SLA 일부를 일부러 확정하지 않아 F10의 재질문을 확인할 수 있다. 참고 catalog에는 기존 메일 업무보고·Outlook·GoodDocs·JIRA·HiQ1 자산과, 메일·회의·문서·RAG·DataLake·h-API·HITL·승인·CUBE·보고서·제조 업무까지 연결할 수 있는 후보를 포함한 총 100건이 들어 있다. 모두 예제용 `metadata_only` 후보이므로 F20이 재사용 후보와 실제 runtime 검증 필요성을 구분하는지도 확인한다. 예제 Skill은 승인 전 게시와 근거 없는 완료 처리를 금지한다.

세 JSON은 모두 테스트 fixture다. API key, MongoDB credential, 사내 원문, 실제 사용자 정보는 예제 JSON에 추가하지 않는다. Secret은 Langflow Secret/Global 또는 사내 secret manager로 전달한다.

예제 전체 경로는 `default`, `internal-assets`, `employee-demo`를 각각 tenant, catalog, owner로 사용한다. F00 Loader 화면에서는 tenant/catalog을 입력하지 않고, 내부적으로 이 고정 scope를 bundle과 MongoDB 문서에 기록한다. F10의 `owner_id`와 Component 45 local demo fixture의 `subject_id`는 모두 `employee-demo`여야 한다. 이 fixture는 sample 확인용 `authenticated_subject_verified=false`이며, 운영에서는 trusted gateway context의 subject가 owner와 일치해야 한다. 하나라도 다르면 정상적으로 fail-closed 된다.

## 2. 사전 준비

### 2.1 운영/검증 런타임 구분

- **운영 F10 UI 및 새 Standalone 생성 요청 호환 기준:** Langflow `1.11.0`
- **source/template build 검증 기준:** Langflow `1.11.1`
- `langflow-base==0.11.5`
- `lfx==1.11.5`
- MongoDB 연결 계정
- 승인된 업무 추출/설계 LLM
- 승인된 embedding provider
- F20 live 검색까지 할 경우 MongoDB Search/Vector Search를 제공하는 환경과 index

아래는 source/template의 고정 검증 환경을 사용하는 예시다. F10 Playground의 실제 choice-only HITL과 새 Component의 운영 smoke test는 1.11.0 환경에서도 별도로 확인한다.

```powershell
$ProjectRoot = 'C:\Users\qkekt\Desktop\Agent_ground\business_work_design_agent'
$Python = 'C:\Users\qkekt\AppData\Local\Temp\business_work_design_lf1111\Scripts\python.exe'
$Lfx = 'C:\Users\qkekt\AppData\Local\Temp\business_work_design_lf1111\Scripts\lfx.exe'
Set-Location $ProjectRoot

& $Python --version
& $Python -m pip show langflow langflow-base lfx pymongo
```

다른 가상환경을 사용한다면 위 두 실행 파일 경로만 바꾸되 세 Langflow package version은 유지한다.

### 2.2 테스트용 MongoDB scope

운영 DB가 아닌 별도의 테스트 DB 사용을 권장한다. 아래 값은 모든 Flow와 두 helper script에서 동일해야 한다.

```powershell
$env:MONGODB_URI = '<test MongoDB URI>'
$env:MONGODB_DATABASE = 'business_work_design_e2e'
```

URI를 shell history, 화면 캡처 또는 문서에 남기지 않는다. 기존 active pointer 보존 검증이 필요하므로 동일 테스트 중에는 DB를 중간에 초기화하지 않는다.

### 2.2.1 Langflow 공통 URI 변수

Langflow Settings의 Global Variables에서 Secret 형식의 **`MONGO_URL`**을 한 번 만든다. 이번 export의 F00·F10·F20·F90 MongoDB URI 입력은 모두 이 변수에 자동 연결되어 있으므로 node마다 URI를 다시 입력하거나 선택할 필요가 없다. `MONGO_URL`에는 위 PowerShell의 `$env:MONGODB_URI`와 같은 테스트 연결 문자열을 넣되, Flow JSON에는 실제 값이 저장되지 않는다. `$env:MONGODB_URI`는 아래 seed/verify helper script 전용 환경 변수다.

필요한 기본 collection은 다음과 같다.

| 영역 | Collection |
| --- | --- |
| Catalog | `catalog_assets`, `catalog_asset_chunks`, `catalog_active_pointers` |
| Skill | `skill_registry` |
| WorkDefinition | `work_definitions`, `work_definition_events` |
| Clarification | `clarification_batches` |

`MONGODB_COLLECTION_PREFIX`는 비워 둔다. 현재 F00, F10의 Component 13/18/39, Component 36과 검증 스크립트가 canonical collection 이름을 공유한다. F10 Playground-native 답변 경로에는 HITL API가 없다.

F00이 쓰는 세 collection의 역할은 다음과 같다.

| Collection | 역할 |
| --- | --- |
| `catalog_assets` | asset별 parent 메타데이터·redacted 원문을 한 번 저장한다. exact title/alias 조회와 최종 상세 정보의 권위 원본이다. |
| `catalog_asset_chunks` | asset을 나눈 검색 chunk와 `embedding.vector`를 저장한다. lexical/vector 검색 후보는 여기서 찾는다. |
| `catalog_active_pointers` | parent/chunk/vector 검증을 모두 통과한 snapshot만 active로 가리킨다. 중간 실패 snapshot은 F20 검색에 노출되지 않는다. |

### 2.3 Runtime embedding contract v2

F00, F20, F90에는 각각 built-in `Embedding Model` node가 있으며 모두 같은 승인 provider/model과 credential을 사용해야 한다. Writer와 Component 29에는 model/version/dimension 또는 HTTP endpoint/token 입력이 없다.

live 실행에서 Writer 또는 Component 29는 Embeddings runtime의 `schema_version`, `runtime_class`, configured `available_models` identity 또는 지원된 runtime metadata에서 해석한 `model_id`, 첫 vector의 실제 `dimension`, 이 값을 묶은 `fingerprint`로 `embedding-runtime-contract/v2` 계약을 만든다. generic Embeddings handle의 임의 속성에서 model/version을 추측하지 않으며, `model_id`를 해석할 수 없으면 fail-closed한다. active pointer의 v2 계약과 F20/F90 query vector 계약이 하나라도 다르면 `QUERY_EMBEDDING_CONTRACT_MISMATCH`로 검색이 중단되어야 한다.

built-in node의 `chunk_size`는 provider 내부 요청 설정이고 catalog text 분할 크기는 `01 Deterministic Chunker`의 `chunk_chars`와 별개다. F00 Writer는 provider 요청에 청크를 묶지 않고 정확히 한 청크씩 `embed_documents([chunk])`로 호출하며, 다음 호출과 일시 오류 재시도 전 `임베딩 호출 간격(초)`만큼 대기한다. 기본값과 최소값은 1초다. 첫 live 실행은 새 청크 최대 80개 또는 Writer 내부 180초까지만 처리하고 10개씩 MongoDB에 checkpoint를 남긴다. 100건 예제처럼 새 청크가 남은 경우 `부분 적재 후 계속 여부 확인 (HITL)=true`여도 **Langflow durable background job**에서만 `WAITING_INGESTION_CONTINUATION` native 카드가 열리고 처리 수·남은 수를 표시한다. **계속 적재**를 누르면 같은 Writer가 checkpoint에서 다음 bounded batch를 처리하고, 마지막 batch에서만 `ACTIVE`와 active pointer 갱신이 발생한다. **중단하고 나중에 실행**을 누르면 checkpoint는 보존되지만 active pointer는 바뀌지 않는다. 일반 Canvas Run Flow는 durable job을 만들지 않으므로 `PARTIAL_EMBEDDINGS_SAVED`, `hitl.available=false`, `hitl.reason=durable_background_job_required`를 반환하며, 같은 전체 파일·chunk 정책·Embedding Model 설정으로 새 실행을 시작한다. `MongoDB 저장 체크포인트 청크 수`는 vector가 만들어진 뒤 MongoDB에 checkpoint로 저장하는 문서 수이며 청크 분할이나 embedding 호출 횟수를 바꾸지 않는다. `MongoDB 연결·서버 선택 제한 시간 (ms)`는 MongoDB 연결과 서버 선택에 적용된다. 소켓 read/write는 적재 안정성을 위해 최소 10초를 사용하며, 두 값 모두 Embedding Model 호출에는 적용되지 않는다. advanced `Dimensions`는 provider가 output-size override를 의도적으로 지원할 때만 설정하고, 기본값은 비워 둔다. Writer와 Retriever는 이 UI 값이 아니라 실제 반환 vector length를 사용한다. Canvas의 **테스트 실행 (저장하지 않음)**은 기본으로 켜져 있고 내부 `dry_run=true`로 실제 embedding 요청/MongoDB를 호출하지 않기 때문에 `embedding_contract.state`는 `DEFERRED`, snapshot은 `null`이다. 단, Langflow graph는 Writer를 build하기 전에 연결된 Embedding Model node를 build할 수 있으므로, 이 테스트 단계에서도 승인 provider/model과 해당 provider가 build에 요구하는 Secret을 설정해야 한다. 이는 실제 vector API 호출이나 1초 대기를 뜻하지 않는다.

### 2.4 MongoDB Search index

F00은 vector를 저장하지만 Search index를 만들지 않는다. F20 live 검색 전에 `catalog_asset_chunks`에 다음 두 index가 준비되어야 한다.

- lexical index 기본 이름: `catalog_lexical`
- vector index 기본 이름: `catalog_vector`
- vector path: `embedding.vector`
- vector 차원: F00의 첫 live vector에서 해석된 active pointer `embedding_contract.dimension`과 동일
- lexical path: `title`, `description`, `lexical_text_redacted`, `category`
- filter scope: `tenant_id`, `snapshot_id`, `asset_type`, `acl.visibility`, `acl.groups`, `acl.subjects`

실제 index definition 문법과 지원 operator는 사용 중인 MongoDB 배포판에서 확인한다. index가 없더라도 F00 저장 검증은 가능하지만 F20/F90 live 검색 합격으로 처리할 수 없다.

## 3. 1단계: 정적 검증

먼저 network나 MongoDB write 없이 source, standalone import, Flow graph를 검증한다.

```powershell
Set-Location $ProjectRoot

& $Python scripts\build_langflow_1_11_flows.py --check
if ($LASTEXITCODE -ne 0) { throw 'Flow generator drift 검사 실패' }

& $Python scripts\validate_langflow_1_11_runtime.py
if ($LASTEXITCODE -ne 0) { throw 'Langflow runtime 검사 실패' }

$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
& $Python -m pytest -q tests --basetemp=.pytest-tmp-example-e2e
if ($LASTEXITCODE -ne 0) { throw 'pytest 실패' }

& $Python -m compileall -q components services scripts tests
if ($LASTEXITCODE -ne 0) { throw 'compileall 실패' }
```

선택적으로 각 Flow를 `lfx validate`로도 확인한다.

```powershell
$FlowFiles = @(
  'flows\F00_catalog_file_vector_ingest.json',
  'flows\F10_work_definition_parent.json',
  'flows\F20_agent_blueprint_design.json',
  'flows\F30_responsive_report.json',
  'flows\F90_search_evaluation.json'
)

foreach ($FlowFile in $FlowFiles) {
  & $Lfx validate $FlowFile --level 3 --format json
  if ($LASTEXITCODE -ne 0) { throw "lfx validate 실패: $FlowFile" }
}
```

합격 기준:

- generator `--check`에서 drift 없음
- runtime validator가 Flow 5개와 embedded Standalone source를 모두 통과
- pytest 실패 0건
- compile 오류 0건
- `lfx validate` error 0건

`lfx`의 generic edge type heuristic warning은 error와 분리해 본다. 기준 수치는 `docs/VALIDATION_REPORT.md`의 최신 실행 결과와 비교한다.

## 4. 2단계: F00 테스트 실행 (저장하지 않음)

### 4.1 Flow import

Langflow 1.11.1에서 `flows/F00_catalog_file_vector_ingest.json`을 개별 Flow로 import한다. 이관용 `00_business_work_design_ALL_FLOWS.json`을 단일 Flow import 화면에 넣지 않는다.

F00 graph가 다음 실행 node 6개와 edge 5개를 갖는지 먼저 확인한다. Canvas 상단에는 설명 전용 Sticky Note 2개가 표시되며 edge에는 포함되지 않는다.

```text
00 Catalog JSON Loader (standalone)
  -> 01 Deterministic Chunker (standalone)
  -> 02 MongoDB Catalog Vector Writer (standalone)
  -> Data to Message
  -> Chat Output

Embedding Model (built-in)
  -> 02 MongoDB Catalog Vector Writer
```

마지막 저장 node는 Langflow built-in MongoDB Atlas가 아니라 프로젝트의 standalone Writer여야 한다. pinned runtime의 Atlas 선택 의존성 import 결함을 피하고 F20이 조회하는 `catalog_asset_chunks.embedding.vector` 및 `catalog_active_pointers` 계약을 동일하게 유지하기 위한 구성이다.

### 4.2 Node 입력

Canvas에서 각 입력이 담당 node에 나뉘어 보이는지 확인하고 다음처럼 설정한다.

| Node | 필드 | 값 |
| --- | --- | --- |
| 00 Loader | Catalog JSON File | `samples/f00_catalog_assets_example.json` |
| 00 Loader | Internal storage scope | 화면 입력 없음. 내부 고정 `tenant_id=default`, `catalog_id=internal-assets` |
| 01 Chunker | Chunk Size / Overlap | live 단계에서 사용할 동일 정책 |
| Embedding Model | Model / Provider / Advanced Dimensions | live 단계에서 사용할 승인 모델. Dimensions는 provider output-size override가 필요할 때만 설정하고 기본은 비움 |
| 02 Writer | MongoDB Database / Collections | 테스트 DB와 canonical 세 collection |
| 02 Writer (advanced) | 실행 1회당 신규 임베딩 청크 수 / 실행 최대 처리 시간 (초) / 임베딩 호출 간격(초) / MongoDB 저장 체크포인트 청크 수 / MongoDB 연결·서버 선택 제한 시간 | 기본값은 새 청크 80개 또는 180초까지만 처리하고, 10개마다 checkpoint를 저장한다. 실제 저장 시 청크 1개씩 순차 임베딩하고 첫 호출 뒤부터 최소 1초 간격을 적용한다. checkpoint 저장 간격은 임베딩 호출 단위와 별개이며 socket read/write는 안정성을 위해 최소 10초를 사용한다. |
| 02 Writer | 테스트 실행 (저장하지 않음) | `true` |
| 02 Writer | 전체 카탈로그 파일 확인 (실제 저장용) | `false` 유지. 테스트 실행에는 필요 없으며 live publish 전에만 켠다. |

테스트 실행에서는 MongoDB URI가 없어도 된다. 그러나 `Embedding Model → Writer` 연결 때문에 Langflow가 upstream Embedding Model을 먼저 build할 수 있으므로, 승인 provider/model과 해당 provider가 build에 요구하는 API Secret은 미리 설정한다. Writer는 `dry_run=true`에서 embedding API, MongoDB network, 임베딩 호출 간 대기를 실행하지 않는다. Loader의 파일 검증·민감정보 제거·canonical text, Chunker의 chunk/hash, Writer의 bundle 검증까지만 수행하며 runtime contract와 snapshot은 이 단계에서 의도적으로 생성하지 않는다. F00은 전체 snapshot 교체 방식이므로 live publish에 사용할 파일은 반드시 현재 전체 catalog여야 하며 신규분만 든 delta 파일을 사용하면 안 된다.

### 4.3 실행 결과

Flow를 실행하고 Chat Output JSON을 저장한다. 합격 기준은 다음과 같다.

- `ok=true`
- `status="DRY_RUN_VALIDATED"`
- 화면에는 **테스트 실행 (저장하지 않음)**, 내부 계약에는 `dry_run=true`
- `message="테스트 실행입니다. MongoDB에는 저장하지 않았습니다."`
- `tenant_id="default"`, `catalog_id="internal-assets"`
- `counts.records=100`
- `counts.chunks`가 1 이상
- `embedding_contract.state="DEFERRED"`, `snapshot_id=null`
- `source_sha256`, `ingest_sha256` 존재
- 응답에 원문 record 전체, API key 또는 vector 배열이 없음

같은 파일과 같은 chunk 정책으로 두 번 실행했을 때 `source_sha256`, `ingest_sha256`가 같아야 한다. 이 단계에서 MongoDB collection이나 active pointer가 새로 생기면 실패다.

## 5. 3단계: F00 live MongoDB/embedding ingest

같은 F00 Flow에서 Writer의 **테스트 실행 (저장하지 않음)**을 끄고, 업로드 파일이 현재 전체 catalog임을 확인한 뒤 **전체 카탈로그 파일 확인 (실제 저장용)**도 켠다. 내부 필드명은 각각 `dry_run=false`, `confirm_complete_catalog_snapshot=true`다. 확인값을 켜지 않으면 live write는 시작하지 않고 `FULL_SNAPSHOT_CONFIRMATION_REQUIRED`로 차단된다.

| Node | 필드 | 설정 원칙 |
| --- | --- | --- |
| 00 Loader | File / Internal scope | 파일만 업로드. scope는 내부 고정 `default` / `internal-assets` |
| 01 Chunker | Chunk Size / Overlap / Limits | 테스트 실행과 동일 |
| Embedding Model | Model / Provider / API Key / Advanced Dimensions | 승인 model/provider, key는 Secret으로만 입력. Dimensions는 provider output-size override일 때만 사용 |
| 02 Writer | MongoDB URI | 자동 연결된 Langflow Secret `MONGO_URL` (값은 `$env:MONGODB_URI`와 같은 테스트 DB credential) |
| 02 Writer | MongoDB Database | `$env:MONGODB_DATABASE`와 동일 |
| 02 Writer | Assets / Chunks / Pointer Collections | `catalog_assets`, `catalog_asset_chunks`, `catalog_active_pointers` |
| 02 Writer | 전체 카탈로그 파일 확인 (실제 저장용) | `true`; 이 업로드가 현재 전체 catalog라는 운영자 확인 |
| 02 Writer | 부분 적재 후 계속 여부 확인 (HITL) | `true`; **durable background job**에서만 부분 checkpoint 뒤 Continue/Stop 카드 표시. Canvas Run Flow는 checkpoint 저장 후 재실행 방식 |
| 02 Writer | 실행 1회당 신규 임베딩 청크 수 | 카드 동작 검증은 `20`, 일반 예제 실행은 기본 `80` |

첫 번째 live 실행의 합격 기준:

- `ok=true`, `dry_run=false`
- 새 청크가 기본 한도(80개) 또는 180초 안에 모두 끝나면 `status="ACTIVE"`; 남고 **durable background job**에서 HITL이 켜져 있으면 `status="WAITING_INGESTION_CONTINUATION"`과 `resume.request_id`가 나온다.
- `WAITING_INGESTION_CONTINUATION`이면 Playground 카드의 처리 수·남은 수를 확인한 뒤 **계속 적재** 또는 **중단하고 나중에 실행**을 선택한다. 계속 적재의 마지막 batch에서만 `status="ACTIVE"`와 active pointer 갱신이 발생한다.
- Continue/Stop 카드를 눈으로 시험하려면 durable background job에서 예제 100건의 `실행 1회당 신규 임베딩 청크 수=20`으로 설정한다. 일반 Canvas Run Flow는 카드를 열지 않는다.
- Canvas Run Flow, HITL이 꺼진 실행 또는 durable job이 없는 실행에서는 `status="PARTIAL_EMBEDDINGS_SAVED"`, `hitl.reason="durable_background_job_required"`(Canvas인 경우), `progress.next_run_required=true`, `progress.remaining_chunks`를 확인하고 **같은 전체 파일·chunk 정책·Embedding Model 설정으로 다시 실행**한다.
- `counts.records`와 예제 record 수가 동일
- `counts.chunks == counts.vectors`
- `embedding_contract`가 `embedding-runtime-contract/v2`, `schema_version`, `runtime_class`, `model_id`, `dimension`, `fingerprint`를 모두 포함
- 같은 파일·chunk 정책·runtime v2 계약으로 재실행하면 같은 deterministic `snapshot_id`
- `catalog_active_pointers`가 모든 parent/chunk/vector 저장과 count 확인 뒤에만 해당 snapshot으로 전환

최종 `ACTIVE` 이후 동일 파일을 한 번 더 실행한다. parent/chunk document가 중복 증가하지 않고 같은 snapshot이 유지되어야 하며, 결과는 `status="ACTIVE_ALREADY_CURRENT"`일 수 있다. 이 경우 Writer는 runtime contract 확인용 첫 chunk만 probe하고 parent/chunk write와 pointer 전환을 하지 않는다.

장애 안전성은 별도로 확인한다.

1. 현재 active pointer ID를 기록한다.
2. model identity를 해석할 수 없는 provider runtime을 쓰거나 F20/F90에 F00과 다른 model을 설정해 한 번 실행한다.
3. `ok=false`이고 `QUERY_EMBEDDING_CONTRACT_MISMATCH`, `EMBEDDING_PROVIDER_FAILED`, `CATALOG_ACTIVATION_CONFLICT` 또는 명시적 validation 오류인지 확인한다.
4. 기존 active pointer가 1번 값에서 바뀌지 않았는지 확인한다.
5. 올바른 설정으로 복구한다.

부분 적재 복원력도 시험한다. live test DB에서 vector 두 개 이상이 저장된 뒤 provider를 중단해 pointer가 바뀌지 않았는지 확인하고, 같은 전체 파일·chunk 정책·Embedding Model로 다시 실행한다. `resume_verified_partial_snapshot=true`일 때 결과의 `embedding_execution.resumed_vectors`가 0보다 크고, 재사용 청크가 hash·runtime contract·finite vector를 다시 검증한 것인지 확인한다. 다른 파일, 다른 정책, 다른 model/runtime 계약에는 재사용하면 안 된다.

실패 시험용 값은 테스트 DB와 테스트 credential에서만 사용한다.

## 6. 4단계: 예제 Skill registry 검증 및 저장

먼저 `--apply` 없이 파일 계약만 검증한다.

```powershell
& $Python scripts\seed_example_skill_registry.py
if ($LASTEXITCODE -ne 0) { throw 'Skill registry 예제 계약 검증 실패' }
```

출력의 `ok=true`, `status="VALIDATED_ONLY"`, `apply_required_for_write=true`, `skill_count=1`을 확인한다. 이 실행은 MongoDB에 쓰면 안 된다. 실제 테스트 DB에 upsert할 때만 `--apply`를 붙인다.

```powershell
& $Python scripts\seed_example_skill_registry.py `
  --sample samples\skill_registry_example.json `
  --mongodb-uri $env:MONGODB_URI `
  --database $env:MONGODB_DATABASE `
  --collection skill_registry `
  --apply
if ($LASTEXITCODE -ne 0) { throw 'Skill registry seed 실패' }
```

출력의 `ok=true`, `status="APPLIED"`, `skill_count=1`을 확인한다. 같은 명령을 두 번 실행해 동일 Skill identity가 중복 insert되지 않고 deterministic upsert/replay가 되는지 확인한다. 두 번째 실행은 보통 `upserted_count=0`이고 동일 문서가 match되어야 한다.

합격 기준:

- `tenant_id`가 `default`
- `status`가 정확히 소문자 `active`
- `skill_id`, `version`, prompt 본문과 `prompt_sha256` 일치
- timezone을 포함한 `approved_at`과 비어 있지 않은 `approved_by` 존재
- 예제 ACL이 `visibility="tenant"`이고 비어 있는 group/subject 배열을 가짐
- prompt에 credential literal이나 실행 secret이 없음

## 7. 5단계: F20·F30·F10 import 및 설정

### 7.1 Import 순서

같은 Langflow project/folder에 다음 순서로 개별 import한다.

1. `flows/F20_agent_blueprint_design.json`
2. `flows/F30_responsive_report.json`
3. `flows/F10_work_definition_parent.json`

F10의 Run Flow node가 export에 기록된 F20/F30 UUID를 유지하는지 확인한다. import 과정에서 UUID가 remap되었다면 `Run Agent Blueprint Design` node와 `Run Responsive Report` node에서 같은 project의 child Flow를 각각 한 번 다시 선택하고 저장한다.

다음 불변조건을 확인한다.

- Run Flow `Tool Mode=false`
- Run Flow `Cache Flow=false`
- F20 Chat Input `Store Messages=false`
- F20에는 Human Input이 없음
- F10 approve success만 `36 → TypeConverter → Run Flow(F20 direct) → 44 handoff gate → Run Flow(F30 direct)`로 연결
- Component 36 또는 Component 44 blocked output은 `41 F10 결과 메시지 → Chat Output` terminal 경로로 끝남
- F10→F20→F30 사이에 HTTP Request/API node가 없음

### 7.2 F20 설정

F20에서 다음을 설정한다.

- built-in `Embedding Model`: F00/F90과 같은 승인 provider/model과 Secret. advanced `Dimensions`는 provider output-size override가 필요한 경우만 설정
- `29 검색 Query Embedding Batcher`: built-in Embeddings handle, query ID 보존, runtime v2 contract 생성. HTTP endpoint/token/model/version/dimension 입력은 없음
- `21 Catalog Hybrid Retriever`: 자동 연결된 `MONGO_URL`, 테스트 DB, `catalog_asset_chunks`, `catalog_assets`, `catalog_active_pointers`, `catalog_lexical`, `catalog_vector`
- `Blueprint Model`: 승인된 구조화 출력 LLM
- provider mode는 첫 E2E에서 기본 `application_rrf` 사용을 권장

F20을 사용자가 직접 실행해 임의 invocation을 붙여 넣는 것은 production 경로 시험이 아니다. F20 설정 확인 후 실제 입력은 F10 승인 성공 경로의 Component 36과 Run Flow가 전달하게 둔다.

F30 Publisher는 첫 E2E에서 `테스트 실행 (저장하지 않음)=true`를 유지한다. 기본 `Report API URL`은 `http://127.0.0.1:5000`이고 base URL 또는 `/reports` endpoint를 넣을 수 있다. 실제 게시은 공유 HTML Report API가 준비된 환경에서만 `dry_run=false`로 확인한다.

### 7.3 F10 공통 설정

F10은 같은 Component source가 여러 회차 node로 펼쳐져 있다. 아래 설정을 해당 source의 모든 node instance에 적용한다.

| 대상 | 필수 설정 |
| --- | --- |
| Component 13 질문 batch (3개 모두) | 자동 연결된 `MONGO_URL`/DB, 내부 `clarification_batches`, `work_definitions`; 1차 질문이 필요하면 revision 0 WorkDefinition을 idempotent 준비 |
| Component 42 보완 답변 HITL (3개 모두) | 외부 설정 없음. `질문 Batch`만 Component 13에서 연결하며, Playground input schema와 답변 type 안내를 자동 생성 |
| Component 39 답변 반영 (3개 모두) | 자동 연결된 `MONGO_URL`/DB, 내부 `work_definitions`, `clarification_batches`; 사번은 batch/업무 컨텍스트에서 자동 검증 |
| Component 18 WorkDefinition store | 자동 연결된 `MONGO_URL`/DB만 환경 설정. 사번·revision·중복 실행 방지 키는 자동 연결/계산하며 `work_definitions`와 `work_definition_events`는 내부 고정 |
| Component 45 인증 Context 경계 | import 직후에는 `local_demo_fixture`와 자동 연결된 사번 기반 실행자를 사용한다. 운영에서는 `trusted_gateway`로 바꾸고 gateway의 subject/group output만 연결한다. |
| Component 36 invocation loader | 자동 연결된 `MONGO_URL`/DB, work/pointer/skill collection, Component 45의 sealed authentication context |
| Extraction/Clarification Model | 승인된 구조화 출력 LLM |

Component 40 Joiner와 Component 41 terminal message에는 별도 외부 설정이 없다. `34_work_runtime_state_store.py`, `35_result_gate.py`, F11/Playground 분리 Flow는 현행 compact F10 Canvas 경로가 아니므로 이 import 절차에서 설정하지 않는다. URI와 token은 node마다 복사해 평문 저장하지 말고 자동 연결된 Langflow Secret Global `MONGO_URL`을 재사용한다. Component 36에는 browser 입력이 아니라 Component 45의 sealed context만 연결한다. 로컬 E2E의 `local_demo_fixture`는 예제 owner와 같은 고정 subject를 쓰되 명시적으로 unverified이며, 운영 설계로 간주하지 않는다.

### 7.4 예제 업무 설명 입력

F10 export에는 `samples/f10_work_request_example.json`의 업무 설명과 추가 프롬프트가 시작 Text Input 두 개에, 팀 명과 사번이 `10 업무 요청 Envelope`에 기본값으로 들어 있다. import 후 값과 연결선을 확인한다. `session_id`는 화면 입력이 아니며 Component 10이 현재 Langflow 실행 session을 자동 사용한다.

| 예제 JSON 필드 | F10 Canvas 입력 |
| --- | --- |
| `request_text` | `업무 설명 원문` Text Input |
| `additional_prompt` | `추가 설계 프롬프트` Text Input |
| `team_name` | 팀 명 |
| `employee_id` | 사번 (`employee-demo`) |

예제의 `expected_clarification_topics`, `expected_design_signals`, `clarification_skip_guidance`, `local_test_note`는 결과를 사람이 확인하기 위한 설명이며 Component 10 입력이 아니다. `language`는 예제 Flow의 기본값 `ko`를 유지한다. `channel_mode`는 입력으로 노출하지 않고 Component 10이 정확히 `native_hitl`로 고정한다. `work_definition_id`, `turn_id`, `submitted_at`은 첫 실행에서는 비워 deterministic/default 생성을 사용할 수 있다. 공용 catalog/Skill 영역은 내부 `default` scope를 계속 사용하므로 `team_name`은 현재 팀별 catalog 격리가 아니라 표시·감사 메타데이터다.

## 8. 6단계: F10 Playground-native HITL 전체 실행

이 E2E는 실제 사용자가 보게 되는 Playground 입력 화면을 확인하는 시험이다. F10을 **Playground에서 직접 실행**한다. 외부 Answer Form 웹 서버, `HITL_API_BEARER_TOKEN`, 질문 batch 등록 HTTP 호출, 수동 `/resume` 요청은 이 시험에 사용하지 않는다.

### 8.1 시작과 질문 카드 확인

1. F10 Canvas의 `업무 설명 원문`, `추가 설계 프롬프트`, 팀 명, 사번이 7.4의 예제 값인지 확인한다. 필요한 경우 `samples/f10_work_request_example.json`의 해당 값으로 바꾼다.
2. Component 13 (세 회차), Component 39 (세 회차), Component 18, Component 36과 F20 Retriever가 공통 Langflow Secret `MONGO_URL`을 참조하는지 확인한다. URI는 card에 직접 평문으로 입력하지 않고 export가 자동 연결한 Global Variable를 사용한다. 격리 테스트라면 Database만 `business_work_design_e2e`로 바꾼다.
3. Playground에서 F10을 실행한다. 예제 업무는 일부 조건이 비어 있으므로 Component 12가 보완을 요구하면 Component 13이 `clarification_batches`에 `WAITING_ANSWER` batch를 만든다. 실행 전에 transaction 가능한 MongoDB replica set/Atlas와 F10 unique/TTL index를 배포 preflight로 확인한다.
4. 응답 화면에 **`Waiting for Human Input`** 카드가 보이면, 질문 문구 아래에 `answer_01` 등 질문 수만큼 실제 입력칸이 표시되는지 확인한다. 이 카드는 `42 보완 답변 HITL`이 보낸 `kind=node_input` + `schema` 카드다. 같은 카드에는 `Submit Answers`, **`추가 입력 건너뛰기` (`Skip Additional Input`)**, `Cancel` 중 하나를 선택하는 action이 보여야 한다.

`Submit Answers`/`추가 입력 건너뛰기`/`Cancel` 버튼만 있고 입력칸이 없다면 과거 built-in `Human Input` export를 연 것이다. 실행을 중단하고 최신 F10과 `42_f10_clarification_answer_gate.py` source를 다시 import/build한 뒤 **새 job**으로 재시작한다. 기존 suspend job은 새 schema를 받지 않는다.

### 8.2 카드 안에서 답변 입력·제출

질문별 입력칸에 답변을 넣는다. 질문 문구와 카드의 형식 안내를 우선한다. F10 Canvas의 `② 최대 3회 HITL 보완` Sticky Note에도 아래와 같은 값이 표시되며, `samples/f10_work_request_example.json`의 `clarification_answer_examples`와 동일하다.

- 대상 범위·고위험 임계값 질문: `A공장 확산·식각 공정의 프로젝트 Alpha, Beta와 담당 설비를 대상으로 합니다. Hold 건수가 전주보다 3건 이상 증가하거나 수율이 목표 대비 2% 이상 낮거나, Severity High JIRA 또는 SLA 24시간 초과가 있으면 고위험 검토로 보냅니다.`
- 권위 데이터·근거 연결 질문: `수율·Hold·설비 이벤트는 DataLake, 이슈 상태와 담당자·목표일은 JIRA, SOP·변경 공지는 승인된 최신 문서를 권위 기준으로 사용합니다. 프로젝트 코드·설비 ID·LOT ID·JIRA Key·주차로 연결하고 키가 맞지 않으면 자동 연결하지 않고 사람 검토로 보냅니다.`
- 승인·게시·알림 권한 질문: `고위험 항목은 품질 담당자와 공정 책임자가 검토하고, 팀장이 GoodDocs 최종 게시와 전체 보고서를 승인합니다. 승인 전에는 GoodDocs 확정, JIRA 신규 등록, CUBE 또는 Teams 알림을 모두 실행하지 않습니다.`
- 실패·SLA 질문: `각 데이터 소스 조회와 게시·알림은 5분 간격으로 한 번만 재시도합니다. 필수 데이터가 없으면 게시와 알림을 중단하고 실패 원인·영향 범위·누락 건수를 담당자와 품질 담당자에게만 보여 줍니다.`

선택형 질문은 안내된 선택지 하나를 정확히 입력한다. 복수 선택은 쉼표로 구분하고, boolean은 `true/false` 또는 `예/아니오`, number는 숫자로 입력한다. 기타 설명을 포함하는 단일 선택은 `{"choice":"__other__","text":"설명"}` 형식을 사용한다.

필수 입력을 채운 뒤 **같은 카드에서** `Submit Answers`를 선택한다. Playground가 정상 인증 session으로 `action_id=submit_answers` 및 `decision.values`를 재개하므로 사용자가 API key, `request_id`, `expected_revision`, `idempotency_key`, `tenant_id`를 입력할 필요가 없다.

Component 42가 안전한 field 이름을 원래 질문 ID와 답변 type으로 복원하고, Component 39가 MongoDB canonical batch·사번·deadline·revision을 다시 확인해 답변과 semantic revision 증가를 CAS로 한 번만 저장한다. 유효한 새 revision만 다음 Component 12 완전성 평가로 전달된다.

### 8.2.1 답변 대신 추가 입력을 건너뛸 때

현재 카드에 대한 추가 정보가 없거나, 미확정 상태를 Preview에서 검토하기로 했다면 입력칸을 비워 둔 채 **`추가 입력 건너뛰기`**를 선택한다. 이는 `Cancel`이 아니다.

1. Component 42는 `skip_additional_input` native event를 만들고, 현재 card에 표시된 질문 전체를 `skipped_question_ids`로 보낸다. 일부 질문만 빈 값으로 제출하는 방식은 사용하지 않는다.
2. Component 39는 `clarification_batches.skip_submission`에 멱등 audit을 남기고, WorkDefinition의 `clarification_skip_history` 및 `unresolved`에 질문 ID·사유·target path를 `unknown` provenance로 기록한다.
3. 답변을 추측하거나 confirmed 값으로 채우지 않는다. 기존에 확정된 정보와 새 `unresolved` 목록으로 `READY_FOR_REVIEW`/`review_path`를 열어 Preview와 최종 승인 단계로 이동한다.
4. 건너뛰기는 현재 질문 card를 종료하는 action이므로 다음 질문을 만들지 않는다. 최대 세 회 정책의 4차 회차가 아니며, F20도 최종 `Approve` 전에는 실행되지 않는다.

검증 시 Component 39 결과에 `clarification_skipped: true`, `skip_summary.skipped_question_ids`, `skip_summary.unresolved_record_ids`가 있고 WorkDefinition `status=READY_FOR_REVIEW`인지 확인한다. 같은 skip 재개는 같은 idempotent 결과를 반환해야 하며, 다른 skip event로 바꾸거나 만료된 batch를 건너뛰려 하면 차단되어야 한다.

### 8.3 Submit 후 차단 오류가 보일 때

`HUMAN_ACTION_AMBIGUOUS`가 보이면 답변 내용의 문제가 아니다. 이전 F10 export에서는 Langflow가 checkpoint 재개 시 Submit/Cancel 출력 값을 함께 평가할 수 있어, Component 39가 두 행동을 동시에 받은 것으로 해석할 수 있었다. 최신 export는 Submit/Skip/Cancel 세 action을 모두 route 값으로 구분하고 다음 두 보호를 적용한다.

- 질문 card가 대기 중인 첫 실행에서는 Submit/Skip/Cancel trigger에 빈 Data만 전달한다.
- 재개 뒤에도 Component 39는 값의 존재 여부가 아니라 각 trigger의 `route`가 Submit, Skip 또는 Cancel과 정확히 일치할 때만 해당 행동으로 처리한다.

따라서 이 오류가 난 기존 suspend job은 재개하지 않는다. 최신 `flows/F10_work_definition_parent.json`을 import한 뒤 **새 job**으로 처음부터 실행한다. 새 Flow에서 같은 오류가 다시 나면 Component 39의 `저장 결과` 전체 JSON을 함께 확인한다. `WORK_DEFINITION_SCHEMA_INVALID`가 뒤이어 보이는 경우는 보통 이 오류 결과가 다음 12번으로 전달된 연쇄 증상이다.

### 8.4 최대 세 회차와 합격 기준

다음 질문이 생기면 새 Component 42 card에 직접 입력·제출을 반복한다. 각 회차는 `12 완전성 평가 → 질문 LLM → 13 질문 batch → 42 Playground 답변 카드 → 39 답변 반영`으로 구성되며 최대 세 회다. 1·2차 card는 최대 3개, 마지막 3차 card는 최대 4개의 입력칸을 표시할 수 있다. 마지막 3차의 네 번째 입력까지 답한 뒤에도 blocking gap이 남으면 4차 질문 batch 없이 `CLARIFICATION_ROUND_LIMIT`로 종료되어야 한다. 반대로 어느 card에서든 `추가 입력 건너뛰기`를 선택하면 해당 card의 audit/unresolved를 남긴 뒤 review로 진행하며, 4차 질문을 열지 않는다.

질문 회차별 합격 기준:

- 질문 batch가 `WAITING_ANSWER`이고 현재 WorkDefinition revision과 일치
- 1차 질문이면 revision 0 WorkDefinition이 중복 없이 준비됨
- `42 보완 답변 HITL` 카드에 입력칸이 표시되고 `Submit Answers`가 values를 전달함
- Component 39가 direct Playground 제출값을 재검증하고 답변 저장과 semantic revision 증가를 CAS로 한 번만 적용
- 새 revision만 다음 Component 12 완전성 평가로 전달
- stale revision, 다른 사번, 만료 batch, 잘못된 question type/값은 다음 의미 단계로 가지 않고 차단
- `추가 입력 건너뛰기`는 별도 skip audit과 질문별 `unresolved`/`unknown` provenance를 남기고 `READY_FOR_REVIEW`로 진행하며, 답변을 만들거나 `CANCELLED`로 바꾸지 않음
- `Cancel`은 답변 저장 없이 terminal 취소 경로로 감

자동화 운영에서 Workflow API로 F10 background job을 시작·조회할 수는 있지만, 그 경우에도 답변은 Component 42의 native schema 카드에서 받는다. `services/hitl_form_api`는 legacy/reference 전용이며 이 active E2E 경로에는 포함하지 않는다.

## 9. 7단계: 승인 후 F20→F30 Run Flow 자동 실행

업무가 완전해지면 최종 Human Input이 `Approve`, `Reject`, `Cancel` 선택지를 제공한다. 전체 성공 시험에서는 `Approve`를 선택한다.

승인 뒤 기대 순서는 다음과 같다.

1. Component 18이 canonical WorkDefinition을 `APPROVED`와 `approved_hash`로 저장한다.
2. Component 36이 승인 receipt를 받아 MongoDB canonical WorkDefinition, active catalog pointer, active Skill registry를 다시 읽는다.
3. canonical/request channel이 모두 `native_hitl`인지, Component 45 sealed authentication context의 subject가 owner와 같은지 검증한다.
4. Component 36이 strict JSON text를 포함한 `agent-design-invocation/v1`을 만든다.
5. built-in TypeConverter가 Data를 Message로 바꾼다.
6. F10의 `Run Flow(tool_mode=false)`가 F20 ChatInput으로 직접 전달한다.
7. F20이 hybrid retrieval, Blueprint normalizer, port validator, readiness classifier와 생성 요청 builder를 실행한다.
8. `38 F20 Report Handoff Builder`가 승인 WorkDefinition, terminal Blueprint envelope, retrieval trace를 strict JSON `f20-report-handoff/v1`으로 고정한다.
9. `44 F20→F30 Report Handoff Gate`가 handoff schema/hash를 확인하고 성공 경로만 F30 Run Flow에 전달한다.
10. F30 `33 Handoff Loader → 30 View Model → 31 Renderer → 32 Publisher(dry-run)`가 실행되고, F30 Chat Output이 같은 F10 job의 최종 output으로 돌아온다.

합격 기준:

- 사용자가 F20을 따로 실행하거나 WorkDefinition을 복사하지 않음
- F10→F20→F30 사이에 HTTP 호출이 없음
- F20 ChatInput에 `should_store_message=false`
- invocation의 tenant/work/revision/approved/design/query hash lock이 downstream에서 유지됨
- retrieval candidate가 active snapshot과 ACL 범위 안에 있음
- 최종 envelope `ok=true`, `status="COMPLETED"`
- `agent-blueprint.v1`, `build_readiness`, blockers, generation request가 일관됨
- 신규 Custom node가 있으면 **운영 Langflow 1.11.0 호환** standalone 생성 요청이 node와 1:1로 존재하며, source/template build 검증은 별도 Langflow 1.11.1 기준으로 통과
- F30 결과가 `ok=true`, `status="would_publish"`이면 보고서 생성·검증은 완료되고 실제 저장만 생략됨

다음 negative path도 최소 한 번 확인한다.

| 시험 | 기대 결과 |
| --- | --- |
| Component 45 trusted gateway subject를 owner와 다른 값으로 설정 | blocked, F20 미실행 |
| active pointer의 runtime v2 계약과 F20 query contract 불일치 | 검색 blocked |
| Skill prompt hash 불일치 또는 status가 `Active` | 해당 Skill fail-closed 제외 |
| 최종 Human Input에서 Reject/Cancel | terminal 상태, F20 미실행 |
| legacy `channel_mode=playground` payload 주입 | authority boundary에서 차단 |
| F20 handoff hash 또는 schema 변조 | F30 미실행, F10 terminal 결과 |

## 10. 8단계: MongoDB 결과 검증

F00 live ingest와 Skill seed가 끝난 뒤 read-only helper를 실행한다.

```powershell
& $Python scripts\verify_example_mongodb.py `
  --mongodb-uri $env:MONGODB_URI `
  --database $env:MONGODB_DATABASE `
  --tenant-id default `
  --catalog-id internal-assets `
  --catalog-sample samples\f00_catalog_assets_example.json `
  --skill-sample samples\skill_registry_example.json
if ($LASTEXITCODE -ne 0) { throw '예제 MongoDB 검증 실패' }
```

helper가 성공해야 할 핵심 항목은 다음과 같다.

- 출력 `ok=true`, `status="EXAMPLE_MONGODB_VERIFIED"`, process exit code 0
- tenant/catalog의 active pointer가 정확히 한 active snapshot을 가리킴
- pointer와 각 parent의 source SHA-256/size가 업로드한 예제 파일과 정확히 일치
- pointer count와 active snapshot의 parent/chunk count 일치
- 예제 `id + version`이 parent에 모두 존재하고 redacted 원문·원문 hash·content hash가 예제와 일치
- 모든 chunk에 finite vector가 있고 vector 길이와 `runtime_class`/`model_id`/fingerprint가 pointer runtime v2 contract와 동일
- chunk의 parent identity와 snapshot이 일치
- 예제 Skill identity/version/prompt hash뿐 아니라 trigger/near-miss rule, ACL, 금지 동작과 승인 증거가 그대로 존재
- 다른 tenant 또는 이전 partial snapshot을 active 결과로 계산하지 않음

F10 실행 결과는 별도로 다음 collection에서 확인한다.

- `work_definitions`: 동일 tenant/owner/session, 최종 `status=APPROVED`, `approved_hash`
- `work_definition_events`: 상태 전이와 idempotency audit
- `clarification_batches`: immutable 질문, answer submission, workflow reference
- `work_runtime_states`: 최종 runtime 상태와 semantic revision reconciliation
- `work_runtime_events`: `WAITING_ANSWER`, `MERGING`, `READY_FOR_REVIEW`, `WAITING_APPROVAL` 순서

검증 스크립트와 운영 확인 쿼리는 secret 원문, 전체 embedding vector, raw 업무 원문을 로그에 출력하지 않아야 한다.

## 11. 9단계: F30 결과 확인 및 F90 선택 검증

### 11.1 F30 반응형 report 테스트 실행 (저장하지 않음)

F30은 9단계 승인 성공 경로에서 자동 실행된다. F30만 독립 dry-run으로 확인할 때는 `flows/F30_responsive_report.json`을 import하고 [`samples/f20_report_handoff.json`](../samples/f20_report_handoff.json) **전체**를 Chat Input에 넣는다. Component 33이 승인 WorkDefinition, terminal Blueprint envelope, retrieval trace를 자동 분리한다.

첫 시험에서는 Component 32 화면의 **테스트 실행 (저장하지 않음)**(`dry_run=true`)을 유지한다. `Report API URL=http://127.0.0.1:5000`, `HTML Link TTL (hours)=4`를 확인한다. 게시 API의 연결/HTTP/응답 오류는 Flow 예외가 아니라 `PUBLISH_FAILED` 결과로 표시된다. 합격 기준:

- view model과 HTML render `ok=true`
- node/edge 클릭 시 업무 방식, 개선 방향, 추천 근거, 적용 Skill, 신규 Custom 생성 요청 확인
- desktop/mobile에서 graph overflow나 detail drawer 잘림이 없음
- HTML/JavaScript를 LLM이 생성하지 않고 Component 31이 deterministic하게 render
- script/HTML injection 문자열이 실행되지 않음
- Report API network publish가 발생하지 않음

실제 게시 시험은 공유 HTML Report API가 실행 중이고 view/download URL을 반환하는 경우에만 수행한다.

### 11.2 F90 검색 평가

`flows/F90_search_evaluation.json`은 active catalog의 검색 품질을 독립적으로 확인하는 선택 Flow다. Playground의 유일한 Chat Input에 **F10 Component 36의 `Verified Design Invocation` 전체 JSON**을 붙여 넣어 실행한다. raw 업무 설명이나 F10/F20 중간 payload는 입력 계약이 아니며 Query Planner의 approval/ACL/snapshot lock 검증에서 차단되는 것이 정상이다. F20과 같은 built-in Embedding Model provider/model, runtime v2 contract, MongoDB/index 설정을 적용하며, Component 29는 F00와 같은 provider 경계 보호를 위해 query batch 사이에 기본·최소 1초를 대기한다. advanced `Dimensions`는 provider output-size override가 필요한 경우만 F00/F20과 일관되게 설정한다.

합격 기준:

- 기대 Component/Flow 자산이 top-N에 포함
- exact/lexical/vector source와 실제 기여 query ID가 trace에 기록
- tenant/snapshot/ACL 밖 자산이 결과에 없음
- `metadata_only`와 `verified_runtime` 상태가 구분됨
- query plan, vector, retrieval trace의 scope hash가 동일

F90을 위해 F10/F20 사이에 HTTP 호출을 추가하지 않는다. F90은 평가용 별도 top-level 실행이며 production 승인 경로를 대체하지 않는다.

## 12. 최종 합격표

| 단계 | 필수 합격 조건 |
| --- | --- |
| 정적 검증 | generator/runtime/pytest/compile error 0 |
| F00 테스트 실행 | deterministic hash/count, network/write 0 |
| F00 live | parent/chunk/vector 완성 후 pointer 전환, replay 중복 0 |
| Skill seed | 테스트 실행 write 0, `--apply` deterministic upsert |
| Import/config | F20 먼저, F10 Run Flow child 해석, `tool_mode=false` |
| native HITL | 42 Playground schema 입력칸 표시·직접 제출 또는 명시적 추가 입력 건너뛰기, 1·2차 최대 3문항·3차 최대 4문항, `decision.values`/skip audit/revision/CAS/state 정상 |
| 승인 handoff | Component 36 authority reload 성공 뒤에만 F20 실행, sealed handoff gate 성공 뒤에만 F30 실행 |
| F20/F30 | active snapshot/ACL 검색과 locked Blueprint/생성 요청 완료, report dry-run 결과 반환 |
| Mongo 검증 | helper exit code 0, pointer/count/vector/Skill 일치 |
| F30/F90 | 선택 실행 시 report/search scope 검증 통과 |

필수 단계 중 하나라도 실패하면 `LOCAL_E2E_PASSED`로 표시하지 않는다. F30/F90을 실행하지 않았다면 각각 `NOT_RUN`으로 명시하며 전체 업무정의→Agent 설계 경로와 검색 인프라 검증 여부를 혼동하지 않는다.

## 13. 이 절차로도 production-ready가 되지 않는 범위

예제 E2E 통과는 기능 계약 검증이다. 다음은 실제 사내 인프라에서 별도 검증해야 한다.

- 2만~3만 행 실제 catalog의 처리시간, memory, batch 한도와 부분 장애 복구
- embedding/LLM gateway의 운영 rate limit, timeout, retry, 승인 model-ID allowlist와 runtime v2 fingerprint 고정
- MongoDB transaction, Search index build/rebuild, backup/restore와 active pointer 장애 복구
- 교차 tenant/owner/group ACL 침투 시험과 gateway identity 위조 차단
- Langflow restart 전후 background job/42 schema card suspend/resume 복구
- 만료된 pending HITL job을 중단하고 terminal runtime event를 기록하는 expiry sweeper
- Report signed capability tamper/expiry/purpose/header 혼용 차단과 access-log query redaction
- report metadata/GridFS retention, legal hold와 purge sweeper
- 실제 Component/Flow port의 runtime 실행 권한과 secret/network zone 검증

특히 local test에서 Component 36의 인증 subject를 node 고정값으로 넣은 것은 편의를 위한 fixture일 뿐이다. production에서는 trusted gateway가 요청별 identity를 주입해야 한다. 위 항목이 끝나기 전 상태는 `PRODUCTION_INFRA_VALIDATION_PENDING`이다.
