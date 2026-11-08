import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build as builder
import release
from versioning import next_snapshot, parse_change, parse_version, read_version, release_version


class VersionTests(unittest.TestCase):
    def test_conventional_commit_version_selection(self):
        cases = [
            ("0.2.1.dev0", "0.2.0", "fix(camera): reconnect", "0.2.1"),
            ("0.2.1.dev0", "0.2.0", "feat(camera): zoom", "0.3.0"),
            ("0.2.1.dev0", "0.2.0", "refactor!: change API", "0.3.0"),
            ("1.2.1.dev0", "1.2.0", "fix: change API\n\nBREAKING CHANGE: old API removed", "2.0.0"),
            ("0.4.0.dev0", "0.2.0", "fix: reconnect", "0.4.0"),
            ("0.3.0.dev0", None, "feat: initial plugin", "0.3.0"),
            ("0.3.1.dev0", "0.3.0", "deps: update websocket", "0.3.1"),
        ]
        for current, latest, message, expected in cases:
            with self.subTest(message=message):
                self.assertEqual(release_version(current, latest, [parse_change(message)]), expected)
        features = [parse_change("feat: one"), parse_change("feat: two")]
        self.assertEqual(release_version("0.3.1.dev0", "0.3.0", features), "0.4.0")

    def test_generated_changes_and_final_versions_do_not_open_release_prs(self):
        changes = [parse_change("chore(snapshot): start 0.3.1.dev0"), parse_change("chore(release): prepare 0.3.0")]
        self.assertIsNone(release_version("0.3.1.dev0", "0.3.0", changes))
        self.assertIsNone(release_version("0.3.0", "0.2.0", [parse_change("feat: zoom")]))
        self.assertEqual(next_snapshot("0.3.0"), "0.3.1.dev0")

    def test_invalid_versions_and_titles(self):
        for version in ["0.3.0-SNAPSHOT", "0.3", "01.0.0", "0.3.0.dev1"]:
            with self.subTest(version=version), self.assertRaises(ValueError):
                parse_version(version)
        for title in ["Update things", "feat:", "fix: "]:
            with self.subTest(title=title), self.assertRaises(ValueError):
                parse_change(title)


class ReleaseFixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for name in ["pyproject.toml", "uv.lock", "LICENSE"]:
            shutil.copyfile(ROOT / name, self.root / name)
        self.write_version("0.3.0.dev0")
        shutil.copytree(ROOT / "src", self.root / "src")
        shutil.copytree(ROOT / "dist/ui", self.root / "dist/ui")
        (self.root / "docs/branding").mkdir(parents=True)
        shutil.copyfile(ROOT / "docs/branding/u1-logo.svg", self.root / "docs/branding/u1-logo.svg")
        (self.root / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
        for module in [release, builder]:
            context = patch.object(module, "ROOT", self.root)
            context.start()
            self.addCleanup(context.stop)
        config = self.root / "uv.toml"
        config.write_text("")
        # Exercise the real tools without inheriting developer config, caches, or signing.
        settings = {key: value for key, value in os.environ.items()
                    if not key.startswith(("UV_", "GIT_")) and key != "GITHUB_SHA"}
        settings.update(GH_REPO="example/plugin", UV_CACHE_DIR=str(self.root / "uv-cache"),
                        UV_CONFIG_FILE=str(config), UV_PYTHON=sys.executable,
                        UV_PYTHON_DOWNLOADS="never",
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        env = patch.dict("os.environ", settings, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def write_version(self, version):
        current = read_version(self.root)
        for name in ["pyproject.toml", "uv.lock"]:
            path = self.root / name
            path.write_text(path.read_text().replace(f'version = "{current}"', f'version = "{version}"', 1))

    def assert_lock_version(self):
        lock = tomllib.loads((self.root / "uv.lock").read_text())
        project = next(package for package in lock["package"] if package["source"] == {"virtual": "."})
        self.assertEqual(project["version"], read_version(self.root))


class FixtureIsolationTests(unittest.TestCase):
    def test_release_tools_ignore_conflicting_user_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            uv_config = Path(directory) / "uv.toml"
            uv_config.write_text('exclude-newer = "1970-01-01T00:00:00Z"\n')
            git_config = Path(directory) / "gitconfig"
            git_config.write_text('[commit]\n gpgsign = true\n[gpg]\n program = missing-test-signer\n')
            with patch.dict(os.environ, UV_CONFIG_FILE=str(uv_config), UV_PYTHON="missing-python",
                            UV_OFFLINE="true", GIT_CONFIG_GLOBAL=str(git_config),
                            GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="commit.gpgsign", GIT_CONFIG_VALUE_0="true"):
                fixture = ReleaseFixture()
                try:
                    fixture.setUp()
                    release.set_version(fixture.root, "0.3.0")
                    fixture.assert_lock_version()
                    subprocess.run(["git", "init", "-q"], cwd=fixture.root, check=True)
                    release.git("add", "pyproject.toml", "uv.lock")
                    release.git("-c", "user.name=Test", "-c", "user.email=test@example.com",
                                "commit", "-qm", "isolated fixture")
                    self.assertEqual(os.environ["UV_PYTHON"], sys.executable)
                    self.assertNotIn("UV_OFFLINE", os.environ)
                finally:
                    fixture.doCleanups()


class PreparationTests(ReleaseFixture):
    def setUp(self):
        super().setUp()
        context = patch.object(release, "api", return_value=[])
        context.start()
        self.addCleanup(context.stop)

    def test_refresh_preserves_public_notes_and_rebuilds_generated_notes(self):
        public = "- See print progress on the camera.\n\nEstimates can change."
        previous = release.format_notes(public, "old generated text", public_first=False)
        change = parse_change("feat(camera): add zoom")
        with patch.object(release, "history", return_value=("0.2.0", [("a" * 40, "feat(camera): add zoom", change)])):
            release.prepare_pr("release", previous_body=previous)
        body = (self.root / "dist/automation-pr-body.md").read_text()
        self.assertEqual(release.public_section(body), public)
        self.assertIn("feat(camera): add zoom", body)
        self.assertNotIn("old generated text", body)
        self.assertLess(body.index("## Generated notes"), body.index("## Public notes"))

    def test_open_release_body_does_not_read_fork_notes(self):
        fork = {"head": {"repo": {"full_name": "someone/fork"}}, "body": "fork text"}
        own = {"head": {"repo": {"full_name": "example/plugin"}}, "body": "reviewed text"}
        with patch.object(release, "api", return_value=[fork, own]):
            self.assertEqual(release.open_release_body(), "reviewed text")

    def test_malformed_public_notes_stop_refresh_before_file_changes(self):
        before = (self.root / "CHANGELOG.md").read_text()
        change = parse_change("feat(camera): add zoom")
        with patch.object(release, "history", return_value=("0.2.0", [("a" * 40, "feat(camera): add zoom", change)])):
            with self.assertRaisesRegex(ValueError, "marker"):
                release.prepare_pr("release", previous_body="<!-- orca:public-notes:start -->unfinished")
        self.assertEqual((self.root / "CHANGELOG.md").read_text(), before)
        self.assertEqual(read_version(self.root), "0.3.0.dev0")

    def test_release_and_next_snapshot_update_project_lock_and_changelog(self):
        change = parse_change("feat(camera): add zoom")
        with patch.object(release, "history", return_value=("0.2.0", [("a" * 40, "feat(camera): add zoom", change)])):
            plan = release.prepare_pr("release")
        self.assertEqual(plan["version"], "0.3.0")
        self.assertEqual(plan["branch"], "automation/release")
        self.assertEqual(read_version(self.root), "0.3.0")
        self.assert_lock_version()
        changelog = (self.root / "CHANGELOG.md").read_text()
        self.assertIn("## 0.3.0", changelog)
        self.assertIn("feat(camera): add zoom", changelog)
        artifact = builder.build(self.root / "dist", release_tag="v0.3.0")
        self.assertIn('# version = "0.3.0"', artifact.read_text())
        plan = release.prepare_pr("snapshot", "0.3.0")
        self.assertEqual(plan["version"], "0.3.1.dev0")
        self.assert_lock_version()
        self.assertEqual((self.root / "CHANGELOG.md").read_text(), changelog)
        self.assertIsNone(release.prepare_pr("snapshot", "0.3.0"))

    def test_empty_release_plan_does_not_change_files(self):
        before = (self.root / "pyproject.toml").read_bytes()
        with patch.object(release, "history", return_value=("0.2.0", [])):
            self.assertIsNone(release.prepare_pr("release"))
        self.assertEqual((self.root / "pyproject.toml").read_bytes(), before)

    def test_final_version_is_allowed_only_on_the_generated_release_pr(self):
        self.write_version("0.3.0")
        commits = [("a" * 40, "fix: camera playback", parse_change("fix: camera playback"))]
        reviewed = f"- [x] {release.REVIEW_TEXT}\n\n" + release.format_notes("Camera controls.", release.format_changes(commits))
        event = {"pull_request": {
            "title": "chore(release): prepare 0.3.0", "body": reviewed,
            "head": {"ref": "automation/release", "repo": {"full_name": "example/plugin"}},
            "base": {"repo": {"full_name": "example/plugin"}}, "labels": [{"name": "release"}],
        }}
        with patch.object(release, "history", return_value=("0.2.0", commits)):
            release.check_pr(event)
        body = event["pull_request"]["body"]
        event["pull_request"]["body"] = release.format_notes(release.PUBLIC_PLACEHOLDER, "generated")
        with self.assertRaisesRegex(ValueError, "Write public release notes"):
            release.check_pr(event)
        event["pull_request"]["body"] = body
        event["pull_request"]["head"]["repo"]["full_name"] = "someone/fork"
        with self.assertRaisesRegex(ValueError, "Only the generated release PR"):
            release.check_pr(event)
        event["pull_request"]["head"]["repo"]["full_name"] = "example/plugin"
        event["pull_request"]["title"] = "chore(release): prepare 0.4.0"
        with self.assertRaisesRegex(ValueError, "title must match"):
            release.check_pr(event)

    def test_history_uses_reachable_stable_tags_and_skips_automation(self):
        generated = "chore(snapshot): start 0.3.1.dev0"
        message = "feat(camera): zoom\n\nBREAKING CHANGE: altered controls\n"
        records = "a" * 40 + "\x00" + generated + "\x00\n" + "b" * 40 + "\x00" + message + "\x00"
        with patch.object(release, "git", side_effect=["v0.2.0\nv0.3.0\nv0.4.0.dev0\nother", records]) as git:
            latest, changes = release.history()
        self.assertEqual(latest, "0.3.0")
        self.assertEqual(len(changes), 1)
        self.assertTrue(changes[0][2].breaking)
        self.assertEqual(git.call_args.args[-1], "v0.3.0..HEAD")


class SnapshotOnlyTests(ReleaseFixture):
    def setUp(self):
        super().setUp()
        self.write_version("0.3.0")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        release.git("add", "pyproject.toml", "uv.lock")
        release.git("-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "-qm", "base")
        self.pr = {"title": "chore(snapshot): start 0.3.1.dev0", "labels": [{"name": "snapshot"}],
                   "head": {"ref": "automation/next-snapshot", "repo": {"full_name": "example/plugin"}},
                   "base": {"ref": "main", "sha": release.git("rev-parse", "HEAD"),
                            "repo": {"full_name": "example/plugin"}}}
        self.write_version("0.3.1.dev0")
        self.commit()

    def commit(self):
        release.git("add", "pyproject.toml", "uv.lock")
        release.git("-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "-qm", "snapshot")

    def test_exact_snapshot_version_bump_uses_light_checks(self):
        self.assertTrue(release.snapshot_only(self.pr))

    def test_code_classification_does_not_validate_editable_pr_policy(self):
        self.pr["title"] = "Unfinished title"
        event = self.root / "event.json"
        event.write_text(json.dumps({"pull_request": self.pr}))
        with patch.dict(os.environ, GITHUB_EVENT_PATH=str(event)), \
                patch("sys.argv", ["release.py", "classify-pr"]), \
                patch.object(release, "check_pr", side_effect=AssertionError("Policy belongs to its own check")), \
                patch.object(release, "write_outputs") as outputs:
            release.main()
        outputs.assert_called_once_with(snapshot_only="false")

    def test_label_alone_or_fork_cannot_skip_full_checks(self):
        self.pr["head"]["ref"] = "feature/example"
        self.assertFalse(release.snapshot_only(self.pr))
        self.pr["head"]["ref"] = "automation/next-snapshot"
        self.pr["head"]["repo"]["full_name"] = "other/fork"
        self.assertFalse(release.snapshot_only(self.pr))

    def test_other_edits_in_version_files_require_full_checks(self):
        with (self.root / "uv.lock").open("a") as output:
            output.write("\n# unrelated lock change\n")
        self.commit()
        self.assertFalse(release.snapshot_only(self.pr))

    def test_other_changed_files_require_full_checks(self):
        (self.root / "extra.py").write_text("pass\n")
        release.git("add", "extra.py")
        self.commit()
        self.assertFalse(release.snapshot_only(self.pr))

    def test_missing_label_or_unexpected_version_requires_full_checks(self):
        self.pr["labels"] = []
        self.assertFalse(release.snapshot_only(self.pr))
        self.pr["labels"] = [{"name": "snapshot"}]
        self.write_version("0.4.0.dev0")
        self.pr["title"] = "chore(snapshot): start 0.4.0.dev0"
        self.commit()
        self.assertFalse(release.snapshot_only(self.pr))


class SnapshotMergeTests(unittest.TestCase):
    def setUp(self):
        self.sha = "a" * 40
        self.pr = {"state": "open", "draft": False, "title": "chore(snapshot): start 0.3.1.dev0",
                   "base": {"ref": "main"}, "head": {"ref": "automation/next-snapshot", "sha": self.sha,
                                                       "repo": {"full_name": "example/plugin"}},
                   "labels": [{"name": "snapshot"}]}
        self.files = [{"filename": "pyproject.toml"}, {"filename": "uv.lock"}]
        context = patch.object(release, "api", side_effect=lambda path: self.files if "/files?" in path else self.pr)
        context.start()
        self.addCleanup(context.stop)
        context = patch.dict("os.environ", {"GH_REPO": "example/plugin", "GH_CHECKS_TOKEN": "checks-token"})
        context.start()
        self.addCleanup(context.stop)
        self.github = Mock(return_value=json.dumps([{"status": "completed", "conclusion": "success"}]))
        context = patch.object(release, "gh", self.github)
        context.start()
        self.addCleanup(context.stop)
        context = patch.object(release.time, "sleep")
        context.start()
        self.addCleanup(context.stop)

    def test_only_the_checked_snapshot_commit_is_squash_merged(self):
        release.merge_snapshot(3, self.sha, "0.3.1.dev0")
        self.github.assert_any_call("run", "list", "--repo", "example/plugin", "--workflow", "check.yml",
                                    "--event", "pull_request", "--commit", self.sha, "--limit", "20",
                                    "--json", "status,conclusion", token="checks-token")
        self.github.assert_any_call("run", "list", "--repo", "example/plugin", "--workflow", "pr-policy.yml",
                                    "--event", "pull_request", "--commit", self.sha, "--limit", "20",
                                    "--json", "status,conclusion", token="checks-token")
        self.github.assert_any_call("pr", "merge", "3", "--repo", "example/plugin", "--squash",
                                    "--match-head-commit", self.sha)

    def test_cancelled_run_does_not_hide_successful_run_for_same_commit(self):
        runs = json.dumps([
            {"status": "completed", "conclusion": "cancelled"},
            {"status": "completed", "conclusion": "success"},
        ])
        self.github.side_effect = lambda *args, **kwargs: runs if args[:2] == ("run", "list") else ""
        release.merge_snapshot(3, self.sha, "0.3.1.dev0")
        self.assertEqual(self.github.call_args.args[:2], ("pr", "merge"))

    def test_waits_for_registered_checks_and_replacements_of_cancelled_runs(self):
        pending = iter([json.dumps(runs) for runs in [[],
            [{"status": "in_progress", "conclusion": None}],
            [{"status": "completed", "conclusion": "cancelled"}],
            [{"status": "completed", "conclusion": "success"}]]])
        self.github.side_effect = lambda *args, **kwargs: (
            next(pending) if "check.yml" in args else self.github.return_value
        ) if args[:2] == ("run", "list") else ""
        release.merge_snapshot(3, self.sha, "0.3.1.dev0")
        self.assertEqual(self.github.call_args.args[:2], ("pr", "merge"))

    def test_code_success_does_not_bypass_missing_or_failed_policy(self):
        for policy in [[], [{"status": "completed", "conclusion": "failure"}],
                       [{"status": "completed", "conclusion": "skipped"}]]:
            with self.subTest(policy=policy):
                self.github.reset_mock()
                self.github.side_effect = lambda *args, **kwargs: (
                    json.dumps(policy) if "pr-policy.yml" in args else self.github.return_value
                )
                with self.assertRaises(ValueError):
                    release.merge_snapshot(3, self.sha, "0.3.1.dev0")
                self.assertFalse(any(call.args[:2] == ("pr", "merge") for call in self.github.call_args_list))

    def test_failed_or_missing_checks_never_merge(self):
        for runs in [[], [{"status": "completed", "conclusion": "failure"}]]:
            with self.subTest(runs=runs):
                self.github.reset_mock()
                self.github.return_value = json.dumps(runs)
                with self.assertRaises(ValueError):
                    release.merge_snapshot(3, self.sha, "0.3.1.dev0")
                self.assertFalse(any(call.args[:2] == ("pr", "merge") for call in self.github.call_args_list))

    def test_release_prs_changed_heads_and_unexpected_files_never_merge(self):
        cases = [lambda: self.pr["head"].update(ref="automation/release"),
                 lambda: self.pr["head"].update(sha="b" * 40),
                 lambda: self.pr["head"]["repo"].update(full_name="someone/fork"),
                 lambda: self.files.append({"filename": "src/plugin.py"})]
        for mutate in cases:
            with self.subTest(mutate=mutate):
                original = json.loads(json.dumps(self.pr))
                files = self.files.copy()
                mutate()
                with self.assertRaises(ValueError):
                    release.merge_snapshot(3, self.sha, "0.3.1.dev0")
                self.github.assert_not_called()
                self.pr, self.files = original, files


class FakeGitHub:
    def __init__(self):
        self.release = None
        self.tag_sha = None
        self.sha = "a" * 40
        self.assets = {}
        self.calls = []
        self.pr = {"merged_at": "2026-11-02T12:00:00Z", "merge_commit_sha": self.sha,
                   "title": "chore(release): prepare 0.3.0",
                   "head": {"ref": "automation/release", "repo": {"full_name": "example/plugin"}},
                   "base": {"ref": "main", "repo": {"full_name": "example/plugin"}},
                   "body": release.format_notes("Camera controls are easier to use.", "internal history")}

    def __call__(self, *args):
        self.calls.append(args)
        if args[0] == "api":
            if f"/commits/{self.sha}/pulls?" in args[1]:
                return json.dumps([self.pr])
            if "/git/ref/tags/" in args[1] and self.tag_sha:
                return json.dumps({"object": {"type": "commit", "sha": self.tag_sha}})
            if "/commits/" in args[1] and self.tag_sha:
                return json.dumps({"sha": self.tag_sha})
            if "/commits/" in args[1]:
                raise RuntimeError("No commit found for SHA: v0.3.0 (HTTP 422)")
            if args[1].endswith("/releases/1") and self.release:
                return json.dumps(dict(self.release, id=1, assets=[{"name": name} for name in self.assets]))
            raise RuntimeError("Not Found (HTTP 404)")
        operation = args[1]
        if operation == "view":
            if self.release is None:
                raise RuntimeError("release not found")
            return json.dumps({"databaseId": 1})
        elif operation == "create":
            self.release = {"draft": True, "tag_name": "untagged-draft", "target_commitish": args[args.index("--target") + 1],
                            "body": Path(args[args.index("--notes-file") + 1]).read_text()}
        elif operation == "upload":
            for filename in args[3:args.index("--repo")]:
                path = Path(filename)
                self.assets[path.name] = path.read_bytes()
        elif operation == "download":
            destination = Path(args[args.index("--dir") + 1])
            for index, argument in enumerate(args):
                if argument == "--pattern":
                    name = args[index + 1]
                    (destination / name).write_bytes(self.assets[name])
        elif operation == "edit":
            self.release["draft"] = False
            self.tag_sha = self.sha
        else:
            raise AssertionError(args)
        return ""


class PublicationTests(ReleaseFixture):
    def setUp(self):
        super().setUp()
        self.write_version("0.3.0")
        (self.root / "CHANGELOG.md").write_text("# Changelog\n\n## 0.3.0\n\n- Camera controls.\n")
        self.github = FakeGitHub()
        for target, value in [
            ("gh", self.github),
            ("git", lambda *args: "" if args[0] == "status" else self.github.sha),
        ]:
            context = patch.object(release, target, value)
            context.start()
            self.addCleanup(context.stop)

        builder.build(self.root / "dist", release_tag="v0.3.0")
        release.prepare_artifact(self.root / "dist")

    def test_publication_uses_prepared_bytes_without_running_the_builder(self):
        with patch.object(builder, "build", side_effect=AssertionError("Publish must not rebuild")):
            release.publish(self.root / "dist")
        self.assertEqual(self.github.assets["orca-snapmaker-u1-camera-plugin_any.py"],
                         (self.root / "dist/orca-snapmaker-u1-camera-plugin_any.py").read_bytes())

    def test_wrong_commit_version_or_digest_is_rejected_before_github_access(self):
        path = self.root / "dist/release-manifest.json"
        original = json.loads(path.read_text())
        for key, value in [("commit", "b" * 40), ("version", "0.4.0"), ("sha256", {})]:
            with self.subTest(key=key):
                path.write_text(json.dumps(dict(original, **{key: value})))
                with self.assertRaisesRegex(ValueError, "release commit, version, and bytes"):
                    release.publish(self.root / "dist")
                self.assertEqual(self.github.calls, [])
        path.write_text(json.dumps(original))

    def test_modified_artifact_and_rewritten_checksum_still_fail_manifest(self):
        artifact = self.root / "dist/orca-snapmaker-u1-camera-plugin_any.py"
        artifact.write_bytes(b"raise RuntimeError('must never execute')\n")
        checksum = release.hashlib.sha256(artifact.read_bytes()).hexdigest()
        (self.root / "dist/SHA256SUMS").write_text(checksum + "  " + artifact.name + "\n")
        with self.assertRaisesRegex(ValueError, "release commit, version, and bytes"):
            release.publish(self.root / "dist")
        self.assertEqual(self.github.calls, [])

    def test_corrupt_checksum_is_rejected_before_github_access(self):
        (self.root / "dist/SHA256SUMS").write_text("incorrect checksum\n")
        with self.assertRaisesRegex(ValueError, "SHA256SUMS"):
            release.publish(self.root / "dist")
        self.assertEqual(self.github.calls, [])

    def test_workflow_checkout_must_match_expected_commit(self):
        with patch.dict(os.environ, GITHUB_SHA="b" * 40):
            with self.assertRaisesRegex(ValueError, "workflow commit"):
                release.publish(self.root / "dist")
        self.assertEqual(self.github.calls, [])

    def test_missing_manifest_does_not_fall_back_to_building(self):
        (self.root / "dist/release-manifest.json").unlink()
        with self.assertRaises(FileNotFoundError):
            release.publish(self.root / "dist")
        self.assertEqual(self.github.calls, [])

    def test_draft_is_verified_before_publication_and_reruns_do_not_write(self):
        self.assertEqual(release.publish(self.root / "dist"), "v0.3.0")
        self.assertFalse(self.github.release["draft"])
        self.assertEqual(self.github.tag_sha, self.github.sha)
        operations = [call[1] for call in self.github.calls if call[0] == "release"]
        self.assertLess(operations.index("download"), operations.index("edit"))
        self.github.calls.clear()
        release.publish(self.root / "dist")
        self.assertFalse(any(call[:2] in [("release", "upload"), ("release", "edit"), ("release", "create")]
                             for call in self.github.calls))

    def test_release_captures_public_notes_first_and_reruns_ignore_pr_edits(self):
        release.publish(self.root / "dist")
        body = self.github.release["body"]
        self.assertEqual(release.public_notes(body), "Camera controls are easier to use.")
        self.assertLess(body.index("## Public notes"), body.index("## Generated notes"))
        self.assertIn("- Camera controls.", body)
        self.assertIn("Source commit:", body)
        self.github.pr["body"] = release.format_notes("Edited after publication", "changed")
        release.publish(self.root / "dist")
        self.assertEqual(self.github.release["body"], body)

    def test_unreviewed_public_notes_do_not_create_a_release(self):
        self.github.pr["body"] = release.format_notes(release.PUBLIC_PLACEHOLDER, "generated")
        with self.assertRaisesRegex(ValueError, "Write public release notes"):
            release.publish(self.root / "dist")
        self.assertIsNone(self.github.release)

    def test_only_exact_same_repository_release_merge_is_used(self):
        for field, value in [("merge_commit_sha", "b" * 40), ("title", "chore(release): prepare 0.4.0"),
                             ("merged_at", None)]:
            original = self.github.pr[field]
            self.github.pr[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "exact release commit and version"):
                release.publish(self.root / "dist")
            self.github.pr[field] = original
        self.github.pr["head"]["repo"]["full_name"] = "someone/fork"
        with self.assertRaisesRegex(ValueError, "exact release commit and version"):
            release.publish(self.root / "dist")
        self.assertIsNone(self.github.release)

    def test_published_mismatched_assets_are_never_overwritten(self):
        release.publish(self.root / "dist")
        self.github.assets["orca-snapmaker-u1-camera-plugin_any.py"] = b"changed"
        self.github.calls.clear()
        with self.assertRaisesRegex(ValueError, "immutable"):
            release.publish(self.root / "dist")
        self.assertFalse(any(call[:2] == ("release", "upload") for call in self.github.calls))

    def test_published_missing_assets_are_never_added(self):
        release.publish(self.root / "dist")
        del self.github.assets["SHA256SUMS"]
        self.github.calls.clear()
        with self.assertRaisesRegex(ValueError, "will not be modified"):
            release.publish(self.root / "dist")
        self.assertFalse(any(call[:2] == ("release", "upload") for call in self.github.calls))

    def test_existing_tag_must_resolve_to_the_release_commit(self):
        self.github.tag_sha = "b" * 40
        with self.assertRaisesRegex(ValueError, "another commit"):
            release.publish(self.root / "dist")
        self.assertFalse(any(call[:2] == ("release", "create") for call in self.github.calls))

    def test_annotated_tag_is_checked_against_its_underlying_commit(self):
        self.github.tag_sha = self.github.sha
        def github(*args):
            if args[0] == "api" and "/git/ref/tags/" in args[1]:
                return json.dumps({"object": {"type": "tag", "sha": "c" * 40}})
            return self.github(*args)
        with patch.object(release, "gh", side_effect=github):
            self.assertEqual(release.publish(self.root / "dist"), "v0.3.0")
        self.assertFalse(self.github.release["draft"])

    def test_tag_lookup_errors_do_not_create_a_release(self):
        def github(*args):
            if args[0] == "api" and "/git/ref/tags/" in args[1]:
                raise RuntimeError("Validation failed (HTTP 422)")
            return self.github(*args)
        with patch.object(release, "gh", side_effect=github):
            with self.assertRaisesRegex(RuntimeError, "HTTP 422"):
                release.publish(self.root / "dist")
        self.assertFalse(any(call[:2] in [("release", "create"), ("release", "upload"), ("release", "edit")]
                             for call in self.github.calls))

    def test_pending_tag_lookup_finds_draft_without_a_matching_rest_tag_name(self):
        self.github.release = {"tag_name": "untagged-draft", "draft": True,
                               "target_commitish": self.github.sha}
        draft = release.release_by_tag("v0.3.0")
        self.assertTrue(draft["draft"])
        self.assertEqual(draft["tag_name"], "untagged-draft")
        self.assertIn(("api", "repos/example/plugin/releases/1"), self.github.calls)

    def test_dirty_checkout_cannot_publish(self):
        with patch.object(release, "git", return_value=" M pyproject.toml"):
            with self.assertRaisesRegex(ValueError, "clean checkout"):
                release.publish(self.root / "dist")
        self.assertEqual(self.github.calls, [])

    def test_development_version_cannot_publish(self):
        path = self.root / "pyproject.toml"
        path.write_text(path.read_text().replace('version = "0.3.0"', 'version = "0.3.1.dev0"'))
        with self.assertRaisesRegex(ValueError, "Development versions"):
            release.publish(self.root / "dist")
        self.assertEqual(self.github.calls, [])

    def test_partial_draft_can_resume_without_replacing_assets(self):
        artifact = builder.build(self.root / "dist")
        self.github.release = {"draft": True, "target_commitish": self.github.sha,
                               "body": release.format_notes("Camera controls.", "generated")}
        self.github.assets[artifact.name] = artifact.read_bytes()
        release.publish(self.root / "dist")
        upload = next(call for call in self.github.calls if call[:2] == ("release", "upload"))
        self.assertNotIn(str(artifact), upload)
        self.assertFalse(self.github.release["draft"])
