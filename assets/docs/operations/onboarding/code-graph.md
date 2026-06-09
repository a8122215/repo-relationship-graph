# Code Graph Onboarding

This repository uses `repo-relationship-graph` generated code relationship artifacts for AI-agent exploration.

## Ownership

The plugin owns the shared engine and templates. This repository owns the actual runtime config and
generated artifacts:

- `codegraph.config.toml`
- `analysis/code_graph/repo_graph.json`
- `analysis/code_graph/repo_graph.schema.json`
- `analysis/code_graph/repo_graph.summary.md`
- `analysis/code_graph/usage/*.local.jsonl`
- Makefile wrappers
- AGENTS/code graph operating notes

`assets/codegraph.config.*.toml` in the plugin checkout are templates only. After installation, the
repo-local `codegraph.config.toml` is the source of truth. Do not copy generated graph artifacts or
local usage logs back into the plugin repository.

Do not read raw `analysis/code_graph/repo_graph.json` during normal exploration. Use `make code-graph-query` or the read-only MCP tools.

## First Setup

1. Confirm the plugin checkout exists.

```bash
test -d "${CODE_GRAPH_PLUGIN_ROOT:-$HOME/plugins/repo-relationship-graph}"
```

2. Confirm `codegraph.config.toml` matches the repository layout.
3. Confirm the Makefile has `code-graph`, `code-graph-check`, `code-graph-query`, `code-graph-feedback`, `code-graph-clean-usage`, `code-graph-mcp`, and `code-graph-smoke`.
4. Generate and verify artifacts.

```bash
make code-graph
make code-graph-check
make code-graph-smoke
```

5. Run at least one representative query.

```bash
make code-graph-query ARGS="explain <repo-relative-path>"
```

## New Repo Install

Preview before writing:

```bash
uv run --project ~/plugins/repo-relationship-graph \
  python ~/plugins/repo-relationship-graph/scripts/install_repo_template.py --dry-run
```

Apply after reviewing the diff:

```bash
uv run --project ~/plugins/repo-relationship-graph \
  python ~/plugins/repo-relationship-graph/scripts/install_repo_template.py --apply
```

For a FastAPI + Vue + Playwright repository, install the full config example and then trim it to the actual layout:

```bash
uv run --project ~/plugins/repo-relationship-graph \
  python ~/plugins/repo-relationship-graph/scripts/install_repo_template.py --apply --config-template full
```

## Existing Repo Update

If the repo already has the `# repo-relationship-graph` marker, update missing snippets with:

```bash
uv run --project ~/plugins/repo-relationship-graph \
  python ~/plugins/repo-relationship-graph/scripts/install_repo_template.py --update --dry-run
uv run --project ~/plugins/repo-relationship-graph \
  python ~/plugins/repo-relationship-graph/scripts/install_repo_template.py --update --apply
```

The installer does not overwrite an existing `codegraph.config.toml`. Review config changes manually.

## Config Checklist

Required for most repos:

- `schema_version = 1`
- `[project]`
- `[plugin]`
- `[outputs]`
- `[usage]`
- `[discovery]`
- `[plugins.python_ast]`

Optional, enable only when the repo uses them:

- `[plugins.fastapi]`
- `[plugins.js_vue]`
- `[plugins.vue_router]`
- `[plugins.frontend_api_calls]`
- `[plugins.playwright]`
- `[plugins.manifests]`
- `[plugins.codeql_candidates]`

Python-only repos should disable JS/Vue-related plugins or set `plugins.js_vue.missing_runtime = "skip"` if Node dependencies are intentionally absent.

FastAPI + Vue + Playwright repos should configure:

- `plugins.python_ast.internal_roots`
- `plugins.js_vue.client_package_root`
- `plugins.js_vue.aliases`
- `plugins.frontend_api_calls.api_base`
- `plugins.playwright.test_roots`
- `plugins.manifests.package_files`

Use the minimal config for Python-only repos or repos where JS/Vue should be inventory-only. Use the full config when the repo has frontend source, Vue Router, Playwright, or frontend API call extraction.

## Known Limitations

- Static Vue route component identifiers may not map to view files.
- API wrappers and aliases may require missed-relation feedback before extraction improves.
- E2E reachability is a static candidate, not a runtime coverage proof.

## Commit Policy

Usually commit:

- `codegraph.config.toml`
- `analysis/code_graph/repo_graph.json`
- `analysis/code_graph/repo_graph.schema.json`
- `analysis/code_graph/repo_graph.summary.md`
- Makefile / AGENTS / docs snippets

Always ignore:

- `analysis/code_graph/usage/*.local.jsonl`
- `analysis/code_graph/usage/*.local.md`
- `analysis/code_graph/usage/*.local.json`
- local CodeQL result files
- local graph comparison artifacts

For public or broadly shared repositories, decide whether the generated endpoint/file inventory is acceptable before committing graph artifacts.

## Daily Use

Use graph queries before broad source search:

```bash
make code-graph-query ARGS="explain <repo-relative-path>"
make code-graph-query ARGS="impacted <repo-relative-path>"
make code-graph-query ARGS="tests-for <repo-relative-path>"
```

Treat graph results as static candidates and verify important behavior in source.

Record useful or missing relationships:

```bash
make code-graph-feedback ARGS="--task '<task>' --query 'explain: <repo-relative-path>' --usefulness medium --outcome used"
make code-graph-feedback ARGS="missed --relation calls_api_endpoint --reason wrapper_function --expected-source <path> --expected-target <node>"
```

## Maintenance

After implementation, documentation, workflow, manifest, or module-structure changes:

```bash
make code-graph
make code-graph-check
```

Include changed generated artifacts in the same commit as the source change.

## Local Usage Log Cleanup

Local usage and missed-relation logs are gitignored. Preview cleanup before deleting old events:

```bash
make code-graph-clean-usage
make code-graph-clean-usage CODE_GRAPH_CLEAN_ARGS="--dry-run --retention-days 30"
```

Apply cleanup explicitly:

```bash
make code-graph-clean-usage CODE_GRAPH_CLEAN_ARGS=--apply
```

`CODE_GRAPH_USAGE_DIR` affects cleanup as well as logging. Confirm the dry-run `usageDir` before using `--apply`.
If all valid records in a usage file expire, `--apply` may remove that local `.jsonl` file.

## Plugin Update

1. Update the plugin checkout.
2. Run plugin validation and tests.

```bash
uv run --project ~/plugins/repo-relationship-graph python -m unittest discover -s ~/plugins/repo-relationship-graph/tests -v
uv run python ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py ~/plugins/repo-relationship-graph
```

3. Run template update dry-run in this repo.
4. Apply only required snippets.
5. Regenerate and check graph artifacts.

```bash
uv run --project ~/plugins/repo-relationship-graph \
  python ~/plugins/repo-relationship-graph/scripts/install_repo_template.py --update --dry-run
make code-graph
make code-graph-check
make code-graph-smoke
```

## Rollback

Rollback repo-side changes by reverting `codegraph.config.toml`, Makefile/AGENTS/docs snippets, and generated artifacts together.

Rollback plugin-side changes by returning the plugin checkout to the previous commit, then rerunning:

```bash
make code-graph-check
make code-graph-smoke
```

If rollback is triggered by stale or misleading graph results, record a missed relation or task feedback before discarding local logs.

## Troubleshooting

| Symptom | Check |
|---|---|
| `CODE_GRAPH_PLUGIN_ROOT does not exist` | Install/update the plugin or set `CODE_GRAPH_PLUGIN_ROOT`. |
| `code graph config was not found` | Run from repo root or pass `--config` / `CODE_GRAPH_CONFIG`. |
| Query returns no results | Run `make code-graph` and `make code-graph-check`, then retry. |
| JS/Vue parser fails | Confirm `client_package_root`, Node dependencies, and `missing_runtime`. |
| Cleanup targets wrong directory | Inspect dry-run `usageDir` and `CODE_GRAPH_USAGE_DIR`. |
