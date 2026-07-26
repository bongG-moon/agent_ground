# EWS Component 설정표

| 입력 | 필수 | 예시/기본값 | 설명 |
| --- | --- | --- | --- |
| 이메일 주소 | 예 | `mailbox@example.invalid` | 조회할 받은 편지함 주소 |
| AD 계정 | 예 | `user-id` | 도메인을 제외한 계정 |
| AD 비밀번호 | 예 | 비밀 입력 | Flow JSON 기본값은 비어 있음 |
| AD 도메인 | 예 | `hynixad` | NTLM `domain\\username` 구성 |
| EWS URL | 예 | `https://mail.example.invalid/EWS/Exchange.asmx` | 실제 사내 주소는 UI에서 입력 |
| 읽을 메일 수 | 아니요 | `10` | 1~100 |
| 제목 필터 키워드 | 아니요 | 빈 값 | 대소문자를 구분하지 않는 포함 검색 |
| 본문 읽기 길이 | 아니요 | `2000` | 모델에 전달할 최대 본문 문자 수 |
| 첨부파일 읽기 | 아니요 | `true` | `GetAttachment` 호출 여부 |
| 인라인 첨부 포함 | 아니요 | `false` | CID·서명 이미지 포함 여부 |
| 첨부당 최대 크기 | 아니요 | `30 MB` | 선언 크기와 실제 다운로드 크기 확인 |
| 전체 첨부 최대 크기 | 아니요 | `100 MB` | 한 번 실행의 총 제한 |
| TLS 인증서 검증 | 아니요 | `false` | 제공 환경과 맞춘 기본값; 운영은 활성화 권장 |
| 사내 CA Bundle 경로 | 아니요 | 빈 값 | TLS 검증 시 사용할 PEM 경로 |
| EWS Timeout | 아니요 | `60초` | 5~300초 |
| 내부 Nexus 자동 설치 | 아니요 | `true` | 의존성이 없을 때만 실행 |
| Nexus URL | 조건부 | 빈 값 | 자동 설치 시 필수 |
| Nexus Trusted Host | 조건부 | 빈 값 | 자동 설치 시 필수 |

Flow를 Export하거나 공유하기 전 이메일 주소, AD 계정·비밀번호, EWS/Nexus 주소가 비어 있는지 확인합니다.

## DRM text API 설정

| 입력 | 필수 | 기본값 | 설명 |
| --- | --- | --- | --- |
| DRM API 주소 | 예 | 빈 값 | `/DRM/decrypt/text` 전체 URL |
| DRM 토큰 | 예 | 빈 Secret | Bearer 토큰 |
| 사번 | 예 | 빈 Secret | `empNo` 값 |
| HTTP DRM API 사용 허용 | 조건부 | `false` | 승인된 폐쇄망 HTTP endpoint에서만 켬 |
| TLS 인증서 검증 | 아니요 | `true` | HTTPS 사용 시 유지 |
| 제한 시간 | 아니요 | `180초` | 파일 하나당 timeout |

DRM API 주소·토큰·사번도 Export 전에 비어 있는지 확인합니다. JPG/JPEG용 vLLM 모델과 API Key도 기본값으로 저장하지 않습니다.
