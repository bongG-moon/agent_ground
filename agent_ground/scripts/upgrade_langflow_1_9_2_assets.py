from __future__ import annotations

"""생성기가 없던 기존 Flow JSON을 Langflow 1.9.2 template으로 이관합니다."""

import argparse
import json
from pathlib import Path

from langflow_1_9_2_compat import (
    TARGET_LANGFLOW_VERSION,
    assert_target_runtime,
    load_component_index,
    upgrade_flow,
    write_json,
)


ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    ROOT / "flows" / "html_report_flow" / "html_report_flow.json",
    ROOT / "flows" / "reusable_data_flow" / "reusable_data_flow.json",
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="기존 Agent Ground Flow JSON을 Langflow 1.9.2 계약으로 이관합니다."
    )
    parser.parse_args()
    assert_target_runtime()
    component_index = load_component_index()

    for path in TARGETS:
        flow = json.loads(path.read_text(encoding="utf-8-sig"))
        upgraded = upgrade_flow(
            flow,
            module_prefix=f"agent_ground.migrated.{path.parent.name}",
            component_index=component_index,
        )
        write_json(path, upgraded)
        print(
            f"updated: {path.relative_to(ROOT)} "
            f"(Langflow {TARGET_LANGFLOW_VERSION}, "
            f"nodes={len(upgraded['data']['nodes'])}, edges={len(upgraded['data']['edges'])})"
        )


if __name__ == "__main__":
    main()
