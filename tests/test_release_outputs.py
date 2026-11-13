"""Exercise workflow commands with noisy task setup and the real release CLI."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib
import pytest

ROOT = Path(__file__).parents[1]

DRIVER = '''
import json, os, sys
sys.path.insert(0, os.environ["RELEASE_SCRIPTS"])
import release
release.read_version = lambda root: "0.4.1.dev0"
release.open_release_body = lambda: ""
release.prepare_pr = lambda *args: json.loads(os.environ["RELEASE_PLAN"])
release.snapshot_only = lambda pr: True
release.check_pr = lambda event: None
sys.argv = ["release.py", sys.argv[-1].split(":")[-1]]
release.main()
'''


class TestReleaseOutput:
    def run_workflow_command(self, tmp_path, workflow, task, expected, plan=None):
        # Execute the actual workflow command, including any shell redirection.
        command = next(line[line.index("mise run release:"):].strip()
                       for line in (ROOT / ".github/workflows" / workflow).read_text().splitlines()
                       if f"mise run release:{task}" in line)
        directory = str(tmp_path)
        root = Path(directory)
        driver = root / "driver.py"
        driver.write_text(DRIVER)
        mise = root / "mise"
        mise.write_text('#!/bin/sh\nprintf "%s\\n" "Dependency verification progress"\n'
                        'exec "$RELEASE_PYTHON" "$RELEASE_DRIVER" "$@"\n')
        mise.chmod(0o755)
        output = root / "outputs"
        output.write_text("earlier=preserved\n")
        event = root / "event.json"
        event.write_text(json.dumps({"pull_request": {}}))
        env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"],
                   GITHUB_OUTPUT=str(output), RELEASE_PYTHON=sys.executable,
                   RELEASE_SCRIPTS=str(ROOT / "scripts"), RELEASE_DRIVER=str(driver),
                   RELEASE_PLAN=json.dumps(plan), RELEASE_MODE="release", GITHUB_EVENT_PATH=str(event))
        result = subprocess.run(["sh", "-eu", "-c", command], env=env,
                                capture_output=True, text=True, check=True)
        lines = output.read_text().splitlines()
        assert all(('=' in line for line in lines)), 'Setup logs must not be parsed as GitHub step outputs'
        assert dict((line.split('=', 1) for line in lines)) == {'earlier': 'preserved', **expected}
        assert 'Dependency verification progress' in result.stdout, 'Setup progress stays in the job log'

    def test_release_plan_survives_noisy_setup(self, tmp_path):
        plan = {"version": "0.4.1", "branch": "automation/release",
                "title": "chore(release): prepare 0.4.1", "label": "release"}
        self.run_workflow_command(tmp_path, "release-pr.yml", "prepare-pr",
                                  {"changed": "true", **plan}, plan)

    def test_no_changes_survives_noisy_setup(self, tmp_path):
        self.run_workflow_command(tmp_path, "release-pr.yml", "prepare-pr", {"changed": "false"})

    def test_version_info_survives_noisy_setup(self, tmp_path):
        self.run_workflow_command(tmp_path, "release.yml", "info",
                                  {"version": "0.4.1.dev0", "final": "false"})

    def test_code_workflow_receives_snapshot_classification(self, tmp_path):
        self.run_workflow_command(tmp_path, "check.yml", "classify-pr", {"snapshot_only": "true"})

    def test_metadata_workflow_checks_policy(self, tmp_path):
        self.run_workflow_command(tmp_path, "pr-policy.yml", "check-pr", {"snapshot_only": "true"})

    def test_publish_workflow_isolated_from_dependency_tools_and_other_runs(self):
        workflow = (ROOT / ".github/workflows/release.yml").read_text()
        validate, publish = workflow.split("  publish:\n", 1)
        publish = publish.split("  next-snapshot:\n", 1)[0]
        assert 'mise run check' in validate
        assert 'python scripts/release.py prepare-artifact' in validate
        assert 'artifact-id: ${{ steps.artifact.outputs.artifact-id }}' in validate
        assert 'artifact-ids: ${{ needs.validate.outputs.artifact-id }}' in publish
        assert 'run-id:' not in publish
        assert 'github-token:' not in publish
        assert 'ref: ${{ github.sha }}' in publish
        assert 'mise' not in publish
        assert publish.index('python3 scripts/release.py verify-artifact') < publish.index('Generate release GitHub App token')
        assert publish.index('Generate release GitHub App token') < publish.index('python3 scripts/release.py publish')
        tasks = tomllib.loads((ROOT / "mise.toml").read_text())["tasks"]
        assert 'depends' not in tasks['release']

    def test_local_cli_still_prints_version_info_without_github_output(self, tmp_path):
        directory = str(tmp_path)
        driver = Path(directory) / "driver.py"
        driver.write_text(DRIVER)
        env = dict(os.environ, RELEASE_SCRIPTS=str(ROOT / "scripts"))
        env.pop("GITHUB_OUTPUT", None)
        result = subprocess.run([sys.executable, str(driver), "info"], env=env,
                                capture_output=True, text=True, check=True)
        assert dict((line.split('=', 1) for line in result.stdout.splitlines())) == {'version': '0.4.1.dev0', 'final': 'false'}
