from __future__ import annotations

from repo_graph.builders.graph_utils import add_edge, add_node
from repo_graph.core.model import CodeqlCandidateRelation, CodeqlSymbol, Edge, Graph, Node


def add_codeql_candidate_relations(
    graph: Graph,
    relations: list[CodeqlCandidateRelation],
    seen_node_ids: set[str],
    seen_edge_keys: set[tuple],
) -> None:
    for relation in relations:
        source_id = codeql_symbol_node_id(relation.source)
        target_id = codeql_symbol_node_id(relation.target)
        add_node(graph, seen_node_ids, codeql_symbol_node(source_id, relation.source))
        add_node(graph, seen_node_ids, codeql_symbol_node(target_id, relation.target))
        metadata = {
            "provider": "codeql",
            "queryId": relation.query_id,
            "candidate": True,
        }
        if relation.query_name is not None:
            metadata["queryName"] = relation.query_name
        add_edge(
            graph,
            seen_edge_keys,
            Edge(
                source=source_id,
                target=target_id,
                type=relation.relation_type,
                confidence=relation.confidence,
                evidence=[relation.evidence],
                metadata=metadata,
            ),
        )


def codeql_symbol_node(node_id: str, symbol: CodeqlSymbol) -> Node:
    return Node(
        id=node_id,
        type="code_symbol",
        name=symbol.qualified_name,
        path=symbol.path,
        language=symbol.language,
        metadata={
            "provider": "codeql",
            "qualifiedName": symbol.qualified_name,
            "kind": symbol.kind,
            "line": symbol.line,
        },
    )


def codeql_symbol_node_id(symbol: CodeqlSymbol) -> str:
    line = symbol.line if symbol.line is not None else "unknown"
    return f"code_symbol:{symbol.path}:{symbol.qualified_name}@{line}"
