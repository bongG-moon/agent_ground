# DRM 문서 텍스트 추출 Flow

PDF·Office·HWP·텍스트·CSV·일반 이미지 파일을 직접 업로드하면 각 파일을 사내 DRM text API에 `multipart/form-data`로 전달하고, API가 반환한 평문을 하나의 Langflow `Message`로 출력합니다. LLM이나 파일 형식별 외부 파서를 추가로 호출하지 않습니다.

## 가져오기

1. [`drm_document_text_extraction_flow.json`](drm_document_text_extraction_flow.json)을 Langflow `1.8.2`에 가져옵니다.
2. `01 DRM 문서 텍스트 추출`에서 문서 파일을 업로드합니다.
3. DRM API 주소, Bearer 토큰, 사번, 허용 DRM 서버를 입력합니다.
4. `02 추출 텍스트 출력`까지 실행합니다.

## 지원 파일

- PDF: `.pdf`
- PowerPoint: `.ppt`, `.pptx`
- Excel: `.xls`, `.xlsx`
- Word: `.doc`, `.docx`
- 한글: `.hwp`, `.hwpx`
- 텍스트·표: `.txt`, `.csv`, `.rtf`
- 일반 이미지: `.png`, `.jpg`, `.jpeg`, `.bmp`, `.tif`, `.tiff`

파일 형식별 파싱 가능 여부는 DRM API의 `/decrypt/text` 구현 범위에 따릅니다. 이미지의 문자 내용은 API가 OCR 평문을 반환해야 읽을 수 있습니다. 이 Flow는 업로드 파일의 바이너리를 변경하지 않고 API의 `file` 필드로 전달합니다.

## 입력값

| 입력 | 필수 | 기본값 | 설명 |
| --- | --- | --- | --- |
| 문서 파일 | 예 | 없음 | 지원 문서를 최대 10개까지 업로드 |
| DRM API 주소 | 예 | 빈 값 | 사내 DRM text API 전체 URL |
| DRM 토큰 | 예 | 빈 Secret | `Authorization: Bearer ...` |
| 사번 | 예 | 빈 Secret | `empNo` query parameter |
| 허용 DRM 서버 | 예 | 빈 값 | API URL host와 일치해야 하는 allowlist |
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
# DRM 문서 텍스트 추출 결과

[FILE 1/2] guide.pdf
문자 수: 1,234

...API가 반환한 평문...
[END FILE 1/2]
```

여러 파일은 파일명과 경계 표시를 포함해 입력 순서대로 합쳐집니다.

## 보안 주의

- 이 Flow는 문서 원문을 DRM API 서버로 업로드합니다. 반드시 승인된 사내 서버만 allowlist에 넣으세요.
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
