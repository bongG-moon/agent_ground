# 00 Document Input Normalizer

Normalize Data, Message, text, or JSON into documents with citation and ACL metadata.

## 상태

- ID: `document_input_normalizer`
- 버전: `0.1.0`
- 상태: `user_testing`
- Standalone: `true`
- 사용 범위: `enterprise_document_rag_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| Document Input | `document_input` | `Data, Message, Text, JSON` | False | False | False |
| Use Demo Corpus When Empty | `use_demo_corpus` | `BoolInput` | False | False | False |
| Default Source Name | `default_source_name` | `MessageTextInput` | False | False | True |
| Default Tenant ID | `default_tenant_id` | `MessageTextInput` | False | False | True |
| Default Classification | `default_classification` | `DropdownInput` | False | False | True |
| Default Allowed Roles | `default_allowed_roles` | `MessageTextInput` | False | False | True |
| Default Allowed Groups | `default_allowed_groups` | `MessageTextInput` | False | False | True |
| Page Break Marker | `page_break_marker` | `MessageTextInput` | False | False | True |
| Max Documents or Pages | `max_documents` | `IntInput` | False | False | True |
| Max Characters per Document | `max_chars_per_document` | `IntInput` | False | False | True |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| Documents | `documents` | `Data` | `build_documents` |



## 등록

`document_input_normalizer.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
