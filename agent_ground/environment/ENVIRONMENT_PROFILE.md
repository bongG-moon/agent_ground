# Agent Builder 환경 프로필

## 기본 대상 환경

| 항목 | 값 | 확인 상태 |
| --- | --- | --- |
| Langflow | `1.9.2` | 프로젝트 고정 기준 |
| langflow-base | `0.9.2` | Langflow 1.9.2 의존 계약 확인 |
| LFX | `0.4.2` | 실제 package metadata와 Component index 확인 |
| Python | `3.12.13` | 프로젝트 격리 환경에서 확인 |
| Flow export `last_tested_version` | `1.9.2` | 전체 생성·이관 대상 |
| 화면 데이터 타입 | `JSON`, `Table` | 1.9 UI 이름. Python 호환 클래스는 `Data`, `DataFrame`일 수 있음 |
| Custom Component import 계열 | `lfx.custom`, `lfx.io`, `lfx.schema` | Standalone 원본과 LFX 0.4.2 loader로 확인 |
| Component 배포 방식 | Standalone 단일 파일 | 프로젝트 필수 조건 |
| Custom Component 실행 정책 | `LANGFLOW_ALLOW_CUSTOM_COMPONENTS` 기본 `true` | 운영 차단 시 승인된 `LANGFLOW_COMPONENTS_PATH` 배포 필요 |

## 현재 검증 범위

- 전체 Standalone Python 자산은 LFX 0.4.2 loader와 `create_component_template`로 평가한다.
- Flow 기본 노드는 Langflow 1.9.2 starter 또는 LFX 0.4.2 Component index에서 가져온다.
- 개별 Flow JSON과 통합 Bundle은 1.9.2 Graph parser로 해석한다.
- 특정 LLM 공급자와 API Key는 export에 고정하지 않는다. 1.9.2의 통합 모델 선택기에서 조직 승인 모델을 사용한다.
- 사용자 Desktop에 다른 버전이 설치되어 있더라도 대상 생성·검증에는 사용하지 않는다.
- Office/PDF 로컬 추출 회귀 테스트는 `environment/langflow-1.9.2-validation-requirements.txt`의 고정 버전을 격리 환경에 추가한 뒤 실행한다.

## 사용자 환경에서 남은 확인

1. Custom Component를 UI에서 영구 등록할 때의 조직 표준 경로와 custom component 허용 정책
2. Oracle, MongoDB, HTTP, Report API 관련 패키지와 사내 network 정책
3. 기존 Flow의 실제 datasource·LLM·Report API 실행
4. 운영 RAG용 identity, DLP, persistent vector-store adapter
5. 전체 Bundle의 Langflow 1.9.2 UI 일괄 Import
6. Skill 기반 Agent에서 승인된 Tool Calling 모델별 Skill 선택 정확도와 무관 요청 처리
7. 동일 프로젝트의 `meeting_action_skill_flow` 이름 탐색, 질문 전달, cold/warm cache와 session 상속

## 비밀값 원칙

- API Key, Token, Mongo URI, Oracle TNS는 코드와 Flow JSON에 실제 값을 저장하지 않습니다.
- 샘플에는 설명용 가짜 주소와 값만 사용합니다.
- 실제 값은 Agent Builder의 보안 입력, Global Variable 또는 서버 환경 설정을 사용합니다.
- command line에 비밀값을 넣지 않고 Global Variables 또는 사내 secret manager로 주입합니다.
