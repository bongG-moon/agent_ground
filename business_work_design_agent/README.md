# Business Work Design Agent

사람이 자연어로 설명한 업무를 HITL 질문으로 구체화하고, 승인된 업무 정의를 사내 Langflow 자산 카탈로그와 하이브리드 검색해 Agent 설계로 변환한 뒤 노드·연결선 기반 반응형 보고서로 제공하는 Langflow 1.11 프로젝트입니다.

기존 `business_agent_design` 코드를 이식하지 않고 `langflow==1.11.1`에서 새 Component template과 edge handle로 생성했습니다. `ai-sop-md-sopax-sop-ui`는 카드형 노드, 연결선, Skill 표시와 상세 패널의 시각 문법만 참고했습니다.

## 구현 결과

- 37개 Custom Component: 모두 한 `.py` 파일에 완결된 Standalone Component
- 6개 Langflow Flow JSON과 일괄 이관용 bundle
- 자연어 업무 추출, 최대 3회 clarification, 승인·거절·취소 상태 전이
- 원본·정규화 text·embedding을 함께 보존하는 MongoDB catalog snapshot pipeline
- exact/filter + lexical + vector 검색과 application/native fusion 모드
- 검증된 catalog allowlist 기반 AgentBlueprint 및 신규 Component 생성 요청 프롬프트
- 결정론적 HTML renderer, 클릭 가능한 노드·edge label, 데스크톱 drawer와 모바일 bottom sheet
- bounded catalog worker, MongoDB 기반 HITL Form API와 immutable Report API

Flow 생성기와 runtime validator는 Langflow 1.11.1 source build, handle compatibility, `Graph.from_payload` 역직렬화와 embedded source hash를 검사합니다. 최종 실행 수치와 bundle hash는 [검증 결과](docs/VALIDATION_REPORT.md)에 실제 명령 결과로만 기록합니다. 실제 MongoDB Search index, embedding/LLM gateway, 사내 인증과 suspend/resume를 연결하기 전에는 `configuration_required` 또는 `trusted_backend_only_configuration_required`이며 운영 준비 완료로 간주하지 않습니다.

## 주요 산출물

- [상세 기술 명세서](docs/TECHNICAL_SPECIFICATION.md)
- [구현 데이터 계약](docs/DATA_CONTRACTS.md)
- [HITL 상태 머신](docs/HITL_STATE_MACHINE.md)
- [설치·Import·운영 가이드](docs/OPERATIONS_GUIDE.md)
- [Standalone Component 생성 요청 프롬프트](docs/CUSTOM_COMPONENT_GENERATION_PROMPTS.md)
- [Flow 사용 안내](flows/README.md)
- [생성된 샘플 반응형 보고서](samples/generated_sample_report.html)

## Flow 구성

| Flow | 역할 | Native HITL |
| --- | --- | --- |
| `F00_catalog_ingestion_admin.json` | Catalog 업로드·secret scan, worker 기반 적재·검증, 관리자 activation 결정 기록 | 활성화 결정 1회 |
| `F10_work_definition_parent.json` | 자연어 업무 정의와 최대 3회 clarification, 별도 runtime 상태 저장, 최종 승인 | 답변 3회 + 최종 승인 1회 |
| `F11_work_definition_chat_turn.json` | 외부에서 복원한 상태와 구조화 command를 처리하는 Playground turn | 없음 |
| `F20_agent_blueprint_design.json` | 승인 업무·ACL·snapshot·추가 설계 프롬프트를 고정한 design scope, Skill context, hybrid search, Blueprint 정규화·검증 | 없음 |
| `F30_responsive_report.json` | ViewModel → HTML → Report API dry-run/게시 | 없음 |
| `F90_search_evaluation.json` | hybrid retrieval 평가 | 없음 |

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

F00의 `09_catalog_pipeline_worker_client.py`는 외부 worker를 호출하는 Standalone adapter입니다. 긴 02~07 stage loop는 `services/catalog_worker`가 담당하고 F00은 `VALIDATED` 결과에 대한 관리자 결정까지 기록·출력합니다. 그 뒤 trusted admin gateway가 F00 run/job/decision을 검증하고 짧은 `catalog-activation-attestation/v1` claim을 발급해 worker `/activate`를 직접 호출합니다. `33_catalog_activation_approval_client.py`는 claim이 실행 전에 준비된 별도 secured activation 호출에만 사용하며 F00에는 포함되지 않습니다. worker가 내부에서 발급·소비하는 raw nonce는 Langflow `Data` edge나 공개 응답으로 전달되지 않습니다. F10의 `34_work_runtime_state_store.py`는 `WAITING_ANSWER`, `MERGING`, `READY_FOR_REVIEW`, `WAITING_APPROVAL`, `BLOCKED`, `CANCELLED` 같은 실행 상태를 `work_runtime_states`/`work_runtime_events`에 기록하되 WorkDefinition의 의미 revision을 증가시키지 않습니다. 답변 저장 뒤 semantic revision을 runtime state에 먼저 reconciliation한 성공 경로만 다음 completeness/review로 진행합니다. `35_result_gate.py`는 F10/F11의 store·loader·merger·graph·preview·approval/action 결과가 명시적 `ok=true`와 필수 payload를 가진 경우에만 다음 경로를 열고, 오류·불완전 envelope는 blocked output으로 끝냅니다. `36_playground_command_router.py`는 F11의 중복 JSON key, nested command, 미지원 command를 거부하고 검증된 최상위 `start`, `submit_answers`, `approve`, `reject`, `cancel` 경로 하나만 엽니다.

현재 F10/F11은 승인 화면의 `request_changes` 재진입을 노출하지 않습니다. 수정이 필요하면 승인 전 clarification에서 반영하거나 현재 작업을 취소하고 새 session을 시작해야 합니다. 신뢰 가능한 revision editor와 재승인 hash 계약을 구현하기 전까지 Component 18의 일반 `request_changes` command를 Flow에 직접 연결하지 않습니다. 또한 native HITL 질문 기한이 지났다고 Langflow suspended job이 자동 종료되는 것은 아니므로, 만료 batch와 pending request를 대조해 job을 중단하고 terminal runtime 상태를 기록하는 외부 sweeper가 배치되기 전에는 F10을 production-ready로 간주하지 않습니다.

이 저장소는 attestation 검증과 server-side activation은 구현하지만 사내 SSO/관리자 권한을 가진 attestation issuer endpoint는 제공하지 않습니다. 실제 활성화에는 별도의 trusted admin gateway 연동이 필수이며, 연동 전 F00 결과는 activation handoff이지 활성화 완료가 아닙니다.

production F20의 WorkDefinition/ACL/snapshot/Skill registry node tweak는 trusted backend가 canonical 저장소와 인증 identity에서 구성해야 합니다. F20은 Component 17과 같은 의미 projection으로 `approved_hash`를 다시 검증하고 승인되지 않은 raw/extension 필드를 설계 scope에서 제거합니다. Skill registry는 timezone-aware 승인 시각/승인자, 명시적 tenant/group/private ACL, bounded rule과 prompt hash가 없으면 fail-closed로 제외하며 같은 Skill identity가 중복되면 충돌 항목 전부를 적용하지 않습니다. Report API는 header-auth 조회를 생성 actor로 제한하고, 생성 응답의 `view_url`/`download_url`에는 tenant·actor·report·content hash·purpose·만료를 묶은 짧은 수명의 signed capability를 넣어 브라우저 열람을 지원합니다. signing secret은 32 UTF-8 byte 이상이어야 하고 signed link는 identity header와 혼용하지 않습니다. capability query는 만료 전 replay 가능한 bearer secret이므로 Uvicorn/reverse proxy access log에서 숨겨야 합니다. Mongo idempotency `PROCESSING`은 만료 lease를 안전하게 reclaim하며, `REPORT_RETENTION_DAYS`와 별개로 report metadata/GridFS blob을 정리하는 lifecycle sweeper는 계속 운영해야 합니다.

## 빠른 검증

```powershell
$env:PYTHONPATH=(Resolve-Path '.').Path
$env:PYTEST_ADDOPTS='-p no:cacheprovider'
.\.venv\Scripts\python.exe -m compileall -q components services scripts tests
.\.venv\Scripts\python.exe -m pytest -q tests
.\.venv\Scripts\python.exe scripts\build_langflow_1_11_flows.py --check
.\.venv\Scripts\python.exe scripts\validate_langflow_1_11_runtime.py
```

의존성은 `requirements.txt`가 `langflow==1.11.1`, `langflow-base==0.11.5`, `lfx==1.11.5`를 고정합니다. 환경 설정과 서비스 실행 순서는 [운영 가이드](docs/OPERATIONS_GUIDE.md)를 따릅니다.

## 참고 기준

- 로컬 개념 비교: `agent_ground/business_agent_design` — 기존 Langflow 1.9.2 구현은 구조 참고만 사용
- MCP/SKILL/HARNESS 개념 참고: `boi-wiki-local` commit `afb6e78a5d6a53cf112853e0a41de846862cdc85`
- 시각화 참고: 로컬 `ai-sop-md-sopax-sop-ui` — 코드·업무 로직·저장 구조는 복제하지 않음

`boi-wiki-local`의 검토 commit에는 루트 라이선스 파일이 확인되지 않아 문구·템플릿·코드를 복사하지 않고 계약 개념만 새 표현과 새 구현으로 재구성했습니다.
