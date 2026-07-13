---
name: build-langflow-flow-package
description: Agent Ground에서 가져오기 가능한 Langflow Flow JSON, 재사용 Component 참조, Flow 내부 Standalone 노드, 연결 가이드, 샘플, 통합 bundle과 검증 자료를 일관되게 만든다. 새 Flow를 구현하거나 기존 Flow를 수정하거나, Run Flow Tool, 하위 Flow, Flow JSON import 오류와 Component·내부 노드 연결 계약을 다룰 때 사용한다.
---

# Langflow Flow 패키지 구현

## 시작 절차

1. 현재 Langflow/LFX 버전과 기존 Flow JSON의 실제 노드 schema를 확인한다.
2. `flows/<flow_id>/manifest.json`, `component_refs.json`, `internal_nodes.json`, 관련 컴포넌트 manifest를 먼저 읽는다.
3. 입력, 최종 출력, 분기, 부작용, 사람 승인 지점을 구현 전에 명시한다.
4. 커스텀 로직은 기능 경계에 따라 공용 `components/` 또는 `flows/<flow_id>/nodes/`로 분리하고 Flow JSON 안의 코드와 원본을 동기화한다.
5. 가져오기 가능한 개별 JSON과 필요한 경우 여러 Flow 통합 bundle을 생성한다.

필수 파일과 계약은 [references/package-layout.md](references/package-layout.md)를 읽는다. Run Flow 또는 Agent Tool을 사용하면 [references/run-flow-contract.md](references/run-flow-contract.md)도 읽는다.

## 설계 원칙

- 연결표의 From 노드/output/type과 To 노드/input/type을 실제 edge와 일치시킨다.
- Flow에 쓰인다는 이유만으로 Component로 등록하지 않는다. 독립 사용 사례, 안정된 계약, 의미 있는 기능, 독립 오류 정책과 별도 유지 가치가 모두 있어야 Component다.
- 공용 Component는 `component_refs.json`, Flow에 종속된 내부 Python 노드는 `internal_nodes.json`과 `nodes/`로 구분한다.
- 실제 분기와 병합을 구조도에 표시하고 단순 나열로 대체하지 않는다.
- 외부 쓰기, 승인, 발송과 삭제는 명시적 확인 단계 뒤에 둔다.
- 비밀값과 사내 endpoint를 JSON 기본값이나 샘플에 넣지 않는다.
- 외부 시스템이 없을 때의 실패 안내 또는 명시적 demo mode를 제공한다.
- 사용자 완료 승인 전 manifest 상태를 `user_testing`으로 유지한다.

## Run Flow Tool 안전 계약

- LLM에 `question`처럼 provider-safe한 고정 인자만 노출한다.
- `ChatInput-xVKPV~input_value` 같은 내부 노드 ID를 Tool parameter 이름으로 노출하지 않는다.
- 실행 직전에 현재 하위 Flow graph에서 Chat Input을 찾고 내부 tweak key를 만든다.
- Chat Input이 없거나 여러 개면 추측하지 말고 명확히 실패한다.
- 세션 상속과 질문 전달을 별개 계약으로 검증한다.
- Flow 이름 조회 결과가 없거나 중복이면 ID를 임의 선택하지 않는다.
- graph만 캐시하고 질문, 결과, 사용자 데이터와 인증값은 캐시하지 않는다.

## 패키지 반영

1. 생성 스크립트가 있으면 JSON을 수동 편집하기보다 스크립트 원본을 수정한다.
2. 개별 Flow JSON, `component_refs.json`, `internal_nodes.json`, manifest, README, `CONNECTION_GUIDE.md`, samples와 tests를 갱신한다.
3. 통합 bundle을 재생성하고 개별 Flow 수와 ID 중복을 검사한다.
4. registry와 포털을 재생성한다.
5. JSON parse, Python compile, 계약 테스트와 대표 입력을 검증한다.
6. 실제 Builder import/실행 여부와 정적 검증 범위를 분리해 보고한다.

## 검증 예시

```powershell
python scripts/build_<flow_id>.py
python -m pytest flows/<flow_id>/tests -q
python scripts/sync_registry.py
python scripts/build_site.py
python scripts/validate_project.py
git diff --check
```

실제 Langflow가 실행 중이면 버전과 health를 먼저 확인한다. 라이브 DB나 사용자의 기존 Flow에는 명시적 요청 없이 import하지 않는다.
