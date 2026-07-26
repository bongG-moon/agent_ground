# Agent Tool과 하위 Flow 계약

Agent Ground의 현재 기본 선택은 **독립 Standalone Component Tool 직접 연결**입니다. 과거 커스텀 이름 기반 Run Flow Tool은 실제 환경 오류가 남아 있어 공개 Component와 예시 Flow에서 제거했습니다.

## 현재 권장 순서

1. 하나의 기능이면 Standalone Component를 Tool Mode로 직접 연결한다.
2. 고정된 여러 단계면 검증된 Workflow 안에서 순서를 강제한다.
3. 조직 공통 기능이면 승인된 MCP Tool 경계를 검토한다.
4. 하위 Flow 실행이 꼭 필요한 경우에만 Langflow 1.9.2 기본 기능을 실제 Builder에서 별도 검증한다.

## 외부 schema

LLM에는 provider가 변형하지 않을 고정 이름을 노출한다.

```json
{
  "question": "사용자의 실제 질문"
}
```

`ChatInput-xVKPV~input_value` 같은 Langflow 내부 key를 외부 Tool parameter로 쓰지 않는다. 일부 provider는 `-`와 `~`를 `_`로 정규화해 질문 전달이 사라질 수 있다.

## 하위 Flow가 꼭 필요한 경우의 내부 변환

```text
Agent question
-> 현재 하위 Flow graph 조회
-> Chat Input 정확히 1개 확인
-> 현재 component ID 확인
-> <ChatInput-ID>~input_value 생성
-> flow_tweak_data에 question 삽입
-> 부모 session_id 상속
-> 하위 Flow 실행
```

## 오류 조건

- 이름 또는 식별자와 일치하는 Flow 0개 또는 2개 이상
- Chat Input 0개 또는 2개 이상
- question이 비어 있음
- 내부 API/version 불일치
- 하위 Flow 실행 실패

세션 이력에서 질문을 추측해 복구하지 않는다. 질문 직접 전달 실패를 이전 대화나 저장 상태로 숨기면 다른 intent를 재사용할 수 있다.

위 검증을 통과하기 전에는 커스텀 이름 기반 Run Flow Tool을 registry, 교육 포털 또는 프로젝트 Bundle에 추가하지 않는다.

## 회귀 검사

- standalone import 뒤 Chat Input ID가 바뀌어도 question 전달
- Gemini/OpenAI 계층에서 안전한 Tool schema
- 부모 session_id 상속
- cold/warm cache 결과 동일
- Flow 이름 중복 시 명시적 실패
- 캐시에 질문, 결과, 인증값 미저장
