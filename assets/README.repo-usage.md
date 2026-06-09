# Repo Relationship Graph Usage

This plugin installs repository-local templates for an AI-agent code relationship graph.

The plugin repository owns the engine and reusable templates only. The consuming repository owns the
actual `codegraph.config.toml`, generated `analysis/code_graph/` artifacts, Makefile wrappers,
AGENTS notes, and local usage logs.

## Install Preview

From any repository root:

```bash
uv run --project ~/plugins/repo-relationship-graph \
  python ~/plugins/repo-relationship-graph/scripts/install_repo_template.py --dry-run
```

Apply only after reviewing the diff:

```bash
uv run --project ~/plugins/repo-relationship-graph \
  python ~/plugins/repo-relationship-graph/scripts/install_repo_template.py --apply
```

Use `--update --apply` to append missing snippets without creating a new config file.

For FastAPI + Vue + Playwright repos, start from the full example:

```bash
uv run --project ~/plugins/repo-relationship-graph \
  python ~/plugins/repo-relationship-graph/scripts/install_repo_template.py --dry-run --config-template full
```

For Python-only repos, use the default minimal template and disable frontend plugins.

## Daily Commands

```bash
make code-graph
make code-graph-check
make code-graph-query ARGS="explain <repo-relative-path>"
```

Treat query results as candidates and verify important behavior in source before editing.

## What To Commit

Commit repo-owned config, generated graph artifacts, and wrapper/docs snippets unless the repository visibility makes endpoint/file inventory sensitive.

Keep local usage logs, local graph comparison artifacts, and local CodeQL result files ignored.

Do not copy repo-owned config, generated graph artifacts, or local usage logs back into the plugin
repository. `assets/codegraph.config.*.toml` are starter templates, not shared runtime config.

## Local Usage Logs

Usage and missed-relation logs are local development artifacts under `analysis/code_graph/usage/` and should stay gitignored.

Preview retention cleanup:

```bash
make code-graph-clean-usage
```

Apply cleanup only after reviewing the dry-run output:

```bash
make code-graph-clean-usage CODE_GRAPH_CLEAN_ARGS=--apply
```

## Update And Rollback

Update installed snippets with:

```bash
uv run --project ~/plugins/repo-relationship-graph \
  python ~/plugins/repo-relationship-graph/scripts/install_repo_template.py --update --dry-run
```

After plugin updates, run:

```bash
make code-graph
make code-graph-check
make code-graph-smoke
```

Rollback repo-side changes by reverting `codegraph.config.toml`, Makefile/AGENTS/docs snippets, and generated artifacts together. Rollback plugin-side changes by returning the plugin checkout to the previous commit and rerunning graph checks.
