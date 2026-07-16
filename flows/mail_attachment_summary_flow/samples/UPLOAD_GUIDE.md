# EWS 실행 가이드

이 Flow는 `.msg` 파일을 업로드하지 않습니다. Langflow 실행 계정이 사내 EWS에 NTLM으로 접속해 받은 편지함을 조회합니다.

## 권장 순서

1. `01 Outlook 메일·첨부 읽기 (EWS)`에 이메일 주소, AD 계정, AD 비밀번호, EWS URL을 입력합니다.
2. 실행 환경에 `requests`와 `requests-ntlm`이 없으면 사내 Nexus URL과 Trusted Host를 입력합니다.
3. `03 EWS 첨부 DRM 텍스트 추출`에 DRM API 주소, Bearer 토큰, 사번과 허용 host를 입력합니다.
4. 두 Language Model 노드에 사내 사용 가능 모델을 선택합니다.
5. 제목 키워드와 읽을 메일 수를 지정하고 Chat Input에 정리 기준을 입력합니다.

## 예시 요청

```text
조회한 메일별로 본문과 첨부파일을 검토해 다음 주 회의 전에 해야 할 일을 정리해줘.
담당자와 기한은 원문에 명시된 경우에만 쓰고, 메일이나 첨부 간 수치가 다르면 충돌로 표시해줘.
```

## 사전 확인

- EWS URL은 `https://.../EWS/Exchange.asmx` 형식이어야 합니다.
- 메일 계정과 AD 계정이 다를 수 있으므로 두 입력을 구분합니다.
- 기본값은 인라인 CID 이미지와 서명 이미지를 제외합니다.
- DRM API가 HTTP라면 승인된 폐쇄망인지 확인한 뒤 `HTTP DRM API 사용 허용`을 켭니다.
- 이미지 첨부는 DRM API가 OCR 평문을 반환하는 경우에만 문자 내용을 읽을 수 있습니다.
- 운영 환경에서는 사내 CA Bundle을 등록하고 TLS 인증서 검증을 켜는 것을 권장합니다.
- 실제 메일·첨부, 계정, 비밀번호, 사내 endpoint를 저장소 샘플에 넣지 않습니다.
