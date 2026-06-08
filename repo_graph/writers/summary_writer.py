from __future__ import annotations

from collections import Counter

from repo_graph.core.model import Graph
from repo_graph.writers.json_writer import graph_to_dict


def graph_to_summary_text(graph: Graph) -> str:
    data = graph_to_dict(graph)
    node_counts = Counter(node["type"] for node in data["nodes"])
    edge_counts = Counter(edge["type"] for edge in data["edges"])
    unsupported_counts = Counter(item["reason"] for item in data["unsupported"])
    role_counts = Counter(item["role"] for item in data["sourceManifest"])
    lines = [
        "# Code Graph Summary",
        "",
        "> DO NOT EDIT: This file is generated from `repo_graph.json` by `make code-graph`.",
        "",
        "## Totals",
        "",
        f"- Source files: {len(data['sourceManifest'])}",
        f"- Parsed/processed files: {role_counts['source'] + role_counts['manifest']}",
        f"- Inventory-only files: {role_counts['inventory_only']}",
        f"- Nodes: {len(data['nodes'])}",
        f"- Edges: {len(data['edges'])}",
        f"- Unsupported records: {len(data['unsupported'])}",
        "",
        "## Source Roles",
        "",
        *format_counts(role_counts),
        "",
        "## Node Types",
        "",
        *format_counts(node_counts),
        "",
        "## Edge Types",
        "",
        *format_counts(edge_counts),
        "",
        "## Unsupported Reasons",
        "",
        *format_counts(unsupported_counts),
        "",
    ]
    return "\n".join(lines)


def format_counts(counter: Counter[str]) -> list[str]:
    if not counter:
        return ["- none"]
    return [f"- `{key}`: {counter[key]}" for key in sorted(counter)]
