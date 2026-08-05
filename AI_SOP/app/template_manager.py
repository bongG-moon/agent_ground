from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


REQUIRED_TEMPLATE_PATHS = [
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


def _hidden_process_kwargs() -> dict[str, object]:
    """Avoid a visible console for server-side Git sync on Windows."""
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


@dataclass(frozen=True)
class TemplateContractResult:
    is_compatible: bool
    missing_paths: list[str]


def inspect_template_contract(root: Path) -> TemplateContractResult:
    missing = [relative for relative in REQUIRED_TEMPLATE_PATHS if not (root / relative).exists()]
    return TemplateContractResult(is_compatible=not missing, missing_paths=missing)


def git_commit(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
        **_hidden_process_kwargs(),
    )
    return result.stdout.strip()


def local_snapshot_id(root: Path) -> str:
    digest = hashlib.sha256()
    for relative in REQUIRED_TEMPLATE_PATHS:
        path = root / relative
        digest.update(relative.encode("utf-8"))
        if path.is_file():
            digest.update(path.read_bytes())
    return f"local-{digest.hexdigest()[:16]}"


def read_agent_context(root: Path) -> str:
    paths = [
        root / "AGENTS.md",
        root / ".agents" / "skills" / "boi-wiki-local" / "SKILL.md",
        root / ".agents" / "skills" / "boi-sop-flow-visualizer" / "SKILL.md",
        root / "data" / "boi" / "index.md",
    ]
    sections = []
    for path in paths:
        if path.exists():
            sections.append(f"## {path.relative_to(root)}\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(sections)


class TemplateManager:
    def __init__(
        self,
        *,
        runtime_root: Path,
        repository_url: str,
        branch: str,
        active_sha: str = "",
        local_path: Path | None = None,
    ) -> None:
        self.runtime_root = runtime_root
        self.repository_url = repository_url
        self.branch = branch
        self.requested_active_sha = active_sha
        self.local_path = local_path
        self.registry_root = runtime_root / "templates"
        self.source_root = runtime_root / "template-source"
        self.active_file = runtime_root / "active-template.json"

    def active_template(self) -> Path | None:
        if self.local_path:
            contract = inspect_template_contract(self.local_path)
            return self.local_path if contract.is_compatible else None
        if self.active_file.exists():
            payload = json.loads(self.active_file.read_text(encoding="utf-8"))
            candidate = self.registry_root / payload["commitSha"]
            if inspect_template_contract(candidate).is_compatible:
                return candidate
        if self.requested_active_sha:
            candidate = self.registry_root / self.requested_active_sha
            if inspect_template_contract(candidate).is_compatible:
                return candidate
        return None

    def active_commit(self) -> str:
        active = self.active_template()
        if active is None:
            return self.requested_active_sha or "unavailable"
        try:
            return git_commit(active)
        except Exception:
            if self.active_file.exists():
                return json.loads(self.active_file.read_text(encoding="utf-8"))["commitSha"]
            return self.requested_active_sha or "snapshot"

    def sync(self) -> dict[str, object]:
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        if self.local_path:
            source = self.local_path
        else:
            if not self.source_root.exists():
                subprocess.run(
                    ["git", "clone", "--depth", "1", "--branch", self.branch, self.repository_url, str(self.source_root)],
                    check=True,
                    timeout=120,
                    **_hidden_process_kwargs(),
                )
            else:
                subprocess.run(
                    ["git", "-C", str(self.source_root), "fetch", "origin", self.branch, "--depth", "1"],
                    check=True,
                    timeout=120,
                    **_hidden_process_kwargs(),
                )
                subprocess.run(
                    ["git", "-C", str(self.source_root), "checkout", "--detach", "FETCH_HEAD"],
                    check=True,
                    timeout=30,
                    **_hidden_process_kwargs(),
                )
            source = self.source_root

        try:
            commit = git_commit(source)
        except (subprocess.CalledProcessError, OSError):
            if not self.local_path:
                raise
            commit = self.requested_active_sha or local_snapshot_id(source)
        target = self.registry_root / commit
        if not target.exists():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        contract = inspect_template_contract(target)
        return {
            "commitSha": commit,
            "path": str(target),
            "isCompatible": contract.is_compatible,
            "missingPaths": contract.missing_paths,
        }

    def activate(self, commit_sha: str) -> dict[str, str]:
        candidate = self.registry_root / commit_sha
        contract = inspect_template_contract(candidate)
        if not contract.is_compatible:
            raise ValueError(f"호환되지 않는 template입니다: {', '.join(contract.missing_paths)}")
        self.active_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"commitSha": commit_sha, "path": str(candidate)}
        self.active_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload


def replace_scaffold_identity(root: Path, employee_id: str) -> None:
    private_root = root / "data" / "boi" / "private"
    scaffold = private_root / "0000000"
    target = private_root / employee_id
    if not target.exists() and scaffold.exists():
        shutil.copytree(scaffold, target)
    for path in target.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        text = text.replace('employee_id: "0000000"', f'employee_id: "{employee_id}"')
        text = text.replace("local_owner_ref: local-private:0000000", f"local_owner_ref: local-private:{employee_id}")
        text = text.replace("data/boi/private/0000000", f"data/boi/private/{employee_id}")
        path.write_text(text, encoding="utf-8")


def safe_copy_template(source: Path, target: Path) -> None:
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(".git", "__pycache__"))
