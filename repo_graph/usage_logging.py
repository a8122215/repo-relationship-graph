from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Any

from repo_graph.config import CodeGraphConfig


DEFAULT_USAGE_DIR = Path("analysis/code_graph/usage")
QUERY_USAGE_FILENAME = "query_usage.local.jsonl"
TRUTHY_VALUES = {"1", "true", "yes", "on"}


class UsageTimer:
    def __init__(self) -> None:
        self._started_at = monotonic()

    def elapsed_ms(self) -> int:
        return max(0, round((monotonic() - self._started_at) * 1000))


class QueryUsageLogger:
    def __init__(
        self,
        *,
        enabled: bool,
        log_path: Path,
        interface: str,
        actor: str = "codex",
        session_id: str | None = None,
    ) -> None:
        self.enabled = enabled
        self.log_path = log_path
        self.interface = interface
        self.actor = actor
        self.session_id = session_id or default_session_id()

    @classmethod
    def from_env(cls, *, interface: str, repo_root: Path | None = None) -> "QueryUsageLogger":
        return cls.from_config_or_env(interface=interface, repo_root=repo_root, config=None)

    @classmethod
    def from_config_or_env(
        cls,
        *,
        interface: str,
        repo_root: Path | None = None,
        config: CodeGraphConfig | None = None,
    ) -> "QueryUsageLogger":
        root = repo_root or Path.cwd()
        enabled = env_enabled("CODE_GRAPH_USAGE_LOG") or bool(
            config.usage.enabled_by_default if config is not None else False
        )
        configured_usage_dir = config.usage.log_dir if config is not None else DEFAULT_USAGE_DIR
        usage_dir = Path(os.environ.get("CODE_GRAPH_USAGE_DIR", str(configured_usage_dir)))
        if not usage_dir.is_absolute():
            usage_dir = root / usage_dir
        return cls(
            enabled=enabled,
            log_path=usage_dir / QUERY_USAGE_FILENAME,
            interface=interface,
            actor=os.environ.get("CODE_GRAPH_USAGE_ACTOR", "codex"),
            session_id=os.environ.get("CODE_GRAPH_USAGE_SESSION_ID"),
        )

    def log_query_call(
        self,
        *,
        tool: str,
        input_kind: str,
        input_value: str,
        normalized_input: str | None,
        result: dict[str, Any] | None,
        status: str,
        latency_ms: int,
        graph_path: Path,
        error: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        event = {
            "schemaVersion": 1,
            "eventType": "query_call",
            "eventId": str(uuid.uuid4()),
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "sessionId": self.session_id,
            "actor": self.actor,
            "interface": self.interface,
            "branch": git_value(["git", "branch", "--show-current"]),
            "commit": git_value(["git", "rev-parse", "HEAD"]),
            "graphHash": graph_hash(graph_path),
            "graphSchemaVersion": graph_schema_version(graph_path),
            "tool": tool,
            "input": {
                "kind": input_kind,
                "value": input_value,
                "normalized": normalized_input,
            },
            "result": summarize_query_result(result or {}),
            "latencyMs": latency_ms,
            "status": status,
        }
        if error:
            event["error"] = error
        append_jsonl(self.log_path, event)


def env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in TRUTHY_VALUES


def default_session_id() -> str:
    return "local-" + datetime.now().astimezone().strftime("%Y%m%d")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def git_value(command: list[str]) -> str | None:
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    value = completed.stdout.strip()
    return value or None


def graph_hash(graph_path: Path) -> str | None:
    try:
        digest = hashlib.sha256(graph_path.read_bytes()).hexdigest()
    except OSError:
        return None
    return f"sha256:{digest}"


def graph_schema_version(graph_path: Path) -> int | None:
    try:
        with graph_path.open(encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None
    value = payload.get("schemaVersion")
    return value if isinstance(value, int) else None


def summarize_query_result(result: dict[str, Any]) -> dict[str, Any]:
    items = result_items(result)
    return {
        "count": int(result.get("total", len(items)) or 0),
        "truncated": bool(result.get("truncated") or result.get("incomingTruncated") or result.get("outgoingTruncated")),
        "confidenceCounts": confidence_counts(items),
        "edgeTypes": edge_type_counts(items),
        "topPaths": top_paths(items),
    }


def result_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key in ("files", "tests", "endpoints", "routes", "incoming", "outgoing"):
        value = result.get(key)
        if isinstance(value, list):
            items.extend(item for item in value if isinstance(item, dict))
    node = result.get("node")
    if isinstance(node, dict):
        items.append(node)
    return items


def confidence_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"high": 0, "medium": 0, "low": 0}
    for item in items:
        add_confidence(counts, item.get("confidence"))
        for edge in item.get("matchingEdges", []):
            if isinstance(edge, dict):
                add_confidence(counts, edge.get("confidence"))
        edge = item.get("edge")
        if isinstance(edge, dict):
            add_confidence(counts, edge.get("confidence"))
    return counts


def add_confidence(counts: dict[str, int], value: Any) -> None:
    if value in counts:
        counts[str(value)] += 1


def edge_type_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        raw_matching_edges = item.get("matchingEdges", [])
        if not isinstance(raw_matching_edges, list):
            raw_matching_edges = []
        matching_edges = [edge for edge in raw_matching_edges if isinstance(edge, dict)]
        if matching_edges:
            for edge in matching_edges:
                add_edge_type(counts, edge.get("type"))
            continue
        add_edge_type(counts, item.get("edgeType"))
        edge = item.get("edge")
        if isinstance(edge, dict):
            add_edge_type(counts, edge.get("type"))
    return counts


def add_edge_type(counts: dict[str, int], value: Any) -> None:
    if isinstance(value, str) and value:
        counts[value] = counts.get(value, 0) + 1


def top_paths(items: list[dict[str, Any]], limit: int = 10) -> list[str]:
    paths: list[str] = []
    for item in items:
        for key in ("path", "routerPath", "sourcePath", "componentPath"):
            value = item.get(key)
            if isinstance(value, str) and value and value not in paths:
                paths.append(value)
                if len(paths) >= limit:
                    return paths
        neighbor = item.get("neighbor")
        if isinstance(neighbor, dict):
            value = neighbor.get("path")
            if isinstance(value, str) and value and value not in paths:
                paths.append(value)
                if len(paths) >= limit:
                    return paths
    return paths
