# LLM Caller

Prompt Template 결과를 LLM에 전달하고, 다음 Normalizer가 읽을 llm_result를 반환합니다.

## 상태

- ID: `llm_caller`
- 버전: `0.9.0`
- 상태: `user_testing`
- Standalone: `true`
- 사용 범위: `reusable_data_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| Prompt | `prompt` | `Message, Data, Text` | False | False | False |
| LLM API Key | `llm_api_key` | `MessageTextInput` | False | False | True |
| Model Name | `model_name` | `MessageTextInput` | False | False | True |
| Temperature | `temperature` | `MessageTextInput` | False | False | True |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| LLM Result | `llm_result` | `Data` | `build_result` |



## 등록

`llm_caller.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
