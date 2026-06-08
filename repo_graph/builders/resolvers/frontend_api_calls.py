from __future__ import annotations

import re

from repo_graph.builders.graph_utils import add_edge, file_node_id
from repo_graph.core.model import Edge, Graph, Node, SourceParseResult
from repo_graph.source_paths import is_frontend_test_path


def add_frontend_api_call_edges(
    graph: Graph,
    parsed_sources: list[SourceParseResult],
    seen_edge_keys: set[tuple],
    frontend_test_roots: tuple[str, ...],
) -> None:
    endpoint_by_method_path: dict[tuple[str, str], Node] = {}
    endpoints_by_method_pattern: dict[tuple[str, str], list[Node]] = {}
    endpoints_by_path: dict[str, list[Node]] = {}
    endpoints_by_pattern: dict[str, list[Node]] = {}
    for node in graph.nodes:
        if node.type != "fastapi_endpoint":
            continue
        method = node.metadata.get("method")
        path = node.metadata.get("path")
        if not isinstance(method, str) or not isinstance(path, str):
            continue
        endpoint_by_method_path[(method.upper(), path)] = node
        endpoints_by_path.setdefault(path, []).append(node)
        pattern_key = parameterized_path_key(path)
        if pattern_key != path:
            endpoints_by_method_pattern.setdefault((method.upper(), pattern_key), []).append(node)
            endpoints_by_pattern.setdefault(pattern_key, []).append(node)

    for result in parsed_sources:
        if is_frontend_test_path(result.path, frontend_test_roots):
            continue
        for api_call in result.api_calls:
            target = None
            match_confidence = "medium"
            matched_by = "path"
            path_pattern = parameterized_path_key(api_call.path)
            if api_call.method:
                target = endpoint_by_method_path.get((api_call.method.upper(), api_call.path))
                match_confidence = "high"
                matched_by = "method_path"
                if target is None and path_pattern != api_call.path:
                    candidates = endpoints_by_method_pattern.get((api_call.method.upper(), path_pattern), [])
                    if len(candidates) == 1:
                        target = candidates[0]
                        match_confidence = "medium"
                        matched_by = "method_path_pattern"
            else:
                candidates = endpoints_by_path.get(api_call.path, [])
                if len(candidates) == 1:
                    target = candidates[0]
                elif path_pattern != api_call.path:
                    candidates = endpoints_by_pattern.get(path_pattern, [])
                    if len(candidates) == 1:
                        target = candidates[0]
                        match_confidence = "low"
                        matched_by = "path_pattern"
            if target is None:
                continue
            metadata = {
                "method": target.metadata.get("method"),
                "path": api_call.path,
                "callKind": api_call.call_kind,
                "candidate": True,
                "matchedBy": matched_by,
            }
            if matched_by.endswith("_pattern"):
                metadata["endpointPath"] = target.metadata.get("path")
                metadata["pathPattern"] = path_pattern
            add_edge(
                graph,
                seen_edge_keys,
                Edge(
                    source=file_node_id(api_call.source_path),
                    target=target.id,
                    type="calls_api_endpoint",
                    confidence=weaker_confidence(match_confidence, api_call.confidence),
                    evidence=[api_call.evidence],
                    metadata=metadata,
                ),
            )


PARAMETERIZED_PATH_SEGMENT_RE = re.compile(r"\{[^}/]+\}")


def parameterized_path_key(path: str) -> str:
    return PARAMETERIZED_PATH_SEGMENT_RE.sub("{}", path)


def weaker_confidence(left: str, right: str) -> str:
    ranks = {"high": 0, "medium": 1, "low": 2}
    return left if ranks.get(left, 3) >= ranks.get(right, 3) else right
