from __future__ import annotations

from pathlib import Path

from repo_graph.builders.graph_utils import (
    add_edge,
    add_node,
    is_python_test,
    module_name_for_source_path,
    module_name_from_path,
    module_node_id,
    read_text,
)
from repo_graph.builders.resolvers.codeql_candidates import add_codeql_candidate_relations
from repo_graph.builders.resolvers.fastapi import add_fastapi_endpoint_edges
from repo_graph.builders.resolvers.frontend_api_calls import add_frontend_api_call_edges
from repo_graph.builders.resolvers.package_dependencies import add_package_dependencies
from repo_graph.builders.resolvers.playwright_routes import add_e2e_route_edges
from repo_graph.builders.resolvers.test_edges import (
    add_frontend_test_edges_from_imports,
    add_test_edges_from_naming,
)
from repo_graph.core.model import CodeqlCandidateRelation, Edge, Graph, Node, SourceFile
from repo_graph.core.registry import ManifestParserRegistry, ParserRegistry
from repo_graph.source_paths import DEFAULT_FRONTEND_TEST_ROOTS, normalize_test_roots


def build_graph(
    repo_root: Path,
    source_files: list[SourceFile],
    parser_registry: ParserRegistry | None = None,
    manifest_registry: ManifestParserRegistry | None = None,
    codeql_relations: list[CodeqlCandidateRelation] | None = None,
    frontend_test_roots: tuple[str, ...] | None = None,
) -> Graph:
    if parser_registry is None:
        raise ValueError("parser_registry is required by the graph builder boundary")
    if manifest_registry is None:
        raise ValueError("manifest_registry is required by the graph builder boundary")

    normalized_frontend_test_roots = normalize_test_roots(frontend_test_roots or DEFAULT_FRONTEND_TEST_ROOTS)
    registry = parser_registry
    manifests = manifest_registry
    classified_sources = classify_source_files(source_files, registry, manifests)
    graph = Graph(source_manifest=sorted(classified_sources, key=lambda item: item.path))
    module_to_path = {
        module_name_from_path(source.path): source.path
        for source in classified_sources
        if source.language == "python"
    }
    known_modules = set(module_to_path.keys())
    parsed_sources = []
    seen_node_ids: set[str] = set()
    seen_edge_keys: set[tuple] = set()

    for source in graph.source_manifest:
        parser = registry.parser_for(source.language)
        if parser is not None:
            module_name = module_name_for_source(source)
            result = parser.parse(
                path=source.path,
                module_name=module_name,
                source=read_text(repo_root, source.path),
                known_modules=known_modules,
            )
            if result.node is not None:
                add_node(graph, seen_node_ids, result.node)
            for node in result.nodes:
                add_node(graph, seen_node_ids, node)
            for edge in result.edges:
                add_edge(graph, seen_edge_keys, edge)
            parsed_sources.append(result)
            graph.unsupported.extend(result.unsupported)
        else:
            unsupported = registry.unsupported_record_for(source)
            if unsupported is None:
                continue
            add_node(
                graph,
                seen_node_ids,
                Node(
                    id=f"file:{source.path}",
                    type="file",
                    name=source.path,
                    path=source.path,
                    language=source.language,
                )
            )
            graph.unsupported.append(unsupported)

    for result in parsed_sources:
        source_id = result.source_id or module_node_id(result.module_name)
        for import_relation in result.imports:
            target_module = import_relation.target_module
            if target_module in module_to_path:
                add_edge(
                    graph,
                    seen_edge_keys,
                    Edge(
                        source=source_id,
                        target=module_node_id(target_module),
                        type="imports",
                        confidence="high",
                        evidence=[import_relation.evidence],
                    )
                )
                if is_python_test(result.path):
                    add_edge(
                        graph,
                        seen_edge_keys,
                        Edge(
                            source=source_id,
                            target=module_node_id(target_module),
                            type="tests",
                            confidence="high",
                            evidence=[import_relation.evidence],
                            metadata={"reason": "test_import"},
                        )
                    )

    add_fastapi_endpoint_edges(graph, parsed_sources, module_to_path, seen_node_ids, seen_edge_keys)
    add_frontend_api_call_edges(graph, parsed_sources, seen_edge_keys, normalized_frontend_test_roots)
    add_e2e_route_edges(graph, parsed_sources, seen_edge_keys, normalized_frontend_test_roots)
    if registry.parser_for("python") is not None:
        add_test_edges_from_naming(graph, classified_sources, module_to_path, seen_edge_keys)
    add_frontend_test_edges_from_imports(graph, classified_sources, seen_edge_keys, normalized_frontend_test_roots)
    add_package_dependencies(graph, repo_root, classified_sources, manifests, seen_node_ids, seen_edge_keys)
    add_codeql_candidate_relations(graph, codeql_relations or [], seen_node_ids, seen_edge_keys)
    return graph


def classify_source_files(
    source_files: list[SourceFile],
    parser_registry: ParserRegistry,
    manifest_registry: ManifestParserRegistry,
) -> list[SourceFile]:
    classified = []
    for source in source_files:
        role = source.role
        has_parser = parser_registry.parser_for(source.language) is not None
        has_deferred_record = parser_registry.unsupported_record_for(source) is not None
        if manifest_registry.supports(source.path):
            role = "manifest"
        elif not has_parser and not has_deferred_record:
            role = "inventory_only"
        classified.append(SourceFile(path=source.path, language=source.language, role=role))
    return classified


def module_name_for_source(source: SourceFile) -> str:
    return module_name_for_source_path(source.path, source.language)
