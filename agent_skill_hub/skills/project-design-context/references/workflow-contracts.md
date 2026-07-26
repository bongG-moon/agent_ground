# Operation and integration contracts

## Bootstrap

Inputs:

- project root;
- user goal and protected behavior;
- existing code, tokens, components, and approved internal brand documents.

Procedure:

1. Inspect before questioning. Ask one consolidated question only for facts the
   repository cannot answer.
2. Produce a proposal containing evidence sources, conflicts, unresolved
   fields, intended file operations, and a `DESIGN.md` preview.
3. Wait for explicit approval.
4. Create `DESIGN.md` from `assets/DESIGN.template.md`. If one exists, propose
   a focused diff; create a timestamped backup only when replacement is
   explicitly approved.
5. Validate the result.
6. Separately offer managed agent-instruction blocks. Do not add them without
   approval.

## Apply

Read the complete `DESIGN.md` and any pending preference log. Extract only the
constraints relevant to the requested surface:

```json
{
  "tokens": {},
  "component_rules": [],
  "voice_rules": [],
  "state_rules": [],
  "motion_rules": [],
  "protected_behavior": [],
  "conflicts": [],
  "unresolved": []
}
```

Give this context to the one primary UI workflow. If a requested value is not
declared, ask or leave it unresolved; do not invent it.

## Record and fold preferences

Use `.design/preferences.md`:

````markdown
---
schema: project-design-preferences/v1
---

# Preference Log

## <ISO timestamp> — <short slug>

```design-preference
id: pref_<timestamp>_<short-hash>
scope: color | typography | component | layout | voice | state | motion
status: pending
source: explicit-user
context: <relative path or task, if available>
```

<one clear rule>
````

Record only after an explicit request or accepted proposal. To fold:

1. Group pending entries by scope and show conflicts.
2. Ask which scopes to apply.
3. Edit only affected `DESIGN.md` sections.
4. Mark each reviewed entry `applied`, `rejected`, or `superseded`, preserving
   its original body.
5. Revalidate. Never auto-fold inferred review findings.

## Audit

Remain read-only. Run the validator, then compare declared values with relevant
artifacts. Report each finding with severity, file/line evidence, declared
rule, observed value, and smallest fix. Do not perform public link checks.

## Optional managed instruction blocks

Add only after approval and preserve all unmarked content.

```markdown
<!-- project-design-context:start v=1 -->
Before UI, styling, microcopy, state, or motion work, read `./DESIGN.md`.
Preserve existing product behavior. Report conflicts instead of guessing.
<!-- project-design-context:end -->
```

Use the same block in `AGENTS.md` and `CLAUDE.md`. Do not create hooks or
settings files.

## Langflow contract

Recommended internal flow:

```text
request
→ load DESIGN.md and approved inputs
→ extract relevant context
→ detect conflicts and unresolved fields
→ internal model proposes DESIGN.md or implementation constraints
→ deterministic validation
→ human approval
→ optional approved local write
```

Input:

```json
{
  "mode": "bootstrap | apply | record | fold | audit",
  "project_brief": "",
  "existing_design_tokens": {},
  "verified_brand_facts": {},
  "protected_behavior": [],
  "user_preferences": []
}
```

Output:

```json
{
  "design_md": "",
  "context": {},
  "unresolved_fields": [],
  "source_provenance": [],
  "conflicts": [],
  "validation": {},
  "write_proposed": false
}
```

The model step must use the company-operated endpoint. Keep file writes in a
separate approved component after the human-approval gate.
