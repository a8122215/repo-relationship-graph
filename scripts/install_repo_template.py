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
    config_asset = ASSETS / f"codegraph.config.{config_template}.toml"
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
        "code-graph",
        "code-graph-check",
        "code-graph-query",
        "code-graph-feedback",
        "code-graph-mcp",
        "code-graph-smoke",
    }
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    if MARKER in existing:
        return []
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
