---
name: maintain-agent-ground
description: Agent Ground 저장소의 구조, 승인 상태, 기준 원본, 검증 순서와 여러 개발 환경 사이의 병합 규칙을 적용한다. Agent Ground 프로젝트를 다른 PC에서 이어서 개발하거나, 작업 위치를 찾거나, 컴포넌트·Flow·포털 변경 범위를 정하거나, 브랜치와 생성 파일 충돌을 안전하게 정리할 때 사용한다.
---

# Agent Ground 운영

## 시작 절차

1. 현재 위치부터 상위 폴더를 탐색해 `AGENT_GROUND_PROJECT_MASTER_GUIDE.md`, `registry/capabilities.json`, `components/`, `flows/`가 함께 있는 프로젝트 루트를 찾는다.
2. 루트의 `AGENT_GROUND_PROJECT_MASTER_GUIDE.md`, `README.md`, 관련 manifest와 `git status --short --branch`를 읽는다.
3. 기억이나 다른 저장소의 구조보다 현재 체크아웃의 파일과 계약을 우선한다.
4. 작업 종류에 맞는 Skill을 함께 적용한다.
   - Standalone Component: `$build-langflow-standalone-component`
   - Flow JSON과 패키지: `$build-langflow-flow-package`
   - HTML 포털과 교육자료: `$maintain-agent-ground-portal`
5. 사용자가 요청하지 않은 라이브 Langflow import, DB 변경, 배포, 외부 발송은 수행하지 않는다.

## 기준 원본

- Component의 기준 원본은 `components/<id>/<id>.py`와 같은 폴더의 `manifest.json`으로 둔다.
- Flow의 기준 원본은 `flows/<id>/<id>.json`, `manifest.json`, `component_refs.json`, `internal_nodes.json`과 `nodes/`로 둔다.
- `registry/capabilities.json`은 component와 flow manifest에서 생성한다.
- 생성 가능한 HTML과 통합 bundle은 원본을 수정한 뒤 생성 스크립트로 다시 만든다.
- 긴 교육 본문처럼 직접 관리하는 HTML은 생성 파일로 간주하지 말고 내용을 보존한다.
- `business_agent_design/`은 일반 Flow 라이브러리와 분리된 상위 서비스로 유지한다.

## 공통 규칙

- Standalone은 포장 방식이고 Component는 독립 기능 단위다. Flow에 사용된 Python 파일을 자동으로 Component로 분류하지 않는다.
- 독립 사용 사례, 안정된 입출력, 의미 있는 기능과 독립 오류 정책을 가진 자산만 `components/`에 둔다.
- Flow 전용 adapter, prompt 조립, demo seed, 결과 포장 노드는 `flows/<id>/nodes/`와 `internal_nodes.json`에 둔다.
- 모든 Langflow 커스텀 Python 자산은 한 파일로 등록 가능한 Standalone 방식으로 작성한다.
- 사용자-facing 이름, 설명, 코드 주석과 문서를 한국어로 작성한다. Langflow 필드명과 타입명은 실제 영문 식별자를 유지한다.
- 입력·출력 계약을 구현보다 먼저 확정한다.
- 상태를 `idea -> building -> user_testing -> approved` 순서로 관리한다.
- 사용자의 완료 의사와 최종 검증이 모두 있기 전에는 `approved`로 바꾸지 않는다.
- 비밀값, 사내 주소, 사용자 홈 절대경로, 캐시, 가상환경과 임시 파일을 커밋하지 않는다.
- 사용자의 기존 변경을 보존하고 관련 없는 파일을 되돌리지 않는다.
- 실패는 증상, 원인, 수정, 재검증을 담은 troubleshooting 자산으로 남긴다.

## 여러 환경에서 이어서 작업하기

환경 간 병합이나 폴더 이동이 포함되면 [references/cross-environment-workflow.md](references/cross-environment-workflow.md)를 읽는다.

핵심 원칙은 다음과 같다.

1. 저장소 전체를 Git으로 동기화하고, `skills/`도 코드와 함께 버전 관리한다.
2. 환경별 설정은 environment variable 또는 추적하지 않는 `.env`에 두고 `.env.example`에는 키 이름만 둔다.
3. 한 작업 단위마다 별도 브랜치를 사용하고 원본 파일을 먼저 병합한다.
4. registry, 포털, bundle 같은 파생 파일은 원본 병합 뒤 한 환경에서 다시 생성한다.
5. 컴포넌트 ID, Flow ID, manifest 버전을 임의로 바꾸지 않는다.
6. 병합 후 `skills/maintain-agent-ground/scripts/audit_workspace.py`와 프로젝트 검증을 실행한다.

## 완료 전 검증

위험과 변경 범위에 비례해 다음을 실행한다.

```powershell
python skills/maintain-agent-ground/scripts/audit_workspace.py
python scripts/sync_registry.py
python scripts/build_site.py
python scripts/validate_project.py
git diff --check
git status --short --branch
```

생성 스크립트가 별도 `--check` 옵션을 제공하면 생성 후 해당 검사도 실행한다. 실제 Langflow 실행을 주장하려면 확인한 버전, 입력, 결과와 남은 수동 검증을 구분해 기록한다.
