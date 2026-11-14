"""Shared release sandbox; plugin/transport fixtures stay beside their tests."""

import pytest

from release_helpers import isolated_release_tree


@pytest.fixture
def release_tree(tmp_path):
    with isolated_release_tree(tmp_path / "release") as tree:
        yield tree
