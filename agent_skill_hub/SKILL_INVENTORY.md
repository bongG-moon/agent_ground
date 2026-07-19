# 현재 Skill 목록과 삭제 이력

기준일: 2026-07-19  
보안 프로필: `internal-enterprise`

## 요약

| 구분 | 수량 | 상태 |
| --- | ---: | --- |
| 활성 Skill | 31 | 설치·export 가능 |
| candidate Skill | 0 | 현재 없음 |
| 삭제 Skill | 14 | canonical 폴더와 파생 파일까지 제거 |
| Langflow export 폴더 | 31 | 활성 Skill과 일치 |
| routing eval suite | 0 | 새 candidate 반입 시 생성 |

삭제 Skill은 `EXCLUDED_SKILLS.md`의 이력에만 이름과 사유가 남아 있습니다. 실제 Skill 폴더, catalog entry, source lock entry, eval case, Langflow export, 전용 import script는 존재하지 않습니다.

## 남아 있는 활성 Skill 31개

### 공통·디자인·시각화 9개

| ID | 역할 | 호출 정책 |
| --- | --- | --- |
| `karpathy-guidelines` | 단순하고 검증 가능한 코딩 원칙 | 일반 |
| `flint-chart-author` | 의미 기반 chart spec과 내부 Flint 연동 | 일반 |
| `hallmark` | UI 구축·감사·재설계 | 일반 |
| `animation-vocabulary` | 모션 표현을 정확한 용어로 변환 | 일반 |
| `apple-design` | Apple식 interaction 전문 지침 | 명시 호출 |
| `emil-design-eng` | UI polish와 design engineering | 명시 호출 |
| `find-animation-opportunities` | motion 기회를 read-only로 탐색 | 명시 호출 |
| `improve-animations` | motion 감사와 개선 계획 | 명시 호출 |
| `review-animations` | animation code 전문 review | 명시 호출 |

### 요구사항·계획 5개

| ID | 역할 |
| --- | --- |
| `interview-me` | 모호한 요구사항을 질문으로 구체화 |
| `idea-refine` | 아이디어 발산·수렴 |
| `spec-driven-development` | 구현 전 명세 작성 |
| `planning-and-task-breakdown` | 작업 순서와 단위 분해 |
| `api-and-interface-design` | API·module·interface contract 설계 |

### 구현 5개

| ID | 역할 |
| --- | --- |
| `source-driven-development` | 승인된 공식 문서를 근거로 구현 |
| `test-driven-development` | 테스트 우선 구현 |
| `incremental-implementation` | 작은 단위 구현과 단계별 검증 |
| `frontend-ui-engineering` | 접근성·반응형 UI 구현 |
| `code-simplification` | 동작을 유지하는 코드 단순화 |

### 검증·리뷰·보안 6개

| ID | 역할 |
| --- | --- |
| `debugging-and-error-recovery` | 체계적인 원인 분석과 복구 |
| `browser-testing-with-devtools` | 격리된 local browser 검증 |
| `code-review-and-quality` | 다각도 code review |
| `doubt-driven-development` | 승인된 내부 fresh-context 반대 검토 |
| `performance-optimization` | 측정 기반 성능 최적화 |
| `security-and-hardening` | 입력·인증·저장·의존성 보안 |

### 운영·문서·context 6개

| ID | 역할 |
| --- | --- |
| `git-workflow-and-versioning` | 승인된 사내 Git과 version 관리 |
| `shipping-and-launch` | 사내 배포 준비·rollout·rollback |
| `observability-and-instrumentation` | 사내 logging·metric·trace |
| `deprecation-and-migration` | 폐기·migration·호환성 계획 |
| `documentation-and-adrs` | 문서와 ADR 작성 |
| `context-engineering` | Agent context와 project rule 관리 |

## 삭제된 Skill 14개

| 구분 | ID |
| --- | --- |
| UI 후보 | `ui-ux-pro-max`, `ui-skills-root`, `baseline-ui`, `fixing-accessibility`, `fixing-metadata`, `fixing-motion-performance`, `improve-ui` |
| Agent·개발 후보 | `superpowers`, `understand-anything`, `agentmemory`, `agent-skill-creator`, `remotion-best-practices` |
| Addy 충돌 항목 | `ci-cd-and-automation`, `using-agent-skills` |

상세 사유와 재반입 조건은 [EXCLUDED_SKILLS.md](./EXCLUDED_SKILLS.md)를 확인합니다.

## 선택 우선순위

1. `SECURITY_PROFILE.md`와 사내 규정
2. 사용자의 현재 요청과 read-only 범위
3. 프로젝트 `AGENTS.md`, `CLAUDE.md`, design system
4. 명시 호출한 specialist
5. 일반 Skill guidance

한 작업에는 primary workflow 하나만 사용합니다. 나머지 Skill은 필요한 review·security·style checklist만 보조합니다.
