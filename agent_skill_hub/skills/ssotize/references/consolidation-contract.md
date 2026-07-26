# SSOT consolidation contract

## Occurrence map

Report the audit before requesting approval:

| Location | Kind | Authority | Unique detail | Proposed action |
| --- | --- | --- | --- | --- |
| relative path or approved system | copy, partial, stale, conflict, intentional | high, peer, low, unknown | detail or none | keep, fold, reference, reconcile |

Then state:

```text
Truth in scope:
Proposed canonical home:
Why this home is authoritative:
Details to fold:
Conflicts requiring a decision:
Trust or permission boundaries:
Exact mutations:
Validation:
```

## Canonical-home selection

Prefer, in order:

1. The source that actually controls runtime behavior
2. An approved configuration or contract maintained with that behavior
3. The owning team's canonical specification or decision record
4. A new dedicated source only when none exists

Do not promote a stale README or convenient copy over the system that owns the
value.

## Replacement rules

- Documentation: link to a stable heading or leave a short `See ...` pointer.
- Code: import or call the shared definition using language-aware edits.
- Configuration: source a supported shared value; do not invent indirection the
  runtime cannot resolve.
- Generated artifacts: fix the generator, then regenerate. Do not edit only the
  generated copy.
- Cross-system copies: consolidate only when both systems and the write path
  were explicitly approved.

## Approval boundary

Approval must cover the named canonical home and every intended mutation.
Return to read-only mode if new locations, permission boundaries, or conflicts
appear after approval.

## No-op

Do not consolidate:

- explanations that intentionally adapt one fact to different audiences;
- legal or audit records that must preserve historical text;
- independent values that merely share a similar label;
- cached or generated copies whose supported regeneration path is unknown.
