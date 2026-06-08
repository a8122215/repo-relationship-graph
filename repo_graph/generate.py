from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from repo_graph.config import CodeGraphConfig, resolve_code_graph_config  # noqa: E402
from repo_graph.core.registry import ManifestParserRegistry  # noqa: E402
from repo_graph.discovery import FileDiscoveryConfig  # noqa: E402
from repo_graph.orchestrator import (  # noqa: E402
    DEFAULT_GRAPH_PATH,
    DEFAULT_SCHEMA_PATH,
    DEFAULT_SUMMARY_PATH,
    ParserRegistryFactory,
    generate_output_texts,
    stale_outputs,
    write_outputs,
)
from repo_graph.plugins.registry import (  # noqa: E402
    default_manifest_parser_registry,
    default_parser_registry,
    frontend_test_roots_for_plugin_config,
    load_codeql_candidate_relations,
    manifest_parser_registry_for_plugin_config,
    parser_registry_for_plugin_config,
)
from repo_graph.version import PLUGIN_VERSION, ensure_plugin_version_compatible  # noqa: E402
from repo_graph.writers.json_writer import GraphOutputMetadata  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate AI-agent code graph artifacts.")
    parser.add_argument("--config", type=Path, default=None, help="Path to codegraph.config.toml.")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--schema-output", type=Path, default=None)
    parser.add_argument("--summary", type=Path, default=None)
    parser.add_argument("--check", action="store_true", help="Compare generated artifacts without writing them.")
    parser.add_argument("--include-untracked", action="store_true", help="Include untracked non-ignored files.")
    parser.add_argument(
        "--allow-legacy-defaults",
        action="store_true",
        help="Allow legacy defaults when no code graph config exists.",
    )
    parser.add_argument(
        "--codeql-results",
        type=Path,
        default=None,
        help="Optional normalized CodeQL candidate JSON to merge into the graph.",
    )
    args = parser.parse_args(argv)

    repo_root = Path.cwd()
    try:
        config_resolution = resolve_code_graph_config(
            repo_root,
            config_path=args.config,
            allow_legacy_defaults=args.allow_legacy_defaults,
        )
        if config_resolution.config is not None:
            ensure_plugin_version_compatible(
                config_resolution.config.plugin.required_version,
                runtime_version=PLUGIN_VERSION,
                allow_newer_minor=config_resolution.config.plugin.allow_newer_minor,
            )
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    config = config_resolution.config
    output_path = args.output or (config.outputs.graph if config is not None else DEFAULT_GRAPH_PATH)
    schema_output_path = args.schema_output or (config.outputs.schema if config is not None else DEFAULT_SCHEMA_PATH)
    summary_path = args.summary or (config.outputs.summary if config is not None else DEFAULT_SUMMARY_PATH)
    committed_graph_path = config.outputs.graph if config is not None else DEFAULT_GRAPH_PATH
    committed_summary_path = config.outputs.summary if config is not None else DEFAULT_SUMMARY_PATH
    if args.codeql_results is not None:
        if args.output is None or args.summary is None:
            print(
                "`--codeql-results` requires explicit local `--output` and `--summary` paths.",
                file=sys.stderr,
            )
            return 2
        if is_default_artifact_path(output_path, committed_graph_path, repo_root) or is_default_artifact_path(
            summary_path,
            committed_summary_path,
            repo_root,
        ):
            print(
                "`--codeql-results` cannot write to committed graph artifacts.",
                file=sys.stderr,
            )
            return 2

    try:
        graph_metadata = graph_metadata_from_config(config, repo_root) if config is not None else None
        discovery_config = discovery_config_from_config(config) if config is not None else None
        parser_registry_factory = (
            parser_registry_factory_from_config(config) if config is not None else default_parser_registry
        )
        manifest_registry = manifest_registry_from_config(config) if config is not None else default_manifest_parser_registry()
        frontend_test_roots = frontend_test_roots_from_config(config) if config is not None else None
        outputs = generate_output_texts(
            repo_root=repo_root,
            output_path=output_path,
            schema_output_path=schema_output_path,
            summary_path=summary_path,
            include_untracked=args.include_untracked,
            codeql_results_path=args.codeql_results,
            codeql_relation_loader=load_codeql_candidate_relations,
            graph_metadata=graph_metadata,
            discovery_config=discovery_config,
            parser_registry_factory=parser_registry_factory,
            manifest_registry=manifest_registry,
            frontend_test_roots=frontend_test_roots,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.check:
        stale = stale_outputs(outputs)
        if stale:
            for path in stale:
                print(f"stale generated artifact: {path.relative_to(repo_root)}", file=sys.stderr)
            print("Run `make code-graph` to update generated artifacts.", file=sys.stderr)
            return 1
        return 0

    write_outputs(outputs)
    return 0


def is_default_artifact_path(path: Path, default_path: Path, repo_root: Path) -> bool:
    candidate = path if path.is_absolute() else repo_root / path
    default = repo_root / default_path
    return candidate.resolve() == default.resolve()


def graph_metadata_from_config(config: CodeGraphConfig, repo_root: Path) -> GraphOutputMetadata:
    return GraphOutputMetadata(
        repo_name=config.project.artifact_repo_name or repo_root.name,
        repo_root=config.project.root,
        generator_version=config.generator.version,
        plugin_version=PLUGIN_VERSION,
    )


def discovery_config_from_config(config: CodeGraphConfig) -> FileDiscoveryConfig:
    return FileDiscoveryConfig(
        include_suffixes=config.discovery.include_suffixes,
        include_filenames=config.discovery.include_filenames,
        exclude_prefixes=config.discovery.exclude_prefixes,
    )


def manifest_registry_from_config(config: CodeGraphConfig) -> ManifestParserRegistry:
    return manifest_parser_registry_for_plugin_config(config.plugins)


def parser_registry_factory_from_config(config: CodeGraphConfig) -> ParserRegistryFactory:
    def create_parser_registry(repo_root: Path, source_files):
        return parser_registry_for_plugin_config(
            repo_root=repo_root,
            source_files=source_files,
            plugins=config.plugins,
        )

    return create_parser_registry


def frontend_test_roots_from_config(config: CodeGraphConfig) -> tuple[str, ...]:
    return frontend_test_roots_for_plugin_config(config.plugins)


if __name__ == "__main__":
    raise SystemExit(main())
