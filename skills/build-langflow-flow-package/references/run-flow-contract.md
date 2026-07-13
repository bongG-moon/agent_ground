# Run Flow Tool 계약

## 외부 schema

LLM에는 provider가 변형하지 않을 고정 이름을 노출한다.

```json
{
  "question": "사용자의 실제 질문"
}
```

`ChatInput-xVKPV~input_value` 같은 Langflow 내부 key를 외부 Tool parameter로 쓰지 않는다. 일부 provider는 `-`와 `~`를 `_`로 정규화해 질문 전달이 사라질 수 있다.

## 내부 변환

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

- 이름과 일치하는 Flow 0개 또는 2개 이상
- Chat Input 0개 또는 2개 이상
- question이 비어 있음
- 내부 API/version 불일치
- 하위 Flow 실행 실패

세션 이력에서 질문을 추측해 복구하지 않는다. 질문 직접 전달 실패를 이전 대화나 저장 상태로 숨기면 다른 intent를 재사용할 수 있다.

## 회귀 검사

- standalone import 뒤 Chat Input ID가 바뀌어도 question 전달
- Gemini/OpenAI 계층에서 안전한 Tool schema
- 부모 session_id 상속
- cold/warm cache 결과 동일
- Flow 이름 중복 시 명시적 실패
- 캐시에 질문, 결과, 인증값 미저장
