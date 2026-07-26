from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MessageTextInput, Output
from lfx.schema.data import Data

DEFAULT_DB_NAME = "business_agent_design"
DEFAULT_CAPABILITY_COLLECTION = "agent_capability_catalog"
DEFAULT_CASE_COLLECTION = "agent_improvement_cases"


def load_catalog_items(
    business_profile_value: Any,
    mongo_uri: str = "",
    db_name: str = DEFAULT_DB_NAME,
    capability_collection: str = DEFAULT_CAPABILITY_COLLECTION,
    case_collection: str = DEFAULT_CASE_COLLECTION,
    top_k: int = 8,
) -> dict[str, Any]:
    """업무 프로필에 맞는 기능/사례 카탈로그를 MongoDB 또는 내장 seed에서 찾아옵니다."""
    payload = _payload(business_profile_value)
    profile = _dict(payload.get("workflow_profile"))
    top_k = max(1, min(_int(top_k, 8), 20))

    source = "seed"
    retrieval_status = "fallback_seed"
    fallback_reason = ""
    items = _seed_catalog_items()

    if str(mongo_uri or "").strip():
        try:
            mongo_items = _load_from_mongodb(
                mongo_uri=mongo_uri,
                db_name=db_name or DEFAULT_DB_NAME,
                capability_collection=capability_collection or DEFAULT_CAPABILITY_COLLECTION,
                case_collection=case_collection or DEFAULT_CASE_COLLECTION,
            )
            if mongo_items:
                items = mongo_items
                source = "mongodb"
                retrieval_status = "ok"
            else:
                fallback_reason = "MongoDB에서 활성 카탈로그 항목을 찾지 못해 seed를 사용했습니다."
        except Exception as exc:
            fallback_reason = f"MongoDB 조회 실패로 seed를 사용했습니다: {exc}"

    eligible_items = [item for item in items if str(item.get("status") or "").lower() == "approved"]
    ranked = _rank_items(profile, eligible_items, top_k)
    trace_id = _stable_id("trace", str(profile) + str([item.get("canonical_key") for item in ranked]))
    context = {
        "business_profile": profile,
        "ranked_catalog_items": ranked,
        "catalog_meta": {
            "source": source,
            "retrieval_status": retrieval_status,
            "fallback_reason": fallback_reason,
            "top_k": top_k,
            "loaded_count": len(items),
            "approved_count": len(eligible_items),
            "excluded_unapproved_count": len(items) - len(eligible_items),
            "selected_count": len(ranked),
            "loaded_at": _now_iso(),
        },
        "recommendation_trace": {
            "trace_id": trace_id,
            "retrieval_source": source,
            "query_terms": sorted(_profile_tokens(profile))[:30],
            "selected_item_keys": [item.get("canonical_key") for item in ranked],
            "scoring_notes": [
                {
                    "catalog_id": item.get("canonical_key"),
                    "score": item.get("_score", 0),
                    "matched_terms": item.get("_matched_terms", []),
                }
                for item in ranked
            ],
        },
    }
    return {**payload, "catalog_context": context}


def _load_from_mongodb(
    mongo_uri: str,
    db_name: str,
    capability_collection: str,
    case_collection: str,
) -> list[dict[str, Any]]:
    from pymongo import MongoClient

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=2500)
    client.admin.command("ping")
    db = client[db_name]
    items: list[dict[str, Any]] = []
    for collection_name in (capability_collection, case_collection):
        collection = db[collection_name]
        query = {"status": "approved"}
        for doc in collection.find(query, limit=200):
            doc.pop("_id", None)
            doc.setdefault("source_collection", collection_name)
            items.append(doc)
    return items


def _seed_catalog_items() -> list[dict[str, Any]]:
    return [
        _item(
            "chat_input_output",
            "Chat Input / Chat Output",
            "사용자 입력과 최종 응답을 연결하는 기본 입출력 구성입니다.",
            ["입출력", "기본 구성"],
            ["질문", "업무 설명", "응답", "채팅"],
            ["Chat Input", "Chat Output"],
            ["https://docs.langflow.org/chat-input-and-output"],
            "low",
        ),
        _item(
            "prompt_template",
            "Prompt Template",
            "업무 설명, 카탈로그, 출력 스키마를 LLM 지시문으로 안정적으로 묶습니다.",
            ["LLM", "프롬프트"],
            ["프롬프트", "지시", "스키마", "구조화"],
            ["Prompt Template", "Language Model"],
            ["https://docs.langflow.org/components-prompts"],
            "low",
        ),
        _item(
            "structured_output",
            "Structured Output",
            "LLM 응답을 Flow가 이해할 수 있는 JSON 구조로 정리합니다.",
            ["JSON", "검증"],
            ["구조화", "스키마", "정규화", "필드"],
            ["Structured Output", "Parser", "Custom Component"],
            ["https://docs.langflow.org/structured-output"],
            "medium",
        ),
        _item(
            "agent_with_tools",
            "Agent + Tools",
            "조건에 맞는 조회, 계산, 검색, 보고서 생성을 Agent가 선택적으로 실행하게 합니다.",
            ["Agent", "자동화"],
            ["도구", "조회", "자동 판단", "계산", "검색"],
            ["Agent", "Tools", "Language Model"],
            ["https://docs.langflow.org/components-agents"],
            "high",
            True,
        ),
        _item(
            "api_request",
            "API Request",
            "HTTP API로 업무 시스템 데이터를 조회하거나 처리 결과를 전달합니다.",
            ["API", "시스템 연동"],
            ["API", "HTTP", "POST", "조회", "전송"],
            ["API Request"],
            ["https://docs.langflow.org/api-request"],
            "high",
            True,
        ),
        _item(
            "mongodb_catalog",
            "MongoDB 카탈로그",
            "기능 목록과 개선 사례를 MongoDB에 저장하고 업무 설명에 맞춰 검색합니다.",
            ["MongoDB", "추천"],
            ["카탈로그", "개선 사례", "추천 근거", "저장"],
            ["MongoDB", "Custom Component"],
            ["https://docs.langflow.org/bundles-mongodb"],
            "medium",
        ),
        _item(
            "human_review_gate",
            "사람 검토 후 실행",
            "메일 발송, 시스템 변경, 승인 같은 영향 큰 작업은 사람 확인 뒤 실행합니다.",
            ["안전", "승인"],
            ["메일", "발송", "수정", "등록", "승인", "검토"],
            ["Human Review", "Chat Output"],
            ["internal:safety-pattern"],
            "high",
            True,
            item_type="pattern",
        ),
        _item(
            "html_report_flow",
            "HTML 리포트 생성 Flow",
            "분석 결과를 KPI, 그래프, 표가 포함된 HTML 결과물로 렌더링하는 사내 예시입니다.",
            ["사내 예시", "리포트"],
            ["보고서", "HTML", "대시보드", "공유"],
            ["Custom Component", "HTML Renderer"],
            ["local:html_report_flow"],
            "low",
            status="user_testing",
            item_type="case",
        ),
        _item(
            "reusable_data_flow",
            "재사용 데이터 조회 Flow",
            "반복 데이터 조회를 메타데이터 기반으로 처리하는 사내 Flow 예시입니다.",
            ["사내 예시", "데이터 조회"],
            ["데이터", "조회", "SQL", "조건", "분석"],
            ["Custom Component"],
            ["local:reusable_data_flow"],
            "medium",
            status="user_testing",
            item_type="case",
        ),
    ]


def _item(
    canonical_key: str,
    title_ko: str,
    summary_ko: str,
    categories: list[str],
    trigger_signals: list[str],
    langflow_building_blocks: list[str],
    source_links: list[str],
    risk_level: str = "medium",
    human_review_required: bool = False,
    item_type: str = "capability",
    status: str = "approved",
) -> dict[str, Any]:
    return {
        "item_type": item_type,
        "status": status,
        "canonical_key": canonical_key,
        "title_ko": title_ko,
        "summary_ko": summary_ko,
        "categories": categories,
        "trigger_signals": trigger_signals,
        "recommended_when": [summary_ko],
        "not_recommended_when": [],
        "langflow_building_blocks": langflow_building_blocks,
        "risk_level": risk_level,
        "human_review_required": human_review_required,
        "source_links": source_links,
    }


def _rank_items(profile: dict[str, Any], items: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    query_tokens = _profile_tokens(profile)
    ranked = []
    for item in items:
        item_tokens = _item_tokens(item)
        matched = sorted(query_tokens & item_tokens)
        score = len(matched) * 3
        if item.get("item_type") == "case":
            score += 1
        if item.get("human_review_required") and _risk_tokens(profile):
            score += 2
        if not score and str(item.get("canonical_key")) in {"chat_input_output", "prompt_template", "structured_output"}:
            score = 1
        enriched = deepcopy(item)
        enriched["_score"] = score
        enriched["_matched_terms"] = matched[:12]
        ranked.append(enriched)
    ranked.sort(key=lambda item: (item.get("_score", 0), item.get("human_review_required", False)), reverse=True)
    return ranked[:top_k]


def _profile_tokens(profile: dict[str, Any]) -> set[str]:
    text_parts = [
        profile.get("business_goal", ""),
        " ".join(profile.get("constraints") or []),
        " ".join(profile.get("desired_outputs") or []),
        " ".join(profile.get("risk_signals") or []),
        profile.get("raw_work_description", ""),
    ]
    for step in profile.get("current_flow") or []:
        step_dict = _dict(step)
        text_parts.extend(
            [
                step_dict.get("title", ""),
                step_dict.get("description", ""),
                " ".join(step_dict.get("systems") or []),
                " ".join(step_dict.get("data") or []),
            ]
        )
    return _tokens(" ".join(str(part) for part in text_parts))


def _risk_tokens(profile: dict[str, Any]) -> set[str]:
    return _tokens(" ".join(profile.get("risk_signals") or []))


def _item_tokens(item: dict[str, Any]) -> set[str]:
    text = " ".join(
        [
            str(item.get("canonical_key") or ""),
            str(item.get("title_ko") or ""),
            str(item.get("summary_ko") or ""),
            " ".join(item.get("categories") or []),
            " ".join(item.get("trigger_signals") or []),
            " ".join(item.get("langflow_building_blocks") or []),
        ]
    )
    return _tokens(text)


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[A-Za-z0-9가-힣_]+", text or "") if len(token) >= 2}


def _payload(value: Any) -> dict[str, Any]:
    data = getattr(value, "data", value)
    return deepcopy(data) if isinstance(data, dict) else {}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _stable_id(prefix: str, text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class MongoCatalogRetriever(Component):
    display_name = "03 MongoDB 기능/사례 검색"
    description = "업무에 맞는 기능/개선 사례를 MongoDB에서 찾고 추천 근거 trace를 생성합니다. Mongo URI가 비어 있으면 내장 seed를 사용합니다."
    icon = "Database"
    inputs = [
        DataInput(name="business_profile", display_name="업무 구조화 결과", required=True),
        MessageTextInput(name="mongo_uri", display_name="Mongo URI", value="", advanced=True),
        MessageTextInput(name="db_name", display_name="DB 이름", value=DEFAULT_DB_NAME, advanced=True),
        MessageTextInput(name="capability_collection", display_name="기능 컬렉션", value=DEFAULT_CAPABILITY_COLLECTION, advanced=True),
        MessageTextInput(name="case_collection", display_name="사례 컬렉션", value=DEFAULT_CASE_COLLECTION, advanced=True),
        MessageTextInput(name="top_k", display_name="검색 개수", value="8", advanced=True),
    ]
    outputs = [Output(name="catalog_context", display_name="추천 컨텍스트", method="build_payload")]

    def build_payload(self) -> Data:
        result = load_catalog_items(
            getattr(self, "business_profile", None),
            mongo_uri=getattr(self, "mongo_uri", ""),
            db_name=getattr(self, "db_name", DEFAULT_DB_NAME),
            capability_collection=getattr(self, "capability_collection", DEFAULT_CAPABILITY_COLLECTION),
            case_collection=getattr(self, "case_collection", DEFAULT_CASE_COLLECTION),
            top_k=getattr(self, "top_k", "8"),
        )
        context = result.get("catalog_context", {})
        meta = context.get("catalog_meta", {})
        self.status = {
            "검색 소스": meta.get("source"),
            "검색 상태": meta.get("retrieval_status"),
            "검색 결과 수": len(context.get("ranked_catalog_items", [])),
            "Trace ID": context.get("recommendation_trace", {}).get("trace_id"),
        }
        return Data(data=result)
