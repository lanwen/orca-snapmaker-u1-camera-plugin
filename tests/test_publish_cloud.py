"""Verify the Cloud upload contract without publishing a real version."""

from email import policy
from email.parser import BytesParser
import io
import hashlib
import json
from pathlib import Path
import sys
import pytest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import publish_cloud as cloud
from release_notes import format_notes


class TestCloudPublication:


    def test_uploads_verified_release_bytes_version_and_notes_with_oidc(self, cloud_release):
        assert cloud.publish() == '0.3.2'
        cloud.release_by_tag.assert_called_once_with("v0.3.2")
        cloud.verify_commit.assert_called_once_with(cloud_release.release, "v0.3.2", "a" * 40)
        args = cloud.gh.call_args.args
        assert args[:3] == ('release', 'download', 'v0.3.2')
        assert args[-4:] == ('--pattern', cloud_release.filename, '--pattern', 'SHA256SUMS')
        oidc = cloud.urlopen.call_args_list[0].args[0]
        assert oidc.full_url == 'https://oidc.example/?request=1&audience=orcacloud'
        assert oidc.get_header('Authorization') == 'Bearer request-token'
        upload = cloud.urlopen.call_args_list[1].args[0]
        assert upload.full_url == 'https://api.orcaslicer.com/api/v1/plugin-publish/releases'
        assert upload.get_header('Authorization') == 'Bearer signed-oidc-token'
        message = BytesParser(policy=policy.default).parsebytes(
            ("Content-Type: " + upload.get_header("Content-type") + "\r\n\r\n").encode() + upload.data)
        metadata, artifact = list(message.iter_parts())
        assert json.loads(metadata.get_payload(decode=True)) == {'version': '0.3.2', 'changelog': cloud_release.notes}
        assert artifact.get_filename() == cloud_release.filename
        assert artifact.get_payload(decode=True) == cloud_release.artifact
        cloud_release.print.assert_called_once_with("::add-mask::signed-oidc-token", flush=True)

    def test_development_draft_or_mismatched_release_never_requests_oidc(self, cloud_release):
        cloud.read_version.return_value = "0.3.3.dev0"
        with pytest.raises(ValueError, match='Development'):
            cloud.publish()
        cloud.read_version.return_value = "0.3.2"
        cloud_release.release["draft"] = True
        with pytest.raises(ValueError, match='stable GitHub release'):
            cloud.publish()
        cloud_release.release["draft"] = False
        cloud.verify_commit.side_effect = ValueError("tag points to another commit")
        with pytest.raises(ValueError, match='another commit'):
            cloud.publish()
        cloud.gh.assert_not_called()
        cloud.urlopen.assert_not_called()

    def test_upload_identifies_the_application_to_the_cloud_edge(self, cloud_release):
        def respond(request, **kwargs):
            if request.full_url.startswith("https://oidc.example/"):
                return io.BytesIO(b'{"value":"token"}')
            agent = request.get_header("User-agent") or ""
            if not agent.startswith("orca-snapmaker-u1-camera-plugin-publisher/"):
                raise HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(b"error code: 1010"))
            response = io.BytesIO(b'{}')
            response.status = 201
            return response

        cloud.urlopen.side_effect = respond
        assert cloud.publish() == '0.3.2'

    @pytest.mark.parametrize('event,tag', [
        pytest.param('push', 'main', id='push'),
        pytest.param('workflow_dispatch', 'v0.3.2', id='manual'),
        pytest.param('release', 'v0.3.1', id='wrong-tag'),
    ])
    def test_push_identity_or_wrong_release_tag_never_requests_oidc(self, cloud_release, event, tag):
        with patch.dict("os.environ", {"GITHUB_EVENT_NAME": event, "GITHUB_REF_NAME": tag}):
            with pytest.raises(ValueError, match="release event.*matching version tag"):
                cloud.publish()
        cloud.urlopen.assert_not_called()
        cloud.release_by_tag.assert_not_called()

    def test_bad_checksum_or_missing_asset_never_requests_oidc(self, cloud_release):
        cloud_release.checksum = f"{'0' * 64}  {cloud_release.filename}\n"
        with pytest.raises(ValueError, match='does not match SHA256SUMS'):
            cloud.publish()
        cloud.gh.side_effect = RuntimeError("release asset missing")
        with pytest.raises(RuntimeError, match='asset missing'):
            cloud.publish()
        cloud.urlopen.assert_not_called()

    def test_cloud_rejection_fails_instead_of_reporting_success(self, cloud_release):
        cloud.urlopen.side_effect = [io.BytesIO(b'{"value":"token"}'),
                                    HTTPError("https://api.orcaslicer.com", 401, "Unauthorized", {},
                                              io.BytesIO(b'{"error":"repository not connected"}'))]
        with pytest.raises(RuntimeError, match='HTTP 401.*repository not connected'):
            cloud.publish()

    def test_invalid_oidc_response_never_uploads(self, cloud_release):
        cloud.urlopen.side_effect = [io.BytesIO(b'{"value":null}')]
        with pytest.raises(ValueError, match='valid.*OIDC token'):
            cloud.publish()
        assert cloud.urlopen.call_count == 1

    @pytest.mark.parametrize('body', [
        pytest.param('Generated notes without public-note markers', id='missing'),
        pytest.param(format_notes('', 'generated'), id='empty'),
        pytest.param(format_notes('x' * 4001, 'generated'), id='oversized'),
    ])
    def test_missing_empty_or_oversized_public_notes_never_request_credentials(self, cloud_release, body):
        cloud_release.release["body"] = body
        with pytest.raises(ValueError):
            cloud.publish()
        cloud.gh.assert_not_called()
        cloud.urlopen.assert_not_called()


class CloudAssets:
    def download(self, *args):
            directory = Path(args[args.index("--dir") + 1])
            (directory / self.filename).write_bytes(self.artifact)
            (directory / "SHA256SUMS").write_text(self.checksum)


@pytest.fixture
def cloud_release(tmp_path, monkeypatch):
    state = CloudAssets()
    state.root = tmp_path
    state.filename = "orca-snapmaker-u1-camera-plugin_any.py"
    state.artifact = b"# downloaded release artifact\n"
    state.checksum = f"{hashlib.sha256(state.artifact).hexdigest()}  {state.filename}\n"
    state.notes = "Release notes"
    state.release = {"draft": False, "prerelease": False,
                     "body": format_notes(state.notes, "- fix(ci): internal [commit](https://example.com)") +
                             "\nSource commit: `abc`.\n"}
    for name, value in {"ROOT": state.root, "read_version": Mock(return_value="0.3.2"),
                        "release_by_tag": Mock(return_value=state.release), "git": Mock(return_value="a" * 40),
                        "verify_commit": Mock(), "gh": Mock(side_effect=state.download), "urlopen": Mock()}.items():
        monkeypatch.setattr(cloud, name, value)
    for name, value in {"ACTIONS_ID_TOKEN_REQUEST_URL": "https://oidc.example/?request=1",
                        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-token",
                        "GITHUB_EVENT_NAME": "release", "GITHUB_REF_NAME": "v0.3.2"}.items():
        monkeypatch.setenv(name, value)
    state.print = Mock()
    monkeypatch.setattr("builtins.print", state.print)
    token = io.BytesIO(b'{"value":"signed-oidc-token"}')
    success = io.BytesIO(b'{}')
    success.status = 201
    cloud.urlopen.side_effect = [token, success]
    return state
