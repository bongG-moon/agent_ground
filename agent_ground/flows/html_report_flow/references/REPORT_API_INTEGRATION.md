# Report API Integration

이 문서는 `html_report_flow`가 생성한 HTML을 로컬 Report API 서버에 저장하고 클릭 가능한 링크로 받는 구조를 설명합니다.

현재 구현은 MongoDB를 사용하지 않습니다. 각 체험자가 자기 PC에서 서버를 실행하고, 생성된 HTML은 그 PC의 로컬 저장 폴더에 저장됩니다.

## 사용자 경험

사용자가 Langflow에서 질의합니다.

```text
공정별 WIP 비교 결과를 KPI 카드와 추이 그래프 중심으로 HTML 리포트로 만들어줘. 24시간 동안 볼 수 있게 링크로 줘.
```

최종 응답 예:

```json
{
  "status": "ok",
  "response_type": "html_report_link",
  "message": "HTML 리포트가 생성되었습니다: 공정별 WIP 비교 리포트\n\n- 다운로드 링크: http://127.0.0.1:8010/reports/download/20260619091530_abcd...\n- 만료 시간: 2026-06-20 09:15 KST",
  "html_report": {
    "title": "공정별 WIP 비교 리포트",
    "download_url": "http://127.0.0.1:8010/reports/download/20260619091530_abcd...",
    "expires_at": "2026-06-20T00:15:30+00:00"
  }
}
```

## 구조

```text
Langflow user question + data
-> 04 HTML 렌더링
-> 05-2 공유 링크 출력
-> POST http://127.0.0.1:8010/reports
-> report_api/server.py
-> local storage folder stores .html + .json
-> API returns view/download links
```

## 왜 로컬 Report API를 쓰는가

- Langflow Playground에는 HTML 원문을 출력할 수 있지만, 사용자가 클릭 가능한 링크로 보기에는 불편합니다.
- 로컬 Report API를 띄우면 `view_url`과 `download_url`을 바로 받을 수 있습니다.
- 체험 목적에서는 MongoDB 설치 없이 각 PC에서 실행하는 방식이 가장 단순합니다.
- 생성 파일은 로컬 폴더에 남으므로 테스트 결과 확인과 삭제가 쉽습니다.

## 저장 방식

기본 저장 위치:

```text
report_api/storage/reports
```

리포트 1개당 파일 2개가 생깁니다.

```text
{report_id}.html
{report_id}.json
```

| 파일 | 내용 |
| --- | --- |
| `.html` | 실제 HTML 리포트 |
| `.json` | 제목, 질문, 생성 시간, 만료 시간, 다운로드 파일명, plan 요약 |

## 05-2 공유 링크 출력 노드

파일:

```text
langflow_components/html_report_flow/05_report_api_publisher.py
```

입력:

| 화면 입력명 | 값 |
| --- | --- |
| `HTML 생성 결과` | `04 HTML 렌더링.HTML 생성 결과` |
| `Report API 주소` | `http://127.0.0.1:8010` |
| `링크 유효시간` | `24` |

출력:

| Output | 용도 |
| --- | --- |
| `링크 메시지` | `Chat Output.input`에 연결해 사용자에게 링크 표시 |

## Report API 서버 실행

처음 한 번:

```powershell
cd C:\Users\<사용자명>\Desktop\기능flow\html_report_flow\report_api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

매번 실행:

```powershell
cd C:\Users\<사용자명>\Desktop\기능flow\html_report_flow\report_api
.\.venv\Scripts\Activate.ps1
python server.py
```

정상 확인:

```text
http://127.0.0.1:8010/
```

브라우저에서 `alive!`가 보이면 됩니다.

## 실행 설정

이 서버는 `.env` 파일 없이 `server.py` 하나로 실행되도록 구성했습니다. 포트, 저장 위치, 기본 만료시간을 바꾸고 싶으면 아래 파일 맨 위 설정값을 직접 수정합니다.

```text
report_api/server.py
```

주요 설정값:

| 설정값 | 기본값 | 설명 |
| --- | --- | --- |
| `SERVER_HOST` | `127.0.0.1` | 서버가 열릴 주소 |
| `SERVER_PORT` | `8010` | 서버 포트 |
| `BASE_URL` | `http://127.0.0.1:8010` | 응답 링크에 들어갈 base URL |
| `STORAGE_DIR` | `report_api/storage` | 리포트 저장 폴더. 실제 파일은 하위 `reports`에 저장 |
| `DEFAULT_TTL_HOURS` | `24` | 기본 링크 유효시간 |
| `MAX_TTL_HOURS` | `168` | 최대 링크 유효시간 |
| `MAX_HTML_BYTES` | `10485760` | HTML 1개 최대 크기 |
| `MAX_STORAGE_BYTES` | `536870912` | 전체 저장소 최대 크기 |
| `USE_ACCESS_TOKEN` | `False` | true면 링크에 token query가 붙음 |

## 만료와 삭제

- 생성 시 `expires_at`이 `.json` 메타데이터에 저장됩니다.
- 서버 시작 시 만료된 파일을 삭제합니다.
- 새 리포트 생성 시 만료된 파일을 삭제합니다.
- 만료된 링크를 열면 해당 파일을 삭제하고 `410 Gone`을 반환합니다.
- 저장소 용량이 `MAX_STORAGE_BYTES`를 넘으면 오래된 리포트부터 삭제합니다.

## 주의사항

- `127.0.0.1` 링크는 서버를 실행한 PC에서만 열립니다.
- 다른 사람 PC에서 링크를 열어야 한다면 해당 PC에서 서버를 실행하거나, 네트워크 접근 가능한 주소로 서버를 열고 `server.py`의 `SERVER_HOST`, `SERVER_PORT`, `BASE_URL`을 그 주소에 맞게 수정해야 합니다.
- 체험 목적이라면 각자 PC에서 서버를 실행하고 각자 생성한 링크를 확인하는 방식을 권장합니다.
