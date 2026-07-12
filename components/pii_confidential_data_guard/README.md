# 01 PII & Confidential Data Guard

Redact or block common sensitive patterns before documents enter the retrieval index.

## 상태

- ID: `pii_confidential_data_guard`
- 버전: `0.1.0`
- 상태: `user_testing`
- Standalone: `true`
- 사용 범위: `enterprise_document_rag_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| Documents | `documents` | `Data, JSON` | False | True | False |
| Guard Mode | `guard_mode` | `DropdownInput` | False | False | False |
| Detect Email Addresses | `detect_emails` | `BoolInput` | False | False | True |
| Detect Phone Numbers | `detect_phone_numbers` | `BoolInput` | False | False | True |
| Detect National IDs | `detect_national_ids` | `BoolInput` | False | False | True |
| Detect Employee IDs | `detect_employee_ids` | `BoolInput` | False | False | True |
| Detect Secret-like Values | `detect_secrets` | `BoolInput` | False | False | True |
| Upgrade Classification When Detected | `upgrade_classification` | `BoolInput` | False | False | True |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| Safe Documents | `safe_documents` | `Data` | `build_safe_documents` |



## 등록

`pii_confidential_data_guard.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
