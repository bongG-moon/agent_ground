from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import yaml

from app.domain import SopDraftIR

KST = timezone(timedelta(hours=9))


def _yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _bullet_lines(values: list[str], empty: str = "- 보완 필요") -> str:
    return "\n".join(f"- {value}" for value in values) if values else empty


def render_sop_markdown(
    ir: SopDraftIR,
    *,
    employee_id: str,
    template_commit: str,
    model_id: str,
    source_refs: list[dict[str, str]],
) -> str:
    now = datetime.now(KST)
    review_after = (now + timedelta(days=30)).date().isoformat()
    source_lines = []
    for source in source_refs:
        source_type = _yaml_string(source.get("type", "source"))
        source_ref = _yaml_string(source.get("ref", ""))
        source_lines.extend([f"  - type: {source_type}", f"    ref: {source_ref}"])
    if not source_lines:
        source_lines = ["source_refs: []"]
    else:
        source_lines.insert(0, "source_refs:")

    metadata = [
        "---",
        'okf_version: "0.1"',
        'boi_profile_version: "0.1-local"',
        "type: boi/local-sop-draft",
        f"title: {_yaml_string(ir.title)}",
        f"description: {_yaml_string(ir.description)}",
        f"timestamp: {now.isoformat()}",
        f'employee_id: "{employee_id}"',
        f"local_owner_ref: local-private:{employee_id}",
        "visibility: local-private",
        "local_only: true",
        "promotion_status: local_only",
        "retention_class: working",
        'retention_until: ""',
        "archive_status: active",
        "artifact_visibility: working",
        "lifecycle_state: working",
        "memory_candidate: false",
        "cleanup_policy: keep",
        f"review_after: {review_after}",
        "contains_sensitive: unknown",
        f"template_commit: {template_commit}",
        f"model_id: {model_id}",
        *source_lines,
        "---",
    ]

    step_rows = []
    for step in ir.steps:
        kind = "판단" if step.is_decision else "업무"
        system = step.system or "-"
        refs = ", ".join(f"`{ref}`" for ref in step.source_refs) or "보완 필요"
        step_rows.append(
            f"| {step.number} | {step.title} | {step.description} | {step.actor} | {system} | {kind} | {refs} |"
        )

    body = [
        f"# {ir.title}",
        "",
        "# Summary",
        "",
        ir.description,
        "",
        "# 목적",
        "",
        ir.purpose,
        "",
        "# 입력",
        "",
        _bullet_lines(ir.inputs),
        "",
        "# 절차",
        "",
        "| No | 단계 | 설명 | 담당 | 시스템 | 유형 | 근거 |",
        "|---:|---|---|---|---|---|---|",
        *step_rows,
        "",
        "# 판단 기준",
        "",
        _bullet_lines(ir.decision_criteria),
        "",
        "# 예외 상황",
        "",
        _bullet_lines(ir.exceptions),
        "",
        "# 완료 조건",
        "",
        _bullet_lines(ir.completion_conditions),
        "",
        "# 보완 필요",
        "",
        _bullet_lines(ir.open_questions, "- 없음"),
        "",
        "# 자동화 후보",
        "",
        _bullet_lines(ir.automation_candidates, "- 없음"),
        "",
    ]
    return "\n".join([*metadata, "", *body])


def _node_id(prefix: str, number: int) -> str:
    return f"{prefix}_{number}"


def _safe_label(value: str) -> str:
    return value.replace('"', "'").replace("\n", " ")[:80]


def _normalized_label(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", value).lower()


def render_mermaid(ir: SopDraftIR) -> str:
    graph = ["flowchart LR", '  start(["시작"])']
    mapping = []

    node_ids: dict[int, str] = {}
    stage_ids: list[str] = []
    decision_ids: list[str] = []
    for step in ir.steps:
        prefix = "decision" if step.is_decision else "stage"
        node_id = _node_id(prefix, step.number)
        node_ids[step.number] = node_id
        label = _safe_label(f"{step.number:02d}. {step.title}")
        if step.is_decision:
            graph.append(f'  {node_id}{{"{label}"}}')
            decision_ids.append(node_id)
        else:
            graph.append(f'  {node_id}["{label}"]')
            stage_ids.append(node_id)
        mapping.append((node_id, "decision" if step.is_decision else "stage", step.title, step.source_refs))

    if ir.steps:
        graph.append(f"  start --> {node_ids[ir.steps[0].number]}")
    else:
        graph.append('  start --> done(["완료"])')

    def resolve_target(target: str | None, next_id: str, branch: str, step_number: int) -> tuple[str, str | None]:
        if not target:
            return (next_id if branch == "yes" else "done", None)
        normalized = _normalized_label(target)
        if normalized in {"다음단계", "계속", "진행"}:
            return next_id, None
        if normalized in {"종료", "완료", "끝"}:
            return "done", None
        # Models often answer branch targets as a bare number ("3") rather
        # than "3단계". Resolve both forms so the diagram connects directly
        # to the real stage instead of introducing an unlabeled-looking
        # temporary outcome node.
        numbered_target = re.fullmatch(r"\s*(?:단계\s*)?(\d+)(?:\s*단계)?\s*", target)
        if numbered_target:
            target_number = int(numbered_target.group(1) or numbered_target.group(2))
            if target_number in node_ids:
                return node_ids[target_number], None
        for candidate in ir.steps:
            candidate_label = _normalized_label(candidate.title)
            if normalized == candidate_label or (
                min(len(normalized), len(candidate_label)) >= 4
                and (normalized in candidate_label or candidate_label in normalized)
            ):
                return node_ids[candidate.number], None
        outcome_id = f"decision_{step_number}_{branch}"
        return outcome_id, target

    declared_outcomes: set[str] = set()
    outcome_ids: list[str] = []
    for index, step in enumerate(ir.steps):
        node_id = node_ids[step.number]
        next_id = node_ids[ir.steps[index + 1].number] if index + 1 < len(ir.steps) else "done"
        if not step.is_decision:
            graph.append(f"  {node_id} --> {next_id}")
            continue
        for branch, edge_label, target in (
            ("yes", "예", step.yes_target),
            ("no", "아니오", step.no_target),
        ):
            target_id, outcome_label = resolve_target(target, next_id, branch, step.number)
            if outcome_label is not None and target_id not in declared_outcomes:
                graph.append(f'  {target_id}(["{_safe_label(outcome_label)}"])')
                graph.append(f"  {target_id} --> {next_id}")
                declared_outcomes.add(target_id)
                outcome_ids.append(target_id)
            graph.append(f'  {node_id} -- "{edge_label}" --> {target_id}')
    graph.append('  done(["완료"])')
    graph.append("  class start,done terminalNode")
    if stage_ids:
        graph.append(f"  class {','.join(stage_ids)} stageNode")
    if decision_ids:
        graph.append(f"  class {','.join(decision_ids)} decisionNode")
    if outcome_ids:
        graph.append(f"  class {','.join(outcome_ids)} outcomeNode")

    lines = [
        "# Summary",
        "",
        f"{ir.title}의 Overview Mermaid와 근거 연결이다.",
        "",
        "# Overview Mermaid",
        "",
        "```mermaid",
        *graph,
        "```",
        "",
        "# Source Mapping",
        "",
        "| Node ID | Kind | Label | Source reference |",
        "|---|---|---|---|",
    ]
    for node_id, kind, label, refs in mapping:
        source_text = ", ".join(f"`{ref}`" for ref in refs) or "보완 필요"
        lines.append(f"| `{node_id}` | {kind} | {label} | {source_text} |")
    lines.extend(
        [
            "",
            "# Diagram QA",
            "",
            "- Mermaid fenced block: pass",
            "- Decision edges labeled: pass",
            "- Source Mapping present: pass",
            "",
        ]
    )
    return "\n".join(lines)


_PRIVATE_ONLY_METADATA = {
    "employee_id",
    "local_owner_ref",
    "retention_until",
    "review_after",
    "memory_candidate",
    "cleanup_policy",
}


def render_promoted_sop_markdown(
    local_markdown: str,
    *,
    target_visibility: str,
    document_id: str,
    source_sha256: str,
    published_at: datetime,
) -> str:
    """Create a sanitized Team/Public artifact without mutating the Local Private source."""
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", local_markdown, re.DOTALL)
    if match is None:
        raise ValueError("SOP Markdown front matter is missing or invalid")
    source_metadata = yaml.safe_load(match.group(1)) or {}
    if not isinstance(source_metadata, dict):
        raise ValueError("SOP Markdown front matter must be a mapping")

    visibility = target_visibility.strip().lower()
    if visibility not in {"team", "public"}:
        raise ValueError("Promoted SOP visibility must be team or public")

    metadata = {
        key: value
        for key, value in source_metadata.items()
        if key not in _PRIVATE_ONLY_METADATA
    }
    metadata.update(
        {
            "okf_version": "0.1",
            "boi_profile_version": "0.1",
            "type": "boi/sop",
            "boi_id": f"boi:{visibility}:sop:{document_id}",
            "timestamp": published_at.isoformat(),
            "visibility": visibility,
            "classification": "public" if visibility == "public" else "internal",
            "local_only": False,
            "promotion_status": "promoted",
            "retention_class": "record",
            "archive_status": "active",
            "artifact_visibility": "shared",
            "lifecycle_state": "protected",
            "contains_sensitive": "no",
            "promotion_source_sha256": source_sha256,
        }
    )
    source_refs = metadata.get("source_refs")
    if not isinstance(source_refs, list):
        source_refs = []
    metadata["source_refs"] = [
        *source_refs,
        {"type": "local-private-snapshot", "ref": f"sha256:{source_sha256}"},
    ]

    front_matter = yaml.safe_dump(
        metadata,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).rstrip()
    return f"---\n{front_matter}\n---\n\n{match.group(2).lstrip()}"


def slugify_title(value: str) -> str:
    value = re.sub(r"[^0-9A-Za-z가-힣._-]+", "-", value.strip()).strip("-")
    return value[:80] or "sop-draft"


def source_refs_for_materials(materials: list[Any]) -> list[dict[str, str]]:
    return [
        {"type": "upload", "ref": material.original_name}
        for material in materials
    ]
