# Source and derivation

- Official repository: <https://github.com/langflow-ai/langflow>
- Reviewed release: `v1.9.2`
- Reviewed commit: `ea3eae8b9e011ff85d7f92f00cf07916dccf755e`
- License: MIT; the reviewed license text is preserved in `SOURCE_LICENSE`.
- Official documentation: <https://docs.langflow.org/>
- Imported on: 2026-07-26

## Internal evidence used

The portable workflow was synthesized from the development and validation
patterns demonstrated in Codex task
`019f9d86-73a0-7c63-928d-815f57c27a55` and these local project artifacts:

- `docs/LANGFLOW_IMPLEMENTATION_GUIDE.md`
- `docs/LANGFLOW_NODE_CONNECTION_GUIDE.md`
- `docs/LANGFLOW_1_9_2_MIGRATION.md`
- `docs/ENVIRONMENT_SETUP.md`
- `docs/ROUTER_FIX_AND_AGENT_TOOL_IMPLEMENTATION_REPORT_20260711.md`
- `langflow_components/route_flow_v2/CONNECTION_GUIDE.md`
- `langflow_components/route_flow_v2/01_cached_named_run_flow_tool.py`
- `langflow_components/gaia_io/01_gaia_output.py`
- `tools/build_import_ready_bundle.py`
- `tools/build_v5_auxiliary_flows.py`
- `tests/test_langflow_components.py`
- `import_ready_flows/README_IMPORT.md`

The paths identify evidence only. Manufacturing metadata, endpoints,
credentials, customer data, and company-specific payloads are not copied into
this Skill.

## Enterprise derivation

This is an original internal workflow, not a verbatim copy of an upstream
Skill. It generalizes version pinning, canonical-source ownership,
deterministic builders, import portability, Router and Tool contracts, session
propagation, final-output recovery, and layered runtime validation.

The package intentionally excludes:

- public deployment, public MCP, webhook callbacks, or consumer model access;
- telemetry, transcript upload, and external tracing;
- literal credentials, internal endpoints, and pre-bound environment Flow IDs;
- runtime package installation and unreviewed external component bundles;
- business-specific metadata, prompts, test data, and database contracts.

Official documentation is used as public read-only reference material. The
exact 1.9.2 package is treated as a compatibility baseline. Because the
official 1.9.3 release recommends an immediate security upgrade, production use
of 1.9.2 requires an internal exception and compensating controls.
