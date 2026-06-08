from __future__ import annotations

from repo_graph.builders.graph_utils import add_edge, add_node, join_paths, module_node_id
from repo_graph.core.model import Edge, Graph, Node, SourceParseResult


def add_fastapi_endpoint_edges(
    graph: Graph,
    parsed_sources: list[SourceParseResult],
    module_to_path: dict[str, str],
    seen_node_ids: set[str],
    seen_edge_keys: set[tuple],
) -> None:
    include_prefixes: dict[str, set[str]] = {}
    for result in parsed_sources:
        for include in result.include_routers:
            if include.target_module:
                include_prefixes.setdefault(include.target_module, set()).add(include.prefix)
                if include.target_module in module_to_path:
                    add_edge(
                        graph,
                        seen_edge_keys,
                        Edge(
                            source=module_node_id(result.module_name),
                            target=module_node_id(include.target_module),
                            type="registers_router",
                            confidence="high",
                            evidence=[include.evidence],
                            metadata={"prefix": include.prefix},
                        ),
                    )

    for result in parsed_sources:
        for endpoint in result.endpoints:
            local_prefix = "" if endpoint.router_name == "app" else result.router_prefixes.get(endpoint.router_name, "")
            include_values = sorted(include_prefixes.get(result.module_name, {""}))
            if endpoint.router_name == "app":
                include_values = [""]
            for include_prefix in include_values:
                full_path = join_paths(include_prefix, local_prefix, endpoint.path)
                endpoint_id = f"api:{endpoint.method} {full_path}"
                add_node(
                    graph,
                    seen_node_ids,
                    Node(
                        id=endpoint_id,
                        type="fastapi_endpoint",
                        name=f"{endpoint.method} {full_path}",
                        path=None,
                        language="virtual",
                        metadata={"method": endpoint.method, "path": full_path},
                    ),
                )
                add_edge(
                    graph,
                    seen_edge_keys,
                    Edge(
                        source=module_node_id(result.module_name),
                        target=endpoint_id,
                        type="exposes_endpoint",
                        confidence="high" if endpoint.router_name == "app" or endpoint.router_name in result.router_prefixes else "medium",
                        evidence=[endpoint.evidence],
                        metadata={"routerName": endpoint.router_name},
                    ),
                )
