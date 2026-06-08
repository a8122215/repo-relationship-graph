from __future__ import annotations

from pathlib import Path

from repo_graph.builders.graph_utils import (
    add_edge,
    file_node_id,
    is_python_test,
    module_node_id,
    path_from_file_node_id,
)
from repo_graph.core.model import Edge, Evidence, Graph, SourceFile
from repo_graph.source_paths import is_frontend_test_path


def add_test_edges_from_naming(
    graph: Graph,
    source_files: list[SourceFile],
    module_to_path: dict[str, str],
    seen_edge_keys: set[tuple],
) -> None:
    path_to_module = {path: module for module, path in module_to_path.items()}
    for source in source_files:
        if not is_python_test(source.path):
            continue
        test_module = path_to_module[source.path]
        subject = Path(source.path).stem.removeprefix("test_")
        candidate_paths = [
            f"server/routers/{subject}.py",
            f"server/services/{subject}.py",
            f"server/services/{subject}_service.py",
        ]
        for candidate_path in candidate_paths:
            candidate_module = path_to_module.get(candidate_path)
            if not candidate_module:
                continue
            key = (module_node_id(test_module), module_node_id(candidate_module), "tests")
            if any(edge.source == key[0] and edge.target == key[1] and edge.type == key[2] for edge in graph.edges):
                continue
            add_edge(
                graph,
                seen_edge_keys,
                Edge(
                    source=key[0],
                    target=key[1],
                    type="tests",
                    confidence="medium",
                    evidence=[Evidence(path=source.path, kind="naming_convention")],
                    metadata={"reason": "naming_convention"},
                )
            )


def add_frontend_test_edges_from_imports(
    graph: Graph,
    source_files: list[SourceFile],
    seen_edge_keys: set[tuple],
    frontend_test_roots: tuple[str, ...],
) -> None:
    frontend_test_ids = {
        file_node_id(source.path)
        for source in source_files
        if is_frontend_test_path(source.path, frontend_test_roots)
    }
    for edge in list(graph.edges):
        if edge.type != "imports" or edge.source not in frontend_test_ids:
            continue
        target_path = path_from_file_node_id(edge.target)
        if target_path is None or is_frontend_test_path(target_path, frontend_test_roots):
            continue
        add_edge(
            graph,
            seen_edge_keys,
            Edge(
                source=edge.source,
                target=edge.target,
                type="tests",
                confidence="high",
                evidence=edge.evidence,
                metadata={"reason": "test_import"},
            ),
        )
