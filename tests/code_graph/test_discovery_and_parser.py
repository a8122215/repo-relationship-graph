import pytest

from repo_graph.builders.graph_builder import build_graph
from repo_graph.discovery import FileDiscovery, FileDiscoveryConfig
from repo_graph.model import (
    Edge,
    Evidence,
    ImportRelation,
    Node,
    PythonParseResult,
    SourceFile,
    SourceParseResult,
)
from repo_graph.parsers.python_ast import PythonAstParser
from repo_graph.parsers.registry import (
    ManifestParserRegistry,
    ParserRegistry,
    manifest_parser_registry_for_paths,
    parser_registry_for_plugin_config,
)
from repo_graph.writers.json_writer import graph_to_dict


pytestmark = pytest.mark.unit


def test_discovery_uses_repo_relative_posix_paths_and_excludes_generated_dirs(tmp_path):
    discovery = FileDiscovery(
        repo_root=tmp_path,
        tracked_files_provider=lambda: [
            "server/main.py",
            "client/src/main.js",
            "client/src/App.vue",
            "analysis/code_graph/repo_graph.json",
            "client/node_modules/vue/index.js",
            "client/dist/assets/app.js",
            ".venv/lib/python/site-packages/x.py",
            "uploads/session.mov",
            "__pycache__/main.pyc",
        ],
    )

    files = discovery.discover()
    paths = [item.path for item in files]

    assert paths == [
        "client/src/App.vue",
        "client/src/main.js",
        "server/main.py",
    ]
    assert all(not path.startswith("/") for path in paths)
    assert all("\\" not in path for path in paths)


def test_discovery_accepts_configured_include_and_exclude_rules(tmp_path):
    discovery = FileDiscovery(
        repo_root=tmp_path,
        tracked_files_provider=lambda: [
            "server/main.py",
            "docs/guide.txt",
            "Dockerfile",
            "generated/guide.txt",
            "analysis/code_graph_backup/keep.txt",
        ],
        config=FileDiscoveryConfig(
            include_suffixes=(".txt",),
            include_filenames=("Dockerfile",),
            exclude_prefixes=("generated",),
        ),
    )

    assert [item.path for item in discovery.discover()] == [
        "Dockerfile",
        "analysis/code_graph_backup/keep.txt",
        "docs/guide.txt",
    ]


def test_python_ast_parser_extracts_imports_routes_and_syntax_errors_without_importing():
    source = """
from fastapi import APIRouter
from server.routers import auth
from ..services import db_proxy

router = APIRouter(prefix="/auth")

@router.post("/login")
async def login():
    raise RuntimeError("would run if imported")
"""

    result = PythonAstParser().parse(
        path="server/routers/auth.py",
        module_name="server.routers.auth",
        source=source,
    )

    assert [item.target_module for item in result.imports] == [
        "fastapi",
        "server.routers.auth",
        "server.services.db_proxy",
    ]
    assert result.router_prefixes == {"router": "/auth"}
    assert len(result.endpoints) == 1
    assert result.endpoints[0].method == "POST"
    assert result.endpoints[0].router_name == "router"
    assert result.endpoints[0].path == "/login"
    assert result.endpoints[0].evidence.line == 8
    assert result.unsupported == []

    bad_result = PythonAstParser().parse(
        path="server/broken.py",
        module_name="server.broken",
        source="def broken(:\n",
    )

    assert bad_result.imports == []
    assert bad_result.endpoints == []
    assert bad_result.unsupported[0].reason == "python_syntax_error"
    assert bad_result.unsupported[0].path == "server/broken.py"
    assert bad_result.unsupported[0].line == 1


def test_python_ast_parser_does_not_double_count_include_router():
    source = """
from fastapi import FastAPI
from server.routers import auth

app = FastAPI()
app.include_router(auth.router, prefix="/api")
"""

    result = PythonAstParser().parse(
        path="server/main.py",
        module_name="server.main",
        source=source,
    )

    assert len(result.include_routers) == 1
    assert result.include_routers[0].target_module == "server.routers.auth"
    assert result.include_routers[0].prefix == "/api"


def test_python_ast_parser_marks_pytest_convention_files_as_tests():
    result = PythonAstParser().parse(
        path="analysis/app/Sample/test_gt_loader.py",
        module_name="analysis.app.Sample.test_gt_loader",
        source="",
    )

    assert result.node is not None
    assert result.node.type == "test_file"

    doc_result = PythonAstParser().parse(
        path="docs/test_reliability_improvement_plan.md",
        module_name="docs.test_reliability_improvement_plan",
        source="",
    )
    assert doc_result.node is not None
    assert doc_result.node.type == "python_module"


def test_build_graph_uses_parser_dtos_and_keeps_js_vue_unsupported(tmp_path):
    (tmp_path / "server/routers").mkdir(parents=True)
    (tmp_path / "server/services").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "client/src").mkdir(parents=True)
    (tmp_path / "client").mkdir(exist_ok=True)
    (tmp_path / "docs").mkdir()

    (tmp_path / "server/main.py").write_text(
        """
from fastapi import FastAPI
from server.routers import auth

app = FastAPI()
app.include_router(auth.router, prefix="/api")

@app.get("/api/health")
async def health_check():
    return {"status": "ok"}
""",
        encoding="utf-8",
    )
    (tmp_path / "server/routers/auth.py").write_text(
        """
from fastapi import APIRouter
from server.services import auth_provider

router = APIRouter(prefix="/auth")

@router.post("/login")
async def login():
    return {}
""",
        encoding="utf-8",
    )
    (tmp_path / "server/services/auth_provider.py").write_text("", encoding="utf-8")
    (tmp_path / "tests/test_auth.py").write_text(
        "from server.routers import auth\n",
        encoding="utf-8",
    )
    (tmp_path / "client/src/main.js").write_text(
        "import App from './App.vue'\n",
        encoding="utf-8",
    )
    (tmp_path / "client/src/App.vue").write_text("<script setup></script>\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "example-app"
dependencies = ["fastapi>=0.115.0"]
""",
        encoding="utf-8",
    )
    (tmp_path / "client/package.json").write_text(
        '{"dependencies": {"vue": "^3.5.0"}, "devDependencies": {"vite": "^8.0.0"}}',
        encoding="utf-8",
    )
    (tmp_path / "docs/index.md").write_text("# Docs\n", encoding="utf-8")

    graph = build_graph(
        repo_root=tmp_path,
        source_files=[
            SourceFile(path="server/main.py", language="python"),
            SourceFile(path="server/routers/auth.py", language="python"),
            SourceFile(path="server/services/auth_provider.py", language="python"),
            SourceFile(path="tests/test_auth.py", language="python"),
            SourceFile(path="client/src/main.js", language="javascript"),
            SourceFile(path="client/src/App.vue", language="vue"),
            SourceFile(path="pyproject.toml", language="toml"),
            SourceFile(path="client/package.json", language="json"),
            SourceFile(path="docs/index.md", language="markdown"),
        ],
        parser_registry=ParserRegistry(
            parsers={"python": PythonAstParser()},
            deferred_languages={
                "javascript": "JS/Vue graph is deferred to Phase 2",
                "vue": "JS/Vue graph is deferred to Phase 2",
            },
        ),
        manifest_registry=manifest_parser_registry_for_paths(("pyproject.toml", "client/package.json")),
    )
    data = graph_to_dict(graph)
    node_ids = [node["id"] for node in data["nodes"]]
    roles_by_path = {item["path"]: item["role"] for item in data["sourceManifest"]}
    edge_keys = [
        (
            edge["source"],
            edge["target"],
            edge["type"],
            edge["confidence"],
            tuple((item["path"], item["kind"], item["line"]) for item in edge["evidence"]),
            tuple(sorted(edge["metadata"].items())),
        )
        for edge in data["edges"]
    ]

    assert len(node_ids) == len(set(node_ids))
    assert len(edge_keys) == len(set(edge_keys))
    assert roles_by_path["client/src/main.js"] == "source"
    assert roles_by_path["client/package.json"] == "manifest"
    assert roles_by_path["docs/index.md"] == "inventory_only"
    assert "file:docs/index.md" not in node_ids
    edge_keys = {(edge["source"], edge["target"], edge["type"]) for edge in data["edges"]}
    assert ("py:server.main", "py:server.routers.auth", "imports") in edge_keys
    assert ("py:server.routers.auth", "api:POST /api/auth/login", "exposes_endpoint") in edge_keys
    assert ("py:server.main", "api:GET /api/health", "exposes_endpoint") in edge_keys
    assert ("py:tests.test_auth", "py:server.routers.auth", "tests") in edge_keys
    assert ("package:python:example-app", "pypi:fastapi", "package_depends_on") in edge_keys
    assert ("package:npm:client", "npm:vue", "package_depends_on") in edge_keys

    unsupported = {(item["path"], item["reason"]) for item in data["unsupported"]}
    assert ("client/src/main.js", "parser_not_enabled") in unsupported
    assert ("client/src/App.vue", "parser_not_enabled") in unsupported
    assert not any(edge["source"].endswith(".js") for edge in data["edges"])
    assert not any(edge["type"] == "renders_view" for edge in data["edges"])


def test_source_manifest_roles_distinguish_inventory_only_files(tmp_path):
    (tmp_path / "server").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "server/main.py").write_text("", encoding="utf-8")
    (tmp_path / "docs/index.md").write_text("# Docs\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "example-app"
dependencies = []
""",
        encoding="utf-8",
    )

    graph = build_graph(
        repo_root=tmp_path,
        source_files=[
            SourceFile(path="server/main.py", language="python"),
            SourceFile(path="docs/index.md", language="markdown"),
            SourceFile(path="pyproject.toml", language="toml"),
        ],
        parser_registry=ParserRegistry(parsers={"python": PythonAstParser()}),
        manifest_registry=manifest_parser_registry_for_paths(("pyproject.toml",)),
    )
    data = graph_to_dict(graph)

    roles_by_path = {item["path"]: item["role"] for item in data["sourceManifest"]}
    node_ids = {node["id"] for node in data["nodes"]}

    assert roles_by_path == {
        "server/main.py": "source",
        "docs/index.md": "inventory_only",
        "pyproject.toml": "manifest",
    }
    assert "file:docs/index.md" not in node_ids


def test_manifest_registry_accepts_configured_package_files(tmp_path):
    (tmp_path / "server").mkdir()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "client").mkdir()
    (tmp_path / "server/main.py").write_text("", encoding="utf-8")
    (tmp_path / "frontend/package.json").write_text(
        '{"name": "frontend-app", "dependencies": {"vue": "^3.5.0"}}',
        encoding="utf-8",
    )
    (tmp_path / "client/package.json").write_text(
        '{"name": "ignored-client", "dependencies": {"vite": "^8.0.0"}}',
        encoding="utf-8",
    )

    graph = build_graph(
        repo_root=tmp_path,
        source_files=[
            SourceFile(path="server/main.py", language="python"),
            SourceFile(path="frontend/package.json", language="json"),
            SourceFile(path="client/package.json", language="json"),
        ],
        parser_registry=ParserRegistry(parsers={"python": PythonAstParser()}),
        manifest_registry=manifest_parser_registry_for_paths(("frontend/package.json",)),
    )
    data = graph_to_dict(graph)
    roles_by_path = {item["path"]: item["role"] for item in data["sourceManifest"]}
    edge_keys = {(edge["source"], edge["target"], edge["type"]) for edge in data["edges"]}

    assert roles_by_path["frontend/package.json"] == "manifest"
    assert roles_by_path["client/package.json"] == "inventory_only"
    assert ("package:npm:frontend-app", "npm:vue", "package_depends_on") in edge_keys
    assert ("package:npm:ignored-client", "npm:vite", "package_depends_on") not in edge_keys


def test_manifest_registry_rejects_unsupported_package_file_names():
    with pytest.raises(ValueError, match="unsupported package manifest file"):
        manifest_parser_registry_for_paths(("requirements.txt",))


def test_parser_registry_for_config_marks_disabled_python_as_unsupported(tmp_path):
    (tmp_path / "server").mkdir()
    (tmp_path / "server/main.py").write_text("def health():\n    return 'ok'\n", encoding="utf-8")
    source_files = [SourceFile(path="server/main.py", language="python")]

    graph = build_graph(
        repo_root=tmp_path,
        source_files=source_files,
        parser_registry=parser_registry_for_plugin_config(
            repo_root=tmp_path,
            source_files=source_files,
            plugins={"python_ast": {"enabled": False}},
        ),
        manifest_registry=ManifestParserRegistry({}),
    )
    data = graph_to_dict(graph)

    assert data["unsupported"] == [
        {
            "path": "server/main.py",
            "language": "python",
            "reason": "parser_not_enabled",
            "message": "Python AST parser disabled by config",
            "phase": "mvp_v0_1",
            "line": None,
        }
    ]


def test_build_graph_requires_explicit_registry_dependencies(tmp_path):
    (tmp_path / "server").mkdir()
    (tmp_path / "server/main.py").write_text("", encoding="utf-8")
    source_files = [SourceFile(path="server/main.py", language="python")]

    with pytest.raises(ValueError, match="parser_registry is required"):
        build_graph(
            repo_root=tmp_path,
            source_files=source_files,
            manifest_registry=ManifestParserRegistry({}),
        )

    with pytest.raises(ValueError, match="manifest_registry is required"):
        build_graph(
            repo_root=tmp_path,
            source_files=source_files,
            parser_registry=ParserRegistry({}),
        )


def test_build_graph_accepts_parser_registry_instead_of_concrete_parser(tmp_path):
    (tmp_path / "server/services").mkdir(parents=True)
    (tmp_path / "server/main.py").write_text("", encoding="utf-8")
    (tmp_path / "server/services/auth_provider.py").write_text("", encoding="utf-8")

    class StubParser:
        def __init__(self) -> None:
            self.calls = []

        def parse(self, path, module_name, source, known_modules=None):
            self.calls.append((path, module_name, source, set(known_modules or ())))
            result = PythonParseResult(path=path, module_name=module_name)
            if path == "server/main.py":
                result.imports.append(
                    ImportRelation(
                        source_module=module_name,
                        target_module="server.services.auth_provider",
                        evidence=Evidence(path=path, kind="stub_import", line=1),
                    )
                )
            return result

    parser = StubParser()
    graph = build_graph(
        repo_root=tmp_path,
        source_files=[
            SourceFile(path="server/main.py", language="python"),
            SourceFile(path="server/services/auth_provider.py", language="python"),
        ],
        parser_registry=ParserRegistry({"python": parser}),
        manifest_registry=ManifestParserRegistry({}),
    )
    data = graph_to_dict(graph)

    assert [call[0] for call in parser.calls] == [
        "server/main.py",
        "server/services/auth_provider.py",
    ]
    assert ("server.services.auth_provider" in parser.calls[0][3])
    assert {
        ("py:server.main", "py:server.services.auth_provider", "imports")
    } <= {(edge["source"], edge["target"], edge["type"]) for edge in data["edges"]}


def test_build_graph_merges_registered_parser_graph_fragments(tmp_path):
    (tmp_path / "client/src").mkdir(parents=True)
    (tmp_path / "client/src/main.js").write_text("import './App.vue'\n", encoding="utf-8")
    (tmp_path / "client/src/App.vue").write_text("<script setup></script>\n", encoding="utf-8")

    class JavaScriptStubParser:
        def parse(self, path, module_name, source, known_modules=None):
            node = Node(
                id=f"file:{path}",
                type="file",
                name=path,
                path=path,
                language="javascript",
            )
            edge = Edge(
                source=f"file:{path}",
                target="file:client/src/App.vue",
                type="imports",
                confidence="high",
                evidence=[Evidence(path=path, kind="stub_import", line=1)],
            )
            return SourceParseResult(
                path=path,
                module_name=module_name,
                language="javascript",
                source_id=f"file:{path}",
                node=node,
                edges=[edge],
            )

    graph = build_graph(
        repo_root=tmp_path,
        source_files=[
            SourceFile(path="client/src/main.js", language="javascript"),
            SourceFile(path="client/src/App.vue", language="vue"),
        ],
        parser_registry=ParserRegistry(
            parsers={"javascript": JavaScriptStubParser()},
            deferred_languages={"vue": "Vue graph is deferred to Phase 2"},
        ),
        manifest_registry=ManifestParserRegistry({}),
    )
    data = graph_to_dict(graph)

    assert {
        ("file:client/src/main.js", "file:client/src/App.vue", "imports")
    } <= {(edge["source"], edge["target"], edge["type"]) for edge in data["edges"]}
    assert ("client/src/main.js", "parser_not_enabled") not in {
        (item["path"], item["reason"]) for item in data["unsupported"]
    }
