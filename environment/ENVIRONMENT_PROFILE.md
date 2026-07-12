# Agent Builder 환경 프로필

## 현재 확인된 정보

| 항목 | 값 | 확인 상태 |
| --- | --- | --- |
| Flow export `last_tested_version` | `1.8.2` | 기존 Flow와 신규 RAG export에서 확인 |
| 실제 사용자 서버 버전 | Langflow `1.8.2` | `/api/v1/version`과 실제 Build로 확인 |
| 실제 Component runtime | LFX `0.3.4`, Python `3.12.13` | Desktop venv에서 확인 |
| Custom Component import 계열 | `lfx.custom`, `lfx.io`, `lfx.schema` | 전체 Standalone Python 원본과 runtime template 생성으로 확인 |
| Component 배포 방식 | Standalone 단일 파일 | 프로젝트 필수 조건 |
| Custom Component 등록 경로 | 미확인 | 사용자 환경에서 재확인 필요 |
| 사용 가능한 외부 패키지 | 자산별 manifest 참고 | 실제 서버 설치 여부 재확인 필요 |
| 신규 RAG 실제 Build | 13 nodes / 10 edges, 실행 node 11/11 valid | 임시 Upload·Full Build·Chat Output 후 삭제 |
| Skill 기반 Agent 예시 | 상위 9 nodes / 8 edges, 회의 하위 5 nodes / 3 edges, 직접 계산 Tool 2개 + Run Flow Tool 1개 | JSON·Tool 계약 확인 후 동일 프로젝트 하위 Flow와 사용자 모델 E2E 필요 |

## 해석

현재 local Agent Builder는 Langflow `1.8.2`로 확인했고 신규 `enterprise_document_rag_flow`는 실제 Upload와 Full Build까지 통과했습니다. Skill 기반 Agent 예시는 특정 공급자와 API Key를 고정하지 않고 경비·휴가의 직접 계산 Tool과 회의 전용 이름 기반 Run Flow Tool을 한 Agent에 연결합니다. 실제 LLM의 Skill 선택, 같은 프로젝트의 `meeting_action_skill_flow` 탐색, cold/warm 실행과 session 상속은 사용자 승인 모델을 연결한 뒤 확인해야 합니다. 기존 이관 Flow와 외부 시스템 의존 Component도 별도 실데이터 검증이 남아 있습니다.

1. Custom Component를 UI에서 영구 등록할 때의 조직 표준 경로
2. Oracle, MongoDB, HTTP, Report API 관련 패키지와 사내 network 정책
3. 기존 두 Flow의 실제 datasource·LLM·Report API 실행
4. 운영 RAG용 identity, DLP, persistent vector-store adapter
5. 전체 Bundle 6개 Flow의 UI 일괄 Import
6. Skill 기반 Agent에서 승인된 Tool Calling 모델별 Skill 선택 정확도와 무관 요청 처리
7. 동일 프로젝트의 `meeting_action_skill_flow` 이름 탐색, 질문 전달, cold/warm cache와 session 상속

## 비밀값 원칙

- API Key, Token, Mongo URI, Oracle TNS는 코드와 Flow JSON에 실제 값을 저장하지 않습니다.
- 샘플에는 설명용 가짜 주소와 값만 사용합니다.
- 실제 값은 Agent Builder의 보안 입력, Global Variable 또는 서버 환경 설정을 사용합니다.
- Desktop 프로세스 시작 정보에 LLM API key 환경 변수가 포함된 정황을 확인했습니다. 값은 기록하지 않았으며, 해당 key를 회전하고 command line이 아닌 Global Variables 또는 사내 secret manager 주입으로 전환해야 합니다.
