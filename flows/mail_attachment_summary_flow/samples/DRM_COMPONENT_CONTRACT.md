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
- `자동(로컬 우선)`: 일반 파일은 원본 경로 통과, 로컬 판별 실패 파일만 API 처리
- `항상 DRM API`: API가 반환한 평문을 별도 UTF-8 TXT 작업 파일로 저장
- `DRM 미사용`: API 호출 없이 모든 첨부의 원본 경로 통과
- JPG/JPEG: 연결된 `LanguageModel`에 멀티모달 Data URL로 전달하고 UTF-8 TXT 생성
- ZIP·RAR·7Z·TAR·GZ 등 미지원 형식: 압축을 열지 않고 안내 TXT 생성
- 출력 경로: `original_file`, `drm_api`, `vision_model`, `vision_failed`, `skipped_unsupported`
- 출력 상태: `not_required`, `bypassed_by_mode`, `text_extracted`, `not_applicable`
- API 실패: 보호 원본으로 fallback하지 않고 예외

## 지원 확장자

- 문서: PDF, PPT/PPTX, XLS/XLSX, DOC/DOCX, HWP/HWPX, RTF
- 텍스트: TXT, CSV
- 이미지: PNG, JPG/JPEG, BMP, TIF/TIFF

지원 목록은 클라이언트가 전송을 허용하는 범위입니다. 실제 평문 반환과 이미지 OCR 가능 여부는 DRM API 서버 구현에 따릅니다.

자동 로컬 판별 범위는 PDF, DOCX, PPTX, XLSX, TXT, CSV입니다. 판별은 공식 DRM 상태 조회가 아니라 로컬 파서 성공 여부를 사용합니다.

## 보안 조건

- API 주소는 `http` 또는 `https` 전체 URL이어야 하며 URL 내부 인증정보와 fragment는 허용하지 않습니다.
- redirect는 따라가지 않습니다.
- HTTP endpoint는 기본 차단하며 승인된 폐쇄망에서만 명시적으로 허용합니다.
- 토큰·사번·API 응답 본문·문서 본문을 오류나 상태에 남기지 않습니다.
- 파일당 업로드 크기와 응답 크기를 제한합니다.
