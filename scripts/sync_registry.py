from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    assets = []
    for manifest_path in sorted((ROOT / "components").glob("*/manifest.json")):
        assets.append(read_json(manifest_path))
    for manifest_path in sorted((ROOT / "flows").glob("*/manifest.json")):
        assets.append(read_json(manifest_path))
    assets.sort(key=lambda item: (item["asset_type"], item["id"]))
    payload = {
        "schema_version": "1.0",
        "generated_at": datetime.now().astimezone().date().isoformat(),
        "source_of_truth": "component and flow manifest files",
        "publication_rule": "Only approved assets may be used by Business Agent Design recommendations.",
        "assets": assets,
    }
    target = ROOT / "registry" / "capabilities.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Synced {len(assets)} assets to {target}")


if __name__ == "__main__":
    main()
