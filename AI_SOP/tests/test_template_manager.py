from __future__ import annotations

from pathlib import Path

from app.template_manager import inspect_template_contract


def test_inspect_template_contract_accepts_required_boi_structure(tmp_path: Path) -> None:
    required_files = [
        "AGENTS.md",
        "data/boi/index.md",
        "data/boi/log.md",
        "data/boi/private/0000000/index.md",
        "data/boi/private/0000000/sop-drafts/index.md",
        "data/boi/private/0000000/diagrams/index.md",
        "data/boi/private/0000000/promotion-drafts/index.md",
        "check.ps1",
        "check.sh",
    ]
    for relative in required_files:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n", encoding="utf-8")

    result = inspect_template_contract(tmp_path)

    assert result.is_compatible is True
    assert result.missing_paths == []


def test_inspect_template_contract_reports_missing_paths(tmp_path: Path) -> None:
    result = inspect_template_contract(tmp_path)

    assert result.is_compatible is False
    assert "AGENTS.md" in result.missing_paths

