import json
from pathlib import Path

import pytest

from repo_graph import orchestrator
from repo_graph.checks.freshness import compare_generated_outputs
from repo_graph.model import Edge, Evidence, Graph, Node, SourceFile, UnsupportedRecord
from repo_graph.writers.json_writer import (
    GraphOutputMetadata,
    build_schema_document,
    graph_to_dict,
    graph_to_json_text,
    serialized_edge_key,
    validate_graph_contract,
)
from repo_graph.writers.summary_writer import graph_to_summary_text
from repo_graph.version import GENERATOR_VERSION, PLUGIN_VERSION


pytestmark = pytest.mark.unit


def _sample_graph() -> Graph:
    return Graph(
        source_manifest=[
            SourceFile(path="server/main.py", language="python"),
            SourceFile(path="docs/index.md", language="markdown", role="inventory_only"),
            SourceFile(path="client/src/App.vue", language="vue"),
        ],
        nodes=[
            Node(
                id="py:server.main",
                type="python_module",
                name="server.main",
                path="server/main.py",
                language="python",
            ),
            Node(
                id="api:GET /api/health",
                type="fastapi_endpoint",
                name="GET /api/health",
                path=None,
                language="virtual",
            ),
        ],
        edges=[
            Edge(
                source="py:server.main",
                target="api:GET /api/health",
                type="exposes_endpoint",
                confidence="high",
                evidence=[Evidence(path="server/main.py", line=7, kind="fastapi_decorator")],
            )
        ],
        unsupported=[
            UnsupportedRecord(
                path="client/src/App.vue",
                language="vue",
                reason="parser_not_enabled",
                phase="mvp_v0_1",
                message="JS/Vue graph is deferred to Phase 2",
            )
        ],
    )


def test_json_writer_is_deterministic_and_omits_environment_specific_metadata(tmp_path):
    graph = _sample_graph()
    graph.edges.append(
        Edge(
            source="py:server.main",
            target="api:GET /api/health",
            type="exposes_endpoint",
            confidence="high",
            evidence=[Evidence(path="zz_extra.py", line=1, kind="extra")],
        )
    )
    graph.edges.append(
        Edge(
            source="py:server.main",
            target="api:GET /api/health",
            type="exposes_endpoint",
            confidence="high",
            evidence=[Evidence(path="aa_extra.py", line=1, kind="extra")],
        )
    )

    first = graph_to_json_text(graph)
    second = graph_to_json_text(graph)
    data = json.loads(first)

    assert first == second
    assert "gitCommit" not in first
    assert "dirty" not in first
    assert "generated_at" not in first
    assert str(tmp_path) not in first
    assert "/home/example/" not in first
    assert data["repo"] == {"name": "repository", "root": "."}
    assert data["generatorVersion"] == GENERATOR_VERSION
    assert data["pluginVersion"] == PLUGIN_VERSION
    assert [node["id"] for node in data["nodes"]] == sorted(node["id"] for node in data["nodes"])
    assert [serialized_edge_key(edge) for edge in data["edges"]] == sorted(
        serialized_edge_key(edge) for edge in data["edges"]
    )
    validate_graph_contract(data)


def test_json_writer_accepts_configured_graph_metadata():
    graph = _sample_graph()

    data = graph_to_dict(
        graph,
        metadata=GraphOutputMetadata(
            repo_name="AnotherRepo",
            repo_root=".",
            generator_version="code-graph@9.8.7",
            plugin_version="repo-relationship-graph@1.2.3",
        ),
    )

    assert data["repo"] == {"name": "AnotherRepo", "root": "."}
    assert data["generatorVersion"] == "code-graph@9.8.7"
    assert data["pluginVersion"] == "repo-relationship-graph@1.2.3"
    validate_graph_contract(data)


def test_schema_document_and_summary_are_generated_from_graph():
    graph = _sample_graph()
    schema = build_schema_document()
    summary = graph_to_summary_text(graph)

    assert schema["type"] == "object"
    assert schema["title"] == "Repository Code Graph"
    assert "schemaVersion" in schema["required"]
    assert "pluginVersion" in schema["required"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["sourceManifest"]["items"]["required"] == ["path", "language", "role"]
    assert schema["properties"]["sourceManifest"]["items"]["properties"]["role"]["enum"] == [
        "inventory_only",
        "manifest",
        "source",
    ]
    assert schema["properties"]["nodes"]["items"]["required"] == [
        "id",
        "type",
        "name",
        "path",
        "language",
        "metadata",
    ]
    assert schema["properties"]["edges"]["items"]["required"] == [
        "source",
        "target",
        "type",
        "confidence",
        "evidence",
        "metadata",
    ]
    assert schema["properties"]["edges"]["items"]["properties"]["confidence"]["enum"] == ["high", "medium", "low"]
    assert schema["properties"]["edges"]["items"]["properties"]["evidence"]["items"]["required"] == [
        "path",
        "kind",
        "line",
    ]
    assert schema["properties"]["unsupported"]["items"]["required"] == [
        "path",
        "language",
        "reason",
        "phase",
        "message",
        "line",
    ]
    assert "DO NOT EDIT" in summary
    assert "Parsed/processed files: 2" in summary
    assert "Inventory-only files: 1" in summary
    assert "python_module" in summary
    assert "parser_not_enabled" in summary
    validate_graph_contract(graph_to_dict(graph))


def test_freshness_check_reports_stale_outputs_without_writing_existing_files(tmp_path):
    existing = tmp_path / "repo_graph.json"
    existing.write_text("old", encoding="utf-8")

    stale = compare_generated_outputs({existing: "new"})

    assert stale == [existing]
    assert existing.read_text(encoding="utf-8") == "old"

    existing.write_text("new", encoding="utf-8")
    assert compare_generated_outputs({existing: "new"}) == []


def test_write_outputs_creates_parent_dirs_with_default_file_mode(tmp_path):
    target = tmp_path / "nested" / "repo_graph.json"

    orchestrator.write_outputs({target: "new\n"})

    assert target.read_text(encoding="utf-8") == "new\n"
    assert file_mode(target) == 0o644


def test_write_outputs_uses_same_directory_atomic_replace_and_preserves_existing_mode(tmp_path, monkeypatch):
    target = tmp_path / "repo_graph.json"
    target.write_text("old\n", encoding="utf-8")
    target.chmod(0o640)
    real_replace = orchestrator.os.replace
    replace_calls: list[tuple[Path, Path, str]] = []

    def recording_replace(source: str, destination: str) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        replace_calls.append((source_path, destination_path, source_path.read_text(encoding="utf-8")))
        real_replace(source, destination)

    monkeypatch.setattr(orchestrator.os, "replace", recording_replace)

    orchestrator.write_outputs({target: "new\n"})

    assert target.read_text(encoding="utf-8") == "new\n"
    assert file_mode(target) == 0o640
    assert len(replace_calls) == 1
    assert replace_calls[0][0].parent == target.parent
    assert replace_calls[0][0].name.startswith(f".{target.name}.")
    assert replace_calls[0][1:] == (target, "new\n")
    assert not list(target.parent.glob(f".{target.name}.*.tmp"))


def test_write_outputs_keeps_existing_file_and_cleans_temp_when_replace_fails(tmp_path, monkeypatch):
    target = tmp_path / "repo_graph.json"
    target.write_text("old\n", encoding="utf-8")
    temp_paths: list[Path] = []

    def failing_replace(source: str, destination: str) -> None:
        temp_paths.append(Path(source))
        assert Path(destination) == target
        assert Path(source).read_text(encoding="utf-8") == "new\n"
        raise OSError("replace failed")

    monkeypatch.setattr(orchestrator.os, "replace", failing_replace)

    with pytest.raises(OSError, match="replace failed"):
        orchestrator.write_outputs({target: "new\n"})

    assert target.read_text(encoding="utf-8") == "old\n"
    assert temp_paths
    assert all(not path.exists() for path in temp_paths)
    assert not list(target.parent.glob(f".{target.name}.*.tmp"))


def test_contract_rejects_absolute_paths_and_missing_evidence():
    good = graph_to_dict(_sample_graph())
    good["nodes"][0]["path"] = "/tmp/server/main.py"
    with pytest.raises(ValueError, match="absolute path"):
        validate_graph_contract(good)

    bad_edge = graph_to_dict(_sample_graph())
    bad_edge["edges"][0]["evidence"] = []
    with pytest.raises(ValueError, match="evidence"):
        validate_graph_contract(bad_edge)

    duplicate_node = graph_to_dict(_sample_graph())
    duplicate_node["nodes"].append(dict(duplicate_node["nodes"][0]))
    with pytest.raises(ValueError, match="duplicate node"):
        validate_graph_contract(duplicate_node)

    duplicate_edge = graph_to_dict(_sample_graph())
    duplicate_edge["edges"].append(dict(duplicate_edge["edges"][0]))
    with pytest.raises(ValueError, match="duplicate edge"):
        validate_graph_contract(duplicate_edge)

    bad_role = graph_to_dict(_sample_graph())
    bad_role["sourceManifest"][0]["role"] = "unknown"
    with pytest.raises(ValueError, match="invalid source file role"):
        validate_graph_contract(bad_role)

    missing_plugin_version = graph_to_dict(_sample_graph())
    del missing_plugin_version["pluginVersion"]
    with pytest.raises(ValueError, match="missing required graph fields"):
        validate_graph_contract(missing_plugin_version)


def test_graph_to_json_text_runs_contract_validation():
    graph = _sample_graph()
    graph.edges[0] = Edge(
        source="py:server.main",
        target="api:GET /api/health",
        type="exposes_endpoint",
        confidence="high",
        evidence=[],
    )

    with pytest.raises(ValueError, match="evidence"):
        graph_to_json_text(graph)


def file_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777
