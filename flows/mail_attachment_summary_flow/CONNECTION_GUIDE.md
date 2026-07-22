# EWS·DRM Outlook 메일 요약 Flow 연결 가이드

## 연결표

| 순서 | From | Output | To | Input | 타입 |
| --- | --- | --- | --- | --- | --- |
| 1 | 01 Outlook 메일·첨부 읽기 (EWS) | `mail_items` | 02 EWS 메일 항목별 반복 | `data` | DataFrame |
| 2 | 02 EWS 메일 항목별 반복 | `item` | 03 EWS 첨부 처리 모드·텍스트 추출 | `file_record` | Data |
| 3 | 03 EWS 첨부 처리 모드·텍스트 추출 | `processed_file` | 04 원본 또는 DRM 평문 파일 읽기 | `file_path` | Data |
| 4 | 04 원본 또는 DRM 평문 파일 읽기 | `dataframe` | 05 메일 항목 내용 정리 | `input_data` | DataFrame |
| 5 | 05 메일 항목 내용 정리 | `parsed_text` | 06 메일 항목별 요약 모델 | `input_value` | Message |
| 6 | 06 메일 항목별 요약 모델 | `text_output` | 02 EWS 메일 항목별 반복 | Loop return | Message |
| 7 | 02 EWS 메일 항목별 반복 | `done` | 07 메일 항목별 요약 합치기 | `input_data` | DataFrame |
| 8 | 07 메일 항목별 요약 합치기 | `parsed_text` | 08 EWS 메일 통합 요약 프롬프트 | `attachment_summaries` | Message |
| 9 | EWS 메일 정리 요청 | `message` | 08 EWS 메일 통합 요약 프롬프트 | `user_request` | Message |
| 10 | 08 EWS 메일 통합 요약 프롬프트 | `prompt` | 09 전체 EWS 메일 통합 요약 모델 | `input_value` | Message |
| 11 | 09 전체 EWS 메일 통합 요약 모델 | `text_output` | 10 EWS 메일 정리 결과 | `input_value` | Message |

`04 → 05`는 양쪽을 모두 `DataFrame` 단일 타입으로 고정합니다. Langflow 1.8.2 기본 Read File은 서버 경로 입력에서 캔버스를 다시 열면 출력을 `Raw Content(Message)`로 복원할 수 있습니다. 04는 Read File의 문서·이미지 파싱 기능을 상속하되 동적 출력 변경만 막은 `StableMailFileReader`이므로 `dataframe` 포트와 연결선이 유지됩니다. 05도 기본 Parser 복제본이 아니라 Flow에 내장된 `MailDataFrameFormatter`이므로 설치 버전별 `JSON or Table`/`Data or DataFrame` 포트명 차이에 영향을 받지 않습니다.

테스트용 `mail_attachment_summary_dummy_flow.json`에서는 1번 연결의 출발점만 `01T 테스트 EWS 메일·첨부 데이터.mail_items(DataFrame)`로 바뀌며 이후 연결과 타입은 운영 Flow와 같습니다.

## DRM 평문 출력 계약

`source_kind=ews_attachment`이면 처리 모드에 따라 원본 파일 경로를 유지하거나 DRM API 평문을 새 TXT 파일로 저장합니다.

```json
{
  "original_file_path": "<temporary>/mail_001/001_report.xlsx",
  "original_file_name": "report.xlsx",
  "file_path": "<temporary>/report_drm_text.txt",
  "file_name": "report_drm_text.txt",
  "content_type": "text/plain",
  "drm_status": "not_required | bypassed_by_mode | text_extracted",
  "processing_mode": "자동(로컬 우선)",
  "processing_path": "original_file | drm_api",
  "drm_response_encoding": "utf-8",
  "drm_response_bytes": 12345,
  "drm_text_char_count": 5678,
  "drm_error": ""
}
```

`not_required`와 `bypassed_by_mode`는 `file_path`가 원본을 가리키며, `text_extracted`만 DRM 평문 TXT를 가리킵니다. 메일 제목·발신자·수신 시각·첨부 순번 등 기존 EWS 메타데이터는 그대로 유지합니다. 메일 본문과 `extraction_error` 안내 파일은 `drm_status=not_applicable`로 통과합니다.

## 안정형 Read File 설정

| 항목 | 값 |
| --- | --- |
| 입력 | DRM 출력의 `file_path` |
| Storage Location | `Local` |
| Advanced Parser | `true` |
| Pipeline | `standard` |
| OCR Engine | `easyocr` |
| Markdown Export | `true` |
| Silent Errors | `false` |
| Delete After Processing | `true` |

DRM 처리 첨부와 메일 본문 TXT는 04가 자동으로 표준 텍스트 파서에 전달하므로 Docling의 `File format not allowed` 오류가 발생하지 않습니다. PDF·Office·이미지 원본은 Advanced Parser와 OCR 설정을 사용합니다.
