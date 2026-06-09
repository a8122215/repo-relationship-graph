#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS = PLUGIN_ROOT / "assets"
MARKER = "# repo-relationship-graph"
PLUGIN_ROOT_VAR = "CODE_GRAPH_PLUGIN_ROOT ?= $(HOME)/plugins/repo-relationship-graph"
HOOK_TEMPLATE_VAR = "CODE_GRAPH_HOOK_TEMPLATE := $(CODE_GRAPH_PLUGIN_ROOT)/assets/pre-commit-code-graph"
CLEAN_ARGS_VAR = "CODE_GRAPH_CLEAN_ARGS ?= --dry-run"
PLUGIN_CHECK_TARGET_SNIPPET = """.PHONY: code-graph-plugin-check

code-graph-plugin-check:
\t@test -d "$(CODE_GRAPH_PLUGIN_ROOT)" || (echo "CODE_GRAPH_PLUGIN_ROOT does not exist: $(CODE_GRAPH_PLUGIN_ROOT)" >&2; echo "Install repo-relationship-graph or set CODE_GRAPH_PLUGIN_ROOT." >&2; exit 2)
\t@test -f "$(CODE_GRAPH_PLUGIN_ROOT)/pyproject.toml" || (echo "CODE_GRAPH_PLUGIN_ROOT is not a valid repo-relationship-graph checkout: $(CODE_GRAPH_PLUGIN_ROOT)" >&2; exit 2)
\t@test -d "$(CODE_GRAPH_PLUGIN_ROOT)/repo_graph" || (echo "CODE_GRAPH_PLUGIN_ROOT is missing repo_graph package: $(CODE_GRAPH_PLUGIN_ROOT)" >&2; exit 2)
"""
INSTALL_HOOK_TARGET_SNIPPET = """.PHONY: code-graph-install-hook

code-graph-install-hook: code-graph-plugin-check
\t@hook_path=$$(git rev-parse --git-path hooks/pre-commit); \\
\tif [ -e "$$hook_path" ] || [ -L "$$hook_path" ]; then \\
\t\techo "$$hook_path already exists; refusing to overwrite."; \\
\t\techo "Merge $(CODE_GRAPH_HOOK_TEMPLATE) into the existing hook manually if needed."; \\
\t\texit 1; \\
\tfi; \\
\tmkdir -p "$$(dirname "$$hook_path")"; \\
\tinstall -m 0755 "$(CODE_GRAPH_HOOK_TEMPLATE)" "$$hook_path"; \\
\techo "Installed code graph pre-commit hook at $$hook_path"
"""
CLEAN_USAGE_TARGET_SNIPPET = """.PHONY: code-graph-clean-usage

code-graph-clean-usage: code-graph-plugin-check
\tuv run --project "$(CODE_GRAPH_PLUGIN_ROOT)" python -m repo_graph.usage_feedback --config codegraph.config.toml cleanup $(CODE_GRAPH_CLEAN_ARGS)
"""


@dataclass(frozen=True)
class PlannedChange:
    path: Path
    action: str
    detail: str
    new_text: str | None = None


def main() -> int:
    parser = argparse.ArgumentParser(description="Install repo relationship graph templates without overwriting files.")
    parser.add_argument(
        "--repo",
        "--repo-root",
        dest="repo",
        type=Path,
        default=Path.cwd(),
        help="Target repository root. Defaults to cwd.",
    )
    parser.add_argument("--apply", action="store_true", help="Write planned changes.")
    parser.add_argument("--dry-run", action="store_true", help="Show planned changes without writing.")
    parser.add_argument("--update", action="store_true", help="Add missing snippets to an existing installation.")
    parser.add_argument("--allow-non-git", action="store_true", help="Allow installing into a non-git directory.")
    parser.add_argument(
        "--config-template",
        choices=("minimal", "full"),
        default="minimal",
        help="Config template to install when codegraph.config.toml is absent.",
    )
    args = parser.parse_args()

    if args.apply and args.dry_run:
        parser.error("--apply and --dry-run are mutually exclusive")
    if not args.apply and not args.dry_run:
        parser.error("choose either --dry-run or --apply")
    dry_run = args.dry_run
    repo = args.repo.resolve()
    if not repo.exists() or not repo.is_dir():
        raise SystemExit(f"repository root does not exist: {repo}")
    if not args.allow_non_git and not is_git_worktree(repo):
        raise SystemExit(f"repository root is not a git worktree: {repo}")

    changes = plan_changes(repo, config_template=args.config_template, update=args.update)
    if not changes:
        print("No changes needed.")
        return 0

    for change in changes:
        print(f"{change.action}: {change.path.relative_to(repo)}")
        if change.detail:
            print(change.detail.rstrip())
    if any(change.action == "conflict" for change in changes):
        raise SystemExit("Conflicts found. Resolve them before applying templates.")

    if dry_run:
        print("Dry run only. Re-run with --apply to write changes.")
        return 0

    apply_changes(repo, changes)
    print("Applied changes.")
    return 0


def plan_changes(repo: Path, *, config_template: str, update: bool) -> list[PlannedChange]:
    changes: list[PlannedChange] = []
    config_asset = config_template_asset(config_template)
    config_target = repo / "codegraph.config.toml"
    if config_target.exists() and not update:
        changes.extend(existing_file_check(repo, config_target, config_asset.read_text(encoding="utf-8")))
    elif not config_target.exists() and not update:
        changes.append(create_change(repo, config_target, config_asset.read_text(encoding="utf-8")))

    docs_asset = ASSETS / "docs/operations/onboarding/code-graph.md"
    docs_target = repo / "docs/operations/onboarding/code-graph.md"
    if not docs_target.exists() and not update:
        changes.append(create_change(repo, docs_target, docs_asset.read_text(encoding="utf-8")))

    changes.extend(makefile_change(repo, repo / "Makefile", ASSETS / "Makefile.snippet"))
    changes.extend(gitignore_change(repo, repo / ".gitignore", ASSETS / ".gitignore.snippet"))
    changes.extend(snippet_change(repo, repo / "AGENTS.md", ASSETS / "AGENTS.code-graph.snippet.md", marker="Code Graph Maintenance"))
    readme_asset = ASSETS / "README.repo-usage.md"
    readme_target = repo / "docs/code-graph-plugin-usage.md"
    if readme_asset.exists() and not readme_target.exists() and not update:
        changes.append(create_change(repo, readme_target, readme_asset.read_text(encoding="utf-8")))
    return changes


def config_template_asset(config_template: str) -> Path:
    if config_template == "full":
        return ASSETS / "codegraph.config.full.example.toml"
    return ASSETS / f"codegraph.config.{config_template}.toml"


def create_change(repo: Path, target: Path, content: str) -> PlannedChange:
    return PlannedChange(
        path=target,
        action="create",
        detail=unified_diff("", content, target.relative_to(repo).as_posix()),
        new_text=content,
    )


def existing_file_check(repo: Path, target: Path, expected: str) -> list[PlannedChange]:
    existing = target.read_text(encoding="utf-8")
    if existing == expected:
        return []
    return [
        PlannedChange(
            path=target,
            action="conflict",
            detail=f"{target.relative_to(repo)} already exists; not overwriting.\n",
        )
    ]


def makefile_change(repo: Path, target: Path, asset: Path) -> list[PlannedChange]:
    target_names = {
        "code-graph-plugin-check",
        "code-graph",
        "code-graph-check",
        "code-graph-query",
        "code-graph-feedback",
        "code-graph-clean-usage",
        "code-graph-mcp",
        "code-graph-smoke",
        "code-graph-install-hook",
    }
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    if MARKER in existing:
        return makefile_update_change(repo, target, existing)
    conflicting = sorted(name for name in target_names if re.search(rf"^{re.escape(name)}\s*:", existing, re.MULTILINE))
    if conflicting:
        return [
            PlannedChange(
                path=target,
                action="conflict",
                detail=f"Makefile already defines code graph targets: {', '.join(conflicting)}\n",
            )
        ]
    return snippet_change(repo, target, asset, marker="code-graph-smoke")


def makefile_update_change(repo: Path, target: Path, existing: str) -> list[PlannedChange]:
    additions: list[str] = []
    if "CODE_GRAPH_PLUGIN_ROOT" not in existing:
        additions.append(PLUGIN_ROOT_VAR)
    if "CODE_GRAPH_HOOK_TEMPLATE" not in existing:
        additions.append(HOOK_TEMPLATE_VAR)
    if "code-graph-plugin-check:" not in existing:
        additions.append(PLUGIN_CHECK_TARGET_SNIPPET.rstrip())
    if "CODE_GRAPH_CLEAN_ARGS" not in existing:
        additions.append(CLEAN_ARGS_VAR)
    if "code-graph-clean-usage:" not in existing:
        additions.append(CLEAN_USAGE_TARGET_SNIPPET.rstrip())
    if "code-graph-install-hook:" not in existing:
        additions.append(INSTALL_HOOK_TARGET_SNIPPET.rstrip())
    if not additions:
        return []
    new_text = existing.rstrip() + "\n\n" + "\n\n".join(additions) + "\n"
    return [
        PlannedChange(
            path=target,
            action="append",
            detail=unified_diff(existing, new_text, target.relative_to(repo).as_posix()),
            new_text=new_text,
        )
    ]


def snippet_change(repo: Path, target: Path, asset: Path, *, marker: str) -> list[PlannedChange]:
    snippet = asset.read_text(encoding="utf-8").rstrip() + "\n"
    if target.exists():
        existing = target.read_text(encoding="utf-8")
        if marker in existing:
            return []
        new_text = existing.rstrip() + "\n\n" + MARKER + "\n" + snippet
        return [
            PlannedChange(
                path=target,
                action="append",
                detail=unified_diff(existing, new_text, target.relative_to(repo).as_posix()),
                new_text=new_text,
            )
        ]
    new_text = MARKER + "\n" + snippet
    return [
        PlannedChange(
            path=target,
            action="create",
            detail=unified_diff("", new_text, target.relative_to(repo).as_posix()),
            new_text=new_text,
        )
    ]


def gitignore_change(repo: Path, target: Path, asset: Path) -> list[PlannedChange]:
    required_lines = [line for line in asset.read_text(encoding="utf-8").splitlines() if line.strip()]
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    existing_lines = set(existing.splitlines())
    missing_lines = [line for line in required_lines if line not in existing_lines]
    if not missing_lines:
        return []
    addition = MARKER + "\n" + "\n".join(missing_lines) + "\n"
    new_text = existing.rstrip() + "\n\n" + addition if existing else addition
    return [
        PlannedChange(
            path=target,
            action="append" if target.exists() else "create",
            detail=unified_diff(existing, new_text, target.relative_to(repo).as_posix()),
            new_text=new_text,
        )
    ]


def apply_changes(repo: Path, changes: list[PlannedChange]) -> None:
    conflicts = [change for change in changes if change.action == "conflict"]
    if conflicts:
        labels = ", ".join(str(change.path.relative_to(repo)) for change in conflicts)
        raise SystemExit(f"refusing to apply with conflicts: {labels}")
    for change in changes:
        if change.new_text is None:
            continue
        change.path.parent.mkdir(parents=True, exist_ok=True)
        change.path.write_text(change.new_text, encoding="utf-8")


def unified_diff(old: str, new: str, label: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{label}",
            tofile=f"b/{label}",
        )
    )


def is_git_worktree(repo: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


if __name__ == "__main__":
    raise SystemExit(main())
