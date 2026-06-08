from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from repo_graph.version import GENERATOR_VERSION, PLUGIN_VERSION

DEFAULT_CONFIG_PATH = Path("codegraph.config.toml")
ENV_CONFIG_PATH = "CODE_GRAPH_CONFIG"
DEFAULT_GENERATOR_VERSION = GENERATOR_VERSION
DEFAULT_PLUGIN_REQUIRED_VERSION = PLUGIN_VERSION
DEFAULT_USAGE_RETENTION_DAYS = 14


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    root: str
    artifact_repo_name: str | None = None


@dataclass(frozen=True)
class GeneratorConfig:
    version: str


@dataclass(frozen=True)
class PluginConfig:
    required_version: str
    root_env: str = "CODE_GRAPH_PLUGIN_ROOT"
    allow_newer_minor: bool = True
    config_required: bool = True


@dataclass(frozen=True)
class OutputsConfig:
    graph: Path
    schema: Path
    summary: Path
    usage_dir: Path


@dataclass(frozen=True)
class McpConfig:
    default_graph: Path
    auto_reload: bool = True


@dataclass(frozen=True)
class UsageConfig:
    enabled_by_default: bool
    log_dir: Path
    retention_days: int


@dataclass(frozen=True)
class DiscoveryConfig:
    include_suffixes: tuple[str, ...]
    include_filenames: tuple[str, ...]
    exclude_prefixes: tuple[str, ...]


@dataclass(frozen=True)
class CodeGraphConfig:
    schema_version: int
    project: ProjectConfig
    generator: GeneratorConfig
    plugin: PluginConfig
    outputs: OutputsConfig
    mcp: McpConfig
    usage: UsageConfig
    discovery: DiscoveryConfig
    plugins: Mapping[str, Mapping[str, Any]]
    path: Path


@dataclass(frozen=True)
class ConfigResolution:
    config: CodeGraphConfig | None
    path: Path | None
    legacy_defaults_used: bool


def resolve_code_graph_config(
    repo_root: Path,
    config_path: Path | None = None,
    env: Mapping[str, str] | None = None,
    require_config: bool = False,
    allow_legacy_defaults: bool = False,
) -> ConfigResolution:
    repo_root = repo_root.resolve()
    env_values = os.environ if env is None else env
    resolved_path = find_config_path(repo_root, config_path, env_values)
    if resolved_path is not None:
        return ConfigResolution(
            config=load_code_graph_config(resolved_path, repo_root=repo_root),
            path=resolved_path,
            legacy_defaults_used=False,
        )
    if require_config or not allow_legacy_defaults:
        raise ValueError(
            "code graph config was not found; pass `--config`, set CODE_GRAPH_CONFIG, "
            "or create codegraph.config.toml"
        )
    return ConfigResolution(config=None, path=None, legacy_defaults_used=True)


def find_config_path(
    repo_root: Path,
    config_path: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path | None:
    if config_path is not None:
        return resolve_cli_path(repo_root, config_path)
    env_values = os.environ if env is None else env
    env_path = env_values.get(ENV_CONFIG_PATH)
    if env_path:
        return resolve_cli_path(repo_root, Path(env_path))
    default_path = repo_root / DEFAULT_CONFIG_PATH
    return default_path if default_path.exists() else None


def load_code_graph_config(path: Path, repo_root: Path) -> CodeGraphConfig:
    config_path = resolve_cli_path(repo_root, path)
    if not config_path.exists():
        raise FileNotFoundError(f"code graph config does not exist: {config_path}")
    with config_path.open("rb") as config_file:
        payload = tomllib.load(config_file)
    if not isinstance(payload, dict):
        raise ValueError("code graph config must be a TOML table")
    validate_unknown_keys(payload)
    return config_from_payload(payload, path=config_path, repo_root=repo_root.resolve())


def config_from_payload(payload: Mapping[str, Any], path: Path, repo_root: Path) -> CodeGraphConfig:
    schema_version = required_int(payload, "schema_version")
    if schema_version != 1:
        raise ValueError(f"unsupported code graph config schema_version: {schema_version}")

    project_payload = required_mapping(payload, "project")
    outputs_payload = required_mapping(payload, "outputs")
    generator_payload = optional_mapping(payload, "generator")
    plugin_payload = optional_mapping(payload, "plugin")
    mcp_payload = optional_mapping(payload, "mcp")
    usage_payload = optional_mapping(payload, "usage")
    discovery_payload = required_mapping(payload, "discovery")
    plugins_payload = required_mapping(payload, "plugins")

    project_root = validated_config_path(required_string(project_payload, "root"), field="project.root")
    if project_root != ".":
        raise ValueError("code graph config `project.root` only supports '.' in Phase 1A")

    project = ProjectConfig(
        name=required_string(project_payload, "name"),
        root=project_root,
        artifact_repo_name=optional_string(project_payload, "artifact_repo_name"),
    )
    generator = GeneratorConfig(
        version=optional_string(generator_payload, "version") or DEFAULT_GENERATOR_VERSION,
    )
    plugin = PluginConfig(
        required_version=optional_string(plugin_payload, "required_version") or DEFAULT_PLUGIN_REQUIRED_VERSION,
        root_env=optional_string(plugin_payload, "root_env") or "CODE_GRAPH_PLUGIN_ROOT",
        allow_newer_minor=optional_bool(plugin_payload, "allow_newer_minor", default=True),
        config_required=optional_bool(plugin_payload, "config_required", default=True),
    )
    outputs = OutputsConfig(
        graph=validated_output_path(outputs_payload, "graph", repo_root),
        schema=validated_output_path(outputs_payload, "schema", repo_root),
        summary=validated_output_path(outputs_payload, "summary", repo_root),
        usage_dir=validated_output_path(outputs_payload, "usage_dir", repo_root),
    )
    mcp = McpConfig(
        default_graph=validated_output_path_or_default(
            mcp_payload,
            "default_graph",
            repo_root,
            default=outputs.graph.as_posix(),
            field="mcp.default_graph",
        ),
        auto_reload=optional_bool(mcp_payload, "auto_reload", default=True),
    )
    usage = UsageConfig(
        enabled_by_default=optional_bool(usage_payload, "enabled_by_default", default=False),
        log_dir=validated_output_path_or_default(
            usage_payload,
            "log_dir",
            repo_root,
            default=outputs.usage_dir.as_posix(),
            field="usage.log_dir",
        ),
        retention_days=optional_positive_int(
            usage_payload,
            "retention_days",
            default=DEFAULT_USAGE_RETENTION_DAYS,
        ),
    )
    discovery = DiscoveryConfig(
        include_suffixes=required_string_tuple(discovery_payload, "include_suffixes"),
        include_filenames=optional_string_tuple(discovery_payload, "include_filenames", default=()),
        exclude_prefixes=tuple(
            normalize_exclude_prefix(validated_config_path(item, field="discovery.exclude_prefixes"))
            for item in required_string_tuple(discovery_payload, "exclude_prefixes")
        ),
    )
    validate_plugin_payloads(plugins_payload)
    ensure_output_paths_do_not_overlap(outputs)
    ensure_outputs_are_excluded(outputs, discovery)
    ensure_manifest_package_files_are_discoverable(plugins_payload, discovery)
    return CodeGraphConfig(
        schema_version=schema_version,
        project=project,
        generator=generator,
        plugin=plugin,
        outputs=outputs,
        mcp=mcp,
        usage=usage,
        discovery=discovery,
        plugins=plugins_payload,
        path=path,
    )


TOP_LEVEL_KEYS = {
    "schema_version",
    "project",
    "generator",
    "plugin",
    "outputs",
    "mcp",
    "usage",
    "discovery",
    "plugins",
}

SECTION_KEYS = {
    "project": {"name", "root", "artifact_repo_name"},
    "generator": {"version"},
    "plugin": {"required_version", "root_env", "allow_newer_minor", "config_required"},
    "outputs": {"graph", "schema", "summary", "usage_dir"},
    "mcp": {"default_graph", "auto_reload"},
    "usage": {"enabled_by_default", "log_dir", "retention_days"},
    "discovery": {"include_suffixes", "include_filenames", "exclude_prefixes"},
}

PLUGIN_KEYS = {
    "python_ast": {"enabled", "internal_roots"},
    "fastapi": {"enabled", "http_methods", "endpoint_node_prefix"},
    "js_vue": {"enabled", "client_package_root", "node_bin", "extensions", "missing_runtime", "aliases"},
    "vue_router": {"enabled", "router_files"},
    "frontend_api_calls": {"enabled", "api_base", "use_api_factory_names", "api_client_names", "api_url_helper_names"},
    "playwright": {"enabled", "test_roots"},
    "manifests": {"enabled", "package_files"},
    "codeql_candidates": {"enabled"},
}


def validate_unknown_keys(payload: Mapping[str, Any]) -> None:
    reject_unknown_keys(payload, TOP_LEVEL_KEYS, "root")
    for section, allowed_keys in SECTION_KEYS.items():
        if section in payload:
            reject_unknown_keys(required_mapping(payload, section), allowed_keys, section)
    plugins = required_mapping(payload, "plugins")
    reject_unknown_keys(plugins, set(PLUGIN_KEYS), "plugins")
    for plugin_name, allowed_keys in PLUGIN_KEYS.items():
        if plugin_name in plugins:
            plugin_payload = required_mapping(plugins, plugin_name)
            reject_unknown_keys(plugin_payload, allowed_keys, f"plugins.{plugin_name}")


def validate_plugin_payloads(plugins: Mapping[str, Any]) -> None:
    for plugin_name in sorted(plugins):
        plugin_payload = required_mapping(plugins, plugin_name)
        if not isinstance(plugin_payload.get("enabled"), bool):
            raise ValueError(f"plugins.{plugin_name}.enabled must be a boolean")
        validate_plugin_paths(plugin_name, plugin_payload)
        validate_plugin_values(plugin_name, plugin_payload)
    js_vue = plugins.get("js_vue")
    if isinstance(js_vue, dict) and "aliases" in js_vue:
        aliases = required_mapping(js_vue, "aliases")
        for alias, target in aliases.items():
            if not isinstance(alias, str) or not alias or not alias.endswith("/"):
                raise ValueError("plugins.js_vue.aliases keys must be non-empty strings ending with '/'")
            if not isinstance(target, str) or not target:
                raise ValueError("plugins.js_vue.aliases values must be non-empty strings")
            validated_config_path(target, field=f"plugins.js_vue.aliases.{alias}")


def validate_plugin_values(plugin_name: str, plugin_payload: Mapping[str, Any]) -> None:
    string_list_keys = {
        "extensions",
        "router_files",
        "test_roots",
        "package_files",
        "internal_roots",
        "use_api_factory_names",
        "api_client_names",
        "api_url_helper_names",
    }
    for key in string_list_keys:
        if key in plugin_payload:
            required_string_tuple(plugin_payload, key)
    if plugin_name == "frontend_api_calls" and "api_base" in plugin_payload:
        api_base = required_string(plugin_payload, "api_base")
        if not api_base.startswith("/") or api_base == "/":
            raise ValueError("plugins.frontend_api_calls.api_base must start with '/' and include a path segment")
    if plugin_name == "js_vue":
        if "extensions" in plugin_payload:
            for extension in required_string_tuple(plugin_payload, "extensions"):
                if not extension.startswith("."):
                    raise ValueError("plugins.js_vue.extensions entries must start with '.'")
        if "missing_runtime" in plugin_payload:
            missing_runtime = required_string(plugin_payload, "missing_runtime")
            if missing_runtime not in {"error", "skip"}:
                raise ValueError("plugins.js_vue.missing_runtime must be one of: error, skip")


def validate_plugin_paths(plugin_name: str, plugin_payload: Mapping[str, Any]) -> None:
    path_list_keys = {
        "internal_roots",
        "router_files",
        "test_roots",
        "package_files",
    }
    path_string_keys = {"client_package_root"}
    for key in path_list_keys:
        if key in plugin_payload:
            for item in required_string_tuple(plugin_payload, key):
                validated_config_path(item, field=f"plugins.{plugin_name}.{key}")
    for key in path_string_keys:
        if key in plugin_payload:
            validated_config_path(required_string(plugin_payload, key), field=f"plugins.{plugin_name}.{key}")


def reject_unknown_keys(payload: Mapping[str, Any], allowed: set[str], section: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"unknown code graph config key in {section}: {unknown[0]}")


def required_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"code graph config `{key}` must be a table")
    return value


def optional_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"code graph config `{key}` must be a table")
    return value


def required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"code graph config `{key}` must be a non-empty string")
    return value


def optional_string(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"code graph config `{key}` must be a non-empty string")
    return value


def required_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"code graph config `{key}` must be an integer")
    return value


def required_positive_int(payload: Mapping[str, Any], key: str) -> int:
    value = required_int(payload, key)
    if value < 1:
        raise ValueError(f"code graph config `{key}` must be a positive integer")
    return value


def optional_positive_int(payload: Mapping[str, Any], key: str, default: int) -> int:
    if key not in payload:
        return default
    return required_positive_int(payload, key)


def optional_bool(payload: Mapping[str, Any], key: str, default: bool) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"code graph config `{key}` must be a boolean")
    return value


def required_string_tuple(payload: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"code graph config `{key}` must be a list of non-empty strings")
    return tuple(value)


def optional_string_tuple(payload: Mapping[str, Any], key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    if key not in payload:
        return default
    return required_string_tuple(payload, key)


def validated_output_path(payload: Mapping[str, Any], key: str, repo_root: Path) -> Path:
    value = validated_config_path(required_string(payload, key), field=f"outputs.{key}")
    ensure_resolves_inside_repo(repo_root, value)
    return Path(value)


def validated_output_path_or_default(
    payload: Mapping[str, Any],
    key: str,
    repo_root: Path,
    default: str,
    field: str,
) -> Path:
    value = required_string(payload, key) if key in payload else default
    value = validated_config_path(value, field=field)
    ensure_resolves_inside_repo(repo_root, value)
    return Path(value)


def validated_config_path(value: str, field: str) -> str:
    if "\\" in value:
        raise ValueError(f"code graph config `{field}` must be a POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ValueError(f"code graph config `{field}` must be repo-relative")
    if any(part == ".." for part in path.parts):
        raise ValueError(f"code graph config `{field}` must not escape the repo root")
    return normalize_posix_path(value)


def ensure_resolves_inside_repo(repo_root: Path, relative_path: str) -> None:
    candidate = repo_root / relative_path
    existing = candidate if candidate.exists() else first_existing_parent(candidate)
    try:
        existing.resolve().relative_to(repo_root.resolve())
    except ValueError as exc:
        raise ValueError(f"code graph config path escapes repo root: {relative_path}") from exc


def first_existing_parent(path: Path) -> Path:
    current = path
    while not current.exists() and current.parent != current:
        current = current.parent
    return current


def ensure_outputs_are_excluded(outputs: OutputsConfig, discovery: DiscoveryConfig) -> None:
    output_paths = (
        outputs.graph.as_posix(),
        outputs.schema.as_posix(),
        outputs.summary.as_posix(),
        outputs.usage_dir.as_posix(),
    )
    for output_path in output_paths:
        if not any(path_is_inside_directory(output_path, prefix) for prefix in discovery.exclude_prefixes):
            raise ValueError(
                "code graph generated artifact path must be covered by discovery.exclude_prefixes: "
                f"{output_path}"
            )


def ensure_manifest_package_files_are_discoverable(
    plugins: Mapping[str, Any],
    discovery: DiscoveryConfig,
) -> None:
    manifests = plugins.get("manifests")
    if not isinstance(manifests, dict) or "package_files" not in manifests:
        return
    if manifests.get("enabled") is False:
        return

    for package_file in required_string_tuple(manifests, "package_files"):
        normalized = validated_config_path(package_file, field="plugins.manifests.package_files")
        if any(path_is_inside_directory(normalized, prefix) for prefix in discovery.exclude_prefixes):
            raise ValueError(
                "plugins.manifests.package_files must not be excluded by discovery.exclude_prefixes: "
                f"{normalized}"
            )
        if not path_matches_discovery_include(normalized, discovery):
            raise ValueError(
                "plugins.manifests.package_files must be covered by discovery.include_suffixes "
                f"or discovery.include_filenames: {normalized}"
            )


def path_matches_discovery_include(path: str, discovery: DiscoveryConfig) -> bool:
    normalized = normalize_posix_path(path)
    return normalized.endswith(discovery.include_suffixes) or normalized in discovery.include_filenames


def ensure_output_paths_do_not_overlap(outputs: OutputsConfig) -> None:
    artifact_paths = (outputs.graph, outputs.schema, outputs.summary)
    if len(set(artifact_paths)) != len(artifact_paths):
        raise ValueError("code graph output artifact paths must be unique")
    usage_dir = normalize_exclude_prefix(outputs.usage_dir.as_posix())
    for artifact_path in artifact_paths:
        if path_is_inside_directory(artifact_path.as_posix(), usage_dir):
            raise ValueError("code graph output artifact paths must not be inside outputs.usage_dir")


def path_is_inside_directory(path: str, directory_prefix: str) -> bool:
    normalized_path = normalize_posix_path(path)
    normalized_directory = normalize_exclude_prefix(directory_prefix)
    return normalized_path.startswith(normalized_directory)


def normalize_exclude_prefix(value: str) -> str:
    normalized = normalize_posix_path(value)
    return normalized if normalized.endswith("/") else f"{normalized}/"


def resolve_cli_path(repo_root: Path, path: Path) -> Path:
    return path if path.is_absolute() else repo_root / path


def normalize_posix_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized
