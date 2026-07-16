# Agent Ground 현재 검증 보고서

> 검증일: 2026-07-16  
> 현재 상태: 실행 가능 자산 `user_testing`, `reusable_data_flow`는 export 불일치로 `building`  
> 범위: 기능 단위 Component 21개와 Flow 내부 Node 30개, 신규 DRM 문서 텍스트 추출 Flow, PPT 참고 이미지 기반 HTML 프레젠테이션 Flow, 입출력·코드 포털, 교육 레이아웃, 이동식 Skill 4종, Flow export 무결성, JSON, Python, HTML, 링크, Registry와 기존 Langflow 계약 회귀

## 통과한 검사

| 검사 | 결과 |
| --- | --- |
| 재사용 데이터 Flow export 감사 | 현재·원본 JSON은 같은 `업무분석flow`; 12개 내부 Node class 포함 0개 확인, `building` 격리 |
| 내부 Node 이동 무결성 | Flow별 소유·경로·class·version을 포함한 총 30개 내부 Node 계약 검증 |
| 신규 Enterprise Document RAG | 기능 Component 6개 + Flow 내부 Node 3개, 13 nodes / 10 edges |
| 신규 하이브리드 Skill 기반 Agent | 상위 9 nodes / 8 edges, 회의 하위 5 nodes / 3 edges, 직접 계산 Tool 2개 + Run Flow Tool 1개 |
| 신규 PPT 참고 이미지 HTML 프레젠테이션 | 17 nodes / 22 edges, Custom Python 11개, 구조화 디자인·모션 정책, 실제 dataset 바인딩, 결정론적 Renderer와 Quality Gate |
| 신규 DRM 문서 텍스트 추출 | 2 nodes / 1 edge, 문서·텍스트·일반 이미지 다중 업로드, Bearer·empNo, host allowlist, HTTPS 기본값, LLM 없는 Message 출력 |
| Python 문법 검사 | 101개 통과 |
| Standalone 단일 파일·상대 import 검사 | 기능 Component 21개 + Flow 내부 Node 30개 통과 |
| Component 카탈로그 범위 | General 8개 + Domain 13개 = 기능 단위 21개, Flow 내부 Node 30개는 제외 |
| Component·내부 Node 상세/코드 | Component 21세트와 Flow 내부 Node 30세트에 실제 AST 입력·출력 계약 및 다크 코드 화면, raw `.py` 링크 0개 |
| JSON 파싱 | 79개 통과: Skill bundle, 7개 실행 가능 Project bundle과 Skill pack manifest 포함 |
| Flow 구조 | `flows/` 아래 Flow manifest 7개, Project Bundle의 실행 가능 Flow 7개에 node/edge 존재 |
| Component와 내부 Node 참조 | `component_refs.json`의 기능 Component 18건과 `internal_nodes.json`의 내부 Node 30건 소유·경로·class·version 통과 |
| Flow refs와 embedded class | runtime-ready Flow는 main/subflow JSON과 자동 대조, 격리 Flow는 명시적 integrity issue 요구 |
| HTML 파싱·구조 | 121개 페이지 통과 |
| 로컬 링크와 자산 경로 | 2,728개 통과 |
| Registry | 기능 Component 21 + 최상위 Flow manifest 7 = 28개 자산 일치, 내부 Node 30개 제외 |
| JavaScript 문법 | 통과 |
| 교육 레이아웃 | 단계 grid, 표 내부 스크롤·sticky header, 720px 이하 세로 전환 정적 검증 |
| 이동식 개발 Skill | 4개 `quick_validate` 통과, 임시 목적지 설치 4/4 확인 |
| HTML 파일 위치 | 모두 `html/` 아래에 위치 |
| Business Agent Design 실행 코드 | 공용 Library와 분리된 메인 10개 + 카탈로그 운영 5개 Standalone 실행 Node |
| Business Agent Design Flow | 24 nodes / 34 edges, Langflow 1.8.2 Import JSON 생성 |
| Business Agent Design 기능 테스트 | 6개 통과: graph, 분기, 개선 상세, HTML 보안, Import shape, handle |
| Business/프로젝트 Bundle | BOM 없음, `{"flows":[` prefix, 격리 donor를 제외한 프로젝트 7개 Flow 포함 |
| Langflow edge handle | 34개 edge의 `œ` decode 및 `edge.data` 일치 확인, `┇` 없음 |
| Business Agent BEFORE/AFTER Flow Chart | 2개 Chart, 분기, 변경 설명 action 확인 |
| 기존 교육 본문 영역 보존 | 전체 markup 포함 확인 |
| 기존 교육 제목 보존 | 141개 전체 일치 |
| 기존 교육 링크·자산 참조 보존 | 202개 전체 포함 |
| Document RAG 기능·보안 테스트 | 13개 통과: ACL, PII, 품질 gate, 거절, injection, version, citation, embedded source |
| Document RAG runtime template | 실제 Langflow `1.8.2` / LFX `0.3.4`에서 Component 6개 + 내부 Node 3개 code template 생성·재생성 일치 |
| Document RAG 실제 Upload | 임시 Flow HTTP 201, 13 nodes / 10 edges |
| Document RAG 실제 Full Build | HTTP 200, 실행 node 11/11 `valid=true`, invalid 0 |
| Document RAG 실제 Chat Output | 답변 322자, 숫자 인용 `[1]`, `RAG lifecycle separation` 및 `p.1` 확인 |
| 임시 Flow 정리 | DELETE 200, Flow 수 75 → 75, 잔여 검증 Flow 0 |
| 하이브리드 Skill Agent 기능·구조 테스트 | 18개 통과: 카탈로그, 계산·구조화, 실제 Tool 호출, 직접 Tool 2개, Run Flow Tool 1개, 상·하위 Graph, 전용·전체 Bundle |
| 하이브리드 Skill Tool 구성 | 경비·휴가 Component Tool Mode 2개, Cached Named Run Flow의 고유 Tool 출력 1개, 회의 하위 Flow의 구조화 Component 1개 |
| Skill Tool 호출 schema | 경비·휴가는 필수 `request` 하나, 회의 Run Flow는 내부 node ID 대신 `flow_tweak_data.question` 고정 계약 사용 |
| Skill Agent Graph 구조 | 실제 LFX에서 상위 7 vertices / 8 edges / Agent Tool 입력 3개, 하위 4 vertices / 3 edges parse 통과 |
| Skill Agent 포털 화면 | 하이브리드 구조·일괄 Bundle·MCP 확장 링크를 포함해 재생성, HTML·로컬 링크 정적 검증 통과 |
| Skill Agent 실제 모델 E2E | 특정 모델·API Key 미포함, 사용자 승인 Tool Calling 모델 연결 후 확인 필요 |
| 공용 Component 추천 조사 | P0 10개, P1 11개, P2 9개 등 총 30개 ITEM과 상태 정리 |
| 선택 공용 Component 구현 | `multi_image_base64_encoder`, `cached_named_run_flow_tool` 2개 Standalone·한글 UI·가이드 생성 |
| 선택 Component 기능·보안 테스트 | 10개 통과: 이미지 순서·decode·signature·용량·SVG, 동일 폴더·cross-folder opt-in·동명 충돌·직접 재귀·고정 question schema·현재 Chat Input 매핑·session·timestamp |
| 선택 Component runtime template | 실제 Langflow `1.8.2` / LFX `0.3.4`에서 2개 template과 다중 FileInput·Tool output 계약 생성 |
| 추천·구현 HTML 문서 | 2개 상세 페이지, 사용 가이드와 추천 현황 카탈로그 연결 |
| Run Flow 초보자 설명 | 기본 엔진 상속, 별도 구현 이유 6가지, 특수문자 key 변형 원인과 고정 `question` 해결 방식을 한글 문서·상세 페이지에 연결 |
| Run Flow 초보자 HTML 화면 | 1280px·375px에서 6개 이유 카드, 실행 순서, 비교표, 경고와 자료 링크 확인 |
| 추천·구현 포털 화면 | 1280px 데스크톱·375px 모바일에서 한글 분류명, 상세 이동, 자료 링크, 가로 넘침 없음 확인 |
| 최소 단위 직접 조회 구현 | Oracle·H-API·Datalake·GooDocs·일반 API 5개 Standalone, 출력 포트 `data_table` 한 개 |
| 직접 조회 기능·보안 계약 테스트 | 8개 통과: template, native bind, 최대 행, 빈 결과, HTTP 기본 차단, 응답 경로, origin redirect, Datalake host·CA·SQL 경계, 자원 정리, GooDocs 교체 경계, 기존 참조 보존 |
| 직접 조회 runtime template | 실제 Langflow `1.8.2` / LFX `0.3.4`에서 5개 template, 한글 UI, `DataFrame` 단일 출력, cache/tool 비활성 확인 |
| 직접 조회 로컬 의존성 | `oracledb`, `requests`, `aiohttp` 확인; `mysql-connector-python`은 현재 Langflow 환경에 없음 |
| 직접 조회 포털 화면 | 1280px·375px 가로 넘침·깨진 이미지·콘솔 오류 없음, 5개 카드와 보안 안내 확인 |
| 직접 조회 문서 이동 | 교육 안내 → 직접 조회 카탈로그 → 기존 재사용 Flow 실제 클릭, 기존 Flow의 역방향 링크 확인 |
| Datalake 상세 화면 | HTTP 토글, 허용 MySQL Host, CA 경로, `data_table`, 설치 패키지명 표시 확인 |
| PPT HTML 프레젠테이션 회귀 테스트 | 전용 Node·Renderer 24개 통과: 실제 Builder 양식·우선순위·슬라이드 수 범위·샘플 PNG·Encoder preset·정책 계약·실데이터 바인딩·HTML escaping·모션 위반·외부 의존성·품질 실패 차단 |
| 전체 독립 회귀 테스트 | 전체 `pytest` 98개 통과, 실제 Langflow 1.8.2/LFX 0.3.4 환경과 7개 Flow Bundle 순서 포함 |

## DRM 문서 텍스트 추출 Flow

`drm_document_text_extraction_flow`는 `drm_document_text_extractor` Standalone Component와 Chat Output만 연결한 최소 Flow입니다. 사용자가 올린 PDF·Office·HWP·텍스트·CSV·일반 이미지 파일을 입력 순서대로 DRM API의 multipart `file` 필드에 `application/octet-stream`으로 전송하고, `Authorization: Bearer ...`와 `empNo` query parameter를 제공 코드와 같은 계약으로 사용합니다. 이미지의 문자 내용은 DRM API가 OCR 평문을 반환할 때 읽을 수 있습니다.

배포 JSON에는 endpoint, 토큰, 사번과 파일 경로가 없습니다. HTTP 전송은 기본 차단하고 HTTPS 인증서 검증을 기본 활성화했으며, API host가 별도 allowlist에 없으면 네트워크 호출 전에 실패합니다. POST redirect, 비정상 응답 본문 노출, 무제한 파일·응답 읽기도 차단했습니다.

가짜 DRM transport를 사용한 전용 테스트에서 UTF-8·CP949 평문, 다중 파일 순서, multipart 요청, HTTP opt-in, allowlist, 확장자·크기 제한과 비밀값 비노출을 확인했습니다. Langflow `1.8.2` / LFX `0.3.4`의 실제 template 생성기로 13개 입력과 `extracted_text: Message`·`processed_file: Data` 출력, 2-node / 1-edge 직접 업로드 Flow JSON, 원본 코드 embed와 7개 Project Bundle을 재생성·검증했습니다. 직접 업로드 Flow에서는 EWS용 `file_record` 입력을 숨기고 Message 출력만 사용하고, EWS 메일 Flow에서는 같은 Component가 반환 평문을 UTF-8 TXT 작업 파일로 저장합니다. 실제 사내 DRM endpoint와 업무 문서는 호출하지 않았으므로 상태는 `user_testing`입니다.

## 신규 공용 Component 선택 구현

2026-07-12에는 사내 업무에서 반복 활용할 Standalone Component 후보 30종을 조사하고 사용자가 선택한 두 항목을 구현했습니다.

- P0: 외부 인프라 없이 먼저 만들기 좋은 10개
- P1: 사내 API·승인·운영 연동 11개
- P2: 추가 패키지나 운영 시스템이 필요한 9개

추천 목록의 `multi_image_base64_encoder`와 외부 참조 기반 `cached_named_run_flow_tool`은 Python 원본, manifest, 테스트, 한글 사용 가이드와 HTML 상세 페이지를 생성했습니다. 별도 Utility 전용 Flow는 만들지 않았지만, `cached_named_run_flow_tool`은 현재 하이브리드 Skill Agent에서 회의 하위 Flow 호출에 재사용합니다.

두 Component는 실제 runtime template과 격리 기능 테스트를 통과했지만 사용자가 Builder에서 직접 등록·실행하기 전까지 `user_testing`입니다. Cached Run Flow 도구는 실제 LFX Pydantic Tool schema에서 바깥 `flow_tweak_data`와 내부 필수 `question` 하나가 생성되고 현재·재import Chat Input ID로 내부 매핑되는 것을 확인했습니다. 현재 Agent Ground에는 이를 사용하는 상위 Agent JSON과 `meeting_action_skill_flow` 하위 Flow JSON을 함께 생성했지만, 실제 Builder에서의 cold/warm 실행과 session 상속 E2E는 아직 수행하지 않았습니다. 다른 프로젝트에서 수행한 Flow 매핑·partial build 기록은 Agent Ground 검증으로 승계하지 않습니다.

## Skill 기반 업무 Agent 예시

`skill_based_agent_flow`는 Langflow 공식 Simple Agent와 같은 `component_as_tool → Agent.tools` 연결을 사용하되, 직접 계산 Tool과 Run Flow Tool을 함께 보여주는 하이브리드 구조입니다. 데모 Skill 카탈로그가 세 업무의 선택 조건·instructions·허용 action을 검증하고 Agent의 `system_prompt`를 동적으로 만듭니다. LLM에는 다음 세 Tool 이름이 보입니다.

- `expense_precheck_skill`: 경비 금액 합산과 데모 한도 비교
- `leave_policy_skill`: 두 ISO 날짜 사이 평일·설정 휴일 계산
- `meeting_action_skill`: 개선형 이름 기반 Run Flow Tool이 같은 프로젝트의 `meeting_action_skill_flow`를 호출해 `담당자 | 할 일 | YYYY-MM-DD` 행 구조화

경비·휴가 Tool은 외부 입력 schema에 필수 `request` 하나만 노출합니다. 회의 Run Flow Tool은 `flow_tweak_data` 안에 provider-safe한 필수 `question` 하나만 두고, 실행 시 현재 하위 Flow의 유일한 Chat Input ID로 변환합니다. 세 Tool 모두 `return_direct=True`이며, 실제 업무 계산 Component는 등록·활성화 Skill과 필수 action을 다시 확인합니다. 승인·저장·결제·메일·캘린더 변경 기능은 구현하지 않았고 입력 원문은 결과 trace에 복제하지 않고 SHA-256과 글자 수만 기록합니다.

표준 Agent는 연결된 세 Tool을 모두 볼 수 있으므로 카탈로그 지침은 선택을 유도하지만 per-turn hard filter는 아닙니다. 이 때문에 현재 자산은 읽기 전용 교육 데모입니다. 외부 변경 Skill로 확장할 때는 구조화된 Skill 선택 뒤 exact allowlist만 전달하는 Tool Gate 또는 고정 하위 Workflow가 필요합니다.

개별 계산 Component와 Cached Named Run Flow Tool의 Langflow `1.8.2` / LFX `0.3.4` 계약을 확인했습니다. 현재 0.2.0 상위·하위 JSON의 LFX Graph parse, Tool 입력 3개, Skill 전용 2개 Flow Bundle과 격리 donor를 제외한 프로젝트 7개 Flow Bundle 순서·BOM 회귀 검증도 통과했습니다. 특정 모델과 API Key를 Flow JSON에 넣지 않았으므로 실제 LLM의 Skill 선택 정확도와 이름 기반 하위 Flow DB 실행은 사용자 승인 Tool Calling 모델을 연결한 뒤 확인해야 합니다.

## PPT 참고 이미지 기반 HTML 프레젠테이션 Flow

`ppt_reference_html_flow`는 표지 참고 이미지 1개와 본문 참고 이미지 최대 5개를 `Multi Image Base64 Encoder`로 Data URL로 변환합니다. Vision 분석은 이미지에서 색상, 타이포그래피 계열, 여백, 배치와 시각적 모티프만 관찰하며 이미지 속 문구·숫자·명령은 발표 사실로 사용하지 않습니다. 발표 제목·문장·수치와 표·차트 값은 사용자가 제공한 `brief`와 `datasets`에서만 가져옵니다.

0.3.0에서는 Request Builder에 발표 제목·부제·목적·청중·언어·톤·목차·CTA·본문·목표 슬라이드 수를 실제 입력 필드로 노출했습니다. 구조화 `presentation_request.brief`가 있으면 이를 우선하고, 없을 때만 개별 양식과 기존 호환용 `brief`를 병합합니다. 1672×941 PNG 표지 샘플 1개와 본문 샘플 2개를 저장했으며, Flow JSON에는 로컬 절대경로를 넣지 않고 Import 뒤 기존 표지·본문 Encoder에서 직접 업로드하도록 유지했습니다.

LLM은 실행 가능한 HTML·CSS·JavaScript를 만들지 않고 디자인 관찰 JSON과 슬라이드 계획 JSON만 제안합니다. `presentation_design_policy_builder`가 Hallmark식 구성 원칙과 Emil식 모션 기준을 `hallmark-emil-balanced-v1` 계약으로 만들고, Generator에는 계획 힌트로, Normalizer·Renderer·Quality Gate에는 강제 규칙으로 전달합니다. `presentation_plan_normalizer`가 실제 dataset과 column 참조, 역할, 요소·bullet 한도를 검증한 뒤, 독립 Component `html_presentation_renderer` 0.2.0이 escaping과 허용 목록을 적용해 외부 CDN이 없는 16:9 HTML을 결정론적으로 생성합니다.

결과 HTML은 이전·다음·처음, 키보드 탐색, 전체 화면, 인쇄/PDF CSS, semantic table, inline SVG 막대·선·산점도를 지원합니다. 키보드 이동은 즉시 전환하고 포인터 버튼에만 180ms transform·opacity 모션을 적용합니다. hover는 fine-pointer media query로 제한하고 reduced-motion에서는 transition·animation을 비활성화합니다. Quality Gate는 정책 ID, 슬라이드 역할, layout 반복, 장식용 gradient, `transition: all`, `scale(0)`, `ease-in`, 300ms 초과 duration과 media query 누락을 차단합니다.

표·차트 자동 선택은 데이터 의미 타입을 사용합니다. 단일 핵심값은 KPI, 시간+수치는 선 그래프, 범주+수치는 막대그래프, 수치 2개는 산점도, 상세 행·식별자·혼합 타입은 표를 우선합니다. 현재 Runtime이 직접 렌더링하지 않는 히스토그램과 누적 막대 제안은 실제 데이터 표로 안전하게 낮춥니다. LLM이 존재하지 않는 dataset·column을 요청하거나 HTML 품질 검사가 실패하면 발행 payload를 내보내지 않습니다.

전용 Node·Renderer 테스트 24개와 전체 회귀 테스트 98개, Flow 생성기 `--check`, Python compile, 생성 포털, Project 검증과 workspace audit를 통과했습니다. 다만 실제 Vision 모델/API Key를 연결한 Builder E2E와 생성 발표물의 브라우저 시각 회귀는 아직 수행하지 않아 `user_testing`으로 남깁니다.

## 최소 단위 직접 데이터 조회 Component

기존 `reusable_data_flow`는 자연어 요청, Source Catalog, 소스 분기, 다중 요청과 결과 병합을 목표로 한 12개 `0.9.0` Flow 내부 Node와 연결 설계를 보존합니다. 다만 현재 JSON이 이 설계가 아닌 `업무분석flow`로 확인되어 완성 Flow로 취급하지 않으며 import와 Project Bundle에서 제외했습니다. 단일 소스를 직접 조회하려는 경우를 위해 다음 기능 단위 Component ID를 추가했습니다.

| Component | 직접 입력 | 출력 |
| --- | --- | --- |
| `oracle_table_query` | DSN, 계정, SQL, bind 변수, 최대 행 | `data_table: DataFrame` |
| `h_api_table_request` | URL, token, bindParams, 응답 경로, 제한 | `data_table: DataFrame` |
| `datalake_table_query` | Cluster API, HTTP opt-in, 허용 DB host, 인증, CA, SQL, bind 변수, 대기 제한 | `data_table: DataFrame` |
| `goodocs_table_reader` | 문서·사용자·token 입력, 시트, 최대 행 | `data_table: DataFrame` |
| `simple_api_table_request` | URL, GET/POST, header/query/body, 응답 경로, 제한 | `data_table: DataFrame` |

다섯 Component는 성공 여부 envelope, 오류 행, Source Catalog, 다중 요청 병합과 dummy fallback을 출력하지 않습니다. 정상적인 0건 조회는 컬럼을 유지한 빈 `DataFrame`이며, 접속·인증·SQL·응답 형식 실패는 한글 예외로 실행을 중단합니다.

로컬 검증은 실제 LFX template과 주입한 가짜 transport/driver를 이용한 계약 테스트입니다. HTTP 기본 차단, 다른 origin 리다이렉트 차단, Datalake endpoint allowlist와 CA 검증 인자도 확인했습니다. 실제 사내 endpoint와 계정은 호출하지 않았고, GooDocs 실제 모듈도 사용자가 교체해야 합니다. Datalake 실호출 전에는 현재 Langflow Python 환경에 `mysql-connector-python`을 설치하고 사내 CA 파일 경로를 준비해야 합니다.

## 교육자료 보존 방식

기존 `LANGFLOW_INTERNAL_TRAINING_PORTAL.html`을 기준 콘텐츠로 사용했습니다. 기존 sidebar, topbar와 CSS는 그대로 복원하지 않고 Agent Ground의 공통 헤더, 새 목차, 검색 툴바, 카드·표·코드 디자인과 반응형 레이아웃으로 교체했습니다. 기존 교육 본문 영역의 markup, 제목 순서, 링크·자산 참조는 그대로 유지되는지 검증합니다.

- 기존 전체 교육 포털: `html/training/index.html`
- 신규 초보자 학습 안내: `html/training/overview.html`
- 기존 예제·샘플·이미지: `html/training/examples`, `sample_files`, `assets`
- 변환·보존 검증 정보: `training/source/legacy_training_portal_manifest.json`

## 현재 Flow 정보

| Flow | 기존 export 기록 | Node | Edge | 기능 Component | Flow 내부 Node |
| --- | --- | ---: | ---: | ---: | ---: |
| `reusable_data_flow` | Langflow `1.8.2` | 16 | 21 | 0 | 12, 현재 JSON 포함 0 |
| `html_report_flow` | Langflow `1.8.2` | 18 | 22 | 3 | 6 |
| `enterprise_document_rag_flow` | Langflow `1.8.2` | 13 | 10 | 6 | 3 |
| `skill_based_agent_flow` 패키지 | Langflow `1.8.2` | 상위 9 | 상위 8 | 4 참조 | 1, 상·하위 공용 |
| `meeting_action_skill_flow` | Langflow `1.8.2` | 5 | 3 | 위 패키지의 회의 Tool 1 | 위 카탈로그 Node 1 |
| `ppt_reference_html_flow` | Langflow `1.8.2` | 17 | 22 | 이미지 Encoder·HTML Renderer·Publisher 3 | 7 |
| `mail_attachment_summary_flow` | Langflow `1.8.2` | 13 | 11 | DRM 문서 텍스트 추출 1 | 1 |
| `drm_document_text_extraction_flow` | Langflow `1.8.2` | 2 | 1 | DRM 문서 텍스트 추출 1 | 0 |
| `business_agent_design_complete` | Langflow `1.8.2` | 24 | 34 | 공용 Registry와 별도 | 서비스 전용 Standalone 구현 15 |

## 로컬 Langflow 확인

- Langflow Desktop 가상환경 패키지: Langflow `1.8.2`, LFX `0.3.4`
- 2026-07-15 최종 회귀에서 `/api/v1/version`은 Langflow `1.8.2`, `/health`는 `ok`를 반환함. 사용자 요청 없이 라이브 Flow Import·덮어쓰기는 수행하지 않고 Desktop Python의 `lfx 0.3.4` template·Flow 생성 계약을 검증함
- 아래 실제 Upload·Full Build·Chat Output 기록은 앞선 같은 버전 검증에서 확인한 결과이며 이번 분류 변경에서는 Flow JSON 내장 code를 유지함
- `enterprise_document_rag_flow`는 고유한 임시 이름으로 실제 Upload와 Full Build를 수행함
- 실제 Chat Output에서 답변·숫자 인용·문서명·page를 확인함
- 현재 0.2.0 상위 Flow는 9 nodes / 8 edges로 생성하고 LFX에서 7 vertices / Agent Tool 입력 3개를 확인함
- 회의 하위 Flow는 5 nodes / 3 edges로 생성하고 LFX에서 4 vertices / 3 edges를 확인함
- 경비·휴가·회의 결정론적 Component와 Cached Named Run Flow schema는 개별 검증했지만 실제 모델/API Key 및 하위 Flow Agent E2E는 수행하지 않음
- PPT 참고 이미지 Flow는 17 nodes / 22 edges / Custom Python 11개와 내장 Python 원본 일치를 검증했지만, 실제 Vision 모델과 실제 이미지 Builder E2E는 수행하지 않음
- 검증 Flow는 즉시 삭제했고 기존 Flow 수와 잔여 이름을 재확인함
- 다른 기존 Flow와 전체 Bundle은 현재 검증에서 자동 Import하지 않음

## Enterprise Document RAG 검증 범위

- 기본 backend: `payload_lexical_v1`
- persistence: `ephemeral_current_run`
- 기본 신원: `identity_trust=demo`로 명시된 employee demo identity
- 기본 모델: 없음. 외부 API key 없이 deterministic 근거 답변
- 운영 전환 시 필수 교체: 검증된 identity adapter, DLP, persistent index writer, 검색 쿼리 단계 ACL filter가 있는 vector retriever, 승인 LLM adapter

이번 검증은 ACL 선필터·검색·품질 gate·답변·인용 계약이 한 실행에서 동작함을 확인합니다. semantic vector search, 대규모 영속 색인, 운영 SSO와 완전한 DLP까지 구현·승인한 것으로 표시하지 않습니다.

발견한 정상 질문 거절 오탐과 Langflow 1.8.2 Milvus 경계는 `html/troubleshooting/enterprise-document-rag-langflow-1-8-2.html`에 별도로 기록했습니다.

## 사용자 환경에서 남은 확인

- [x] 로컬 Agent Builder 버전 `1.8.2`와 health 확인
- [x] 신규 RAG Component 6개 + 내부 Node 3개가 Langflow 1.8.2 runtime template으로 생성되는지 확인
- [x] 신규 RAG JSON actual upload와 11개 실행 node full build 확인
- [x] 신규 RAG 대표 질문의 답변·인용·문서명·page 확인
- [x] 신규 RAG 허용·거절·ACL·PII·injection·version 회귀 테스트 확인
- [x] 선택 Utility 2개 Component의 Langflow 1.8.2 runtime template과 한글 UI 계약 확인
- [x] 이미지 순서·decode·signature·용량·SVG와 Run Flow 폴더 격리·동명 충돌·재귀·session 회귀 테스트 확인
- [x] Run Flow Tool schema가 바깥 `flow_tweak_data`와 내부 필수 `question` 하나이고 provider 변형 키를 거부하며 현재 Chat Input ID로 변환되는지 확인
- [x] 추천 현황과 Run Flow 상세 페이지를 1280px·375px에서 열어 링크·버튼·가로 넘침 확인
- [x] 직접 조회 5개 Component의 Langflow 1.8.2 template, 한글 입력, `data_table: DataFrame` 단일 출력 확인
- [x] Oracle·Datalake native bind, HTTP 기본 차단, H-API body, API 응답 경로·origin redirect, Datalake host·CA·SQL 경계, GooDocs 교체 구역을 격리 테스트로 확인
- [x] `reusable_data_flow` JSON과 `internal_nodes.json` 불일치 확인, 상태 격리, Project Bundle 제외와 문제 해결 HTML 생성
- [x] 직접 조회 포털을 1280px·375px에서 열고 교육·카탈로그·기존 Flow 양방향 링크와 Datalake 보안 입력 표시 확인
- [x] 경비·휴가·회의 결정론적 결과, 비활성 Skill, 미등록 Tool, 지시 덮어쓰기 문구와 외부 변경 금지 계약 확인
- [x] Cached Named Run Flow Tool의 고정 `question`, 동일 폴더 기본값, 이름 중복·재귀 차단 계약 확인
- [x] 0.2.0 상위 Flow의 직접 Tool 2개 + Run Flow Tool 1개와 동적 `system_prompt` 연결을 실제 LFX Graph로 재확인
- [x] `meeting_action_skill_flow`의 Chat Input 하나·Chat Output 하나와 3개 edge를 실제 LFX Graph로 재확인
- [x] Skill 전용 2개 Flow Bundle과 프로젝트 실행 가능 7개 Flow Bundle의 순서·BOM·원본 일치 재확인
- [x] DRM 문서 텍스트 추출 Component의 실제 1.8.2 template, 다중 FileInput, Secret 입력, Message 출력과 Flow embed 확인
- [x] 가짜 DRM API로 multipart file, Bearer header, empNo, UTF-8·CP949, allowlist, HTTP opt-in과 크기 제한 확인
- [x] PPT 참고 이미지 Flow의 17 nodes / 22 edges, Custom Python 11개, 실제 source embed와 Generator 재현성 확인
- [x] brief·dataset 의미 타입 기반 KPI·표·막대·선·산점도 선택과 존재하지 않는 dataset·column 차단 확인
- [x] HTML Renderer의 escaping, 외부 CDN·URL 차단, semantic table, inline SVG, 키보드·전체화면·인쇄/PDF와 Quality Gate fail-closed 확인
- [x] Hallmark식 역할·밀도·layout 정책과 Emil식 duration·easing·reduced-motion·fine-pointer 정적 위반 검사 확인
- [ ] 두 신규 Python 파일을 Builder에 직접 등록하고 입력·출력 이름 확인
- [ ] 여러 실제 이미지 업로드 후 순서와 Base64/Data URL 결과 확인
- [ ] 실제 같은 폴더 하위 Flow로 Cached Run Flow cold/warm 실행과 session 상속 확인
- [ ] 하위 Flow 재import·Chat Input 교체 후 Tool schema 재구성과 실제 질문 전달 확인
- [ ] 다른 폴더 동명 Flow·간접 순환 Flow가 운영 구성에 없는지 확인
- [ ] 직접 조회 5개 Python 파일을 Builder에 각각 등록하고 `data_table` 포트 확인
- [ ] 실제 조회 전용 Oracle 계정으로 native bind SQL과 0건·제한 초과 결과 확인
- [ ] 실제 H-API endpoint에서 bindParams 순서와 `data.row` 응답 계약 확인
- [ ] Langflow 환경에 승인된 `mysql-connector-python`을 설치하고 Datalake Cluster API·StarRocks 조회 확인
- [ ] `goodocs_table_reader.py`의 교체 구역에 실제 GooDocs 모듈을 넣고 문서·시트 조회 확인
- [ ] 승인된 일반 API에서 GET·POST, 인증 header, 응답 경로와 최대 크기 정책 확인
- [ ] Skill Supervisor Agent에 회사 승인 Tool Calling 모델과 API Key를 연결하고 세 단일 의도의 실제 Tool 선택 확인
- [ ] Skill Agent의 비대상·복합 의도·Prompt Injection 질문에서 Tool 미호출·요청 분리·외부 변경 금지 확인
- [ ] DRM 문서 텍스트 추출 Flow를 Builder에 가져오고 실제 사내 DRM API로 PDF·PPTX·XLSX·DOCX 각각 확인
- [ ] 실제 사용할 기능 Component를 Builder에 등록하고 21개 카탈로그 중 해당 자산의 입력·출력 이름 확인
- [ ] 실행 Flow를 가져온 뒤 포함된 내부 Node의 입력 필드, 고급 설정, Output 이름이 Flow 설명서와 같은지 확인
- [ ] 기존 HTML Report Flow JSON import 후 invalid handle 여부 확인
- [ ] Business Agent Design 개별 JSON import 후 24 nodes / 34 connections 확인
- [ ] Agent Ground 전체 Bundle import 화면에서 실행 가능 7개 Flow 확인
- [ ] PPT 참고 이미지 Flow에 승인된 Vision 모델/API Key, 표지 1개와 본문 이미지 여러 개를 연결해 실제 디자인 분석 확인
- [ ] 샘플 dataset과 실제 업무 dataset으로 생성한 HTML의 표·차트 값, 슬라이드 전환, 전체화면과 인쇄/PDF 결과 확인
- [ ] 실제 업무 설명으로 BEFORE/AFTER 분기와 `개선 설명` 버튼 확인
- [ ] 올바른 `reusable_data_flow` export 제공 또는 신규 재구축 후 대표 catalog와 질문 실행
- [ ] `html_report_flow` 샘플 CSV/JSON 실행과 HTML 확인
- [ ] 선택적으로 Report API 보기·다운로드 링크 확인
- [ ] 운영 RAG 전환 전 실제 identity/DLP/vector store adapter 통합 테스트
- [ ] 노출 가능성이 있는 기존 LLM API key 회전 및 command-line 외 secret 주입 확인
- [ ] 사용자가 모바일과 데스크톱에서 포털 디자인 직접 확인
- [ ] 발견된 차이를 문제 해결 HTML로 기록
- [ ] 사용자의 완료 승인 후 `approved` 전환

정적 검사가 통과했더라도 위 실제 환경 확인 전에는 완료 또는 승인 상태로 표시하지 않습니다.
