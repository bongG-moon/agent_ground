# Evidence, precedence, and conflict handling

## Evidence classes

Classify inputs before writing rules.

| Class | Examples | May become an approved rule? |
|---|---|---|
| Verified repository evidence | token files, CSS variables, component variants, tests, existing product behavior | Yes |
| Verified internal brand evidence | approved brand guide, internal design-system docs, approved screenshots | Yes |
| Explicit user statement | “CTA는 대문자를 사용하지 않는다” | Yes, after confirming scope |
| Inference | repeated pattern, likely intent, model interpretation | Draft proposal only |
| Unknown | missing value, conflicting sources, unclear ownership | No; mark unresolved |
| Public reference | approved read-only documentation imported without confidential query data | Inspiration only unless approved |

Never convert a public company's brand fact into a fact about the user's
project. Never treat model familiarity as evidence.

## Precedence

Resolve conflicts in this order:

1. Current explicit user instruction
2. Protected product behavior and established repository contracts
3. Approved internal design system and brand guidance
4. Approved project `DESIGN.md`
5. Explicit pending correction
6. Specialist Skill guidance
7. Framework defaults

If two sources at the same level conflict, do not choose silently. Report:

```text
Conflict: <decision>
Source A: <path or user statement>
Source B: <path or user statement>
Impact: <affected surfaces>
Recommended resolution: <smallest coherent choice>
```

## Interaction with existing Skills

- `hallmark`: primary workflow for greenfield UI, audits, and redesigns.
  Supply `DESIGN.md` constraints; do not start a competing design workflow.
- `frontend-ui-engineering`: implements responsive and accessible UI. Supply
  tokens, states, and protected behavior.
- `apple-design` and `emil-design-eng`: explicit specialist lenses. They may
  refine implementation only when consistent with project evidence.
- motion Skills: review or improve declared motion; they may not invent a new
  motion identity.
- `flint-chart-author`: owns chart specifications. This Skill may supply brand
  colors only when the chart task explicitly needs them.
- `context-engineering`: may manage broader project rules. Coordinate managed
  blocks and never overwrite unmarked content.

Use at most one primary workflow. This Skill is primary only for creating,
updating, or auditing design context; otherwise it is supporting context.

## Data boundary

Keep code, prompts, screenshots, brand documents, paths, and generated
artifacts inside approved local or internal systems. Do not:

- send project material to public models, websites, MCP servers, or telemetry;
- fetch a reference using an internal project name or confidential query;
- control an authenticated personal browser;
- download or execute public packages at runtime;
- add external fonts, assets, analytics, or links without approval.

If an approved internal source cannot be reached, stop with an unresolved item
instead of falling back to a public service.

