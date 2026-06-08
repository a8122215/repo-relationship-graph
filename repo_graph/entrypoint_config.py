from __future__ import annotations

from pathlib import Path

from repo_graph.config import CodeGraphConfig, resolve_code_graph_config


def load_entrypoint_config(repo_root: Path, config_path: Path | None) -> CodeGraphConfig:
    resolution = resolve_code_graph_config(
        repo_root,
        config_path=config_path,
        allow_legacy_defaults=False,
    )
    if resolution.config is None:
        raise ValueError("code graph config was not found")
    return resolution.config


def query_graph_path(
    repo_root: Path,
    config: CodeGraphConfig,
    explicit_graph_path: Path | None,
) -> Path:
    if explicit_graph_path is not None:
        return repo_path(repo_root, explicit_graph_path)
    return repo_path(repo_root, config.outputs.graph)


def mcp_graph_path(
    repo_root: Path,
    config: CodeGraphConfig,
    explicit_graph_path: Path | None,
) -> Path:
    if explicit_graph_path is not None:
        return repo_path(repo_root, explicit_graph_path)
    return repo_path(repo_root, config.mcp.default_graph)


def repo_path(repo_root: Path, path: Path) -> Path:
    return path if path.is_absolute() else repo_root / path
