from pathlib import Path

import pytest

from repo_graph.builders.graph_builder import build_graph
from repo_graph.discovery import language_for_path
from repo_graph.model import SourceFile
from repo_graph.parsers.python_ast import PythonAstParser
from repo_graph.parsers.js_vue_structure import JavaScriptVueParserConfig, JavaScriptVueStructureParser
from repo_graph.parsers.registry import ManifestParserRegistry, ParserRegistry, parser_registry_for_plugin_config
from repo_graph.writers.json_writer import graph_to_dict


pytestmark = pytest.mark.unit


CLIENT_PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def test_js_vue_parser_extracts_relative_and_alias_import_edges(tmp_path):
    write_frontend_fixture(tmp_path)
    source_files = frontend_source_files()
    parser = JavaScriptVueStructureParser(
        repo_root=tmp_path,
        source_files=source_files,
        client_package_root=CLIENT_PACKAGE_ROOT,
    )

    main_result = parser.parse(
        path="client/src/main.js",
        module_name="client/src/main.js",
        source=(tmp_path / "client/src/main.js").read_text(encoding="utf-8"),
    )
    app_result = parser.parse(
        path="client/src/App.vue",
        module_name="client/src/App.vue",
        source=(tmp_path / "client/src/App.vue").read_text(encoding="utf-8"),
    )

    main_edges = {(edge.source, edge.target, edge.type) for edge in main_result.edges}
    app_edges = {(edge.source, edge.target, edge.type) for edge in app_result.edges}

    assert ("file:client/src/main.js", "file:client/src/App.vue", "imports") in main_edges
    assert ("file:client/src/main.js", "file:client/src/composables/useApi.js", "imports") in main_edges
    assert ("file:client/src/main.js", "file:client/src/lazy.js", "imports") in main_edges
    assert ("file:client/src/main.js", "file:client/src/lib/helper.js", "imports") in main_edges
    assert ("file:client/src/main.js", "file:client/src/lib/extra.js", "imports") in main_edges
    assert ("file:client/src/App.vue", "file:client/src/components/HelloWorld.vue", "imports") in app_edges
    assert ("file:client/src/App.vue", "file:client/src/components/ClassicPanel.vue", "imports") in app_edges
    assert app_result.edges[0].evidence[0].kind == "vue_import"
    assert app_result.unsupported == []


def test_js_vue_parser_resolves_configured_aliases_and_extensions(tmp_path):
    (tmp_path / "app/source").mkdir(parents=True)
    (tmp_path / "shared/ui").mkdir(parents=True)
    source_path = "app/source/main.js"
    (tmp_path / source_path).write_text(
        "\n".join(
            [
                "import Button from '~ui/Button'",
                "import { helper } from '#/helper'",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "shared/ui/Button.vue").write_text("<script setup></script>\n", encoding="utf-8")
    (tmp_path / "app/source/helper.js").write_text("export const helper = true\n", encoding="utf-8")
    source_files = [
        SourceFile(path=source_path, language="javascript"),
        SourceFile(path="shared/ui/Button.vue", language="vue"),
        SourceFile(path="app/source/helper.js", language="javascript"),
    ]
    parser = JavaScriptVueStructureParser(
        repo_root=tmp_path,
        source_files=source_files,
        client_package_root=CLIENT_PACKAGE_ROOT,
        parser_config=JavaScriptVueParserConfig(
            aliases={"~ui/": "shared/ui/", "#/": "app/source/"},
            extensions=(".js", ".vue"),
        ),
    )

    result = parser.parse(
        path=source_path,
        module_name=source_path,
        source=(tmp_path / source_path).read_text(encoding="utf-8"),
    )

    edge_keys = {(edge.source, edge.target, edge.type) for edge in result.edges}
    assert ("file:app/source/main.js", "file:shared/ui/Button.vue", "imports") in edge_keys
    assert ("file:app/source/main.js", "file:app/source/helper.js", "imports") in edge_keys


def test_registered_js_vue_parser_replaces_unsupported_records(tmp_path):
    write_frontend_fixture(tmp_path)
    source_files = frontend_source_files()
    parser = JavaScriptVueStructureParser(
        repo_root=tmp_path,
        source_files=source_files,
        client_package_root=CLIENT_PACKAGE_ROOT,
    )

    graph = build_graph(
        repo_root=tmp_path,
        source_files=source_files,
        parser_registry=ParserRegistry(
            parsers={"javascript": parser, "vue": parser},
            deferred_languages={},
        ),
        manifest_registry=ManifestParserRegistry({}),
    )
    data = graph_to_dict(graph)
    edge_keys = {(edge["source"], edge["target"], edge["type"]) for edge in data["edges"]}

    assert ("file:client/src/main.js", "file:client/src/App.vue", "imports") in edge_keys
    assert ("file:client/src/App.vue", "file:client/src/components/HelloWorld.vue", "imports") in edge_keys
    assert not any(
        item["path"].endswith((".js", ".vue")) and item["reason"] == "parser_not_enabled"
        for item in data["unsupported"]
    )


def test_vitest_imports_create_tests_edges_for_frontend_sources(tmp_path):
    (tmp_path / "client/src/components/__tests__").mkdir(parents=True)
    (tmp_path / "client/src/components/TargetPanel.vue").write_text(
        "<script setup></script>\n",
        encoding="utf-8",
    )
    (tmp_path / "client/src/components/__tests__/TargetPanel.spec.js").write_text(
        """
import { describe, expect, it } from 'vitest'
import TargetPanel from '../TargetPanel.vue'

describe('TargetPanel', () => {
  it('mounts', () => {
    expect(TargetPanel).toBeDefined()
  })
})
""",
        encoding="utf-8",
    )
    (tmp_path / "client/src/components/__tests__/fixture.js").write_text(
        "import TargetPanel from '../TargetPanel.vue'\nexport const fixture = TargetPanel\n",
        encoding="utf-8",
    )
    source_files = [
        SourceFile(path="client/src/components/TargetPanel.vue", language="vue"),
        SourceFile(
            path="client/src/components/__tests__/TargetPanel.spec.js",
            language="javascript",
        ),
        SourceFile(
            path="client/src/components/__tests__/fixture.js",
            language="javascript",
        ),
    ]
    parser = JavaScriptVueStructureParser(
        repo_root=tmp_path,
        source_files=source_files,
        client_package_root=CLIENT_PACKAGE_ROOT,
    )

    graph = build_graph(
        repo_root=tmp_path,
        source_files=source_files,
        parser_registry=ParserRegistry(
            parsers={"javascript": parser, "vue": parser},
            deferred_languages={},
        ),
        manifest_registry=ManifestParserRegistry({}),
    )
    data = graph_to_dict(graph)
    nodes_by_id = {node["id"]: node for node in data["nodes"]}
    tests_edges = [
        edge
        for edge in data["edges"]
        if edge["target"] == "file:client/src/components/TargetPanel.vue"
        and edge["type"] == "tests"
    ]

    assert (
        nodes_by_id["file:client/src/components/__tests__/TargetPanel.spec.js"]["type"]
        == "test_file"
    )
    assert nodes_by_id["file:client/src/components/__tests__/fixture.js"]["type"] == "file"
    assert [edge["source"] for edge in tests_edges] == [
        "file:client/src/components/__tests__/TargetPanel.spec.js"
    ]
    test_edge = tests_edges[0]
    assert test_edge["confidence"] == "high"
    assert test_edge["metadata"] == {"reason": "test_import"}
    assert test_edge["evidence"] == [
        {
            "path": "client/src/components/__tests__/TargetPanel.spec.js",
            "kind": "js_import",
            "line": 3,
        }
    ]


def test_configured_frontend_test_root_creates_tests_edges_for_plain_js_file(tmp_path):
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "custom/e2e").mkdir(parents=True)
    (tmp_path / "src/TargetPanel.vue").write_text("<script setup></script>\n", encoding="utf-8")
    (tmp_path / "custom/e2e/flow.js").write_text(
        """
import TargetPanel from '../../src/TargetPanel.vue'

console.log(TargetPanel)
""",
        encoding="utf-8",
    )
    source_files = [
        SourceFile(path="src/TargetPanel.vue", language="vue"),
        SourceFile(path="custom/e2e/flow.js", language="javascript"),
    ]
    parser = JavaScriptVueStructureParser(
        repo_root=tmp_path,
        source_files=source_files,
        client_package_root=CLIENT_PACKAGE_ROOT,
        frontend_test_roots=("custom/e2e",),
    )

    graph = build_graph(
        repo_root=tmp_path,
        source_files=source_files,
        parser_registry=ParserRegistry(
            parsers={"javascript": parser, "vue": parser},
            deferred_languages={},
        ),
        manifest_registry=ManifestParserRegistry({}),
        frontend_test_roots=("custom/e2e",),
    )
    data = graph_to_dict(graph)
    nodes_by_id = {node["id"]: node for node in data["nodes"]}
    edge_keys = {(edge["source"], edge["target"], edge["type"]) for edge in data["edges"]}

    assert nodes_by_id["file:custom/e2e/flow.js"]["type"] == "test_file"
    assert ("file:custom/e2e/flow.js", "file:src/TargetPanel.vue", "tests") in edge_keys


def test_parser_registry_passes_configured_js_vue_options(tmp_path):
    (tmp_path / "src/routes").mkdir(parents=True)
    (tmp_path / "src/views").mkdir(parents=True)
    (tmp_path / "src/api").mkdir(parents=True)
    router_path = "src/routes/app.js"
    view_path = "src/views/HomeView.vue"
    api_path = "src/api/client.js"
    (tmp_path / router_path).write_text(
        "export const routes = [{ path: '/', name: 'Home', component: () => import('@views/HomeView.vue') }]\n",
        encoding="utf-8",
    )
    (tmp_path / view_path).write_text("<script setup></script>\n", encoding="utf-8")
    (tmp_path / api_path).write_text(
        "async function load() { await http.get('/users') }\n",
        encoding="utf-8",
    )
    source_files = [
        SourceFile(path=router_path, language="javascript"),
        SourceFile(path=view_path, language="vue"),
        SourceFile(path=api_path, language="javascript"),
    ]
    registry = parser_registry_for_plugin_config(
        repo_root=tmp_path,
        source_files=source_files,
        plugins={
            "python_ast": {"enabled": False},
            "js_vue": {
                "enabled": True,
                "client_package_root": str(CLIENT_PACKAGE_ROOT),
                "aliases": {"@views/": "src/views/"},
            },
            "vue_router": {"enabled": True, "router_files": ["src/routes/**/*.js"]},
            "frontend_api_calls": {
                "enabled": True,
                "api_base": "/backend",
                "api_client_names": ["http"],
            },
            "playwright": {"enabled": True, "test_roots": ["custom/e2e"]},
        },
    )
    parser = registry.require("javascript")

    router_result = parser.parse(
        path=router_path,
        module_name=router_path,
        source=(tmp_path / router_path).read_text(encoding="utf-8"),
    )
    api_result = parser.parse(
        path=api_path,
        module_name=api_path,
        source=(tmp_path / api_path).read_text(encoding="utf-8"),
    )

    assert router_result.nodes[0].metadata["componentPath"] == view_path
    assert [(call.method, call.path, call.call_kind) for call in api_result.api_calls] == [
        ("GET", "/backend/users", "member.get")
    ]


def test_js_vue_parser_extracts_static_frontend_api_call_dtos(tmp_path):
    (tmp_path / "client/src").mkdir(parents=True)
    source_path = "client/src/apiClient.js"
    (tmp_path / source_path).write_text(
        """
async function load() {
  const { request } = useApi()
  const { request: aliasedRequest } = useApi()
  const api = useApi()
  await fetch('/api/health')
  await window.fetch('/api/auth/login', { method: 'POST' })
  await fetch(getApiUrl('/auth/me'))
  await request('/typing/sessions')
  await aliasedRequest('/typing/aliases')
  await api.request('recording/sessions', { method: 'POST' })
  await request(`/recording/sessions/${sessionId}/progress`, { method: 'PATCH' })
  await apiClient.get('/users?active=1')
  await params.get('/users')
  await fetch('https://third-party.example/api/users')
  await fetch(`/api/dynamic/${id}`)
  await request(`/${org}/api/tenant`)
  await fetch(`${API_BASE}/health`)
  await request(API_PATHS.users)
  vi.mock('/api/auth/login')
  expect(url).toContain('/api/health')
}
""",
        encoding="utf-8",
    )
    source_files = [SourceFile(path=source_path, language="javascript")]
    parser = JavaScriptVueStructureParser(
        repo_root=tmp_path,
        source_files=source_files,
        client_package_root=CLIENT_PACKAGE_ROOT,
    )

    result = parser.parse(
        path=source_path,
        module_name=source_path,
        source=(tmp_path / source_path).read_text(encoding="utf-8"),
    )

    calls = [
        (call.method, call.path, call.call_kind, call.confidence, call.evidence.kind)
        for call in result.api_calls
    ]
    assert calls == [
        ("GET", "/api/health", "fetch", "high", "frontend_api_call"),
        ("POST", "/api/auth/login", "window.fetch", "high", "frontend_api_call"),
        ("GET", "/api/auth/me", "fetch", "high", "frontend_api_call"),
        ("GET", "/api/typing/sessions", "use_api_request", "high", "frontend_api_call"),
        ("GET", "/api/typing/aliases", "use_api_request", "high", "frontend_api_call"),
        ("POST", "/api/recording/sessions", "use_api_request", "high", "frontend_api_call"),
        ("PATCH", "/api/recording/sessions/{param}/progress", "use_api_request", "medium", "frontend_api_call"),
        ("GET", "/api/users", "member.get", "medium", "frontend_api_call"),
        ("GET", "/api/dynamic/{param}", "fetch", "medium", "frontend_api_call"),
    ]


def test_js_vue_parser_uses_configured_api_helpers_and_base(tmp_path):
    (tmp_path / "client/src").mkdir(parents=True)
    source_path = "client/src/customApiClient.js"
    (tmp_path / source_path).write_text(
        """
async function load() {
  const { request: send } = createApi()
  const custom = createApi()
  await send('/sessions')
  await custom.request('/custom-request', { method: 'POST' })
  await http.get('/users')
  await fetch(buildApiPath('/health'))
  await fetch('/backend/status')
  await fetch('/api/ignored')
  await api.get('/ignored-default-client')
  const { request } = useApi()
  await request('/ignored-default-use-api')
}
""",
        encoding="utf-8",
    )
    source_files = [SourceFile(path=source_path, language="javascript")]
    parser = JavaScriptVueStructureParser(
        repo_root=tmp_path,
        source_files=source_files,
        client_package_root=CLIENT_PACKAGE_ROOT,
        parser_config=JavaScriptVueParserConfig(
            api_base="/backend",
            use_api_factory_names=("createApi",),
            api_client_names=("http",),
            api_url_helper_names=("buildApiPath",),
        ),
    )

    result = parser.parse(
        path=source_path,
        module_name=source_path,
        source=(tmp_path / source_path).read_text(encoding="utf-8"),
    )

    assert [(call.method, call.path, call.call_kind, call.confidence) for call in result.api_calls] == [
        ("GET", "/backend/sessions", "use_api_request", "high"),
        ("POST", "/backend/custom-request", "use_api_request", "high"),
        ("GET", "/backend/users", "member.get", "medium"),
        ("GET", "/backend/health", "fetch", "high"),
        ("GET", "/backend/status", "fetch", "high"),
        ("GET", "/backend/ignored-default-use-api", "request", "medium"),
    ]


def test_js_vue_parser_can_disable_use_api_factory_detection(tmp_path):
    (tmp_path / "client/src").mkdir(parents=True)
    source_path = "client/src/noUseApi.js"
    (tmp_path / source_path).write_text(
        """
async function load() {
  const { request: send } = useApi()
  await send('/typing/sessions')
  await request('/legacy')
}
""",
        encoding="utf-8",
    )
    source_files = [SourceFile(path=source_path, language="javascript")]
    parser = JavaScriptVueStructureParser(
        repo_root=tmp_path,
        source_files=source_files,
        client_package_root=CLIENT_PACKAGE_ROOT,
        parser_config=JavaScriptVueParserConfig(use_api_factory_names=()),
    )

    result = parser.parse(
        path=source_path,
        module_name=source_path,
        source=(tmp_path / source_path).read_text(encoding="utf-8"),
    )

    assert [(call.method, call.path, call.call_kind, call.confidence) for call in result.api_calls] == [
        ("GET", "/api/legacy", "request", "medium")
    ]


def test_js_vue_parser_treats_root_api_base_as_identity_for_direct_config(tmp_path):
    (tmp_path / "client/src").mkdir(parents=True)
    source_path = "client/src/rootApiBase.js"
    (tmp_path / source_path).write_text(
        """
async function load() {
  await api.get('/users')
}
""",
        encoding="utf-8",
    )
    source_files = [SourceFile(path=source_path, language="javascript")]
    parser = JavaScriptVueStructureParser(
        repo_root=tmp_path,
        source_files=source_files,
        client_package_root=CLIENT_PACKAGE_ROOT,
        parser_config=JavaScriptVueParserConfig(api_base="/"),
    )

    result = parser.parse(
        path=source_path,
        module_name=source_path,
        source=(tmp_path / source_path).read_text(encoding="utf-8"),
    )

    assert [(call.method, call.path, call.call_kind, call.confidence) for call in result.api_calls] == [
        ("GET", "/users", "member.get", "medium")
    ]


def test_js_vue_parser_marks_unqualified_request_as_medium_candidate(tmp_path):
    (tmp_path / "client/src").mkdir(parents=True)
    source_path = "client/src/legacyRequest.js"
    (tmp_path / source_path).write_text(
        """
async function load() {
  await request('/typing/sessions', { method: 'POST' })
  await otherRequest('/typing/sessions')
}
""",
        encoding="utf-8",
    )
    source_files = [SourceFile(path=source_path, language="javascript")]
    parser = JavaScriptVueStructureParser(
        repo_root=tmp_path,
        source_files=source_files,
        client_package_root=CLIENT_PACKAGE_ROOT,
    )

    result = parser.parse(
        path=source_path,
        module_name=source_path,
        source=(tmp_path / source_path).read_text(encoding="utf-8"),
    )

    assert [(call.method, call.path, call.call_kind, call.confidence) for call in result.api_calls] == [
        ("POST", "/api/typing/sessions", "request", "medium")
    ]


def test_graph_builder_links_frontend_api_calls_to_fastapi_endpoints(tmp_path):
    write_api_call_graph_fixture(tmp_path)
    source_files = api_call_graph_source_files()
    parser = JavaScriptVueStructureParser(
        repo_root=tmp_path,
        source_files=source_files,
        client_package_root=CLIENT_PACKAGE_ROOT,
    )

    graph = build_graph(
        repo_root=tmp_path,
        source_files=source_files,
        parser_registry=ParserRegistry(
            parsers={"python": PythonAstParser(), "javascript": parser, "vue": parser},
            deferred_languages={},
        ),
        manifest_registry=ManifestParserRegistry({}),
    )
    data = graph_to_dict(graph)
    api_edges = {
        (edge["source"], edge["target"], edge["type"]): edge
        for edge in data["edges"]
        if edge["type"] == "calls_api_endpoint"
    }

    assert (
        "file:client/src/apiClient.js",
        "api:GET /api/health",
        "calls_api_endpoint",
    ) in api_edges
    assert (
        "file:client/src/apiClient.js",
        "api:POST /api/typing/sessions",
        "calls_api_endpoint",
    ) in api_edges
    assert (
        "file:client/src/apiClient.js",
        "api:POST /api/auth/login",
        "calls_api_endpoint",
    ) in api_edges
    assert (
        "file:client/src/apiClient.js",
        "api:PATCH /api/recording/sessions/{session_id}/progress",
        "calls_api_endpoint",
    ) in api_edges
    assert (
        "file:client/src/apiClient.js",
        "api:GET /api/single-unknown",
        "calls_api_endpoint",
    ) in api_edges
    assert (
        "file:client/src/legacyRequest.js",
        "api:POST /api/auth/login",
        "calls_api_endpoint",
    ) in api_edges
    health_edge = api_edges[
        (
            "file:client/src/apiClient.js",
            "api:GET /api/health",
            "calls_api_endpoint",
        )
    ]
    unknown_edge = api_edges[
        (
            "file:client/src/apiClient.js",
            "api:GET /api/single-unknown",
            "calls_api_endpoint",
        )
    ]
    typing_edge = api_edges[
        (
            "file:client/src/apiClient.js",
            "api:POST /api/typing/sessions",
            "calls_api_endpoint",
        )
    ]
    legacy_edge = api_edges[
        (
            "file:client/src/legacyRequest.js",
            "api:POST /api/auth/login",
            "calls_api_endpoint",
        )
    ]
    progress_edge = api_edges[
        (
            "file:client/src/apiClient.js",
            "api:PATCH /api/recording/sessions/{session_id}/progress",
            "calls_api_endpoint",
        )
    ]
    assert health_edge["confidence"] == "high"
    assert health_edge["metadata"] == {
        "method": "GET",
        "path": "/api/health",
        "callKind": "fetch",
        "candidate": True,
        "matchedBy": "method_path",
    }
    assert health_edge["evidence"] == [
        {"path": "client/src/apiClient.js", "kind": "frontend_api_call", "line": 2}
    ]
    assert typing_edge["confidence"] == "high"
    assert typing_edge["metadata"]["callKind"] == "use_api_request"
    assert progress_edge["confidence"] == "medium"
    assert progress_edge["metadata"] == {
        "method": "PATCH",
        "path": "/api/recording/sessions/{param}/progress",
        "callKind": "use_api_request",
        "candidate": True,
        "matchedBy": "method_path_pattern",
        "endpointPath": "/api/recording/sessions/{session_id}/progress",
        "pathPattern": "/api/recording/sessions/{}/progress",
    }
    assert legacy_edge["confidence"] == "medium"
    assert legacy_edge["metadata"]["callKind"] == "request"
    assert unknown_edge["confidence"] == "medium"
    assert unknown_edge["metadata"]["matchedBy"] == "path"
    assert (
        "file:client/src/apiClient.js",
        "api:GET /api/ambiguous",
        "calls_api_endpoint",
    ) not in api_edges
    assert (
        "file:client/src/apiClient.js",
        "api:POST /api/ambiguous",
        "calls_api_endpoint",
    ) not in api_edges
    assert not any(edge["source"] == "file:client/src/__tests__/ApiClient.spec.js" for edge in api_edges.values())


def test_graph_builder_links_unique_path_only_parameterized_api_call(tmp_path):
    write_api_call_graph_fixture(tmp_path)
    source_files = api_call_graph_source_files()
    parser = JavaScriptVueStructureParser(
        repo_root=tmp_path,
        source_files=source_files,
        client_package_root=CLIENT_PACKAGE_ROOT,
    )

    graph = build_graph(
        repo_root=tmp_path,
        source_files=source_files,
        parser_registry=ParserRegistry(
            parsers={"python": PythonAstParser(), "javascript": parser, "vue": parser},
            deferred_languages={},
        ),
        manifest_registry=ManifestParserRegistry({}),
    )
    data = graph_to_dict(graph)
    api_edges = {
        (edge["source"], edge["target"], edge["type"]): edge
        for edge in data["edges"]
        if edge["type"] == "calls_api_endpoint"
    }

    unique_edge = api_edges[
        (
            "file:client/src/pathOnlyTemplate.js",
            "api:GET /api/users/{user_id}/profile",
            "calls_api_endpoint",
        )
    ]
    assert unique_edge["confidence"] == "low"
    assert unique_edge["metadata"] == {
        "method": "GET",
        "path": "/api/users/{param}/profile",
        "callKind": "fetch",
        "candidate": True,
        "matchedBy": "path_pattern",
        "endpointPath": "/api/users/{user_id}/profile",
        "pathPattern": "/api/users/{}/profile",
    }
    assert (
        "file:client/src/pathOnlyTemplate.js",
        "api:GET /api/projects/{project_id}",
        "calls_api_endpoint",
    ) not in api_edges
    assert (
        "file:client/src/pathOnlyTemplate.js",
        "api:POST /api/projects/{project_id}",
        "calls_api_endpoint",
    ) not in api_edges


def test_playwright_page_goto_creates_e2e_reaches_route_edges(tmp_path):
    write_router_fixture(tmp_path)
    (tmp_path / "client/e2e").mkdir(parents=True)
    e2e_path = "client/e2e/admin-smoke.js"
    (tmp_path / e2e_path).write_text(
        """
import { test } from '@playwright/test'

test('admin users', async ({ page }) => {
  await page.goto('http://localhost:5173/admin/users?x=1#top')
  await expect(page).toHaveURL(/^\\/$/)
  await expect(page).toHaveURL(/https:\\/\\/example.com\\/admin$/)
  await expect(page).toHaveURL(destinationPattern)
  await page.goto('https://example.com/admin/users')
  await page.goto(routePath)
})
""",
        encoding="utf-8",
    )
    source_files = router_source_files() + [SourceFile(path=e2e_path, language="javascript")]
    parser = JavaScriptVueStructureParser(
        repo_root=tmp_path,
        source_files=source_files,
        client_package_root=CLIENT_PACKAGE_ROOT,
    )

    graph = build_graph(
        repo_root=tmp_path,
        source_files=source_files,
        parser_registry=ParserRegistry(
            parsers={"javascript": parser, "vue": parser},
            deferred_languages={},
        ),
        manifest_registry=ManifestParserRegistry({}),
    )
    data = graph_to_dict(graph)
    nodes_by_id = {node["id"]: node for node in data["nodes"]}
    e2e_edges = sorted(
        (edge for edge in data["edges"] if edge["type"] == "e2e_reaches_route"),
        key=lambda edge: edge["target"],
    )

    assert nodes_by_id[f"file:{e2e_path}"]["type"] == "test_file"
    assert e2e_edges == [
        {
            "source": f"file:{e2e_path}",
            "target": "vue_route:client/src/router/index.js:/#Home",
            "type": "e2e_reaches_route",
            "confidence": "high",
            "evidence": [{"path": e2e_path, "kind": "playwright_to_have_url", "line": 6}],
            "metadata": {"routePath": "/", "candidate": True},
        },
        {
            "source": f"file:{e2e_path}",
            "target": "vue_route:client/src/router/index.js:/admin/users#AdminUsers",
            "type": "e2e_reaches_route",
            "confidence": "high",
            "evidence": [{"path": e2e_path, "kind": "playwright_page_goto", "line": 5}],
            "metadata": {"routePath": "/admin/users", "candidate": True},
        }
    ]


def test_playwright_to_have_url_extracts_static_route_assertions(tmp_path):
    (tmp_path / "client/e2e").mkdir(parents=True)
    e2e_path = "client/e2e/route-assertions.js"
    (tmp_path / e2e_path).write_text(
        """
import { test } from '@playwright/test'

test('route assertions', async ({ page, dialog }) => {
  await expect(page).toHaveURL('/admin')
  await expect(page).toHaveURL(/^\\/admin\\/users$/)
  await expect(page).toHaveURL(/https:\\/\\/example.com\\/admin$/)
  await expect(page).toHaveURL(destinationPattern)
  await expect(dialog).toHaveURL('/admin')
})
""",
        encoding="utf-8",
    )
    parser = JavaScriptVueStructureParser(
        repo_root=tmp_path,
        source_files=[SourceFile(path=e2e_path, language="javascript")],
        client_package_root=CLIENT_PACKAGE_ROOT,
    )

    result = parser.parse(
        path=e2e_path,
        module_name=e2e_path,
        source=(tmp_path / e2e_path).read_text(encoding="utf-8"),
    )

    navigations = [
        (navigation.route_path, navigation.evidence.kind, navigation.evidence.line)
        for navigation in result.page_navigations
    ]
    assert navigations == [
        ("/admin", "playwright_to_have_url", 5),
        ("/admin/users", "playwright_to_have_url", 6),
    ]


def test_js_vue_parser_records_parse_errors_as_unsupported(tmp_path):
    write_frontend_fixture(tmp_path)
    source_files = frontend_source_files()
    parser = JavaScriptVueStructureParser(
        repo_root=tmp_path,
        source_files=source_files,
        client_package_root=CLIENT_PACKAGE_ROOT,
    )

    js_result = parser.parse(
        path="client/src/broken.js",
        module_name="client/src/broken.js",
        source="const =\n",
    )
    vue_result = parser.parse(
        path="client/src/Broken.vue",
        module_name="client/src/Broken.vue",
        source="<script setup>\nconst =\n</script>\n",
    )

    assert js_result.edges == []
    assert js_result.unsupported[0].reason == "js_parse_error"
    assert js_result.unsupported[0].phase == "phase_2"
    assert vue_result.edges == []
    assert vue_result.unsupported[0].reason == "vue_parse_error"
    assert vue_result.unsupported[0].phase == "phase_2"


def test_vue_router_routes_render_view_edges(tmp_path):
    write_router_fixture(tmp_path)
    source_files = router_source_files()
    parser = JavaScriptVueStructureParser(
        repo_root=tmp_path,
        source_files=source_files,
        client_package_root=CLIENT_PACKAGE_ROOT,
    )

    result = parser.parse(
        path="client/src/router/index.js",
        module_name="client/src/router/index.js",
        source=(tmp_path / "client/src/router/index.js").read_text(encoding="utf-8"),
    )

    route_nodes = {node.id: node for node in result.nodes}
    render_edges = {
        (edge.source, edge.target, edge.type): edge
        for edge in result.edges
        if edge.type == "renders_view"
    }

    assert route_nodes["vue_route:client/src/router/index.js:/#Home"].metadata == {
        "routeName": "Home",
        "routePath": "/",
        "routeSourcePath": "client/src/router/index.js",
        "componentPath": "client/src/views/HomeView.vue",
    }
    assert (
        route_nodes["vue_route:client/src/router/index.js:/admin/users#AdminUsers"].metadata[
            "routePath"
        ]
        == "/admin/users"
    )
    assert (
        route_nodes["vue_route:client/src/router/index.js:/admin#AdminTextSets"].metadata[
            "routePath"
        ]
        == "/admin"
    )
    assert (
        "vue_route:client/src/router/index.js:/#Home",
        "file:client/src/views/HomeView.vue",
        "renders_view",
    ) in render_edges
    assert (
        "vue_route:client/src/router/index.js:/admin/users#AdminUsers",
        "file:client/src/views/admin/UserManager.vue",
        "renders_view",
    ) in render_edges
    assert render_edges[
        (
            "vue_route:client/src/router/index.js:/admin/users#AdminUsers",
            "file:client/src/views/admin/UserManager.vue",
            "renders_view",
        )
    ].evidence[0].kind == "vue_router_route"


def test_vue_router_route_ids_include_path_for_duplicate_route_names(tmp_path):
    (tmp_path / "client/src/router").mkdir(parents=True)
    (tmp_path / "client/src/views").mkdir(parents=True)
    (tmp_path / "client/src/router/index.js").write_text(
        """
import { createRouter, createWebHistory } from 'vue-router'

export default createRouter({
  history: createWebHistory('/'),
  routes: [
    {
      path: '/one',
      name: 'Duplicate',
      component: () => import('../views/OneView.vue'),
    },
    {
      path: '/two',
      name: 'Duplicate',
      component: () => import('../views/TwoView.vue'),
    },
  ],
})
""",
        encoding="utf-8",
    )
    for path in [
        "client/src/views/OneView.vue",
        "client/src/views/TwoView.vue",
    ]:
        (tmp_path / path).write_text("<script setup></script>\n", encoding="utf-8")
    source_files = [
        SourceFile(path="client/src/router/index.js", language="javascript"),
        SourceFile(path="client/src/views/OneView.vue", language="vue"),
        SourceFile(path="client/src/views/TwoView.vue", language="vue"),
    ]
    parser = JavaScriptVueStructureParser(
        repo_root=tmp_path,
        source_files=source_files,
        client_package_root=CLIENT_PACKAGE_ROOT,
    )

    result = parser.parse(
        path="client/src/router/index.js",
        module_name="client/src/router/index.js",
        source=(tmp_path / "client/src/router/index.js").read_text(encoding="utf-8"),
    )

    route_ids = {node.id for node in result.nodes if node.type == "vue_route"}
    render_edges = {(edge.source, edge.target, edge.type) for edge in result.edges}

    assert route_ids == {
        "vue_route:client/src/router/index.js:/one#Duplicate",
        "vue_route:client/src/router/index.js:/two#Duplicate",
    }
    assert (
        "vue_route:client/src/router/index.js:/one#Duplicate",
        "file:client/src/views/OneView.vue",
        "renders_view",
    ) in render_edges
    assert (
        "vue_route:client/src/router/index.js:/two#Duplicate",
        "file:client/src/views/TwoView.vue",
        "renders_view",
    ) in render_edges


def test_vue_router_routes_are_only_extracted_from_router_files(tmp_path):
    write_router_fixture(tmp_path)
    source_files = router_source_files() + [
        SourceFile(path="client/src/not_router.js", language="javascript")
    ]
    parser = JavaScriptVueStructureParser(
        repo_root=tmp_path,
        source_files=source_files,
        client_package_root=CLIENT_PACKAGE_ROOT,
    )

    result = parser.parse(
        path="client/src/not_router.js",
        module_name="client/src/not_router.js",
        source="""
const routes = [
  { path: '/fake', name: 'Fake', component: () => import('./views/HomeView.vue') },
]
""",
    )

    assert not any(node.type == "vue_route" for node in result.nodes)
    assert not any(edge.type == "renders_view" for edge in result.edges)


def test_vue_router_routes_are_extracted_from_configured_router_files(tmp_path):
    (tmp_path / "src/routes").mkdir(parents=True)
    (tmp_path / "src/views").mkdir(parents=True)
    router_path = "src/routes/appRoutes.js"
    view_path = "src/views/DashboardView.vue"
    (tmp_path / router_path).write_text(
        """
export const routes = [
  { path: '/dashboard', name: 'Dashboard', component: () => import('../views/DashboardView.vue') },
]
""",
        encoding="utf-8",
    )
    (tmp_path / view_path).write_text("<script setup></script>\n", encoding="utf-8")
    source_files = [
        SourceFile(path=router_path, language="javascript"),
        SourceFile(path=view_path, language="vue"),
    ]
    parser = JavaScriptVueStructureParser(
        repo_root=tmp_path,
        source_files=source_files,
        client_package_root=CLIENT_PACKAGE_ROOT,
        parser_config=JavaScriptVueParserConfig(router_files=("src/routes/**/*.js",)),
    )

    result = parser.parse(
        path=router_path,
        module_name=router_path,
        source=(tmp_path / router_path).read_text(encoding="utf-8"),
    )

    route_nodes = {node.id: node for node in result.nodes}
    assert route_nodes["vue_route:src/routes/appRoutes.js:/dashboard#Dashboard"].metadata == {
        "routeName": "Dashboard",
        "routePath": "/dashboard",
        "routeSourcePath": router_path,
        "componentPath": view_path,
    }


def test_js_vue_parser_honors_feature_disable_flags(tmp_path):
    write_router_fixture(tmp_path)
    source_files = router_source_files()
    source_path = "client/src/router/index.js"
    (tmp_path / source_path).write_text(
        """
const routes = [
  { path: '/', name: 'Home', component: () => import('../views/HomeView.vue') },
]
async function load({ page }) {
  await fetch('/api/health')
  await page.goto('/')
}
""",
        encoding="utf-8",
    )
    parser = JavaScriptVueStructureParser(
        repo_root=tmp_path,
        source_files=source_files,
        client_package_root=CLIENT_PACKAGE_ROOT,
        parser_config=JavaScriptVueParserConfig(
            vue_router_enabled=False,
            frontend_api_calls_enabled=False,
            playwright_enabled=False,
        ),
    )

    result = parser.parse(
        path=source_path,
        module_name=source_path,
        source=(tmp_path / source_path).read_text(encoding="utf-8"),
    )

    assert result.nodes == []
    assert result.api_calls == []
    assert result.page_navigations == []


def test_js_vue_parser_preflights_client_package_root(tmp_path):
    with pytest.raises(RuntimeError, match="package.json"):
        JavaScriptVueStructureParser(
            repo_root=tmp_path,
            source_files=[],
            client_package_root=tmp_path / "missing-client",
        )


def test_js_vue_parser_missing_runtime_skip_returns_unsupported_record(tmp_path):
    source_path = "client/src/main.js"
    source_files = [SourceFile(path=source_path, language="javascript")]
    parser = JavaScriptVueStructureParser(
        repo_root=tmp_path,
        source_files=source_files,
        client_package_root=tmp_path / "missing-client",
        parser_config=JavaScriptVueParserConfig(missing_runtime="skip"),
    )

    result = parser.parse(path=source_path, module_name=source_path, source="import './missing.js'\n")

    assert result.edges == []
    assert result.unsupported[0].reason == "runtime_missing"
    assert "package.json" in result.unsupported[0].message


def write_router_fixture(tmp_path: Path) -> None:
    (tmp_path / "client/src/router").mkdir(parents=True)
    (tmp_path / "client/src/views/admin").mkdir(parents=True)
    (tmp_path / "client/src/router/index.js").write_text(
        """
import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'Home',
    component: () => import('../views/HomeView.vue'),
  },
  {
    path: '/admin',
    name: 'Admin',
    component: () => import('../views/AdminView.vue'),
    children: [
      {
        path: '',
        name: 'AdminTextSets',
        component: () => import('../views/admin/TextSetManager.vue'),
      },
      {
        path: 'users',
        name: 'AdminUsers',
        component: () => import('../views/admin/UserManager.vue'),
      },
    ],
  },
]

export default createRouter({
  history: createWebHistory('/'),
  routes,
})
""",
        encoding="utf-8",
    )
    for path in [
        "client/src/views/HomeView.vue",
        "client/src/views/AdminView.vue",
        "client/src/views/admin/TextSetManager.vue",
        "client/src/views/admin/UserManager.vue",
    ]:
        (tmp_path / path).write_text("<script setup></script>\n", encoding="utf-8")


def write_frontend_fixture(tmp_path: Path) -> None:
    (tmp_path / "client/src/components").mkdir(parents=True)
    (tmp_path / "client/src/composables").mkdir(parents=True)
    (tmp_path / "client/src/lib").mkdir(parents=True)
    (tmp_path / "client/src/main.js").write_text(
        "\n".join(
            [
                "import App from './App.vue'",
                "import { useApi } from '@/composables/useApi'",
                "export { helper } from './lib/helper.js'",
                "export * from './lib/extra.js'",
                "export function loadLazy() { return import('./lazy.js') }",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "client/src/App.vue").write_text(
        """
<script>
import ClassicPanel from './components/ClassicPanel.vue'
</script>

<script setup>
import HelloWorld from './components/HelloWorld.vue'
</script>

<template>
  <HelloWorld />
</template>
""",
        encoding="utf-8",
    )
    (tmp_path / "client/src/components/HelloWorld.vue").write_text(
        "<script setup></script>\n",
        encoding="utf-8",
    )
    (tmp_path / "client/src/components/ClassicPanel.vue").write_text(
        "<script setup></script>\n",
        encoding="utf-8",
    )
    (tmp_path / "client/src/composables/useApi.js").write_text(
        "export function useApi() { return {} }\n",
        encoding="utf-8",
    )
    (tmp_path / "client/src/lazy.js").write_text("export const lazy = true\n", encoding="utf-8")
    (tmp_path / "client/src/lib/helper.js").write_text("export const helper = true\n", encoding="utf-8")
    (tmp_path / "client/src/lib/extra.js").write_text("export const extra = true\n", encoding="utf-8")


def frontend_source_files() -> list[SourceFile]:
    paths = [
        "client/src/main.js",
        "client/src/App.vue",
        "client/src/components/HelloWorld.vue",
        "client/src/components/ClassicPanel.vue",
        "client/src/composables/useApi.js",
        "client/src/lazy.js",
        "client/src/lib/helper.js",
        "client/src/lib/extra.js",
    ]
    return [SourceFile(path=path, language=language_for_path(path)) for path in paths]


def router_source_files() -> list[SourceFile]:
    paths = [
        "client/src/router/index.js",
        "client/src/views/HomeView.vue",
        "client/src/views/AdminView.vue",
        "client/src/views/admin/TextSetManager.vue",
        "client/src/views/admin/UserManager.vue",
    ]
    return [SourceFile(path=path, language=language_for_path(path)) for path in paths]


def write_api_call_graph_fixture(tmp_path: Path) -> None:
    (tmp_path / "server").mkdir(parents=True)
    (tmp_path / "client/src").mkdir(parents=True)
    (tmp_path / "client/src/__tests__").mkdir(parents=True)
    (tmp_path / "server/main.py").write_text(
        """
from fastapi import FastAPI

app = FastAPI()

@app.get('/api/health')
async def health():
    return {}

@app.post('/api/typing/sessions')
async def create_typing_session():
    return {}

@app.post('/api/auth/login')
async def login():
    return {}

@app.patch('/api/recording/sessions/{session_id}/progress')
async def update_progress(session_id: int):
    return {}

@app.get('/api/users/{user_id}/profile')
async def user_profile(user_id: int):
    return {}

@app.get('/api/projects/{project_id}')
async def get_project(project_id: int):
    return {}

@app.post('/api/projects/{project_id}')
async def update_project(project_id: int):
    return {}

@app.get('/api/single-unknown')
async def single_unknown():
    return {}

@app.get('/api/ambiguous')
async def get_ambiguous():
    return {}

@app.post('/api/ambiguous')
async def post_ambiguous():
    return {}
""",
        encoding="utf-8",
    )
    (tmp_path / "client/src/apiClient.js").write_text(
        "\n".join(
            [
                "async function load() {",
                "  await fetch('/api/health')",
                "  const { request } = useApi()",
                "  await request('/typing/sessions', { method: 'POST' })",
                "  await fetch(getApiUrl('/auth/login'), { method: 'POST' })",
                "  await request(`/recording/sessions/${sessionId}/progress`, { method: 'PATCH' })",
                "  await fetch('/api/single-unknown', options)",
                "  await fetch('/api/ambiguous', options)",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "client/src/__tests__/ApiClient.spec.js").write_text(
        "async function testRequest() { await fetch('/api/health') }\n",
        encoding="utf-8",
    )
    (tmp_path / "client/src/legacyRequest.js").write_text(
        "async function load() { await request('/auth/login', { method: 'POST' }) }\n",
        encoding="utf-8",
    )
    (tmp_path / "client/src/pathOnlyTemplate.js").write_text(
        "\n".join(
            [
                "async function load() {",
                "  await fetch(`/api/users/${userId}/profile`, options)",
                "  await fetch(`/api/projects/${projectId}`, options)",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def api_call_graph_source_files() -> list[SourceFile]:
    paths = [
        "server/main.py",
        "client/src/apiClient.js",
        "client/src/legacyRequest.js",
        "client/src/pathOnlyTemplate.js",
        "client/src/__tests__/ApiClient.spec.js",
    ]
    return [SourceFile(path=path, language=language_for_path(path) if path.endswith(".js") else "python") for path in paths]
