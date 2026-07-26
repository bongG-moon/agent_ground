---
name: ssotize
description: Audit a specific fact, value, definition, status, decision, or rule scattered across multiple files or approved internal systems; choose one canonical source, propose exact replacement references, and mutate only after explicit approval. Use when a user asks to locate the source of truth, deduplicate, consolidate, unify, reconcile, or establish SSOT across artifacts. Do not use for cleanup within one artifact, behavior-changing refactoring, database migration, or repeated wording that is intentionally contextual.
---

# SSOTize

Establish one authoritative home for one truth. Begin read-only and never
consolidate across a trust or permission boundary without an explicit decision.

## Audit

1. Name the single fact in scope. Do not treat an entire document as one fact.
2. Search all user-approved locations twice using different identifiers,
   synonyms, or mechanisms.
3. Classify each occurrence as exact copy, paraphrase, partial, stale,
   contradictory, generated, or intentional context.
4. Choose the canonical home closest to where the truth is maintained. If no
   location is authoritative, propose a new home without creating it.
5. Read [consolidation-contract.md](references/consolidation-contract.md) and
   present the occurrence map, conflicts, unique details, canonical-home
   rationale, and exact mutation plan.

Stop after the audit unless the user explicitly approves the plan.

## Consolidate after approval

1. Make the canonical home complete before removing any copy.
2. Resolve contradictions from authoritative evidence or a user decision.
   Never select a convenient value silently.
3. Replace each redundant occurrence with a live reference, import, shared
   read, or stable pointer appropriate to the medium.
4. Edit per occurrence with Unicode-safe, target-aware operations. Assert the
   target still matches the approved plan; report drift instead of applying a
   blanket replacement.
5. Preserve intentional context, audit history, legal text, generated outputs,
   and boundaries between internal and public material.

## Verify

- Re-run both discovery methods.
- Confirm the canonical home contains every unique required detail.
- Resolve each pointer and ensure consumers still work.
- Run affected tests or validators.
- Report consolidated, audited-only, conflicting, and unresolved locations.

A pass that finds no scatter changes nothing. If the task is a single-artifact
rewrite, code cleanup, or unapproved cross-system migration, otherwise do not
apply this Skill.

Keep searches, artifacts, and results local or on approved internal endpoints.
Do not call public APIs, upload content, or alter external systems.
