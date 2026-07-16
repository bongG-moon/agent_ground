# 입력 예시

실제 endpoint, 토큰, 사번은 조직의 비밀값 관리 정책에 따라 입력합니다. 아래 값은 형식만 보여 주는 예시입니다.

| 필드 | 예시 |
| --- | --- |
| 문서 파일 | `quarterly_review.pdf`, `cost_plan.xlsx` |
| DRM API 주소 | `https://drm.example.internal/DRM/decrypt/text` |
| DRM 토큰 | Flow의 Secret 입력에 직접 입력 |
| 사번 | Flow의 Secret 입력에 직접 입력 |
| 허용 DRM 서버 | `drm.example.internal` |
| HTTP DRM API 사용 허용 | `false` |
| TLS 인증서 검증 | `true` |

실제 토큰과 사번을 샘플 파일이나 Export JSON에 저장하지 마세요.
