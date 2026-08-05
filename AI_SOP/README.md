# AI SOP Studio

자연어 업무 설명과 첨부 자료를 BoI Wiki Local 규칙의 OKF Markdown SOP와 Mermaid 흐름도로 변환하는 Web 서비스 PoC다. 작성 중인 내용은 개인 초안으로 보관하고, 사용자가 명시적으로 승인한 스냅샷만 공용 Wiki에 등록한다.

## 실행

Python 3.11 이상이 필요하다.

```powershell
cd AI_SOP
python -m venv .venv
.\.venv\Scripts\Activate.ps1
Copy-Item .env.example .env
python -m pip install -e ".[dev]"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

브라우저에서 `http://127.0.0.1:8765`를 연다. 최초 확인은 `.env`의 `AI_SOP_DEMO_MODE=true` 그대로 실행할 수 있으며, 이때 MongoDB나 Gemini 키 없이 메모리 저장소와 시연용 생성기를 사용한다.

## MongoDB + LLM 연결 모드

`.env`에서 아래 항목을 설정한다. `.env`는 Git에서 제외되어 있다.

```dotenv
AI_SOP_DEMO_MODE=false
AI_SOP_SESSION_SECRET=충분히-긴-무작위-문자열
AI_SOP_ADMIN_TOKEN=관리자-전용-토큰
MONGODB_URI=mongodb://127.0.0.1:27017
MONGODB_DATABASE=ai_sop
AI_SOP_LLM_PROVIDER=gemini
GEMINI_API_KEY=발급받은-키
GEMINI_MODEL=사용할-정확한-모델-ID
```

연결 모드는 시작할 때 MongoDB에 ping한다. `AI_SOP_LLM_PROVIDER=gemini`는 공식 `google-genai` SDK를 사용하며, `langchain_openai`로 변경하면 사내 OpenAI 호환 엔드포인트를 `ChatOpenAI`로 호출한다. 업로드 원본·추출 텍스트·생성 산출물은 개인 GridFS에, 승인 스냅샷은 별도 공개 GridFS에 저장한다.

사내 OpenAI 호환 엔드포인트를 사용할 때는 다음처럼 설정한다.

```dotenv
AI_SOP_DEMO_MODE=false
AI_SOP_LLM_PROVIDER=langchain_openai
OPENAI_API_KEY=사내-발급-토큰
OPENAI_BASE_URL=https://사내-게이트웨이.example/v1
OPENAI_MODEL=사내-모델-ID
OPENAI_STRUCTURED_OUTPUT_METHOD=json_schema
OPENAI_TIMEOUT_SECONDS=60
OPENAI_MAX_RETRIES=2
OPENAI_ENABLE_MULTIMODAL_SOURCES=false
```

`OPENAI_STRUCTURED_OUTPUT_METHOD`는 엔드포인트 기능에 맞춘다. 네이티브 Structured Outputs를 지원하면 `json_schema`, 도구 호출만 지원하면 `function_calling`, JSON 응답 모드만 지원하면 `json_mode`를 사용한다. `OPENAI_ENABLE_MULTIMODAL_SOURCES=false`일 때 첨부 자료는 서버에서 추출한 텍스트만 모델에 전달되어 사내 게이트웨이 호환성이 가장 높다.

## BoI 템플릿 업데이트

서비스는 원본 Git 저장소를 수정하지 않는다. 업데이트 후보를 commit SHA별 `.runtime/templates/`에 받고 필수 계약을 확인한 뒤 활성 포인터만 전환한다.

```powershell
python scripts/sync_boi_template.py
python scripts/sync_boi_template.py --activate
```

운영에서는 `BOI_TEMPLATE_LOCAL_PATH`를 비워 원격 저장소 동기화 방식을 사용한다. 문제가 있으면 이전 SHA에 대해 관리자 활성화 API를 호출해 즉시 되돌릴 수 있다.

## 주요 API

- `POST /api/session`: 임시 7자리 사용자 작업공간 생성
- `POST /api/drafts`: 개인 초안 생성
- `POST /api/drafts/{id}/sources`: 근거 파일 업로드
- `POST /api/drafts/{id}/questions`: 보완 질문 생성
- `POST /api/drafts/{id}/generate`: OKF SOP와 Mermaid 생성
- `POST /api/drafts/{id}/approve`: 명시적 승인 후 Wiki 등록
- `GET /api/wiki`: 승인 문서만 조회
- `/api/docs`: OpenAPI 문서

## 검증

```powershell
python -m pytest --basetemp .pytest-temp
```

만료된 MongoDB 개인 초안과 연결 GridFS 파일은 스케줄러에서 아래 명령을 주기적으로 실행해 정리한다.

```powershell
python scripts/cleanup_expired_drafts.py
```

## 설계 문서

- [AI SOP Web 서비스 기능 설계](WEB_SERVICE_FUNCTIONAL_DESIGN.md)
- [MongoDB·Google Gemini API 기술 결정](TECHNOLOGY_DECISIONS.md)
- [사내 SSO 연동 참고](SSO_AUTH_REFERENCE.md)
- [BoI Wiki MCP 기능 분석](MCP_FUNCTIONS_REFERENCE.md)
