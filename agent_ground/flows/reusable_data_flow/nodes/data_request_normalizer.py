from __future__ import annotations

import ast
import json
import re
from copy import deepcopy
from typing import Any, Dict

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MultilineInput, Output
from lfx.schema.data import Data


def _make_data(payload: Dict[str, Any]) -> Any:
    """Langflow 버전에 따라 Data 생성자 모양이 달라지는 부분을 고려하여 Data객체 생성 시 아래 방식으로 구현."""
    try:
        return Data(data=payload)
    except TypeError:
        return Data(payload)


def _payload_from_value(value: Any) -> Dict[str, Any]:
    """LLM 출력이나 Text Input 값을 내부 처리 flow에 사용할 dict type으로 변환합니다."""
    # 값이 없으면 뒤쪽 로직이 빈 dict를 기준으로 안전하게 분기하도록 합니다.
    if value is None:
        return {}

    # Langflow 노드가 이미 dict를 넘긴 경우에는 복사해서 사용(원본을 건들지 않기위해 deepcopy활용)
    if isinstance(value, dict):
        return deepcopy(value)

    # Data 출력으로 연결된 경우에는 보통 .data 안에 payload가 들어 있습니다.
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return deepcopy(data)

    # Message/Text 출력으로 연결된 경우에는 텍스트를 꺼내 JSON/Python literal로 파싱합니다.
    text = _text_from_value(value)
    if text:
        parsed = _parse_json_or_python(text)
        if isinstance(parsed, dict):
            return parsed
    # dict로 해석하지 못하면 호출부가 "payload 없음"으로 처리하도록 빈 dict를 반환합니다.
    return {}


def _text_from_value(value: Any) -> str:
    """Message/Data/dict 안에서 LLM이 만든 텍스트를 찾아 꺼냅니다."""
    # None은 연결이 비어 있는 상태이므로 빈 문자열로 통일합니다.
    if value is None:
        return ""

    # Text Input이나 MessageTextInput에서 바로 온 값은 문자열 자체입니다.
    if isinstance(value, str):
        return value.strip()

    # dict payload에서는 Langflow/LLM/Text Input wrapper마다 다른 key를 쓸 수 있어 흔한 key를 순서대로 확인합니다.
    if isinstance(value, dict):
        for key in ("llm_result", "llm_text", "text", "content", "message", "response", "output", "result", "value", "output_text", "output_value"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
            if isinstance(item, dict):
                # 값이 한 번 더 dict로 감싸진 경우 재귀적으로 안쪽 텍스트를 찾습니다.
                nested = _text_from_value(item)
                if nested:
                    return nested
        if isinstance(value.get("data"), dict):
            # 일부 Data preview/copy 결과는 {"data": {...}} 모양으로 한 번 더 감싸질 수 있습니다.
            nested = _text_from_value(value["data"])
            if nested:
                return nested

    # Langflow Message 객체는 버전에 따라 text/content/message 속성을 가질 수 있습니다.
    for attr in ("text", "content", "message"):
        item = getattr(value, attr, None)
        if isinstance(item, str) and item.strip():
            return item.strip()

    # Langflow Data 객체 안의 .data가 dict이면 위 dict 처리 로직을 재사용합니다.
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return _text_from_value(data)
    return ""


def _extract_json_text(text: str) -> str:
    """마크다운 코드블록이나 설명문에 섞인 JSON object/list 부분만 잘라냅니다."""
    # 1) LLM이 ```json ... ``` 형태로 감싸는 경우가 많아서 먼저 코드블록 껍데기를 제거합니다.
    #    아래 정규식은 ```json 으로 시작해서 다음 ``` 전까지의 본문만 찾습니다.
    cleaned = str(text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        cleaned = fence.group(1).strip()

    # 2) 응답이 설명문 + JSON일 수도 있으므로, object({})와 list([]) 후보를 모두 찾습니다.
    candidates: list[tuple[int, int, str]] = []
    first_object = cleaned.find("{")
    last_object = cleaned.rfind("}")
    if 0 <= first_object < last_object:
        candidates.append((first_object, last_object, cleaned[first_object : last_object + 1]))
    first_list = cleaned.find("[")
    last_list = cleaned.rfind("]")
    if 0 <= first_list < last_list:
        candidates.append((first_list, last_list, cleaned[first_list : last_list + 1]))

    # 3) 가장 먼저 시작하는 JSON 후보를 선택합니다.
    #    같은 위치라면 더 긴 후보를 고르면 중첩 JSON을 덜 잘라내게 됩니다.
    if candidates:
        candidates.sort(key=lambda item: (item[0], -(item[1] - item[0])))
        return candidates[0][2]
    return cleaned


def _escape_newlines_inside_json_strings(text: str) -> str:
    """JSON 문자열 값 안에 실제 줄바꿈이 섞인 경우 \\n 문자로 보정합니다."""
    # catalog_data preview를 복사하면 query_template 값 안의 \n이 실제 줄바꿈으로 풀려
    # JSON 파서가 실패할 수 있습니다. 따옴표 안쪽 줄바꿈만 escape해서 다시 JSON으로 읽게 합니다.
    result: list[str] = []
    in_string = False
    escaped = False
    for char in str(text or ""):
        if escaped:
            result.append(char)
            escaped = False
            continue
        if char == "\\" and in_string:
            result.append(char)
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            result.append(char)
            continue
        if in_string and char == "\n":
            result.append("\\n")
            continue
        if in_string and char == "\r":
            result.append("\\r")
            continue
        result.append(char)
    return "".join(result)


def _parse_json_or_python(text: str) -> Any:
    """JSON을 우선 파싱하고, 작은따옴표 dict 같은 Python literal도 보조로 허용합니다."""
    # LLM 응답이 코드블록/설명문을 포함할 수 있어 JSON으로 보이는 부분만 먼저 잘라냅니다.
    cleaned = _extract_json_text(text)

    # 표준 JSON을 먼저 시도하고, 작은따옴표 dict 같은 Python literal을 보조로 허용합니다.
    for parser in (json.loads, ast.literal_eval):
        try:
            return parser(cleaned)
        except Exception:
            pass

    # Text Input에 붙여넣은 catalog_data가 query_template 안에 실제 줄바꿈을 포함하면
    # JSON 문법상 invalid가 되므로, 따옴표 안의 줄바꿈만 \n으로 바꿔 한 번 더 시도합니다.
    repaired = _escape_newlines_inside_json_strings(cleaned)
    if repaired != cleaned:
        try:
            return json.loads(repaired)
        except Exception:
            pass

    # 둘 다 실패하면 호출부가 오류 처리로 넘어갈 수 있도록 None을 반환합니다.
    return None


def _as_list(value: Any) -> list[Any]:
    """단일 값/tuple/set/None을 반복 처리하기 쉬운 list로 맞춥니다."""
    # None은 "값 없음"이므로 빈 list로 맞춥니다.
    if value is None:
        return []

    # 이미 list이면 그대로 사용해 불필요한 변환을 피합니다.
    if isinstance(value, list):
        return value

    # tuple/set은 반복 가능한 목록으로 바꿔 같은 루프에서 처리합니다.
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)

    # 단일 값은 하나짜리 list로 감싸 downstream 로직을 단순하게 만듭니다.
    return [value]


def _string_list(value: Any) -> list[str]:
    """쉼표, 세미콜론, 줄바꿈으로 적은 값을 깨끗한 문자열 목록으로 바꿉니다."""
    result: list[str] = []

    # 사람이 Text Input에 "DATE, YM"처럼 적은 경우를 지원하기 위해 문자열은 구분자로 분해합니다.
    # 정규식 [,;\n]은 쉼표, 세미콜론, 줄바꿈 중 하나를 구분자로 본다는 뜻입니다.
    if isinstance(value, str):
        items = re.split(r"[,;\n]", value)
    else:
        # list/tuple/set/단일 값은 공통 list 형태로 맞춘 뒤 같은 루프에서 정리합니다.
        items = _as_list(value)

    # 빈 값은 버리고 앞뒤 공백을 제거해 catalog/request 비교가 안정적으로 되게 합니다.
    for item in items:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result


def _string_dict(value: Any) -> Dict[str, str]:
    """param_formats처럼 key/value로 된 설정을 깨끗한 dict로 맞춥니다."""
    result: Dict[str, str] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key or "").strip()
            text = str(item or "").strip()
            if name and text:
                result[name] = text
        return result

    # 사람이 Text Input에 "DATE=YYYYMMDD, YM=YYYYMM"처럼 적은 경우를 지원합니다.
    for part in re.split(r"[,;\n]", str(value or "")):
        match = re.match(r"\s*([A-Za-z][A-Za-z0-9_]*)\s*(?:=|:)\s*(.+?)\s*$", part)
        if match:
            result[match.group(1)] = match.group(2).strip()
    return result


def _source_type(value: Any) -> str:
    """사용자가 쓴 source_type 별칭을 내부 표준값으로 맞춥니다."""
    # 하이픈/공백 표기는 underscore로 바꿔 같은 source_type으로 비교합니다.
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")

    # 사용자가 흔히 적는 별칭을 data node가 기대하는 표준 이름으로 변환합니다.
    aliases = {
        "oracle_db": "oracle",
        "oracledb": "oracle",
        "hapi": "h_api",
        "lake": "datalake",
        "lakehouse": "datalake",
        "goodoc": "goodocs",
    }
    return aliases.get(text, text)


def _catalog_sources(source_catalog_value: Any) -> list[Dict[str, Any]]:
    """Text Input 또는 Data로 들어온 source_catalog를 source dict 목록으로 정규화합니다."""
    # 1) Text Input은 문자열로, 이전 노드 Data 출력은 dict로 들어올 수 있으므로 둘 다 준비합니다.
    text = _text_from_value(source_catalog_value)
    payload = _payload_from_value(source_catalog_value)

    # 2) 문자열만 있는 경우 JSON/Python literal로 해석합니다.
    #    줄글 source 설명은 Catalog Normalizer에서 먼저 JSON catalog로 바꾸는 것을 표준으로 둡니다.
    if text and not any(key in payload for key in ("source_catalog", "catalog", "sources", "data")):
        parsed = _parse_json_or_python(text)
        if isinstance(parsed, dict):
            payload = parsed
        elif isinstance(parsed, list):
            payload = {"sources": parsed}

    # 3) catalog가 한 번 더 감싸져 있으면 실제 sources가 들어있는 안쪽 dict로 이동합니다.
    if isinstance(payload.get("data"), dict):
        data_payload = payload["data"]
        if any(key in data_payload for key in ("source_catalog", "catalog", "sources")):
            payload = deepcopy(data_payload)
    if isinstance(payload.get("source_catalog"), dict):
        payload = deepcopy(payload["source_catalog"])
    if isinstance(payload.get("catalog"), dict):
        payload = deepcopy(payload["catalog"])

    # 4) sources는 dict/list/단일 source object 세 가지 모양을 허용합니다.
    raw_sources: list[Any] = []
    if isinstance(payload.get("sources"), dict):
        for name, item in payload["sources"].items():
            if isinstance(item, dict):
                copied = deepcopy(item)
                copied.setdefault("name", str(name))
                raw_sources.append(copied)
    elif isinstance(payload.get("sources"), list):
        raw_sources = payload["sources"]
    elif payload:
        raw_sources = [payload]

    # 5) data node가 기대하는 표준 필드와 source_config 별칭을 한 번에 정리합니다.
    sources: list[Dict[str, Any]] = []
    for item in raw_sources:
        if not isinstance(item, dict):
            continue
        source = deepcopy(item)
        source["name"] = str(source.get("name") or source.get("source") or source.get("dataset_key") or "").strip()
        source["source_type"] = _source_type(source.get("source_type") or source.get("source"))
        source["required_params"] = _string_list(source.get("required_params"))
        source["param_order"] = _string_list(source.get("param_order")) or list(source["required_params"])
        source["param_formats"] = _string_dict(source.get("param_formats") or source.get("value_formats"))
        source["keywords"] = _string_list(source.get("keywords"))
        source["aliases"] = _string_list(source.get("aliases"))
        source["example_questions"] = _string_list(source.get("example_questions"))
        config = deepcopy(source.get("source_config")) if isinstance(source.get("source_config"), dict) else {}
        for key in ("db_key", "query_template", "sql_template", "sql", "query", "api_url", "url", "timeout", "doc_id", "document_id", "response_path"):
            if source.get(key) not in (None, "", [], {}) and key not in config:
                config[key] = deepcopy(source[key])
        if config.get("url") and not config.get("api_url"):
            config["api_url"] = config["url"]
        if config.get("document_id") and not config.get("doc_id"):
            config["doc_id"] = config["document_id"]
        for alias in ("sql_template", "sql", "query"):
            if config.get(alias) and not config.get("query_template"):
                config["query_template"] = config[alias]
        source["source_config"] = config
        sources.append(source)
    return sources


def _find_catalog_source(request: Dict[str, Any], sources: list[Dict[str, Any]]) -> Dict[str, Any]:
    """LLM 요청의 name/source_type과 source_catalog 항목을 연결합니다."""
    # LLM은 name/source_name/dataset_key/tool_name 중 아무 이름으로 source를 가리킬 수 있습니다.
    wanted_names = _string_list([request.get("name"), request.get("source_name"), request.get("dataset_key"), request.get("tool_name")])
    wanted_source_type = _source_type(request.get("source_type") or request.get("source"))
    lower_names = {name.lower() for name in wanted_names if name}

    # catalog source의 name뿐 아니라 aliases/keywords까지 비교해 자연어 기반 매칭을 조금 더 유연하게 합니다.
    for source in sources:
        names = [source.get("name")] + _as_list(source.get("aliases")) + _as_list(source.get("keywords"))
        if any(str(name or "").strip().lower() in lower_names for name in names):
            return source

    # 이름으로 못 찾았고 같은 source_type이 딱 하나라면 그 source를 안전하게 선택할 수 있습니다.
    same_type = [source for source in sources if source.get("source_type") == wanted_source_type and wanted_source_type]
    return same_type[0] if len(same_type) == 1 else {}


def _normalize_request(request: Dict[str, Any], sources: list[Dict[str, Any]]) -> tuple[Dict[str, Any], list[str]]:
    """LLM이 만든 짧은 요청에 catalog의 실행 설정을 채워 실행 가능한 request로 만듭니다."""
    warnings: list[str] = []

    # 1) LLM은 보통 name/source_type/params 정도만 반환합니다.
    #    source_catalog에서 같은 source를 찾아 query/api/doc 설정을 기본값으로 깔아 둡니다.
    catalog = _find_catalog_source(request, sources)
    normalized = deepcopy(catalog) if catalog else {}

    # 2) 사용자 질문에서 추출된 params/variables는 실제 실행값이므로 catalog 값보다 우선합니다.
    if request:
        normalized["params"] = deepcopy(request.get("params")) if isinstance(request.get("params"), dict) else {}
        if isinstance(request.get("variables"), dict):
            for key, value in request["variables"].items():
                normalized["params"].setdefault(key, deepcopy(value))

        # 3) LLM이 직접 넘긴 source_config가 있으면 catalog 설정 위에 덮어씁니다.
        #    다만 정상 흐름에서는 SQL/API URL을 LLM이 매번 복사하지 않도록 catalog에서 채우는 편이 좋습니다.
        normalized["name"] = str(request.get("name") or normalized.get("name") or request.get("dataset_key") or request.get("tool_name") or "").strip()
        normalized["source_type"] = _source_type(request.get("source_type") or normalized.get("source_type") or request.get("source"))
        if isinstance(request.get("source_config"), dict):
            config = deepcopy(normalized.get("source_config")) if isinstance(normalized.get("source_config"), dict) else {}
            config.update(deepcopy(request["source_config"]))
            normalized["source_config"] = config
        for key in ("required_params", "param_order"):
            if request.get(key):
                normalized[key] = _string_list(request.get(key))
        if request.get("param_formats") or request.get("value_formats"):
            # LLM이 source_catalog의 param_formats를 다시 보내거나 보정한 경우도 보존합니다.
            param_formats = _string_dict(normalized.get("param_formats"))
            param_formats.update(_string_dict(request.get("param_formats") or request.get("value_formats")))
            normalized["param_formats"] = param_formats

    # 4) 뒤쪽 data node가 key 유무를 매번 방어하지 않아도 되도록 빈 기본값을 보장합니다.
    if not normalized.get("source_type"):
        warnings.append("Request is missing source_type.")
    if not normalized.get("name"):
        normalized["name"] = str(normalized.get("source_type") or "source")
    normalized["params"] = deepcopy(normalized.get("params")) if isinstance(normalized.get("params"), dict) else {}
    normalized["required_params"] = _string_list(normalized.get("required_params"))
    normalized["param_order"] = _string_list(normalized.get("param_order")) or list(normalized["required_params"])
    normalized["param_formats"] = _string_dict(normalized.get("param_formats"))
    normalized["source_config"] = deepcopy(normalized.get("source_config")) if isinstance(normalized.get("source_config"), dict) else {}
    return normalized, warnings


def _unwrap_candidate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """data_request/body wrapper를 벗겨 실제 후보 JSON만 남깁니다."""
    # 앞단 adapter가 만든 data_request wrapper가 있으면 그 안쪽이 실제 request입니다.
    if isinstance(payload.get("data_request"), dict):
        return deepcopy(payload["data_request"])

    # body wrapper가 있으면 버전 문자열과 무관하게 body를 실제 request로 사용합니다.
    if isinstance(payload.get("body"), dict):
        return deepcopy(payload["body"])

    # wrapper가 없으면 원본 payload를 단일 request 후보로 사용합니다.
    return deepcopy(payload)


def normalize_simple_data_request_llm_result(llm_result_value: Any, source_catalog_value: Any = None) -> Dict[str, Any]:
    """LLM 응답과 source_catalog를 합쳐 다음 data node들이 읽을 data_request를 만듭니다."""
    errors: list[str] = []
    warnings: list[str] = []

    # 1) 먼저 source_catalog를 표준 source 목록으로 만들어 둡니다.
    #    이후 LLM 응답은 이 catalog를 참조해서 실행 설정을 보강합니다.
    sources = _catalog_sources(source_catalog_value)
    text = _text_from_value(llm_result_value)

    # 2) LLM 응답은 Message 텍스트일 수도 있고 Data dict일 수도 있어 텍스트 파싱을 먼저 시도합니다.
    parsed: Any = _parse_json_or_python(text) if text else None
    if parsed is None:
        parsed = _payload_from_value(llm_result_value)
    if not isinstance(parsed, dict):
        errors.append("LLM output must be a JSON object.")
        parsed = {}

    # 3) data_request/body wrapper를 벗긴 뒤 실제 request 후보만 처리합니다.
    candidate = _unwrap_candidate(parsed)

    if candidate.get("needs_more_info"):
        # 4-A) LLM이 정보 부족을 선언한 경우에는 data node로 보내지 않고 질문 목록만 유지합니다.
        questions = _string_list(candidate.get("questions"))
        data_request = {"needs_more_info": True, "questions": questions, "requests": []}
    elif isinstance(candidate.get("requests"), list):
        # 4-B) 여러 source를 한 번에 조회하는 요청은 각 item별로 catalog 설정을 보강합니다.
        requests = []
        for item in candidate["requests"]:
            if isinstance(item, dict):
                normalized, item_warnings = _normalize_request(item, sources)
                requests.append(normalized)
                warnings.extend(item_warnings)
            else:
                warnings.append("Ignored a non-object request item.")
        data_request = {"requests": requests}
    elif candidate:
        # 4-C) 단일 source 조회는 data_request 자체가 하나의 실행 요청이 됩니다.
        data_request, item_warnings = _normalize_request(candidate, sources)
        warnings.extend(item_warnings)
    else:
        data_request = {}

    return {"data_request": data_request, "parse_errors": errors, "warnings": warnings}


class DataRequestNormalizer(Component):
    """LLM이 만든 짧은 요청을 source_catalog 기반 실행 요청으로 바꾸는 노드입니다."""
    display_name = "Data Request Normalizer"
    description = "LLM 응답을 data_request로 정규화하고 source_catalog의 실행 설정을 채웁니다."
    icon = "ListChecks"
    name = "DataRequestNormalizer"

    inputs = [
        DataInput(name="llm_result", display_name="LLM Result", input_types=["Message", "Data", "Text", "JSON"]),
        MultilineInput(name="source_catalog", display_name="Data Catalog", value="", advanced=False),
    ]
    outputs = [
        Output(name="data_request", display_name="Data Request", method="build_data_request", types=["Data"]),
    ]

    def _payload(self) -> Dict[str, Any]:
        """source_catalog 입력 경로(Data 또는 Text)를 고르고 정규화 결과를 캐시합니다."""
        # Langflow는 output이 여러 개면 같은 컴포넌트 메서드를 반복 호출할 수 있어 결과를 캐시합니다.
        cached = getattr(self, "_cached_payload", None)
        if isinstance(cached, dict):
            return cached

        # 아니면 사용자가 노드 안에 직접 붙여 넣은 source_catalog 텍스트를 사용합니다.
        source_catalog_value = getattr(self, "source_catalog", "")

        # LLM 결과와 catalog를 합쳐 data node가 바로 실행할 data_request를 만듭니다.
        payload = normalize_simple_data_request_llm_result(getattr(self, "llm_result", None), source_catalog_value)
        self._cached_payload = payload
        data_request = payload.get("data_request", {})

        # status에는 화면에서 바로 확인할 수 있는 파싱/누락 상태만 작게 남깁니다.
        self.status = {
            "parse_errors": len(payload.get("parse_errors", [])),
            "warnings": len(payload.get("warnings", [])),
            "needs_more_info": bool(data_request.get("needs_more_info")) if isinstance(data_request, dict) else False,
        }
        return payload

    def build_data_request(self):
        """data node들이 사용할 data_request 본문만 Data로 내보냅니다."""
        return _make_data(self._payload().get("data_request", {}))
