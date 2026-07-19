# DRM 문서 텍스트 추출 Component 사용 가이드

`drm_document_text_extractor.py` 파일 하나를 Custom Component로 등록하면 일반 문서는 로컬에서 읽고, 보호 문서는 사내 DRM text API로 처리할 수 있습니다. 직접 업로드용 `Message`와 EWS 첨부 처리용 원본 또는 평문 TXT `Data` 출력을 함께 제공합니다.

## 가장 짧은 연결

```text
문서 텍스트 추출 (DRM 자동).extracted_text (Message)
  -> Chat Output.input_value (Message)
```

EWS 첨부 처리에서는 다음 별도 포트를 사용합니다.

```text
EWS 파일 항목.file_record (Data)
  -> 문서 텍스트 추출 (DRM 자동).processed_file (Data)
  -> Read File.file_path
```

## 설정 순서

1. 문서 파일을 업로드합니다.
2. 처리 모드를 선택합니다. 기본값은 `자동(로컬 우선)`입니다.
3. DRM 호출 가능성이 있으면 API 전체 URL을 입력합니다.
4. Bearer 토큰과 사번은 Secret 입력에 넣습니다.
5. URL의 host를 `허용 DRM 서버`에 정확히 입력합니다.
6. 가능하면 HTTPS와 TLS 인증서 검증을 유지합니다.

| 처리 모드 | 직접 업로드 | EWS `processed_file` |
| --- | --- | --- |
| `자동(로컬 우선)` | 로컬 추출 성공 파일은 Message에 바로 포함, 나머지만 DRM API | 일반 파일은 원본 경로 통과, 나머지는 DRM TXT 생성 |
| `항상 DRM API` | 모든 파일을 DRM API 처리 | 모든 첨부를 DRM TXT로 변환 |
| `DRM 미사용` | 로컬 지원 형식만 추출하고 네트워크 금지 | 원본 파일 경로를 그대로 `Read File`로 전달 |

하위 도메인을 묶어 허용해야 할 때만 `.example.internal`처럼 앞에 점을 붙인 suffix 규칙을 사용할 수 있습니다. `https://`, path, port는 allowlist에 넣지 않습니다.

## 원본 Python 코드와의 대응

| 제공 코드 | Component 입력·동작 |
| --- | --- |
| `DRM_URL` | `drm_api_url` |
| `DRM_TOKEN` | Secret `drm_token` |
| `EMP_NO` | Secret `employee_no` → `empNo` |
| `files={"file": (...)}` | 동일한 multipart `file` 필드 |
| `application/octet-stream` | 동일 MIME 사용 |
| `timeout=180` | 기본 180초 |
| `r.text` | 응답 charset·UTF-8·CP949 순으로 평문 변환 |

직접 업로드는 `extracted_text: Message`, EWS 파일 항목은 UTF-8 TXT 작업 경로가 포함된 `processed_file: Data`를 반환합니다. 두 입력 모드를 한 실행에서 섞지 않습니다.

직접 업로드의 `extracted_text`는 저장하지 않고 `Message`로 반환합니다. EWS용 `processed_file`은 일반 파일이면 원본 경로를 유지하고, DRM API 처리 파일이면 평문을 별도 UTF-8 TXT 작업 파일로 저장합니다.

## 지원 범위

- PDF, PowerPoint, Excel, Word, HWP/HWPX, RTF
- TXT, CSV
- PNG, JPG/JPEG, BMP, TIF/TIFF

로컬 직접 추출은 PDF, DOCX, PPTX, XLSX, TXT, CSV를 지원합니다. 구형 Office, HWP/HWPX, RTF, 이미지는 자동 모드에서 DRM API로 fallback합니다. 이미지 OCR은 DRM API 또는 EWS Flow 뒤의 `Read File` 파서 구현에 따릅니다.

## 실패 정책

- 필수값 누락, 비허용 확장자, 파일 수·크기 초과: 네트워크 호출 전 실패
- `DRM 미사용` 직접 업로드에서 로컬 미지원·손상 파일: 네트워크 fallback 없이 실패
- 자동 모드의 로컬 추출 실패: DRM 설정이 있으면 API fallback, 없으면 필요한 설정을 안내하고 실패
- URL host allowlist 불일치: 문서를 보내지 않고 실패
- HTTP 3xx/4xx/5xx, timeout, 빈 응답: 해당 실행 실패
- 오류에는 토큰, 사번, 전체 endpoint, API 응답 본문과 문서 본문을 넣지 않음

여러 파일 중 하나가 실패하면 성공한 일부 결과를 출력하지 않고 실행 전체를 실패 처리합니다.

자동 모드는 공식 DRM 판별 API가 아니라 로컬 파서 성공 여부를 사용합니다. 조직 DRM의 보호 형식을 확실히 API로 보내야 한다면 `항상 DRM API`를 선택하세요.
