# Catalog MongoDB Store

source_catalog를 source 이름 기준으로 MongoDB에 저장하거나 업데이트합니다.

## 상태

- ID: `source_catalog_mongodb_store`
- 버전: `0.9.0`
- 상태: `user_testing`
- Standalone: `true`
- 사용 범위: `reusable_data_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| Catalog Data | `source_catalog_data` | `Data, JSON, Message, Text` | False | False | False |
| Mongo URI | `mongo_uri` | `MessageTextInput` | False | False | False |
| DB Name | `db_name` | `MessageTextInput` | False | False | False |
| Collection Name | `collection_name` | `MessageTextInput` | False | False | False |
| Timeout MS | `timeout_ms` | `MessageTextInput` | False | False | True |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| Catalog Data | `catalog_data` | `Data` | `build_catalog_data` |
| Store Result | `store_result` | `Data` | `build_store_result` |



## 등록

`source_catalog_mongodb_store.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
