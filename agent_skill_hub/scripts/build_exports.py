from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


HUB_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = HUB_ROOT / "catalog.json"
LOCK_PATH = HUB_ROOT / "sources.lock.json"
COLLECTIONS_ROOT = HUB_ROOT / "collections"
EVAL_MANIFEST_PATH = HUB_ROOT / "evals" / "manifest.json"
SECURITY_PROFILE_PATH = HUB_ROOT / "security-profile.json"
SECURITY_POLICY_PATH = HUB_ROOT / "SECURITY_PROFILE.md"


class ValidationError(RuntimeError):
    pass


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def merge_dicts(defaults: dict, overrides: dict) -> dict:
    merged = dict(defaults)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_catalog_skills() -> tuple[list[dict], dict[str, dict]]:
    catalog = load_json(CATALOG_PATH)
    skills = list(catalog.get("skills", []))
    collections: dict[str, dict] = {}

    if COLLECTIONS_ROOT.is_dir():
        for path in sorted(COLLECTIONS_ROOT.glob("*/catalog.json")):
            collection = load_json(path)
            collection_id = collection.get("id")
            if not collection_id:
                raise ValidationError(f"{path}: missing collection id")
            if collection_id != path.parent.name:
                raise ValidationError(
                    f"{path}: collection id '{collection_id}' does not match folder '{path.parent.name}'"
                )
            if collection_id in collections:
                raise ValidationError(f"duplicate collection id: {collection_id}")
            collections[collection_id] = collection

            defaults = collection.get("skill_defaults", {})
            for entry in collection.get("skills", []):
                skill = merge_dicts(defaults, entry)
                skill["collection_id"] = collection_id
                skills.append(skill)

    return skills, collections


def load_source_entries() -> tuple[list[dict], list[dict]]:
    lock = load_json(LOCK_PATH)
    entries = list(lock.get("sources", []))
    shared_packages: list[dict] = []

    if COLLECTIONS_ROOT.is_dir():
        for path in sorted(COLLECTIONS_ROOT.glob("*/sources.lock.json")):
            collection_lock = load_json(path)
            collection_id = collection_lock.get("id")
            if not collection_id:
                raise ValidationError(f"{path}: missing collection id")
            common = {
                key: collection_lock[key]
                for key in ("source_url", "resolved_url", "commit", "license", "imported_on")
                if key in collection_lock
            }
            for entry in collection_lock.get("skills", []):
                merged = {**common, **entry, "collection_id": collection_id}
                entries.append(merged)
            for package in collection_lock.get("shared_packages", []):
                shared_packages.append({**package, "collection_id": collection_id})

    return entries, shared_packages


def parse_skill(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValidationError(f"{path}: missing opening YAML frontmatter marker")

    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise ValidationError(f"{path}: missing closing YAML frontmatter marker") from exc

    metadata: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise ValidationError(f"{path}: unsupported frontmatter line: {line!r}")
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"').strip("'")

    body = "\n".join(lines[end + 1 :]).strip() + "\n"
    return metadata, body


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_sha256(skill_directory: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(
        (path for path in skill_directory.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(skill_directory).as_posix(),
    )
    for path in files:
        relative = path.relative_to(skill_directory).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def preserved_upstream_package_sha256(skill_directory: Path) -> str:
    digest = hashlib.sha256()
    mapped_files: list[tuple[str, Path]] = []
    for path in skill_directory.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(skill_directory).as_posix()
        if relative in {"SKILL.md", "SOURCE.md", "SOURCE_LICENSE"}:
            continue
        if relative == "UPSTREAM_SKILL.md":
            relative = "SKILL.md"
        mapped_files.append((relative, path))
    for relative, path in sorted(mapped_files, key=lambda item: item[0]):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def generated_header(skill_id: str, source_hash: str) -> str:
    return (
        "<!-- Generated by agent_skill_hub/scripts/build_exports.py. "
        "Do not edit directly. -->\n"
        f"<!-- source_skill={skill_id}; source_sha256={source_hash} -->\n\n"
    )


def expected_exports(
    skill: dict,
    body: str,
    source_hash: str,
    security_profile: dict,
    security_policy: str,
) -> dict[Path, str]:
    skill_id = skill["id"]
    title = skill["title"]
    header = generated_header(skill_id, source_hash)
    policy_block = (
        "## Mandatory security profile\n\n"
        f"Profile: `{security_profile['id']}`. The following policy overrides conflicting Skill instructions.\n\n"
        + security_policy.strip()
        + "\n\n"
    )

    exports: dict[Path, str] = {}
    chatgpt_mode = skill["activation"].get("chatgpt")
    if chatgpt_mode == "project-instructions":
        exports[HUB_ROOT / "exports" / "chatgpt" / skill_id / "PROJECT_INSTRUCTIONS.md"] = (
            header
            + f"# ChatGPT Project Instructions: {title}\n\n"
            + "Apply the following guidance when writing, reviewing, debugging, or refactoring code. "
            + "For trivial edits, use proportionate judgment.\n\n"
            + policy_block
            + body
        )

    langflow = skill["langflow"]
    langflow_mode = langflow["recommended_mode"]
    prompt_file: str | None = None
    if langflow_mode == "system-prompt":
        prompt_file = "SYSTEM_PROMPT.md"
        exports[HUB_ROOT / "exports" / "langflow" / skill_id / prompt_file] = (
            header
            + f"# Langflow Agent Instructions: {title}\n\n"
            + "Apply these instructions before planning, generating, reviewing, or changing code. "
            + "Keep project-specific rules and explicit user requirements higher priority.\n\n"
            + policy_block
            + body
        )
    elif langflow_mode == "prompt-template" or langflow.get("fallback_mode") == "prompt-template":
        prompt_file = "PROMPT_TEMPLATE.md"
        exports[HUB_ROOT / "exports" / "langflow" / skill_id / prompt_file] = (
            header
            + f"# Langflow Prompt Template: {title}\n\n"
            + "Use this specialist guidance only for tasks that match the Skill description. "
            + "Explicit user requirements and project contracts remain higher priority.\n\n"
            + policy_block
            + body
        )

    langflow_adapter = {
        "schema_version": 2,
        "skill_id": skill_id,
        "title": title,
        "description": skill["description"],
        "status": skill["status"],
        "recommended_mode": langflow_mode,
        "reason": langflow["reason"],
        "canonical_skill_path": skill["canonical_path"],
        "prompt_file": prompt_file,
        "configuration": {
            key: value
            for key, value in langflow.items()
            if key not in {"recommended_mode", "reason"}
        },
        "source_sha256": source_hash,
        "security_profile": security_profile["id"],
        "security_policy_file": security_profile["policy_file"],
    }
    collection_id = skill.get("collection_id")
    if collection_id:
        langflow_adapter["collection_id"] = collection_id
    if skill.get("evaluation"):
        langflow_adapter["evaluation"] = skill["evaluation"]
    shared_references = skill.get("shared_references", [])
    if collection_id and shared_references:
        langflow_adapter["shared_references"] = [
            f"collections/{collection_id}/shared/references/{name}"
            for name in shared_references
        ]

    exports[HUB_ROOT / "exports" / "langflow" / skill_id / "adapter.json"] = (
        json.dumps(langflow_adapter, ensure_ascii=False, indent=2) + "\n"
    )
    return exports


def validate_and_collect() -> dict[Path, str]:
    skills, collections = load_catalog_skills()
    source_entries, shared_packages = load_source_entries()
    security_profile = load_json(SECURITY_PROFILE_PATH)
    security_policy = SECURITY_POLICY_PATH.read_text(encoding="utf-8")
    if security_profile.get("id") != "internal-enterprise":
        raise ValidationError("security-profile.json must define the internal-enterprise profile")
    if security_profile.get("policy_file") != SECURITY_POLICY_PATH.name:
        raise ValidationError("security-profile.json has an unexpected policy_file")
    source_by_id = {entry["id"]: entry for entry in source_entries}
    if len(source_entries) != len(source_by_id):
        raise ValidationError("source lock files contain duplicate skill ids")

    ids = [skill.get("id") for skill in skills]
    if not ids or any(not skill_id for skill_id in ids):
        raise ValidationError("catalog.json must contain at least one skill with an id")
    if len(ids) != len(set(ids)):
        raise ValidationError("catalog.json contains duplicate skill ids")

    excluded_ids = set(security_profile.get("excluded_skill_ids", []))
    excluded_cataloged = sorted(excluded_ids.intersection(ids))
    if excluded_cataloged:
        raise ValidationError(
            "security profile excludes cataloged skills: " + ", ".join(excluded_cataloged)
        )

    eval_suites: set[str] = set()
    if EVAL_MANIFEST_PATH.is_file():
        eval_manifest = load_json(EVAL_MANIFEST_PATH)
        eval_suites = {suite["id"] for suite in eval_manifest.get("suites", [])}

    expected: dict[Path, str] = {}
    for skill in skills:
        skill_id = skill["id"]
        for field in ("title", "description", "status", "canonical_path", "targets", "activation", "langflow"):
            if field not in skill:
                raise ValidationError(f"catalog skill '{skill_id}' is missing '{field}'")
        if skill["status"] not in {"active", "candidate", "disabled"}:
            raise ValidationError(f"catalog skill '{skill_id}' has unsupported status '{skill['status']}'")

        evaluation = skill.get("evaluation")
        if skill["status"] == "candidate" and not evaluation:
            raise ValidationError(f"candidate skill '{skill_id}' is missing evaluation metadata")
        if evaluation:
            suite_id = evaluation.get("suite")
            if not suite_id or suite_id not in eval_suites:
                raise ValidationError(f"skill '{skill_id}' references unknown eval suite '{suite_id}'")
            if (
                skill["status"] == "active"
                and evaluation.get("required_for_active")
                and evaluation.get("status") != "passed"
            ):
                raise ValidationError(f"skill '{skill_id}' is active without a passed required eval")

        canonical = HUB_ROOT / skill["canonical_path"]
        if not canonical.is_file():
            raise ValidationError(f"missing canonical skill: {canonical}")

        metadata, body = parse_skill(canonical)
        for field in ("name", "description"):
            if not metadata.get(field):
                raise ValidationError(f"{canonical}: frontmatter is missing '{field}'")
        if metadata["name"] != skill_id:
            raise ValidationError(
                f"{canonical}: frontmatter name '{metadata['name']}' does not match catalog id '{skill_id}'"
            )

        source = source_by_id.get(skill_id)
        if source is None:
            raise ValidationError(f"sources.lock.json is missing '{skill_id}'")

        actual_hash = sha256(canonical)
        if actual_hash != source.get("skill_sha256"):
            raise ValidationError(
                f"{canonical}: SHA-256 drift; expected {source.get('skill_sha256')}, got {actual_hash}"
            )
        if not source.get("license"):
            raise ValidationError(f"sources.lock.json entry '{skill_id}' is missing 'license'")
        if metadata.get("license") and metadata["license"] != source.get("license"):
            raise ValidationError(
                f"{canonical}: license '{metadata['license']}' does not match lock '{source.get('license')}'"
            )

        upstream_hash = source.get("upstream_skill_sha256")
        if upstream_hash:
            preserved_upstream = canonical.parent / "UPSTREAM_SKILL.md"
            if not preserved_upstream.is_file():
                raise ValidationError(f"missing byte-preserved upstream Skill: {preserved_upstream}")
            actual_upstream_hash = sha256(preserved_upstream)
            if actual_upstream_hash != upstream_hash:
                raise ValidationError(
                    f"{preserved_upstream}: upstream SHA-256 drift; "
                    f"expected {upstream_hash}, got {actual_upstream_hash}"
                )
            expected_upstream_package = source.get("upstream_package_sha256")
            if not expected_upstream_package:
                raise ValidationError(f"source lock entry '{skill_id}' is missing upstream_package_sha256")
            actual_upstream_package = preserved_upstream_package_sha256(canonical.parent)
            if actual_upstream_package != expected_upstream_package:
                raise ValidationError(
                    f"{canonical.parent}: preserved upstream package drift; "
                    f"expected {expected_upstream_package}, got {actual_upstream_package}"
                )

        source_notice = canonical.parent / "SOURCE.md"
        if not source_notice.is_file():
            raise ValidationError(f"missing source and license notice: {source_notice}")

        actual_package_hash = package_sha256(canonical.parent)
        if actual_package_hash != source.get("package_sha256"):
            raise ValidationError(
                f"{canonical.parent}: package SHA-256 drift; "
                f"expected {source.get('package_sha256')}, got {actual_package_hash}"
            )

        collection_id = skill.get("collection_id")
        if collection_id:
            collection = collections.get(collection_id)
            if collection is None:
                raise ValidationError(f"skill '{skill_id}' references unknown collection '{collection_id}'")
            shared_root = COLLECTIONS_ROOT / collection_id / "shared"
            if not (shared_root / "LICENSE").is_file():
                raise ValidationError(f"collection '{collection_id}' is missing its shared LICENSE")
            for reference in skill.get("shared_references", []):
                reference_path = shared_root / "references" / reference
                if not reference_path.is_file():
                    raise ValidationError(
                        f"skill '{skill_id}' requires missing shared reference: {reference_path}"
                    )

        expected.update(
            expected_exports(skill, body, actual_hash, security_profile, security_policy)
        )

    extra_locks = sorted(set(source_by_id) - set(ids))
    if extra_locks:
        raise ValidationError(f"sources.lock.json has entries not present in catalog.json: {extra_locks}")

    for package in shared_packages:
        for field in ("id", "canonical_path", "package_sha256"):
            if not package.get(field):
                raise ValidationError(
                    f"shared package in collection '{package.get('collection_id')}' is missing '{field}'"
                )
        package_path = HUB_ROOT / package["canonical_path"]
        if not package_path.is_dir():
            raise ValidationError(f"missing shared package: {package_path}")
        actual_hash = package_sha256(package_path)
        if actual_hash != package["package_sha256"]:
            raise ValidationError(
                f"{package_path}: package SHA-256 drift; "
                f"expected {package['package_sha256']}, got {actual_hash}"
            )

    discovered = {
        path.name
        for path in (HUB_ROOT / "skills").iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    }
    uncataloged = sorted(discovered - set(ids))
    if uncataloged:
        raise ValidationError(f"skill folders are missing from catalog.json: {uncataloged}")

    excluded_folders = sorted(excluded_ids.intersection(discovered))
    if excluded_folders:
        raise ValidationError(
            "security profile excludes skill folders still on disk: " + ", ".join(excluded_folders)
        )

    return expected


def run(check: bool) -> None:
    expected = validate_and_collect()
    stale: list[str] = []

    for path, content in expected.items():
        if check:
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                stale.append(str(path.relative_to(HUB_ROOT)))
            continue

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")

    if stale:
        raise ValidationError("missing or stale generated exports: " + ", ".join(stale))

    mode = "validated" if check else "generated"
    print(f"SKILL_HUB_OK: {mode} {len(expected)} exports")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and validate Agent Skill Hub exports.")
    parser.add_argument("--check", action="store_true", help="Validate without writing files.")
    args = parser.parse_args()

    try:
        run(check=args.check)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        print(f"SKILL_HUB_ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
