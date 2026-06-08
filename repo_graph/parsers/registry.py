from __future__ import annotations

from typing import Any

from repo_graph.core.registry import ManifestParser, ManifestParserRegistry, ParserRegistry, SourceParser

_PLUGIN_EXPORTS = {
    "DEFAULT_MANIFEST_PACKAGE_FILES",
    "default_manifest_package_files",
    "default_manifest_parser_registry",
    "default_parser_registry",
    "frontend_test_roots_for_plugin_config",
    "js_vue_parser_config_for_plugin_config",
    "manifest_parser_for_path",
    "manifest_parser_registry_for_paths",
    "manifest_parser_registry_for_plugin_config",
    "optional_plugin_string",
    "optional_plugin_string_tuple",
    "parser_registry_for_plugin_config",
    "plugin_aliases",
    "plugin_enabled",
}

__all__ = [
    "ManifestParser",
    "ManifestParserRegistry",
    "ParserRegistry",
    "SourceParser",
    *_PLUGIN_EXPORTS,
]


def __getattr__(name: str) -> Any:
    if name not in _PLUGIN_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from repo_graph.plugins import registry as plugin_registry

    value = getattr(plugin_registry, name)
    globals()[name] = value
    return value
