# 02 문서 청크·색인 생성

문서를 안정적인 ID의 중복 제거 청크로 나누고 버전·삭제 계획이 포함된 검색용 payload 색인을 만듭니다.

## 상태

- ID: `document_chunk_index_builder`
- 버전: `0.1.0`
- 상태: `user_testing`
- 패키징: `standalone`
- Component 범위: `domain`
- 자격 판정: `qualified_component`
- 사용 범위: `enterprise_document_rag_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| 보호 처리된 문서 | `documents` | `Data, JSON` | False | True | False |
| 청크 글자 수 | `chunk_chars` | `IntInput` | False | False | True |
| 청크 중복 글자 수 | `overlap_chars` | `IntInput` | False | False | True |
| 최대 청크 수 | `max_chunks` | `IntInput` | False | False | True |
| 최신 버전만 유지 | `latest_version_only` | `BoolInput` | False | False | True |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| 문서 색인 | `document_index` | `Data` | `build_document_index` |



## 등록

`document_chunk_index_builder.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
