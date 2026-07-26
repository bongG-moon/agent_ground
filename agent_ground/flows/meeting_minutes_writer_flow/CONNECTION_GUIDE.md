# 사용자 스타일 기반 회의록 작성 Flow 연결 가이드

## 연결표

| # | From Node | Output | To Node | Input | 화면 타입 |
| ---: | --- | --- | --- | --- | --- |
| 1 | 01 과거 녹취 TXT 읽기 | `extracted_text` | 04 회의록 작성 요청 정리 | `historical_transcripts` | Message |
| 2 | 02 과거 실제 회의록 읽기 | `extracted_text` | 04 회의록 작성 요청 정리 | `historical_minutes` | Message |
| 3 | 03 현재 녹취 TXT 읽기 | `extracted_text` | 04 회의록 작성 요청 정리 | `current_transcript` | Message |
| 4 | 추가 작성 지시 | `message` | 04 회의록 작성 요청 정리 | `additional_instructions` | Message |
| 5 | 04 회의록 작성 요청 정리 | `request` | 05 사용자 회의록 스타일 분석 | `request` | JSON/Data |
| 6 | 회의록 작성·검토 Language Model | `model_output` | 05 사용자 회의록 스타일 분석 | `model` | LanguageModel |
| 7 | 04 회의록 작성 요청 정리 | `request` | 06 현재 회의록 초안 작성 | `request` | JSON/Data |
| 8 | 05 사용자 회의록 스타일 분석 | `style_profile` | 06 현재 회의록 초안 작성 | `style_profile` | JSON/Data |
| 9 | 회의록 작성·검토 Language Model | `model_output` | 06 현재 회의록 초안 작성 | `model` | LanguageModel |
| 10 | 04 회의록 작성 요청 정리 | `request` | 07 사실·지시·스타일 최종 검토 | `request` | JSON/Data |
| 11 | 05 사용자 회의록 스타일 분석 | `style_profile` | 07 사실·지시·스타일 최종 검토 | `style_profile` | JSON/Data |
| 12 | 06 현재 회의록 초안 작성 | `draft` | 07 사실·지시·스타일 최종 검토 | `draft` | Message |
| 13 | 회의록 작성·검토 Language Model | `model_output` | 07 사실·지시·스타일 최종 검토 | `model` | LanguageModel |
| 14 | 07 사실·지시·스타일 최종 검토 | `final_minutes` | 08 최종 회의록 출력 | `input_value` | Message |

`07`의 `quality_report: Data(JSON)`는 기본 Chat Output과 연결하지 않습니다. 필요하면 별도 Data/JSON 출력 또는 후속 저장 단계에 연결해 확인합니다.

기본 `08 최종 회의록 출력`은 `07`의 `final_minutes: Message`만 받아 제목부터 시작하는 Markdown 본문을 표시합니다. 검토 모델이 내부적으로 JSON을 반환해도 `07`이 `final_minutes` 필드만 추출하므로 기본 화면에 JSON wrapper, `corrections`, `remaining_checks`가 함께 나오면 안 됩니다.

## 파일 입력 설정

| Node | 기본 처리 모드 | 최대 파일 | 용도 |
| --- | --- | ---: | --- |
| 01 과거 녹취 TXT 읽기 | `DRM 미사용` | 10 | 스타일 비교용 과거 녹취 |
| 02 과거 실제 회의록 읽기 | `자동(로컬 우선)` | 10 | TXT·Word 회의록, 필요 시 DRM fallback |
| 03 현재 녹취 TXT 읽기 | `DRM 미사용` | 1 | 지금 작성할 회의의 사실 근거 |

과거 두 목록은 업로드 순서로 짝지어집니다. 연결선은 목록을 재정렬하지 않습니다.

```text
01의 첫 번째 파일 ↔ 02의 첫 번째 파일
01의 두 번째 파일 ↔ 02의 두 번째 파일
...
01의 열 번째 파일 ↔ 02의 열 번째 파일
```

- 과거 녹취와 과거 회의록은 각각 1~10개를 다중 선택할 수 있습니다.
- 두 입력의 파일 수가 다르면 `04 회의록 작성 요청 정리`가 오류로 중단합니다.
- 파일명을 기준으로 자동 짝짓기하지 않으므로 업로드 순서를 직접 맞춥니다.
- 제공 샘플에서는 `historical_minutes_01.docx`, `historical_minutes_02.docx`를 실제 Word 입력 예시로 사용할 수 있습니다.
- 같은 회의의 `.docx`와 `.txt`는 내용이 같으므로 둘 중 하나만 업로드합니다.
- `03 현재 녹취 TXT 읽기`는 정확히 한 개만 허용하며 여러 현재 회의를 일괄 생성하지 않습니다.
- 기본 문자 제한은 과거 파일당 60,000자, 과거 예시 전체 300,000자, 현재 녹취 120,000자입니다. 제한에 걸린 경우 경고를 확인하고 예시 수나 파일 분량을 조정합니다.

## 실행 순서

Langflow는 연결 의존성에 따라 아래 순서를 따릅니다.

```text
파일 추출 3개 + 추가 작성 지시
  -> 요청 정리
  -> 스타일 분석
  -> 초안 작성
  -> 최종 검토
  -> Chat Output
```

세 LLM 단계는 같은 Language Model 객체를 사용하지만 각각 별도 호출입니다. 모델의 Context Window, 비용과 지연을 운영 환경에서 확인해야 합니다.

## 변경·저장 경계

이 Flow는 최종 회의록을 Message로 반환할 뿐 파일 저장, 메일 발송, 결재, 시스템 등록을 수행하지 않습니다. Word 파일 생성이나 사내 시스템 저장은 담당자 확인 이후 별도 승인형 Flow로 연결합니다.
