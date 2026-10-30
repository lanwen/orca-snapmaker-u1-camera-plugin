import json
from pathlib import Path
import shutil
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
        (self.root / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
        for module in [release, builder]:
            context = patch.object(module, "ROOT", self.root)
            context.start()
            self.addCleanup(context.stop)
        # Release preparation must work without a developer's cached package metadata.
        env = patch.dict("os.environ", {"GH_REPO": "example/plugin", "UV_CACHE_DIR": str(self.root / "uv-cache")})
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


class PreparationTests(ReleaseFixture):
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
        event = {"pull_request": {
            "title": "chore(release): prepare 0.3.0", "body": "",
            "head": {"ref": "automation/release", "repo": {"full_name": "example/plugin"}},
            "base": {"repo": {"full_name": "example/plugin"}}, "labels": [{"name": "release"}],
        }}
        release.check_pr(event)
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
                                    "--event", "pull_request", "--commit", self.sha, "--limit", "1",
                                    "--json", "status,conclusion", token="checks-token")
        self.github.assert_any_call("pr", "merge", "3", "--repo", "example/plugin", "--squash",
                                    "--match-head-commit", self.sha)

    def test_waits_for_registered_checks_and_replacements_of_cancelled_runs(self):
        self.github.side_effect = [json.dumps(runs) for runs in [[],
            [{"status": "in_progress", "conclusion": None}],
            [{"status": "completed", "conclusion": "cancelled"}],
            [{"status": "completed", "conclusion": "success"}]]] + [""]
        release.merge_snapshot(3, self.sha, "0.3.1.dev0")
        self.assertEqual(self.github.call_args.args[:2], ("pr", "merge"))

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

    def __call__(self, *args):
        self.calls.append(args)
        if args[0] == "api":
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
            self.release = {"draft": True, "tag_name": "untagged-draft", "target_commitish": args[args.index("--target") + 1]}
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
            ("build", lambda **kwargs: builder.build(self.root / "dist", **kwargs)),
        ]:
            context = patch.object(release, target, value)
            context.start()
            self.addCleanup(context.stop)

    def test_draft_is_verified_before_publication_and_reruns_do_not_write(self):
        self.assertEqual(release.publish(), "v0.3.0")
        self.assertFalse(self.github.release["draft"])
        self.assertEqual(self.github.tag_sha, self.github.sha)
        operations = [call[1] for call in self.github.calls if call[0] == "release"]
        self.assertLess(operations.index("download"), operations.index("edit"))
        self.github.calls.clear()
        release.publish()
        self.assertFalse(any(call[:2] in [("release", "upload"), ("release", "edit"), ("release", "create")]
                             for call in self.github.calls))

    def test_published_mismatched_assets_are_never_overwritten(self):
        release.publish()
        self.github.assets["orca-snapmaker-u1-camera-plugin.py"] = b"changed"
        self.github.calls.clear()
        with self.assertRaisesRegex(ValueError, "immutable"):
            release.publish()
        self.assertFalse(any(call[:2] == ("release", "upload") for call in self.github.calls))

    def test_published_missing_assets_are_never_added(self):
        release.publish()
        del self.github.assets["SHA256SUMS"]
        self.github.calls.clear()
        with self.assertRaisesRegex(ValueError, "will not be modified"):
            release.publish()
        self.assertFalse(any(call[:2] == ("release", "upload") for call in self.github.calls))

    def test_existing_tag_must_resolve_to_the_release_commit(self):
        self.github.tag_sha = "b" * 40
        with self.assertRaisesRegex(ValueError, "another commit"):
            release.publish()
        self.assertFalse(any(call[:2] == ("release", "create") for call in self.github.calls))

    def test_annotated_tag_is_checked_against_its_underlying_commit(self):
        self.github.tag_sha = self.github.sha
        def github(*args):
            if args[0] == "api" and "/git/ref/tags/" in args[1]:
                return json.dumps({"object": {"type": "tag", "sha": "c" * 40}})
            return self.github(*args)
        with patch.object(release, "gh", side_effect=github):
            self.assertEqual(release.publish(), "v0.3.0")
        self.assertFalse(self.github.release["draft"])

    def test_tag_lookup_errors_do_not_create_a_release(self):
        def github(*args):
            if args[0] == "api" and "/git/ref/tags/" in args[1]:
                raise RuntimeError("Validation failed (HTTP 422)")
            return self.github(*args)
        with patch.object(release, "gh", side_effect=github):
            with self.assertRaisesRegex(RuntimeError, "HTTP 422"):
                release.publish()
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
                release.publish()
        self.assertEqual(self.github.calls, [])

    def test_development_version_cannot_publish(self):
        path = self.root / "pyproject.toml"
        path.write_text(path.read_text().replace('version = "0.3.0"', 'version = "0.3.1.dev0"'))
        with self.assertRaisesRegex(ValueError, "Development versions"):
            release.publish()
        self.assertEqual(self.github.calls, [])

    def test_partial_draft_can_resume_without_replacing_assets(self):
        artifact = builder.build(self.root / "dist")
        self.github.release = {"draft": True, "target_commitish": self.github.sha}
        self.github.assets[artifact.name] = artifact.read_bytes()
        release.publish()
        upload = next(call for call in self.github.calls if call[:2] == ("release", "upload"))
        self.assertNotIn(str(artifact), upload)
        self.assertFalse(self.github.release["draft"])
