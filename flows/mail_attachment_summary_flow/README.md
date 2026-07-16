# 다중 MSG·DRM 첨부파일 요약 Flow

사용자가 Outlook `.msg` 파일을 직접 여러 개 업로드하면 메일 본문·메타데이터·첨부파일을 로컬에서 분해하고, 첨부파일만 사내 DRM 어댑터로 해제한 뒤 하나의 업무 요약으로 합치는 Langflow `1.8.2` Flow입니다.

## 제약을 반영한 범위

- Outlook Connector, Microsoft Graph, MCP, API Request를 사용하지 않습니다.
- MSG 분해와 DRM 연동에만 Flow 전용 내부 노드 두 개를 사용하고, 이후 파싱·OCR·반복·요약은 Langflow 기본 Component를 사용합니다.
- 모델과 API key, DRM 키·토큰·사내 endpoint를 Flow JSON에 넣지 않습니다.
- DRM 어댑터 기본값은 fail-closed입니다. 사내 구현이 없으면 보호 첨부를 그대로 다음 단계로 보내지 않습니다.
- 업로드한 MSG와 해제된 파일은 외부 저장소로 전송하지 않습니다.

## 캔버스 구조

```text
01 MSG 본문·첨부파일 분해 (여러 .msg 업로드)
  -> 02 MSG 항목별 반복
      -> 03 첨부파일 DRM 해제
      -> 04 DRM 해제 파일 읽기 / OCR
      -> 05 메일 항목 내용 정리
      -> 06 메일 항목별 요약 모델
      -> 02 Loop 복귀
  -> 07 메일 항목별 요약 합치기

Chat Input (정리 요청) ------------------\
                                         -> 08 MSG 통합 요약 프롬프트
07 메일 항목별 요약 합치기 -------------/
  -> 09 전체 MSG 통합 요약 모델
  -> 10 Chat Output
```

## 필수 준비

### 1. MSG 파서

기본 구현은 로컬 Python 패키지 `extract-msg 0.55.x`의 공개 `openMsg` API를 사용합니다. 현재 Langflow Desktop 환경에는 이 패키지가 설치되어 있지 않습니다.

사내 오픈소스 및 GPLv3 반입 승인을 받은 경우에만 Langflow Desktop 가상환경에 설치합니다.

```powershell
$lfPython = "$env:LOCALAPPDATA\com.LangflowDesktop\.langflow-venv\Scripts\python.exe"
& $lfPython -m pip install "extract-msg==0.55.0"
```

승인되지 않으면 `nodes/msg_attachment_extractor.py`의 `_default_msg_opener`만 사내 MSG 파서로 교체합니다. 입력은 파일 경로이고 결과 객체는 `subject`, `sender`, `to`, `cc`, `date`, `body` 또는 `htmlBody`, `attachments` 속성을 제공해야 합니다.

### 2. DRM 어댑터

`nodes/drm_unlock_adapter.py`의 `company_drm_unlock` 함수에 사용자가 작성한 사내 DRM SDK 호출을 넣습니다.

```python
def company_drm_unlock(source_path: Path, destination_path: Path) -> str:
    # 승인된 사내 DRM SDK로 destination_path에 작업용 결과 생성
    # return "unlocked" 또는 "not_protected"
```

전체 계약과 금지 사항은 `samples/DRM_COMPONENT_CONTRACT.md`에 있습니다.

## 첫 실행

1. `mail_attachment_summary_flow.json`을 Langflow에 가져옵니다.
2. `01 MSG 본문·첨부파일 분해`에 `.msg` 파일을 한 개 이상 업로드합니다.
3. `company_drm_unlock`을 사내 DRM SDK로 구현합니다.
4. `06 메일 항목별 요약 모델`과 `09 전체 MSG 통합 요약 모델`에 같은 승인 모델을 선택합니다.
5. Chat Input의 정리 요청을 필요에 맞게 수정하고 Chat Output까지 실행합니다.

## MSG 처리 방식

- 메일 제목·발신자·수신자·참조·시간·본문은 UTF-8 TXT 작업 파일로 만듭니다.
- 일반 첨부는 원래 확장자를 유지해 별도 작업 파일로 저장합니다.
- 메일 본문과 분해 오류 안내 파일은 DRM 단계를 통과만 하고, 실제 첨부만 사내 DRM SDK를 호출합니다.
- hidden/CID 인라인 이미지는 기본 제외하며 `인라인 이미지 포함`을 켜면 처리합니다.
- 내장 `.msg` 첨부와 비표준 attachment 객체는 재귀 실행하지 않고 확인 필요 항목으로 남깁니다.

## 주의점

- `.msg` 컨테이너 자체가 DRM으로 보호되어 OLE 서명이 보이지 않으면 분해 전에 별도 MSG DRM 해제가 필요합니다.
- DRM 어댑터는 원본을 덮어쓰면 안 되며 반드시 다른 작업 경로에 결과를 생성해야 합니다.
- Langflow 실행 계정에 사내 DRM Agent·SDK 사용 권한이 있어야 합니다.
- `Read File`은 Advanced Parser와 EasyOCR를 사용하므로 PDF, Office 문서, 이미지 등을 처리할 수 있습니다.
- 파일 내용은 신뢰할 수 없는 데이터로 취급하며 내부 명령·링크·프롬프트를 실행하지 않습니다.
- OCR과 모델 요약 결과는 의사결정 전에 원문과 대조해야 합니다.

## 출력 형식

1. 전체 요약
2. 메일별 본문 및 첨부파일 핵심 내용
3. 결정 사항 및 요청 사항
4. 실행 항목
5. 파일 간 공통점·차이·충돌
6. 확인이 필요한 사항 및 읽지 못한 내용

## 파일

- `mail_attachment_summary_flow.json`: Langflow 개별 Import 파일
- `nodes/msg_attachment_extractor.py`: MSG 분해 내부 노드 원본
- `nodes/drm_unlock_adapter.py`: 사내 DRM 구현 지점
- `CONNECTION_GUIDE.md`: 포트 단위 연결표와 Builder 설정
- `samples/UPLOAD_GUIDE.md`: 다중 MSG 업로드 순서
- `samples/DRM_COMPONENT_CONTRACT.md`: DRM 함수 계약
- `samples/sample_extracted_msg_records.json`: MSG 분해 출력 예시
- `samples/TEST_CASES.md`: 대표 검증 항목
- `tests/test_mail_attachment_summary_flow.py`: JSON·내장 코드·fail-closed 검사

## 개발자 재검증

```powershell
$lfPython = "$env:LOCALAPPDATA\com.LangflowDesktop\.langflow-venv\Scripts\python.exe"
& $lfPython scripts\build_mail_attachment_summary_flow.py --check
& $lfPython -m pytest -q flows\mail_attachment_summary_flow\tests
```

현재 검증은 Langflow `1.8.2`의 Flow 생성·내부 Node schema·순수 함수 계약 검사까지입니다. `extract-msg`와 실제 사내 DRM SDK, 승인 모델을 연결한 사용자 Builder 실행 전까지 상태는 `user_testing`입니다.
