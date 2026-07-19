# 제외된 Skill

기준일: 2026-07-19  
적용 정책: `internal-enterprise`

아래 14개 Skill은 catalog, source lock, eval, Langflow export, 전용 import script, canonical Skill 폴더에서 실제로 제거했습니다. 현재 Hub에는 실행·설치 가능한 복사본이 없고, 이 문서에 이름과 사유만 남아 있습니다. 이유는 사내 정보의 외부 전송 가능성, 공개 서비스 의존, 미완료 보안 평가, 또는 활성 Skill과의 명확한 workflow 충돌입니다.

| 제외 Skill | 원본 | 주된 이유 |
| --- | --- | --- |
| `ui-ux-pro-max` | `nextlevelbuilder/ui-ux-pro-max-skill` | 공개 패키지·CDN·배포 예시가 많고 Hallmark·frontend·motion Skill과 trigger가 크게 중복 |
| `ui-skills-root`, `baseline-ui`, `fixing-accessibility`, `fixing-metadata`, `fixing-motion-performance`, `improve-ui` | `ibelick/ui-skills` | specialist 간 중복과 routing 충돌, 사내 모델에서 blind eval 미완료 |
| `superpowers` | `obra/superpowers` @ `d884ae04edebef577e82ff7c4e143debd0bbec99` | Addy workflow 묶음과 거의 같은 planning·TDD·debug·review orchestration을 중복 적용 |
| `understand-anything` | `Lum1104/Understand-Anything` @ `5c3bc1b7fdefd17b19b44420e89d279ded21dce8` | 대형 plugin·dashboard·Figma API·package 실행 표면, 사내 코드 전체 색인 위험 |
| `agentmemory` | `rohitg00/agentmemory` @ `93ae9bc04f3ab5042f982aaadf11f1e3f5137531` | MCP/REST/hook 기반 지속 저장이 데이터 보존·삭제·반출 정책과 별도 승인 필요 |
| `agent-skill-creator` | `anthropics/skills` @ `fa0fa64bdc967915dc8399e803be67759e1e62b8` | 외부 eval 경로와 원격 font가 포함된 생성물, 현재 Hub 저작 절차와 기능 중복 |
| `remotion-best-practices` | `remotion-dev/skills` @ `ad2321d187d2b15f65cdbfb9cb9f9a57e1a75e8c` | 고정 원본에 명확한 licence 파일이 없고 외부 media·service workflow가 광범위 |
| `ci-cd-and-automation` | `addyosmani/agent-skills` @ `5a1b82d6445d1e2f0abeea1072851419a50c0e5c` | GitHub Actions, Vercel 등 public CI/deploy 예시가 중심이라 사내 pipeline 계약과 맞지 않음 |
| `using-agent-skills` | `addyosmani/agent-skills` @ `5a1b82d6445d1e2f0abeea1072851419a50c0e5c` | Codex·Claude의 native routing 및 Hub catalog와 중복되는 meta-router라 이중 선택 위험 |

## 다시 들이는 조건

제외 파일은 현재 Hub 안에서 복구할 수 없습니다. 필요하면 위 원본을 새 폴더에 다시 받아 다음 조건을 모두 충족한 뒤 별도 candidate로 반입합니다.

1. 사내 보안 담당자가 source, licence, dependency, executable support files를 검토
2. 외부 model, MCP, telemetry, webhook, Git/CI/deploy, browser profile, memory persistence 경로 제거 또는 사내 endpoint로 고정
3. 기존 Skill과의 trigger 및 workflow precedence 문서화
4. 10~20개의 happy/negative prompt로 승인된 사내 Claude와 ChatGPT Enterprise에서 blind routing eval 수행
5. source와 파생본 hash를 각각 기록하고 Langflow adapter는 eval 통과 전 비활성 유지

현재 운영에서는 이 목록을 설치하거나 자동 복원하지 않습니다.
