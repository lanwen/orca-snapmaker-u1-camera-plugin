"""Isolated release worktrees and their real Git/uv inputs."""

from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import shutil
import sys
import tomllib
from unittest.mock import Mock, patch

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build as builder
import release
from versioning import read_version


class ReleaseTree:
    def __init__(self, root):
        self.root = root

    def write_version(self, version):
        current = read_version(self.root)
        for name in ("pyproject.toml", "uv.lock"):
            path = self.root / name
            path.write_text(path.read_text().replace(f'version = "{current}"', f'version = "{version}"', 1))

    def assert_lock_version(self):
        lock = tomllib.loads((self.root / "uv.lock").read_text())
        project = next(package for package in lock["package"] if package["source"] == {"virtual": "."})
        assert project["version"] == read_version(self.root)

    def commit(self):
        release.git("add", "pyproject.toml", "uv.lock")
        release.git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "snapshot")


@contextmanager
def isolated_release_tree(root):
    root.mkdir(parents=True, exist_ok=True)
    tree = ReleaseTree(root)
    for name in ("pyproject.toml", "uv.lock", "LICENSE"):
        shutil.copyfile(ROOT / name, root / name)
    tree.write_version("0.3.0.dev0")
    shutil.copytree(ROOT / "src", root / "src")
    shutil.copytree(ROOT / "dist/ui", root / "dist/ui")
    (root / "docs/branding").mkdir(parents=True)
    shutil.copyfile(ROOT / "docs/branding/u1-logo.svg", root / "docs/branding/u1-logo.svg")
    (root / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
    config = root / "uv.toml"
    config.write_text("")
    # Real tools run only in this worktree, without developer config, caches or signing.
    settings = {key: value for key, value in os.environ.items()
                if not key.startswith(("UV_", "GIT_")) and key != "GITHUB_SHA"}
    settings.update(GH_REPO="example/plugin", UV_CACHE_DIR=str(root / "uv-cache"),
                    UV_CONFIG_FILE=str(config), UV_PYTHON=sys.executable, UV_PYTHON_DOWNLOADS="never",
                    GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    with ExitStack() as stack:
        for module in (release, builder):
            stack.enter_context(patch.object(module, "ROOT", root))
        stack.enter_context(patch.dict(os.environ, settings, clear=True))
        stack.enter_context(patch.object(release, "api", Mock(return_value=[])))
        yield tree
