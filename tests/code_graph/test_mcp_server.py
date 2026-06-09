import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from repo_graph.mcp_server import CodeGraphMcpServer, ReloadingCodeGraphQueryServiceProvider
from repo_graph.query import CodeGraphQueryService
from repo_graph.usage_logging import QueryUsageLogger


pytestmark = pytest.mark.unit


def test_query_service_finds_graph_neighbors_for_path_tools():
    service = CodeGraphQueryService(sample_graph())

    impacted = service.find_impacted_files("server/routers/auth.py")
    tests = service.find_tests_for("server/routers/auth.py")
    endpoints = service.find_endpoints_for_router("server/routers/auth.py")
    mounted_endpoints = service.find_endpoints_for_router("server/main.py")
    routes = service.find_routes_for_view("client/src/views/HomeView.vue")
    metadata_routes = service.find_routes_for_view("client/src/views/SettingsView.vue")
    api_callers = service.find_api_callers_for_endpoint("POST /api/auth/login")
    e2e_route = service.find_e2e_for_route("/")
    e2e_view = service.find_e2e_for_view("client/src/views/HomeView.vue")

    assert [item["path"] for item in impacted["files"]] == [
        "server/main.py",
        "tests/test_auth.py",
    ]
    assert impacted["total"] == 2
    assert impacted["truncated"] is False
    assert impacted["note"].startswith("Impact candidates")
    assert [item["path"] for item in tests["tests"]] == ["tests/test_auth.py"]
    assert [item["id"] for item in endpoints["endpoints"]] == ["api:POST /api/auth/login"]
    assert mounted_endpoints["endpoints"][0]["via"] == "registered_router"
    assert mounted_endpoints["endpoints"][0]["routerPath"] == "server/routers/auth.py"
    assert [item["metadata"]["routeName"] for item in routes["routes"]] == ["Home"]
    assert metadata_routes["routes"][0]["match"] == "metadata_component_path"
    assert metadata_routes["routes"][0]["routeName"] == "Settings"
    assert [item["path"] for item in api_callers["files"]] == ["client/src/views/LoginView.vue"]
    assert api_callers["files"][0]["edgeType"] == "calls_api_endpoint"
    assert api_callers["note"].startswith("Frontend API call edges")
    assert [item["path"] for item in e2e_route["tests"]] == ["client/e2e/home-smoke.js"]
    assert e2e_route["tests"][0]["routes"][0]["routePath"] == "/"
    assert [item["path"] for item in e2e_view["tests"]] == ["client/e2e/home-smoke.js"]
    assert e2e_view["note"].startswith("E2E view results")


def test_query_service_includes_codeql_candidate_edges_in_impact_results():
    graph = sample_graph()
    graph["nodes"].extend(
        [
            {
                "id": "code_symbol:server/routers/auth.py:login@20",
                "type": "code_symbol",
                "name": "login",
                "path": "server/routers/auth.py",
                "language": "python",
                "metadata": {},
            },
            {
                "id": "code_symbol:server/services/auth_service.py:authenticate@8",
                "type": "code_symbol",
                "name": "authenticate",
                "path": "server/services/auth_service.py",
                "language": "python",
                "metadata": {},
            },
        ]
    )
    graph["edges"].append(
        {
            "source": "code_symbol:server/services/auth_service.py:authenticate@8",
            "target": "code_symbol:server/routers/auth.py:login@20",
            "type": "calls",
            "confidence": "medium",
            "evidence": [{"path": "server/services/auth_service.py", "kind": "codeql_calls", "line": 8}],
            "metadata": {"provider": "codeql", "candidate": True},
        }
    )
    service = CodeGraphQueryService(graph)

    impacted = service.find_impacted_files("server/routers/auth.py")

    assert [item["path"] for item in impacted["files"]] == [
        "server/main.py",
        "server/services/auth_service.py",
        "tests/test_auth.py",
    ]
    codeql_item = impacted["files"][1]
    assert codeql_item["edgeType"] == "calls"
    assert codeql_item["confidence"] == "medium"
    assert codeql_item["matchingEdges"][0]["metadata"] == {"provider": "codeql", "candidate": True}


def test_query_service_explains_node_with_bounded_edges():
    service = CodeGraphQueryService(sample_graph())

    result = service.explain_node("py:server.routers.auth", max_results=1)

    assert result["node"]["path"] == "server/routers/auth.py"
    assert result["incomingTotal"] == 2
    assert result["outgoingTotal"] == 1
    assert result["incomingTruncated"] is True
    assert result["outgoingTruncated"] is False
    assert len(result["incoming"]) == 1
    assert len(result["outgoing"]) == 1
    assert result["incoming"][0]["neighbor"]["path"] in {"server/main.py", "tests/test_auth.py"}
    assert result["outgoing"][0]["edge"]["target"] == "api:POST /api/auth/login"


def test_query_service_bounds_results_and_rejects_unsafe_paths():
    service = CodeGraphQueryService(sample_graph())

    impacted = service.find_impacted_files("./server/routers/auth.py", max_results=1)

    assert impacted["total"] == 2
    assert impacted["truncated"] is True
    assert len(impacted["files"]) == 1
    with pytest.raises(ValueError, match="repo-relative"):
        service.find_tests_for("../server/routers/auth.py")
    with pytest.raises(ValueError, match="repo-relative"):
        service.find_tests_for("/workspace/fixture-repo/server/routers/auth.py")
    with pytest.raises(ValueError, match="repo-relative"):
        service.find_tests_for("server/routers/auth.py/..")
    with pytest.raises(ValueError, match="repo-relative"):
        service.find_tests_for("C:/Projects/FixtureRepo/server/routers/auth.py")


def test_query_service_explains_unique_paths_and_rejects_ambiguous_paths():
    graph = sample_graph()
    graph["nodes"].append(
        {
            "id": "symbol:server.routers.auth.login",
            "type": "code_symbol",
            "name": "login",
            "path": "server/routers/auth.py",
            "language": "python",
            "metadata": {},
        }
    )
    service = CodeGraphQueryService(graph)

    unique = CodeGraphQueryService(sample_graph()).explain_node("server/routers/auth.py")

    assert unique["node"]["id"] == "py:server.routers.auth"
    with pytest.raises(ValueError, match="ambiguous node path"):
        service.explain_node("server/routers/auth.py")


def test_query_service_explains_missing_exact_node_id_as_empty_result():
    service = CodeGraphQueryService(sample_graph())

    result = service.explain_node("py:server.routers.nope")

    assert result["query"]["lookup"] == "py:server.routers.nope"
    assert result["query"]["matchedNodeId"] is None
    assert result["node"] is None
    assert result["incoming"] == []
    assert result["outgoing"] == []


def test_query_service_from_path_validates_graph_contract(tmp_path):
    valid_path = tmp_path / "valid_repo_graph.json"
    invalid_path = tmp_path / "invalid_repo_graph.json"
    valid_path.write_text(json.dumps(sample_graph()), encoding="utf-8")
    invalid_path.write_text(json.dumps({"nodes": []}), encoding="utf-8")

    service = CodeGraphQueryService.from_path(valid_path)

    assert service.explain_node("py:server.main")["node"]["path"] == "server/main.py"
    with pytest.raises(ValueError, match="missing required graph fields"):
        CodeGraphQueryService.from_path(invalid_path)


def test_mcp_server_lists_tools_and_calls_structured_results():
    server = CodeGraphMcpServer(CodeGraphQueryService(sample_graph()))

    initialize = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test"}},
        }
    )
    tools = server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    ping = server.handle_message({"jsonrpc": "2.0", "id": 20, "method": "ping"})
    notification = server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"})
    call = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "find_tests_for",
                "arguments": {"path": "server/routers/auth.py"},
            },
        }
    )
    api_call = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 30,
            "method": "tools/call",
            "params": {
                "name": "find_api_callers_for_endpoint",
                "arguments": {"endpoint": "POST /api/auth/login"},
            },
        }
    )
    e2e_call = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 31,
            "method": "tools/call",
            "params": {
                "name": "find_e2e_for_route",
                "arguments": {"route": "/"},
            },
        }
    )

    assert initialize["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert ping["result"] == {}
    assert notification is None
    tool_names = [tool["name"] for tool in tools["result"]["tools"]]
    assert tool_names == [
        "find_impacted_files",
        "find_tests_for",
        "find_endpoints_for_router",
        "find_routes_for_view",
        "find_api_callers_for_endpoint",
        "find_e2e_for_route",
        "find_e2e_for_view",
        "explain_node",
    ]
    assert all(tool["annotations"]["readOnlyHint"] is True for tool in tools["result"]["tools"])
    assert call["result"]["structuredContent"]["tests"][0]["path"] == "tests/test_auth.py"
    assert json.loads(call["result"]["content"][0]["text"]) == call["result"]["structuredContent"]
    assert api_call["result"]["structuredContent"]["files"][0]["path"] == "client/src/views/LoginView.vue"
    assert e2e_call["result"]["structuredContent"]["tests"][0]["path"] == "client/e2e/home-smoke.js"


def test_mcp_server_writes_opt_in_usage_log(tmp_path):
    log_path = tmp_path / "query_usage.local.jsonl"
    graph_path = tmp_path / "repo_graph.json"
    graph_path.write_text(json.dumps(sample_graph()), encoding="utf-8")
    server = CodeGraphMcpServer(
        CodeGraphQueryService(sample_graph()),
        usage_logger=QueryUsageLogger(enabled=True, log_path=log_path, interface="mcp", session_id="mcp-test"),
        graph_path=graph_path,
    )

    response = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "find_e2e_for_route",
                "arguments": {"route": "/"},
            },
        }
    )

    assert response["result"]["structuredContent"]["tests"][0]["path"] == "client/e2e/home-smoke.js"
    event = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert event["eventType"] == "query_call"
    assert event["interface"] == "mcp"
    assert event["tool"] == "find_e2e_for_route"
    assert event["input"] == {"kind": "route", "value": "/", "normalized": "/"}
    assert event["result"]["count"] == 1
    assert event["result"]["edgeTypes"] == {"e2e_reaches_route": 1}
    assert event["status"] == "ok"


def test_mcp_server_auto_reloads_graph_when_file_mtime_changes(tmp_path):
    graph_path = tmp_path / "repo_graph.json"
    write_graph_with_test_path(graph_path, "tests/test_auth.py")
    provider = ReloadingCodeGraphQueryServiceProvider(graph_path)
    server = CodeGraphMcpServer(provider.current)

    first = call_find_tests_for(server, "server/routers/auth.py", request_id=1)

    write_graph_with_test_path(graph_path, "tests/test_auth_reloaded.py")
    bump_mtime(graph_path)
    second = call_find_tests_for(server, "server/routers/auth.py", request_id=2)

    assert first["result"]["structuredContent"]["tests"][0]["path"] == "tests/test_auth.py"
    assert second["result"]["structuredContent"]["tests"][0]["path"] == "tests/test_auth_reloaded.py"


def test_mcp_server_reports_invalid_auto_reload_as_server_error_without_corrupting_recovery(tmp_path):
    graph_path = tmp_path / "repo_graph.json"
    write_graph_with_test_path(graph_path, "tests/test_auth.py")
    provider = ReloadingCodeGraphQueryServiceProvider(graph_path)
    server = CodeGraphMcpServer(provider.current)

    graph_path.write_text(json.dumps({"nodes": []}), encoding="utf-8")
    bump_mtime(graph_path)
    invalid = call_find_tests_for(server, "server/routers/auth.py", request_id=1)

    write_graph_with_test_path(graph_path, "tests/test_auth_recovered.py")
    bump_mtime(graph_path)
    recovered = call_find_tests_for(server, "server/routers/auth.py", request_id=2)

    assert invalid["error"]["code"] == -32603
    assert "code graph reload failed" in invalid["error"]["message"]
    assert "missing required graph fields" in invalid["error"]["message"]
    assert recovered["result"]["structuredContent"]["tests"][0]["path"] == "tests/test_auth_recovered.py"


def test_mcp_server_reports_malformed_auto_reload_as_server_error(tmp_path):
    graph_path = tmp_path / "repo_graph.json"
    write_graph_with_test_path(graph_path, "tests/test_auth.py")
    provider = ReloadingCodeGraphQueryServiceProvider(graph_path)
    server = CodeGraphMcpServer(provider.current)

    graph_path.write_text("{", encoding="utf-8")
    bump_mtime(graph_path)
    malformed = call_find_tests_for(server, "server/routers/auth.py", request_id=1)

    assert malformed["error"]["code"] == -32603
    assert "code graph reload failed" in malformed["error"]["message"]


def test_mcp_server_reports_missing_auto_reload_as_server_error_and_recovers(tmp_path):
    graph_path = tmp_path / "repo_graph.json"
    write_graph_with_test_path(graph_path, "tests/test_auth.py")
    provider = ReloadingCodeGraphQueryServiceProvider(graph_path)
    server = CodeGraphMcpServer(provider.current)

    graph_path.unlink()
    missing = call_find_tests_for(server, "server/routers/auth.py", request_id=1)

    write_graph_with_test_path(graph_path, "tests/test_auth_recovered.py")
    bump_mtime(graph_path)
    recovered = call_find_tests_for(server, "server/routers/auth.py", request_id=2)

    assert missing["error"]["code"] == -32603
    assert "code graph reload failed" in missing["error"]["message"]
    assert recovered["result"]["structuredContent"]["tests"][0]["path"] == "tests/test_auth_recovered.py"


def test_mcp_initialize_does_not_echo_unsupported_protocol_version():
    server = CodeGraphMcpServer(CodeGraphQueryService(sample_graph()))

    response = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "1900-01-01"},
        }
    )

    assert response["result"]["protocolVersion"] == "2025-06-18"


def test_mcp_server_reports_invalid_tool_arguments_as_json_rpc_error():
    server = CodeGraphMcpServer(CodeGraphQueryService(sample_graph()))

    response = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "find_tests_for", "arguments": {"path": "server/routers/auth.py", "maxResults": 0}},
        }
    )

    assert response["error"]["code"] == -32602
    assert "maxResults" in response["error"]["message"]


def test_mcp_server_script_help_works_from_repo_root():
    repo_root = Path(__file__).resolve().parents[2]

    result = subprocess.run(
        [sys.executable, "repo_graph/mcp_server.py", "--help"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Serve code graph tools" in result.stdout


def test_mcp_server_stdio_reports_parse_error_and_continues(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "repo_graph/mcp_server.py"
    graph_path = tmp_path / "repo_graph.json"
    graph_path.write_text(json.dumps(sample_graph()), encoding="utf-8")
    write_config(tmp_path)
    stdin = '\n'.join(
        [
            "not json",
            json.dumps({"jsonrpc": "2.0", "id": 10, "method": "ping"}),
            "",
        ]
    )

    result = subprocess.run(
        [sys.executable, str(script), "--config", "codegraph.config.toml", "--graph", str(graph_path)],
        cwd=tmp_path,
        input=stdin,
        check=False,
        capture_output=True,
        text=True,
    )

    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert result.returncode == 0
    assert responses[0]["error"]["code"] == -32700
    assert responses[1]["id"] == 10
    assert responses[1]["result"] == {}


def test_mcp_server_config_reads_mcp_default_graph(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "repo_graph/mcp_server.py"
    graph_path = tmp_path / "configured/repo_graph.json"
    graph_path.parent.mkdir()
    graph_path.write_text(json.dumps(sample_graph()), encoding="utf-8")
    write_config(
        tmp_path,
        graph="generated/unused_repo_graph.json",
        mcp_graph="configured/repo_graph.json",
    )
    stdin = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {
                "name": "find_tests_for",
                "arguments": {"path": "server/routers/auth.py"},
            },
        }
    )

    result = subprocess.run(
        [sys.executable, str(script), "--config", "codegraph.config.toml"],
        cwd=tmp_path,
        input=stdin,
        check=False,
        capture_output=True,
        text=True,
    )

    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert result.returncode == 0
    assert responses[0]["result"]["structuredContent"]["tests"][0]["path"] == "tests/test_auth.py"


def test_mcp_server_requires_config_even_with_explicit_graph(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "repo_graph/mcp_server.py"
    graph_path = tmp_path / "repo_graph.json"
    graph_path.write_text(json.dumps(sample_graph()), encoding="utf-8")
    stdin = json.dumps({"jsonrpc": "2.0", "id": 10, "method": "ping"})

    result = subprocess.run(
        [sys.executable, str(script), "--graph", str(graph_path)],
        cwd=tmp_path,
        input=stdin,
        check=False,
        capture_output=True,
        text=True,
        env=env_without_code_graph_config(),
    )

    assert result.returncode == 2
    assert "code graph config was not found" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


def call_find_tests_for(server: CodeGraphMcpServer, path: str, request_id: int) -> dict[str, object]:
    response = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {
                "name": "find_tests_for",
                "arguments": {"path": path},
            },
        }
    )
    assert response is not None
    return response


def write_graph_with_test_path(graph_path: Path, test_path: str) -> None:
    graph = sample_graph()
    for node in graph["nodes"]:
        if node["id"] == "py:tests.test_auth":
            node["path"] = test_path
            node["name"] = test_path.removesuffix(".py").replace("/", ".")
    graph_path.write_text(json.dumps(graph), encoding="utf-8")


def bump_mtime(path: Path) -> None:
    current = path.stat().st_mtime_ns
    os.utime(path, ns=(current + 1_000_000_000, current + 1_000_000_000))


def env_without_code_graph_config() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("CODE_GRAPH_CONFIG", None)
    return env


def sample_graph():
    return {
        "schemaVersion": 1,
        "generatorVersion": "code-graph@0.1.0",
        "pluginVersion": "repo-relationship-graph@0.1.0",
        "repo": {"name": "FixtureRepo", "root": "."},
        "sourceManifest": [],
        "nodes": [
            {
                "id": "py:server.main",
                "type": "python_module",
                "name": "server.main",
                "path": "server/main.py",
                "language": "python",
                "metadata": {},
            },
            {
                "id": "py:server.routers.auth",
                "type": "python_module",
                "name": "server.routers.auth",
                "path": "server/routers/auth.py",
                "language": "python",
                "metadata": {},
            },
            {
                "id": "py:tests.test_auth",
                "type": "test_file",
                "name": "tests.test_auth",
                "path": "tests/test_auth.py",
                "language": "python",
                "metadata": {},
            },
            {
                "id": "api:POST /api/auth/login",
                "type": "fastapi_endpoint",
                "name": "POST /api/auth/login",
                "path": None,
                "language": "virtual",
                "metadata": {"method": "POST", "path": "/api/auth/login"},
            },
            {
                "id": "file:client/src/views/HomeView.vue",
                "type": "file",
                "name": "client/src/views/HomeView.vue",
                "path": "client/src/views/HomeView.vue",
                "language": "vue",
                "metadata": {},
            },
            {
                "id": "file:client/src/views/LoginView.vue",
                "type": "file",
                "name": "client/src/views/LoginView.vue",
                "path": "client/src/views/LoginView.vue",
                "language": "vue",
                "metadata": {},
            },
            {
                "id": "file:client/e2e/home-smoke.js",
                "type": "test_file",
                "name": "client/e2e/home-smoke.js",
                "path": "client/e2e/home-smoke.js",
                "language": "javascript",
                "metadata": {},
            },
            {
                "id": "vue_route:client/src/router/index.js:/#Home",
                "type": "vue_route",
                "name": "Home",
                "path": None,
                "language": "virtual",
                "metadata": {
                    "routeName": "Home",
                    "routePath": "/",
                    "routeSourcePath": "client/src/router/index.js",
                    "componentPath": "client/src/views/HomeView.vue",
                },
            },
            {
                "id": "vue_route:client/src/router/index.js:/settings#Settings",
                "type": "vue_route",
                "name": "Settings",
                "path": None,
                "language": "virtual",
                "metadata": {
                    "routeName": "Settings",
                    "routePath": "/settings",
                    "routeSourcePath": "client/src/router/index.js",
                    "componentPath": "client/src/views/SettingsView.vue",
                },
            },
        ],
        "edges": [
            {
                "source": "py:server.main",
                "target": "py:server.routers.auth",
                "type": "registers_router",
                "confidence": "high",
                "evidence": [{"path": "server/main.py", "kind": "fastapi_include_router", "line": 12}],
                "metadata": {"prefix": "/api"},
            },
            {
                "source": "py:tests.test_auth",
                "target": "py:server.routers.auth",
                "type": "tests",
                "confidence": "high",
                "evidence": [{"path": "tests/test_auth.py", "kind": "ast_import", "line": 1}],
                "metadata": {"reason": "test_import"},
            },
            {
                "source": "py:server.routers.auth",
                "target": "api:POST /api/auth/login",
                "type": "exposes_endpoint",
                "confidence": "high",
                "evidence": [{"path": "server/routers/auth.py", "kind": "fastapi_decorator", "line": 20}],
                "metadata": {"routerName": "router"},
            },
            {
                "source": "file:client/src/views/LoginView.vue",
                "target": "api:POST /api/auth/login",
                "type": "calls_api_endpoint",
                "confidence": "high",
                "evidence": [{"path": "client/src/views/LoginView.vue", "kind": "frontend_api_call", "line": 12}],
                "metadata": {
                    "method": "POST",
                    "path": "/api/auth/login",
                    "callKind": "request",
                    "candidate": True,
                    "matchedBy": "method_path",
                },
            },
            {
                "source": "vue_route:client/src/router/index.js:/#Home",
                "target": "file:client/src/views/HomeView.vue",
                "type": "renders_view",
                "confidence": "high",
                "evidence": [{"path": "client/src/router/index.js", "kind": "vue_router_route", "line": 10}],
                "metadata": {"routeName": "Home", "routePath": "/", "routeSourcePath": "client/src/router/index.js"},
            },
            {
                "source": "file:client/e2e/home-smoke.js",
                "target": "vue_route:client/src/router/index.js:/#Home",
                "type": "e2e_reaches_route",
                "confidence": "high",
                "evidence": [{"path": "client/e2e/home-smoke.js", "kind": "playwright_page_goto", "line": 5}],
                "metadata": {"routePath": "/", "candidate": True},
            },
        ],
        "unsupported": [],
    }


def write_config(
    repo_root: Path,
    *,
    graph: str = "analysis/code_graph/repo_graph.json",
    mcp_graph: str | None = None,
) -> Path:
    config_path = repo_root / "codegraph.config.toml"
    mcp_graph = mcp_graph or graph
    config_path.write_text(
        f"""
schema_version = 1

[project]
name = "McpFixture"
root = "."

[outputs]
graph = "{graph}"
schema = "generated/repo_graph.schema.json"
summary = "generated/repo_graph.summary.md"
usage_dir = "generated/usage"

[mcp]
default_graph = "{mcp_graph}"

[discovery]
include_suffixes = [".py", ".json"]
exclude_prefixes = ["analysis/code_graph/", "generated/"]

[plugins.python_ast]
enabled = true
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return config_path
