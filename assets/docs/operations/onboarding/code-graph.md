# Code Graph Onboarding

This repository uses generated code relationship graph artifacts for AI-agent exploration.

## First Setup

1. Confirm `codegraph.config.toml` matches the repository layout.
2. Confirm the Makefile has the `code-graph`, `code-graph-check`, `code-graph-query`, `code-graph-feedback`, `code-graph-mcp`, and `code-graph-smoke` targets.
3. Generate artifacts:

```bash
make code-graph
make code-graph-check
```

## Daily Use

Use graph queries before broad source search:

```bash
make code-graph-query ARGS="explain <repo-relative-path>"
make code-graph-query ARGS="impacted <repo-relative-path>"
make code-graph-query ARGS="tests-for <repo-relative-path>"
```

Treat graph results as candidates and verify important behavior in source.

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
```

Apply cleanup explicitly:

```bash
make code-graph-clean-usage CODE_GRAPH_CLEAN_ARGS=--apply
```
