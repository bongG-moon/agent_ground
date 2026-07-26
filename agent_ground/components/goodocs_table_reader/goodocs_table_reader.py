from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import pandas as pd
from pydantic import SecretStr

from lfx.custom import Component
from lfx.io import IntInput, Output, SecretStrInput, StrInput
from lfx.schema import DataFrame


GOODOCS_SYSTEM_COLUMNS = {
    "ROW_INDEX",
    "LastUser",
    "LastTime",
    "LastEditType",
    "FirstUser",
    "FirstTime",
    "ROW_ID",
}
GOODOCS_SYSTEM_COLUMN_KEYS = {
    re.sub(r"[^a-z0-9]", "", column.lower()) for column in GOODOCS_SYSTEM_COLUMNS
}
DEFAULT_MAX_ROWS = 5000
MAX_ALLOWED_ROWS = 100000


class GooDocsModuleNotConfiguredError(RuntimeError):
    """실제 GooDocs 모듈이 아직 연결되지 않았음을 명확하게 알립니다."""


# =============================================================================
# 실제 GooDocs 모듈 교체 구역
# =============================================================================
# 아래 Goodocs 클래스 전체를 사내에서 사용하는 실제 구현으로 교체하세요.
# 이 컴포넌트가 요구하는 최소 계약은 두 가지뿐입니다.
#
# 1. Goodocs(auth: dict) 형태로 생성할 수 있어야 합니다.
# 2. read_all()이 pandas DataFrame 또는 list[dict]를 반환해야 합니다.
#
# auth에는 USER_ID, DOC_ID, TOKEN_SOURCE, TOKEN_KEY가 들어가며,
# 시트 이름을 입력한 경우에만 SHEET_NAME도 추가됩니다.
# 별도 Python 모듈을 import하는 방식은 그 모듈이 Langflow 실행 환경에
# 설치되어 있어야 하므로, 파일 하나만 등록하는 Standalone 방식과 다릅니다.
class Goodocs:
    """실제 사내 GooDocs 구현으로 교체하기 위한 자리표시자입니다."""

    def __init__(self, auth: dict[str, Any]):
        self.auth = auth

    def read_all(self) -> Any:
        """실제 모듈로 교체하기 전에는 조회 성공을 가장하지 않습니다."""
        raise GooDocsModuleNotConfiguredError(
            "실제 GooDocs 모듈이 연결되지 않았습니다. "
            "goodocs_table_reader.py의 '실제 GooDocs 모듈 교체 구역'을 교체해 주세요."
        )


# =============================================================================
# 실제 GooDocs 모듈 교체 구역 끝
# =============================================================================


def _secret_text(value: Any) -> str:
    """SecretStr 또는 일반 문자열을 실제 인증 문자열로 변환합니다."""
    if isinstance(value, SecretStr):
        return value.get_secret_value().strip()
    get_secret_value = getattr(value, "get_secret_value", None)
    if callable(get_secret_value):
        return str(get_secret_value() or "").strip()
    return str(value or "").strip()


def _validated_max_rows(value: Any) -> int:
    """최대 출력 행 수를 안전한 정수 범위로 검증합니다."""
    try:
        max_rows = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("최대 출력 행 수는 정수로 입력해 주세요.") from exc

    if not 1 <= max_rows <= MAX_ALLOWED_ROWS:
        raise ValueError(f"최대 출력 행 수는 1~{MAX_ALLOWED_ROWS:,} 사이여야 합니다.")
    return max_rows


def _build_auth(
    document_id: Any,
    user_id: Any,
    token_source: Any,
    token_key: Any,
    sheet_name: Any = "",
) -> dict[str, str]:
    """입력값을 검증하고 실제 GooDocs 모듈에 전달할 인증 사전을 만듭니다."""
    values = {
        "문서 ID": str(document_id or "").strip(),
        "사용자 ID": str(user_id or "").strip(),
        "토큰 소스": _secret_text(token_source),
        "토큰 키": _secret_text(token_key),
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ValueError(f"필수 입력값이 없습니다: {', '.join(missing)}")

    auth = {
        "USER_ID": values["사용자 ID"],
        "DOC_ID": values["문서 ID"],
        "TOKEN_SOURCE": values["토큰 소스"],
        "TOKEN_KEY": values["토큰 키"],
    }
    normalized_sheet_name = str(sheet_name or "").strip()
    if normalized_sheet_name:
        auth["SHEET_NAME"] = normalized_sheet_name
    return auth


def _is_system_column(column_name: Any) -> bool:
    """대소문자와 구분문자 차이를 무시하고 GooDocs 관리용 열인지 판단합니다."""
    normalized = re.sub(r"[^a-z0-9]", "", str(column_name or "").strip().lower())
    return normalized in GOODOCS_SYSTEM_COLUMN_KEYS


def _is_empty_value(value: Any) -> bool:
    """시트의 완전히 빈 행을 판별하기 위한 빈 값 규칙을 적용합니다."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    try:
        is_na = pd.isna(value)
        if isinstance(is_na, bool):
            return is_na
        # numpy.bool_은 bool과 다른 타입이므로 bool 변환을 한 번 더 시도합니다.
        if type(is_na).__name__ == "bool_":
            return bool(is_na)
    except Exception:
        pass
    return False


def _records_from_result(result: Any) -> list[dict[str, Any]]:
    """실제 모듈의 반환값을 행 순서가 유지되는 레코드 목록으로 변환합니다."""
    if isinstance(result, pd.DataFrame):
        if not result.columns.is_unique:
            raise ValueError("GooDocs 조회 결과에 중복된 열 이름이 있습니다.")
        records = result.reset_index(drop=True).to_dict(orient="records")
    elif isinstance(result, list):
        if not all(isinstance(row, dict) for row in result):
            raise TypeError("GooDocs 조회 결과 목록의 모든 항목은 dict 행이어야 합니다.")
        records = result
    else:
        raise TypeError(
            "GooDocs read_all()은 pandas DataFrame 또는 list[dict]를 반환해야 합니다."
        )

    cleaned_rows: list[dict[str, Any]] = []
    for row in records:
        cleaned = {
            str(column): value
            for column, value in row.items()
            if not _is_system_column(column)
        }
        if cleaned and any(not _is_empty_value(value) for value in cleaned.values()):
            cleaned_rows.append(cleaned)
    return cleaned_rows


def read_goodocs_rows(
    document_id: Any,
    user_id: Any,
    token_source: Any,
    token_key: Any,
    sheet_name: Any = "",
    max_rows: Any = DEFAULT_MAX_ROWS,
    *,
    client_factory: Callable[[dict[str, str]], Any] | None = None,
) -> list[dict[str, Any]]:
    """GooDocs를 한 번 조회하고 후속 처리에 바로 쓸 수 있는 행 목록을 반환합니다.

    `client_factory`에 가짜 클라이언트를 전달할 수 있으므로 실제 사내 모듈이나
    네트워크 없이도 인증 계약과 결과 정제 규칙을 단위 테스트할 수 있습니다.
    """
    auth = _build_auth(document_id, user_id, token_source, token_key, sheet_name)
    output_limit = _validated_max_rows(max_rows)
    factory = client_factory or Goodocs

    try:
        client = factory(auth)
        read_all = getattr(client, "read_all", None)
        if not callable(read_all):
            raise TypeError("GooDocs 클라이언트에 호출 가능한 read_all()이 없습니다.")
        result = read_all()
    except GooDocsModuleNotConfiguredError:
        raise
    except Exception as exc:
        # 인증값이나 외부 응답 본문이 오류 메시지에 섞이지 않도록 원문은 노출하지 않습니다.
        raise RuntimeError(
            "GooDocs 문서를 조회하지 못했습니다. 실제 모듈 설정과 인증 정보를 확인해 주세요. "
            f"(오류 유형: {type(exc).__name__})"
        ) from None

    try:
        rows = _records_from_result(result)
    except (TypeError, ValueError):
        raise
    except Exception as exc:
        raise RuntimeError(
            "GooDocs 조회 결과를 데이터 테이블로 변환하지 못했습니다. "
            f"(오류 유형: {type(exc).__name__})"
        ) from None
    return rows[:output_limit]


class GooDocsTableReader(Component):
    """GooDocs 문서 한 개를 조회해 데이터 테이블 하나만 출력합니다."""

    display_name = "GooDocs 테이블 조회기"
    description = (
        "문서 ID와 인증값으로 GooDocs 문서 한 개를 조회하고 데이터 테이블만 출력합니다. "
        "실제 사내 GooDocs 모듈을 코드의 교체 구역에 넣어야 합니다."
    )
    icon = "TableProperties"
    name = "GooDocsTableReader"

    inputs = [
        StrInput(
            name="document_id",
            display_name="문서 ID",
            info="조회할 GooDocs 문서의 식별자입니다.",
            required=True,
        ),
        StrInput(
            name="user_id",
            display_name="사용자 ID",
            info="GooDocs 인증에 사용하는 사용자 ID입니다.",
            required=True,
        ),
        SecretStrInput(
            name="token_source",
            display_name="토큰 소스",
            info="GooDocs 인증에 사용하는 토큰 소스입니다.",
            required=True,
        ),
        SecretStrInput(
            name="token_key",
            display_name="토큰 키",
            info="GooDocs 인증에 사용하는 토큰 키입니다.",
            required=True,
        ),
        StrInput(
            name="sheet_name",
            display_name="시트 이름",
            info="특정 시트를 지정해야 할 때 입력합니다. 비워 두면 SHEET_NAME을 전달하지 않습니다.",
            value="",
            required=False,
            advanced=True,
        ),
        IntInput(
            name="max_rows",
            display_name="최대 출력 행 수",
            info=(
                "read_all()이 반환한 전체 결과 중 출력할 최대 행 수입니다. "
                "원본 문서의 실제 조회량을 줄이는 설정은 아닙니다."
            ),
            value=DEFAULT_MAX_ROWS,
            required=True,
            advanced=True,
        ),
    ]

    outputs = [
        Output(
            name="data_table",
            display_name="데이터 테이블",
            method="build_data_table",
            types=["DataFrame"],
            cache=False,
            tool_mode=False,
        )
    ]

    def build_data_table(self) -> DataFrame:
        """GooDocs 조회 결과를 다른 포장 없이 DataFrame으로 출력합니다."""
        rows = read_goodocs_rows(
            document_id=getattr(self, "document_id", ""),
            user_id=getattr(self, "user_id", ""),
            token_source=getattr(self, "token_source", ""),
            token_key=getattr(self, "token_key", ""),
            sheet_name=getattr(self, "sheet_name", ""),
            max_rows=getattr(self, "max_rows", DEFAULT_MAX_ROWS),
        )
        table = DataFrame(rows)
        self.status = f"조회 완료 · {len(table):,}행 · {len(table.columns):,}열"
        return table
