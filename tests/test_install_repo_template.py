from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = PLUGIN_ROOT / "scripts/install_repo_template.py"


class InstallRepoTemplateTests(unittest.TestCase):
    def test_dry_run_does_not_write_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_repo(Path(tmp))

            result = run_installer(repo, "--dry-run")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Dry run only", result.stdout)
            self.assertFalse((repo / "codegraph.config.toml").exists())
            subprocess.run(["git", "-C", str(repo), "diff", "--exit-code"], check=True)

    def test_apply_is_idempotent_and_installs_ignored_local_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_repo(Path(tmp))

            first = run_installer(repo, "--apply")
            second = run_installer(repo, "--apply")

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("No changes needed.", second.stdout)
            self.assertTrue((repo / "codegraph.config.toml").exists())
            self.assertTrue((repo / "docs/operations/onboarding/code-graph.md").exists())
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            ignored = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "check-ignore",
                    "analysis/code_graph/usage/query_usage.local.jsonl",
                    "analysis/code_graph/codeql-candidates.local.json",
                    "analysis/code_graph/repo_graph.local.json",
                    "analysis/code_graph/repo_graph.local.schema.json",
                    "analysis/code_graph/repo_graph.local.summary.md",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("analysis/code_graph/repo_graph.local.json", ignored.stdout)
            self.assertIn("analysis/code_graph/repo_graph.local.schema.json", ignored.stdout)

    def test_makefile_template_does_not_install_missing_cleanup_command(self) -> None:
        snippet = (PLUGIN_ROOT / "assets/Makefile.snippet").read_text(encoding="utf-8")

        self.assertNotIn("code-graph-clean-usage", snippet)
        self.assertIn("code-graph-feedback", snippet)

    def test_plugin_mcp_config_uses_portable_plugin_root(self) -> None:
        payload = json.loads((PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8"))
        server = payload["mcpServers"]["repo-relationship-graph"]
        command_text = " ".join([server["command"], *server["args"]])

        self.assertIn("CODE_GRAPH_PLUGIN_ROOT", command_text)
        self.assertIn("$HOME/plugins/repo-relationship-graph", command_text)
        self.assertNotIn("/Users/mid/plugins/repo-relationship-graph", command_text)

    def test_existing_code_graph_makefile_target_is_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_repo(Path(tmp))
            (repo / "Makefile").write_text("code-graph:\n\ttrue\n", encoding="utf-8")

            result = run_installer(repo, "--dry-run")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("conflict: Makefile", result.stdout)


def init_repo(path: Path) -> Path:
    subprocess.run(["git", "-C", str(path), "init"], check=True, capture_output=True)
    return path


def run_installer(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(INSTALLER), "--repo-root", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    )


if __name__ == "__main__":
    unittest.main()
