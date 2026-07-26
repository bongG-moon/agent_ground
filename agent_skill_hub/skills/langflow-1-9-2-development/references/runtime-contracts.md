# Runtime contracts

## Choose the routing shape

Use a deterministic API Router when classification is bounded and latency,
endpoint control, and one-child-call behavior matter. Connect Chat Input to the
Router once; connect each route output to only its selected caller. Do not
fan-out Chat Input to every branch.

Use an Agent plus Run Flow tools when natural-language tool selection is
materially useful. Give the Agent the smallest stable tool schema, normally a
business input such as `question`, not import-specific node IDs or internal
prompts. For a router that must select one child, cap it at one Tool iteration.

Use a deterministic plan, validation, and loop when multiple dependent tools
must run. Validate tool names, step count, dependencies, handoff references,
payload size, and stop behavior before executing the first step.

## Session ownership

- Send the same `session_id` in the top-level `/api/v1/run/...` payload for
  every turn that belongs to one conversation.
- Prefer `graph.session_id` or runtime-injected session state over a second
  manually connected session edge.
- Pass the parent session to child flows at the actual Tool output method.
  Do not rely only on `_pre_run_setup()`; a Tool wrapper may call the output
  method through a copied component path.
- Import and run parent and child flows under the same Langflow user or API-key
  identity when lookup and graph caches are user-scoped.

Test the session contract through the real wrapper path, not only by calling a
component method directly.

## Message history and storage

Choose exactly one owner for the current user message and one owner for the
final assistant message. In a parent/child Tool flow, disable child Chat
Input/Output storage during nested execution and let the parent store the final
conversation once.

When Chat Input stores the current question before Agent history is loaded:

1. fetch enough messages for the desired previous turns plus the current one;
2. preserve the input Message ID through adapters;
3. remove history with the same Message ID as the current input;
4. verify the model receives the current question once.

Do not set history to zero merely to hide duplication if multi-turn context is
required.

## Run Flow identity and cache

Flow IDs are environment-specific. Resolve and save the imported ID before
production. Restrict name fallback to exact, unique, current-user matches.

`cache_flow=true` caches graph construction, not database reads, LLM calls,
analysis results, or responses. Invalidate a cached graph when the child
Flow's update identity changes. Never describe graph caching as result caching.

## Tool return and final output

`return_direct=true` can remove an extra Agent rewrite, but verify the final
Chat/API output, not only the Tool event card. In the 1.9.2/LFX 0.4.2 path, a
completed Tool result can exist in Agent events while the Agent response body
is empty. If this occurs, use a deterministic output adapter that extracts the
last completed Tool result without another model call and preserves structured
metadata.

Treat streaming display timing as a topology property. Compare Agent directly
to Chat Output with Agent through an output adapter before blaming the runtime
version.

## Runtime trace

For each representative request capture:

- parent `session_id` and authenticated identity;
- selected Tool or route;
- exact public Tool arguments;
- child Flow ID and resolution mode;
- Tool invocation count;
- child terminal output;
- Agent response and final Chat/API output;
- stored message count and IDs;
- cache cold/warm state;
- warnings and errors.

A successful node build or visible Tool card is not sufficient evidence of a
successful end-to-end response.
