import ast
import hashlib
import importlib.util
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib
import pytest


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("builder", ROOT / "scripts/build.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class TestBuild:
    def test_self_contained_build_metadata_checksum_and_reproducibility(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        with tempfile.TemporaryDirectory() as directory:
            artifact = builder.build(Path(directory) / "first")
            assert artifact.name == project['name'] + '_any.py'
            built = artifact.read_text()
            page = next(node.value for node in ast.parse(built).body
                        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "PAGE"
                                                               for target in node.targets))
            assert ast.literal_eval(page) == (ROOT / 'dist/ui/camera.html').read_text()
            block = re.search(r"(?ms)^# /// script\n(.*?)^# ///$", built).group(1)
            metadata = tomllib.loads("\n".join(line.removeprefix("#").removeprefix(" ") for line in block.splitlines()))
            assert metadata['tool']['orcaslicer']['plugin']['version'] == project['version']
            assert metadata['dependencies'] == project['dependencies']
            assert project['license'] == 'MIT'
            license_text = (ROOT / "LICENSE").read_text()
            notice = "\n".join("# " + line if line else "#" for line in license_text.splitlines())
            assert notice in built
            checksum, filename = artifact.with_name("SHA256SUMS").read_text().split()
            assert filename == artifact.name
            assert checksum == hashlib.sha256(artifact.read_bytes()).hexdigest()
            again = builder.build(Path(directory) / "second")
            assert artifact.read_bytes() == again.read_bytes()

    def test_release_version_mismatch_produces_no_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            with pytest.raises(ValueError, match='Release tag'):
                builder.build(directory, "v0.0.0")
            assert list(Path(directory).iterdir()) == []

    def test_development_version_cannot_be_built_as_a_release(self):
        version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
        if not version.endswith(".dev0"):
            pytest.skip('The checkout currently contains a final release version')
        with tempfile.TemporaryDirectory() as directory:
            with pytest.raises(ValueError, match='Development versions'):
                builder.build(directory, "v" + version)
            assert list(Path(directory).iterdir()) == []

    def test_address_resolution_does_not_lazily_open_codec_cache_files(self):
        code = '''
import importlib.util, socket, sys, types
orca = types.ModuleType("orca")
orca.pages = types.SimpleNamespace(PagesPluginCapabilityBase=object)
orca.base = object
orca.plugin = lambda cls: cls
sys.modules["orca"] = orca
spec = importlib.util.spec_from_file_location("plugin", sys.argv[1])
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)
opens = []
sys.addaudithook(lambda event, args: opens.append(type(args[0]).__name__) if event == "open" else None)
socket.getaddrinfo("127.0.0.1", 80)
assert not opens, opens
'''
        with tempfile.TemporaryDirectory() as directory:
            artifact = builder.build(Path(directory) / "dist")
            subprocess.run([sys.executable, "-X", "pycache_prefix=" + directory + "/cache",
                            "-c", code, str(artifact)], check=True, capture_output=True, text=True)
