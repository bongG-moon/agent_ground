# Flow 패키지 구조

```text
flows/<flow_id>/
├─ <flow_id>.json
├─ manifest.json
├─ component_refs.json
├─ internal_nodes.json
├─ nodes/
│  └─ <node_id>.py
├─ README.md
├─ CONNECTION_GUIDE.md
├─ samples/
└─ tests/
```

여러 Flow가 함께 import되어야 할 때만 `00_<NAME>_ALL_FLOWS.json`을 추가한다.

## 필수 계약

- `manifest.json`: ID, 한글 이름, 상태, version, 목적, 입력, 출력, 위험, 검증 환경과 문서 경로
- `component_refs.json`: 이 Flow가 재사용하는 실제 Component ID와 정확한 version
- `internal_nodes.json`: 이 Flow에서만 의미가 있는 내부 Python 노드 ID, version, `source_path`
- `nodes/`: Flow 내부 노드의 Standalone 원본. Component registry와 공용 Component 목록에는 넣지 않음
- `README.md`: 무엇을 해결하는지, import 순서, 환경값, 최소 실행, 기대 결과
- `CONNECTION_GUIDE.md`: 실제 포트 단위 From/output/type -> To/input/type 표
- `samples/`: secret과 사내 endpoint가 없는 복사 가능한 입력과 기대 결과
- `tests/`: JSON 구조, component source sync, edge, 대표 계약과 회귀 검사

## 완료 판정

- JSON parse만 통과한 상태를 실행 완료로 표현하지 않는다.
- import 성공, component load, 대표 실행과 외부 시스템 검증을 각각 구분한다.
- 사용자 환경 확인이 남으면 `user_testing`으로 기록한다.
- 사용자 승인과 최종 재검증 뒤에만 `approved`로 변경한다.
