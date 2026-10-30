"""Prepare release/snapshot PR changes and publish immutable GitHub release assets."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from build import ROOT, build
from versioning import next_snapshot, parse_change, parse_version, read_version, release_version, set_version


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def repository():
    value = os.environ.get("GH_REPO") or git("remote", "get-url", "origin")
    value = re.sub(r"^(https://github.com/|git@github.com:)", "", value).removesuffix(".git")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", value):
        raise ValueError("Set GH_REPO to owner/repository")
    return value


def history():
    tags = []
    for tag in git("tag", "--merged", "HEAD", "--list", "v*").splitlines():
        try:
            parts, development = parse_version(tag[1:])
            if not development:
                tags.append((parts, tag))
        except ValueError:
            continue
    latest = max(tags)[1] if tags else None
    revision = latest + "..HEAD" if latest else "HEAD"
    records = git("log", "--no-merges", "--format=%H%x00%B%x00", revision).split("\x00")
    changes = []
    for index in range(0, len(records) - 1, 2):
        sha, message = records[index].strip(), records[index + 1].strip()
        try:
            change = parse_change(message)
            if not change.generated:
                changes.append((sha, message.splitlines()[0], change))
        except ValueError:
            continue
    return latest[1:] if latest else None, changes


def prepare_pr(mode, released=None):
    current = read_version(ROOT)
    _, development = parse_version(current)
    if mode == "snapshot":
        parse_version(released or "")
        if development or current != released:
            return None
        version = next_snapshot(released)
        body = f"Start development at `{version}` after release `v{released}`.\n"
        branch, title, label = "automation/next-snapshot", f"chore(snapshot): start {version}", "snapshot"
    else:
        if not development:
            return None
        latest, commits = history()
        version = release_version(current, latest, [change for _, _, change in commits])
        if version is None:
            return None
        notes = "\n".join(f"- {title} ([{sha[:7]}](https://github.com/{repository()}/commit/{sha}))"
                          for sha, title, _ in commits)
        changelog = ROOT / "CHANGELOG.md"
        existing = changelog.read_text(encoding="utf-8") if changelog.exists() else "# Changelog\n"
        header = "# Changelog\n"
        if not existing.startswith(header):
            raise ValueError("CHANGELOG.md must start with '# Changelog'")
        changelog.write_text(header + f"\n## {version}\n\n" + notes + "\n\n" + existing[len(header):].lstrip(), encoding="utf-8")
        body = f"Release `{version}`.\n\n{notes}\n\nMerging this PR publishes `v{version}` from its merge commit.\n"
        branch, title, label = "automation/release", f"chore(release): prepare {version}", "release"
    set_version(ROOT, version)
    body_file = ROOT / "dist/automation-pr-body.md"
    body_file.parent.mkdir(exist_ok=True)
    body_file.write_text(body, encoding="utf-8")
    return {"version": version, "branch": branch, "title": title, "label": label}


def check_pr(event):
    pr = event["pull_request"]
    parse_change(pr["title"] + "\n" + (pr.get("body") or ""))
    version = read_version(ROOT)
    _, development = parse_version(version)
    is_release = (pr["head"]["ref"] == "automation/release"
                  and pr["head"]["repo"]["full_name"] == pr["base"]["repo"]["full_name"]
                  and any(label["name"] == "release" for label in pr.get("labels", [])))
    if not development and not is_release:
        raise ValueError("Only the generated release PR may carry a final pyproject.toml version")
    if is_release and (development or pr["title"] != f"chore(release): prepare {version}"):
        raise ValueError("Release PR title must match the final pyproject.toml version")


def gh(*args):
    result = subprocess.run(["gh", *args], cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "GitHub request failed")
    return result.stdout


def api(path, optional=False):
    try:
        return json.loads(gh("api", f"repos/{repository()}/{path}"))
    except RuntimeError as error:
        if optional and "HTTP 404" in str(error):
            return None
        raise


def tag_commit(tag):
    # The commits endpoint returns 422 for an unknown tag; the ref endpoint returns 404.
    if api(f"git/ref/tags/{tag}", optional=True) is None:
        return None
    return api(f"commits/{tag}")  # Resolve annotated tags to their underlying commit too.


def release_by_tag(tag, optional=False):
    # Listing includes drafts, which the tag endpoint omits until publication.
    page = 1
    while True:
        releases = api(f"releases?per_page=100&page={page}")
        for release in releases:
            if release["tag_name"] == tag:
                return release
        if len(releases) < 100:
            if optional:
                return None
            raise ValueError(f"Release {tag} was not found")
        page += 1


def verify_commit(release, tag, sha):
    commit = tag_commit(tag)
    if commit is not None:
        if commit["sha"] != sha:
            raise ValueError(f"Existing tag {tag} does not point to release commit {sha}")
    elif not release["draft"] or release["target_commitish"] != sha:
        raise ValueError("Release does not identify the expected source commit")


def verify_assets(release, tag, assets):
    available = {asset["name"] for asset in release["assets"]}
    present = [asset for asset in assets if asset.name in available]
    if present:
        with tempfile.TemporaryDirectory() as directory:
            patterns = [argument for asset in present for argument in ("--pattern", asset.name)]
            gh("release", "download", tag, "--dir", directory, "--repo", repository(), *patterns)
            for asset in present:
                if asset.read_bytes() != (Path(directory) / asset.name).read_bytes():
                    raise ValueError(f"Release asset {asset.name} differs; existing assets are immutable")
    missing = [asset for asset in assets if asset.name not in available]
    if missing and not release["draft"]:
        raise ValueError("Published release is missing assets; it will not be modified")
    return missing


def publish():
    version = read_version(ROOT)
    _, development = parse_version(version)
    if development:
        raise ValueError("Development versions cannot be published as releases")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise ValueError("Publish from a clean checkout")
    sha = git("rev-parse", "HEAD")
    tag = "v" + version
    artifact = build(release_tag=tag)
    assets = [artifact, artifact.with_name("SHA256SUMS")]
    release = release_by_tag(tag, optional=True)
    if release is None:
        commit = tag_commit(tag)
        if commit is not None and commit["sha"] != sha:
            raise ValueError(f"Existing tag {tag} points to another commit")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        section = re.search(r"(?ms)^## " + re.escape(version) + r"\n(.*?)(?=^## |\Z)", changelog)
        if section is None:
            raise ValueError("Missing changelog section for " + version)
        notes = ROOT / "dist/release-notes.md"
        notes.write_text(section[1].strip() + f"\n\nSource commit: `{sha}`.\n", encoding="utf-8")
        gh("release", "create", tag, "--draft", "--target", sha, "--title", f"Snapmaker U1 {tag}",
           "--notes-file", str(notes), "--repo", repository())
        release = release_by_tag(tag)
    verify_commit(release, tag, sha)
    missing = verify_assets(release, tag, assets)
    if release["draft"]:
        if missing:
            gh("release", "upload", tag, *map(str, missing), "--repo", repository())
        release = release_by_tag(tag)
        if verify_assets(release, tag, assets):
            raise ValueError("Draft release is missing assets")
        verify_commit(release, tag, sha)
        gh("release", "edit", tag, "--draft=false", "--repo", repository())
        verify_commit(release_by_tag(tag), tag, sha)
    return tag


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["info", "prepare-pr", "check-pr", "publish"])
    args = parser.parse_args()
    if args.command == "info":
        version = read_version(ROOT)
        _, development = parse_version(version)
        print(f"version={version}\nfinal={str(not development).lower()}")
    elif args.command == "prepare-pr":
        mode = os.environ.get("RELEASE_MODE", "release")
        if mode not in ("release", "snapshot"):
            raise ValueError("RELEASE_MODE must be release or snapshot")
        result = prepare_pr(mode, os.environ.get("RELEASE_VERSION"))
        print("changed=" + str(result is not None).lower())
        for key, value in (result or {}).items():
            print(f"{key}={value}")
    elif args.command == "check-pr":
        check_pr(json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8")))
    else:
        print(publish())


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error))
