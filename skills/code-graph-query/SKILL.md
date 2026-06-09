---
name: code-graph-query
description: "Use before broad source search when a repository has code graph artifacts: impact surface, related tests, endpoints, frontend routes, E2E reachability, and direct graph neighbors. Prevents wasting context on raw graph JSON."
---

# Code Graph Query

Use the generated graph as a bounded index, not as source text. Query the graph first, then open only the returned source files needed to verify behavior.

## Rules

- Do not read `analysis/code_graph/repo_graph.json` directly unless debugging the graph generator, schema, writer, freshness check, or raw artifact contents.
- Prefer MCP graph tools when available. Otherwise use `make code-graph-query`.
- Treat graph results as candidates. Verify important behavior in source or tests before editing.
- If results look stale, run `make code-graph` and `make code-graph-check`.
- Keep graph output small. Use text output for exploration and `--format json` only when structured fields are needed.

## Query Commands

```bash
make code-graph-query ARGS="impacted <repo-relative-path>"
make code-graph-query ARGS="tests-for <repo-relative-path>"
make code-graph-query ARGS="endpoints-for-router <router-or-main-path>"
make code-graph-query ARGS="routes-for-view <vue-view-path>"
make code-graph-query ARGS="api-callers-for-endpoint '<METHOD> <api-path>'"
make code-graph-query ARGS="e2e-for-route <route-path>"
make code-graph-query ARGS="e2e-for-view <vue-view-path>"
make code-graph-query ARGS="explain <repo-relative-path-or-node-id>"
```

Equivalent direct CLI:

```bash
uv run --project "${CODE_GRAPH_PLUGIN_ROOT:-$HOME/plugins/repo-relationship-graph}" python -m repo_graph.query_cli --config codegraph.config.toml impacted <repo-relative-path>
uv run --project "${CODE_GRAPH_PLUGIN_ROOT:-$HOME/plugins/repo-relationship-graph}" python -m repo_graph.query_cli --config codegraph.config.toml --format json explain <repo-relative-path-or-node-id>
```

## Workflow

1. Identify the seed file or node from the user request.
2. Run the most specific graph query:
   - backend router/API work: `endpoints-for-router`, then `tests-for` and `impacted`
   - backend endpoint caller work: `api-callers-for-endpoint`, then open returned frontend files
   - frontend view/routing work: `routes-for-view`, then `e2e-for-view`, `tests-for`, and `impacted`
   - E2E route work: `e2e-for-route`, then open returned specs
   - unknown relation work: `explain`, then `impacted`
3. Open only the returned source/test files that are relevant to the task.
4. Use `rg` only for details the graph does not model, such as function-local behavior, UI text, highly dynamic API calls, or semantic relationships.
5. After implementation changes, run the repository's required graph maintenance commands:

```bash
make code-graph
make code-graph-check
```

## Feedback Logging

When graph usefulness or a graph gap is relevant to the task, record compact local feedback. Do not include source code, raw graph JSON, secrets, or long model output.

Opt-in query usage logging:

```bash
CODE_GRAPH_USAGE_LOG=1 make code-graph-query ARGS="<query>"
```

Task-level feedback:

```bash
make code-graph-feedback ARGS="--task '<short task>' --query '<tool>: <input>' --opened <path> --usefulness high --note '<short note>'"
```

Missed or noisy relation:

```bash
make code-graph-feedback ARGS="missed --relation calls_api_endpoint --reason template_with_buildQuery --expected-source <path> --expected-target <node-id>"
```

Promote repeated or important misses into `tests/code_graph/fixtures/query_eval_cases.jsonl` so future graph changes keep the behavior fixed.

## Subagent Prompt Rule

When delegating exploration to subagents, include this skill and say:

```text
Use code-graph-query first. Do not read analysis/code_graph/repo_graph.json directly.
Report the graph commands you ran, then the source files you opened to verify.
Do not modify files.
```

Ask subagents to return concise evidence:

```text
- Graph queries run:
- Source files verified:
- Impact candidates:
- Related tests:
- Unknowns / graph gaps:
```

## Known Gaps

Current graph results do not prove runtime coverage. Verify these with source search when needed:

- Dynamic frontend API strings such as variables, concatenation, base URL template expressions, or leading dynamic path segments. Path-segment template literals can be matched to parameterized backend paths, but remain static candidates.
- Router tests that indirectly cover a view.
- Playwright navigation caused by clicks, router push, or non-static URL assertions.
- Function-level call graph unless explicit candidate data such as CodeQL results was generated.

Static `calls_api_endpoint` and `e2e_reaches_route` results are candidates. They show literal code relationships, not runtime proof or coverage proof.
