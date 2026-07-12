from __future__ import annotations

import ast
import json
import re
from copy import deepcopy
from typing import Any, Dict

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message


SUPPORTED_SOURCE_TYPES = {"oracle", "h_api", "datalake", "goodocs"}


def _make_message(text: str) -> Any:
    """Langflow 버전에 따라 Message 생성자 모양이 달라지는 부분을 흡수합니다."""
    # 최신/구버전 Langflow가 Message(text=...) 또는 Message(content=...)를 다르게 받을 수 있어 순서대로 시도합니다.
    try:
        return Message(text=text)
    except TypeError:
        try:
            return Message(content=text)
        except TypeError:
            return Message(text)


def _make_data(payload: Dict[str, Any]) -> Any:
    """Langflow 버전에 따라 Data 생성자 모양이 달라지는 부분을 흡수합니다."""
    try:
        return Data(data=payload)
    except TypeError:
        return Data(payload)


def _text_from_value(value: Any) -> str:
    """LLM/Message/Data/dict 어디에서 오든 source 설명 텍스트를 찾아냅니다."""
    # 연결이 비어 있으면 빈 문자열로 통일합니다.
    if value is None:
        return ""

    # Text Input이나 Chat Input으로 직접 온 값입니다.
    if isinstance(value, str):
        return value.strip()

    # dict payload에서는 Langflow/LLM wrapper마다 다른 key를 쓸 수 있어 흔한 key를 순서대로 확인합니다.
    if isinstance(value, dict):
        for key in ("llm_result", "llm_text", "text", "content", "message", "response", "output", "result"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
            if isinstance(item, dict):
                # 값이 한 번 더 감싸져 있으면 재귀적으로 안쪽 텍스트를 찾습니다.
                nested = _text_from_value(item)
                if nested:
                    return nested

    # Langflow Message 객체는 버전에 따라 text/content/message 속성을 가질 수 있습니다.
    for attr in ("text", "content", "message"):
        item = getattr(value, attr, None)
        if isinstance(item, str) and item.strip():
            return item.strip()

    # Langflow Data 객체는 .data에 dict payload를 담습니다.
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return _text_from_value(data)
    return ""


def _payload_from_value(value: Any) -> Any:
    """이미 JSON 형태인 입력은 그대로, 텍스트 입력은 JSON/Python literal로 파싱합니다."""
    # 입력이 비어 있으면 payload 없음으로 처리합니다.
    if value is None:
        return {}

    # dict/list는 이미 JSON 구조이므로 복사해서 반환합니다.
    if isinstance(value, (dict, list)):
        return deepcopy(value)

    # Langflow Data 객체는 .data 안쪽을 우선 사용합니다.
    data = getattr(value, "data", None)
    if isinstance(data, (dict, list)):
        return deepcopy(data)

    # Message/Text 객체는 텍스트를 꺼내 JSON/Python literal로 해석합니다.
    text = _text_from_value(value)
    if text:
        parsed = _parse_json_or_python(text)
        if parsed is not None:
            return parsed
    return {}


def _extract_json_text(text: str) -> str:
    """마크다운 코드블록이나 설명문에 섞인 JSON object/list 부분만 잘라냅니다."""
    # LLM은 JSON만 반환하라고 해도 ```json ... ``` 코드블록으로 감싸는 경우가 있습니다.
    # 먼저 앞뒤 공백을 제거하고, 코드블록이 있으면 블록 내부만 파싱 대상으로 좁힙니다.
    cleaned = str(text or "").strip()
    # 정규식은 ```json 으로 시작해서 다음 ``` 전까지의 본문만 찾습니다.
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        cleaned = fence.group(1).strip()

    # 응답 앞뒤에 "아래 JSON입니다" 같은 설명문이 붙을 수 있으므로
    # JSON object({ ... }) 후보와 JSON list([ ... ]) 후보를 둘 다 찾아둡니다.
    candidates: list[tuple[int, int, str]] = []
    first_object = cleaned.find("{")
    last_object = cleaned.rfind("}")
    if 0 <= first_object < last_object:
        candidates.append((first_object, last_object, cleaned[first_object : last_object + 1]))

    # source_catalog는 object로 오는 경우가 많지만, LLM이 source 배열만 반환할 수도 있습니다.
    # 그래서 list 후보도 별도로 잘라두고 아래에서 더 적절한 후보를 고릅니다.
    first_list = cleaned.find("[")
    last_list = cleaned.rfind("]")
    if 0 <= first_list < last_list:
        candidates.append((first_list, last_list, cleaned[first_list : last_list + 1]))

    if candidates:
        # 설명문 중간에 JSON이 있을 때는 가장 먼저 시작하는 후보가 실제 JSON일 가능성이 큽니다.
        # 시작 위치가 같다면 더 긴 후보를 고르면 중첩 JSON을 중간에서 잘라먹는 일을 줄일 수 있습니다.
        candidates.sort(key=lambda item: (item[0], -(item[1] - item[0])))
        return candidates[0][2]

    # JSON 괄호를 찾지 못한 경우에는 원문 전체를 그대로 반환합니다.
    # 이후 단계의 json.loads / ast.literal_eval이 실패하면 줄글 파서로 넘어갑니다.
    return cleaned


def _parse_json_or_python(text: str) -> Any:
    """JSON을 우선 파싱하고, 작은따옴표 dict 같은 Python literal도 보조로 허용합니다."""
    # 코드블록/설명문을 제거한 JSON 후보만 파서에 넣습니다.
    cleaned = _extract_json_text(text)

    # 표준 JSON을 먼저 시도하고, Python literal은 보조로만 허용합니다.
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(cleaned)
            if isinstance(parsed, str) and parsed.strip() and parsed.strip() != cleaned:
                # LLM이 JSON 문자열 안에 다시 JSON을 문자열로 넣는 경우가 있어 한 번 더 파싱합니다.
                nested = _parse_json_or_python(parsed)
                if nested is not None:
                    return nested
            return parsed
        except Exception:
            pass

    # 모두 실패하면 줄글 parser가 처리할 수 있도록 None을 반환합니다.
    return None


def _as_list(value: Any) -> list[Any]:
    """None/단일 값/tuple/set을 반복 처리 가능한 list로 맞춥니다."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    return [value]


def _string_list(value: Any) -> list[str]:
    """쉼표, 세미콜론, 줄바꿈으로 적은 값을 깨끗한 문자열 목록으로 바꿉니다."""
    result: list[str] = []
    # 문자열은 사람이 직접 입력한 목록일 가능성이 높아 구분자로 나눕니다.
    if isinstance(value, str):
        items = re.split(r"[,;\n]", value)
    else:
        items = _as_list(value)

    # 빈 값은 제외하고 앞뒤 공백을 제거합니다.
    for item in items:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result


PARAM_FORMAT_PATTERN = r"YYYY-MM-DD|YYYY/MM/DD|YYYYMMDD|YYYY-MM|YYYY/MM|YYYYMM|YYYY|YYMMDD|YY-MM-DD|text|string|number|integer|int|float|decimal|date|datetime|timestamp"


def _clean_param_format(value: Any) -> str:
    """DATE 같은 변수의 값 형식을 사람이 읽기 좋은 문자열로 정리합니다."""
    # 문장 끝 조사/마침표가 붙어도 실제 형식값만 남기기 위한 작은 정리 함수입니다.
    text = str(value or "").strip()
    text = re.sub(r"\s*(형식|format|포맷)\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*(입니다|이다|입니다\.|이다\.)\s*$", "", text)
    return text.strip(" .。")


def _param_formats_from_value(value: Any) -> Dict[str, str]:
    """dict 또는 DATE=YYYYMMDD 문자열을 param_formats dict로 변환합니다."""
    result: Dict[str, str] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key or "").strip()
            fmt = _clean_param_format(item)
            if name and fmt:
                result[name] = fmt
        return result

    # Text Input에서는 "DATE=YYYYMMDD, FROM_YM=YYYYMM"처럼 간단히 적을 수 있게 합니다.
    for part in re.split(r"[,;\n]", str(value or "")):
        match = re.match(r"\s*([A-Za-z][A-Za-z0-9_]*)\s*(?:=|:)\s*(.+?)\s*$", part)
        if match:
            result[match.group(1)] = _clean_param_format(match.group(2))
    return result


def _param_formats_from_sentence(line: str) -> Dict[str, str]:
    """'DATE 형식은 YYYYMMDD' 같은 줄글에서 변수 형식을 추출합니다."""
    result: Dict[str, str] = {}
    text = str(line or "")
    if "형식" not in text and "포맷" not in text and "format" not in text.lower():
        return result

    # "DATE 형식은 YYYYMMDD" 또는 "DATE format: YYYY-MM-DD" 형태를 찾습니다.
    pattern_after_label = rf"([A-Za-z][A-Za-z0-9_]*)\s*(?:의\s*)?(?:값\s*)?(?:형식|포맷|format)\s*(?:은|는|:|=)?\s*({PARAM_FORMAT_PATTERN})"
    for match in re.finditer(pattern_after_label, text, flags=re.IGNORECASE):
        result[match.group(1)] = _clean_param_format(match.group(2))

    # "FROM_YM, TO_YM 형식은 YYYYMM"처럼 여러 변수를 한꺼번에 적은 문장도 처리합니다.
    group_pattern = rf"([A-Za-z][A-Za-z0-9_,\s]*?)\s*(?:형식|포맷|format)\s*(?:은|는|:|=)\s*({PARAM_FORMAT_PATTERN})"
    for match in re.finditer(group_pattern, text, flags=re.IGNORECASE):
        for name in _identifier_list(match.group(1)):
            result[name] = _clean_param_format(match.group(2))

    # "DATE는 YYYYMMDD 형식"처럼 형식값이 먼저 나오는 문장도 허용합니다.
    pattern_before_label = rf"([A-Za-z][A-Za-z0-9_]*)\s*(?:은|는|:|=)\s*({PARAM_FORMAT_PATTERN})\s*(?:형식|포맷|format)"
    for match in re.finditer(pattern_before_label, text, flags=re.IGNORECASE):
        result[match.group(1)] = _clean_param_format(match.group(2))
    return result


def _source_type(value: Any) -> str:
    """사용자가 쓴 source_type 별칭을 내부 표준값으로 맞춥니다."""
    # 하이픈/공백 표기는 underscore로 통일해 비교합니다.
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "oracle_db": "oracle",
        "oracledb": "oracle",
        "hapi": "h_api",
        "h_api": "h_api",
        "lake": "datalake",
        "lakehouse": "datalake",
        "goodoc": "goodocs",
    }
    return aliases.get(text, text)


def _placeholder_params(query_template: str) -> list[str]:
    """쿼리 템플릿의 {DATE}, {YM} 같은 변수를 required_params 후보로 추출합니다."""
    params: list[str] = []
    # SQL 템플릿에서 중괄호로 감싼 모든 placeholder를 찾습니다.
    # SQL 템플릿 안의 {DATE}, {OPER_NAME} 같은 placeholder 이름만 추출합니다.
    for match in re.finditer(r"\{([^{}]+)\}", str(query_template or "")):
        key = match.group(1).strip()
        if key and key not in params:
            params.append(key)
    return params


def _add_unique_text(values: list[str], text: Any) -> None:
    """중복 없이 문자열 목록에 값을 추가합니다."""
    # 사람이 입력한 공백을 제거한 뒤 빈 값과 중복 값은 추가하지 않습니다.
    item = str(text or "").strip()
    if item and item not in values:
        values.append(item)


def _quoted_values(text: str) -> list[str]:
    """예시 질문처럼 따옴표 안에 들어간 값을 목록으로 뽑습니다."""
    result: list[str] = []
    # "..." 또는 한글 문서에서 복사된 “...” 따옴표를 모두 허용합니다.
    # 예시 질문에서 "..." 또는 “...” 따옴표 안쪽 문장만 추출합니다.
    for match in re.finditer(r'["“”]([^"“”]+)["“”]', str(text or "")):
        _add_unique_text(result, match.group(1))
    return result


def _identifier_list(text: str) -> list[str]:
    """LOT_ID, DATE 같은 영문/숫자/underscore 파라미터 후보를 뽑습니다."""
    result: list[str] = []
    # SQL/파라미터 이름은 보통 영문자로 시작하고 숫자/underscore를 포함합니다.
    # LOT_ID처럼 영문자로 시작하고 숫자/underscore가 이어지는 식별자만 찾습니다.
    for match in re.finditer(r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_]*)(?![A-Za-z0-9_])", str(text or "")):
        _add_unique_text(result, match.group(1))
    return result


def _clean_sentence_value(text: str) -> str:
    """'입니다', '이다' 같은 문장 끝 표현을 제거해 설정값만 남깁니다."""
    # 자연어 문장에서 값 뒤에 붙은 종결 표현과 마침표를 제거합니다.
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"\s*(입니다|이다|입니다\.|이다\.)\s*$", "", cleaned)
    cleaned = cleaned.strip(" .。")
    return cleaned.strip('"“”')


def _infer_source_type_from_text(text: str) -> str:
    """줄글 설명 안의 Oracle/H-API/Datalake/Goodocs 단어로 source_type을 추정합니다."""
    # source_type을 명시하지 않아도 대표 키워드가 있으면 표준 source_type으로 추정합니다.
    lower = str(text or "").lower()
    if "goodocs" in lower or "goodoc" in lower:
        return "goodocs"
    if "h-api" in lower or "h api" in lower or "hapi" in lower:
        return "h_api"
    if "datalake" in lower or "data lake" in lower or "lakehouse" in lower:
        return "datalake"
    if "oracle" in lower or "오라클" in lower:
        return "oracle"
    return ""


def _keywords_from_sentence(line: str) -> list[str]:
    """'같은 말이 나오면' 앞쪽을 keywords 후보로 해석합니다."""
    # "생산, 생산량 같은 말이 나오면..."에서 marker 앞쪽만 keyword 후보로 봅니다.
    text = str(line or "").strip()
    for marker in ("같은 말이 나오면", "키워드는", "keywords:", "keyword:"):
        if marker in text:
            text = text.split(marker, 1)[0]
            break
    return _string_list(text)


def _required_params_from_sentence(line: str) -> list[str]:
    """'DATE가 필수 파라미터다' 같은 문장에서 required_params를 추출합니다."""
    result: list[str] = []
    text = str(line or "")
    # "필수 파라미터" 앞쪽에 있는 영문 식별자를 변수명 후보로 사용합니다.
    before_required = text.split("필수 파라미터", 1)[0]
    for item in _identifier_list(before_required):
        if item.lower() not in {"params", "param", "parameter", "required"}:
            _add_unique_text(result, item)
    return result


def _param_order_from_sentence(line: str) -> list[str]:
    """'bindParams 순서는 LOT_ID다' 같은 문장에서 param_order를 추출합니다."""
    text = str(line or "")
    if "순서" not in text:
        return []
    # "순서" 뒤쪽에 나오는 영문 식별자를 bindParams 순서로 사용합니다.
    after_order = text.split("순서", 1)[1]
    return _identifier_list(after_order)


def _response_path_from_sentence(line: str) -> str:
    """'data 필드 안에 row 배열' 같은 설명을 response_path=data.row로 바꿉니다."""
    # 현재 간단 flow에서는 가장 흔한 H-API 응답 형태인 data.row만 자연어로 추정합니다.
    text = str(line or "")
    if "data" in text and "row" in text and ("응답" in text or "response" in text.lower()):
        return "data.row"
    return ""


def _db_key_from_oracle_sentence(line: str) -> str:
    """'Oracle PKG_RPT에서 조회' 같은 문장에서 db_key를 추출합니다."""
    # Oracle 바로 뒤의 영문/숫자/underscore 토큰을 DB key로 봅니다.
    match = re.search(r"Oracle\s+([A-Za-z0-9_]+)", str(line or ""), flags=re.IGNORECASE)
    return match.group(1) if match else ""


def _api_url_from_sentence(line: str) -> str:
    """줄글 안의 http/https URL을 api_url로 추출합니다."""
    # 문장 중간에 있는 첫 번째 URL을 API endpoint로 사용합니다.
    match = re.search(r"https?://\S+", str(line or ""))
    if not match:
        return ""
    return match.group(0).rstrip(" .。")


def _simple_catalog_blocks(text: str) -> list[Dict[str, Any]]:
    """LLM 없이 줄글로 쓴 source 설명을 source별 dict 목록으로 나눕니다."""
    # 사용자가 여러 source를 한 번에 적을 때는 --- 구분선을 쓰도록 안내했습니다.
    # 여기서는 각 블록을 하나의 source 설명으로 보고 독립적으로 파싱합니다.
    # 줄 전체가 --- 인 부분을 source 구분선으로 보고 여러 source 설명을 나눕니다.
    blocks = re.split(r"(?m)^\s*---+\s*$", str(text or "").strip())
    sources: list[Dict[str, Any]] = []

    for block in blocks:
        # source에는 표준 필드(name, source_type 등)를, source_config에는 실행 설정만 모읍니다.
        # 이렇게 분리해야 뒤쪽 data node가 source_config만 보고 실행할 수 있습니다.
        source: Dict[str, Any] = {}
        source_config: Dict[str, Any] = {}
        description_lines: list[str] = []
        query_lines: list[str] = []
        lines = block.splitlines()
        index = 0

        while index < len(lines):
            line = lines[index].strip()
            index += 1
            if not line:
                continue

            if "쿼리는 아래와 같다" in line or line.lower().startswith(("query:", "sql:", "query_template:")):
                # SQL은 줄바꿈이 의미 있는 경우가 많으므로, "쿼리는 아래와 같다" 이후의 모든 줄을
                # query_template로 보존합니다. 사용자가 복사한 SQL을 최대한 그대로 유지하기 위함입니다.
                if ":" in line and line.split(":", 1)[1].strip():
                    query_lines.append(line.split(":", 1)[1].strip())
                while index < len(lines):
                    query_line = lines[index].rstrip()
                    index += 1
                    if query_line.strip() or query_lines:
                        query_lines.append(query_line)
                continue

            # source: production, db_key: PKG_RPT 같은 key:value 한 줄을 찾습니다.
            key_match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", line)
            if key_match:
                # source: production 처럼 명시적인 key:value 형식은 가장 확실한 신호입니다.
                # 표준 필드는 source에 넣고, URL/쿼리/문서번호 같은 실행값은 source_config에 넣습니다.
                key = key_match.group(1).strip()
                value = key_match.group(2).strip()
                if key in ("source", "name", "source_name"):
                    source["name"] = value
                elif key == "source_type":
                    source["source_type"] = _source_type(value)
                elif key in ("keywords", "aliases", "example_questions", "required_params", "param_order"):
                    source[key] = _string_list(value)
                elif key in ("param_formats", "param_format", "value_formats", "value_format"):
                    source["param_formats"] = _param_formats_from_value(value)
                elif key in ("doc_id", "document_id", "db_key", "api_url", "url", "query_template"):
                    source_config[key] = value
                else:
                    source[key] = value
                continue

            param_formats = _param_formats_from_sentence(line)
            if param_formats:
                # DATE=YYYYMMDD 같은 값 형식은 LLM이 params를 만들 때 참고해야 하므로 source 필드로 보존합니다.
                current_formats = source.get("param_formats") if isinstance(source.get("param_formats"), dict) else {}
                current_formats.update(param_formats)
                source["param_formats"] = current_formats
                continue

            if "필수 파라미터" in line:
                # "DATE가 필수 파라미터다" 같은 자연어 문장에서 필요한 변수명을 뽑습니다.
                # param_order가 같이 적혀 있으면 H-API bindParams 순서에도 사용합니다.
                required_params = source.get("required_params") if isinstance(source.get("required_params"), list) else []
                for param in _required_params_from_sentence(line):
                    _add_unique_text(required_params, param)
                source["required_params"] = required_params

                param_order = source.get("param_order") if isinstance(source.get("param_order"), list) else []
                for param in _param_order_from_sentence(line):
                    _add_unique_text(param_order, param)
                if param_order:
                    source["param_order"] = param_order
                continue

            lower_line = line.lower()
            if "예시 질문" in line or re.match(r"^(example|examples|example_questions)\b", lower_line):
                # 예시 질문은 나중에 LLM이 어떤 source를 골라야 하는지 판단하는 힌트입니다.
                # 따옴표 안에 있는 문장만 뽑아 example_questions에 저장합니다.
                questions = source.get("example_questions") if isinstance(source.get("example_questions"), list) else []
                for question in _quoted_values(line):
                    _add_unique_text(questions, question)
                source["example_questions"] = questions
                continue

            response_path = _response_path_from_sentence(line)
            if response_path:
                # H-API 응답에서 실제 row 배열이 들어있는 위치를 source_config에 저장합니다.
                # 예: "data 필드 안에 row 배열" -> data.row
                source_config["response_path"] = response_path
                continue

            api_url = _api_url_from_sentence(line)
            if api_url:
                # URL은 문장 어디에 있어도 API 호출에 필요한 실행값이므로 source_config로 이동합니다.
                source_config["api_url"] = api_url
                continue

            if "문서번호" in line or "doc_id" in line.lower() or "document_id" in line.lower():
                # Goodocs는 doc_id가 핵심 실행값입니다.
                # "문서번호는 XXX이다" 같은 문장을 사람이 읽는 조사/마침표 없이 값만 남깁니다.
                value = line
                if ":" in value:
                    value = value.split(":", 1)[1]
                elif "는" in value:
                    value = value.split("는", 1)[1]
                source_config["doc_id"] = _clean_sentence_value(value)
                continue

            if "같은 말이 나오면" in line or "키워드" in line or "keyword" in line.lower():
                # "생산, output 같은 말이 나오면..." 문장에서 검색 키워드를 추출합니다.
                # 이 값은 이후 자연어 요청을 어떤 source로 보낼지 판단할 때 사용됩니다.
                keywords = source.get("keywords") if isinstance(source.get("keywords"), list) else []
                for keyword in _keywords_from_sentence(line):
                    if keyword not in keywords:
                        keywords.append(keyword)
                source["keywords"] = keywords
                continue

            source_type = _infer_source_type_from_text(line)
            if source_type:
                # "Oracle에서 조회한다", "H-API로 조회한다"처럼 source_type이 문장에 섞인 경우입니다.
                # Oracle 문장에는 db_key가 같이 붙는 경우가 많아 함께 추출합니다.
                if not source.get("source_type"):
                    source["source_type"] = source_type
                db_key = _db_key_from_oracle_sentence(line)
                if db_key and not source_config.get("db_key"):
                    source_config["db_key"] = db_key
                description_lines.append(line)
                continue

            description_lines.append(line)

        if source or source_config or description_lines:
            # 명시 description이 없으면, 파싱 중 실행 설정으로 분류되지 않은 문장들을 설명으로 묶습니다.
            # query_lines가 있으면 줄바꿈을 보존해서 source_config.query_template에 저장합니다.
            if description_lines and not source.get("description"):
                source["description"] = " ".join(description_lines).strip()
            if query_lines:
                source_config["query_template"] = "\n".join(query_lines).strip()
            if source_config:
                source["source_config"] = source_config
            sources.append(source)

    return sources


def _copy_runtime_config(source: Dict[str, Any]) -> Dict[str, Any]:
    """query_template, api_url, doc_id처럼 실행에 필요한 설정만 source_config에 모읍니다."""
    # source_config가 이미 있으면 그것을 기본으로 두고, 상위에 흩어진 실행 key를 추가로 모읍니다.
    config = deepcopy(source.get("source_config")) if isinstance(source.get("source_config"), dict) else {}

    # 아래 key들은 source 설명이 아니라 실제 data node 실행에 쓰이는 설정값입니다.
    runtime_keys = [
        "db_key",
        "query_template",
        "sql_template",
        "sql",
        "query",
        "api_url",
        "url",
        "timeout",
        "response_path",
        "doc_id",
        "document_id",
    ]
    for key in runtime_keys:
        if source.get(key) not in (None, "", [], {}) and key not in config:
            config[key] = deepcopy(source[key])

    # url/document_id/sql 같은 별칭은 data node가 기대하는 표준 key로 통일합니다.
    if config.get("url") and not config.get("api_url"):
        config["api_url"] = config["url"]
    if config.get("document_id") and not config.get("doc_id"):
        config["doc_id"] = config["document_id"]
    for alias in ("sql_template", "sql", "query"):
        if config.get(alias) and not config.get("query_template"):
            config["query_template"] = config[alias]
    return config


def _source_name(source: Dict[str, Any], index: int) -> str:
    """source 이름이 없을 때도 source_1 같은 안전한 이름을 보장합니다."""
    # LLM이나 줄글 parser가 source 이름을 여러 key 중 하나로 만들 수 있어 순서대로 확인합니다.
    for key in ("name", "source", "source_name", "dataset_key", "tool_name"):
        text = str(source.get(key) or "").strip()
        if text:
            if "\n" in text:
                # 잘못 파싱되어 여러 줄이 name에 들어온 경우 첫 줄만 사용합니다.
                text = text.splitlines()[0].strip()
            if text.lower().startswith("source:"):
                # "source: production"처럼 값까지 들어온 경우 production만 남깁니다.
                text = text.split(":", 1)[1].strip()
            if not text:
                continue
            return text

    # 이름을 전혀 못 찾으면 source_1, source_2처럼 안전한 fallback 이름을 만듭니다.
    return f"source_{index}"


def _normalize_one_source(raw_source: Dict[str, Any], index: int) -> tuple[str, Dict[str, Any], list[str], list[str]]:
    """source 한 개를 표준 필드로 정리하고 누락 정보 질문을 만듭니다."""
    # 원본 source는 그대로 보존하기 위해 복사본에서 정규화합니다.
    # warnings는 동작은 가능하지만 주의가 필요한 정보, questions는 실행에 필요한 누락 정보입니다.
    warnings: list[str] = []
    questions: list[str] = []
    source = deepcopy(raw_source)
    name = _source_name(source, index)
    source_type = _source_type(source.get("source_type") or source.get("type") or source.get("source"))
    config = _copy_runtime_config(source)

    # required_params가 빠져 있어도 SQL 템플릿에 {DATE}, {YM} 같은 placeholder가 있으면
    # 실행에 필요한 변수로 볼 수 있으므로 자동으로 보강합니다.
    required_params = _string_list(source.get("required_params"))
    inferred_params = _placeholder_params(str(config.get("query_template") or ""))
    for param in inferred_params:
        if param not in required_params:
            required_params.append(param)

    # H-API bindParams처럼 순서가 필요한 source도 있으므로 param_order를 보장합니다.
    # 별도 순서가 없으면 required_params 순서를 그대로 사용합니다.
    param_order = _string_list(source.get("param_order"))
    if not param_order:
        param_order = list(required_params)

    # downstream 노드들이 공통으로 기대하는 catalog 모양입니다.
    # 실행 설정은 source_config 하나에만 모아두어 중복 필드를 줄입니다.
    normalized = {
        "source_type": source_type,
        "description": str(source.get("description") or source.get("info") or "").strip(),
        "keywords": _string_list(source.get("keywords")),
        "aliases": _string_list(source.get("aliases")),
        "example_questions": _string_list(source.get("example_questions")),
        "required_params": required_params,
        "param_order": param_order,
        "param_formats": _param_formats_from_value(source.get("param_formats") or source.get("value_formats")),
        "source_config": config,
    }

    if not source_type:
        questions.append(f"{name} source_type을 알려주세요. 가능한 값: oracle, h_api, datalake, goodocs.")
    elif source_type not in SUPPORTED_SOURCE_TYPES:
        warnings.append(f"{name} has unsupported source_type: {source_type}")

    # source_type별로 실제 조회에 반드시 필요한 값을 확인합니다.
    # 누락된 값은 즉시 실패시키지 않고 questions로 돌려 사용자가 보완할 수 있게 합니다.
    if source_type == "oracle":
        if not str(config.get("db_key") or "").strip():
            questions.append(f"{name} Oracle 조회에 사용할 db_key를 알려주세요.")
        if not str(config.get("query_template") or "").strip():
            questions.append(f"{name} Oracle query_template을 알려주세요.")
    elif source_type == "h_api":
        if not str(config.get("api_url") or "").strip():
            questions.append(f"{name} H-API api_url을 알려주세요.")
        if not normalized["param_order"] and normalized["required_params"]:
            normalized["param_order"] = list(normalized["required_params"])
    elif source_type == "datalake":
        if not str(config.get("query_template") or "").strip():
            questions.append(f"{name} Datalake query_template을 알려주세요.")
    elif source_type == "goodocs":
        if not str(config.get("doc_id") or "").strip():
            questions.append(f"{name} Goodocs doc_id를 알려주세요.")

    return name, normalized, warnings, questions


def _raw_sources_from_payload(payload: Any) -> tuple[list[Dict[str, Any]], bool, list[str]]:
    """LLM 결과가 sources dict/list/source_catalog 중 어떤 모양이어도 source 목록으로 풉니다."""
    # LLM이 정보 부족을 반환한 경우에는 source 정규화를 진행하지 않고 질문만 전달합니다.
    if isinstance(payload, dict) and payload.get("needs_more_info"):
        return [], True, _string_list(payload.get("questions"))

    # source_catalog/catalog wrapper가 있으면 실제 catalog 안쪽으로 들어갑니다.
    if isinstance(payload, dict) and isinstance(payload.get("source_catalog"), dict):
        payload = payload["source_catalog"]
    if isinstance(payload, dict) and isinstance(payload.get("catalog"), dict):
        payload = payload["catalog"]

    raw_sources: list[Dict[str, Any]] = []
    if isinstance(payload, dict) and isinstance(payload.get("sources"), dict):
        # {"sources": {"production": {...}}} 형태에서는 dict key를 source name으로 보강합니다.
        for name, value in payload["sources"].items():
            if isinstance(value, dict):
                copied = deepcopy(value)
                copied.setdefault("name", str(name))
                raw_sources.append(copied)
    elif isinstance(payload, dict) and isinstance(payload.get("sources"), list):
        # {"sources": [{...}, {...}]} 형태입니다.
        for item in payload["sources"]:
            if isinstance(item, dict):
                raw_sources.append(deepcopy(item))
    elif isinstance(payload, list):
        # LLM이 sources wrapper 없이 배열만 반환한 경우입니다.
        for item in payload:
            if isinstance(item, dict):
                raw_sources.append(deepcopy(item))
    elif isinstance(payload, dict) and payload:
        # 단일 source object로 들어온 경우입니다.
        raw_sources.append(deepcopy(payload))
    return raw_sources, False, []


def normalize_source_catalog_llm_result(llm_result_value: Any) -> Dict[str, Any]:
    """LLM 결과 또는 줄글 source 설명을 조회 flow에서 쓸 source_catalog JSON으로 정규화합니다."""
    # 1) 입력이 이미 JSON/Data인 경우를 먼저 처리합니다.
    #    Text Input이나 Chat Output Message로 들어온 경우를 위해 원문 텍스트도 함께 확보합니다.
    parsed = _payload_from_value(llm_result_value)
    text = _text_from_value(llm_result_value)

    # 2) dict처럼 보이지만 sources/source_catalog가 없는 경우는
    #    "source: production ..." 같은 줄글을 JSON으로 잘못 감싼 상황일 수 있어 텍스트를 재파싱합니다.
    if isinstance(parsed, dict) and "sources" not in parsed and "source_catalog" not in parsed and text:
        text_payload = _parse_json_or_python(text)
        if text_payload is not None:
            parsed = text_payload
        else:
            # JSON 파싱이 안 되면 사람이 작성한 source 설명으로 보고 block parser를 사용합니다.
            text_sources = _simple_catalog_blocks(text)
            if text_sources:
                parsed = {"sources": text_sources}
    elif not parsed and text:
        # JSON 후보 자체가 없을 때도 줄글 parser로 마지막 변환을 시도합니다.
        text_sources = _simple_catalog_blocks(text)
        if text_sources:
            parsed = {"sources": text_sources}
    if not parsed:
        return {
            "source_catalog": {"sources": {}, "needs_more_info": True, "questions": ["소스 설명을 JSON으로 변환하지 못했습니다. 입력 내용을 다시 확인해주세요."]},
            "warnings": [],
            "questions": ["소스 설명을 JSON으로 변환하지 못했습니다. 입력 내용을 다시 확인해주세요."],
        }

    # 3) LLM이 반환한 sources dict/list, 단일 source object 등을 모두 raw source 목록으로 통일합니다.
    raw_sources, needs_more_info, initial_questions = _raw_sources_from_payload(parsed)
    sources: Dict[str, Any] = {}
    warnings: list[str] = []
    questions: list[str] = list(initial_questions)

    if not needs_more_info:
        # 4) source별로 표준 필드 정리와 누락 정보 검사를 수행합니다.
        #    결과 dict의 key는 source 이름이므로 뒤쪽 request normalizer가 이름으로 찾을 수 있습니다.
        for index, raw_source in enumerate(raw_sources, start=1):
            name, normalized, item_warnings, item_questions = _normalize_one_source(raw_source, index)
            sources[name] = normalized
            warnings.extend(item_warnings)
            questions.extend(item_questions)

    # 5) 정상적으로 파싱했지만 source가 하나도 없으면 사용자가 바로 수정할 수 있는 질문을 남깁니다.
    if not sources and not questions:
        questions.append("변환된 source가 없습니다. 최소 하나의 source 설명을 입력해주세요.")

    # 6) Text Input에 연결하기 좋은 실제 catalog는 source_catalog 아래에 모읍니다.
    #    warnings/questions는 Langflow status나 디버깅에서 바로 볼 수 있도록 상위에도 유지합니다.
    source_catalog = {
        "sources": sources,
        "needs_more_info": bool(questions),
        "questions": questions,
        "warnings": warnings,
    }
    return {
        "source_catalog": source_catalog,
        "warnings": warnings,
        "questions": questions,
    }


def build_source_catalog_message(payload: Dict[str, Any]) -> str:
    """Text Input에 바로 연결할 수 있도록 source_catalog만 JSON 문자열로 직렬화합니다."""
    catalog = {}
    # normalizer 표준 결과에서는 source_catalog 아래에 실제 catalog가 있습니다.
    if isinstance(payload, dict) and isinstance(payload.get("source_catalog"), dict):
        catalog = payload["source_catalog"]
    elif isinstance(payload, dict) and isinstance(payload.get("sources"), (dict, list)):
        # 이미 catalog 본문만 들어온 경우도 그대로 직렬화합니다.
        catalog = payload
    # Text Input/Prompt Template에 연결하기 쉽도록 들여쓰기 있는 JSON 문자열로 반환합니다.
    return json.dumps(catalog, ensure_ascii=False, indent=2, default=str)


class SourceCatalogNormalizer(Component):
    """LLM이 만든 source catalog 후보를 Text Input에 연결 가능한 JSON Message로 바꾸는 노드입니다."""
    display_name = "Catalog Normalizer"
    description = "LLM이 만든 source_catalog 후보를 저장하지 않고 조회 Flow용 형태로 정규화합니다."
    icon = "FileJson"
    name = "SourceCatalogNormalizer"

    inputs = [
        DataInput(name="llm_result", display_name="LLM Result", input_types=["Message", "Data", "Text", "JSON"]),
    ]
    outputs = [
        Output(name="catalog_message", display_name="Catalog(Text직접연결용)", method="build_catalog_message", types=["Message"]),
        Output(name="catalog_data", display_name="Catalog(DB저장용)", method="build_catalog_data", types=["Data"]),
    ]

    def _payload(self) -> Dict[str, Any]:
        """source_catalog를 정규화하고 Langflow status에 source 개수와 누락 여부를 표시합니다."""
        # LLM 결과나 줄글 설명을 source_catalog JSON으로 변환합니다.
        payload = normalize_source_catalog_llm_result(getattr(self, "llm_result", None))
        catalog = payload.get("source_catalog", {})

        # status에는 생성된 source 수와 추가 정보 필요 여부만 간단히 표시합니다.
        self.status = {
            "source_count": len(catalog.get("sources", {})) if isinstance(catalog.get("sources"), dict) else 0,
            "needs_more_info": bool(catalog.get("needs_more_info")),
            "warning_count": len(payload.get("warnings", [])),
        }
        return payload

    def build_catalog_message(self):
        """다음 Text Input 또는 Prompt Template에 연결할 JSON Message를 내보냅니다."""
        return _make_message(build_source_catalog_message(self._payload()))

    def build_catalog_data(self):
        """분리 실행이나 결과 복사용으로 source_catalog 본문을 Data로 내보냅니다."""
        return _make_data(self._payload().get("source_catalog", {}))
