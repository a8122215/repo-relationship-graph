from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_graph.entrypoint_config import load_entrypoint_config, query_graph_path  # noqa: E402
from repo_graph.query import CodeGraphQueryService, DEFAULT_MAX_RESULTS  # noqa: E402
from repo_graph.usage_logging import QueryUsageLogger, UsageTimer  # noqa: E402


QueryHandler = Callable[[CodeGraphQueryService, argparse.Namespace], dict[str, Any]]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo_root = Path.cwd()
    try:
        config = load_entrypoint_config(repo_root, args.config)
        graph_path = query_graph_path(repo_root, config, args.graph)
    except (FileNotFoundError, ValueError) as exc:
        print(f"code graph query failed: {exc}", file=sys.stderr)
        return 1
    logger = QueryUsageLogger.from_config_or_env(interface="cli", repo_root=repo_root, config=config)
    timer = UsageTimer()
    try:
        service = CodeGraphQueryService.from_path(graph_path, default_max_results=args.max_results)
        result = args.handler(service, args)
        log_query(logger, args, graph_path, result, "ok", timer.elapsed_ms())
        write_result(result, args.format, args.command)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        log_query(logger, args, graph_path, None, "error", timer.elapsed_ms(), error=str(exc))
        print(f"code graph query failed: {exc}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Query code graph artifacts without printing raw repo_graph.json.",
    )
    parser.add_argument("--config", type=Path, default=None, help="Path to codegraph.config.toml.")
    parser.add_argument("--graph", type=Path, default=None, help="Override the configured graph path.")
    parser.add_argument("--max-results", type=int, default=DEFAULT_MAX_RESULTS)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_path_command(
        subparsers,
        "impacted",
        "Find files that directly depend on or are related to a path.",
        handle_impacted,
    )
    add_path_command(
        subparsers,
        "tests-for",
        "Find graph-linked tests for a path.",
        handle_tests_for,
    )
    add_path_command(
        subparsers,
        "endpoints-for-router",
        "Find FastAPI endpoints exposed by a router/module path.",
        handle_endpoints_for_router,
    )
    add_path_command(
        subparsers,
        "routes-for-view",
        "Find Vue Router routes that render a view path.",
        handle_routes_for_view,
    )
    add_path_command(
        subparsers,
        "api-callers-for-endpoint",
        "Find frontend files with static API call candidates for an endpoint.",
        handle_api_callers_for_endpoint,
    )
    add_path_command(
        subparsers,
        "e2e-for-route",
        "Find Playwright E2E specs with static page.goto/toHaveURL candidates for a route.",
        handle_e2e_for_route,
    )
    add_path_command(
        subparsers,
        "e2e-for-view",
        "Find Playwright E2E specs that reach routes rendering a view path.",
        handle_e2e_for_view,
    )
    explain = subparsers.add_parser("explain", help="Explain one graph node by exact id or unique path.")
    explain.add_argument("id")
    explain.set_defaults(handler=handle_explain)
    return parser


def add_path_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    help_text: str,
    handler: QueryHandler,
) -> None:
    command = subparsers.add_parser(name, help=help_text)
    command.add_argument("path")
    command.set_defaults(handler=handler)


def handle_impacted(service: CodeGraphQueryService, args: argparse.Namespace) -> dict[str, Any]:
    return service.find_impacted_files(args.path, args.max_results)


def handle_tests_for(service: CodeGraphQueryService, args: argparse.Namespace) -> dict[str, Any]:
    return service.find_tests_for(args.path, args.max_results)


def handle_endpoints_for_router(service: CodeGraphQueryService, args: argparse.Namespace) -> dict[str, Any]:
    return service.find_endpoints_for_router(args.path, args.max_results)


def handle_routes_for_view(service: CodeGraphQueryService, args: argparse.Namespace) -> dict[str, Any]:
    return service.find_routes_for_view(args.path, args.max_results)


def handle_api_callers_for_endpoint(service: CodeGraphQueryService, args: argparse.Namespace) -> dict[str, Any]:
    return service.find_api_callers_for_endpoint(args.path, args.max_results)


def handle_e2e_for_route(service: CodeGraphQueryService, args: argparse.Namespace) -> dict[str, Any]:
    return service.find_e2e_for_route(args.path, args.max_results)


def handle_e2e_for_view(service: CodeGraphQueryService, args: argparse.Namespace) -> dict[str, Any]:
    return service.find_e2e_for_view(args.path, args.max_results)


def handle_explain(service: CodeGraphQueryService, args: argparse.Namespace) -> dict[str, Any]:
    return service.explain_node(args.id, args.max_results)


def log_query(
    logger: QueryUsageLogger,
    args: argparse.Namespace,
    graph_path: Path,
    result: dict[str, Any] | None,
    status: str,
    latency_ms: int,
    error: str | None = None,
) -> None:
    input_kind, input_value = query_input(args)
    normalized_input = normalized_query_input(result, input_value)
    logger.log_query_call(
        tool=args.command,
        input_kind=input_kind,
        input_value=input_value,
        normalized_input=normalized_input,
        result=result,
        status=status,
        latency_ms=latency_ms,
        graph_path=graph_path,
        error=error,
    )


def query_input(args: argparse.Namespace) -> tuple[str, str]:
    if args.command == "api-callers-for-endpoint":
        return "endpoint", args.path
    if args.command == "e2e-for-route":
        return "route", args.path
    if args.command == "explain":
        return "node_or_path", args.id
    return "path", args.path


def normalized_query_input(result: dict[str, Any] | None, fallback: str) -> str:
    if not isinstance(result, dict):
        return fallback
    query = result.get("query")
    if not isinstance(query, dict):
        return fallback
    for key in ("normalizedPath", "normalizedRoutePath", "lookup", "input"):
        value = query.get(key)
        if isinstance(value, str) and value:
            return value
    return fallback


def write_result(result: dict[str, Any], output_format: str, command: str) -> None:
    if output_format == "json":
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return
    print(format_text_result(result, command))


def format_text_result(result: dict[str, Any], command: str) -> str:
    if command == "impacted":
        return format_list_result(result, "files", format_file_result)
    if command == "tests-for":
        return format_list_result(result, "tests", format_test_result)
    if command == "endpoints-for-router":
        return format_list_result(result, "endpoints", format_endpoint_result)
    if command == "routes-for-view":
        return format_list_result(result, "routes", format_route_result)
    if command == "api-callers-for-endpoint":
        return format_list_result(result, "files", format_file_result)
    if command in {"e2e-for-route", "e2e-for-view"}:
        return format_list_result(result, "tests", format_test_result)
    if command == "explain":
        return format_explain_result(result)
    raise ValueError(f"unknown command: {command}")


def format_list_result(
    result: dict[str, Any],
    key: str,
    item_formatter: Callable[[dict[str, Any]], str],
) -> str:
    lines = [query_header(result)]
    items = result.get(key, [])
    if not items:
        lines.append(f"No {key.replace('_', ' ')} found.")
    else:
        lines.extend(item_formatter(item) for item in items)
    note = result.get("note")
    if note:
        lines.append(f"Note: {note}")
    return "\n".join(lines)


def query_header(result: dict[str, Any]) -> str:
    query = result.get("query", {})
    total = result.get("total", 0)
    truncated = "yes" if result.get("truncated") else "no"
    lookup = query.get("lookup", query.get("input", ""))
    matched = query.get("matchedNodeIds", [])
    if "matchedNodeId" in query:
        matched = [query["matchedNodeId"]] if query["matchedNodeId"] else []
    return f"Query: {lookup}\nMatched: {', '.join(matched) or 'none'}\nTotal: {total} truncated: {truncated}"


def format_file_result(item: dict[str, Any]) -> str:
    reasons = ",".join(item.get("reasons", [])) or item.get("reason", "")
    return f"- {item.get('path')} [{item.get('edgeType')} {item.get('confidence')} {reasons}]"


def format_test_result(item: dict[str, Any]) -> str:
    return f"- {item.get('path')} [{item.get('confidence')} {item.get('reason')}]"


def format_endpoint_result(item: dict[str, Any]) -> str:
    return f"- {item.get('method')} {item.get('path')} [{item.get('confidence')} via={item.get('via')}]"


def format_route_result(item: dict[str, Any]) -> str:
    return (
        f"- {item.get('routePath')} name={item.get('routeName')} "
        f"component={item.get('componentPath')} [{item.get('confidence')}]"
    )


def format_explain_result(result: dict[str, Any]) -> str:
    lines = [query_header({"query": result.get("query", {}), "total": 1 if result.get("node") else 0})]
    node = result.get("node")
    if node is None:
        lines.append("No node found.")
        return "\n".join(lines)
    lines.append(f"Node: {node.get('id')} type={node.get('type')} path={node.get('path')}")
    lines.append(f"Incoming: {result.get('incomingTotal', 0)} truncated: {'yes' if result.get('incomingTruncated') else 'no'}")
    lines.extend(format_explain_edge("in", item) for item in result.get("incoming", []))
    lines.append(f"Outgoing: {result.get('outgoingTotal', 0)} truncated: {'yes' if result.get('outgoingTruncated') else 'no'}")
    lines.extend(format_explain_edge("out", item) for item in result.get("outgoing", []))
    return "\n".join(lines)


def format_explain_edge(direction: str, item: dict[str, Any]) -> str:
    edge = item.get("edge", {})
    neighbor = item.get("neighbor") or {}
    neighbor_name = neighbor.get("path") or neighbor.get("id")
    return f"- {direction} {edge.get('type')} {neighbor_name} [{edge.get('confidence')}]"


if __name__ == "__main__":
    raise SystemExit(main())
