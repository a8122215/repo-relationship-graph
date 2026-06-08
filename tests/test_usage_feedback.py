from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


class UsageFeedbackTests(unittest.TestCase):
    def test_cleanup_reports_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write_config(repo, usage_log_dir="usage")

            result = run_usage_feedback(repo, "cleanup", "--dry-run")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("mode=dry-run", result.stdout)
            self.assertIn("query_usage.local.jsonl: existing=no kept=0 removed=0", result.stdout)
            self.assertIn("task_feedback.local.jsonl: existing=no kept=0 removed=0", result.stdout)
            self.assertIn("missed_relation.local.jsonl: existing=no kept=0 removed=0", result.stdout)

    def test_cleanup_dry_run_and_apply_prune_expired_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write_config(repo, usage_log_dir="usage")
            usage_dir = repo / "usage"
            usage_dir.mkdir()
            log_path = usage_dir / "task_feedback.local.jsonl"
            old_event = {
                "eventType": "task_feedback",
                "timestamp": "2000-01-01T00:00:00+00:00",
                "task": {"title": "old"},
            }
            current_event = {
                "eventType": "task_feedback",
                "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
                "task": {"title": "current"},
            }
            log_path.write_text(
                json.dumps(old_event, sort_keys=True)
                + "\n"
                + json.dumps(current_event, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )

            dry_run = run_usage_feedback(repo, "cleanup", "--dry-run")

            self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
            self.assertIn("task_feedback.local.jsonl: existing=yes kept=1 removed=1", dry_run.stdout)
            self.assertEqual(len(log_path.read_text(encoding="utf-8").splitlines()), 2)

            applied = run_usage_feedback(repo, "cleanup", "--apply")

            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertIn("mode=apply", applied.stdout)
            remaining = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(remaining, [current_event])

    def test_cleanup_keeps_malformed_and_missing_timestamp_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write_config(repo, usage_log_dir="usage")
            usage_dir = repo / "usage"
            usage_dir.mkdir()
            log_path = usage_dir / "missed_relation.local.jsonl"
            missing_timestamp = {"eventType": "missed_relation"}
            log_path.write_text("not-json\n" + json.dumps(missing_timestamp) + "\n", encoding="utf-8")

            result = run_usage_feedback(repo, "cleanup", "--apply")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("kept=2 removed=0 malformed=1 missingTimestamp=1", result.stdout)
            self.assertEqual(log_path.read_text(encoding="utf-8").splitlines(), ["not-json", json.dumps(missing_timestamp)])

    def test_cleanup_retention_days_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write_config(repo, usage_log_dir="usage", retention_days=365)
            usage_dir = repo / "usage"
            usage_dir.mkdir()
            log_path = usage_dir / "query_usage.local.jsonl"
            log_path.write_text(
                json.dumps({"timestamp": "2000-01-01T00:00:00+00:00"}) + "\n",
                encoding="utf-8",
            )

            result = run_usage_feedback(repo, "cleanup", "--dry-run", "--retention-days", "1")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("retentionDays=1", result.stdout)
            self.assertIn("query_usage.local.jsonl: existing=yes kept=0 removed=1", result.stdout)


def run_usage_feedback(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PLUGIN_ROOT)
    return subprocess.run(
        [sys.executable, "-m", "repo_graph.usage_feedback", "--config", "codegraph.config.toml", *args],
        cwd=repo,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def write_config(repo: Path, *, usage_log_dir: str, retention_days: int = 14) -> None:
    (repo / "codegraph.config.toml").write_text(
        f"""
schema_version = 1

[project]
name = "UsageFeedbackFixture"
root = "."

[outputs]
graph = "analysis/code_graph/repo_graph.json"
schema = "analysis/code_graph/repo_graph.schema.json"
summary = "analysis/code_graph/repo_graph.summary.md"
usage_dir = "analysis/code_graph/usage"

[usage]
enabled_by_default = false
log_dir = "{usage_log_dir}"
retention_days = {retention_days}

[discovery]
include_suffixes = [".py"]
exclude_prefixes = ["analysis/code_graph/"]

[plugins.python_ast]
enabled = true
""".strip()
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
