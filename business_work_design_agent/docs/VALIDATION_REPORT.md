# 검증 결과

검증 일자: 2026-08-28 (Asia/Seoul)

## 고정 런타임

- `langflow==1.11.1`
- `langflow-base==0.11.5`
- `lfx==1.11.5`
- Python `3.13.14`

검증은 전용 환경 `C:\Users\qkekt\AppData\Local\Temp\business_work_design_lf1111`에서 수행했다. 별도 설치된 Langflow Desktop은 변경하지 않았다.

## 자동 검증

| 항목 | 결과 |
| --- | --- |
| 전체 pytest | `240 passed, 1 skipped, 8 warnings` (`33.04s`) |
| skip 사유 | Windows host에서 file symlink 생성 불가 시 경계 테스트 1건 skip |
| Python compile | `components`, `services`, `scripts`, `tests` 통과 |
| Standalone source build | 37개 전부 Langflow 1.11.1 template build 통과 |
| Flow `Graph.from_payload` | 6개 Flow, 총 348 node/428 edge 역직렬화·handle 검사 통과 |
| `lfx validate --level 3` | 6개 Flow 중 6개 통과, 0개 실패 |
| Flow generator drift | 6개 Flow JSON과 bundle이 현재 source와 일치 |
| Bundle SHA-256 | `57ca8dee70ec0b67bf5a04bf5868d1f9105467ebdcd989e0b4a28d422b00470e` |

검증 범위에는 Component subclass 수, 명시적 표준/`lfx` import allowlist, 상대·로컬·private/dynamic import 및 reflection 우회 금지, embedded source byte/hash, 실제 output/input handle 호환성, catalog worker와 F00 decision/trusted-gateway attested activation 분리, HITL 배치·semantic/runtime revision·state 전이, strict answer/deadline, Component 35 fail-closed 결과 분기, 승인 semantic hash 재검증, Skill ACL fail-closed와 7필드 authority projection, design scope/query/candidate allowlist lock, canonical port contract hash와 catalog asset binding, 표시 포트 변조 차단 및 node/detail 포트 동일성, generation request 결정론·1:1 binding, report signed capability, deterministic report identity, schema/reference, tenant/owner 격리, idempotency lease recovery, CSP/hash/XSS가 포함된다. 이 정적 source guard는 Python 보안 sandbox를 대신하지 않는다.

`lfx validate` verbose 결과에는 현재 CLI가 Flow의 `built with Langflow 1.11.1`을 설치된 `langflow-base 0.11.5`와 비교해 표시하는 version warning, `lfx.interface.utils.initialize_components` registry import warning, generic `other` input에 대한 휴리스틱 edge-type warning이 남는다. 명령 종료 코드는 0이고 6개 모두 `ok=true`다. 이 CLI warning 때문에 별도 runtime validator가 실제 설치 버전(`langflow 1.11.1`, `langflow-base 0.11.5`, `lfx 1.11.5`), 37개 source build, embedded byte/hash, handle, `Graph.from_payload`를 직접 검증한다.

생성 manifest의 Flow별 결과:

| Flow | Node/Edge | SHA-256 |
| --- | ---: | --- |
| F00 | 10 / 10 | `5f6557160e9a82d1a52b0bfae8e50c384840ddb666c405843d2ad9529836b2f8` |
| F10 | 224 / 286 | `47eefeb131aff2bb409a2dc5e7fec81a1f25bba7633b69cb9c976f4c460d29c4` |
| F11 | 89 / 103 | `74d7adb6ec223d01dc9d5b598e1d1cbc1d574c14fdd7cd6d0c260dc05362d6c7` |
| F20 | 16 / 21 | `64c29d4a36a614ffba6bf1c4eb832a17039021d7bd6eb9ef7b512fb4cc2854eb` |
| F30 | 3 / 2 | `52535dfd5a75b9227046f152327678533cd283529fde4c1774347fb472149ed7` |
| F90 | 6 / 6 | `b78cd0631ffbbc70895bed5c8eca1a241ba5053392b67310ca5c9c959faba328` |

## 브라우저 반응형 QA

자체 생성 샘플 `samples/generated_sample_report.html`을 로컬 HTTP로 열어 확인했다.

- 1440×900: 문서 `scrollWidth=clientWidth=1425`, 두 graph와 숨겨진 desktop drawer 정상
- 390×844: 문서 `scrollWidth=clientWidth=375`, drawer가 fixed bottom sheet로 전환
- 노드 선택: 상세 내용 표시, close focus 이동 정상
- edge label 선택: source/target/port/mapping 상세 표시
- 전체 흐름 맞춤과 확대 동작 정상
- console error/warning 없음
- JavaScript 사용 시 중복 static fallback 숨김, JavaScript 비활성/인쇄 시 text fallback 유지
- 알려진 UI 범위: graph `groups` metadata는 보존하지만 현재 renderer는 group overlay·접기/펼치기를 제공하지 않음

## 실제 인프라에서 남은 검증

아래는 로컬 계약/역직렬화 검증만으로 완료 처리할 수 없다.

- 운영 MongoDB replica set transaction과 Atlas Search index
- 실제 embedding provider의 model/version/dimension 및 endpoint allowlist
- 사내 LLM gateway의 구조화 출력
- Langflow background workflow suspend → pending 검증 → resume E2E
- 사내 trusted admin gateway의 F00 decision 검증 → attestation 발급 → worker `/activate` E2E
- 사내 gateway bearer/tenant/actor 주입과 교차 tenant 차단
- F20 trusted backend의 canonical 승인 WorkDefinition·identity/ACL·snapshot·Skill registry 조립과 caller tweak 위조 차단
- Report API 실제 게시, purpose별 signed capability URL browser 조회, tamper/expiry/purpose/header-mix 차단과 교차 tenant 차단
- Uvicorn/reverse proxy/access analytics의 signed capability query redaction 또는 suppression
- report metadata/GridFS artifact retention·hold·purge sweeper와 backup/restore
- 사내 catalog 원본 2만~3만 행의 성능·재시작·장애 주입 검증
- 만료된 native HITL pending job을 중단하고 terminal runtime event를 기록하는 운영 sweeper

따라서 현재 생성물은 Langflow 1.11.1에서 검증된 구현 및 import artifact이지만, 위 항목을 통과하기 전에는 production-ready로 승격하지 않는다.
