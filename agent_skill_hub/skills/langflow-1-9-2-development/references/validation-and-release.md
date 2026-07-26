# Validation and release

## 1. Static and unit checks

Run repository-native formatting, compilation, and tests. Add targeted tests
for every changed contract:

- component loads as a standalone file;
- direct `lfx` imports work;
- input and output names do not collide;
- payload normalization is deterministic;
- builders reproduce expected nodes, edges, versions, and settings;
- secret and environment-specific fields remain empty in exports.

Do not replace project-native commands with these examples; adapt them:

```powershell
python -m compileall langflow_components tests tools
python -m pytest -q
```

## 2. Representative behavior

Maintain positive and negative request sets that exercise real routing and
payload contracts. Include:

- direct requests for each child Flow;
- ambiguous requests and no-Tool behavior;
- malformed model output;
- downstream errors and timeouts;
- same-session follow-ups;
- different-session isolation;
- repeated import or duplicate-name failure;
- empty child ID and stale child ID behavior.

Record exact passed/total counts. A single happy-path Playground run is not a
release gate.

## 3. Source/export synchronization

Regenerate all impacted flows and import bundles. Check:

- every serialized custom-code node maps to a canonical source file;
- source and embedded code hashes match;
- node and edge counts are deterministic;
- all edge endpoints and handles resolve;
- `last_tested_version` and all node `lf_version` values are `1.9.2`;
- Flow IDs, credentials, provider settings, and internal URLs follow the
  portability policy;
- JSON and ZIP contents are strict UTF-8 and parse successfully.

Run:

```powershell
python scripts/audit_flow_export.py <export-path> --strict
```

## 4. Exact LFX parse

Use a separate environment containing:

```text
langflow==1.9.2
langflow-base==0.9.2
lfx==0.4.2
```

For every Python component, call the exact runtime's
`lfx.custom.eval.eval_custom_component_code`, instantiate the returned class,
and build its template. Parse every serialized node template, not just one
component per Flow. Report parsed/total counts and failures by source path and
node ID.

## 5. Isolated import

Import the generated JSON or project ZIP into a disposable, authenticated 1.9.2
instance. Confirm:

- expected Flow count;
- no suffixed duplicate names;
- model, database, secret, and child Flow rebindings;
- current-user visibility for parent and child flows;
- one smoke run per independent subflow;
- one run through each Router or orchestration entry point.

Do not import into production as the first runtime test.

## 6. Runtime E2E

Call the real Run API with `input_value`, `input_type`, `output_type`, and an
explicit `session_id`. Repeat a follow-up with the same ID and an unrelated
request with a new ID. Inspect the runtime trace using the fields in
`runtime-contracts.md`.

For Agent Tools, verify cold and warm graph-cache runs separately. Confirm
exactly one selected Tool call when that is the contract, a non-empty final
output, and no duplicated message storage.

## 7. Handoff

Report:

- exact runtime and Python versions;
- changed canonical sources and contracts;
- regenerated exports and bundles;
- test, representative-case, source-sync, node-parse, import, and E2E counts;
- environment-owned settings that remain intentionally blank;
- security controls and accepted version exceptions;
- unresolved failures or untested paths.

Do not claim production readiness when only static validation or partial node
builds passed.
