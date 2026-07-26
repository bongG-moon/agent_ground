# 00 문서 입력 정규화

Data, Message, 텍스트 또는 JSON을 인용·접근권한 메타데이터가 포함된 문서 목록으로 정규화합니다.

## 상태

- ID: `document_input_normalizer`
- 버전: `0.1.0`
- 상태: `user_testing`
- 패키징: `standalone`
- Component 범위: `domain`
- 자격 판정: `qualified_component`
- 사용 범위: `enterprise_document_rag_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| 문서 입력 | `document_input` | `Data, Message, Text, JSON` | False | False | False |
| 빈 입력 시 데모 문서 사용 | `use_demo_corpus` | `BoolInput` | False | False | False |
| 기본 소스 이름 | `default_source_name` | `MessageTextInput` | False | False | True |
| 기본 테넌트 ID | `default_tenant_id` | `MessageTextInput` | False | False | True |
| 기본 보안 등급 | `default_classification` | `DropdownInput` | False | False | True |
| 기본 허용 역할 | `default_allowed_roles` | `MessageTextInput` | False | False | True |
| 기본 허용 그룹 | `default_allowed_groups` | `MessageTextInput` | False | False | True |
| 페이지 구분 표식 | `page_break_marker` | `MessageTextInput` | False | False | True |
| 최대 문서·페이지 수 | `max_documents` | `IntInput` | False | False | True |
| 문서당 최대 글자 수 | `max_chars_per_document` | `IntInput` | False | False | True |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| 정규화 문서 | `documents` | `Data` | `build_documents` |



## 등록

`document_input_normalizer.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
