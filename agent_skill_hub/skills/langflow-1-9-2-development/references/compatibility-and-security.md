# Compatibility and security contract

## Version baseline

Use this fixed compatibility set:

```text
langflow==1.9.2
langflow-base==0.9.2
lfx==0.4.2
Python >=3.10,<3.14
```

Pin all three packages. The Langflow 1.9.2 package declares a lower bound for
`langflow-base`, so installing only `langflow==1.9.2` can resolve a different
base or LFX version and change serialized templates.

Create a separate exact-version environment for Langflow parsing and import
checks. If the application repository needs packages that are not present
there, run full repository tests in its project environment and Langflow
compatibility checks in the exact-version environment. Report both.

Stamp every generated flow's `last_tested_version` and every serialized node's
`lf_version` as `1.9.2`. A newer Desktop installation must use the 1.9.2
component index supplied by the exact-version environment rather than its
current templates.

## Security status

Treat 1.9.2 as a compatibility target, not a blanket production-security
approval. The official 1.9.3 release is a critical security release and
recommends immediate upgrade. If 1.9.2 remains mandatory:

- keep it on an approved internal, authenticated, network-segmented service;
- do not expose Flow build, upload, Playground, webhook, MCP, or run endpoints
  to the public internet;
- accept only reviewed flow JSON and trusted custom component code;
- do not allow LLM-generated code to execute without a constrained executor;
- record the version exception and a tested upgrade path to the approved patch
  release.

Custom components execute Python in the Langflow process. Review imports, file
access, subprocess use, dynamic evaluation, network calls, and environment
reads. Do not treat the component editor as a sandbox.

## Enterprise data boundary

- Use only company-operated model, database, storage, tracing, and API
  endpoints.
- Set `DO_NOT_TRACK=True`; disable or internally route all optional telemetry
  and tracing.
- Do not enable public MCP servers, webhook callbacks, public package installs,
  remote memory, or consumer-model review.
- Keep secrets in approved Credential Global Variables or server-side secret
  stores. Export variable names, never literal secret values.
- Export with API keys disabled. Audit every JSON and ZIP before distribution.
- Fetch public reference material only through the approved read-only ingress
  path and never send confidential project data outward.

## Import gate

Before running an imported bundle, verify:

1. exact runtime versions;
2. authenticated user and API-key policy;
3. no duplicate Flow names or suffixed copies;
4. all provider and database bindings point to internal services;
5. all literal credentials and unapproved external URLs are absent;
6. environment-specific Flow IDs are rebound;
7. imported custom code matches the reviewed source hash.
