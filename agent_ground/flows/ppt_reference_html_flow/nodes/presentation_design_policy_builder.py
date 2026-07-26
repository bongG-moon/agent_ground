from __future__ import annotations

"""Build the deterministic design and motion policy used by this flow.

Hallmark-inspired composition rules and Emil-inspired motion review rules are
expressed as project-owned structured data.  The policy is intentionally kept
outside the LLM prompt so the normalizer, renderer, and quality gate can all
enforce the same contract.
"""

import json
from copy import deepcopy
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, Output
from lfx.schema.data import Data


POLICY_ID = "hallmark-emil-balanced-v1"


def _make_data(payload: dict[str, Any]) -> Data:
    try:
        return Data(data=payload)
    except TypeError:
        return Data(payload)


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return deepcopy(value)
    data = getattr(value, "data", None)
    if isinstance(data, dict):
        return deepcopy(data)
    text = getattr(value, "text", None) or getattr(value, "content", None)
    if isinstance(text, str) and text.strip():
        try:
            parsed = json.loads(text)
            return deepcopy(parsed) if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def build_presentation_design_policy(request_value: Any = None, analysis_value: Any = None) -> dict[str, Any]:
    """Return a compact, deterministic policy contract for downstream nodes."""

    request_payload = _payload(request_value)
    request = request_payload.get("request") if isinstance(request_payload.get("request"), dict) else request_payload
    analysis_payload = _payload(analysis_value)
    analysis = analysis_payload.get("analysis") if isinstance(analysis_payload.get("analysis"), dict) else analysis_payload
    datasets = request.get("datasets") if isinstance(request.get("datasets"), list) else []
    storyline_pattern = "claim-evidence-decision" if datasets else "context-message-action"
    reference_confidence = analysis.get("confidence") if isinstance(analysis, dict) else None

    policy = {
        "contract_version": "presentation-design-policy-v1",
        "policy_id": POLICY_ID,
        "sources": [
            {
                "name": "Hallmark",
                "role": "composition and design-review principles",
                "license": "MIT",
            },
            {
                "name": "Emil Kowalski Skills",
                "role": "motion and interaction review principles",
                "license": "MIT",
            },
        ],
        "precedence": [
            "company_and_user_brief",
            "explicit_visual_direction",
            "reference_image_dna",
            "policy_defaults",
        ],
        "composition": {
            "storyline_pattern": storyline_pattern,
            "one_message_per_slide": True,
            "max_consecutive_same_layout": 2,
            "max_content_elements": 6,
            "max_bullets": 6,
            "require_design_role": True,
            "allowed_design_roles": [
                "cover",
                "framing",
                "evidence",
                "comparison",
                "transition",
                "action",
            ],
            "allowed_visual_weights": ["quiet", "balanced", "strong"],
            "avoid_uniform_card_grid": True,
            "allow_decorative_gradient": False,
            "preserve_reference_dna": True,
            "hierarchy": ["dominant_message", "supporting_evidence", "quiet_metadata"],
        },
        "motion": {
            "profile": "purposeful-subtle",
            "pointer_only_slide_motion": True,
            "keyboard_navigation_motion": False,
            "max_ui_duration_ms": 300,
            "button_press_duration_ms": 120,
            "slide_enter_duration_ms": 180,
            "slide_exit_duration_ms": 120,
            "easing": "cubic-bezier(0.23, 1, 0.32, 1)",
            "allowed_properties": ["transform", "opacity", "color", "background-color", "box-shadow"],
            "forbidden_patterns": ["transition: all", "scale(0)", "ease-in", "layout-property-animation"],
            "reduced_motion_required": True,
            "hover_pointer_gate_required": True,
        },
        "quality_gates": [
            "policy-present",
            "slide-role-present",
            "one-message-per-slide",
            "layout-repetition",
            "content-density",
            "decorative-gradient",
            "motion-static-analysis",
            "reduced-motion",
            "pointer-hover-gate",
        ],
    }
    return {
        "payload_version": "ppt-reference-html-v1",
        "design_policy": policy,
        "warnings": [],
        "errors": [],
        "meta": {
            "status": "ready",
            "policy_id": POLICY_ID,
            "storyline_pattern": storyline_pattern,
            "reference_confidence": reference_confidence,
        },
    }


class PresentationDesignPolicyBuilder(Component):
    """Flow-internal policy node shared by planning, rendering, and review."""

    display_name = "04 디자인·모션 정책"
    description = "Hallmark식 구성 원칙과 Emil식 모션 검사 기준을 구조화된 결정론적 계약으로 만듭니다."
    icon = "Palette"
    name = "PresentationDesignPolicyBuilder"

    inputs = [
        DataInput(name="request", display_name="정규화된 발표 요청", required=True),
        DataInput(name="analysis", display_name="참고 디자인 분석", required=True),
    ]
    outputs = [Output(name="design_policy", display_name="디자인·모션 정책", method="build_policy", types=["Data"])]

    def build_policy(self) -> Data:
        result = build_presentation_design_policy(
            getattr(self, "request", None),
            getattr(self, "analysis", None),
        )
        self.status = result["meta"]
        return _make_data(result)
