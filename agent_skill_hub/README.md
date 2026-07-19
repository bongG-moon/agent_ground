# Agent Skill Hub

`agent_skill_hub`는 사내 Claude Code CLI, Codex App, 조직에서 승인한 ChatGPT Enterprise, 기타 Agent Skill 호환 도구와 Langflow가 같은 Skill 원본을 재사용하도록 관리하는 저장소입니다.

현재 운영 프로필은 `internal-enterprise`입니다. 모든 설치본과 Prompt export에는 [SECURITY_PROFILE.md](./SECURITY_PROFILE.md)가 우선 정책으로 포함됩니다.

## 운영 경계

- 모델 사용: 회사가 운영하는 Claude Code 모델 endpoint 또는 조직이 승인한 ChatGPT Enterprise만 허용
- 외부 전송: 소스 코드, 프롬프트, 로그, 사내 URL·호스트명, 사용자·개인정보, 자격 증명, 운영 데이터 전송 금지
- 외부 유입: 공개 문서와 패키지는 내부 proxy·mirror 또는 승인된 read-only ingress로만 가져오기
- 도구 연결: local 또는 사내 MCP, Git, CI/CD, telemetry, database, browser test 환경만 허용
- 배포·전송: push, release, deploy, webhook, feedback upload는 승인된 사내 목적지와 명시적 사용자 지시가 모두 있을 때만 수행
- 충돌 처리: 보안 정책과 read-only 요청이 다른 Skill보다 우선하며, 한 작업에는 하나의 주 workflow만 선택

제외한 14개 Skill은 canonical 폴더, catalog, source lock, eval, Langflow export, 전용 import script에서 모두 삭제했습니다. 이름과 삭제 사유만 [EXCLUDED_SKILLS.md](./EXCLUDED_SKILLS.md)에 이력으로 남깁니다.

## 현재 구성

활성 Skill은 총 31개입니다.

- 루트 Skill 9개: Karpathy, Flint Chart, Hallmark, animation vocabulary와 5개 motion/design specialist
- `addy-agent-skills` 22개: 요구사항, 설계, 구현, 테스트, 리뷰, 보안, 운영 workflow
- candidate: 현재 0개
- routing eval suite: 현재 0개. 새 candidate를 들일 때 10~20개의 happy/negative case를 먼저 만듭니다.

31개 전체 ID·역할·호출 정책과 삭제된 14개 목록은 [SKILL_INVENTORY.md](./SKILL_INVENTORY.md)를 확인합니다.

외부 출처, 고정 commit, hash, licence는 [sources.lock.json](./sources.lock.json)과 컬렉션별 `sources.lock.json`에서 관리합니다.

## 폴더 역할

```text
agent_skill_hub/
├── skills/                      # 유일한 Skill 원본
├── collections/                 # 여러 Skill을 묶은 카탈로그·출처·공용 reference
├── evals/                       # candidate 라우팅 평가 정의와 사례
├── exports/                     # ChatGPT/Langflow용 생성물; 직접 수정 금지
├── scripts/                     # export·eval 검증 도구
├── catalog.json                 # 루트 Skill 목록과 활성화 방식
├── sources.lock.json            # 출처·commit·hash·licence
├── security-profile.json        # 설치기와 검증기가 읽는 보안 정책
├── SECURITY_PROFILE.md          # 사람이 읽는 필수 보안 경계
├── SKILL_INVENTORY.md           # 현재 31개와 삭제 14개 목록
├── EXCLUDED_SKILLS.md           # 삭제 사유와 재반입 조건
└── install.ps1                  # 대상별 설치기
```

`skills/`만 정본입니다. 설치 경로와 `exports/`는 배포본이므로 수정하지 말고 원본을 고친 뒤 다시 생성·설치합니다.

## 빠른 설치

프로젝트 범위가 기본 권장값입니다. 프로젝트별로 필요한 Skill을 분리할 수 있습니다.

```powershell
$HubRoot = (Resolve-Path '.\agent_skill_hub').Path
$ProjectRoot = 'C:\path\to\project'

powershell -NoProfile -ExecutionPolicy Bypass -File "$HubRoot\install.ps1" `
  -Target Codex,ClaudeCode `
  -Scope Project `
  -ProjectRoot $ProjectRoot `
  -SecurityProfile InternalEnterprise
```

사용자 범위에 설치하면 같은 Windows 사용자로 실행하는 모든 프로젝트에서 보입니다. 일반 PowerShell에서 실제 사용자 홈을 먼저 확인합니다.

```powershell
$UserRoot = [Environment]::GetFolderPath('UserProfile')
$HubRoot = (Resolve-Path '.\agent_skill_hub').Path
$UserRoot
$HubRoot

powershell -NoProfile -ExecutionPolicy Bypass -File "$HubRoot\install.ps1" `
  -Target Codex,ClaudeCode `
  -Scope User `
  -SecurityProfile InternalEnterprise
```

설치 위치:

- Codex App: 프로젝트 `.agents/skills` 또는 사용자 `%USERPROFILE%\.agents\skills`
- Claude Code: 프로젝트 `.claude/skills` 또는 사용자 `%USERPROFILE%\.claude\skills`
- 다른 호환 CLI: `-Target Custom -Destination <approved-local-path>`

특정 Skill이나 컬렉션만 설치할 수 있습니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$HubRoot\install.ps1" `
  -Target Codex,ClaudeCode `
  -Skill karpathy-guidelines,hallmark,flint-chart-author `
  -Scope Project `
  -ProjectRoot $ProjectRoot `
  -SecurityProfile InternalEnterprise

powershell -NoProfile -ExecutionPolicy Bypass -File "$HubRoot\install.ps1" `
  -Target Codex,ClaudeCode `
  -Collection addy-agent-skills `
  -Scope Project `
  -ProjectRoot $ProjectRoot `
  -SecurityProfile InternalEnterprise
```

기존 설치본을 새 정책으로 갱신할 때는 `-Force`를 붙입니다. 이번 보안 정리 전 설치본은 반드시 다시 설치해야 `SECURITY_POLICY.md`와 정책 preamble이 들어갑니다.

## ChatGPT Enterprise

ChatGPT Enterprise에는 개인·consumer 계정이 아니라 조직이 승인한 Enterprise workspace만 사용합니다. 로컬 `ChatGPTDesktop` 대상은 Codex와 같은 `.agents/skills` 배포 경로를 쓰는 환경을 위한 별칭입니다. Enterprise workspace에 native Skill을 올릴 때는 `-Target Custom`으로 만든 정책 포함 패키지를 사용하고, Project instruction 형식이 생성된 Skill만 `exports/chatgpt/`를 사용합니다. 조직 관리자가 허용한 업로드 절차를 따라야 합니다.

내부 코드·로그·운영 데이터가 포함된 Skill 실행은 Enterprise workspace의 보존·connector·학습·관리 정책이 사내 규정에 맞는지 확인된 범위에서만 수행합니다. 외부 connector나 public GPT/action은 이 Hub의 승인 대상이 아닙니다.

## Langflow

Langflow는 동일한 정본에서 생성한 `exports/langflow/<skill-id>/adapter.json`과 Prompt를 사용합니다. 모델 endpoint, tool endpoint, tracing, storage는 모두 사내 주소여야 합니다. 외부 MCP·webhook·telemetry·LLM provider를 adapter에 추가하지 않습니다.

## 생성과 검증

```powershell
$env:PYTHONUTF8 = '1'
python "$HubRoot\scripts\build_exports.py"
python "$HubRoot\scripts\build_exports.py" --check
python "$HubRoot\scripts\skill_eval.py" validate --suite all
```

검증은 catalog와 Skill 구조, source hash, 보안 제외 목록, export 최신성, candidate eval 연결을 확인합니다.

## 새 Skill 반입 절차

1. 출처, 고정 commit, licence, 전체 파일을 확보합니다.
2. 실행 코드, network egress, telemetry, MCP, hook, browser, Git/CI/deploy 지시를 독립 검토합니다.
3. 기존 Skill과 trigger·workflow·쓰기 권한 충돌을 확인합니다.
4. `candidate`로 등록하고 10~20개 happy/negative routing eval을 작성합니다.
5. 실제 승인 모델 환경에서 blind eval을 통과시킵니다.
6. 보안 검토와 hash 갱신 후에만 `active`로 승격합니다.

자세한 절차는 [ADDING_A_SKILL.md](./ADDING_A_SKILL.md)와 [EVALUATION.md](./EVALUATION.md)를 따릅니다.

## 상세 문서

- [CODEX_CLAUDE_USAGE_GUIDE.md](./CODEX_CLAUDE_USAGE_GUIDE.md): 설치, 확인, 업데이트, 제거
- [PORTABILITY.md](./PORTABILITY.md): 실행 환경별 차이와 fallback
- [LANGFLOW_INTEGRATION.md](./LANGFLOW_INTEGRATION.md): Langflow 연결 경계
- [CROSS_AGENT_INSTRUCTIONS.md](./CROSS_AGENT_INSTRUCTIONS.md): `AGENTS.md`와 `CLAUDE.md` 단일 정본 운영
- [SECURITY_PROFILE.md](./SECURITY_PROFILE.md): 사내 필수 정책
- [SKILL_INVENTORY.md](./SKILL_INVENTORY.md): 현재 활성·삭제 Skill 전체 목록
- [EXCLUDED_SKILLS.md](./EXCLUDED_SKILLS.md): 제거 대상과 재검토 조건
