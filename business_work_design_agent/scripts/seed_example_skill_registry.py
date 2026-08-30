from __future__ import annotations

"""Validate or explicitly seed the local-demo Skill registry example."""

import argparse
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pymongo import ASCENDING, MongoClient, ReplaceOne


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SAMPLE = PROJECT_ROOT / "samples" / "skill_registry_example.json"
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")
SKILL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,127}$")
HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
ALLOWED_KEYS = {
    "tenant_id",
    "skill_id",
    "name",
    "version",
    "prompt_sha256",
    "trigger_rules",
    "near_miss_rules",
    "prompt_text",
    "forbidden_actions",
    "status",
    "acl",
    "approved_by",
    "approved_at",
    "match_reason",
    "target_stage",
}


def _load_items(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("items")
    if not isinstance(payload, list) or not payload:
        raise ValueError("Skill sample must be a non-empty JSON array or {items:[...]} object.")
    if any(not isinstance(item, dict) for item in payload):
        raise ValueError("Every Skill registry item must be a JSON object.")
    return payload


def _valid_rule(rule: Any) -> bool:
    if isinstance(rule, str):
        return bool(rule.strip()) and len(rule) <= 1000
    if not isinstance(rule, dict) or set(rule) - {"kind", "value", "values", "terms"}:
        return False
    if str(rule.get("kind") or "contains").lower() not in {"contains", "all"}:
        return False
    value_keys = [key for key in ("value", "values", "terms") if key in rule]
    if len(value_keys) != 1:
        return False
    selected = rule[value_keys[0]]
    values = [selected] if value_keys[0] == "value" else selected
    return (
        isinstance(values, list)
        and 1 <= len(values) <= 100
        and all(isinstance(value, str) and value.strip() and len(value) <= 1000 for value in values)
    )


def validate_skill(entry: dict[str, Any]) -> dict[str, Any]:
    extra = set(entry) - ALLOWED_KEYS
    if extra:
        raise ValueError(f"Skill registry item contains unsupported keys: {sorted(extra)}")
    for field in ("tenant_id", "skill_id", "name", "version", "prompt_text", "approved_by", "approved_at"):
        if not isinstance(entry.get(field), str) or not entry[field].strip():
            raise ValueError(f"Skill registry field {field!r} must be a non-empty string.")
    if len(entry["tenant_id"]) > 128 or len(entry["version"]) > 128:
        raise ValueError("tenant_id and version must be at most 128 characters.")
    if not SKILL_ID_PATTERN.fullmatch(entry["skill_id"]):
        raise ValueError("skill_id must match the approved lower-case identifier contract.")
    if len(entry["name"]) > 256 or len(entry["approved_by"]) > 256:
        raise ValueError("name and approved_by exceed the Skill registry limits.")
    if len(entry["prompt_text"]) > 50_000:
        raise ValueError("prompt_text exceeds the 50,000 character Skill registry limit.")
    prompt_hash = entry.get("prompt_sha256")
    if not isinstance(prompt_hash, str) or not HASH_PATTERN.fullmatch(prompt_hash):
        raise ValueError("prompt_sha256 must use sha256:<64 lower-case hex> format.")
    actual_hash = "sha256:" + hashlib.sha256(entry["prompt_text"].encode("utf-8")).hexdigest()
    if prompt_hash != actual_hash:
        raise ValueError("prompt_sha256 does not match prompt_text.")
    approved_at = datetime.fromisoformat(entry["approved_at"].replace("Z", "+00:00"))
    if approved_at.tzinfo is None:
        raise ValueError("approved_at must include an explicit timezone.")
    if entry.get("status") != "active":
        raise ValueError("The E2E sample Skill must be active.")
    forbidden_actions = entry.get("forbidden_actions", [])
    if (
        not isinstance(forbidden_actions, list)
        or len(forbidden_actions) > 100
        or any(not isinstance(item, str) or len(item) > 128 for item in forbidden_actions)
    ):
        raise ValueError("forbidden_actions must contain at most 100 strings of at most 128 characters.")
    match_reason = entry.get("match_reason")
    target_stage = entry.get("target_stage")
    if match_reason is not None and (not isinstance(match_reason, str) or len(match_reason) > 500):
        raise ValueError("match_reason must be a string of at most 500 characters.")
    if target_stage is not None and (not isinstance(target_stage, str) or len(target_stage) > 100):
        raise ValueError("target_stage must be a string of at most 100 characters.")
    trigger_rules = entry.get("trigger_rules")
    near_miss_rules = entry.get("near_miss_rules")
    if (
        not isinstance(trigger_rules, list)
        or not 1 <= len(trigger_rules) <= 100
        or not all(_valid_rule(rule) for rule in trigger_rules)
    ):
        raise ValueError("trigger_rules must contain at least one valid bounded rule.")
    if (
        not isinstance(near_miss_rules, list)
        or len(near_miss_rules) > 100
        or not all(_valid_rule(rule) for rule in near_miss_rules)
    ):
        raise ValueError("near_miss_rules must be a list of valid bounded rules.")
    acl = entry.get("acl")
    if not isinstance(acl, dict) or set(acl) != {"visibility", "groups", "subjects"}:
        raise ValueError("acl must contain exactly visibility, groups, and subjects.")
    if acl.get("visibility") not in {"tenant", "group", "private"}:
        raise ValueError("acl visibility is invalid.")
    groups = acl.get("groups")
    subjects = acl.get("subjects")
    if (
        not isinstance(groups, list)
        or len(groups) > 200
        or any(not isinstance(item, str) or not item.strip() or len(item) > 128 for item in groups)
    ):
        raise ValueError("acl groups must be at most 200 non-empty strings of at most 128 characters.")
    if (
        not isinstance(subjects, list)
        or len(subjects) > 200
        or any(not isinstance(item, str) or not item.strip() or len(item) > 256 for item in subjects)
    ):
        raise ValueError("acl subjects must be at most 200 non-empty strings of at most 256 characters.")
    if acl["visibility"] == "group" and not acl["groups"]:
        raise ValueError("group-visible Skill requires at least one group.")
    if acl["visibility"] == "private" and not acl["subjects"]:
        raise ValueError("private Skill requires at least one subject.")
    return json.loads(json.dumps(entry, ensure_ascii=False))


def _identifier(value: str, label: str) -> str:
    if not IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"{label} is not a safe MongoDB identifier.")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate or seed samples/skill_registry_example.json.")
    parser.add_argument("--sample", type=Path, default=DEFAULT_SAMPLE)
    parser.add_argument("--mongodb-uri", default=os.environ.get("MONGODB_URI", ""))
    parser.add_argument("--database", default=os.environ.get("MONGODB_DATABASE", "business_work_design"))
    parser.add_argument("--collection", default="skill_registry")
    parser.add_argument("--apply", action="store_true", help="Actually upsert the validated sample into MongoDB.")
    args = parser.parse_args()

    sample_path = args.sample.resolve()
    skills = [validate_skill(item) for item in _load_items(sample_path)]
    identities = {(item["tenant_id"], item["skill_id"], item["version"]) for item in skills}
    if len(identities) != len(skills):
        raise ValueError("Duplicate tenant_id + skill_id + version in sample.")
    database_name = _identifier(str(args.database), "database")
    collection_name = _identifier(str(args.collection), "collection")

    if not args.apply:
        print(json.dumps({
            "ok": True,
            "status": "VALIDATED_ONLY",
            "apply_required_for_write": True,
            "sample": str(sample_path),
            "database": database_name,
            "collection": collection_name,
            "skill_count": len(skills),
        }, ensure_ascii=False, indent=2))
        return 0
    if not str(args.mongodb_uri).strip():
        raise ValueError("MONGODB_URI or --mongodb-uri is required with --apply.")

    client = MongoClient(
        str(args.mongodb_uri),
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        retryReads=True,
        retryWrites=True,
    )
    try:
        client.admin.command("ping")
        collection = client[database_name][collection_name]
        collection.create_index(
            [("tenant_id", ASCENDING), ("skill_id", ASCENDING), ("version", ASCENDING)],
            unique=True,
            name="uq_skill_registry_identity",
        )
        result = collection.bulk_write(
            [
                ReplaceOne(
                    {"tenant_id": item["tenant_id"], "skill_id": item["skill_id"], "version": item["version"]},
                    item,
                    upsert=True,
                )
                for item in skills
            ],
            ordered=True,
        )
        print(json.dumps({
            "ok": True,
            "status": "APPLIED",
            "database": database_name,
            "collection": collection_name,
            "skill_count": len(skills),
            "matched_count": result.matched_count,
            "modified_count": result.modified_count,
            "upserted_count": result.upserted_count,
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
