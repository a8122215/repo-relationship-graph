import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def test_usage_feedback_records_task_feedback(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "repo_graph/usage_feedback.py"
    usage_dir = tmp_path / "usage"
    write_config(tmp_path, usage_log_dir="configured/usage")

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--config",
            "codegraph.config.toml",
            "--task",
            "ResearchAnalysisManager view change",
            "--category",
            "frontend_view_change",
            "--query",
            "e2e-for-view: client/src/views/admin/ResearchAnalysisManager.vue",
            "--opened",
            "client/e2e/research-analysis-smoke.spec.js",
            "--selected",
            "client/e2e/research-analysis-smoke.spec.js",
            "--usefulness",
            "high",
            "--note",
            "view -> route -> E2E was direct",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env=env_without_code_graph_config(CODE_GRAPH_USAGE_DIR=str(usage_dir)),
    )

    assert result.returncode == 0
    log_path = usage_dir / "task_feedback.local.jsonl"
    event = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert event["eventType"] == "task_feedback"
    assert event["task"]["title"] == "ResearchAnalysisManager view change"
    assert event["graphUsage"]["queries"] == [
        {
            "tool": "e2e-for-view",
            "input": "client/src/views/admin/ResearchAnalysisManager.vue",
        }
    ]
    assert event["openedFiles"] == ["client/e2e/research-analysis-smoke.spec.js"]
    assert event["selectedFiles"] == ["client/e2e/research-analysis-smoke.spec.js"]
    assert event["usefulness"] == "high"
    assert event["outcome"] == "used"


def test_usage_feedback_config_uses_configured_usage_dir(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "repo_graph/usage_feedback.py"
    write_config(tmp_path, usage_log_dir="configured/usage")

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--config",
            "codegraph.config.toml",
            "--task",
            "Configured usage feedback",
            "--usefulness",
            "medium",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    log_path = tmp_path / "configured/usage/task_feedback.local.jsonl"
    event = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert event["eventType"] == "task_feedback"
    assert event["task"]["title"] == "Configured usage feedback"
    assert str(log_path) in result.stdout


def test_usage_feedback_requires_config_before_writing_logs(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "repo_graph/usage_feedback.py"
    usage_dir = tmp_path / "usage"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--task",
            "Missing config feedback",
            "--usefulness",
            "low",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env=env_without_code_graph_config(CODE_GRAPH_USAGE_DIR=str(usage_dir)),
    )

    assert result.returncode == 2
    assert "code graph config was not found" in result.stderr
    assert "Traceback" not in result.stderr
    assert not usage_dir.exists()


def test_usage_feedback_records_missed_relation(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "repo_graph/usage_feedback.py"
    usage_dir = tmp_path / "usage"
    write_config(tmp_path, usage_log_dir="configured/usage")

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--config",
            "codegraph.config.toml",
            "missed",
            "--task",
            "Recording progress API review",
            "--query",
            "api-callers-for-endpoint: PUT /api/recording/sessions/{session_id}/progress",
            "--relation",
            "calls_api_endpoint",
            "--reason",
            "template_with_buildQuery",
            "--expected-source",
            "client/src/composables/useRecordingApi.js",
            "--expected-target",
            "api:PUT /api/recording/sessions/{session_id}/progress",
            "--suggested-fix",
            "Support query suffix after path templates",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env=env_without_code_graph_config(CODE_GRAPH_USAGE_DIR=str(usage_dir)),
    )

    assert result.returncode == 0
    log_path = usage_dir / "missed_relation.local.jsonl"
    event = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert event["eventType"] == "missed_relation"
    assert event["query"] == {
        "tool": "api-callers-for-endpoint",
        "input": "PUT /api/recording/sessions/{session_id}/progress",
    }
    assert event["missed"]["relationType"] == "calls_api_endpoint"
    assert event["missed"]["reason"] == "template_with_buildQuery"
    assert event["severity"] == "medium"


def test_usage_feedback_cleanup_prunes_expired_events_only_when_applied(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "repo_graph/usage_feedback.py"
    write_config(tmp_path, usage_log_dir="configured/usage")
    usage_dir = tmp_path / "configured/usage"
    usage_dir.mkdir(parents=True)
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
        json.dumps(old_event, sort_keys=True) + "\n" + json.dumps(current_event, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    dry_run = subprocess.run(
        [sys.executable, str(script), "--config", "codegraph.config.toml", "cleanup", "--dry-run"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert dry_run.returncode == 0
    assert "mode=dry-run" in dry_run.stdout
    assert "task_feedback.local.jsonl: existing=yes kept=1 removed=1" in dry_run.stdout
    assert len(log_path.read_text(encoding="utf-8").splitlines()) == 2

    applied = subprocess.run(
        [sys.executable, str(script), "--config", "codegraph.config.toml", "cleanup", "--apply"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert applied.returncode == 0
    assert "mode=apply" in applied.stdout
    remaining = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert remaining == [current_event]


def test_usage_feedback_cleanup_keeps_unparseable_records(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "repo_graph/usage_feedback.py"
    write_config(tmp_path, usage_log_dir="configured/usage")
    usage_dir = tmp_path / "configured/usage"
    usage_dir.mkdir(parents=True)
    log_path = usage_dir / "missed_relation.local.jsonl"
    missing_timestamp = {"eventType": "missed_relation"}
    invalid_timestamp = {"eventType": "missed_relation", "timestamp": "not-a-timestamp"}
    expired = {"eventType": "missed_relation", "timestamp": "2000-01-01T00:00:00+00:00"}
    log_path.write_text(
        "\n".join(
            [
                "not-json",
                json.dumps(missing_timestamp, sort_keys=True),
                json.dumps(invalid_timestamp, sort_keys=True),
                json.dumps(expired, sort_keys=True),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(script), "--config", "codegraph.config.toml", "cleanup", "--apply"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "missed_relation.local.jsonl: existing=yes kept=3 removed=1 malformed=1 missingTimestamp=2" in result.stdout
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "not-json",
        json.dumps(missing_timestamp, sort_keys=True),
        json.dumps(invalid_timestamp, sort_keys=True),
    ]


def test_usage_feedback_cleanup_deletes_file_when_all_records_expire(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "repo_graph/usage_feedback.py"
    write_config(tmp_path, usage_log_dir="configured/usage", retention_days=365)
    usage_dir = tmp_path / "configured/usage"
    usage_dir.mkdir(parents=True)
    log_path = usage_dir / "query_usage.local.jsonl"
    expired = {"eventType": "query_call", "timestamp": "2000-01-01T00:00:00+00:00"}
    log_path.write_text(json.dumps(expired, sort_keys=True) + "\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--config",
            "codegraph.config.toml",
            "cleanup",
            "--apply",
            "--retention-days",
            "1",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "retentionDays=1" in result.stdout
    assert "query_usage.local.jsonl: existing=yes kept=0 removed=1" in result.stdout
    assert not log_path.exists()


def write_config(repo_root: Path, *, usage_log_dir: str, retention_days: int = 14) -> Path:
    config_path = repo_root / "codegraph.config.toml"
    config_path.write_text(
        f"""
schema_version = 1

[project]
name = "UsageFeedbackFixture"
root = "."

[outputs]
graph = "generated/repo_graph.json"
schema = "generated/repo_graph.schema.json"
summary = "generated/repo_graph.summary.md"
usage_dir = "generated/usage"

[usage]
enabled_by_default = false
log_dir = "{usage_log_dir}"
retention_days = {retention_days}

[discovery]
include_suffixes = [".py", ".json"]
exclude_prefixes = ["generated/"]

[plugins.python_ast]
enabled = true
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return config_path


def env_without_code_graph_config(**overrides: str) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("CODE_GRAPH_CONFIG", None)
    env.update(overrides)
    return env
