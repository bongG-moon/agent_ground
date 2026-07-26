# 01 개인정보·기밀정보 보호

문서가 검색 색인에 들어가기 전에 일반적인 개인정보와 비밀값 패턴을 마스킹하거나 차단합니다.

## 상태

- ID: `pii_confidential_data_guard`
- 버전: `0.1.0`
- 상태: `user_testing`
- 패키징: `standalone`
- Component 범위: `domain`
- 자격 판정: `qualified_component`
- 사용 범위: `enterprise_document_rag_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| 문서 | `documents` | `Data, JSON` | False | True | False |
| 보호 방식 | `guard_mode` | `DropdownInput` | False | False | False |
| 이메일 주소 탐지 | `detect_emails` | `BoolInput` | False | False | True |
| 전화번호 탐지 | `detect_phone_numbers` | `BoolInput` | False | False | True |
| 주민등록번호 형태 탐지 | `detect_national_ids` | `BoolInput` | False | False | True |
| 사번 형태 탐지 | `detect_employee_ids` | `BoolInput` | False | False | True |
| 비밀값 형태 탐지 | `detect_secrets` | `BoolInput` | False | False | True |
| 탐지 시 보안 등급 상향 | `upgrade_classification` | `BoolInput` | False | False | True |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| 보호 처리된 문서 | `safe_documents` | `Data` | `build_safe_documents` |



## 등록

`pii_confidential_data_guard.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
