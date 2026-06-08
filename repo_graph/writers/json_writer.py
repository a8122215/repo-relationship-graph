from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repo_graph.core.model import (
    CONFIDENCE_VALUES,
    SOURCE_FILE_ROLES,
    Edge,
    Evidence,
    Graph,
    Node,
    SourceFile,
    UnsupportedRecord,
)
from repo_graph.version import GENERATOR_VERSION, PLUGIN_VERSION

CONFIDENCE_ENUM = ["high", "medium", "low"]
DEFAULT_REPO_NAME = "repository"


@dataclass(frozen=True)
class GraphOutputMetadata:
    repo_name: str = DEFAULT_REPO_NAME
    repo_root: str = "."
    generator_version: str = GENERATOR_VERSION
    plugin_version: str = PLUGIN_VERSION


def graph_to_json_text(graph: Graph, metadata: GraphOutputMetadata | None = None) -> str:
    data = graph_to_dict(graph, metadata=metadata)
    validate_graph_contract(data)
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def graph_to_dict(graph: Graph, metadata: GraphOutputMetadata | None = None) -> dict[str, Any]:
    metadata = metadata or GraphOutputMetadata()
    data = {
        "schemaVersion": 1,
        "generatorVersion": metadata.generator_version,
        "pluginVersion": metadata.plugin_version,
        "repo": {"name": metadata.repo_name, "root": metadata.repo_root},
        "sourceManifest": [source_file_to_dict(item) for item in graph.source_manifest],
        "nodes": [node_to_dict(item) for item in graph.nodes],
        "edges": [edge_to_dict(item) for item in graph.edges],
        "unsupported": [unsupported_to_dict(item) for item in graph.unsupported],
    }
    data["sourceManifest"].sort(key=lambda item: item["path"])
    data["nodes"].sort(key=lambda item: item["id"])
    data["edges"].sort(key=serialized_edge_key)
    data["unsupported"].sort(key=lambda item: (item["path"], item["reason"]))
    return data


def source_file_to_dict(source_file: SourceFile) -> dict[str, Any]:
    return {
        "path": source_file.path,
        "language": source_file.language,
        "role": source_file.role,
    }


def node_to_dict(node: Node) -> dict[str, Any]:
    return {
        "id": node.id,
        "type": node.type,
        "name": node.name,
        "path": node.path,
        "language": node.language,
        "metadata": node.metadata,
    }


def edge_to_dict(edge: Edge) -> dict[str, Any]:
    return {
        "source": edge.source,
        "target": edge.target,
        "type": edge.type,
        "confidence": edge.confidence,
        "evidence": [evidence_to_dict(item) for item in edge.evidence],
        "metadata": edge.metadata,
    }


def evidence_to_dict(evidence: Evidence) -> dict[str, Any]:
    return {
        "path": evidence.path,
        "line": evidence.line,
        "kind": evidence.kind,
    }


def unsupported_to_dict(record: UnsupportedRecord) -> dict[str, Any]:
    return {
        "path": record.path,
        "language": record.language,
        "reason": record.reason,
        "phase": record.phase,
        "message": record.message,
        "line": record.line,
    }


def build_schema_document() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Repository Code Graph",
        "type": "object",
        "required": [
            "schemaVersion",
            "generatorVersion",
            "pluginVersion",
            "repo",
            "sourceManifest",
            "nodes",
            "edges",
            "unsupported",
        ],
        "additionalProperties": False,
        "properties": {
            "schemaVersion": {"type": "integer"},
            "generatorVersion": {"type": "string"},
            "pluginVersion": {"type": "string"},
            "repo": {
                "type": "object",
                "required": ["name", "root"],
                "additionalProperties": False,
                "properties": {"name": {"type": "string"}, "root": {"type": "string"}},
            },
            "sourceManifest": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["path", "language", "role"],
                    "additionalProperties": False,
                    "properties": {
                        "path": {"type": "string"},
                        "language": {"type": "string"},
                        "role": {"type": "string", "enum": sorted(SOURCE_FILE_ROLES)},
                    },
                },
            },
            "nodes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "type", "name", "path", "language", "metadata"],
                    "additionalProperties": False,
                    "properties": {
                        "id": {"type": "string"},
                        "type": {"type": "string"},
                        "name": {"type": "string"},
                        "path": {"type": ["string", "null"]},
                        "language": {"type": "string"},
                        "metadata": {"type": "object"},
                    },
                },
            },
            "edges": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["source", "target", "type", "confidence", "evidence", "metadata"],
                    "additionalProperties": False,
                    "properties": {
                        "source": {"type": "string"},
                        "target": {"type": "string"},
                        "type": {"type": "string"},
                        "confidence": {"type": "string", "enum": CONFIDENCE_ENUM},
                        "evidence": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["path", "kind", "line"],
                                "additionalProperties": False,
                                "properties": {
                                    "path": {"type": "string"},
                                    "kind": {"type": "string"},
                                    "line": {"type": ["integer", "null"]},
                                },
                            },
                        },
                        "metadata": {"type": "object"},
                    },
                },
            },
            "unsupported": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["path", "language", "reason", "phase", "message", "line"],
                    "additionalProperties": False,
                    "properties": {
                        "path": {"type": "string"},
                        "language": {"type": "string"},
                        "reason": {"type": "string"},
                        "phase": {"type": "string"},
                        "message": {"type": "string"},
                        "line": {"type": ["integer", "null"]},
                    },
                },
            },
        },
    }


def schema_to_json_text() -> str:
    return json.dumps(build_schema_document(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def validate_graph_contract(data: dict[str, Any]) -> None:
    required = {
        "schemaVersion",
        "generatorVersion",
        "pluginVersion",
        "repo",
        "sourceManifest",
        "nodes",
        "edges",
        "unsupported",
    }
    missing = required - set(data)
    if missing:
        raise ValueError(f"missing required graph fields: {sorted(missing)}")
    if "gitCommit" in data.get("repo", {}) or "dirty" in data.get("repo", {}):
        raise ValueError("repo metadata must be deterministic")
    repo = data["repo"]
    if not isinstance(repo.get("name"), str) or not repo["name"]:
        raise ValueError("repo.name must be a non-empty string")
    validate_path(repo["root"])
    for source_file in data["sourceManifest"]:
        validate_path(source_file["path"])
        if source_file.get("role") not in SOURCE_FILE_ROLES:
            raise ValueError(f"invalid source file role: {source_file.get('role')}")
    for node in data["nodes"]:
        for field in ("id", "type", "name", "language", "metadata"):
            if field not in node:
                raise ValueError(f"node missing {field}")
        if node.get("path") is not None:
            validate_path(node["path"])
    node_ids = [node["id"] for node in data["nodes"]]
    if len(node_ids) != len(set(node_ids)):
        raise ValueError("duplicate node ids are not allowed")
    for edge in data["edges"]:
        for field in ("source", "target", "type", "confidence", "evidence", "metadata"):
            if field not in edge:
                raise ValueError(f"edge missing {field}")
        if edge["confidence"] not in CONFIDENCE_VALUES:
            raise ValueError(f"invalid confidence: {edge['confidence']}")
        if not edge["evidence"]:
            raise ValueError("edge evidence is required")
        for evidence in edge["evidence"]:
            validate_path(evidence["path"])
            if "kind" not in evidence:
                raise ValueError("evidence kind is required")
    edge_keys = [serialized_edge_key(edge) for edge in data["edges"]]
    if len(edge_keys) != len(set(edge_keys)):
        raise ValueError("duplicate edges are not allowed")
    for unsupported in data["unsupported"]:
        for field in ("path", "language", "reason", "phase", "message"):
            if field not in unsupported:
                raise ValueError(f"unsupported missing {field}")
        validate_path(unsupported["path"])


def validate_path(path: str) -> None:
    if Path(path).is_absolute() or path.startswith("/") or "\\" in path:
        raise ValueError(f"absolute path or non-POSIX path is not allowed: {path}")


def serialized_edge_key(edge: dict[str, Any]) -> str:
    return json.dumps(
        {
            "source": edge["source"],
            "target": edge["target"],
            "type": edge["type"],
            "confidence": edge["confidence"],
            "evidence": edge["evidence"],
            "metadata": edge["metadata"],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
