"""Tool downloads must be verified before any installer is executed."""
from pathlib import Path
import hashlib
import importlib.util
import io
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

spec = importlib.util.spec_from_file_location("acceptance_tools", Path(__file__).resolve().parents[1] / "packaging/acceptance_tools.py")
tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tools)


class AcceptanceToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cache = self.root / "cache"
        self.cache.mkdir()
        self.data = b"verified original installer fixture"
        self.sha = hashlib.sha256(self.data).hexdigest()
        self.url = "https://example.invalid/tool.zip"

    def tearDown(self):
        self.temp.cleanup()

    def test_verified_original_cache_avoids_network(self):
        target = self.cache / "tool.zip"
        target.write_bytes(self.data)
        with patch.object(tools.urllib.request, "urlopen", side_effect=AssertionError("duplicate download")):
            self.assertEqual(tools.download(self.cache, self.url, self.sha), target)

    def test_tampered_cached_installer_fails_before_network_or_execution(self):
        target = self.cache / "tool.zip"
        target.write_bytes(b"tampered executable")
        with patch.object(tools.urllib.request, "urlopen") as remote, patch.object(tools, "run") as run:
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                tools.download(self.cache, self.url, self.sha)
        remote.assert_not_called()
        run.assert_not_called()
        self.assertEqual(target.read_bytes(), b"tampered executable")

    def test_verified_download_is_reused_without_second_request(self):
        with patch.object(tools.urllib.request, "urlopen", return_value=io.BytesIO(self.data)) as remote:
            path = tools.download(self.cache, self.url, self.sha)
            self.assertEqual(path.read_bytes(), self.data)
            self.assertEqual(tools.download(self.cache, self.url, self.sha), path)
        remote.assert_called_once()

    def test_bad_checksum_retains_neither_installer_nor_partial(self):
        with patch.object(tools.urllib.request, "urlopen", return_value=io.BytesIO(b"foreign bytes")):
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                tools.download(self.cache, self.url, self.sha)
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_interrupted_response_retains_no_partial(self):
        class Interrupted(io.BytesIO):
            def read(self, *args):
                super().read(*args)
                raise OSError("connection interrupted")
        with patch.object(tools.urllib.request, "urlopen", return_value=Interrupted(self.data)):
            with self.assertRaisesRegex(OSError, "interrupted"):
                tools.download(self.cache, self.url, self.sha)
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_unexpected_installer_archive_is_never_executed(self):
        archive = self.root / "unexpected.zip"
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("../unexpected.jar", b"bad installer")
        with patch.object(tools, "download", return_value=archive), patch.object(tools, "run") as run:
            with self.assertRaisesRegex(ValueError, "expected installer"):
                tools.install_verapdf(self.cache, self.root, "java")
        run.assert_not_called()
        self.assertFalse((self.root / "unexpected.jar").exists())

    def test_verapdf_installer_configuration_handles_spaces_and_xml_characters(self):
        archive = self.root / "installer.zip"
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("verapdf-greenfield-1.30.3/verapdf-izpack-installer-1.30.3.jar", b"jar fixture")
        output = self.root / "tools & validation"
        output.mkdir()
        cli = output / "verapdf/bin/cli-1.30.3.jar"
        def install(command):
            if len(command) == 4 and Path(command[-1]).suffix == ".xml":
                cli.parent.mkdir(parents=True)
                cli.write_bytes(b"installed CLI")
        with patch.object(tools, "download", return_value=archive), patch.object(tools, "run", side_effect=install):
            self.assertEqual(tools.install_verapdf(self.cache, output, "java"), cli)
        root = ET.parse(output / "verapdf-install.xml").getroot()
        self.assertEqual(root.find(".//installpath").text, str(output / "verapdf"))
        selected = [p.attrib["name"] for p in root.findall(".//pack") if p.attrib["selected"] == "true"]
        self.assertEqual(selected, ["veraPDF CLI"])


if __name__ == "__main__":
    unittest.main()
