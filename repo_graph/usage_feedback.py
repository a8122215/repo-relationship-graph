from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from repo_graph.config import CodeGraphConfig  # noqa: E402
from repo_graph.entrypoint_config import load_entrypoint_config  # noqa: E402
from repo_graph.usage_logging import DEFAULT_USAGE_DIR, QUERY_USAGE_FILENAME, append_jsonl  # noqa: E402


TASK_FEEDBACK_FILENAME = "task_feedback.local.jsonl"
MISSED_RELATION_FILENAME = "missed_relation.local.jsonl"
LOCAL_USAGE_FILENAMES = (QUERY_USAGE_FILENAME, TASK_FEEDBACK_FILENAME, MISSED_RELATION_FILENAME)
USEFULNESS_VALUES = ("high", "medium", "low", "none", "misleading")
OUTCOME_VALUES = ("used", "partially_used", "ignored", "no_result", "too_many", "stale", "error")
RELATION_TYPES = (
    "python_import",
    "js_vue_import",
    "registers_router",
    "exposes_endpoint",
    "tests",
    "renders_view",
    "calls_api_endpoint",
    "e2e_reaches_route",
    "package_depends_on",
    "codeql_calls",
    "codeql_data_flows_to",
    "unknown",
)
MISSED_REASONS = (
    "dynamic_path",
    "string_concatenation",
    "base_url_template",
    "leading_dynamic_segment",
    "wrapper_function",
    "template_with_buildQuery",
    "scope_shadowing",
    "route_regex_too_complex",
    "click_navigation",
    "router_push",
    "stale_graph",
    "confidence_too_low",
    "confidence_too_high",
    "false_positive",
    "unknown",
)
SEVERITY_VALUES = ("low", "medium", "high")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parsed_argv = normalize_argv(argv)
    args = parser.parse_args(parsed_argv)
    try:
        config = load_entrypoint_config(Path.cwd(), args.config)
        if args.command == "cleanup":
            print(cleanup_usage_logs(config, args))
            return 0
        output_path = usage_dir(config) / (
            MISSED_RELATION_FILENAME if args.command == "missed" else TASK_FEEDBACK_FILENAME
        )
        payload = missed_relation_payload(args) if args.command == "missed" else task_feedback_payload(args)
        append_jsonl(output_path, payload)
        print(str(output_path))
        return 0
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


def normalize_argv(argv: list[str] | None) -> list[str] | None:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        return argv
    if argv[0] in {"task", "missed", "cleanup"}:
        return argv
    global_args, command_args = split_leading_global_args(argv)
    if command_args and command_args[0] in {"task", "missed", "cleanup"}:
        return [*global_args, *command_args]
    return [*global_args, "task", *command_args]


def split_leading_global_args(argv: list[str]) -> tuple[list[str], list[str]]:
    global_args: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--config":
            if index + 1 >= len(argv):
                return argv, []
            global_args.extend(argv[index : index + 2])
            index += 2
            continue
        if arg.startswith("--config="):
            global_args.append(arg)
            index += 1
            continue
        break
    return global_args, argv[index:]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Append local code graph usage feedback JSONL records.")
    parser.add_argument("--config", type=Path, default=None, help="Path to codegraph.config.toml.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    task = subparsers.add_parser("task", help="Record task-level graph usefulness feedback.")
    task.add_argument("--task", required=True)
    task.add_argument("--category", default=None)
    task.add_argument("--branch", default=None)
    task.add_argument("--query", action="append", default=[])
    task.add_argument("--query-event-id", action="append", default=[])
    task.add_argument("--opened", action="append", default=[])
    task.add_argument("--selected", action="append", default=[])
    task.add_argument("--usefulness", choices=USEFULNESS_VALUES, required=True)
    task.add_argument("--outcome", choices=OUTCOME_VALUES, default="used")
    task.add_argument("--source-search-fallback", action="store_true")
    task.add_argument("--note", default="")

    missed = subparsers.add_parser("missed", help="Record a missed or noisy graph relation.")
    missed.add_argument("--task", default="")
    missed.add_argument("--query", default="")
    missed.add_argument("--relation", choices=RELATION_TYPES, required=True)
    missed.add_argument("--reason", choices=MISSED_REASONS, required=True)
    missed.add_argument("--expected-source", default="")
    missed.add_argument("--expected-target", default="")
    missed.add_argument("--found-by", default="source_search")
    missed.add_argument("--example-pattern", default="")
    missed.add_argument("--severity", choices=SEVERITY_VALUES, default="medium")
    missed.add_argument("--suggested-fix", default="")

    cleanup = subparsers.add_parser("cleanup", help="Remove local usage log events older than retention days.")
    cleanup_mode = cleanup.add_mutually_exclusive_group()
    cleanup_mode.add_argument("--dry-run", action="store_true", help="Print cleanup counts without rewriting log files.")
    cleanup_mode.add_argument("--apply", action="store_true", help="Rewrite local log files and remove expired events.")
    cleanup.add_argument("--retention-days", type=positive_int, default=None)
    return parser


def usage_dir(config: CodeGraphConfig | None = None) -> Path:
    raw_path = os.environ.get("CODE_GRAPH_USAGE_DIR")
    if raw_path:
        path = Path(raw_path)
        return path if path.is_absolute() else Path.cwd() / path
    configured = config.usage.log_dir if config is not None else DEFAULT_USAGE_DIR
    return configured if configured.is_absolute() else Path.cwd() / configured


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def cleanup_usage_logs(config: CodeGraphConfig, args: argparse.Namespace) -> str:
    retention_days = args.retention_days if args.retention_days is not None else config.usage.retention_days
    cleanup_root = usage_dir(config)
    now = datetime.now().astimezone()
    cutoff = now - timedelta(days=retention_days)
    apply_changes = bool(args.apply)
    lines = [
        (
            f"usage cleanup mode={'apply' if apply_changes else 'dry-run'} "
            f"retentionDays={retention_days} cutoff={cutoff.isoformat(timespec='seconds')} "
            f"usageDir={cleanup_root}"
        )
    ]
    for filename in LOCAL_USAGE_FILENAMES:
        summary = cleanup_jsonl_file(cleanup_root / filename, cutoff=cutoff, apply_changes=apply_changes)
        lines.append(
            (
                f"{filename}: existing={'yes' if summary['existing'] else 'no'} "
                f"kept={summary['kept']} removed={summary['removed']} "
                f"malformed={summary['malformed']} missingTimestamp={summary['missingTimestamp']}"
            )
        )
    return "\n".join(lines)


def cleanup_jsonl_file(path: Path, *, cutoff: datetime, apply_changes: bool) -> dict[str, int | bool]:
    summary: dict[str, int | bool] = {
        "existing": path.exists(),
        "kept": 0,
        "removed": 0,
        "malformed": 0,
        "missingTimestamp": 0,
    }
    if not path.exists():
        return summary

    retained_lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if should_keep_usage_line(line, cutoff=cutoff, summary=summary):
            retained_lines.append(line)

    if apply_changes and summary["removed"]:
        if retained_lines:
            temp_path = path.with_name(f"{path.name}.tmp")
            temp_path.write_text("\n".join(retained_lines) + "\n", encoding="utf-8")
            temp_path.replace(path)
        else:
            path.unlink()
    return summary


def should_keep_usage_line(line: str, *, cutoff: datetime, summary: dict[str, int | bool]) -> bool:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        summary["malformed"] = int(summary["malformed"]) + 1
        summary["kept"] = int(summary["kept"]) + 1
        return True
    timestamp_value = payload.get("timestamp") if isinstance(payload, dict) else None
    event_timestamp = parse_timestamp(timestamp_value)
    if event_timestamp is None:
        summary["missingTimestamp"] = int(summary["missingTimestamp"]) + 1
        summary["kept"] = int(summary["kept"]) + 1
        return True
    if event_timestamp < cutoff:
        summary["removed"] = int(summary["removed"]) + 1
        return False
    summary["kept"] = int(summary["kept"]) + 1
    return True


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed


def task_feedback_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "eventType": "task_feedback",
        "feedbackId": str(uuid.uuid4()),
        "timestamp": timestamp(),
        "task": {
            "title": args.task,
            "category": args.category,
            "branch": args.branch,
        },
        "graphUsage": {
            "queryEventIds": args.query_event_id,
            "queries": [query_record(value) for value in args.query],
        },
        "openedFiles": args.opened,
        "selectedFiles": args.selected,
        "usefulness": args.usefulness,
        "outcome": args.outcome,
        "sourceSearchFallback": args.source_search_fallback,
        "notes": args.note,
    }


def missed_relation_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "eventType": "missed_relation",
        "eventId": str(uuid.uuid4()),
        "timestamp": timestamp(),
        "task": args.task,
        "query": query_record(args.query),
        "missed": {
            "relationType": args.relation,
            "expectedSource": args.expected_source,
            "expectedTarget": args.expected_target,
            "foundBy": args.found_by,
            "reason": args.reason,
            "examplePattern": args.example_pattern,
        },
        "severity": args.severity,
        "suggestedFix": args.suggested_fix,
    }


def query_record(value: str) -> dict[str, str]:
    if not value:
        return {"tool": "", "input": ""}
    if ":" not in value:
        return {"tool": value, "input": ""}
    tool, query_input = value.split(":", 1)
    return {"tool": tool.strip(), "input": query_input.strip()}


def timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
