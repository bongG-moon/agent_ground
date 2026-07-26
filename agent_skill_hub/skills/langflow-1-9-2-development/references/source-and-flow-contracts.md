# Source and flow contracts

## Repository shape

Prefer this separation:

```text
langflow_components/<flow-or-domain>/*.py   canonical custom components
tools/build_*.py                            deterministic flow builders
flow_exports/*.json                        generated individual flows
import_ready_flows/*.json                  generated import artifacts
tests/                                     component, builder, and contract tests
docs/                                      connection and import runbooks
```

Keep secrets, generated caches, runtime databases, and local `.env` files out
of exports and source control.

## Canonical ownership

- Edit Python source, prompt source, schemas, and builders first.
- Rebuild JSON and bundles from those sources.
- Verify that serialized component code is byte-for-byte or hash-equivalent to
  its canonical source.
- Do not repair a source defect only inside downloaded JSON.
- Make builders deterministic: stable node identifiers where possible, stable
  endpoint names, reproducible ordering, strict UTF-8 without BOM, and explicit
  version stamps.

## Component contract

Each numbered or reusable custom component must:

- work when copied as one standalone file;
- import public `lfx` APIs directly;
- avoid sibling or repository helper imports;
- expose unique input and output names within the component;
- keep component class, display identity, input/output names, and types stable;
- validate LLM output before it reaches deterministic execution;
- return compact, documented payloads rather than duplicating full state;
- put credentials in Secret inputs or approved Global Variables.

Declare structured terminal behavior in Python when the component contract
requires it. Do not rely on manually inserted JSON flags. Leave structured
terminal outputs unconnected; route the user-facing `Message` through one
message adapter and one Chat Output.

## Flow boundary

Document for every flow:

- entry component and accepted input type;
- terminal message and structured output names;
- payload envelope and canonical owner of each field;
- session and message-storage owner;
- external services and secret bindings;
- child Flow or Tool mapping;
- timeout and failure behavior;
- import-time rebindings;
- representative smoke tests.

Keep business-specific rules in metadata or prompt contracts when they are
expected to change. Keep authorization, allowlists, shape validation,
fail-closed checks, and output normalization in deterministic code.

## Portable IDs and names

Flow database IDs change after import. Leave `flow_id_selected` and similar
environment-owned child IDs empty in repository exports unless an isolated
deployment manifest owns the mapping. After import, refresh and select each
child Flow, save the new ID, and then use ID-only mode when stable.

Name fallback is suitable only for bootstrap. It must:

- search within the current authenticated user's scope;
- reject zero or multiple matches;
- never select a suffixed duplicate silently;
- be replaced by a saved ID before production use.

Use unique Flow names and endpoint names. Remove or quarantine old imported
copies before reimporting a compatible bundle.

## Import set

Import mutually dependent flows, adapters, and builders as one tested
compatibility set. Do not mix exports produced by different Langflow or LFX
versions. After import, rebind internal models, Global Variables, credentials,
and child Flow selections, then save and smoke-test each entry point.
