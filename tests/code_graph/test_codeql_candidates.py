import json

import pytest

from repo_graph.builders.graph_builder import build_graph
from repo_graph.generate import main as generate_main
from repo_graph.orchestrator import generate_output_texts
from repo_graph.model import (
    CodeqlCandidateRelation,
    CodeqlSymbol,
    Evidence,
    SourceFile,
)
from repo_graph.parsers.codeql_candidates import (
    load_codeql_candidate_relations,
    parse_codeql_candidate_text,
)
from repo_graph.parsers.registry import ManifestParserRegistry, ParserRegistry
from repo_graph.plugins.registry import default_manifest_parser_registry, default_parser_registry
from repo_graph.writers.json_writer import graph_to_json_text
from repo_graph.writers.summary_writer import graph_to_summary_text


pytestmark = pytest.mark.unit


def test_codeql_candidate_adapter_normalizes_paths_and_drops_external_rows(tmp_path):
    repo_root = tmp_path
    known_paths = [
        "server/routers/auth.py",
        "server/services/auth_service.py",
        "server/services/db_proxy.py",
    ]
    for path in known_paths:
        (repo_root / path).parent.mkdir(parents=True, exist_ok=True)
        (repo_root / path).write_text("", encoding="utf-8")

    payload = {
        "schemaVersion": 1,
        "provider": "codeql",
        "relations": [
            {
                "type": "data_flows_to",
                "queryId": "fixture/python/request-to-db",
                "queryName": "Request field reaches DB write candidate",
                "source": {
                    "path": "server/routers/auth.py",
                    "qualifiedName": "server.routers.auth.login.email",
                    "kind": "parameter",
                    "line": 12,
                },
                "target": {
                    "path": f"file://{repo_root / 'server/services/db_proxy.py'}",
                    "qualifiedName": "server.services.db_proxy.execute_query",
                    "kind": "function",
                    "line": 44,
                },
                "evidence": {
                    "path": str(repo_root / "server/routers/auth.py"),
                    "kind": "codeql_data_flow",
                    "line": 12,
                },
            },
            {
                "type": "calls",
                "queryId": "fixture/python/calls",
                "source": {
                    "path": str(repo_root / "server/routers/auth.py"),
                    "qualifiedName": "server.routers.auth.login",
                    "kind": "function",
                    "line": 10,
                },
                "target": {
                    "path": "server/services/auth_service.py",
                    "qualifiedName": "server.services.auth_service.login_with_password",
                    "kind": "function",
                    "line": 20,
                },
                "evidence": {
                    "path": "server/routers/auth.py",
                    "kind": "codeql_call",
                    "line": 13,
                },
                "confidence": "medium",
            },
            {
                "type": "calls",
                "queryId": "fixture/python/calls",
                "source": {
                    "path": "server/generated/not_tracked.py",
                    "qualifiedName": "server.generated.not_tracked.call",
                },
                "target": {
                    "path": "server/services/auth_service.py",
                    "qualifiedName": "server.services.auth_service.login_with_password",
                },
                "evidence": {
                    "path": "server/generated/not_tracked.py",
                    "kind": "codeql_call",
                },
            },
            {
                "type": "calls",
                "queryId": "fixture/python/calls",
                "source": {
                    "path": "/outside/repo.py",
                    "qualifiedName": "outside.call",
                },
                "target": {
                    "path": "server/services/auth_service.py",
                    "qualifiedName": "server.services.auth_service.login_with_password",
                },
                "evidence": {
                    "path": "/outside/repo.py",
                    "kind": "codeql_call",
                },
            },
        ],
    }

    relations = parse_codeql_candidate_text(
        json.dumps(payload),
        repo_root=repo_root,
        known_paths=known_paths,
    )

    assert [relation.relation_type for relation in relations] == ["calls", "data_flows_to"]
    assert relations[0].source.path == "server/routers/auth.py"
    assert relations[0].target.path == "server/services/auth_service.py"
    assert relations[0].evidence == Evidence(
        path="server/routers/auth.py",
        kind="codeql_call",
        line=13,
    )
    assert relations[1].target.path == "server/services/db_proxy.py"
    assert relations[1].query_name == "Request field reaches DB write candidate"


def test_codeql_candidate_adapter_rejects_boolean_line_values(tmp_path):
    payload = {
        "relations": [
            {
                "type": "calls",
                "queryId": "fixture/python/calls",
                "source": {
                    "path": "server/routers/auth.py",
                    "qualifiedName": "server.routers.auth.login",
                    "line": True,
                },
                "target": {
                    "path": "server/services/auth_service.py",
                    "qualifiedName": "server.services.auth_service.login_with_password",
                },
                "evidence": {
                    "path": "server/routers/auth.py",
                    "kind": "codeql_call",
                    "line": 13,
                },
            }
        ],
    }

    with pytest.raises(ValueError, match="line"):
        parse_codeql_candidate_text(
            json.dumps(payload),
            repo_root=tmp_path,
            known_paths=[
                "server/routers/auth.py",
                "server/services/auth_service.py",
            ],
        )


@pytest.mark.parametrize("line", [0, -1])
def test_codeql_candidate_adapter_rejects_non_positive_line_values(tmp_path, line):
    payload = {
        "relations": [
            {
                "type": "calls",
                "queryId": "fixture/python/calls",
                "source": {
                    "path": "server/routers/auth.py",
                    "qualifiedName": "server.routers.auth.login",
                    "line": line,
                },
                "target": {
                    "path": "server/services/auth_service.py",
                    "qualifiedName": "server.services.auth_service.login_with_password",
                },
                "evidence": {
                    "path": "server/routers/auth.py",
                    "kind": "codeql_call",
                    "line": 13,
                },
            }
        ],
    }

    with pytest.raises(ValueError, match="line"):
        parse_codeql_candidate_text(
            json.dumps(payload),
            repo_root=tmp_path,
            known_paths=[
                "server/routers/auth.py",
                "server/services/auth_service.py",
            ],
        )


def test_codeql_candidate_missing_optional_artifact_is_noop(tmp_path):
    relations = load_codeql_candidate_relations(
        None,
        repo_root=tmp_path,
        known_paths=["server/main.py"],
    )

    assert relations == []


def test_explicit_codeql_candidate_missing_artifact_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="does not exist"):
        load_codeql_candidate_relations(
            tmp_path / "missing-codeql-candidates.json",
            repo_root=tmp_path,
            known_paths=["server/main.py"],
        )


def test_codeql_results_cli_requires_local_output_and_summary(tmp_path):
    candidate_path = tmp_path / "codeql-candidates.json"

    assert generate_main(["--codeql-results", str(candidate_path)]) == 2
    assert (
        generate_main(
            [
                "--codeql-results",
                str(candidate_path),
                "--output",
                "analysis/code_graph/repo_graph.json",
                "--summary",
                "analysis/code_graph/repo_graph.local.summary.md",
            ]
        )
        == 2
    )
    assert (
        generate_main(
            [
                "--codeql-results",
                str(candidate_path),
                "--output",
                "analysis/code_graph/repo_graph.local.json",
                "--summary",
                "analysis/code_graph/repo_graph.summary.md",
            ]
        )
        == 2
    )


def test_codeql_results_cli_reports_missing_explicit_results(tmp_path, monkeypatch):
    write_minimal_git_repo(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert (
        generate_main(
            [
                "--allow-legacy-defaults",
                "--codeql-results",
                "missing-codeql-candidates.json",
                "--output",
                "analysis/code_graph/repo_graph.local.json",
                "--summary",
                "analysis/code_graph/repo_graph.local.summary.md",
            ]
        )
        == 2
    )


def test_generate_check_mode_does_not_create_missing_artifact_dirs(tmp_path, monkeypatch):
    write_minimal_git_repo(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert (
        generate_main(
            [
                "--allow-legacy-defaults",
                "--check",
                "--output",
                "generated/code_graph/repo_graph.json",
                "--schema-output",
                "generated/code_graph/repo_graph.schema.json",
                "--summary",
                "generated/code_graph/repo_graph.summary.md",
            ]
        )
        == 1
    )
    assert not (tmp_path / "generated").exists()


def test_generate_output_texts_only_merges_codeql_candidates_when_explicit(tmp_path):
    write_minimal_git_repo(tmp_path)

    candidate_path = tmp_path / "codeql-candidates.json"
    candidate_path.write_text(
        json.dumps(
            {
                "relations": [
                    {
                        "type": "calls",
                        "queryId": "fixture/python/calls",
                        "source": {
                            "path": "server/routers/auth.py",
                            "qualifiedName": "server.routers.auth.login",
                            "kind": "function",
                            "line": 10,
                        },
                        "target": {
                            "path": "server/services/auth_service.py",
                            "qualifiedName": "server.services.auth_service.login_with_password",
                            "kind": "function",
                            "line": 20,
                        },
                        "evidence": {
                            "path": "server/routers/auth.py",
                            "kind": "codeql_call",
                            "line": 13,
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    default_data = json.loads(
        generate_output_texts(
            repo_root=tmp_path,
            parser_registry_factory=default_parser_registry,
            manifest_registry=default_manifest_parser_registry(),
        )[tmp_path / "analysis/code_graph/repo_graph.json"]
    )
    explicit_data = json.loads(
        generate_output_texts(
            repo_root=tmp_path,
            codeql_results_path=candidate_path,
            codeql_relation_loader=load_codeql_candidate_relations,
            parser_registry_factory=default_parser_registry,
            manifest_registry=default_manifest_parser_registry(),
        )[tmp_path / "analysis/code_graph/repo_graph.json"]
    )

    assert not any(node["type"] == "code_symbol" for node in default_data["nodes"])
    assert not any(edge["type"] == "calls" for edge in default_data["edges"])
    assert any(node["type"] == "code_symbol" for node in explicit_data["nodes"])
    call_edge = next(edge for edge in explicit_data["edges"] if edge["type"] == "calls")
    assert call_edge["metadata"] == {
        "candidate": True,
        "provider": "codeql",
        "queryId": "fixture/python/calls",
    }


def test_generate_output_texts_requires_explicit_orchestration_dependencies(tmp_path):
    write_minimal_git_repo(tmp_path)

    with pytest.raises(ValueError, match="parser_registry_factory is required"):
        generate_output_texts(repo_root=tmp_path)


def test_generate_output_texts_requires_codeql_loader_for_codeql_path(tmp_path):
    write_minimal_git_repo(tmp_path)
    candidate_path = tmp_path / "codeql-candidates.json"
    candidate_path.write_text('{"relations": []}', encoding="utf-8")

    with pytest.raises(ValueError, match="codeql_results_path requires codeql_relation_loader"):
        generate_output_texts(
            repo_root=tmp_path,
            codeql_results_path=candidate_path,
            parser_registry_factory=default_parser_registry,
            manifest_registry=default_manifest_parser_registry(),
        )


def test_codeql_candidate_relations_create_deterministic_graph_edges(tmp_path):
    source_files = [
        SourceFile(path="server/routers/auth.py", language="python"),
        SourceFile(path="server/services/auth_service.py", language="python"),
        SourceFile(path="server/services/db_proxy.py", language="python"),
    ]
    for source in source_files:
        (tmp_path / source.path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / source.path).write_text("", encoding="utf-8")

    call_relation = CodeqlCandidateRelation(
        source=CodeqlSymbol(
            path="server/routers/auth.py",
            qualified_name="server.routers.auth.login",
            kind="function",
            line=10,
        ),
        target=CodeqlSymbol(
            path="server/services/auth_service.py",
            qualified_name="server.services.auth_service.login_with_password",
            kind="function",
            line=20,
        ),
        relation_type="calls",
        confidence="medium",
        evidence=Evidence(path="server/routers/auth.py", kind="codeql_call", line=13),
        query_id="fixture/python/calls",
    )
    data_flow_relation = CodeqlCandidateRelation(
        source=CodeqlSymbol(
            path="server/routers/auth.py",
            qualified_name="server.routers.auth.login.email",
            kind="parameter",
            line=12,
        ),
        target=CodeqlSymbol(
            path="server/services/db_proxy.py",
            qualified_name="server.services.db_proxy.execute_query",
            kind="function",
            line=44,
        ),
        relation_type="data_flows_to",
        confidence="medium",
        evidence=Evidence(path="server/routers/auth.py", kind="codeql_data_flow", line=12),
        query_id="fixture/python/request-to-db",
        query_name="Request field reaches DB write candidate",
    )

    graph = build_graph(
        repo_root=tmp_path,
        source_files=source_files,
        parser_registry=ParserRegistry(parsers={}),
        manifest_registry=ManifestParserRegistry({}),
        codeql_relations=[call_relation, call_relation, data_flow_relation],
    )
    json_text = graph_to_json_text(graph)
    data = json.loads(json_text)
    edge_keys = [(edge["source"], edge["target"], edge["type"]) for edge in data["edges"]]
    summary = graph_to_summary_text(graph)

    assert edge_keys.count(
        (
            "code_symbol:server/routers/auth.py:server.routers.auth.login@10",
            "code_symbol:server/services/auth_service.py:server.services.auth_service.login_with_password@20",
            "calls",
        )
    ) == 1
    data_flow_edge = next(edge for edge in data["edges"] if edge["type"] == "data_flows_to")
    assert data_flow_edge["metadata"] == {
        "candidate": True,
        "provider": "codeql",
        "queryId": "fixture/python/request-to-db",
        "queryName": "Request field reaches DB write candidate",
    }
    assert all("/home/example/" not in json.dumps(item) for item in data["nodes"] + data["edges"])
    assert "- `calls`: 1" in summary
    assert "- `data_flows_to`: 1" in summary


def run_git(repo_root, *args):
    import subprocess

    subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def write_minimal_git_repo(repo_root):
    (repo_root / "server/routers").mkdir(parents=True)
    (repo_root / "server/services").mkdir(parents=True)
    (repo_root / "server/routers/auth.py").write_text(
        "from server.services import auth_service\n",
        encoding="utf-8",
    )
    (repo_root / "server/services/auth_service.py").write_text("", encoding="utf-8")
    run_git(repo_root, "init")
    run_git(repo_root, "config", "user.email", "code-graph@example.test")
    run_git(repo_root, "config", "user.name", "Code Graph Test")
    run_git(repo_root, "add", "server/routers/auth.py", "server/services/auth_service.py")
