# EWS·DRM Outlook 메일 요약 Flow

사내 EWS와 NTLM 인증으로 받은 편지함의 최근 메일을 읽고, 파일 첨부를 사내 DRM text API로 전송해 반환된 평문을 `.txt` 작업 파일로 만든 뒤 메일별·전체 업무 요약을 생성하는 Langflow `1.8.2` Flow입니다.

## 실제 환경 반영 범위

- `requests + requests-ntlm + EWS SOAP` 방식으로 `FindItem → GetItem → GetAttachment`를 호출합니다.
- Outlook Connector, Microsoft Graph, MCP, API Request Component는 사용하지 않습니다.
- EWS의 파일 첨부 Base64 Content를 로컬 임시 파일로 복원합니다.
- `source_kind=ews_attachment`인 파일은 공용 `drm_document_text_extractor` Component에 전달합니다.
- DRM Component는 `multipart/form-data`의 `file`, `Authorization: Bearer`, `empNo`, 기본 180초 timeout으로 호출합니다.
- API가 반환한 평문을 UTF-8 `.txt`로 저장하고 기존 메일 메타데이터와 함께 `Read File`로 전달합니다.
- EWS·Nexus·DRM 주소, AD 계정·비밀번호, Bearer 토큰과 사번은 Flow JSON 기본값에 포함하지 않습니다.

Microsoft 문서상 `GetItem`은 첨부 메타데이터를 반환하며 실제 FileAttachment Content에는 `GetAttachment`가 추가로 필요합니다.

- <https://learn.microsoft.com/en-us/exchange/client-developer/exchange-web-services/attachments-and-ews-in-exchange>
- <https://learn.microsoft.com/en-us/exchange/client-developer/web-service-reference/getattachment-operation>

## 캔버스 구조

```text
01 Outlook 메일·첨부 읽기 (EWS)
  -> 02 EWS 메일 항목별 반복
      -> 03 EWS 첨부 DRM 텍스트 추출
      -> 04 DRM 평문 TXT 읽기
      -> 05 메일 항목 내용 정리
      -> 06 메일 항목별 요약 모델
      -> 02 Loop 복귀
  -> 07 메일 항목별 요약 합치기

Chat Input --------------------------------\
                                            -> 08 EWS 메일 통합 요약 프롬프트
07 메일 항목별 요약 합치기 ---------------/
  -> 09 전체 EWS 메일 통합 요약 모델
  -> 10 Chat Output
```

## DRM API 입력

| 입력 | 기본값 | 설명 |
| --- | --- | --- |
| DRM API 주소 | 빈 값 | `/DRM/decrypt/text` 전체 주소 |
| DRM 토큰 | 빈 Secret | `Authorization: Bearer ...` |
| 사번 | 빈 Secret | `empNo` query parameter |
| 허용 DRM 서버 | 빈 값 | API URL host와 일치하는 allowlist |
| HTTP DRM API 사용 허용 | `false` | 제공 예시처럼 HTTP인 폐쇄망에서만 활성화 |
| TLS 인증서 검증 | `true` | HTTPS 운영 환경에서 유지 |
| 제한 시간 | `180초` | 첨부파일 하나의 요청 timeout |
| 파일당 최대 크기 | `50 MB` | DRM API 업로드 제한 |
| 파일당 최대 응답 | `20 MB` | 평문 응답 읽기 제한 |

지원 입력은 PDF, PowerPoint, Excel, Word, HWP/HWPX, TXT, CSV, RTF와 PNG/JPEG/BMP/TIFF입니다. 실제 추출 가능 범위는 DRM API 구현에 따르며 이미지 본문은 API가 OCR 평문을 반환해야 읽을 수 있습니다.

## 첫 실행

1. `mail_attachment_summary_flow.json`을 Langflow에 가져옵니다.
2. EWS URL, 메일 주소, AD 계정·비밀번호를 입력합니다.
3. 필요하면 내부 Nexus URL·Trusted Host를 입력합니다.
4. DRM API URL, 토큰, 사번과 허용 host를 입력합니다.
5. DRM 주소가 HTTP라면 폐쇄망·보안 승인을 확인한 뒤 `HTTP DRM API 사용 허용`을 켭니다.
6. 두 Language Model에 같은 사내 승인 모델을 선택합니다.
7. EWS 노드부터 Chat Output까지 실행합니다.

## 실패 정책

- 메일 본문과 EWS 오류 안내 TXT는 DRM API 호출 없이 통과합니다.
- 파일 첨부는 DRM API가 실패하면 원본 보호 파일로 fallback하지 않고 실행을 중단합니다.
- API redirect는 따르지 않으며 URL host가 allowlist에 없으면 파일 전송 전에 중단합니다.
- HTTP 오류에는 토큰·사번·응답 본문·문서 본문을 포함하지 않습니다.
- 평문 TXT는 `Read File` 처리 후 삭제되지만 EWS 원본 임시 폴더는 운영 정리 정책이 필요합니다.

## 보안·운영 주의점

- EWS는 제공 환경과의 호환을 위해 `verify_tls=false`가 기본입니다. 가능한 경우 사내 CA bundle을 등록하고 켜세요.
- DRM API의 HTTP 사용은 Bearer 토큰과 원문이 암호화되지 않으므로 승인된 폐쇄망에서만 허용하세요.
- Flow를 Export하기 전에 AD 비밀번호, DRM 토큰·사번, EWS/Nexus/DRM 주소가 비어 있는지 확인하세요.
- EWS ItemId와 AttachmentId는 결과 DataFrame·상태·로그에 남기지 않습니다.
- 실제 EWS와 DRM API를 연결한 사용자 환경 실행 전까지 상태는 `user_testing`입니다.

## 개발자 재검증

```powershell
$lfPython = "$env:LOCALAPPDATA\com.LangflowDesktop\.langflow-venv\Scripts\python.exe"
& $lfPython scripts\build_mail_attachment_summary_flow.py --check
& $lfPython -m pytest -q flows\mail_attachment_summary_flow\tests
```

