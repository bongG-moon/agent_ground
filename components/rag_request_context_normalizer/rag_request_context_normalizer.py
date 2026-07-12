from __future__ import annotations

"""Normalize a RAG question and a server-verified actor context.

This is a Standalone Langflow component.  Production identity and ACL claims
are accepted only through ``trusted_context``; the natural-language question is
never parsed for roles, groups, tenant, or clearance.  A visibly marked demo
identity keeps the imported education flow runnable until that input is wired.
"""

import json
import re
import uuid
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import BoolInput, DataInput, DropdownInput, MessageTextInput, Output
from lfx.schema.data import Data


CLASSIFICATION_LEVELS = {
    "public": 0,
    "internal": 1,
    "confidential": 2,
    "restricted": 3,
}


def _payload(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return data
    text = _text(value)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    for attr in ("text", "content"):
        candidate = getattr(value, attr, None)
        if isinstance(candidate, str):
            return candidate.strip()
    return str(value).strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "verified"}


def _safe_scalar(value: Any, limit: int = 128) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\x00-\x1f\x7f]", "", text)
    return text[:limit]


def _safe_list(value: Any, limit: int = 32) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = re.split(r"[,;\n]", value)
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = [value]
    result: list[str] = []
    for item in items:
        normalized = _safe_scalar(item).lower()
        if normalized and normalized not in result:
            result.append(normalized)
        if len(result) >= limit:
            break
    return result


def _classification(value: Any) -> str:
    text = _safe_scalar(value).lower()
    return text if text in CLASSIFICATION_LEVELS else "internal"


def normalize_request(
    question_value: Any,
    trusted_value: Any,
    *,
    use_demo_identity: bool = True,
    demo_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    trusted = _payload(trusted_value)
    trusted_present = bool(trusted)
    actor_raw = (
        _dict(trusted.get("actor_context"))
        or _dict(trusted.get("actor"))
        or _dict(_dict(trusted.get("request")).get("actor_context"))
        or _dict(_dict(trusted.get("identity")).get("actor_context"))
    )
    identity_trust = "verified_context"
    if not trusted_present and _bool(use_demo_identity):
        actor_raw = {
            **_dict(demo_identity),
            "identity_verified": True,
        }
        identity_trust = "demo"

    question = _text(question_value)[:8000]
    identity_verified = _bool(
        actor_raw.get("identity_verified", actor_raw.get("verified", trusted.get("identity_verified")))
    )
    user_id = _safe_scalar(actor_raw.get("user_id") or actor_raw.get("subject") or actor_raw.get("employee_id"))
    tenant_id = _safe_scalar(actor_raw.get("tenant_id") or actor_raw.get("organization_id"))
    roles = _safe_list(actor_raw.get("roles") or actor_raw.get("role"))
    groups = _safe_list(actor_raw.get("groups") or actor_raw.get("group_ids") or actor_raw.get("departments"))
    clearance = _classification(actor_raw.get("clearance") or actor_raw.get("max_classification"))

    # Fail closed.  A question is never allowed to supply or repair identity
    # fields, and a partially populated trusted context does not become valid.
    ready = bool(question and identity_verified and user_id and tenant_id)
    supplied_request_id = _safe_scalar(
        trusted.get("request_id") or _dict(trusted.get("request")).get("request_id"),
        limit=96,
    )
    request_id = supplied_request_id or str(uuid.uuid4())

    errors: list[str] = []
    if not question:
        errors.append("A question is required.")
    if not ready and question:
        # Deliberately generic: callers must not learn which identity/ACL claim
        # was missing from a response intended for an end user.
        errors.append("Verified request context is unavailable.")

    return {
        "contract": "agent_ground.enterprise_rag.request.v1",
        "success": ready,
        "request_id": request_id,
        "question": question,
        "actor": {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "roles": roles,
            "groups": groups,
            "clearance": clearance,
            "clearance_level": CLASSIFICATION_LEVELS[clearance],
            "identity_verified": identity_verified,
            "identity_trust": identity_trust if ready else "unavailable",
        },
        "security": {
            "authorized_for_retrieval": ready,
            "policy": "fail_closed",
            "reason_code": "ready" if ready else "context_unavailable",
        },
        "errors": errors,
        "warnings": [],
    }


class RagRequestContextNormalizer(Component):
    display_name = "RAG Request Context Normalizer"
    description = "사용자 질문과 서버에서 검증한 사용자·조직·역할 컨텍스트를 fail-closed 요청으로 정규화합니다."
    icon = "ShieldCheck"
    name = "RagRequestContextNormalizer"

    inputs = [
        MessageTextInput(name="question", display_name="Question", required=True),
        DataInput(
            name="trusted_context",
            display_name="Trusted Context",
            info="Gateway 또는 검증된 upstream이 만든 actor_context입니다. Chat Input의 역할 주장은 사용하지 않습니다.",
            input_types=["Data", "JSON"],
            required=False,
        ),
        BoolInput(
            name="use_demo_identity",
            display_name="Use Demo Identity",
            info="교육용 기본 실행에만 사용합니다. 사내 연동 시 끄고 Trusted Context를 연결하세요.",
            value=True,
        ),
        MessageTextInput(name="demo_user_id", display_name="Demo User ID", value="demo.employee", advanced=True),
        MessageTextInput(name="demo_tenant_id", display_name="Demo Tenant ID", value="agent-ground", advanced=True),
        MessageTextInput(name="demo_roles", display_name="Demo Roles", value="employee", advanced=True),
        MessageTextInput(name="demo_groups", display_name="Demo Groups", value="all-employees", advanced=True),
        DropdownInput(
            name="demo_clearance",
            display_name="Demo Clearance",
            options=["public", "internal", "confidential", "restricted"],
            value="internal",
            advanced=True,
        ),
    ]
    outputs = [Output(name="request", display_name="Request", method="build_request", types=["Data"])]

    def build_request(self) -> Data:
        demo_identity = {
            "user_id": getattr(self, "demo_user_id", "demo.employee"),
            "tenant_id": getattr(self, "demo_tenant_id", "agent-ground"),
            "roles": getattr(self, "demo_roles", "employee"),
            "groups": getattr(self, "demo_groups", "all-employees"),
            "clearance": getattr(self, "demo_clearance", "internal"),
        }
        result = normalize_request(
            getattr(self, "question", ""),
            getattr(self, "trusted_context", None),
            use_demo_identity=getattr(self, "use_demo_identity", True),
            demo_identity=demo_identity,
        )
        self.status = "request ready" if result["success"] else "request unavailable"
        return Data(data=result)
