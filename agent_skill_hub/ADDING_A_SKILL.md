# 새 Skill 추가하기

## 1. 추가 가치부터 확인

다음 조건을 만족할 때만 추가합니다.

- 반복해서 쓰는 작업 또는 지식인가
- 기존 Skill과 역할이 겹치지 않는가
- 입력, 출력 또는 성공 기준을 설명할 수 있는가
- 외부 Skill이면 라이선스와 출처를 확인할 수 있는가
- 외부 model·MCP·telemetry·webhook·memory·Git/CI/deploy로 사내 정보가 나갈 경로가 없는가
- `SKILL_INVENTORY.md`의 기존 31개와 trigger·workflow·쓰기 권한이 겹치지 않는가
- Langflow에서 항상 적용할 지침인지, 필요할 때 호출할 기능인지 구분할 수 있는가

## 2. 원본 폴더 만들기

```text
skills/<skill-id>/
├── SKILL.md
├── SOURCE.md            # 외부 Skill일 때 권장
├── agents/openai.yaml   # Codex/ChatGPT Desktop UI 메타데이터가 필요할 때
├── references/          # 선택
└── scripts/             # 선택
```

`SKILL.md`에는 최소한 다음 frontmatter가 있어야 합니다.

```yaml
---
name: skill-id
description: 언제 사용하고 언제 사용하지 않는지 분명하게 설명합니다.
---
```

Agent Skills 표준의 필수 frontmatter는 `name`과 `description`입니다. `license`는 upstream에 있으면 보존하고, 없으면 `sources.lock.json`과 Skill 폴더의 `SOURCE.md`에서 관리합니다.

## 3. 외부 출처 잠그기

외부 Skill은 `main`의 최신 내용을 계속 따라가지 않습니다. 검토한 커밋을 고정하고 `sources.lock.json`에 다음을 기록합니다.

- 사용자가 준 원래 URL
- 이동 또는 리다이렉트된 현재 URL
- 저장소 내부 경로
- 40자리 Git 커밋
- 가져온 `SKILL.md`의 SHA-256
- 로컬 출처 고지를 포함한 전체 Skill 패키지 SHA-256
- 라이선스

업데이트할 때는 새 커밋과 기존 커밋의 diff를 먼저 검토한 뒤 SHA-256을 갱신합니다.

## 4. 사내 보안 파생본 만들기

외부 원문을 그대로 `active`로 사용하지 않습니다. 다음을 제거하거나 사내 대체 경로로 고정합니다.

- third-party model과 consumer AI fallback
- public MCP, telemetry, webhook, memory persistence
- public Git push, CI/CD, deployment, feedback upload
- 개인 browser profile과 외부 authenticated service 제어
- `npx -y ...@latest` 같은 runtime public package download
- internal URL, code, log, screenshot, credential을 외부 request에 넣는 지시

원본 hash는 `source_skill_sha256`, `source_package_sha256`으로 보존하고, 사내 파생본 hash를 `skill_sha256`, `package_sha256`으로 기록합니다. `derivation`에는 `internal-enterprise-sanitized-canonical`을 사용합니다.

## 5. 카탈로그 등록

`catalog.json`에 ID, 용도, 상태, 카테고리, 대상 환경, 활성화 방식을 추가합니다.

Langflow 모드는 다음 기준을 사용합니다.

- `system-prompt`: 코딩 원칙, 안전 규칙, 응답 정책처럼 항상 적용해야 하는 지침
- `tool`: 검색, 계산, API 호출, 문서 로딩처럼 Agent가 필요할 때 호출할 기능
- `run-flow`: 여러 단계와 상태가 있는 재사용 업무 절차
- `component`: 하나의 명확한 입력/출력 계약을 가진 재사용 기능

## 6. 여러 Skill을 컬렉션으로 추가

한 저장소에서 여러 Skill이 함께 오고 루트 레퍼런스를 공유한다면 `catalog.json`에 모두 펼쳐 쓰지 않고 `collections/<collection-id>/`로 분리합니다.

```text
collections/<collection-id>/
├── catalog.json
├── sources.lock.json
└── shared/
    ├── LICENSE
    └── references/
```

컬렉션 카탈로그의 `skill_defaults`에는 공통 활성화 정책과 대상 환경을 두고, 각 Skill에는 ID·설명·분류·예외 설정·필요한 `shared_references`만 기록합니다. 설치기는 공용 파일을 필요한 Skill 패키지에만 결합해야 합니다.

## 7. candidate eval

신규 Skill은 먼저 `status: candidate`로 등록합니다. `evaluation.required_for_active: true`로 설정하고 `evals/manifest.json`의 suite에 연결합니다.

`security-profile.json`의 `excluded_skill_ids`에 있는 ID는 같은 이름으로 재등록하지 않습니다. 재검토가 승인되면 새 source review와 eval을 수행하고, 제외 사유가 해소됐다는 기록을 먼저 남깁니다.

각 suite는 다음을 만족해야 합니다.

- 실제 요청을 닮은 JSONL 프롬프트 10~20개
- Skill이 선택되어야 하는 happy path
- 비슷한 단어가 있어도 선택되면 안 되는 negative/no-op case
- canonical `SKILL.md` 500단어 이하
- 범위 밖 요청에서 동작하지 않는 명시적 no-op 지침
- 기존 Skill과 중복될 때 가장 작은 Skill만 고르는 기대값

```powershell
python .\agent_skill_hub\scripts\skill_eval.py validate --suite <suite-id>
python .\agent_skill_hub\scripts\skill_eval.py prepare `
  --suite <suite-id> `
  --output .\.pytest-tmp\<suite-id>-blind.jsonl
```

블라인드 모델 결과를 `score`로 평가해 임계치를 모두 통과한 뒤에만 `evaluation.status: passed`와 `status: active`로 승격합니다. 자세한 절차는 `EVALUATION.md`를 따릅니다.

## 8. 생성 및 검증

```powershell
python .\agent_skill_hub\scripts\build_exports.py
python .\agent_skill_hub\scripts\build_exports.py --check
python .\agent_skill_hub\scripts\skill_eval.py validate --suite all
```

마지막으로 Codex와 Claude Code 프로젝트 설치를 각각 임시 폴더에 실행해 폴더 구조를 확인합니다.
