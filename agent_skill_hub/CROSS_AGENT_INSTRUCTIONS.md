# 여러 코딩 에이전트의 프로젝트 규칙을 하나로 관리하기

Codex, ChatGPT Desktop, Claude Code와 다른 바이브 코딩 CLI를 같은 저장소에서 사용할 때 규칙과 워크플로가 서로 갈라지지 않도록 관리하는 실전 매뉴얼입니다.

## 결론

- 공통 프로젝트 규칙의 정본은 저장소 루트 `AGENTS.md`로 둡니다.
- Claude Code용 `CLAUDE.md`는 `@AGENTS.md`로 정본을 import합니다.
- Claude 전용 규칙이 필요할 때만 import 아래에 짧은 별도 섹션을 둡니다.
- 반복 실행 절차와 커맨드는 규칙 파일에 넣지 않고 포터블 `SKILL.md`로 관리합니다.
- 반드시 강제해야 하는 정책은 지시문이 아니라 hook, lint, test, CI로 집행합니다.

이 Hub에서는 [SECURITY_PROFILE.md](./SECURITY_PROFILE.md)가 공통 규칙과 Skill보다 우선합니다. Claude Code는 회사 운영 model endpoint, ChatGPT는 승인된 Enterprise workspace만 사용하며, hook·CI·MCP·Git·telemetry도 local 또는 사내 승인 서비스로 제한합니다.

Threads 게시물의 “`CLAUDE.md`에서 `AGENTS.md`를 참고하게 한다”는 방향은 유효합니다. 다만 자연어 문장만 쓰는 것보다 Claude Code가 공식 지원하는 `@path` import 문법이 더 명시적이고 검증 가능합니다.

## 권장 구조

```text
repo-root/
├── AGENTS.md                    # 공통 프로젝트 규칙의 유일한 정본
├── CLAUDE.md                    # @AGENTS.md bridge + 최소 Claude 전용 규칙
├── .agents/skills/              # Codex/ChatGPT Desktop 설치 대상
├── .claude/skills/              # Claude Code 설치 대상
└── agent_skill_hub/skills/      # 선택 사항: Skill 원본 저장소
```

`AGENTS.md`에는 모든 작업에서 알아야 하는 짧고 검증 가능한 규칙만 둡니다.

```markdown
# Project instructions

## Build and test
- Run `npm test` after changing application code.
- Run `npm run lint` before declaring completion.

## Repository rules
- Keep public API changes backward compatible unless the task explicitly allows a break.
- Do not edit generated files directly.

## Review
- Report validation commands and unresolved risks in the final response.
```

Claude 전용 규칙이 없다면 `CLAUDE.md`는 한 줄이면 충분합니다.

```markdown
@AGENTS.md
```

Claude 전용 차이가 있을 때만 아래처럼 추가합니다.

```markdown
@AGENTS.md

## Claude Code only
- Use plan mode before changing files under `src/billing/`.
```

## 왜 자연어 포인터 대신 import를 쓰는가

`AGENTS.md를 참고해`는 모델에게 파일을 읽으라는 일반 지시입니다. 실제로 읽었는지, 어느 시점에 읽었는지, 압축 이후에도 유지되는지를 별도로 확인해야 합니다.

`@AGENTS.md`는 Claude Code의 공식 import 문법입니다. 상대 경로는 `CLAUDE.md`가 있는 위치를 기준으로 해석되고, 세션 시작 시 import된 내용이 함께 로드됩니다. 외부 경로를 처음 import할 때는 Claude Code가 승인 창을 표시할 수 있으므로 비밀 파일이나 사용자 홈의 민감한 문서를 연결하지 않습니다.

## 규칙과 Skill을 구분하기

| 내용 | 둘 위치 | 이유 |
| --- | --- | --- |
| 빌드·테스트 명령, 코드 규칙, 저장소 구조 | `AGENTS.md` | 모든 작업에 적용되는 짧은 사실과 규칙 |
| Claude에서만 필요한 동작 | `CLAUDE.md`의 전용 섹션 | 공통 정본을 오염시키지 않는 호스트 차이 |
| 특정 폴더에만 필요한 규칙 | 해당 폴더의 `AGENTS.md`와 `CLAUDE.md` bridge | 가장 가까운 범위에서만 적용 |
| 리뷰, 배포, 분석 같은 다단계 절차 | `SKILL.md` | 필요할 때만 로드하는 반복 워크플로 |
| 브라우저, 사내 Git·DB 같은 실제 기능 | Skill + 승인된 local/사내 MCP·도구 | 지침과 실행 능력을 분리 |
| 절대 위반하면 안 되는 정책 | 사내 hook, lint, test, CI | 규칙 파일은 강제 계층이 아님 |

긴 절차를 `AGENTS.md`나 `CLAUDE.md`에 넣으면 모든 세션의 context를 차지합니다. 여러 단계, 예시, 참고 자료 또는 스크립트가 필요한 내용은 Skill로 이동합니다.

## `.claude/commands/`를 함께 쓰는 방법

`.claude/commands/*.md`는 Claude Code에서 계속 동작하지만 현재는 레거시 형식입니다. 공용 워크플로를 위해 Codex에게 이 폴더를 매번 검색하라고 지시하는 방식은 기본안으로 삼지 않습니다.

새 워크플로는 다음 형태로 작성합니다.

```text
agent_skill_hub/skills/review-change/
└── SKILL.md
```

그 한 원본을 설치기로 다음 위치에 배포합니다.

- Codex/ChatGPT Desktop: `.agents/skills/review-change/SKILL.md`
- Claude Code: `.claude/skills/review-change/SKILL.md`
- 다른 CLI: Agent Skills 경로 또는 명시적 `SKILL.md` 절대 경로

기존 `.claude/commands/review.md`가 있다면 내용을 새 Skill로 옮기고, 동작 검증 후 레거시 command를 제거합니다. 같은 이름의 command와 Skill을 함께 두면 Claude Code에서는 Skill이 우선하므로 장기간 이중 관리하지 않습니다.

## 하위 폴더와 모노레포

Codex는 프로젝트 루트에서 현재 작업 디렉터리까지의 `AGENTS.md`를 합치며 더 가까운 파일을 나중에 적용합니다. Claude Code도 디렉터리 계층의 `CLAUDE.md`를 읽고 하위 파일은 관련 파일을 읽을 때 발견합니다.

하위 폴더에 공통 규칙이 필요하면 같은 패턴을 반복합니다.

```text
packages/frontend/
├── AGENTS.md
└── CLAUDE.md    # 내용: @AGENTS.md
```

루트 `CLAUDE.md`가 루트 `AGENTS.md`를 import한다고 해서 하위 `AGENTS.md`까지 자동 import되는 것은 아닙니다. 하위 규칙을 두 도구가 함께 사용해야 한다면 해당 폴더에도 bridge를 둡니다.

## 심볼릭 링크와 자동 생성

| 방식 | 권장 상황 | 주의점 |
| --- | --- | --- |
| `CLAUDE.md`의 `@AGENTS.md` | 기본 권장 | 단순하고 Windows에서도 관리하기 쉬움 |
| `CLAUDE.md -> AGENTS.md` 심볼릭 링크 | Unix 중심이며 Claude 전용 섹션이 전혀 없음 | Windows는 관리자 권한 또는 Developer Mode가 필요할 수 있음 |
| 한 정본에서 두 파일 자동 생성 | 조직이 이미 생성·검증 파이프라인을 운영함 | 생성 누락과 수동 수정으로 drift가 생기지 않도록 CI 필요 |
| 두 파일 수동 복제 | 권장하지 않음 | 내용이 조용히 갈라짐 |

## 기존 저장소 전환 절차

1. 루트와 하위 폴더의 `AGENTS.md`, `CLAUDE.md`, `.claude/rules/`, `.claude/commands/`, `.agents/skills/`, `.claude/skills/`를 목록화합니다.
2. 두 규칙 파일의 공통 내용, 충돌, 오래된 명령을 비교합니다.
3. 모든 호스트가 따라야 할 짧은 규칙만 루트 `AGENTS.md`로 합칩니다.
4. `CLAUDE.md`를 `@AGENTS.md` bridge로 바꾸고 Claude 전용 내용만 남깁니다.
5. 다단계 절차와 레거시 command는 포터블 Skill로 이동합니다.
6. 민감한 로컬 설정은 커밋하지 말고 각 호스트의 local/user 범위에 둡니다.
7. 두 에이전트에서 실제 로딩을 확인한 뒤 중복 규칙을 삭제합니다.

## 검증 체크리스트

- Codex에 “현재 적용 중인 프로젝트 지침을 요약해줘”라고 요청해 루트와 하위 `AGENTS.md` 범위를 확인합니다.
- Claude Code에서 `/context`를 실행해 `CLAUDE.md`와 import된 `AGENTS.md`가 로드됐는지 확인합니다.
- `CLAUDE.md`가 `@AGENTS.md`를 정확히 한 번 import하는지 확인합니다.
- 공통 규칙이 `CLAUDE.md`에 다시 복제되지 않았는지 확인합니다.
- `.agents/skills`와 `.claude/skills`의 설치본을 직접 수정하지 않고 canonical Skill에서 재배포하는지 확인합니다.
- 규칙 파일의 명령이 현재 저장소에서 실제로 실행 가능한지 확인합니다.
- 보안·배포 차단 같은 하드룰에 hook 또는 CI 검증이 있는지 확인합니다.

## Langflow와 ChatGPT Enterprise

Langflow에는 저장소의 전체 `AGENTS.md`를 무조건 system prompt로 넣지 않습니다. 필요한 규칙만 flow의 system instruction으로 선택하고, 반복 절차는 같은 canonical Skill에서 생성한 Prompt, MCP Tool, Component 또는 Run Flow 어댑터를 사용합니다.

ChatGPT Enterprise에는 조직이 승인한 workspace와 업로드 기능만 사용합니다. 필요한 Skill은 정책이 포함된 Custom 설치 패키지 또는 생성된 Project instruction으로 전달하며, consumer ChatGPT나 외부 connector에는 내부 자료를 첨부하지 않습니다. 이 경우에도 원본을 별도로 수정하지 않고 Hub의 canonical 파일에서 다시 내보냅니다.

## 출처

- [Threads 원문: Codex와 Claude Code 규칙 파일 단일화](https://www.threads.com/@workfree.wave/post/Da5Lo0QlIwS)
- [OpenAI Codex: Custom instructions with AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- [OpenAI Codex: Customization overview](https://learn.chatgpt.com/docs/customization/overview)
- [Claude Code: How Claude remembers your project](https://code.claude.com/docs/en/memory)
- [Claude Code: Extend Claude with skills](https://code.claude.com/docs/en/slash-commands)
