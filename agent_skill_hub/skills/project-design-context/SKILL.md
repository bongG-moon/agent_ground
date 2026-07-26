---
name: project-design-context
description: Create, apply, update, or audit a project-owned DESIGN.md from verified local evidence. Use when a user asks to establish a design system or brand context, follow DESIGN.md during UI work, record an explicit design preference, fold approved preferences into the spec, or compare an implementation with its declared colors, typography, components, voice, states, and motion. Do not use for ordinary UI implementation with no DESIGN.md or brand-governance request, chart authoring, animation terminology, or general code review.
---

# Project Design Context

Maintain `DESIGN.md` as a portable contract shared by coding agents, ChatGPT Enterprise, and approved internal Langflow flows. Supply constraints to the primary UI workflow; do not replace it.

## Choose one mode

- **Bootstrap**: inspect the repository and propose a new `DESIGN.md`.
- **Apply**: read the complete existing file and extract constraints for a UI task.
- **Record**: append an explicitly approved correction to `.design/preferences.md`.
- **Fold**: propose how pending corrections change `DESIGN.md`, then apply approved scopes.
- **Audit**: compare artifacts with the spec without editing them.

If the request is only to build or redesign UI, let `hallmark` or the project UI workflow lead and use this Skill only as supporting context. Otherwise do not apply this Skill.

## Respect authority

Apply this order:

1. Current explicit user instruction
2. Protected product behavior and established repository design system
3. Verified internal brand evidence
4. Approved `DESIGN.md`
5. Pending preferences, after surfacing conflicts
6. Specialist guidance and framework defaults

Never silently make a lower source override a higher one.

## Work evidence-first

1. Read project instructions, existing `DESIGN.md`, token/config files, shared components, and approved internal brand material.
2. Classify every proposed rule as verified, user-provided, inferred, or unresolved. Do not present inference as fact.
3. Read [evidence-and-precedence.md](references/evidence-and-precedence.md) before creating or replacing the spec.
4. Use [DESIGN.template.md](assets/DESIGN.template.md) and [design-md-schema.md](references/design-md-schema.md). Keep `[UNRESOLVED: ...]` markers where evidence is missing.
5. Show the proposed file operations and conflicts. Require explicit approval before creating, replacing, folding, or adding agent-instruction blocks.
6. Run `python scripts/validate_design_md.py <path>` after a draft and `--strict` before marking it approved.

## Execute safely

- Preserve an existing `DESIGN.md`; propose a focused diff or a timestamped backup instead of overwriting it.
- Record preferences only when the user explicitly asks or accepts a recording proposal. Never infer and persist taste automatically.
- In Audit mode remain read-only and cite file/line evidence for every failure.
- Use the operation and Langflow contracts in [workflow-contracts.md](references/workflow-contracts.md).
- Keep all confidential inputs and outputs local or on approved internal endpoints. Do not fetch public references at runtime, run public packages, control authenticated external browsers, call consumer models, install hooks, or upload artifacts.

