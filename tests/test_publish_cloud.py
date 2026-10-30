"""Verify the Cloud upload contract without publishing a real version."""

from email import policy
from email.parser import BytesParser
import io
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import publish_cloud as cloud


class CloudPublicationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.filename = "orca-snapmaker-u1-camera-plugin_any.py"
        self.artifact = b"# downloaded release artifact\n"
        self.checksum = f"{hashlib.sha256(self.artifact).hexdigest()}  {self.filename}\n"
        self.release = {"draft": False, "prerelease": False, "body": "Release notes"}
        for name, value in {"ROOT": self.root, "read_version": Mock(return_value="0.3.2"),
                            "release_by_tag": Mock(return_value=self.release), "git": Mock(return_value="a" * 40),
                            "verify_commit": Mock(), "gh": Mock(side_effect=self.download), "urlopen": Mock()}.items():
            context = patch.object(cloud, name, value)
            context.start()
            self.addCleanup(context.stop)
        context = patch.dict("os.environ", {"ACTIONS_ID_TOKEN_REQUEST_URL": "https://oidc.example/?request=1",
                                           "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-token"})
        context.start()
        self.addCleanup(context.stop)
        context = patch("builtins.print")
        self.print = context.start()
        self.addCleanup(context.stop)
        token = io.BytesIO(b'{"value":"signed-oidc-token"}')
        success = io.BytesIO(b'{}')
        success.status = 201
        cloud.urlopen.side_effect = [token, success]

    def download(self, *args):
        directory = Path(args[args.index("--dir") + 1])
        (directory / self.filename).write_bytes(self.artifact)
        (directory / "SHA256SUMS").write_text(self.checksum)

    def test_uploads_verified_release_bytes_version_and_notes_with_oidc(self):
        self.assertEqual(cloud.publish(), "0.3.2")
        cloud.release_by_tag.assert_called_once_with("v0.3.2")
        cloud.verify_commit.assert_called_once_with(self.release, "v0.3.2", "a" * 40)
        args = cloud.gh.call_args.args
        self.assertEqual(args[:3], ("release", "download", "v0.3.2"))
        self.assertEqual(args[-4:], ("--pattern", self.filename, "--pattern", "SHA256SUMS"))
        oidc = cloud.urlopen.call_args_list[0].args[0]
        self.assertEqual(oidc.full_url, "https://oidc.example/?request=1&audience=orcacloud")
        self.assertEqual(oidc.get_header("Authorization"), "Bearer request-token")
        upload = cloud.urlopen.call_args_list[1].args[0]
        self.assertEqual(upload.full_url, "https://api.orcaslicer.com/api/v1/plugin-publish/releases")
        self.assertEqual(upload.get_header("Authorization"), "Bearer signed-oidc-token")
        message = BytesParser(policy=policy.default).parsebytes(
            ("Content-Type: " + upload.get_header("Content-type") + "\r\n\r\n").encode() + upload.data)
        metadata, artifact = list(message.iter_parts())
        self.assertEqual(json.loads(metadata.get_payload(decode=True)),
                         {"version": "0.3.2", "changelog": "Release notes"})
        self.assertEqual(artifact.get_filename(), self.filename)
        self.assertEqual(artifact.get_payload(decode=True), self.artifact)
        self.print.assert_called_once_with("::add-mask::signed-oidc-token", flush=True)

    def test_development_draft_or_mismatched_release_never_requests_oidc(self):
        cloud.read_version.return_value = "0.3.3.dev0"
        with self.assertRaisesRegex(ValueError, "Development"):
            cloud.publish()
        cloud.read_version.return_value = "0.3.2"
        self.release["draft"] = True
        with self.assertRaisesRegex(ValueError, "stable GitHub release"):
            cloud.publish()
        self.release["draft"] = False
        cloud.verify_commit.side_effect = ValueError("tag points to another commit")
        with self.assertRaisesRegex(ValueError, "another commit"):
            cloud.publish()
        cloud.gh.assert_not_called()
        cloud.urlopen.assert_not_called()

    def test_bad_checksum_or_missing_asset_never_requests_oidc(self):
        self.checksum = f"{'0' * 64}  {self.filename}\n"
        with self.assertRaisesRegex(ValueError, "does not match SHA256SUMS"):
            cloud.publish()
        cloud.gh.side_effect = RuntimeError("release asset missing")
        with self.assertRaisesRegex(RuntimeError, "asset missing"):
            cloud.publish()
        cloud.urlopen.assert_not_called()

    def test_cloud_rejection_fails_instead_of_reporting_success(self):
        cloud.urlopen.side_effect = [io.BytesIO(b'{"value":"token"}'),
                                    HTTPError("https://api.orcaslicer.com", 401, "Unauthorized", {},
                                              io.BytesIO(b'{"error":"repository not connected"}'))]
        with self.assertRaisesRegex(RuntimeError, "HTTP 401.*repository not connected"):
            cloud.publish()

    def test_invalid_oidc_response_never_uploads(self):
        cloud.urlopen.side_effect = [io.BytesIO(b'{"value":null}')]
        with self.assertRaisesRegex(ValueError, "valid.*OIDC token"):
            cloud.publish()
        self.assertEqual(cloud.urlopen.call_count, 1)
