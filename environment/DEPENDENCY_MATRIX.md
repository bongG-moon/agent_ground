# Dependency Matrix

기능 Component의 정확한 의존성은 해당 `manifest.json`, Flow 내부 Node는 소유 Flow의 `internal_nodes.json`과 Python 원본이 기준입니다.

| 분류 | 주요 자산 | 비고 |
| --- | --- | --- |
| Langflow runtime | 전체 기능 Component와 Flow 내부 Node | `lfx` import가 필요합니다. |
| Oracle | `oracle_table_query`, 재사용 데이터 Flow의 `oracle_data` 내부 Node | 실제 조회 경로에서 `oracledb`가 필요할 수 있습니다. |
| Datalake | `datalake_table_query`, 재사용 데이터 Flow의 `datalake_data` 내부 Node | 실제 조회 경로에서 `aiohttp`, `mysql-connector-python`, `pandas`가 필요할 수 있습니다. |
| MongoDB | 재사용 데이터 Flow의 catalog store/loader 내부 Node | 실제 저장·조회에 `pymongo`가 필요합니다. |
| HTTP/Report API | H-API, publisher | 서버 네트워크 정책과 API 접근 권한을 확인합니다. |
| Report API server | `html_report_flow/report_api` | FastAPI 기반 보조 서비스이며 Langflow Component와 별도 실행됩니다. |
| Enterprise Document RAG 기본 경로 | 기능 Component 6개 + Flow 내부 Node 3개 | Python stdlib + `lfx`만 사용하며 LLM key·Vector DB가 필요하지 않습니다. |
| Enterprise Document RAG PDF 확장 | 별도 parser/OCR upstream | 현재 runtime에는 `pypdf`, Docling이 있지만 scanned PDF와 page 경계는 별도 통합 검증이 필요합니다. |
| Enterprise Document RAG 운영 색인 | 전용 vector-store adapter | 기본 Milvus 1.8.2 node는 ACL search filter와 stable-ID replacement 계약을 충족하지 않습니다. |
| PPT 참조 이미지 HTML 프레젠테이션 | `multi_image_base64_encoder`, Flow 내부 Node 6개, `html_presentation_renderer` | 기본 렌더링·데이터 바인딩은 Python stdlib + `lfx`만 사용합니다. 실제 참고 이미지 분석에는 Data URL 이미지를 지원하는 승인된 멀티모달 LanguageModel과 API Key가 필요합니다. |
| 프레젠테이션 데이터 파일 | `presentation_request_builder` | JSON·UTF-8 CSV는 기본 지원합니다. XLSX는 0.1.0 입력 계약에 포함하지 않으며 먼저 JSON/CSV로 변환합니다. |

외부 패키지는 Python 원본에 import 구문이 있다는 이유만으로 설치되어 있다고 가정하지 않습니다. `user_testing` 단계에서 실제 Agent Builder 서버의 사용 가능 여부를 확인합니다.
