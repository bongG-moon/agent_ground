# Business Work Design Agent

사람이 자연어로 설명한 업무를 HITL 질문으로 구체화하고, 승인된 업무 정의를 사내 Langflow 자산 카탈로그와 하이브리드 검색해 Agent 설계로 변환한 뒤 노드·연결선 기반 반응형 보고서로 제공하는 Langflow 1.11 프로젝트입니다.

기존 `business_agent_design` 코드를 이식하지 않고 `langflow==1.11.1`에서 새 Component template과 edge handle로 생성했습니다. `ai-sop-md-sopax-sop-ui`는 카드형 노드, 연결선, Skill 표시와 상세 패널의 시각 문법만 참고했습니다.

## 구현 결과

- 32개 Custom Component: 모두 한 `.py` 파일에 완결된 Standalone Component
- 5개 Langflow Flow JSON과 일괄 이관용 bundle
- 자연어 업무 추출, 최대 3회 clarification, 승인·거절·취소 상태 전이
- 파일 로더·결정론적 chunker·Embedding Model·MongoDB writer가 Canvas에 분리되어 보이는 catalog ingest
- exact/filter + lexical + vector 검색과 application/native fusion 모드
- 검증된 catalog allowlist 기반 AgentBlueprint 및 신규 Component 생성 요청 프롬프트
- 결정론적 HTML renderer, 클릭 가능한 노드·edge label, 데스크톱 drawer와 모바일 bottom sheet
- MongoDB 기반 HITL Form API와 공유 HTML Report API 연동 adapter

Flow 생성기와 runtime validator는 Langflow 1.11.1 source build, handle compatibility, `Graph.from_payload` 역직렬화와 embedded source hash를 검사합니다. 최종 실행 수치와 bundle hash는 [검증 결과](docs/VALIDATION_REPORT.md)에 실제 명령 결과로만 기록합니다. 실제 MongoDB Search index, embedding/LLM gateway, 사내 인증과 suspend/resume를 연결하기 전에는 `configuration_required` 또는 `trusted_backend_only_configuration_required`이며 운영 준비 완료로 간주하지 않습니다.

## 주요 산출물

- [상세 기술 명세서](docs/TECHNICAL_SPECIFICATION.md)
- [구현 데이터 계약](docs/DATA_CONTRACTS.md)
- [HITL 상태 머신](docs/HITL_STATE_MACHINE.md)
- [설치·Import·운영 가이드](docs/OPERATIONS_GUIDE.md)
- [예제 데이터 기반 전체 E2E 테스트 가이드](docs/EXAMPLE_END_TO_END_TEST_GUIDE.md)
- [F20→F30 Report Handoff JSON 필드 가이드](docs/F20_REPORT_HANDOFF_REFERENCE.md)
- [Standalone Component 생성 요청 프롬프트](docs/CUSTOM_COMPONENT_GENERATION_PROMPTS.md)
- [Flow 사용 안내](flows/README.md)
- [생성된 샘플 반응형 보고서](samples/generated_sample_report.html)

바로 실행할 수 있는 예제 입력은 다음과 같습니다.

- `samples/f10_work_request_example.json`: F10의 두 Text Input과 팀 명·사번 기본값으로 들어 있는 **생산·프로젝트 리스크 통합 주간보고** 복합 예제(메일·CUBE·JIRA·DataLake·SOP·승인·예외 처리 포함)
- `samples/f00_catalog_assets_example.json`: F00 `00 Catalog JSON Loader`의 FileInput에 업로드하는 업무 설계·메일 보고·문서·데이터·승인·리포트 참고 Component/Flow 100건
- `samples/skill_registry_example.json`: F20 Skill 적용을 위한 `default` tenant 승인 Skill 1건
- `samples/f20_report_handoff.json`: F20에서 F30으로 전달되는 sealed report handoff 단독 테스트 fixture

F00 업로드 파일은 Langflow 서버가 관리하는 파일 경로로 변환되므로 Flow JSON에 로컬 경로를 하드코딩하지 않습니다. F10은 실행마다 Langflow가 부여한 고유 `run_id`를 내부적으로 사용하므로 사용자가 `session_id`를 입력하거나 바꿀 필요가 없습니다. 같은 HITL 실행을 재개할 때는 같은 run ID를 유지하고, 새 전체 실행은 새 WorkDefinition과 질문 Batch를 만듭니다.

## Flow 구성

| Flow | 역할 | Native HITL |
| --- | --- | --- |
| `F00_catalog_file_vector_ingest.json` | JSON/JSONL 파일 1개 → 정규화·청킹 → built-in Embedding Model → F20 호환 MongoDB vector snapshot 저장 | durable background job에서만 부분 checkpoint 계속/중단 카드 |
| `F10_work_definition_parent.json` | 자연어 업무 정의와 최대 3회 clarification, 최종 승인 후 MongoDB 권위 데이터 재확인 및 F20→F30 자동 실행 | 답변 3회 + 최종 승인 1회 |
| `F20_agent_blueprint_design.json` | 단일 trusted invocation으로 design scope, Skill context, hybrid search, Blueprint 정규화·검증 및 sealed report handoff 생성 | 없음 |
| `F30_responsive_report.json` | sealed F20 handoff 검증 → **업무 개요·현행 문제·개선 방향·권장 운영 방식·카탈로그/신규 구현 분담**을 포함한 완성형 보고서 HTML → Report API 테스트 실행/게시 | 없음 |
| `F90_search_evaluation.json` | Component 36의 Verified Design Invocation 전체 JSON으로 실행하는 evaluation-only hybrid retrieval 평가 | 없음 |

개별 JSON은 Langflow UI/API의 직접 import 대상입니다. `flows/00_business_work_design_ALL_FLOWS.json`은 여러 Flow를 함께 이관하기 위한 bundle이며 단일 Flow import endpoint에 넣지 않습니다.

## Standalone 원칙

`components/*/[0-9][0-9]_*.py`는 각각 아래를 지킵니다.

- `from lfx.custom import Component` 공개 API 사용
- Component subclass 정확히 하나
- 형제 파일, 프로젝트 package, 상대 경로 import 금지
- `sys.path` 조작, dynamic import, `eval`/`exec` 금지
- 모든 helper·상수·검증 로직을 같은 파일 안에 포함
- Flow에서 사용하는 source는 node에 byte 전체를 embed하고 SHA-256으로 원본과 고정; Flow에 미배치된 standalone source도 별도 단독 build 검증

이 규칙은 저장소가 배포하는 source의 정적 계약과 Langflow build 가능성을 검증하는 것이며 Python 보안 sandbox를 대신하지 않습니다. Custom Component는 여전히 임의 Python 실행 권한을 가질 수 있으므로 관리자 review와 격리된 allowlist runtime이 필요합니다.

신규 업무용 Component가 필요하면 [생성 요청 프롬프트](docs/CUSTOM_COMPONENT_GENERATION_PROMPTS.md)의 공통 제한과 해당 유형 템플릿을 함께 사용합니다.

F00은 `00 Catalog JSON Loader → 01 Deterministic Chunker → 02 MongoDB Catalog Vector Writer → Data to Message → Chat Output`의 주 경로와 `Embedding Model → MongoDB Writer`의 side edge로 구성됩니다. 앞의 세 Custom Component는 각각 한 파일에 완결된 Standalone Component이며, 다른 Langflow Flow나 별도 Catalog Worker HTTP API를 호출하지 않습니다. 사용자가 Loader에서 넣는 값은 업로드 파일 하나뿐입니다. 이 파일은 **현재 전체 카탈로그**여야 합니다. F00은 신규분만 병합하는 방식이 아니라 업로드 파일 전체를 다음 active snapshot으로 교체하므로, delta 파일을 올리면 그 파일에서 빠진 기존 자산은 다음 검색 대상에서 제외됩니다. 카탈로그 상세 페이지가 있다면 각 항목에 `catalog_url`(또는 `detail_url`, `asset_url`, `link`, `url`)을 함께 넣을 수 있습니다. F20/F30은 승인된 검색 후보와 정확히 일치하는 안전한 HTTP(S) 링크만 보고서의 **카탈로그 상세 열기**로 표시하며, credential·token 등이 포함된 링크는 표시하지 않습니다. `tenant_id`는 내부적으로 항상 `default`, `catalog_id`는 항상 `internal-assets`로 넣어 bundle과 MongoDB 문서에 보존하지만 Canvas 입력으로 노출하지 않습니다.

모델 선택과 credential은 F00/F20/F90의 built-in `Embedding Model` node에만 설정합니다. Writer와 query embedding node에는 model/version/dimension 입력이 없습니다. built-in node의 advanced `Dimensions`는 provider가 output-size override를 의도적으로 지원할 때만 설정하며, 기본적으로 비워 둡니다. 이는 저장 계약이 아니며 runtime은 실제 반환 vector의 길이를 사용합니다. 실행 시 Embeddings runtime의 `runtime_class`, configured `available_models` identity 또는 지원된 runtime metadata에서 해석한 `model_id`, 첫 vector의 실제 `dimension`, 이 값을 묶은 `fingerprint`로 `embedding-runtime-contract/v2` 계약을 생성합니다. `model_id`를 안전하게 해석할 수 없거나 세 Flow의 runtime 계약이 다르면 fail-closed합니다. F20/F90의 `29 Search Query Embedding Batcher`도 F00과 같은 provider 경계를 사용하므로, query batch 사이에는 기본·최소 1초를 대기합니다. Writer는 이 계약과 vector를 저장하고 성공한 마지막 단계에서만 `catalog_active_pointers`를 전환합니다. Canvas의 **테스트 실행 (저장하지 않음)**은 기본으로 켜져 있고 내부 호환성 필드 `dry_run=true`를 사용하며 provider나 MongoDB를 호출하지 않으므로 `embedding_contract.state=DEFERRED`, `snapshot_id`는 `null`로 반환합니다. 다만 Langflow는 Writer에 연결된 Embedding Model node를 먼저 build할 수 있으므로, 테스트 실행 전에도 승인 provider/model과 그 provider가 build에 요구하는 Secret은 설정해야 합니다. 실제 embedding 요청·1초 대기·MongoDB 저장은 이 단계에서 일어나지 않습니다. 실제 저장은 테스트 실행을 끈 뒤 **전체 카탈로그 파일 확인 (실제 저장용)**을 명시적으로 켠 경우에만 시작합니다.

F10 Canvas는 실행 node 39개와 Sticky Note 6개로 정리했습니다. 시작의 Text Input 두 개가 업무 설명 원문·추가 설계 프롬프트를 각각 전달하고, Envelope 화면에는 팀 명·사번만 보입니다. 세 번의 질문·답변·재평가와 최종 승인은 그대로 유지하되, `42_f10_clarification_answer_gate.py`가 각 질문을 Playground 카드의 실제 입력칸으로 표시하고 `39_f10_answer_commit.py`가 제출 답변을 `clarification_batches`에 기록·검증한 뒤 병합·revision CAS 저장·다음 회차/검토/차단을 판정합니다. 별도 Answer Form이나 Flow 간 HTTP API는 필요하지 않습니다. 첫 질문이 실제로 필요한 경우 `13_clarification_batch_builder.py`가 WorkDefinition revision 0과 immutable batch를 멱등적으로 준비합니다. 검토 단계의 Component 18 `review_and_request_approval`은 검증된 Preview를 한 번의 CAS 저장과 event 기록으로 `WAITING_APPROVAL` 상태로 전환합니다. Revision·중복 실행 방지 키·컬렉션은 자동/내부값이고 MongoDB URI·Database만 환경 설정입니다. `40_f10_review_entry_joiner.py`는 가능한 검토 진입 결과 하나만 고르고, Component 12·13·16·17·18·39·40·42는 선택된 group output만 열어 실패가 후속 LLM·Human Input·저장 단계로 흐르지 않게 합니다. 최종 built-in Human Input 뒤의 `43_f10_final_approval_route_gate.py`는 선택하지 않은 두 상태 저장 branch를 즉시 제외합니다. `41_f10_terminal_result_message.py`는 모든 intentional cancel/reject/blocked 결과만 event-list terminal로 표시합니다. 승인 성공 경로에서는 `45_f10_authentication_context.py`가 local demo fixture와 trusted gateway 인증을 구분하고, `36_approved_design_invocation_loader.py`가 sealed context의 subject와 WorkDefinition, 활성 catalog pointer, 승인 Skill registry를 MongoDB에서 다시 대조한 뒤 F20에 전달할 단일 invocation을 만듭니다.

MongoDB를 사용하는 모든 Flow의 Database 입력은 `business_work_design`으로 미리 채워집니다. Flow export의 모든 MongoDB URI 입력은 Langflow Secret Global Variable **`MONGO_URL`**에 자동 연결됩니다. 따라서 Settings의 Global Variables에서 `MONGO_URL`을 한 번만 Secret으로 만들면 F00·F10·F20·F90의 14개 MongoDB node가 같은 URI를 사용하며, 실제 URI는 Flow JSON에 저장되지 않습니다. E2E 격리 테스트처럼 의도적으로 별도 Database를 쓸 때에만 Database를 명시적으로 바꿉니다.

현재 `team_name`은 업무의 팀 표시·감사 메타데이터이고, F00/F20의 공용 카탈로그 scope는 내부적으로 `default`를 유지합니다. 팀별 catalog/Skill 격리가 필요하면 해당 팀별 active pointer와 Skill registry를 별도로 적재한 뒤 내부 scope 매핑을 추가해야 합니다.

F00의 MongoDB collection 세 개는 역할이 다릅니다. `catalog_assets`는 자산별 parent 메타데이터와 redacted 원문을 한 번 저장해 정확한 제목/alias 조회와 최종 상세 정보를 제공하고, `catalog_asset_chunks`는 parent에서 나눈 검색 단위와 `embedding.vector`를 저장해 lexical/vector 검색 후보를 만듭니다. `catalog_active_pointers`는 모든 parent/chunk/vector 저장과 검증이 끝난 snapshot 하나만 가리키는 게시 스위치입니다. 따라서 적재 중 실패하면 F20은 이전에 검증된 snapshot을 계속 검색합니다. 중단된 실행을 같은 전체 파일·chunk 정책·embedding 계약으로 다시 실행하면, Writer는 hash·vector·계약이 모두 일치하는 부분 청크만 재사용합니다. 동시에 두 적재가 끝나도 먼저 바뀐 pointer를 늦게 끝난 실행이 되돌리지 않도록 마지막 게시에는 compare-and-swap 검증을 사용합니다.

F10은 승인 화면의 `request_changes` 재진입을 노출하지 않습니다. 수정이 필요하면 승인 전 clarification에서 반영하거나 현재 작업을 취소하고 새 session을 시작해야 합니다. 신뢰 가능한 revision editor와 재승인 hash 계약을 구현하기 전까지 Component 18의 일반 `request_changes` command를 Flow에 직접 연결하지 않습니다. 또한 native HITL 질문 기한이 지났다고 Langflow suspended job이 자동 종료되는 것은 아니므로, 만료 batch와 pending request를 대조해 job을 중단하고 terminal runtime 상태를 기록하는 외부 sweeper가 배치되기 전에는 F10을 production-ready로 간주하지 않습니다.

F00은 파일 한 건을 직접 처리하는 동기 실행형 Flow입니다. Writer는 Chunker가 만든 청크를 **한 번에 하나씩** Embedding Model에 보내며, 다음 호출과 일시 오류 재시도 전에는 `임베딩 호출 간격(초)`만큼 기다립니다(기본·최소 1초). 기본 설정은 새 청크 최대 80개 또는 내부 180초까지만 처리하고 10개마다 MongoDB checkpoint를 저장합니다. 청크가 남은 경우 native HITL 카드의 `처리됨/전체/남음`과 **계속 적재**/**중단하고 나중에 실행** 버튼은 **Langflow durable background job**에서만 열립니다. 일반 Canvas **Run Flow**에서는 durable pause/resume job이 없으므로 `PARTIAL_EMBEDDINGS_SAVED`와 `hitl.available=false`, `reason=durable_background_job_required`를 반환하며, **같은 전체 파일·chunk 정책·Embedding Model 설정으로 F00을 다시 실행**해 검증된 checkpoint 청크를 건너뛰고 이어갑니다. `MongoDB 저장 체크포인트 청크 수`는 이미 임베딩된 문서를 저장하는 간격일 뿐 청크 분할이나 Embedding 호출 횟수는 바꾸지 않습니다. 운영에서는 파일 크기, 최대 record/chunk 수, chunk size/overlap, 호출 간격, MongoDB timeout을 제한하며 MongoDB와 embedding 설정이 없으면 실패하고 임의의 memory/vector fallback을 사용하지 않습니다. 이전 snapshot은 자동 삭제하지 않으므로 보존 기간·정리 정책은 별도로 승인해야 합니다.

F10은 승인 성공 시 HTTP API가 아니라 Langflow 1.11.1의 `Run Flow`를 direct mode로 실행하여 F20과 F30을 순서대로 호출합니다. 사용자가 WorkDefinition을 복사해 F20/F30에 다시 넣지 않으며, F20에는 MongoDB 권위 재확인을 통과한 `agent-design-invocation/v1` 하나만 전달합니다. F20 `38 F20 Report Handoff Builder`는 승인 WorkDefinition, terminal Blueprint envelope, retrieval trace를 canonical `f20-report-handoff/v1` JSON으로 묶고, F10 `44 F20→F30 Report Handoff Gate`가 schema/hash를 확인한 경우에만 F30 ChatInput으로 전달합니다. F30은 Loader에서 동일 identity를 재검증한 뒤 report를 만들며 기본값은 `dry_run=true`입니다. F20/F30 내부 ChatInput은 `should_store_message=false`라서 권한성 payload를 대화 이력에 저장하지 않습니다. `Run Flow`의 tool mode는 꺼져 있어 LLM이 호출 여부나 권한성 인자를 선택하지 않습니다. 마지막 Publisher는 공유 HTML Report API의 `POST /reports` 계약을 사용하고, `37 보고서 게시 결과 메시지`가 서버의 `view_url`/`download_url`을 **보고서 열기**·**HTML 다운로드** 하이퍼링크로 바꿔 Playground에 표시합니다. API URL은 base URL 또는 `/reports` endpoint로 지정할 수 있고, API 연결/HTTP/응답 오류는 Flow를 중단하지 않고 읽기 쉬운 실패 안내로 표시합니다.

F30의 첫 화면은 단순 노드 캔버스가 아니라 **업무 방식 및 개선 실행 보고서**입니다. 승인된 사실만 사용해 업무 목표·범위·입출력·담당/시스템, 현행 절차와 문제·위험, 개선 원칙, 권장 TO-BE 절차와 분기·예외·사람 검토 지점, 카탈로그 재사용/검토 후보와 신규 Standalone Component의 역할, 구현 로드맵·검증 기준·미확정 사항을 읽기 쉬운 순서로 제공합니다. 확정되지 않은 정보는 추측하지 않고 `미확정/추가 확인 필요`로 표시합니다. 이어지는 Flow 캔버스와 카탈로그 카드는 이 보고서의 근거를 상세 확인하는 용도입니다.

F20 검색은 승인 WorkDefinition의 확정된 목표·단계·시스템·입출력뿐 아니라, 사용자가 HITL 보완을 건너뛴 경우에도 검증된 최초 업무 설명을 **검색 전용 seed**로 사용합니다. 원문은 Blueprint prompt나 승인 사실로 복사하지 않습니다. `exact/lexical/vector`이 모두 0건인데 같은 권한 범위의 catalog data가 있으면 F20은 관련 단어가 실제로 일치하는 같은 tenant·snapshot·ACL의 metadata 후보만 낮은 신뢰도로 보조 제시합니다. 이때 `retrieval_trace.fallback.semantic_match_verified=false`와 `scope_diagnostics`가 남으므로 Atlas index/filter 문제와 진짜 재사용 후보 부재를 구분할 수 있습니다. 관련 단어가 없는 인기 자산을 임의 추천하지는 않습니다.

F90은 F10→F20 production child 경로에 연결되지 않는 top-level **evaluation-only** Flow입니다. Playground에서 독립 실행할 때는 raw 업무 설명이 아니라 Component 36의 `Verified Design Invocation` 전체 JSON을 F90의 유일한 Chat Input에 붙여 넣습니다. F90 Query Planner는 tenant/ACL/approved work/active snapshot lock을 다시 검증하므로, 임의 JSON이나 lock이 없는 원문은 검색을 시작하지 않고 안전하게 차단합니다.

## 빠른 검증

```powershell
$env:PYTHONPATH=(Resolve-Path '.').Path
$env:PYTEST_ADDOPTS='-p no:cacheprovider'
.\.venv\Scripts\python.exe -m compileall -q components services scripts tests
.\.venv\Scripts\python.exe -m pytest -q tests
.\.venv\Scripts\python.exe scripts\build_langflow_1_11_flows.py --check
.\.venv\Scripts\python.exe scripts\validate_langflow_1_11_runtime.py
```

의존성은 `requirements.txt`가 `langflow==1.11.1`, `langflow-base==0.11.5`, `lfx==1.11.5`를 고정합니다. MongoDB를 처음 연결하기 전에는 [배포 준비점검](docs/DEPLOYMENT_PREFLIGHT.md)의 read-only 확인을 먼저 실행하고, 검토 후 필요한 일반 index만 `--apply`로 생성합니다. 환경 설정과 서비스 실행 순서는 [운영 가이드](docs/OPERATIONS_GUIDE.md)를 따릅니다.

## 참고 기준

- 로컬 개념 비교: `agent_ground/business_agent_design` — 기존 Langflow 1.9.2 구현은 구조 참고만 사용
- MCP/SKILL/HARNESS 개념 참고: `boi-wiki-local` commit `afb6e78a5d6a53cf112853e0a41de846862cdc85`
- 시각화 참고: 로컬 `ai-sop-md-sopax-sop-ui` — 코드·업무 로직·저장 구조는 복제하지 않음

`boi-wiki-local`의 검토 commit에는 루트 라이선스 파일이 확인되지 않아 문구·템플릿·코드를 복사하지 않고 계약 개념만 새 표현과 새 구현으로 재구성했습니다.
