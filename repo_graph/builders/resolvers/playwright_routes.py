from __future__ import annotations

from repo_graph.builders.graph_utils import add_edge, file_node_id
from repo_graph.core.model import Edge, Graph, Node, SourceParseResult
from repo_graph.source_paths import is_frontend_test_path


def add_e2e_route_edges(
    graph: Graph,
    parsed_sources: list[SourceParseResult],
    seen_edge_keys: set[tuple],
    frontend_test_roots: tuple[str, ...],
) -> None:
    routes_by_path: dict[str, list[Node]] = {}
    for node in graph.nodes:
        if node.type != "vue_route":
            continue
        route_path = node.metadata.get("routePath")
        if isinstance(route_path, str):
            routes_by_path.setdefault(route_path, []).append(node)

    for result in parsed_sources:
        if not is_frontend_test_path(result.path, frontend_test_roots):
            continue
        for navigation in result.page_navigations:
            for route in routes_by_path.get(navigation.route_path, []):
                add_edge(
                    graph,
                    seen_edge_keys,
                    Edge(
                        source=file_node_id(navigation.source_path),
                        target=route.id,
                        type="e2e_reaches_route",
                        confidence="high",
                        evidence=[navigation.evidence],
                        metadata={
                            "routePath": navigation.route_path,
                            "candidate": True,
                        },
                    ),
                )
