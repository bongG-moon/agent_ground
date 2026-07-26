# Langflow 1.9.2 이관 가이드

> Agent Ground 기본 환경: Langflow `1.9.2`, langflow-base `0.9.2`, LFX `0.4.2`, Python `3.12`

## 무엇이 달라졌나

Agent Ground의 기존 자산은 Langflow 1.8.2에서 만들어졌습니다. 현재는 단순 버전 문자열 교체가 아니라 다음 항목을 1.9.2 실제 패키지 기준으로 다시 만들었습니다.

1. 기본 노드는 Langflow 1.9.2 starter 또는 LFX 0.4.2 Component index에서 가져옵니다.
2. Standalone Python은 LFX 0.4.2 loader로 평가하고 `create_component_template({"code": code, "output_types": []})`로 입력·출력과 metadata를 다시 만듭니다.
3. 모든 연결선은 새 template의 실제 출력·입력 타입으로 handle을 다시 만듭니다.
4. 개별 Flow와 통합 Bundle을 Langflow 1.9.2 Graph parser로 읽습니다.
5. Flow와 manifest의 기준 버전을 1.9.2로 통일합니다.

## 화면 타입 이름

Langflow 1.9 화면에서는 이전의 `Data`와 `DataFrame`이 주로 `JSON`과 `Table`로 표시됩니다.

| 1.9.2 화면 | Standalone Python에서 볼 수 있는 이름 | 의미 |
| --- | --- | --- |
| `JSON` | `Data` | key/value 구조화 데이터 |
| `Table` | `DataFrame` | 행과 열이 있는 표 데이터 |
| `Message` | `Message` | 대화 입력과 출력 |

화면 이름이 바뀌었다고 Python 클래스까지 모두 바꾸면 기존 코드와 호환되지 않을 수 있습니다. 문서에서는 화면 타입과 Python 내부 타입을 구분합니다.

## 모델과 MCP

- 1.9.2의 Language Model은 통합 모델 선택기를 사용합니다. 배포 JSON에는 특정 공급자나 API Key를 미리 선택하지 않고 조직 승인 모델을 화면에서 연결합니다.
- MCP 서버는 Settings의 MCP Servers에서 등록하고 MCP sidebar에서 Tool을 가져옵니다. 예전 Agent Component 목록에서 서버를 직접 찾는 방식으로 안내하지 않습니다.

## Custom Component 운영 설정

`LANGFLOW_ALLOW_CUSTOM_COMPONENTS`의 기본값은 `true`입니다. 운영 서버가 이를 `false`로 설정하면 사용자가 임의 코드를 만들거나 편집하지 못할 수 있습니다. 승인된 Component를 서버의 `LANGFLOW_COMPONENTS_PATH`로 배포하는 방식과 조직 승인 절차를 먼저 확인합니다.

## 가져오기 전 확인

1. Builder 버전이 Langflow 1.9.2인지 확인합니다.
2. 필요한 외부 Python 패키지가 설치되어 있는지 확인합니다.
3. Flow JSON에 API Key, 토큰, 사내 endpoint와 사용자 절대경로가 없는지 확인합니다.
4. 통합 Bundle이 필요한 Flow는 하위 Flow와 함께 가져옵니다.
5. Language Model과 MCP는 조직 승인 설정을 화면에서 연결합니다.
6. 가장 작은 샘플 입력으로 각 노드의 화면 타입과 설명서가 같은지 확인합니다.

## 검증 상태를 읽는 법

- `template loader 통과`: Standalone 코드와 UI schema를 LFX 0.4.2가 읽음
- `Graph parse 통과`: 1.9.2 backend가 노드와 연결선을 해석함
- `user_testing`: 사용자 Builder의 실제 모델·계정·사내 endpoint 실행이 남음
- `approved`: 사용자 완료 승인과 최종 재검증까지 끝남

Graph parse가 통과했다고 외부 시스템 호출까지 성공했다는 뜻은 아닙니다. Oracle, EWS, DRM, GooDocs, Datalake, 사내 LLM과 MCP는 각 사용자 환경에서 별도로 확인합니다.

## 다음 버전으로 올릴 때

버전 변경은 `README.md`, Master Guide, `environment/`, 네 개의 `skills/`, Flow 생성기, manifest, 교육자료와 회귀 테스트를 한 작업에서 함께 수정합니다. 이전 버전의 starter ID나 template을 복사하지 말고 새 버전의 실제 패키지에서 다시 생성합니다.
