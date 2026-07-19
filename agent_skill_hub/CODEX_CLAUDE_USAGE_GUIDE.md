# Codex App·Claude Code·ChatGPT Enterprise 적용 가이드

이 문서는 `agent_skill_hub`를 단일 원본으로 유지하면서 회사 운영 Claude Code CLI, Codex App, 조직 승인 ChatGPT Enterprise, Langflow에 배포하는 절차입니다. Windows PowerShell 기준이며 운영 보안 프로필은 `InternalEnterprise`입니다.

## 1. 먼저 지킬 경계

설치된 모든 Skill에는 [SECURITY_PROFILE.md](./SECURITY_PROFILE.md)가 우선 적용됩니다.

- 회사 운영 Claude model endpoint와 승인된 ChatGPT Enterprise만 사용
- 내부 코드, prompt, log, URL·hostname, 사용자·개인정보, credential, 운영 데이터의 외부 전송 금지
- 공개 문서·패키지는 승인된 read-only proxy·mirror로만 유입
- MCP, browser, Git, CI/CD, deploy, telemetry, database는 local 또는 사내 서비스만 사용
- push, release, deploy, feedback, webhook은 승인된 사내 목적지와 명시적 사용자 지시가 모두 필요
- 외부 model review와 consumer AI 계정 사용 금지

## 2. 원본과 설치본

```text
agent_skill_hub/skills/<skill-id>/       ← 수정할 유일한 원본
             ├─ .agents/skills/<id>/    ← Codex 배포본
             ├─ .claude/skills/<id>/    ← Claude Code 배포본
             └─ exports/                ← ChatGPT/Langflow 생성물
```

설치본과 `exports/`는 직접 수정하지 않습니다. 원본을 고친 뒤 export를 재생성하고 `-Force`로 다시 설치합니다.

## 3. 설치 범위 선택

| 범위 | Codex | Claude Code | 의미 |
| --- | --- | --- | --- |
| Project | `<project>/.agents/skills` | `<project>/.claude/skills` | 해당 프로젝트에서만 사용; 팀 운영 권장 |
| User | `%USERPROFILE%/.agents/skills` | `%USERPROFILE%/.claude/skills` | 같은 Windows 사용자의 모든 프로젝트에서 사용 |

프로젝트별 규칙과 데이터 민감도가 다르면 Project 범위를 권장합니다. User 범위는 어느 프로젝트에서 실행하든 발견될 수 있으므로 조직 공통 Skill만 설치합니다.

## 4. 프로젝트 범위 설치

```powershell
$HubRoot = (Resolve-Path '.\agent_skill_hub').Path
$ProjectRoot = 'C:\path\to\actual-project'

if (-not (Test-Path -LiteralPath $HubRoot -PathType Container)) {
    throw "Hub를 찾을 수 없습니다: $HubRoot"
}
if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
    throw "프로젝트를 찾을 수 없습니다: $ProjectRoot"
}

powershell -NoProfile -ExecutionPolicy Bypass -File "$HubRoot\install.ps1" `
  -Target Codex,ClaudeCode `
  -Scope Project `
  -ProjectRoot $ProjectRoot `
  -SecurityProfile InternalEnterprise
```

현재 31개의 `active` Skill이 설치됩니다. 정확한 ID와 역할은 [SKILL_INVENTORY.md](./SKILL_INVENTORY.md)를 확인합니다. 일부만 설치하려면:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$HubRoot\install.ps1" `
  -Target Codex,ClaudeCode `
  -Skill karpathy-guidelines,hallmark,flint-chart-author `
  -Scope Project `
  -ProjectRoot $ProjectRoot `
  -SecurityProfile InternalEnterprise
```

Addy 컬렉션 22개만 설치하려면:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$HubRoot\install.ps1" `
  -Target Codex,ClaudeCode `
  -Collection addy-agent-skills `
  -Scope Project `
  -ProjectRoot $ProjectRoot `
  -SecurityProfile InternalEnterprise
```

## 5. 사용자 범위 설치

Codex sandbox가 아닌 일반 Windows PowerShell을 열고, `agent_skill_hub`의 상위 폴더에서 실제 사용자 홈과 Hub 경로를 확인합니다.

```powershell
$UserRoot = [Environment]::GetFolderPath('UserProfile')
$HubRoot = (Resolve-Path '.\agent_skill_hub').Path
$UserRoot
$HubRoot
```

두 경로가 현재 PC의 실제 위치와 일치하는지 확인한 후 실행합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$HubRoot\install.ps1" `
  -Target Codex,ClaudeCode `
  -Scope User `
  -SecurityProfile InternalEnterprise
```

설치 결과:

```text
%USERPROFILE%\.agents\skills\<skill-id>\
%USERPROFILE%\.claude\skills\<skill-id>\
```

네, User 범위에 설치하면 같은 Windows 사용자로 실행하는 다른 프로젝트에서도 발견됩니다. 다만 프로젝트 설치본과 같은 ID가 겹치면 혼란이 생길 수 있으므로 중복을 피하고, 민감한 프로젝트 전용 Skill은 Project 범위에 둡니다.

## 6. 기존 설치본 보안 갱신

이번 정리 이전에 설치했다면 기존 폴더에는 새 정책 preamble이 없습니다. 같은 설치 조건에 `-Force`를 붙여 갱신합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$HubRoot\install.ps1" `
  -Target Codex,ClaudeCode `
  -Scope Project `
  -ProjectRoot $ProjectRoot `
  -SecurityProfile InternalEnterprise `
  -Force
```

설치기는 각 Skill 폴더에 `SECURITY_POLICY.md`를 넣고 `SKILL.md` frontmatter 뒤에 enterprise policy preamble을 삽입합니다. `-Force`는 파일을 덮어쓰지만 원본에서 사라진 낡은 파일을 자동 삭제하는 완전 동기화는 아닙니다.

이번에 제거된 14개 Skill은 Hub 원본과 파생 파일에서 모두 삭제됐습니다. 다만 사용자·프로젝트 경로에 과거 복사본이 있다면 Hub 재설치만으로 삭제되지 않습니다. [SKILL_INVENTORY.md](./SKILL_INVENTORY.md)와 [EXCLUDED_SKILLS.md](./EXCLUDED_SKILLS.md)의 ID를 확인하고 정확한 설치 폴더만 별도로 검토·제거해야 합니다.

## 7. 설치 확인

```powershell
$CodexRoot = Join-Path $ProjectRoot '.agents\skills'
$ClaudeRoot = Join-Path $ProjectRoot '.claude\skills'

Get-ChildItem -LiteralPath $CodexRoot -Directory | Sort-Object Name | Select-Object Name
Get-ChildItem -LiteralPath $ClaudeRoot -Directory | Sort-Object Name | Select-Object Name

Test-Path -LiteralPath (Join-Path $CodexRoot 'hallmark\SKILL.md')
Test-Path -LiteralPath (Join-Path $CodexRoot 'hallmark\SECURITY_POLICY.md')
Test-Path -LiteralPath (Join-Path $ClaudeRoot 'hallmark\SKILL.md')
Test-Path -LiteralPath (Join-Path $ClaudeRoot 'hallmark\SECURITY_POLICY.md')
```

네 줄이 모두 `True`여야 합니다.

## 8. Codex App

1. Skill을 설치한 `$ProjectRoot`를 Codex App에서 엽니다.
2. 새 작업을 시작하거나 앱을 다시 시작합니다.
3. 먼저 명시적으로 호출합니다.

```text
$hallmark를 사용해 현재 로컬 UI를 감사해줘.
SECURITY_POLICY.md를 먼저 적용하고 외부 URL, 외부 asset, 외부 model은 사용하지 마.
```

자동 선택은 description에 의존합니다. 보안·재현성이 중요한 작업은 Skill 이름을 명시합니다.

## 9. Claude Code CLI

```powershell
Set-Location -LiteralPath $ProjectRoot
claude
```

명시 호출 예:

```text
/hallmark 현재 UI를 read-only로 감사하고 수정 우선순위만 정리해줘
```

회사 운영 Claude endpoint를 사용하도록 CLI 인증·환경 설정이 사전에 고정되어 있어야 합니다. consumer Claude 계정이나 public API key로 fallback하지 않습니다.

## 10. ChatGPT Enterprise

ChatGPT Enterprise에서는 조직 관리자가 승인한 Enterprise workspace만 사용합니다.

- native Skill 업로드가 허용된 경우 `-Target Custom`으로 만든 정책 포함 패키지를 사용하고, Project instruction 형식이 생성된 Skill만 `exports/chatgpt/`를 사용
- local `ChatGPTDesktop` 설치 대상은 `.agents/skills`를 읽는 데스크톱 환경을 위한 별칭이며 Enterprise workspace 배포 자체를 뜻하지 않음
- connector, action, GPT, 외부 website, 외부 file store를 임의로 연결하지 않음
- workspace의 data retention, connector, admin, training 설정이 사내 규정에 맞는 범위에서만 내부 데이터 사용

조직 기능이 확정되지 않았으면 Skill 전체를 업로드하지 말고, 승인된 Project instruction에 필요한 최소 정책만 넣습니다.

## 11. Tool과 Skill은 별도

`install.ps1`은 지침 파일을 복사할 뿐 다음을 설치하거나 연결하지 않습니다.

- Flint MCP와 renderer package
- Chrome DevTools MCP와 browser
- Python, Node.js, package manager
- Git remote, CI/CD, deployment
- telemetry collector
- Langflow Component·Flow

Flint와 DevTools 실행 파일은 내부 registry·mirror에서 버전을 고정해 미리 설치합니다. 실행 시 `npx -y ...@latest` 같은 public download를 사용하지 않습니다. Tool이 없으면 Skill은 spec·계획·정적 검토로 낮추고 실행됐다고 말하지 않아야 합니다.

## 12. Skill 충돌을 피하는 사용법

- 프로젝트 design system이 Hallmark, Apple, Emil보다 우선
- Apple·Emil·animation specialist는 명시 호출
- `find-animation-opportunities`, `improve-animations`, `review-animations`는 기본적으로 read-only
- planning, spec, TDD, incremental implementation 중 가장 구체적인 primary workflow 하나만 선택
- security policy와 사용자의 read-only 요청은 모든 Skill보다 우선
- external reviewer를 제안하지 않고 승인된 내부 fresh-context review만 사용

## 13. 원본 검증과 export 재생성

```powershell
$env:PYTHONUTF8 = '1'

python "$HubRoot\scripts\skill_eval.py" validate --suite all
python "$HubRoot\scripts\build_exports.py"
python "$HubRoot\scripts\build_exports.py" --check
```

검증 후 설치본을 `-Force`로 갱신합니다.

## 14. 제거

먼저 정확한 대상 하나를 출력하고 확인합니다.

```powershell
$TargetSkill = Join-Path $ProjectRoot '.claude\skills\hallmark'
$TargetSkill
Test-Path -LiteralPath $TargetSkill
```

의도한 프로젝트의 정확한 Skill 폴더가 맞을 때만 제거합니다. 사용자 홈, `.claude`, `.agents`, 프로젝트 루트, wildcard를 재귀 삭제 대상으로 사용하지 않습니다. 제거 후 앱이나 CLI 세션을 다시 시작합니다.

## 15. 완료 체크리스트

- [ ] Project 또는 User 범위를 의도대로 선택
- [ ] `SECURITY_POLICY.md`가 설치됨
- [ ] Codex와 Claude Code에서 명시 호출 확인
- [ ] 회사 운영 model endpoint 또는 승인 Enterprise workspace만 사용
- [ ] MCP·Git·CI·telemetry·storage endpoint가 모두 사내 allowlist
- [ ] public runtime package download 없음
- [ ] 제거된 Skill이 기존 설치 경로에 남지 않았는지 확인
- [ ] 원본 변경 후 export check와 `-Force` 재설치 완료
