---
name: re0
description: Rewrite an existing artifact that has accumulated stale deltas, duplicated guidance, scaffolding residue, or patch-on-patch prose into a clean current v0. Use when a user asks to clean up, sync, deduplicate, de-noise, refresh, or rewrite an existing document, specification, prompt, or configuration after iteration. Do not use for creating a new artifact, a routine typo fix, behavior-changing code refactoring, consolidation of one fact across many locations, or changelogs and release notes that intentionally preserve history.
---

# Re0

Rewrite the target as if the current truth had been written cleanly the first
time. Preserve useful behavior and voice; remove the history of how it drifted.

## Establish scope

1. Identify the exact artifact and requested write boundary.
2. Read it end to end. Read only the canonical or sibling artifacts required
   to distinguish current truth from stale residue.
3. List protected behavior, interfaces, evidence, intentional history, and
   unresolved conflicts before changing them.
4. If the target, authority, or replacement scope remains ambiguous, stop with
   one concrete question. Otherwise proceed within the user's requested scope.

For a complex or multi-artifact rewrite, read
[artifact-reset-contract.md](references/artifact-reset-contract.md).

## Rewrite

1. Remove stale deltas, repeated rules, obsolete scaffolding, contradictory
   leftovers, and process narration that does not help future execution.
2. Fold durable lessons into the section where they should have lived from the
   start. Express what is true now, not a history of edits.
3. Prefer editing existing sections over adding new ones. Do not create
   auxiliary files unless the user requests them.
4. Preserve citations, legal notices, public interfaces, test contracts,
   identifiers, and deliberate historical records.
5. Use Unicode-safe, target-specific edits. Assert the intended occurrence
   exists; report a miss instead of silently applying a broad replacement.

## Verify

- Re-read the whole result without relying on session context.
- Confirm the current rules remain complete, contradictions are resolved or
  explicitly surfaced, and no protected detail was lost.
- Run the artifact's existing formatter, validator, or tests when available.
- Report what noise was removed, what truth was retained, and any unresolved
  judgment call.

A pass that finds nothing genuinely stale changes nothing. If the request is
outside this scope, otherwise do not apply this Skill.

Keep confidential artifacts local or on approved internal endpoints. Do not
fetch public references, install packages, call consumer models, add hooks, or
publish changes as part of this workflow.
