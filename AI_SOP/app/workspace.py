from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from app.rendering import slugify_title
from app.template_manager import replace_scaffold_identity, safe_copy_template


def _hidden_process_kwargs() -> dict[str, object]:
    """Keep validation helpers invisible when the service runs on Windows.

    The browser only calls the API; BoI validation is a server-side child
    process. CREATE_NO_WINDOW prevents PowerShell from flashing a console while
    preserving captured stdout/stderr for diagnostics.
    """
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _append_index(path: Path, label: str, filename: str) -> None:
    text = path.read_text(encoding="utf-8") if path.exists() else "# Index\n"
    entry = f"* [{label}]({filename})"
    if entry not in text:
        path.write_text(text.rstrip() + "\n\n" + entry + "\n", encoding="utf-8")


class WorkspaceBuilder:
    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = runtime_root

    def build(
        self,
        *,
        template_root: Path,
        employee_id: str,
        draft_id: str,
        title: str,
        markdown: str,
        mermaid: str,
    ) -> tuple[str, dict]:
        workspace = self.runtime_root / "workspaces" / employee_id / draft_id
        safe_copy_template(template_root, workspace)
        replace_scaffold_identity(workspace, employee_id)

        slug = slugify_title(title)
        private_root = workspace / "data" / "boi" / "private" / employee_id
        sop_dir = private_root / "sop-drafts"
        diagram_dir = private_root / "diagrams"
        sop_dir.mkdir(parents=True, exist_ok=True)
        diagram_dir.mkdir(parents=True, exist_ok=True)
        sop_file = sop_dir / f"{slug}.md"
        diagram_file = diagram_dir / f"{slug}-mermaid.md"
        sop_file.write_text(markdown, encoding="utf-8")
        diagram_file.write_text(mermaid, encoding="utf-8")
        _append_index(sop_dir / "index.md", title, sop_file.name)
        _append_index(diagram_dir / "index.md", f"{title} Mermaid", diagram_file.name)

        log_path = workspace / "data" / "boi" / "log.md"
        log_text = log_path.read_text(encoding="utf-8") if log_path.exists() else "# Local BoI Log\n"
        log_entry = f"- AI SOP draft generated: `{employee_id}/sop-drafts/{sop_file.name}`"
        if log_entry not in log_text:
            date = datetime.now(timezone.utc).date().isoformat()
            log_path.write_text(log_text.rstrip() + f"\n\n## {date}\n\n{log_entry}\n", encoding="utf-8")

        validation = self.validate(workspace, employee_id)
        validation["sopPath"] = str(sop_file.relative_to(workspace)).replace("\\", "/")
        validation["diagramPath"] = str(diagram_file.relative_to(workspace)).replace("\\", "/")
        return str(workspace), validation

    @staticmethod
    def validate(workspace: Path, employee_id: str) -> dict:
        env = os.environ.copy()
        env["BOI_LOCAL_EMPLOYEE_ID"] = employee_id
        try:
            if os.name == "nt":
                command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(workspace / "check.ps1"), "-Root", str(workspace)]
            else:
                command = ["bash", str(workspace / "check.sh"), str(workspace)]
            result = subprocess.run(
                command,
                cwd=workspace,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                **_hidden_process_kwargs(),
            )
            output = (result.stdout + "\n" + result.stderr).strip()[-8000:]
            return {"passed": result.returncode == 0, "exitCode": result.returncode, "output": output}
        except Exception as exc:
            return {"passed": False, "exitCode": None, "output": f"검증 실행 실패: {exc}"}
