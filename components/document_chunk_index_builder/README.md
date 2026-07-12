# 02 Document Chunk Index Builder

Build stable, deduplicated chunks and version/tombstone operations for a model-free payload index.

## 상태

- ID: `document_chunk_index_builder`
- 버전: `0.1.0`
- 상태: `user_testing`
- Standalone: `true`
- 사용 범위: `enterprise_document_rag_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| Safe Documents | `documents` | `Data, JSON` | False | True | False |
| Chunk Characters | `chunk_chars` | `IntInput` | False | False | True |
| Overlap Characters | `overlap_chars` | `IntInput` | False | False | True |
| Maximum Chunks | `max_chunks` | `IntInput` | False | False | True |
| Keep Latest Version Only | `latest_version_only` | `BoolInput` | False | False | True |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| Document Index | `document_index` | `Data` | `build_document_index` |



## 등록

`document_chunk_index_builder.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
