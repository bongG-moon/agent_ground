# Agent Ground 폴더 가이드

이 문서는 `agent_ground/` 안의 각 폴더가 왜 존재하는지와 어디를 수정해야 하는지를 설명합니다.

## 핵심 자산

| 폴더 | 무엇이 들어 있는가 | 언제 수정하는가 |
| --- | --- | --- |
| `components/` | Flow와 무관하게 재사용할 수 있는 기능 단위 Standalone Component, 입출력 계약, manifest와 사용법 | 독립적인 기능을 추가하거나 기존 Component 계약을 바꿀 때 |
| `flows/` | Langflow Import JSON, Flow별 내부 Node, 연결 가이드, 샘플과 테스트 | 여러 Node를 연결한 업무 흐름을 만들거나 수정할 때 |
| `business_agent_design/` | 업무 설명을 BEFORE/AFTER Flow Chart와 Agent 설계안으로 바꾸는 별도 상위 기능 | 업무 Agent 설계 기능과 전용 Node를 수정할 때 |
| `training/` | 초보자 교육 원본, 이전 자료 이관 기준과 1.9.2 참고문서 | 교육 내용을 추가하거나 수정할 때 |
| `html/` | 브라우저에서 보는 통합 포털과 생성된 Component·Flow 설명 페이지 | 디자인 시스템이나 최종 안내 페이지를 확인할 때 |

## 운영·개발 지원

| 폴더 | 무엇이 들어 있는가 | 주의사항 |
| --- | --- | --- |
| `registry/` | 공개 자산의 상태, 경로와 분류를 모은 기준 데이터 | 수작업 수정 후 `scripts/sync_registry.py` 결과와 일치하는지 확인합니다. |
| `scripts/` | Flow JSON 생성, 1.9.2 template 변환, manifest·registry·HTML 생성과 전체 검증 자동화 | Langflow Builder에서 실행하는 Component가 아닙니다. 개발자가 반복 작업을 안전하게 수행하기 위한 도구입니다. |
| `skills/` | 다른 PC의 Codex가 같은 규칙으로 개발하도록 하는 이식 가능한 `SKILL.md` 묶음 | Agent Ground 작업 규칙을 바꾸면 Master Guide와 함께 수정합니다. |
| `environment/` | 기준 버전, 선택 의존성, 사내 환경에서 필요한 설정 설명 | 기본 기준은 Langflow 1.9.2, langflow-base 0.9.2, LFX 0.4.2, Python 3.12입니다. |
| `tests/` | 여러 Component에 공통으로 적용되는 회귀 테스트 | 개별 자산 테스트는 해당 Component나 Flow의 `tests/`에 둡니다. |
| `archive/` | 이관 중 임시 snapshot을 둘 자리 | 현재 운영 자산을 넣지 않으며 비어 있는 구조만 유지합니다. |

## 루트 문서

| 파일 | 용도 |
| --- | --- |
| `README.md` | 현재 구현 범위와 주요 진입점 |
| `AGENT_GROUND_PROJECT_MASTER_GUIDE.md` | 설계 원칙, 승인 절차와 필수 구현 지침 |
| `VALIDATION_REPORT.md` | 마지막 자동 검증 결과와 사용자 환경 확인이 남은 항목 |
| `CHANGELOG.md` | 날짜별 주요 변경 기록 |

## 어디에 새 파일을 둘지 판단하는 기준

1. 한 파일로 등록되고 다른 Flow에서도 독립적으로 쓸 기능이면 `components/<component_id>/`에 둡니다.
2. 특정 Flow 안에서만 의미가 있는 변환·프롬프트·포장 단계면 `flows/<flow_id>/nodes/`에 둡니다.
3. Builder에 Import할 전체 연결 구조면 `flows/<flow_id>/<flow_id>.json`에 둡니다.
4. 사용법과 실패 해결 내용은 원본 문서와 `html/` 포털 양쪽에서 접근할 수 있게 합니다.
5. 생성·동기화·검증을 반복하는 개발 도구만 `scripts/`에 둡니다.

`Standalone`은 배포 형식이고 `Component`는 기능 분류입니다. Flow에 Python Node가 사용된다는 이유만으로 모두 공용 Component로 올리지 않습니다.
