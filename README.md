# Repo Relationship Graph

Repo Relationship Graph is a Codex-oriented repository relationship graph engine and plugin bundle.
It generates deterministic, machine-readable graph artifacts for AI coding agents, then exposes
bounded query commands and read-only MCP tools so agents do not need to read raw graph JSON.

## Ownership Model

This repository owns only shared code and reusable templates:

- `repo_graph/` engine, parser adapters, graph builder, writers, queries, MCP server, and feedback helpers
- `skills/` Codex skills for graph query and graph maintenance workflows
- `assets/` install templates and example `codegraph.config.*.toml` files
- `scripts/install_repo_template.py` for copying templates into a target repository

Each consuming repository owns its own configuration and generated artifacts:

- `codegraph.config.toml`
- `analysis/code_graph/repo_graph.json`
- `analysis/code_graph/repo_graph.schema.json`
- `analysis/code_graph/repo_graph.summary.md`
- `analysis/code_graph/usage/*.local.jsonl`
- repo-specific Makefile, AGENTS, and onboarding notes

The files under `assets/codegraph.config.*.toml` are templates only. Do not commit real consumer
repo configs, generated graph artifacts, usage logs, CodeQL local results, `.env` files, or private
repo inventory into this plugin repository.

## Install Into A Repository

Clone this plugin checkout somewhere stable, usually:

```bash
git clone git@github.com:a8122215/repo-relationship-graph.git ~/plugins/repo-relationship-graph
```

Preview the repo-local template installation from the target repository root:

```bash
uv run --project ~/plugins/repo-relationship-graph \
  python ~/plugins/repo-relationship-graph/scripts/install_repo_template.py --dry-run
```

Apply only after reviewing the diff:

```bash
uv run --project ~/plugins/repo-relationship-graph \
  python ~/plugins/repo-relationship-graph/scripts/install_repo_template.py --apply
```

For FastAPI + Vue + Playwright repositories, start from the full example:

```bash
uv run --project ~/plugins/repo-relationship-graph \
  python ~/plugins/repo-relationship-graph/scripts/install_repo_template.py --dry-run --config-template full
```

After installation, edit the target repo's `codegraph.config.toml`, then run:

```bash
make code-graph
make code-graph-check
make code-graph-smoke
```

## Direct CLI

From a consuming repository that has `codegraph.config.toml`:

```bash
uv run --project "${CODE_GRAPH_PLUGIN_ROOT:-$HOME/plugins/repo-relationship-graph}" \
  python -m repo_graph.generate --config codegraph.config.toml

uv run --project "${CODE_GRAPH_PLUGIN_ROOT:-$HOME/plugins/repo-relationship-graph}" \
  python -m repo_graph.query_cli --config codegraph.config.toml explain <repo-relative-path>
```

Prefer the installed `make code-graph*` targets when they are available.

## Public Repository Hygiene

This repository is intended to be safe to keep public as source-available tooling. Before pushing,
verify that it contains no consumer repo artifacts:

```bash
git status --short
git ls-files | rg '(^analysis/code_graph/|codegraph.config.toml$|\\.env$|\\.local\\.)'
rg -n '/Users/' --glob '!README.md'
rg -n 'SECRET|TOKEN|API_KEY|PRIVATE KEY' --glob '!README.md'
```

Only templates and generic examples should match `codegraph.config` in this repository.

## Validation

```bash
uv run --project ~/plugins/repo-relationship-graph \
  python -m unittest discover -s ~/plugins/repo-relationship-graph/tests -v

uv run python ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py \
  ~/plugins/repo-relationship-graph
```
