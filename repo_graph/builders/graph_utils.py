from __future__ import annotations

from pathlib import Path

from repo_graph.core.model import Edge, Graph, Node


def add_node(graph: Graph, seen_node_ids: set[str], node: Node) -> None:
    if node.id in seen_node_ids:
        return
    graph.nodes.append(node)
    seen_node_ids.add(node.id)


def add_edge(graph: Graph, seen_edge_keys: set[tuple], edge: Edge) -> None:
    key = edge_key(edge)
    if key in seen_edge_keys:
        return
    graph.edges.append(edge)
    seen_edge_keys.add(key)


def edge_key(edge: Edge) -> tuple:
    evidence = tuple((item.path, item.kind, item.line) for item in edge.evidence)
    metadata = tuple(sorted((key, str(value)) for key, value in edge.metadata.items()))
    return edge.source, edge.target, edge.type, edge.confidence, evidence, metadata


def read_text(repo_root: Path, relative_path: str) -> str:
    return (repo_root / relative_path).read_text(encoding="utf-8")


def module_name_from_path(path: str) -> str:
    without_suffix = path[:-3] if path.endswith(".py") else path
    parts = without_suffix.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def module_name_for_source_path(path: str, language: str) -> str:
    if language == "python":
        return module_name_from_path(path)
    return path


def module_node_id(module_name: str) -> str:
    return f"py:{module_name}"


def file_node_id(path: str) -> str:
    return f"file:{path}"


def path_from_file_node_id(node_id: str) -> str | None:
    if not node_id.startswith("file:"):
        return None
    return node_id.removeprefix("file:")


def is_python_test(path: str) -> bool:
    if not path.endswith(".py"):
        return False
    name = Path(path).name
    return name.startswith("test_") or name.endswith("_test.py")


def join_paths(*parts: str) -> str:
    cleaned = []
    for part in parts:
        if part is None:
            continue
        stripped = str(part).strip("/")
        if stripped:
            cleaned.append(stripped)
    return "/" + "/".join(cleaned)
