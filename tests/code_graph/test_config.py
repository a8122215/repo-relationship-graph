import json
import os
import subprocess
from pathlib import Path

import pytest

from repo_graph.generate import main as generate_main
from repo_graph.config import (
    DEFAULT_GENERATOR_VERSION,
    DEFAULT_PLUGIN_REQUIRED_VERSION,
    ENV_CONFIG_PATH,
    load_code_graph_config,
    resolve_code_graph_config,
)
from repo_graph.version import PLUGIN_VERSION


pytestmark = pytest.mark.unit


CLIENT_PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def test_load_code_graph_config_accepts_minimal_profile_with_defaults(tmp_path):
    config_path = write_config(tmp_path)

    config = load_code_graph_config(config_path, repo_root=tmp_path)

    assert config.schema_version == 1
    assert config.project.name == "ExampleRepo"
    assert config.project.root == "."
    assert config.generator.version == DEFAULT_GENERATOR_VERSION
    assert config.plugin.required_version == DEFAULT_PLUGIN_REQUIRED_VERSION
    assert config.outputs.graph == Path("analysis/code_graph/repo_graph.json")
    assert config.mcp.default_graph == Path("analysis/code_graph/repo_graph.json")
    assert config.usage.log_dir == Path("analysis/code_graph/usage")
    assert config.usage.retention_days == 14
    assert config.discovery.include_filenames == ()
    assert set(config.plugins) == {"python_ast"}


def test_resolve_code_graph_config_uses_explicit_env_default_precedence(tmp_path):
    write_config(tmp_path, path=tmp_path / "codegraph.config.toml", project_name="DefaultRepo")
    write_config(tmp_path, path=tmp_path / "env.toml", project_name="EnvRepo")
    write_config(tmp_path, path=tmp_path / "explicit.toml", project_name="ExplicitRepo")

    explicit = resolve_code_graph_config(
        tmp_path,
        config_path=Path("explicit.toml"),
        env={ENV_CONFIG_PATH: "env.toml"},
    )
    from_env = resolve_code_graph_config(tmp_path, env={ENV_CONFIG_PATH: "env.toml"})
    from_default = resolve_code_graph_config(tmp_path, env={})

    assert explicit.config is not None
    assert explicit.config.project.name == "ExplicitRepo"
    assert from_env.config is not None
    assert from_env.config.project.name == "EnvRepo"
    assert from_default.config is not None
    assert from_default.config.project.name == "DefaultRepo"


def test_resolve_code_graph_config_accepts_absolute_env_config_path(tmp_path):
    env_config = write_config(tmp_path, path=tmp_path / "env.toml", project_name="EnvRepo")

    resolution = resolve_code_graph_config(tmp_path, env={ENV_CONFIG_PATH: str(env_config)})

    assert resolution.config is not None
    assert resolution.config.project.name == "EnvRepo"


def test_missing_config_requires_explicit_legacy_fallback(tmp_path):
    with pytest.raises(ValueError, match="code graph config was not found"):
        resolve_code_graph_config(tmp_path, env={})

    resolution = resolve_code_graph_config(tmp_path, env={}, allow_legacy_defaults=True)
    assert resolution.config is None
    assert resolution.path is None
    assert resolution.legacy_defaults_used is True

    with pytest.raises(ValueError, match="code graph config was not found"):
        resolve_code_graph_config(tmp_path, env={}, require_config=True, allow_legacy_defaults=True)


def test_config_rejects_unknown_top_level_key(tmp_path):
    config_path = write_config(tmp_path, extra_root='unexpected = "value"')

    with pytest.raises(ValueError, match="unknown code graph config key in root: unexpected"):
        load_code_graph_config(config_path, repo_root=tmp_path)


def test_config_rejects_unknown_plugin_or_plugin_key(tmp_path):
    unknown_plugin = write_config(tmp_path, extra_plugins="\n[plugins.unknown]\nenabled = true")

    with pytest.raises(ValueError, match="unknown code graph config key in plugins: unknown"):
        load_code_graph_config(unknown_plugin, repo_root=tmp_path)

    unknown_key = write_config(tmp_path, extra_plugins="\n[plugins.fastapi]\nenabled = false\nextra = true")

    with pytest.raises(ValueError, match="unknown code graph config key in plugins.fastapi: extra"):
        load_code_graph_config(unknown_key, repo_root=tmp_path)


def test_config_accepts_js_vue_feature_disable_flags(tmp_path):
    for plugin_name in ("frontend_api_calls", "playwright", "vue_router"):
        config_path = write_config(
            tmp_path,
            path=tmp_path / f"{plugin_name}.toml",
            extra_plugins=f"\n[plugins.{plugin_name}]\nenabled = false",
        )

        config = load_code_graph_config(config_path, repo_root=tmp_path)

        assert config.plugins[plugin_name]["enabled"] is False


def test_config_validates_js_vue_phase2_plugin_values(tmp_path):
    config_path = write_config(
        tmp_path,
        extra_plugins="""
[plugins.js_vue]
enabled = true
extensions = [".js", ".vue"]
missing_runtime = "skip"

[plugins.js_vue.aliases]
"@/" = "client/src/"
"~ui/" = "client/src/components/"

[plugins.vue_router]
enabled = true
router_files = ["client/src/router/index.js", "client/src/router/**/*.js"]

[plugins.frontend_api_calls]
enabled = true
api_base = "/backend"
use_api_factory_names = ["createApi"]
api_client_names = ["http"]
api_url_helper_names = ["buildApiPath"]

[plugins.playwright]
enabled = true
test_roots = ["custom/e2e"]
""",
    )

    config = load_code_graph_config(config_path, repo_root=tmp_path)

    assert config.plugins["js_vue"]["extensions"] == [".js", ".vue"]
    assert config.plugins["js_vue"]["missing_runtime"] == "skip"
    assert config.plugins["js_vue"]["aliases"]["~ui/"] == "client/src/components/"
    assert config.plugins["vue_router"]["router_files"] == [
        "client/src/router/index.js",
        "client/src/router/**/*.js",
    ]
    assert config.plugins["frontend_api_calls"]["api_base"] == "/backend"
    assert config.plugins["frontend_api_calls"]["api_client_names"] == ["http"]
    assert config.plugins["playwright"]["test_roots"] == ["custom/e2e"]


@pytest.mark.parametrize(
    ("extra_plugins", "error"),
    [
        (
            """
[plugins.js_vue]
enabled = true
extensions = ["js"]
""",
            "plugins.js_vue.extensions entries must start",
        ),
        (
            """
[plugins.js_vue]
enabled = true
missing_runtime = "warn"
""",
            "plugins.js_vue.missing_runtime",
        ),
        (
            """
[plugins.frontend_api_calls]
enabled = true
api_base = "api"
""",
            "plugins.frontend_api_calls.api_base",
        ),
        (
            """
[plugins.frontend_api_calls]
enabled = true
api_base = "/"
""",
            "plugins.frontend_api_calls.api_base",
        ),
        (
            """
[plugins.frontend_api_calls]
enabled = true
api_client_names = "api"
""",
            "api_client_names",
        ),
        (
            """
[plugins.js_vue]
enabled = true

[plugins.js_vue.aliases]
"" = "client/src/"
""",
            "plugins.js_vue.aliases keys",
        ),
        (
            """
[plugins.js_vue]
enabled = true

[plugins.js_vue.aliases]
"@" = "client/src/"
""",
            "plugins.js_vue.aliases keys",
        ),
        (
            """
[plugins.js_vue]
enabled = true

[plugins.js_vue.aliases]
"@/" = ""
""",
            "plugins.js_vue.aliases values",
        ),
    ],
)
def test_config_rejects_invalid_js_vue_phase2_plugin_values(tmp_path, extra_plugins, error):
    config_path = write_config(tmp_path, extra_plugins=extra_plugins)

    with pytest.raises(ValueError, match=error):
        load_code_graph_config(config_path, repo_root=tmp_path)


def test_config_rejects_absolute_output_path(tmp_path):
    config_path = write_config(tmp_path, graph="/tmp/repo_graph.json")

    with pytest.raises(ValueError, match="outputs.graph.*repo-relative"):
        load_code_graph_config(config_path, repo_root=tmp_path)


def test_config_rejects_parent_escape(tmp_path):
    config_path = write_config(tmp_path, graph="../repo_graph.json")

    with pytest.raises(ValueError, match="outputs.graph.*escape"):
        load_code_graph_config(config_path, repo_root=tmp_path)


def test_config_rejects_project_root_other_than_current_directory(tmp_path):
    config_path = write_config(tmp_path, project_root="server")

    with pytest.raises(ValueError, match="project.root.*only supports"):
        load_code_graph_config(config_path, repo_root=tmp_path)


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink is unavailable")
def test_config_rejects_output_symlink_escape(tmp_path):
    repo_root = tmp_path / "repo"
    outside = tmp_path / "outside"
    repo_root.mkdir()
    outside.mkdir()
    os.symlink(outside, repo_root / "linked-output")
    config_path = write_config(
        repo_root,
        graph="linked-output/repo_graph.json",
        schema="analysis/code_graph/repo_graph.schema.json",
        summary="analysis/code_graph/repo_graph.summary.md",
        usage_dir="analysis/code_graph/usage",
        exclude_prefixes=("analysis/code_graph/", "linked-output/"),
    )

    with pytest.raises(ValueError, match="escapes repo root"):
        load_code_graph_config(config_path, repo_root=repo_root)


def test_config_rejects_generated_outputs_not_excluded_from_discovery(tmp_path):
    config_path = write_config(tmp_path, exclude_prefixes=(".git/",))

    with pytest.raises(ValueError, match="generated artifact path must be covered"):
        load_code_graph_config(config_path, repo_root=tmp_path)


def test_config_normalizes_exclude_prefixes_as_directory_prefixes(tmp_path):
    config_path = write_config(tmp_path, exclude_prefixes=("analysis/code_graph",))

    config = load_code_graph_config(config_path, repo_root=tmp_path)

    assert config.discovery.exclude_prefixes == ("analysis/code_graph/",)


def test_config_rejects_exclude_prefix_boundary_false_positive(tmp_path):
    config_path = write_config(
        tmp_path,
        graph="analysis/code_graph_backup/repo_graph.json",
        schema="analysis/code_graph_backup/repo_graph.schema.json",
        summary="analysis/code_graph_backup/repo_graph.summary.md",
        usage_dir="analysis/code_graph_backup/usage",
        exclude_prefixes=("analysis/code_graph",),
    )

    with pytest.raises(ValueError, match="generated artifact path must be covered"):
        load_code_graph_config(config_path, repo_root=tmp_path)


def test_config_rejects_duplicate_output_artifact_paths(tmp_path):
    config_path = write_config(tmp_path, schema="analysis/code_graph/repo_graph.json")

    with pytest.raises(ValueError, match="output artifact paths must be unique"):
        load_code_graph_config(config_path, repo_root=tmp_path)


def test_config_rejects_output_artifact_inside_usage_dir(tmp_path):
    config_path = write_config(tmp_path, graph="analysis/code_graph/usage/repo_graph.json")

    with pytest.raises(ValueError, match="must not be inside outputs.usage_dir"):
        load_code_graph_config(config_path, repo_root=tmp_path)


def test_config_rejects_unsafe_manifest_package_file_path(tmp_path):
    config_path = write_config(tmp_path, manifest_package_files=("../package.json",))

    with pytest.raises(ValueError, match="plugins.manifests.package_files.*escape"):
        load_code_graph_config(config_path, repo_root=tmp_path)


def test_config_rejects_absolute_manifest_package_file_path(tmp_path):
    config_path = write_config(tmp_path, manifest_package_files=("/package.json",))

    with pytest.raises(ValueError, match="plugins.manifests.package_files.*repo-relative"):
        load_code_graph_config(config_path, repo_root=tmp_path)


def test_config_rejects_manifest_package_file_not_covered_by_discovery(tmp_path):
    config_path = write_config(
        tmp_path,
        include_suffixes=(".py",),
        manifest_package_files=("frontend/package.json",),
    )

    with pytest.raises(ValueError, match="plugins.manifests.package_files.*discovery.include"):
        load_code_graph_config(config_path, repo_root=tmp_path)


def test_config_rejects_manifest_package_file_excluded_from_discovery(tmp_path):
    config_path = write_config(
        tmp_path,
        include_suffixes=(".json",),
        exclude_prefixes=("analysis/code_graph/", "frontend/"),
        manifest_package_files=("frontend/package.json",),
    )

    with pytest.raises(ValueError, match="plugins.manifests.package_files.*discovery.exclude"):
        load_code_graph_config(config_path, repo_root=tmp_path)


def test_generate_cli_requires_config_unless_legacy_defaults_are_allowed(tmp_path, monkeypatch):
    write_minimal_git_repo(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert (
        generate_main(
            [
                "--check",
                "--output",
                "analysis/code_graph/repo_graph.json",
                "--schema-output",
                "analysis/code_graph/repo_graph.schema.json",
                "--summary",
                "analysis/code_graph/repo_graph.summary.md",
            ]
        )
        == 2
    )


def test_generate_cli_uses_configured_output_paths(tmp_path, monkeypatch):
    write_minimal_git_repo(tmp_path)
    write_config(
        tmp_path,
        graph="generated/code_graph/repo_graph.json",
        schema="generated/code_graph/repo_graph.schema.json",
        summary="generated/code_graph/repo_graph.summary.md",
        usage_dir="generated/code_graph/usage",
        exclude_prefixes=("generated/code_graph/",),
    )
    monkeypatch.chdir(tmp_path)

    assert generate_main(["--config", "codegraph.config.toml"]) == 0

    graph_path = tmp_path / "generated/code_graph/repo_graph.json"
    assert graph_path.exists()
    assert (tmp_path / "generated/code_graph/repo_graph.schema.json").exists()
    assert (tmp_path / "generated/code_graph/repo_graph.summary.md").exists()
    assert '"pluginVersion": "repo-relationship-graph@0.1.0"' in graph_path.read_text(encoding="utf-8")


def test_generate_cli_rejects_codeql_results_for_configured_committed_outputs(tmp_path, monkeypatch, capsys):
    write_config(
        tmp_path,
        graph="generated/code_graph/repo_graph.json",
        schema="generated/code_graph/repo_graph.schema.json",
        summary="generated/code_graph/repo_graph.summary.md",
        usage_dir="generated/code_graph/usage",
        exclude_prefixes=("generated/code_graph",),
    )
    monkeypatch.chdir(tmp_path)

    exit_code = generate_main(
        [
            "--config",
            "codegraph.config.toml",
            "--codeql-results",
            "missing-codeql-candidates.json",
            "--output",
            "generated/code_graph/repo_graph.json",
            "--summary",
            "generated/code_graph/repo_graph.summary.md",
        ]
    )

    assert exit_code == 2
    assert "cannot write to committed graph artifacts" in capsys.readouterr().err


def test_generate_cli_uses_artifact_repo_name_in_graph_metadata(tmp_path, monkeypatch):
    write_minimal_git_repo(tmp_path)
    write_config(tmp_path, artifact_repo_name="ArtifactRepo")
    monkeypatch.chdir(tmp_path)

    assert generate_main(["--config", "codegraph.config.toml"]) == 0

    graph_text = (tmp_path / "analysis/code_graph/repo_graph.json").read_text(encoding="utf-8")
    assert '"name": "ArtifactRepo"' in graph_text
    assert f'"pluginVersion": "{PLUGIN_VERSION}"' in graph_text


def test_generate_cli_falls_back_to_repo_root_name_for_graph_metadata(tmp_path, monkeypatch):
    repo_root = tmp_path / "example-repo"
    repo_root.mkdir()
    write_minimal_git_repo(repo_root)
    write_config(repo_root)
    monkeypatch.chdir(repo_root)

    assert generate_main(["--config", "codegraph.config.toml"]) == 0

    graph_text = (repo_root / "analysis/code_graph/repo_graph.json").read_text(encoding="utf-8")
    assert '"name": "example-repo"' in graph_text


def test_generate_cli_rejects_plugin_major_version_mismatch(tmp_path, monkeypatch, capsys):
    write_minimal_git_repo(tmp_path)
    write_config(tmp_path, plugin_required_version="repo-relationship-graph@1.0.0")
    monkeypatch.chdir(tmp_path)

    assert generate_main(["--config", "codegraph.config.toml", "--check"]) == 2

    assert "incompatible code graph plugin major version" in capsys.readouterr().err


def test_generate_cli_reports_unsupported_manifest_parser_without_traceback(tmp_path, monkeypatch, capsys):
    write_minimal_git_repo(tmp_path)
    write_config(
        tmp_path,
        include_filenames=("requirements.txt",),
        manifest_package_files=("requirements.txt",),
    )
    monkeypatch.chdir(tmp_path)

    assert generate_main(["--config", "codegraph.config.toml"]) == 2

    captured = capsys.readouterr()
    assert "unsupported package manifest file: requirements.txt" in captured.err
    assert "Traceback" not in captured.err


def test_generate_cli_honors_js_vue_disabled_without_node_preflight(tmp_path, monkeypatch):
    write_minimal_git_repo(tmp_path)
    (tmp_path / "client/src").mkdir(parents=True)
    (tmp_path / "client/src/main.js").write_text("import './missing.js'\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "client/src/main.js"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    write_config(
        tmp_path,
        include_suffixes=(".py", ".js"),
        extra_plugins="""
[plugins.js_vue]
enabled = false
client_package_root = "missing-client"
""",
    )
    monkeypatch.chdir(tmp_path)

    assert generate_main(["--config", "codegraph.config.toml"]) == 0

    data = json.loads((tmp_path / "analysis/code_graph/repo_graph.json").read_text(encoding="utf-8"))
    unsupported = {
        (item["path"], item["reason"], item["message"])
        for item in data["unsupported"]
    }
    assert (
        "client/src/main.js",
        "parser_not_enabled",
        "JS/Vue parser disabled by config",
    ) in unsupported


def test_generate_cli_honors_manifest_plugin_disabled(tmp_path, monkeypatch):
    write_minimal_git_repo(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "example"
dependencies = ["fastapi"]
""",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "pyproject.toml"], cwd=tmp_path, check=True, capture_output=True)
    write_config(
        tmp_path,
        extra_plugins="""
[plugins.manifests]
enabled = false
package_files = ["pyproject.toml"]
""",
    )
    monkeypatch.chdir(tmp_path)

    assert generate_main(["--config", "codegraph.config.toml"]) == 0

    data = json.loads((tmp_path / "analysis/code_graph/repo_graph.json").read_text(encoding="utf-8"))
    roles_by_path = {item["path"]: item["role"] for item in data["sourceManifest"]}
    assert roles_by_path["pyproject.toml"] == "inventory_only"
    assert not any(edge["type"] == "package_depends_on" for edge in data["edges"])


def test_generate_cli_honors_python_ast_disabled_without_python_edges(tmp_path, monkeypatch):
    write_minimal_git_repo(tmp_path)
    (tmp_path / "server/routers").mkdir(parents=True)
    (tmp_path / "server/services").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "server/routers/foo.py").write_text(
        """
from fastapi import APIRouter

router = APIRouter()

@router.get('/foo')
def read_foo():
    return {'ok': True}
""",
        encoding="utf-8",
    )
    (tmp_path / "server/services/foo.py").write_text("def get_foo():\n    return 'foo'\n", encoding="utf-8")
    (tmp_path / "tests/test_foo.py").write_text(
        "from server.services.foo import get_foo\n\n\ndef test_get_foo():\n    assert get_foo() == 'foo'\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "server/routers/foo.py", "server/services/foo.py", "tests/test_foo.py"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    write_config(tmp_path, python_ast_enabled=False)
    monkeypatch.chdir(tmp_path)

    assert generate_main(["--config", "codegraph.config.toml"]) == 0

    data = json.loads((tmp_path / "analysis/code_graph/repo_graph.json").read_text(encoding="utf-8"))
    unsupported_paths = {item["path"] for item in data["unsupported"]}
    edge_types = {edge["type"] for edge in data["edges"]}

    assert {"server/routers/foo.py", "server/services/foo.py", "tests/test_foo.py"}.issubset(unsupported_paths)
    assert "tests" not in edge_types
    assert "imports" not in edge_types
    assert "exposes_endpoint" not in edge_types


def test_generate_cli_honors_js_vue_feature_disable_flags(tmp_path, monkeypatch):
    write_minimal_git_repo(tmp_path)
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor/client").symlink_to(CLIENT_PACKAGE_ROOT, target_is_directory=True)
    (tmp_path / "server/main.py").write_text(
        """
from fastapi import FastAPI

app = FastAPI()

@app.get('/api/health')
async def health():
    return {}
""",
        encoding="utf-8",
    )
    (tmp_path / "src/views").mkdir(parents=True)
    (tmp_path / "src/router.js").write_text(
        """
export const routes = [
  { path: '/', name: 'Home', component: () => import('./views/HomeView.vue') },
]
""",
        encoding="utf-8",
    )
    (tmp_path / "src/views/HomeView.vue").write_text("<script setup></script>\n", encoding="utf-8")
    (tmp_path / "src/apiClient.js").write_text("async function load() { await fetch('/api/health') }\n", encoding="utf-8")
    (tmp_path / "custom/e2e").mkdir(parents=True)
    (tmp_path / "custom/e2e/home.js").write_text("async function flow({ page }) { await page.goto('/') }\n", encoding="utf-8")
    subprocess.run(
        [
            "git",
            "add",
            "server/main.py",
            "src/router.js",
            "src/views/HomeView.vue",
            "src/apiClient.js",
            "custom/e2e/home.js",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    write_config(
        tmp_path,
        include_suffixes=(".py", ".js", ".vue"),
        extra_plugins="""
[plugins.js_vue]
enabled = true
client_package_root = "vendor/client"

[plugins.vue_router]
enabled = false
router_files = ["src/router.js"]

[plugins.frontend_api_calls]
enabled = false
api_base = "/api"

[plugins.playwright]
enabled = false
test_roots = ["custom/e2e"]
""",
    )
    monkeypatch.chdir(tmp_path)

    assert generate_main(["--config", "codegraph.config.toml"]) == 0

    data = json.loads((tmp_path / "analysis/code_graph/repo_graph.json").read_text(encoding="utf-8"))
    edge_keys = {(edge["source"], edge["target"], edge["type"]) for edge in data["edges"]}
    node_types = {node["type"] for node in data["nodes"]}
    edge_types = {edge["type"] for edge in data["edges"]}

    assert ("file:src/router.js", "file:src/views/HomeView.vue", "imports") in edge_keys
    assert "vue_route" not in node_types
    assert "renders_view" not in edge_types
    assert "calls_api_endpoint" not in edge_types
    assert "e2e_reaches_route" not in edge_types


def test_generate_cli_uses_configured_discovery_and_manifest_paths(tmp_path, monkeypatch):
    write_minimal_git_repo(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "docs/guide.txt").write_text("guide\n", encoding="utf-8")
    (tmp_path / "frontend/package.json").write_text(
        '{"name": "custom-frontend", "dependencies": {"vue": "^3.5.0"}}',
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "docs/guide.txt", "frontend/package.json"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    write_config(
        tmp_path,
        include_suffixes=(".txt", ".json"),
        manifest_package_files=("frontend/package.json",),
    )
    monkeypatch.chdir(tmp_path)

    assert generate_main(["--config", "codegraph.config.toml"]) == 0

    data = json.loads((tmp_path / "analysis/code_graph/repo_graph.json").read_text(encoding="utf-8"))
    roles_by_path = {item["path"]: item["role"] for item in data["sourceManifest"]}
    edge_keys = {(edge["source"], edge["target"], edge["type"]) for edge in data["edges"]}

    assert "server/main.py" not in roles_by_path
    assert roles_by_path["docs/guide.txt"] == "inventory_only"
    assert roles_by_path["frontend/package.json"] == "manifest"
    assert ("package:npm:custom-frontend", "npm:vue", "package_depends_on") in edge_keys


def write_config(
    repo_root: Path,
    *,
    path: Path | None = None,
    project_name: str = "ExampleRepo",
    project_root: str = ".",
    artifact_repo_name: str | None = None,
    plugin_required_version: str | None = None,
    allow_newer_minor: bool = True,
    graph: str = "analysis/code_graph/repo_graph.json",
    schema: str = "analysis/code_graph/repo_graph.schema.json",
    summary: str = "analysis/code_graph/repo_graph.summary.md",
    usage_dir: str = "analysis/code_graph/usage",
    include_suffixes: tuple[str, ...] = (".py", ".toml"),
    include_filenames: tuple[str, ...] | None = None,
    exclude_prefixes: tuple[str, ...] = ("analysis/code_graph/",),
    manifest_package_files: tuple[str, ...] | None = None,
    python_ast_enabled: bool = True,
    extra_root: str = "",
    extra_plugins: str = "",
) -> Path:
    config_path = path or repo_root / "codegraph.config.toml"
    include_suffix_items = ", ".join(f'"{item}"' for item in include_suffixes)
    include_filenames_line = ""
    if include_filenames is not None:
        include_filename_items = ", ".join(f'"{item}"' for item in include_filenames)
        include_filenames_line = f"include_filenames = [{include_filename_items}]"
    exclude_items = ", ".join(f'"{item}"' for item in exclude_prefixes)
    artifact_repo_name_line = f'artifact_repo_name = "{artifact_repo_name}"' if artifact_repo_name is not None else ""
    plugin_section = ""
    if plugin_required_version is not None:
        allow_newer_minor_value = str(allow_newer_minor).lower()
        plugin_section = f"""
[plugin]
required_version = "{plugin_required_version}"
allow_newer_minor = {allow_newer_minor_value}
"""
    manifest_section = ""
    if manifest_package_files is not None:
        manifest_package_file_items = ", ".join(f'"{item}"' for item in manifest_package_files)
        manifest_section = f"""
[plugins.manifests]
enabled = true
package_files = [{manifest_package_file_items}]
"""
    config_path.write_text(
        f"""
schema_version = 1
{extra_root}

[project]
name = "{project_name}"
root = "{project_root}"
{artifact_repo_name_line}
{plugin_section}

[outputs]
graph = "{graph}"
schema = "{schema}"
summary = "{summary}"
usage_dir = "{usage_dir}"

[discovery]
include_suffixes = [{include_suffix_items}]
{include_filenames_line}
exclude_prefixes = [{exclude_items}]

[plugins.python_ast]
enabled = {str(python_ast_enabled).lower()}
{manifest_section}
{extra_plugins}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return config_path


def write_minimal_git_repo(repo_root: Path) -> None:
    (repo_root / "server").mkdir()
    (repo_root / "server/main.py").write_text("def health():\n    return 'ok'\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "add", "server/main.py"], cwd=repo_root, check=True, capture_output=True)
