# DRM 문서 텍스트 추출 Flow

PDF·Office·HWP·텍스트·CSV·일반 이미지 파일을 직접 업로드하면 처리 모드에 따라 일반 파일은 Langflow 로컬 환경에서 읽고, 보호되었거나 로컬에서 해석할 수 없는 파일만 사내 DRM text API에 전달합니다. 결과는 하나의 Langflow `Message`로 출력하며 LLM은 사용하지 않습니다.

## 가져오기

1. [`drm_document_text_extraction_flow.json`](drm_document_text_extraction_flow.json)을 Langflow `1.8.2`에 가져옵니다.
2. `01 문서 텍스트 추출 (DRM 자동)`에서 문서 파일을 업로드합니다.
3. `자동(로컬 우선)`, `항상 DRM API`, `DRM 미사용` 중 처리 모드를 선택합니다.
4. DRM 호출 가능성이 있는 모드라면 API 주소, Bearer 토큰, 사번, 허용 DRM 서버를 입력합니다.
5. `02 추출 텍스트 출력`까지 실행합니다.

## 처리 모드

| 모드 | 동작 | DRM 설정 |
| --- | --- | --- |
| `자동(로컬 우선)` | PDF·DOCX·PPTX·XLSX·TXT·CSV를 로컬에서 먼저 추출하고, 실패하거나 지원하지 않는 형식만 DRM API로 전송 | fallback 가능성이 있으므로 권장 |
| `항상 DRM API` | 지원 파일을 모두 DRM API로 전송 | 필수 |
| `DRM 미사용` | 네트워크를 호출하지 않고 로컬 추출만 수행 | 불필요 |

자동 판별은 DRM 제품의 공식 보호 여부 조회가 아니라 **로컬 파서 성공 여부**를 기준으로 합니다. 조직 DRM이 로컬 파서에서도 일부 내용을 노출하는 형식이라면 `항상 DRM API`를 사용하세요.

## 지원 파일

- PDF: `.pdf`
- PowerPoint: `.ppt`, `.pptx`
- Excel: `.xls`, `.xlsx`
- Word: `.doc`, `.docx`
- 한글: `.hwp`, `.hwpx`
- 텍스트·표: `.txt`, `.csv`, `.rtf`
- 일반 이미지: `.png`, `.jpg`, `.jpeg`, `.bmp`, `.tif`, `.tiff`

로컬 직접 추출은 `.pdf`, `.docx`, `.pptx`, `.xlsx`, `.txt`, `.csv`를 지원합니다. 구형 Office, HWP/HWPX, RTF와 이미지는 자동 모드에서 DRM API 경로를 사용합니다. `DRM 미사용` 직접 업로드에서 로컬 미지원 형식을 넣으면 명확한 오류로 중단합니다. 이미지의 문자 내용은 DRM API가 OCR 평문을 반환해야 읽을 수 있습니다.

## 입력값

| 입력 | 필수 | 기본값 | 설명 |
| --- | --- | --- | --- |
| 문서 파일 | 예 | 없음 | 지원 문서를 최대 10개까지 업로드 |
| 처리 모드 | 예 | `자동(로컬 우선)` | 로컬 우선, DRM 강제, DRM 미사용 선택 |
| DRM API 주소 | 조건부 | 빈 값 | DRM 호출이 필요한 경우의 사내 API 전체 URL |
| DRM 토큰 | 조건부 | 빈 Secret | `Authorization: Bearer ...` |
| 사번 | 조건부 | 빈 Secret | `empNo` query parameter |
| 허용 DRM 서버 | 조건부 | 빈 값 | API URL host와 일치해야 하는 allowlist |
| HTTP DRM API 사용 허용 | 아니오 | `false` | 폐쇄된 테스트망에서만 명시적으로 활성화 |
| TLS 인증서 검증 | 아니오 | `true` | HTTPS 운영 환경에서는 켠 상태 유지 |
| 제한 시간 | 아니오 | `180초` | 파일 하나의 연결·읽기 timeout |

배포 JSON에는 endpoint, 토큰, 사번, 업로드 파일 경로가 들어 있지 않습니다.

## 요청 계약

각 파일은 입력 순서대로 한 번씩 전송됩니다.

```text
POST <DRM API URL>?empNo=<employee number>
Authorization: Bearer <token>
Content-Type: multipart/form-data
file: (<original filename>, <binary>, application/octet-stream)
```

- POST redirect는 따라가지 않습니다.
- API URL host가 `허용 DRM 서버`에 없으면 파일을 보내기 전에 중단합니다.
- 비정상 HTTP 응답에서는 응답 본문을 오류 메시지에 포함하지 않습니다.
- 개별 파일, 전체 파일, 응답 크기를 제한합니다.

## 출력 예시

```text
# 문서 텍스트 추출 결과

[FILE 1/2] guide.pdf
처리 경로: 로컬 추출
문자 수: 1,234

...API가 반환한 평문...
[END FILE 1/2]
```

여러 파일은 파일명과 경계 표시를 포함해 입력 순서대로 합쳐집니다.

## 보안 주의

- 자동 모드에서 로컬 추출에 성공한 파일은 외부로 전송하지 않습니다. DRM fallback 파일만 API로 업로드합니다.
- `항상 DRM API`와 자동 fallback은 문서 원문을 서버로 업로드하므로 반드시 승인된 사내 서버만 allowlist에 넣으세요.
- 제공 코드의 `http://...` 형식을 그대로 쓰려면 `HTTP DRM API 사용 허용`을 켜야 합니다. 운영에서는 HTTPS와 사내 CA를 권장합니다.
- 추출 결과는 DRM이 해제된 평문이므로 Chat Output, 실행 기록, 후속 LLM 연결의 접근권한과 보존기간을 별도로 통제하세요.
- 실제 사내 API 호출 검증 전까지 상태는 `user_testing`입니다.

## 개발자 검증

```powershell
$lfPython = "$env:LOCALAPPDATA\com.LangflowDesktop\.langflow-venv\Scripts\python.exe"
& $lfPython scripts\build_drm_document_text_extraction_flow.py --check
& $lfPython -m pytest -q flows\drm_document_text_extraction_flow\tests
```

자세한 연결 포트는 [CONNECTION_GUIDE.md](CONNECTION_GUIDE.md), Component 단독 사용은 [Component 사용 가이드](../../components/drm_document_text_extractor/USAGE_GUIDE.md)를 확인합니다.
