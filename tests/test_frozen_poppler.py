"""Release second-renderer evidence must come from matching native frozen builds."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("frozen_poppler", Path(__file__).resolve().parents[1] / "packaging/frozen_poppler.py")
reviewer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reviewer)
XML = b'''<report><job><validationReport profileName="PDF/A-2b validation profile" jobEndStatus="normal" isCompliant="true"><details passedRules="100" failedRules="0" failedChecks="0"/></validationReport></job><batchSummary totalJobs="1" failedToParse="0" encrypted="0" outOfMemory="0" veraExceptions="0"><validationReports failedJobs="0" compliant="1" nonCompliant="0">1</validationReports></batchSummary></report>'''


class FrozenPopplerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.commit = "a" * 40
        for platform, plugin in (("linux", "xcb"), ("windows", "windows")):
            run = self.root / ("runtime-" + platform) / "frozen-smoke/run-test"
            case = run / "v09-core/test_pdfa_requires_actual_validator_and_preserves_order_orientation"
            case.mkdir(parents=True)
            (case / "verapdf.xml").write_bytes(XML)
            report = {"result": "PASS", "frozen": True, "app_version": "0.9.0",
                      "provenance": {"source_commit": self.commit}, "executable_sha256": "b" * 64,
                      "gui": {"qt_platform": plugin}, "core": {"tests": 34, "skipped": []}}
            (run / "result.json").write_text(json.dumps(report))
        self.assets = self.root / "assets"

    def tearDown(self):
        self.temp.cleanup()

    def render(self, command, **kwargs):
        destination = Path(command[-1]); destination.mkdir(parents=True)
        (destination / "result.json").write_text(json.dumps({"result": "PASS", "checks": list(range(7)), "input_sha256": {"actual-export.pdf": "c" * 64}}))

    def test_flat_reports_are_bound_to_commit_and_actual_exe_and_preserve_validator_bytes(self):
        with patch.object(reviewer.subprocess, "run", side_effect=self.render) as renderer:
            reviewer.review(self.root, self.root / "rendered", self.assets, self.commit, "0.9.0")
        self.assertEqual(renderer.call_count, 2)
        self.assertEqual(len(list(self.assets.iterdir())), 4)
        for platform in ("linux", "windows"):
            data = json.loads((self.assets / f"PDFWorkbench-v0.9.0-{platform}-poppler.json").read_text())
            self.assertEqual(data["source_commit"], self.commit)
            self.assertEqual(data["frozen_executable_sha256"], "b" * 64)
            self.assertEqual((self.assets / f"PDFWorkbench-v0.9.0-{platform}-pdfa.xml").read_bytes(), XML)
        self.assertFalse(any(p.is_dir() for p in self.assets.iterdir()))

    def test_other_commit_is_rejected_before_renderer_or_publication(self):
        with patch.object(reviewer.subprocess, "run") as renderer:
            with self.assertRaisesRegex(ValueError, "provenance"):
                reviewer.review(self.root, self.root / "rendered", self.assets, "d" * 40, "0.9.0")
        renderer.assert_not_called()
        self.assertFalse(self.assets.exists())

    def test_optional_or_other_validator_result_cannot_be_published(self):
        xml = next((self.root / "runtime-linux").rglob("verapdf.xml"))
        xml.write_bytes(XML.replace(b'isCompliant="true"', b'isCompliant="false"').replace(b'compliant="1" nonCompliant="0"', b'compliant="0" nonCompliant="1"'))
        with patch.object(reviewer.subprocess, "run") as renderer:
            with self.assertRaises(ValueError):
                reviewer.review(self.root, self.root / "rendered", self.assets, self.commit, "0.9.0")
        renderer.assert_not_called()
        self.assertFalse(self.assets.exists())

    def test_renderer_failure_on_second_platform_leaves_no_publishable_report(self):
        def render(command, **kwargs):
            if "windows" == Path(command[-1]).name:
                raise RuntimeError("independent renderer failed")
            self.render(command, **kwargs)
        with patch.object(reviewer.subprocess, "run", side_effect=render):
            with self.assertRaisesRegex(RuntimeError, "renderer failed"):
                reviewer.review(self.root, self.root / "rendered", self.assets, self.commit, "0.9.0")
        self.assertFalse(self.assets.exists())


if __name__ == "__main__":
    unittest.main()
