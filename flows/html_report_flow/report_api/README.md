# Local HTML Report API

이 서버는 Langflow가 생성한 HTML 리포트를 각 PC의 로컬 폴더에 저장하고, 보기/다운로드 링크를 반환합니다.

MongoDB와 `.env` 파일은 사용하지 않습니다. 체험자는 설치 후 아래처럼 실행하면 됩니다.

```powershell
python server.py
```

## 1. 서버 설정 위치

설정은 모두 [server.py](server.py) 파일 상단에 있습니다.

```python
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8010
BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"
STORAGE_DIR = Path(__file__).resolve().parent / "storage"
DEFAULT_TTL_HOURS = 24
MAX_TTL_HOURS = 24 * 7
MAX_HTML_BYTES = 10 * 1024 * 1024
MAX_STORAGE_BYTES = 512 * 1024 * 1024
USE_ACCESS_TOKEN = False
```

대부분은 수정하지 않아도 됩니다.

| 값 | 기본 의미 |
| --- | --- |
| `SERVER_HOST` | 로컬 PC에서만 접속할 때 `127.0.0.1` |
| `SERVER_PORT` | 서버 포트. 기본 `8010` |
| `BASE_URL` | Langflow에 반환할 링크 주소 |
| `STORAGE_DIR` | HTML 저장 폴더 |
| `DEFAULT_TTL_HOURS` | 기본 링크 유효시간 |
| `MAX_STORAGE_BYTES` | 저장소 최대 용량 |

## 2. 처음 한 번만 설치

PowerShell에서 실행합니다.

```powershell
cd C:\Users\<사용자명>\Desktop\기능flow\html_report_flow\report_api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

가상환경 실행 정책 오류가 나면 현재 PowerShell 창에서만 아래를 실행한 뒤 다시 activate 합니다.

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## 3. 서버 실행

설치 후에는 아래만 실행하면 됩니다.

```powershell
cd C:\Users\<사용자명>\Desktop\기능flow\html_report_flow\report_api
.\.venv\Scripts\Activate.ps1
python server.py
```

정상 실행되면 아래와 비슷한 로그가 나옵니다.

```text
Local HTML Report API: http://127.0.0.1:8010
Storage folder: C:\Users\<사용자명>\Desktop\기능flow\html_report_flow\report_api\storage\reports
Uvicorn running on http://127.0.0.1:8010
```

이 PowerShell 창은 닫지 않습니다. 서버가 켜져 있어야 Langflow에서 링크를 만들 수 있습니다.

## 4. 서버 상태 확인

브라우저에서 아래 주소를 엽니다.

```text
http://127.0.0.1:8010/
```

`alive!`가 보이면 정상입니다.

## 5. 저장 구조

기본 저장 폴더:

```text
C:\Users\<사용자명>\Desktop\기능flow\html_report_flow\report_api\storage\reports
```

리포트 1개가 생성되면 파일 2개가 생깁니다.

```text
20260619091530_abcd....html
20260619091530_abcd....json
```

| 파일 | 내용 |
| --- | --- |
| `.html` | 브라우저에서 보는 실제 HTML 리포트 |
| `.json` | 제목, 질문, 만료 시간, 다운로드 파일명, row 수 같은 메타데이터 |

저장소의 실제 파일명은 충돌 방지를 위해 `report_id.html` 형식으로 유지됩니다.
사용자가 다운로드할 때 보이는 파일명은 Langflow가 보낸 `filename_hint` 값을 사용하며, 한글 파일명도 지원합니다.

## 6. Langflow 연결

Langflow flow에서 `05-2 공유 링크 출력` 노드를 사용합니다.

| 05-2 입력명 | 값 |
| --- | --- |
| `HTML 생성 결과` | `04 HTML 렌더링.HTML 생성 결과` 연결 |
| `Report API 주소` | `http://127.0.0.1:8010` |
| `링크 유효시간` | `24` |

출력 연결:

| 05-2 출력명 | 연결 대상 |
| --- | --- |
| `링크 메시지` | `Chat Output.input` |

## 7. API 단독 테스트

서버 실행 상태에서 새 PowerShell 창을 열고 실행합니다.

```powershell
$body = @{
  title = "Local Test Report"
  question = "테스트 리포트"
  view_request = "보기/다운로드 링크 확인"
  html = "<!doctype html><html><body><h1>Local Test Report</h1><p>Hello</p></body></html>"
  ttl_hours = 24
  filename_hint = "local_test_report"
} | ConvertTo-Json -Depth 20

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8010/reports" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

응답의 `view_url`을 브라우저에서 열면 HTML이 보입니다.

## 8. 자주 바꾸는 설정

### 포트가 이미 사용 중일 때

[server.py](server.py) 상단에서 포트를 바꿉니다.

```python
SERVER_PORT = 8011
BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"
```

그 다음 Langflow `05-2.Report API 주소`도 아래처럼 맞춥니다.

```text
http://127.0.0.1:8011
```

### 저장 폴더를 바꾸고 싶을 때

[server.py](server.py) 상단에서 `STORAGE_DIR`을 바꿉니다.

```python
STORAGE_DIR = Path(r"C:\Users\<사용자명>\Desktop\html_report_storage")
```

실제 파일은 그 아래 `reports` 폴더에 저장됩니다.

## 9. 만료와 자동 삭제

- `ttl_hours`가 지난 리포트는 만료됩니다.
- 만료된 링크를 열면 `410 Gone`이 반환됩니다.
- 서버 시작 시, 새 리포트 생성 시, 만료된 링크 조회 시 오래된 `.html/.json` 파일을 삭제합니다.
- 전체 저장 용량이 `MAX_STORAGE_BYTES`를 넘으면 오래된 리포트부터 삭제합니다.

## 10. 주의사항

- `127.0.0.1` 링크는 서버를 실행한 PC에서만 열립니다.
- 체험 목적이면 각자 PC에서 `python server.py`로 서버를 실행하고 자기 PC에서 링크를 확인하는 방식을 권장합니다.
- 생성 HTML 안에는 비밀번호, 토큰, 내부 접속 정보 같은 민감 정보를 넣지 마세요.
