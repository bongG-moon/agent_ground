---
name: langflow-1-9-2-development
description: Build, modify, debug, validate, export, import, or review Langflow 1.9.2 flows and standalone Python custom components. Use for source-to-JSON synchronization, deterministic flow builders, Router or Run Flow tools, Agent tool wiring, session and message-history continuity, structured terminal outputs, import-ready bundles, or 1.9.2 runtime compatibility failures. Do not use for generic Python or LangChain work, Flowise or n8n, UI-only flow operation, or upgrades targeting a different Langflow version.
---

# Langflow 1.9.2 Development

Treat `langflow==1.9.2`, `langflow-base==0.9.2`, and `lfx==0.4.2` as one compatibility contract. Do not silently adopt newer component templates or APIs.

## Work from contracts

1. Read project instructions, package locks, flow builders, canonical component sources, exports, tests, and import notes.
2. Identify the authoritative source. Prefer standalone Python plus deterministic builders; treat exported JSON and ZIP files as generated artifacts.
3. Map the boundary: inputs, outputs, payload envelope, session ownership, message storage, terminal outputs, child flows, and API consumers.
4. Read [compatibility-and-security.md](references/compatibility-and-security.md). Stop if the target is public-facing, imports untrusted code, exports credentials, or requires external telemetry or endpoints.
5. Read [source-and-flow-contracts.md](references/source-and-flow-contracts.md) before changing components, builders, or exports.
6. For Router, Agent, Tool, multi-turn, or structured-output work, also read [runtime-contracts.md](references/runtime-contracts.md).

## Implement narrowly

- Keep each custom component standalone; use direct `lfx` imports and avoid project-relative helpers.
- Preserve component class names, input/output names, data types, and saved-flow compatibility unless an explicit migration is requested.
- Change canonical source first, regenerate exports, and never hand-edit generated JSON to create a lasting fix.
- Keep environment-specific Flow IDs and credentials out of portable exports. Rebind approved internal models, globals, and child Flow selections after import.
- Do not infer success from a rendered Playground card or static JSON alone.

## Validate in layers

Follow [validation-and-release.md](references/validation-and-release.md). At minimum:

1. compile and run targeted repository tests;
2. regenerate and compare source-to-export synchronization;
3. parse every custom component and serialized node in the exact LFX runtime;
4. import into an isolated 1.9.2 instance;
5. run representative single-turn and same-`session_id` multi-turn cases;
6. inspect traces for Tool name, input, call count, child output, final output, and duplicate message storage.

Run `python scripts/audit_flow_export.py <json-or-directory>` before handoff. Use `--strict` for a release gate.

Report the changed contracts, regenerated artifacts, exact runtime versions, validation counts, unresolved environment rebindings, and security exceptions.
