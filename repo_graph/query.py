from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from repo_graph.writers.json_writer import validate_graph_contract


DEFAULT_MAX_RESULTS = 50
MAX_RESULT_LIMIT = 200
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}


class CodeGraphQueryService:
    def __init__(self, graph: dict[str, Any], default_max_results: int = DEFAULT_MAX_RESULTS) -> None:
        self.graph = graph
        self.default_max_results = clamp_limit(default_max_results)
        self.nodes_by_id = {node["id"]: node for node in graph.get("nodes", [])}
        self.nodes_by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for node in graph.get("nodes", []):
            if node.get("path"):
                self.nodes_by_path[node["path"]].append(node)
        self.outgoing_edges: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.incoming_edges: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in graph.get("edges", []):
            self.outgoing_edges[edge["source"]].append(edge)
            self.incoming_edges[edge["target"]].append(edge)

    @classmethod
    def from_path(
        cls,
        graph_path: Path = Path("analysis/code_graph/repo_graph.json"),
        default_max_results: int = DEFAULT_MAX_RESULTS,
    ) -> "CodeGraphQueryService":
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        validate_graph_contract(graph)
        return cls(graph, default_max_results)

    def find_impacted_files(self, path: str, max_results: int | None = None) -> dict[str, Any]:
        limit = self._limit(max_results)
        resolution = self.resolve_nodes(path)
        results_by_path: dict[str, dict[str, Any]] = {}
        for seed in resolution.nodes:
            for edge in self.incoming_edges.get(seed["id"], []):
                if edge["type"] not in {
                    "imports",
                    "registers_router",
                    "tests",
                    "calls",
                    "data_flows_to",
                    "calls_api_endpoint",
                    "e2e_reaches_route",
                }:
                    continue
                self._add_node_path_result(results_by_path, edge["source"], edge, "incoming")
            for edge in self.incoming_edges.get(seed["id"], []):
                if edge["type"] != "renders_view":
                    continue
                route_source_path = edge.get("metadata", {}).get("routeSourcePath")
                if isinstance(route_source_path, str):
                    self._add_path_result(results_by_path, route_source_path, edge, "route_source")
        results = sorted(results_by_path.values(), key=result_sort_key)
        return {
            "query": resolution.to_query(limit),
            "total": len(results),
            "truncated": len(results) > limit,
            "files": results[:limit],
            "note": "Impact candidates are graph neighbors, not proof of runtime impact.",
        }

    def find_tests_for(self, path: str, max_results: int | None = None) -> dict[str, Any]:
        limit = self._limit(max_results)
        resolution = self.resolve_nodes(path)
        results_by_node: dict[str, dict[str, Any]] = {}
        for seed in resolution.nodes:
            for edge in self.incoming_edges.get(seed["id"], []):
                if edge["type"] != "tests":
                    continue
                test_node = self.nodes_by_id.get(edge["source"])
                if test_node is None or test_node.get("type") != "test_file" or not test_node.get("path"):
                    continue
                existing = results_by_node.setdefault(
                    test_node["id"],
                    {
                        "path": test_node["path"],
                        "nodeId": test_node["id"],
                        "nodeType": test_node["type"],
                        "confidence": edge.get("confidence"),
                        "reason": edge.get("metadata", {}).get("reason"),
                        "evidence": edge.get("evidence", []),
                        "matchingEdges": [],
                    },
                )
                existing["matchingEdges"].append(edge_summary(edge, "incoming"))
                existing["confidence"] = strongest_confidence(existing.get("confidence"), edge.get("confidence"))
        results = sorted(results_by_node.values(), key=test_result_sort_key)
        return {
            "query": resolution.to_query(limit),
            "total": len(results),
            "truncated": len(results) > limit,
            "tests": results[:limit],
            "note": "Test relations are graph-index candidates; inspect tests directly before relying on them.",
        }

    def find_endpoints_for_router(self, path: str, max_results: int | None = None) -> dict[str, Any]:
        limit = self._limit(max_results)
        resolution = self.resolve_nodes(path)
        endpoints_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        for seed in resolution.nodes:
            self._add_endpoint_results(endpoints_by_key, seed, seed, via="direct")
            for registration_edge in self.outgoing_edges.get(seed["id"], []):
                if registration_edge["type"] != "registers_router":
                    continue
                router = self.nodes_by_id.get(registration_edge["target"])
                if router is None:
                    continue
                self._add_endpoint_results(
                    endpoints_by_key,
                    seed,
                    router,
                    via="registered_router",
                    registration_edge=registration_edge,
                )
        endpoints = sorted(endpoints_by_key.values(), key=endpoint_result_sort_key)
        return {
            "query": resolution.to_query(limit),
            "total": len(endpoints),
            "truncated": len(endpoints) > limit,
            "endpoints": endpoints[:limit],
        }

    def find_routes_for_view(self, path: str, max_results: int | None = None) -> dict[str, Any]:
        limit = self._limit(max_results)
        resolution = self.resolve_nodes(path)
        routes_by_id: dict[str, dict[str, Any]] = {}
        for seed in resolution.nodes:
            for edge in self.incoming_edges.get(seed["id"], []):
                if edge["type"] != "renders_view":
                    continue
                route = self.nodes_by_id.get(edge["source"])
                if route is None:
                    continue
                routes_by_id.setdefault(route["id"], route_result(route, edge, "renders_view_edge"))
        if resolution.normalized_path is not None:
            for route in self.graph.get("nodes", []):
                metadata = route.get("metadata", {})
                if route.get("type") != "vue_route" or metadata.get("componentPath") != resolution.normalized_path:
                    continue
                routes_by_id.setdefault(route["id"], route_result(route, None, "metadata_component_path"))
        routes = sorted(routes_by_id.values(), key=route_result_sort_key)
        return {
            "query": resolution.to_query(limit),
            "total": len(routes),
            "truncated": len(routes) > limit,
            "routes": routes[:limit],
        }

    def find_api_callers_for_endpoint(self, endpoint: str, max_results: int | None = None) -> dict[str, Any]:
        limit = self._limit(max_results)
        resolution = self.resolve_endpoint_nodes(endpoint)
        results_by_path: dict[str, dict[str, Any]] = {}
        for endpoint_node in resolution.nodes:
            for edge in self.incoming_edges.get(endpoint_node["id"], []):
                if edge["type"] != "calls_api_endpoint":
                    continue
                self._add_node_path_result(results_by_path, edge["source"], edge, "api_caller")
        results = sorted(results_by_path.values(), key=result_sort_key)
        return {
            "query": resolution.to_query(limit),
            "total": len(results),
            "truncated": len(results) > limit,
            "files": results[:limit],
            "note": "Frontend API call edges are static candidates, not runtime proof.",
        }

    def find_e2e_for_route(self, route: str, max_results: int | None = None) -> dict[str, Any]:
        limit = self._limit(max_results)
        resolution = self.resolve_route_nodes(route)
        results_by_node: dict[str, dict[str, Any]] = {}
        for route_node in resolution.nodes:
            self._add_e2e_specs_for_route(results_by_node, route_node)
        results = sorted(results_by_node.values(), key=test_result_sort_key)
        return {
            "query": resolution.to_query(limit),
            "total": len(results),
            "truncated": len(results) > limit,
            "tests": results[:limit],
            "note": "E2E route reachability edges are static page.goto/toHaveURL candidates, not coverage proof.",
        }

    def find_e2e_for_view(self, path: str, max_results: int | None = None) -> dict[str, Any]:
        limit = self._limit(max_results)
        resolution = self.resolve_nodes(path)
        routes_by_id: dict[str, dict[str, Any]] = {}
        for seed in resolution.nodes:
            for edge in self.incoming_edges.get(seed["id"], []):
                if edge["type"] != "renders_view":
                    continue
                route = self.nodes_by_id.get(edge["source"])
                if route is not None:
                    routes_by_id[route["id"]] = route
        if resolution.normalized_path is not None:
            for route in self.graph.get("nodes", []):
                metadata = route.get("metadata", {})
                if route.get("type") == "vue_route" and metadata.get("componentPath") == resolution.normalized_path:
                    routes_by_id[route["id"]] = route
        results_by_node: dict[str, dict[str, Any]] = {}
        for route_node in routes_by_id.values():
            self._add_e2e_specs_for_route(results_by_node, route_node)
        results = sorted(results_by_node.values(), key=test_result_sort_key)
        return {
            "query": resolution.to_query(limit),
            "total": len(results),
            "truncated": len(results) > limit,
            "tests": results[:limit],
            "note": "E2E view results are derived through Vue routes and static page.goto/toHaveURL candidates.",
        }

    def explain_node(self, node_id: str, max_results: int | None = None) -> dict[str, Any]:
        limit = self._limit(max_results)
        resolution = self.resolve_unique_node(node_id)
        node = resolution.node
        if node is None:
            return {
                "query": resolution.to_query(limit),
                "node": None,
                "incomingTotal": 0,
                "outgoingTotal": 0,
                "incomingTruncated": False,
                "outgoingTruncated": False,
                "incoming": [],
                "outgoing": [],
            }
        incoming = [self._edge_with_neighbor(edge, "source") for edge in self.incoming_edges.get(node["id"], [])]
        outgoing = [self._edge_with_neighbor(edge, "target") for edge in self.outgoing_edges.get(node["id"], [])]
        incoming.sort(key=edge_result_sort_key)
        outgoing.sort(key=edge_result_sort_key)
        return {
            "query": resolution.to_query(limit),
            "node": node,
            "incomingTotal": len(incoming),
            "outgoingTotal": len(outgoing),
            "incomingTruncated": len(incoming) > limit,
            "outgoingTruncated": len(outgoing) > limit,
            "incoming": incoming[:limit],
            "outgoing": outgoing[:limit],
        }

    def nodes_for_path_or_id(self, value: str) -> list[dict[str, Any]]:
        return self.resolve_nodes(value).nodes

    def resolve_nodes(self, value: str) -> "NodeResolution":
        if value in self.nodes_by_id:
            return NodeResolution(
                input=value,
                lookup=value,
                normalized_path=None,
                exact_id=True,
                nodes=[self.nodes_by_id[value]],
            )
        normalized_path = normalize_path(value)
        return NodeResolution(
            input=value,
            lookup=normalized_path,
            normalized_path=normalized_path,
            exact_id=False,
            nodes=sorted(self.nodes_by_path.get(normalized_path, []), key=lambda node: node["id"]),
        )

    def resolve_unique_node(self, value: str) -> "UniqueNodeResolution":
        if value in self.nodes_by_id:
            return UniqueNodeResolution(
                input=value,
                lookup=value,
                normalized_path=None,
                exact_id=True,
                node=self.nodes_by_id[value],
            )
        if ":" in value:
            return UniqueNodeResolution(input=value, lookup=value, normalized_path=None, exact_id=True, node=None)
        resolution = self.resolve_nodes(value)
        if len(resolution.nodes) > 1:
            matching_ids = ", ".join(node["id"] for node in resolution.nodes)
            raise ValueError(f"ambiguous node path `{resolution.lookup}`; use one of: {matching_ids}")
        node = resolution.nodes[0] if resolution.nodes else None
        return UniqueNodeResolution.from_resolution(resolution, node)

    def resolve_endpoint_nodes(self, value: str) -> "QueryNodeResolution":
        if value in self.nodes_by_id:
            node = self.nodes_by_id[value]
            return QueryNodeResolution(
                input=value,
                lookup=value,
                exact_id=True,
                nodes=[node] if node.get("type") == "fastapi_endpoint" else [],
            )
        method, path = parse_endpoint_query(value)
        matching = []
        for node in self.graph.get("nodes", []):
            metadata = node.get("metadata", {})
            if node.get("type") != "fastapi_endpoint":
                continue
            if metadata.get("path") != path:
                continue
            if method is not None and metadata.get("method") != method:
                continue
            matching.append(node)
        lookup = f"{method} {path}" if method else path
        return QueryNodeResolution(input=value, lookup=lookup, exact_id=False, nodes=sorted(matching, key=lambda node: node["id"]))

    def resolve_route_nodes(self, value: str) -> "QueryNodeResolution":
        if value in self.nodes_by_id:
            node = self.nodes_by_id[value]
            return QueryNodeResolution(
                input=value,
                lookup=value,
                exact_id=True,
                nodes=[node] if node.get("type") == "vue_route" else [],
            )
        route_path = normalize_route_query_path(value)
        matching = [
            node
            for node in self.graph.get("nodes", [])
            if node.get("type") == "vue_route" and node.get("metadata", {}).get("routePath") == route_path
        ]
        return QueryNodeResolution(input=value, lookup=route_path, exact_id=False, nodes=sorted(matching, key=lambda node: node["id"]))

    def _add_node_path_result(
        self,
        results_by_path: dict[str, dict[str, Any]],
        node_id: str,
        edge: dict[str, Any],
        reason: str,
    ) -> None:
        node = self.nodes_by_id.get(node_id)
        if node is None or not node.get("path"):
            return
        self._add_path_result(results_by_path, node["path"], edge, reason, node)

    def _add_path_result(
        self,
        results_by_path: dict[str, dict[str, Any]],
        path: str,
        edge: dict[str, Any],
        reason: str,
        node: dict[str, Any] | None = None,
    ) -> None:
        result = results_by_path.setdefault(
            path,
            {
                "path": path,
                "reason": reason,
                "reasons": [],
                "edgeType": edge["type"],
                "confidence": edge.get("confidence"),
                "evidence": edge.get("evidence", []),
                "matchingEdges": [],
            },
        )
        if node is not None:
            result["nodeId"] = node["id"]
            result["nodeType"] = node["type"]
        result["confidence"] = strongest_confidence(result.get("confidence"), edge.get("confidence"))
        if reason not in result["reasons"]:
            result["reasons"].append(reason)
            result["reasons"].sort()
        result["matchingEdges"].append(edge_summary(edge, reason))

    def _add_endpoint_results(
        self,
        endpoints_by_key: dict[tuple[str, str], dict[str, Any]],
        seed: dict[str, Any],
        router: dict[str, Any],
        via: str,
        registration_edge: dict[str, Any] | None = None,
    ) -> None:
        for edge in self.outgoing_edges.get(router["id"], []):
            if edge["type"] != "exposes_endpoint":
                continue
            endpoint = self.nodes_by_id.get(edge["target"])
            if endpoint is None:
                continue
            key = (endpoint["id"], router["id"])
            metadata = endpoint.get("metadata", {})
            result = endpoints_by_key.setdefault(
                key,
                {
                    "id": endpoint["id"],
                    "name": endpoint["name"],
                    "method": metadata.get("method"),
                    "path": metadata.get("path"),
                    "metadata": metadata,
                    "routerNodeId": router["id"],
                    "routerPath": router.get("path"),
                    "sourceNodeId": seed["id"],
                    "sourcePath": seed.get("path"),
                    "via": via,
                    "confidence": edge.get("confidence"),
                    "evidence": edge.get("evidence", []),
                    "registrationEvidence": registration_edge.get("evidence", []) if registration_edge else [],
                    "matchingEdges": [],
                },
            )
            result["confidence"] = strongest_confidence(result.get("confidence"), edge.get("confidence"))
            result["matchingEdges"].append(edge_summary(edge, via))

    def _add_e2e_specs_for_route(
        self,
        results_by_node: dict[str, dict[str, Any]],
        route_node: dict[str, Any],
    ) -> None:
        for edge in self.incoming_edges.get(route_node["id"], []):
            if edge["type"] != "e2e_reaches_route":
                continue
            test_node = self.nodes_by_id.get(edge["source"])
            if test_node is None or test_node.get("type") != "test_file" or not test_node.get("path"):
                continue
            existing = results_by_node.setdefault(
                test_node["id"],
                {
                    "path": test_node["path"],
                    "nodeId": test_node["id"],
                    "nodeType": test_node["type"],
                    "confidence": edge.get("confidence"),
                    "reason": "e2e_reaches_route",
                    "evidence": edge.get("evidence", []),
                    "routes": [],
                    "matchingEdges": [],
                },
            )
            existing["confidence"] = strongest_confidence(existing.get("confidence"), edge.get("confidence"))
            existing["matchingEdges"].append(edge_summary(edge, "e2e_reaches_route"))
            route_summary = route_result(route_node, edge, "e2e_reaches_route")
            if route_summary["id"] not in {route["id"] for route in existing["routes"]}:
                existing["routes"].append(route_summary)

    def _edge_with_neighbor(self, edge: dict[str, Any], neighbor_key: str) -> dict[str, Any]:
        neighbor = self.nodes_by_id.get(edge[neighbor_key])
        return {
            "edge": edge_summary(edge, "explain"),
            "neighbor": node_summary(neighbor),
        }

    def _limit(self, max_results: int | None) -> int:
        if max_results is None:
            return self.default_max_results
        return clamp_limit(max_results)


@dataclass(frozen=True)
class NodeResolution:
    input: str
    lookup: str
    normalized_path: str | None
    exact_id: bool
    nodes: list[dict[str, Any]]

    def to_query(self, limit: int) -> dict[str, Any]:
        return {
            "input": self.input,
            "lookup": self.lookup,
            "exactId": self.exact_id,
            "matchedNodeIds": [node["id"] for node in self.nodes],
            "maxResults": limit,
        }


@dataclass(frozen=True)
class QueryNodeResolution:
    input: str
    lookup: str
    exact_id: bool
    nodes: list[dict[str, Any]]

    def to_query(self, limit: int) -> dict[str, Any]:
        return {
            "input": self.input,
            "lookup": self.lookup,
            "exactId": self.exact_id,
            "matchedNodeIds": [node["id"] for node in self.nodes],
            "maxResults": limit,
        }


@dataclass(frozen=True)
class UniqueNodeResolution:
    input: str
    lookup: str
    normalized_path: str | None
    exact_id: bool
    node: dict[str, Any] | None

    @classmethod
    def from_resolution(cls, resolution: NodeResolution, node: dict[str, Any] | None) -> "UniqueNodeResolution":
        return cls(
            input=resolution.input,
            lookup=resolution.lookup,
            normalized_path=resolution.normalized_path,
            exact_id=resolution.exact_id,
            node=node,
        )

    def to_query(self, limit: int) -> dict[str, Any]:
        return {
            "input": self.input,
            "lookup": self.lookup,
            "exactId": self.exact_id,
            "matchedNodeId": self.node["id"] if self.node else None,
            "maxResults": limit,
        }


def normalize_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    posix_path = PurePosixPath(normalized)
    parts = posix_path.parts
    if not normalized or normalized == "." or not parts:
        raise ValueError("path must be a repo-relative path or exact node id")
    if posix_path.is_absolute() or any(part == ".." for part in parts) or any(":" in part for part in parts):
        raise ValueError("path must be repo-relative and must not contain parent traversal")
    return str(posix_path)


def parse_endpoint_query(value: str) -> tuple[str | None, str]:
    stripped = value.strip()
    if not stripped:
        raise ValueError("endpoint must be a non-empty endpoint id, `METHOD /path`, or `/path`")
    if stripped.startswith("api:"):
        stripped = stripped.removeprefix("api:").strip()
    parts = stripped.split(maxsplit=1)
    if len(parts) == 2 and parts[0].upper() in HTTP_METHODS:
        return parts[0].upper(), normalize_endpoint_path(parts[1])
    return None, normalize_endpoint_path(stripped)


def normalize_endpoint_path(value: str) -> str:
    path = value.strip()
    if not path:
        raise ValueError("endpoint path must be non-empty")
    if "://" in path:
        raise ValueError("endpoint path must be a path, not an absolute URL")
    while path.startswith("./"):
        path = path[2:]
    path = path.split("#", 1)[0].split("?", 1)[0]
    path = path.replace("\\", "/")
    while "//" in path:
        path = path.replace("//", "/")
    if not path.startswith("/"):
        path = f"/{path}"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    if ".." in PurePosixPath(path).parts:
        raise ValueError("endpoint path must not contain parent traversal")
    return path


def normalize_route_query_path(value: str) -> str:
    path = value.strip()
    if not path:
        raise ValueError("route path must be non-empty")
    if "://" in path:
        raise ValueError("route path must be a route path or exact route node id, not an absolute URL")
    while path.startswith("./"):
        path = path[2:]
    path = path.split("#", 1)[0].split("?", 1)[0]
    path = path.replace("\\", "/")
    while "//" in path:
        path = path.replace("//", "/")
    if not path.startswith("/"):
        path = f"/{path}"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    if ".." in PurePosixPath(path).parts:
        raise ValueError("route path must not contain parent traversal")
    return path


def clamp_limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("maxResults must be a positive integer")
    return min(value, MAX_RESULT_LIMIT)


def result_sort_key(item: dict[str, Any]) -> tuple:
    return (item.get("path", ""), item.get("nodeId", ""), item.get("edgeType", ""), item.get("reason", ""))


def test_result_sort_key(item: dict[str, Any]) -> tuple:
    return (confidence_rank(item.get("confidence")), item.get("path", ""), item.get("nodeId", ""))


def endpoint_result_sort_key(item: dict[str, Any]) -> tuple:
    return (item.get("path") or "", item.get("method") or "", item.get("id", ""), item.get("routerNodeId", ""))


def route_result_sort_key(item: dict[str, Any]) -> tuple:
    return (item.get("routePath") or "", item.get("routeName") or "", item.get("id", ""))


def edge_result_sort_key(item: dict[str, Any]) -> tuple:
    edge = item["edge"]
    evidence = edge.get("evidence", [])
    first_evidence = evidence[0] if evidence else {}
    metadata = json.dumps(edge.get("metadata", {}), ensure_ascii=False, sort_keys=True)
    return (
        edge.get("type", ""),
        edge.get("source", ""),
        edge.get("target", ""),
        confidence_rank(edge.get("confidence")),
        first_evidence.get("path", ""),
        first_evidence.get("kind", ""),
        first_evidence.get("line", 0) or 0,
        metadata,
    )


def edge_summary(edge: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "source": edge.get("source"),
        "target": edge.get("target"),
        "type": edge.get("type"),
        "reason": reason,
        "confidence": edge.get("confidence"),
        "metadata": edge.get("metadata", {}),
        "evidence": edge.get("evidence", []),
    }


def node_summary(node: dict[str, Any] | None) -> dict[str, Any] | None:
    if node is None:
        return None
    return {
        "id": node.get("id"),
        "type": node.get("type"),
        "name": node.get("name"),
        "path": node.get("path"),
        "language": node.get("language"),
        "metadata": node.get("metadata", {}),
    }


def route_result(route: dict[str, Any], edge: dict[str, Any] | None, match: str) -> dict[str, Any]:
    metadata = route.get("metadata", {})
    return {
        "id": route["id"],
        "name": route["name"],
        "routePath": metadata.get("routePath"),
        "routeName": metadata.get("routeName"),
        "routeSourcePath": metadata.get("routeSourcePath"),
        "componentPath": metadata.get("componentPath"),
        "metadata": metadata,
        "match": match,
        "confidence": edge.get("confidence") if edge else "medium",
        "evidence": edge.get("evidence", []) if edge else [],
    }


def strongest_confidence(left: str | None, right: str | None) -> str | None:
    if left is None:
        return right
    if right is None:
        return left
    return left if confidence_rank(left) <= confidence_rank(right) else right


def confidence_rank(value: str | None) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(value or "", 3)
