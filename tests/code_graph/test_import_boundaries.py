from __future__ import annotations

import ast
import importlib
import sys
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

from repo_graph.parsers.codeql_candidates import parse_codeql_candidate_text
from repo_graph.parsers.js_vue_structure import JavaScriptVueStructureParser
from repo_graph.parsers.python_ast import PythonAstParser
from repo_graph.model import SourceFile, SourceParseResult
from repo_graph.core.registry import ParserRegistry as CoreParserRegistry


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROOT = REPO_ROOT / "repo_graph"
CLIENT_PACKAGE_ROOT = REPO_ROOT
CONCRETE_PARSER_MODULES = {
    "repo_graph.parsers.codeql_candidates",
    "repo_graph.parsers.js_vue_structure",
    "repo_graph.parsers.manifests",
    "repo_graph.parsers.python_ast",
}
CONCRETE_IMPORT_ALLOWLIST = {
    Path("repo_graph/plugins/registry.py"),
}
PROTECTED_IMPORTERS = {
    Path("repo_graph/orchestrator.py"),
    Path("repo_graph/query.py"),
    Path("repo_graph/query_cli.py"),
    Path("repo_graph/mcp_server.py"),
}
PROTECTED_PREFIXES = (
    Path("repo_graph/core"),
    Path("repo_graph/builders"),
)


def test_concrete_parser_imports_stay_behind_plugin_registry():
    offenders = []
    for path in sorted(TOOLS_ROOT.rglob("*.py")):
        relative_path = path.relative_to(REPO_ROOT)
        if relative_path in CONCRETE_IMPORT_ALLOWLIST:
            continue
        imported = imported_modules(path)
        concrete_imports = sorted(module for module in imported if is_concrete_parser_import(module))
        if concrete_imports:
            offenders.append((relative_path.as_posix(), concrete_imports))

    assert offenders == []


def test_core_builder_query_and_mcp_do_not_import_plugin_modules():
    checked_paths = set(PROTECTED_IMPORTERS)
    for prefix in PROTECTED_PREFIXES:
        checked_paths.update(path.relative_to(REPO_ROOT) for path in (REPO_ROOT / prefix).rglob("*.py"))

    offenders = []
    for relative_path in sorted(checked_paths):
        imported = imported_modules(REPO_ROOT / relative_path)
        plugin_imports = sorted(module for module in imported if module.startswith("repo_graph.plugins"))
        if plugin_imports:
            offenders.append((relative_path.as_posix(), plugin_imports))

    assert offenders == []


def test_legacy_parser_registry_core_import_does_not_load_plugin_registry():
    sys.modules.pop("repo_graph.parsers.registry", None)
    sys.modules.pop("repo_graph.plugins.registry", None)

    legacy_registry = importlib.import_module("repo_graph.parsers.registry")

    assert legacy_registry.ParserRegistry is CoreParserRegistry
    assert "repo_graph.plugins.registry" not in sys.modules


def test_parser_adapters_return_core_dtos_only(tmp_path):
    python_result = PythonAstParser().parse(
        path="server/main.py",
        module_name="server.main",
        source="from server.services import auth\n",
        known_modules={"server.services.auth"},
    )

    (tmp_path / "client/src").mkdir(parents=True)
    (tmp_path / "client/src/main.js").write_text("import './helper.js'\n", encoding="utf-8")
    (tmp_path / "client/src/helper.js").write_text("export const helper = true\n", encoding="utf-8")
    js_parser = JavaScriptVueStructureParser(
        repo_root=tmp_path,
        source_files=[
            SourceFile(path="client/src/main.js", language="javascript"),
            SourceFile(path="client/src/helper.js", language="javascript"),
        ],
        client_package_root=CLIENT_PACKAGE_ROOT,
    )
    js_result = js_parser.parse(
        path="client/src/main.js",
        module_name="client/src/main.js",
        source=(tmp_path / "client/src/main.js").read_text(encoding="utf-8"),
    )

    codeql_relations = parse_codeql_candidate_text(
        """
{
  "relations": [
    {
      "type": "calls",
      "queryId": "fixture/python/calls",
      "source": {
        "path": "server/main.py",
        "qualifiedName": "server.main.read",
        "kind": "function",
        "line": 1
      },
      "target": {
        "path": "server/services/auth.py",
        "qualifiedName": "server.services.auth.login",
        "kind": "function",
        "line": 4
      },
      "evidence": {"path": "server/main.py", "kind": "codeql_call", "line": 1}
    }
  ]
}
""",
        repo_root=tmp_path,
        known_paths=["server/main.py", "server/services/auth.py"],
    )

    assert isinstance(python_result, SourceParseResult)
    assert isinstance(js_result, SourceParseResult)
    assert_core_dto_tree(python_result)
    assert_core_dto_tree(js_result)
    assert_core_dto_tree(codeql_relations)


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
            modules.update(
                f"{node.module}.{alias.name}"
                for alias in node.names
                if alias.name != "*"
            )
    return modules


def is_concrete_parser_import(module: str) -> bool:
    return any(module == concrete or module.startswith(f"{concrete}.") for concrete in CONCRETE_PARSER_MODULES)


def assert_core_dto_tree(value: Any) -> None:
    if is_dataclass(value) and not isinstance(value, type):
        assert type(value).__module__ == "repo_graph.core.model"
        for field in fields(value):
            assert_core_dto_tree(getattr(value, field.name))
        return
    if isinstance(value, dict):
        for key, item in value.items():
            assert_core_dto_tree(key)
            assert_core_dto_tree(item)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            assert_core_dto_tree(item)
        return
    assert not isinstance(value, ast.AST)
