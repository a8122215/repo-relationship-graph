import json
import os
import subprocess
import sys
from pathlib import Path


def test_query_cli_text_output_returns_bounded_result_not_raw_graph(tmp_path):
    graph_path = tmp_path / "repo_graph.json"
    graph_path.write_text(json.dumps(sample_graph()), encoding="utf-8")
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "repo_graph/query_cli.py"
    write_config(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--config",
            "codegraph.config.toml",
            "--graph",
            str(graph_path),
            "tests-for",
            "server/routers/auth.py",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "tests/test_auth.py" in result.stdout
    assert '"nodes"' not in result.stdout
    assert "schemaVersion" not in result.stdout


def test_query_cli_json_output_returns_query_result_not_raw_graph(tmp_path):
    graph_path = tmp_path / "repo_graph.json"
    graph_path.write_text(json.dumps(sample_graph()), encoding="utf-8")
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "repo_graph/query_cli.py"
    write_config(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--config",
            "codegraph.config.toml",
            "--graph",
            str(graph_path),
            "--format",
            "json",
            "routes-for-view",
            "client/src/views/HomeView.vue",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["routes"][0]["routePath"] == "/"
    assert "nodes" not in payload
    assert "edges" not in payload


def test_query_cli_api_callers_for_endpoint_text_output(tmp_path):
    graph_path = tmp_path / "repo_graph.json"
    graph_path.write_text(json.dumps(sample_graph()), encoding="utf-8")
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "repo_graph/query_cli.py"
    write_config(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--config",
            "codegraph.config.toml",
            "--graph",
            str(graph_path),
            "api-callers-for-endpoint",
            "POST /api/auth/login",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "client/src/views/LoginView.vue" in result.stdout
    assert '"nodes"' not in result.stdout
    assert "schemaVersion" not in result.stdout


def test_query_cli_e2e_for_view_json_output(tmp_path):
    graph_path = tmp_path / "repo_graph.json"
    graph_path.write_text(json.dumps(sample_graph()), encoding="utf-8")
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "repo_graph/query_cli.py"
    write_config(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--config",
            "codegraph.config.toml",
            "--graph",
            str(graph_path),
            "--format",
            "json",
            "e2e-for-view",
            "client/src/views/HomeView.vue",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["tests"][0]["path"] == "client/e2e/home-smoke.js"
    assert "nodes" not in payload
    assert "edges" not in payload


def test_query_cli_config_reads_configured_graph_and_usage_log(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "repo_graph/query_cli.py"
    graph_path = tmp_path / "generated/repo_graph.json"
    graph_path.parent.mkdir()
    graph_path.write_text(json.dumps(sample_graph()), encoding="utf-8")
    write_config(tmp_path, graph="generated/repo_graph.json", usage_log_dir="usage/query")

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--config",
            "codegraph.config.toml",
            "tests-for",
            "server/routers/auth.py",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "tests/test_auth.py" in result.stdout
    log_path = tmp_path / "usage/query/query_usage.local.jsonl"
    event = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert event["eventType"] == "query_call"
    assert event["graphHash"].startswith("sha256:")
    assert event["tool"] == "tests-for"


def test_query_cli_requires_config_even_with_explicit_graph(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "repo_graph/query_cli.py"
    graph_path = tmp_path / "repo_graph.json"
    graph_path.write_text(json.dumps(sample_graph()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--graph",
            str(graph_path),
            "tests-for",
            "server/routers/auth.py",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env=env_without_code_graph_config(),
    )

    assert result.returncode == 1
    assert "code graph config was not found" in result.stderr
    assert "Traceback" not in result.stderr
    assert "tests/test_auth.py" not in result.stdout


def test_query_cli_reports_invalid_input_without_traceback(tmp_path):
    graph_path = tmp_path / "repo_graph.json"
    graph_path.write_text(json.dumps(sample_graph()), encoding="utf-8")
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "repo_graph/query_cli.py"
    write_config(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--config",
            "codegraph.config.toml",
            "--graph",
            str(graph_path),
            "tests-for",
            "../server/routers/auth.py",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "code graph query failed:" in result.stderr
    assert "Traceback" not in result.stderr


def test_query_cli_help_works_from_repo_root():
    repo_root = Path(__file__).resolve().parents[2]

    result = subprocess.run(
        [sys.executable, "repo_graph/query_cli.py", "--help"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "without printing raw repo_graph.json" in result.stdout


def test_query_cli_writes_opt_in_usage_log_without_raw_graph(tmp_path):
    graph_path = tmp_path / "repo_graph.json"
    usage_dir = tmp_path / "usage"
    graph_path.write_text(json.dumps(sample_graph()), encoding="utf-8")
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "repo_graph/query_cli.py"
    write_config(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--config",
            "codegraph.config.toml",
            "--graph",
            str(graph_path),
            "api-callers-for-endpoint",
            "POST /api/auth/login",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "CODE_GRAPH_USAGE_LOG": "1",
            "CODE_GRAPH_USAGE_DIR": str(usage_dir),
            "CODE_GRAPH_USAGE_SESSION_ID": "test-session",
        },
    )

    assert result.returncode == 0
    log_path = usage_dir / "query_usage.local.jsonl"
    event = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert event["eventType"] == "query_call"
    assert event["sessionId"] == "test-session"
    assert event["interface"] == "cli"
    assert event["tool"] == "api-callers-for-endpoint"
    assert event["input"] == {
        "kind": "endpoint",
        "value": "POST /api/auth/login",
        "normalized": "POST /api/auth/login",
    }
    assert event["result"]["count"] == 1
    assert event["result"]["edgeTypes"] == {"calls_api_endpoint": 1}
    assert event["result"]["topPaths"] == ["client/src/views/LoginView.vue"]
    assert event["status"] == "ok"
    assert event["graphHash"].startswith("sha256:")
    assert "nodes" not in event
    assert "edges" not in event


def sample_graph():
    return {
        "schemaVersion": 1,
        "generatorVersion": "code-graph@0.1.0",
        "pluginVersion": "repo-relationship-graph@0.1.0",
        "repo": {"name": "FixtureRepo", "root": "."},
        "sourceManifest": [],
        "nodes": [
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
        ],
        "edges": [
            {
                "source": "py:tests.test_auth",
                "target": "py:server.routers.auth",
                "type": "tests",
                "confidence": "high",
                "evidence": [{"path": "tests/test_auth.py", "kind": "ast_import", "line": 1}],
                "metadata": {"reason": "test_import"},
            },
            {
                "source": "file:client/src/views/LoginView.vue",
                "target": "api:POST /api/auth/login",
                "type": "calls_api_endpoint",
                "confidence": "high",
                "evidence": [{"path": "client/src/views/LoginView.vue", "kind": "frontend_api_call", "line": 10}],
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
                "metadata": {
                    "routeName": "Home",
                    "routePath": "/",
                    "routeSourcePath": "client/src/router/index.js",
                },
            },
            {
                "source": "file:client/e2e/home-smoke.js",
                "target": "vue_route:client/src/router/index.js:/#Home",
                "type": "e2e_reaches_route",
                "confidence": "high",
                "evidence": [{"path": "client/e2e/home-smoke.js", "kind": "playwright_page_goto", "line": 4}],
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
    usage_log_dir: str = "analysis/code_graph/usage",
) -> Path:
    config_path = repo_root / "codegraph.config.toml"
    mcp_graph = mcp_graph or graph
    config_path.write_text(
        f"""
schema_version = 1

[project]
name = "QueryCliFixture"
root = "."

[outputs]
graph = "{graph}"
schema = "generated/repo_graph.schema.json"
summary = "generated/repo_graph.summary.md"
usage_dir = "generated/usage"

[mcp]
default_graph = "{mcp_graph}"

[usage]
enabled_by_default = true
log_dir = "{usage_log_dir}"

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


def env_without_code_graph_config() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("CODE_GRAPH_CONFIG", None)
    return env
