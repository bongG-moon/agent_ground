# 01 데이터 구조 분석

리포트에 쓸 컬럼 유형, 숫자/범주/날짜 후보, 데이터 품질 신호를 요약합니다.

## 상태

- ID: `html_report_data_profile_builder`
- 버전: `0.9.0`
- 상태: `user_testing`
- 패키징: `standalone`
- Component 범위: `domain`
- 자격 판정: `qualified_component`
- 사용 범위: `html_report_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| 요청 데이터 | `analysis_payload` | `DataInput` | False | True | False |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| 데이터 분석 결과 | `data_profile` | `Data` | `build_profile` |



## 등록

`html_report_data_profile_builder.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
