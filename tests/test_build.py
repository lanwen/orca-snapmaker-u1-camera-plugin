import ast
import hashlib
import importlib.util
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("builder", ROOT / "scripts/build.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class BuildTests(unittest.TestCase):
    def test_self_contained_build_metadata_checksum_and_reproducibility(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        with tempfile.TemporaryDirectory() as directory:
            artifact = builder.build(Path(directory) / "first")
            built = artifact.read_text()
            page = next(node.value for node in ast.parse(built).body
                        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "PAGE"
                                                               for target in node.targets))
            self.assertEqual(ast.literal_eval(page), (ROOT / "src/camera.html").read_text())
            block = re.search(r"(?ms)^# /// script\n(.*?)^# ///$", built).group(1)
            metadata = tomllib.loads("\n".join(line.removeprefix("#").removeprefix(" ") for line in block.splitlines()))
            self.assertEqual(metadata["tool"]["orcaslicer"]["plugin"]["version"], project["version"])
            self.assertEqual(metadata["dependencies"], project["dependencies"])
            self.assertEqual(project["license"], "MIT")
            license_text = (ROOT / "LICENSE").read_text()
            notice = "\n".join("# " + line if line else "#" for line in license_text.splitlines())
            self.assertIn(notice, built)
            checksum = artifact.with_name("SHA256SUMS").read_text().split()[0]
            self.assertEqual(checksum, hashlib.sha256(artifact.read_bytes()).hexdigest())
            again = builder.build(Path(directory) / "second")
            self.assertEqual(artifact.read_bytes(), again.read_bytes())

    def test_release_version_mismatch_produces_no_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Release tag"):
                builder.build(directory, "v0.0.0")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_development_version_cannot_be_built_as_a_release(self):
        version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
        if not version.endswith(".dev0"):
            self.skipTest("The checkout currently contains a final release version")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Development versions"):
                builder.build(directory, "v" + version)
            self.assertEqual(list(Path(directory).iterdir()), [])

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
