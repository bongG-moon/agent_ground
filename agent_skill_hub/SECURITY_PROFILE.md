# Internal Enterprise security profile

This policy is mandatory for every Skill installed or exported from this Hub.
If another Skill instruction conflicts with this file, this file wins.

## Approved model boundaries

- Claude Code may use only the company-operated internal model endpoint.
- ChatGPT may use only the organization-approved ChatGPT Enterprise workspace.
- Do not invoke Gemini, consumer Claude, public Codex/API accounts, or another
  external model for review, evaluation, fallback, or comparison.

## Data egress

- Treat source code, diffs, prompts, logs, screenshots, internal URLs, file
  paths, project names, environment variables, tokens, credentials, user data,
  schemas, dependency inventories, and generated artifacts as confidential.
- Never send confidential data to a public MCP server, connector, telemetry
  backend, webhook, feedback endpoint, public repository, public CI service,
  deployment service, package audit service, or memory service.
- Public documentation and packages may be fetched read-only only through an
  organization-approved proxy, mirror, or browser path. Requests must not
  contain confidential identifiers, code, logs, internal URLs, or query data.
- MCP, databases, browsers, Git remotes, CI, observability collectors, and
  deployment targets must be localhost or explicitly approved internal
  endpoints. Merely being reachable does not make an endpoint approved.
- Do not use authenticated personal browser profiles. Browser inspection must
  use an isolated test profile and only localhost or an approved internal test
  application.
- Do not upload feedback, transcripts, artifacts, screenshots, or reports.

## Actions and conflicts

- External-model review paths are disabled. Use a fresh-context reviewer on the
  approved internal Claude model, or review sequentially in the same session.
- Public `git push`, public pull requests, external deployment, external CI,
  external telemetry, and remote memory capture are prohibited. Internal
  equivalents still require the user's explicit request when they change state.
- A read-only or audit request overrides any implementation, commit, deployment,
  or cleanup instruction from another Skill.
- Use at most one primary workflow Skill. Supporting review, security, motion,
  or style Skills may add checks but must not restart or broaden the workflow.
- Existing project conventions and design systems take priority over generic
  style guidance. `apple-design` and `emil-design-eng` are explicit specialist
  lenses, not automatic replacements for `hallmark` or project UI rules.
- If an action's destination or data boundary is unclear, stop without making
  the request and report what approval or internal endpoint is missing.

