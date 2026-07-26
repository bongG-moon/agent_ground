# Source and derivation

- Original site: <https://oh-my-design.kr/>
- Repository: <https://github.com/kwakseongjae/oh-my-design>
- Reviewed commit: `0a7f3a1e17814c8a1b000ce075b3b2620b70db9e`
- License: MIT; the reviewed license text is preserved in `SOURCE_LICENSE`.
- Imported on: 2026-07-26

## Reviewed upstream material

- `spec/omd-v0.1.md`
- `skills/omd-init/SKILL.md`
- `skills/omd-apply/SKILL.md`
- `skills/omd-remember/SKILL.md`
- `skills/omd-learn/SKILL.md`
- `skills/omd-sync/SKILL.md`
- `skills/omd-final-qa/SKILL.md`
- `package.json` and `scripts/postinstall.cjs`
- External-capability review of `claude-design`, `omd-reference-capture`,
  `omd-asset-fetch`, and `omd-codex-image`

## Enterprise derivation

This package retains the project-owned `DESIGN.md` concept, evidence-before-
inference rule, preference review loop, non-destructive managed instruction
blocks, and read-only final audit. It is rewritten as one portable Skill with
standard-library validation and no dependency on the upstream CLI.

The following upstream capabilities are intentionally not bundled:

- company reference catalog, logos, screenshots, fonts, or captured assets
- `npx` installation, postinstall behavior, settings changes, and hooks
- public web fetch, link checking, remote asset download, or live capture
- consumer Claude/Codex access, authenticated browser automation, or MCP
- automatic preference persistence, multi-agent orchestration, or deployment

Third-party company references remain the property of their respective owners
and are not copied into this package. Public material may be reviewed only
through an organization-approved read-only ingress path and must never receive
confidential project context.

