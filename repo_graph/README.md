# Repo Graph Engine

`repo_graph` generates deterministic, machine-readable code relationship artifacts for AI agents.
The engine lives in this plugin repository, but each consuming repository owns its own
`codegraph.config.toml` and generated `analysis/code_graph/` artifacts.

The CLI is intentionally thin:

```bash
uv run --project "${CODE_GRAPH_PLUGIN_ROOT:-$HOME/plugins/repo-relationship-graph}" \
  python -m repo_graph.generate --config codegraph.config.toml
uv run --project "${CODE_GRAPH_PLUGIN_ROOT:-$HOME/plugins/repo-relationship-graph}" \
  python -m repo_graph.generate --config codegraph.config.toml --check
uv run --project "${CODE_GRAPH_PLUGIN_ROOT:-$HOME/plugins/repo-relationship-graph}" \
  python -m repo_graph.generate --config codegraph.config.toml \
  --codeql-results analysis/code_graph/codeql-candidates.local.json \
  --output analysis/code_graph/repo_graph.local.json \
  --summary analysis/code_graph/repo_graph.local.summary.md
```

For agent-facing exploration, do not paste or summarize raw `repo_graph.json` into the model context. Use the bounded query CLI or MCP tools so only the relevant graph slice is returned:

```bash
uv run --project "${CODE_GRAPH_PLUGIN_ROOT:-$HOME/plugins/repo-relationship-graph}" \
  python -m repo_graph.query_cli --config codegraph.config.toml impacted server/routers/example.py
uv run --project "${CODE_GRAPH_PLUGIN_ROOT:-$HOME/plugins/repo-relationship-graph}" \
  python -m repo_graph.query_cli --config codegraph.config.toml tests-for server/routers/example.py
uv run --project "${CODE_GRAPH_PLUGIN_ROOT:-$HOME/plugins/repo-relationship-graph}" \
  python -m repo_graph.query_cli --config codegraph.config.toml endpoints-for-router server/routers/example.py
uv run --project "${CODE_GRAPH_PLUGIN_ROOT:-$HOME/plugins/repo-relationship-graph}" \
  python -m repo_graph.query_cli --config codegraph.config.toml routes-for-view client/src/views/ExampleView.vue
uv run --project "${CODE_GRAPH_PLUGIN_ROOT:-$HOME/plugins/repo-relationship-graph}" \
  python -m repo_graph.query_cli --config codegraph.config.toml api-callers-for-endpoint /api/example
uv run --project "${CODE_GRAPH_PLUGIN_ROOT:-$HOME/plugins/repo-relationship-graph}" \
  python -m repo_graph.query_cli --config codegraph.config.toml e2e-for-route /example
uv run --project "${CODE_GRAPH_PLUGIN_ROOT:-$HOME/plugins/repo-relationship-graph}" \
  python -m repo_graph.query_cli --config codegraph.config.toml e2e-for-view client/src/views/ExampleView.vue
uv run --project "${CODE_GRAPH_PLUGIN_ROOT:-$HOME/plugins/repo-relationship-graph}" \
  python -m repo_graph.query_cli --config codegraph.config.toml --format json explain server/routers/example.py
```

The same commands are available through Make when shell quoting is more convenient:

```bash
make code-graph-query ARGS="tests-for server/routers/example.py"
```

Responsibilities are split across discovery, parser, builder, writer, and freshness-check modules. Python parsing uses the standard `ast` module. JS/Vue import parsing and Vue Router route extraction use `@babel/parser` and `@vue/compiler-sfc` through the Node structure extractor.

Generated artifacts are written with per-file atomic replacement: the generator writes each artifact to a temporary file in the target directory, flushes it, and then replaces the final path. This keeps MCP auto-reload from reading a partially written `repo_graph.json` during normal `make code-graph` runs. The three artifacts are not updated as a single transaction; `repo_graph.json` remains the graph source of truth.

Vue Router extraction currently supports `const routes = [...]` and inline `createRouter({ routes: [...] })` arrays in `client/src/router/index.js` or `client/src/router/**/*.js`. It resolves only route components whose `component` property directly exposes a string-literal dynamic import such as `component: () => import("../views/HomeView.vue")`. Static imported component identifiers such as `component: HomeView` are not resolved yet. Route node IDs include the source router file and route path, plus the route name when present.

Vitest relation extraction marks `.spec.js` and `.test.js` files as `test_file` nodes. It also supports the standard `.jsx`, `.ts`, and `.tsx` spec/test suffixes if discovery includes those files later. When those test files statically import an internal frontend source file, the graph emits a high-confidence `tests` edge with `metadata.reason="test_import"`. That edge is not a coverage proof; it only means the test file imports the source file. The extractor does not infer relations from `__tests__` directory names alone, `vi.mock`, test names, or route names.

Phase 8A / 8B extends the same extractor and query flow without adding runtime app impact. Phase 8A adds `calls_api_endpoint` candidate edges from Vue/JS application API call literals such as `fetch('/api/health')`, `request('/typing/sessions')`, `api.post('/users')`, and path-segment template literals such as ``request(`/recording/sessions/${sessionId}/progress`)`` to existing FastAPI endpoint nodes. Template expressions are normalized to path-parameter patterns and only create edges when they match existing FastAPI parameterized paths. Method-aware pattern matches use `matchedBy=method_path_pattern` / `confidence=medium`; method-unknown unique pattern matches use `matchedBy=path_pattern` / `confidence=low`. Frontend test files do not emit API caller edges. Phase 8B adds `e2e_reaches_route` candidate edges from static Playwright `page.goto('/route')` literals and simple static `expect(page).toHaveURL('/route')` or `expect(page).toHaveURL(/\/route$/)` assertions to existing Vue route nodes. Both edges are static candidates only; arbitrary variables, string concatenation, base URL template expressions, leading dynamic path segments, complex URL assertion regexes, click navigation inference, router push inference, and runtime coverage proof are intentionally out of scope.

Agent-facing exploration should use bounded query commands such as:

```bash
make code-graph-query ARGS="api-callers-for-endpoint /api/example"
make code-graph-query ARGS="e2e-for-route /example"
make code-graph-query ARGS="e2e-for-view client/src/views/ExampleView.vue"
```

Query usage logging is opt-in. When `CODE_GRAPH_USAGE_LOG=1` is set, the query CLI and MCP server append compact JSONL records under `analysis/code_graph/usage/` without storing raw graph JSON, source code, or full query results:

```bash
CODE_GRAPH_USAGE_LOG=1 make code-graph-query ARGS="e2e-for-view client/src/views/ExampleView.vue"
CODE_GRAPH_USAGE_LOG=1 make code-graph-mcp
```

Task-level usefulness and missed relation feedback can be recorded manually:

```bash
make code-graph-feedback ARGS="--task 'View change' --query 'e2e-for-view: client/src/views/ExampleView.vue' --usefulness high"
make code-graph-feedback ARGS="missed --relation calls_api_endpoint --reason wrapper_function --expected-source client/src/api/example.js"
```

Phase 4 CodeQL candidate ingestion is opt-in. `--codeql-results` accepts a normalized JSON file with `relations[]` rows for `calls` and `data_flows_to` candidates. When `--codeql-results` is used, the CLI requires explicit local `--output` and `--summary` paths and rejects the committed default artifacts. Local files such as `analysis/code_graph/codeql-candidates.local.json`, `analysis/code_graph/repo_graph.local.json`, and `analysis/code_graph/repo_graph.local.summary.md` are ignored by git. Missing explicit results files are errors; only an unspecified results path is a no-op. Default `make code-graph` and CI do not require CodeQL CLI or a local database. Imported CodeQL edges use `metadata.provider="codeql"` and `metadata.candidate=true`; they are static-analysis candidates, not runtime proof.

Phase 5 adds a read-only MCP stdio query server over the committed graph:

```bash
make code-graph-mcp
uv run --project "${CODE_GRAPH_PLUGIN_ROOT:-$HOME/plugins/repo-relationship-graph}" \
  python -m repo_graph.mcp_server --config codegraph.config.toml
```

The MCP adapter exposes `find_impacted_files`, `find_tests_for`, `find_endpoints_for_router`, `find_routes_for_view`, `find_api_callers_for_endpoint`, `find_e2e_for_route`, `find_e2e_for_view`, and `explain_node`. Query behavior lives in `query.py`; `mcp_server.py` only handles JSON-RPC/MCP transport, and `query_cli.py` is the shell wrapper around the same read-only query service. Results include bounded `total` / `truncated` metadata, and all path inputs are treated as exact node IDs first or repo-relative POSIX paths. The server reads the graph at startup and, since Phase 7A, checks the graph file mtime before tool calls. After structural changes, run `make code-graph`; the next tool call normally sees the regenerated graph without restarting the MCP process. If the regenerated graph is invalid, malformed, or missing, that tool call returns a server-side JSON-RPC error and can recover after a valid graph is written. To recover manually, run `make code-graph` and then `make code-graph-check`. It does not import backend/frontend application modules and has no runtime effect on the app.

Optional local pre-commit hooks should stay repo-local and thin:

```bash
make code-graph-install-hook
```

The hook should delegate to `make code-graph-check` and let the Python entrypoints resolve
`codegraph.config.toml`. It must not regenerate artifacts, stage files, or detect partial staging.
CI remains the authoritative freshness gate. To avoid partial-staging drift, stage related source
changes and `analysis/code_graph/` artifacts together. Watch mode and mandatory hook installation
are intentionally deferred.

Run `cd client && npm ci` before local generation if frontend parser dependencies are missing. `sourceManifest.role` distinguishes parsed `source` files, processed `manifest` files, and `inventory_only` files that are listed but not parsed into graph relations.
