from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from repo_graph.core.model import SourceFile
from repo_graph.core.registry import ManifestParser, ManifestParserRegistry, ParserRegistry, SourceParser
from repo_graph.parsers.js_vue_structure import (
    DEFAULT_API_BASE,
    DEFAULT_API_CLIENT_NAMES,
    DEFAULT_API_URL_HELPER_NAMES,
    DEFAULT_JS_VUE_ALIASES,
    DEFAULT_JS_VUE_EXTENSIONS,
    DEFAULT_USE_API_FACTORY_NAMES,
    DEFAULT_VUE_ROUTER_FILES,
    JavaScriptVueParserConfig,
    JavaScriptVueStructureParser,
)
from repo_graph.parsers.codeql_candidates import load_codeql_candidate_relations
from repo_graph.parsers.manifests import parse_package_json, parse_pyproject
from repo_graph.parsers.python_ast import PythonAstParser
from repo_graph.source_paths import DEFAULT_FRONTEND_TEST_ROOTS, normalize_test_roots

__all__ = [
    "DEFAULT_MANIFEST_PACKAGE_FILES",
    "default_manifest_package_files",
    "default_manifest_parser_registry",
    "default_parser_registry",
    "frontend_test_roots_for_plugin_config",
    "js_vue_parser_config_for_plugin_config",
    "load_codeql_candidate_relations",
    "manifest_parser_for_path",
    "manifest_parser_registry_for_paths",
    "manifest_parser_registry_for_plugin_config",
    "optional_plugin_string",
    "optional_plugin_string_tuple",
    "parser_registry_for_plugin_config",
    "plugin_aliases",
    "plugin_enabled",
]


DEFAULT_MANIFEST_PACKAGE_FILES = ("pyproject.toml", "client/package.json")


def default_parser_registry(
    repo_root: Path | None = None,
    source_files: Iterable[SourceFile] | None = None,
) -> ParserRegistry:
    parsers: dict[str, SourceParser] = {"python": PythonAstParser()}
    deferred_languages = {
        "javascript": "JS/Vue parser requires repository context",
        "vue": "JS/Vue parser requires repository context",
    }
    source_file_list = list(source_files) if source_files is not None else None
    has_js_vue_sources = source_file_list is not None and any(
        source.language in {"javascript", "vue"} for source in source_file_list
    )
    if repo_root is not None and source_file_list is not None and has_js_vue_sources:
        js_vue_parser = JavaScriptVueStructureParser(repo_root=repo_root, source_files=source_file_list)
        parsers["javascript"] = js_vue_parser
        parsers["vue"] = js_vue_parser
        deferred_languages = {}
    return ParserRegistry(
        parsers=parsers,
        deferred_languages=deferred_languages,
    )


def parser_registry_for_plugin_config(
    repo_root: Path,
    source_files: Iterable[SourceFile],
    plugins: Mapping[str, Mapping[str, Any]],
) -> ParserRegistry:
    parsers: dict[str, SourceParser] = {}
    deferred_languages: dict[str, str] = {}
    source_file_list = list(source_files)

    if plugin_enabled(plugins, "python_ast", default=True):
        parsers["python"] = PythonAstParser()
    else:
        deferred_languages["python"] = "Python AST parser disabled by config"

    js_vue_languages = {"javascript", "vue"}
    has_js_vue_sources = any(source.language in js_vue_languages for source in source_file_list)
    if plugin_enabled(plugins, "js_vue", default=True):
        if has_js_vue_sources:
            js_vue_plugin = plugins.get("js_vue", {})
            client_package_root = repo_root / optional_plugin_string(
                js_vue_plugin,
                "client_package_root",
                default="client",
            )
            js_vue_parser = JavaScriptVueStructureParser(
                repo_root=repo_root,
                source_files=source_file_list,
                client_package_root=client_package_root,
                node_bin=optional_plugin_string(js_vue_plugin, "node_bin", default="node"),
                frontend_test_roots=frontend_test_roots_for_plugin_config(plugins),
                parser_config=js_vue_parser_config_for_plugin_config(plugins),
            )
            parsers["javascript"] = js_vue_parser
            parsers["vue"] = js_vue_parser
    else:
        for language in js_vue_languages:
            deferred_languages[language] = "JS/Vue parser disabled by config"

    return ParserRegistry(parsers=parsers, deferred_languages=deferred_languages)


def default_manifest_parser_registry() -> ManifestParserRegistry:
    return manifest_parser_registry_for_paths(DEFAULT_MANIFEST_PACKAGE_FILES)


def manifest_parser_registry_for_plugin_config(
    plugins: Mapping[str, Mapping[str, Any]],
) -> ManifestParserRegistry:
    if not plugin_enabled(plugins, "manifests", default=True):
        return ManifestParserRegistry({})
    manifests = plugins.get("manifests", {})
    package_files = manifests.get("package_files") or default_manifest_package_files()
    return manifest_parser_registry_for_paths(tuple(str(path) for path in package_files))


def manifest_parser_registry_for_paths(package_files: Iterable[str]) -> ManifestParserRegistry:
    parsers: dict[str, ManifestParser] = {}
    for path in package_files:
        parser = manifest_parser_for_path(path)
        parsers[path] = parser
    return ManifestParserRegistry(parsers)


def manifest_parser_for_path(path: str) -> ManifestParser:
    filename = Path(path).name
    if filename == "pyproject.toml":
        return parse_pyproject
    if filename == "package.json":
        return parse_package_json
    raise ValueError(f"unsupported package manifest file: {path}")


def default_manifest_package_files() -> tuple[str, ...]:
    return DEFAULT_MANIFEST_PACKAGE_FILES


def js_vue_parser_config_for_plugin_config(
    plugins: Mapping[str, Mapping[str, Any]],
) -> JavaScriptVueParserConfig:
    js_vue = plugins.get("js_vue", {})
    vue_router = plugins.get("vue_router", {})
    frontend_api_calls = plugins.get("frontend_api_calls", {})
    return JavaScriptVueParserConfig(
        extensions=optional_plugin_string_tuple(js_vue, "extensions", default=DEFAULT_JS_VUE_EXTENSIONS),
        aliases=plugin_aliases(js_vue),
        router_files=optional_plugin_string_tuple(vue_router, "router_files", default=DEFAULT_VUE_ROUTER_FILES),
        vue_router_enabled=plugin_enabled(plugins, "vue_router", default=True),
        frontend_api_calls_enabled=plugin_enabled(plugins, "frontend_api_calls", default=True),
        api_base=optional_plugin_string(frontend_api_calls, "api_base", default=DEFAULT_API_BASE),
        use_api_factory_names=optional_plugin_string_tuple(
            frontend_api_calls,
            "use_api_factory_names",
            default=DEFAULT_USE_API_FACTORY_NAMES,
        ),
        api_client_names=optional_plugin_string_tuple(
            frontend_api_calls,
            "api_client_names",
            default=DEFAULT_API_CLIENT_NAMES,
        ),
        api_url_helper_names=optional_plugin_string_tuple(
            frontend_api_calls,
            "api_url_helper_names",
            default=DEFAULT_API_URL_HELPER_NAMES,
        ),
        playwright_enabled=plugin_enabled(plugins, "playwright", default=True),
        missing_runtime=optional_plugin_string(js_vue, "missing_runtime", default="error"),
    )


def plugin_aliases(plugin_payload: Mapping[str, Any]) -> Mapping[str, str]:
    aliases = plugin_payload.get("aliases")
    if aliases is None:
        return DEFAULT_JS_VUE_ALIASES
    if not isinstance(aliases, Mapping):
        raise ValueError("plugins.js_vue.aliases must be a table")
    if any(not isinstance(alias, str) or not alias or not alias.endswith("/") for alias in aliases):
        raise ValueError("plugins.js_vue.aliases keys must be non-empty strings ending with '/'")
    if any(not isinstance(target, str) or not target for target in aliases.values()):
        raise ValueError("plugins.js_vue.aliases values must be non-empty strings")
    return dict(aliases)


def frontend_test_roots_for_plugin_config(plugins: Mapping[str, Mapping[str, Any]]) -> tuple[str, ...]:
    if not plugin_enabled(plugins, "playwright", default=True):
        return ()
    playwright = plugins.get("playwright", {})
    test_roots = playwright.get("test_roots")
    if test_roots is None:
        return DEFAULT_FRONTEND_TEST_ROOTS
    if not isinstance(test_roots, list) or any(not isinstance(root, str) for root in test_roots):
        raise ValueError("plugins.playwright.test_roots must be a list of strings")
    return normalize_test_roots(tuple(test_roots))


def plugin_enabled(
    plugins: Mapping[str, Mapping[str, Any]],
    name: str,
    *,
    default: bool,
) -> bool:
    payload = plugins.get(name)
    if payload is None:
        return default
    return payload.get("enabled", default) is True


def optional_plugin_string(payload: Mapping[str, Any], key: str, *, default: str) -> str:
    value = payload.get(key, default)
    if not isinstance(value, str) or not value:
        raise ValueError(f"plugins option `{key}` must be a non-empty string")
    return value


def optional_plugin_string_tuple(
    payload: Mapping[str, Any],
    key: str,
    *,
    default: tuple[str, ...],
) -> tuple[str, ...]:
    value = payload.get(key)
    if value is None:
        return default
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"plugins option `{key}` must be a list of non-empty strings")
    return tuple(value)
