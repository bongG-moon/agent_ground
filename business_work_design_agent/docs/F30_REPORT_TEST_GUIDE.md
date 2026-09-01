# F30 반응형 Report 테스트 가이드

## 목적

F30은 승인된 업무 정의와 Agent Blueprint를 사람이 검토할 수 있는 반응형 HTML 보고서로 만들고, 필요할 때 Report API로 게시하는 Flow입니다.

이 가이드는 F10/F20을 실제로 다시 실행하지 않아도, 준비된 성공 산출물로 F30의 다음 경로를 안전하게 확인하는 방법을 설명합니다.

```text
F20 sealed report handoff
  -> Chat Input -> Type Convert(JSON) -> 33 Handoff Loader
  -> 30: Report View Model -> 31: Responsive HTML Renderer
  -> 32: Publisher dry-run (저장/게시 없음)
  -> 37: 게시 결과 메시지 -> Chat Output
```

이 테스트는 F30의 입력 검증, HTML 렌더링, 비저장 Publisher 경로를 검증합니다. F10의 승인/HITL 및 F20의 검색·설계 자체를 검증하는 테스트는 아닙니다.

## 가장 빠른 방법: 샘플 산출물로 Langflow Canvas에서 dry-run

### 1. F30 Flow를 import

Langflow에서 [`F30_responsive_report.json`](../flows/F30_responsive_report.json)을 import합니다.

F30은 이제 F10의 `Run Flow`가 직접 호출할 수 있는 child Flow입니다. Canvas에서 단독 확인할 때도 Component 30에 세 산출물을 나누어 넣지 않고, F20이 만든 **단일 sealed handoff JSON**을 Chat Input에 넣습니다.

### 2. Chat Input에 넣을 샘플

[`samples/f20_report_handoff.json`](../samples/f20_report_handoff.json)의 **전체 JSON object**를 F30 `Chat Input`에 붙여 넣습니다. 이 fixture에는 동일 실행의 승인 WorkDefinition, F20 terminal Blueprint envelope, retrieval trace, tenant/actor context 및 canonical hash가 함께 들어 있습니다.

`33 F30 Report Handoff Loader`가 이 값을 검증하고 세 값을 Component 30에 자동 분리해 전달합니다. `approved_work_definition.json`, `agent_blueprint_terminal.json`, `candidate_context.json`을 각각 넣는 이전 방식은 더 이상 F30 Canvas의 정상 실행 경로가 아닙니다.

### 3. Component 32를 안전한 테스트 모드로 설정

Component 32 (`Business Flow Report Publisher`)에서 다음을 확인합니다.

| 설정 | 샘플 테스트 값 | 이유 |
| --- | --- | --- |
| `테스트 실행 (저장하지 않음)` | `true` | Report API에 저장하거나 게시하지 않습니다. |
| `Report API URL` | `http://127.0.0.1:5000` | API base URL입니다. `/reports`까지 넣어도 됩니다. |
| `HTML Link TTL (hours)` | `4` | 서버가 생성할 view/download 링크의 보관·만료 요청값입니다. |

### 4. 실행 및 합격 기준

Chat Input부터 Publisher까지 실행합니다. 다음 결과면 합격입니다.

| 확인 위치 | 기대 결과 |
| --- | --- |
| Component 33 | handoff가 검증되고 `Work Definition` / `Blueprint` / `Retrieval Trace` 출력이 생성됨 |
| Component 30 | view model 결과에 `ok: true` |
| Component 31 | `ok: true`, `status: "RENDERED"` 및 self-contained HTML |
| Component 32 | `ok: true`, `status: "would_publish"` |
| Component 37 / Chat Output | "Report API에는 저장하지 않았습니다" 취지의 읽기 쉬운 테스트 안내 |

`would_publish`는 오류가 아닙니다. **dry-run이 성공했고 실제 게시만 생략했다**는 의미입니다.

HTML 결과에서는 다음을 확인합니다.

- 보고서 첫 화면에 **업무 개요 → 현행 절차/문제 → 개선 방향 → 권장 운영 방식 → 구현 분담 → 구현·검증 계획 → 미확정 사항** 순서의 완성형 본문이 표시되는지
- 업무 목표·범위·입출력·담당/시스템, 현행 절차·문제·위험/통제, TO-BE 절차와 분기·예외·사람 검토 지점이 승인된 입력을 근거로 표시되는지
- 정보가 없는 항목을 그럴듯하게 생성하지 않고 `미확정/추가 확인 필요`로 표시하는지
- AS-IS/TO-BE Flow, 분기·예외 경로, 적용 Skill, 신규 Standalone Custom Component 제안이 본문 및 Flow 근거와 일치하는지
- **카탈로그 기반 적용 계획**에서 단계별 적용/검토 후보, 카테고리·버전·계약 상태·선정 이유가 표시되는지
- 업로드 카탈로그에 `catalog_url`(또는 동등 링크 필드)이 있었다면 해당 카드의 **카탈로그 상세 열기**가 정상 링크인지
- node/edge 클릭 시 상세 내용이 열리는지
- desktop과 mobile 폭에서 graph overflow 또는 detail drawer 잘림이 없는지
- 입력 문자열에 HTML/script 형태의 텍스트가 있어도 실행되지 않고 텍스트로 처리되는지

## 실제 F10/F20 결과로 F30을 검증하는 방법

F10/F20이 정상 동작한 뒤에는 샘플 대신 실제 산출물을 사용합니다.

정상 경로에서는 수동 복사가 필요 없습니다. F10의 승인 성공 경로가 F20을 실행하고, F20 `38 F20 Report Handoff Builder`가 세 artifact를 하나의 `f20-report-handoff/v1` JSON으로 고정합니다. F10 `44 F20→F30 Report Handoff Gate`가 schema/hash를 확인한 뒤에만 F30 `Run Flow`로 전달합니다.

F30만 독립 실행해야 한다면 같은 F20 실행에서 받은 **완전한 handoff JSON 하나**를 Chat Input에 넣습니다. 서로 다른 실행의 값을 섞어 새 JSON을 조립하지 마세요. F30 Loader와 Component 30이 identity/revision/hash를 교차 검증하여 의도적으로 차단합니다.

실제 게시 전에도 먼저 `dry_run=true`로 위 합격 기준을 통과시키세요. 실제 Report API 게시(`dry_run=false`)는 공유 HTML Report API가 실행 중일 때만 수행합니다. F30 Publisher는 `POST {Report API URL}/reports`로 HTML, 제목, TTL, filename hint와 F30용 report plan을 보냅니다. 이 API는 서버가 `view_url`과 `download_url`을 새로 생성하며, `37 보고서 게시 결과 메시지`가 이를 **보고서 열기**와 **HTML 다운로드** 하이퍼링크로 출력합니다.

## 자동화된 로컬 회귀 테스트

아래 명령은 샘플 산출물의 재현성, F20 terminal envelope -> F30 handoff, HTML 렌더링, Publisher dry-run의 네트워크 미호출을 검증합니다.

```powershell
$ProjectRoot = 'C:\Users\qkekt\Desktop\Agent_ground\business_work_design_agent'
$Python = 'C:\Users\qkekt\AppData\Local\Temp\business_work_design_lf1111\Scripts\python.exe'
Set-Location $ProjectRoot

# 샘플이 현재 component 계약으로 재현되는지 검사 (쓰기 없음)
& $Python scripts\build_sample_contracts.py --check

# Langflow Desktop 환경의 외부 pytest 플러그인 충돌을 피함
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
& $Python -m pytest -q tests\test_sample_contracts.py tests\test_report_components.py
```

단순 렌더링 결과만 다시 생성하는 `scripts/render_sample_report.py`는 `samples/report_view_model.json`과 `samples/generated_sample_report.html`을 덮어씁니다. 검증만 하려는 경우에는 실행하지 말고, 위의 `--check` 및 pytest 명령을 사용합니다.

## 자주 발생하는 실패

| 증상 | 원인 및 조치 |
| --- | --- |
| scope/hash, snapshot, tenant, revision 오류 | handoff를 변조했거나 서로 다른 F10/F20 실행의 artifact를 섞었습니다. F20이 만든 handoff 전체를 다시 사용합니다. |
| `would_publish`가 표시됨 | 정상 dry-run 성공입니다. `dry_run=true`에서는 실제 URL이 생성되거나 게시되지 않습니다. |
| `PUBLISH_FAILED`가 표시됨 | Flow가 멈춘 것이 아니라 게시 API가 반환한 오류입니다. `error.code`, `error.message`, `target_url`을 확인합니다. 연결 오류는 API server 주소/실행 상태를 먼저 확인합니다. |
| Report API 연결 오류를 예상함 | dry-run에서는 네트워크 호출이 없어야 합니다. 실제 게시 테스트에서만 API URL과 서버 상태를 확인합니다. |
| 일반 문장을 입력하고 실행함 | F30은 일반 대화형 Flow가 아닙니다. Chat Input에는 `f20-report-handoff/v1` JSON 전체가 필요합니다. |

## 테스트 기록 체크리스트

- [ ] 사용한 Flow: F30 최신 import본
- [ ] artifact 출처: 샘플 `f20_report_handoff.json` 또는 동일 F10/F20 실행의 sealed handoff
- [ ] Component 33/30/31 결과가 성공
- [ ] Component 32 결과 `status: "would_publish"`
- [ ] Report API 네트워크 게시 없음
- [ ] desktop/mobile UI와 detail drawer 확인 완료
