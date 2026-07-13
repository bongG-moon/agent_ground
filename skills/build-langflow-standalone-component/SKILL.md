---
name: build-langflow-standalone-component
description: Agent Ground용 Langflow Python 자산이 독립 기능 Component인지 Flow 내부 노드인지 판정하고, 기능 단위 Component를 한글 Standalone 파일로 설계·구현·문서화·검증한다. 새 커스텀 Python 노드를 추가하거나 기존 Component의 입력, 출력, Tool Mode, 오류 처리, manifest, 사용 설명과 테스트를 수정할 때 사용한다.
---

# Langflow Standalone Component 구현

## 구현 순서

1. 관련 Flow, 기존 유사 컴포넌트, 현재 Langflow/LFX 버전과 실제 import 경로를 확인한다.
2. 아래 **Component 승격 기준**을 먼저 통과하는지 판단한다.
3. 구현 전에 목적 한 문장, 입력 표, 출력 표, 실패 형태와 다음 연결 대상을 확정한다.
4. 승격 대상이면 `components/<component_id>/<component_id>.py` 한 파일만 복사해 등록할 수 있게 구현한다.
5. 같은 폴더의 `manifest.json`과 사용자 설명을 실제 Python 정의에서 생성하거나 동기화한다.
6. 정상, 빈 입력, 잘못된 입력, 외부 연결 실패와 크기 제한을 검증한다.
7. 사용자 실제 환경 확인 전 상태를 `user_testing`으로 유지한다.

입출력 계약을 정할 때 [references/component-contract.md](references/component-contract.md)를 읽는다.

## Component 승격 기준

Flow에 들어간 Python 노드라고 해서 자동으로 Component가 되는 것은 아니다. 다음 조건을 모두 만족할 때만 공용 `components/` 라이브러리로 승격한다.

- Flow 밖에서도 설명 가능한 독립 사용 사례가 있다.
- 입력과 출력이 특정 앞뒤 노드의 내부 envelope에 과도하게 묶이지 않은 안정된 계약이다.
- 입력을 검증하고 의미 있는 업무 결과를 만든다. 단순 필드명 변경, prompt 조립, demo seed, 최종 포장만 하는 단계가 아니다.
- 실패 정책과 보안 경계를 독립적으로 문서화할 수 있다.
- 별도 버전, manifest, 단위 테스트와 사용자 가이드를 유지할 가치가 있다.

위 기준을 통과하지 못하면 `flows/<flow_id>/nodes/<node_id>.py`에 Flow 내부 노드로 둔다. 그 노드도 실행 환경 때문에 Standalone 한 파일이어야 하지만, Component 목록·registry·업무 Agent 추천에는 노출하지 않는다. 기본 Langflow 노드로 충분하다면 새 Python 파일도 만들지 않는다.

## Standalone 경계

- 형제 파일, 저장소 공용 helper, 상대 import에 의존하지 않는다.
- 필요한 작은 helper와 데이터 정규화 로직을 같은 `.py` 파일 안에 둔다.
- `display_name`, `description`, input `display_name/info`, output `display_name`을 한국어로 작성한다.
- 클래스명과 필드 식별자는 안정적인 영문 `snake_case`를 사용한다.
- 비밀값에는 `SecretStrInput`을 사용하고 status, 예외, 출력에 원문을 포함하지 않는다.
- 사용자가 직접 바꾸는 boolean은 `BoolInput` 토글로 노출한다.
- 연결 데이터 입력과 화면 설정 입력을 구분하고, 필수 여부와 기본값을 명시한다.
- Tool로 쓸 컴포넌트는 이름, 사용 조건, 사용하지 않을 조건, 안전한 인자 이름을 설명한다.
- Agent Tool schema에는 `-`, `~`가 있는 내부 노드 ID를 노출하지 않는다.

## 출력과 오류

- 출력은 한 가지 핵심 책임을 갖게 하고 Langflow 타입을 정확히 선언한다.
- DataFrame 전용 조회 컴포넌트는 정상 0건에 빈 DataFrame을 반환하되 인증·접속·형식 오류를 성공으로 숨기지 않는다.
- Data 출력은 `success`, `data`, `errors`, `meta`처럼 문서화된 모양을 유지한다.
- 실패 문구는 사용자가 다음에 확인할 입력이나 환경을 알 수 있게 한국어로 작성한다.
- 토큰, 비밀번호, 전체 사내 endpoint와 민감 문서 내용을 오류에 넣지 않는다.

## 저장소 반영

1. Python 원본을 수정한다.
2. Component 승격 기준을 다시 확인하고, 통과하지 못하면 Flow의 `nodes/`와 `internal_nodes.json`으로 이동한다.
3. `scripts/build_component_manifests.py`가 해당 가족을 올바르게 분류하는지 확인한다.
4. manifest와 README를 재생성한다.
5. `scripts/sync_registry.py`와 `scripts/build_site.py`를 실행한다.
6. 포털 상세 화면에서 목적, 입력, 출력, 코드 보기가 정확한지 확인한다.
7. 관련 Flow의 `component_refs.json` 버전과 Python 원본이 일치하는지 확인한다.

## 검증

```powershell
python -m py_compile components/<component_id>/<component_id>.py
python scripts/build_component_manifests.py
python scripts/sync_registry.py
python scripts/build_site.py
python scripts/validate_project.py
```

해당 컴포넌트의 단위 테스트와 관련 Flow 회귀 테스트도 실행한다. 로컬 Langflow가 이미 실행 중이면 `/api/v1/version`과 `/health`를 읽기 전용으로 확인한 뒤 격리된 테스트를 사용한다. 사용자 요청 없이 라이브 Flow를 import하거나 덮어쓰지 않는다.
