from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK_SOURCE = REPO_ROOT / "assets/pre-commit-code-graph"


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def run_hook(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(repo / "pre-commit-code-graph")],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )


def create_repo(tmp_path: Path, make_target: str) -> Path:
    repo = tmp_path
    run_git(repo, "init")
    run_git(repo, "config", "user.email", "test@example.com")
    run_git(repo, "config", "user.name", "Test User")

    shutil.copy(HOOK_SOURCE, repo / "pre-commit-code-graph")
    os.chmod(repo / "pre-commit-code-graph", 0o755)
    (repo / "Makefile").write_text(make_target, encoding="utf-8")
    (repo / "README.md").write_text("hook fixture\n", encoding="utf-8")
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-m", "initial hook test repo")
    return repo


def test_pre_commit_hook_delegates_to_make_code_graph_check(tmp_path: Path) -> None:
    repo = create_repo(tmp_path, "code-graph-check:\n\t@echo checked >> hook.log\n")

    result = run_hook(repo)

    assert result.returncode == 0
    assert "Running make code-graph-check..." in result.stdout
    assert (repo / "hook.log").read_text(encoding="utf-8") == "checked\n"


def test_pre_commit_hook_propagates_make_failure(tmp_path: Path) -> None:
    repo = create_repo(tmp_path, "code-graph-check:\n\t@echo stale graph\n\t@exit 7\n")

    result = run_hook(repo)

    assert result.returncode == 2
    assert "stale graph" in result.stdout
