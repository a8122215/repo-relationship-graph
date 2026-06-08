from __future__ import annotations

from pathlib import Path

from repo_graph.builders.graph_utils import add_edge, add_node, read_text
from repo_graph.core.model import Edge, Graph, Node, SourceFile
from repo_graph.core.registry import ManifestParserRegistry


def add_package_dependencies(
    graph: Graph,
    repo_root: Path,
    source_files: list[SourceFile],
    manifest_registry: ManifestParserRegistry,
    seen_node_ids: set[str],
    seen_edge_keys: set[tuple],
) -> None:
    package_dependencies = []
    for source in source_files:
        if not manifest_registry.supports(source.path):
            continue
        package_dependencies.extend(
            manifest_registry.parse(source.path, read_text(repo_root, source.path))
        )
    for dependency in package_dependencies:
        add_node(
            graph,
            seen_node_ids,
            Node(
                id=dependency.source_id,
                type="package",
                name=dependency.source_name,
                path=dependency.evidence.path,
                language="toml" if dependency.manager == "pypi" else "json",
                metadata={"manager": dependency.manager},
            ),
        )
        add_node(
            graph,
            seen_node_ids,
            Node(
                id=dependency.target_id,
                type="external_package",
                name=dependency.target_name,
                path=None,
                language="virtual",
                metadata={"manager": dependency.manager},
            ),
        )
        add_edge(
            graph,
            seen_edge_keys,
            Edge(
                source=dependency.source_id,
                target=dependency.target_id,
                type="package_depends_on",
                confidence="high",
                evidence=[dependency.evidence],
                metadata={"manager": dependency.manager, "section": dependency.section},
            )
        )
