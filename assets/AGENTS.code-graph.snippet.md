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
