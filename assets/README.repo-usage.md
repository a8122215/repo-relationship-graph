# Repo Relationship Graph Usage

This plugin installs repository-local templates for an AI-agent code relationship graph.

## Install Preview

From any repository root:

```bash
python ~/plugins/repo-relationship-graph/scripts/install_repo_template.py --dry-run
```

Apply only after reviewing the diff:

```bash
python ~/plugins/repo-relationship-graph/scripts/install_repo_template.py --apply
```

Use `--update --apply` to append missing snippets without creating a new config file.

## Daily Commands

```bash
make code-graph
make code-graph-check
make code-graph-query ARGS="explain <repo-relative-path>"
```

Treat query results as candidates and verify important behavior in source before editing.
