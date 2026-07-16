# 다중 MSG·DRM 첨부파일 요약 Flow 연결 가이드

## 연결표

| 순서 | From | Output | To | Input | 타입 |
| --- | --- | --- | --- | --- | --- |
| 1 | 01 MSG 본문·첨부파일 분해 | `extracted_items` | 02 MSG 항목별 반복 | `data` | DataFrame |
| 2 | 02 MSG 항목별 반복 | `item` | 03 첨부파일 DRM 해제 | `file_record` | Data |
| 3 | 03 첨부파일 DRM 해제 | `unlocked_file` | 04 DRM 해제 파일 읽기 | `file_path` | Data |
| 4 | 04 DRM 해제 파일 읽기 | `dataframe` | 05 메일 항목 내용 정리 | `input_data` | DataFrame |
| 5 | 05 메일 항목 내용 정리 | `parsed_text` | 06 메일 항목별 요약 모델 | `input_value` | Message |
| 6 | 06 메일 항목별 요약 모델 | `text_output` | 02 MSG 항목별 반복 | Loop return | Message |
| 7 | 02 MSG 항목별 반복 | `done` | 07 메일 항목별 요약 합치기 | `input_data` | DataFrame |
| 8 | 07 메일 항목별 요약 합치기 | `parsed_text` | 08 MSG 통합 요약 프롬프트 | `attachment_summaries` | Message |
| 9 | MSG 메일 정리 요청 | `message` | 08 MSG 통합 요약 프롬프트 | `user_request` | Message |
| 10 | 08 MSG 통합 요약 프롬프트 | `prompt` | 09 전체 MSG 통합 요약 모델 | `input_value` | Message |
| 11 | 09 전체 MSG 통합 요약 모델 | `text_output` | 10 MSG 메일 정리 결과 | `input_value` | Message |

## MSG Extractor 설정

| 항목 | 기본값 | 설명 |
| --- | --- | --- |
| Outlook MSG 파일 | 사용자 다중 업로드 | `.msg`만 허용 |
| 인라인 이미지 포함 | `false` | 서명 로고 같은 hidden/CID 이미지 제외 |
| 최대 MSG 수 | `10` | 서비스 정책에 맞게 조정 |
| MSG당 최대 크기 | `50MB` | 과대 입력 차단 |
| 메일당 최대 첨부 | `50` | 비정상 컨테이너 방어 |
| 전체 추출 최대 크기 | `200MB` | 압축·첨부 폭증 방어 |

## DRM 어댑터 계약

입력 `Data`의 주요 필드:

```json
{
  "file_path": "MSG에서 분리한 작업 파일 경로",
  "file_name": "첨부파일명",
  "source_kind": "mail_body | msg_attachment | extraction_error",
  "parent_msg": "원본.msg",
  "mail_subject": "메일 제목",
  "drm_status": "pending"
}
```

`source_kind=msg_attachment`인 경우에만 `company_drm_unlock`을 호출합니다. 함수는 원본을 수정하지 않고 지정된 `destination_path`를 생성한 다음 `unlocked` 또는 `not_protected`를 반환해야 합니다.

출력 `Data`는 기존 메타데이터를 유지하고 다음 필드를 갱신합니다.

```json
{
  "original_file_path": "원본 작업 경로",
  "file_path": "DRM 처리된 새 작업 경로",
  "drm_status": "unlocked | not_protected",
  "drm_error": ""
}
```

## Read File 설정

| 항목 | 값 | 이유 |
| --- | --- | --- |
| 입력 | `Server File Path` | DRM 출력 `Data.file_path` 소비 |
| Storage Location | `Local` | 외부 저장소 미사용 |
| Advanced Parser | `true` | PDF·Office·이미지 처리 |
| Pipeline | `standard` | 로컬 문서 파싱 |
| OCR Engine | `easyocr` | 스캔 이미지 텍스트 추출 |
| Markdown Export | `true` | 항목당 읽기 쉬운 텍스트 생성 |
| Silent Errors | `false` | 읽기 실패를 숨기지 않음 |
| Delete After Processing | `true` | DRM 작업 파일 잔존 최소화 |

## 모델 설정

두 Language Model에 동일한 조직 승인 모델을 선택하고 외부 Web·Tool 호출은 비활성화합니다. Temperature는 `0.1`이며 API key는 Global Variable 또는 조직 Secret으로 주입합니다.
