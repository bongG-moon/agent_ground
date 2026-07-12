# Cached Named Run Flow Tool 사용 가이드

> 상태: `user_testing`  
> 기준 환경: Langflow `1.8.2`, LFX `0.3.4`  
> 배포 방식: `cached_named_run_flow_tool.py` 파일 하나를 등록하는 Standalone Component

처음 접하는 사용자는 먼저 [`BEGINNER_GUIDE.md`](BEGINNER_GUIDE.md)에서 기본 Run Flow와 이 Component의 차이, 별도로 만든 이유와 실제 실행 순서를 확인합니다.

## 목적

Agent가 같은 Langflow 사용자 영역에 있는 하위 Flow를 Tool로 실행할 때 사용합니다.

일반 Run Flow 설정에 export 당시 Flow ID를 고정하면 다른 환경으로 import할 때 새 DB ID가 발급되어 연결이 깨질 수 있습니다. 이 Component는 고정 ID 대신 정확한 Flow 이름을 저장하고 실행 시 현재 DB ID와 `updated_at`을 다시 조회합니다.

참고 구현은 `metadata_driven_v5/langflow_components/route_flow_v2/01_cached_named_run_flow_tool.py`와 같은 폴더의 연결·Tool 설명 문서를 검토해 Agent Ground용으로 일반화했습니다.

## 권장 연결

```text
Chat Input.message -> Agent.input_value
Cached Named Run Flow Tool.component_as_tool -> Agent.tools
Agent.response -> Chat Output.input_value
```

여러 하위 Flow를 제공하려면 이 Component를 Flow별로 하나씩 배치하고 `대상 Flow 이름`, `도구 이름`, `도구 설명`만 다르게 설정합니다.

## 대상 하위 Flow의 필수 조건

1. 기본 설정에서는 부모 Router와 같은 Langflow 폴더에 있어야 합니다.
2. 설정한 Flow 이름이 선택 범위에서 정확히 일치하고 고유해야 합니다.
3. 사용자 요청을 받는 `Chat Input`이 정확히 하나여야 합니다.
4. Agent Tool로 사용할 terminal output이 정확히 하나여야 합니다.
5. 하위 Flow 자체가 최종 사용자 응답과 오류 메시지를 완결성 있게 만들어야 합니다.

Flow 이름이 바뀌거나 같은 이름이 중복되면 실행을 거절합니다. 운영에서는 이름을 배포 계약으로 관리하고 중복 Flow를 만들지 않습니다.

## 입력

| 입력 | 역할 | 기본값 |
| --- | --- | --- |
| `flow_name_selected` | 실행 시 실제 ID로 다시 해석할 정확한 하위 Flow 이름 | 필수 |
| `flow_id_selected` | runtime이 채우는 숨김 ID. export 값 고정 금지 | 빈 값 |
| `session_id` | 직접 지정하거나 비워서 부모 graph session 상속 | 빈 값 |
| `cache_flow` | 실제 `user_id + flow_id` 기준 graph cache 사용 | `true` |
| `allow_cross_folder` | 다른 폴더까지 조회 범위를 넓힘. 현재 사용자 전체에서 이름이 고유해야 함 | `false` |
| `tool_name` | Agent에 노출할 64자 이하 이름. 첫 글자는 영문·숫자, 이후에는 영문·숫자·밑줄·하이픈만 허용 | 필수 |
| `tool_description` | Agent가 이 Flow를 선택할 조건과 제외 조건 | 필수 |
| `return_direct` | 하위 Flow 결과를 부모 Agent 재작성 없이 반환 | `true` |

## 실제 동작 순서

1. 기본적으로 부모 Router와 같은 폴더에서 `대상 Flow 이름`을 조회합니다.
2. `allow_cross_folder=true`일 때만 현재 사용자의 전체 폴더로 범위를 넓힙니다.
3. 같은 이름이 없거나 둘 이상이면 실행을 거절합니다.
4. 현재 부모 Flow 자신을 대상으로 지정하면 직접 재귀 실행을 차단합니다.
5. 실제 DB ID와 `updated_at`을 runtime 속성에 저장합니다.
6. `cache_flow=true`이면 실제 `user_id + flow_id` key로 graph cache를 조회합니다.
7. `updated_at`이 달라졌으면 오래된 graph cache를 폐기하고 다시 구성합니다.
8. 하위 Flow의 `ChatInput.input_value`를 외부 Tool schema의 고정 `question` 필드로 바꿉니다.
9. Agent가 `question`을 전달하면 현재 그래프의 실제 Chat Input ID로 내부 tweak를 생성합니다.
10. 부모 Flow session을 하위 Flow 실행에 상속합니다.
11. 하위 Flow는 매 요청 새로 실행합니다.

## 캐시 범위

캐시되는 것:

- 하위 Flow graph의 파싱·구성 결과

캐시되지 않는 것:

- 사용자 질문
- DB·API 조회 결과
- pandas 실행 결과
- LLM 응답
- 최종 답변

Langflow `1.8.2`의 shared component cache는 backend process 메모리에 있으며 기본 만료시간은 1시간입니다. 서버를 재시작하면 사라지고 여러 worker가 같은 cache를 공유하지 않습니다. Shared component cache가 없는 환경에서는 캐시 없이 실행을 계속합니다.

Agent Ground 버전은 기반 Component가 microsecond를 제거하던 timestamp 비교를 보강해, 같은 초 안에 Flow를 다시 수정한 경우에도 `updated_at` 차이를 반영합니다.

## Tool schema를 줄이는 이유

기본 Run Flow Tool은 하위 Flow에 있는 여러 편집용 Text Input을 Agent 인자로 노출할 수 있습니다. 이 Component는 내부 node ID를 제거한 필수 `question` 하나만 남겨 prompt·repair 설정과 `ChatInput-...~input_value`가 Agent 요청 schema에 포함되지 않도록 합니다.

내부 prompt는 하위 Flow canvas에서 계속 편집할 수 있지만 Agent가 실행마다 변경할 수 없습니다.

Langflow Tool wrapper를 포함한 실제 호출 모양은 다음과 같습니다. Agent가 채우는 동적 업무 입력은 `question` 하나입니다.

```json
{
  "flow_tweak_data": {
    "question": "현재 등록된 계산 로직 관련 도메인은 어떤 것들이 있어?"
  }
}
```

## 고정 `question`과 현재 Chat Input 매핑

이전 구현은 외부 Tool schema에 `ChatInput-xVKPV~input_value` 같은 내부 이름을 노출했습니다. Gemini 등 일부 provider가 `-`와 `~`를 `_`로 바꾸면 기본 Run Flow가 키를 인식하지 못해 세션은 이어져도 질문만 빈 문자열이 되는 문제가 있었습니다.

현재 구현은 다음 순서로 처리합니다.

```text
Agent Tool 입력: flow_tweak_data.question
→ 현재 하위 Flow 그래프 조회
→ 사용자 입력용 Chat Input이 정확히 하나인지 확인
→ 현재 Chat Input ID 확인
→ 내부적으로 {현재_ID: {input_value: question}} 생성
→ 기본 Run Flow 엔진으로 실행
```

따라서 standalone 재import나 Chat Input 교체로 node ID가 바뀌어도 외부 Tool 인자는 계속 `question`입니다. 세션 이력에서 질문을 추정하는 fallback은 사용하지 않으며, `question`이 비어 있으면 하위 Flow를 실행하기 전에 오류로 중단합니다.

하위 Flow에 Chat Input이 없거나 둘 이상이면 자동 선택하지 않고 실행을 거절합니다. 자세한 배경과 초보자용 예시는 [`BEGINNER_GUIDE.md`](BEGINNER_GUIDE.md)의 질문 전달 설명을 확인합니다.

## `return_direct` 주의사항

`return_direct=true`이면 하위 Flow의 결과를 부모 Agent가 다시 요약하거나 수정하지 않습니다. 응답 속도와 내용 보존에는 유리하지만, 최종 보안 필터·인용·표현 규칙은 하위 Flow 안에서 이미 완료되어야 합니다.

부모 Agent가 반드시 후처리해야 하는 업무에서는 `false`로 변경합니다.

## 재귀와 세션 주의사항

- 현재 부모 Flow 자신을 직접 대상으로 지정하는 것은 코드에서 차단합니다.
- Flow A가 B를 실행하고 B가 다시 A를 실행하는 간접 순환은 배포 검토에서 별도로 차단해야 합니다.
- 명시적 세션 ID는 255자와 제어문자 제한을 적용합니다.
- 다른 대화 상태와 의도적으로 합쳐야 하는 경우가 아니면 세션 ID를 비우고 부모 session 상속을 사용합니다.

## 예시 설정

```text
대상 Flow 이름: enterprise_document_search
도구 이름: run_document_search
도구 설명: 사내 문서의 내용과 근거를 조회할 때만 사용합니다. 데이터 등록이나 외부 발송에는 사용하지 않습니다.
Flow 그래프 캐시: 켜짐
다른 폴더 Flow 허용: 꺼짐
하위 결과 직접 반환: 켜짐
```

## 버전 경계

이 Component는 Langflow `1.8.2`의 내부 `RunFlowBaseComponent`와 shared component cache 계약을 사용합니다. Langflow를 업그레이드할 때는 다음을 다시 확인해야 합니다.

- `get_flow`, `get_graph`, `get_new_fields`, `_build_flow_tweak_data`, `_get_tools`, `_pre_run_setup` method signature
- cache key에 사용자 ID와 실제 Flow ID가 포함되는지
- Chat Input의 type·field name이 `ChatInput.input_value`인지
- Tool output type과 `return_direct` 동작
- 대상 Flow 수정 후 `updated_at` 기반 cache 무효화

## 사용자 확인 항목

- [ ] 정확한 이름의 하위 Flow를 실제 ID로 해석한다.
- [ ] 기본 설정에서 다른 폴더의 동명 Flow를 실행하지 않는다.
- [ ] 같은 범위에 동명 Flow가 둘 이상이면 실행을 거절한다.
- [ ] 현재 부모 Flow 자신을 대상으로 지정하면 실행을 거절한다.
- [ ] 하위 Flow 이름 변경 시 잘못된 Flow를 실행하지 않고 중단된다.
- [ ] Tool schema에 사용자 질문 한 필드만 표시된다.
- [ ] Tool schema의 `flow_tweak_data` 안에는 node ID가 없는 고정 `question`만 있다.
- [ ] 하위 Flow 재import 후에도 현재 Chat Input ID로 질문이 전달된다.
- [ ] 밑줄로 변형된 과거 내부 키를 질문 fallback으로 사용하지 않는다.
- [ ] 부모와 하위 Flow의 session이 이어진다.
- [ ] 첫 실행과 warm 실행 결과가 같고 graph cache만 재사용된다.
- [ ] `return_direct` 설정에 따라 부모 Agent 재작성 여부가 의도와 같다.

사용자 확인이 끝나기 전까지 상태는 `user_testing`입니다.
