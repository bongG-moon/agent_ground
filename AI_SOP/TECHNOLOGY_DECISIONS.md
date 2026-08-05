# AI SOP 기술 결정

## 상태

- 결정일: 2026-08-05
- 상태: 1차 설계 확정
- 관련 문서: [AI SOP Web 서비스 기능 설계](WEB_SERVICE_FUNCTIONAL_DESIGN.md)

## 결정 1. MongoDB를 주 저장소로 사용

### 적용 범위

- 사용자 session과 개인 draft
- AI 인터뷰 message
- 원본 자료 metadata와 근거 연결
- 생성 job과 검증 결과
- 승인 Wiki 문서와 버전
- template version과 업데이트 검증 결과
- 승인·게시 audit event

### 파일 저장

일반 MongoDB document의 크기 제한과 파일 streaming을 고려해 원본 파일과 생성 artifact는 GridFS에 저장한다.

- `private_assets`: 개인 원본, 추출 결과, SOP 초안, Mermaid
- `published_assets`: 승인 snapshot과 공개가 허용된 첨부파일

GridFS는 여러 collection을 포함하는 transaction을 지원하지 않으므로 게시 처리는 `publishing -> published` 상태와 hash 검증, idempotency key, 실패 cleanup으로 구현한다.

### 사용 driver

- Python: 공식 `pymongo`
- 대용량 파일: `GridFSBucket` 또는 `AsyncGridFSBucket`

## 결정 2. LLM provider 추상화와 Structured Output 사용

AI 모델을 서비스 코드에 직접 결합하지 않고 `SopModelProvider` 인터페이스 뒤에 둔다. 현재 지원 provider는 다음 두 가지다.

- `gemini`: 공식 Google GenAI SDK를 사용하는 기본 PoC provider
- `langchain_openai`: `langchain_openai.ChatOpenAI`를 사용하는 OpenAI 호환 provider

운영 환경에서는 `AI_SOP_LLM_PROVIDER`로 하나를 선택한다. 두 provider 모두 질문 생성과 `SopDraftIR` 생성을 같은 계약으로 반환하므로, MongoDB 저장·OKF Markdown renderer·Mermaid renderer·승인 흐름은 provider와 독립적이다.

### 사용 SDK

- 공식 Google GenAI SDK
- Python package: `google-genai`
- legacy `google-generativeai`는 신규 구현에 사용하지 않는다.

### 생성 방식

Gemini가 Markdown을 무제한 자유 생성하게 하지 않고 다음 단계를 사용한다.

1. 사용자 설명과 허용된 자료 분석
2. 부족한 정보 질문
3. Structured Output으로 `SopDraftIR` 생성
4. Pydantic과 업무 규칙으로 검증
5. 서비스 renderer가 BoI Markdown과 Mermaid 생성
6. 원본 Git validator와 서비스 validator 실행

### 모델 선택

- 정확한 모델 ID는 환경별 평가 후 결정한다.
- Gemini는 `GEMINI_MODEL`, LangChain OpenAI 호환 endpoint는 `OPENAI_MODEL`로 설정한다.
- 운영에는 검증된 stable model ID와 endpoint를 고정한다.
- 모든 결과에 실제 model ID를 기록한다.

### LangChain OpenAI 호환 endpoint 설정

```dotenv
AI_SOP_DEMO_MODE=false
AI_SOP_LLM_PROVIDER=langchain_openai
OPENAI_API_KEY=사내-토큰
OPENAI_BASE_URL=https://사내-게이트웨이.example/v1
OPENAI_MODEL=사내-모델-ID
OPENAI_STRUCTURED_OUTPUT_METHOD=json_schema
OPENAI_TIMEOUT_SECONDS=60
OPENAI_MAX_RETRIES=2
OPENAI_ENABLE_MULTIMODAL_SOURCES=false
```

`OPENAI_STRUCTURED_OUTPUT_METHOD`는 endpoint 지원 수준에 맞춘다.

- `json_schema`: native Structured Outputs를 지원하는 경우
- `function_calling`: tool/function calling만 지원하는 경우
- `json_mode`: JSON object 응답만 보장하는 경우

이미지 원본을 모델에 직접 전달해야 하고 모델이 vision 입력을 지원하면 `OPENAI_ENABLE_MULTIMODAL_SOURCES=true`로 설정한다. 사내 endpoint 호환성과 정보 전송 최소화가 우선이면 `false`로 두고 서버에서 추출한 텍스트만 전달한다.

현재 구현은 `OPENAI_API_KEY`를 `ChatOpenAI(api_key=...)`로 전달하므로 표준 Bearer API key 방식의 endpoint를 전제로 한다. `X-API-Key` 같은 별도 인증 header가 필요한 gateway라면 `ChatOpenAI(default_headers=...)`를 추가하는 Adapter 확장이 필요하다.

## 결정 3. 원본 BoI Wiki Local은 수정하지 않음

- 원본 Repository를 commit별 read-only template으로 보관
- Web/API/Gemini/MongoDB integration은 원본 밖의 service code로 구현
- 새 commit은 candidate checkout에서 원본 검사와 E2E 호환성 검증 수행
- 운영자가 검증된 commit을 활성화
- 기존 draft와 게시 문서는 생성 당시 `template_commit` 유지
- 문제 발생 시 이전 active commit으로 롤백

## 보안 전제

선택한 LLM provider 호출에는 외부 또는 사내 gateway로의 네트워크 전송이 발생한다. 실제 사내 자료를 사용하기 전에 다음 승인이 필요하다.

- 전송 가능한 자료 등급
- 민감정보 마스킹 기준
- provider/gateway logging과 보존 기간
- 데이터 공유 opt-in 비활성화
- Files API 사용과 원격 파일 삭제 정책
- API key secret 보관 방식

## 공식 참고자료

- [Google Gemini API libraries](https://ai.google.dev/gemini-api/docs/libraries)
- [Google Gemini structured outputs](https://ai.google.dev/gemini-api/docs/structured-output)
- [Google Gemini document understanding](https://ai.google.dev/gemini-api/docs/document-processing)
- [Google Gemini data logging and sharing](https://ai.google.dev/gemini-api/docs/logs-policy)
- [LangChain ChatOpenAI integration](https://docs.langchain.com/oss/python/integrations/chat/openai)
- [MongoDB PyMongo driver](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/)
- [MongoDB GridFS with PyMongo](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/crud/gridfs/)
- [MongoDB GridFS manual](https://www.mongodb.com/docs/manual/core/gridfs/)
