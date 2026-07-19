# 문서 텍스트 추출 (DRM 자동)

처리 모드에 따라 일반 파일은 로컬에서 읽고 DRM 파일만 API로 보내 평문을 반환합니다.

## 상태

- ID: `drm_document_text_extractor`
- 버전: `0.3.0`
- 상태: `user_testing`
- 패키징: `standalone`
- Component 범위: `general`
- 자격 판정: `qualified_component`
- 사용 범위: `enterprise_utility_components`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| 문서 파일 | `document_files` | `FileInput` | True | False | False |
| EWS 파일 항목 | `file_record` | `DataInput` | False | False | True |
| 처리 모드 | `processing_mode` | `DropdownInput` | False | True | False |
| DRM API 주소 | `drm_api_url` | `MessageTextInput` | False | False | False |
| DRM 토큰 | `drm_token` | `SecretStrInput` | False | False | False |
| 사번 | `employee_no` | `SecretStrInput` | False | False | False |
| 허용 DRM 서버 | `allowed_drm_hosts` | `MessageTextInput` | False | False | False |
| HTTP DRM API 사용 허용 | `allow_insecure_http` | `BoolInput` | False | False | True |
| TLS 인증서 검증 | `verify_tls` | `BoolInput` | False | False | True |
| 제한 시간(초) | `timeout_seconds` | `IntInput` | False | False | True |
| 최대 파일 수 | `max_files` | `IntInput` | False | False | True |
| 파일당 최대 크기(MB) | `max_file_size_mb` | `IntInput` | False | False | True |
| 전체 최대 크기(MB) | `max_total_size_mb` | `IntInput` | False | False | True |
| 파일당 최대 응답 크기(MB) | `max_response_mb` | `IntInput` | False | False | True |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| 추출된 문서 텍스트 | `extracted_text` | `Message` | `build_extracted_text` |
| 처리된 파일 | `processed_file` | `Data` | `build_processed_file` |


## 상세 사용 가이드

[`USAGE_GUIDE.md`](USAGE_GUIDE.md)에서 연결 방법, 운영 조건과 사용자 확인 항목을 확인합니다.


## 등록

`drm_document_text_extractor.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
