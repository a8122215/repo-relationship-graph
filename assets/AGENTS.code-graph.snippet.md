## Code Graph Maintenance

This repository can maintain AI-agent code graph artifacts under `analysis/code_graph/`.

After Codex changes implementation files, module structure, tests, manifests, docs, workflows, or Makefile targets, run:

```bash
make code-graph
make code-graph-check
```

If `make code-graph` changes files under `analysis/code_graph/`, include those generated artifacts in the same commit as the source change. Do not edit generated graph artifacts manually.

For normal codebase exploration, query the graph first and then open only the returned source files:

```bash
make code-graph-query ARGS="impacted <repo-relative-path>"
make code-graph-query ARGS="tests-for <repo-relative-path>"
make code-graph-query ARGS="explain <repo-relative-path-or-node-id>"
```

Do not read `analysis/code_graph/repo_graph.json` directly unless debugging the graph generator, schema, writer, or freshness checks.

Local usage logs under `analysis/code_graph/usage/` are development-only artifacts and should remain uncommitted. Use `make code-graph-clean-usage` to preview retention cleanup, and only use `make code-graph-clean-usage CODE_GRAPH_CLEAN_ARGS=--apply` when old local events should be removed.

For new or updated installations, preview template changes first:

```bash
python ~/plugins/repo-relationship-graph/scripts/install_repo_template.py --update --dry-run
```

After plugin or template updates, run `make code-graph`, `make code-graph-check`, and `make code-graph-smoke`. Commit repo-owned config, Makefile/AGENTS/docs snippets, and generated graph artifacts together. Do not commit local usage logs, local graph comparison artifacts, or local CodeQL result files.

If graph output becomes stale, misleading, or blocks normal development, first run `make code-graph` and `make code-graph-check`. If the issue remains, record task feedback or a missed relation, then rollback repo-side graph template/config changes and generated artifacts together.
