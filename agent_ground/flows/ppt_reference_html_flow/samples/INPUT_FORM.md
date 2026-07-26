# Langflow 실제 입력 양식

`ppt_reference_html_flow.json`을 Import하면 `02 발표 요청 정리` Node에 아래 입력 필드가 표시됩니다. 새 Flow JSON에는 예시값이 미리 들어 있으므로 첫 실행에서는 필요한 부분만 교체하면 됩니다.

## Builder 입력 필드

| 화면 필드 | 코드 필드 | 입력 예시 |
| --- | --- | --- |
| 발표 제목 | `presentation_title` | `2026년 상반기 운영 품질 보고` |
| 발표 부제 | `presentation_subtitle` | `처리량 증가와 오류 감소 과제` |
| 발표 목적 | `presentation_purpose` | `경영진이 하반기 개선 우선순위를 결정하도록 돕는다.` |
| 대상 청중 | `target_audience` | `경영진 및 운영 책임자` |
| 발표 언어 | `presentation_language` | `ko` (고급 입력) |
| 발표 톤 | `presentation_tone` | `간결하고 근거 중심` |
| 슬라이드 목차 | `content_outline` | 한 줄에 한 항목씩 입력 |
| 마지막 요청·의사결정 | `call_to_action` | `우선 적용할 개선 과제를 승인한다.` |
| 발표 본문 | `content` | 실제 발표에 사용할 설명과 배경 |
| 데이터셋 JSON | `datasets_json` | `sample_presentation_data.json`의 `datasets` 배열 |
| CSV/JSON 데이터 파일 | `dataset_files` | 실제 행 데이터 파일 여러 개 |
| 목표 슬라이드 수 | `target_slide_count` | `8` |

`기존 Brief 문자열·JSON`은 이전 버전 호환 입력입니다. 위 개별 입력 필드를 사용하면 비워 둡니다. `구조화 발표 요청` Data에 `brief`가 이미 들어 있으면 해당 구조화 brief를 우선합니다.

## 이미지 업로드

### 표지 Encoder

`reference_images/reference_cover_navy_teal.png` 한 장을 업로드합니다.

- `output_format`: `data_url`
- `error_policy`: `reject_batch`
- `max_files`: `1`

### 본문 Encoder

다음 순서로 두 장을 업로드합니다.

1. `reference_images/reference_body_trend.png`
2. `reference_images/reference_body_comparison_table.png`

- `output_format`: `data_url`
- `error_policy`: `skip_invalid`
- 첫 이미지는 일반 데이터·차트 본문의 우선 참고 자료로 사용합니다.
- 두 번째 이미지는 비교·표 중심 변형의 참고 자료로 사용합니다.

## 데이터 입력 방법

둘 중 하나만 선택합니다.

1. `sample_presentation_data.json`의 `datasets` 배열을 `데이터셋 JSON`에 붙여 넣습니다.
2. `sample_presentation_data.json`, 별도 CSV 또는 JSON 파일을 `CSV/JSON 데이터 파일`에 업로드합니다.

동일한 데이터를 JSON 입력과 파일 업로드에 동시에 넣으면 데이터셋이 중복될 수 있습니다.

## 실행 전 확인

- Language Model Node에 Vision 이미지 입력이 가능한 승인 모델과 API Key를 설정합니다.
- 이미지에 실제 회사명·기밀정보·실적값을 넣지 않습니다.
- 발표의 실제 문장과 숫자는 `brief`, `content`, `datasets`에서만 가져옵니다.
- 이미지 파일은 Flow JSON 안에 절대경로로 고정하지 않았습니다. Import 후 Encoder에서 직접 업로드해야 다른 PC에서도 안전하게 사용할 수 있습니다.
