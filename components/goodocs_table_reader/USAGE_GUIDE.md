# GooDocs 테이블 조회기 사용 가이드

## 이 컴포넌트가 하는 일

`GooDocs 테이블 조회기`는 GooDocs 문서 한 개를 읽어 Langflow의 `DataFrame` 하나로 출력하는 최소 단위 컴포넌트입니다.

기존 `Goodocs Data` 컴포넌트처럼 다음 기능을 함께 수행하지 않습니다.

- `data_request` 구조 해석
- 여러 데이터 소스 중 GooDocs 요청 선택
- 여러 문서 요청의 일괄 처리
- `success`, `source_type`, `row_count` 같은 결과 포장
- 테스트용 더미 데이터 반환

따라서 문서 ID와 인증값을 직접 입력하고, 출력 포트에서는 실제 데이터 테이블만 받습니다.

## 등록 방법

1. `goodocs_table_reader.py` 파일을 엽니다.
2. 코드 상단의 `실제 GooDocs 모듈 교체 구역`을 찾습니다.
3. 자리표시자 `Goodocs` 클래스를 사내에서 사용하는 실제 구현으로 교체합니다.
4. 완성된 Python 파일 하나를 Langflow의 Custom Component에 등록합니다.

파일 안에 실제 클래스를 함께 넣으면 형제 파일 import가 필요 없는 Standalone 방식이 유지됩니다.

별도 모듈을 다음처럼 import하려면 해당 모듈이 Langflow가 실행되는 Python 환경에도 설치되어 있어야 합니다.

```python
from company_goodocs import Goodocs
```

이 경우에는 Python 파일 하나만 등록하는 것만으로 실행되지 않을 수 있습니다.

## 실제 GooDocs 모듈의 최소 계약

컴포넌트는 실제 모듈을 다음 방식으로 호출합니다.

```python
client = Goodocs(auth)
result = client.read_all()
```

`auth`에는 다음 키가 들어갑니다.

```python
{
    "USER_ID": "사용자 ID",
    "DOC_ID": "문서 ID",
    "TOKEN_SOURCE": "토큰 소스",
    "TOKEN_KEY": "토큰 키",
    # 시트 이름을 입력한 경우에만 포함됩니다.
    "SHEET_NAME": "시트 이름",
}
```

`read_all()`의 반환값은 다음 중 하나여야 합니다.

- pandas `DataFrame`
- `list[dict]`

`dict` 한 개, 일반 문자열, 튜플, `list[str]` 같은 형식은 오류로 처리합니다. 잘못된 형식을 빈 테이블로 바꾸면 조회 성공으로 오해할 수 있기 때문입니다.

## 입력

| 화면 이름 | 코드 이름 | 필수 | 설명 |
| --- | --- | --- | --- |
| 문서 ID | `document_id` | 예 | 조회할 GooDocs 문서의 식별자 |
| 사용자 ID | `user_id` | 예 | GooDocs 인증 사용자 ID |
| 토큰 소스 | `token_source` | 예 | Secret 입력으로 받는 인증값 |
| 토큰 키 | `token_key` | 예 | Secret 입력으로 받는 인증값 |
| 시트 이름 | `sheet_name` | 아니요 | 입력한 경우 `SHEET_NAME`으로 전달 |
| 최대 출력 행 수 | `max_rows` | 예 | 기본 5,000행, 허용 범위 1~100,000행 |

`최대 출력 행 수`는 `read_all()`이 끝난 후 출력할 행 수를 제한합니다. GooDocs 서버에서 읽어 오는 원본 문서의 양 자체를 줄이는 옵션은 아닙니다.

## 출력

출력 포트는 `데이터 테이블(data_table)` 하나뿐입니다.

```text
GooDocs 테이블 조회기.data_table → 후속 DataFrame 처리 컴포넌트
```

출력 테이블에는 `success`, `error`, `row_count` 같은 제어용 행이나 감싸기 객체를 추가하지 않습니다.

조회가 성공했지만 데이터가 0건이면 정상적인 빈 `DataFrame`을 반환합니다. 조회 자체가 실패하면 빈 테이블을 반환하지 않고 실행 오류를 발생시킵니다.

## 자동 정제 규칙

GooDocs가 관리 목적으로 붙이는 다음 열은 출력에서 제거합니다.

- `ROW_INDEX`
- `LastUser`
- `LastTime`
- `LastEditType`
- `FirstUser`
- `FirstTime`
- `ROW_ID`

대소문자, 공백, 밑줄 차이는 무시합니다. 예를 들어 `row_id`, `ROW ID`, `Row-Id`도 같은 관리용 열로 판단합니다.

관리용 열을 제거한 뒤 모든 값이 비어 있는 행도 제거합니다. 나머지 행 순서와 열 순서는 원본 순서를 유지합니다.

## 실패 처리 원칙

이 컴포넌트에는 더미 성공이나 자동 fallback이 없습니다.

- 실제 모듈 미연결: 교체 구역을 안내하는 오류
- 필수 입력 누락: 누락된 입력 이름을 표시하는 오류
- 인증 또는 외부 조회 실패: 인증값을 노출하지 않는 조회 오류
- 잘못된 반환 형식: 기대하는 반환 형식을 알려 주는 오류
- 중복된 열 이름: 데이터 유실을 막기 위한 오류

토큰 값, 전체 인증 사전, 외부 응답 본문은 상태 메시지나 오류 메시지에 출력하지 않습니다.

## 실제 모듈 없이 테스트하는 방법

순수 함수 `read_goodocs_rows()`는 `client_factory`를 받을 수 있습니다. 테스트에서는 가짜 클라이언트를 주입해 네트워크 연결 없이 계약을 확인할 수 있습니다.

```python
class FakeGoodocs:
    def __init__(self, auth):
        self.auth = auth

    def read_all(self):
        return [
            {"DATE": "2026-07-12", "PLAN_QTY": 120, "ROW_ID": "제거됨"},
            {"DATE": "2026-07-13", "PLAN_QTY": 80, "ROW_ID": "제거됨"},
        ]


rows = read_goodocs_rows(
    document_id="DOC-001",
    user_id="USER-001",
    token_source="TOKEN-SOURCE",
    token_key="TOKEN-KEY",
    max_rows=100,
    client_factory=FakeGoodocs,
)
```

이 테스트는 가짜 데이터로 컴포넌트 실행 성공을 꾸미기 위한 기능이 아닙니다. 개발자가 인증 사전과 테이블 변환 규칙을 검증하기 위한 코드 수준의 주입 지점입니다.

## 실제 환경 확인 항목

사용자 완료 승인 전에는 상태를 `user_testing`으로 유지합니다.

- 실제 `Goodocs` 클래스가 `auth` 사전을 올바르게 받는지
- 문서 ID만 입력했을 때 원하는 문서를 읽는지
- 시트 이름 입력 시 실제 모듈이 `SHEET_NAME`을 사용하는지
- 숫자와 날짜 열의 타입이 후속 DataFrame 처리에서 유지되는지
- 0건 조회와 인증 실패가 명확히 구분되는지
- 토큰 값이 Langflow 상태·로그·오류 화면에 노출되지 않는지
