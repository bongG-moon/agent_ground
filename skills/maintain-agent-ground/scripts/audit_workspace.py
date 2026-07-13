from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT_MARKERS = (
    "AGENT_GROUND_PROJECT_MASTER_GUIDE.md",
    "registry/capabilities.json",
    "components",
    "flows",
)


def find_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if all((candidate / marker).exists() for marker in ROOT_MARKERS):
            return candidate
    raise FileNotFoundError("Agent Ground 프로젝트 루트를 찾지 못했습니다.")


def count_files(root: Path, pattern: str) -> int:
    return sum(1 for _ in root.glob(pattern))


def git_status(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "status", "--short", "--branch"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        return f"확인 실패: {exc}"


def main() -> int:
    try:
        root = find_root(Path.cwd().resolve())
        registry = json.loads((root / "registry/capabilities.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"[실패] {exc}", file=sys.stderr)
        return 1

    assets = registry.get("assets", [])
    component_assets = [item for item in assets if item.get("asset_type") == "component"]
    flow_assets = [item for item in assets if item.get("asset_type") == "flow"]
    missing_docs = [
        item.get("id", "unknown")
        for item in assets
        if item.get("documentation_path") and not (root / item["documentation_path"]).is_file()
    ]

    print(f"프로젝트 루트: {root}")
    print(f"Component manifest: {count_files(root, 'components/*/manifest.json')}")
    print(f"Flow manifest: {count_files(root, 'flows/*/manifest.json')}")
    print(f"Registry 자산: Component {len(component_assets)}, Flow {len(flow_assets)}")
    print(f"Project Skill: {count_files(root, 'skills/*/SKILL.md')}")
    print(f"문서 누락: {', '.join(missing_docs) if missing_docs else '없음'}")
    print("Git 상태:")
    print(git_status(root))
    return 1 if missing_docs else 0


if __name__ == "__main__":
    raise SystemExit(main())
