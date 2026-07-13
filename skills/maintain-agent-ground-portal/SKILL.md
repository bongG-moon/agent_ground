---
name: maintain-agent-ground-portal
description: Agent Ground의 registry 기반 정적 HTML 포털, 컴포넌트·Flow 설명서, 처음 배우기 교육자료와 문제 해결 화면을 서비스 수준으로 생성하고 검증한다. 포털 목록이나 상세 페이지, 입출력 계약, Python 코드 보기, 반응형 레이아웃, 표 overflow, 링크와 내비게이션을 수정할 때 사용한다.
---

# Agent Ground 포털 유지보수

## 시작 절차

1. `scripts/build_site.py`, `registry/capabilities.json`, 대상 manifest와 현재 HTML을 함께 확인한다.
2. 생성 페이지인지 직접 관리하는 교육 본문인지 구분한다.
3. 생성 페이지는 생성기를 먼저 수정하고 HTML을 재생성한다.
4. 기존 교육 내용은 삭제하거나 요약하지 말고 디자인과 배치만 개선한다.
5. 정적 `file://`로 열어도 핵심 탐색, 계약, 코드 보기가 동작하게 만든다.

상세 UI 기준은 [references/portal-ui-checklist.md](references/portal-ui-checklist.md)를 읽는다.

## 컴포넌트 정보 구조

- 첫 화면에 `무엇을 하는가`, `입력`, `출력`을 짧고 명확하게 표시한다.
- 입력에는 화면 이름, 식별자, Langflow 타입, 필수 여부, 기본값과 설명을 표시한다.
- 출력에는 화면 이름, 식별자, 타입, method와 다음 연결 대상을 표시한다.
- `입출력 계약 보기`는 README 경로나 로컬 파일 경로가 아니라 같은 페이지의 계약 section anchor로 이동시킨다.
- `Python 파일 열기`는 raw 파일을 열지 말고 HTML escaped 원문을 다크 코드 뷰어로 제공한다.
- 코드 뷰어에 줄 번호, 복사, 가로 스크롤, 키보드 포커스와 닫기 또는 돌아가기를 제공한다.

## 목록 분류

- 사용자가 직접 가져다 쓰는 핵심 공용 컴포넌트를 먼저 보여준다.
- Component Library에는 독립적인 기능 단위만 보여준다.
- 특정 Flow 내부 노드는 해당 Flow 상세 화면의 `내부 실행 노드`로 분리하고 Component 수, registry와 업무 Agent 추천 수에 포함하지 않는다.
- Standalone 여부는 코드 포장 정보로만 표시하며 Component 자격의 근거로 사용하지 않는다.
- 자산을 삭제하기 전에 `component_refs.json`, Flow JSON과 `used_by_flows`를 모두 확인한다.
- 승인되지 않은 자산은 상태를 그대로 표시하고 승인 완료처럼 표현하지 않는다.

## 반응형과 교육자료

- 교육 단계 카드는 데스크톱에서 문장이 과도하게 줄바꿈되지 않을 폭을 확보하고 모바일에서는 한 열로 전환한다.
- 넓은 표는 페이지 전체를 밀어내지 않게 wrapper 내부에서 가로 스크롤하고 header를 식별 가능하게 유지한다.
- 코드, 긴 URL, JSON과 표에 `min-width: 0`, overflow와 word-break 정책을 적용한다.
- 360px, 768px, 1280px 기준으로 내비게이션, 버튼, 표, 코드, focus 상태를 확인한다.
- 모든 하위 페이지에 홈, 이전, 상위 목록으로 가는 경로를 제공한다.

## 검증

```powershell
python scripts/sync_registry.py
python scripts/build_site.py
python scripts/validate_project.py
git diff --check
```

생성 후 이상한 절대경로 링크, 존재하지 않는 anchor, raw `.py` 직접 링크, HTML escaping 누락, 가로 overflow와 모바일 겹침을 검사한다. 가능하면 실제 브라우저의 데스크톱과 모바일 viewport에서 시각 확인한다.
