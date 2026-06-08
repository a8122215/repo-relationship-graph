---
name: code-graph-maintenance
description: "Maintain repository code graph artifacts after implementation, docs, workflow, manifest, or module-structure changes. Use to regenerate/check graph outputs and record graph usefulness feedback without reading raw graph JSON."
---

# Code Graph Maintenance

Use this skill when a repository has `analysis/code_graph/` artifacts or a `codegraph.config.toml`.

## Required Maintenance

After changing implementation files, module structure, tests, manifests, docs, workflows, or Makefile targets, run:

```bash
make code-graph
make code-graph-check
```

If generated files under `analysis/code_graph/` change, include them in the same commit as the source change.

## Safe Use

- Do not manually edit `analysis/code_graph/repo_graph.json`, `repo_graph.schema.json`, or `repo_graph.summary.md`.
- Do not read raw `repo_graph.json` for normal exploration. Use `code-graph-query` or MCP tools.
- For documentation-only or graph-only changes, `make code-graph-check` plus relevant focused tests is usually enough.
- For runtime frontend/backend changes, run the appropriate project tests in addition to graph commands.

## Freshness Troubleshooting

If `make code-graph-check` reports stale artifacts:

```bash
make code-graph
make code-graph-check
git status --short
```

If new files were added and are not yet tracked, stage them before relying on default graph generation, or run:

```bash
uv run python tools/code_graph/generate.py --check --include-untracked
```

## Local Feedback

Record compact, non-sensitive feedback when graph use affected the task:

```bash
make code-graph-feedback ARGS="--task '<short task>' --query '<query>' --opened <path> --usefulness high --note '<short note>'"
make code-graph-feedback ARGS="missed --relation <relation> --reason <short_reason> --expected-source <path> --expected-target <node-id>"
```

Keep feedback local and small. Do not include secrets, raw graph JSON, source code, or long model output.
