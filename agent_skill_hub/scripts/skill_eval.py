from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


HUB_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = HUB_ROOT / "evals" / "manifest.json"


class EvalError(RuntimeError):
    pass


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise EvalError(f"{path}:{number}: {exc}") from exc
    return rows


def merge_dicts(defaults: dict, overrides: dict) -> dict:
    merged = dict(defaults)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_hub_skills() -> dict[str, dict]:
    base = load_json(HUB_ROOT / "catalog.json")
    skills = {entry["id"]: entry for entry in base.get("skills", [])}
    collections_root = HUB_ROOT / "collections"
    if collections_root.is_dir():
        for path in sorted(collections_root.glob("*/catalog.json")):
            collection = load_json(path)
            defaults = collection.get("skill_defaults", {})
            for entry in collection.get("skills", []):
                resolved = merge_dicts(defaults, entry)
                resolved["collection_id"] = collection["id"]
                if resolved["id"] in skills:
                    raise EvalError(f"duplicate Skill id: {resolved['id']}")
                skills[resolved["id"]] = resolved
    return skills


def suite_map() -> tuple[dict, dict[str, dict]]:
    manifest = load_json(MANIFEST_PATH)
    suites = {suite["id"]: suite for suite in manifest.get("suites", [])}
    if len(suites) != len(manifest.get("suites", [])):
        raise EvalError("eval manifest contains duplicate suite ids")
    return manifest, suites


def selected_suites(name: str) -> tuple[dict, list[dict]]:
    manifest, suites = suite_map()
    if name == "all":
        return manifest, list(suites.values())
    if name not in suites:
        raise EvalError(f"unknown suite '{name}'; expected one of: {', '.join(sorted(suites))}")
    return manifest, [suites[name]]


def skill_word_count(path: Path) -> int:
    return len(re.findall(r"\S+", path.read_text(encoding="utf-8")))


def validate_suite(manifest: dict, suite: dict, hub_skills: dict[str, dict]) -> list[dict]:
    policies = manifest["policies"]
    suite_id = suite["id"]
    evaluated = set(suite["skills"])
    unknown = sorted(evaluated - set(hub_skills))
    if unknown:
        raise EvalError(f"suite '{suite_id}' references unknown Skills: {unknown}")

    cases_path = HUB_ROOT / suite["cases_path"]
    cases = load_jsonl(cases_path)
    minimum = policies["cases_min"]
    maximum = policies["cases_max"]
    if not minimum <= len(cases) <= maximum:
        raise EvalError(f"suite '{suite_id}' must contain {minimum}-{maximum} cases, got {len(cases)}")

    ids = [case.get("id") for case in cases]
    if any(not case_id for case_id in ids) or len(ids) != len(set(ids)):
        raise EvalError(f"suite '{suite_id}' has missing or duplicate case ids")

    kinds = {case.get("kind") for case in cases}
    for required in policies["required_case_kinds"]:
        if required not in kinds:
            raise EvalError(f"suite '{suite_id}' is missing required case kind '{required}'")

    positive_coverage: set[str] = set()
    no_op_count = 0
    for case in cases:
        for field in ("prompt", "expected_skills", "forbidden_skills", "kind"):
            if field not in case:
                raise EvalError(f"suite '{suite_id}' case '{case['id']}' is missing '{field}'")
        expected = set(case["expected_skills"])
        forbidden = set(case["forbidden_skills"])
        unknown_case_skills = sorted((expected | forbidden) - set(hub_skills))
        if unknown_case_skills:
            raise EvalError(
                f"suite '{suite_id}' case '{case['id']}' references unknown Skills: {unknown_case_skills}"
            )
        if expected & forbidden:
            raise EvalError(f"suite '{suite_id}' case '{case['id']}' expects and forbids the same Skill")
        positive_coverage.update(expected & evaluated)
        if case["kind"] == "negative" and not expected:
            no_op_count += 1

    missing_positive = sorted(evaluated - positive_coverage)
    if missing_positive:
        raise EvalError(f"suite '{suite_id}' lacks a happy-path case for: {missing_positive}")
    if no_op_count < policies["no_op_cases_min"]:
        raise EvalError(
            f"suite '{suite_id}' requires at least {policies['no_op_cases_min']} negative no-op cases"
        )

    no_op_phrases = tuple(phrase.lower() for phrase in policies["no_op_phrases"])
    for skill_id in evaluated:
        skill = hub_skills[skill_id]
        path = HUB_ROOT / skill["canonical_path"]
        words = skill_word_count(path)
        if words > policies["skill_word_limit"]:
            raise EvalError(
                f"{path}: {words} words exceeds routing budget {policies['skill_word_limit']}"
            )
        text = path.read_text(encoding="utf-8").lower()
        if not any(phrase in text for phrase in no_op_phrases):
            raise EvalError(f"{path}: missing explicit no-op guidance")
        evaluation = skill.get("evaluation", {})
        if evaluation.get("suite") != suite_id:
            raise EvalError(f"skill '{skill_id}' does not point back to eval suite '{suite_id}'")
        if (
            skill.get("status") == "active"
            and evaluation.get("required_for_active")
            and evaluation.get("status") != "passed"
        ):
            raise EvalError(f"skill '{skill_id}' is active without a passed required eval")
    return cases


def validate(name: str) -> None:
    manifest, suites = selected_suites(name)
    hub_skills = load_hub_skills()
    total = 0
    for suite in suites:
        cases = validate_suite(manifest, suite, hub_skills)
        total += len(cases)
        print(f"EVAL_SUITE_OK: {suite['id']} ({len(cases)} cases, {len(suite['skills'])} skills)")
    print(f"SKILL_EVAL_OK: validated {len(suites)} suites and {total} cases")


def prepare(name: str, output: Path) -> None:
    manifest, suites = selected_suites(name)
    hub_skills = load_hub_skills()
    packet = []
    for suite in suites:
        cases = validate_suite(manifest, suite, hub_skills)
        catalog = [
            {
                "id": skill_id,
                "description": hub_skills[skill_id]["description"],
            }
            for skill_id in suite["skills"]
        ]
        for case in cases:
            packet.append(
                {
                    "suite": suite["id"],
                    "id": case["id"],
                    "prompt": case["prompt"],
                    "available_skills": catalog,
                    "response_contract": {"selected_skills": ["zero to three skill ids"]},
                }
            )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in packet),
        encoding="utf-8",
        newline="\n",
    )
    print(f"SKILL_EVAL_PACKET_OK: wrote {len(packet)} blind prompts to {output}")


def score(name: str, results_path: Path) -> None:
    manifest, suites = selected_suites(name)
    hub_skills = load_hub_skills()
    results = {row["id"]: row for row in load_jsonl(results_path)}
    failed = False
    for suite in suites:
        cases = validate_suite(manifest, suite, hub_skills)
        evaluated = set(suite["skills"])
        missing = [case["id"] for case in cases if case["id"] not in results]
        if missing:
            raise EvalError(f"missing model results for suite '{suite['id']}': {missing}")

        tp = fp = fn = exact = no_op_total = no_op_correct = 0
        forbidden_hits = []
        for case in cases:
            raw_selected = results[case["id"]].get("selected_skills", [])
            if not isinstance(raw_selected, list) or any(not isinstance(item, str) for item in raw_selected):
                raise EvalError(f"result '{case['id']}' selected_skills must be an array of Skill ids")
            selected = set(raw_selected)
            unavailable = sorted(selected - evaluated)
            if unavailable:
                raise EvalError(f"result '{case['id']}' selected unavailable Skills: {unavailable}")
            if len(selected) > 3:
                raise EvalError(f"result '{case['id']}' selected more than three Skills")
            expected = set(case["expected_skills"]) & evaluated
            forbidden = set(case["forbidden_skills"]) & evaluated
            tp += len(selected & expected)
            fp += len(selected - expected)
            fn += len(expected - selected)
            exact += int(selected == expected)
            if case["kind"] == "negative" and not expected:
                no_op_total += 1
                no_op_correct += int(not selected)
            if selected & forbidden:
                forbidden_hits.append(case["id"])

        precision = tp / (tp + fp) if tp + fp else 1.0
        recall = tp / (tp + fn) if tp + fn else 1.0
        no_op = no_op_correct / no_op_total if no_op_total else 1.0
        exact_rate = exact / len(cases)
        thresholds = suite.get("thresholds", manifest["thresholds"])
        passed = (
            precision >= thresholds["precision"]
            and recall >= thresholds["recall"]
            and no_op >= thresholds["no_op_accuracy"]
            and exact_rate >= thresholds["exact_match"]
            and not forbidden_hits
        )
        failed = failed or not passed
        print(
            f"EVAL_SCORE: {suite['id']} pass={str(passed).lower()} "
            f"precision={precision:.3f} recall={recall:.3f} "
            f"no_op={no_op:.3f} exact={exact_rate:.3f} forbidden_hits={forbidden_hits}"
        )
    if failed:
        raise EvalError("one or more eval suites failed their activation thresholds")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate, blind, and score Agent Skill routing evals.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--suite", default="all")

    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--suite", default="all")
    prepare_parser.add_argument("--output", type=Path, required=True)

    score_parser = subparsers.add_parser("score")
    score_parser.add_argument("--suite", default="all")
    score_parser.add_argument("--results", type=Path, required=True)

    args = parser.parse_args()
    try:
        if args.command == "validate":
            validate(args.suite)
        elif args.command == "prepare":
            prepare(args.suite, args.output)
        else:
            score(args.suite, args.results)
    except (OSError, json.JSONDecodeError, EvalError) as exc:
        print(f"SKILL_EVAL_ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
