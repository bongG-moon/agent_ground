# DRM text API Component 계약

공용 구현: `components/drm_document_text_extractor/drm_document_text_extractor.py`

## 요청

```text
POST <DRM API URL>?empNo=<employee number>
Authorization: Bearer <token>
Content-Type: multipart/form-data
file: (<original filename>, <binary>, application/octet-stream)
timeout: 180초
```

## EWS 입력과 출력

- 입력: `file_path`, `file_name`, `source_kind`와 메일 메타데이터가 있는 `Data`
- `mail_body`, `extraction_error`: API 호출 없이 원본 경로 통과
- `ews_attachment`: API가 반환한 평문을 별도 UTF-8 TXT 작업 파일로 저장
- 출력 상태: `text_extracted`
- API 실패: 보호 원본으로 fallback하지 않고 예외

## 지원 확장자

- 문서: PDF, PPT/PPTX, XLS/XLSX, DOC/DOCX, HWP/HWPX, RTF
- 텍스트: TXT, CSV
- 이미지: PNG, JPG/JPEG, BMP, TIF/TIFF

지원 목록은 클라이언트가 전송을 허용하는 범위입니다. 실제 평문 반환과 이미지 OCR 가능 여부는 DRM API 서버 구현에 따릅니다.

## 보안 조건

- API URL host는 `허용 DRM 서버` 목록과 일치해야 합니다.
- redirect는 따라가지 않습니다.
- HTTP endpoint는 기본 차단하며 승인된 폐쇄망에서만 명시적으로 허용합니다.
- 토큰·사번·API 응답 본문·문서 본문을 오류나 상태에 남기지 않습니다.
- 파일당 업로드 크기와 응답 크기를 제한합니다.

