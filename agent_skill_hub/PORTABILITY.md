# Portability

모든 환경은 `skills/<id>/SKILL.md`를 정본으로 사용합니다. 설치기가 복사한 `SECURITY_POLICY.md`와 `SKILL.md`의 enterprise preamble이 원본 workflow보다 우선합니다.

현재 배포 가능한 Skill은 31개이며 candidate는 없습니다. 전체 목록은 [SKILL_INVENTORY.md](./SKILL_INVENTORY.md), 삭제 이력은 [EXCLUDED_SKILLS.md](./EXCLUDED_SKILLS.md)를 확인합니다.

| 환경 | 배포 방식 | 허용 범위 |
| --- | --- | --- |
| Claude Code CLI | 프로젝트 `.claude/skills` 또는 사용자 `%USERPROFILE%\.claude\skills` | 회사 운영 model endpoint, local·사내 tool |
| Codex App | 프로젝트 `.agents/skills` 또는 사용자 `%USERPROFILE%\.agents\skills` | local workspace와 승인된 사내 endpoint |
| ChatGPT Enterprise | 조직이 허용한 Skill/Project 업로드 또는 `exports/chatgpt` | 승인된 Enterprise workspace 안에서만 사용 |
| 기타 CLI | `-Target Custom -Destination <local-path>` | native Skill 형식과 사내 network 정책을 모두 지원할 때만 |
| Langflow | `exports/langflow` adapter와 Prompt | 사내 model, storage, trace, MCP, API endpoint만 |

## 기능별 fallback

| Skill·기능 | 필요한 능력 | 능력이 없을 때 |
| --- | --- | --- |
| Flint MCP | 내부 mirror에서 설치한 local `flint-chart-mcp` | chart spec만 작성하고 실행 가능하다고 가장하지 않음 |
| Browser testing | local Chrome DevTools MCP, `--isolated`, localhost/사내 test app | 정적 코드 리뷰와 수동 test plan 제공 |
| Source-driven development | 승인된 read-only docs proxy/mirror | 로컬 문서만 사용하고 미검증 부분 표시 |
| Observability | 사내 collector·storage·dashboard | local structured log와 계측 계획까지만 작성 |
| Shipping/Git | 사내 Git·CI·artifact·deploy target | 로컬 변경과 계획에서 멈추고 push/deploy하지 않음 |
| Hallmark assets | 사용자 제공 또는 승인된 local/DAM asset | typography, CSS, SVG, 명시적 placeholder 사용 |

## 충돌 우선순위

1. `SECURITY_PROFILE.md`와 조직 정책
2. 사용자의 현재 요청과 read-only/승인 경계
3. 프로젝트의 `AGENTS.md`·`CLAUDE.md`·design system
4. 명시적으로 선택한 specialist Skill
5. 일반 Skill guidance

한 작업에 planning·TDD·incremental·review workflow를 모두 자동 중첩하지 않습니다. 가장 구체적인 primary workflow 하나를 고르고, 다른 Skill은 필요한 체크리스트만 참조합니다. Animation audit Skill은 수정 권한을 주지 않으며, 구현 Skill보다 read-only 계약이 우선합니다.
