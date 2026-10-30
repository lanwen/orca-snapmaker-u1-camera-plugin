"""Conventional Commit version selection and development-version updates."""

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import tomllib


VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(\.dev0)?")
TITLE = re.compile(
    r"(?P<kind>feat|fix|perf|refactor|docs|test|build|ci|chore|style|revert|deps)"
    r"(?:\((?P<scope>[^()\r\n]+)\))?(?P<breaking>!)?: (?P<subject>\S[^\r\n]*)"
)


def parse_version(value):
    match = VERSION.fullmatch(value)
    if not match:
        raise ValueError("Use a version such as 0.3.0 or 0.3.1.dev0")
    return tuple(map(int, match.group(1, 2, 3))), bool(match.group(4))


def format_version(parts, development=False):
    return ".".join(map(str, parts)) + (".dev0" if development else "")


def read_version(root):
    return tomllib.loads((Path(root) / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def set_version(root, version):
    parse_version(version)
    path = Path(root) / "pyproject.toml"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    in_project = False
    for index, line in enumerate(lines):
        if line.startswith("["):
            in_project = line.strip() == "[project]"
        if in_project and re.match(r"version\s*=", line):
            lines[index] = f'version = "{version}"\n'
            path.write_text("".join(lines), encoding="utf-8")
            subprocess.run(["uv", "lock", "--offline"], cwd=root, check=True)
            return
    raise ValueError("Missing [project].version")


@dataclass(frozen=True)
class Change:
    kind: str
    scope: str
    subject: str
    breaking: bool

    @property
    def generated(self):
        return self.kind == "chore" and self.scope in ("release", "snapshot")


def parse_change(message):
    title, _, body = message.partition("\n")
    match = TITLE.fullmatch(title.strip())
    if not match:
        raise ValueError("Use a Conventional Commit title, for example feat(camera): add zoom")
    return Change(match["kind"], match["scope"] or "", match["subject"],
                  bool(match["breaking"]) or bool(re.search(r"(?m)^BREAKING[ -]CHANGE: \S", body)))


def release_version(current, latest, changes):
    floor, development = parse_version(current)
    if not development:
        return None
    changes = [change for change in changes if not change.generated]
    if not changes:
        return None
    base = parse_version(latest)[0] if latest else (0, 0, 0)
    major, minor, patch = base
    if any(change.breaking for change in changes) and major:
        target = major + 1, 0, 0
    elif any(change.breaking or change.kind == "feat" for change in changes):
        target = major, minor + 1, 0
    else:
        target = major, minor, patch + 1
    return format_version(max(floor, target))


def next_snapshot(released):
    (major, minor, patch), development = parse_version(released)
    if development:
        raise ValueError("A snapshot must follow a final release")
    return format_version((major, minor, patch + 1), development=True)
