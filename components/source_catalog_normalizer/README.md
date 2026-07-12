# Catalog Normalizer

LLM이 만든 source_catalog 후보를 저장하지 않고 조회 Flow용 형태로 정규화합니다.

## 상태

- ID: `source_catalog_normalizer`
- 버전: `0.9.0`
- 상태: `user_testing`
- Standalone: `true`
- 사용 범위: `reusable_data_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| LLM Result | `llm_result` | `Message, Data, Text, JSON` | False | False | False |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| Catalog(Text직접연결용) | `catalog_message` | `Message` | `build_catalog_message` |
| Catalog(DB저장용) | `catalog_data` | `Data` | `build_catalog_data` |



## 등록

`source_catalog_normalizer.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
