from __future__ import annotations

"""Validate the generated one-flow export in a Langflow 1.11.x runtime.

This validator is intentionally separate from the generator so it can run on
the operating server after import preparation.  It validates the embedded
standalone source, exact Canvas edge contract, prohibited architecture, and a
real LFX Graph import without calling an LLM or Report API.
"""

import argparse
import json
from pathlib import Path
from typing import Any

import build_single_flow


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"Expected a JSON object: {path}")
    return value


def validate(path: Path, *, check_graph: bool = True, check_regeneration: bool = True) -> dict[str, Any]:
    flow = _load(path)
    summary = build_single_flow.validate_flow_payload(flow, check_graph=check_graph)
    payload = (json.dumps(flow, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if check_regeneration:
        regenerated = build_single_flow.build_flow()
        regenerated_payload = (json.dumps(regenerated, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if payload != regenerated_payload:
            raise ValueError(
                "F01 JSON differs from the current standalone sources/prompt. "
                "Run scripts/build_single_flow.py in this Langflow 1.11.x runtime."
            )
    summary.update(
        {
            "flow_path": str(path.resolve()),
            "graph_import_checked": bool(check_graph),
            "regeneration_checked": bool(check_regeneration),
        }
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flow", type=Path, default=build_single_flow.FLOW_PATH)
    parser.add_argument("--skip-graph", action="store_true", help="Skip the LFX Graph import (not recommended).")
    parser.add_argument("--skip-regeneration", action="store_true", help="Skip byte-for-byte regeneration drift check.")
    args = parser.parse_args()
    result = validate(
        args.flow,
        check_graph=not args.skip_graph,
        check_regeneration=not args.skip_regeneration,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
