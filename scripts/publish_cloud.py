"""Publish the verified GitHub release artifact to the connected Orca Cloud plugin."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

from build import ROOT
from release import gh, git, release_by_tag, verify_commit
from versioning import parse_version, read_version


def publish():
    version = read_version(ROOT)
    if parse_version(version)[1]:
        raise ValueError("Development versions cannot be published to Orca Cloud")
    tag = "v" + version
    release = release_by_tag(tag)
    if release["draft"] or release["prerelease"]:
        raise ValueError("Publish the stable GitHub release before uploading to Orca Cloud")
    verify_commit(release, tag, git("rev-parse", "HEAD"))
    filename = "orca-snapmaker-u1-camera-plugin_any.py"
    with tempfile.TemporaryDirectory() as directory:
        gh("release", "download", tag, "--dir", directory,
           "--pattern", filename, "--pattern", "SHA256SUMS")
        artifact = (Path(directory) / filename).read_bytes()
        checksum = (Path(directory) / "SHA256SUMS").read_text(encoding="utf-8").strip()
        if checksum != f"{hashlib.sha256(artifact).hexdigest()}  {filename}":
            raise ValueError("Released plugin does not match SHA256SUMS")

    oidc_url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"] + "&audience=orcacloud"
    request = Request(oidc_url, headers={
        "Authorization": "Bearer " + os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"],
    })
    with urlopen(request, timeout=30) as response:
        token = json.load(response).get("value")
    if not isinstance(token, str) or not token or "\n" in token or "\r" in token:
        raise ValueError("GitHub did not return a valid Orca Cloud OIDC token")
    print("::add-mask::" + token, flush=True)

    metadata = json.dumps({"version": version, "changelog": (release.get("body") or "")[:4000]})
    boundary = uuid.uuid4().hex
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\n\r\n{metadata}\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{filename}"\r\n'
        'Content-Type: application/octet-stream\r\n\r\n'
    ).encode("utf-8") + artifact + f"\r\n--{boundary}--\r\n".encode("ascii")
    request = Request("https://api.orcaslicer.com/api/v1/plugin-publish/releases", data=body, headers={
        "Authorization": "Bearer " + token,
        "Content-Type": "multipart/form-data; boundary=" + boundary,
        # Cloudflare rejects urllib's default user-agent before Orca can authenticate.
        "User-Agent": f"orca-snapmaker-u1-camera-plugin-publisher/{version} "
                      "(+https://github.com/lanwen/orca-snapmaker-u1-camera-plugin)",
    })
    try:
        with urlopen(request, timeout=120) as response:
            if response.status != 201:
                raise ValueError(f"Orca Cloud returned HTTP {response.status}; expected 201")
    except HTTPError as error:
        detail = error.read(16_000).decode("utf-8", errors="replace")
        raise RuntimeError(f"Orca Cloud rejected publication (HTTP {error.code}): {detail}") from None
    return version


if __name__ == "__main__":
    try:
        print("Published to Orca Cloud: " + publish())
    except (KeyError, OSError, ValueError, RuntimeError) as error:
        raise SystemExit(str(error))
