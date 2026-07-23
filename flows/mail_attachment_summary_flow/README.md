# EWS·DRM Outlook 메일 요약 Flow

사내 EWS와 NTLM 인증으로 받은 편지함의 최근 메일을 읽고, 일반 문서는 로컬 `Read File`, 보호 문서는 DRM API, JPG/JPEG는 연결된 vLLM Vision 모델로 처리합니다. ZIP 등 미지원 형식은 오류로 중단하지 않고 안내 텍스트로 바꾼 뒤 메일별·전체 업무 요약을 생성하는 Langflow `1.8.2` Flow입니다.

## 실제 환경 반영 범위

- `requests + requests-ntlm + EWS SOAP` 방식으로 `FindItem → GetItem → GetAttachment`를 호출합니다.
- Outlook Connector, Microsoft Graph, MCP, API Request Component는 사용하지 않습니다.
- EWS의 파일 첨부 Base64 Content를 로컬 임시 파일로 복원합니다.
- `source_kind=ews_attachment`인 파일은 공용 `drm_document_text_extractor` Component에 전달합니다.
- 기본 `자동(로컬 우선)` 모드는 PDF·DOCX·PPTX·XLSX·TXT·CSV의 로컬 판별을 먼저 수행합니다.
- 일반 파일은 원본 경로로, 로컬 판별 실패 파일은 DRM API의 UTF-8 `.txt` 경로로 `Read File`에 전달합니다.
- JPG/JPEG 첨부는 `03A JPG 이미지 해석 모델 (vLLM)`에 멀티모달 Data URL로 전달하고 결과를 UTF-8 `.txt`로 저장합니다.
- ZIP·RAR·7Z·TAR·GZ 등 미지원 형식은 압축을 열지 않고 `skipped_unsupported` 안내 TXT로 바꿔 다음 Loop 항목을 계속합니다.
- `항상 DRM API`에서는 `multipart/form-data`의 `file`, `Authorization: Bearer`, `empNo`, 기본 180초 timeout으로 모든 첨부를 처리합니다.
- EWS·Nexus·DRM 주소, AD 계정·비밀번호, Bearer 토큰과 사번은 Flow JSON 기본값에 포함하지 않습니다.
- 기본 Read File은 서버 경로 입력에서 출력을 `Raw Content(Message)`로 되돌리는 동적 동작이 있으므로, 04는 Read File을 상속한 `StableMailFileReader`로 구성했습니다.
- `04 파일 내용 DataFrame(DataFrame) → 05 DataFrame 입력(DataFrame)`을 정확한 단일 타입으로 고정해 Flow를 가져올 때도 연결선이 유지됩니다.
- 04는 TXT·CSV·JSON·Markdown·XML·HTML·소스 파일을 표준 파서로 읽고, PDF·Office·이미지는 필요할 때 Advanced Parser/Docling으로 처리합니다. 따라서 DRM 평문 TXT에 Docling을 강제하지 않습니다.
- EWS가 없는 테스트 환경에서는 `mail_attachment_summary_dummy_flow.json`을 사용해 네트워크 호출 없이 메일 본문과 TXT 첨부를 생성할 수 있습니다.

Microsoft 문서상 `GetItem`은 첨부 메타데이터를 반환하며 실제 FileAttachment Content에는 `GetAttachment`가 추가로 필요합니다.

- <https://learn.microsoft.com/en-us/exchange/client-developer/exchange-web-services/attachments-and-ews-in-exchange>
- <https://learn.microsoft.com/en-us/exchange/client-developer/web-service-reference/getattachment-operation>

## 캔버스 구조

```text
01 Outlook 메일·첨부 읽기 (EWS)
  -> 02 EWS 메일 항목별 반복
      -> 03B EWS 문서·JPG·미지원 첨부 처리
      -> 04 원본 또는 DRM 평문 파일 읽기
      -> 05 메일 항목 내용 정리
      -> 06 메일 항목별 요약 모델
      -> 02 Loop 복귀
  -> 07 메일 항목별 요약 합치기

03A JPG 이미지 해석 모델 (vLLM) ----------> 03B.vision_model

Chat Input --------------------------------\
                                            -> 08 EWS 메일 통합 요약 프롬프트
07 메일 항목별 요약 합치기 ---------------/
  -> 09 전체 EWS 메일 통합 요약 모델
  -> 10 Chat Output
```

## DRM API 입력

| 입력 | 기본값 | 설명 |
| --- | --- | --- |
| 처리 모드 | `자동(로컬 우선)` | 일반 파일 로컬 우선, 항상 DRM, DRM 미사용 선택 |
| DRM API 주소 | 빈 값 | `/DRM/decrypt/text` 전체 주소 |
| DRM 토큰 | 빈 Secret | `Authorization: Bearer ...` |
| 사번 | 빈 Secret | `empNo` query parameter |
| HTTP DRM API 사용 허용 | `false` | 제공 예시처럼 HTTP인 폐쇄망에서만 활성화 |
| TLS 인증서 검증 | `true` | HTTPS 운영 환경에서 유지 |
| 제한 시간 | `180초` | 첨부파일 하나의 요청 timeout |
| 파일당 최대 크기 | `50 MB` | DRM API 업로드 제한 |
| 파일당 최대 응답 | `20 MB` | 평문 응답 읽기 제한 |

자동 로컬 판별은 PDF, DOCX, PPTX, XLSX, TXT, CSV를 지원합니다. 구형 Office, HWP/HWPX, RTF와 비-JPEG 이미지는 DRM API로 fallback합니다. JPG/JPEG는 처리 모드와 별도로 연결된 Vision 모델을 사용합니다. `DRM 미사용`에서는 문서 원본을 `Read File`로 넘기지만 JPG/JPEG Vision 경로는 계속 사용할 수 있습니다.

## 첫 실행

1. `mail_attachment_summary_flow.json`을 Langflow에 가져옵니다.
2. EWS URL, 메일 주소, AD 계정·비밀번호를 입력합니다.
3. 필요하면 내부 Nexus URL·Trusted Host를 입력합니다.
4. 첨부 처리 모드를 선택합니다.
5. 자동 fallback 또는 항상 DRM을 사용하면 API URL, 토큰, 사번을 입력합니다.
6. DRM 주소가 HTTP라면 폐쇄망·보안 승인을 확인한 뒤 `HTTP DRM API 사용 허용`을 켭니다.
7. `03A JPG 이미지 해석 모델 (vLLM)`에 사내 승인 멀티모달 모델을 선택합니다.
8. 두 요약 Language Model에 같은 사내 승인 모델을 선택합니다.
9. EWS 노드부터 Chat Output까지 실행합니다.

## EWS 없는 테스트 환경

1. `mail_attachment_summary_dummy_flow.json`을 새 Flow로 가져옵니다.
2. `01T 테스트 EWS 메일·첨부 데이터`에서 더미 메일 수와 첨부 포함 여부를 선택합니다.
3. DRM 노드는 `자동(로컬 우선)`을 유지합니다. 더미 첨부가 TXT이므로 DRM API를 호출하지 않습니다.
4. JPG 테스트를 추가할 경우 `03A`에 사내 Vision 모델을 연결합니다.
5. 두 요약 Language Model에 사내 승인 모델을 선택하고 Chat Output까지 실행합니다.

기본 설정은 메일 2통을 만들며 각 메일은 본문 1개와 TXT 첨부 1개를 가져 총 4개 Loop 항목이 됩니다. 생성 행은 운영 EWS와 동일하게 `mail_index`, `mail_subject`, `sender`, `received_time`, `file_path`, `source_kind`, `drm_status` 등을 포함합니다.

## 실패 정책

- 메일 본문과 EWS 오류 안내 TXT는 DRM API 호출 없이 통과합니다.
- ZIP 등 미지원 형식은 오류 대신 `skipped_unsupported` 안내 TXT로 변환해 Loop를 계속합니다.
- JPG/JPEG 모델 미연결·호출 실패는 `vision_failed` 안내 TXT로 변환해 Loop를 계속합니다.
- 자동 모드에서 로컬 추출에 성공한 일반 첨부는 원본 경로로 통과합니다.
- 자동 fallback 또는 항상 DRM에서 API 호출이 시작된 뒤 실패하면 원본 보호 파일로 우회하지 않고 실행을 중단합니다.
- `DRM 미사용`은 사용자가 명시적으로 선택한 경우에만 원본 파일을 그대로 `Read File`로 전달합니다.
- API redirect는 따르지 않으며 URL 내부 인증정보와 fragment는 허용하지 않습니다.
- HTTP 오류에는 토큰·사번·응답 본문·문서 본문을 포함하지 않습니다.
- 평문 TXT는 `Read File` 처리 후 삭제되지만 EWS 원본 임시 폴더는 운영 정리 정책이 필요합니다.

## 보안·운영 주의점

- EWS는 제공 환경과의 호환을 위해 `verify_tls=false`가 기본입니다. 가능한 경우 사내 CA bundle을 등록하고 켜세요.
- DRM API의 HTTP 사용은 Bearer 토큰과 원문이 암호화되지 않으므로 승인된 폐쇄망에서만 허용하세요.
- JPG/JPEG 원본은 연결된 Vision 모델로 전송되므로 사내 승인 vLLM endpoint와 멀티모달 모델만 사용하세요.
- Flow를 Export하기 전에 AD 비밀번호, DRM 토큰·사번, EWS/Nexus/DRM 주소가 비어 있는지 확인하세요.
- EWS ItemId와 AttachmentId는 결과 DataFrame·상태·로그에 남기지 않습니다.
- 실제 EWS와 DRM API를 연결한 사용자 환경 실행 전까지 상태는 `user_testing`입니다.
- 자동 판별은 공식 DRM 상태 API가 아니라 로컬 파서 성공 여부이므로 조직 DRM이 로컬 파서에 일부 내용을 노출할 수 있으면 `항상 DRM API`를 사용하세요.

## 개발자 재검증

```powershell
$lfPython = "$env:LOCALAPPDATA\com.LangflowDesktop\.langflow-venv\Scripts\python.exe"
& $lfPython scripts\build_mail_attachment_summary_flow.py --check
& $lfPython -m pytest -q flows\mail_attachment_summary_flow\tests
```
