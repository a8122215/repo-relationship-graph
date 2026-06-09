from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_graph.entrypoint_config import load_entrypoint_config, mcp_graph_path  # noqa: E402
from repo_graph.query import CodeGraphQueryService, DEFAULT_MAX_RESULTS  # noqa: E402
from repo_graph.usage_logging import QueryUsageLogger, UsageTimer  # noqa: E402


DEFAULT_PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = {DEFAULT_PROTOCOL_VERSION}
SERVER_NAME = "repo-relationship-graph"
SERVER_VERSION = "code-graph@0.1.0"


class CodeGraphMcpServer:
    def __init__(
        self,
        query_service: CodeGraphQueryService | Callable[[], CodeGraphQueryService],
        usage_logger: QueryUsageLogger | None = None,
        graph_path: Path = Path("analysis/code_graph/repo_graph.json"),
    ) -> None:
        if callable(query_service):
            self._query_service_provider = query_service
        else:
            self._query_service_provider = lambda: query_service
        self.usage_logger = usage_logger or QueryUsageLogger.from_env(interface="mcp", repo_root=Path.cwd())
        self.graph_path = graph_path
        self.tools: dict[str, ToolDefinition] = {
            "find_impacted_files": ToolDefinition(
                name="find_impacted_files",
                description="Find files that directly depend on or are related to a repo-relative path.",
                input_schema=path_input_schema(),
                handler=self._find_impacted_files,
            ),
            "find_tests_for": ToolDefinition(
                name="find_tests_for",
                description="Find test files that the code graph links to a repo-relative path.",
                input_schema=path_input_schema(),
                handler=self._find_tests_for,
            ),
            "find_endpoints_for_router": ToolDefinition(
                name="find_endpoints_for_router",
                description="Find FastAPI endpoints exposed by a router/module path.",
                input_schema=path_input_schema(),
                handler=self._find_endpoints_for_router,
            ),
            "find_routes_for_view": ToolDefinition(
                name="find_routes_for_view",
                description="Find Vue Router routes that render a view path.",
                input_schema=path_input_schema(),
                handler=self._find_routes_for_view,
            ),
            "find_api_callers_for_endpoint": ToolDefinition(
                name="find_api_callers_for_endpoint",
                description="Find frontend files with static API call candidates for a FastAPI endpoint.",
                input_schema=endpoint_input_schema(),
                handler=self._find_api_callers_for_endpoint,
            ),
            "find_e2e_for_route": ToolDefinition(
                name="find_e2e_for_route",
                description="Find Playwright E2E specs with static page.goto/toHaveURL candidates for a Vue route.",
                input_schema=route_input_schema(),
                handler=self._find_e2e_for_route,
            ),
            "find_e2e_for_view": ToolDefinition(
                name="find_e2e_for_view",
                description="Find Playwright E2E specs that reach routes rendering a view path.",
                input_schema=path_input_schema(),
                handler=self._find_e2e_for_view,
            ),
            "explain_node": ToolDefinition(
                name="explain_node",
                description="Return a graph node plus bounded incoming and outgoing edges by exact id or unique path.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "maxResults": {"type": "integer", "minimum": 1, "maximum": 200},
                    },
                    "required": ["id"],
                    "additionalProperties": False,
                },
                handler=self._explain_node,
            ),
        }

    @property
    def query_service(self) -> CodeGraphQueryService:
        return self._query_service_provider()

    def handle_message(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict):
            return self._error(None, -32600, "Invalid JSON-RPC request")
        request_id = message.get("id")
        method = message.get("method")
        if request_id is None:
            return None
        try:
            if method == "initialize":
                return self._success(request_id, self._initialize_result(message.get("params") or {}))
            if method == "ping":
                return self._success(request_id, {})
            if method == "tools/list":
                return self._success(request_id, {"tools": [tool.to_mcp_tool() for tool in self.tools.values()]})
            if method == "tools/call":
                return self._success(request_id, self._call_tool(message.get("params") or {}))
            return self._error(request_id, -32601, f"Method not found: {method}")
        except ValueError as exc:
            return self._error(request_id, -32602, str(exc))
        except CodeGraphReloadError as exc:
            return self._error(request_id, -32603, str(exc))
        except Exception as exc:  # pragma: no cover - defensive JSON-RPC boundary
            return self._error(request_id, -32603, str(exc))

    def _initialize_result(self, params: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise ValueError("initialize params must be an object")
        protocol_version = negotiate_protocol_version(params.get("protocolVersion"))
        return {
            "protocolVersion": protocol_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise ValueError("tools/call params must be an object")
        tool_name = params.get("name")
        if not isinstance(tool_name, str):
            raise ValueError("tools/call requires params.name")
        tool = self.tools.get(tool_name)
        if tool is None:
            raise ValueError(f"unknown tool: {tool_name}")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise ValueError("tools/call params.arguments must be an object")
        timer = UsageTimer()
        try:
            result = tool.handler(arguments)
        except Exception as exc:
            self._log_tool_call(tool_name, arguments, None, "error", timer.elapsed_ms(), str(exc))
            raise
        self._log_tool_call(tool_name, arguments, result, "ok", timer.elapsed_ms())
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result, ensure_ascii=False, sort_keys=True),
                }
            ],
            "structuredContent": result,
            "isError": False,
        }

    def _log_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any] | None,
        status: str,
        latency_ms: int,
        error: str | None = None,
    ) -> None:
        input_kind, input_value = mcp_query_input(tool_name, arguments)
        self.usage_logger.log_query_call(
            tool=tool_name,
            input_kind=input_kind,
            input_value=input_value,
            normalized_input=normalized_query_input(result, input_value),
            result=result,
            status=status,
            latency_ms=latency_ms,
            graph_path=self.graph_path,
            error=error,
        )

    def _find_impacted_files(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = required_string_arg(arguments, "path")
        return self.query_service.find_impacted_files(path, optional_limit(arguments))

    def _find_tests_for(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = required_string_arg(arguments, "path")
        return self.query_service.find_tests_for(path, optional_limit(arguments))

    def _find_endpoints_for_router(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = required_string_arg(arguments, "path")
        return self.query_service.find_endpoints_for_router(path, optional_limit(arguments))

    def _find_routes_for_view(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = required_string_arg(arguments, "path")
        return self.query_service.find_routes_for_view(path, optional_limit(arguments))

    def _find_api_callers_for_endpoint(self, arguments: dict[str, Any]) -> dict[str, Any]:
        endpoint = required_string_arg(arguments, "endpoint")
        return self.query_service.find_api_callers_for_endpoint(endpoint, optional_limit(arguments))

    def _find_e2e_for_route(self, arguments: dict[str, Any]) -> dict[str, Any]:
        route = required_string_arg(arguments, "route")
        return self.query_service.find_e2e_for_route(route, optional_limit(arguments))

    def _find_e2e_for_view(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = required_string_arg(arguments, "path")
        return self.query_service.find_e2e_for_view(path, optional_limit(arguments))

    def _explain_node(self, arguments: dict[str, Any]) -> dict[str, Any]:
        node_id = required_string_arg(arguments, "id")
        return self.query_service.explain_node(node_id, optional_limit(arguments))

    def _success(self, request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _error(self, request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


class ToolDefinition:
    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.handler = handler

    def to_mcp_tool(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {"readOnlyHint": True, "destructiveHint": False},
        }


class CodeGraphReloadError(RuntimeError):
    pass


class ReloadingCodeGraphQueryServiceProvider:
    def __init__(
        self,
        graph_path: Path,
        default_max_results: int = DEFAULT_MAX_RESULTS,
    ) -> None:
        self.graph_path = graph_path
        self.default_max_results = default_max_results
        self._service = CodeGraphQueryService.from_path(graph_path, default_max_results=default_max_results)
        self._mtime_ns = self._graph_mtime_ns()

    def current(self) -> CodeGraphQueryService:
        try:
            current_mtime_ns = self._graph_mtime_ns()
            if current_mtime_ns == self._mtime_ns:
                return self._service
            service = CodeGraphQueryService.from_path(self.graph_path, default_max_results=self.default_max_results)
        except (OSError, ValueError) as exc:
            raise CodeGraphReloadError(f"code graph reload failed: {exc}") from exc
        self._service = service
        self._mtime_ns = current_mtime_ns
        return self._service

    def _graph_mtime_ns(self) -> int:
        return self.graph_path.stat().st_mtime_ns


def serve_stdio(server: CodeGraphMcpServer) -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            response = server.handle_message(message)
        if response is None:
            continue
        sys.stdout.write(json.dumps(response, ensure_ascii=False, sort_keys=True) + "\n")
        sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve code graph tools over MCP stdio JSON-RPC.")
    parser.add_argument("--config", type=Path, default=None, help="Path to codegraph.config.toml.")
    parser.add_argument("--graph", type=Path, default=None, help="Override the configured graph path.")
    parser.add_argument("--max-results", type=int, default=DEFAULT_MAX_RESULTS)
    args = parser.parse_args(argv)
    repo_root = Path.cwd()
    try:
        config = load_entrypoint_config(repo_root, args.config)
        graph_path = mcp_graph_path(repo_root, config, args.graph)
        service_provider = ReloadingCodeGraphQueryServiceProvider(
            graph_path,
            default_max_results=args.max_results,
        )
        serve_stdio(
            CodeGraphMcpServer(
                service_provider.current,
                usage_logger=QueryUsageLogger.from_config_or_env(interface="mcp", repo_root=repo_root, config=config),
                graph_path=graph_path,
            )
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


def path_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "maxResults": {"type": "integer", "minimum": 1, "maximum": 200},
        },
        "required": ["path"],
        "additionalProperties": False,
    }


def endpoint_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "endpoint": {"type": "string"},
            "maxResults": {"type": "integer", "minimum": 1, "maximum": 200},
        },
        "required": ["endpoint"],
        "additionalProperties": False,
    }


def route_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "route": {"type": "string"},
            "maxResults": {"type": "integer", "minimum": 1, "maximum": 200},
        },
        "required": ["route"],
        "additionalProperties": False,
    }


def negotiate_protocol_version(requested: Any) -> str:
    if requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return DEFAULT_PROTOCOL_VERSION


def required_string_arg(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"argument `{key}` must be a non-empty string")
    return value


def optional_limit(arguments: dict[str, Any]) -> int | None:
    value = arguments.get("maxResults")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("argument `maxResults` must be an integer")
    return value


def mcp_query_input(tool_name: str, arguments: dict[str, Any]) -> tuple[str, str]:
    if tool_name == "find_api_callers_for_endpoint":
        return "endpoint", str(arguments.get("endpoint", ""))
    if tool_name == "find_e2e_for_route":
        return "route", str(arguments.get("route", ""))
    if tool_name == "explain_node":
        return "node_or_path", str(arguments.get("id", ""))
    return "path", str(arguments.get("path", ""))


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


if __name__ == "__main__":
    raise SystemExit(main())
