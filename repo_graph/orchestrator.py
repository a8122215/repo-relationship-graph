from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from repo_graph.builders.graph_builder import build_graph
from repo_graph.checks.freshness import compare_generated_outputs
from repo_graph.core.model import CodeqlCandidateRelation, Graph, SourceFile
from repo_graph.core.registry import ManifestParserRegistry, ParserRegistry
from repo_graph.discovery import FileDiscovery, FileDiscoveryConfig
from repo_graph.writers.json_writer import GraphOutputMetadata, graph_to_json_text, schema_to_json_text
from repo_graph.writers.summary_writer import graph_to_summary_text


DEFAULT_GRAPH_PATH = Path("analysis/code_graph/repo_graph.json")
DEFAULT_SCHEMA_PATH = Path("analysis/code_graph/repo_graph.schema.json")
DEFAULT_SUMMARY_PATH = Path("analysis/code_graph/repo_graph.summary.md")
ParserRegistryFactory = Callable[[Path, list[SourceFile]], ParserRegistry]
CodeqlRelationLoader = Callable[[Path | None, Path, list[str]], list[CodeqlCandidateRelation]]


def generate_graph(
    repo_root: Path,
    include_untracked: bool = False,
    codeql_results_path: Path | None = None,
    codeql_relation_loader: CodeqlRelationLoader | None = None,
    codeql_relations: list[CodeqlCandidateRelation] | None = None,
    discovery_config: FileDiscoveryConfig | None = None,
    parser_registry_factory: ParserRegistryFactory | None = None,
    manifest_registry: ManifestParserRegistry | None = None,
    frontend_test_roots: tuple[str, ...] | None = None,
) -> Graph:
    if parser_registry_factory is None:
        raise ValueError("parser_registry_factory is required by the orchestration boundary")
    if manifest_registry is None:
        raise ValueError("manifest_registry is required by the orchestration boundary")
    if codeql_results_path is not None and codeql_relation_loader is None and codeql_relations is None:
        raise ValueError("codeql_results_path requires codeql_relation_loader")

    source_files = FileDiscovery(repo_root, config=discovery_config).discover(include_untracked=include_untracked)
    parser_registry = parser_registry_factory(repo_root, source_files)
    if codeql_relations is None and codeql_relation_loader is not None:
        codeql_relations = codeql_relation_loader(
            codeql_results_path,
            repo_root,
            [source.path for source in source_files],
        )
    return build_graph(
        repo_root,
        source_files,
        parser_registry=parser_registry,
        manifest_registry=manifest_registry,
        codeql_relations=codeql_relations,
        frontend_test_roots=frontend_test_roots,
    )


def generate_output_texts(
    repo_root: Path,
    output_path: Path = DEFAULT_GRAPH_PATH,
    schema_output_path: Path = DEFAULT_SCHEMA_PATH,
    summary_path: Path = DEFAULT_SUMMARY_PATH,
    include_untracked: bool = False,
    codeql_results_path: Path | None = None,
    codeql_relation_loader: CodeqlRelationLoader | None = None,
    codeql_relations: list[CodeqlCandidateRelation] | None = None,
    graph_metadata: GraphOutputMetadata | None = None,
    discovery_config: FileDiscoveryConfig | None = None,
    parser_registry_factory: ParserRegistryFactory | None = None,
    manifest_registry: ManifestParserRegistry | None = None,
    frontend_test_roots: tuple[str, ...] | None = None,
) -> dict[Path, str]:
    graph = generate_graph(
        repo_root,
        include_untracked=include_untracked,
        codeql_results_path=codeql_results_path,
        codeql_relation_loader=codeql_relation_loader,
        codeql_relations=codeql_relations,
        discovery_config=discovery_config,
        parser_registry_factory=parser_registry_factory,
        manifest_registry=manifest_registry,
        frontend_test_roots=frontend_test_roots,
    )
    return {
        repo_root / output_path: graph_to_json_text(graph, metadata=graph_metadata),
        repo_root / schema_output_path: schema_to_json_text(),
        repo_root / summary_path: graph_to_summary_text(graph),
    }


def write_outputs(outputs: dict[Path, str]) -> None:
    for path, text in outputs.items():
        atomic_write_text(path, text)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd = -1
    temp_path: Path | None = None
    try:
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temp_path = Path(temp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
            fd = -1
            temp_file.write(text)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    except Exception:
        if fd != -1:
            os.close(fd)
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise


def stale_outputs(outputs: dict[Path, str]) -> list[Path]:
    return compare_generated_outputs(outputs)
