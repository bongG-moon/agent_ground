# Langflow 1.11.1 Flow exports

이 폴더의 `F00`~`F90` JSON은 `scripts/build_langflow_1_11_flows.py`가 생성한 Langflow 1.11.1 import 파일이다. Custom Component source는 각 node의 `template.code.value`에 원본 byte와 동일하게 embed되며, node metadata의 `standalone_source_sha256`으로 원본 파일에 고정된다.

## Import 단위

- `F00_catalog_ingestion_admin.json`: 관리자 전용 top-level ingest/worker validation/activation decision Flow
- `F10_work_definition_parent.json`: native Human Input을 사용하는 top-level 업무 정의 Flow
- `F11_work_definition_chat_turn.json`: Human Input 없이 구조화 명령을 라우팅하는 Playground turn Flow
- `F20_agent_blueprint_design.json`: 별도 추가 설계 프롬프트와 고정 design scope를 사용하는 HITL-free Agent Blueprint child Flow
- `F30_responsive_report.json`: HITL-free report child Flow
- `F90_search_evaluation.json`: hybrid retrieval 평가 Flow
- `00_business_work_design_ALL_FLOWS.json`: 위 여섯 Flow의 이관용 bundle

개별 Flow JSON이 Langflow UI/API의 직접 import 대상이다. Bundle은 여러 Flow를 함께 이관하는 상위 artifact이므로 단일 Flow import endpoint에 넣지 않는다.

## 중요한 실행 조건

Flow JSON은 secret이나 production endpoint를 포함하지 않는다. Import 후 `build_manifest.json`과 각 Flow의 `metadata.required_configuration`을 확인하여 MongoDB, embedding provider, 승인된 model, tenant/ACL, Report API를 명시적으로 설정해야 한다. 특히 F20은 trusted backend가 canonical 승인 상태·identity/ACL·snapshot·Skill registry를 구성하고 실제 사내 자산 port를 검증하기 전까지 `trusted_backend_only_configuration_required`이며 `import_ready`로 간주하지 않는다.

F00과 F10의 Human Input만 top-level pause/resume에 사용한다. F11, F20, F30, F90에는 Human Input을 추가하지 않는다.

F00은 `00 File Intake → 01 Secret Scanner → 09 Catalog Pipeline Worker Client`까지만 Canvas에서 직접 연결한다. `services/catalog_worker`가 lease, 전체 deadline, stage별 subprocess timeout과 durable cursor를 적용해 standalone stage `02`~`07`을 반복 실행하고, `VALIDATED` 결과만 activation gate로 보낸다. Human Input의 승인/거절 branch는 결정 결과만 출력하며 F00 안에서 snapshot을 활성화하지 않는다. trusted admin gateway가 해당 F00 run/job/request/decision과 validation hash를 서버 측으로 확인한 뒤 `catalog-activation-attestation/v1`을 발급하고 worker `/activate`를 직접 호출한다. worker가 단회 nonce를 내부 발급·소비해 standalone `08`을 실행한다. Component 33은 signed claim이 실행 시작 전에 준비된 별도 secured activation 호출용이며 F00에 포함하지 않는다. raw nonce는 Langflow `Data` edge나 공개 응답에 나타나지 않는다.

attestation issuer는 이 프로젝트에 포함되지 않는 사내 gateway integration이다. 따라서 F00의 승인 output만으로 활성화 완료를 표시하지 않으며, gateway 직접 호출과 returned active pointer가 확인되어야 활성화 완료다.

F10은 최초 정규화 결과를 먼저 저장한 뒤 최대 3회의 질문/답변 라운드를 수행한다. 4번째 완전성 판정은 질문을 더 만들지 않는 최종 gate이며, 미완료 상태가 승인 미리보기에 유입되지 않게 차단한다. 각 질문 gate 앞의 `34 Work Runtime State Store`가 `WAITING_ANSWER`, 제출 branch의 동일 Component가 `MERGING`, 각 router 실패 branch가 `BLOCKED`를 `work_runtime_states`와 append-only `work_runtime_events`에 기록한다. 이 runtime revision은 WorkDefinition 의미 revision과 분리되며, persistence가 실패하면 Human Input이나 Answer Loader로 진행하지 않고 전용 진단 출력으로 종료한다. `35 Result Gate`는 최초 저장, answer loader/merger/store, review join/graph/preview/store/approval과 최종 action 결과마다 `ok=true`와 필수 payload를 검사하고 오류·누락 envelope를 blocked output으로 끝낸다. 검토 상태 저장과 `request_approval` 전이를 거친 뒤 최종 Human Input의 승인, 수정 요청, 반려, 취소를 각각 별도의 상태 명령으로 기록한다.

F11은 자체적으로 대화 상태를 복원하는 Flow가 아니다. `start`, `submit_answers`, `approve`, `reject`, `cancel` JSON 명령만 처리하는 단일 turn 계약이며, 후속 turn은 호출자가 기존 WorkDefinition, 활성 질문 batch, 승인 token을 Flow metadata에 기록된 node id로 주입해야 한다. `request_changes`는 공개 명령이 아니므로 수정이 필요하면 현재 session을 취소하고 새 `start`로 시작한다. 승인 token은 trusted gateway가 생성한 32~512 byte 원문을 한 번만 제출하고 MongoDB에는 session/channel/revision/preview/actor/허용 command에 묶인 SHA-256만 저장한다. durable WorkDefinition이 action의 의미 원본이므로 action payload로 goal이나 preview를 바꿀 수 없다. F11의 저장·loader·merger·graph·preview·approval/action 결과도 Component 35의 verified success path만 다음 단계 또는 public output으로 진행한다. 누락된 외부 상태를 자동 생성하거나 무음 fallback하지 않는다.

F20은 승인된 WorkDefinition, tenant/ACL, 활성 snapshot ID와 별도 추가 설계 프롬프트를 Query Planner에서 `design_scope_sha256`/`query_plan_sha256`으로 고정한다. 승인 Skill context를 추가 설계 프롬프트처럼 재사용하지 않는다. query embedding 결과는 두 lock을 보존하고 Retriever는 query plan canonical hash와 vector lock을 재검증하며, Skill/Blueprint 단계는 design scope canonical hash를 다시 계산한다. 추가 설계 프롬프트는 검색 query와 Blueprint prompt 양쪽에서 사용하지만 catalog/Skill 본문처럼 실행 지시로 취급하지 않는다. Flow JSON의 WorkDefinition/ACL/snapshot/Skill registry tweak는 전달 형식일 뿐 신뢰 근거가 아니며, production에서는 trusted backend가 현재 승인 WorkDefinition, 인증 identity/ACL, active catalog pointer, immutable Skill registry를 canonical 저장소에서 읽어 검증한 뒤에만 구성한다.

F30의 게시 요청은 header-authenticated Report API에 전달되고, 응답의 `view_url`/`download_url`은 `view`와 `download` purpose가 분리된 `report-capability/v1` query를 포함한다. capability는 tenant/actor/report/content hash와 60~3600초 만료에 묶이며 signed link 요청에서 identity header와 혼용하지 않는다. `REPORT_RETENTION_DAYS`는 idempotency TTL이므로 report metadata/GridFS artifact 삭제는 별도 lifecycle sweeper가 담당한다.

## 재생성 및 drift 검사

```powershell
& 'C:\Users\qkekt\AppData\Local\Temp\business_work_design_lf1111\Scripts\python.exe' scripts\build_langflow_1_11_flows.py
& 'C:\Users\qkekt\AppData\Local\Temp\business_work_design_lf1111\Scripts\python.exe' scripts\build_langflow_1_11_flows.py --check
```

생성기는 실제 resolved runtime인 `langflow==1.11.1`, `langflow-base==0.11.5`, `lfx==1.11.5`에서만 실행된다.
