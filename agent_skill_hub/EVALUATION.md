# Skill 배포 전 평가

새 외부 Skill은 바로 `active`로 만들지 않습니다. 먼저 `candidate`로 등록하고 보안 검토와 blind routing eval을 모두 통과시킵니다. 현재 candidate와 suite는 0개입니다.

기존 비승인 candidate는 평가 대기 상태로 남겨두지 않고 파일과 파생 항목을 모두 삭제했습니다. 재반입이 필요하면 [EXCLUDED_SKILLS.md](./EXCLUDED_SKILLS.md)의 조건에 따라 새 candidate로 시작합니다.

## 반입 전 보안 평가

1. source URL, commit, licence, `SKILL.md`, references, scripts, hooks, binaries를 고정합니다.
2. 다음 문자열과 동작을 수동 확인합니다: HTTP client, model API, MCP/REST, telemetry, webhook, browser profile, Git push, CI/deploy, credential, package runtime download.
3. 사내 정보가 request URL, prompt, header, body, log, trace, crash report, feedback, vector store로 나갈 수 있는 경로를 찾습니다.
4. 기존 Skill과 trigger, 쓰기 권한, workflow 순서, output contract가 겹치는지 확인합니다.
5. 외부 전송 경로를 제거할 수 없거나 licence가 불명확하면 반입하지 않습니다.

## Routing eval

각 candidate에 10~20개 JSONL case를 둡니다.

- happy: Skill이 필요한 대표 요청
- negative/no-op: 비슷해 보이지만 선택하면 안 되는 요청
- 최소 5개 no-op case
- 정답과 평가 의도는 모델에 전달하지 않음
- Skill 본문은 500단어 이하를 기본 목표로 하고 상세 내용은 references로 분리

```powershell
python .\agent_skill_hub\scripts\skill_eval.py validate --suite all
python .\agent_skill_hub\scripts\skill_eval.py prepare --suite <suite-id> --output <blind-packet.jsonl>
python .\agent_skill_hub\scripts\skill_eval.py score --suite <suite-id> --results <model-results.jsonl>
```

승인된 사내 Claude endpoint와 ChatGPT Enterprise에서 각각 실행합니다. 외부 model로 평가하지 않습니다.

## 승격 기준

- precision ≥ 0.90
- recall ≥ 0.90
- no-op accuracy ≥ 0.90
- exact match ≥ 0.85
- 보안 검토 통과
- 기존 Skill과 precedence 정의
- source와 파생 package hash 갱신
- Langflow adapter의 endpoint와 권한이 사내 범위임을 확인

모든 조건을 충족한 뒤에만 `status: active`로 바꿉니다.
