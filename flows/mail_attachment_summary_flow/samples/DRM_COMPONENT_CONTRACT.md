# 사내 DRM 컴포넌트 구현 계약

구현 파일: `flows/mail_attachment_summary_flow/nodes/drm_unlock_adapter.py`

수정 대상은 `company_drm_unlock` 함수입니다.

```python
def company_drm_unlock(source_path: Path, destination_path: Path) -> str:
    ...
```

## 입력

- `source_path`: MSG에서 분리된 원본 작업 파일
- `destination_path`: DRM 처리 결과를 생성해야 하는 새 작업 파일 경로

## 반환

- `unlocked`: DRM 보호를 해제해 결과 파일 생성
- `not_protected`: 비보호 파일의 작업용 복사본 생성

## 필수 조건

- `source_path`를 수정하거나 덮어쓰지 않습니다.
- 성공 반환 전에 `destination_path`가 실제 파일로 존재해야 합니다.
- 확장자와 파일 내용 형식을 가능한 한 유지합니다.
- Langflow 실행 계정에 필요한 DRM Agent·SDK 권한을 부여합니다.
- 파일명 외의 전체 경로, 파일 본문, 키, 토큰을 로그에 남기지 않습니다.
- 오류 시 부분 결과를 성공으로 반환하지 않고 예외를 발생시킵니다.
- 사내 SDK가 UI 세션이나 Windows 사용자 로그인을 요구하는지 배포 전에 확인합니다.

## 금지

- DRM 우회 또는 승인되지 않은 복호화 방식
- 외부 SaaS나 인터넷 endpoint로 파일 전송
- 코드 안에 계정·키·사내 endpoint 하드코딩
- 실패 시 원본 보호 파일을 그대로 반환하는 fallback
