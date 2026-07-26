# 다중 이미지 Base64 인코더

여러 이미지 파일을 검증하고 입력 순서대로 Base64 또는 Data URL 목록으로 변환합니다.

## 상태

- ID: `multi_image_base64_encoder`
- 버전: `0.1.0`
- 상태: `user_testing`
- 패키징: `standalone`
- Component 범위: `general`
- 자격 판정: `qualified_component`
- 사용 범위: `사내 공용 독립 Component (특정 Flow 미지정)`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| 이미지 파일 | `image_files` | `FileInput` | True | True | False |
| 출력 형식 | `output_format` | `DropdownInput` | False | False | False |
| 오류 처리 방식 | `error_policy` | `DropdownInput` | False | False | False |
| 엄격한 정적 SVG 허용 | `allow_svg` | `BoolInput` | False | False | True |
| 최대 파일 수 | `max_files` | `IntInput` | False | False | True |
| 파일당 최대 크기(MB) | `max_file_size_mb` | `IntInput` | False | False | True |
| 전체 원본 최대 크기(MB) | `max_total_size_mb` | `IntInput` | False | False | True |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| 인코딩된 이미지 | `encoded_images` | `Data` | `encode_images` |


## 상세 사용 가이드

[`USAGE_GUIDE.md`](USAGE_GUIDE.md)에서 연결 방법, 운영 조건과 사용자 확인 항목을 확인합니다.


## 등록

`multi_image_base64_encoder.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
