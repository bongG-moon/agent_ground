from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


SECTION_PATTERN = re.compile(r"^##\s+(\d+)\.\s+(.+?)\s*$")
UNRESOLVED_PATTERN = re.compile(r"\[UNRESOLVED:\s*.+?\]", re.IGNORECASE)
REQUIRED_SECTIONS = {
    1: "Visual Theme & Atmosphere",
    2: "Color Palette & Roles",
    3: "Typography Rules",
    4: "Component Stylings",
    5: "Layout Principles",
}
RECOMMENDED_SECTIONS = {
    6: "Depth & Elevation",
    7: "Do's and Don'ts",
    8: "Responsive Behavior",
    9: "Agent Prompt Guide",
    10: "Voice & Tone",
    11: "Brand Narrative",
    12: "Principles",
    13: "Personas",
    14: "States",
    15: "Motion & Easing",
}


def parse_frontmatter(lines: list[str]) -> tuple[dict[str, str], int, list[str]]:
    errors: list[str] = []
    if not lines or lines[0].strip() != "---":
        return {}, 0, ["line 1: missing YAML frontmatter opening marker"]
    try:
        end = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration:
        return {}, 0, ["missing YAML frontmatter closing marker"]

    metadata: dict[str, str] = {}
    for index, raw in enumerate(lines[1:end], 2):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            errors.append(f"line {index}: unsupported frontmatter entry")
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"').strip("'")
    return metadata, end + 1, errors


def validate(path: Path, strict: bool) -> dict:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    metadata, body_start, errors = parse_frontmatter(lines)
    warnings: list[str] = []

    if metadata.get("omd") not in {"0.1", "0.1.0"}:
        errors.append("frontmatter: omd must be 0.1 or 0.1.0")
    if not metadata.get("brand"):
        errors.append("frontmatter: brand is required")
    status = metadata.get("status", "draft")
    if status not in {"draft", "approved"}:
        errors.append("frontmatter: status must be draft or approved")
    if metadata.get("security_profile") not in {None, "internal-enterprise"}:
        errors.append("frontmatter: unsupported security_profile")

    found: dict[int, dict[str, object]] = {}
    order: list[int] = []
    for index, raw in enumerate(lines[body_start:], body_start + 1):
        match = SECTION_PATTERN.match(raw)
        if not match:
            continue
        number = int(match.group(1))
        if number in found:
            errors.append(f"line {index}: duplicate section {number}")
            continue
        found[number] = {"title": match.group(2).strip(), "line": index, "content": []}
        order.append(number)

    current: int | None = None
    for index, raw in enumerate(lines[body_start:], body_start + 1):
        match = SECTION_PATTERN.match(raw)
        if match:
            current = int(match.group(1))
            continue
        if current in found:
            found[current]["content"].append((index, raw))

    for number, expected_title in REQUIRED_SECTIONS.items():
        if number not in found:
            errors.append(f"missing required section {number}: {expected_title}")
        elif found[number]["title"] != expected_title:
            errors.append(
                f"section {number} must be titled '{expected_title}', "
                f"got '{found[number]['title']}'"
            )
    missing_recommended = [
        f"{number}: {title}"
        for number, title in RECOMMENDED_SECTIONS.items()
        if number not in found
    ]
    if missing_recommended:
        warnings.append("missing recommended sections: " + ", ".join(missing_recommended))
    for number, expected_title in RECOMMENDED_SECTIONS.items():
        if number in found and found[number]["title"] != expected_title:
            errors.append(
                f"section {number} must be titled '{expected_title}', "
                f"got '{found[number]['title']}'"
            )

    expected_order = sorted(order)
    if order != expected_order:
        errors.append(f"sections are out of order: {order}")

    for number, entry in sorted(found.items()):
        content = [
            raw.strip()
            for _, raw in entry["content"]
            if raw.strip() and not raw.strip().startswith("<!--")
        ]
        if not content:
            errors.append(f"section {number} at line {entry['line']} has no content")

    unresolved = [
        {"line": index, "marker": match.group(0)}
        for index, raw in enumerate(lines, 1)
        for match in UNRESOLVED_PATTERN.finditer(raw)
    ]
    if unresolved:
        warnings.append(f"{len(unresolved)} unresolved marker(s) remain")

    approved_or_strict = status == "approved" or strict
    if approved_or_strict:
        if missing_recommended:
            errors.append("approved/strict validation requires sections 1-15")
        if unresolved:
            errors.append("approved/strict validation does not allow unresolved markers")

    return {
        "path": str(path),
        "valid": not errors,
        "status": status,
        "metadata": metadata,
        "sections": [
            {
                "number": number,
                "title": entry["title"],
                "line": entry["line"],
            }
            for number, entry in sorted(found.items())
        ],
        "unresolved": unresolved,
        "warnings": warnings,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an enterprise project DESIGN.md.")
    parser.add_argument("path", nargs="?", type=Path, default=Path("DESIGN.md"))
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    try:
        result = validate(args.path, args.strict)
    except (OSError, UnicodeError) as exc:
        print(f"DESIGN_CONTEXT_ERROR: {exc}", file=sys.stderr)
        return 2

    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        state = "OK" if result["valid"] else "FAIL"
        print(
            f"DESIGN_CONTEXT_{state}: {args.path} "
            f"sections={len(result['sections'])} unresolved={len(result['unresolved'])}"
        )
        for warning in result["warnings"]:
            print(f"WARNING: {warning}")
        for error in result["errors"]:
            print(f"ERROR: {error}")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
