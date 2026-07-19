# Langflow 통합

Langflow 전용 Skill 사본을 만들지 않습니다. `skills/<id>/SKILL.md`를 정본으로 두고 `scripts/build_exports.py`가 `exports/langflow/<id>/`를 생성합니다.

현재 활성 Skill 31개와 Langflow export 폴더 31개가 일치합니다. 삭제된 14개는 adapter와 Prompt도 제거되어 Registry에 노출되지 않습니다. 전체 목록은 [SKILL_INVENTORY.md](./SKILL_INVENTORY.md)를 확인합니다.

## 필수 보안 경계

- model: 사내에서 운영하는 승인 endpoint만
- MCP·Tool·API: local 또는 사내 endpoint만
- storage·memory·vector DB: 사내 저장소와 보존·삭제 정책 적용
- trace·log·metric: 사내 collector만, prompt·source·PII·credential redaction
- webhook·public Git·public CI·external deploy·external feedback: 금지
- public docs: 내부 proxy/mirror를 통한 read-only ingress만
- adapter와 Flow export에 secret을 넣지 않음

## 적용 모드

| 모드 | 사용 시점 |
| --- | --- |
| `system-prompt` | 항상 적용할 짧은 행동 원칙 |
| `prompt-template` | 외부 동작이 없는 전문 지식·작성 지침 |
| `mcp-tool` | 승인된 local/사내 MCP가 실제 실행을 담당 |
| `run-flow` | 입력, 계획, 승인, 실행, 검증을 단계별로 유지해야 하는 workflow |

Flint는 승인된 local `flint-chart-mcp`가 있을 때만 `mcp-tool`로 실행합니다. Browser testing도 local isolated DevTools MCP와 localhost/승인된 사내 test app에만 연결합니다. Tool이 없으면 Prompt fallback으로 낮추고 실행 성공을 가장하지 않습니다.

`addy-agent-skills`의 22개 Skill은 대부분 `run-flow`가 적합하지만 한 요청에 여러 lifecycle workflow를 동시에 시작하지 않습니다. 가장 구체적인 primary workflow 하나를 선택하고 security, review, test checklist를 필요한 단계에서만 참조합니다.

## Registry loader 조건

운영 Registry에는 다음을 모두 만족하는 항목만 노출합니다.

1. catalog의 `status == active`
2. security profile의 excluded ID가 아님
3. 필수 eval이 있으면 `evaluation.status == passed`
4. adapter의 endpoint와 tool 권한이 사내 allowlist 안에 있음
5. secret과 사용자 데이터가 export에 포함되지 않음

## 생성과 확인

```powershell
python .\agent_skill_hub\scripts\build_exports.py
python .\agent_skill_hub\scripts\build_exports.py --check
```

생성된 `adapter.json`과 Prompt는 직접 수정하지 않습니다. 보안 정책 또는 Skill 원본을 고친 뒤 다시 생성합니다.
