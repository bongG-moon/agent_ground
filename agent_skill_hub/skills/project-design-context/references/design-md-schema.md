# Enterprise DESIGN.md schema

Use this schema for a project-owned, stack-independent design contract.
`DESIGN.md` describes intent and constraints; it does not replace CSS, design
tokens, component code, or product behavior.

## Frontmatter

```yaml
---
omd: 0.1
brand: <verified project or product name>
status: draft
security_profile: internal-enterprise
---
```

Allowed `status` values:

- `draft`: unresolved fields and missing recommended sections are allowed.
- `approved`: all required sections are complete and no unresolved marker
  remains.

Do not add confidential URLs, credentials, employee identifiers, internal
hostnames, or absolute user paths to frontmatter.

## Section contract

Sections 1–5 are required. Sections 6–15 are recommended and become required
when `status: approved` or strict validation is requested.

| # | Section | Record |
|---:|---|---|
| 1 | Visual Theme & Atmosphere | Intended feeling, density, visual restraint, and anti-patterns |
| 2 | Color Palette & Roles | Semantic names, exact values, roles, and contrast intent |
| 3 | Typography Rules | Families, fallbacks, sizes, weights, line height, numerals, and locale behavior |
| 4 | Component Stylings | Existing component variants, states, shape, focus, and disabled behavior |
| 5 | Layout Principles | Grid, spacing, reading width, hierarchy, and information density |
| 6 | Depth & Elevation | Borders, shadows, overlays, and layering |
| 7 | Do's and Don'ts | Concrete allowed and prohibited patterns |
| 8 | Responsive Behavior | Breakpoints, reflow, density, and touch targets |
| 9 | Agent Prompt Guide | Protected behavior and how agents should resolve missing rules |
| 10 | Voice & Tone | Context-specific voice and forbidden phrases |
| 11 | Brand Narrative | Verified project purpose; no invented history, claims, or quotes |
| 12 | Principles | First-principles rules that resolve ambiguous design decisions |
| 13 | Personas | Verified or user-approved user segments, not fabricated biographies |
| 14 | States | Empty, loading, error, success, partial, and degraded behavior |
| 15 | Motion & Easing | Named durations, easing, interruption, and reduced-motion behavior |

## Evidence notation

Prefer concise inline labels where the source matters:

```markdown
- **Action blue (`#1f6feb`)** — primary action and focus ring.
  Evidence: `src/styles/tokens.css:12` (verified).
```

Use this exact marker when evidence is missing:

```text
[UNRESOLVED: state what must be confirmed and by whom]
```

Do not replace an unresolved value with a familiar framework default.

## Completeness rules

- Give semantic colors both a name and an exact code.
- Explain why a rule exists at least once per section.
- Use existing repository component names and token identifiers where possible.
- Treat behavior, URLs, field names, and data contracts as protected unless the
  user explicitly authorizes a change.
- Include accessible focus, error, reduced-motion, and responsive behavior.
- Keep one authoritative rule per decision; point to it instead of duplicating
  divergent values across sections.

