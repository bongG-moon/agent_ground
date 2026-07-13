# HTML 프레젠테이션 렌더러

검증된 슬라이드 계획을 탐색·인쇄 가능한 독립 16:9 HTML 프레젠테이션으로 변환합니다.

## 상태

- ID: `html_presentation_renderer`
- 버전: `0.1.0`
- 상태: `user_testing`
- 패키징: `standalone`
- Component 범위: `domain`
- 자격 판정: `qualified_component`
- 사용 범위: `ppt_reference_html_flow`

## 입력

| 화면 이름 | 코드 이름 | 타입 | 목록 | 필수 | 고급 |
| --- | --- | --- | --- | --- | --- |
| 검증된 프레젠테이션 계획 | `presentation_plan` | `DataInput` | False | True | False |

## 출력

| 화면 이름 | 코드 이름 | 타입 | 실행 method |
| --- | --- | --- | --- |
| HTML 프레젠테이션 결과 | `presentation_artifact` | `Data` | `build_artifact` |



## 등록

`html_presentation_renderer.py` 파일 하나를 Agent Builder의 Custom Component로 등록합니다. 이 파일은 형제 모듈 import를 사용하지 않습니다.

검증 결과는 manifest의 `verified_environment`를 기준으로 확인합니다. 사용자 완료 승인 전까지 추천 카탈로그에는 포함하지 않습니다.
