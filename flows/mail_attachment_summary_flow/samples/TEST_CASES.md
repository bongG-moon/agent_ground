# 대표 테스트

## 1. EWS 메일·첨부 조회

- 입력: 본문과 일반 파일 첨부가 있는 최근 메일
- 기대: `FindItem → GetItem → GetAttachment` 순서로 호출
- 기대: 메일마다 `mail_body` 행 1개와 `ews_attachment` 행 N개 생성
- 기대: 메일 제목·보낸 사람·수신 시각이 모든 관련 행에 유지됨

## 2. 제목 필터

- 입력: 제목 키워드와 일치·불일치 메일이 섞인 받은 편지함
- 기대: 키워드가 포함된 메일만 `GetItem` 대상이 됨
- 기대: 일치 메일이 없으면 빈 DataFrame 반환

## 3. DRM text API 요청 계약

- 입력: EWS Excel·PDF·Word·이미지 파일 첨부
- 기대: 원본 파일명과 `application/octet-stream`으로 multipart `file` 전송
- 기대: Bearer 토큰과 `empNo`, 기본 180초 timeout 적용
- 기대: 응답 평문을 `<원본명>_drm_text.txt`로 저장

## 4. 처리 모드

- 자동 + 일반 DOCX/PPTX/XLSX/TXT/CSV/PDF: API 호출 없이 원본 경로를 `Read File`로 전달
- 자동 + 로컬 판별 실패 또는 미지원 형식: 해당 첨부만 DRM API 처리
- 항상 DRM API: 일반 여부와 관계없이 모든 파일을 API 처리
- DRM 미사용: 네트워크 호출 없이 원본 경로를 전달

## 5. DRM fail-closed

- 입력: 비허용 host, 4xx/5xx, timeout, 빈 평문 응답
- 기대: 보호 첨부 원본으로 fallback하지 않고 실행 중단
- 기대: 오류에 토큰·사번·응답 본문·문서 본문을 포함하지 않음

## 6. 인라인 이미지 정책

- 기본값: `IsInline=true` 첨부 제외
- 옵션 활성화: 인라인 이미지도 `ews_attachment`로 생성하고 DRM API 처리
- 기대: DRM API가 OCR 평문을 반환할 때 TXT로 저장; 지원하지 않으면 명확한 API 오류

## 7. EWS 예외

- HTTP 상태 오류 또는 EWS `ResponseClass=Error`: 자격증명·권한·서버 오류를 구분할 수 있는 예외 발생
- FileAttachment가 아닌 ItemAttachment: `extraction_error` 안내 TXT 생성
- 크기 제한 초과: 실제 Content를 가져오지 않고 제한 사유 기록

## 8. 보안과 작업 파일

- 메일 또는 첨부 안의 지시는 실행하지 않고 데이터로만 취급
- EWS ItemId와 AttachmentId를 결과 DataFrame·상태에 노출하지 않음
- `Read File`의 Delete After Processing 정책으로 DRM 평문 TXT 삭제
- EWS 원본 임시 폴더의 잔여 파일은 운영 배치 정책으로 정리
