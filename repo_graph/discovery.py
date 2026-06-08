from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from repo_graph.core.model import SourceFile


EXCLUDED_PREFIXES = (
    ".git/",
    ".venv/",
    "__pycache__/",
    ".pytest_cache/",
    "client/node_modules/",
    "client/dist/",
    "uploads/",
    "analysis/code_graph/",
    "training/yolo_model/dataset_output/",
)
EXCLUDED_PARTS = {"/__pycache__/", "/.pytest_cache/"}
INCLUDED_SUFFIXES = (".py", ".js", ".vue", ".toml", ".json", ".md", ".yml", ".yaml")
INCLUDED_FILENAMES = ("Makefile",)


@dataclass(frozen=True)
class FileDiscoveryConfig:
    include_suffixes: tuple[str, ...] = INCLUDED_SUFFIXES
    include_filenames: tuple[str, ...] = INCLUDED_FILENAMES
    exclude_prefixes: tuple[str, ...] = EXCLUDED_PREFIXES


class FileDiscovery:
    def __init__(
        self,
        repo_root: Path,
        tracked_files_provider: Callable[[], Iterable[str]] | None = None,
        untracked_files_provider: Callable[[], Iterable[str]] | None = None,
        config: FileDiscoveryConfig | None = None,
    ) -> None:
        self.repo_root = repo_root
        self._tracked_files_provider = tracked_files_provider
        self._untracked_files_provider = untracked_files_provider
        self.config = config or FileDiscoveryConfig()

    def discover(self, include_untracked: bool = False) -> list[SourceFile]:
        paths = set(self._tracked_paths())
        if include_untracked:
            paths.update(self._untracked_paths())
        source_files = [
            SourceFile(path=path, language=language_for_path(path))
            for path in sorted(paths)
            if should_include_path(path, self.config)
        ]
        return source_files

    def _tracked_paths(self) -> list[str]:
        if self._tracked_files_provider is not None:
            return normalize_paths(self._tracked_files_provider())
        return run_git_ls_files(self.repo_root, ["ls-files", "-z"])

    def _untracked_paths(self) -> list[str]:
        if self._untracked_files_provider is not None:
            return normalize_paths(self._untracked_files_provider())
        return run_git_ls_files(
            self.repo_root,
            ["ls-files", "--others", "--exclude-standard", "-z"],
        )


def run_git_ls_files(repo_root: Path, args: list[str]) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=True,
        capture_output=True,
    )
    raw_paths = completed.stdout.decode("utf-8").split("\0")
    return normalize_paths(path for path in raw_paths if path)


def normalize_paths(paths: Iterable[str]) -> list[str]:
    normalized = []
    for path in paths:
        posix_path = str(path).replace("\\", "/")
        if posix_path.startswith("./"):
            posix_path = posix_path[2:]
        if posix_path:
            normalized.append(posix_path)
    return normalized


def should_include_path(path: str, config: FileDiscoveryConfig | None = None) -> bool:
    config = config or FileDiscoveryConfig()
    posix_path = path.replace("\\", "/")
    if posix_path.startswith("./"):
        posix_path = posix_path[2:]
    if any(path_matches_exclude_prefix(posix_path, prefix) for prefix in config.exclude_prefixes):
        return False
    if any(part in f"/{posix_path}" for part in EXCLUDED_PARTS):
        return False
    return posix_path.endswith(config.include_suffixes) or posix_path in config.include_filenames


def path_matches_exclude_prefix(path: str, prefix: str) -> bool:
    normalized_path = path.replace("\\", "/").removeprefix("./")
    normalized_prefix = prefix.replace("\\", "/").removeprefix("./")
    if not normalized_prefix.endswith("/"):
        normalized_prefix = f"{normalized_prefix}/"
    return normalized_path.startswith(normalized_prefix)


def language_for_path(path: str) -> str:
    if path.endswith(".py"):
        return "python"
    if path.endswith(".vue"):
        return "vue"
    if path.endswith(".js"):
        return "javascript"
    if path.endswith(".toml"):
        return "toml"
    if path.endswith(".json"):
        return "json"
    if path.endswith(".md"):
        return "markdown"
    if path.endswith((".yml", ".yaml")):
        return "yaml"
    if path == "Makefile":
        return "make"
    return "other"
