# 초보자를 위한 캐시된 이름 기반 Run Flow 도구 설명

> 기준 환경: Langflow `1.8.2`, LFX `0.3.4`  
> 대상 Component: `cached_named_run_flow_tool.py`  
> 이 문서는 “왜 기본 Run Flow가 있는데 별도 Component를 만들었는가?”를 처음 접하는 사람도 이해할 수 있게 설명합니다.

## 먼저 한 문장으로 이해하기

이 Component는 Langflow의 기본 Run Flow 실행 기능을 새로 만든 것이 아닙니다.

**기본 Run Flow 엔진은 그대로 사용하고, 다른 환경으로 옮겨도 다시 연결하기 쉽고 Agent가 사용하기 편하도록 앞부분과 뒷부분을 보완한 전용 포장 Component입니다.**

자동차로 비유하면 다음과 같습니다.

| 역할 | 자동차 비유 | 실제 Langflow 역할 |
| --- | --- | --- |
| 기본 `RunFlowBaseComponent` | 자동차 엔진 | 하위 Flow를 실제로 실행 |
| 캐시된 이름 기반 Run Flow 도구 | 내비게이션과 운전 규칙 | 실행할 Flow 찾기, Agent 입력 정리, 세션·결과 처리 |

엔진을 버리고 새 자동차를 만든 것이 아니라, 기존 엔진을 안전하고 편리하게 사용할 수 있도록 내비게이션과 운전 규칙을 추가한 것입니다.

## 용어부터 간단히 보기

| 용어 | 쉬운 설명 |
| --- | --- |
| 상위 Flow | 사용자의 질문을 먼저 받고 어떤 일을 시킬지 정하는 Flow |
| Router Agent | 여러 도구 중 지금 사용할 도구 하나를 선택하는 Agent |
| 하위 Flow | 문서 검색, 데이터 분석, 메타데이터 저장처럼 실제 전문 업무를 수행하는 Flow |
| Run Flow | 한 Flow 안에서 다른 Flow를 실행하는 Langflow 기능 |
| Tool schema | Agent에게 “이 도구는 어떤 값을 받아야 하는가”를 알려주는 입력 명세 |
| Flow ID | Langflow DB가 Flow마다 부여하는 내부 식별값 |
| 세션 ID | 같은 대화에 속한 요청이라는 것을 구분하는 대화 식별값 |
| Graph cache | Flow의 노드와 연결 구조를 해석한 결과를 잠시 재사용하는 것 |

## 어떤 상황에서 필요했나

Route Flow V2에는 하나의 Agent가 여러 전문 하위 Flow 중 하나를 골라 실행하는 구조가 있었습니다.

```text
사용자 질문
    ↓
Router Agent
    ├─ 데이터 분석 Flow
    ├─ 메타데이터 확인 Flow
    ├─ 도메인 메타데이터 저장 Flow
    ├─ 테이블 카탈로그 저장 Flow
    └─ 필수 필터 저장 Flow
```

기본 Run Flow도 하위 Flow를 실행할 수 있습니다. 다만 이 구조를 Standalone JSON으로 다른 Langflow 환경에 옮기고, 여러 하위 Flow를 Agent Tool로 제공하면서 몇 가지 불편이 생겼습니다.

## 이유 1. 다른 환경으로 옮기면 Flow ID가 달라질 수 있음

### 어떤 문제인가

Flow ID는 사람에게 보이는 Flow 이름과 다릅니다. Langflow DB가 내부적으로 부여하는 번호에 가깝습니다.

예를 들어 원래 개발 환경의 데이터 분석 Flow가 다음 ID를 가질 수 있습니다.

```text
aaa-111
```

같은 JSON을 다른 Langflow 환경에 가져오면 새 ID가 발급될 수 있습니다.

```text
bbb-222
```

기본 Run Flow에 예전 ID인 `aaa-111`이 남아 있으면 새 환경에서 사용자가 대상 Flow를 다시 선택해야 할 수 있습니다.

### 어떻게 보완했나

이 Component는 JSON에 이전 환경의 ID를 실행 계약으로 고정하지 않습니다.

```text
저장하는 값: 사람이 정한 정확한 Flow 이름
실행할 때 찾는 값: 현재 환경의 실제 Flow ID
```

실행할 때마다 다음 순서로 확인합니다.

1. 부모 Router와 같은 폴더에서 정확한 Flow 이름을 찾습니다.
2. 이름이 정확히 일치하는 Flow가 하나인지 확인합니다.
3. 현재 환경에서 발급된 실제 Flow ID를 가져옵니다.
4. 그 실제 ID로 Langflow 기본 실행 엔진을 호출합니다.

쉽게 말하면, 예전에 적어 둔 전화번호를 계속 쓰는 대신 회사 주소록에서 이름을 검색해 현재 전화번호를 확인하고 전화하는 방식입니다.

> 기본 Run Flow가 잘못된 것은 아닙니다. 가져온 뒤 사용자가 대상 Flow를 다시 선택해도 됩니다. 이 Component는 그 수동 재연결을 줄이기 위해 만든 것입니다.

## 이유 2. Agent에게 불필요한 입력까지 보일 수 있음

### 어떤 문제인가

Agent가 하위 Flow를 Tool로 실행하려면 Tool이 받을 입력을 알아야 합니다. 기본 Run Flow는 하위 Flow의 시작 입력들을 살펴보고 Tool 입력 명세를 만듭니다.

그런데 하위 Flow 안에는 사용자 질문 외에도 개발자가 캔버스에서 관리하는 내부 설정이 있을 수 있습니다.

예를 들면 다음과 같습니다.

- 사용자 질문
- 제품·의도 분석 지침
- pandas helper 코드
- 최종 답변 생성 지침
- 오류 복구 Prompt

Agent가 실제로 채워야 하는 값은 사용자 질문 하나인데, 내부 Prompt와 helper까지 Tool 입력 후보로 보이면 도구 사용법이 복잡해지고 잘못된 값을 넣을 가능성이 커집니다.

### 어떻게 보완했나

이 Component는 Agent에게 하위 Flow의 `Chat Input.input_value` 하나만 노출합니다.

```text
기본 방식의 예
사용자 질문 + 내부 지침 + helper + 답변 지침 + 복구 Prompt

현재 방식
사용자 질문 1개
```

원본 Route Flow V2의 특정 데이터 분석 Flow에서 측정했을 때 Tool 입력은 5개에서 1개로 줄었고, schema 크기는 `26,338 bytes`에서 `356 bytes`로 줄었습니다. 이 숫자는 해당 Flow에서 측정한 예시이며 하위 Flow 구조에 따라 달라집니다.

### 자주 하는 오해

이 기능이 하위 Flow 내부의 LLM Prompt나 데이터까지 줄이는 것은 아닙니다.

- 줄어드는 것: 상위 Router Agent가 보는 Tool 설명과 입력 명세
- 그대로인 것: 하위 Flow가 실행할 때 사용하는 내부 Prompt, DB 조회, pandas, LLM 작업

## 이유 3. Agent가 여러 Tool을 구분하기 쉬워야 함

기본 Run Flow는 Flow 이름을 바탕으로 Tool 이름과 설명을 자동으로 만듭니다. 하위 Flow가 하나라면 충분할 수 있지만, 조회 Tool과 저장 Tool이 여러 개 있으면 Agent가 역할을 혼동할 수 있습니다.

그래서 이 Component에서는 다음 두 값을 사람이 명확하게 작성합니다.

```text
도구 이름: run_document_search
도구 설명: 사내 문서의 내용과 근거를 조회할 때만 사용합니다.
```

- `도구 이름`은 Agent가 식별하는 짧은 이름입니다.
- `도구 설명`은 언제 사용하고 언제 사용하지 말아야 하는지 알려주는 선택 기준입니다.
- 두 값은 하위 Flow의 업무 데이터로 전달되지 않습니다.

즉, Tool 이름과 설명은 하위 직원에게 넘기는 업무 자료가 아니라 Router Agent가 보는 업무 분장표입니다.

## 이유 4. 질문 전달 경로와 세션을 분리함

초기 구조처럼 하나의 Chat Input을 Agent와 여러 Run Flow Component에 동시에 연결하면 연결선이 많아지고, 같은 질문이 여러 경로에서 처리될 가능성을 검토해야 합니다.

현재 권장 연결은 단순합니다.

```text
Chat Input → Agent → Agent가 선택한 Tool 하나 → 해당 하위 Flow
```

사용자 질문은 Agent가 선택한 Tool의 Chat Input 인자로 전달합니다.

세션 ID는 질문 본문과 다른 값입니다. 이 Component는 다음 순서로 세션 ID를 정합니다.

```text
1순위: Component에 직접 입력한 세션 ID
2순위: 부모 Flow의 세션 ID
3순위: 빈 값
```

보통은 세션 ID 입력을 비워 두고 부모 대화를 상속하면 됩니다.

## 이유 5. Flow 구조를 매번 처음부터 해석하지 않음

하위 Flow를 실행하려면 Langflow가 JSON에 들어 있는 노드와 연결선을 읽어 실행 가능한 Graph로 구성해야 합니다.

`Flow 그래프 캐시`를 켜면 한 번 구성한 Graph를 잠시 재사용할 수 있습니다.

```text
첫 실행
이름 조회 → Graph 구성 → 하위 Flow 전체 실행

다음 실행
이름 조회 → 캐시된 Graph 사용 → 하위 Flow 전체 실행
```

### 캐시하는 것

- 하위 Flow의 노드·연결 구조
- 파싱된 Graph 구성 결과

### 캐시하지 않는 것

- 사용자 질문
- DB·API 조회 결과
- pandas 실행 결과
- LLM 응답
- 이전 최종 답변

따라서 같은 질문에 이전 답을 그대로 돌려주는 답변 캐시가 아닙니다. 하위 Flow의 실제 업무는 요청마다 다시 실행됩니다.

또한 이 캐시는 Langflow backend process 메모리에 있습니다. 서버를 재시작하면 사라지고 여러 worker가 하나의 캐시를 공유하지 않습니다.

## 이유 6. 완성된 하위 답변을 Agent가 다시 쓰지 않게 할 수 있음

일반적인 Agent Tool 실행은 다음과 같이 동작할 수 있습니다.

```text
하위 Flow 실행
→ 결과를 부모 Agent에게 반환
→ 부모 Agent가 결과를 다시 작성
→ 최종 출력
```

하지만 하위 Flow가 이미 최종 한국어 답변, 표, 경고와 오류를 완성했다면 부모 Agent의 재작성 때문에 다음 문제가 생길 수 있습니다.

- 추가 LLM 호출로 응답이 늦어짐
- 토큰 비용 증가
- 표나 경고가 빠짐
- 원래 답변의 의미가 달라짐

`하위 결과 직접 반환`을 켜면 하위 Flow의 완성된 결과를 부모 Agent가 다시 쓰지 않고 바로 반환합니다.

> 이 설정은 메시지 중복 저장을 직접 막는 기능이 아닙니다. 메시지 중복은 Chat Output 개수와 여러 갈래 연결을 함께 확인해야 합니다.

## 실제 실행 흐름

```text
1. 사용자가 질문을 입력함
2. Router Agent가 Tool 이름과 설명을 보고 Tool 하나를 선택함
3. 선택된 Component가 정확한 하위 Flow 이름을 찾음
4. 현재 Langflow DB의 실제 Flow ID와 수정 시간을 확인함
5. 최신 Graph cache가 있으면 재사용함
6. 사용자 질문을 하위 Flow의 Chat Input 인자로 전달함
7. Langflow 기본 Run Flow 엔진이 하위 Flow 전체를 실행함
8. 하위 Flow가 최종 결과를 만듦
9. 직접 반환 설정이 켜져 있으면 부모 Agent 재작성 없이 출력함
```

## 기본 기능 중 그대로 사용하는 부분

별도 Component라고 해서 하위 Flow 실행 기술을 새로 구현한 것은 아닙니다. 다음 기능은 Langflow의 기본 `RunFlowBaseComponent` 구현을 그대로 사용합니다.

- Flow payload를 Graph로 구성
- 하위 Flow 입력값 적용
- `run_flow()` 실행
- 하위 Flow 출력 해석
- callback과 Tool toolkit
- shared Graph cache
- 수정 시간에 따른 캐시 무효화

Agent Ground Component가 주로 바꾼 것은 다음 경계입니다.

- 어떤 범위에서 어떤 이름의 Flow를 찾을지
- Agent에게 어떤 입력만 보여줄지
- Tool 이름과 설명을 어떻게 정할지
- 세션을 어떻게 이어 줄지
- 하위 결과를 부모 Agent가 다시 작성할지

## 기본 Run Flow와 비교

| 항목 | 기본 Run Flow | 캐시된 이름 기반 Run Flow 도구 |
| --- | --- | --- |
| 실행 엔진 | Langflow 기본 엔진 | 같은 기본 엔진을 상속 |
| 대상 선택 | Builder에서 Flow 선택 | 정확한 Flow 이름 입력 |
| Flow ID | 선택 시점 ID가 저장될 수 있음 | 실행 시 현재 ID를 다시 조회 |
| 다른 환경으로 이동 | 대상 재선택이 필요할 수 있음 | 이름이 고유하면 현재 ID 해석 |
| Agent 입력 | 여러 시작 입력이 노출될 수 있음 | Chat Input 하나만 허용 |
| Tool 이름·설명 | 자동 생성 | 업무에 맞게 직접 지정 |
| Graph cache 기본값 | 일반적으로 꺼짐 | 켜짐 |
| 세션 | 직접 입력 | 직접 입력 또는 부모 세션 상속 |
| 최종 결과 | 부모 Agent가 다시 처리할 수 있음 | 직접 반환 선택 가능 |
| 적합한 범위 | 여러 입력·출력의 범용 Flow | 질문 하나를 받는 전문 하위 Flow |

## 언제 기본 Run Flow를 쓰고 언제 이 Component를 쓰나

### 기본 Run Flow가 더 적합한 경우

- 하위 Flow의 입력이 여러 개이고 Agent가 각각 채워야 함
- 여러 종류의 출력을 동적으로 사용해야 함
- Builder에서 대상 Flow를 직접 선택·관리하는 것이 편함
- 한 환경 안에서만 사용해 ID 재연결 문제가 거의 없음

### 이 Component가 더 적합한 경우

- Standalone JSON을 다른 Langflow 환경으로 자주 옮김
- Router Agent가 여러 전문 하위 Flow 중 하나를 선택함
- 각 하위 Flow가 사용자 질문 하나를 받음
- Tool 이름과 사용 조건을 명확하게 관리해야 함
- 하위 Flow가 이미 최종 답변을 완성함

이 Component는 범용 Run Flow를 완전히 대체하지 않습니다. 정해진 형태의 전문 하위 Flow를 Agent Tool로 제공하는 데 초점을 맞춥니다.

## Agent Ground에서 추가로 보강한 안전장치

원본 Route Flow V2 설명을 검토한 뒤 Agent Ground 버전에는 다음 안전 조건을 더 명확히 적용했습니다.

- 기본적으로 부모 Router와 같은 폴더에서만 대상 검색
- 다른 폴더 검색은 고급 설정을 직접 켠 경우에만 허용
- 이름이 없거나 같은 이름이 둘 이상이면 실행 거절
- 현재 부모 Flow 자신을 실행하는 직접 재귀 차단
- 같은 초 안의 수정도 구분하도록 microsecond 단위 수정 시간 비교
- Tool 이름·설명과 세션 ID 길이·문자 검증

## 해결된 문제: 질문 키가 밑줄로 바뀌던 `empty_question`

이전 Component는 질문 입력을 하나로 줄였지만 외부 Tool 인자 이름에는 다음과 같은 내부 node ID가 남아 있었습니다.

```text
ChatInput-xVKPV~input_value
```

Gemini 같은 provider가 Tool 인자 이름의 `-`와 `~`를 `_`로 바꾸면 다음과 같이 전달될 수 있습니다.

```text
ChatInput_xVKPV_input_value
```

사람에게는 비슷해 보이지만 기본 Run Flow는 원래 `~`가 있는 키만 하위 Flow 입력으로 인식합니다. 그래서 Agent가 질문을 보냈고 세션도 정상 상속됐는데 하위 Chat Input에는 빈 문자열이 들어가는 문제가 발생했습니다.

### 지금은 어떻게 바뀌었나

Agent에게는 node ID가 없는 고정 필드 하나만 보입니다.

```json
{
  "flow_tweak_data": {
    "question": "현재 등록된 계산 로직 관련 도메인은 어떤 것들이 있어?"
  }
}
```

`flow_tweak_data`는 Langflow가 Run Flow 입력들을 묶는 바깥 포장입니다. Agent가 실제로 채우는 질문 필드는 그 안의 `question` 하나입니다.

Component 내부에서 실행 직전에 현재 하위 Flow의 Chat Input을 찾습니다.

```text
Agent가 flow_tweak_data.question 전달
→ 현재 하위 Flow Graph 확인
→ Chat Input이 정확히 하나인지 확인
→ 현재 Chat Input ID 확인
→ 내부 Run Flow 입력으로 변환
→ 기본 Run Flow 엔진 실행
```

이 변환은 내부에서만 수행하므로 standalone 재import로 `ChatInput-...` ID가 바뀌어도 Agent Tool schema는 계속 `question`입니다.

### 일부러 추가하지 않은 fallback

질문이 없을 때 부모 세션 이력이나 이전 분석 상태에서 질문을 추정하지 않습니다. 이런 방식은 비슷한 후속 질문에서는 정상처럼 보일 수 있지만, 조건이 달라지면 이전 intent를 잘못 재사용할 수 있기 때문입니다.

`question`이 비어 있으면 하위 Flow를 실행하기 전에 요청을 거절합니다. 하위 Flow에 Chat Input이 없거나 둘 이상이어도 임의로 선택하지 않습니다. 다만 Langflow 기본 실행기가 내부 오류를 공통 문구로 감싸는 경우에는 Agent 화면에 세부 한국어 원인 대신 일반 실행 오류가 표시될 수 있습니다.

## 처음 등록한 뒤 확인할 최소 항목

- [ ] Python 파일 하나로 Custom Component가 등록된다.
- [ ] `대상 Flow 이름`에 같은 폴더의 정확한 이름을 입력했다.
- [ ] 대상 하위 Flow에 Chat Input이 정확히 하나 있다.
- [ ] 대상 하위 Flow에 Tool용 최종 출력이 정확히 하나 있다.
- [ ] Agent의 Tools 입력에 `Flow 도구` 출력을 연결했다.
- [ ] Tool 이름과 설명만 보고도 사용 조건을 구분할 수 있다.
- [ ] 실제 질문이 하위 Flow Chat Input에 전달된다.
- [ ] 첫 실행과 두 번째 실행의 답변 내용이 같다.
- [ ] 하위 Flow를 수정하면 새 Graph가 사용된다.
- [ ] 직접 반환 설정이 의도한 최종 출력과 맞는다.
- [ ] 간접 순환 구조인 `Flow A → Flow B → Flow A`가 없다.

## 최종 정리

이 Component를 별도로 만든 이유는 기본 Run Flow가 실행을 못해서가 아닙니다.

기본 Run Flow가 잘 수행하는 **하위 Flow 실행**은 그대로 맡기고, Router Agent와 Standalone 배포에서 반복해서 불편했던 다음 부분만 보완한 것입니다.

```text
이식성: 현재 환경의 실제 Flow ID를 다시 찾음
단순성: Agent에게 질문 입력 하나만 보여줌
선택성: Tool 이름과 설명을 업무별로 명확히 지정
연속성: 부모 대화 세션을 하위 Flow로 이어 줌
효율성: 하위 Flow Graph 구성 결과만 재사용
보존성: 완성된 하위 답변을 다시 쓰지 않고 반환 가능
```

따라서 이 Component는 “새 Run Flow 엔진”이 아니라 **기본 Run Flow를 Router Agent와 Standalone 배포 방식에 맞게 안전하게 사용하는 전용 어댑터**라고 이해하면 됩니다.
