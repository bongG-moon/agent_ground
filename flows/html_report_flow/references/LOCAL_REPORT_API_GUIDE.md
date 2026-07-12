# 로컬 Report API 체험 가이드

이 문서는 HTML 생성 FLOW 체험자가 자기 PC에서 링크 생성용 서버를 실행하는 방법을 설명합니다.

MongoDB와 `.env` 파일은 필요 없습니다. 서버 실행은 최종적으로 아래 한 줄입니다.

```powershell
python server.py
```

## 0. 준비물

- Python 3.10 이상
- Langflow에서 HTML 생성 flow import 완료
- 이 폴더 전체:

```text
C:\Users\<사용자명>\Desktop\기능flow\html_report_flow
```

## 1. 처음 한 번만 설치

PowerShell을 열고 아래를 실행합니다.

```powershell
cd C:\Users\<사용자명>\Desktop\기능flow\html_report_flow\report_api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

만약 `Activate.ps1` 실행 정책 오류가 나오면 같은 PowerShell 창에서 아래를 먼저 실행합니다.

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## 2. 서버 실행

설치가 끝났으면 매번 아래만 실행하면 됩니다.

```powershell
cd C:\Users\<사용자명>\Desktop\기능flow\html_report_flow\report_api
.\.venv\Scripts\Activate.ps1
python server.py
```

정상 실행 메시지:

```text
Local HTML Report API: http://127.0.0.1:8010
Storage folder: C:\Users\<사용자명>\Desktop\기능flow\html_report_flow\report_api\storage\reports
Uvicorn running on http://127.0.0.1:8010
```

이 PowerShell 창은 닫지 말고 그대로 둡니다. 서버가 실행 중이어야 Langflow에서 링크를 만들 수 있습니다.

## 3. 서버 상태 확인

브라우저에서 아래 주소를 엽니다.

```text
http://127.0.0.1:8010/
```

아래 텍스트가 보이면 정상입니다.

```text
alive!
```

## 4. Langflow 연결

HTML 생성 flow에서 링크 출력 경로는 아래처럼 연결합니다.

```text
04 HTML 렌더링.HTML 생성 결과
-> 05-2 공유 링크 출력.HTML 생성 결과
-> 05-2 공유 링크 출력.링크 메시지
-> Chat Output.input
```

`05-2 공유 링크 출력` 입력값:

| 입력명 | 값 |
| --- | --- |
| `HTML 생성 결과` | `04 HTML 렌더링.HTML 생성 결과` 연결 |
| `Report API 주소` | `http://127.0.0.1:8010` |
| `링크 유효시간` | `24` |

## 5. 실행 결과 확인

Langflow Playground에서 실행하면 Chat Output에 아래와 비슷한 메시지가 나옵니다.

```text
HTML 리포트가 생성되었습니다: 공정별 WIP 리포트

- 다운로드 링크: http://127.0.0.1:8010/reports/download/20260619091530_abcd...
- 만료 시간: 2026-06-20 09:15 KST
```

`다운로드 링크`를 열면 `.html` 파일이 다운로드됩니다.
다운로드 시 보이는 파일명은 Langflow가 보낸 `filename_hint` 또는 리포트 제목을 기반으로 정해지며, 한글 파일명도 지원합니다.

## 6. 저장된 파일 위치

기본 저장 위치:

```text
C:\Users\<사용자명>\Desktop\기능flow\html_report_flow\report_api\storage\reports
```

리포트 1개마다 아래 파일 2개가 생성됩니다.

```text
20260619091530_abcd....html
20260619091530_abcd....json
```

`.html`은 실제 리포트입니다. `.json`은 제목, 질문, 만료 시간 같은 메타데이터입니다.

## 7. 설정을 바꾸고 싶을 때

설정은 모두 아래 파일 상단에 있습니다.

```text
C:\Users\<사용자명>\Desktop\기능flow\html_report_flow\report_api\server.py
```

기본값:

```python
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8010
BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"
STORAGE_DIR = Path(__file__).resolve().parent / "storage"
```

### 포트 변경

8010 포트가 이미 사용 중이면 `server.py`에서 아래처럼 바꿉니다.

```python
SERVER_PORT = 8011
BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"
```

그리고 Langflow `05-2.Report API 주소`도 아래로 바꿉니다.

```text
http://127.0.0.1:8011
```

### 저장 폴더 변경

`server.py`에서 `STORAGE_DIR`을 바꿉니다.

```python
STORAGE_DIR = Path(r"C:\Users\<사용자명>\Desktop\html_report_storage")
```

실제 파일은 그 아래 `reports` 폴더에 저장됩니다.

## 8. 서버 종료와 재실행

서버 종료:

```text
Ctrl + C
```

다시 실행:

```powershell
cd C:\Users\<사용자명>\Desktop\기능flow\html_report_flow\report_api
.\.venv\Scripts\Activate.ps1
python server.py
```

서버를 껐다 켜도 만료되지 않은 로컬 파일은 남아 있습니다.

## 9. 단독 API 테스트

Langflow 연결 전에 API만 테스트하려면 서버 실행 상태에서 새 PowerShell 창을 열고 실행합니다.

```powershell
$body = @{
  title = "Local Test Report"
  question = "테스트"
  view_request = "링크 확인"
  html = "<!doctype html><html><body><h1>Local Test Report</h1></body></html>"
  ttl_hours = 24
  filename_hint = "local_test_report"
} | ConvertTo-Json -Depth 20

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8010/reports" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

응답에 `view_url`, `download_url`이 나오면 정상입니다.

## 10. 자주 나는 문제

| 증상 | 해결 |
| --- | --- |
| `05-2`에서 연결 실패 | 서버 PowerShell 창이 켜져 있는지 확인 |
| `Connection refused` | `python server.py` 실행 여부 확인 |
| `Report API 주소`가 비어 있음 | `05-2` 입력에 `http://127.0.0.1:8010` 입력 |
| 링크를 열면 404 | storage 폴더의 파일이 삭제됐거나 잘못된 report_id |
| 링크를 열면 410 | TTL 만료. Langflow에서 다시 생성 |
| 다른 사람 PC에서 링크가 안 열림 | `127.0.0.1`은 자기 PC 전용 주소입니다. 체험자는 각자 서버를 실행해야 합니다 |
| port 8010 사용 중 | `server.py`의 `SERVER_PORT`를 8011로 바꾸고 `05-2.Report API 주소`도 `http://127.0.0.1:8011`로 변경 |

## 11. 체험자에게 안내할 짧은 버전

1. `report_api` 폴더에서 `python -m venv .venv`
2. `.\.venv\Scripts\Activate.ps1`
3. `pip install -r requirements.txt`
4. `python server.py`
5. Langflow `05-2.Report API 주소`에 `http://127.0.0.1:8010` 입력
6. `05-2.링크 메시지 -> Chat Output.input` 연결
