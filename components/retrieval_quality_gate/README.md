# Retrieval Quality Gate

ACL 적용 여부·검색 점수·문서 지시 신호를 확인하고 근거가 부족하면 LLM 앞에서 답변을 차단합니다.

## 상태

- ID: `retrieval_quality_gate`
- 버전: `0.1.0`
- 상태: `user_testing`
- Standalone: `true`
- 사용 범위: `enterprise_document_rag_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| Retrieval | `retrieval` | `Data, JSON` | False | True | False |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| Gate | `gate` | `Data` | `build_gate` |



## 등록

`retrieval_quality_gate.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
