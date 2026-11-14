import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
import pytest
from types import SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build as builder
import release
from versioning import next_snapshot, parse_change, parse_version, read_version, release_version
from release_helpers import isolated_release_tree

release_api = release.api


class TestVersion:
    @pytest.mark.parametrize("current,latest,message,expected", [
        pytest.param("0.2.1.dev0", "0.2.0", "fix(camera): reconnect", "0.2.1", id="patch"),
        pytest.param("0.2.1.dev0", "0.2.0", "feat(camera): zoom", "0.3.0", id="feature"),
        pytest.param("0.2.1.dev0", "0.2.0", "refactor!: change API", "0.3.0", id="pre-one-breaking"),
        pytest.param("1.2.1.dev0", "1.2.0", "fix: change API\n\nBREAKING CHANGE: old API removed", "2.0.0", id="breaking-footer"),
        pytest.param("0.4.0.dev0", "0.2.0", "fix: reconnect", "0.4.0", id="development-floor"),
        pytest.param("0.3.0.dev0", None, "feat: initial plugin", "0.3.0", id="initial"),
        pytest.param("0.3.1.dev0", "0.3.0", "deps: update websocket", "0.3.1", id="dependencies"),
    ])
    def test_conventional_commit_version_selection(self, current, latest, message, expected):
        assert release_version(current, latest, [parse_change(message)]) == expected

    def test_multiple_features_share_one_minor_release(self):
        features = [parse_change("feat: one"), parse_change("feat: two")]
        assert release_version("0.3.1.dev0", "0.3.0", features) == "0.4.0"

    def test_generated_changes_and_final_versions_do_not_open_release_prs(self):
        changes = [parse_change("chore(snapshot): start 0.3.1.dev0"), parse_change("chore(release): prepare 0.3.0")]
        assert release_version('0.3.1.dev0', '0.3.0', changes) is None
        assert release_version('0.3.0', '0.2.0', [parse_change('feat: zoom')]) is None
        assert next_snapshot('0.3.0') == '0.3.1.dev0'

    @pytest.mark.parametrize('version', [
        pytest.param('0.3.0-SNAPSHOT', id='snapshot-suffix'),
        pytest.param('0.3', id='missing-patch'),
        pytest.param('01.0.0', id='leading-zero'),
        pytest.param('0.3.0.dev1', id='nonzero-dev'),
    ])
    def test_invalid_versions(self, version):
        with pytest.raises(ValueError):
            parse_version(version)

    @pytest.mark.parametrize('title', [
        pytest.param('Update things', id='nonconventional'),
        pytest.param('feat:', id='empty-feature'),
        pytest.param('fix: ', id='empty-fix'),
    ])
    def test_invalid_titles(self, title):
        with pytest.raises(ValueError):
            parse_change(title)


class TestFixtureIsolation:
    def test_release_tools_ignore_conflicting_user_configuration(self, tmp_path):
        directory = str(tmp_path)
        uv_config = Path(directory) / "uv.toml"
        uv_config.write_text('exclude-newer = "1970-01-01T00:00:00Z"\n')
        git_config = Path(directory) / "gitconfig"
        git_config.write_text('[commit]\n gpgsign = true\n[gpg]\n program = missing-test-signer\n')
        with patch.dict(os.environ, UV_CONFIG_FILE=str(uv_config), UV_PYTHON="missing-python",
                        UV_OFFLINE="true", GIT_CONFIG_GLOBAL=str(git_config),
                        GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="commit.gpgsign", GIT_CONFIG_VALUE_0="true"):
            with isolated_release_tree(Path(directory) / "fixture") as fixture:
                release.set_version(fixture.root, "0.3.0")
                fixture.assert_lock_version()
                subprocess.run(["git", "init", "-q"], cwd=fixture.root, check=True)
                release.git("add", "pyproject.toml", "uv.lock")
                release.git("-c", "user.name=Test", "-c", "user.email=test@example.com",
                            "commit", "-qm", "isolated fixture")
                assert os.environ["UV_PYTHON"] == sys.executable
                assert "UV_OFFLINE" not in os.environ



class TestPreparation:

    def test_refresh_preserves_public_notes_and_rebuilds_generated_notes(self, release_tree):
        public = "- See print progress on the camera.\n\nEstimates can change."
        previous = release.format_notes(public, "old generated text", public_first=False)
        change = parse_change("feat(camera): add zoom")
        with patch.object(release, "history", return_value=("0.2.0", [("a" * 40, "feat(camera): add zoom", change)])):
            release.prepare_pr("release", previous_body=previous)
        body = (release_tree.root / "dist/automation-pr-body.md").read_text()
        assert release.public_section(body) == public
        assert 'feat(camera): add zoom' in body
        assert 'old generated text' not in body
        assert body.index('## Generated notes') < body.index('## Public notes')

    def test_open_release_body_does_not_read_fork_notes(self, release_tree):
        fork = {"head": {"repo": {"full_name": "someone/fork"}}, "body": "fork text"}
        own = {"head": {"repo": {"full_name": "example/plugin"}}, "body": "reviewed text"}
        with patch.object(release, "api", return_value=[fork, own]):
            assert release.open_release_body() == 'reviewed text'

    def test_malformed_public_notes_stop_refresh_before_file_changes(self, release_tree):
        before = (release_tree.root / "CHANGELOG.md").read_text()
        change = parse_change("feat(camera): add zoom")
        with patch.object(release, "history", return_value=("0.2.0", [("a" * 40, "feat(camera): add zoom", change)])):
            with pytest.raises(ValueError, match='marker'):
                release.prepare_pr("release", previous_body="<!-- orca:public-notes:start -->unfinished")
        assert (release_tree.root / 'CHANGELOG.md').read_text() == before
        assert read_version(release_tree.root) == '0.3.0.dev0'

    def test_release_and_next_snapshot_update_project_lock_and_changelog(self, release_tree):
        change = parse_change("feat(camera): add zoom")
        with patch.object(release, "history", return_value=("0.2.0", [("a" * 40, "feat(camera): add zoom", change)])):
            plan = release.prepare_pr("release")
        assert plan['version'] == '0.3.0'
        assert plan['branch'] == 'automation/release'
        assert read_version(release_tree.root) == '0.3.0'
        release_tree.assert_lock_version()
        changelog = (release_tree.root / "CHANGELOG.md").read_text()
        assert '## 0.3.0' in changelog
        assert 'feat(camera): add zoom' in changelog
        artifact = builder.build(release_tree.root / "dist", release_tag="v0.3.0")
        assert '# version = "0.3.0"' in artifact.read_text()
        plan = release.prepare_pr("snapshot", "0.3.0")
        assert plan['version'] == '0.3.1.dev0'
        release_tree.assert_lock_version()
        assert (release_tree.root / 'CHANGELOG.md').read_text() == changelog
        assert release.prepare_pr('snapshot', '0.3.0') is None

    def test_empty_release_plan_does_not_change_files(self, release_tree):
        before = (release_tree.root / "pyproject.toml").read_bytes()
        with patch.object(release, "history", return_value=("0.2.0", [])):
            assert release.prepare_pr('release') is None
        assert (release_tree.root / 'pyproject.toml').read_bytes() == before

    def test_final_version_is_allowed_only_on_the_generated_release_pr(self, release_tree):
        release_tree.write_version("0.3.0")
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
        with pytest.raises(ValueError, match='Write public release notes'):
            release.check_pr(event)
        event["pull_request"]["body"] = body
        event["pull_request"]["head"]["repo"]["full_name"] = "someone/fork"
        with pytest.raises(ValueError, match='Only the generated release PR'):
            release.check_pr(event)
        event["pull_request"]["head"]["repo"]["full_name"] = "example/plugin"
        event["pull_request"]["title"] = "chore(release): prepare 0.4.0"
        with pytest.raises(ValueError, match='title must match'):
            release.check_pr(event)

    def test_history_uses_reachable_stable_tags_and_skips_automation(self, release_tree):
        generated = "chore(snapshot): start 0.3.1.dev0"
        message = "feat(camera): zoom\n\nBREAKING CHANGE: altered controls\n"
        records = "a" * 40 + "\x00" + generated + "\x00\n" + "b" * 40 + "\x00" + message + "\x00"
        with patch.object(release, "git", side_effect=["v0.2.0\nv0.3.0\nv0.4.0.dev0\nother", records]) as git:
            latest, changes = release.history()
        assert latest == '0.3.0'
        assert len(changes) == 1
        assert changes[0][2].breaking
        assert git.call_args.args[-1] == 'v0.3.0..HEAD'


class TestSnapshotOnly:


    def test_exact_snapshot_version_bump_uses_light_checks(self, snapshot):
        assert release.snapshot_only(snapshot.pr)

    def test_code_classification_does_not_validate_editable_pr_policy(self, snapshot):
        snapshot.pr["title"] = "Unfinished title"
        event = snapshot.root / "event.json"
        event.write_text(json.dumps({"pull_request": snapshot.pr}))
        with patch.dict(os.environ, GITHUB_EVENT_PATH=str(event)), \
                patch("sys.argv", ["release.py", "classify-pr"]), \
                patch.object(release, "check_pr", side_effect=AssertionError("Policy belongs to its own check")), \
                patch.object(release, "write_outputs") as outputs:
            release.main()
        outputs.assert_called_once_with(snapshot_only="false")

    def test_label_alone_or_fork_cannot_skip_full_checks(self, snapshot):
        snapshot.pr["head"]["ref"] = "feature/example"
        assert not release.snapshot_only(snapshot.pr)
        snapshot.pr["head"]["ref"] = "automation/next-snapshot"
        snapshot.pr["head"]["repo"]["full_name"] = "other/fork"
        assert not release.snapshot_only(snapshot.pr)

    def test_other_edits_in_version_files_require_full_checks(self, snapshot):
        with (snapshot.root / "uv.lock").open("a") as output:
            output.write("\n# unrelated lock change\n")
        snapshot.commit()
        assert not release.snapshot_only(snapshot.pr)

    def test_other_changed_files_require_full_checks(self, snapshot):
        (snapshot.root / "extra.py").write_text("pass\n")
        release.git("add", "extra.py")
        snapshot.commit()
        assert not release.snapshot_only(snapshot.pr)

    def test_missing_label_or_unexpected_version_requires_full_checks(self, snapshot):
        snapshot.pr["labels"] = []
        assert not release.snapshot_only(snapshot.pr)
        snapshot.pr["labels"] = [{"name": "snapshot"}]
        snapshot.write_version("0.4.0.dev0")
        snapshot.pr["title"] = "chore(snapshot): start 0.4.0.dev0"
        snapshot.commit()
        assert not release.snapshot_only(snapshot.pr)


class TestSnapshotMerge:

    def test_only_the_checked_snapshot_commit_is_squash_merged(self, snapshot_merge):
        release.merge_snapshot(3, snapshot_merge.sha, "0.3.1.dev0")
        snapshot_merge.github.assert_any_call("run", "list", "--repo", "example/plugin", "--workflow", "check.yml",
                                    "--event", "pull_request", "--commit", snapshot_merge.sha, "--limit", "20",
                                    "--json", "status,conclusion", token="checks-token")
        snapshot_merge.github.assert_any_call("run", "list", "--repo", "example/plugin", "--workflow", "pr-policy.yml",
                                    "--event", "pull_request", "--commit", snapshot_merge.sha, "--limit", "20",
                                    "--json", "status,conclusion", token="checks-token")
        snapshot_merge.github.assert_any_call("pr", "merge", "3", "--repo", "example/plugin", "--squash",
                                    "--match-head-commit", snapshot_merge.sha)

    def test_cancelled_run_does_not_hide_successful_run_for_same_commit(self, snapshot_merge):
        runs = json.dumps([
            {"status": "completed", "conclusion": "cancelled"},
            {"status": "completed", "conclusion": "success"},
        ])
        snapshot_merge.github.side_effect = lambda *args, **kwargs: runs if args[:2] == ("run", "list") else ""
        release.merge_snapshot(3, snapshot_merge.sha, "0.3.1.dev0")
        assert snapshot_merge.github.call_args.args[:2] == ('pr', 'merge')

    def test_waits_for_registered_checks_and_replacements_of_cancelled_runs(self, snapshot_merge):
        pending = iter([json.dumps(runs) for runs in [[],
            [{"status": "in_progress", "conclusion": None}],
            [{"status": "completed", "conclusion": "cancelled"}],
            [{"status": "completed", "conclusion": "success"}]]])
        snapshot_merge.github.side_effect = lambda *args, **kwargs: (
            next(pending) if "check.yml" in args else snapshot_merge.github.return_value
        ) if args[:2] == ("run", "list") else ""
        release.merge_snapshot(3, snapshot_merge.sha, "0.3.1.dev0")
        assert snapshot_merge.github.call_args.args[:2] == ('pr', 'merge')

    @pytest.mark.parametrize('policy', [
        pytest.param([], id='empty'),
        pytest.param([{'status': 'completed', 'conclusion': 'failure'}], id='status-completed-conclusion-failure'),
        pytest.param([{'status': 'completed', 'conclusion': 'skipped'}], id='status-completed-conclusion-skipped'),
    ])
    def test_code_success_does_not_bypass_missing_or_failed_policy(self, snapshot_merge, policy):
        snapshot_merge.github.reset_mock()
        snapshot_merge.github.side_effect = lambda *args, **kwargs: (
            json.dumps(policy) if "pr-policy.yml" in args else snapshot_merge.github.return_value
        )
        with pytest.raises(ValueError):
            release.merge_snapshot(3, snapshot_merge.sha, "0.3.1.dev0")
        assert not any((call.args[:2] == ('pr', 'merge') for call in snapshot_merge.github.call_args_list))

    @pytest.mark.parametrize('runs', [
        pytest.param([], id='empty'),
        pytest.param([{'status': 'completed', 'conclusion': 'failure'}], id='status-completed-conclusion-failure'),
    ])
    def test_failed_or_missing_checks_never_merge(self, snapshot_merge, runs):
        snapshot_merge.github.reset_mock()
        snapshot_merge.github.return_value = json.dumps(runs)
        with pytest.raises(ValueError):
            release.merge_snapshot(3, snapshot_merge.sha, "0.3.1.dev0")
        assert not any((call.args[:2] == ('pr', 'merge') for call in snapshot_merge.github.call_args_list))

    @pytest.mark.parametrize("mutate", [
        pytest.param(lambda state: state.pr["head"].update(ref="automation/release"), id="release-branch"),
        pytest.param(lambda state: state.pr["head"].update(sha="b" * 40), id="changed-head"),
        pytest.param(lambda state: state.pr["head"]["repo"].update(full_name="someone/fork"), id="fork"),
        pytest.param(lambda state: state.files.append({"filename": "src/plugin.py"}), id="source-file"),
    ])
    def test_release_prs_changed_heads_and_unexpected_files_never_merge(self, snapshot_merge, mutate):
        mutate(snapshot_merge)
        with pytest.raises(ValueError):
            release.merge_snapshot(3, snapshot_merge.sha, "0.3.1.dev0")
        snapshot_merge.github.assert_not_called()


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


class TestPublication:

    def test_publication_uses_prepared_bytes_without_running_the_builder(self, publication):
        with patch.object(builder, "build", side_effect=AssertionError("Publish must not rebuild")):
            release.publish(publication.root / "dist")
        assert publication.github.assets['orca-snapmaker-u1-camera-plugin_any.py'] == (publication.root / 'dist/orca-snapmaker-u1-camera-plugin_any.py').read_bytes()

    @pytest.mark.parametrize("key,value", [
        pytest.param("commit", "b" * 40, id="commit"),
        pytest.param("version", "0.4.0", id="version"),
        pytest.param("sha256", {}, id="digest"),
    ])
    def test_wrong_commit_version_or_digest_is_rejected_before_github_access(self, publication, key, value):
        path = publication.root / "dist/release-manifest.json"
        original = json.loads(path.read_text())
        path.write_text(json.dumps(dict(original, **{key: value})))
        with pytest.raises(ValueError, match="release commit, version, and bytes"):
            release.publish(publication.root / "dist")
        assert publication.github.calls == []

    def test_modified_artifact_and_rewritten_checksum_still_fail_manifest(self, publication):
        artifact = publication.root / "dist/orca-snapmaker-u1-camera-plugin_any.py"
        artifact.write_bytes(b"raise RuntimeError('must never execute')\n")
        checksum = release.hashlib.sha256(artifact.read_bytes()).hexdigest()
        (publication.root / "dist/SHA256SUMS").write_text(checksum + "  " + artifact.name + "\n")
        with pytest.raises(ValueError, match='release commit, version, and bytes'):
            release.publish(publication.root / "dist")
        assert publication.github.calls == []

    def test_corrupt_checksum_is_rejected_before_github_access(self, publication):
        (publication.root / "dist/SHA256SUMS").write_text("incorrect checksum\n")
        with pytest.raises(ValueError, match='SHA256SUMS'):
            release.publish(publication.root / "dist")
        assert publication.github.calls == []

    def test_workflow_checkout_must_match_expected_commit(self, publication):
        with patch.dict(os.environ, GITHUB_SHA="b" * 40):
            with pytest.raises(ValueError, match='workflow commit'):
                release.publish(publication.root / "dist")
        assert publication.github.calls == []

    def test_missing_manifest_does_not_fall_back_to_building(self, publication):
        (publication.root / "dist/release-manifest.json").unlink()
        with pytest.raises(FileNotFoundError):
            release.publish(publication.root / "dist")
        assert publication.github.calls == []

    def test_draft_is_verified_before_publication_and_reruns_do_not_write(self, publication):
        assert release.publish(publication.root / 'dist') == 'v0.3.0'
        assert not publication.github.release['draft']
        assert publication.github.tag_sha == publication.github.sha
        operations = [call[1] for call in publication.github.calls if call[0] == "release"]
        assert operations.index('download') < operations.index('edit')
        publication.github.calls.clear()
        release.publish(publication.root / "dist")
        assert not any((call[:2] in [('release', 'upload'), ('release', 'edit'), ('release', 'create')] for call in publication.github.calls))

    def test_release_captures_public_notes_first_and_reruns_ignore_pr_edits(self, publication):
        notes = "Camera controls are easier to use."
        publication.github.pr["body"] = release.format_notes(notes, "internal history")
        release.publish(publication.root / "dist")
        body = publication.github.release["body"]
        assert release.public_notes(body) == notes
        assert body.index('## Public notes') < body.index('## Generated notes')
        assert '- Camera controls.' in body
        assert 'Source commit:' in body
        publication.github.pr["body"] = release.format_notes("Edited after publication", "changed")
        release.publish(publication.root / "dist")
        assert publication.github.release['body'] == body

    def test_unreviewed_public_notes_do_not_create_a_release(self, publication):
        publication.github.pr["body"] = release.format_notes(release.PUBLIC_PLACEHOLDER, "generated")
        with pytest.raises(ValueError, match='Write public release notes'):
            release.publish(publication.root / "dist")
        assert publication.github.release is None

    @pytest.mark.parametrize("field,value", [
        pytest.param("merge_commit_sha", "b" * 40, id="commit"),
        pytest.param("title", "chore(release): prepare 0.4.0", id="version"),
        pytest.param("merged_at", None, id="unmerged"),
    ])
    def test_only_exact_same_repository_release_merge_is_used(self, publication, field, value):
        publication.github.pr[field] = value
        with pytest.raises(ValueError, match="exact release commit and version"):
            release.publish(publication.root / "dist")
        assert publication.github.release is None

    def test_foreign_repository_release_merge_is_rejected(self, publication):
        publication.github.pr["head"]["repo"]["full_name"] = "someone/fork"
        with pytest.raises(ValueError, match="exact release commit and version"):
            release.publish(publication.root / "dist")
        assert publication.github.release is None

    def test_published_mismatched_assets_are_never_overwritten(self, publication):
        release.publish(publication.root / "dist")
        publication.github.assets["orca-snapmaker-u1-camera-plugin_any.py"] = b"changed"
        publication.github.calls.clear()
        with pytest.raises(ValueError, match='immutable'):
            release.publish(publication.root / "dist")
        assert not any((call[:2] == ('release', 'upload') for call in publication.github.calls))

    def test_published_missing_assets_are_never_added(self, publication):
        release.publish(publication.root / "dist")
        del publication.github.assets["SHA256SUMS"]
        publication.github.calls.clear()
        with pytest.raises(ValueError, match='will not be modified'):
            release.publish(publication.root / "dist")
        assert not any((call[:2] == ('release', 'upload') for call in publication.github.calls))

    def test_existing_tag_must_resolve_to_the_release_commit(self, publication):
        publication.github.tag_sha = "b" * 40
        with pytest.raises(ValueError, match='another commit'):
            release.publish(publication.root / "dist")
        assert not any((call[:2] == ('release', 'create') for call in publication.github.calls))

    def test_annotated_tag_is_checked_against_its_underlying_commit(self, publication):
        publication.github.tag_sha = publication.github.sha
        def github(*args):
            if args[0] == "api" and "/git/ref/tags/" in args[1]:
                return json.dumps({"object": {"type": "tag", "sha": "c" * 40}})
            return publication.github(*args)
        with patch.object(release, "gh", side_effect=github):
            assert release.publish(publication.root / 'dist') == 'v0.3.0'
        assert not publication.github.release['draft']

    def test_tag_lookup_errors_do_not_create_a_release(self, publication):
        def github(*args):
            if args[0] == "api" and "/git/ref/tags/" in args[1]:
                raise RuntimeError("Validation failed (HTTP 422)")
            return publication.github(*args)
        with patch.object(release, "gh", side_effect=github):
            with pytest.raises(RuntimeError, match='HTTP 422'):
                release.publish(publication.root / "dist")
        assert not any((call[:2] in [('release', 'create'), ('release', 'upload'), ('release', 'edit')] for call in publication.github.calls))

    def test_pending_tag_lookup_finds_draft_without_a_matching_rest_tag_name(self, publication):
        publication.github.release = {"tag_name": "untagged-draft", "draft": True,
                               "target_commitish": publication.github.sha}
        draft = release.release_by_tag("v0.3.0")
        assert draft['draft']
        assert draft['tag_name'] == 'untagged-draft'
        assert ('api', 'repos/example/plugin/releases/1') in publication.github.calls

    def test_dirty_checkout_cannot_publish(self, publication):
        with patch.object(release, "git", return_value=" M pyproject.toml"):
            with pytest.raises(ValueError, match='clean checkout'):
                release.publish(publication.root / "dist")
        assert publication.github.calls == []

    def test_development_version_cannot_publish(self, publication):
        path = publication.root / "pyproject.toml"
        path.write_text(path.read_text().replace('version = "0.3.0"', 'version = "0.3.1.dev0"'))
        with pytest.raises(ValueError, match='Development versions'):
            release.publish(publication.root / "dist")
        assert publication.github.calls == []

    def test_partial_draft_can_resume_without_replacing_assets(self, publication):
        artifact = builder.build(publication.root / "dist")
        publication.github.release = {"draft": True, "target_commitish": publication.github.sha,
                               "body": release.format_notes("Camera controls.", "generated")}
        publication.github.assets[artifact.name] = artifact.read_bytes()
        release.publish(publication.root / "dist")
        upload = next(call for call in publication.github.calls if call[:2] == ("release", "upload"))
        assert str(artifact) not in upload
        assert not publication.github.release['draft']


@pytest.fixture
def snapshot(release_tree):
    tree = release_tree
    tree.write_version("0.3.0")
    subprocess.run(["git", "init", "-q"], cwd=tree.root, check=True)
    release.git("add", "pyproject.toml", "uv.lock")
    release.git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "base")
    tree.pr = {"title": "chore(snapshot): start 0.3.1.dev0", "labels": [{"name": "snapshot"}],
               "head": {"ref": "automation/next-snapshot", "repo": {"full_name": "example/plugin"}},
               "base": {"ref": "main", "sha": release.git("rev-parse", "HEAD"),
                        "repo": {"full_name": "example/plugin"}}}
    tree.write_version("0.3.1.dev0")
    tree.commit()
    return tree


@pytest.fixture
def snapshot_merge(monkeypatch):
    state = SimpleNamespace(sha="a" * 40)
    state.pr = {"state": "open", "draft": False, "title": "chore(snapshot): start 0.3.1.dev0",
                "base": {"ref": "main"}, "head": {"ref": "automation/next-snapshot", "sha": state.sha,
                "repo": {"full_name": "example/plugin"}}, "labels": [{"name": "snapshot"}]}
    state.files = [{"filename": "pyproject.toml"}, {"filename": "uv.lock"}]
    monkeypatch.setattr(release, "api", lambda path: state.files if "/files?" in path else state.pr)
    monkeypatch.setenv("GH_REPO", "example/plugin")
    monkeypatch.setenv("GH_CHECKS_TOKEN", "checks-token")
    state.github = Mock(return_value=json.dumps([{"status": "completed", "conclusion": "success"}]))
    monkeypatch.setattr(release, "gh", state.github)
    monkeypatch.setattr(release.time, "sleep", Mock())
    return state


@pytest.fixture
def publication(release_tree, monkeypatch):
    tree = release_tree
    tree.write_version("0.3.0")
    (tree.root / "CHANGELOG.md").write_text("# Changelog\n\n## 0.3.0\n\n- Camera controls.\n")
    tree.github = FakeGitHub()
    monkeypatch.setattr(release, "gh", tree.github)
    monkeypatch.setattr(release, "api", release_api)
    monkeypatch.setattr(release, "git", lambda *args: "" if args[0] == "status" else tree.github.sha)
    builder.build(tree.root / "dist", release_tag="v0.3.0")
    release.prepare_artifact(tree.root / "dist")
    return tree
