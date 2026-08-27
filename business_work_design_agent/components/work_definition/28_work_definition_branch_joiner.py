from __future__ import annotations

"""Join exactly one conditional WorkDefinition branch without local imports."""

import copy
import json
import re
import uuid
from typing import Any

from lfx.custom import Component
from lfx.io import DataInput, Output
from lfx.schema import Data


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return copy.deepcopy(value)
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return copy.deepcopy(data)
    text = getattr(value, "text", value if isinstance(value, str) else "")
    if isinstance(text, str) and text.strip():
        return json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE))
    return {}


def _work(value: Any) -> dict[str, Any]:
    payload = _payload(value)
    nested = payload.get("work_definition")
    return copy.deepcopy(nested) if isinstance(nested, dict) else payload


def join_work_definition_branches(answered_value: Any = None, review_value: Any = None) -> dict[str, Any]:
    trace_id = f"trace-{uuid.uuid4()}"
    candidates: list[tuple[str, dict[str, Any]]] = []
    parse_errors: list[str] = []
    for branch, value in (("answered", answered_value), ("review", review_value)):
        try:
            work = _work(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            parse_errors.append(branch)
            continue
        if all(work.get(key) not in (None, "") for key in ("work_definition_id", "tenant_id", "owner_id", "session_id", "channel_mode", "revision")):
            candidates.append((branch, work))
    if len(candidates) != 1:
        code = "WORK_BRANCH_AMBIGUOUS" if len(candidates) > 1 else "WORK_BRANCH_MISSING"
        return {
            "ok": False,
            "status": "BLOCKED",
            "artifact_refs": [],
            "error": {
                "code": code,
                "message": "활성 업무 분기는 정확히 하나여야 합니다.",
                "retryable": False,
                "details": {
                    "active_branches": [branch for branch, _ in candidates],
                    "parse_error_branches": parse_errors,
                },
            },
            "resume": None,
            "trace_id": trace_id,
        }
    selected_branch, work = candidates[0]
    return {
        "ok": True,
        "status": str(work.get("status") or "EXTRACTING"),
        "artifact_refs": [
            {
                "kind": "work_definition",
                "id": work["work_definition_id"],
                "revision": int(work["revision"]),
            }
        ],
        "selected_branch": selected_branch,
        "work_definition": copy.deepcopy(work),
        "trace_id": trace_id,
    }


class WorkDefinitionBranchJoinerComponent(Component):
    display_name = "28 WorkDefinition 분기 Joiner"
    description = "질문 답변 후 저장 경로와 질문 없는 review 경로 중 정확히 하나의 WorkDefinition만 합칩니다."
    icon = "GitMerge"
    name = "WorkDefinitionBranchJoiner"

    inputs = [
        DataInput(
            name="answered_work_definition",
            display_name="답변 병합·저장 WorkDefinition",
            input_types=["Data", "JSON"],
            required=False,
        ),
        DataInput(
            name="review_work_definition",
            display_name="질문 없는 Review WorkDefinition",
            input_types=["Data", "JSON"],
            required=False,
        ),
    ]
    outputs = [
        Output(
            name="joined_work_definition",
            display_name="선택된 WorkDefinition",
            method="join_branches",
            types=["Data"],
        )
    ]

    def join_branches(self) -> Data:
        result = join_work_definition_branches(
            getattr(self, "answered_work_definition", None),
            getattr(self, "review_work_definition", None),
        )
        self.status = {
            "ok": result.get("ok"),
            "status": result.get("status"),
            "selected_branch": result.get("selected_branch"),
        }
        return Data(data=result)
