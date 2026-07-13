# 여러 개발 환경 동기화와 병합

## 목표

집, 회사 또는 별도 PC에서 같은 Agent Ground 구조를 유지하고 원본 중심으로 병합한다.

## 이동 가능한 범위

- 함께 이동: `components/`, `flows/`, `business_agent_design/`, `training/`, `registry/`, `html/`, `scripts/`, `tests/`, `skills/`, 프로젝트 문서
- 이동하지 않음: `.env`, API key, 토큰, 사용자별 절대경로, `.venv`, `__pycache__`, `.pytest_cache`, Langflow 사용자 DB, 임시 Office 파일
- 샘플 설정: 실제 값 대신 `.env.example` 또는 문서의 placeholder 사용

## 권장 Git 흐름

1. 작업 전 `git status --short --branch`와 `git pull --ff-only` 가능 여부를 확인한다.
2. 환경과 기능이 드러나는 짧은 브랜치를 만든다. 예: `home/component-image-encoder`, `office/flow-rag-fix`.
3. 한 브랜치에는 한 기능 또는 한 문서 묶음만 커밋한다.
4. Python, manifest, Flow 생성 스크립트와 직접 관리 문서를 먼저 병합한다.
5. `registry/capabilities.json`, 생성 HTML, 통합 Flow bundle은 기준 원본 병합 뒤 다시 생성한다.
6. 생성 파일 충돌을 손으로 섞지 말고 생성기를 다시 실행해 결과를 비교한다.
7. 테스트와 링크 검증 뒤 기본 브랜치에 병합한다.

## 충돌 우선순위

1. 사용자 승인 상태와 실제 검증 증거
2. 현재 환경에서 실행된 Python/Flow 계약
3. component와 flow manifest
4. 생성 스크립트
5. registry, HTML과 bundle 같은 파생 결과

두 환경에서 같은 컴포넌트를 수정했다면 ID를 새로 만들기 전에 입력·출력 계약을 비교한다. 계약이 같으면 구현을 통합하고 version을 올린다. 계약이 의도적으로 다르면 별도 component ID와 migration 설명을 둔다.

## Skill 설치

저장소의 `skills/` 폴더 전체를 옮긴 뒤 다음 중 하나를 사용한다.

```powershell
# 저장소에서 사용자 Codex Skill 폴더로 복사
powershell -ExecutionPolicy Bypass -File skills/install.ps1

# 별도 목적지 지정
powershell -ExecutionPolicy Bypass -File skills/install.ps1 -Destination D:\agent-skills
```

기존 Skill을 덮어쓸 때만 `-Force`를 명시한다. 설치 후 각 Skill 폴더의 `SKILL.md`와 `agents/openai.yaml`이 함께 있는지 확인한다.
